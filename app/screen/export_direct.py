"""The screener's one-hop export: a POST body in, the file out.

The job-shaped export (:mod:`app.screen.export` + ``POST
/screens/{id}/export``) writes two scopes to disk and answers a 202, which is
the right shape for a bundle nobody is waiting on. A reader who clicks Export
on a 200-row screen is waiting: this module answers that reader, synchronously,
with the bytes and nothing else.

Four decisions are worth stating, because each one is a promise a client can
observe:

* **One cap, one word for it.** :data:`MAX_ROWS` = 200. A limit over it is a
  422 named ``export_limit_exceeded`` that says the cap in its message; an
  omitted (or ``0``) limit means the cap, never "everything". There is no
  minimum beyond zero because a caller asking for zero asked for the default,
  and no page of 0 rows is a file anyone wants.
* **The files are the server's.** Rows come from the same pipeline
  ``POST /screens/select`` runs (:func:`app.api.screens._current_universe`), the
  same filters, the same column resolver and the same symbol resolver. The
  client names what it wants and downloads what it is given — it never
  serialises a row. That is what keeps a label (`NASDAQ`) out of a file whose
  contract says a code (`XNAS`).
* **Missing is blank.** ``None`` is an empty CSV cell, a blank XLSX cell and
  JSON ``null``; it is never ``0`` and never ``"N/A"``. A zero that means
  "unknown" survives every downstream average as a lie.
* **A spreadsheet is not a program.** Text that begins with ``= + - @`` (or a
  tab/CR) is prefixed with ``'`` in CSV and XLSX so Excel cannot execute a cell;
  numbers are left alone, because a negative price is data.

Formats are the three a reader can open: ``csv``, ``xlsx`` and ``json``. XLSX is
here because ``xlsxwriter`` is already a runtime dependency (:mod:`app.export.writer`
uses it); a format that needed a new package would not be.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from app.screen.selection import (
    SelectionError,
    SelectionRequest,
    _column_row,
    _columns,
    _resolved,
)
from app.screen.snapshot import SOURCE, display_row
from app.screen.universe import REGIONS

#: The export cap. A screen is a page a reader is reading, not a data dump; 200
#: rows is ten panels of twenty, and the whole file fits in one response.
MAX_ROWS = 200

#: The three file formats, in the menu's order.
FORMATS: tuple[str, ...] = ("csv", "xlsx", "json")

#: The two ways to name a population.
MODES: tuple[str, ...] = ("criteria", "symbols")

MEDIA_TYPES: Mapping[str, str] = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
}

EXTENSIONS: Mapping[str, str] = {"csv": "csv", "xlsx": "xlsx", "json": "json"}

#: The columns of a row that names no projection: :func:`display_row`'s keys, in
#: its order. Used only when there are no rows to read a header from.
DEFAULT_COLUMNS: tuple[str, ...] = (
    "rank",
    "symbol",
    "name",
    "exchange",
    "country",
    "sector",
    "price",
    "change_pct",
    "volume",
    "turnover",
    "turnover_usd",
    "market_cap_usd",
    "is_primary",
    "as_of",
)

_BODY_KEYS: frozenset[str] = frozenset(
    {"mode", "criteria", "symbols", "columns", "format", "limit", "region", "narrow", "signal"}
)
_CRITERIA_KEYS: frozenset[str] = frozenset({"filters", "sort", "view"})

#: Text a spreadsheet would treat as a formula. Prefixing it with ``'`` keeps
#: the cell text; the character is not part of the value a reader copies.
_INJECTION_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")


class ExportError(ValueError):
    """A body the route refuses, carrying the code the envelope publishes."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class NarrowSpec:
    """Narrow filters the client sends for page/selected scopes."""

    filters: tuple[dict[str, Any], ...]
    signal: str | None


@dataclass(frozen=True, slots=True)
class ExportSpec:
    """A validated export body: what to write, in what shape, how many rows."""

    mode: str
    format: str
    limit: int
    region: str
    criteria: Mapping[str, Any] | None
    symbols: tuple[str, ...]
    columns: tuple[str, ...]
    narrow: NarrowSpec | None
    signal: str | None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _limit(raw: Any) -> int:
    """The row limit: ``0``/absent is the cap, a negative or fractional is a 422.

    ``201`` is refused rather than clamped **by name**, because a caller who
    asked for 500 rows and silently received 200 has been told the file is
    complete when it is a prefix. The client clamps its own input; the API
    states its ceiling.
    """

    if raw is None:
        return MAX_ROWS
    if isinstance(raw, bool) or not isinstance(raw, int):
        if isinstance(raw, float) and raw.is_integer():
            raw = int(raw)
        else:
            raise ExportError(
                f"'limit' must be a whole number of rows, got {raw!r}", code="invalid_limit"
            )
    if raw < 0:
        raise ExportError(
            f"'limit' cannot be negative, got {raw}", code="invalid_limit"
        )
    if raw == 0:
        return MAX_ROWS
    if raw > MAX_ROWS:
        raise ExportError(
            f"limit {raw} exceeds the export cap of {MAX_ROWS} rows; the export "
            f"writes at most {MAX_ROWS} rows per file",
            code="export_limit_exceeded",
        )
    return raw


def _symbols(raw: Any) -> tuple[str, ...]:
    """A non-empty ticker list, each ticker once, in the caller's order."""

    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)) or not raw:
        raise ExportError(
            "mode 'symbols' needs a non-empty 'symbols' array of ticker strings",
            code="invalid_symbols",
        )
    ordered: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, str) or not item.strip():
            raise ExportError(
                f"'symbols[{index}]' must be a non-blank string, got {item!r}",
                code="invalid_symbols",
            )
        symbol = item.strip().upper()
        if symbol in seen:
            raise ExportError(
                f"'symbols' repeats {symbol!r}; every ticker once", code="invalid_symbols"
            )
        seen.add(symbol)
        ordered.append(symbol)
    return tuple(ordered)


def _columns_of(raw: Any) -> tuple[str, ...]:
    try:
        return _columns(raw)
    except SelectionError as exc:
        raise ExportError(str(exc), code="invalid_columns") from exc


def _optional_string(raw: Any) -> str | None:
    """A string the caller may have sent, or None when absent or blank."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    trimmed = raw.strip()
    return trimmed if len(trimmed) > 0 else None


def _read_narrow(raw: Any) -> NarrowSpec | None:
    """Validate the narrow block, or None when absent."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ExportError(
            "narrow must be an object with a 'filters' array",
            code="invalid_narrow",
        )
    raw_filters = raw.get("filters")
    if raw_filters is None:
        raise ExportError(
            "narrow must include a 'filters' array (empty is ok)",
            code="invalid_narrow",
        )
    if not isinstance(raw_filters, (list, tuple)):
        raise ExportError(
            "narrow.filters must be an array",
            code="invalid_narrow",
        )
    signal = _optional_string(raw.get("signal"))
    return NarrowSpec(
        filters=tuple(raw_filters),
        signal=signal,
    )


def parse_export(payload: Any) -> ExportSpec:
    """Validate an export body; every refusal carries its own code."""

    if not isinstance(payload, Mapping):
        raise ExportError(
            "the export body must be a JSON object", code="invalid_export_body"
        )
    extra = sorted(set(payload) - _BODY_KEYS)
    if extra:
        raise ExportError(
            f"unknown export knob(s) {extra}; expected {sorted(_BODY_KEYS)}",
            code="invalid_export_body",
        )

    mode = payload.get("mode")
    if mode not in MODES:
        raise ExportError(
            f"unknown mode {mode!r}; expected one of {', '.join(MODES)}", code="invalid_mode"
        )

    fmt = payload.get("format")
    if fmt not in FORMATS:
        raise ExportError(
            f"unknown export format {fmt!r}; expected one of {', '.join(FORMATS)}",
            code="unknown_format",
        )

    limit = _limit(payload.get("limit"))

    region = payload.get("region", "us")
    if region not in REGIONS:
        raise ExportError(
            f"unknown region {region!r}; expected one of {', '.join(REGIONS)}",
            code="invalid_region",
        )

    columns = _columns_of(payload.get("columns"))

    signal = _optional_string(payload.get("signal"))
    narrow = _read_narrow(payload.get("narrow"))

    if mode == "criteria":
        if "symbols" in payload:
            raise ExportError(
                "mode 'criteria' exports a query and does not take 'symbols'",
                code="invalid_export_body",
            )
        if "narrow" in payload:
            raise ExportError(
                "mode 'criteria' merges narrow into criteria on the client; "
                "send narrow dimensions as extra filters in criteria and signal at the top level",
                code="invalid_export_body",
            )
        criteria = payload.get("criteria")
        if not isinstance(criteria, Mapping):
            raise ExportError(
                "mode 'criteria' needs a 'criteria' object with filters and sort",
                code="invalid_criteria",
            )
        unknown = sorted(set(criteria) - _CRITERIA_KEYS)
        if unknown:
            raise ExportError(
                f"unknown 'criteria' knob(s) {unknown}; expected {sorted(_CRITERIA_KEYS)}",
                code="invalid_criteria",
            )
        return ExportSpec(
            mode="criteria",
            format=str(fmt),
            limit=limit,
            region=str(region),
            criteria=dict(criteria),
            symbols=(),
            columns=columns,
            narrow=None,
            signal=signal,
        )

    if "criteria" in payload:
        raise ExportError(
            "mode 'symbols' exports a ticker list and does not take 'criteria'",
            code="invalid_export_body",
        )
    if "signal" in payload:
        raise ExportError(
            "mode 'symbols' sends its signal inside the narrow block; remove the top-level 'signal' key",
            code="invalid_export_body",
        )
    return ExportSpec(
        mode="symbols",
        format=str(fmt),
        limit=limit,
        region=str(region),
        criteria=None,
        symbols=_symbols(payload.get("symbols")),
        columns=columns,
        narrow=narrow,
        signal=None,
    )


# ---------------------------------------------------------------------------
# The selection the export runs
# ---------------------------------------------------------------------------


def selection_body(spec: ExportSpec) -> dict[str, Any]:
    """The ``POST /screens/select`` body a criteria export is validated as.

    ``criteria.view`` lives at the top level there (the request contract puts
    the output shape beside ``columns``), so it is moved rather than copied:
    one body, one meaning. ``limit`` is capped to the panel's own ceiling for
    validation only — the export then asks the resolver for its own cap, which
    is larger than a panel and is the number this route promised.
    """

    assert spec.criteria is not None
    block = dict(spec.criteria)
    body: dict[str, Any] = {
        "mode": "criteria",
        "region": spec.region,
        "criteria": {
            "filters": block.get("filters") or [],
            # Validation only; the caller's export limit is applied below.
            "limit": 1,
        },
        "columns": list(spec.columns) if spec.columns else None,
    }
    if block.get("sort") is not None:
        body["criteria"]["sort"] = block["sort"]
    if block.get("view") is not None:
        body["view"] = block["view"]
    if not spec.columns:
        del body["columns"]
    return body


def symbols_request(spec: ExportSpec, *, asset_class: str) -> SelectionRequest:
    """The request a ticker export runs: the caller's list, their order.

    ``snapshot_id`` is empty because no snapshot is involved: the tickers are
    resolved against the live universe the same way a symbols selection resolves
    them, and the file dates itself with its own read moment.
    """

    return SelectionRequest(
        mode="symbols",
        region=None,
        filters=(),
        sort=None,
        limit=spec.limit,
        symbols=spec.symbols,
        source=SOURCE,
        snapshot_id=None,
        asset_class=asset_class,
        columns=spec.columns,
    )


def resolve_symbols(
    rows: Sequence[dict[str, Any]], request: SelectionRequest
) -> tuple[list[dict[str, Any]], int, tuple[str, ...]]:
    """The symbol resolver's answer: rows in request order, total, missing."""

    return _resolved(rows, request)


def project(
    rows: Sequence[Mapping[str, Any]], columns: Sequence[str], *, as_of: str
) -> list[dict[str, Any]]:
    """Shape resolved rows for a file: the projection asked for, or the display row.

    The two resolvers are the selection route's own, so an exported cell and a
    rendered cell cannot disagree — including the ten computed columns, which
    have a public key and a different row field.
    """

    if columns:
        return [_column_row(row, columns, as_of=as_of) for row in rows]
    return [display_row(row, as_of=as_of) for row in rows]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def header_for(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> list[str]:
    """The file's header: the rows' own keys, or the projection when there are none."""

    if rows:
        return list(rows[0])
    if columns:
        return ["symbol", *columns, "as_of"]
    return list(DEFAULT_COLUMNS)


def _text(value: Any) -> str:
    """One CSV/XLSX cell: blank for missing, JSON for a container, guarded text."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    text = str(value)
    if text.startswith(_INJECTION_PREFIXES):
        return "'" + text
    return text


def _csv(rows: Sequence[Mapping[str, Any]], header: Sequence[str]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([_text(row.get(name)) for name in header])
    return buffer.getvalue().encode("utf-8")


def _json(
    rows: Sequence[Mapping[str, Any]],
    header: Sequence[str],
    *,
    mode: str,
    region: str,
    as_of: str,
    total: int,
    missing: Sequence[str],
) -> bytes:
    document = {
        "mode": mode,
        "region": region,
        "source": SOURCE,
        "as_of": as_of,
        "total": total,
        "row_count": len(rows),
        "missing": list(missing),
        "columns": list(header),
        "rows": [dict(row) for row in rows],
    }
    return json.dumps(
        document, ensure_ascii=False, allow_nan=False, separators=(",", ":"), default=str
    ).encode("utf-8")


def _xlsx(rows: Sequence[Mapping[str, Any]], header: Sequence[str]) -> bytes:
    """A one-sheet workbook, written in memory (`xlsxwriter` is a runtime dep)."""

    import xlsxwriter

    buffer = io.BytesIO()
    workbook = xlsxwriter.Workbook(
        buffer,
        {
            "in_memory": True,
            # A cell is data: never a formula, never a hyperlink.
            "strings_to_formulas": False,
            "strings_to_urls": False,
        },
    )
    sheet = workbook.add_worksheet("screen")
    bold = workbook.add_format({"bold": True})
    for column, name in enumerate(header):
        sheet.write_string(0, column, str(name), bold)
    for index, row in enumerate(rows, start=1):
        for column, name in enumerate(header):
            value = row.get(name)
            if value is None:
                continue  # a blank cell, never a zero
            if isinstance(value, bool):
                sheet.write_boolean(index, column, value)
            elif isinstance(value, (int, float)):
                sheet.write_number(index, column, value)
            else:
                sheet.write_string(index, column, _text(value))
    if rows:
        sheet.autofilter(0, 0, len(rows), max(0, len(header) - 1))
    sheet.freeze_panes(1, 0)
    workbook.close()
    return buffer.getvalue()


def render(
    spec: ExportSpec,
    rows: Sequence[Mapping[str, Any]],
    *,
    as_of: str,
    total: int,
    missing: Sequence[str] = (),
) -> bytes:
    """The answer's body, in the format the caller named."""

    header = header_for(rows, spec.columns)
    if spec.format == "csv":
        return _csv(rows, header)
    if spec.format == "json":
        return _json(
            rows,
            header,
            mode=spec.mode,
            region=spec.region,
            as_of=as_of,
            total=total,
            missing=missing,
        )
    return _xlsx(rows, header)


def filename(spec: ExportSpec, *, as_of: str) -> str:
    """``ztockz-screen-20261102-153012.csv`` — the reader's copy, dated."""

    moment = datetime.fromisoformat(as_of).strftime("%Y%m%d-%H%M%S")
    return f"ztockz-{spec.mode}-{moment}.{EXTENSIONS[spec.format]}"


def media_type(spec: ExportSpec) -> str:
    return MEDIA_TYPES[spec.format]


__all__ = [
    "DEFAULT_COLUMNS",
    "ExportError",
    "ExportSpec",
    "EXTENSIONS",
    "FORMATS",
    "MAX_ROWS",
    "MEDIA_TYPES",
    "filename",
    "header_for",
    "media_type",
    "parse_export",
    "project",
    "render",
    "resolve_symbols",
    "selection_body",
    "symbols_request",
]
