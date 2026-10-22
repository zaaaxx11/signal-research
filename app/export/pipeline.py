"""Export pipeline: shards, four formats, manifest, custody (Fase 5 gate 1-3).

One call turns a row stream into an auditable bundle. The order of operations is
the contract, because every step exists to make the next one survivable:

1. **Shard first.** Rows are cut into ``rows_per_shard`` chunks and each chunk is
   staged as ``.<job>.parts/shard-NNN.jsonl`` (canonical JSON, one object per
   row). A shard is written to a temp file and renamed, so "the file exists"
   means "the shard is complete".
2. **Resume by content, not by file name.** A staged shard is reused only when
   its SHA-256 equals the hash of the shard this run would write. A shard left
   over from a different table is therefore rewritten instead of silently
   merged, and a resumed run does not touch the finished shards.
3. **Never publish a partial table.** Data files are merged from the staged
   shards only after every shard is final. A failure while sharding leaves no
   CSV, no XLSX, no manifest — only the shards that did finish, which is exactly
   what the next run needs.
4. **Coverage is checked, then promised.** ``expected_rows`` (when the caller
   knows it) and each writer's own row count must agree with the shard plan
   before the manifest is written. A manifest for an incomplete table would be a
   signed lie.
5. **Manifest last, custody always.** The manifest hashes the real bytes; the
   custody log records every step, including the failure, so the bundle can be
   reconstructed after a crash.

``content_sha256`` is the identity of the *table*: SHA-256 over
``json.dumps([cell, ...], separators=(",", ":")) + "\\n"`` in canonical column
order. It does not depend on the shard size, on the format, or on library
metadata inside the artifact bytes, so two runs that produced the same table
agree even if one of them wrote a different set of formats.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from app.export.manifest import COLUMNS_VERSION, CustodyLog, file_sha256, write_manifest
from app.export.writer import (
    FORMATS,
    XLSX_SHEET_ROW_LIMIT,
    json_cell,
    write_export,
)

#: Rows per staged shard. A shard is the unit of resume: big enough that a
#: 100k-row export is a handful of files, small enough that a retry re-does
#: minutes of work rather than hours.
SHARD_ROWS = 1_000

#: The four data formats one run produces. ``json`` (the array document) is
#: still writable by request but is not part of the default bundle: the manifest
#: is the array-shaped document, and JSONL is the line-shaped one.
DEFAULT_FORMATS: tuple[str, ...] = ("csv", "xlsx", "jsonl", "parquet")

#: Provenance block used when the caller supplies none. The runner passes the
#: real request/plan hashes, provider usage and quality counters; an export made
#: outside the worker still has to be self-describing, so the shape is frozen
#: and the values are marked as placeholders instead of being invented silently.
#:
#: The ``quality.provenance`` marker is the honest part: ``filled_ratio 0.71``
#: is not a measurement of the exported table, it is the frozen placeholder the
#: contract pins until the worker wires ``metadata`` through.
DEFAULT_METADATA: dict[str, Any] = {
    "request_hash": "sha256:" + "1" * 64,
    "plan_hash": "sha256:" + "2" * 64,
    "as_of_requested": "2026-10-22T00:00:00+00:00",
    "providers": [
        {"name": "fake", "capability": "quote", "requests": 6, "verdicts": {"ok": 6}}
    ],
    "usage": {
        "requests_total": 6,
        "bytes_total": 1843112,
        "credits_total": 0,
        "cache_hits": 2,
    },
    "quality": {
        "filled_ratio": 0.71,
        "verified_ratio": 0.93,
        "rejections_by_rule": {"V14": 3},
        "provenance": "default-placeholder",
    },
}


class ExportCoverageError(ValueError):
    """The table written is not the table that was promised (never published)."""


@dataclass(frozen=True)
class ExportReport:
    """What one export produced, in the order the caller needs it."""

    job_id: str
    files: dict[str, Path]
    manifest_path: Path
    custody_path: Path
    row_count: int
    content_sha256: str
    shards_written: tuple[int, ...] = ()
    shards_skipped: tuple[int, ...] = ()
    resumed: bool = False
    formats: tuple[str, ...] = ()
    bytes_total: int = 0


@dataclass
class _Plan:
    """The shard plan, decided while the rows are already being read."""

    shard_paths: list[tuple[int, Path, int]] = field(default_factory=list)
    written: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    digest: Any = field(default_factory=hashlib.sha256)
    as_of_min: str | None = None
    as_of_max: str | None = None

    def note_as_of(self, value: Any) -> None:
        if value is None:
            return
        text = value.isoformat() if isinstance(value, (datetime, date)) else str(value)
        if self.as_of_min is None or text < self.as_of_min:
            self.as_of_min = text
        if self.as_of_max is None or text > self.as_of_max:
            self.as_of_max = text


@dataclass(frozen=True)
class Shard:
    """One contiguous slice of the table: ``rows[start:stop]``, 0-based."""

    index: int
    start: int
    stop: int

    @property
    def row_count(self) -> int:
        return self.stop - self.start


def plan_shards(row_count: int, rows_per_shard: int) -> tuple[Shard, ...]:
    """Every shard of the table; contiguous, ordered and disjoint.

    The plan is a pure function of two integers so the resume path has one
    definition of "which rows belong to shard 3" — a re-derivation that drifts
    is how a resumed export duplicates its tail.
    """

    if (
        isinstance(rows_per_shard, bool)
        or not isinstance(rows_per_shard, int)
        or rows_per_shard < 1
    ):
        raise ValueError(f"rows_per_shard must be an int >= 1, got {rows_per_shard!r}")
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
        raise ValueError(f"row_count must be an int >= 0, got {row_count!r}")
    return tuple(
        Shard(index=index, start=start, stop=min(start + rows_per_shard, row_count))
        for index, start in enumerate(range(0, row_count, rows_per_shard))
    )


def export_job(
    rows: Iterable[Mapping[str, Any]],
    *,
    job_id: str,
    directory: str | Path,
    columns: Sequence[str],
    formats: Sequence[str] = DEFAULT_FORMATS,
    rows_per_shard: int = SHARD_ROWS,
    xlsx_sheet_rows: int = XLSX_SHEET_ROW_LIMIT,
    metadata: Mapping[str, Any] | None = None,
    resume: bool = True,
    on_shard: Callable[[int, int], None] | None = None,
    expected_rows: int | None = None,
) -> ExportReport:
    """Write the staged shards, the data files, the manifest and the custody log."""

    requested = _validate_request(
        job_id=job_id,
        formats=formats,
        rows_per_shard=rows_per_shard,
        xlsx_sheet_rows=xlsx_sheet_rows,
        expected_rows=expected_rows,
    )
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    parts = root / f".{job_id}.parts"
    parts.mkdir(parents=True, exist_ok=True)
    custody = CustodyLog(root / f"{job_id}.custody.jsonl")
    resolved = _resolve_metadata(metadata)

    custody.append(
        "export_started",
        job_id=job_id,
        formats=list(requested),
        rows_per_shard=rows_per_shard,
        resume=resume,
    )

    plan = _Plan()
    files: dict[str, Path] = {}
    counts: dict[str, int] = {}
    try:
        _stage_shards(
            rows,
            plan=plan,
            parts=parts,
            columns=columns,
            rows_per_shard=rows_per_shard,
            resume=resume,
            on_shard=on_shard,
            custody=custody,
            job_id=job_id,
        )

        row_count = sum(count for _index, _path, count in plan.shard_paths)
        if expected_rows is not None and row_count != expected_rows:
            raise ExportCoverageError(
                f"coverage refused: wrote {row_count} rows, expected {expected_rows}; "
                "no manifest was written for an incomplete table"
            )

        if plan.skipped:
            custody.append(
                "export_resumed", job_id=job_id, skipped_shards=list(plan.skipped)
            )

        for fmt in requested:
            target = root / f"{job_id}.{fmt}"
            result = write_export(
                _merged_rows(plan.shard_paths, columns),
                fmt,
                target,
                columns=columns,
                xlsx_sheet_rows=xlsx_sheet_rows,
                metadata=resolved,
            )
            if result.row_count != row_count:
                raise ExportCoverageError(
                    f"coverage refused: {fmt} holds {result.row_count} rows but the "
                    f"shard plan has {row_count}"
                )
            files[fmt] = target
            counts[fmt] = result.row_count
            custody.append(
                "format_written",
                job_id=job_id,
                name=target.name,
                sha256=result.sha256,
                bytes=result.bytes,
                row_count=result.row_count,
            )
    except Exception as exc:
        _discard_partial(root, job_id, files)
        custody.append(
            "export_failed", job_id=job_id, error=f"{type(exc).__name__}: {exc}"
        )
        raise

    manifest = _manifest_document(
        job_id=job_id,
        files=files,
        counts=counts,
        row_count=row_count,
        expected_rows=expected_rows,
        columns=columns,
        requested=requested,
        rows_per_shard=rows_per_shard,
        xlsx_sheet_rows=xlsx_sheet_rows,
        resume=resume,
        metadata=resolved,
        plan=plan,
    )
    manifest_path = write_manifest(root / f"{job_id}.manifest.json", manifest)
    custody.append(
        "manifest_written",
        job_id=job_id,
        sha256=file_sha256(manifest_path),
        bytes=manifest_path.stat().st_size,
    )
    custody.append(
        "export_completed", job_id=job_id, row_count=row_count, files_count=len(files)
    )

    return ExportReport(
        job_id=job_id,
        files=files,
        manifest_path=manifest_path,
        custody_path=custody.path,
        row_count=row_count,
        content_sha256=manifest["content_sha256"],
        shards_written=tuple(plan.written),
        shards_skipped=tuple(plan.skipped),
        resumed=bool(plan.skipped),
        formats=tuple(requested),
        bytes_total=sum(entry["bytes"] for entry in manifest["files"]),
    )


# ---------------------------------------------------------------------------
# Sharding
# ---------------------------------------------------------------------------


def _stage_shards(
    rows: Iterable[Mapping[str, Any]],
    *,
    plan: _Plan,
    parts: Path,
    columns: Sequence[str],
    rows_per_shard: int,
    resume: bool,
    on_shard: Callable[[int, int], None] | None,
    custody: CustodyLog,
    job_id: str,
) -> None:
    """Consume the row stream once, staging one shard at a time.

    ``on_shard`` fires **before** the shard is written, so a caller that uses it
    as a failure seam leaves no half-written shard behind: the shard that failed
    simply is not there, and the next run writes it.
    """

    source = iter(rows)
    index = 0
    while True:
        chunk = _take(source, rows_per_shard)
        if not chunk:
            break
        payload = bytearray()
        for row in chunk:
            payload += _canonical_object(row, columns)
            plan.digest.update(_canonical_array(row, columns))
            plan.note_as_of(row.get("as_of_achieved"))
        data = bytes(payload)
        path = parts / f"shard-{index:03d}.jsonl"
        if resume and path.exists() and file_sha256(path) == hashlib.sha256(data).hexdigest():
            plan.skipped.append(index)
        else:
            if on_shard is not None:
                on_shard(index, len(chunk))
            _atomic_write(path, data)
            custody.append(
                "shard_written", job_id=job_id, index=index, row_count=len(chunk)
            )
            plan.written.append(index)
        plan.shard_paths.append((index, path, len(chunk)))
        index += 1


def _take(iterator: Iterator[Mapping[str, Any]], count: int) -> list[Mapping[str, Any]]:
    """Pull at most ``count`` rows: the stream is read one shard at a time."""

    chunk: list[Mapping[str, Any]] = []
    for _ in range(count):
        try:
            chunk.append(next(iterator))
        except StopIteration:
            break
    return chunk


def _canonical_object(row: Mapping[str, Any], columns: Sequence[str]) -> bytes:
    record = {column: json_cell(row.get(column)) for column in columns}
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str)
    return (line + "\n").encode("utf-8")


def _canonical_array(row: Mapping[str, Any], columns: Sequence[str]) -> bytes:
    """The identity of one row: the canonical cells, independent of the format."""

    line = json.dumps(
        [json_cell(row.get(column)) for column in columns],
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return (line + "\n").encode("utf-8")


def _merged_rows(
    shards: Sequence[tuple[int, Path, int]], columns: Sequence[str]
) -> Iterator[dict[str, Any]]:
    """Re-read the staged shards in plan order; the table is the shards."""

    for _index, path, _count in shards:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                yield {column: record.get(column) for column in columns}


def _discard_partial(root: Path, job_id: str, files: Mapping[str, Path]) -> None:
    """A bundle is published by its manifest; without one, no data file stays.

    Only touched when this run actually rewrote a data file: a failure before
    the first ``write_export`` (a coverage refusal, a bad shard) must not delete
    a previous, still-valid bundle.
    """

    if not files:
        return
    for path in files.values():
        Path(path).unlink(missing_ok=True)
    (root / f"{job_id}.manifest.json").unlink(missing_ok=True)


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def _manifest_document(
    *,
    job_id: str,
    files: Mapping[str, Path],
    counts: Mapping[str, int],
    row_count: int,
    expected_rows: int | None,
    columns: Sequence[str],
    requested: Sequence[str],
    rows_per_shard: int,
    xlsx_sheet_rows: int,
    resume: bool,
    metadata: Mapping[str, Any],
    plan: _Plan,
) -> dict[str, Any]:
    entries = [
        {
            "name": path.name,
            "bytes": path.stat().st_size,
            "sha256": file_sha256(path),
            "row_count": int(counts.get(fmt, 0)),
        }
        for fmt, path in files.items()
    ]
    expected = row_count if expected_rows is None else int(expected_rows)
    return {
        "job_id": job_id,
        "created_at": datetime.now(UTC).isoformat(timespec="microseconds"),
        "columns_version": COLUMNS_VERSION,
        "columns": list(columns),
        "row_count": row_count,
        "files_count": len(entries),
        "files": entries,
        "coverage": {
            "rows_expected": expected,
            "rows_written": row_count,
            "ratio": 1.0 if row_count == expected else 0.0,
            "by_format": {fmt: int(counts.get(fmt, 0)) for fmt in requested},
        },
        "options": {
            "formats": list(requested),
            "rows_per_shard": rows_per_shard,
            "xlsx_sheet_rows": xlsx_sheet_rows,
            "resume": resume,
        },
        "content_sha256": plan.digest.hexdigest(),
        "request_hash": metadata.get("request_hash"),
        "plan_hash": metadata.get("plan_hash"),
        "as_of_requested": metadata.get("as_of_requested"),
        "as_of_achieved": (
            {"min": plan.as_of_min, "max": plan.as_of_max}
            if plan.as_of_min is not None
            else None
        ),
        "providers": list(metadata.get("providers") or []),
        "usage": dict(metadata.get("usage") or {}),
        "quality": dict(metadata.get("quality") or {}),
    }


def _resolve_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """The frozen default block, overridden by what the caller actually knows."""

    resolved = copy.deepcopy(DEFAULT_METADATA)
    if metadata:
        resolved.update(dict(metadata))
    return resolved


def _validate_request(
    *,
    job_id: str,
    formats: Sequence[str],
    rows_per_shard: int,
    xlsx_sheet_rows: int,
    expected_rows: int | None,
) -> tuple[str, ...]:
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("job_id must be a non-empty string")
    requested = tuple(formats)
    if not requested:
        raise ValueError("an export needs at least one format")
    if len(set(requested)) != len(requested):
        raise ValueError(f"duplicate export formats requested: {list(requested)}")
    unknown = [fmt for fmt in requested if fmt not in FORMATS]
    if unknown:
        raise ValueError(
            f"unknown export formats {unknown}; expected any of {', '.join(FORMATS)}"
        )
    if (
        isinstance(rows_per_shard, bool)
        or not isinstance(rows_per_shard, int)
        or rows_per_shard < 1
    ):
        raise ValueError(f"rows_per_shard must be an int >= 1, got {rows_per_shard!r}")
    if (
        isinstance(xlsx_sheet_rows, bool)
        or not isinstance(xlsx_sheet_rows, int)
        or xlsx_sheet_rows < 1
    ):
        raise ValueError(f"xlsx_sheet_rows must be an int >= 1, got {xlsx_sheet_rows!r}")
    if expected_rows is not None and (
        isinstance(expected_rows, bool)
        or not isinstance(expected_rows, int)
        or expected_rows < 0
    ):
        raise ValueError(f"expected_rows must be an int >= 0, got {expected_rows!r}")
    return requested


__all__ = [
    "DEFAULT_FORMATS",
    "DEFAULT_METADATA",
    "ExportCoverageError",
    "ExportReport",
    "SHARD_ROWS",
    "export_job",
    "plan_shards",
]
