"""Row-level quality accounting (02 §3.6/§3.7, Phase-0 approximation).

Three numbers decide whether a row is honest:

* ``filled_columns_count`` — how many of the 92 cells actually hold a value;
* ``verified_columns_count`` — how many of those passed a verifier rule;
* ``data_quality`` — a closed enum describing *coverage*, not quality.

Phase 0 has no verifier (V1..V16 arrive in Phase 4), so
``verified_columns_count`` is reported as ``0`` for every row. Reporting
"filled == verified" — which the fake fixture happens to claim — would be a
fabricated verification claim, and that is precisely the class of lie this
project exists to avoid.

``data_quality`` here is a coverage classifier, not a quality score:

* ``identity_only`` — identity/classification only;
* ``quote_only`` — identity + a quote, nothing deeper;
* ``partial`` — a quote *and* something deeper, or deeper data without a quote;
* ``complete`` — every non-derived canonical column is filled.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from app.contracts import (
    CANONICAL_COLUMNS_V1,
    CORE_COLUMNS,
    DATA_QUALITY_VALUES,
    DERIVED_QUALITY_COLUMNS,
    columns_in_group,
)

#: Columns whose emptiness is a fact about the data, not a missing computation.
_DATA_COLUMNS: tuple[str, ...] = tuple(
    column for column in CANONICAL_COLUMNS_V1 if column not in DERIVED_QUALITY_COLUMNS
)

_DEEP_GROUPS = ("EOD & technical", "Performance", "Fundamentals", "Statements", "Options")
_DEEP_COLUMNS: frozenset[str] = frozenset(
    column for group in _DEEP_GROUPS for column in columns_in_group(group)
)
_QUOTE_COLUMNS: tuple[str, ...] = columns_in_group("Quote")

#: A classifier that can return a string outside the enum is a contract bug.
if not {"complete", "quote_only", "identity_only", "partial"} <= set(DATA_QUALITY_VALUES):
    raise RuntimeError(f"data_quality enum changed: {DATA_QUALITY_VALUES}")


def is_filled(value: Any) -> bool:
    """``None`` and blank strings are empty; ``0`` and ``False`` are values."""

    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def missing_core_columns(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the core columns a row lacks; such a row must not be exported."""

    return tuple(column for column in sorted(CORE_COLUMNS) if not is_filled(row.get(column)))


def filled_columns(row: Mapping[str, Any]) -> int:
    """Count non-empty canonical data columns (derived quality columns excluded)."""

    return sum(1 for column in _DATA_COLUMNS if is_filled(row.get(column)))


def classify(row: Mapping[str, Any]) -> str:
    """Map a row to one of :data:`app.contracts.DATA_QUALITY_VALUES`."""

    filled = filled_columns(row)
    if filled >= len(_DATA_COLUMNS):
        return "complete"
    has_quote = any(is_filled(row.get(column)) for column in _QUOTE_COLUMNS)
    has_deep = any(is_filled(row.get(column)) for column in _DEEP_COLUMNS)
    if has_quote and not has_deep:
        return "quote_only"
    if not has_quote and not has_deep:
        return "identity_only"
    return "partial"


def finalize_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Keep canonical keys only and (re)compute the three derived columns.

    Dropping non-canonical keys is what keeps provenance and provider debug
    fields out of column 93 of the export.
    """

    cleaned = {column: row.get(column) for column in CANONICAL_COLUMNS_V1 if column in row}
    cleaned["filled_columns_count"] = filled_columns(cleaned)
    # Phase 0: no verifier has run, so nothing is verified. Not a placeholder 0
    # to be "fixed later" silently — Phase 4 must raise this number.
    cleaned["verified_columns_count"] = 0
    cleaned["data_quality"] = classify(cleaned)
    return cleaned


def aggregate(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Coverage + per-column counts for ``GET /requests/{id}``."""

    materialised = list(rows)
    count = len(materialised)
    by_column: dict[str, dict[str, int]] = {}
    for column in CANONICAL_COLUMNS_V1:
        filled = sum(1 for row in materialised if is_filled(row.get(column)))
        by_column[column] = {
            "filled": filled,
            "verified": 0,
            "rejected": 0,
            "disputed": 0,
            "empty": count - filled,
        }

    filled_total = sum(entry["filled"] for entry in by_column.values())
    capacity = count * len(CANONICAL_COLUMNS_V1)
    return {
        "row_count": count,
        "coverage": {
            "filled_ratio": round(filled_total / capacity, 6) if capacity else 0.0,
            "verified_ratio": 0.0,
        },
        "quality": {"by_column": by_column},
        "rows_by_quality": {
            value: sum(1 for row in materialised if row.get("data_quality") == value)
            for value in DATA_QUALITY_VALUES
        },
    }


__all__ = [
    "aggregate",
    "classify",
    "filled_columns",
    "finalize_row",
    "is_filled",
    "missing_core_columns",
]
