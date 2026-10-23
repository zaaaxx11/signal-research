"""Export manifest and chain-of-custody (02 §4.5/§4.8, Fase 5 gate 2).

The manifest is the document that makes an export auditable *after* the process
that wrote it is gone. Four properties are deliberate:

* **It never describes itself.** ``files`` lists data files only; the manifest
  and the custody log are not in it, because a checksum list that contains its
  own checksum cannot be verified by the person who receives it.
* **Every field is recomputable.** ``files[*].sha256``/``bytes`` are properties
  of the bytes on disk, ``coverage`` is arithmetic over the same numbers and
  ``content_sha256`` is a hash of the table (not of a format's bytes, which
  carry library metadata). ``verify_manifest`` recomputes all of it and says
  what is wrong instead of returning a boolean.
* **Coverage is a comparison, not a tautology.** ``rows_expected`` comes from
  the caller (or from the shard plan) and ``rows_written`` from each writer's
  own count; ``ratio`` is only ``1.0`` when the two agree for the table *and*
  for every format.
* **The selection is recorded, not inferred.** A bundle answers a question, so
  :func:`audit_block` pins the six fields that name it — ``selection_id``,
  ``filter_hash``, ``view``, ``source_graph_version``, ``criteria_snapshot``
  and ``as_of`` — with ``None`` for anything the writer was not told. An empty
  criteria document is a claim ("this selection had no criteria"), so absence
  is written as ``null`` instead; :data:`MANIFEST_VERSION` lets a reader tell
  which shape of document it is holding.

The custody log is append-only JSONL with a monotonically increasing ``seq``
and an aware-UTC ``at``. "We deleted it" without "what it was" is not an audit,
so every purge carries the hash and size of the file it destroyed.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

#: Bumped when ``CANONICAL_COLUMNS_V1`` changes; stamped on the Parquet schema
#: and in the manifest so a file can always be matched to its column contract.
COLUMNS_VERSION = "v1"

#: Version of the manifest **document** this module writes and can verify. A
#: manifest that does not declare it predates the field and is still read with
#: v1 semantics — every key it carries is a subset of v1 — but a document that
#: declares a version this reader does not know is not silently accepted: the
#: audit guarantee of v1 cannot be assumed for a shape it never saw.
MANIFEST_VERSION = "v1"

#: What a manifest records about the *question* that produced the table, as
#: opposed to the table itself. Every name is written even when the value is
#: unknown, so a reader can tell "not recorded" (``None``) from "recorded as
#: this" with one lookup instead of a ``KeyError``-versus-``None`` puzzle.
AUDIT_FIELDS: tuple[str, ...] = (
    "selection_id",
    "filter_hash",
    "view",
    "source_graph_version",
    "criteria_snapshot",
    "as_of",
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(moment: datetime) -> str:
    """Aware UTC, microseconds, so lexical order equals chronological order."""

    if moment.tzinfo is None:
        raise ValueError("naive datetimes are not allowed in an audit log")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def file_sha256(path: str | Path, *, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestCheck:
    """The result of re-deriving a manifest from the files on disk."""

    ok: bool
    problems: tuple[str, ...] = ()
    files_checked: int = 0


def read_manifest(path: str | Path) -> dict[str, Any]:
    """Read a manifest as a plain dict; a missing file is an error, not ``{}``."""

    target = Path(path)
    document = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"manifest {target} is not a JSON object")
    return document


def write_manifest(path: str | Path, manifest: Mapping[str, Any]) -> Path:
    """Write the manifest atomically (tmp in the same directory, then replace)."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}-{os.urandom(4).hex()}")
    try:
        payload = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


def audit_block(values: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The audit block of a manifest: one key per :data:`AUDIT_FIELDS`.

    A value the writer was not given stays ``None`` — never ``""``, never
    ``{}``, never something re-derived from the rows. ``criteria_snapshot`` is
    where that matters most: an empty document reads as "a selection with no
    criteria", which is a claim, while ``None`` reads as "this bundle did not
    record the criteria", which is the truth.

    An unknown key is refused rather than dropped: a field silently discarded
    from an audit document is an audit defect, not a cosmetic one.
    """

    block: dict[str, Any] = {name: None for name in AUDIT_FIELDS}
    if values is None:
        return block
    unknown = sorted(key for key in values if key not in AUDIT_FIELDS)
    if unknown:
        raise ValueError(
            f"unknown audit field(s) {unknown}; expected any of {', '.join(AUDIT_FIELDS)}"
        )
    block.update({key: value for key, value in values.items()})
    return block


def verify_manifest(path: str | Path) -> ManifestCheck:
    """Recompute every hash and every coverage number the manifest claims.

    What is checked: existence, byte size and SHA-256 of each listed file; the
    number of listed files; ``rows_expected == rows_written == row_count``;
    ``ratio == 1.0``; ``coverage.by_format[*]`` and ``files[*].row_count``
    against ``row_count``. What is *not* checked: that the rows inside each file
    equal the table — that needs a re-import, which is a separate gate.
    """

    target = Path(path)
    try:
        manifest = read_manifest(target)
    except (OSError, ValueError) as exc:
        return ManifestCheck(ok=False, problems=(f"manifest unreadable: {exc}",), files_checked=0)

    problems: list[str] = []
    declared = manifest.get("manifest_version")
    if declared is not None and declared != MANIFEST_VERSION:
        problems.append(
            f"manifest_version {declared!r} is not supported; this reader verifies "
            f"{MANIFEST_VERSION!r} and cannot vouch for a shape it does not know"
        )

    if "as_of" in manifest:
        as_of = manifest.get("as_of")
        if not isinstance(as_of, str) or not as_of.strip():
            problems.append(
                f"as_of {as_of!r} is not a non-empty ISO-8601 timestamp: a bundle "
                "that cannot date its table cannot be audited"
            )

    files = manifest.get("files")
    if not isinstance(files, list):
        return ManifestCheck(
            ok=False, problems=("manifest has no files list",), files_checked=0
        )

    root = target.parent
    checked = 0
    for entry in files:
        if not isinstance(entry, Mapping):
            problems.append(f"files entry is not an object: {entry!r}")
            continue
        name = str(entry.get("name") or "")
        if not name:
            problems.append("files entry without a name")
            continue
        artifact = root / name
        checked += 1
        if not artifact.exists():
            problems.append(f"{name}: missing file")
            continue
        size = artifact.stat().st_size
        if entry.get("bytes") != size:
            problems.append(f"{name}: bytes {entry.get('bytes')!r} != {size} on disk")
        actual = file_sha256(artifact)
        if entry.get("sha256") != actual:
            problems.append(f"{name}: sha256 mismatch (manifest says {entry.get('sha256')!r})")

    files_count = manifest.get("files_count")
    if files_count != len(files):
        problems.append(f"files_count {files_count!r} != {len(files)} listed files")

    row_count = manifest.get("row_count")
    coverage = manifest.get("coverage")
    if not isinstance(coverage, Mapping):
        problems.append("manifest has no coverage block")
    else:
        expected = coverage.get("rows_expected")
        written = coverage.get("rows_written")
        ratio = coverage.get("ratio")
        if expected != row_count:
            problems.append(f"coverage rows_expected {expected!r} != row_count {row_count!r}")
        if written != expected:
            problems.append(f"coverage rows_written {written!r} != rows_expected {expected!r}")
        if ratio != 1.0:
            problems.append(f"coverage ratio {ratio!r} is not 1.0")
        by_format = coverage.get("by_format")
        if not isinstance(by_format, Mapping) or not by_format:
            problems.append("coverage has no by_format counts")
        else:
            for fmt, count in sorted(by_format.items()):
                if count != row_count:
                    problems.append(
                        f"coverage by_format[{fmt}] {count!r} != row_count {row_count!r}"
                    )

    for entry in files:
        if isinstance(entry, Mapping) and entry.get("row_count") != row_count:
            problems.append(
                f"{entry.get('name')}: row_count {entry.get('row_count')!r} != {row_count!r}"
            )

    return ManifestCheck(ok=not problems, problems=tuple(problems), files_checked=checked)


# ---------------------------------------------------------------------------
# Custody
# ---------------------------------------------------------------------------


def read_custody(path: str | Path) -> tuple[dict[str, Any], ...]:
    """Every record of an append-only log, in file order.

    A missing log is an empty history (nothing has happened yet), not an error.
    """

    target = Path(path)
    if not target.exists():
        return ()
    records: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(json.loads(line))
    return tuple(records)


@dataclass
class CustodyLog:
    """Append-only writer: one JSON object per line, ``seq`` never repeats.

    The file is opened per append and flushed to disk, so a crash between two
    events loses at most the event that was being written — never history that
    was already recorded.
    """

    path: Path
    clock: Any = utc_now
    _seq: int = field(default=0, init=False, repr=False)
    _loaded: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    @property
    def seq(self) -> int:
        if not self._loaded:
            self._seq = self._last_seq()
            self._loaded = True
        return self._seq

    def _last_seq(self) -> int:
        last = 0
        for record in read_custody(self.path):
            try:
                last = max(last, int(record.get("seq") or 0))
            except (TypeError, ValueError):  # pragma: no cover - a hand-edited log
                continue
        return last

    def append(self, event: str, *, job_id: str, **fields: Any) -> dict[str, Any]:
        self._seq = self.seq + 1
        self._loaded = True
        record: dict[str, Any] = {
            "seq": self._seq,
            "at": iso(self.clock()),
            "job_id": job_id,
            "event": event,
        }
        record.update(fields)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record


__all__ = [
    "AUDIT_FIELDS",
    "COLUMNS_VERSION",
    "CustodyLog",
    "MANIFEST_VERSION",
    "ManifestCheck",
    "audit_block",
    "file_sha256",
    "iso",
    "read_custody",
    "read_manifest",
    "utc_now",
    "verify_manifest",
    "write_manifest",
]
