"""Two-year daily bar backfill for the top 500 symbols.

Reads ``web/src/data/top500.json`` (a JSON array of ticker strings), fetches
daily OHLCV from Yahoo's chart endpoint, and appends the bars to the bar
store.  Concurrency is 2, matching the existing depth source.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.market.barstore import BarStore
from app.market.chart import ChartBar, _bars
from app.market.patterns import detect_all

logger = logging.getLogger(__name__)

#: Path to the top-500 symbol list (one JSON array of strings).
TOP500_PATH = Path(
    os.environ.get(
        "ZSTOCKZ_TOP500_PATH",
        "web/src/data/top500.json",
    )
)

#: Concurrency matches the depth source.
_CONCURRENCY = 2

#: Yahoo chart base URL.
_BASE_URL = "https://query1.finance.yahoo.com"

#: Two years of daily bars.
_CHART_PATH = "/v8/finance/chart/{symbol}"


async def _fetch_bars(
    client: Any,
    symbol: str,
    period1: int,
    period2: int,
) -> list[ChartBar]:
    """Fetch daily bars from Yahoo and return ChartBar objects."""

    url = (
        f"{_BASE_URL}{_CHART_PATH.format(symbol=symbol)}"
        f"?period1={period1}&period2={period2}&interval=1d"
    )
    try:
        response = await client.get(
            url,
            headers={
                "user-agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "accept": "application/json",
            },
        )
        if response.status_code != 200:
            logger.warning("yahoo chart %s: HTTP %d", symbol, response.status_code)
            return []
        payload = response.json()
        result = (payload.get("chart") or {}).get("result") or []
        if not result:
            return []
        return _bars(result[0])
    except Exception as exc:
        logger.warning("yahoo chart failed for %s: %s", symbol, exc)
        return []


async def run_backfill() -> dict[str, Any]:
    """Backfill 2 years of daily bars for the top 500 symbols.

    Returns a summary: ``{fetched, symbols, bars, errors}``.
    """

    if not TOP500_PATH.exists():
        raise FileNotFoundError(
            f"Top-500 symbol list not found at {TOP500_PATH}. "
            "Create it first (e.g. ``web/src/data/top500.json``)."
        )

    with open(TOP500_PATH, "r", encoding="utf-8") as fh:
        symbols = json.load(fh)
    if not isinstance(symbols, list) or not symbols:
        raise ValueError(f"{TOP500_PATH} must be a non-empty JSON array")

    symbols = [s.upper() for s in symbols][:500]
    store = BarStore()

    now = datetime.now(UTC)
    period2 = int(now.timestamp())
    period1 = int(datetime(now.year - 2, now.month, now.day, tzinfo=UTC).timestamp())

    semaphore = asyncio.Semaphore(_CONCURRENCY)

    from app.providers import http as provider_http

    async with provider_http.build_client(timeout_s=30) as client:

        async def one(sym: str) -> None:
            async with semaphore:
                bars = await _fetch_bars(client, sym, period1, period2)
                if bars:
                    store.append_many(sym, bars)
                    logger.debug("backfilled %d bars for %s", len(bars), sym)

        await asyncio.gather(*(one(sym) for sym in symbols))

    total_bars = sum(store.count(sym) for sym in store.symbols())
    return {
        "fetched": len(symbols),
        "symbols": len(store.symbols()),
        "bars": total_bars,
        "errors": [],
    }


__all__ = ["run_backfill"]
