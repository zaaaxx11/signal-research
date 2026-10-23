"""P8 sources: Yahoo batch parsing, Nasdaq fallback, and the engine's batch path.

Everything here is driven by ``httpx2.MockTransport`` (through the one HTTP seam)
and by injected sources with a fake clock, so no test opens a socket and no test
depends on how fast the machine is. What is pinned:

* Yahoo answers twenty symbols per request, is asked for the Windows UA and the
  crumb, and maps an absent/zero/untimed row to ``None`` — never ``0``;
* Nasdaq parses ``"$231.50"``/``"28,960,975.244719"``/``"… PM ET"`` and carries
  ``isRealTime`` through; ``data: null`` is "no quote", a broken body is an error;
* ``QuoteEngine.poll_many`` asks a batch source once, fails over per symbol,
  keeps the 5 s staleness rule, dedupes prints and computes signed velocity;
* a source that fails for the whole batch is recorded as an ERROR attempt for
  every pending symbol, and a symbol nobody answered stays ``None``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, Sequence

import httpx2
import pytest

from app.market.quote import (
    QuoteEngine,
    QuoteSource,
    QuoteTick,
    SourceOutcome,
    SourceQuote,
)
from app.market.sources import (
    MAX_BATCH,
    NasdaqQuoteSource,
    QuoteSourceError,
    YahooQuoteSource,
    clean_symbols,
    parse_nasdaq_quote,
    parse_symbol_list,
    parse_yahoo_quotes,
)
from tests._market_quote import (
    TEST_CRUMB,
    crumb_requests,
    install_seam,
    nasdaq_body,
    nasdaq_requests,
    nasdaq_route,
    now_ts,
    requested_symbols,
    yahoo_data_requests,
    yahoo_route,
    yahoo_row,
)
from tests._phase2 import MockWeb

T0 = datetime(2026, 10, 23, 14, 30, 0, tzinfo=UTC)
TS = int(T0.timestamp())


class FakeClock:
    """A clock the test moves by hand; aware UTC, like production."""

    def __init__(self, moment: datetime = T0) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, seconds: float) -> datetime:
        self.moment += timedelta(seconds=seconds)
        return self.moment


def _quote(
    last: float = 101.0,
    prev_close: float | None = 100.0,
    volume: float | None = 1_500.0,
    *,
    at: datetime = T0,
) -> SourceQuote:
    return SourceQuote(last=last, prev_close=prev_close, volume=volume, ts_exchange=at)


def _symbols_of(url: str) -> list[str]:
    return requested_symbols(httpx2.Request("GET", url))


@pytest.fixture()
def web(monkeypatch: pytest.MonkeyPatch) -> MockWeb:
    """A recording fake transport installed on the one HTTP seam."""

    site = MockWeb()
    install_seam(monkeypatch, site)
    return site


# ---------------------------------------------------------------------------
# Yahoo: one request per twenty symbols, parsed strictly
# ---------------------------------------------------------------------------


async def test_yahoo_batches_twenty_symbols_in_one_request(web: MockWeb) -> None:
    symbols = [f"SYM{index:02d}" for index in range(MAX_BATCH)]
    table = {
        symbol: yahoo_row(symbol, price=100.0 + index, ts=TS)
        for index, symbol in enumerate(symbols)
    }
    yahoo_route(web, table)

    answers = await YahooQuoteSource().fetch_many(symbols)

    data = yahoo_data_requests(web)
    assert len(data) == 1, "twenty symbols are one Yahoo request, not twenty"
    assert _symbols_of(data[0].url) == symbols
    assert f"crumb={TEST_CRUMB}" in data[0].url, "the crumb must ride the data URL"
    assert "Windows NT 10.0" in data[0].headers["user-agent"], (
        "Yahoo answers 429 to a Linux UA; the Windows string is part of the protocol"
    )
    assert len(crumb_requests(web)) == 1

    first = answers["SYM00"]
    assert first is not None
    assert first.last == 100.0
    assert first.prev_close == 229.0
    assert first.volume == 1_234_567.0
    assert first.ts_exchange == T0
    assert first.realtime is None, "v7 does not claim realtime, so the source must not"
    assert len(answers) == MAX_BATCH


async def test_yahoo_splits_more_than_twenty_symbols_into_capped_chunks(web: MockWeb) -> None:
    symbols = [f"S{index:03d}" for index in range(45)]
    yahoo_route(web, {symbol: yahoo_row(symbol, ts=TS) for symbol in symbols})

    answers = await YahooQuoteSource().fetch_many(symbols)

    batches = [_symbols_of(record.url) for record in yahoo_data_requests(web)]
    assert [len(batch) for batch in batches] == [20, 20, 5]
    assert sum(len(batch) for batch in batches) == 45
    assert set(answers) == set(symbols)
    assert all(answer is not None for answer in answers.values())


@pytest.mark.parametrize("batch_size", [0, 21, -1, 100])
def test_yahoo_rejects_a_batch_size_the_provider_cannot_answer(batch_size: int) -> None:
    with pytest.raises(ValueError, match="batch_size"):
        YahooQuoteSource(batch_size=batch_size)


def test_yahoo_batch_size_is_exactly_twenty_by_default() -> None:
    source = YahooQuoteSource()

    assert source.batch_size == MAX_BATCH == 20
    assert YahooQuoteSource(batch_size=20).batch_size == 20


def test_yahoo_rejects_a_non_integer_batch_size() -> None:
    with pytest.raises(TypeError, match="batch_size"):
        YahooQuoteSource(batch_size=True)  # type: ignore[arg-type]


async def test_yahoo_unknown_symbol_answers_none_not_a_zero(web: MockWeb) -> None:
    yahoo_route(web, {"AAPL": yahoo_row("AAPL", ts=TS)})

    answers = await YahooQuoteSource().fetch_many(["AAPL", "ZZZZ"])

    assert answers["AAPL"] is not None
    assert answers["ZZZZ"] is None, "Yahoo omits unknown tickers; that is not a 0.0 quote"


@pytest.mark.parametrize("price", [0, 0.0, -3.5, None, "N/A", "garbage"])
async def test_yahoo_a_row_without_a_usable_price_is_not_a_quote(
    web: MockWeb, price: Any
) -> None:
    yahoo_route(web, {"AAPL": yahoo_row("AAPL", price=price, ts=TS)})

    answers = await YahooQuoteSource().fetch_many(["AAPL"])

    assert answers["AAPL"] is None


async def test_yahoo_a_row_without_a_timestamp_is_not_a_quote(web: MockWeb) -> None:
    row = dict(yahoo_row("AAPL", ts=TS))
    row["regularMarketTime"] = None
    yahoo_route(web, {"AAPL": row})

    answers = await YahooQuoteSource().fetch_many(["AAPL"])

    assert answers["AAPL"] is None, "a price without the data's clock may not exist"


async def test_yahoo_non_200_raises_and_keeps_the_status(web: MockWeb) -> None:
    yahoo_route(web, {"AAPL": yahoo_row("AAPL", ts=TS)}, status=429)

    with pytest.raises(QuoteSourceError) as caught:
        await YahooQuoteSource().fetch_many(["AAPL"])

    assert caught.value.status == 429
    assert "429" in str(caught.value)
    assert len(yahoo_data_requests(web)) == 1, "a 429 is not retried in a tight loop"


async def test_yahoo_a_body_without_the_envelope_is_an_error(web: MockWeb) -> None:
    web.route("fc.yahoo.com", lambda _request: httpx2.Response(404, text=""))

    def api(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/v1/test/getcrumb"):
            return httpx2.Response(200, text=TEST_CRUMB)
        return httpx2.Response(
            200, json={"finance": {"result": None, "error": {"code": "Unauthorized"}}}
        )

    web.route("query1.finance.yahoo.com", api)

    with pytest.raises(QuoteSourceError, match="quoteResponse"):
        await YahooQuoteSource().fetch_many(["AAPL"])


async def test_yahoo_refreshes_a_rejected_crumb_once(web: MockWeb) -> None:
    state = {"data": 0}

    web.route("fc.yahoo.com", lambda _request: httpx2.Response(404, text=""))

    def api(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/v1/test/getcrumb"):
            return httpx2.Response(200, text=TEST_CRUMB)
        state["data"] += 1
        if state["data"] == 1:
            return httpx2.Response(
                401, json={"finance": {"result": None, "error": {"code": "Unauthorized"}}}
            )
        return httpx2.Response(
            200, json={"quoteResponse": {"result": [yahoo_row("AAPL", ts=TS)], "error": None}}
        )

    web.route("query1.finance.yahoo.com", api)

    answers = await YahooQuoteSource().fetch_many(["AAPL"])

    assert answers["AAPL"] is not None, "a stale crumb is auth plumbing, not a verdict"
    assert state["data"] == 2
    assert len(crumb_requests(web)) == 2, "one fresh handshake before the retry"


async def test_yahoo_reuses_the_crumb_across_polls(web: MockWeb) -> None:
    yahoo_route(web, {"AAPL": yahoo_row("AAPL", ts=TS)})
    source = YahooQuoteSource()

    await source.fetch_many(["AAPL"])
    answers = await source.fetch_many(["AAPL"])

    assert answers["AAPL"] is not None
    assert len(crumb_requests(web)) == 1, "the handshake is cached, not repeated per poll"
    assert len(yahoo_data_requests(web)) == 2


async def test_yahoo_recovers_when_the_cached_crumb_is_rejected(web: MockWeb) -> None:
    state = {"data": 0, "crumbs": 0}

    web.route("fc.yahoo.com", lambda _request: httpx2.Response(404, text=""))

    def api(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/v1/test/getcrumb"):
            state["crumbs"] += 1
            return httpx2.Response(200, text=TEST_CRUMB)
        state["data"] += 1
        if state["data"] == 2:
            return httpx2.Response(
                401, json={"finance": {"result": None, "error": {"code": "Invalid Crumb"}}}
            )
        return httpx2.Response(
            200, json={"quoteResponse": {"result": [yahoo_row("AAPL", ts=TS)], "error": None}}
        )

    web.route("query1.finance.yahoo.com", api)
    source = YahooQuoteSource()

    assert await source.fetch_many(["AAPL"]) is not None  # warms and caches
    answers = await source.fetch_many(["AAPL"])  # 401, refresh, retry

    assert answers["AAPL"] is not None
    assert state == {"data": 3, "crumbs": 2}
    assert len(yahoo_data_requests(web)) == 3


async def test_yahoo_a_failed_chunk_does_not_discard_the_good_chunks(web: MockWeb) -> None:
    symbols = [f"S{index:03d}" for index in range(25)]
    yahoo_route(
        web,
        {symbol: yahoo_row(symbol, ts=TS) for symbol in symbols},
        fail_chunks={1},
    )

    answers = await YahooQuoteSource().fetch_many(symbols)

    assert all(answers[symbol] is not None for symbol in symbols[:20])
    assert all(answers[symbol] is None for symbol in symbols[20:]), (
        "the failed chunk's symbols fall back per symbol; they are not zeroes"
    )


async def test_yahoo_every_chunk_failing_is_a_source_error(web: MockWeb) -> None:
    yahoo_route(web, {"AAPL": yahoo_row("AAPL", ts=TS)}, fail_chunks={0})

    with pytest.raises(QuoteSourceError, match="yahoo failed"):
        await YahooQuoteSource().fetch_many(["AAPL"])


def test_parse_yahoo_quotes_reads_every_documented_field() -> None:
    payload = {
        "quoteResponse": {
            "result": [
                {
                    "symbol": "aapl",
                    "regularMarketPrice": 231.5,
                    "regularMarketPreviousClose": 229.0,
                    "regularMarketVolume": 12_345,
                    "regularMarketTime": TS,
                }
            ],
            "error": None,
        }
    }

    answers = parse_yahoo_quotes(payload, ["AAPL", "MSFT"])

    assert answers["AAPL"] is not None and answers["AAPL"].last == 231.5
    assert answers["MSFT"] is None


def test_parse_yahoo_quotes_rejects_a_non_json_body() -> None:
    with pytest.raises(QuoteSourceError):
        parse_yahoo_quotes("<!DOCTYPE html>", ["AAPL"])


# ---------------------------------------------------------------------------
# Nasdaq: per symbol, currency strings and a human timestamp
# ---------------------------------------------------------------------------


async def test_nasdaq_parses_prices_volume_and_the_realtime_flag(web: MockWeb) -> None:
    nasdaq_route(
        web,
        {
            "AAPL": nasdaq_body(
                "AAPL", price="$231.50", volume="28,960,975.244719", ts=TS, realtime=True
            )
        },
    )

    quote = await NasdaqQuoteSource().fetch("aapl")

    record = nasdaq_requests(web)[0]
    assert record.url.endswith("/api/quote/AAPL/info?assetclass=stocks")
    assert record.headers["origin"] == "https://www.nasdaq.com"
    assert "nasdaq.com" in record.headers["referer"]
    assert "Windows NT 10.0" in record.headers["user-agent"]

    assert quote is not None
    assert quote.last == 231.5
    assert quote.volume == pytest.approx(28_960_975.244719)
    assert quote.ts_exchange == T0
    assert quote.realtime is True
    assert quote.prev_close is None, "Nasdaq publishes no previous close here — never invent one"


async def test_nasdaq_delayed_quote_is_still_a_quote_with_realtime_false(web: MockWeb) -> None:
    nasdaq_route(web, {"AAPL": nasdaq_body("AAPL", ts=TS, realtime=False)})

    quote = await NasdaqQuoteSource().fetch("AAPL")

    assert quote is not None
    assert quote.realtime is False
    assert quote.ts_exchange == T0


async def test_nasdaq_unknown_symbol_is_none_not_an_error(web: MockWeb) -> None:
    nasdaq_route(web, {"ZZZZ": nasdaq_body("ZZZZ", data=False)})

    assert await NasdaqQuoteSource().fetch("ZZZZ") is None


async def test_nasdaq_unparseable_timestamp_is_an_error(web: MockWeb) -> None:
    body = nasdaq_body("AAPL", ts=TS)
    body["data"]["primaryData"]["lastTradeTimestamp"] = "yesterday-ish"
    nasdaq_route(web, {"AAPL": body})

    with pytest.raises(QuoteSourceError, match="lastTradeTimestamp"):
        await NasdaqQuoteSource().fetch("AAPL")


async def test_nasdaq_non_200_is_a_source_error(web: MockWeb) -> None:
    nasdaq_route(web, {}, status=503)

    with pytest.raises(QuoteSourceError) as caught:
        await NasdaqQuoteSource().fetch("AAPL")

    assert caught.value.status == 503


async def test_nasdaq_na_price_is_none(web: MockWeb) -> None:
    nasdaq_route(web, {"AAPL": nasdaq_body("AAPL", price="N/A")})

    assert await NasdaqQuoteSource().fetch("AAPL") is None


def test_parse_nasdaq_quote_of_an_empty_body_is_none() -> None:
    assert parse_nasdaq_quote({"data": None}) is None
    assert parse_nasdaq_quote({"data": {"primaryData": None}}) is None


def test_raw_nasdaq_numbers_are_accepted_too() -> None:
    body = {
        "data": {
            "primaryData": {
                "lastSalePrice": 99.5,
                "lastTradeTimestamp": "Oct 23, 2026 3:48 PM ET",
                "isRealTime": True,
                "volume": 10,
            }
        }
    }

    quote = parse_nasdaq_quote(body)

    assert quote is not None and quote.last == 99.5 and quote.volume == 10.0


# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------


def test_symbol_lists_are_split_deduped_and_uppercased() -> None:
    assert parse_symbol_list(" aapl, MSFT,AAPL , brk-b,^GSPC ") == (
        "AAPL",
        "MSFT",
        "BRK-B",
        "^GSPC",
    )
    assert parse_symbol_list("   ") == ()
    assert clean_symbols(["nvda", "NVDA", " eur=x "]) == ("NVDA", "EUR=X")


@pytest.mark.parametrize("text", ["AAPL,,MSFT", "AAPL,", "AAPL,$BAD", "AAP L", "A" * 40])
def test_malformed_symbol_lists_raise(text: str) -> None:
    with pytest.raises(ValueError):
        parse_symbol_list(text)


# ---------------------------------------------------------------------------
# Engine: batch polling keeps the single-symbol contract
# ---------------------------------------------------------------------------


async def test_poll_many_asks_a_batch_source_once_and_fails_over_per_symbol() -> None:
    asked: list[tuple[str, ...]] = []

    def yahoo(symbols: Sequence[str]) -> Mapping[str, SourceQuote | None]:
        asked.append(tuple(symbols))
        return {"AAPL": _quote(last=101.0), "MSFT": None}

    def nasdaq(symbol: str) -> SourceQuote | None:
        return _quote(last=202.0) if symbol == "MSFT" else None

    engine = QuoteEngine(
        [QuoteSource("yahoo", fetch_many=yahoo), QuoteSource("nasdaq", fetch=nasdaq)],
        clock=FakeClock(),
    )

    ticks = await engine.poll_many(["aapl", "msft", "AAPL"])

    assert asked == [("AAPL", "MSFT")], "one batch, deduped and normalized"
    assert list(ticks) == ["AAPL", "MSFT"]
    assert ticks["AAPL"] is not None and ticks["AAPL"].source == "yahoo"
    assert ticks["MSFT"] is not None and ticks["MSFT"].source == "nasdaq"
    assert [(a.symbol, a.source, a.outcome) for a in engine.attempts] == [
        ("AAPL", "yahoo", SourceOutcome.OK),
        ("MSFT", "yahoo", SourceOutcome.EMPTY),
        ("MSFT", "nasdaq", SourceOutcome.OK),
    ]


async def test_poll_many_stale_primary_loses_to_a_fresh_backup() -> None:
    clock = FakeClock(T0 + timedelta(seconds=60))
    engine = QuoteEngine(
        [
            QuoteSource("yahoo", fetch_many=lambda symbols: {s: _quote(at=T0) for s in symbols}),
            QuoteSource("nasdaq", fetch=lambda symbol: _quote(at=clock())),
        ],
        clock=clock,
    )

    ticks = await engine.poll_many(["AAPL"])

    assert ticks["AAPL"] is not None
    assert ticks["AAPL"].source == "nasdaq"
    assert ticks["AAPL"].stale is False
    assert [(a.source, a.outcome) for a in engine.attempts] == [
        ("yahoo", SourceOutcome.STALE),
        ("nasdaq", SourceOutcome.OK),
    ]


async def test_poll_many_serves_the_freshest_stale_tick_flagged() -> None:
    clock = FakeClock(T0)
    engine = QuoteEngine(
        [
            QuoteSource(
                "yahoo", fetch_many=lambda symbols: {s: _quote(last=95.0, at=T0 - timedelta(seconds=10)) for s in symbols}
            ),
            QuoteSource("nasdaq", fetch=lambda symbol: _quote(last=90.0, at=T0 - timedelta(seconds=30))),
        ],
        clock=clock,
    )

    ticks = await engine.poll_many(["AAPL"])

    assert ticks["AAPL"] is not None
    assert ticks["AAPL"].source == "yahoo"
    assert ticks["AAPL"].last == 95.0
    assert ticks["AAPL"].stale is True


async def test_poll_many_no_answer_is_none_not_a_zero_tick() -> None:
    engine = QuoteEngine(
        [
            QuoteSource("yahoo", fetch_many=lambda symbols: {s: None for s in symbols}),
            QuoteSource("nasdaq", fetch=lambda symbol: None),
        ],
        clock=FakeClock(),
    )

    ticks = await engine.poll_many(["AAPL", "MSFT"])

    assert ticks == {"AAPL": None, "MSFT": None}
    assert [a.outcome for a in engine.attempts] == [SourceOutcome.EMPTY] * 4
    assert engine.history("AAPL") == ()


async def test_a_whole_source_failure_is_recorded_for_every_pending_symbol() -> None:
    def broken(symbols: Sequence[str]) -> Mapping[str, SourceQuote | None]:
        raise RuntimeError("yahoo exploded")

    engine = QuoteEngine(
        [QuoteSource("yahoo", fetch_many=broken), QuoteSource("nasdaq", fetch=lambda s: _quote())],
        clock=FakeClock(),
    )

    ticks = await engine.poll_many(["AAPL", "MSFT"])

    assert ticks["AAPL"] is not None and ticks["AAPL"].source == "nasdaq"
    assert ticks["MSFT"] is not None and ticks["MSFT"].source == "nasdaq"
    yahoo_attempts = [a for a in engine.attempts if a.source == "yahoo"]
    assert [(a.symbol, a.outcome) for a in yahoo_attempts] == [
        ("AAPL", SourceOutcome.ERROR),
        ("MSFT", SourceOutcome.ERROR),
    ]
    assert "yahoo exploded" in (yahoo_attempts[0].detail or "")


async def test_a_non_mapping_batch_answer_is_rejected() -> None:
    engine = QuoteEngine(
        [
            QuoteSource("yahoo", fetch_many=lambda symbols: [_quote()]),  # type: ignore[arg-type,return-value]
            QuoteSource("nasdaq", fetch=lambda symbol: _quote(last=77.0)),
        ],
        clock=FakeClock(),
    )

    ticks = await engine.poll_many(["AAPL"])

    assert ticks["AAPL"] is not None and ticks["AAPL"].last == 77.0
    assert engine.attempts[0].outcome is SourceOutcome.ERROR
    assert "mapping" in (engine.attempts[0].detail or "")


async def test_poll_many_accepts_async_sources() -> None:
    async def yahoo(symbols: Sequence[str]) -> Mapping[str, SourceQuote | None]:
        return {symbol: _quote(last=101.0) for symbol in symbols}

    async def nasdaq(symbol: str) -> SourceQuote:
        return _quote(last=202.0)

    engine = QuoteEngine(
        [QuoteSource("yahoo", fetch_many=yahoo), QuoteSource("nasdaq", fetch=nasdaq)],
        clock=FakeClock(),
    )

    ticks = await engine.poll_many(["AAPL"])

    assert ticks["AAPL"] is not None and ticks["AAPL"].source == "yahoo"


async def test_poll_many_dedupes_identical_prints_and_keeps_velocity() -> None:
    clock = FakeClock()
    engine = QuoteEngine(
        [QuoteSource("yahoo", fetch_many=lambda symbols: {s: _quote(at=T0) for s in symbols})],
        clock=clock,
    )

    first = (await engine.poll_many(["AAPL"]))["AAPL"]
    clock.advance(2.0)
    second = (await engine.poll_many(["AAPL"]))["AAPL"]

    assert first is not None and second is not None
    assert second.ts_received == first.ts_received, "the arrival of that print, not of the re-poll"
    assert len(engine.history("AAPL")) == 1
    assert second.velocity is None


async def test_poll_many_velocity_is_signed_per_second() -> None:
    clock = FakeClock()
    prices = iter([100.0, 102.0])
    engine = QuoteEngine(
        [
            QuoteSource(
                "yahoo",
                fetch_many=lambda symbols: {s: _quote(last=next(prices), at=clock()) for s in symbols},
            )
        ],
        clock=clock,
    )

    assert (await engine.poll_many(["AAPL"]))["AAPL"] is not None
    clock.advance(2.0)
    second = (await engine.poll_many(["AAPL"]))["AAPL"]

    assert second is not None
    assert second.velocity == pytest.approx(1.0)
    assert second.direction.value == "up"


async def test_per_symbol_answers_are_stamped_at_their_own_arrival() -> None:
    """A slow per-symbol fallback must not make its first answer look stale."""

    moment = {"now": T0}

    def clock() -> datetime:
        return moment["now"]

    def nasdaq(symbol: str) -> SourceQuote:
        quote = _quote(at=moment["now"])
        moment["now"] += timedelta(seconds=3)  # the HTTP call takes three seconds
        return quote

    engine = QuoteEngine([QuoteSource("nasdaq", fetch=nasdaq)], clock=clock)

    ticks = await engine.poll_many(["AAPL", "MSFT"])

    assert ticks["AAPL"] is not None and ticks["MSFT"] is not None
    assert ticks["AAPL"].ts_received == T0 + timedelta(seconds=3)
    assert ticks["MSFT"].ts_received == T0 + timedelta(seconds=6)
    assert ticks["AAPL"].stale is False, "judging AAPL at MSFT's arrival would flag it"
    assert ticks["MSFT"].stale is False


async def test_poll_many_falls_back_to_cache_re_aged_on_the_clock() -> None:
    clock = FakeClock()
    state = {"down": False}

    def feed(symbols: Sequence[str]) -> Mapping[str, SourceQuote | None]:
        if state["down"]:
            raise RuntimeError("feed down")
        return {symbol: _quote(at=clock()) for symbol in symbols}

    engine = QuoteEngine([QuoteSource("yahoo", fetch_many=feed)], clock=clock)
    first = (await engine.poll_many(["AAPL"]))["AAPL"]
    assert first is not None and first.stale is False

    clock.advance(6.0)
    state["down"] = True
    second = (await engine.poll_many(["AAPL"]))["AAPL"]

    assert second is not None
    assert second.ts_exchange == first.ts_exchange
    assert second.stale is True


async def test_a_source_that_accepts_stale_ends_the_search_for_that_symbol() -> None:
    clock = FakeClock(T0 + timedelta(seconds=60))
    fallbacks: list[str] = []

    def nasdaq(symbol: str) -> SourceQuote:
        fallbacks.append(symbol)
        return _quote(at=clock())

    engine = QuoteEngine(
        [
            QuoteSource(
                "yahoo",
                fetch_many=lambda symbols: {s: _quote(at=T0) for s in symbols},
                accept_stale=True,
            ),
            QuoteSource("nasdaq", fetch=nasdaq),
        ],
        clock=clock,
    )

    ticks = await engine.poll_many(["AAPL"])

    assert ticks["AAPL"] is not None
    assert ticks["AAPL"].source == "yahoo"
    assert ticks["AAPL"].stale is True, "accepting a stale answer must not make it look fresh"
    assert fallbacks == [], "a closed market's last print is the answer, not a fallback trigger"
    assert [(a.source, a.outcome) for a in engine.attempts] == [
        ("yahoo", SourceOutcome.STALE)
    ]


def test_sync_poll_also_honours_accept_stale() -> None:
    clock = FakeClock(T0 + timedelta(seconds=60))
    engine = QuoteEngine(
        [
            QuoteSource("yahoo", fetch=lambda symbol: _quote(at=T0), accept_stale=True),
            QuoteSource("nasdaq", fetch=lambda symbol: _quote(last=99.0, at=clock())),
        ],
        clock=clock,
    )

    tick = engine.poll("AAPL")

    assert tick is not None and tick.source == "yahoo" and tick.stale is True


def test_accept_stale_must_be_a_bool() -> None:
    with pytest.raises(TypeError, match="accept_stale"):
        QuoteSource("yahoo", fetch_many=lambda symbols: {}, accept_stale="yes")  # type: ignore[arg-type]


async def test_concurrent_poll_many_calls_share_state_without_corruption() -> None:
    async def yahoo(symbols: Sequence[str]) -> Mapping[str, SourceQuote | None]:
        await asyncio.sleep(0)  # yield mid-poll, as a real HTTP call would
        return {symbol: _quote(last=101.0, at=T0) for symbol in symbols}

    engine = QuoteEngine([QuoteSource("yahoo", fetch_many=yahoo)], clock=FakeClock())

    results = await asyncio.gather(
        engine.poll_many(["AAPL"]), engine.poll_many(["AAPL"]), engine.poll_many(["AAPL"])
    )

    assert all(result["AAPL"] is not None for result in results)
    assert len(engine.history("AAPL")) == 1, "three concurrent reads of one print are one tick"


async def test_poll_many_of_an_empty_list_is_empty() -> None:
    engine = QuoteEngine([QuoteSource("yahoo", fetch_many=lambda symbols: {})], clock=FakeClock())

    assert await engine.poll_many([]) == {}
    assert engine.attempts == ()


async def test_poll_many_does_not_treat_a_lowercase_batch_key_as_missing() -> None:
    engine = QuoteEngine(
        [
            QuoteSource(
                "yahoo",
                fetch_many=lambda symbols: {symbol.lower(): _quote() for symbol in symbols},
            )
        ],
        clock=FakeClock(),
    )

    tick = (await engine.poll_many(["AAPL"]))["AAPL"]

    assert tick is not None and tick.source == "yahoo"


def test_sync_poll_reports_a_batch_only_source_instead_of_pretending_empty() -> None:
    engine = QuoteEngine(
        [QuoteSource("yahoo", fetch_many=lambda symbols: {})],
        clock=FakeClock(),
    )

    assert engine.poll("AAPL") is None
    attempt = engine.attempts[0]
    assert attempt.outcome is SourceOutcome.ERROR
    assert "poll_many" in (attempt.detail or "")


def test_a_source_must_declare_fetch_or_fetch_many() -> None:
    with pytest.raises(ValueError, match="fetch"):
        QuoteSource("empty")
    with pytest.raises(TypeError, match="fetch_many"):
        QuoteSource("bad", fetch_many="nope")  # type: ignore[arg-type]


def test_engine_still_accepts_positional_callables() -> None:
    def nasdaq(symbol: str) -> SourceQuote:
        return _quote(last=101.0)

    engine = QuoteEngine([nasdaq], ("AAPL",), clock=FakeClock())
    tick = engine.poll("AAPL")

    assert engine.sources == ("nasdaq",)
    assert isinstance(tick, QuoteTick) and tick.source == "nasdaq"


def test_source_quote_realtime_must_be_a_bool() -> None:
    with pytest.raises(TypeError, match="realtime"):
        SourceQuote(last=10.0, ts_exchange=T0, realtime="yes")  # type: ignore[arg-type]


def test_now_ts_is_a_current_epoch() -> None:
    assert abs(now_ts() - datetime.now(UTC).timestamp()) < 5
