"""Coverage report for one ingested :class:`~app.engine.ingest.EngineDataset`.

A dataset is only as useful as the cells it can actually fill. This module
answers "how much of the universe do we really know?" as a reproducible
document instead of a hand-written claim: identity fields present, fact symbols,
fact cells, and provenance coverage.

The report is deliberately a *description*, not a gate. A low industry count is
not an error — it is the measurement that tells the operator which enrichment
pass to run next. Sector is reported as ``None`` coverage because SEC publishes
SIC industry, not a sector taxonomy; the note says so rather than inventing a
mapping. The same holds for the two partitions R3 cannot fill: a ``global``
slice has no identity source in engine v2 (the SEC index is US-listed), and the
index carries current registrants only, so ``active=False`` cannot yet be
written. Reporting those gaps as notes is the honest alternative to fabricating
rows for them.

Contract (frozen in ``briefs/market-engine/R3-CONTRACT.md`` §2):

* :func:`dataset_coverage` is pure and returns the exact document shape.
  Counts are row counts over ``dataset.security_master.records()``; a text field
  counts only when it is not ``None`` and not blank.
* Percentages are rounded to two decimals; an empty dataset reports `0.0`
  rather than dividing by zero.
* :func:`main` is the ``python -m app.engine.coverage --snapshot <path>`` CLI,
  returning an exit code instead of raising, so a missing snapshot is a typed
  JSON error document and never a traceback.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence

from app.engine.ingest import EngineDataset
from app.engine.model import Cell, SecurityRecord
from app.engine.store import EngineStore, SnapshotError

#: Identity fields the report counts, in output order.
IDENTITY_FIELDS: tuple[str, ...] = (
    "cik",
    "exchange",
    "mic",
    "industry",
    "country",
    "sector",
)

#: The gaps an operator must not mistake for a bug, in the contract's wording.
#: These are measurements of what the sources *are*, so the text is frozen with
#: the keys: changing it would change the claim, not just the presentation.
NOTES: dict[str, str] = {
    "sector": "SEC publishes SIC industry, not a sector taxonomy",
    "global": "no global identity source in engine v2 (SEC is US-listed)",
    "delisted": "the SEC index carries current registrants only",
}

#: Exit code for an unreadable or invalid snapshot (distinct from argparse's own
#: usage failure, which also exits 2, but prints usage rather than a document).
EXIT_SNAPSHOT_ERROR = 2


def _is_populated(value: object) -> bool:
    """Whether one identity value counts as populated.

    ``None`` is "the document did not tell us"; a blank or whitespace-only
    string is the same absence spelled differently. Both are absences, so
    counting them would inflate coverage with a field no source supplied.
    """

    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _pct(count: int, total: int) -> float:
    """``count`` as a percentage of ``total``, rounded to two decimals.

    An empty universe has no denominator, and ``0.0`` is the honest answer:
    a report over nothing covers nothing.
    """

    if total <= 0:
        return 0.0
    return round(count / total * 100, 2)


def _identity_counts(records: Sequence[SecurityRecord]) -> dict[str, int]:
    """How many rows carry each identity field, over the master's records."""

    counts = {field: 0 for field in IDENTITY_FIELDS}
    for record in records:
        for field in IDENTITY_FIELDS:
            if _is_populated(getattr(record, field)):
                counts[field] += 1
    return counts


def _fact_counts(facts: Mapping[str, Mapping[str, Cell]]) -> tuple[int, int, int]:
    """``(fact symbols, cells, provenance cells)`` over ``dataset.facts``.

    ``symbols`` counts a symbol only when it holds at least one cell: an empty
    mapping means the document was read and had nothing usable, which is not
    the same fact as "this symbol has data". Provenance is counted separately
    from the cells so a mismatch (a value without evidence) is visible in the
    report instead of being averaged away.
    """

    symbols = 0
    cells = 0
    provenance_cells = 0
    for cells_by_metric in facts.values():
        if cells_by_metric:
            symbols += 1
        for cell in cells_by_metric.values():
            cells += 1
            if cell.provenance is not None:
                provenance_cells += 1
    return symbols, cells, provenance_cells


def dataset_coverage(dataset: EngineDataset) -> dict[str, object]:
    """Describe how much of ``dataset`` is populated.

    Pure: every count is read from the dataset, no attribute is written and no
    clock or network is touched, so two calls over one dataset agree and the
    same snapshot always reports the same numbers.
    """

    records = dataset.security_master.records()
    total = len(records)
    active = sum(1 for record in records if record.active)
    identity = _identity_counts(records)
    fact_symbols, fact_cells, provenance_cells = _fact_counts(dataset.facts)

    return {
        "total": total,
        "active": active,
        "identity": identity,
        "identity_pct": {
            field: _pct(identity[field], total) for field in IDENTITY_FIELDS
        },
        "facts": {
            "symbols": fact_symbols,
            "cells": fact_cells,
            "provenance_cells": provenance_cells,
            "pct_of_universe": _pct(fact_symbols, total),
        },
        # A copy: a caller that mutates the document must not rewrite the
        # module's frozen wording for the next report in the same process.
        "notes": dict(NOTES),
    }


def main(argv: list[str] | None = None) -> int:
    """Print the coverage document for one snapshot; return the exit code.

    The snapshot is read through :class:`EngineStore`, the only component that
    verifies schema, checksum and shape. A failure is reported as the store's
    own ``code`` in a JSON document on stderr, because an operator piping this
    command needs a parseable error and a non-zero exit, not a traceback.
    """

    parser = argparse.ArgumentParser(
        prog="python -m app.engine.coverage",
        description="Report identity and fact coverage for one engine snapshot.",
    )
    parser.add_argument(
        "--snapshot",
        required=True,
        help="path to the snapshot written by EngineStore.save",
    )
    args = parser.parse_args(argv)

    try:
        dataset = EngineStore(args.snapshot).load()
    except SnapshotError as exc:
        payload = {"error": exc.code, "errorMessage": str(exc)}
        print(json.dumps(payload), file=sys.stderr)
        return EXIT_SNAPSHOT_ERROR

    print(json.dumps(dataset_coverage(dataset), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EXIT_SNAPSHOT_ERROR",
    "IDENTITY_FIELDS",
    "NOTES",
    "dataset_coverage",
    "main",
]
