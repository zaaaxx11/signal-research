"""P12 RED — the news pipeline: merge, dedupe, order, cache, failures, no network.

The service is the product here, so the suite drives it directly (no HTTP): the
providers are injected fakes that count their calls, and the two real providers
are exercised against fixtures through the one HTTP seam
(``tests._phase2.MockWeb``). What is pinned:

* one consolidated document per symbol — ``{symbol, as_of, count,
  latest_published_at, items, provider_errors}`` — and **no per-source columns**;
* every item carries its own provenance (``headline``, ``url``, ``source``,
  ``published_at``, ``source_url``) and an undated headline keeps ``None``;
* duplicates collapse by URL **and** by headline, across wires, keeping the
  newest timestamp and filling a missing publisher;
* newest first, undated last, deterministic ties;
* eight headlines by default, a bounded pool, and a ``limit`` that slices it;
* two providers are asked **concurrently**; a hanging one times out into an
  error cell;
* a document lives fifteen minutes: a second read — at any limit — sends zero
  provider requests;
* one broken wire is a ``provider_errors`` cell, two broken wires raise
  :class:`AllNewsProvidersFailed` (the route's 503) and are **not** cached;
* no news is a document with ``count=0``, not an error;
* ``app.news.rss``'s parser is reused, not re-implemented.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

import pytest

from app.config import Settings
from app.news import rss
from app.news.service import (
    CACHE_TTL_S,
    DEFAULT_ITEMS,
    MAX_POOL_ITEMS,
    AllNewsProvidersFailed,
    CallableNewsProvider,
    GdeltError,
    GdeltNewsProvider,
    GoogleNewsProvider,
    InvalidNewsSymbol,
    NewsItem,
    NewsProviderError,
    NewsService,
    build_news_providers,
    build_news_service,
    gdelt_url,
    merge_items,
    normalize_symbol,
    order_items,
    parse_gdelt,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOOGLE_FEED = FIXTURES / "google_news_sample.xml"
GDELT_PAYLOAD = FIXTURES / "gdelt_news_sample.json"

DOCUMENT_KEYS = {
    "symbol",
    "as_of",
    "count",
    "latest_published_at",
    "items",
    "provider_errors",
}
ITEM_KEYS = {"headline", "url", "source", "published_at", "source_url"}


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


def item(
    headline: str,
    url: str,
    *,
    source: str = "Newswire",
    published: datetime | None = datetime(2026, 10, 23, 12, 0, tzinfo=UTC),
    source_url: str | None = None,
) -> NewsItem:
    """One headline in the parser's shape."""

    return NewsItem(
        headline=headline,
        url=url,
        source=source,
        published_at=published,
        source_url=source_url,
    )


class FakeProvider:
    """A wire that records every question it is asked."""

    def __init__(
        self,
        name: str,
        items: Sequence[NewsItem] = (),
        *,
        error: BaseException | None = None,
        delay: float = 0.0,
        answer: object | None = None,
        barrier: "Barrier | None" = None,
    ) -> None:
        self.name = name
        self._items = list(items)
        self._error = error
        self._delay = delay
        self._answer = answer
        self._barrier = barrier
        self.calls: list[str] = []

    async def fetch(self, symbol: str) -> Any:
        self.calls.append(symbol)
        if self._barrier is not None:
            await self._barrier.wait()
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        if self._answer is not None:
            return self._answer
        return list(self._items)


class Barrier:
    """Every provider must arrive before any may leave — proof of concurrency."""

    def __init__(self, parties: int) -> None:
        self._parties = parties
        self._arrived = 0
        self._open = asyncio.Event()

    async def wait(self) -> None:
        self._arrived += 1
        if self._arrived >= self._parties:
            self._open.set()
        await asyncio.wait_for(self._open.wait(), timeout=1.0)


class Clock:
    """A hand-wound UTC clock, so a fifteen-minute window is not a sleep."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: Any) -> None:
        self.now += timedelta(**kwargs)


START = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# The document: one row per symbol, no per-source columns
# ---------------------------------------------------------------------------


async def test_the_document_is_one_consolidated_row_per_symbol() -> None:
    google = FakeProvider("google_news", [item("Alpha rises", "https://e.test/1")])
    gdelt = FakeProvider(
        "gdelt",
        [item("Beta falls", "https://e.test/2", published=datetime(2026, 10, 23, 13, 0, tzinfo=UTC))],
    )
    service = NewsService([google, gdelt], clock=Clock(START))

    document = await service.news("RHI")

    assert set(document) == DOCUMENT_KEYS, (
        "the document is one row per symbol; a per-source block would change "
        f"its shape with the weather, got {sorted(document)}"
    )
    assert document["symbol"] == "RHI"
    assert document["count"] == 2
    assert len(document["items"]) == 2
    assert [row["headline"] for row in document["items"]] == ["Beta falls", "Alpha rises"]
    for row in document["items"]:
        assert set(row) == ITEM_KEYS
    flattened = json.dumps(document)
    assert "google_news" not in flattened and "gdelt" not in flattened, (
        "no per-source columns: the provider name lives in provider_errors only"
    )


async def test_as_of_dates_the_document_not_the_articles() -> None:
    moment = datetime(2026, 10, 24, 9, 30, tzinfo=UTC)
    service = NewsService(
        [FakeProvider("google_news", [item("Alpha rises", "https://e.test/1")])],
        clock=Clock(moment),
    )

    document = await service.news("RHI")

    assert document["as_of"] == moment.isoformat(timespec="microseconds")
    assert document["items"][0]["published_at"] == "2026-10-23T12:00:00+00:00"
    assert document["latest_published_at"] == "2026-10-23T12:00:00+00:00"


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


async def test_items_are_newest_first_and_undated_items_come_last() -> None:
    older = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
    newer = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
    service = NewsService(
        [
            FakeProvider(
                "google_news",
                [
                    item("Older story", "https://e.test/1", published=older),
                    item("Undated story", "https://e.test/2", published=None),
                    item("Newest story", "https://e.test/3", published=newer),
                ],
            )
        ]
    )

    document = await service.news("RHI")

    assert [row["headline"] for row in document["items"]] == [
        "Newest story",
        "Older story",
        "Undated story",
    ], "an item without a timestamp is never dated to 'now' and never sorts above a dated one"
    assert document["items"][2]["published_at"] is None
    assert document["latest_published_at"] == newer.isoformat()


def test_order_items_is_deterministic_for_equal_timestamps() -> None:
    stamp = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
    first = item("Bravo", "https://e.test/b", published=stamp)
    second = item("alpha", "https://e.test/a", published=stamp)

    assert order_items([first, second]) == order_items([second, first])


# ---------------------------------------------------------------------------
# Merge and dedupe
# ---------------------------------------------------------------------------


def test_a_duplicate_url_is_one_headline_with_the_newest_timestamp() -> None:
    early = item(
        "Robert Half beats estimates",
        "https://e.test/x",
        published=datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
    )
    late = item(
        "Robert Half beats estimates",
        "https://e.test/x#section",
        source="",
        published=datetime(2026, 10, 23, 9, 0, tzinfo=UTC),
        source_url="https://reuters.com",
    )

    merged = merge_items([early, late])

    assert len(merged) == 1
    assert merged[0].published_at == datetime(2026, 10, 23, 9, 0, tzinfo=UTC), (
        "the surviving copy carries the newest claim, not the first one read"
    )
    assert merged[0].source_url == "https://reuters.com", (
        "a missing cell is filled from the duplicate, never invented"
    )


def test_a_duplicate_headline_from_two_wires_is_one_headline() -> None:
    google_copy = item(
        "Robert Half beats quarterly estimates",
        "https://news.google.com/rss/articles/CBMiA1JV?oc=5",
        source="Reuters",
        published=datetime(2026, 10, 23, 12, 0, tzinfo=UTC),
        source_url="https://www.reuters.com",
    )
    gdelt_copy = item(
        "Robert Half beats quarterly estimates - reuters.com",
        "https://www.reuters.com/markets/rhi-quarterly-results-beat",
        source="reuters.com",
        published=datetime(2026, 10, 23, 14, 15, tzinfo=UTC),
    )

    merged = merge_items([google_copy, gdelt_copy])

    assert len(merged) == 1, "one story must not occupy two of the eight rows"
    assert merged[0].source == "Reuters", "the first copy's provenance is kept"
    assert merged[0].published_at == datetime(2026, 10, 23, 14, 15, tzinfo=UTC)


def test_two_different_headlines_stay_two_rows() -> None:
    merged = merge_items(
        [
            item("Robert Half beats estimates", "https://e.test/1"),
            item("Staffing demand cools at Robert Half", "https://e.test/2"),
        ]
    )

    assert len(merged) == 2


def test_a_story_reached_by_two_keys_joins_the_two_groups() -> None:
    """The bridge case: one copy links a title from one group to a URL of another."""

    by_title = item("Robert Half beats quarterly estimates", "https://g.test/a")
    by_url = item("Staffing demand cools at Robert Half", "https://e.test/b")
    bridge = item("Robert Half beats quarterly estimates", "https://e.test/b")

    assert len(merge_items([by_title, by_url])) == 2, "two keys, two stories so far"
    merged = merge_items([by_title, by_url, bridge])

    assert len(merged) == 1, (
        "the third copy carries the first story's headline and the second's URL, "
        "so the two groups were always one story"
    )


def test_a_url_fragment_does_not_make_a_second_story() -> None:
    merged = merge_items(
        [
            item("Robert Half beats estimates", "https://e.test/a"),
            item("Robert Half beats estimates", "https://e.test/a#section"),
        ]
    )

    assert len(merged) == 1


async def test_a_duplicate_across_wires_costs_one_row_of_the_limit() -> None:
    shared = "https://e.test/one-story"
    google = FakeProvider("google_news", [item("One story", shared)])
    gdelt = FakeProvider("gdelt", [item("One story", shared, source="e.test")])
    service = NewsService([google, gdelt])

    document = await service.news("RHI")

    assert document["count"] == 1


# ---------------------------------------------------------------------------
# Limit
# ---------------------------------------------------------------------------


def _headlines(count: int, *, prefix: str = "Story") -> list[NewsItem]:
    return [
        item(
            f"{prefix} {index}",
            f"https://e.test/{index}",
            published=datetime(2026, 10, 23, 12, 0, tzinfo=UTC) - timedelta(minutes=index),
        )
        for index in range(count)
    ]


async def test_the_default_panel_is_eight_headlines() -> None:
    assert DEFAULT_ITEMS == 8, "Finviz parity: eight headlines per ticker"
    service = NewsService([FakeProvider("google_news", _headlines(12))])

    document = await service.news("RHI")

    assert document["count"] == 8
    assert len(document["items"]) == 8
    assert document["items"][0]["headline"] == "Story 0"


async def test_an_explicit_limit_slices_the_pool() -> None:
    service = NewsService([FakeProvider("google_news", _headlines(12))])

    document = await service.news("RHI", limit=3)

    assert document["count"] == 3
    assert [row["headline"] for row in document["items"]] == ["Story 0", "Story 1", "Story 2"]


async def test_the_pool_is_capped_even_for_a_huge_limit() -> None:
    service = NewsService([FakeProvider("google_news", _headlines(MAX_POOL_ITEMS + 30))])

    document = await service.news("RHI", limit=10_000)

    assert document["count"] == MAX_POOL_ITEMS, "the cache holds a bounded pool"


async def test_a_limit_below_one_is_refused_by_the_service_too() -> None:
    service = NewsService([FakeProvider("google_news")])

    with pytest.raises(ValueError):
        await service.news("RHI", limit=0)
    with pytest.raises(InvalidNewsSymbol):
        normalize_symbol("   ")


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


async def test_a_second_read_inside_the_window_sends_zero_requests() -> None:
    clock = Clock(START)
    google = FakeProvider("google_news", _headlines(3))
    gdelt = FakeProvider("gdelt", [item("Wire story", "https://e.test/wire")])
    service = NewsService([google, gdelt], clock=clock)

    first = await service.news("RHI")
    second = await service.news("RHI")

    assert google.calls == ["RHI"] and gdelt.calls == ["RHI"], (
        "the fifteen-minute window is the point: a browse tab is not a quote terminal"
    )
    assert first == second, "a cached document is served verbatim, as_of included"


async def test_a_read_at_another_limit_is_still_a_cache_hit() -> None:
    clock = Clock(START)
    google = FakeProvider("google_news", _headlines(12))
    service = NewsService([google], clock=clock)

    wide = await service.news("RHI", limit=8)
    narrow = await service.news("RHI", limit=3)

    assert len(google.calls) == 1
    assert wide["count"] == 8 and narrow["count"] == 3
    assert narrow["items"] == wide["items"][:3]


async def test_the_window_is_fifteen_minutes() -> None:
    assert CACHE_TTL_S == 900
    clock = Clock(START)
    google = FakeProvider("google_news", _headlines(2))
    service = NewsService([google], clock=clock)

    await service.news("RHI")
    clock.advance(seconds=CACHE_TTL_S - 1)
    await service.news("RHI")
    assert len(google.calls) == 1, "899 s is still inside the window"

    clock.advance(seconds=1)
    await service.news("RHI")
    assert len(google.calls) == 2, "900 s is outside it"


async def test_the_cache_is_per_symbol() -> None:
    google = FakeProvider("google_news", _headlines(2))
    service = NewsService([google], clock=Clock(START))

    await service.news("RHI")
    await service.news("AAPL")

    assert google.calls == ["RHI", "AAPL"]


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


async def test_one_broken_wire_is_a_cell_not_an_exception() -> None:
    google = FakeProvider("google_news", [item("Alpha rises", "https://e.test/1")])
    gdelt = FakeProvider("gdelt", error=RuntimeError("gdelt answered 429"))
    service = NewsService([google, gdelt])

    document = await service.news("RHI")

    assert document["count"] == 1
    assert document["provider_errors"] == [
        {"provider": "gdelt", "error": "RuntimeError: gdelt answered 429"}
    ], "the outage is reported beside the news, not instead of it"


async def test_a_partial_failure_is_cached_with_its_error_cell() -> None:
    clock = Clock(START)
    google = FakeProvider("google_news", [item("Alpha rises", "https://e.test/1")])
    gdelt = FakeProvider("gdelt", error=RuntimeError("gdelt answered 429"))
    service = NewsService([google, gdelt], clock=clock)

    first = await service.news("RHI")
    second = await service.news("RHI")

    assert len(gdelt.calls) == 1
    assert second["provider_errors"] == first["provider_errors"]


async def test_every_wire_failing_raises_and_is_not_cached() -> None:
    google = FakeProvider("google_news", error=RuntimeError("google answered 503"))
    gdelt = FakeProvider("gdelt", error=RuntimeError("gdelt answered 429"))
    service = NewsService([google, gdelt], clock=Clock(START))

    with pytest.raises(AllNewsProvidersFailed) as raised:
        await service.news("RHI")
    assert raised.value.symbol == "RHI"
    assert len(raised.value.failures) == 2
    assert "google answered 503" in str(raised.value)

    # An outage must not be remembered: the next read retries, and a recovered
    # wire answers.
    google._error = None  # noqa: SLF001 - the double recovers
    google._items = [item("Alpha rises", "https://e.test/1")]  # noqa: SLF001
    document = await service.news("RHI")

    assert document["count"] == 1
    assert len(google.calls) == 2 and len(gdelt.calls) == 2


async def test_no_news_is_an_empty_document_not_an_error() -> None:
    google = FakeProvider("google_news", [])
    gdelt = FakeProvider("gdelt", [])
    service = NewsService([google, gdelt], clock=Clock(START))

    document = await service.news("RHI")

    assert document["count"] == 0
    assert document["items"] == []
    assert document["latest_published_at"] is None
    assert document["provider_errors"] == []


async def test_an_empty_answer_is_cached_too() -> None:
    clock = Clock(START)
    google = FakeProvider("google_news", [])
    service = NewsService([google], clock=clock)

    await service.news("RHI")
    await service.news("RHI")

    assert len(google.calls) == 1, "'nothing happened' is a fact for the next fifteen minutes"


async def test_a_service_without_providers_cannot_claim_no_news() -> None:
    service = NewsService([], clock=Clock(START))

    with pytest.raises(AllNewsProvidersFailed):
        await service.news("RHI")


async def test_a_hanging_wire_times_out_into_an_error_cell() -> None:
    google = FakeProvider("google_news", [item("Alpha rises", "https://e.test/1")])
    slow = FakeProvider("gdelt", [item("Late", "https://e.test/late")], delay=5.0)
    service = NewsService([google, slow], timeout_s=0.01)

    document = await service.news("RHI")

    assert document["count"] == 1
    assert document["provider_errors"][0]["provider"] == "gdelt"
    assert "TimeoutError" in document["provider_errors"][0]["error"]


async def test_a_provider_that_returns_junk_is_an_error_cell() -> None:
    google = FakeProvider("google_news", answer="not a sequence of items")
    gdelt = FakeProvider("gdelt", [item("Alpha rises", "https://e.test/1")])
    service = NewsService([google, gdelt])

    document = await service.news("RHI")

    assert document["count"] == 1
    assert document["provider_errors"][0]["provider"] == "google_news"
    assert "TypeError" in document["provider_errors"][0]["error"]


async def test_a_non_news_item_in_a_provider_answer_is_a_programming_error() -> None:
    google = FakeProvider("google_news", answer=[{"headline": "a dict, not an item"}])
    gdelt = FakeProvider("gdelt", [item("Alpha rises", "https://e.test/1")])
    service = NewsService([google, gdelt])

    document = await service.news("RHI")

    assert [error["provider"] for error in document["provider_errors"]] == ["google_news"]
    assert "TypeError" in document["provider_errors"][0]["error"]


async def test_an_item_without_a_headline_or_url_is_dropped() -> None:
    google = FakeProvider(
        "google_news",
        [
            item("", "https://e.test/blank"),
            item("No link", ""),
            item("Readable", "https://e.test/ok"),
        ],
    )
    service = NewsService([google])

    document = await service.news("RHI")

    assert [row["headline"] for row in document["items"]] == ["Readable"]


# ---------------------------------------------------------------------------
# Concurrency and validation
# ---------------------------------------------------------------------------


async def test_the_providers_are_asked_concurrently() -> None:
    barrier = Barrier(parties=2)
    google = FakeProvider("google_news", _headlines(1), barrier=barrier)
    gdelt = FakeProvider("gdelt", [item("Wire story", "https://e.test/wire")], barrier=barrier)
    service = NewsService([google, gdelt])

    document = await service.news("RHI")

    assert document["count"] == 2, (
        "both wires must be in flight at once; a sequential pass would deadlock "
        "on the barrier and time out"
    )


def test_symbols_are_normalised_and_nonsense_is_refused() -> None:
    assert normalize_symbol(" rhi ") == "RHI"
    assert normalize_symbol("brk.b") == "BRK.B"
    assert normalize_symbol("EURUSD=X") == "EURUSD=X"
    assert normalize_symbol("^GSPC") == "^GSPC"

    for bad in ("", "   ", "AAPL X", "../etc/passwd", "AAPL;DROP", "$AAPL"):
        with pytest.raises(InvalidNewsSymbol):
            normalize_symbol(bad)


async def test_an_invalid_symbol_is_refused_before_any_request() -> None:
    google = FakeProvider("google_news", _headlines(1))
    service = NewsService([google])

    with pytest.raises(InvalidNewsSymbol):
        await service.news("not a ticker")
    assert google.calls == []


def test_providers_need_unique_names_and_a_name_at_all() -> None:
    with pytest.raises(ValueError):
        NewsService(
            [FakeProvider("gdelt"), FakeProvider("gdelt")]
        )
    with pytest.raises(ValueError):
        NewsService([FakeProvider("")])


def test_the_constructor_refuses_a_nonsense_window() -> None:
    with pytest.raises(ValueError):
        NewsService([FakeProvider("gdelt")], cache_ttl_s=0)
    with pytest.raises(ValueError):
        NewsService([FakeProvider("gdelt")], limit=0)
    with pytest.raises(ValueError):
        NewsService([FakeProvider("gdelt")], timeout_s=0)


async def test_a_naive_timestamp_is_anchored_to_utc() -> None:
    naive = datetime(2026, 10, 23, 12, 0)
    google = FakeProvider("google_news", [item("Alpha rises", "https://e.test/1", published=naive)])
    service = NewsService([google])

    document = await service.news("RHI")

    assert document["items"][0]["published_at"] == "2026-10-23T12:00:00+00:00", (
        "two wires must be comparable; a naive stamp is anchored, not guessed"
    )


async def test_a_callable_provider_is_a_first_class_wire() -> None:
    async def fetch(symbol: str) -> Sequence[NewsItem]:
        return [item(f"{symbol} story", "https://e.test/1")]

    service = NewsService([CallableNewsProvider("custom", fetch)])

    document = await service.news("RHI")

    assert document["items"][0]["headline"] == "RHI story"
    assert service.provider_names == ("custom",)


# ---------------------------------------------------------------------------
# The two real providers, off the fixtures, through the one HTTP seam
# ---------------------------------------------------------------------------


async def test_google_provider_reads_the_rss_fixture(monkeypatch: Any) -> None:
    from tests._phase2 import MockWeb, install_web

    web = MockWeb()
    web.route("news.google", lambda _request: _xml_response(GOOGLE_FEED))
    install_web(monkeypatch, web)
    provider = GoogleNewsProvider()

    items = await provider.fetch("RHI")

    parsed = rss.parse_google_news(GOOGLE_FEED.read_text(encoding="utf-8"))
    assert [entry.headline for entry in items] == [entry.headline for entry in parsed], (
        "the provider reuses the pure parser; it does not grow a second one"
    )
    assert len(items) == 5, "the fixture carries five readable items"
    assert items[0].headline == "Nvidia hits record high on AI demand"
    assert items[0].source == "Reuters"
    assert "news.google.com" in web.requests[0].url
    assert "RHI%20stock" in web.requests[0].url, "the query names the company, not a bare word"


async def test_google_provider_turns_a_non_200_into_a_provider_error(monkeypatch: Any) -> None:
    from tests._phase2 import MockWeb, install_web

    web = MockWeb()
    web.status("news.google", 429)
    install_web(monkeypatch, web)

    with pytest.raises(NewsProviderError) as raised:
        await GoogleNewsProvider().fetch("RHI")
    assert raised.value.status == 429


async def test_google_provider_rejects_a_document_that_is_not_a_feed(monkeypatch: Any) -> None:
    from tests._phase2 import MockWeb, install_web

    web = MockWeb()
    web.route(
        "news.google",
        lambda _request: _text_response("<!DOCTYPE html><html>maintenance</html>"),
    )
    install_web(monkeypatch, web)

    with pytest.raises(NewsProviderError):
        await GoogleNewsProvider().fetch("RHI")


async def test_gdelt_provider_reads_the_artlist_fixture(monkeypatch: Any) -> None:
    from tests._phase2 import MockWeb, install_web

    web = MockWeb()
    web.route(
        "gdeltproject",
        lambda _request: _json_response(GDELT_PAYLOAD.read_text(encoding="utf-8")),
    )
    install_web(monkeypatch, web)

    items = await GdeltNewsProvider().fetch("RHI")

    assert len(items) == 4, (
        "the fixture's unlinked article and its duplicate URL are dropped"
    )
    assert items[0].headline == "Robert Half beats quarterly estimates as hiring stabilises"
    assert items[0].source == "reuters.com"
    assert items[0].source_url is None, "GDELT publishes a domain, not a source URL"
    assert items[0].published_at == datetime(2026, 10, 23, 14, 15, tzinfo=UTC)
    assert items[-1].published_at is None
    assert "api.gdeltproject.org" in web.requests[0].url
    assert "mode=artlist" in web.requests[0].url


async def test_gdelt_rate_limit_notice_is_a_provider_error(monkeypatch: Any) -> None:
    from tests._phase2 import MockWeb, install_web

    web = MockWeb()
    web.route(
        "gdeltproject",
        lambda _request: _text_response(
            "Please limit requests to one every 5 seconds or use the cloud API."
        ),
    )
    install_web(monkeypatch, web)

    with pytest.raises(NewsProviderError) as raised:
        await GdeltNewsProvider().fetch("RHI")
    assert "limit requests" in str(raised.value), (
        "a plain-text notice is a failure, never an empty article list"
    )


def test_gdelt_url_encodes_the_phrase_and_names_the_mode() -> None:
    url = gdelt_url("BRK.B stock", max_records=10, timespan="7d")

    assert url.startswith("https://api.gdeltproject.org/api/v2/doc/doc?query=BRK.B%20stock")
    assert "mode=artlist" in url and "format=json" in url
    assert "maxrecords=10" in url and "timespan=7d" in url and "sort=datedesc" in url
    assert " " not in url, "urllib is a banned transport; the encoder is local"

    with pytest.raises(ValueError):
        gdelt_url("   ")


def test_parse_gdelt_refuses_an_envelope_it_cannot_read() -> None:
    for payload in ("", "[]", '"a string"', '{"articles": "nope"}'):
        with pytest.raises(GdeltError):
            parse_gdelt(payload)
    # A list of non-mappings is dropped rather than trusted: an article that is
    # not an object carries no headline and no URL.
    assert parse_gdelt('{"articles": [1, 2]}') == []


def test_parse_gdelt_reads_an_empty_envelope_as_no_news() -> None:
    assert parse_gdelt('{"count": 0}') == []


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_the_default_providers_are_google_news_then_gdelt() -> None:
    providers = build_news_providers(Settings())

    assert [provider.name for provider in providers] == ["google_news", "gdelt"]


def test_gdelt_can_be_turned_off_without_shrinking_the_document() -> None:
    providers = build_news_providers(Settings(news_gdelt_enabled=False))

    assert [provider.name for provider in providers] == ["google_news"]


def test_build_news_service_takes_its_window_and_limit_from_settings() -> None:
    service = build_news_service(
        Settings(news_cache_ttl_s=60, news_limit=5, news_timeout_s=2.5)
    )

    assert service.cache_ttl_s == 60
    assert service.default_limit == 5
    assert service.provider_names == ("google_news", "gdelt")


# ---------------------------------------------------------------------------
# HTTP response helpers (MockWeb answers with httpx2 responses)
# ---------------------------------------------------------------------------


def _xml_response(path: Path) -> Any:
    import httpx2

    return httpx2.Response(200, text=path.read_text(encoding="utf-8"))


def _text_response(body: str) -> Any:
    import httpx2

    return httpx2.Response(200, text=body)


def _json_response(body: str) -> Any:
    import httpx2

    return httpx2.Response(
        200, content=body.encode("utf-8"), headers={"content-type": "application/json"}
    )
