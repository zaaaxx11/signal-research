"""Offline contract tests for the Yahoo/Nasdaq RSS news source.

Every test feeds literal documents (or an injected fetcher/client) into the
parser and the depth source. ``tests/conftest.py`` blocks egress, so a test that
reaches the network fails here by construction — the fetcher seam is the only
transport these tests know.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any

import pytest

from app.market.depth import DepthObservation, canonical_symbol
from app.market.news import (
    DEFAULT_LIMIT,
    NewsFeedError,
    NewsHeadline,
    RssNewsSource,
    clean_url,
    feed_urls,
    parse_nasdaq_rss,
    parse_pubdate,
    parse_rss,
    parse_yahoo_rss,
    title_key,
)

CLOCK = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)

YAHOO_DOC = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Yahoo Finance</title>
    <item>
      <title>Apple beats estimates as services grow</title>
      <link>https://finance.yahoo.com/news/apple-beats-estimates-123456.html?.tsrc=rss</link>
      <pubDate>Tue, 27 Oct 2026 14:05:00 GMT</pubDate>
      <guid isPermaLink="false">aapl-1</guid>
    </item>
    <item>
      <title>Apple unveils new MacBook lineup</title>
      <link>https://finance.yahoo.com/news/apple-macbook-654321.html?.tsrc=rss&amp;x=1</link>
      <pubDate>Mon, 26 Oct 2026 08:00:00 -0400</pubDate>
    </item>
  </channel>
</rss>
"""

NASDAQ_DOC = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Nasdaq</title>
    <item>
      <title>3 &quot;Magnificent Seven&quot; Stocks to Buy &amp; Hold</title>
      <link>https://www.nasdaq.com/articles/3-magnificent-seven-stocks-to-buy-hold</link>
      <pubDate>Wed, 28 Oct 2026 09:30:00 -0400</pubDate>
    </item>
    <item>
      <title>Nasdaq futures rise ahead of earnings</title>
      <link>https://www.nasdaq.com/articles/nasdaq-futures-rise</link>
      <pubDate>Wed, 28 Oct 2026 07:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


def _rss(*items: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel><title>fixture</title>'
        + "".join(items)
        + "</channel></rss>"
    )


def _item(
    title: str,
    link: str,
    published: datetime | None,
    *,
    guid: str = "",
    guid_permalink: str | None = None,
) -> str:
    parts = [f"<title>{title}</title>"]
    if link:
        parts.append(f"<link>{link}</link>")
    if published is not None:
        parts.append(f"<pubDate>{format_datetime(published)}</pubDate>")
    if guid:
        attribute = "" if guid_permalink is None else f' isPermaLink="{guid_permalink}"'
        parts.append(f"<guid{attribute}>{guid}</guid>")
    return "<item>" + "".join(parts) + "</item>"


class FakeFeeds:
    """Fetcher seam keyed by host: every call is recorded, nothing dials out."""

    def __init__(self, *, yahoo: Any = None, nasdaq: Any = None) -> None:
        self.documents = {"yahoo": yahoo, "nasdaq": nasdaq}
        self.calls: list[str] = []

    async def __call__(self, url: str) -> str | bytes:
        self.calls.append(url)
        feed = "yahoo" if "feeds.finance.yahoo.com" in url else "nasdaq"
        document = self.documents[feed]
        if isinstance(document, Exception):
            raise document
        if document is None:
            raise AssertionError(f"no fixture for {url}")
        return document


class FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class FakeClient:
    def __init__(self, *, yahoo: FakeResponse, nasdaq: FakeResponse) -> None:
        self.responses = {"yahoo": yahoo, "nasdaq": nasdaq}
        self.calls: list[str] = []

    async def get(self, url: str) -> FakeResponse:
        self.calls.append(url)
        return self.responses["yahoo" if "feeds.finance.yahoo.com" in url else "nasdaq"]


# ---------------------------------------------------------------------------
# Parser: the two real feed shapes
# ---------------------------------------------------------------------------


def test_yahoo_rss_item_maps_to_headline() -> None:
    items = parse_yahoo_rss(YAHOO_DOC, limit=None)

    assert [item.feed for item in items] == ["yahoo", "yahoo"]
    newest = items[0]
    assert newest.title == "Apple beats estimates as services grow"
    assert newest.url == "https://finance.yahoo.com/news/apple-beats-estimates-123456.html"
    assert newest.published_at == datetime(2026, 10, 27, 14, 5, tzinfo=UTC)
    # The remaining `x=1` query survives; only the tracking key is removed.
    assert items[1].url == "https://finance.yahoo.com/news/apple-macbook-654321.html?x=1"
    assert items[1].published_at == datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def test_nasdaq_rss_unescapes_html_entities() -> None:
    items = parse_nasdaq_rss(NASDAQ_DOC)

    assert len(items) == 2
    assert items[0].feed == "nasdaq"
    assert items[0].title == '3 "Magnificent Seven" Stocks to Buy & Hold'
    assert items[0].published_at == datetime(2026, 10, 28, 13, 30, tzinfo=UTC)
    assert items[1].published_at == datetime(2026, 10, 28, 7, 0, tzinfo=UTC)


def test_bytes_document_parses_like_text() -> None:
    assert parse_yahoo_rss(YAHOO_DOC.encode("utf-8")) == parse_yahoo_rss(YAHOO_DOC)
    assert parse_yahoo_rss("\ufeff" + YAHOO_DOC) == parse_yahoo_rss(YAHOO_DOC)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Tue, 27 Oct 2026 14:05:00 GMT", datetime(2026, 10, 27, 14, 5, tzinfo=UTC)),
        ("Wed, 28 Oct 2026 09:30:00 -0400", datetime(2026, 10, 28, 13, 30, tzinfo=UTC)),
        ("Thu, 29 Oct 2026 00:15:00 +0530", datetime(2026, 10, 28, 18, 45, tzinfo=UTC)),
        # RFC-822 "-0000" is "UTC, zone unknown": parsedate returns naive and
        # the parser anchors it instead of leaving one naive stamp in the set.
        ("Tue, 27 Oct 2026 14:05:00 -0000", datetime(2026, 10, 27, 14, 5, tzinfo=UTC)),
        ("2026-10-27T14:05:00Z", datetime(2026, 10, 27, 14, 5, tzinfo=UTC)),
    ],
)
def test_rfc822_pubdate_becomes_aware_utc(raw: str, expected: datetime) -> None:
    parsed = parse_pubdate(raw)

    assert parsed == expected
    assert parsed is not None
    assert parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


@pytest.mark.parametrize("raw", ["", "   ", "not a date", "Invalid Date", None, "2026-13-45"])
def test_unreadable_pubdate_is_none_not_now(raw: Any) -> None:
    assert parse_pubdate(raw) is None


# ---------------------------------------------------------------------------
# Parser: what is not a headline
# ---------------------------------------------------------------------------


def test_items_without_title_or_openable_link_are_dropped() -> None:
    document = _rss(
        # no title
        f"<item><link>https://example.com/a</link>"
        f"<pubDate>{format_datetime(CLOCK)}</pubDate></item>",
        # empty title of punctuation only
        f"<item><title> ... </title><link>https://example.com/b</link>"
        f"<pubDate>{format_datetime(CLOCK)}</pubDate></item>",
        # no link and no guid
        f"<item><title>No link</title><pubDate>{format_datetime(CLOCK)}</pubDate></item>",
        # relative link: no scheme, not openable
        f"<item><title>Relative</title><link>www.example.com/c</link>"
        f"<pubDate>{format_datetime(CLOCK)}</pubDate></item>",
        # javascript: is not http(s)
        f"<item><title>Script</title><link>javascript:alert(1)</link>"
        f"<pubDate>{format_datetime(CLOCK)}</pubDate></item>",
        _item("The one usable headline", "https://example.com/ok", CLOCK),
    )

    items = parse_rss(document, limit=None)

    assert [item.title for item in items] == ["The one usable headline"]


def test_permalink_guid_is_a_link_only_when_link_is_missing() -> None:
    document = _rss(
        _item("Guid permalink", "", CLOCK, guid="https://example.com/guid-ok"),
        _item(
            "Non permalink guid",
            "",
            CLOCK,
            guid="https://example.com/skip",
            guid_permalink="false",
        ),
        _item(
            "Link wins over guid",
            "https://example.com/link",
            CLOCK,
            guid="https://example.com/guid-loses",
        ),
    )

    items = parse_rss(document, limit=None)

    assert [(item.title, item.url) for item in items] == [
        ("Guid permalink", "https://example.com/guid-ok"),
        ("Link wins over guid", "https://example.com/link"),
    ]


def test_item_without_a_readable_date_is_dropped_not_stamped_with_fetch_time() -> None:
    document = _rss(
        _item("Undated", "https://example.com/undated", None),
        _item("Bad date", "https://example.com/bad", None).replace(
            "</item>", "<pubDate>not a date</pubDate></item>"
        ),
        _item("Dated", "https://example.com/dated", CLOCK),
    )

    items = parse_rss(document, limit=None)

    assert [item.title for item in items] == ["Dated"]
    assert items[0].published_at == CLOCK


def test_clean_url_drops_only_the_tracking_query() -> None:
    assert clean_url("https://finance.yahoo.com/a?.tsrc=rss") == "https://finance.yahoo.com/a"
    assert clean_url("https://x.com/a?b=1&tsrc=rss&c=2") == "https://x.com/a?b=1&c=2"
    assert clean_url("https://x.com/a?b=1&amp;c=2") == "https://x.com/a?b=1&c=2"
    assert clean_url("www.example.com/a") == ""
    assert clean_url("javascript:alert(1)") == ""
    assert clean_url("") == ""


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------


def test_title_key_ignores_case_spacing_punctuation_and_publisher() -> None:
    assert title_key("Apple  Beats Estimates! - Yahoo Finance") == title_key(
        "apple beats estimates"
    )
    # A tail with digits is content ("Q4 2026"), not a publisher suffix.
    assert title_key("Stock drops 5% - Q4 2026") != title_key("Stock drops 5%")


def test_duplicate_title_within_a_feed_keeps_the_newest_url() -> None:
    document = _rss(
        _item(
            "Apple  Beats Estimates!",
            "https://finance.yahoo.com/old",
            CLOCK - timedelta(hours=2),
        ),
        _item(
            "apple beats estimates - Yahoo Finance",
            "https://finance.yahoo.com/new",
            CLOCK,
        ),
    )

    items = parse_rss(document, limit=None)

    assert len(items) == 1
    assert items[0].url == "https://finance.yahoo.com/new"
    assert items[0].published_at == CLOCK


async def test_cross_feed_duplicate_picks_the_newest_copy() -> None:
    yahoo = _rss(
        _item(
            "Apple beats estimates",
            "https://finance.yahoo.com/story",
            CLOCK - timedelta(hours=1),
        )
    )
    nasdaq = _rss(
        _item(
            "Apple Beats Estimates!",
            "https://www.nasdaq.com/articles/story",
            CLOCK,
        )
    )
    source = RssNewsSource(fetcher=FakeFeeds(yahoo=yahoo, nasdaq=nasdaq))

    observation = (await source.fetch_many(["AAPL"]))["AAPL"]

    assert observation.fields["news_title"] == "Apple Beats Estimates!"
    assert observation.fields["news_url"] == "https://www.nasdaq.com/articles/story"
    assert observation.fields["news_time"] == "2026-10-28T12:00:00+00:00"


# ---------------------------------------------------------------------------
# Source: depth contract
# ---------------------------------------------------------------------------


async def test_source_publishes_only_the_three_news_cells() -> None:
    source = RssNewsSource(
        fetcher=FakeFeeds(yahoo=YAHOO_DOC, nasdaq=NASDAQ_DOC),
        clock=lambda: CLOCK,
    )

    result = await source.fetch_many(["AAPL"])
    observation = result["AAPL"]

    assert isinstance(observation, DepthObservation)
    assert source.name == "news"
    assert observation.source == "news"
    assert observation.as_of == CLOCK
    assert observation.symbol == "AAPL"
    assert set(observation.fields) == {"news_title", "news_url", "news_time"}
    assert observation.fields["news_title"] == (
        '3 "Magnificent Seven" Stocks to Buy & Hold'
    )
    assert observation.fields["news_time"] == "2026-10-28T13:30:00+00:00"
    stamped = datetime.fromisoformat(observation.fields["news_time"])
    assert stamped.tzinfo is not None and stamped.utcoffset() == timedelta(0)


async def test_one_failing_feed_does_not_erase_the_other() -> None:
    source = RssNewsSource(fetcher=FakeFeeds(yahoo=RuntimeError("boom"), nasdaq=NASDAQ_DOC))

    observation = (await source.fetch_many(["AAPL"]))["AAPL"]

    assert observation.fields["news_url"].startswith("https://www.nasdaq.com/")
    assert "Magnificent" in observation.fields["news_title"]


async def test_malformed_feed_is_a_failure_not_a_crash() -> None:
    source = RssNewsSource(fetcher=FakeFeeds(yahoo=b"<not-rss", nasdaq="<rss"))

    assert await source.fetch_many(["AAPL"]) == {}


async def test_client_http_error_is_a_feed_failure_not_a_crash() -> None:
    client = FakeClient(
        yahoo=FakeResponse(500, "<html>error</html>"),
        nasdaq=FakeResponse(200, NASDAQ_DOC),
    )
    source = RssNewsSource(client=client)

    observation = (await source.fetch_many(["AAPL"]))["AAPL"]

    assert observation.fields["news_url"].startswith("https://www.nasdaq.com/")
    assert len(client.calls) == 2


async def test_symbol_is_canonical_in_key_and_feed_urls() -> None:
    fetcher = FakeFeeds(yahoo=YAHOO_DOC, nasdaq=NASDAQ_DOC)
    source = RssNewsSource(fetcher=fetcher)

    result = await source.fetch_many([" brk.b "])

    assert list(result) == ["BRK-B"]
    assert result["BRK-B"].symbol == canonical_symbol("BRK.B")
    assert any("s=BRK-B" in url for url in fetcher.calls)
    assert any("symbol=BRK-B" in url for url in fetcher.calls)
    assert [feed for feed, _ in feed_urls("brk.b")] == ["yahoo", "nasdaq"]


async def test_empty_symbol_list_makes_no_request() -> None:
    fetcher = FakeFeeds(yahoo=YAHOO_DOC, nasdaq=NASDAQ_DOC)
    source = RssNewsSource(fetcher=fetcher)

    assert await source.fetch_many([]) == {}
    assert fetcher.calls == []


# ---------------------------------------------------------------------------
# Bounded store
# ---------------------------------------------------------------------------


def test_limit_keeps_only_the_newest_items_from_a_long_feed() -> None:
    base = datetime(2026, 10, 1, tzinfo=UTC)
    items = [
        _item(f"Headline {index}", f"https://example.com/{index}", base + timedelta(hours=index))
        for index in range(100)
    ]

    parsed = parse_rss(_rss(*items), limit=DEFAULT_LIMIT)

    assert len(parsed) == DEFAULT_LIMIT
    assert [item.title for item in parsed] == [
        f"Headline {index}" for index in range(99, 89, -1)
    ]


async def test_source_never_publishes_more_than_the_newest_headline() -> None:
    base = datetime(2026, 10, 1, tzinfo=UTC)
    items = [
        _item(f"Headline {index}", f"https://example.com/{index}", base + timedelta(hours=index))
        for index in range(100)
    ]
    source = RssNewsSource(fetcher=FakeFeeds(yahoo=_rss(*items)), limit=3)

    observation = (await source.fetch_many(["AAPL"]))["AAPL"]

    assert observation.fields["news_title"] == "Headline 99"
    assert observation.fields["news_time"] == "2026-10-05T03:00:00+00:00"
    # One depth row, three cells: the 100-item feed is not copied into a store.
    assert len(observation.fields) == 3


# ---------------------------------------------------------------------------
# Shape guards
# ---------------------------------------------------------------------------


def test_empty_feed_and_zero_limit_are_empty_not_errors() -> None:
    assert parse_rss(_rss()) == []
    assert parse_rss(_rss(), limit=0) == []
    assert parse_rss(_rss(), limit=None) == []


def test_unreadable_document_raises_news_feed_error() -> None:
    with pytest.raises(NewsFeedError):
        parse_rss("<rss><channel>")
    assert issubclass(NewsFeedError, ValueError)


def test_constructor_rejects_impossible_configuration() -> None:
    with pytest.raises(ValueError):
        RssNewsSource(limit=0)
    with pytest.raises(TypeError):
        RssNewsSource(fetcher="not callable")
    with pytest.raises(ValueError):
        RssNewsSource(feeds=("reuters",))


def test_headline_requires_an_aware_timestamp() -> None:
    with pytest.raises(ValueError):
        NewsHeadline(
            title="x",
            url="https://example.com",
            published_at=datetime(2026, 10, 28, 12, 0),
            feed="yahoo",
        )
    headline = NewsHeadline(
        title="x",
        url="https://example.com",
        published_at=datetime(
            2026, 10, 28, 9, 0, tzinfo=timezone(timedelta(hours=-3))
        ),
        feed="yahoo",
    )
    assert headline.published_at == datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fallback mode: Yahoo first, Nasdaq only when Yahoo came back empty
# ---------------------------------------------------------------------------


async def test_fallback_yahoo_headlines_skip_the_nasdaq_request() -> None:
    fetcher = FakeFeeds(yahoo=YAHOO_DOC, nasdaq=NASDAQ_DOC)
    source = RssNewsSource(fetcher=fetcher, fallback=True, clock=lambda: CLOCK)

    observation = (await source.fetch_many(["AAPL"]))["AAPL"]

    assert source.fallback is True
    # One request for the symbol, not two: the first feed already answered.
    assert len(fetcher.calls) == 1
    assert "feeds.finance.yahoo.com" in fetcher.calls[0]
    assert "s=AAPL" in fetcher.calls[0]
    assert observation.as_of == CLOCK
    assert observation.symbol == "AAPL"
    assert set(observation.fields) == {"news_title", "news_url", "news_time"}
    assert observation.fields["news_title"] == "Apple beats estimates as services grow"
    assert (
        observation.fields["news_url"]
        == "https://finance.yahoo.com/news/apple-beats-estimates-123456.html"
    )
    # The article's own date, not CLOCK and not the fetch time.
    assert observation.fields["news_time"] == "2026-10-27T14:05:00+00:00"


async def test_fallback_empty_yahoo_feed_dials_nasdaq() -> None:
    fetcher = FakeFeeds(yahoo=_rss(), nasdaq=NASDAQ_DOC)
    source = RssNewsSource(fetcher=fetcher, fallback=True)

    observation = (await source.fetch_many(["AAPL"]))["AAPL"]

    assert len(fetcher.calls) == 2
    assert "feeds.finance.yahoo.com" in fetcher.calls[0]
    assert "nasdaq.com" in fetcher.calls[1]
    assert observation.fields["news_url"].startswith("https://www.nasdaq.com/")
    assert "Magnificent" in observation.fields["news_title"]
    assert observation.fields["news_time"] == "2026-10-28T13:30:00+00:00"


async def test_fallback_yahoo_exception_dials_nasdaq() -> None:
    fetcher = FakeFeeds(yahoo=RuntimeError("Yahoo answered 500"), nasdaq=NASDAQ_DOC)
    source = RssNewsSource(fetcher=fetcher, fallback=True)

    observation = (await source.fetch_many(["AAPL"]))["AAPL"]

    assert len(fetcher.calls) == 2
    assert observation.fields["news_url"].startswith("https://www.nasdaq.com/")
    assert observation.fields["news_time"] == "2026-10-28T13:30:00+00:00"


async def test_fallback_malformed_yahoo_document_dials_nasdaq() -> None:
    fetcher = FakeFeeds(yahoo=b"<not-rss", nasdaq=NASDAQ_DOC)
    source = RssNewsSource(fetcher=fetcher, fallback=True)

    observation = (await source.fetch_many(["AAPL"]))["AAPL"]

    assert len(fetcher.calls) == 2
    assert observation.fields["news_title"] == '3 "Magnificent Seven" Stocks to Buy & Hold'


async def test_fallback_undated_yahoo_items_do_not_count_as_an_answer() -> None:
    # A headline with no <pubDate> is not publishable, so Yahoo is "empty" for
    # this symbol and Nasdaq is still asked — but nothing is ever stamped with
    # the fetch clock, so an all-undated day produces no row at all.
    yahoo = _rss(_item("Breaking but undated", "https://finance.yahoo.com/undated", None))
    fetcher = FakeFeeds(yahoo=yahoo, nasdaq=_rss())
    source = RssNewsSource(fetcher=fetcher, fallback=True, clock=lambda: CLOCK)

    assert await source.fetch_many(["AAPL"]) == {}
    assert len(fetcher.calls) == 2


async def test_fallback_linkless_yahoo_items_fall_through_to_nasdaq() -> None:
    # Titles alone are not a headline: no openable link means Yahoo has nothing
    # publishable, so the chain still asks Nasdaq.
    yahoo = _rss(
        _item("Relative link only", "www.yahoo.com/relative", CLOCK),
        _item("Script link only", "javascript:alert(1)", CLOCK),
    )
    fetcher = FakeFeeds(yahoo=yahoo, nasdaq=NASDAQ_DOC)
    source = RssNewsSource(fetcher=fetcher, fallback=True)

    observation = (await source.fetch_many(["AAPL"]))["AAPL"]

    assert len(fetcher.calls) == 2
    assert observation.fields["news_url"].startswith("https://www.nasdaq.com/")


async def test_fallback_publishes_nothing_when_both_feeds_come_back_empty() -> None:
    fetcher = FakeFeeds(yahoo=_rss(), nasdaq=_rss())
    source = RssNewsSource(fetcher=fetcher, fallback=True)

    assert await source.fetch_many(["AAPL"]) == {}
    assert len(fetcher.calls) == 2


async def test_fallback_both_feeds_failing_is_an_empty_batch_not_a_crash() -> None:
    fetcher = FakeFeeds(yahoo=RuntimeError("boom"), nasdaq=RuntimeError("boom"))
    source = RssNewsSource(fetcher=fetcher, fallback=True)

    assert await source.fetch_many(["AAPL", "MSFT"]) == {}
    assert len(fetcher.calls) == 4


async def test_fallback_default_is_off_and_keeps_the_two_feed_union() -> None:
    fetcher = FakeFeeds(
        yahoo=_rss(
            _item(
                "Apple beats estimates",
                "https://finance.yahoo.com/story",
                CLOCK - timedelta(hours=1),
            )
        ),
        nasdaq=_rss(
            _item(
                "Apple Beats Estimates!",
                "https://www.nasdaq.com/articles/story",
                CLOCK,
            )
        ),
    )
    source = RssNewsSource(fetcher=fetcher)

    assert source.fallback is False
    observation = (await source.fetch_many(["AAPL"]))["AAPL"]

    # Without the flag the union still wins: both feeds are read and the newer
    # Nasdaq copy of the same headline is the one published.
    assert len(fetcher.calls) == 2
    assert observation.fields["news_url"] == "https://www.nasdaq.com/articles/story"
    assert observation.fields["news_time"] == "2026-10-28T12:00:00+00:00"


async def test_fallback_mode_dedupes_titles_inside_the_first_feed() -> None:
    yahoo = _rss(
        _item(
            "Apple  Beats Estimates!",
            "https://finance.yahoo.com/old",
            CLOCK - timedelta(hours=3),
        ),
        _item(
            "apple beats estimates - Yahoo Finance",
            "https://finance.yahoo.com/new",
            CLOCK,
        ),
    )
    fetcher = FakeFeeds(yahoo=yahoo, nasdaq=NASDAQ_DOC)
    source = RssNewsSource(fetcher=fetcher, fallback=True)

    observation = (await source.fetch_many(["AAPL"]))["AAPL"]

    assert len(fetcher.calls) == 1
    assert observation.fields["news_url"] == "https://finance.yahoo.com/new"
    assert observation.fields["news_time"] == "2026-10-28T12:00:00+00:00"


async def test_fallback_uses_only_the_configured_first_feed() -> None:
    fetcher = FakeFeeds(yahoo=YAHOO_DOC, nasdaq=NASDAQ_DOC)
    source = RssNewsSource(fetcher=fetcher, feeds=("nasdaq", "yahoo"), fallback=True)

    observation = (await source.fetch_many(["AAPL"]))["AAPL"]

    assert len(fetcher.calls) == 1
    assert "nasdaq.com" in fetcher.calls[0]
    assert observation.fields["news_url"].startswith("https://www.nasdaq.com/")


async def test_fallback_with_a_single_feed_never_reaches_for_a_second() -> None:
    fetcher = FakeFeeds(yahoo=_rss(), nasdaq=NASDAQ_DOC)
    source = RssNewsSource(fetcher=fetcher, feeds=("yahoo",), fallback=True)

    # Nothing to fall back to: no IndexError, and Nasdaq is never dialled.
    assert await source.fetch_many(["AAPL"]) == {}
    assert len(fetcher.calls) == 1


async def test_fallback_client_http_500_on_yahoo_still_publishes_nasdaq() -> None:
    client = FakeClient(
        yahoo=FakeResponse(500, "<html>error</html>"),
        nasdaq=FakeResponse(200, NASDAQ_DOC),
    )
    source = RssNewsSource(client=client, fallback=True)

    observation = (await source.fetch_many(["AAPL"]))["AAPL"]

    assert len(client.calls) == 2
    assert "nasdaq.com" in client.calls[1]
    assert "Magnificent" in observation.fields["news_title"]


async def test_fallback_canonicalises_the_symbol_once() -> None:
    fetcher = FakeFeeds(yahoo=YAHOO_DOC, nasdaq=NASDAQ_DOC)
    source = RssNewsSource(fetcher=fetcher, fallback=True)

    result = await source.fetch_many([" brk.b "])

    assert list(result) == ["BRK-B"]
    assert result["BRK-B"].symbol == canonical_symbol("BRK.B")
    assert len(fetcher.calls) == 1
    assert "s=BRK-B" in fetcher.calls[0]


async def test_fallback_skips_every_request_for_an_empty_symbol_list() -> None:
    fetcher = FakeFeeds(yahoo=YAHOO_DOC, nasdaq=NASDAQ_DOC)
    source = RssNewsSource(fetcher=fetcher, fallback=True)

    assert await source.fetch_many([]) == {}
    assert fetcher.calls == []


async def test_fallback_concurrency_costs_one_request_per_symbol() -> None:
    symbols = [f"SYM{index}" for index in range(40)]
    fetcher = FakeFeeds(yahoo=YAHOO_DOC, nasdaq=NASDAQ_DOC)
    source = RssNewsSource(fetcher=fetcher, fallback=True, concurrency=4)

    result = await source.fetch_many(symbols)

    assert len(result) == len(symbols)
    # 40 symbols × 2 feeds = 80 requests without the flag; the chain is 40.
    assert len(fetcher.calls) == len(symbols)
    assert all("feeds.finance.yahoo.com" in url for url in fetcher.calls)
    assert sorted(url.split("s=")[1].split("&")[0] for url in fetcher.calls) == sorted(symbols)


async def test_fallback_keeps_the_newest_of_a_hundred_item_feed_in_one_request() -> None:
    base = datetime(2026, 10, 1, tzinfo=UTC)
    items = [
        _item(f"Headline {index}", f"https://example.com/{index}", base + timedelta(hours=index))
        for index in range(100)
    ]
    fetcher = FakeFeeds(yahoo=_rss(*items), nasdaq=NASDAQ_DOC)
    source = RssNewsSource(fetcher=fetcher, fallback=True, limit=3)

    observation = (await source.fetch_many(["AAPL"]))["AAPL"]

    assert len(fetcher.calls) == 1
    assert observation.fields["news_title"] == "Headline 99"
    assert observation.fields["news_time"] == "2026-10-05T03:00:00+00:00"
    assert set(observation.fields) == {"news_title", "news_url", "news_time"}
