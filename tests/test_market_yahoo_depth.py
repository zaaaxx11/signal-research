"""``YahooDepthSource`` (full): auth, 14 modules, timeseries growth, filtering.

Every test here runs against ``MockWeb`` through the one HTTP seam
(``app.providers.http.build_client``); any socket attempt is caught by the
suite-wide egress guard in ``tests/conftest.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping
from urllib.parse import unquote

import httpx2
import pytest

from app.market.depth import QUOTE_ONLY_FIELDS, canonical_symbol
from app.market.depth_fields import CATALOG_FIELDS
from app.market.venue import EXCHANGE_SOURCE_FIELD
from app.market.yahoo_depth import (
    EMITTED_FIELDS,
    QUOTE_SUMMARY_MODULES,
    SUPPORTED_FIELDS,
    TIMESERIES_TYPES,
    YahooDepthSource,
    parse_optionable,
    parse_quote_summary,
    parse_timeseries,
)
from app.providers.adapters.yahoo import BROWSER_USER_AGENT
from tests._market_quote import install_seam
from tests._phase2 import MockWeb

T0 = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)
EARNINGS_EPOCH = int(datetime(2026, 11, 2, tzinfo=UTC).timestamp())
#: Two Friday expiries, as ``/v7/finance/options`` prints them (epoch seconds).
EXPIRY_1 = int(datetime(2026, 11, 20, tzinfo=UTC).timestamp())
EXPIRY_2 = int(datetime(2026, 12, 18, tzinfo=UTC).timestamp())


# ---------------------------------------------------------------------------
# Fake Yahoo: cookie host + API host, with counters
# ---------------------------------------------------------------------------


def quote_summary_body(modules: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "quoteSummary": {
            "result": [dict(modules)] if modules is not None else None,
            "error": None,
        }
    }


def timeseries_body(series: Mapping[str, Any]) -> dict[str, Any]:
    """One block per type — the shape ``merge=false`` answers with."""

    blocks = []
    for name, points in series.items():
        blocks.append(
            {
                "meta": {"type": [name]},
                name: [
                    {"asOfDate": day, "reportedValue": {"raw": value}}
                    for day, value in points
                ],
            }
        )
    return {"timeseries": {"result": blocks, "error": None}}


def options_body(
    expirations: Any = None,
    *,
    entry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A ``/v7/finance/options`` front-chain answer (the expiry list only)."""

    block = dict(entry) if entry is not None else {"expirationDates": list(expirations or [])}
    return {"optionChain": {"result": [block], "error": None}}


class FakeYahoo:
    """Routes ``fc.yahoo.com`` and ``query1.finance.yahoo.com``, and counts."""

    def __init__(self) -> None:
        self.web = MockWeb()
        self.cookie_calls = 0
        self.crumb_calls = 0
        self.crumb_tokens: list[str] = ["test-crumb"]
        self.quote_handler: Any = None
        self.timeseries_handler: Any = None
        self.options_handler: Any = None
        self.quote_requests: list[httpx2.Request] = []
        self.timeseries_requests: list[httpx2.Request] = []
        self.options_requests: list[httpx2.Request] = []
        self.web.route("fc.yahoo.com", self._cookie)
        self.web.route("query1.finance.yahoo.com", self._api)

    # -- routes ------------------------------------------------------------

    def _cookie(self, _request: httpx2.Request) -> httpx2.Response:
        self.cookie_calls += 1
        return httpx2.Response(404, text="")

    def _api(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path.endswith("/v1/test/getcrumb"):
            token = self.crumb_tokens[min(self.crumb_calls, len(self.crumb_tokens) - 1)]
            self.crumb_calls += 1
            return httpx2.Response(200, text=token)
        if "/quoteSummary/" in path:
            self.quote_requests.append(request)
            symbol = unquote(path.split("/quoteSummary/", 1)[1]).upper()
            if self.quote_handler is not None:
                return self.quote_handler(symbol, request)
            return httpx2.Response(200, json=quote_summary_body(sample_modules(symbol)))
        if "/timeseries/" in path:
            self.timeseries_requests.append(request)
            symbol = unquote(path.rsplit("/timeseries/", 1)[1]).upper()
            if self.timeseries_handler is not None:
                return self.timeseries_handler(symbol, request)
            return httpx2.Response(200, json=timeseries_body(sample_series()))
        if "/finance/options/" in path:
            self.options_requests.append(request)
            symbol = unquote(path.rsplit("/finance/options/", 1)[1]).upper()
            if self.options_handler is not None:
                return self.options_handler(symbol, request)
            return httpx2.Response(200, json=options_body([EXPIRY_1, EXPIRY_2]))
        return httpx2.Response(404, json={"error": "unrouted path"})

    # -- helpers -----------------------------------------------------------

    def requests_for(self, symbol: str) -> list[httpx2.Request]:
        return [
            request
            for request in self.quote_requests
            if request.url.path.endswith(f"/{symbol}")
        ]

    def crumbs_seen(self) -> list[str]:
        return [
            str(request.url.params.get("crumb") or "")
            for request in self.quote_requests
            + self.timeseries_requests
            + self.options_requests
        ]


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


def sample_modules(symbol: str = "AAPL") -> dict[str, Any]:
    """A realistic 14-module answer, trimmed to the keys the parser reads."""

    return {
        "price": {
            "symbol": symbol,
            "longName": f"{symbol} Inc.",
            "marketCap": {"raw": 5_040_000_000_000},
            "exchangeName": "NasdaqGS",
            # Quote-hub keys that must never reach the depth row.
            "regularMarketPrice": 345.4,
            "regularMarketPreviousClose": 347.1,
            "regularMarketVolume": 7_840_000,
            "regularMarketOpen": 346.9,
            # A provider key that is not a catalog column.
            "someFutureYahooKey": 12.5,
        },
        "summaryDetail": {
            "trailingPE": {"raw": 39.61},
            "forwardPE": {"raw": 36.12},
            "priceToSalesTrailing12Months": {"raw": 10.8},
            "beta": {"raw": 1.069},
            "dividendRate": {"raw": 1.08},
            "dividendYield": {"raw": 0.0031},
            "trailingAnnualDividendRate": {"raw": 1.05},
            "payoutRatio": {"raw": 0.1204},
            "exDividendDate": {"raw": 1_754_784_000},
            "allTimeHigh": {"raw": 400.0},
            "allTimeLow": {"raw": 50.0},
        },
        "defaultKeyStatistics": {
            "enterpriseValue": {"raw": 5_088_000_000_000},
            "pegRatio": {"raw": 2.76},
            "priceToBook": {"raw": 46.92},
            "enterpriseToEbitda": {"raw": 30.29},
            "enterpriseToRevenue": {"raw": 10.9},
            "trailingEps": {"raw": 8.72},
            "netIncomeToCommon": {"raw": 128_930_000_000},
            "sharesOutstanding": {"raw": 14_590_000_000},
            "floatShares": {"raw": 14_570_000_000},
            "shortPercentOfFloat": {"raw": 0.009},
            "shortRatio": {"raw": 3.48},
            "bookValue": {"raw": 7.36},
        },
        "financialData": {
            "totalRevenue": {"raw": 466_820_000_000},
            "netIncomeToCommon": {"raw": 128_930_000_000},
            "grossMargins": {"raw": 0.4865},
            "operatingMargins": {"raw": 0.3262},
            "profitMargins": {"raw": 0.2762},
            "returnOnAssets": {"raw": 0.2708},
            "returnOnEquity": {"raw": 1.488},
            "currentRatio": {"raw": 1.003},
            "quickRatio": {"raw": 0.812},
            "debtToEquity": {"raw": 78.44},
            "totalCash": {"raw": 62_400_000_000},
            "freeCashflow": {"raw": 107_700_000_000},
            "totalCashPerShare": {"raw": 4.276},
            "recommendationMean": {"raw": 2.25},
            "targetMeanPrice": {"raw": 330.0},
        },
        "assetProfile": {
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "country": "United States",
            "website": "https://www.apple.com",
            "fullTimeEmployees": 150_000,
        },
        "quoteType": {
            "quoteType": "EQUITY",
            "symbol": symbol,
            "exchange": "NMS",
            "firstTradeDateEpochUtc": 345_479_400,
        },
        "calendarEvents": {
            "earnings": {"earningsDate": [{"raw": EARNINGS_EPOCH, "fmt": "2026-11-02"}]}
        },
        "earnings": {"financialsChart": {"yearly": [{"date": 2025, "revenue": 1}]}},
        "earningsTrend": {
            "trend": [
                {"period": "0q", "earningsEstimate": {"avg": {"raw": 1.979}}},
                {"period": "+1q", "earningsEstimate": {"avg": {"raw": 2.1}}},
                {
                    "period": "0y",
                    "growth": {"raw": 0.1847},
                    "earningsEstimate": {"avg": {"raw": 8.2}},
                    "yearAgoEps": {"raw": 6.9},
                },
                {"period": "+1y", "growth": {"raw": 0.082}},
            ]
        },
        "earningsHistory": {
            "history": [
                {"quarter": 1, "surprisePercent": {"raw": 0.02}},
                {"quarter": 2, "surprisePercent": {"raw": 0.0674}},
            ]
        },
        "recommendationTrend": {"trend": [{"period": "0m", "strongBuy": 20}]},
        "majorHoldersBreakdown": {
            "insidersPercentHeld": {"raw": 0.01648},
            "institutionsPercentHeld": {"raw": 0.6635},
        },
        "netSharePurchaseActivity": {
            "netPercentInsiderShares": {"raw": 0.001},
            "netInstBuyingPercent": {"raw": -0.1398},
        },
        "institutionOwnership": {"ownershipList": []},
    }


def sample_series() -> dict[str, list[tuple[str, float]]]:
    """4 fiscal years, 5 quarters and 5 trailing points with clean arithmetic."""

    annual = ["2022-09-24", "2023-09-30", "2024-09-28", "2025-09-27"]
    quarterly = [
        "2024-12-28",
        "2025-03-29",
        "2025-06-28",
        "2025-09-27",
        "2025-12-27",
    ]
    trailing = [
        "2024-10-01",
        "2025-01-01",
        "2025-04-01",
        "2025-07-01",
        "2025-10-01",
    ]
    return {
        "annualDilutedEPS": list(zip(annual, [1.0, 1.1, 1.21, 1.331])),
        "annualTotalRevenue": list(zip(annual, [100.0, 110.0, 121.0, 133.1])),
        "annualEBIT": [("2025-09-27", 130.0)],
        "annualInvestedCapital": [("2025-09-27", 400.0)],
        "annualPretaxIncome": [("2025-09-27", 150.0)],
        "annualTaxProvision": [("2025-09-27", 30.0)],
        "annualLongTermDebt": [("2025-09-27", 90.0)],
        "annualStockholdersEquity": [("2025-09-27", 60.0)],
        "quarterlyDilutedEPS": list(zip(quarterly, [1.6, 1.3, 1.4, 1.5, 2.0])),
        "quarterlyTotalRevenue": list(zip(quarterly, [32.0, 29.0, 30.0, 31.0, 40.0])),
        "trailingDilutedEPS": list(zip(trailing, [6.0, 6.3, 6.6, 7.0, 7.2])),
        "trailingTotalRevenue": list(zip(trailing, [400.0, 410.0, 420.0, 430.0, 440.0])),
    }


def build_source(**kwargs: Any) -> YahooDepthSource:
    return YahooDepthSource(clock=lambda: T0, **kwargs)


def first_value(result: Mapping[str, Any], symbol: str, field: str) -> Any:
    assert symbol in result, f"{symbol} missing from {sorted(result)}"
    return result[symbol].fields[field]


# ---------------------------------------------------------------------------
# Auth + request shape
# ---------------------------------------------------------------------------


async def test_one_cookie_and_one_crumb_serve_two_symbols(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    install_seam(monkeypatch, fake.web)
    source = build_source(concurrency=2)

    result = await source.fetch_many(["AAPL", "MSFT"])

    assert fake.cookie_calls == 1, "one cookie warm-up per batch, not per symbol"
    assert fake.crumb_calls == 1, "one crumb per batch"
    assert len(fake.quote_requests) == 2
    assert len(fake.timeseries_requests) == 2
    assert set(result) == {"AAPL", "MSFT"}
    # Every data request carries the same token.
    assert set(fake.crumbs_seen()) == {"test-crumb"}
    assert fake.web.unrouted == []


async def test_quote_summary_requests_all_fourteen_modules(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    install_seam(monkeypatch, fake.web)
    source = build_source()

    await source.fetch_many(["AAPL"])

    request = fake.quote_requests[0]
    modules = str(request.url.params.get("modules") or "").split(",")
    assert len(QUOTE_SUMMARY_MODULES) == 14
    assert modules == list(QUOTE_SUMMARY_MODULES)
    assert set(modules) == {
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
        "recommendationTrend",
        "majorHoldersBreakdown",
        "netSharePurchaseActivity",
        "institutionOwnership",
    }
    assert request.url.params.get("formatted") == "false"
    assert request.headers["user-agent"] == BROWSER_USER_AGENT
    assert "Windows NT 10.0" in request.headers["user-agent"]


async def test_timeseries_request_carries_every_type_in_one_call(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    install_seam(monkeypatch, fake.web)
    source = build_source()

    await source.fetch_many(["AAPL"])

    assert len(fake.timeseries_requests) == 1
    request = fake.timeseries_requests[0]
    types = str(request.url.params.get("type") or "").split(",")
    assert types == list(TIMESERIES_TYPES)
    assert "annualDilutedEPS" in types and "trailingDilutedEPS" in types
    assert request.url.params.get("merge") == "false"
    assert request.url.params.get("crumb") == "test-crumb"


async def test_batch_returns_one_observation_per_requested_symbol(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["AAPL"])

    observation = result["AAPL"]
    assert observation.source == "yahoo_full"
    assert observation.as_of == T0
    assert observation.symbol == "AAPL"
    assert all(value is not None for value in observation.fields.values())


# ---------------------------------------------------------------------------
# Field mapping + unit normalisation
# ---------------------------------------------------------------------------


async def test_maps_market_cap_pe_and_roe(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["AAPL"])

    assert first_value(result, "AAPL", "market_cap") == pytest.approx(5.04e12)
    assert first_value(result, "AAPL", "pe") == pytest.approx(39.61)
    assert first_value(result, "AAPL", "roe") == pytest.approx(148.8)
    assert first_value(result, "AAPL", "eps") == pytest.approx(8.72)
    assert first_value(result, "AAPL", "company") == "AAPL Inc."
    assert first_value(result, "AAPL", "sector") == "Technology"
    assert first_value(result, "AAPL", "earnings_date") == "2026-11-02"


async def test_debt_to_equity_is_divided_exactly_once(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["AAPL"])

    value = first_value(result, "AAPL", "debt_to_equity")
    assert value == pytest.approx(0.7844)
    # 78.44 would be the raw percent; 0.007844 would be a double division.
    assert value != pytest.approx(78.44)
    assert value > 0.5


async def test_dividend_yield_fraction_becomes_percent(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["AAPL"])

    assert first_value(result, "AAPL", "dividend_yield") == pytest.approx(0.31)
    assert first_value(result, "AAPL", "payout_ratio") == pytest.approx(12.04)
    assert first_value(result, "AAPL", "short_float") == pytest.approx(0.9)
    assert first_value(result, "AAPL", "insider_own") == pytest.approx(1.648)


async def test_derived_valuation_and_float_pct(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["AAPL"])

    # 5.04T / 62.4B
    assert first_value(result, "AAPL", "pc") == pytest.approx(80.7692307, rel=1e-6)
    # 5.04T / 107.7B
    assert first_value(result, "AAPL", "pfcf") == pytest.approx(46.7966573, rel=1e-6)
    assert first_value(result, "AAPL", "float_pct") == pytest.approx(
        14_570_000_000 / 14_590_000_000 * 100.0
    )


# ---------------------------------------------------------------------------
# All-time high/low: USD levels, and nothing invented
# ---------------------------------------------------------------------------


def test_all_time_high_and_low_are_read_as_price_levels() -> None:
    fields = parse_quote_summary(sample_modules("AAPL"))

    assert fields["all_time_high"] == pytest.approx(400.0)
    assert fields["all_time_low"] == pytest.approx(50.0)
    # The catalog prints a percentage distance, but the depth row stores the
    # level: a distance would need the quote hub's live price.
    assert fields["all_time_high"] > 0.0 and fields["all_time_low"] > 0.0


async def test_all_time_levels_reach_the_depth_row(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["AAPL"])

    assert first_value(result, "AAPL", "all_time_high") == pytest.approx(400.0)
    assert first_value(result, "AAPL", "all_time_low") == pytest.approx(50.0)


async def test_a_null_all_time_high_is_absent_not_zero(monkeypatch: Any) -> None:
    modules = sample_modules("AAPL")
    modules["summaryDetail"]["allTimeHigh"] = {"raw": None}
    fake = FakeYahoo()
    fake.quote_handler = lambda _symbol, _request: httpx2.Response(
        200, json=quote_summary_body(modules)
    )
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["AAPL"])

    fields = result["AAPL"].fields
    assert "all_time_high" not in fields, "null must not become 0 or a distance"
    assert fields["all_time_low"] == pytest.approx(50.0)


def test_an_absent_all_time_key_is_absent() -> None:
    modules = sample_modules("AAPL")
    modules["summaryDetail"].pop("allTimeLow")

    fields = parse_quote_summary(modules)

    assert "all_time_low" not in fields
    # Absence is per payload, not per source: the field stays supported.
    assert "all_time_low" in SUPPORTED_FIELDS
    assert fields["all_time_high"] == pytest.approx(400.0)


def test_all_time_high_accepts_a_wrapper_or_a_bare_number() -> None:
    wrapped = sample_modules("AAPL")
    wrapped["summaryDetail"]["allTimeHigh"] = {"raw": 400, "fmt": "400.00"}
    bare = sample_modules("AAPL")
    bare["summaryDetail"]["allTimeHigh"] = 400
    quoted = sample_modules("AAPL")
    quoted["summaryDetail"]["allTimeHigh"] = {"raw": "400.5"}

    assert parse_quote_summary(wrapped)["all_time_high"] == 400.0
    assert parse_quote_summary(bare)["all_time_high"] == 400.0
    assert parse_quote_summary(quoted)["all_time_high"] == pytest.approx(400.5)


def test_all_time_low_is_read_only_from_summary_detail() -> None:
    modules = sample_modules("AAPL")
    # Same-named keys elsewhere in the payload must not shadow the owner.
    modules["defaultKeyStatistics"]["allTimeLow"] = {"raw": 1.0}
    modules["price"]["allTimeLow"] = {"raw": 2.0}

    fields = parse_quote_summary(modules)

    assert fields["all_time_low"] == pytest.approx(50.0)


def test_non_finite_all_time_levels_are_absent() -> None:
    modules = sample_modules("AAPL")
    modules["summaryDetail"]["allTimeHigh"] = {"raw": float("inf")}
    modules["summaryDetail"]["allTimeLow"] = {"raw": float("nan")}

    fields = parse_quote_summary(modules)

    assert "all_time_high" not in fields
    assert "all_time_low" not in fields
    # No fabricated 0.0 stands in for either cell.
    assert fields["pe"] == pytest.approx(39.61)


# ---------------------------------------------------------------------------
# Dividend growth: no honest source in this payload, so absent
# ---------------------------------------------------------------------------


def test_dividend_growth_is_absent_and_not_faked() -> None:
    modules = sample_modules("AAPL")
    # Both dividend-adjacent keys Yahoo actually ships in these modules.
    modules["summaryDetail"]["fiveYearAvgDividendYield"] = {"raw": 0.53}
    modules["defaultKeyStatistics"]["lastDividendValue"] = {"raw": 0.25}
    # A key Yahoo does not ship: it must not become a growth rate either.
    modules["summaryDetail"]["dividendGrowth"] = {"raw": 12.0}

    fields = parse_quote_summary(modules)

    for name in ("dividend_growth_1y", "dividend_growth_3y", "dividend_growth_5y"):
        assert name not in fields
        assert name not in SUPPORTED_FIELDS, f"{name} has no source in this payload"
    # The honest dividend cells survive.
    assert fields["dividend"] == pytest.approx(1.08)
    assert fields["dividend_ttm"] == pytest.approx(1.05)


def test_all_time_fields_are_catalog_owned_and_index_membership_is_elsewhere() -> None:
    assert {"all_time_high", "all_time_low"} <= EMITTED_FIELDS
    assert {"all_time_high", "all_time_low"} <= CATALOG_FIELDS
    assert not ({"all_time_high", "all_time_low"} & QUOTE_ONLY_FIELDS)
    # Index membership belongs to the official source; this parser must not
    # invent it from an empty ownership list.
    assert "index_membership" not in SUPPORTED_FIELDS
    assert "index_membership" not in parse_quote_summary(sample_modules("AAPL"))


# ---------------------------------------------------------------------------
# Timeseries growth
# ---------------------------------------------------------------------------


async def test_timeseries_growth_cells(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["AAPL"])

    # 3y CAGR of 1.0 -> 1.331 and 100 -> 133.1 is exactly 10%.
    assert first_value(result, "AAPL", "eps_past_3y") == pytest.approx(10.0)
    assert first_value(result, "AAPL", "sales_past_3y") == pytest.approx(10.0)
    # Latest quarter vs the same quarter a year earlier: 2.0 / 1.6 - 1.
    assert first_value(result, "AAPL", "eps_qoq") == pytest.approx(25.0)
    # 40.0 / 32.0 - 1 (2025-12-27 vs 2024-12-28).
    assert first_value(result, "AAPL", "sales_qoq") == pytest.approx(25.0)
    # 7.2 / 6.0 - 1 over twelve months of trailing EPS.
    assert first_value(result, "AAPL", "eps_yoy_ttm") == pytest.approx(20.0)
    assert first_value(result, "AAPL", "sales_yoy_ttm") == pytest.approx(10.0)
    # 130 * (1 - 30/150) / 400.
    assert first_value(result, "AAPL", "roic") == pytest.approx(26.0)
    assert first_value(result, "AAPL", "lt_debt_to_equity") == pytest.approx(1.5)


async def test_five_year_growth_is_absent_not_invented(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["AAPL"])

    fields = result["AAPL"].fields
    assert "eps_past_5y" not in fields
    assert "sales_past_5y" not in fields
    assert "eps_next_5y" not in fields


async def test_three_fiscal_years_do_not_produce_a_cagr(monkeypatch: Any) -> None:
    series = sample_series()
    series["annualDilutedEPS"] = [
        ("2023-09-30", 1.1),
        ("2024-09-28", 1.21),
        ("2025-09-27", 1.331),
    ]
    series["annualTotalRevenue"] = [
        ("2023-09-30", 110.0),
        ("2024-09-28", 121.0),
        ("2025-09-27", 133.1),
    ]
    fake = FakeYahoo()
    fake.timeseries_handler = lambda _symbol, _request: httpx2.Response(
        200, json=timeseries_body(series)
    )
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["AAPL"])

    fields = result["AAPL"].fields
    assert "eps_past_3y" not in fields, "three fiscal years cannot form a 3y CAGR"
    assert "sales_past_3y" not in fields
    # The rest of the row still lands.
    assert fields["market_cap"] == pytest.approx(5.04e12)


async def test_negative_base_does_not_become_a_fake_cagr() -> None:
    series = {
        "annualDilutedEPS": [
            ("2022-09-24", -1.0),
            ("2023-09-30", 0.5),
            ("2024-09-28", 1.0),
            ("2025-09-27", 2.0),
        ]
    }
    assert parse_timeseries(timeseries_body(series)) == {}


async def test_empty_timeseries_payload_is_not_an_error() -> None:
    assert parse_timeseries({"timeseries": {"result": None, "error": None}}) == {}
    assert parse_timeseries({"timeseries": {"result": [{}], "error": None}}) == {}
    assert parse_timeseries(None) == {}


# ---------------------------------------------------------------------------
# Filtering: catalog and quote-only
# ---------------------------------------------------------------------------


async def test_only_catalog_fields_are_emitted(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["AAPL"])

    fields = set(result["AAPL"].fields)
    # The parser emits catalog keys only; the *observation* adds exactly one
    # documented metadata cell — the venue spelling behind the MIC (D2).
    assert fields <= CATALOG_FIELDS | {EXCHANGE_SOURCE_FIELD}
    assert "someFutureYahooKey" not in fields
    assert not (fields & QUOTE_ONLY_FIELDS)
    for banned in ("price", "change_pct", "volume", "prev_close", "regularMarketPrice"):
        assert banned not in fields


async def test_supported_fields_are_a_catalog_subset() -> None:
    assert SUPPORTED_FIELDS <= CATALOG_FIELDS
    assert EMITTED_FIELDS <= SUPPORTED_FIELDS
    assert not (EMITTED_FIELDS & QUOTE_ONLY_FIELDS)
    assert len(EMITTED_FIELDS) == len(SUPPORTED_FIELDS)


def test_supported_fields_is_exactly_what_a_full_payload_produces() -> None:
    produced = (
        set(parse_quote_summary(sample_modules("AAPL")))
        | set(parse_timeseries(timeseries_body(sample_series())))
        | set(parse_optionable(options_body([EXPIRY_1, EXPIRY_2])))
    )

    assert produced == SUPPORTED_FIELDS


def test_parser_ignores_foreign_keys_inside_a_module() -> None:
    modules = sample_modules("AAPL")
    modules["price"]["notAColumn"] = 1.0
    modules["vendorExtension"] = {"pe": 12.0}

    fields = parse_quote_summary(modules)

    assert "notAColumn" not in fields
    assert "vendorExtension" not in fields
    assert set(fields) <= CATALOG_FIELDS


# ---------------------------------------------------------------------------
# Adversarial: auth, partial batches, empty bodies, class shares
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_refusal_is_retried_once_with_a_new_crumb(
    monkeypatch: Any, status: int
) -> None:
    fake = FakeYahoo()
    fake.crumb_tokens = ["stale-crumb", "fresh-crumb"]

    def quote(symbol: str, request: httpx2.Request) -> httpx2.Response:
        if request.url.params.get("crumb") == "stale-crumb":
            return httpx2.Response(
                status,
                json={
                    "finance": {
                        "result": None,
                        "error": {"code": "Unauthorized", "description": "Invalid Crumb"},
                    }
                },
            )
        return httpx2.Response(200, json=quote_summary_body(sample_modules(symbol)))

    fake.quote_handler = quote
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["AAPL"])

    assert fake.crumb_calls == 2, "one fresh handshake after the refusal"
    assert len(fake.quote_requests) == 2, "exactly one retry"
    assert "crumb=fresh-crumb" in str(fake.quote_requests[-1].url)
    assert result["AAPL"].fields["market_cap"] == pytest.approx(5.04e12)


async def test_a_second_refusal_gives_up_instead_of_looping(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    fake.quote_handler = lambda _symbol, _request: httpx2.Response(
        401,
        json={
            "finance": {
                "result": None,
                "error": {"code": "Unauthorized", "description": "Invalid Crumb"},
            }
        },
    )
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["AAPL"])

    assert len(fake.quote_requests) == 2
    assert fake.timeseries_requests == [], "no timeseries for a refused symbol"
    assert result == {}


async def test_one_failed_symbol_does_not_kill_the_batch(monkeypatch: Any) -> None:
    fake = FakeYahoo()

    def quote(symbol: str, _request: httpx2.Request) -> httpx2.Response:
        if symbol == "BAD":
            return httpx2.Response(
                404,
                json={
                    "quoteSummary": {
                        "result": None,
                        "error": {"code": "Not Found", "description": "Quote not found"},
                    }
                },
            )
        return httpx2.Response(200, json=quote_summary_body(sample_modules(symbol)))

    fake.quote_handler = quote
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["BAD", "GOOD"])

    assert set(result) == {"GOOD"}
    assert result["GOOD"].fields["market_cap"] == pytest.approx(5.04e12)


async def test_a_transport_exception_on_one_symbol_does_not_kill_the_batch(
    monkeypatch: Any,
) -> None:
    fake = FakeYahoo()

    def quote(symbol: str, _request: httpx2.Request) -> httpx2.Response:
        if symbol == "BOOM":
            raise RuntimeError("connection reset")
        return httpx2.Response(200, json=quote_summary_body(sample_modules(symbol)))

    fake.quote_handler = quote
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["BOOM", "GOOD"])

    assert set(result) == {"GOOD"}


async def test_null_result_is_empty_and_skips_the_second_request(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    fake.quote_handler = lambda _symbol, _request: httpx2.Response(
        200, json={"quoteSummary": {"result": None, "error": None}}
    )
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["AAPL"])

    assert result == {}
    assert len(fake.quote_requests) == 1
    assert fake.timeseries_requests == [], "a symbol with no quoteSummary has no row"


async def test_empty_modules_do_not_fabricate_zeroes(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    fake.quote_handler = lambda _symbol, _request: httpx2.Response(
        200, json=quote_summary_body({})
    )
    fake.timeseries_handler = lambda _symbol, _request: httpx2.Response(
        200, json=timeseries_body({})
    )
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["AAPL"])

    assert result == {}


async def test_empty_modules_still_allow_timeseries_fields(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    fake.quote_handler = lambda _symbol, _request: httpx2.Response(
        200, json=quote_summary_body({})
    )
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["AAPL"])

    # The timeseries call is independent of quoteSummary's module payload.
    assert first_value(result, "AAPL", "eps_past_3y") == pytest.approx(10.0)
    assert "market_cap" not in result["AAPL"].fields


async def test_class_shares_use_the_dash_notation(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["BRK.B"])

    assert canonical_symbol("BRK.B") == "BRK-B"
    assert fake.quote_requests[0].url.path.endswith("/quoteSummary/BRK-B")
    assert fake.timeseries_requests[0].url.path.endswith("/timeseries/BRK-B")
    assert set(result) == {"BRK-B"}
    assert result["BRK-B"].fields["ticker"] == "BRK-B"


async def test_duplicate_symbols_are_requested_once(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["AAPL", "aapl", "AAPL"])

    assert len(fake.quote_requests) == 1
    assert set(result) == {"AAPL"}


async def test_an_empty_symbol_list_makes_no_request(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    install_seam(monkeypatch, fake.web)
    source = build_source()

    assert await source.fetch_many([]) == {}
    assert fake.web.requests == []


def test_nan_and_infinite_numbers_are_skipped() -> None:
    modules = sample_modules("AAPL")
    modules["price"]["marketCap"] = {"raw": float("nan")}
    modules["summaryDetail"]["trailingPE"] = {"raw": float("inf")}
    modules["defaultKeyStatistics"]["trailingEps"] = {"raw": None}

    fields = parse_quote_summary(modules)

    assert "market_cap" not in fields
    assert "pe" not in fields
    assert "eps" not in fields
    assert "pc" not in fields, "no division by a NaN market cap"
    # The rest of the row is untouched.
    assert fields["roe"] == pytest.approx(148.8)


async def test_a_null_cell_does_not_erase_the_rest_of_the_row(monkeypatch: Any) -> None:
    modules = sample_modules("AAPL")
    modules["price"]["marketCap"] = {"raw": None}
    fake = FakeYahoo()
    fake.quote_handler = lambda _symbol, _request: httpx2.Response(
        200, json=quote_summary_body(modules)
    )
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["AAPL"])

    fields = result["AAPL"].fields
    assert "market_cap" not in fields
    assert "pc" not in fields
    assert fields["pe"] == pytest.approx(39.61)


async def test_timeseries_can_be_disabled(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    install_seam(monkeypatch, fake.web)
    source = build_source(include_timeseries=False)

    result = await source.fetch_many(["AAPL"])

    assert fake.timeseries_requests == []
    assert "eps_past_3y" not in result["AAPL"].fields
    assert result["AAPL"].fields["market_cap"] == pytest.approx(5.04e12)


# ---------------------------------------------------------------------------
# optionable: one optional /v7/finance/options request
# ---------------------------------------------------------------------------


async def test_optionable_probe_is_off_by_default(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    install_seam(monkeypatch, fake.web)
    source = build_source()

    result = await source.fetch_many(["AAPL", "MSFT"])

    assert fake.options_requests == [], "the default run must not buy the cell"
    assert "optionable" not in result["AAPL"].fields
    # The old budget is unchanged: one cookie, one crumb, two bodies per symbol.
    assert fake.cookie_calls == 1 and fake.crumb_calls == 1
    assert len(fake.quote_requests) == 2 and len(fake.timeseries_requests) == 2
    assert fake.web.unrouted == []


async def test_two_expiries_mean_optionable(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    install_seam(monkeypatch, fake.web)
    source = build_source(include_options=True)

    result = await source.fetch_many(["AAPL"])

    assert first_value(result, "AAPL", "optionable") == "Yes"
    assert len(fake.options_requests) == 1
    request = fake.options_requests[0]
    assert request.url.path == "/v7/finance/options/AAPL"
    assert request.url.params.get("crumb") == "test-crumb"
    # The expiry list is the answer; the dated chain is a second request this
    # cell never needs, so no ``date`` may ride the probe.
    assert request.url.params.get("date") is None
    # The rest of the row is untouched by the probe.
    assert result["AAPL"].fields["market_cap"] == pytest.approx(5.04e12)
    # Nothing outside the catalog arrives with the new cell — except the one
    # documented venue-provenance metadata key.
    assert set(result["AAPL"].fields) <= CATALOG_FIELDS | {EXCHANGE_SOURCE_FIELD}


async def test_option_probe_runs_after_the_timeseries(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    install_seam(monkeypatch, fake.web)
    source = build_source(include_options=True)

    await source.fetch_many(["AAPL"])

    order = [
        "options"
        if "/finance/options/" in str(record.url)
        else "timeseries"
        if "/timeseries/" in str(record.url)
        else "quoteSummary"
        if "/quoteSummary/" in str(record.url)
        else "auth"
        for record in fake.web.requests
    ]
    assert order == ["auth", "auth", "quoteSummary", "timeseries", "options"]


async def test_empty_expiration_list_means_not_optionable(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    fake.options_handler = lambda _symbol, _request: httpx2.Response(
        200, json=options_body([])
    )
    install_seam(monkeypatch, fake.web)
    source = build_source(include_options=True)

    result = await source.fetch_many(["AAPL"])

    # Yahoo answered: an empty list is a real "No", not a missing cell.
    assert first_value(result, "AAPL", "optionable") == "No"


def test_an_empty_chain_result_is_no_and_a_missing_one_is_absent() -> None:
    assert parse_optionable({"optionChain": {"result": [], "error": None}}) == {
        "optionable": "No"
    }
    assert parse_optionable({"optionChain": {"result": None, "error": None}}) == {}
    assert parse_optionable({"optionChain": {"error": None}}) == {}
    assert parse_optionable({}) == {}
    assert parse_optionable(None) == {}
    assert parse_optionable([]) == {}
    # A result entry with no expiry key cannot answer either way.
    assert parse_optionable({"optionChain": {"result": [{"quote": {}}]}}) == {}


async def test_missing_option_chain_leaves_the_field_absent(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    fake.options_handler = lambda _symbol, _request: httpx2.Response(
        200, json={"optionChain": {"result": None, "error": None}}
    )
    install_seam(monkeypatch, fake.web)
    source = build_source(include_options=True)

    result = await source.fetch_many(["AAPL"])

    fields = result["AAPL"].fields
    assert "optionable" not in fields, "no chain envelope is not a 'No'"
    assert fields["market_cap"] == pytest.approx(5.04e12)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_option_probe_refusals_do_not_publish_a_false_no(
    monkeypatch: Any, status: int
) -> None:
    fake = FakeYahoo()
    fake.options_handler = lambda _symbol, _request: httpx2.Response(
        status, json={"finance": {"result": None, "error": {"code": f"HTTP {status}"}}}
    )
    install_seam(monkeypatch, fake.web)
    source = build_source(include_options=True)

    result = await source.fetch_many(["AAPL"])

    assert "optionable" not in result["AAPL"].fields
    # One attempt, no retry loop on a non-auth refusal.
    assert len(fake.options_requests) == 1
    assert fake.crumb_calls == 1


async def test_option_probe_parses_nothing_from_an_html_challenge(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    fake.options_handler = lambda _symbol, _request: httpx2.Response(
        200, text="<!DOCTYPE html><html><body>verify you are human</body></html>"
    )
    install_seam(monkeypatch, fake.web)
    source = build_source(include_options=True)

    result = await source.fetch_many(["AAPL"])

    assert "optionable" not in result["AAPL"].fields
    assert result["AAPL"].fields["market_cap"] == pytest.approx(5.04e12)


async def test_option_probe_401_is_retried_once_with_a_fresh_crumb(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    fake.crumb_tokens = ["stale-crumb", "fresh-crumb"]

    def options(_symbol: str, request: httpx2.Request) -> httpx2.Response:
        if request.url.params.get("crumb") == "stale-crumb":
            return httpx2.Response(
                401,
                json={
                    "finance": {
                        "result": None,
                        "error": {"code": "Unauthorized", "description": "Invalid Crumb"},
                    }
                },
            )
        return httpx2.Response(200, json=options_body([EXPIRY_1, EXPIRY_2]))

    fake.options_handler = options
    install_seam(monkeypatch, fake.web)
    source = build_source(include_options=True)

    result = await source.fetch_many(["AAPL"])

    assert first_value(result, "AAPL", "optionable") == "Yes"
    assert fake.crumb_calls == 2, "one fresh handshake after the refusal"
    assert len(fake.options_requests) == 2, "exactly one retry"
    assert fake.options_requests[-1].url.params.get("crumb") == "fresh-crumb"
    # The other two endpoints were never re-asked by the options retry.
    assert len(fake.quote_requests) == 1
    assert len(fake.timeseries_requests) == 1


async def test_a_second_option_refusal_gives_up_instead_of_looping(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    fake.options_handler = lambda _symbol, _request: httpx2.Response(
        401, json={"finance": {"result": None, "error": {"code": "Unauthorized"}}}
    )
    install_seam(monkeypatch, fake.web)
    source = build_source(include_options=True)

    result = await source.fetch_many(["AAPL"])

    assert len(fake.options_requests) == 2
    assert "optionable" not in result["AAPL"].fields
    assert result["AAPL"].fields["market_cap"] == pytest.approx(5.04e12)


async def test_no_option_probe_without_a_quote_summary_row(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    fake.quote_handler = lambda _symbol, _request: httpx2.Response(
        200, json={"quoteSummary": {"result": None, "error": None}}
    )
    install_seam(monkeypatch, fake.web)
    source = build_source(include_options=True)

    assert await source.fetch_many(["AAPL"]) == {}
    assert fake.options_requests == [], "a symbol with no row has no cell to buy"


async def test_option_probe_works_with_the_timeseries_disabled(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    install_seam(monkeypatch, fake.web)
    source = build_source(include_timeseries=False, include_options=True)

    result = await source.fetch_many(["AAPL"])

    assert fake.timeseries_requests == []
    assert first_value(result, "AAPL", "optionable") == "Yes"


async def test_option_probe_uses_the_dash_notation(monkeypatch: Any) -> None:
    fake = FakeYahoo()
    install_seam(monkeypatch, fake.web)
    source = build_source(include_options=True)

    result = await source.fetch_many(["BRK.B"])

    assert fake.options_requests[0].url.path == "/v7/finance/options/BRK-B"
    assert first_value(result, "BRK-B", "optionable") == "Yes"


def test_optionable_is_catalog_owned_and_the_only_new_cell() -> None:
    assert {"optionable"} <= SUPPORTED_FIELDS
    assert {"optionable"} <= EMITTED_FIELDS
    assert {"optionable"} <= CATALOG_FIELDS
    assert not ({"optionable"} & QUOTE_ONLY_FIELDS)
    # The probe adds exactly one supported name, never a provider key.
    assert SUPPORTED_FIELDS <= CATALOG_FIELDS
    assert parse_optionable(options_body([EXPIRY_1])) == {"optionable": "Yes"}
