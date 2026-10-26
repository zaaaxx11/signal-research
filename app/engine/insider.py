"""SEC Form 4 insider transactions: the filing's own words, one row each.

Form 4 is the document an insider files after buying, selling or exercising a
security. It is public, small (a few KB of XML) and already carries the fields a
reader needs: who, what security, which transaction code, how many shares, at
what price and what they held afterwards.

This slice reads only the non-derivative table. Derivative transactions
(options, warrants) have a different shape and a different meaning; pretending
they are the same row would publish an exercise as a purchase. That omission is
a documented gap, not a silent zero.

Contract: ``briefs/market-engine/R5A-CONTRACT.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class InsiderTransaction:
    """One non-derivative Form 4 transaction, with its filing context."""

    accession: str
    filed: str
    period_of_report: str
    owner_name: str
    owner_cik: str | None
    role: str | None
    security: str
    transaction_date: str
    code: str
    shares: float | None
    price: float | None
    acquired_disposed: str | None
    shares_after: float | None
    url: str

    def document(self) -> dict[str, object]:
        """Project the transaction into its JSON shape."""

        return {
            "accession": self.accession,
            "filed": self.filed,
            "period_of_report": self.period_of_report,
            "owner_name": self.owner_name,
            "owner_cik": self.owner_cik,
            "role": self.role,
            "security": self.security,
            "transaction_date": self.transaction_date,
            "code": self.code,
            "shares": self.shares,
            "price": self.price,
            "acquired_disposed": self.acquired_disposed,
            "shares_after": self.shares_after,
            "url": self.url,
        }


def parse_form4(
    xml_text: str, *, accession: str, filed: str, url: str
) -> tuple[InsiderTransaction, ...]:
    """Parse one Form 4 body. Implemented in R5A."""

    raise NotImplementedError("parse_form4 is implemented in R5A")


async def collect_insider(
    symbol: str,
    cik: str,
    *,
    client: Any = None,
    cache_dir: Path | None = None,
    limit: int = 5,
) -> tuple[tuple[InsiderTransaction, ...], tuple[str, ...]]:
    """Read recent Form 4 filings for one issuer. Implemented in R5A."""

    raise NotImplementedError("collect_insider is implemented in R5A")


__all__ = ["InsiderTransaction", "collect_insider", "parse_form4"]
