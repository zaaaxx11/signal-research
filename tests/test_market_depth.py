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
    parse_stockanalysis_statistics,
)
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


def test_store_merges_by_field_and_keeps_the_newest_value() -> None:
    store = DepthStore()
    assert store.merge(
        _obs("AAPL", {"pe": 30.0, "employees": 100}, ts=T0), priority=0
    ) == 2
    # A newer source changes `pe` but does not erase `employees`.
    store.merge(_obs("AAPL", {"pe": 28.0, "roe": 40.0}, ts=T0 + timedelta(hours=1)), priority=1)
    document = store.document(["AAPL"])
    assert document["symbols"]["AAPL"] == {"employees": 100, "pe": 28.0, "roe": 40.0}
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
        "pe": 28.0,
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
                """,
            }

    source = FinvizDepthSource(adapter=FakeAdapter(), concurrency=1, delay_s=0)
    values = asyncio.run(source.fetch_many(["AAPL"]))
    assert values["AAPL"].fields["pe"] == 20.5
    assert values["AAPL"].fields["employees"] == 1000
    assert values["AAPL"].fields["short_float"] == 0.83
    assert values["AAPL"].fields["operating_margin"] == 33.17
    assert "oper_margin" not in values["AAPL"].fields


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
