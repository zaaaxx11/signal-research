"""Keyless price registry: CNBC batch primary, Nasdaq per-symbol fallback.

Everything here runs on ``httpx2.MockTransport`` through the one HTTP seam or
on injected fakes, so no test opens a socket (the conftest guard fails the run
if anything tries) and no test needs a credential. What is pinned:

* ``PriceQuote`` is frozen, validates its own fields and derives
  ``change_pct`` from ``last``/``prev_close`` — a field the provider did not
  publish is ``None``, never ``0``;
* ``CnbcPriceSource`` reuses ``CnbcBatchSource`` (one request per 500 symbols,
  keyless) and normalizes every answer to ``PriceQuote`` with
  ``source="cnbc"``; a symbol CNBC omits is ``None``;
* ``NasdaqPriceSource`` reuses ``NasdaqQuoteSource`` per symbol (keyless) and
  isolates each symbol: a 429 or a broken body answers ``None`` for that
  symbol — never a zero price — and never for its neighbours;
* ``PriceRegistry`` asks the primary once, then the fallback **only** for the
  symbols the primary missed; a source that raises or times out loses its own
  pass, never the batch;
* no API key is read anywhere: the module names no credential, publishes its
  public API, and the registry answers with every key-shaped variable scrubbed
  from the environment.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx2
import pytest

from app.market import price as price_module
from app.market.bulk import CnbcBatchSource
from app.market.price import (
    CnbcPriceSource,
    NasdaqPriceSource,
    PriceQuote,
    PriceRegistry,
)
from app.market.quote import SourceQuote
from app.market.sources import NasdaqQuoteSource, QuoteSourceError
from tests._market_quote import install_seam, nasdaq_body, nasdaq_requests, now_ts
from tests._phase2 import MockWeb

ROOT = Path(__file__).resolve().parents[1]
CNBC_HOST = "quote.cnbc.com"
NASDAQ_HOST = "api.nasdaq.com"

#: CNBC prints ``"2026-10-27T14:49:51.640-0400"``; this is that instant in UTC.
T0 = datetime(2026, 10, 27, 18, 49, 51, 640000, tzinfo=UTC)
CNBC_STAMP = "2026-10-27T14:49:51.640-0400"

#: Every key-shaped variable a keyless stack must never need.
KEY_ENV_VARS = (
    "FINNHUB_API_KEY",
    "POLYGON_API_KEY",
    "EODHD_API_KEY",
    "TIINGO_API_KEY",
    "TWELVE_DATA_API_KEY",
    "OPENFIGI_API_KEY",
    "CNBC_API_KEY",
    "NASDAQ_API_KEY",
    "YAHOO_API_KEY",
    "MEE_API_KEY",
    "API_KEY",
)

#: Substrings that would mean the module reaches for a credential. ``import
#: os`` is on the list on purpose: in a keyless stack the environment is the
#: only door a key could walk through, so this module does not even open it.
KEY_TOKENS = (
    "os.environ",
    "getenv",
    "import os",
    "from os",
    "api key",
    "api_key",
    "apikey",
    "api-key",
    "secret",
    "credential",
    "authorization",
    "bearer",
    "x-api",
)


# ---------------------------------------------------------------------------
# Payload builders and routes (CNBC's restQuote envelope, as probed live)
# ---------------------------------------------------------------------------


def cnbc_row(
    symbol: str,
    *,
    last: Any = "218.555",
    prev_close: Any = "217.50",
    volume: Any = "28,960,975",
    stamp: Any = CNBC_STAMP,
) -> dict[str, Any]:
    """One ``FormattedQuote`` entry, as CNBC's backend prints it."""

    return {
        "symbol": symbol,
        "last": last,
        "last_time": stamp,
        "previous_day_closing": prev_close,
        "volume": volume,
        "realTime": "true",
    }


def cnbc_body(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {"FormattedQuoteResult": {"FormattedQuote": list(rows)}}


def cnbc_route(web: MockWeb, table: Mapping[str, Mapping[str, Any]]) -> None:
    """Serve one CNBC body per request; a symbol absent from ``table`` is omitted."""

    def responder(request: httpx2.Request) -> httpx2.Response:
        raw = request.url.params.get("symbols") or ""
        wanted = [token.strip().upper() for token in raw.split("|") if token.strip()]
        rows = [table[symbol] for symbol in wanted if symbol in table]
        return httpx2.Response(200, json=cnbc_body(rows))

    web.route(CNBC_HOST, responder)


def cnbc_requests(web: MockWeb) -> list[Any]:
    return [record for record in web.requests if record.host == CNBC_HOST]


def cnbc_symbols(web: MockWeb) -> list[list[str]]:
    """The symbol list of every CNBC request, in request order."""

    asked: list[list[str]] = []
    for record in cnbc_requests(web):
        url = httpx2.Request("GET", record.url).url
        raw = url.params.get("symbols") or ""
        asked.append([token.strip().upper() for token in raw.split("|") if token.strip()])
    return asked


def price(
    symbol: str,
    last: float = 100.0,
    *,
    prev_close: float | None = None,
    volume: float | None = None,
    at: datetime = T0,
    source: str = "fake",
) -> PriceQuote:
    return PriceQuote(
        symbol=symbol,
        last=last,
        prev_close=prev_close,
        change_pct=None,
        volume=volume,
        as_of=at,
        source=source,
    )


class FakeSource:
    """A recording source with the registry's protocol (``name`` + ``fetch_many``)."""

    def __init__(
        self,
        name: str,
        answers: Mapping[str, PriceQuote | None] | None = None,
        *,
        raises: BaseException | None = None,
        delay_s: float = 0.0,
    ) -> None:
        self.name = name
        self.answers = dict(answers or {})
        self.raises = raises
        self.delay_s = delay_s
        self.calls: list[tuple[str, ...]] = []

    async def fetch_many(self, symbols: Sequence[str]) -> dict[str, PriceQuote | None]:
        self.calls.append(tuple(symbols))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.raises is not None:
            raise self.raises
        return {symbol: self.answers.get(symbol) for symbol in symbols}


@pytest.fixture()
def web(monkeypatch: pytest.MonkeyPatch) -> MockWeb:
    """A recording fake transport installed on the one HTTP seam."""

    site = MockWeb()
    install_seam(monkeypatch, site)
    return site


def _scrub_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# PriceQuote
# ---------------------------------------------------------------------------


def test_price_quote_derives_change_pct_from_last_and_prev_close() -> None:
    quote = price("AAPL", 218.555, prev_close=217.5)

    assert quote.change_pct == pytest.approx((218.555 / 217.5 - 1.0) * 100.0)
    assert price("AAPL", 100.0).change_pct is None, "no previous close, no change"


def test_price_quote_keeps_an_explicit_change_pct() -> None:
    quote = PriceQuote(
        symbol="AAPL",
        last=100.0,
        prev_close=90.0,
        change_pct=12.5,
        volume=None,
        as_of=T0,
        source="caller",
    )

    assert quote.change_pct == 12.5


def test_price_quote_is_frozen() -> None:
    quote = price("AAPL")

    with pytest.raises(dataclasses.FrozenInstanceError):
        quote.last = 1.0  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("last", 0.0),
        ("last", -1.0),
        ("prev_close", 0.0),
        ("volume", -1.0),
        ("change_pct", float("nan")),
    ],
)
def test_price_quote_rejects_a_field_that_is_not_a_price(
    field: str, value: float
) -> None:
    kwargs: dict[str, Any] = {
        "symbol": "AAPL",
        "last": 100.0,
        "prev_close": None,
        "change_pct": None,
        "volume": None,
        "as_of": T0,
        "source": "test",
    }
    kwargs[field] = value

    with pytest.raises(ValueError):
        PriceQuote(**kwargs)


def test_price_quote_rejects_a_naive_clock_and_empty_names() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        price("AAPL", at=datetime(2026, 10, 27, 18, 49, 51))
    with pytest.raises(ValueError, match="symbol"):
        PriceQuote(
            symbol="  ",
            last=100.0,
            prev_close=None,
            change_pct=None,
            volume=None,
            as_of=T0,
            source="test",
        )
    with pytest.raises(ValueError, match="source"):
        PriceQuote(
            symbol="AAPL",
            last=100.0,
            prev_close=None,
            change_pct=None,
            volume=None,
            as_of=T0,
            source=" ",
        )


# ---------------------------------------------------------------------------
# CnbcPriceSource: batch primary, normalization
# ---------------------------------------------------------------------------


def test_cnbc_price_source_wraps_cnbc_batch_source_by_default() -> None:
    source = CnbcPriceSource(batch_size=100, timeout_s=2.5)

    assert source.name == "cnbc"
    assert isinstance(source.batch_source, CnbcBatchSource)
    assert source.batch_source.batch_size == 100
    assert source.batch_source.timeout_s == 2.5


async def test_cnbc_price_source_normalizes_batch_answers() -> None:
    class FakeBatch:
        name = "cnbc"

        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        async def fetch_many(
            self, symbols: Sequence[str]
        ) -> dict[str, SourceQuote | None]:
            self.calls.append(tuple(symbols))
            return {
                "AAPL": SourceQuote(
                    last=218.555,
                    ts_exchange=T0,
                    prev_close=217.5,
                    volume=28_960_975.0,
                ),
                "MSFT": None,
            }

    batch = FakeBatch()
    source = CnbcPriceSource(batch_source=batch)

    answers = await source.fetch_many(["AAPL", "MSFT"])

    assert batch.calls == [("AAPL", "MSFT")], "the wrapper adds no chunking of its own"
    aapl = answers["AAPL"]
    assert isinstance(aapl, PriceQuote)
    assert aapl.symbol == "AAPL"
    assert aapl.last == 218.555
    assert aapl.prev_close == 217.5
    assert aapl.volume == 28_960_975.0
    assert aapl.as_of == T0
    assert aapl.source == "cnbc"
    assert aapl.change_pct == pytest.approx((218.555 / 217.5 - 1.0) * 100.0)
    assert answers["MSFT"] is None


async def test_cnbc_price_source_parses_a_live_shaped_body(web: MockWeb) -> None:
    cnbc_route(
        web,
        {
            "AAPL": cnbc_row("AAPL"),
            "MSFT": cnbc_row("MSFT", last="N/A"),
        },
    )

    answers = await CnbcPriceSource().fetch_many(["AAPL", "MSFT", "ZZZZ"])

    assert cnbc_symbols(web) == [["AAPL", "MSFT", "ZZZZ"]], "one batch request, pipe-joined"
    assert len(cnbc_requests(web)) == 1
    assert "Ztockz" in cnbc_requests(web)[0].headers["user-agent"]
    aapl = answers["AAPL"]
    assert aapl is not None
    assert aapl.last == 218.555
    assert aapl.prev_close == 217.5
    assert aapl.volume == 28_960_975.0
    assert aapl.as_of == T0
    assert aapl.change_pct == pytest.approx((218.555 / 217.5 - 1.0) * 100.0)
    assert aapl.source == "cnbc"
    assert answers["MSFT"] is None, "an N/A price is not a quote"
    assert answers["ZZZZ"] is None, "a symbol CNBC omits is missing, not zero"


async def test_cnbc_price_source_propagates_a_source_error() -> None:
    class FakeBatch:
        name = "cnbc"

        async def fetch_many(
            self, symbols: Sequence[str]
        ) -> dict[str, SourceQuote | None]:
            raise QuoteSourceError("cnbc answered 503", status=503)

    with pytest.raises(QuoteSourceError, match="503"):
        await CnbcPriceSource(batch_source=FakeBatch()).fetch_many(["AAPL"])


# ---------------------------------------------------------------------------
# NasdaqPriceSource: per-symbol fallback, per-symbol isolation
# ---------------------------------------------------------------------------


def test_nasdaq_price_source_wraps_nasdaq_quote_source_by_default() -> None:
    source = NasdaqPriceSource(timeout_s=3.0)

    assert source.name == "nasdaq"
    assert isinstance(source.quote_source, NasdaqQuoteSource)
    assert source.quote_source.timeout_s == 3.0


async def test_nasdaq_price_source_parses_one_body(web: MockWeb) -> None:
    # Nasdaq prints the timestamp as an ET sentence with minute precision.
    stamp = now_ts() // 60 * 60
    web.route(
        NASDAQ_HOST,
        lambda _request: httpx2.Response(
            200,
            json=nasdaq_body(
                "AAPL", price="$231.50", volume="28,960,975.244719", ts=stamp
            ),
        ),
    )

    quote = await NasdaqPriceSource().fetch("aapl")

    record = nasdaq_requests(web)[0]
    assert record.url.endswith("/api/quote/AAPL/info?assetclass=stocks")
    assert record.headers["origin"] == "https://www.nasdaq.com"
    assert quote is not None
    assert quote.symbol == "AAPL"
    assert quote.last == 231.5
    assert quote.volume == pytest.approx(28_960_975.244719)
    assert quote.as_of == datetime.fromtimestamp(stamp, UTC)
    assert quote.prev_close is None, "Nasdaq publishes no previous close here"
    assert quote.change_pct is None
    assert quote.source == "nasdaq"


async def test_nasdaq_price_source_429_leaves_the_symbol_missing_not_zero(
    web: MockWeb,
) -> None:
    web.route(
        NASDAQ_HOST,
        lambda _request: httpx2.Response(
            429, json={"data": None, "message": "rate limited"}
        ),
    )
    source = NasdaqPriceSource()

    answers = await source.fetch_many(["AAPL", "MSFT"])

    assert answers == {"AAPL": None, "MSFT": None}
    assert len(nasdaq_requests(web)) == 2, "a 429 on one symbol does not skip the next"
    assert "429" in source.last_errors["AAPL"]
    assert "429" in source.last_errors["MSFT"]


async def test_nasdaq_price_source_one_symbol_failing_does_not_sink_the_others(
    web: MockWeb,
) -> None:
    stamp = now_ts() // 60 * 60

    def responder(request: httpx2.Request) -> httpx2.Response:
        symbol = request.url.path.split("/api/quote/", 1)[-1].split("/", 1)[0].upper()
        if symbol == "MSFT":
            return httpx2.Response(503, json={"data": None, "message": "boom"})
        return httpx2.Response(200, json=nasdaq_body(symbol, price="$231.50", ts=stamp))

    web.route(NASDAQ_HOST, responder)
    source = NasdaqPriceSource()

    answers = await source.fetch_many(["AAPL", "MSFT"])

    assert answers["AAPL"] is not None and answers["AAPL"].last == 231.5
    assert answers["MSFT"] is None
    assert "503" in source.last_errors["MSFT"]
    assert "AAPL" not in source.last_errors


async def test_nasdaq_price_source_unknown_symbol_is_none(web: MockWeb) -> None:
    web.route(
        NASDAQ_HOST,
        lambda _request: httpx2.Response(200, json=nasdaq_body("ZZZZ", data=False)),
    )

    assert await NasdaqPriceSource().fetch("ZZZZ") is None


# ---------------------------------------------------------------------------
# PriceRegistry: primary first, fallback for the missing only
# ---------------------------------------------------------------------------


async def test_registry_asks_the_fallback_only_for_symbols_the_primary_missed() -> None:
    primary = FakeSource(
        "cnbc",
        {
            "AAPL": price("AAPL", 218.555, prev_close=217.5, source="cnbc"),
            "MSFT": None,
        },
    )
    fallback = FakeSource(
        "nasdaq",
        {
            "MSFT": price("MSFT", 412.30, source="nasdaq"),
            "ZZZZ": price("ZZZZ", 51.25, source="nasdaq"),
        },
    )
    registry = PriceRegistry(primary=primary, fallback=fallback)

    result = await registry.quotes(["AAPL", "MSFT", "ZZZZ"])

    assert primary.calls == [("AAPL", "MSFT", "ZZZZ")]
    assert fallback.calls == [("MSFT", "ZZZZ")], "only the symbols the primary missed"
    assert result["AAPL"] is not None and result["AAPL"].source == "cnbc"
    assert result["MSFT"] is not None and result["MSFT"].source == "nasdaq"
    assert result["ZZZZ"] is not None and result["ZZZZ"].last == 51.25
    assert registry.last_errors == {}


async def test_registry_never_calls_the_fallback_when_the_primary_answers_all() -> None:
    primary = FakeSource("cnbc", {symbol: price(symbol) for symbol in ("AAPL", "MSFT")})
    fallback = FakeSource("nasdaq")
    registry = PriceRegistry(primary=primary, fallback=fallback)

    result = await registry.quotes(["AAPL", "MSFT"])

    assert fallback.calls == []
    assert set(result) == {"AAPL", "MSFT"}
    assert all(quote is not None for quote in result.values())


async def test_registry_primary_failure_sends_every_symbol_to_the_fallback() -> None:
    primary = FakeSource("cnbc", raises=RuntimeError("cnbc exploded"))
    fallback = FakeSource(
        "nasdaq",
        {symbol: price(symbol, 10.0 + index) for index, symbol in enumerate(("AAPL", "MSFT"))},
    )
    registry = PriceRegistry(primary=primary, fallback=fallback)

    result = await registry.quotes(["AAPL", "MSFT"])

    assert fallback.calls == [("AAPL", "MSFT")]
    assert all(quote is not None for quote in result.values())
    assert "cnbc exploded" in registry.last_errors["cnbc"]


async def test_registry_fallback_error_leaves_the_symbol_missing_not_zero() -> None:
    primary = FakeSource("cnbc", {"AAPL": None, "MSFT": None})
    fallback = FakeSource(
        "nasdaq", raises=QuoteSourceError("nasdaq answered 429", status=429)
    )
    registry = PriceRegistry(primary=primary, fallback=fallback)

    result = await registry.quotes(["AAPL", "MSFT"])

    assert result == {"AAPL": None, "MSFT": None}
    assert "429" in registry.last_errors["nasdaq"]


async def test_registry_primary_timeout_does_not_kill_the_batch() -> None:
    primary = FakeSource("cnbc", {"AAPL": price("AAPL")}, delay_s=5.0)
    fallback = FakeSource(
        "nasdaq",
        {symbol: price(symbol, 99.0, source="nasdaq") for symbol in ("AAPL", "MSFT")},
    )
    registry = PriceRegistry(
        primary=primary, fallback=fallback, primary_timeout_s=0.05, fallback_timeout_s=5.0
    )

    result = await registry.quotes(["AAPL", "MSFT"])

    assert fallback.calls == [("AAPL", "MSFT")], "a dead primary hands everything over"
    assert all(
        quote is not None and quote.source == "nasdaq" for quote in result.values()
    )
    assert "timeout" in registry.last_errors["cnbc"]


async def test_registry_fallback_timeout_leaves_those_symbols_missing() -> None:
    primary = FakeSource(
        "cnbc",
        {"AAPL": price("AAPL", 218.555, source="cnbc"), "MSFT": None},
    )
    fallback = FakeSource(
        "nasdaq", {"MSFT": price("MSFT", 51.25, source="nasdaq")}, delay_s=5.0
    )
    registry = PriceRegistry(
        primary=primary, fallback=fallback, primary_timeout_s=5.0, fallback_timeout_s=0.05
    )

    result = await registry.quotes(["AAPL", "MSFT"])

    assert result["AAPL"] is not None and result["AAPL"].source == "cnbc", (
        "the primary's answer survives the fallback's timeout"
    )
    assert result["MSFT"] is None, "a timed-out fallback leaves the symbol missing"
    assert "timeout" in registry.last_errors["nasdaq"]


async def test_registry_normalizes_dedupes_and_uppercases_symbols() -> None:
    primary = FakeSource("cnbc", {symbol: price(symbol) for symbol in ("AAPL", "MSFT")})
    fallback = FakeSource("nasdaq")
    registry = PriceRegistry(primary=primary, fallback=fallback)

    result = await registry.quotes(["aapl", "AAPL", " msft "])

    assert list(result) == ["AAPL", "MSFT"]
    assert primary.calls == [("AAPL", "MSFT")]
    assert fallback.calls == []


async def test_registry_of_an_empty_list_asks_nobody() -> None:
    primary = FakeSource("cnbc")
    fallback = FakeSource("nasdaq")
    registry = PriceRegistry(primary=primary, fallback=fallback)

    assert await registry.quotes([]) == {}
    assert primary.calls == [] and fallback.calls == []


def test_registry_rejects_a_source_without_fetch_many() -> None:
    class Dumb:
        name = "dumb"

    with pytest.raises(TypeError, match="fetch_many"):
        PriceRegistry(primary=Dumb(), fallback=FakeSource("nasdaq"))
    with pytest.raises(ValueError, match="name"):
        PriceRegistry(primary=FakeSource(""), fallback=FakeSource("nasdaq"))


def test_registry_rejects_a_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="primary_timeout_s"):
        PriceRegistry(
            primary=FakeSource("cnbc"), fallback=FakeSource("nasdaq"), primary_timeout_s=0
        )
    with pytest.raises(ValueError, match="fallback_timeout_s"):
        PriceRegistry(
            primary=FakeSource("cnbc"),
            fallback=FakeSource("nasdaq"),
            fallback_timeout_s=-1.0,
        )


async def test_registry_rejects_a_token_that_is_not_a_symbol() -> None:
    registry = PriceRegistry(primary=FakeSource("cnbc"), fallback=FakeSource("nasdaq"))

    with pytest.raises(ValueError, match="invalid symbol"):
        await registry.quotes(["AAPL", "$BAD"])


# ---------------------------------------------------------------------------
# Keyless
# ---------------------------------------------------------------------------


def test_no_api_key_is_read_anywhere() -> None:
    source = (ROOT / "app" / "market" / "price.py").read_text(encoding="utf-8")
    lowered = source.lower()

    for token in KEY_TOKENS:
        assert token not in lowered, (
            f"app/market/price.py must stay keyless, found {token!r}"
        )


def test_price_module_publishes_its_public_api() -> None:
    published = set(getattr(price_module, "__all__", ()))

    assert {
        "PriceQuote",
        "CnbcPriceSource",
        "NasdaqPriceSource",
        "PriceRegistry",
    } <= published


async def test_registry_answers_with_every_key_shaped_variable_scrubbed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _scrub_keys(monkeypatch)
    primary = FakeSource(
        "cnbc", {"AAPL": price("AAPL", 218.555, prev_close=217.5, source="cnbc")}
    )
    fallback = FakeSource("nasdaq", {"MSFT": price("MSFT", 51.25, source="nasdaq")})
    registry = PriceRegistry(primary=primary, fallback=fallback)

    result = await registry.quotes(["AAPL", "MSFT"])

    assert result["AAPL"] is not None and result["AAPL"].source == "cnbc"
    assert result["MSFT"] is not None and result["MSFT"].source == "nasdaq"


# ---------------------------------------------------------------------------
# End to end through the one HTTP seam
# ---------------------------------------------------------------------------


async def test_registry_end_to_end_cnbc_then_nasdaq_for_the_missing_one(
    web: MockWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scrub_keys(monkeypatch)
    stamp = now_ts() // 60 * 60
    cnbc_route(
        web,
        {
            "AAPL": cnbc_row("AAPL"),
            "MSFT": cnbc_row(
                "MSFT", last="412.30", prev_close="410.00", volume="12,345,678"
            ),
        },
    )
    web.route(
        NASDAQ_HOST,
        lambda _request: httpx2.Response(
            200,
            json=nasdaq_body(
                "ZZZZ", price="$51.25", volume="1,000,000", ts=stamp, realtime=False
            ),
        ),
    )

    registry = PriceRegistry(primary=CnbcPriceSource(), fallback=NasdaqPriceSource())
    result = await registry.quotes(["AAPL", "MSFT", "ZZZZ"])

    assert [quote.source for quote in result.values()] == ["cnbc", "cnbc", "nasdaq"]
    assert result["AAPL"] is not None and result["AAPL"].last == 218.555
    assert result["AAPL"].change_pct == pytest.approx((218.555 / 217.5 - 1.0) * 100.0)
    assert result["MSFT"] is not None and result["MSFT"].last == 412.3
    assert result["MSFT"].volume == 12_345_678.0
    assert result["ZZZZ"] is not None and result["ZZZZ"].last == 51.25
    assert result["ZZZZ"].prev_close is None
    assert result["ZZZZ"].as_of == datetime.fromtimestamp(stamp, UTC)
    assert len(cnbc_requests(web)) == 1, "three symbols are one CNBC batch request"
    asked = nasdaq_requests(web)
    assert len(asked) == 1 and "/api/quote/ZZZZ/" in asked[0].url, (
        "Nasdaq is asked only for the symbol CNBC missed"
    )
