"""Ticker → SEC fund series mapping.

A fund trust can file one N-PORT-P per series; the trust's CIK alone cannot say
which series a filing belongs to. SEC publishes ``company_tickers_mf.json``
(``cik``, ``seriesId``, ``classId``, ``symbol``) — the official bridge from an
ETF ticker to the series id that appears inside the N-PORT XML.

A missing map is a normal state: callers fall back to the single-series path
rather than inventing a series.

Contract: ``briefs/market-engine/R5E-CONTRACT.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

FUND_SERIES_FILENAME = "company_tickers_mf.json"
FUND_SERIES_URL = "https://www.sec.gov/files/company_tickers_mf.json"


@dataclass(frozen=True, slots=True)
class FundSeries:
    """One row of the SEC fund ticker map."""

    cik: str
    series_id: str
    class_id: str | None
    symbol: str


def fund_series_path(cache_dir: Path) -> Path:
    """Where the downloaded map lives inside the engine cache."""

    return Path(cache_dir) / FUND_SERIES_FILENAME


def parse_fund_series(payload: object) -> dict[str, FundSeries]:
    """Parse the SEC fund ticker map. Implemented in R5E."""

    raise NotImplementedError("parse_fund_series is implemented in R5E")


def load_fund_series(path: Path) -> dict[str, FundSeries]:
    """Read the cached map, or ``{}`` when it is absent. Implemented in R5E."""

    raise NotImplementedError("load_fund_series is implemented in R5E")


async def download_fund_series(
    path: Path, *, client: Any = None
) -> dict[str, FundSeries]:
    """Download and cache the map. Implemented in R5E."""

    raise NotImplementedError("download_fund_series is implemented in R5E")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Implemented in R5E."""

    raise NotImplementedError("main is implemented in R5E")


__all__ = [
    "FUND_SERIES_FILENAME",
    "FUND_SERIES_URL",
    "FundSeries",
    "download_fund_series",
    "fund_series_path",
    "load_fund_series",
    "main",
    "parse_fund_series",
]
