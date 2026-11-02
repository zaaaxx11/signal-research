"""Nightly incremental bar update.

Fetches the latest daily bar for every symbol in the top-500 list and appends
it to the bar store.  Designed to run from cron after market close.
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

#: Latest 5 days (to get the most recent trading day).
_CHART_PATH = "/v8/finance/chart/{symbol}"


async def _fetch_latest_bars(
    client: Any,
    symbol: str,
) -> list[ChartBar]:
    """Fetch the most recent daily bars from Yahoo."""

    url = (
        f"{_BASE_URL}{_CHART_PATH.format(symbol=symbol)}"
        "?period1=0&period2=9999999999&interval=1d"
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


def _compute_mtd(bars: list[ChartBar], current_day: Any) -> float | None:
    """Month-to-date return: ``(current_close / prior_month_close - 1) * 100``."""

    if not bars:
        return None
    current = bars[-1]
    # Find the last bar of the previous calendar month.
    prev_month = current_day.month - 1 if current_day.month > 1 else 12
    prev_year = current_day.year if current_day.month > 1 else current_day.year - 1
    prev_month_close: float | None = None
    for bar in reversed(bars[:-1]):
        if bar.day.year == prev_year and bar.day.month == prev_month:
            prev_month_close = bar.close
            break
    if prev_month_close is None or prev_month_close <= 0:
        return None
    return (current.close / prev_month_close - 1.0) * 100.0


async def run_nightly() -> dict[str, Any]:
    """Run the nightly incremental update.

    Returns a summary document: ``{fetched, appended, errors}``.
    """

    if not TOP500_PATH.exists():
        raise FileNotFoundError(
            f"Top-500 symbol list not found at {TOP500_PATH}. "
            "Run backfill first."
        )

    with open(TOP500_PATH, "r", encoding="utf-8") as fh:
        symbols = json.load(fh)
    if not isinstance(symbols, list) or not symbols:
        raise ValueError(f"{TOP500_PATH} must be a non-empty JSON array")

    symbols = [s.upper() for s in symbols][:500]
    store = BarStore()

    semaphore = asyncio.Semaphore(_CONCURRENCY)

    from app.providers import http as provider_http

    fetched = 0
    appended = 0
    errors: list[str] = []

    async with provider_http.build_client(timeout_s=30) as client:

        async def one(sym: str) -> None:
            nonlocal fetched, appended
            async with semaphore:
                bars = await _fetch_latest_bars(client, sym)
                if not bars:
                    errors.append(sym)
                    return
                fetched += 1
                n = store.append_many(sym, bars)
                appended += n

        await asyncio.gather(*(one(sym) for sym in symbols))

    logger.info(
        "nightly complete: fetched=%d appended=%d errors=%d",
        fetched,
        appended,
        len(errors),
    )
    return {"fetched": fetched, "appended": appended, "errors": errors}


__all__ = ["run_nightly"]
