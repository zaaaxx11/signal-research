"""``GET /market/quote`` — the realtime read, end to end but offline.

The app is built by :func:`tests._market_quote.market_client`, whose only HTTP
client is an ``httpx2.MockTransport``; nothing here touches a socket. What is
pinned (P8 gate):

* one request answers a watchlist: 200, one document, per-quote provenance;
* ``symbols`` is deduped and never truncated — over 100 is a 422 *before* any
  provider is called;
* an empty or malformed list is a 422 in the house envelope;
* a symbol nobody answered is in ``missing`` — never a zero-priced quote;
* Yahoo is asked once per batch and Nasdaq only for the symbols Yahoo could
  not answer; a dead Yahoo is transparent (``source: nasdaq``);
* every source down is a 503, not a 200 with an empty or zeroed book;
* a quote older than 5 s is flagged ``stale`` at both levels.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import httpx2

from app.market.quote import QuoteEngine, QuoteSource, SourceQuote
from tests._market_quote import (
    TEST_CRUMB,
    market_client,
    nasdaq_body,
    nasdaq_requests,
    nasdaq_route,
    now_ts,
    requested_symbols,
    yahoo_data_requests,
    yahoo_route,
    yahoo_row,
)

ROUTE = "/market/quote"

DOCUMENT_KEYS = {
    "as_of",
    "source",
    "stale",
    "latency_ms",
    "requested",
    "returned",
    "quotes",
    "missing",
}

QUOTE_KEYS = {
    "symbol",
    "last",
    "prev_close",
    "change",
    "change_pct",
    "volume",
    "direction",
    "velocity",
    # The extended session: published null when the provider returned no
    # post-market print, which is the same fact the screener's after-hours
    # buckets read.
    "post_market_price",
    "post_market_change_pct",
    "ts_exchange",
    "ts_received",
    "source",
    "stale",
}


def _symbols_of(url: str) -> list[str]:
    return requested_symbols(httpx2.Request("GET", url))


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


async def test_quote_answers_a_two_symbol_watchlist_from_one_yahoo_batch(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with market_client(tmp_path, monkeypatch) as harness:
        stamp = now_ts()
        yahoo_route(
            harness.web,
            {
                "AAPL": yahoo_row("AAPL", price=231.5, prev_close=229.0, volume=1_000, ts=stamp),
                "MSFT": yahoo_row("MSFT", price=410.0, prev_close=412.0, volume=2_000, ts=stamp),
            },
        )

        response = await harness.get("AAPL,MSFT")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == DOCUMENT_KEYS
    assert body["source"] == "yahoo"
    assert body["stale"] is False
    assert body["requested"] == 2
    assert body["returned"] == 2
    assert body["missing"] == []
    assert isinstance(body["latency_ms"], (int, float)) and body["latency_ms"] >= 0
    assert datetime.fromisoformat(body["as_of"]).utcoffset() is not None

    quotes = body["quotes"]
    assert [quote["symbol"] for quote in quotes] == ["AAPL", "MSFT"]
    for quote in quotes:
        assert set(quote) == QUOTE_KEYS, f"quote fields drifted: {sorted(set(quote) ^ QUOTE_KEYS)}"
        assert quote["last"] > 0
        assert quote["source"] == "yahoo"
        assert quote["stale"] is False
        assert quote["ts_exchange"] == body["as_of"] or quote["ts_exchange"] <= body["as_of"]

    by_symbol = {quote["symbol"]: quote for quote in quotes}
    assert by_symbol["AAPL"]["last"] == 231.5
    assert by_symbol["AAPL"]["prev_close"] == 229.0
    assert by_symbol["AAPL"]["change"] == 2.5
    assert by_symbol["AAPL"]["direction"] == "up"
    assert by_symbol["AAPL"]["volume"] == 1_000.0
    assert by_symbol["MSFT"]["direction"] == "down"
    assert by_symbol["AAPL"]["velocity"] is None, "one print cannot have a velocity"

    data = yahoo_data_requests(harness.web)
    assert len(data) == 1, "two symbols are one Yahoo batch"
    assert _symbols_of(data[0].url) == ["AAPL", "MSFT"]
    assert f"crumb={TEST_CRUMB}" in data[0].url
    assert nasdaq_requests(harness.web) == [], "Nasdaq is a fallback, not a first choice"


async def test_symbols_are_deduped_and_normalized_before_polling(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with market_client(tmp_path, monkeypatch) as harness:
        yahoo_route(harness.web, {"AAPL": yahoo_row("AAPL", ts=now_ts())})

        response = await harness.get("AAPL, aapl ,AAPL")

    body = response.json()
    assert response.status_code == 200
    assert body["requested"] == 1
    assert body["returned"] == 1
    assert [quote["symbol"] for quote in body["quotes"]] == ["AAPL"]
    assert len(yahoo_data_requests(harness.web)) == 1
    assert _symbols_of(yahoo_data_requests(harness.web)[0].url) == ["AAPL"]


async def test_the_engine_is_built_once_and_cached_on_the_app(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with market_client(tmp_path, monkeypatch) as harness:
        yahoo_route(harness.web, {"AAPL": yahoo_row("AAPL", ts=now_ts())})
        assert harness.engine is None, "create_app must not do I/O for a quote"

        await harness.get("AAPL")
        engine = harness.engine

        await harness.get("AAPL")

    assert engine is not None
    assert harness.engine is engine, "one engine per app, or the audit trail splits"


async def test_an_injected_engine_replaces_the_provider_stack(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with market_client(tmp_path, monkeypatch) as harness:
        harness.application.state.quote_engine = QuoteEngine(
            [
                QuoteSource(
                    "fixture",
                    fetch=lambda symbol: SourceQuote(
                        last=123.0, prev_close=120.0, ts_exchange=datetime.now().astimezone()
                    ),
                )
            ]
        )

        response = await harness.get("AAPL")

    body = response.json()
    assert response.status_code == 200
    assert body["source"] == "fixture"
    assert body["quotes"][0]["last"] == 123.0
    assert harness.web.requests == [], "an injected engine must not reach the network"


# ---------------------------------------------------------------------------
# Missing symbols and failover
# ---------------------------------------------------------------------------


async def test_a_symbol_nobody_answered_is_listed_as_missing_not_zeroed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with market_client(tmp_path, monkeypatch) as harness:
        yahoo_route(harness.web, {"AAPL": yahoo_row("AAPL", ts=now_ts())})
        nasdaq_route(harness.web, {})

        response = await harness.get("AAPL,ZZZZ")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["returned"] == 1
    assert [quote["symbol"] for quote in body["quotes"]] == ["AAPL"]
    assert body["missing"] == ["ZZZZ"]
    assert all(quote["last"] > 0 for quote in body["quotes"])

    fallbacks = nasdaq_requests(harness.web)
    assert len(fallbacks) == 1, "only the symbol Yahoo missed is asked of Nasdaq"
    assert "/api/quote/ZZZZ/" in fallbacks[0].url


async def test_nasdaq_covers_exactly_the_symbols_yahoo_could_not_answer(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with market_client(tmp_path, monkeypatch) as harness:
        yahoo_route(harness.web, {"AAPL": yahoo_row("AAPL", ts=now_ts())})
        nasdaq_route(harness.web, {"MSFT": nasdaq_body("MSFT", ts=now_ts())})

        response = await harness.get("AAPL,MSFT")

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["source"] == "mixed", "two sources answered, so the document is mixed"
    by_symbol = {quote["symbol"]: quote["source"] for quote in body["quotes"]}
    assert by_symbol == {"AAPL": "yahoo", "MSFT": "nasdaq"}
    assert body["missing"] == []
    assert len(yahoo_data_requests(harness.web)) == 1
    assert [record.url for record in nasdaq_requests(harness.web)] == [
        "https://api.nasdaq.com/api/quote/MSFT/info?assetclass=stocks"
    ]


async def test_a_dead_yahoo_is_transparent_because_nasdaq_answers_every_symbol(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with market_client(tmp_path, monkeypatch) as harness:
        yahoo_route(harness.web, {}, status=500)
        nasdaq_route(
            harness.web,
            {
                "AAPL": nasdaq_body("AAPL", price="$231.50", ts=now_ts()),
                "MSFT": nasdaq_body("MSFT", price="$410.00", ts=now_ts()),
            },
        )

        response = await harness.get("AAPL,MSFT")

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["source"] == "nasdaq"
    assert body["returned"] == 2
    assert body["missing"] == []
    assert {quote["last"] for quote in body["quotes"]} == {231.5, 410.0}
    assert len(nasdaq_requests(harness.web)) == 2, "the fallback is per symbol"
    assert len(yahoo_data_requests(harness.web)) == 1, "Yahoo is asked once, not per symbol"


async def test_every_source_down_is_503_with_no_quotes_and_no_zeroes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with market_client(tmp_path, monkeypatch) as harness:
        yahoo_route(harness.web, {}, status=500)
        nasdaq_route(harness.web, {}, status=503)

        response = await harness.get("AAPL")

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["error"] == "quotes_unavailable"
    assert body["errorMessage"]
    assert "quotes" not in body, "a 503 must not smuggle a zero-priced quote into the body"
    assert body.get("last") is None


# ---------------------------------------------------------------------------
# Validation: 422 before any provider is called
# ---------------------------------------------------------------------------


async def test_missing_symbols_parameter_is_422(tmp_path: Path, monkeypatch: Any) -> None:
    async with market_client(tmp_path, monkeypatch) as harness:
        response = await harness.get(None)

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_symbols"
    assert harness.web.requests == []


async def test_an_empty_symbols_parameter_is_422(tmp_path: Path, monkeypatch: Any) -> None:
    async with market_client(tmp_path, monkeypatch) as harness:
        response = await harness.get("   ")

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_symbols"
    assert "required" in response.json()["errorMessage"]
    assert harness.web.requests == []


async def test_a_malformed_symbol_is_422_and_named(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with market_client(tmp_path, monkeypatch) as harness:
        response = await harness.get("AAPL,$BAD")

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "invalid_symbols"
    assert "$BAD" in body["errorMessage"]
    assert harness.web.requests == [], "validation precedes the network"


async def test_more_than_one_hundred_symbols_is_422_and_sends_no_request(
    tmp_path: Path, monkeypatch: Any
) -> None:
    symbols = ",".join(f"S{index:03d}" for index in range(101))
    async with market_client(tmp_path, monkeypatch) as harness:
        response = await harness.get(symbols)

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "too_many_symbols"
    assert "100" in body["errorMessage"]
    assert harness.web.requests == []


async def test_exactly_one_hundred_symbols_is_five_yahoo_batches(
    tmp_path: Path, monkeypatch: Any
) -> None:
    symbols = [f"S{index:03d}" for index in range(100)]
    async with market_client(tmp_path, monkeypatch) as harness:
        yahoo_route(
            harness.web,
            {symbol: yahoo_row(symbol, price=10.0 + index, ts=now_ts()) for index, symbol in enumerate(symbols)},
        )

        response = await harness.get(",".join(symbols))

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["requested"] == body["returned"] == 100
    batches = [_symbols_of(record.url) for record in yahoo_data_requests(harness.web)]
    assert [len(batch) for batch in batches] == [20, 20, 20, 20, 20]
    assert nasdaq_requests(harness.web) == []


# ---------------------------------------------------------------------------
# Staleness and shape
# ---------------------------------------------------------------------------


async def test_a_quote_older_than_five_seconds_is_flagged_within_the_document(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with market_client(tmp_path, monkeypatch) as harness:
        yahoo_route(harness.web, {"AAPL": yahoo_row("AAPL", ts=now_ts() - 60)})

        response = await harness.get("AAPL")

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["stale"] is True, "a partly-stale document may not read as fresh"
    assert body["quotes"][0]["stale"] is True
    assert datetime.fromisoformat(body["as_of"]) < datetime.now().astimezone()
    assert nasdaq_requests(harness.web) == [], (
        "a closed market's last Yahoo print is the answer; a per-symbol US-only "
        "fallback can only add seconds"
    )


async def test_one_stale_quote_makes_the_document_stale_but_not_its_neighbour(
    tmp_path: Path, monkeypatch: Any
) -> None:
    stamp = now_ts()
    async with market_client(tmp_path, monkeypatch) as harness:
        yahoo_route(
            harness.web,
            {
                "AAPL": yahoo_row("AAPL", ts=stamp),
                "MSFT": yahoo_row("MSFT", ts=stamp - 60),
            },
        )

        response = await harness.get("AAPL,MSFT")

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["stale"] is True, "a partly-stale document may not read as fresh"
    by_symbol = {quote["symbol"]: quote["stale"] for quote in body["quotes"]}
    assert by_symbol == {"AAPL": False, "MSFT": True}


async def test_a_zero_priced_yahoo_row_fails_over_instead_of_reaching_the_client(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with market_client(tmp_path, monkeypatch) as harness:
        yahoo_route(harness.web, {"AAPL": yahoo_row("AAPL", price=0, ts=now_ts())})
        nasdaq_route(harness.web, {"AAPL": nasdaq_body("AAPL", price="$231.50", ts=now_ts())})

        response = await harness.get("AAPL")

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["quotes"][0]["last"] == 231.5, "a 0 is not a price; the fallback must answer"
    assert body["quotes"][0]["source"] == "nasdaq"
    assert len(nasdaq_requests(harness.web)) == 1


async def test_the_route_is_mounted_at_the_documented_path(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with market_client(tmp_path, monkeypatch) as harness:
        paths = {getattr(route, "path", None) for route in harness.application.routes}

    assert ROUTE in paths, f"{ROUTE} must be an introspectable route, got {sorted(p for p in paths if p)}"
