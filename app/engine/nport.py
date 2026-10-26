"""ETF holdings from SEC Form N-PORT.

An ETF files N-PORT-P every quarter with its full portfolio: one ``invstOrSec``
row per position, carrying name, CUSIP, balance, USD value, percentage and
asset category. That is the official answer to "what does this fund hold?" — no
vendor top-10 list, no scraping.

``pctVal`` is copied as SEC reports it: **percent points** (SPY's 504 rows sum
to ≈100). Treating it as a fraction would turn 0.08% into 8%.

Contract: ``briefs/market-engine/R5D-CONTRACT.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class HoldingsNotFound(LookupError):
    """The issuer has no N-PORT-P filing in its recent submissions."""


class HoldingsUnavailable(RuntimeError):
    """The N-PORT body could not be fetched or parsed."""


@dataclass(frozen=True, slots=True)
class EtfHolding:
    """One portfolio position exactly as the fund reported it."""

    name: str
    title: str | None
    cusip: str | None
    balance: float | None
    value_usd: float | None
    pct_val: float | None
    asset_category: str | None
    country: str | None
    currency: str | None

    def document(self) -> dict[str, object]:
        """Project the holding into its JSON shape."""

        return {
            "name": self.name,
            "title": self.title,
            "cusip": self.cusip,
            "balance": self.balance,
            "value_usd": self.value_usd,
            "pct_val": self.pct_val,
            "asset_category": self.asset_category,
            "country": self.country,
            "currency": self.currency,
        }


def parse_nport(xml_text: str) -> tuple[EtfHolding, ...]:
    """Parse one N-PORT body. Implemented in R5D."""

    raise NotImplementedError("parse_nport is implemented in R5D")


async def collect_holdings(
    symbol: str,
    cik: str,
    *,
    client: Any = None,
    cache_dir: Path | None = None,
    limit: int = 50,
) -> dict[str, object]:
    """Read the latest N-PORT-P portfolio. Implemented in R5D."""

    raise NotImplementedError("collect_holdings is implemented in R5D")


__all__ = [
    "EtfHolding",
    "HoldingsNotFound",
    "HoldingsUnavailable",
    "collect_holdings",
    "parse_nport",
]
