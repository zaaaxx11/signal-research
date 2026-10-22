"""Per-column quality accounting and the PASS/DEGRADED/REJECT verdict (02 §3.7).

Two identities decide whether a quality report is worth reading:

* ``filled + empty == row_count`` — nothing appears or vanishes;
* ``verified + rejected + disputed == filled`` — every filled cell has a fate.

A ``filled`` cell is a cell that held a value **when the verifier saw it**. A
rejected cell moves to ``rejected`` and is empty in the export; it is not
silently dropped from the denominator, because that is how a broken column
starts reporting a 100% pass rate.

The verdict is the product's only honest summary. ``PASS`` means clean,
``DEGRADED`` means a major rule or a provider disagreement, and ``REJECT``
means a critical rule (V7 unit, V9 parity, V11 currency, V15 band, V16 future
``as_of``) or a report that does not reconcile — the case in which the export
must be refused rather than shipped with an apology.

``verify/`` is import-pure (``tests/_import_rules.py``): the 92-column contract
is **injected** by the caller (``columns=``) and provenance is read from the
row's ``_provenance`` data key, never imported from ``app.enrich``.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from app.verify.rules import severity_of

#: The closed verdict vocabulary. Order is part of the contract.
VERDICTS: tuple[str, ...] = ("PASS", "DEGRADED", "REJECT")

PASS = "PASS"
DEGRADED = "DEGRADED"
REJECT = "REJECT"

#: Row key holding the per-cell provenance mapping (02 §3.5). A literal, because
#: importing ``app.enrich.provenance`` would break the verify-purity gate.
PROVENANCE_KEY = "_provenance"

#: Counters every block carries, in a stable order.
BLOCK_COUNTERS: tuple[str, ...] = ("filled", "verified", "rejected", "disputed", "empty")


class QualityError(ValueError):
    """A quality report that does not reconcile is a lie, not a warning."""


def _is_filled(value: Any) -> bool:
    """``None`` and blank strings are empty; ``0`` and ``False`` are values."""

    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _column_of(record: Mapping[str, Any]) -> str | None:
    """The column a rejection/dispute marker names, under either frozen spelling."""

    for key in ("column", "field"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _cells(markers: Iterable[Mapping[str, Any]]) -> set[tuple[Any, str]]:
    """``(instrument_id, column)`` pairs, deduplicated: a retry is not a new cell."""

    cells: set[tuple[Any, str]] = set()
    for marker in markers:
        if not isinstance(marker, Mapping):
            continue
        column = _column_of(marker)
        if column is None:
            continue
        cells.add((marker.get("instrument_id"), column))
    return cells


def _provenance(row: Mapping[str, Any]) -> Mapping[str, Any]:
    record = row.get(PROVENANCE_KEY)
    return record if isinstance(record, Mapping) else {}


def column_blocks(
    rows: Sequence[Mapping[str, Any]],
    *,
    columns: Sequence[str],
    rejections: Iterable[Mapping[str, Any]] = (),
    disputed: Iterable[Mapping[str, Any]] = (),
) -> dict[str, dict[str, Any]]:
    """One block per column: ``filled/verified/rejected/disputed/empty`` + sources.

    ``columns`` is injected rather than imported so this module stays pure. The
    block order is the caller's order — the frozen contract order in production.
    """

    materialised = [dict(row) for row in rows]
    row_count = len(materialised)
    rejected_cells = _cells(rejections)
    disputed_cells = _cells(disputed)

    blocks: dict[str, dict[str, Any]] = {}
    for column in columns:
        filled = verified = rejected = disputed_count = empty = 0
        sources: dict[str, int] = {}
        stamps: list[str] = []

        for row in materialised:
            key = (row.get("instrument_id"), column)
            present = _is_filled(row.get(column))
            if not present and key not in rejected_cells:
                empty += 1
                continue

            # Filled counts the cell the verifier *saw*, including one it then
            # rejected: removing a rejected cell from the denominator would
            # turn a broken column into a 100% pass rate.
            filled += 1
            if key in rejected_cells:
                rejected += 1
            elif key in disputed_cells:
                disputed_count += 1
            else:
                verified += 1

            if present:
                entry = _provenance(row).get(column)
                if isinstance(entry, Mapping):
                    source = entry.get("source")
                    if isinstance(source, str) and source.strip():
                        name = source.strip()
                        sources[name] = sources.get(name, 0) + 1
                    as_of = entry.get("as_of")
                    if as_of:
                        stamps.append(str(as_of))

        blocks[column] = {
            "filled": filled,
            "verified": verified,
            "rejected": rejected,
            "disputed": disputed_count,
            "empty": empty,
            "sources": sources,
            "min_as_of": min(stamps) if stamps else None,
            "max_as_of": max(stamps) if stamps else None,
        }
        if filled + empty != row_count:  # pragma: no cover - construction invariant
            raise QualityError(
                f"{column}: filled({filled}) + empty({empty}) != row_count({row_count})"
            )
        if verified + rejected + disputed_count != filled:  # pragma: no cover
            raise QualityError(
                f"{column}: verified({verified}) + rejected({rejected}) + "
                f"disputed({disputed_count}) != filled({filled})"
            )

    return blocks


def reconcile(
    blocks: Mapping[str, Mapping[str, Any]],
    *,
    row_count: int,
    columns: Sequence[str],
) -> None:
    """Raise :class:`QualityError` when a report is missing a column or a count."""

    expected = [str(column) for column in columns]
    missing = [column for column in expected if column not in blocks]
    extra = [column for column in blocks if column not in set(expected)]
    if missing or extra:
        parts: list[str] = []
        if missing:
            parts.append("missing column(s): " + ", ".join(missing))
        if extra:
            parts.append("unknown column(s): " + ", ".join(sorted(extra)))
        raise QualityError("quality report does not cover the contract; " + "; ".join(parts))

    for column in expected:
        block = blocks[column]
        filled = int(block.get("filled") or 0)
        verified = int(block.get("verified") or 0)
        rejected = int(block.get("rejected") or 0)
        disputed = int(block.get("disputed") or 0)
        empty = int(block.get("empty") or 0)
        if filled + empty != int(row_count):
            raise QualityError(
                f"{column}: filled({filled}) + empty({empty}) != row_count({row_count})"
            )
        if verified + rejected + disputed != filled:
            raise QualityError(
                f"{column}: verified({verified}) + rejected({rejected}) + "
                f"disputed({disputed}) != filled({filled})"
            )


def verdict(
    blocks: Mapping[str, Mapping[str, Any]],
    *,
    rejections: Iterable[Mapping[str, Any]] = (),
    reconciled: bool = True,
) -> str:
    """``PASS``/``DEGRADED``/``REJECT`` for one report; unknown rules raise."""

    if not reconciled:
        # A report that does not add up must not become a Success with
        # interesting numbers (02 §3.7, Fase 4 gate 5).
        return REJECT

    severities: list[str] = []
    for item in rejections:
        rule = item.get("rule") if isinstance(item, Mapping) else None
        if rule is None:
            raise ValueError(f"rejection without a rule: {item!r}")
        # severity_of raises ValueError for an unknown rule: a verdict that
        # cannot classify a rejection is not a verdict.
        severities.append(severity_of(str(rule)))

    if "critical" in severities:
        return REJECT

    disputed_cells = sum(int(block.get("disputed") or 0) for block in blocks.values())
    if severities or disputed_cells:
        return DEGRADED
    return PASS


def summarise(blocks: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    """Totals across every column, for ``usage`` and the manifest."""

    totals = {name: 0 for name in BLOCK_COUNTERS}
    for block in blocks.values():
        for name in BLOCK_COUNTERS:
            totals[name] += int(block.get(name) or 0)
    return totals


__all__ = [
    "BLOCK_COUNTERS",
    "DEGRADED",
    "PASS",
    "PROVENANCE_KEY",
    "QualityError",
    "REJECT",
    "VERDICTS",
    "column_blocks",
    "reconcile",
    "summarise",
    "verdict",
]
