"""P6 — the US ETF universe: ``type=fund``/``subtype=etf`` with no cap floor.

Contract (PLAN-finviz-parity.md §2/§5 P0, PARITY-STATUS gap 1):

* the ETF screen is a **separate tab**, not a flag mixed into the stock screen:
  ``region=us_etf`` scans ``type=fund`` + ``subtype=etf`` on the America venue;
* the ETF universe has **no market-cap floor** (a USD 5M fund is a listed fund)
  and keeps a row whose cap the provider did not send, while the stock tab is
  byte-identical to what it was: ``type=stock``, ``subtype=common``, USD 100M;
* OTC/pink-sheet funds stay out of both;
* the live probe behind the plan answered **6,123** ETFs for
  ``type=fund&subtype=etf`` (Finviz: 6,168), so ``total=6,123`` is the gate — and
  the recorded fixture carries one OTC fund beyond that count to prove the
  exclusion is a filter, not a coincidence of the body;
* one refresh pass writes **six** snapshots: the four stock screens plus
  ``us_etf_market_cap`` and ``us_etf_turnover``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

from app.screen import refresh as refresh_mod
from app.screen import scanner as scanner_mod
from app.screen import universe as universe_mod
from tests import _screen as S

ETF_SCAN_PATH = S.FIXTURES / "etf_scan.json"

#: The 20 largest funds in the fixture, in market-cap order. Hand-written so a
#: fixture edit that changes the ranking fails here instead of passing silently.
TOP20 = [
    "SPY",
    "IVV",
    "VOO",
    "VTI",
    "QQQ",
    "VEA",
    "IEFA",
    "VTV",
    "VUG",
    "IWF",
    "VWO",
    "AGG",
    "VIG",
    "IEMG",
    "IJH",
    "VXUS",
    "VO",
    "VB",
    "IJR",
    "SCHD",
]

#: 6,123 listed ETFs + the one OTC fund the universe must drop.
ETF_UNIVERSE_TOTAL = 6_123
ETF_RAW = 6_124


@lru_cache(maxsize=1)
def etf_body() -> dict[str, Any]:
    return json.loads(ETF_SCAN_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def etf_rows() -> list[dict[str, Any]]:
    body = etf_body()
    columns = tuple(body["columns"])
    rows: list[dict[str, Any]] = []
    for entry in body["data"]:
        row = dict(zip(columns, entry["d"], strict=True))
        row["venue"], row["symbol"] = str(entry["s"]).split(":", 1)
        rows.append(row)
    return rows


def etf_web(seen: list[dict[str, Any]] | None = None) -> Any:
    """A scanner whose America endpoint answers by the ``type`` filter it sees.

    The responder echoes the columns the client asked for, so the same fixture
    serves the light browse scan and the parity scan a selection runs.
    """

    web = S.MockWeb()
    index = {(row["venue"], row["symbol"]): row for row in etf_rows()}

    def respond(request: Any) -> dict[str, Any]:
        body = json.loads((request.content or b"{}").decode("utf-8"))
        if seen is not None:
            seen.append(body)
        left = {item["left"]: item["right"] for item in body["filter"]}
        if "global" in str(request.url):
            return S.scan_body("global")
        if left.get("type") == "fund":
            columns = body["columns"]
            data = [
                {"s": f"{venue}:{symbol}", "d": [row.get(column) for column in columns]}
                for (venue, symbol), row in index.items()
            ]
            return {"totalCount": len(data), "data": data}
        return S.scan_body("us")

    web.json("tradingview", respond)
    return web


# ---------------------------------------------------------------------------
# scan_body
# ---------------------------------------------------------------------------


def test_cboe_listed_etf_is_us_not_dropped() -> None:
    """CBOE is a US ETF venue; an unmapped code silently loses ~1.7k live funds."""

    row = {
        "type": "fund",
        "subtype": "etf",
        "exchange": "CBOE",
        "symbol": "IROC",
        "name": "iShares US Aerospace & Defense ETF",
        "close": 48.68,
        "change": 0.1,
        "volume": 1000,
        "Value.Traded": 48680.0,
        "market_cap_basic": None,
        "currency": "USD",
    }
    universe = universe_mod.build_universe([row], region="us_etf")
    assert [item["symbol"] for item in universe] == ["IROC"]
    assert universe[0]["exchange"] == "XCBO"
    # The stock sentinel 0 ("admit nano caps") must not demand a reported cap
    # on the ETF tab: most funds have market_cap_basic=None.
    assert universe_mod.build_universe([row], region="us_etf", min_market_cap=0) == universe


def test_etf_aum_is_the_market_cap_column() -> None:
    """ETFs have no ``market_cap_basic``; their AUM is the size metric."""

    row = _fund("SPY", market_cap_basic=None, aum=650_000_000_000.0)
    universe = universe_mod.build_universe([row], region="us_etf")
    assert universe[0]["market_cap_usd"] == 650_000_000_000.0


def test_etf_scan_is_fund_etf_without_a_cap_floor() -> None:
    body = scanner_mod.scan_body("us", asset_class="etf")

    assert body["filter"] == [
        {"left": "type", "operation": "equal", "right": "fund"},
        {"left": "subtype", "operation": "equal", "right": "etf"},
        {"left": "exchange", "operation": "not_in_range", "right": ["OTC", "PNK"]},
    ], "the ETF scan is type=fund + subtype=etf + the OTC exclusion, and no cap floor"
    assert body["markets"] == ["america"]
    assert body["columns"] == list(scanner_mod.ETF_SCAN_COLUMNS)


def test_the_us_etf_region_infers_the_family_and_keeps_the_stock_default_intact() -> None:
    assert scanner_mod.asset_class_for("us_etf") == "etf"
    assert scanner_mod.asset_class_for("us") == "stock"
    assert scanner_mod.scan_body("us_etf")["filter"] == scanner_mod.scan_body(
        "us", asset_class="etf"
    )["filter"]
    assert scanner_mod.scan_body("us")["filter"] == [
        {"left": "type", "operation": "equal", "right": "stock"},
        {"left": "subtype", "operation": "equal", "right": "common"},
        {"left": "market_cap_basic", "operation": "greater", "right": 100_000_000},
        {"left": "exchange", "operation": "not_in_range", "right": ["OTC", "PNK"]},
    ]


def test_global_and_stock_only_combinations_are_refused() -> None:
    with pytest.raises(ValueError, match="ETF screen is US-only"):
        scanner_mod.scan_body("global", asset_class="etf")
    with pytest.raises(ValueError, match="US ETF tab"):
        scanner_mod.scan_body("us_etf", asset_class="stock")


# ---------------------------------------------------------------------------
# build_universe
# ---------------------------------------------------------------------------


def _fund(symbol: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": symbol,
        "symbol": symbol,
        "description": f"{symbol} Fund",
        "exchange": "AMEX",
        "country": "United States",
        "sector": "Financial",
        "type": "fund",
        "subtype": "etf",
        "currency": "USD",
        "close": 50.0,
        "change": 0.4,
        "volume": 100_000,
        "Value.Traded": 5_000_000.0,
        "market_cap_basic": 5_000_000.0,
        "RSI": 50.0,
        "beta_1_year": 1.0,
        "price_52_week_high": 60.0,
        "price_52_week_low": 40.0,
    }
    row.update(overrides)
    return row


def test_etf_universe_has_no_floor_but_still_drops_otc_and_stocks() -> None:
    rows = [
        _fund("SMALL", market_cap_basic=5_000_000.0),
        _fund("NOCAP", market_cap_basic=None),
        _fund("OTCETF", exchange="OTC", market_cap_basic=900_000_000_000.0),
        _fund("STOCKY", type="stock", subtype="common"),
        _fund("CEF", subtype="closed_end"),
    ]

    universe = universe_mod.build_universe(rows, region="us_etf")

    assert [row["symbol"] for row in universe] == ["SMALL", "NOCAP"], (
        "the ETF tab keeps a USD 5M fund and a fund with no reported cap, but a "
        "stock, a closed-end fund and an OTC fund are not the ETF universe"
    )
    caps = {row["symbol"]: row["market_cap_usd"] for row in universe}
    assert caps["SMALL"] == 5_000_000.0
    assert caps["NOCAP"] is None, "no floor means unknown size is kept as unknown, never 0"


def test_etf_universe_total_matches_the_live_probe() -> None:
    universe = universe_mod.build_universe(etf_rows(), region="us_etf")

    assert len(universe) == ETF_UNIVERSE_TOTAL == 6_123, (
        f"expected {ETF_UNIVERSE_TOTAL} listed ETFs (the live probe count), "
        f"got {len(universe)}"
    )
    assert ETF_RAW == len(etf_rows()) == 6_124, "the fixture carries one OTC fund too"
    symbols = {row["symbol"] for row in universe}
    assert "OTCETF" not in symbols, "an OTC fund must never reach the ETF tab"
    assert "SPY" in symbols
    assert {row["exchange"] for row in universe} <= S.US_EXCHANGES


# ---------------------------------------------------------------------------
# The API tab
# ---------------------------------------------------------------------------


async def test_etf_browse_serves_the_top_twenty_with_total_6123(
    tmp_path: Path, monkeypatch: Any
) -> None:
    seen: list[dict[str, Any]] = []
    async with S.screen_client(tmp_path, monkeypatch, web=etf_web(seen)) as harness:
        page = await harness.create_screen(region="us_etf", sort="market_cap")

    assert page["region"] == "us_etf"
    assert page["matched"] == ETF_UNIVERSE_TOTAL
    assert page["returned"] == S.DISPLAY_ROWS
    assert [row["symbol"] for row in page["rows"]] == TOP20
    assert page["rows"][0]["market_cap_usd"] == 650_000_000_000
    caps = [row["market_cap_usd"] for row in page["rows"]]
    assert caps == sorted(caps, reverse=True)

    assert seen, "the browse must have scanned once"
    body = seen[0]
    filters = {item["left"]: item["right"] for item in body["filter"]}
    assert filters["type"] == "fund"
    assert filters["subtype"] == "etf"
    assert "market_cap_basic" not in filters, "the ETF tab has no cap floor"
    assert body["range"] == [0, scanner_mod.FULL_SCAN_ROWS]


async def test_the_stock_tab_still_excludes_every_etf(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page = await harness.create_screen(region="us", sort="market_cap")

    symbols = {row["symbol"] for row in page["rows"]}
    assert page["matched"] == S.US_TOTAL == 23, (
        "the stock universe is unchanged by the new tab, got "
        f"total={page['total']}"
    )
    assert not symbols & set(TOP20), (
        f"the stock tab leaked ETFs: {sorted(symbols & set(TOP20))}"
    )
    for symbol in ("SPY", "VNQ"):
        assert symbol not in symbols


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


async def test_one_refresh_pass_writes_six_screens_including_the_two_etf_tabs() -> None:
    calls: list[tuple[str, str]] = []

    async def fake(region: str, sort: str, **_kwargs: Any) -> Any:
        calls.append((region, sort))
        return type(
            "Snapshot",
            (),
            {
                "screen_id": f"scr_{region}_{sort}",
                "screen": {"total_count": 1},
                "rows": [],
            },
        )()

    report = await refresh_mod.refresh_all_screens(object(), refresh=fake)  # type: ignore[arg-type]

    assert len(calls) == 6
    assert [name for name in report["screens"]] == [
        "us_market_cap",
        "us_turnover",
        "global_market_cap",
        "global_turnover",
        "us_etf_market_cap",
        "us_etf_turnover",
    ]
    assert report["ok"] == 6 and report["failed"] == 0
    assert (("us_etf", "market_cap") in calls) and (("us_etf", "turnover") in calls)
