"""Coverage report for one ingested :class:`~app.engine.ingest.EngineDataset`.

A dataset is only as useful as the cells it can actually fill. This module
answers "how much of the universe do we really know?" as a reproducible
document instead of a hand-written claim: identity fields present, fact symbols,
fact cells, and provenance coverage.

The report is deliberately a *description*, not a gate. A low industry count is
not an error — it is the measurement that tells the operator which enrichment
pass to run next. Sector is reported as ``None`` coverage because SEC publishes
SIC industry, not a sector taxonomy; the note says so rather than inventing a
mapping.

Contract (frozen in ``briefs/market-engine/R3-CONTRACT.md`` §2):

* :func:`dataset_coverage` is pure and returns the exact document shape.
* Percentages are rounded to two decimals; an empty dataset reports `0.0`
  rather than dividing by zero.
* :func:`main` is the ``python -m app.engine.coverage --snapshot <path>`` CLI,
  returning an exit code instead of raising.
"""

from __future__ import annotations

from app.engine.ingest import EngineDataset

#: Identity fields the report counts, in output order.
IDENTITY_FIELDS: tuple[str, ...] = (
    "cik",
    "exchange",
    "mic",
    "industry",
    "country",
    "sector",
)


def dataset_coverage(dataset: EngineDataset) -> dict[str, object]:
    """Describe how much of ``dataset`` is populated. Implemented in R3."""

    raise NotImplementedError("dataset_coverage is implemented in R3")


def main(argv: list[str] | None = None) -> int:
    """Run the coverage CLI. Implemented in R3."""

    raise NotImplementedError("the coverage CLI is implemented in R3")


__all__ = ["IDENTITY_FIELDS", "dataset_coverage", "main"]
