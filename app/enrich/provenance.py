"""Per-cell provenance (02 §3.5, §4.6, §3.8.2).

Every value in the table was observed somewhere, at some time, by somebody. That
sentence is the whole contract of this module, and it is why provenance is a
**deliverable** rather than debug output: two vendors disagree about a price, and
the only way a reader can tell which one they are looking at is to carry the
source beside the cell.

The rules:

* **Exact, not approximate.** ``set(provenance(row)) == filled_cells(row)``. A
  filled cell without a record has an unknown origin; a record without a cell
  describes a value the row does not carry. Both fail the audit, so the test
  asserts equality, never a subset.
* **Outside the table.** ``PROVENANCE_KEY`` is not one of the 92 canonical
  columns. Column 93 would turn a table that pandas can read into a nested
  document, so provenance lives under one non-canonical key that the export
  writer (which writes ``columns`` only) can never emit.
* **No time, no entry.** ``as_of`` is the time of the **data**, never the time of
  the fetch, and it is mandatory. ``fetch`` time is not a substitute.
* **A closed status vocabulary.** ``{ok, partial, stale, disputed, rejected,
  estimated}`` — a free string would make the sidecar unqueryable.

The derived quality columns (``data_quality``, ``filled_columns_count``,
``verified_columns_count``) are computed by the engine, not observed from a
provider, so they are excluded from ``filled_cells`` by construction.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Mapping

from app.contracts import CANONICAL_COLUMNS_V1, DERIVED_QUALITY_COLUMNS
from app.enrich.quality import is_filled

#: Row key holding the provenance mapping. Deliberately not a canonical column.
PROVENANCE_KEY = "_provenance"

#: Statuses a provenance record may carry (02 §3.5).
PROVENANCE_STATUSES: frozenset[str] = frozenset(
    {"ok", "partial", "stale", "disputed", "rejected", "estimated"}
)

#: Keys every record must expose (02 §4.6 + the verified tie-break of §3.5).
PROVENANCE_FIELDS: frozenset[str] = frozenset(
    {"source", "as_of", "status", "trust", "verified"}
)

#: ``VerdictKind`` → provenance status. A verdict that says "this value is not
#: usable" maps to ``rejected`` instead of pretending to be ``ok``.
_VERDICT_STATUS: dict[str, str] = {
    "ok": "ok",
    "stale": "stale",
    "mismatch": "rejected",
    "unit_anomaly": "rejected",
    "schema_drift": "rejected",
    "not_found": "rejected",
    "empty": "partial",
    "disputed": "disputed",
}

_DATA_COLUMNS: tuple[str, ...] = tuple(
    column for column in CANONICAL_COLUMNS_V1 if column not in DERIVED_QUALITY_COLUMNS
)


def coerce_as_of(value: Any) -> str:
    """Return a timezone-aware ISO timestamp, or raise ``ValueError``.

    Naive datetimes are rejected rather than assumed to be UTC: a timestamp
    without a zone is exactly the ambiguity ``as_of`` exists to remove.
    """

    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime(value.year, value.month, value.day, tzinfo=UTC)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("as_of may not be blank")
        parsed = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            moment = datetime.fromisoformat(parsed)
        except ValueError as exc:
            raise ValueError(f"as_of is not an ISO timestamp: {value!r}") from exc
    else:
        raise ValueError(f"as_of must be a datetime or ISO string, got {type(value).__name__}")

    if moment.tzinfo is None:
        raise ValueError(f"as_of must carry a timezone, got naive {value!r}")
    return moment.isoformat()


def status_for_verdict(verdict: Any) -> str:
    """Map a provider verdict (or a provenance status) onto the status enum."""

    raw = getattr(verdict, "value", verdict)
    text = str(raw or "ok").strip().lower()
    if text in PROVENANCE_STATUSES:
        return text
    return _VERDICT_STATUS.get(text, "partial")


def make_entry(
    *,
    source: str,
    as_of: Any,
    status: Any = "ok",
    trust: int = 4,
    verified: bool = False,
    source_id: str | None = None,
    confidence: float | None = None,
) -> dict[str, Any]:
    """Build one validated provenance record.

    Validation happens here, at write time, because a record that only fails
    later (when the export sidecar is assembled) has already been counted as a
    filled cell with a bogus origin.
    """

    if not isinstance(source, str) or not source.strip():
        raise ValueError("a value without a named source is unattributable")
    if not isinstance(trust, int) or isinstance(trust, bool) or not 1 <= trust <= 4:
        raise ValueError(f"trust must be an int in 1..4 (02 §4.6), got {trust!r}")
    state = status_for_verdict(status)
    if state not in PROVENANCE_STATUSES:  # pragma: no cover - status_for_verdict is total
        raise ValueError(f"unknown provenance status {status!r}")

    entry: dict[str, Any] = {
        "source": source.strip(),
        "as_of": coerce_as_of(as_of),
        "status": state,
        "trust": trust,
        "verified": bool(verified),
    }
    if source_id is not None:
        entry["source_id"] = str(source_id)
    if confidence is not None:
        entry["confidence"] = float(confidence)
    return entry


def provenance(row: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the row's provenance mapping (``{}`` when it carries none)."""

    record = row.get(PROVENANCE_KEY) if isinstance(row, Mapping) else None
    return record if isinstance(record, dict) else {}


def set_entry(row: dict[str, Any], column: str, entry: Mapping[str, Any]) -> None:
    """Attach one record, creating the mapping on first write."""

    record = row.get(PROVENANCE_KEY)
    if not isinstance(record, dict):
        record = {}
        row[PROVENANCE_KEY] = record
    record[column] = dict(entry)


def drop_entry(row: dict[str, Any], column: str) -> None:
    """Remove one record; removes the mapping when it becomes empty."""

    record = row.get(PROVENANCE_KEY)
    if not isinstance(record, dict):
        return
    record.pop(column, None)
    if not record:
        row.pop(PROVENANCE_KEY, None)


def filled_cells(row: Mapping[str, Any]) -> set[str]:
    """Canonical columns that hold a value, minus the three derived ones.

    ``0`` and ``False`` are values; ``None``, ``""`` and whitespace are not.
    """

    return {column for column in _DATA_COLUMNS if is_filled(row.get(column))}


def reconcile(row: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Drop orphan records; report which filled cells still lack provenance.

    Returns ``(orphans_removed, missing_columns)`` so the caller can attribute the
    missing ones to their owning phase instead of inventing an "unknown" source.
    """

    filled = filled_cells(row)
    record = provenance(row)

    orphans = {column for column in record if column not in filled}
    for column in orphans:
        drop_entry(row, column)

    return orphans, {column for column in filled if column not in record}


def audit(rows: Any) -> list[str]:
    """Return one sentence per row whose provenance is not exact (empty = clean)."""

    problems: list[str] = []
    for index, row in enumerate(rows):
        filled = filled_cells(row)
        record = set(provenance(row))
        if record == filled:
            continue
        identifier = row.get("instrument_id") or f"#{index}"
        problems.append(
            f"{identifier}: missing {sorted(filled - record)}, "
            f"orphan {sorted(record - filled)}"
        )
    return problems


__all__ = [
    "PROVENANCE_FIELDS",
    "PROVENANCE_KEY",
    "PROVENANCE_STATUSES",
    "audit",
    "coerce_as_of",
    "drop_entry",
    "filled_cells",
    "make_entry",
    "provenance",
    "reconcile",
    "set_entry",
    "status_for_verdict",
]
