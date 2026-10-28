"""Keyless fast-path benchmark: 100 symbols, SEC bulk + CNBC + Yahoo chart.

Measures the fast path the plan promises under 5 minutes for 4,217 symbols:
SEC quarterly bulk fundamentals, CNBC batch quote, Yahoo chart OHLC/technicals.
No API keys. Run: .venv/bin/python scripts/fast_path_bench.py [n]
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx2

from app.engine.bulk_financials import (
    FactIndex,
    current_quarter,
    dataset_cache_path,
    download_dataset,
    parse_quarter,
    quarter_label,
)
from app.market.history import compute_technicals, parse_bars
from app.market.price import (
    CnbcPriceSource,
    NasdaqPriceSource,
    PriceRegistry,
)
from app.market.sec_fundamentals import fundamentals_for_symbol

UNIVERSE = Path("/home/ubuntu/briefs/market-engine/research/probes-1000/universe.json")
SNAPSHOT = Path("/home/ubuntu/ztockz-data/engine-snapshot.json")
CACHE = Path("/home/ubuntu/ztockz-data/cache")
CATALOG = Path("/home/ubuntu/ztockz/app/engine/data/finviz_columns_116.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def load_universe(limit: int) -> list[str]:
    rows = json.loads(UNIVERSE.read_text())["rows"]
    return [r["name"] for r in rows if r.get("name")][:limit]


def cik_map() -> dict[str, str]:
    securities = json.loads(SNAPSHOT.read_text())["dataset"]["securities"]
    return {s["symbol"].upper(): s["cik"] for s in securities if s.get("cik")}


async def yahoo_bars(symbols: list[str], conc: int = 16) -> dict[str, dict]:
    """Yahoo chart OHLC -> technicals, one request per symbol, parallel."""
    out: dict[str, dict] = {}
    async with httpx2.AsyncClient(
        headers={"user-agent": UA}, timeout=20, follow_redirects=True
    ) as client:
        sem = asyncio.Semaphore(conc)

        async def one(sym: str) -> None:
            async with sem:
                try:
                    r = await client.get(
                        f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
                        "?interval=1d&range=1y"
                    )
                    if r.status_code != 200:
                        return
                    bars = parse_bars(r.json())
                    tech = compute_technicals(bars)
                    if tech:
                        out[sym] = tech
                except Exception:
                    return

        await asyncio.gather(*(one(s) for s in symbols))
    return out


def previous_quarter(label: str, back: int = 1) -> str:
    """SEC publishes with a lag; step back N quarters from a ``2026q4`` label."""
    year, quarter = parse_quarter(label)
    total = year * 4 + (quarter - 1) - back
    return quarter_label(total // 4, total % 4 + 1)


#: Yahoo v7 batch quote → catalog key. One request answers 100–400 symbols,
#: which is what makes the fast path fast without an API key.
YAHOO_BATCH_MAP = {
    "marketCap": "market_cap",
    "trailingPE": "pe",
    "forwardPE": "forward_pe",
    "priceToBook": "pb",
    "epsTrailingTwelveMonths": "eps",
    "epsForward": "eps_next_y",
    "epsCurrentYear": "eps_this_y",
    "dividendRate": "dividend",
    "dividendYield": "dividend_yield",
    "trailingAnnualDividendRate": "dividend_ttm",
    "bookValue": "book_per_share",
    "sharesOutstanding": "shares_outstanding",
    "averageDailyVolume3Month": "average_volume",
    "fiftyTwoWeekHigh": "high_52w",
    "fiftyTwoWeekLow": "low_52w",
    "fiftyTwoWeekRange": "week_52_range",
    "fiftyTwoWeekChangePercent": "perf_52w",
    "averageAnalystRating": "recommendation",
    "regularMarketPrice": "price",
    "regularMarketChange": "change",
    "regularMarketChangePercent": "change_pct",
    "regularMarketVolume": "volume",
    "regularMarketPreviousClose": "prev_close",
    "regularMarketOpen": "open",
    "regularMarketDayHigh": "high",
    "regularMarketDayLow": "low",
    "longName": "company",
}


async def yahoo_batch_quotes(symbols: list[str], batch: int = 400) -> dict[str, dict]:
    """Yahoo v7 quote, batched: one request per ``batch`` symbols."""
    out: dict[str, dict] = {}
    async with httpx2.AsyncClient(
        headers={"user-agent": UA}, timeout=30, follow_redirects=True
    ) as client:
        await client.get("https://fc.yahoo.com")
        crumb = (
            await client.get("https://query1.finance.yahoo.com/v1/test/getcrumb")
        ).text.strip()
        groups = [symbols[i : i + batch] for i in range(0, len(symbols), batch)]
        sem = asyncio.Semaphore(3)

        async def one(group: list[str]) -> None:
            async with sem:
                try:
                    r = await client.get(
                        "https://query1.finance.yahoo.com/v7/finance/quote"
                        f"?symbols={','.join(group)}&crumb={crumb}"
                    )
                    if r.status_code != 200:
                        return
                    for q in r.json()["quoteResponse"]["result"]:
                        sym = str(q.get("symbol", "")).upper()
                        if not sym:
                            continue
                        row = {
                            key: q[src]
                            for src, key in YAHOO_BATCH_MAP.items()
                            if q.get(src) is not None
                        }
                        # sma50/sma200 as percent distance, matching chart.py
                        close = q.get("regularMarketPrice")
                        for src, key in (
                            ("fiftyDayAverage", "sma50"),
                            ("twoHundredDayAverage", "sma200"),
                        ):
                            avg = q.get(src)
                            if close and avg:
                                row[key] = (close - avg) / avg * 100.0
                        # relative volume against the 3-month average
                        vol, avg_vol = (
                            q.get("regularMarketVolume"),
                            q.get("averageDailyVolume3Month"),
                        )
                        if vol and avg_vol:
                            row["relative_volume"] = vol / avg_vol
                        out[sym] = row
                except Exception:
                    return

        await asyncio.gather(*(one(g) for g in groups))
    return out


async def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    symbols = load_universe(limit)
    ciks = cik_map()
    print(f"symbols={len(symbols)} cik_map={len(ciks)}", flush=True)

    # --- SEC bulk fundamentals -------------------------------------------
    t0 = time.perf_counter()
    # SEC publishes with a lag: the calendar quarter (2026q4 in late Oct) is
    # not out yet, so walk back to the newest quarter that answers 200.
    quarter = current_quarter()
    zip_path = dataset_cache_path(CACHE, quarter)
    if not zip_path.exists():
        for back in range(1, 4):
            prev = previous_quarter(quarter, back)
            candidate = dataset_cache_path(CACHE, prev)
            if candidate.exists():
                quarter, zip_path = prev, candidate
                break
        else:
            quarter = previous_quarter(quarter, 1)
            zip_path = dataset_cache_path(CACHE, quarter)
    if not zip_path.exists():
        await download_dataset(quarter, cache_dir=CACHE)
    wanted = {ciks[s] for s in symbols if s in ciks}
    index = FactIndex.build(zip_path, ciks=wanted)
    print(
        f"sec_bulk quarter={quarter} ciks={len(index.ciks())} "
        f"build={time.perf_counter() - t0:.1f}s",
        flush=True,
    )

    # --- CNBC price (primary) + Nasdaq (fallback for misses) --------------
    t0 = time.perf_counter()
    registry = PriceRegistry(
        primary=CnbcPriceSource(), fallback=NasdaqPriceSource()
    )
    quotes = await registry.quotes(symbols)
    priced = {s: q for s, q in quotes.items() if q is not None}
    print(
        f"price ok={len(priced)}/{len(symbols)} "
        f"wall={time.perf_counter() - t0:.1f}s errors={registry.last_errors}",
        flush=True,
    )

    # --- Yahoo chart technicals -------------------------------------------
    t0 = time.perf_counter()
    techs = await yahoo_bars(symbols)
    print(
        f"chart ok={len(techs)}/{len(symbols)} wall={time.perf_counter() - t0:.1f}s",
        flush=True,
    )

    # --- Yahoo v7 batch quote (one request per 400 symbols) ----------------
    t0 = time.perf_counter()
    batch_quotes = await yahoo_batch_quotes(symbols)
    print(
        f"yahoo_batch ok={len(batch_quotes)}/{len(symbols)} "
        f"wall={time.perf_counter() - t0:.1f}s",
        flush=True,
    )

    # --- Merge -------------------------------------------------------------
    merged: dict[str, dict] = {}
    for sym in symbols:
        row: dict = {}
        price = priced[sym].last if sym in priced else None
        if price is None and sym in batch_quotes:
            price = batch_quotes[sym].get("price")
        if sym in ciks:
            row.update(fundamentals_for_symbol(index, sym, ciks, price=price))
        if sym in batch_quotes:
            row.update(batch_quotes[sym])
        if sym in priced:
            q = priced[sym]
            row["price"] = q.last
            row["prev_close"] = q.prev_close
            row["change_pct"] = q.change_pct
            row["volume"] = q.volume
        if sym in techs:
            row.update(techs[sym])
        merged[sym] = row

    # --- Coverage ----------------------------------------------------------
    catalog = json.loads(CATALOG.read_text())
    names = [c["key"] for c in catalog]
    filled = {n for n in names if any(r.get(n) is not None for r in merged.values())}
    per_symbol = [sum(1 for v in r.values() if v is not None) for r in merged.values()]
    per_symbol.sort()
    median = (
        per_symbol[len(per_symbol) // 2] if per_symbol else 0
    )
    print(
        json.dumps(
            {
                "coverage": f"{len(filled)}/{len(names)}",
                "median_fields": median,
                "min_fields": per_symbol[0] if per_symbol else 0,
                "max_fields": per_symbol[-1] if per_symbol else 0,
            },
            indent=1,
        )
    )
    out = Path("/home/ubuntu/briefs/market-engine/research/fast-path-100.json")
    out.write_text(json.dumps({"merged": merged}, indent=1))
    print(f"written {out}")


if __name__ == "__main__":
    asyncio.run(main())
