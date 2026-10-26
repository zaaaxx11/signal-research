"""Annual statements table from SEC ``companyfacts``.

``facts.py`` answers "what is the latest/TTM value of one metric"; this module
answers "show me the income statement, balance sheet and cash-flow lines for the
last N annual periods". It is a different question: a table needs aligned
periods, a missing row must stay a visible absence, and a restatement must win
inside its own period without moving the period.

Only 10-K observations are candidates: a 10-Q is a different period and mixing
it into an annual column is the exact error this module exists to prevent.

Contract: ``briefs/market-engine/R5C-CONTRACT.md``.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.engine.facts import FactObservation


def build_statements(
    observations: Sequence[FactObservation], *, periods: int = 4
) -> dict[str, object]:
    """Build the annual statement table. Implemented in R5C."""

    raise NotImplementedError("build_statements is implemented in R5C")


async def collect_statements(
    symbol: str,
    cik: str,
    *,
    client: Any = None,
    cache_dir: Path | None = None,
    periods: int = 4,
) -> dict[str, object]:
    """Fetch companyfacts and build one issuer's table. Implemented in R5C."""

    raise NotImplementedError("collect_statements is implemented in R5C")


__all__ = ["build_statements", "collect_statements"]
