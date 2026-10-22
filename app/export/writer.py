"""Streaming export writer: csv / json (xlsx, parquet land in Phase 5).

Rules that the tests pin down, and why each one is a rule and not a taste:

* **Header is the contract.** ``columns`` must be exactly
  ``CANONICAL_COLUMNS_V1``; a 91-column header is a broken export, not a
  flexible one. Subsets belong in ``GET /requests/{id}.preview``.
* **Atomic write.** Bytes go to ``<name>.tmp-<pid>-<rand>`` in the *same*
  directory and are ``os.replace``d only after ``fsync``. A crash leaves the old
  artifact intact instead of a half-written table.
* **Streaming.** One row is in memory at a time; ``write_export`` drains the
  same generator ``write_export_iter`` exposes, so the two can never diverge.
* **Empty is empty.** ``None`` becomes ``""`` in CSV and ``null`` in JSON —
  never ``"N/A"``, never ``0``. A zero that means "missing" is a lie that
  survives every downstream average.
* **Formula injection.** Text starting with ``= + - @ \\t \\r`` is prefixed with
  ``'`` so a spreadsheet cannot execute a cell. Numbers are *not* prefixed: a
  negative price is data, not a formula.
* **Provenance never becomes column 93.** Only ``columns`` are written.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

FORMATS: tuple[str, ...] = ("csv", "json", "xlsx", "parquet")
#: Formats with a writer today. The other two are declared in the contract and
#: answer 501 (``ExportFormatPending``) instead of pretending to succeed.
IMPLEMENTED_FORMATS: tuple[str, ...] = ("csv", "json")

_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
#: A spreadsheet cell cannot hold more than 32,767 characters (02 §3.8.2).
XLSX_CELL_LIMIT = 32767


class ExportFormatError(ValueError):
    """Unknown or malformed format: the caller asked for something wrong (422)."""

    status_code = 422


class ExportFormatPending(NotImplementedError):
    """Known format whose writer is a later phase (501, never a silent lie)."""

    status_code = 501


@dataclass(frozen=True)
class ExportResult:
    path: Path
    fmt: str
    row_count: int
    bytes: int
    sha256: str


class _RowCounter:
    """Pass-through iterator that remembers how many rows it yielded."""

    def __init__(self, rows: Iterable[Mapping[str, Any]]) -> None:
        self._rows = rows
        self.count = 0

    def __iter__(self) -> Iterator[Mapping[str, Any]]:
        for row in self._rows:
            self.count += 1
            yield row


def write_export(
    rows: Iterable[Mapping[str, Any]],
    fmt: str,
    path: str | Path,
    *,
    columns: Sequence[str],
) -> ExportResult:
    """Write one artifact from a row iterable and return its checksum."""

    counted = _RowCounter(rows)
    for _chunk in write_export_iter(counted, fmt, path, columns=columns):
        pass

    target = Path(path)
    return ExportResult(
        path=target,
        fmt=fmt,
        row_count=counted.count,
        bytes=target.stat().st_size,
        sha256=file_sha256(target),
    )


def write_export_iter(
    rows: Iterable[Mapping[str, Any]],
    fmt: str,
    path: str | Path,
    *,
    columns: Sequence[str],
) -> Iterator[bytes]:
    """Streaming variant: yields every chunk it writes, then renames the file.

    The artifact exists at ``path`` only when the iterator is exhausted; an
    abandoned iterator deletes its temporary file instead of leaving a
    plausible-looking partial export behind.
    """

    _validate(fmt, columns)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")

    handle = open(tmp, "wb")
    completed = False
    try:
        for chunk in _chunks(rows, fmt, columns):
            handle.write(chunk)
            yield chunk
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(tmp, target)
        completed = True
    finally:
        if not handle.closed:
            handle.close()
        if not completed:
            tmp.unlink(missing_ok=True)


def file_sha256(path: str | Path, *, chunk_size: int = 1 << 20) -> str:
    """Hash a file without reading it into memory."""

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _validate(fmt: str, columns: Sequence[str]) -> None:
    if fmt not in FORMATS:
        raise ExportFormatError(f"unknown export format {fmt!r}; expected one of {', '.join(FORMATS)}")
    if fmt not in IMPLEMENTED_FORMATS:
        raise ExportFormatPending(
            f"export format {fmt!r} is declared in the contract but its writer is a later phase"
        )

    from app.contracts import CANONICAL_COLUMNS_V1

    if tuple(columns) != CANONICAL_COLUMNS_V1:
        raise ExportFormatError(
            "export columns must be exactly CANONICAL_COLUMNS_V1 "
            f"({len(CANONICAL_COLUMNS_V1)} names, ordered); got {len(columns)}"
        )


def _chunks(rows: Iterable[Mapping[str, Any]], fmt: str, columns: Sequence[str]) -> Iterator[bytes]:
    if fmt == "csv":
        yield from _csv_chunks(rows, columns)
    elif fmt == "json":
        yield from _json_chunks(rows, columns)
    else:  # pragma: no cover - guarded by _validate
        raise ExportFormatPending(fmt)


def _csv_chunks(rows: Iterable[Mapping[str, Any]], columns: Sequence[str]) -> Iterator[bytes]:
    yield _csv_line(columns)
    for row in rows:
        yield _csv_line([_cell(row.get(column)) for column in columns])


def _csv_line(cells: Sequence[str]) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerow(cells)
    return buffer.getvalue().encode("utf-8")


def _json_chunks(rows: Iterable[Mapping[str, Any]], columns: Sequence[str]) -> Iterator[bytes]:
    yield b"[\n"
    first = True
    for row in rows:
        record = {column: _json_value(row.get(column)) for column in columns}
        line = json.dumps(record, ensure_ascii=False, default=str)
        prefix = "" if first else ",\n"
        first = False
        yield (prefix + line).encode("utf-8")
    yield b"\n]\n"


def _json_value(value: Any) -> Any:
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        # JSON has no NaN/Infinity literal; a string keeps the anomaly visible.
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _cell(value: Any) -> str:
    """Format one CSV cell. ``None`` is the empty string, never a sentinel."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    text = str(value)
    if text[:1] in _INJECTION_PREFIXES:
        return "'" + text
    return text


__all__ = [
    "FORMATS",
    "IMPLEMENTED_FORMATS",
    "XLSX_CELL_LIMIT",
    "ExportFormatError",
    "ExportFormatPending",
    "ExportResult",
    "file_sha256",
    "write_export",
    "write_export_iter",
]
