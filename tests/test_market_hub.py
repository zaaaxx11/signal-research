"""QuoteHub: merge rules, cadence, persistence, public document, and API read.

No test reaches a provider: every source is a small async callable and the API
test reads a hub that was refreshed by one of them.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.market.bulk import CnbcBatchSource, parse_cnbc_quotes
from app.market.hub import QuoteHub, QuoteStore, QuoteValue
from app.market.quote import SourceQuote
from tests._market_quote import install_seam
from tests._phase2 import MockWeb

T0 = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


def _value(
    symbol: str = "AAPL",
    *,
    last: float = 101.0,
    prev_close: float | None = 100.0,
    ts: datetime = T0,
    tier: str = "free",
    source: str = "fixture",
) -> QuoteValue:
    return QuoteValue(
        symbol=symbol,
        last=last,
        prev_close=prev_close,
        volume=1_000,
        ts_exchange=ts,
        ts_received=ts,
        source=source,
        tier=tier,
    )


def _source_quote(
    last: float = 101.0,
    *,
    prev_close: float | None = 100.0,
    ts: datetime = T0,
) -> SourceQuote:
    return SourceQuote(last=last, ts_exchange=ts, prev_close=prev_close, volume=1_000)


class FakeSource:
    def __init__(self, values: dict[str, SourceQuote | None], name: str = "fixture") -> None:
        self.name = name
        self.values = values
        self.calls: list[tuple[str, ...]] = []

    async def fetch_many(
        self, symbols: Sequence[str]
    ) -> dict[str, SourceQuote | None]:
        self.calls.append(tuple(symbols))
        return {symbol: self.values.get(symbol) for symbol in symbols}


def test_store_merge_is_guarded_by_timestamp_and_tier() -> None:
    store = QuoteStore()
    assert store.merge(_value("AAPL", last=100.0, ts=T0))
    assert store.merge(_value("AAPL", last=102.0, ts=T0 + timedelta(seconds=30), tier="premium"))

    # An older free print cannot replace the newer premium one, and a free
    # print at the same exchange timestamp cannot win either.
    assert not store.merge(_value("AAPL", last=90.0, ts=T0 + timedelta(seconds=10)))
    assert not store.merge(_value("AAPL", last=90.0, ts=T0 + timedelta(seconds=30)))
    assert store.get("AAPL").last == 102.0  # type: ignore[union-attr]

    # A newer premium print wins; a newer free print is allowed only when it is
    # actually newer than the current premium observation.
    assert store.merge(_value("AAPL", last=104.0, ts=T0 + timedelta(seconds=60), tier="premium"))
    assert store.merge(_value("AAPL", last=105.0, ts=T0 + timedelta(seconds=90)))
    assert store.get("AAPL").last == 105.0  # type: ignore[union-attr]


def test_public_document_never_carries_source_or_tier() -> None:
    store = QuoteStore()
    store.merge(_value("AAPL", last=101.0))
    document = store.document(["AAPL", "MSFT"])

    assert document["count"] == 1
    assert document["missing"] == ["MSFT"]
    assert document["as_of"] == T0.isoformat()
    quote = document["quotes"][0]
    assert set(quote) == {
        "symbol",
        "last",
        "prev_close",
        "change",
        "change_pct",
        "volume",
        "ts_exchange",
    }
    assert quote["change"] == 1.0
    assert quote["change_pct"] == pytest.approx(1.0)
    assert "source" not in quote
    assert "tier" not in quote


def test_hub_refresh_updates_store_and_writes_json(tmp_path: Path) -> None:
    target = tmp_path / "quotes.json"
    history = tmp_path / "history"
    free = FakeSource({"AAPL": _source_quote(101.0), "MSFT": _source_quote(202.0)}, name="yahoo")
    premium = FakeSource({"AAPL": _source_quote(103.0, ts=T0 + timedelta(seconds=1))}, name="cnbc")
    hub = QuoteHub(
        free_source=free,
        premium_source=premium,
        symbols=["aapl", "MSFT", "AAPL"],
        store_path=target,
        history_dir=history,
        clock=lambda: T0 + timedelta(seconds=2),
        random_source=random.Random(7),
    )

    free_result = _run(hub.refresh("free"))
    premium_result = _run(hub.refresh("premium"))

    assert free_result.requested == 2
    assert free_result.returned == 2
    assert premium_result.returned == 1
    assert hub.store.get("AAPL").last == 103.0  # type: ignore[union-attr]
    assert hub.store.get("MSFT").last == 202.0  # type: ignore[union-attr]
    assert target.exists()
    payload = json.loads(target.read_text())
    assert payload["count"] == 2
    assert {quote["symbol"] for quote in payload["quotes"]} == {"AAPL", "MSFT"}
    assert all("source" not in quote and "tier" not in quote for quote in payload["quotes"])
    hourly = history / "2026-10-27" / "15.json"
    assert hourly.exists(), "one snapshot per hour, not one per refresh"
    assert json.loads(hourly.read_text())["count"] == 2


def test_premium_delay_is_randomized_inside_the_range() -> None:
    hub = QuoteHub(
        free_source=FakeSource({}),
        premium_source=FakeSource({}),
        symbols=["AAPL"],
        free_interval_s=900,
        premium_min_s=60,
        premium_max_s=120,
        store_path=None,
        random_source=random.Random(7),
    )
    assert hub.next_delay("free") == 900
    values = [hub.next_delay("premium") for _ in range(20)]
    assert all(60 <= value <= 120 for value in values)
    assert len(set(values)) > 1


def test_hub_requires_a_symbol() -> None:
    with pytest.raises(ValueError):
        QuoteHub(
            free_source=FakeSource({}),
            premium_source=FakeSource({}),
            symbols=[],
            store_path=None,
        )


def test_build_hub_uses_the_configured_free_and_premium_cadence(tmp_path: Path) -> None:
    from app.config import Settings
    from app.market.hub import build_quote_hub

    settings = Settings(
        quote_free_refresh_s=900,
        quote_premium_refresh_min_s=60,
        quote_premium_refresh_max_s=120,
        quote_store_path=tmp_path / "quotes.json",
    )
    hub = build_quote_hub(settings, ["AAPL"], random_source=random.Random(7))
    assert hub.free_interval_s == 900
    assert (hub.premium_min_s, hub.premium_max_s) == (60, 120)
    assert 60 <= hub.next_delay("premium") <= 120


def test_hub_start_stops_both_loops() -> None:
    free = FakeSource({"AAPL": _source_quote(101.0)}, name="free")
    premium = FakeSource({"AAPL": _source_quote(102.0)}, name="premium")
    hub = QuoteHub(
        free_source=free,
        premium_source=premium,
        symbols=["AAPL"],
        store_path=None,
    )

    async def scenario() -> None:
        await hub.start()
        await asyncio.sleep(0.05)
        await hub.stop()

    asyncio.run(scenario())
    assert free.calls
    assert premium.calls
    assert not hub.started


def test_api_hub_route_reads_the_store_without_a_source_label(tmp_path: Path) -> None:
    target = tmp_path / "quotes.json"
    source = FakeSource(
        {"AAPL": _source_quote(101.0), "MSFT": _source_quote(202.0)}, name="fixture"
    )
    hub = QuoteHub(
        free_source=source,
        premium_source=source,
        symbols=["AAPL", "MSFT"],
        store_path=target,
        clock=lambda: T0 + timedelta(seconds=1),
    )
    _run(hub.refresh("free"))

    # The app is built with the default (hub-disabled) settings; this route
    # reads the hub object attached to app.state, never a provider.
    response = _run(_hub_response(hub))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["requested"] == 2
    assert body["returned"] == 2
    assert body["missing"] == []
    assert set(body) == {"as_of", "requested", "returned", "quotes", "missing"}
    assert all("source" not in quote and "tier" not in quote for quote in body["quotes"])


async def _hub_response(hub: QuoteHub) -> httpx2.Response:
    from app.main import create_app

    application = create_app()
    application.state.quote_hub = hub
    transport = httpx2.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx2.AsyncClient(transport=transport, base_url="http://ztockz.test") as client:
        return await client.get("/market/hub", params={"symbols": "AAPL,MSFT"})


def test_cnbc_parser_handles_formatted_numbers_and_millisecond_time() -> None:
    payload = {
        "FormattedQuoteResult": {
            "FormattedQuote": [
                {
                    "symbol": "AAPL",
                    "last": "345.68",
                    "last_time": "2026-10-27T14:49:51.640-0400",
                    "previous_day_closing": "343.91",
                    "volume": "17,642,482",
                    "realTime": "true",
                }
            ]
        }
    }
    answers = parse_cnbc_quotes(payload, ["AAPL", "MSFT"])
    assert answers["AAPL"] is not None
    assert answers["AAPL"].last == 345.68  # type: ignore[union-attr]
    assert answers["AAPL"].prev_close == 343.91  # type: ignore[union-attr]
    assert answers["AAPL"].volume == 17_642_482  # type: ignore[union-attr]
    assert answers["AAPL"].ts_exchange.tzinfo is not None  # type: ignore[union-attr]
    assert answers["MSFT"] is None


def test_cnbc_batch_source_splits_batches_and_parses(monkeypatch: Any) -> None:
    web = MockWeb()

    def responder(request: httpx2.Request) -> httpx2.Response:
        raw = request.url.params.get("symbols") or ""
        symbols = [part for part in raw.split("|") if part]
        rows = [
            {
                "symbol": symbol,
                "last": "100.50",
                "last_time": "2026-10-27T14:49:51.640-0400",
                "previous_day_closing": "100.00",
                "volume": "1,000",
                "realTime": "true",
            }
            for symbol in symbols
        ]
        return httpx2.Response(200, json={"FormattedQuoteResult": {"FormattedQuote": rows}})

    web.route("quote.cnbc.com", responder)
    install_seam(monkeypatch, web)
    source = CnbcBatchSource(batch_size=2)
    answers = _run(source.fetch_many(["AAPL", "MSFT", "NVDA"]))
    assert len(answers) == 3
    assert all(answers[symbol] is not None for symbol in answers)
    assert len(web.requests) == 2
    assert all("symbols=" in record.url for record in web.requests)


def _run(awaitable: Any) -> Any:
    import asyncio

    return asyncio.run(awaitable)
