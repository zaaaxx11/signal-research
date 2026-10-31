"""P10.1 RED — Finviz and Google Finance as default verifiers.

The P10 contract (``tests/test_screen_multi_source.py``) says a display row is
one stock with one median per cell and no per-source columns. This module pins
the *four-verifier* extension of it:

* **The default set is Yahoo + Nasdaq + Finviz + Google.** The two HTML legs are
  built by :func:`build_multi_source_verifiers`, still replaceable by injection,
  and can be switched off without shrinking :data:`VERIFICATION_SOURCES`.
* **Confidence is measured against the five documented sources**, not against
  the number of verifiers that happen to be configured: four of five in perfect
  agreement is 0.8, three of five is 0.6. A deployment cannot buy a 1.0 by
  turning a leg off, and a source that answers nothing is simply not counted.
* **The HTML legs are bounded and honest.** At most three requests in flight,
  one timeout per symbol, a non-200 or a bot wall is a *failure* (not a null
  quote), a missing cell stays ``None`` (never ``0``), and US class shares are
  spelled the way each provider spells them (``BRK-B`` / ``BRK.B:NYSE``).
* **The parsers read the page, not the session next to it.** Finviz prints the
  regular session before the after-hours block and Google reuses its change
  classes in the after-hours block, so both fixtures pin the regular value and
  the tests assert the after-hours number was *not* taken.

The fixtures are trimmed but verbatim samples of the live pages (see the comment
inside each); the HTTP tests drive them through ``MockWeb``, the one seam.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.market.sources import InvalidSymbol, QuoteSourceError
from app.screen.multi_source import (
    MAX_WEB_CONCURRENCY,
    MULTI_SOURCE,
    VERIFICATION_SOURCES,
    FinvizVerifier,
    GoogleVerifier,
    MultiSourceScreen,
    Observation,
    build_multi_source_verifiers,
    finviz_symbol,
    google_symbol,
    yahoo_symbol,
    parse_finviz_quote,
    parse_google_quote,
    source_summary,
)
from tests._phase2 import MockWeb, install_web
from tests.test_screen_multi_source import (
    Clock,
    FailingVerifier,
    price_verifier,
    seeded_repository,
)

FIXTURES = Path(__file__).parent / "fixtures"
PER_SOURCE_KEYS = frozenset({"sources", "tradingview", "yahoo", "nasdaq", "finviz", "google"})
SUMMARY_KEYS = frozenset(
    {"method", "source_count", "agreement", "spread_pp", "confidence", "verified_at"}
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def rows_for(symbols: Sequence[str], *, exchange: str = "XNAS") -> list[dict[str, Any]]:
    """Screen rows with the given symbols, enough shape for a verifier."""

    return [
        {"rank": index + 1, "symbol": symbol, "exchange": exchange}
        for index, symbol in enumerate(symbols)
    ]


# ---------------------------------------------------------------------------
# The default set
# ---------------------------------------------------------------------------


def test_the_default_verifier_set_is_yahoo_nasdaq_finviz_google(monkeypatch: Any) -> None:
    monkeypatch.delenv("MEE_SCREEN_MULTI_SOURCE", raising=False)
    monkeypatch.delenv("MEE_SCREEN_HTML_VERIFY", raising=False)

    verifiers = build_multi_source_verifiers(Settings())

    assert [verifier.name for verifier in verifiers] == [
        "yahoo",
        "nasdaq",
        "finviz",
        "google",
    ], f"got {[v.name for v in verifiers]}"
    assert isinstance(verifiers[2], FinvizVerifier)
    assert isinstance(verifiers[3], GoogleVerifier)
    assert verifiers[2].concurrency == MAX_WEB_CONCURRENCY == 3, (
        "the HTML legs run three pages at a time, never more"
    )
    assert verifiers[3].concurrency == 3
    assert verifiers[2].timeout_s == Settings().screen_verify_timeout_s


def test_the_documented_sources_are_the_five_way_set() -> None:
    assert VERIFICATION_SOURCES == (
        "tradingview",
        "yahoo",
        "nasdaq",
        "finviz",
        "google",
    ), (
        "confidence is measured against the sources the product claims to "
        f"consult; got {VERIFICATION_SOURCES}"
    )


def test_the_html_legs_can_be_switched_off_without_shrinking_the_set(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("MEE_SCREEN_MULTI_SOURCE", raising=False)
    monkeypatch.setenv("MEE_SCREEN_HTML_VERIFY", "false")

    verifiers = build_multi_source_verifiers(Settings())

    assert [verifier.name for verifier in verifiers] == ["yahoo", "nasdaq"]
    assert len(VERIFICATION_SOURCES) == 5, (
        "turning a leg off must not inflate confidence: the denominator stays "
        "the five documented sources"
    )


def test_a_concurrency_above_three_is_refused() -> None:
    for bad in (0, 4):
        with pytest.raises(ValidationError):
            Settings(screen_html_verify_concurrency=bad)
    assert MAX_WEB_CONCURRENCY == 3
    for concurrency in (0, 4):
        with pytest.raises(ValueError, match="concurrency must be"):
            FinvizVerifier(concurrency=concurrency)
        with pytest.raises(ValueError, match="concurrency must be"):
            GoogleVerifier(concurrency=concurrency)
    with pytest.raises(ValueError, match="timeout_s must be"):
        GoogleVerifier(timeout_s=0)


# ---------------------------------------------------------------------------
# Four-source consensus
# ---------------------------------------------------------------------------


async def test_four_sources_agree_and_the_row_counts_four(tmp_path: Path) -> None:
    repository, _screen = await seeded_repository(tmp_path)
    try:
        service = MultiSourceScreen(
            repository=repository,
            verifiers=[
                price_verifier("yahoo", 2.0),
                # A wild print: 100/102/104/130 has median 103, but a mean of
                # 109 would move the displayed price.
                price_verifier("finviz", 30.0),
                price_verifier("google", 4.0),
            ],
            clock=Clock(),
        )
        document = await service.load("us", "market_cap")
    finally:
        await repository.aclose()

    row = document["rows"][0]
    assert document["source"] == MULTI_SOURCE and document["degraded"] is False
    assert row["source_summary"]["source_count"] == 4, (
        "TradingView + Yahoo + Finviz + Google answered, even though Nasdaq is "
        f"absent; got {row['source_summary']['source_count']}"
    )
    assert row["price"] == pytest.approx(103.0), (
        "the median of 100/102/104/130 averages the two middle values; one wild "
        "provider prints 130 and must not move the display (a mean would be 109)"
    )
    assert row["source_summary"]["agreement"] == pytest.approx(1.0)
    assert row["source_summary"]["confidence"] == pytest.approx(4 / 5), (
        "four of the five documented sources answered in perfect agreement"
    )
    assert set(row["source_summary"]) == SUMMARY_KEYS
    assert not PER_SOURCE_KEYS & set(row), (
        f"no per-source columns, got {sorted(PER_SOURCE_KEYS & set(row))}"
    )
    nested = {
        key
        for key, value in row.items()
        if isinstance(value, Mapping) and key != "source_summary"
    }
    assert nested == set()


async def test_a_dead_nasdaq_leaves_the_four_that_answered(tmp_path: Path) -> None:
    repository, _screen = await seeded_repository(tmp_path)
    try:
        service = MultiSourceScreen(
            repository=repository,
            verifiers=[
                price_verifier("yahoo", 2.0),
                FailingVerifier("nasdaq", "nasdaq timeout"),
                price_verifier("finviz", 6.0),
                price_verifier("google", 4.0),
            ],
            clock=Clock(),
        )
        document = await service.load("us", "market_cap")
    finally:
        await repository.aclose()

    row = document["rows"][0]
    assert document["source"] == MULTI_SOURCE
    assert document["verification_error"] is None, (
        "a partial outage is not an error state: the consensus still exists"
    )
    assert row["source_summary"]["source_count"] == 4, (
        "the dead verifier is not counted, and it does not erase the four that "
        f"answered; got {row['source_summary']['source_count']}"
    )
    assert row["price"] == pytest.approx(103.0)


async def test_one_broken_html_leg_keeps_the_others(tmp_path: Path) -> None:
    repository, _screen = await seeded_repository(tmp_path)
    try:
        service = MultiSourceScreen(
            repository=repository,
            verifiers=[
                price_verifier("yahoo", 2.0),
                price_verifier("finviz", 6.0),
                FailingVerifier("google", "google answered a bot challenge"),
            ],
            clock=Clock(),
        )
        document = await service.load("us", "market_cap")
    finally:
        await repository.aclose()

    row = document["rows"][0]
    assert document["source"] == MULTI_SOURCE
    assert row["source_summary"]["source_count"] == 3
    assert row["price"] == pytest.approx(102.0)
    assert row["source_summary"]["confidence"] == pytest.approx(3 / 5)


def test_source_summary_divides_by_the_documented_five() -> None:
    def legs(count: int) -> list[Observation]:
        return [
            Observation(source=name, price=100.0, change_pct=1.0)
            for name in VERIFICATION_SOURCES[:count]
        ]

    assert source_summary(legs(5), verified_at="2026-10-24T00:00:00+00:00")[
        "confidence"
    ] == pytest.approx(1.0)
    assert source_summary(legs(4), verified_at="2026-10-24T00:00:00+00:00")[
        "confidence"
    ] == pytest.approx(4 / 5)
    assert source_summary(legs(2), verified_at="2026-10-24T00:00:00+00:00")[
        "confidence"
    ] == pytest.approx(2 / 5)
    empty = source_summary([], verified_at="2026-10-24T00:00:00+00:00")
    assert empty["source_count"] == 0 and empty["confidence"] is None


# ---------------------------------------------------------------------------
# Parsers: the fixtures
# ---------------------------------------------------------------------------


def test_parse_finviz_quote_reads_the_regular_session_not_the_after_hours() -> None:
    observation = parse_finviz_quote(fixture("finviz_quote_sample.html"))

    assert observation is not None
    assert observation.source == "finviz"
    assert observation.price == pytest.approx(347.73), (
        "the header's first block is the regular session; 347.64 is the "
        "after-hours print that reuses the same markup"
    )
    assert observation.change_pct == pytest.approx(-0.14)
    assert observation.volume == pytest.approx(39_539_912), (
        "the snapshot table carries the exact share volume"
    )
    assert observation.market_cap_usd == pytest.approx(5_074.76e9)


def test_parse_finviz_quote_falls_back_to_the_snapshot_table() -> None:
    body = """
    <html><body>
      <table class="snapshot-table2">
        <tr><td><div class="snapshot-td-label">Market Cap</div></td>
            <td><div class="snapshot-td-content"><b>3.05T</b></div></td></tr>
        <tr><td><div class="snapshot-td-label">Prev Close</div></td>
            <td><div class="snapshot-td-content"><b>100.00</b></div></td></tr>
        <tr><td><div class="snapshot-td-label">Price</div></td>
            <td><div class="snapshot-td-content"><b>102.00</b></div></td></tr>
        <tr><td><div class="snapshot-td-label">Change %</div></td>
            <td><div class="snapshot-td-content"><b><span>2.00%</span></b></div></td></tr>
      </table>
    </body></html>
    """

    observation = parse_finviz_quote(body)

    assert observation is not None
    assert observation.price == pytest.approx(102.0)
    assert observation.change_pct == pytest.approx(2.0)
    assert observation.market_cap_usd == pytest.approx(3.05e12)
    assert observation.volume is None, "an absent volume is null, not zero"


def test_parse_finviz_quote_returns_none_when_no_price_is_published() -> None:
    assert parse_finviz_quote("") is None
    assert (
        parse_finviz_quote(
            "<html><body><p>Please verify you are a human</p></body></html>"
        )
        is None
    ), "a bot wall is 'no data' at the parser, and a named failure at the verifier"


def test_parse_google_quote_reads_the_first_change_after_the_price() -> None:
    observation = parse_google_quote(fixture("google_quote_sample.html"))

    assert observation is not None
    assert observation.source == "google"
    assert observation.price == pytest.approx(347.72)
    assert observation.change_pct == pytest.approx(-0.14), (
        "the after-hours block reuses the DAicsd class and prints -0.03%; the "
        "regular session's change is the first one after the price"
    )
    assert observation.market_cap_usd == pytest.approx(5.07e12)
    assert observation.volume is None, "Google abbreviates volume, so it is not sold"


def test_parse_google_quote_reads_an_up_move() -> None:
    body = """
    <html><body><div class="LhDNu"><div class="zhtAvb"><div class="ujg0He">
      <div class="N6SYTe"><span><span>$524.51</span></span></div>
      <div class="DAicsd">
        <div><i class="ymyBi">arrow_upward</i></div>
        <span><span class="ougHge">+0.66%</span></span>
        <div><span class="ougHge"><span>( +3.43 ) Today</span></span></div>
      </div>
    </div></div></div></body></html>
    """

    observation = parse_google_quote(body)

    assert observation is not None
    assert observation.price == pytest.approx(524.51)
    assert observation.change_pct == pytest.approx(0.66)
    assert observation.market_cap_usd is None, "no stats grid means no cap, not 0"


def test_parse_google_quote_returns_none_without_a_price() -> None:
    assert parse_google_quote("") is None
    assert (
        parse_google_quote(
            "<html><body><div class='DAicsd'><span>-1.00%</span></div></body></html>"
        )
        is None
    ), "a change with no price is not a quote"


# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------


def test_yahoo_symbol_normalizes_us_class_shares_but_not_foreign_suffixes() -> None:
    assert yahoo_symbol({"symbol": "BRK.B", "exchange": "XNYS"}) == "BRK-B"
    assert yahoo_symbol({"symbol": "2330", "exchange": "TWSE"}) == "2330.TW"


def test_finviz_symbols_are_us_only_with_class_shares_hyphenated() -> None:
    assert finviz_symbol({"symbol": "aapl", "exchange": "XNAS"}) == "AAPL"
    assert finviz_symbol({"symbol": "BRK.B", "exchange": "XNYS"}) == "BRK-B", (
        "Finviz spells class shares with a hyphen where TradingView uses a dot"
    )
    assert finviz_symbol({"symbol": "7203", "exchange": "TSE"}) is None, (
        "a foreign venue has no bare Finviz ticker we can trust"
    )
    assert finviz_symbol({"symbol": "MC", "exchange": "EURONEXT"}) is None
    assert finviz_symbol({"symbol": "BAD TICKER", "exchange": "XNAS"}) is None
    assert finviz_symbol({"symbol": "^VIX", "exchange": "XNAS"}) is None, (
        "an index is a provider symbol, not an equity quote page"
    )
    assert finviz_symbol({"symbol": "", "exchange": "XNAS"}) is None


def test_google_symbols_carry_the_us_venue_suffix() -> None:
    assert google_symbol({"symbol": "aapl", "exchange": "XNAS"}) == "AAPL:NASDAQ"
    assert google_symbol({"symbol": "XOM", "exchange": "XNYS"}) == "XOM:NYSE"
    assert google_symbol({"symbol": "IBKR", "exchange": "XASE"}) == "IBKR:AMEX"
    assert google_symbol({"symbol": "BRK.B", "exchange": "XNYS"}) == "BRK.B:NYSE", (
        "Google keeps the dot: the venue is the suffix, the class is the symbol"
    )
    assert google_symbol({"symbol": "AAPL", "exchange": "NASDAQ"}) == "AAPL:NASDAQ", (
        "the scanner's TradingView code must map too"
    )
    assert google_symbol({"symbol": "SPY", "exchange": "CBOE"}) is None, (
        "CBOE has no known Google spelling, so the leg is not asked at all"
    )
    assert google_symbol({"symbol": "7203", "exchange": "TSE"}) is None


# ---------------------------------------------------------------------------
# Concurrency, timeout and outage
# ---------------------------------------------------------------------------


async def test_the_html_legs_keep_at_most_three_requests_in_flight() -> None:
    inflight = 0
    peak = 0
    calls: list[str] = []

    async def fetch(ticker: str) -> Observation | None:
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        calls.append(ticker)
        await asyncio.sleep(0.01)
        inflight -= 1
        return Observation(source="finviz", price=10.0)

    verifier = FinvizVerifier(fetcher=fetch)
    answers = await verifier.observe(rows_for([f"S{index:02d}" for index in range(12)]))

    assert len(calls) == len(answers) == 12
    assert peak <= MAX_WEB_CONCURRENCY, (
        f"concurrency is capped at {MAX_WEB_CONCURRENCY}, peaked at {peak}"
    )
    assert peak == MAX_WEB_CONCURRENCY, "three in flight is the point of the cap"
    assert all(observation is not None for observation in answers.values())


async def test_a_hanging_symbol_times_out_and_the_rest_still_answer() -> None:
    async def fetch(ticker: str) -> Observation | None:
        if ticker.startswith("S00"):
            await asyncio.sleep(30)
        return Observation(source="google", price=10.0)

    verifier = GoogleVerifier(timeout_s=0.05, fetcher=fetch)
    started = time.perf_counter()
    answers = await verifier.observe(rows_for(["S00", "S01", "S02"]))
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0, f"one hanging page must not hold the pass, took {elapsed:.1f}s"
    assert answers["S00"] is None, "a timed-out symbol is missing data, not a zero"
    assert answers["S01"] is not None and answers["S02"] is not None


async def test_rows_without_a_provider_ticker_are_not_asked_at_all() -> None:
    calls: list[str] = []

    async def fetch(ticker: str) -> Observation | None:
        calls.append(ticker)
        return Observation(source="finviz", price=10.0)

    rows = [
        {"rank": 1, "symbol": "7203", "exchange": "TSE"},
        {"rank": 2, "symbol": "SPY", "exchange": "CBOE"},
        {"rank": 3, "symbol": "AAPL", "exchange": "XNAS"},
    ]
    finviz_answers = await FinvizVerifier(fetcher=fetch).observe(rows)

    assert calls == ["SPY", "AAPL"], (
        "a foreign venue costs no request; a CBOE listing is fine for Finviz "
        f"(no suffix is needed) but not for Google; got {calls}"
    )
    assert set(finviz_answers) == {"SPY", "AAPL"}

    calls.clear()
    google_answers = await GoogleVerifier(fetcher=fetch).observe(rows)
    assert calls == ["AAPL:NASDAQ"], (
        "CBOE has no Google spelling and TSE is not a US venue, so neither is "
        f"asked; got {calls}"
    )
    assert set(google_answers) == {"AAPL"}


async def test_every_symbol_failing_is_an_outage_not_an_empty_answer() -> None:
    async def fetch(ticker: str) -> Observation | None:
        raise QuoteSourceError(f"finviz answered 503 for {ticker}", status=503)

    with pytest.raises(QuoteSourceError, match="finviz failed for 3 symbol"):
        await FinvizVerifier(fetcher=fetch).observe(rows_for(["A", "B", "C"]))


async def test_one_unknown_ticker_is_not_an_outage() -> None:
    async def fetch(ticker: str) -> Observation | None:
        if ticker.startswith("BAD"):
            raise InvalidSymbol(ticker)
        return Observation(source="finviz", price=10.0)

    answers = await FinvizVerifier(fetcher=fetch).observe(rows_for(["BAD", "GOOD"]))

    assert answers["BAD"] is None
    assert answers["GOOD"] is not None


# ---------------------------------------------------------------------------
# The one HTTP seam: real fetch(), fixture pages
# ---------------------------------------------------------------------------


def _web() -> MockWeb:
    web = MockWeb()
    finviz = fixture("finviz_quote_sample.html")
    google = fixture("google_quote_sample.html")

    def finviz_page(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, html=finviz)

    def google_page(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, html=google)

    web.route("finviz", finviz_page)
    web.route("google", google_page)
    return web


async def test_the_html_legs_read_their_pages_through_the_one_http_seam(
    monkeypatch: Any,
) -> None:
    web = _web()
    install_web(monkeypatch, web)

    finviz = FinvizVerifier()
    google = GoogleVerifier()
    rows = [
        {"rank": 1, "symbol": "AAPL", "exchange": "XNAS"},
        {"rank": 2, "symbol": "BRK.B", "exchange": "XNYS"},
    ]
    finviz_answers = await finviz.observe(rows)
    google_answers = await google.observe(rows)

    assert finviz.url("BRK-B") == "https://finviz.com/stock?t=BRK-B", (
        "the quote page, not /quote.ashx: the legacy path 301s and the client "
        "does not follow redirects"
    )
    assert google.url("BRK.B:NYSE") == (
        "https://www.google.com/finance/quote/BRK.B:NYSE"
    )
    assert finviz_answers["AAPL"] is not None
    assert finviz_answers["AAPL"].price == pytest.approx(347.73)
    assert google_answers["BRK.B"] is not None, (
        "the class-share row is addressed as BRK.B:NYSE and answered under the "
        "row's own symbol"
    )
    assert web.unrouted == [], f"unexpected hosts: {web.unrouted}"
    urls = [served.url for served in web.requests]
    assert len(urls) == 4, f"two legs x two rows, got {urls}"
    assert any(url.endswith("/stock?t=AAPL") for url in urls)
    assert any(url.endswith("/stock?t=BRK-B") for url in urls), (
        "the class-share row is asked with Finviz's hyphen spelling"
    )
    assert any(url.endswith("/finance/quote/AAPL:NASDAQ") for url in urls)
    assert any(url.endswith("/finance/quote/BRK.B:NYSE") for url in urls), (
        "the class-share row is asked with Google's dotted ticker + venue"
    )


async def test_a_bot_challenge_page_is_a_named_failure(monkeypatch: Any) -> None:
    web = MockWeb()
    web.html("finviz")
    install_web(monkeypatch, web)

    with pytest.raises(QuoteSourceError, match="bot challenge"):
        await FinvizVerifier().observe(rows_for(["AAPL"]))


# ---------------------------------------------------------------------------
# Cache: the fourth leg is not re-asked inside the window
# ---------------------------------------------------------------------------


async def test_the_four_source_document_costs_zero_verifier_calls_on_a_second_read(
    tmp_path: Path,
) -> None:
    clock = Clock()
    repository, _screen = await seeded_repository(tmp_path, now=clock())
    yahoo = price_verifier("yahoo", 2.0)
    nasdaq = price_verifier("nasdaq", 4.0)
    finviz = price_verifier("finviz", 6.0)
    google = price_verifier("google", 4.0)
    try:
        service = MultiSourceScreen(
            repository=repository,
            verifiers=[yahoo, nasdaq, finviz, google],
            clock=clock,
        )
        first = await service.load("us", "market_cap")
        second = await service.load("us", "market_cap")
    finally:
        await repository.aclose()

    assert [v.calls for v in (yahoo, nasdaq, finviz, google)] == [1, 1, 1, 1], (
        "the first read asks each of the four once, got "
        f"{[v.calls for v in (yahoo, nasdaq, finviz, google)]}"
    )
    assert second["rows"] == first["rows"]
    assert second["rows"][0]["source_summary"]["source_count"] == 5, (
        "TradingView plus all four verifiers"
    )
    assert second["rows"][0]["source_summary"]["confidence"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# A CBOE listing is a US listing (research D8)
# ---------------------------------------------------------------------------


def test_a_cboe_listing_is_asked_by_every_leg_that_can_spell_it() -> None:
    """The venue sets used to hold ``CBOE`` and not ``XCBO`` — the MIC a row carries.

    A snapshot row stores the MIC (``mic_for``), so every leg filtered all 1,678
    CBOE rows out before any request: no error, no log line, no verification.
    """

    for venue in ("XCBO", "CBOE"):
        row = {"symbol": "SPY", "exchange": venue}
        assert yahoo_symbol(row) == "SPY", venue
        assert finviz_symbol(row) == "SPY", venue

    assert google_symbol({"symbol": "SPY", "exchange": "XCBO"}) is None, (
        "Google has no CBOE spelling; not asking beats asking with an invented one"
    )


async def test_the_quote_leg_accepts_a_cboe_mic() -> None:
    from app.screen.multi_source import NasdaqVerifier

    calls: list[str] = []

    class Source:
        async def fetch(self, symbol: str) -> None:
            calls.append(symbol)
            return None

    await NasdaqVerifier(source=Source()).observe(
        [{"symbol": "SPY", "exchange": "XCBO"}]
    )
    assert calls == ["SPY"], "a CBOE row must reach the quote leg like any other"
