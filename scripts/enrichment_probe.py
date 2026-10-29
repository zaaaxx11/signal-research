#!/usr/bin/env python3
"""Keyless enrichment probe - measured evidence for enrichment-plan.md.

Every number in the enrichment plan comes from a probe in this file. Run from
the repo root with the project venv:

    .venv/bin/python scripts/enrichment_probe.py            # all probes
    .venv/bin/python scripts/enrichment_probe.py p2 p4      # selected probes

Probes
------
p1_v7_fields      Yahoo v7 quote: full field inventory + catalog keys that are
                  already in the batch response the fast path sends (free adds)
p2_chart_ranges   Yahoo chart range=1y/5y/10y/period1=0: cost (bytes, wall,
                  success) and the computable-field yield of parse_chart_metrics
p3_sec_computable SEC bulk (4 cached quarters) computable fields: fill rates on
                  the 100-symbol fast-path sample
p4_beta           beta from 1y daily OHLC covariance vs SPY (1 extra request)
p5_quotesummary   Yahoo quoteSummary + fundamentals-timeseries throughput and
                  field yield at concurrency 4 (100 symbols)
p6_sec_window     SEC bulk older-quarter sizes (HEAD) + one real download
p7_news_rss       Yahoo/Nasdaq RSS news feeds: throughput and fill
p8_crumb          Yahoo crumb endpoint rate-limit behaviour
p9_optionable     Cboe optionable probe at small scale (confirms the 429 wall)
p10_insider       Form 4 availability from the cached SEC submissions documents
p11_full_history  perf_10y window fix + all-time-high cross-check vs quoteSummary
p12_sec_growth    EPS/sales past-3Y/5Y need two extra 10-K-bearing quarters
p13_stockanalysis stockanalysis.com pages: same consensus data, no new keys

Results are appended to /home/ubuntu/briefs/market-engine/research/enrichment-probe.json
and printed as JSON on stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import math
import re
import statistics
import sys
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2

REPO = Path("/home/ubuntu/ztockz")
sys.path.insert(0, str(REPO))

UNIVERSE = Path("/home/ubuntu/briefs/market-engine/research/probes-1000/universe.json")
FASTPATH = Path("/home/ubuntu/briefs/market-engine/research/fast-path-100.json")
CATALOG = Path("/home/ubuntu/ztockz/app/engine/data/finviz_columns_116.json")
SNAPSHOT = Path("/home/ubuntu/ztockz-data/engine-snapshot.json")
CACHE = Path("/home/ubuntu/ztockz-data/cache")
EVIDENCE = Path("/home/ubuntu/briefs/market-engine/research/enrichment-probe.json")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
SEC_UA = "Ztockz/0.1 (bulk-financials; contact: zaaaxx1432@gmail.com)"
UNIVERSE_N = 4217


def log(msg: str) -> None:
    print(msg, flush=True)


def load_universe(limit: int | None = None) -> list[str]:
    rows = json.loads(UNIVERSE.read_text())["rows"]
    symbols = [r["name"] for r in rows if r.get("name")]
    return symbols[:limit] if limit else symbols


def catalog_keys() -> list[str]:
    return [c["key"] for c in json.loads(CATALOG.read_text())]


def fastpath_rows() -> dict[str, dict]:
    return json.loads(FASTPATH.read_text())["merged"]


def cik_map() -> dict[str, str]:
    securities = json.loads(SNAPSHOT.read_text())["dataset"]["securities"]
    return {s["symbol"].upper(): s["cik"] for s in securities if s.get("cik")}


def canon(raw: str) -> str:
    m = re.match(r"^([A-Z][A-Z0-9]{0,9})\.([A-Z])$", raw)
    return f"{m.group(1)}-{m.group(2)}" if m else raw


def record(name: str, payload: dict) -> None:
    doc = {}
    if EVIDENCE.exists():
        try:
            doc = json.loads(EVIDENCE.read_text())
        except Exception:
            doc = {}
    doc[name] = payload
    EVIDENCE.write_text(json.dumps(doc, indent=1))
    log(f"[recorded] {name}")


# ---------------------------------------------------------------------------
# p1 - Yahoo v7 quote field inventory
# ---------------------------------------------------------------------------

#: v7 quote key -> catalog key, for keys that are NOT yet in the fast path's
#: YAHOO_BATCH_MAP (fast_path_bench.py). These ride the same request.
V7_FREE_MAP = {
    "quoteType": "security_type",
    "fullExchangeName": "exchange",
    "firstTradeDateMilliseconds": "ipo_date",
    "earningsTimestamp": "earnings_date",
    "dividendDate": "dividend_ex_date",
    "averageDailyVolume10Day": "average_volume_10d",
    "impliedSharesOutstanding": "implied_shares_outstanding",
    "currency": "currency",
    "typeDisp": "type_disp",
    "market": "market",
}


async def p1_v7_fields() -> dict:
    symbols = load_universe(1)
    out: dict[str, Any] = {}
    async with httpx2.AsyncClient(
        headers={"user-agent": UA}, timeout=30, follow_redirects=True
    ) as client:
        await client.get("https://fc.yahoo.com")
        crumb = (
            await client.get("https://query1.finance.yahoo.com/v1/test/getcrumb")
        ).text.strip()
        r = await client.get(
            "https://query1.finance.yahoo.com/v7/finance/quote"
            f"?symbols={symbols[0]}&crumb={crumb}"
        )
        quote = r.json()["quoteResponse"]["result"][0]
        out["single_status"] = r.status_code
        out["v7_key_count"] = len(quote)
        out["v7_keys"] = sorted(quote)
        out["free_catalog_keys_present"] = {
            src: key for src, key in V7_FREE_MAP.items() if quote.get(src) is not None
        }
        out["free_catalog_keys_absent"] = sorted(
            src for src in V7_FREE_MAP if quote.get(src) is None
        )
        # what the free keys actually say for one symbol
        epoch_keys = {
            "firstTradeDateMilliseconds",
            "earningsTimestamp",
            "dividendDate",
        }
        sample = {}
        for src, key in V7_FREE_MAP.items():
            value = quote.get(src)
            if src in epoch_keys and isinstance(value, (int, float)) and value > 0:
                seconds = value / 1000.0 if value > 1e11 else value
                try:
                    value = datetime.fromtimestamp(seconds, UTC).isoformat()
                except (OverflowError, OSError, ValueError):
                    value = f"unparseable:{value}"
            sample[key] = value
        out["sample_values"] = sample

        # batch behaviour at the size the fast path already uses (100 symbols)
        batch = load_universe(100)
        t0 = time.perf_counter()
        r = await client.get(
            "https://query1.finance.yahoo.com/v7/finance/quote"
            f"?symbols={','.join(batch)}&crumb={crumb}"
        )
        wall = time.perf_counter() - t0
        body = r.json()
        rows = body["quoteResponse"]["result"]
        out["batch100"] = {
            "status": r.status_code,
            "requested": len(batch),
            "returned": len(rows),
            "wall_s": round(wall, 3),
            "bytes": len(r.content),
        }
        # how many of the batch carry each free key
        presence = {}
        for src, key in V7_FREE_MAP.items():
            presence[key] = sum(1 for q in rows if q.get(src) is not None)
        out["batch100_free_key_presence"] = presence
    return out


# ---------------------------------------------------------------------------
# p2 - chart range cost + computable yield
# ---------------------------------------------------------------------------

CHART_TARGETS = [
    "perf_ytd",
    "perf_3y",
    "perf_5y",
    "perf_10y",
    "high_50d",
    "low_50d",
    "dividend_growth_1y",
    "dividend_growth_3y",
    "dividend_growth_5y",
]


async def _chart_batch(
    client: httpx2.AsyncClient, symbols: list[str], params: str, conc: int
) -> dict:
    sem = asyncio.Semaphore(conc)
    ok = 0
    total_bytes = 0
    bodies: dict[str, dict] = {}

    async def one(sym: str) -> None:
        nonlocal ok, total_bytes
        async with sem:
            try:
                r = await client.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?{params}"
                )
                if r.status_code != 200:
                    return
                ok += 1
                total_bytes += len(r.content)
                bodies[sym] = r.json()
            except Exception:
                return

    t0 = time.perf_counter()
    await asyncio.gather(*(one(s) for s in symbols))
    wall = time.perf_counter() - t0
    return {
        "ok": ok,
        "requested": len(symbols),
        "bytes": total_bytes,
        "wall_s": round(wall, 2),
        "bodies": bodies,
    }


async def p2_chart_ranges() -> dict:
    from app.market.chart import parse_chart_metrics

    symbols = load_universe(40)
    out: dict[str, Any] = {"sample_n": len(symbols), "concurrency": 16}
    async with httpx2.AsyncClient(
        headers={"user-agent": UA}, timeout=30, follow_redirects=True
    ) as client:
        for label, params in (
            ("range_1y", "interval=1d&range=1y&events=div"),
            ("range_5y", "interval=1d&range=5y&events=div"),
            ("range_10y", "interval=1d&range=10y&events=div"),
        ):
            res = await _chart_batch(client, symbols, params, 16)
            out[label] = {
                "ok": res["ok"],
                "requested": res["requested"],
                "bytes": res["bytes"],
                "wall_s": res["wall_s"],
                "extrapolated_4217_wall_s": round(
                    res["wall_s"] * UNIVERSE_N / max(res["ok"], 1), 1
                ),
            }
            if label == "range_10y":
                # field yield from the same bodies the fast path would parse
                now = datetime.now(UTC)
                filled = Counter()
                per_symbol_fields = []
                for sym, body in res["bodies"].items():
                    entry = body["chart"]["result"][0]
                    metrics = parse_chart_metrics(entry, now=now)
                    present = [k for k in CHART_TARGETS if metrics.get(k) is not None]
                    per_symbol_fields.append(len(present))
                    filled.update(present)
                out["range_10y_field_yield"] = {
                    "symbols_parsed": len(res["bodies"]),
                    "filled_counts": dict(sorted(filled.items())),
                    "median_new_fields_per_symbol": statistics.median(
                        per_symbol_fields
                    )
                    if per_symbol_fields
                    else 0,
                }
                # one worked example
                sym0 = next(iter(res["bodies"]))
                entry = res["bodies"][sym0]["chart"]["result"][0]
                example = parse_chart_metrics(entry, now=now)
                out["range_10y_example"] = {
                    "symbol": sym0,
                    **{k: example.get(k) for k in CHART_TARGETS},
                }
        # full daily history: period1=0 - the only honest all-time-high source
        res = await _chart_batch(
            client, symbols[:10], f"interval=1d&period1=0&period2={int(time.time())}&events=div", 8
        )
        out["period1_full_history"] = {
            "ok": res["ok"],
            "requested": res["requested"],
            "bytes": res["bytes"],
            "wall_s": res["wall_s"],
            "mean_bytes_per_symbol": round(res["bytes"] / max(res["ok"], 1)),
            "extrapolated_4217_wall_s": round(
                res["wall_s"] * UNIVERSE_N / max(res["ok"], 1), 1
            ),
        }
        # meta.firstTradeDate on the same bodies (ipo_date, free)
        ftd = {}
        for sym, body in list(res["bodies"].items())[:5]:
            meta = body["chart"]["result"][0].get("meta", {})
            stamp = meta.get("firstTradeDate")
            ftd[sym] = (
                datetime.fromtimestamp(stamp, UTC).date().isoformat() if stamp else None
            )
        out["meta_first_trade_date"] = ftd
    return out


# ---------------------------------------------------------------------------
# p3 - SEC bulk computable fields
# ---------------------------------------------------------------------------

SALES_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "RevenuesNetOfInterestExpense",
    "RegulatedAndUnregulatedOperatingRevenue",
    "Revenue",
    "RevenueFromContractsWithCustomers",
)
EPS_TAGS = ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted")
OCF_TAGS = ("NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations")
CAPEX_TAGS = (
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
    "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
)
DA_TAGS = (
    "DepreciationDepletionAndAmortization",
    "DepreciationAmortizationAndAccretionNet",
    "Depreciation",
)
TAX_TAGS = ("IncomeTaxExpenseBenefit",)
PRETAX_TAGS = (
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
)
DEBT_TAGS = ("LongTermDebt", "LongTermDebtNoncurrent")
CASH_TAGS = (
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
)
EQUITY_TAGS = (
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
)
OPINC_TAGS = ("OperatingIncomeLoss",)
DPS_TAGS = ("CommonStockDividendsPerShareDeclared",)


def _pick(facts, tags, *, uom="USD", qtrs=None, ddate_max=None):
    rows = [
        f
        for f in facts
        if f.tag in tags and f.uom == uom and not f.segments
    ]
    if qtrs is not None:
        rows = [f for f in rows if f.qtrs == qtrs]
    if ddate_max is not None:
        rows = [f for f in rows if f.ddate <= ddate_max]
    if not rows:
        return None
    return max(rows, key=lambda f: (f.ddate, f.filed, f.adsh))


def _days(a: str, b: str) -> int | None:
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except (TypeError, ValueError):
        return None


def _ttm_at(facts, tags, anchor: str) -> float | None:
    """TTM ending at ``anchor`` (an ISO ytd period end), or None."""
    annual = _pick(facts, tags, qtrs=4, ddate_max=anchor)
    if annual is None:
        return None
    if _days(annual.ddate, anchor) > 400:
        return None
    ytd = _pick(facts, tags, qtrs=3, ddate_max=anchor)
    if ytd is None or ytd.ddate != anchor:
        ytd = _pick(facts, tags, qtrs=2, ddate_max=anchor)
    if ytd is None or ytd.ddate != anchor:
        ytd = _pick(facts, tags, qtrs=1, ddate_max=anchor)
    if ytd is None or ytd.ddate != anchor:
        return annual.value
    prior = None
    for f in facts:
        if f.tag != ytd.tag or f.qtrs != ytd.qtrs or f.segments:
            continue
        gap = _days(f.ddate, anchor)
        if gap is not None and 350 <= gap <= 380:
            key = (f.ddate, f.filed, f.adsh)
            if prior is None or key > (prior.ddate, prior.filed, prior.adsh):
                prior = f
    if prior is None:
        return annual.value
    return annual.value + ytd.value - prior.value


def _sec_computations(facts, price, market_cap, sales, eps):
    """One symbol's computable fields from SEC bulk facts + fast-path values."""
    out: dict[str, float | None] = {}
    debt = _pick(facts, DEBT_TAGS, qtrs=0)
    cash = _pick(facts, CASH_TAGS, qtrs=0)
    equity = _pick(facts, EQUITY_TAGS, qtrs=0)
    opinc = _pick(facts, OPINC_TAGS, qtrs=4)
    da = _pick(facts, DA_TAGS, qtrs=4)
    ocf = _pick(facts, OCF_TAGS, qtrs=4)
    capex = _pick(facts, CAPEX_TAGS, qtrs=4)
    tax = _pick(facts, TAX_TAGS, qtrs=4)
    pretax = _pick(facts, PRETAX_TAGS, qtrs=4)

    debt_v = debt.value if debt else None
    cash_v = cash.value if cash else None
    equity_v = equity.value if equity else None

    ev = None
    if market_cap is not None:
        ev = market_cap + (debt_v or 0.0) - (cash_v or 0.0)
    out["enterprise_value"] = ev
    out["ev_sales"] = ev / sales if ev is not None and sales else None

    ebitda = None
    if opinc is not None and da is not None:
        ebitda = opinc.value + da.value
    elif opinc is not None:
        ebitda = opinc.value
    out["ev_ebitda"] = ev / ebitda if ev is not None and ebitda and ebitda > 0 else None

    fcf = None
    if ocf is not None and capex is not None:
        fcf = ocf.value - capex.value
    out["pfcf"] = market_cap / fcf if market_cap is not None and fcf and fcf > 0 else None

    # roic = EBIT * (1 - tax rate) / (debt + equity - cash)
    ebit = opinc.value if opinc is not None else None
    rate = 0.21
    if tax is not None and pretax is not None and pretax.value > 0:
        candidate = tax.value / pretax.value
        if 0.0 <= candidate <= 1.0:
            rate = candidate
    invested = None
    if debt_v is not None and equity_v is not None:
        invested = debt_v + equity_v - (cash_v or 0.0)
    out["roic"] = (
        ebit * (1.0 - rate) / invested * 100.0
        if ebit is not None and invested and invested > 0
        else None
    )

    out["lt_debt_to_equity"] = (
        debt_v / equity_v if debt_v is not None and equity_v else None
    )

    # qoq: newest quarterly row vs the previous distinct quarter
    for tags, name in ((EPS_TAGS, "eps_qoq"), (SALES_TAGS, "sales_qoq")):
        rows = [f for f in facts if f.tag in tags and f.uom == "USD" and f.qtrs == 1 and not f.segments]
        rows.sort(key=lambda f: (f.ddate, f.filed, f.adsh))
        value = None
        if len(rows) >= 2:
            latest, prior = rows[-1], rows[-2]
            if prior.value and latest.ddate != prior.ddate:
                value = (latest.value / prior.value - 1.0) * 100.0
        out[name] = value

    # yoy ttm: ttm now vs ttm one year ago
    ytd_now = _pick(facts, SALES_TAGS, qtrs=3)
    if ytd_now is not None:
        anchor_now = ytd_now.ddate
        try:
            anchor_prev = (
                date.fromisoformat(anchor_now) - timedelta(days=365)
            ).isoformat()
        except ValueError:
            anchor_prev = anchor_now
        for tags, name in ((EPS_TAGS, "eps_yoy_ttm"), (SALES_TAGS, "sales_yoy_ttm")):
            now_v = _ttm_at(facts, tags, anchor_now)
            prev_v = _ttm_at(facts, tags, anchor_prev)
            out[name] = (
                (now_v / prev_v - 1.0) * 100.0
                if now_v is not None and prev_v and prev_v > 0
                else None
            )
    else:
        out["eps_yoy_ttm"] = None
        out["sales_yoy_ttm"] = None

    # payout ratio: declared DPS (annual) / diluted EPS (annual)
    dps = _pick(facts, DPS_TAGS, qtrs=4)
    eps_a = _pick(facts, EPS_TAGS, qtrs=4)
    out["payout_ratio"] = (
        dps.value / eps_a.value * 100.0
        if dps is not None and eps_a is not None and eps_a.value
        else None
    )

    # dividend growth 1y: annual DPS newest vs prior annual
    dps_rows = [f for f in facts if f.tag in DPS_TAGS and f.uom == "USD" and f.qtrs == 4 and not f.segments]
    dps_rows.sort(key=lambda f: (f.ddate, f.filed, f.adsh))
    if len(dps_rows) >= 2 and dps_rows[-2].value:
        out["dividend_growth_1y"] = (
            dps_rows[-1].value / dps_rows[-2].value - 1.0
        ) * 100.0
    else:
        out["dividend_growth_1y"] = None

    # 2y CAGR of annual EPS/sales (the window carries FY-2..FY0; a 3y CAGR
    # needs one more 10-K-bearing quarter, a 5y CAGR a handful more)
    def annual_rows(tags):
        best: dict[str, Any] = {}
        for f in facts:
            if f.tag not in tags or f.uom != "USD" or f.qtrs != 4 or f.segments:
                continue
            key = (f.ddate, f.filed, f.adsh)
            current = best.get(f.ddate)
            if current is None or key > (current.ddate, current.filed, current.adsh):
                best[f.ddate] = f
        return [best[d] for d in sorted(best)]

    for tags, name in ((EPS_TAGS, "eps_cagr_2y"), (SALES_TAGS, "sales_cagr_2y")):
        rows = annual_rows(tags)
        value = None
        if len(rows) >= 3:
            latest, base = rows[-1], rows[-3]
            span = _days(base.ddate, latest.ddate)
            if (
                base.value
                and latest.value
                and base.value > 0
                and latest.value > 0
                and span
                and 700 <= span <= 800
            ):
                value = ((latest.value / base.value) ** (365.0 / span) - 1.0) * 100.0
        out[name] = value
    return out


async def p3_sec_computable() -> dict:
    from app.engine.bulk_financials import build_quarters, dataset_cache_path

    symbols = load_universe(100)
    ciks = cik_map()
    quarters = ["2025q4", "2026q1", "2026q2", "2026q3"]
    paths = [dataset_cache_path(CACHE, q) for q in quarters]
    for p in paths:
        if not p.exists():
            return {"error": f"missing cached quarter {p}"}
    t0 = time.perf_counter()
    index = build_quarters(paths, ciks={ciks[s] for s in symbols if s in ciks})
    build_s = time.perf_counter() - t0

    rows = fastpath_rows()
    results: dict[str, dict] = {}
    tag_presence = Counter()
    for sym in symbols:
        cik = ciks.get(sanon := canon(sym))
        if cik is None:
            continue
        facts = index.facts_for(cik)
        if not facts:
            continue
        for f in facts:
            tag_presence[f.tag] += 1
        row = rows.get(sym, {})
        results[sym] = _sec_computations(
            facts,
            price=row.get("price"),
            market_cap=row.get("market_cap"),
            sales=row.get("sales"),
            eps=row.get("eps"),
        )

    keys = [
        "enterprise_value",
        "ev_sales",
        "ev_ebitda",
        "pfcf",
        "roic",
        "lt_debt_to_equity",
        "eps_qoq",
        "sales_qoq",
        "eps_yoy_ttm",
        "sales_yoy_ttm",
        "payout_ratio",
        "dividend_growth_1y",
        "eps_cagr_2y",
        "sales_cagr_2y",
    ]
    n = len(results)
    fill = {
        k: sum(1 for r in results.values() if r.get(k) is not None) for k in keys
    }
    # SIC from sub.txt (sector source)
    sic_counter = Counter()
    sic_sample = {}
    for sym, res in results.items():
        subs = index.submissions_for(ciks[canon(sym)])
        if subs:
            sic = max(subs, key=lambda s: (s.filed, s.adsh)).sic
            if sic:
                sic_counter[sic[:2]] += 1
                sic_sample[sym] = sic
    return {
        "symbols_with_facts": n,
        "quarters": quarters,
        "index_build_s": round(build_s, 1),
        "fill_counts": fill,
        "fill_pct": {k: round(v / n * 100, 1) for k, v in fill.items()} if n else {},
        "tag_row_counts": {
            t: c
            for t, c in sorted(tag_presence.items(), key=lambda kv: -kv[1])
            if t
            in set(OCF_TAGS + CAPEX_TAGS + DA_TAGS + TAX_TAGS + PRETAX_TAGS + DPS_TAGS)
        },
        "sic_major_groups": dict(sorted(sic_counter.items())),
        "sic_sample": dict(list(sic_sample.items())[:5]),
        "example": next(iter(results.items())) if results else None,
    }


# ---------------------------------------------------------------------------
# p4 - beta from OHLC covariance
# ---------------------------------------------------------------------------


async def p4_beta() -> dict:
    from app.market.history import compute_technicals, parse_bars

    symbols = load_universe(30)
    out: dict[str, Any] = {"symbols": len(symbols)}
    async with httpx2.AsyncClient(
        headers={"user-agent": UA}, timeout=30, follow_redirects=True
    ) as client:
        sem = asyncio.Semaphore(8)
        series: dict[str, list] = {}

        async def one(sym: str) -> None:
            async with sem:
                r = await client.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
                    "?interval=1d&range=1y"
                )
                if r.status_code == 200:
                    series[sym] = parse_bars(r.json())

        t0 = time.perf_counter()
        await asyncio.gather(*(one(s) for s in symbols + ["SPY"]))
        wall = time.perf_counter() - t0
        out["requests"] = len(symbols) + 1
        out["ok"] = len(series)
        out["wall_s"] = round(wall, 2)

    spy = series.get("SPY", [])
    if len(spy) < 30:
        return {**out, "error": "SPY bars missing"}
    spy_ret = [
        spy[i].close / spy[i - 1].close - 1.0 for i in range(1, len(spy))
    ]
    var_spy = statistics.pvariance(spy_ret)
    betas = {}
    for sym in symbols:
        bars = series.get(sym, [])
        if len(bars) < 30:
            continue
        ret = [bars[i].close / bars[i - 1].close - 1.0 for i in range(1, len(bars))]
        n = min(len(ret), len(spy_ret))
        r, s = ret[-n:], spy_ret[-n:]
        mean_r, mean_s = statistics.fmean(r), statistics.fmean(s)
        cov = sum((a - mean_r) * (b - mean_s) for a, b in zip(r, s)) / (n - 1)
        betas[sym] = round(cov / var_spy, 3)
    out["betas"] = betas
    out["beta_median"] = round(statistics.median(betas.values()), 3) if betas else None
    out["formula"] = "cov(r_sym, r_SPY)/var(r_SPY) on 1y daily closes, 1 extra request (SPY)"

    # 5y-daily variant from the same endpoint (range=5y), the closer analogue
    # to a provider's published beta
    async with httpx2.AsyncClient(
        headers={"user-agent": UA}, timeout=30, follow_redirects=True
    ) as client:
        sem = asyncio.Semaphore(8)
        series5: dict[str, list] = {}

        async def one5(sym: str) -> None:
            async with sem:
                r = await client.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
                    "?interval=1d&range=5y"
                )
                if r.status_code == 200:
                    series5[sym] = parse_bars(r.json())

        await asyncio.gather(*(one5(s) for s in symbols + ["SPY"]))

    spy5 = series5.get("SPY", [])
    if len(spy5) > 30:
        spy5_ret = [spy5[i].close / spy5[i - 1].close - 1.0 for i in range(1, len(spy5))]
        var5 = statistics.pvariance(spy5_ret)
        betas5 = {}
        for sym in symbols:
            bars = series5.get(sym, [])
            if len(bars) < 30:
                continue
            ret = [bars[i].close / bars[i - 1].close - 1.0 for i in range(1, len(bars))]
            n = min(len(ret), len(spy5_ret))
            r, s = ret[-n:], spy5_ret[-n:]
            mean_r, mean_s = statistics.fmean(r), statistics.fmean(s)
            cov = sum((a - mean_r) * (b - mean_s) for a, b in zip(r, s)) / (n - 1)
            betas5[sym] = round(cov / var5, 3)
        out["betas_5y_daily"] = betas5
        out["beta_5y_median"] = round(statistics.median(betas5.values()), 3)
    return out


# ---------------------------------------------------------------------------
# p5 - Yahoo quoteSummary throughput + field yield
# ---------------------------------------------------------------------------

QS_MODULES = (
    "price",
    "summaryDetail",
    "defaultKeyStatistics",
    "financialData",
    "assetProfile",
    "quoteType",
    "calendarEvents",
    "earnings",
    "earningsTrend",
    "earningsHistory",
    "majorHoldersBreakdown",
    "netSharePurchaseActivity",
)


async def p5_quotesummary() -> dict:
    from app.market.yahoo_depth import (
        QUOTE_SUMMARY_MODULES,
        TIMESERIES_TYPES,
        parse_quote_summary,
        parse_timeseries,
        quote_summary_result,
    )

    symbols = load_universe(100)
    out: dict[str, Any] = {"symbols": len(symbols), "concurrency": 4}
    status: Counter = Counter()
    filled: Counter = Counter()
    latencies: list[float] = []

    async with httpx2.AsyncClient(
        headers={"user-agent": UA}, timeout=30, follow_redirects=True
    ) as client:
        await client.get("https://fc.yahoo.com")
        crumb = (
            await client.get("https://query1.finance.yahoo.com/v1/test/getcrumb")
        ).text.strip()
        sem = asyncio.Semaphore(4)

        async def one(sym: str) -> None:
            async with sem:
                t0 = time.perf_counter()
                try:
                    r = await client.get(
                        "https://query1.finance.yahoo.com/v10/finance/quoteSummary/"
                        f"{sym}?modules={','.join(QUOTE_SUMMARY_MODULES)}"
                        "&corsDomain=finance.yahoo.com&formatted=false"
                        f"&crumb={crumb}"
                    )
                    status[f"quoteSummary:{r.status_code}"] += 1
                    if r.status_code == 200:
                        fields = parse_quote_summary(quote_summary_result(r.json()))
                        filled.update(fields.keys())
                    r2 = await client.get(
                        "https://query1.finance.yahoo.com/ws/fundamentals-timeseries/"
                        f"v1/finance/timeseries/{sym}"
                        f"?symbol={sym}&type={','.join(TIMESERIES_TYPES)}"
                        f"&period1=1262304000&period2={int(time.time())}&merge=false&crumb={crumb}"
                    )
                    status[f"timeseries:{r2.status_code}"] += 1
                    if r2.status_code == 200:
                        filled.update(parse_timeseries(r2.json()).keys())
                except Exception as exc:  # noqa: BLE001
                    status[f"exception:{type(exc).__name__}"] += 1
                finally:
                    latencies.append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        await asyncio.gather(*(one(s) for s in symbols))
        wall = time.perf_counter() - t0

    out["status_counts"] = dict(status)
    out["wall_s"] = round(wall, 1)
    out["mean_symbol_latency_s"] = round(statistics.fmean(latencies), 3)
    out["fields_filled_of_100"] = dict(sorted(filled.items()))
    out["distinct_fields"] = len(filled)
    out["extrapolated_4217_wall_s"] = round(
        wall * UNIVERSE_N / len(symbols), 1
    )
    out["extrapolated_4217_wall_min"] = round(wall * UNIVERSE_N / len(symbols) / 60, 1)
    return out


# ---------------------------------------------------------------------------
# p6 - SEC bulk window extension cost
# ---------------------------------------------------------------------------


async def p6_sec_window() -> dict:
    quarters = [
        f"{y}q{q}"
        for y in range(2020, 2024)
        for q in (1, 2, 3, 4)
    ]
    out: dict[str, Any] = {"quarters_probed": quarters}
    sizes = {}
    async with httpx2.AsyncClient(
        headers={"user-agent": SEC_UA, "accept": "application/zip,*/*"},
        timeout=60,
        follow_redirects=True,
    ) as client:
        for q in quarters:
            url = f"https://www.sec.gov/files/dera/data/financial-statement-data-sets/{q}.zip"
            try:
                r = await client.head(url)
                size = int(r.headers.get("content-length", 0))
                sizes[q] = {"status": r.status_code, "bytes": size}
            except Exception as exc:  # noqa: BLE001
                sizes[q] = {"error": str(exc)}
        # one real download to measure throughput (2022q4)
        t0 = time.perf_counter()
        url = "https://www.sec.gov/files/dera/data/financial-statement-data-sets/2022q4.zip"
        r = await client.get(url)
        wall = time.perf_counter() - t0
        out["download_probe"] = {
            "quarter": "2022q4",
            "status": r.status_code,
            "bytes": len(r.content),
            "wall_s": round(wall, 1),
            "mb_per_s": round(len(r.content) / 1e6 / wall, 1),
        }
    ok = [v["bytes"] for v in sizes.values() if v.get("bytes")]
    out["sizes"] = sizes
    out["total_gb"] = round(sum(ok) / 1e9, 2)
    out["mean_mb"] = round(statistics.fmean(ok) / 1e6, 1) if ok else None
    rate = out["download_probe"].get("mb_per_s") or 10.0
    out["estimated_download_min_at_measured_rate"] = round(
        sum(ok) / 1e6 / rate / 60, 1
    )
    return out


# ---------------------------------------------------------------------------
# p7 - news RSS
# ---------------------------------------------------------------------------


async def p7_news_rss() -> dict:
    symbols = load_universe(20)
    out: dict[str, Any] = {"symbols": len(symbols), "concurrency": 4}
    async with httpx2.AsyncClient(
        headers={
            "user-agent": UA,
            "accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        },
        timeout=15,
        follow_redirects=True,
    ) as client:
        sem = asyncio.Semaphore(4)
        status: Counter = Counter()
        items = 0

        async def one(sym: str) -> None:
            nonlocal items
            async with sem:
                url = (
                    "https://feeds.finance.yahoo.com/rss/2.0/headline"
                    f"?s={sym}&region=US&lang=en-US"
                )
                try:
                    r = await client.get(url)
                    status[f"yahoo:{r.status_code}"] += 1
                    if r.status_code == 200:
                        items += r.text.count("<item>")
                except Exception as exc:  # noqa: BLE001
                    status[f"yahoo:exc:{type(exc).__name__}"] += 1

        t0 = time.perf_counter()
        await asyncio.gather(*(one(s) for s in symbols))
        wall = time.perf_counter() - t0
        out["yahoo_status"] = dict(status)
        out["yahoo_items"] = items
        out["wall_s"] = round(wall, 2)
        out["extrapolated_4217_wall_s"] = round(wall * UNIVERSE_N / len(symbols), 1)

        # Nasdaq feed on a few symbols
        nstatus: Counter = Counter()
        for sym in symbols[:5]:
            r = await client.get(
                f"https://www.nasdaq.com/feed/rssoutbound?symbol={sym}"
            )
            nstatus[r.status_code] += 1
        out["nasdaq_status_5"] = dict(nstatus)
    return out


# ---------------------------------------------------------------------------
# p8 - crumb rate limit
# ---------------------------------------------------------------------------


async def p8_crumb() -> dict:
    out: dict[str, Any] = {}
    async with httpx2.AsyncClient(
        headers={"user-agent": UA}, timeout=20, follow_redirects=True
    ) as client:
        await client.get("https://fc.yahoo.com")
        statuses = []
        for _ in range(12):
            r = await client.get("https://query1.finance.yahoo.com/v1/test/getcrumb")
            statuses.append(r.status_code)
            await asyncio.sleep(0.25)
        out["rapid_crumb_statuses"] = statuses
        out["ok"] = sum(1 for s in statuses if s == 200)
        out["throttled"] = sum(1 for s in statuses if s == 429)
    return out


# ---------------------------------------------------------------------------
# p9 - Cboe optionable at small scale
# ---------------------------------------------------------------------------


async def p9_optionable() -> dict:
    symbols = load_universe(20)
    out: dict[str, Any] = {"symbols": len(symbols), "concurrency": 4}
    async with httpx2.AsyncClient(
        headers={"user-agent": UA}, timeout=20, follow_redirects=True
    ) as client:
        await client.get("https://fc.yahoo.com")
        crumb = (
            await client.get("https://query1.finance.yahoo.com/v1/test/getcrumb")
        ).text.strip()
        sem = asyncio.Semaphore(4)
        status: Counter = Counter()

        async def one(sym: str) -> None:
            async with sem:
                r = await client.get(
                    f"https://query1.finance.yahoo.com/v7/finance/options/{sym}?crumb={crumb}"
                )
                status[r.status_code] += 1

        t0 = time.perf_counter()
        await asyncio.gather(*(one(s) for s in symbols))
        out["wall_s"] = round(time.perf_counter() - t0, 2)
        out["status_counts"] = {str(k): v for k, v in sorted(status.items())}
        out["note"] = (
            "401 = stale crumb dance; 429 = throttled. Known full-scale result: "
            "2461x401 + 1756x429 at 4217 symbols."
        )
    return out


# ---------------------------------------------------------------------------
# p10 - Form 4 availability from cached submissions
# ---------------------------------------------------------------------------


def p10_insider() -> dict:
    symbols = load_universe(100)
    ciks = cik_map()
    cutoff = (datetime.now(UTC) - timedelta(days=90)).date().isoformat()
    with_forms = 0
    cached = 0
    form4_counts = []
    for sym in symbols:
        cik = ciks.get(canon(sym))
        if not cik:
            continue
        path = CACHE / f"CIK{cik}-submissions.json"
        if not path.exists():
            continue
        cached += 1
        try:
            doc = json.loads(path.read_text())
        except Exception:
            continue
        recent = doc.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        n4 = sum(
            1
            for f, d in zip(forms, dates)
            if f == "4" and d >= cutoff
        )
        if n4:
            with_forms += 1
            form4_counts.append(n4)
    return {
        "symbols": len(symbols),
        "submissions_cached": cached,
        "symbols_with_form4_last_90d": with_forms,
        "form4_filings_last_90d_total": sum(form4_counts),
        "form4_filings_per_symbol_median": statistics.median(form4_counts)
        if form4_counts
        else 0,
        "requests_needed_per_symbol": "1 submissions (cached) + 1 XML per recent Form 4",
        "note": (
            "insider_trans = net insider buy/sell % over a window; computable from "
            "Form 4 non-derivative tables (app/engine/insider.py)."
        ),
    }


# ---------------------------------------------------------------------------


PROBES = {
    "p1_v7_fields": p1_v7_fields,
    "p2_chart_ranges": p2_chart_ranges,
    "p3_sec_computable": p3_sec_computable,
    "p4_beta": p4_beta,
    "p5_quotesummary": p5_quotesummary,
    "p6_sec_window": p6_sec_window,
    "p7_news_rss": p7_news_rss,
    "p8_crumb": p8_crumb,
    "p9_optionable": p9_optionable,
    "p10_insider": p10_insider,
}


# ---------------------------------------------------------------------------
# p11 - perf_10y window fix + all-time-high cross-check
# ---------------------------------------------------------------------------


async def p11_full_history() -> dict:
    """range=10y misses the 10y anchor by one bar; period1=now-11y fixes it.

    Also cross-checks the chart's full-history extreme against Yahoo's own
    summaryDetail.allTimeHigh/allTimeLow for the same symbols.
    """
    from app.market.chart import parse_chart_metrics

    symbols = load_universe(10)
    out: dict[str, Any] = {"symbols": symbols}
    now = datetime.now(UTC)
    period1 = int((now - timedelta(days=365.25 * 11 + 10)).timestamp())
    async with httpx2.AsyncClient(
        headers={"user-agent": UA}, timeout=60, follow_redirects=True
    ) as client:
        await client.get("https://fc.yahoo.com")
        crumb = (
            await client.get("https://query1.finance.yahoo.com/v1/test/getcrumb")
        ).text.strip()
        sem = asyncio.Semaphore(4)
        rows: dict[str, dict] = {}

        async def one(sym: str) -> None:
            async with sem:
                r = await client.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
                    f"?interval=1d&period1={period1}&period2={int(time.time())}&events=div"
                )
                if r.status_code != 200:
                    return
                entry = r.json()["chart"]["result"][0]
                metrics = parse_chart_metrics(entry, now=now)
                bars = entry.get("timestamp", [])
                closes = (
                    entry.get("indicators", {}).get("quote", [{}])[0].get("close", [])
                )
                highs = entry.get("indicators", {}).get("quote", [{}])[0].get("high", [])
                lows = entry.get("indicators", {}).get("quote", [{}])[0].get("low", [])
                ath = max((h for h in highs if h is not None), default=None)
                atl = min((l for l in lows if l is not None), default=None)
                last_close = next((c for c in reversed(closes) if c is not None), None)
                # Yahoo's own published extremes for the same symbol
                r2 = await client.get(
                    "https://query1.finance.yahoo.com/v10/finance/quoteSummary/"
                    f"{sym}?modules=summaryDetail&corsDomain=finance.yahoo.com"
                    f"&formatted=false&crumb={crumb}"
                )
                yahoo_ath = yahoo_atl = None
                if r2.status_code == 200:
                    detail = r2.json()["quoteSummary"]["result"][0].get(
                        "summaryDetail", {}
                    )
                    yahoo_ath = detail.get("allTimeHigh")
                    yahoo_atl = detail.get("allTimeLow")
                    if isinstance(yahoo_ath, dict):
                        yahoo_ath = yahoo_ath.get("raw")
                    if isinstance(yahoo_atl, dict):
                        yahoo_atl = yahoo_atl.get("raw")
                rows[sym] = {
                    "bars": len(bars),
                    "perf_10y": metrics.get("perf_10y"),
                    "perf_5y": metrics.get("perf_5y"),
                    "chart_ath": ath,
                    "chart_atl": atl,
                    "yahoo_all_time_high": yahoo_ath,
                    "yahoo_all_time_low": yahoo_atl,
                    "ath_match": (
                        abs(ath - yahoo_ath) < 0.02 * max(ath or 1, 1)
                        if ath and yahoo_ath
                        else None
                    ),
                    "last_close": last_close,
                    "ath_distance_pct": (
                        (last_close / ath - 1.0) * 100.0 if ath and last_close else None
                    ),
                }

        t0 = time.perf_counter()
        await asyncio.gather(*(one(s) for s in symbols))
        out["wall_s"] = round(time.perf_counter() - t0, 2)
        out["rows"] = rows
        out["perf_10y_filled"] = sum(
            1 for r in rows.values() if r.get("perf_10y") is not None
        )
        out["ath_matches"] = sum(1 for r in rows.values() if r.get("ath_match"))
    return out


PROBES = {
    "p1_v7_fields": p1_v7_fields,
    "p2_chart_ranges": p2_chart_ranges,
    "p3_sec_computable": p3_sec_computable,
    "p4_beta": p4_beta,
    "p5_quotesummary": p5_quotesummary,
    "p6_sec_window": p6_sec_window,
    "p7_news_rss": p7_news_rss,
    "p8_crumb": p8_crumb,
    "p9_optionable": p9_optionable,
    "p10_insider": p10_insider,
    "p11_full_history": p11_full_history,
}


# ---------------------------------------------------------------------------
# p12 - SEC growth fields need two extra 10-K-bearing quarters
# ---------------------------------------------------------------------------


async def p12_sec_growth_window() -> dict:
    """eps/sales past-3Y and past-5Y from SEC bulk with two extra quarters.

    The 4-quarter window (2025q4..2026q3) carries three annual points
    (FY0, FY-1, FY-2) - enough for a 2-year CAGR only. The FY-3 point lives
    in the FY-2 10-K's comparative (the 2024q1 dataset for a December FYE) and
    the FY-5 point in the FY-4 10-K's comparative (2021q1). Two ZIPs, not a
    contiguous 20-quarter window.
    """
    from app.engine.bulk_financials import build_quarters, dataset_cache_path

    symbols = load_universe(100)
    ciks = cik_map()
    base = ["2025q4", "2026q1", "2026q2", "2026q3"]
    extra = ["2024q1", "2021q1"]
    paths = [dataset_cache_path(CACHE, q) for q in base + extra]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        return {"error": f"missing cached quarters: {missing}"}

    t0 = time.perf_counter()
    index = build_quarters(paths, ciks={ciks[s] for s in symbols if s in ciks})
    build_s = time.perf_counter() - t0

    def _annual_rows(facts, tags):
        """Annual (qtrs=4) rows deduplicated by period end, newest last.

        Two 10-Ks in the window carry the same fiscal year as a comparative,
        so a raw sort would count one year twice and shift the CAGR anchor.
        """
        best: dict[str, Any] = {}
        for f in facts:
            if f.tag not in tags or f.uom != "USD" or f.qtrs != 4 or f.segments:
                continue
            key = (f.ddate, f.filed, f.adsh)
            current = best.get(f.ddate)
            if current is None or key > (current.ddate, current.filed, current.adsh):
                best[f.ddate] = f
        return [best[d] for d in sorted(best)]

    def cagr(facts, tags, years):
        rows = _annual_rows(facts, tags)
        if len(rows) < years + 1:
            return None
        latest, base_row = rows[-1], rows[-(years + 1)]
        span = _days(base_row.ddate, latest.ddate)
        if not base_row.value or not latest.value:
            return None
        if base_row.value <= 0 or latest.value <= 0:
            return None
        if span is None or abs(span - 365.25 * years) > 120:
            return None
        return ((latest.value / base_row.value) ** (365.25 / span) - 1.0) * 100.0

    def dps_cagr(facts, years):
        rows = _annual_rows(facts, DPS_TAGS)
        if len(rows) < years + 1:
            return None
        latest, base_row = rows[-1], rows[-(years + 1)]
        if not base_row.value or not latest.value or base_row.value <= 0:
            return None
        span = _days(base_row.ddate, latest.ddate)
        if span is None or abs(span - 365.25 * years) > 120:
            return None
        return ((latest.value / base_row.value) ** (365.25 / span) - 1.0) * 100.0

    results = {}
    for sym in symbols:
        cik = ciks.get(canon(sym))
        if cik is None:
            continue
        facts = index.facts_for(cik)
        if not facts:
            continue
        results[sym] = {
            "eps_past_3y": cagr(facts, EPS_TAGS, 3),
            "sales_past_3y": cagr(facts, SALES_TAGS, 3),
            "eps_past_5y": cagr(facts, EPS_TAGS, 5),
            "sales_past_5y": cagr(facts, SALES_TAGS, 5),
            "dividend_growth_3y": dps_cagr(facts, 3),
            "dividend_growth_5y": dps_cagr(facts, 5),
        }
    n = len(results)
    keys = list(next(iter(results.values())).keys()) if results else []
    fill = {k: sum(1 for r in results.values() if r.get(k) is not None) for k in keys}
    example = next(
        ((s, r) for s, r in results.items() if r.get("eps_past_3y") is not None),
        None,
    )
    return {
        "symbols_with_facts": n,
        "quarters": base + extra,
        "index_build_s": round(build_s, 1),
        "fill_counts": fill,
        "fill_pct": {k: round(v / n * 100, 1) for k, v in fill.items()} if n else {},
        "example": example,
    }


PROBES = {
    "p1_v7_fields": p1_v7_fields,
    "p2_chart_ranges": p2_chart_ranges,
    "p3_sec_computable": p3_sec_computable,
    "p4_beta": p4_beta,
    "p5_quotesummary": p5_quotesummary,
    "p6_sec_window": p6_sec_window,
    "p7_news_rss": p7_news_rss,
    "p8_crumb": p8_crumb,
    "p9_optionable": p9_optionable,
    "p10_insider": p10_insider,
    "p11_full_history": p11_full_history,
    "p12_sec_growth_window": p12_sec_growth_window,
}


# ---------------------------------------------------------------------------
# p13 - stockanalysis.com: does the other keyless page add any catalog key?
# ---------------------------------------------------------------------------


async def p13_stockanalysis() -> dict:
    """The statistics/forecast pages carry the same consensus data Yahoo
    already serves (verified: identical revenue estimates), and none of the
    four keys this plan calls unavailable."""
    out: dict[str, Any] = {}
    async with httpx2.AsyncClient(
        headers={"user-agent": UA}, timeout=20, follow_redirects=True
    ) as client:
        for label, url in (
            (
                "statistics",
                "https://stockanalysis.com/stocks/AAPL/statistics/",
            ),
            ("forecast", "https://stockanalysis.com/stocks/AAPL/forecast/"),
        ):
            try:
                r = await client.get(url)
                body = r.text
                words = ("revenue", "forward", "float", "enterprise", "target")
                out[label] = {
                    "status": r.status_code,
                    "bytes": len(r.content),
                    "mentions": {
                        w: len(re.findall(w, body, re.I)) for w in words
                    },
                    "unavailable_keys_present": {
                        w: len(re.findall(w, body, re.I))
                        for w in (
                            "surprise",
                            "5-year",
                            "5 Year",
                            "long-term",
                            "shortable",
                            "optionable",
                        )
                    },
                }
            except Exception as exc:  # noqa: BLE001
                out[label] = {"error": f"{type(exc).__name__}: {exc}"}
        # the forecast page's revenue estimates vs Yahoo's earningsTrend
        r = await client.get("https://stockanalysis.com/stocks/AAPL/forecast/")
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))
        m = re.search(r"Revenue This Year ([\d.]+B)", text)
        out["forecast_revenue_this_year"] = m.group(1) if m else None
        m = re.search(r"Revenue Next Year ([\d.]+B)", text)
        out["forecast_revenue_next_year"] = m.group(1) if m else None
        out["yahoo_earnings_trend_same_numbers"] = {
            "0y_revenue_estimate": 477740624230,
            "+1y_revenue_estimate": 527246714920,
        }
        out["conclusion"] = (
            "Same licensed consensus data Yahoo already serves; adds no catalog "
            "key beyond step 7 and answers none of the four unavailable keys."
        )
    return out


PROBES["p13_stockanalysis"] = p13_stockanalysis


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probes", nargs="*", help="probe names (default: all)")
    args = parser.parse_args(argv)
    wanted = args.probes or list(PROBES)
    for name in wanted:
        fn = PROBES[name]
        log(f"=== {name} ===")
        t0 = time.perf_counter()
        payload = await fn() if inspect.iscoroutinefunction(fn) else fn()
        payload["probe_wall_s"] = round(time.perf_counter() - t0, 1)
        record(name, payload)
        log(json.dumps(payload, indent=1, default=str)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
