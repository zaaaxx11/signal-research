"""Operational surface: metrics, cache retention, snapshot backup.

The engine is one process and one JSON snapshot. That is simple until the disk
fills or the box is lost: this module answers "how big is the cache", "what may
be deleted without breaking the fund map", and "can I restore a known-good
snapshot". It deliberately has no network and no vendor.

Contract: ``briefs/market-engine/R7-CONTRACT.md``.

Two rules shape almost every function here:

* **The document is safe to serve.** Cache stats carry no directory, no
  basename and no key: it is operational metadata that a public deployment may
  answer, and a path would tell an anonymous caller where to aim.
* **Nothing is invented.** A missing directory is zeros, an unparseable
  ``fetched_at`` is ``None``, and a restore without a matching digest writes
  nothing at all. An operator can act on ``None``; a guessed date or a
  half-restored snapshot is worse than no answer.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from stat import S_ISREG
from typing import Any

from app.engine import paths
from app.engine.service import EngineService

#: The cache vocabulary, in report order. Every kind is always present so a
#: reader can compare two processes without wondering whether a missing key
#: means "none" or "this build forgot the kind".
_CACHE_KINDS: tuple[str, ...] = (
    "fund_series",
    "insider",
    "nport",
    "facts",
    "submissions",
    "other",
)

#: The official ticker → fund-series map. It is what makes IVV's holdings
#: correct, so retention must never treat it as an ordinary cache file.
_FUND_MAP_NAME = "company_tickers_mf.json"

#: Snapshot naming. The fixed-width UTC stamp sorts lexicographically exactly
#: as it sorts chronologically, which is what makes rotation a plain sort.
_SNAPSHOT_PREFIX = "engine-snapshot-"
_SNAPSHOT_STAMP = "%Y%m%dT%H%M%SZ"
_SNAPSHOT_SUFFIX = ".json"
_SNAPSHOT_GLOB = f"{_SNAPSHOT_PREFIX}*{_SNAPSHOT_SUFFIX}"
_SIDECAR_SUFFIX = ".sha256"

#: Streaming block size for snapshot hashing/copying: large enough to keep the
#: syscall count down, small enough that a multi-gigabyte snapshot never has to
#: fit in memory.
_CHUNK_BYTES = 1 << 20


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """One cache file, with the two facts retention needs."""

    path: Path
    size: int
    mtime: float


# ---------------------------------------------------------------------------
# cache inspection
# ---------------------------------------------------------------------------


def scan_cache(cache_dir: Path) -> tuple[CacheEntry, ...]:
    """List the files directly inside ``cache_dir``, sorted by name.

    Only direct children are candidates: the engine writes one file per
    document, and walking subdirectories would let a stray tree be deleted as
    if it were cache. A missing directory is "nothing cached", not an error —
    a fresh deployment has no cache yet and ``/engine/metrics`` must still
    answer. A file that disappears between listing and ``stat`` is skipped for
    the same reason: the answer describes what is there now.
    """

    try:
        children = sorted(cache_dir.iterdir(), key=lambda child: child.name)
    except (FileNotFoundError, NotADirectoryError):
        return ()

    entries: list[CacheEntry] = []
    for path in children:
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        if not S_ISREG(stat.st_mode):
            continue
        entries.append(CacheEntry(path=path, size=stat.st_size, mtime=stat.st_mtime))
    return tuple(entries)


def _cache_kind(name: str) -> str:
    """The kind of one cache file, from its name alone.

    The names are written by the collectors (``facts_cache_path`` and friends),
    so the filename is the only metadata available without opening a document.
    ``company_tickers_mf.json`` is checked first: it is the one file whose loss
    silently changes fund holdings, and no ``CIK`` prefix may claim it.
    """

    if name == _FUND_MAP_NAME:
        return "fund_series"
    if name.startswith("CIK"):
        if "-form4" in name:
            return "insider"
        if "-nport" in name:
            return "nport"
        if "-companyfacts" in name:
            return "facts"
        if "-submissions" in name:
            return "submissions"
    return "other"


def _iso_utc(timestamp: float) -> str:
    """One epoch timestamp as an ISO-8601 UTC string."""

    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


def cache_stats(cache_dir: Path) -> dict[str, object]:
    """Summarise the cache without exposing a path or a filename.

    Per-kind blocks carry both a file count and a byte total because the two
    answer different operator questions: "is the insider cache growing without
    bound" and "is it the byte hog". Totals and the mtime extremes describe the
    whole directory; ``None`` means there is nothing to describe.
    """

    return _stats_of(scan_cache(cache_dir))


def zero_cache_stats() -> dict[str, object]:
    """The cache block for "no cache directory is configured".

    The route uses this rather than pointing ``cache_stats`` at a placeholder
    path: an unset setting must not be mistaken for an empty directory that
    happens to exist.
    """

    return _stats_of(())


def _stats_of(entries: Sequence[CacheEntry]) -> dict[str, object]:
    """Build the stats document from already-scanned entries."""

    files: dict[str, int] = dict.fromkeys(_CACHE_KINDS, 0)
    sizes: dict[str, int] = dict.fromkeys(_CACHE_KINDS, 0)
    for entry in entries:
        kind = _cache_kind(entry.path.name)
        files[kind] += 1
        sizes[kind] += entry.size

    mtimes = [entry.mtime for entry in entries]
    return {
        "files": len(entries),
        "bytes": sum(entry.size for entry in entries),
        "by_kind": {
            kind: {"files": files[kind], "bytes": sizes[kind]} for kind in _CACHE_KINDS
        },
        "oldest_mtime": _iso_utc(min(mtimes)) if mtimes else None,
        "newest_mtime": _iso_utc(max(mtimes)) if mtimes else None,
    }


# ---------------------------------------------------------------------------
# dataset metrics
# ---------------------------------------------------------------------------


def dataset_metrics(
    service: EngineService, *, now: datetime | None = None
) -> dict[str, object]:
    """Describe the loaded dataset and its age, or state that none is loaded.

    ``dataset: None`` is deliberate rather than a block of zeroes: an engine
    that never ingested anything and one that ingested an empty universe are
    different deployments, and only the second has a ``fetched_at`` to report.

    ``facts_symbols`` counts symbols that carry facts, not fact cells: a
    document asking "how much of the universe has fundamentals" is not helped
    by AAPL's two hundred cells outweighing MSFT's five.
    """

    dataset = service.dataset
    public_mode = bool(service.policy.public_mode)
    if dataset is None:
        return {"dataset": None, "public_mode": public_mode}

    clock = _as_utc(now) if now is not None else datetime.now(UTC)
    return {
        "dataset": {
            "symbols": len(dataset.security_master),
            "facts_symbols": len(dataset.facts),
            "fetched_at": dataset.fetched_at,
            "age_seconds": _age_seconds(dataset.fetched_at, clock),
            "errors": len(dataset.errors),
        },
        "public_mode": public_mode,
    }


def _age_seconds(fetched_at: object, now: datetime) -> int | None:
    """Seconds between ``fetched_at`` and ``now``, clamped at zero.

    A clock skew (a snapshot written on a box a minute ahead) must not publish
    a negative age, and a timestamp this build cannot parse must not become a
    guess: it is ``None``. A timestamp without a zone is read as UTC because
    the engine only ever writes UTC.
    """

    if not isinstance(fetched_at, str):
        return None
    try:
        parsed = datetime.fromisoformat(fetched_at)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0, int((now - parsed).total_seconds()))


def _as_utc(value: datetime) -> datetime:
    """Read a naive clock as UTC and convert an aware one."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# ---------------------------------------------------------------------------
# cache retention
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _RetentionPlan:
    """What retention would remove, and what it would leave behind."""

    doomed: tuple[CacheEntry, ...]
    eligible_kept: tuple[CacheEntry, ...]
    protected: tuple[CacheEntry, ...]

    def report(self, *, dry_run: bool) -> dict[str, object]:
        """The public report. Counts only: never a path an operator did not ask for."""

        kept = self.eligible_kept + self.protected
        return {
            "deleted": len(self.doomed),
            "freed_bytes": sum(entry.size for entry in self.doomed),
            "kept_files": len(kept),
            "kept_bytes": sum(entry.size for entry in kept),
            "protected_files": len(self.protected),
            "dry_run": dry_run,
        }


def _is_eligible(name: str) -> bool:
    """May retention ever delete this file?

    Only documents the collectors can re-fetch: a ``CIK``-prefixed JSON/XML
    cache file. Everything else — above all ``company_tickers_mf.json`` — is
    protected. Deleting the fund map would not lose a cache entry; it would
    silently bring back wrong holdings, because the map is what resolves an
    ETF ticker to its SEC series.
    """

    return name.startswith("CIK") and name.endswith((".json", ".xml"))


def _plan_retention(
    entries: Sequence[CacheEntry],
    *,
    max_bytes: int | None,
    max_age_days: float | None,
    now: datetime,
) -> _RetentionPlan:
    """Choose victims: age first, then oldest-first until the budget fits."""

    if max_bytes is not None and max_bytes < 0:
        raise ValueError(f"max_bytes must be >= 0, got {max_bytes}")
    if max_age_days is not None and max_age_days < 0:
        raise ValueError(f"max_age_days must be >= 0, got {max_age_days}")

    ordered = sorted(entries, key=lambda entry: (entry.mtime, entry.path.name))
    protected = tuple(entry for entry in ordered if not _is_eligible(entry.path.name))
    eligible = [entry for entry in ordered if _is_eligible(entry.path.name)]

    doomed: list[CacheEntry] = []
    if max_age_days is not None:
        cutoff = now.timestamp() - max_age_days * 86_400.0
        survivors: list[CacheEntry] = []
        for entry in eligible:
            if entry.mtime < cutoff:
                doomed.append(entry)
            else:
                survivors.append(entry)
        eligible = survivors

    if max_bytes is not None:
        total = sum(entry.size for entry in eligible)
        index = 0
        # Oldest first, and stop the moment the remaining eligible bytes fit.
        # Protected files are not counted in ``total`` and are never victims,
        # so the budget describes what retention can actually control.
        while total > max_bytes and index < len(eligible):
            victim = eligible[index]
            doomed.append(victim)
            total -= victim.size
            index += 1
        eligible = eligible[index:]

    return _RetentionPlan(
        doomed=tuple(doomed),
        eligible_kept=tuple(eligible),
        protected=protected,
    )


def plan_retention(
    entries: Sequence[CacheEntry],
    *,
    max_bytes: int | None = None,
    max_age_days: float | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Report the retention plan without touching the filesystem.

    The report is a count, not a list of paths: it is meant to be logged,
    served and diffed, and absolute paths in such a document leak more about
    the box than the operator asked to publish. ``dry_run`` is always true
    here — a plan cannot have deleted anything.
    """

    clock = _as_utc(now) if now is not None else datetime.now(UTC)
    plan = _plan_retention(
        entries, max_bytes=max_bytes, max_age_days=max_age_days, now=clock
    )
    return plan.report(dry_run=True)


def apply_retention(
    cache_dir: Path,
    *,
    max_bytes: int | None = None,
    max_age_days: float | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    """Scan ``cache_dir``, plan retention, and unlink the victims.

    A file that vanished between the scan and the unlink is tolerated: another
    process (or an earlier run) already did the work, and failing the whole
    command for that would hide the files that remain over budget. Any other
    ``OSError`` propagates — a permission problem must be visible, not counted
    as a successful delete.
    """

    entries = scan_cache(cache_dir)
    plan = _plan_retention(
        entries, max_bytes=max_bytes, max_age_days=max_age_days, now=datetime.now(UTC)
    )
    report = plan.report(dry_run=bool(dry_run))
    if dry_run:
        return report

    for entry in plan.doomed:
        try:
            entry.path.unlink()
        except FileNotFoundError:
            continue
    return report


# ---------------------------------------------------------------------------
# snapshot backup / restore
# ---------------------------------------------------------------------------


def _digest_file(path: Path) -> tuple[str, int]:
    """The SHA-256 and byte length of ``path``, streamed."""

    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _copy_with_digest(source: Path, destination: Path) -> tuple[str, int]:
    """Copy ``source`` to ``destination`` in one read pass, hashing as it goes.

    One pass gives the digest of exactly the bytes that were written, which is
    what makes the post-copy check meaningful; hashing the file separately
    afterwards would only prove the copy is self-consistent.
    """

    digest = hashlib.sha256()
    size = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, destination.open("wb") as writer:
        while chunk := reader.read(_CHUNK_BYTES):
            digest.update(chunk)
            writer.write(chunk)
            size += len(chunk)
        writer.flush()
        # fsync before the atomic rename: a crash after ``os.replace`` must not
        # be able to leave a named snapshot with unwritten bytes behind it.
        os.fsync(writer.fileno())
    return digest.hexdigest(), size


def _atomic_path(directory: Path, name: str) -> Path:
    """A unique temp path inside ``directory``, on the same filesystem as the target."""

    handle, raw = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=directory)
    os.close(handle)
    return Path(raw)


def backup_snapshot(
    snapshot: Path,
    dest_dir: Path,
    *,
    keep: int = 3,
    now: datetime | None = None,
) -> dict[str, object]:
    """Copy ``snapshot`` into ``dest_dir`` under an injected-clock name.

    The copy is written to a temp file and renamed, so a reader never sees a
    half-written snapshot under the final name. The sidecar is written after
    the body and hashed from the copied bytes, then compared with the source
    digest: a backup that cannot be verified is not a backup, and finding that
    out at restore time is too late.

    ``dest_dir`` may be a mounted off-box path; nothing here assumes a network
    or an object store.
    """

    if keep < 1:
        raise ValueError(f"keep must be at least 1, got {keep}")

    source = Path(snapshot)
    destination = Path(dest_dir)
    destination.mkdir(parents=True, exist_ok=True)

    clock = _as_utc(now) if now is not None else datetime.now(UTC)
    name = f"{_SNAPSHOT_PREFIX}{clock.strftime(_SNAPSHOT_STAMP)}{_SNAPSHOT_SUFFIX}"
    target = destination / name
    sidecar = destination / f"{name}{_SIDECAR_SUFFIX}"

    temporary = _atomic_path(destination, name)
    try:
        source_digest, size = _copy_with_digest(source, temporary)
        copied_digest, copied_size = _digest_file(temporary)
        if (copied_digest, copied_size) != (source_digest, size):
            raise ValueError(
                f"snapshot copy of {source.name!r} does not match the source bytes"
            )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)

    sidecar_temp = _atomic_path(destination, f"{name}{_SIDECAR_SUFFIX}")
    try:
        sidecar_temp.write_text(
            f"{source_digest}  {name}\n", encoding="utf-8", newline="\n"
        )
        os.replace(sidecar_temp, sidecar)
    finally:
        sidecar_temp.unlink(missing_ok=True)

    kept, pruned = _prune_snapshots(destination, keep=keep)
    return {
        "backup": str(target),
        "sha256": source_digest,
        "bytes": size,
        "kept": kept,
        "pruned": pruned,
    }


def _prune_snapshots(destination: Path, *, keep: int) -> tuple[int, int]:
    """Delete all but the newest ``keep`` snapshots; also drop their sidecars.

    Returns ``(kept, pruned)``. The names embed a fixed-width UTC stamp, so
    sorting them by name is sorting them by time. A missing file is skipped:
    another process pruning the same directory is not an error.
    """

    names = sorted(
        (path for path in destination.glob(_SNAPSHOT_GLOB) if path.is_file()),
        key=lambda path: path.name,
    )
    survivors = names[-keep:]
    pruned = 0
    for stale in names[:-keep]:
        try:
            stale.unlink()
        except FileNotFoundError:
            continue
        pruned += 1
        try:
            stale.with_name(f"{stale.name}{_SIDECAR_SUFFIX}").unlink()
        except FileNotFoundError:
            continue
    return len(survivors), pruned


def _read_sidecar(sidecar: Path, *, backup_name: str) -> str:
    """The digest promised by ``sidecar`` for ``backup_name``.

    The format is ``sha256sum``'s: ``<hex>  <name>``. A sidecar that names a
    different file is refused even when its digest is well-formed, because that
    is a backup from somewhere else and restoring it would silently swap the
    deployment's evidence.
    """

    try:
        text = sidecar.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read digest file {sidecar.name!r}: {exc}") from exc

    tokens = text.split()
    if not tokens:
        raise ValueError(f"digest file {sidecar.name!r} is empty")
    digest = tokens[0].lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError(
            f"digest file {sidecar.name!r} does not hold a sha256 hex digest"
        )
    if len(tokens) > 1 and tokens[1] != backup_name:
        raise ValueError(
            f"digest file {sidecar.name!r} names {tokens[1]!r}, "
            f"not the requested backup {backup_name!r}"
        )
    return digest


def restore_snapshot(backup: Path, target: Path) -> dict[str, object]:
    """Verify ``backup`` against its sidecar, then copy it to ``target``.

    Verification happens **before** the target is created or replaced: a
    missing or mismatched digest raises ``ValueError`` and leaves an existing
    target byte-identical. A restore is the one operation allowed to overwrite
    live evidence, so it must either be proven good or not happen.
    """

    source = Path(backup)
    destination = Path(target)
    if not source.is_file():
        raise ValueError(f"backup {source.name!r} does not exist")

    sidecar = source.with_name(f"{source.name}{_SIDECAR_SUFFIX}")
    if not sidecar.is_file():
        raise ValueError(
            f"backup {source.name!r} has no {_SIDECAR_SUFFIX} digest file; "
            "an unverified snapshot cannot be restored"
        )
    expected = _read_sidecar(sidecar, backup_name=source.name)

    actual, size = _digest_file(source)
    if actual != expected:
        raise ValueError(
            f"backup {source.name!r} does not match its digest file: "
            f"expected {expected}, got {actual}"
        )

    # Only now, with the bytes proven, may the target's directory be created:
    # a rejected restore must leave the filesystem exactly as it found it.
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = _atomic_path(destination.parent, destination.name)
    try:
        copied, copied_size = _copy_with_digest(source, temporary)
        if copied != expected or copied_size != size:
            raise ValueError(
                f"restored copy of {source.name!r} does not match its digest file"
            )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

    return {"target": str(destination), "sha256": expected, "bytes": size}


async def smoke_check(
    base_url: str,
    *,
    client: Any = None,
    expect_public: bool | None = None,
    max_age_seconds: float | None = None,
) -> dict[str, object]:
    """Check a running deployment's health and metrics. Implemented in R7."""

    if client is not None:
        return await _smoke_document(
            base_url,
            client=client,
            expect_public=expect_public,
            max_age_seconds=max_age_seconds,
        )
    from app.providers import http as provider_http

    async with provider_http.build_client() as owned:
        return await _smoke_document(
            base_url,
            client=owned,
            expect_public=expect_public,
            max_age_seconds=max_age_seconds,
        )


async def _smoke_document(
    base_url: str,
    *,
    client: Any,
    expect_public: bool | None,
    max_age_seconds: float | None,
) -> dict[str, object]:
    """Read the two documents and turn them into named checks.

    An HTTP failure is a failed check, not an exception: ``smoke`` exists to
    report a deployment, and a traceback is not a report.  The four always-on
    checks are status codes and dataset presence; the two conditional checks
    appear only when the operator asked for them, so a report cannot claim a
    check it never ran.
    """

    base = base_url.rstrip("/")
    checks: list[dict[str, object]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    async def fetch(path: str) -> tuple[Any, dict[str, object] | None, str | None]:
        try:
            response = await client.get(f"{base}{path}")
        except Exception as exc:  # noqa: BLE001 - the failure is the check
            return None, None, f"{type(exc).__name__}: {exc}"
        try:
            payload = response.json()
        except ValueError as exc:
            return response, None, f"non-JSON body: {type(exc).__name__}"
        if not isinstance(payload, dict):
            return response, None, "body is not a JSON object"
        return response, payload, None

    health_response, health, health_error = await fetch("/engine/health")
    metrics_response, metrics, metrics_error = await fetch("/engine/metrics")

    add(
        "health_status",
        health_response is not None and health_response.status_code == 200,
        "HTTP 200" if health_response is not None and health_response.status_code == 200
        else (health_error or f"HTTP {getattr(health_response, 'status_code', 'none')}"),
    )
    add(
        "metrics_status",
        metrics_response is not None and metrics_response.status_code == 200,
        "HTTP 200" if metrics_response is not None and metrics_response.status_code == 200
        else (metrics_error or f"HTTP {getattr(metrics_response, 'status_code', 'none')}"),
    )

    dataset = health.get("dataset") if isinstance(health, dict) else None
    symbols = dataset.get("symbols") if isinstance(dataset, dict) else None
    add(
        "dataset_symbols",
        isinstance(symbols, int) and symbols > 0,
        f"symbols={symbols}" if symbols is not None else "no dataset loaded",
    )

    startup = None
    for document in (health, metrics):
        if isinstance(document, dict) and document.get("startup_error"):
            startup = document["startup_error"]
            break
    add(
        "startup_error",
        startup is None,
        "clean" if startup is None else str(startup),
    )

    if expect_public is not None:
        actual = metrics.get("public_mode") if isinstance(metrics, dict) else None
        add(
            "public_mode",
            actual is expect_public,
            f"expected {expect_public}, got {actual}",
        )

    if max_age_seconds is not None:
        metrics_dataset = metrics.get("dataset") if isinstance(metrics, dict) else None
        age = (
            metrics_dataset.get("age_seconds")
            if isinstance(metrics_dataset, dict)
            else None
        )
        add(
            "dataset_age",
            isinstance(age, (int, float)) and age <= max_age_seconds,
            f"age={age}s limit={max_age_seconds}s",
        )

    return {
        "ok": all(bool(item["ok"]) for item in checks),
        "base_url": base,
        "checks": checks,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class _UsageError(Exception):
    """The command line asked for something this tool cannot do."""


class _Parser(argparse.ArgumentParser):
    """A parser whose errors are JSON, not argparse's prose on stderr."""

    def error(self, message: str) -> None:  # type: ignore[override]
        raise _UsageError(message)


def _build_parser() -> argparse.ArgumentParser:
    """The operator CLI described by the contract."""

    parser = _Parser(
        prog="python -m app.engine.ops",
        description="Inspect the engine cache, apply retention, back up and restore.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    cache = commands.add_parser("cache", help="print the cache statistics document")
    cache.add_argument(
        "--cache-dir", default=None, help="engine cache directory (default: env/data)"
    )

    retention = commands.add_parser(
        "retention", help="delete re-fetchable cache documents over a limit"
    )
    retention.add_argument(
        "--cache-dir", default=None, help="engine cache directory (default: env/data)"
    )
    retention.add_argument("--max-bytes", type=int, default=None, help="byte budget")
    retention.add_argument(
        "--max-age-days", type=float, default=None, help="age cutoff in days"
    )
    retention.add_argument(
        "--dry-run", action="store_true", help="report the plan without deleting"
    )

    backup = commands.add_parser("backup", help="copy and rotate a snapshot")
    backup.add_argument(
        "--snapshot", default=None, help="snapshot file to copy (default: env/data)"
    )
    backup.add_argument(
        "--dest", default=None, help="directory to keep backups in (default: env/data)"
    )
    backup.add_argument("--keep", type=int, default=3, help="newest backups to keep")

    restore = commands.add_parser("restore", help="verify and restore a snapshot")
    restore.add_argument("--backup", required=True, help="backup file to restore")
    restore.add_argument("--target", required=True, help="file to write the snapshot to")

    smoke = commands.add_parser(
        "smoke", help="check a running deployment's health and metrics"
    )
    smoke.add_argument("--base-url", required=True, help="engine base URL")
    smoke.add_argument(
        "--expect-public",
        action="store_true",
        default=None,
        help="fail unless public_mode is true",
    )
    smoke.add_argument(
        "--max-age-seconds",
        type=float,
        default=None,
        help="fail when the dataset is older than this",
    )
    return parser


def _run(args: argparse.Namespace) -> dict[str, object]:
    """Dispatch one parsed command to the function that owns it."""

    if args.command == "cache":
        return cache_stats(
            Path(args.cache_dir) if args.cache_dir else paths.cache_dir()
        )
    if args.command == "retention":
        if args.max_bytes is None and args.max_age_days is None:
            raise _UsageError(
                "retention needs --max-bytes or --max-age-days; refusing to "
                "delete every eligible cache file without a stated limit"
            )
        return apply_retention(
            Path(args.cache_dir) if args.cache_dir else paths.cache_dir(),
            max_bytes=args.max_bytes,
            max_age_days=args.max_age_days,
            dry_run=bool(args.dry_run),
        )
    if args.command == "backup":
        return backup_snapshot(
            Path(args.snapshot) if args.snapshot else paths.snapshot_path(),
            Path(args.dest) if args.dest else paths.backup_dir(),
            keep=int(args.keep),
        )
    if args.command == "smoke":
        return asyncio.run(
            smoke_check(
                args.base_url,
                expect_public=args.expect_public,
                max_age_seconds=args.max_age_seconds,
            )
        )
    return restore_snapshot(Path(args.backup), Path(args.target))


def _fail(error: str, message: str) -> int:
    """Write the house error envelope to stderr and answer exit code 2."""

    print(json.dumps({"error": error, "errorMessage": message}, sort_keys=True), file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    """Run one command; a JSON document on stdout or the envelope on stderr.

    Exit 0 means the document was produced. Exit 2 is the operator's problem to
    fix — a bad flag, a limit that was not stated, a backup without a digest —
    and is always reported as ``{error, errorMessage}`` so a script never has
    to parse argparse prose to find out what happened.
    """

    parser = _build_parser()
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else list(argv))
        document = _run(args)
    except _UsageError as exc:
        return _fail("usage_error", str(exc))
    except ValueError as exc:
        return _fail("invalid_request", str(exc))
    except OSError as exc:
        # The class is kept in the message: ENOSPC and EACCES are different
        # operator stories even when both arrive as OSError.
        return _fail("io_error", f"{type(exc).__name__}: {exc}")

    print(json.dumps(document, sort_keys=True))
    if args.command == "smoke" and not document.get("ok"):
        # A smoke report that says "unhealthy" must not exit 0: cron/systemd
        # would mark the check green from the exit code alone.
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main([...])
    raise SystemExit(main())


__all__ = [
    "CacheEntry",
    "apply_retention",
    "backup_snapshot",
    "cache_stats",
    "dataset_metrics",
    "main",
    "plan_retention",
    "restore_snapshot",
    "scan_cache",
    "zero_cache_stats",
]
