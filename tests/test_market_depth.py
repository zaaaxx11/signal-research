"""Depth collector: field-level merge, all-source collection, parsers, API read."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

import httpx2
import pytest

from app.market.depth import (
    DepthCollector,
    DepthObservation,
    DepthStore,
    FinvizDepthSource,
    SecDepthSource,
    parse_stockanalysis_statistics,
)
from app.market.sectors import classify_sector
from tests._market_quote import install_seam
from tests._phase2 import MockWeb

T0 = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


def _obs(
    symbol: str,
    fields: dict[str, Any],
    *,
    source: str = "fixture",
    ts: datetime = T0,
) -> DepthObservation:
    return DepthObservation(symbol=symbol, fields=fields, as_of=ts, source=source)


class FakeDepthSource:
    def __init__(self, values: dict[str, DepthObservation], name: str) -> None:
        self.name = name
        self.values = values
        self.calls: list[tuple[str, ...]] = []

    async def fetch_many(self, symbols: Sequence[str]) -> dict[str, DepthObservation]:
        self.calls.append(tuple(symbols))
        return {symbol: self.values[symbol] for symbol in symbols if symbol in self.values}


def test_store_merges_by_field_and_keeps_priority_stable() -> None:
    store = DepthStore()
    assert store.merge(
        _obs("AAPL", {"pe": 30.0, "employees": 100}, ts=T0), priority=0
    ) == 2
    # A newer lower-priority source fills a missing field, but does not replace
    # the higher-priority source's `pe`.
    store.merge(_obs("AAPL", {"pe": 28.0, "roe": 40.0}, ts=T0 + timedelta(hours=1)), priority=1)
    document = store.document(["AAPL"])
    assert document["symbols"]["AAPL"] == {"employees": 100, "pe": 30.0, "roe": 40.0}
    assert "source" not in json.dumps(document["symbols"])


def test_a_tie_is_broken_by_source_priority() -> None:
    store = DepthStore()
    store.merge(_obs("AAPL", {"pe": 30.0}, source="first"), priority=0)
    store.merge(_obs("AAPL", {"pe": 99.0}, source="second"), priority=1)
    assert store.document(["AAPL"])["symbols"]["AAPL"]["pe"] == 30.0


def test_collector_asks_every_source_and_merges_their_fields(tmp_path: Path) -> None:
    first = FakeDepthSource(
        {"AAPL": _obs("AAPL", {"pe": 30.0, "employees": 100}, source="first")},
        name="first",
    )
    second = FakeDepthSource(
        {"AAPL": _obs("AAPL", {"pe": 28.0, "roe": 40.0}, source="second", ts=T0 + timedelta(hours=1))},
        name="second",
    )
    collector = DepthCollector(
        symbols=["AAPL"],
        sources=(first, second),
        store_path=tmp_path / "depth.json",
        clock=lambda: T0 + timedelta(hours=2),
    )
    result = asyncio.run(collector.refresh())
    assert result.returned == 1
    assert first.calls and second.calls, "all sources are collected, not fallback"
    assert collector.store.document(["AAPL"])["symbols"]["AAPL"] == {
        "employees": 100,
        "pe": 30.0,
        "roe": 40.0,
    }
    assert json.loads((tmp_path / "depth.json").read_text())["count"] == 1


def test_collector_does_not_let_one_source_hide_another(tmp_path: Path) -> None:
    class Broken:
        name = "broken"

        async def fetch_many(self, symbols: Sequence[str]) -> dict[str, DepthObservation]:
            raise RuntimeError("down")

    good = FakeDepthSource({"AAPL": _obs("AAPL", {"pe": 28.0})}, name="good")
    collector = DepthCollector(
        symbols=["AAPL"],
        sources=(Broken(), good),
        store_path=tmp_path / "depth.json",
    )
    result = asyncio.run(collector.refresh())
    assert result.returned == 1
    assert result.errors == 1
    assert collector.store.document(["AAPL"])["symbols"]["AAPL"]["pe"] == 28.0


def test_stockanalysis_parser_reads_label_value_rows() -> None:
    html = """
    <table>
      <tr><td>Market Cap</td><td>3.99T</td></tr>
      <tr><td>PE Ratio</td><td>39.82</td></tr>
      <tr><td>Return on Equity (ROE)</td><td>148.19%</td></tr>
      <tr><td>Dividend Yield</td><td>0.31%</td></tr>
      <tr><td>Debt / Equity</td><td>0.78</td></tr>
      <tr><td>N/A label</td><td>--</td></tr>
    </table>
    """
    values = parse_stockanalysis_statistics(html)
    assert values["market_cap"] == 3.99e12
    assert values["pe"] == 39.82
    assert values["roe"] == 148.19
    assert values["dividend_yield"] == 0.31
    assert values["debt_to_equity"] == 0.78


def test_finviz_source_uses_the_existing_wide_parser() -> None:
    class FakeAdapter:
        async def fetch(self, capability: Any, instrument: Any, fields: Any) -> dict[str, Any]:
            return {
                "status": 200,
                "body": b"""
                <div class="snapshot-td-label">P/E</div><div class="snapshot-td-content">20.5</div>
                <div class="snapshot-td-label">Employees</div><div class="snapshot-td-content">1000</div>
                <div class="snapshot-td-label">Short Float</div><div class="snapshot-td-content">0.83%</div>
                <div class="snapshot-td-label">Oper. Margin</div><div class="snapshot-td-content">33.17%</div>
                <div class="snapshot-td-label">Dividend TTM</div><div class="snapshot-td-content">1.06 (0.31%)</div>
                <div class="snapshot-td-label">EPS/Sales Surpr.</div><div class="snapshot-td-content">6.77% 0.35%</div>
                <div class="snapshot-td-label">Volatility</div><div class="snapshot-td-content">1.75% 1.81%</div>
                <div class="snapshot-td-label">Option/Short</div><div class="snapshot-td-content">Yes / No</div>
                """,
            }

    source = FinvizDepthSource(adapter=FakeAdapter(), concurrency=1, delay_s=0)
    values = asyncio.run(source.fetch_many(["AAPL"]))
    assert values["AAPL"].fields["pe"] == 20.5
    assert values["AAPL"].fields["employees"] == 1000
    assert values["AAPL"].fields["short_float"] == 0.83
    assert values["AAPL"].fields["operating_margin"] == 33.17
    assert values["AAPL"].fields["dividend_ttm"] == 1.06
    assert values["AAPL"].fields["eps_surprise"] == 6.77
    assert values["AAPL"].fields["revenue_surprise"] == 0.35
    assert values["AAPL"].fields["volatility_1w"] == 1.75
    assert values["AAPL"].fields["volatility_4w"] == 1.81
    assert values["AAPL"].fields["optionable"] == "Yes"
    assert values["AAPL"].fields["shortable"] == "No"
    assert "oper_margin" not in values["AAPL"].fields


def test_yahoo_debt_to_equity_is_normalized_to_a_ratio() -> None:
    value = _obs("AAPL", {"debt_to_equity": 16.97}, source="yahoo")
    assert value.fields["debt_to_equity"] == pytest.approx(0.1697)


def test_orphan_duplicates_are_dropped_and_revenue_is_aliased() -> None:
    value = _obs(
        "AAPL",
        {
            "eps_past_3_5y": "6.89% 17.91%",
            "sales_past_3_5y": "1.81% 8.71%",
            "option_short": "Yes / Yes",
            "volatility": "1.75% 1.81%",
            "revenue_ttm": 466.82e9,
        },
        source="finviz",
    )
    assert value.fields == {"sales": 466.82e9}


def test_stockanalysis_parser_maps_revenue_to_sales() -> None:
    values = parse_stockanalysis_statistics(
        "<table><tr><td>Revenue</td><td>466.82B</td></tr></table>"
    )
    assert values == {"sales": 466.82e9}


def test_depth_api_reads_the_store_without_source_labels() -> None:
    collector = DepthCollector(
        symbols=["AAPL"],
        sources=(FakeDepthSource({"AAPL": _obs("AAPL", {"pe": 28.0, "roe": 40.0})}, "fixture"),),
        store_path=None,
    )
    asyncio.run(collector.refresh())

    async def scenario() -> httpx2.Response:
        from app.main import create_app

        application = create_app()
        application.state.depth = collector
        transport = httpx2.ASGITransport(app=application, raise_app_exceptions=False)
        async with httpx2.AsyncClient(transport=transport, base_url="http://ztockz.test") as client:
            return await client.get("/market/depth", params={"symbols": "AAPL"})

    response = asyncio.run(scenario())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbols"]["AAPL"] == {"pe": 28.0, "roe": 40.0}
    assert "source" not in json.dumps(body)


def test_yahoo_bulk_reuses_one_cookie_and_crumb(monkeypatch: Any) -> None:
    web = MockWeb()

    def responder(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/v1/test/getcrumb"):
            return httpx2.Response(200, text="crumb-1")
        return httpx2.Response(
            200,
            json={
                "quoteSummary": {
                    "result": [
                        {"price": {"regularMarketPrice": 101.0}}
                    ]
                }
            },
        )

    web.route("fc.yahoo.com", lambda _request: httpx2.Response(404, text=""))
    web.route("query1.finance.yahoo.com", responder)
    install_seam(monkeypatch, web)

    from app.providers.adapters.yahoo import YahooAdapter
    from app.providers.base import InstrumentRef

    adapter = YahooAdapter()
    instruments = [InstrumentRef.from_symbol("AAPL"), InstrumentRef.from_symbol("MSFT")]
    result = asyncio.run(adapter.fetch_fundamentals_many(instruments, concurrency=2))
    assert set(result) == {"AAPL", "MSFT"}
    cookie_requests = [r for r in web.requests if r.host == "fc.yahoo.com"]
    crumb_requests = [
        r for r in web.requests
        if r.host == "query1.finance.yahoo.com" and r.url.endswith("/v1/test/getcrumb")
    ]
    data_requests = [
        r for r in web.requests
        if r.host == "query1.finance.yahoo.com" and "quoteSummary" in r.url
    ]
    assert len(cookie_requests) == 1
    assert len(crumb_requests) == 1
    assert len(data_requests) == 2


@pytest.mark.parametrize(
    ("industry", "sector"),
    [
        ("Electronic Computers", "Technology"),
        ("Semiconductors & Related Devices", "Technology"),
        ("State Commercial Banks", "Financials"),
        ("Pharmaceutical Preparations", "Healthcare"),
        ("Petroleum Refining", "Energy"),
        ("Air Transportation, Scheduled", "Industrials"),
        ("Food and Kindred Products", "Consumer Staples"),
        ("Real Estate Investment Trusts", "Real Estate"),
        ("Telephone Communications", "Communication Services"),
        ("Electric Services", "Utilities"),
    ],
)
def test_sic_industry_classification(industry: str, sector: str) -> None:
    assert classify_sector(industry) == sector


def test_sec_depth_source_reads_identity_and_classifies_sector() -> None:
    from types import SimpleNamespace

    class FakeMaster:
        def get(self, symbol: str) -> Any:
            if symbol != "AAPL":
                return None
            return SimpleNamespace(
                symbol="AAPL",
                name="Apple Inc.",
                exchange="Nasdaq",
                industry="Electronic Computers",
                country="United States",
                sector=None,
                asset_class="equity",
            )

    source = SecDepthSource(snapshot_path="/definitely/missing.json")
    source._loaded = True
    source._dataset = SimpleNamespace(
        security_master=FakeMaster(),
        fetched_at="2026-10-28T00:00:00+00:00",
    )
    values = asyncio.run(source.fetch_many(["AAPL", "MSFT"]))
    assert values["AAPL"].fields == {
        "ticker": "AAPL",
        "company": "Apple Inc.",
        "security_type": "Stock",
        "exchange": "Nasdaq",
        "industry": "Electronic Computers",
        "country": "United States",
        "sector": "Technology",
    }
    assert "MSFT" not in values


def test_settings_parses_depth_flags(monkeypatch: Any) -> None:
    from app.config import Settings

    monkeypatch.setenv("MEE_DEPTH_FINVIZ", "0")
    monkeypatch.setenv("MEE_DEPTH_CHART", "1")
    settings = Settings()
    assert settings.depth_finviz is False
    assert settings.depth_chart is True


def test_build_depth_collector_can_exclude_finviz() -> None:
    from types import SimpleNamespace

    from app.market.depth import build_depth_collector

    settings = SimpleNamespace(
        depth_finviz=False,
        depth_chart=False,
        depth_sec_snapshot=None,
        depth_concurrency=1,
        depth_delay_s=0.0,
        depth_yahoo_concurrency=1,
        depth_store_path=None,
    )
    collector = build_depth_collector(settings, ["AAPL"])
    assert "finviz" not in [source.name for source in collector.sources]


def test_build_depth_collector_keeps_finviz_by_default() -> None:
    from types import SimpleNamespace

    from app.market.depth import build_depth_collector

    settings = SimpleNamespace(
        depth_sec_snapshot=None,
        depth_concurrency=1,
        depth_delay_s=0.0,
        depth_yahoo_concurrency=1,
        depth_store_path=None,
    )
    collector = build_depth_collector(settings, ["AAPL"])
    assert "finviz" in [source.name for source in collector.sources]


def _chart_payload(symbol: str = "AAPL") -> dict[str, Any]:
    from datetime import timedelta

    start = datetime(2025, 1, 1, tzinfo=UTC)
    closes = [100.0] * 260
    timestamps = [int((start + timedelta(days=i)).timestamp()) for i in range(260)]
    return {
        "chart": {
            "result": [
                {
                    "meta": {"symbol": symbol},
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": closes,
                                "high": [101.0] * 260,
                                "low": [99.0] * 260,
                                "close": closes,
                                "volume": [1000.0] * 260,
                            }
                        ]
                    },
                }
            ]
        }
    }


def test_yahoo_chart_source_emits_canonical_fields(monkeypatch: Any) -> None:
    from app.market.depth import YahooChartDepthSource

    web = MockWeb()
    web.route(
        "query1.finance.yahoo.com",
        lambda request: httpx2.Response(200, json=_chart_payload()),
    )
    install_seam(monkeypatch, web)
    source = YahooChartDepthSource(concurrency=1)
    values = asyncio.run(source.fetch_many(["AAPL"]))
    assert values["AAPL"].source == "yahoo_chart"
    assert "sma20" in values["AAPL"].fields
    assert "price" not in values["AAPL"].fields
    assert "volume" not in values["AAPL"].fields


def test_yahoo_chart_source_keeps_other_symbols_on_one_failure(
    monkeypatch: Any,
) -> None:
    from app.market.depth import YahooChartDepthSource

    web = MockWeb()
    web.route(
        "query1.finance.yahoo.com",
        lambda request: (
            httpx2.Response(200, json=_chart_payload())
            if "AAPL" in str(request.url)
            else httpx2.Response(404, json={})
        ),
    )
    install_seam(monkeypatch, web)
    values = asyncio.run(
        YahooChartDepthSource(concurrency=1).fetch_many(["AAPL", "MISSING"])
    )
    assert set(values) == {"AAPL"}


def test_build_depth_collector_adds_chart_when_enabled() -> None:
    from types import SimpleNamespace

    from app.market.depth import build_depth_collector

    settings = SimpleNamespace(
        depth_finviz=False,
        depth_chart=True,
        depth_sec_snapshot=None,
        depth_concurrency=1,
        depth_delay_s=0.0,
        depth_yahoo_concurrency=1,
        depth_store_path=None,
    )
    collector = build_depth_collector(settings, ["AAPL"])
    assert "yahoo_chart" in [source.name for source in collector.sources]


def test_depth_store_seed_is_low_priority_fallback() -> None:
    store = DepthStore()
    payload = {
        "as_of": "2026-10-28T00:00:00+00:00",
        "symbols": {"AAPL": {"market_cap": 1.0, "pe": 20.0}},
    }
    assert store.seed(payload) == 2
    assert store.document()["symbols"]["AAPL"]["market_cap"] == 1.0
    store.merge(_obs("AAPL", {"market_cap": 2.0}), priority=0)
    assert store.document()["symbols"]["AAPL"]["market_cap"] == 2.0
    # A field the live source did not supply keeps the cached value.
    assert store.document()["symbols"]["AAPL"]["pe"] == 20.0


def test_depth_collector_seeds_before_first_cycle(tmp_path: Path) -> None:
    seed = tmp_path / "depth.json"
    seed.write_text(
        json.dumps(
            {
                "as_of": "2026-10-28T00:00:00+00:00",
                "symbols": {"AAPL": {"market_cap": 1.0}},
            }
        ),
        encoding="utf-8",
    )
    collector = DepthCollector(
        symbols=["AAPL"],
        sources=[FakeDepthSource({"AAPL": _obs("AAPL", {"market_cap": 1.0})}, "fake")],
        store_path=None,
        seed_path=seed,
    )
    assert collector._seed() == 1
    assert collector.document()["symbols"]["AAPL"]["market_cap"] == 1.0


# ---------------------------------------------------------------------------
# Full-run reliability: a source is fetched per chunk, under its own deadline
# ---------------------------------------------------------------------------


class RecordingChunkSource:
    """Fake source that records every chunk it was handed and answers at once."""

    def __init__(
        self, name: str = "record", *, values: dict[str, DepthObservation] | None = None
    ) -> None:
        self.name = name
        self.values = values or {}
        self.calls: list[tuple[str, ...]] = []

    async def fetch_many(self, symbols: Sequence[str]) -> dict[str, DepthObservation]:
        self.calls.append(tuple(symbols))
        if self.values:
            return {
                symbol: self.values[symbol]
                for symbol in symbols
                if symbol in self.values
            }
        return {
            symbol: _obs(symbol, {"pe": 10.0}, source=self.name) for symbol in symbols
        }


class HangingChunkSource:
    """Fake source whose selected chunks never resolve; ``only=None`` hangs all."""

    def __init__(self, name: str = "hang", *, only: set[str] | None = None) -> None:
        self.name = name
        self.only = only
        self.calls: list[tuple[str, ...]] = []

    async def fetch_many(self, symbols: Sequence[str]) -> dict[str, DepthObservation]:
        self.calls.append(tuple(symbols))
        if self.only is None or any(symbol in self.only for symbol in symbols):
            await asyncio.Event().wait()
        return {
            symbol: _obs(symbol, {"pe": 1.0}, source=self.name) for symbol in symbols
        }


def test_full_run_is_split_into_bounded_chunks() -> None:
    symbols = [f"S{index:04d}" for index in range(4217)]
    source = RecordingChunkSource("bulk")
    collector = DepthCollector(symbols=symbols, sources=[source], store_path=None)
    result = asyncio.run(collector.refresh())
    assert collector.source_chunk_size == 250
    assert collector.source_timeout_s == 180.0
    assert len(source.calls) == 17
    assert [len(chunk) for chunk in source.calls] == [250] * 16 + [217]
    assert tuple(symbol for chunk in source.calls for symbol in chunk) == tuple(symbols)
    assert result.requested == 4217
    assert result.returned == 4217
    assert result.errors == 0
    assert result.error is None


def test_a_chunk_larger_than_the_run_makes_one_call() -> None:
    source = RecordingChunkSource("bulk")
    collector = DepthCollector(
        symbols=["AAPL", "MSFT"],
        sources=[source],
        store_path=None,
        source_chunk_size=1000,
    )
    result = asyncio.run(collector.refresh())
    assert source.calls == [("AAPL", "MSFT")]
    assert result.requested == 2
    assert result.returned == 2


def test_one_hung_chunk_times_out_without_stopping_the_run(tmp_path: Path) -> None:
    symbols = [f"S{index}" for index in range(6)]
    slow = HangingChunkSource("slow", only={"S2"})
    good = RecordingChunkSource("good")
    collector = DepthCollector(
        symbols=symbols,
        sources=[slow, good],
        store_path=tmp_path / "depth.json",
        source_timeout_s=0.05,
        source_chunk_size=2,
    )
    result = asyncio.run(collector.refresh())
    assert slow.calls == [("S0", "S1"), ("S2", "S3"), ("S4", "S5")]
    assert result.requested == 6
    assert result.errors == 1
    assert result.error is not None
    assert "slow" in result.error and "timeout" in result.error
    # The timed-out chunk contributes nothing, its neighbours and the second
    # source still do; a lower-priority source fills the missing symbols.
    document = collector.document(symbols)["symbols"]
    assert document["S0"]["pe"] == 1.0
    assert document["S2"]["pe"] == 10.0
    assert document["S4"]["pe"] == 1.0
    assert result.returned == 6


def test_a_source_whose_every_chunk_hangs_is_bounded_and_persisted(
    tmp_path: Path,
) -> None:
    symbols = [f"S{index}" for index in range(5)]
    dead = HangingChunkSource("dead")
    good = RecordingChunkSource("good")
    collector = DepthCollector(
        symbols=symbols,
        sources=[dead, good],
        store_path=tmp_path / "depth.json",
        source_timeout_s=0.03,
        source_chunk_size=2,
    )
    result = asyncio.run(collector.refresh())
    assert dead.calls == [("S0", "S1"), ("S2", "S3"), ("S4",)]
    assert result.errors == 3
    assert result.error is not None and result.error.count("timeout") == 3
    assert result.returned == 5
    assert result.seconds < 5.0, "a hanging source must not hang the cycle"
    written = json.loads((tmp_path / "depth.json").read_text(encoding="utf-8"))
    assert written["count"] == 5
    assert written["missing"] == []


def test_a_source_that_swallows_cancellation_is_still_a_timeout() -> None:
    class Swallowing:
        name = "swallow"

        async def fetch_many(self, symbols: Sequence[str]) -> dict[str, DepthObservation]:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # A provider must not be able to turn its own deadline into a
                # successful, late answer.
                return {
                    symbol: _obs(symbol, {"pe": 0.0}, source=self.name)
                    for symbol in symbols
                }
            raise AssertionError("an Event that was never set cannot return")

    good = RecordingChunkSource("good")
    collector = DepthCollector(
        symbols=["AAPL"],
        sources=[Swallowing(), good],
        store_path=None,
        source_timeout_s=0.05,
    )
    result = asyncio.run(collector.refresh())
    assert result.errors == 1
    assert result.error is not None
    assert "swallow" in result.error and "timeout" in result.error
    assert collector.document(["AAPL"])["symbols"]["AAPL"] == {"pe": 10.0}


def test_a_provider_that_ignores_cancellation_cannot_hold_the_cycle(
    monkeypatch: Any,
) -> None:
    import app.market.depth as depth_module

    # Keep the bounded drain short in the test; production uses the constant.
    monkeypatch.setattr(depth_module, "CHUNK_DRAIN_S", 0.05)

    class Stubborn:
        name = "stubborn"

        async def fetch_many(self, symbols: Sequence[str]) -> dict[str, DepthObservation]:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                # Deliberately ignore the deadline and keep going.
                await asyncio.sleep(30)
            return {
                symbol: _obs(symbol, {"pe": 0.0}, source=self.name)
                for symbol in symbols
            }

    good = RecordingChunkSource("good")
    collector = DepthCollector(
        symbols=["AAPL"],
        sources=[Stubborn(), good],
        store_path=None,
        source_timeout_s=0.05,
    )
    result = asyncio.run(collector.refresh())
    assert result.errors == 1
    assert result.error is not None
    assert "stubborn" in result.error and "timeout" in result.error
    assert collector.document(["AAPL"])["symbols"]["AAPL"] == {"pe": 10.0}


def test_an_exception_after_a_successful_chunk_keeps_the_first_chunk(
    tmp_path: Path,
) -> None:
    class FlakyChunk:
        name = "flaky"

        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        async def fetch_many(
            self, symbols: Sequence[str]
        ) -> dict[str, DepthObservation]:
            self.calls.append(tuple(symbols))
            if "S2" in symbols:
                raise RuntimeError("chunk exploded")
            return {
                symbol: _obs(symbol, {"pe": 5.0}, source=self.name)
                for symbol in symbols
            }

    symbols = [f"S{index}" for index in range(6)]
    flaky = FlakyChunk()
    collector = DepthCollector(
        symbols=symbols,
        sources=[flaky],
        store_path=tmp_path / "depth.json",
        source_chunk_size=2,
    )
    result = asyncio.run(collector.refresh())
    assert flaky.calls == [("S0", "S1"), ("S2", "S3"), ("S4", "S5")]
    assert result.errors == 1
    assert result.error is not None
    assert "flaky" in result.error and "RuntimeError" in result.error
    document = collector.document(symbols)["symbols"]
    assert document["S0"]["pe"] == 5.0
    assert document["S4"]["pe"] == 5.0
    assert "S2" not in document and "S3" not in document
    assert result.returned == 4
    written = json.loads((tmp_path / "depth.json").read_text(encoding="utf-8"))
    assert written["count"] == 4
    assert set(written["symbols"]) == {"S0", "S1", "S4", "S5"}


def test_chunking_keeps_source_priority_and_ignores_chunk_order() -> None:
    symbols = ["AAPL", "MSFT", "NVDA", "AMD", "INTC"]
    first = RecordingChunkSource(
        "first",
        values={
            "AAPL": _obs("AAPL", {"pe": 30.0}, source="first"),
            "AMD": _obs("AMD", {"pe": 31.0}, source="first"),
        },
    )
    second = RecordingChunkSource(
        "second",
        values={
            symbol: _obs(symbol, {"pe": 99.0}, source="second") for symbol in symbols
        },
    )
    collector = DepthCollector(
        symbols=symbols,
        sources=[first, second],
        store_path=None,
        source_chunk_size=2,
    )
    result = asyncio.run(collector.refresh())
    document = collector.document(symbols)["symbols"]
    assert document["AAPL"]["pe"] == 30.0
    assert document["AMD"]["pe"] == 31.0
    assert document["MSFT"]["pe"] == 99.0
    assert document["NVDA"]["pe"] == 99.0
    assert result.returned == 5


def test_a_failed_write_keeps_the_merged_result(tmp_path: Path) -> None:
    collector = DepthCollector(
        symbols=["AAPL"],
        sources=[RecordingChunkSource("ok")],
        store_path=tmp_path,  # a directory: the atomic replace must fail
    )
    result = asyncio.run(collector.refresh())
    assert result.returned == 1
    assert result.errors == 1
    assert result.error is not None
    assert "persist" in result.error
    assert collector.document(["AAPL"])["symbols"]["AAPL"] == {"pe": 10.0}
    assert collector.last_cycle is result


def test_collector_rejects_bad_source_limits_and_empty_inputs() -> None:
    source = RecordingChunkSource("ok")
    with pytest.raises(ValueError):
        DepthCollector(
            symbols=["AAPL"], sources=[source], store_path=None, source_timeout_s=0
        )
    with pytest.raises(ValueError):
        DepthCollector(
            symbols=["AAPL"],
            sources=[source],
            store_path=None,
            source_timeout_s=0.0,
        )
    with pytest.raises(ValueError):
        DepthCollector(
            symbols=["AAPL"], sources=[source], store_path=None, source_chunk_size=0
        )
    with pytest.raises(ValueError):
        DepthCollector(symbols=[], sources=[source], store_path=None)
    with pytest.raises(ValueError):
        DepthCollector(symbols=["AAPL"], sources=[], store_path=None)


def test_settings_parse_depth_source_limits(monkeypatch: Any) -> None:
    from app.config import Settings

    monkeypatch.setenv("MEE_DEPTH_SOURCE_TIMEOUT_S", "120.5")
    monkeypatch.setenv("MEE_DEPTH_SOURCE_CHUNK_SIZE", "500")
    settings = Settings()
    assert settings.depth_source_timeout_s == 120.5
    assert settings.depth_source_chunk_size == 500
    assert Settings().depth_source_timeout_s > 0
    assert Settings().depth_source_chunk_size >= 1


def test_settings_reject_a_zero_depth_source_timeout(monkeypatch: Any) -> None:
    from pydantic import ValidationError

    from app.config import Settings

    monkeypatch.setenv("MEE_DEPTH_SOURCE_TIMEOUT_S", "0")
    with pytest.raises(ValidationError):
        Settings()


def test_build_depth_collector_carries_the_source_limits() -> None:
    from types import SimpleNamespace

    from app.market.depth import build_depth_collector

    settings = SimpleNamespace(
        depth_finviz=False,
        depth_chart=False,
        depth_official=False,
        depth_finra=False,
        depth_sec_snapshot=None,
        depth_concurrency=1,
        depth_delay_s=0.0,
        depth_yahoo_concurrency=1,
        depth_store_path=None,
        depth_source_timeout_s=42.0,
        depth_source_chunk_size=7,
    )
    collector = build_depth_collector(settings, ["AAPL"])
    assert collector.source_timeout_s == 42.0
    assert collector.source_chunk_size == 7


def test_each_source_reports_its_own_duration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A cycle's only completion log fires at the end.

    That makes a stalled source, or a cycle that overruns its budget,
    invisible until someone measures by hand — which is exactly why
    "cycle <= 60 minutes" stayed unverified. One line per source, carrying its
    name and duration, turns the gate into an observation.
    """

    import logging

    collector = DepthCollector(
        symbols=["AAA", "BBB"], sources=[RecordingChunkSource("alpha")], store_path=None
    )

    with caplog.at_level(logging.INFO, logger="mee.depth"):
        asyncio.run(collector.refresh())

    lines = [record.getMessage() for record in caplog.records]
    assert any(line.startswith("depth source alpha done in") for line in lines), lines
