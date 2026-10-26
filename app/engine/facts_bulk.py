"""Bounded companyfacts enrichment for one snapshot.

The identity universe is 10k+ symbols; facts exist only for the symbols an
operator chose to ingest. Reading every issuer's ``companyfacts`` document is
tens of gigabytes and hours of parsing, so it is a **batch**, not a startup
step: the cache makes it resumable, ``limit``/``time_budget`` make each run
bounded, and the report states exactly how many documents were read versus how
many symbols they filled (share classes share one CIK document).

Contract: ``briefs/market-engine/R4.5-CONTRACT.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.engine.ingest import EngineDataset

#: The observation the report belongs to.
FACTS_KIND = "companyfacts"


@dataclass(frozen=True, slots=True)
class FactsReport:
    """One bounded facts pass, as counts an operator can reconcile.

    ``requested == attempted + deferred`` over **CIK documents**.
    ``documents`` is how many were read successfully, ``symbols`` how many fact
    rows were added (a CIK can own several share classes), and ``skipped``
    counts symbols that were never eligible (no CIK, or already carrying facts).
    ``requests`` counts cache misses, not attempts.
    """

    requested: int = 0
    attempted: int = 0
    documents: int = 0
    symbols: int = 0
    skipped: int = 0
    failed: int = 0
    requests: int = 0
    deferred: int = 0

    def document(self) -> dict[str, object]:
        """Project the report into its JSON shape."""

        return {
            "requested": self.requested,
            "attempted": self.attempted,
            "documents": self.documents,
            "symbols": self.symbols,
            "skipped": self.skipped,
            "failed": self.failed,
            "requests": self.requests,
            "deferred": self.deferred,
        }


async def enrich_facts(
    dataset: EngineDataset,
    *,
    client: Any = None,
    cache_dir: Path | None = None,
    limit: int | None = None,
    time_budget_s: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[EngineDataset, FactsReport]:
    """Fill missing facts for a bounded set of CIK documents. Implemented in R4.5."""

    raise NotImplementedError("enrich_facts is implemented in R4.5")


def main(argv: list[str] | None = None) -> int:
    """Run the facts CLI. Implemented in R4.5."""

    raise NotImplementedError("the facts CLI is implemented in R4.5")


__all__ = ["FACTS_KIND", "FactsReport", "enrich_facts", "main"]
