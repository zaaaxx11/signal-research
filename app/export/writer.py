"""Streaming export writer: csv / json / jsonl / xlsx / parquet.

Rules that the tests pin down, and why each one is a rule and not a taste:

* **Header is the contract.** ``columns`` must be exactly
  ``CANONICAL_COLUMNS_V1``; a 91-column header is a broken export, not a
  flexible one. Subsets belong in ``GET /requests/{id}.preview``.
* **Atomic write.** Bytes go to ``<name>.tmp-<pid>-<rand>`` in the *same*
  directory and are ``os.replace``d only after ``fsync``. A crash leaves the old
  artifact intact instead of a half-written table.
* **Streaming.** One row is in memory at a time; ``write_export`` drains the
  same generator ``write_export_iter`` exposes, so the two can never diverge.
  The two columnar formats cannot emit a cell at a time: they own their staging
  file and finish in one atomic replace (``xlsx`` still streams through
  ``constant_memory``, one row group at a time).
* **Empty is empty.** ``None`` becomes ``""`` in CSV, ``null`` in JSON/JSONL and
  a blank cell in XLSX — never ``"N/A"``, never ``0``. A zero that means
  "missing" is a lie that survives every downstream average.
* **Formula injection.** Text starting with ``= + - @ \\t \\r`` is prefixed with
  ``'`` so a spreadsheet cannot execute a cell. Numbers are *not* prefixed: a
  negative price is data, not a formula.
* **Containers are JSON, not ``repr``.** A list/dict column is written as
  compact JSON text in CSV/XLSX and stays a real array/object in JSON/JSONL, so
  a reader never has to parse Python syntax.
* **Parquet has a declared schema.** One column per canonical name, in order;
  bool -> bool, int -> int64, int+float -> double, anything else -> string.
  The schema carries ``mee.columns_version='v1'`` so an auditor can tell which
  column contract the file was written against.
* **Re-export is byte-identical.** Text formats are deterministic by
  construction; the XLSX ``created`` property is pinned and Parquet is written
  from a schema, so the same table produces the same bytes and the manifest's
  hash is reproducible.
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
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

#: Every format this layer knows. Four of them are data files; ``json`` is the
#: array document the earlier phases already shipped. ``jsonl`` was added in
#: Fase 5 because one record per line is what a resumable shard *is*.
FORMATS: tuple[str, ...] = ("csv", "json", "xlsx", "jsonl", "parquet")

#: Formats the API advertises because the *runner* materialises them today.
#: Fase 6 wires the worker through ``app.export.pipeline.export_job``, which
#: writes every one of ``FORMATS`` plus the manifest and the custody log, so the
#: advertised tuple and the writer's tuple are now the same thing. Narrowing it
#: again would be a promise the download route cannot keep.
IMPLEMENTED_FORMATS: tuple[str, ...] = FORMATS

_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
#: A spreadsheet cell cannot hold more than 32,767 characters (02 §3.8.2).
XLSX_CELL_LIMIT = 32767
#: One worksheet holds 50.000 data rows; a bigger table is split, never truncated.
XLSX_SHEET_ROW_LIMIT = 50_000
#: Workbook ``created`` is pinned so re-exporting the same table yields the same
#: bytes. The real export time lives in the manifest and the custody log.
XLSX_PINNED_CREATED = datetime(2000, 1, 1, 0, 0, 0)

QUALITY_HEADER: tuple[str, ...] = (
    "column",
    "filled",
    "empty",
    "verified",
    "rejected",
    "disputed",
)
PROVIDERS_HEADER: tuple[str, ...] = ("provider", "capability", "requests", "verdicts")


class ExportFormatError(ValueError):
    """Unknown or malformed format: the caller asked for something wrong (422)."""

    status_code = 422


class ExportFormatPending(NotImplementedError):
    """Known format whose writer is a later phase (501, never a silent lie).

    No format is pending in Fase 5 — the writer covers all of ``FORMATS``. The
    class stays because the HTTP surface maps it to ``501`` and a caller must
    not have to change when a future format is declared before it is written.
    """

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
    xlsx_sheet_rows: int = XLSX_SHEET_ROW_LIMIT,
    metadata: Mapping[str, Any] | None = None,
) -> ExportResult:
    """Write one canonical artifact from a row iterable and return its checksum."""

    _validate(fmt, columns)
    return write_table(
        rows,
        fmt,
        path,
        columns=columns,
        xlsx_sheet_rows=xlsx_sheet_rows,
        metadata=metadata,
    )


def write_table(
    rows: Iterable[Mapping[str, Any]],
    fmt: str,
    path: str | Path,
    *,
    columns: Sequence[str],
    xlsx_sheet_rows: int = XLSX_SHEET_ROW_LIMIT,
    metadata: Mapping[str, Any] | None = None,
) -> ExportResult:
    """Write one artifact for **any** declared column list.

    ``write_export`` is this function plus the 92-column gate. The gate exists
    for the enrichment export (a 91-column header is a broken table), but the
    screen bundle legitimately ships a second, 13-column light table, and the
    file writer has no business refusing a column list the caller declared.
    Keeping one implementation means the screen's light table gets the same
    atomic write, the same cell oracles and the same hashing as the deep one —
    only the *policy* about which columns are acceptable differs.
    """

    counted = _RowCounter(rows)
    for _chunk in write_table_iter(
        counted,
        fmt,
        path,
        columns=columns,
        xlsx_sheet_rows=xlsx_sheet_rows,
        metadata=metadata,
    ):
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
    xlsx_sheet_rows: int = XLSX_SHEET_ROW_LIMIT,
    metadata: Mapping[str, Any] | None = None,
) -> Iterator[bytes]:
    """Streaming :func:`write_export`: validated columns, same writer."""

    _validate(fmt, columns)
    yield from write_table_iter(
        rows,
        fmt,
        path,
        columns=columns,
        xlsx_sheet_rows=xlsx_sheet_rows,
        metadata=metadata,
    )


def write_table_iter(
    rows: Iterable[Mapping[str, Any]],
    fmt: str,
    path: str | Path,
    *,
    columns: Sequence[str],
    xlsx_sheet_rows: int = XLSX_SHEET_ROW_LIMIT,
    metadata: Mapping[str, Any] | None = None,
) -> Iterator[bytes]:
    """Streaming variant: yields every chunk it writes, then renames the file.

    The artifact exists at ``path`` only when the iterator is exhausted; an
    abandoned iterator deletes its temporary file instead of leaving a
    plausible-looking partial export behind. The columnar formats build their
    file in one pass and yield no chunk (there is no incremental byte stream to
    hand out), but they obey the same staging-file discipline.

    Only the *format* is validated here; the column list is the caller's
    declaration (:func:`write_export_iter` is the validated entry point).
    """

    _validate_format(fmt)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "xlsx":
        _sink(target, lambda tmp: _xlsx_write(
            rows, tmp, columns, rows_per_sheet=xlsx_sheet_rows, metadata=metadata
        ))
        return
    if fmt == "parquet":
        _sink(target, lambda tmp: _parquet_write(rows, tmp, columns))
        return

    tmp = _temp_name(target)
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
# Sheet naming (pure, so the 50.000 threshold is testable without writing it)
# ---------------------------------------------------------------------------


def xlsx_sheet_names(
    total_rows: int, *, rows_per_sheet: int = XLSX_SHEET_ROW_LIMIT
) -> tuple[str, ...]:
    """The data-sheet names for ``total_rows`` at ``rows_per_sheet`` per sheet.

    ``data`` always exists (an empty table still needs a header), then
    ``data_2``, ``data_3``... A table that fits is never split "just in case".
    """

    _check_rows_per_sheet(rows_per_sheet)
    if isinstance(total_rows, bool) or not isinstance(total_rows, int) or total_rows < 0:
        raise ValueError(f"total_rows must be an int >= 0, got {total_rows!r}")

    count = max(1, -(-total_rows // rows_per_sheet))
    return ("data",) + tuple(f"data_{index}" for index in range(2, count + 1))


def _check_rows_per_sheet(rows_per_sheet: int) -> None:
    if (
        isinstance(rows_per_sheet, bool)
        or not isinstance(rows_per_sheet, int)
        or rows_per_sheet < 1
    ):
        raise ValueError(f"rows_per_sheet must be an int >= 1, got {rows_per_sheet!r}")


def _sheet_name(index: int) -> str:
    return "data" if index == 0 else f"data_{index + 1}"


# ---------------------------------------------------------------------------
# Cell oracles — the one place a value becomes a cell
# ---------------------------------------------------------------------------


def json_cell(value: Any) -> Any:
    """A date/time as ISO-8601 text; everything else keeps its JSON type."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _container_text(value: Sequence[Any] | Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _prefixed(text: str) -> str:
    return "'" + text if text[:1] in _INJECTION_PREFIXES else text


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
    if isinstance(value, (list, dict, tuple)):
        return _prefixed(_container_text(value))
    return _prefixed(str(value))


def _xlsx_cell(value: Any) -> Any:
    """A spreadsheet cell: numbers stay numbers, blanks stay blank."""

    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            # A spreadsheet cannot hold NaN; the text keeps the anomaly visible.
            return str(value)
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, dict, tuple)):
        return _prefixed(_container_text(value))
    text = str(value)
    if len(text) > XLSX_CELL_LIMIT:
        text = text[:XLSX_CELL_LIMIT]
    return _prefixed(text)


def _parquet_text(value: Any) -> str | None:
    """The string form of a value in a string-typed Parquet column."""

    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, dict, tuple)):
        return _container_text(value)
    return str(value)


def _kind(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "string"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _validate_format(fmt: str) -> None:
    if fmt not in FORMATS:
        raise ExportFormatError(
            f"unknown export format {fmt!r}; expected one of {', '.join(FORMATS)}"
        )


def _validate(fmt: str, columns: Sequence[str]) -> None:
    _validate_format(fmt)

    from app.contracts import CANONICAL_COLUMNS_V1

    if tuple(columns) != CANONICAL_COLUMNS_V1:
        raise ExportFormatError(
            "export columns must be exactly CANONICAL_COLUMNS_V1 "
            f"({len(CANONICAL_COLUMNS_V1)} names, ordered); got {len(columns)}"
        )


def _temp_name(target: Path) -> Path:
    return target.with_name(f"{target.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")


def _sink(target: Path, build: Callable[[Path], None]) -> None:
    """Build a staged file with ``build``, fsync it and swap it into place."""

    tmp = _temp_name(target)
    try:
        build(tmp)
        with open(tmp, "rb") as handle:
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def _chunks(
    rows: Iterable[Mapping[str, Any]], fmt: str, columns: Sequence[str]
) -> Iterator[bytes]:
    if fmt == "csv":
        yield from _csv_chunks(rows, columns)
    elif fmt == "json":
        yield from _json_chunks(rows, columns)
    elif fmt == "jsonl":
        yield from _jsonl_chunks(rows, columns)
    else:  # pragma: no cover - guarded by _validate
        raise ExportFormatError(f"fmt {fmt!r} has no streaming writer")


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
        record = {column: json_cell(row.get(column)) for column in columns}
        line = json.dumps(record, ensure_ascii=False, default=str)
        prefix = "" if first else ",\n"
        first = False
        yield (prefix + line).encode("utf-8")
    yield b"\n]\n"


def _jsonl_chunks(rows: Iterable[Mapping[str, Any]], columns: Sequence[str]) -> Iterator[bytes]:
    """One record per line: the format a resumable shard is made of."""

    for row in rows:
        record = {column: json_cell(row.get(column)) for column in columns}
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str)
        yield (line + "\n").encode("utf-8")


def _xlsx_write(
    rows: Iterable[Mapping[str, Any]],
    target: Path,
    columns: Sequence[str],
    *,
    rows_per_sheet: int,
    metadata: Mapping[str, Any] | None,
) -> None:
    """Write the workbook: data sheet(s), ``quality``, ``providers``.

    ``constant_memory`` keeps one worksheet row in memory, not the table, so the
    50.000-row sheet limit is a split rather than a memory budget. Every string
    is written with ``write_string`` semantics (``strings_to_formulas`` is off)
    and every injection-shaped string already carries its apostrophe, which is
    why "zero formula cells" is a property of the writer and not a promise.
    """

    import xlsxwriter

    _check_rows_per_sheet(rows_per_sheet)

    workbook = xlsxwriter.Workbook(
        str(target),
        {
            "constant_memory": True,
            "strings_to_formulas": False,
            "strings_to_numbers": False,
            "strings_to_urls": False,
            "strings_to_hyperlinks": False,
        },
    )
    try:
        workbook.set_properties({"created": XLSX_PINNED_CREATED})
        header = workbook.add_format({"bold": True})

        filled = {column: 0 for column in columns}
        row_count = 0
        sheet_index = 0
        sheet = workbook.add_worksheet(_sheet_name(sheet_index))
        sheet.write_row(0, 0, list(columns), header)
        sheet.freeze_panes(1, 0)
        in_sheet = 0

        for row in rows:
            if in_sheet == rows_per_sheet:
                sheet_index += 1
                sheet = workbook.add_worksheet(_sheet_name(sheet_index))
                sheet.write_row(0, 0, list(columns), header)
                sheet.freeze_panes(1, 0)
                in_sheet = 0
            values: list[Any] = []
            for column in columns:
                value = row.get(column)
                if value is not None:
                    filled[column] += 1
                values.append(_xlsx_cell(value))
            _xlsx_write_row(sheet, in_sheet + 1, values)
            in_sheet += 1
            row_count += 1

        _xlsx_quality_sheet(workbook, columns, filled, row_count, header, metadata)
        _xlsx_providers_sheet(workbook, header, metadata)
    finally:
        workbook.close()


def _xlsx_write_row(sheet: Any, row: int, values: Sequence[Any]) -> None:
    """Write one row cell by cell.

    ``write_row`` would route an empty string through ``write_blank`` and turn a
    present ``""`` into a missing cell. A cell that exists and is empty is data
    (the table says "this column is empty for this row"), so each type gets its
    explicit writer and a blank is written only for ``None``.
    """

    for column, value in enumerate(values):
        if value is None:
            sheet.write_blank(row, column, None)
        elif isinstance(value, bool):
            sheet.write_boolean(row, column, value)
        elif isinstance(value, (int, float)):
            sheet.write_number(row, column, value)
        else:
            sheet.write_string(row, column, value)


def _xlsx_quality_sheet(
    workbook: Any,
    columns: Sequence[str],
    filled: Mapping[str, int],
    row_count: int,
    header: Any,
    metadata: Mapping[str, Any] | None,
) -> None:
    """``filled + empty == row_count`` for every canonical column.

    ``filled``/``empty`` are counted from the rows this run actually wrote;
    verification counters come from the runner's ``quality.by_column`` block
    when it is present. Without that block ``verified`` mirrors ``filled``
    (only verified cells reach the table — Phase 4 drops the rest) and
    ``rejected``/``disputed`` are 0 because a per-column count was never
    computed, not because nothing was rejected.
    """

    blocks: Mapping[str, Mapping[str, Any]] = {}
    if metadata:
        quality = metadata.get("quality") or {}
        if isinstance(quality, Mapping):
            candidate = quality.get("by_column") or {}
            if isinstance(candidate, Mapping):
                blocks = candidate

    sheet = workbook.add_worksheet("quality")
    sheet.write_row(0, 0, list(QUALITY_HEADER), header)
    sheet.freeze_panes(1, 0)
    for index, column in enumerate(columns):
        block = blocks.get(column) or {}
        present = int(filled.get(column, 0))
        sheet.write_row(
            index + 1,
            0,
            [
                column,
                present,
                row_count - present,
                int(block.get("verified", present)),
                int(block.get("rejected", 0)),
                int(block.get("disputed", 0)),
            ],
        )


def _xlsx_providers_sheet(
    workbook: Any, header: Any, metadata: Mapping[str, Any] | None
) -> None:
    """One row per provider the run used; ``verdicts`` is JSON, not ``repr``."""

    providers = (metadata or {}).get("providers") or []
    sheet = workbook.add_worksheet("providers")
    sheet.write_row(0, 0, list(PROVIDERS_HEADER), header)
    sheet.freeze_panes(1, 0)
    for index, entry in enumerate(providers):
        if not isinstance(entry, Mapping):
            continue
        sheet.write_row(
            index + 1,
            0,
            [
                _prefixed(str(entry.get("name") or "")),
                _prefixed(str(entry.get("capability") or "")),
                int(entry.get("requests") or 0),
                json.dumps(entry.get("verdicts") or {}, ensure_ascii=False, separators=(",", ":")),
            ],
        )


def _arrow_type(kinds: set[str]) -> Any:
    """The declared Parquet type for a column, from the source kinds it held."""

    import pyarrow as pa

    if not kinds:
        return pa.string()
    if kinds == {"bool"}:
        return pa.bool_()
    if kinds <= {"int"}:
        return pa.int64()
    if kinds <= {"int", "float"}:
        return pa.float64()
    return pa.string()


def _parquet_value(value: Any, typ: Any) -> Any:
    import pyarrow as pa

    if value is None:
        return None
    if pa.types.is_boolean(typ):
        return bool(value)
    if pa.types.is_integer(typ):
        return int(value)
    if pa.types.is_floating(typ):
        return float(value)
    return _parquet_text(value)


def _parquet_write(
    rows: Iterable[Mapping[str, Any]], target: Path, columns: Sequence[str]
) -> None:
    """Write a Parquet file with an explicit, stable schema.

    The type of each column is decided from the values it holds, so ``bool``
    stays boolean, a mix of ``int`` and ``float`` becomes ``double`` and
    anything heterogeneous (including all-null) is a string column. A null
    column declared ``string`` is not a ``null`` type: a reader that gets
    ``null`` cannot union it with the next file.
    """

    import pyarrow as pa
    import pyarrow.parquet as pq

    values: dict[str, list[Any]] = {column: [] for column in columns}
    kinds: dict[str, set[str]] = {column: set() for column in columns}
    for row in rows:
        for column in columns:
            value = row.get(column)
            values[column].append(value)
            kind = _kind(value)
            if kind is not None:
                kinds[column].add(kind)

    fields = [pa.field(column, _arrow_type(kinds[column])) for column in columns]
    schema = pa.schema(fields, metadata={b"mee.columns_version": b"v1"})
    arrays = [
        pa.array([_parquet_value(value, field.type) for value in values[field.name]], type=field.type)
        for field in fields
    ]
    table = pa.Table.from_arrays(arrays, schema=schema)
    pq.write_table(table, str(target))


__all__ = [
    "FORMATS",
    "IMPLEMENTED_FORMATS",
    "PROVIDERS_HEADER",
    "QUALITY_HEADER",
    "XLSX_CELL_LIMIT",
    "XLSX_PINNED_CREATED",
    "XLSX_SHEET_ROW_LIMIT",
    "ExportFormatError",
    "ExportFormatPending",
    "ExportResult",
    "file_sha256",
    "json_cell",
    "write_export",
    "write_export_iter",
    "write_table",
    "write_table_iter",
    "xlsx_sheet_names",
]
