"""Bulk submissions-profile enrichment for one snapshot.

R3 made venues universal in one request, but `industry`/`country` only exist on
each issuer's ``submissions`` document. Fetching ten thousand of those at SEC's
10-request/second ceiling is a ~17-minute batch, not a startup step and not
something a screen request may trigger.

This module is that batch: bounded, resumable, cache-backed, and honest about
what it did. It selects the records still missing a profile, reads each
submission (cache first), merges the identity fields through
:class:`~app.engine.security.SecurityMaster`, and reports attempted / enriched /
skipped / conflicts / failed / requests / deferred.

Contract: ``briefs/market-engine/R3.5-CONTRACT.md``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from app.engine.ingest import EngineDataset


@dataclass(frozen=True, slots=True)
class EnrichmentReport:
    """One bounded enrichment pass, as counts an operator can reconcile.

    ``requested == attempted + deferred`` always. ``attempted`` splits into the
    symbols that changed (``enriched``), the ones a competing SEC document
    contradicted (``conflicts``) and the ones that errored (``failed``).
    ``skipped`` counts records that were never eligible (already complete, or no
    CIK) and is reported separately so it cannot be mistaken for work done.
    """

    requested: int = 0
    attempted: int = 0
    enriched: int = 0
    skipped: int = 0
    conflicts: int = 0
    failed: int = 0
    requests: int = 0
    deferred: int = 0

    def document(self) -> dict[str, object]:
        """Project the report into its JSON shape."""

        return {
            "requested": self.requested,
            "attempted": self.attempted,
            "enriched": self.enriched,
            "skipped": self.skipped,
            "conflicts": self.conflicts,
            "failed": self.failed,
            "requests": self.requests,
            "deferred": self.deferred,
        }


async def enrich_profiles(
    dataset: EngineDataset,
    *,
    client: Any = None,
    cache_dir: Any = None,
    limit: int | None = None,
    time_budget_s: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    on_progress: Callable[[int, int], None] | None = None,
) -> EnrichmentReport:
    """Enrich missing industry/country fields. Implemented in R3.5."""

    raise NotImplementedError("enrich_profiles is implemented in R3.5")


def main(argv: list[str] | None = None) -> int:
    """Run the enrichment CLI. Implemented in R3.5."""

    raise NotImplementedError("the enrichment CLI is implemented in R3.5")


__all__ = ["EnrichmentReport", "enrich_profiles", "main"]
