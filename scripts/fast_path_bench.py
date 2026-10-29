"""Keyless fast-path benchmark: 100 symbols, SEC bulk + CNBC + Yahoo chart.

Measures the fast path the plan promises under 5 minutes for 4,217 symbols:
SEC quarterly bulk fundamentals, CNBC batch quote, Yahoo chart OHLC/technicals,
Yahoo v7 batch quote — and the two keyless depth sources, FINRA consolidated
short interest and the official index membership files. No API keys.
Run: .venv/bin/python scripts/fast_path_bench.py [n]
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx2

from app.engine.bulk_financials import (
    FactIndex,
    build_quarters,
    current_quarter,
    dataset_cache_path,
    download_dataset,
    parse_quarter,
    quarter_label,
)
from app.market.depth import QUOTE_ONLY_FIELDS, canonical_symbol
from app.market.finra import FinraShortSource
from app.market.history import compute_technicals, parse_bars
from app.market.official import OfficialDepthSource
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


# ---------------------------------------------------------------------------
# Keyless depth sources: FINRA short interest + official index membership
# ---------------------------------------------------------------------------


def merge_observation_fields(
    row: dict[str, Any], fields: Mapping[str, Any]
) -> set[str]:
    """Fill one row's missing catalog fields from a depth observation.

    Returns the set of keys this call actually wrote. The fast path's own
    sources are primary: an existing non-None value is never replaced, so
    adding a depth source can only grow coverage, never move it. ``None``
    values and the quote-hub fields (``price``, ``change_pct``, ``volume``,
    ``prev_close``) are never written — those belong to the quote store.
    """
    added: set[str] = set()
    for name, value in fields.items():
        key = str(name).strip()
        if not key or key in QUOTE_ONLY_FIELDS or value is None:
            continue
        if row.get(key) is not None:
            continue
        row[key] = value
        added.add(key)
    return added


def merge_depth_observations(
    merged: dict[str, dict[str, Any]],
    observations: Mapping[str, Any],
) -> dict[str, set[str]]:
    """Merge one source's observations into the benchmark rows.

    ``observations`` maps symbol -> observation (anything carrying
    ``.symbol``, ``.fields`` and ``.source``; ``DepthObservation`` in
    production). Symbols are canonicalised (``BRK.B`` -> ``BRK-B``) before
    the merge, and a symbol the earlier stages missed gets a fresh row.
    Returns ``{source_name: keys_added}`` for the coverage report.
    """
    added: dict[str, set[str]] = {}
    for observation in observations.values():
        symbol = canonical_symbol(getattr(observation, "symbol", "") or "")
        if not symbol:
            continue
        row = merged.get(symbol)
        if row is None:
            # The universe spells class shares with a dot (``BRK.B``) while
            # the depth layer canonicalises to a dash (``BRK-B``); the row
            # the earlier stages already built is the one to fill, so a
            # class share never ends up split across two rows.
            row = merged.get(symbol.replace("-", "."))
        if row is None:
            row = merged.setdefault(symbol, {})
        keys = merge_observation_fields(row, observation.fields)
        if keys:
            added.setdefault(str(observation.source), set()).update(keys)
    return added


async def _run_source(
    source: Any, symbols: Sequence[str]
) -> tuple[Mapping[str, Any], float, str | None]:
    """One source, one clock; a failure is reported, never raised."""

    started = time.perf_counter()
    try:
        observations = await source.fetch_many(symbols)
    except Exception as exc:  # noqa: BLE001 - one source is not the benchmark
        return {}, time.perf_counter() - started, f"{type(exc).__name__}: {exc}"
    return observations, time.perf_counter() - started, None


async def depth_stage(
    merged: dict[str, dict[str, Any]],
    symbols: Sequence[str],
    *,
    finra_source: Any | None = None,
    official_source: Any | None = None,
    average_volume: Mapping[str, Any] | None = None,
) -> tuple[dict[str, set[str]], dict[str, float], dict[str, str]]:
    """Run the keyless depth sources and merge their fields into ``merged``.

    FINRA's consolidated short interest file and the official index
    membership files (SSGA / Nasdaq / iShares) both answer without an API
    key. The two fetches run concurrently, each on its own clock, and a
    failure degrades to "adds nothing" rather than failing the benchmark.
    ``average_volume`` is FINRA's reference map for ``short_ratio``: its own
    ``daysToCover`` matches Finviz only 57/98, while the quotient matches
    97/98, so the divisor comes from the caller. Shares float is not on the
    fast path, so ``short_float`` stays absent rather than guessed.

    Returns ``(added_keys_by_source, wall_seconds_by_source, errors)``.
    """
    finra = finra_source if finra_source is not None else FinraShortSource()
    official = (
        official_source
        if official_source is not None
        # optionable stays off: the Cboe HEAD probe 429s at universe scale.
        else OfficialDepthSource(optionable=False)
    )
    if average_volume and hasattr(finra, "set_average_volume"):
        finra.set_average_volume(average_volume)

    (finra_obs, finra_s, finra_err), (official_obs, official_s, official_err) = (
        await asyncio.gather(
            _run_source(finra, symbols), _run_source(official, symbols)
        )
    )

    added = merge_depth_observations(merged, finra_obs)
    for source_name, keys in merge_depth_observations(merged, official_obs).items():
        added.setdefault(source_name, set()).update(keys)

    timings = {
        "finra": finra_s,
        "official": official_s,
        "total": max(finra_s, official_s),
    }
    errors = {
        name: message
        for name, message in (("finra", finra_err), ("official", official_err))
        if message is not None
    }
    return added, timings, errors


def coverage_of(
    merged: Mapping[str, Mapping[str, Any]], catalog_keys: Sequence[str]
) -> dict[str, Any]:
    """Coverage against the 116-key catalog: filled keys + per-symbol stats."""

    filled = {
        key
        for key in catalog_keys
        if any(row.get(key) is not None for row in merged.values())
    }
    per_symbol = sorted(
        sum(1 for value in row.values() if value is not None)
        for row in merged.values()
    )
    median = per_symbol[len(per_symbol) // 2] if per_symbol else 0
    return {
        "coverage": f"{len(filled)}/{len(catalog_keys)}",
        "filled": len(filled),
        "total_keys": len(catalog_keys),
        "filled_keys": sorted(filled),
        "median_fields": median,
        "min_fields": per_symbol[0] if per_symbol else 0,
        "max_fields": per_symbol[-1] if per_symbol else 0,
    }


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
    # Four quarters: the annual row lives in whichever quarter carried the
    # 10-K, and fiscal year ends differ (Sept FY → 2025q4, Dec FY → 2026q1,
    # June FY → the current quarter), so one prior quarter is not enough.
    quarters = [quarter]
    for back in range(1, 4):
        prev = previous_quarter(quarter, back)
        prev_path = dataset_cache_path(CACHE, prev)
        if not prev_path.exists():
            await download_dataset(prev, cache_dir=CACHE)
        quarters.append(prev)
    paths = [dataset_cache_path(CACHE, q) for q in quarters]
    wanted = {ciks[s] for s in symbols if s in ciks}
    index = build_quarters(paths, ciks=wanted)
    print(
        f"sec_bulk quarters={'+'.join(quarters)} ciks={len(index.ciks())} "
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

    # --- Coverage: before the keyless depth sources ------------------------
    catalog = json.loads(CATALOG.read_text())
    names = [c["key"] for c in catalog]
    before = coverage_of(merged, names)
    print(
        f"coverage_before={before['coverage']} "
        f"median_fields={before['median_fields']}",
        flush=True,
    )

    # --- FINRA short interest + official index membership (keyless) --------
    # The Yahoo v7 batch already measured each symbol's 3-month average
    # volume; hand it to FINRA as the short_ratio divisor. Shares float is
    # not on the fast path, so short_float stays absent rather than guessed.
    average_volume = {
        sym: row["average_volume"]
        for sym, row in batch_quotes.items()
        if row.get("average_volume")
    }
    added, timings, errors = await depth_stage(
        merged, symbols, average_volume=average_volume
    )
    added_report = {name: sorted(keys) for name, keys in sorted(added.items())}
    print(
        f"depth finra={timings.get('finra', 0.0):.1f}s "
        f"official={timings.get('official', 0.0):.1f}s "
        f"total={timings.get('total', 0.0):.1f}s "
        f"added={added_report} errors={errors}",
        flush=True,
    )

    # --- Coverage: after ----------------------------------------------------
    after = coverage_of(merged, names)
    print(
        json.dumps(
            {
                "coverage_before": before["coverage"],
                "coverage_after": after["coverage"],
                "delta_fields": after["filled"] - before["filled"],
                "added_by_source": added_report,
                "median_fields": after["median_fields"],
                "min_fields": after["min_fields"],
                "max_fields": after["max_fields"],
            },
            indent=1,
        )
    )
    out = Path("/home/ubuntu/briefs/market-engine/research/fast-path-100.json")
    out.write_text(json.dumps({"merged": merged}, indent=1))
    print(f"written {out}")


if __name__ == "__main__":
    asyncio.run(main())
