"""``GET /market/news``: twelve feeds, one list, one row per story.

The merge is the product: a reader should learn a thing once, see every symbol
it concerns, and never be handed a link that is not a link. These tests hold
that and the four failure modes around it — a dead feed, a dead market, a
revalidated read, and a story whose date is unknown.

Every feed here is a fake; ``tests/conftest.py`` blocks egress, so a test that
reached a news wire would fail by construction.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator

from app.news.service import AllNewsProvidersFailed
from tests import _screen as S  # noqa: F401  (the house app harness imports)

ROUTE = "/market/news"

NEWEST = datetime(2026, 10, 31, 18, 0, tzinfo=UTC)


class FakeFeed:
    """The news service, reduced to the one method this route calls."""

    def __init__(self, documents: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.documents = documents or {}
        self.calls: list[str] = []

    async def news(self, symbol: str, *, limit: int | None = None) -> dict[str, Any]:
        self.calls.append(symbol)
        if symbol in self.failed:
            # The service's own failure type: the route catches that one, and
            # anything else would be a programming error rather than a dead wire.
            raise AllNewsProvidersFailed(symbol, ["wire down"])
        rows = self.documents.get(symbol, [])
        return {
            "symbol": symbol,
            "as_of": NEWEST.isoformat(),
            "count": len(rows),
            "latest_published_at": rows[0]["published_at"] if rows else None,
            "items": rows[: limit or len(rows)],
        }

    failed: set[str] = set()


def story(
    headline: str,
    url: str,
    *,
    published: datetime | None = NEWEST,
    source: str = "Newswire",
) -> dict[str, Any]:
    return {
        "headline": headline,
        "url": url,
        "source": source,
        "published_at": published.isoformat() if published else None,
    }


@asynccontextmanager
async def news_client(tmp_path: Path, monkeypatch: Any, feed: FakeFeed) -> AsyncIterator[Any]:
    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))

    from httpx2 import ASGITransport, AsyncClient

    from app.main import create_app

    application = create_app()
    application.state.news = feed
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://mee.test") as client:
        yield client, application


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


async def test_the_route_is_mounted_and_head_is_its_own_operation(
    tmp_path: Path, monkeypatch: Any
) -> None:
    feed = FakeFeed({"SPY": [story("A", "https://e.test/a")]})
    async with news_client(tmp_path, monkeypatch, feed) as (_client, application):
        paths = [getattr(route, "path", None) for route in application.routes]
        methods: dict[str, set[str]] = {}
        for route in application.routes:
            if getattr(route, "path", None) == ROUTE:
                methods.setdefault(ROUTE, set()).update(getattr(route, "methods", set()) or set())

    assert ROUTE in paths
    assert {"GET", "HEAD"} <= methods[ROUTE], methods


async def test_one_row_per_story_with_every_symbol_that_carried_it(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Two feeds, one URL: one row, both symbols, sorted."""

    shared = "https://e.test/story"
    feed = FakeFeed(
        {
            "SPY": [story("Fed holds rates", shared, source="Wire A")],
            "AAPL": [story("Fed holds rates", shared, source="Wire B")],
            "NVDA": [story("Chips rally", "https://e.test/chips")],
        }
    )
    async with news_client(tmp_path, monkeypatch, feed) as (client, _app):
        response = await client.get(ROUTE)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"as_of", "symbols", "count", "items", "provider_errors"}
    assert body["count"] == len(body["items"]) == 2
    merged = next(item for item in body["items"] if item["url"] == shared)
    assert merged["symbols"] == ["AAPL", "SPY"]
    assert merged["headline"] == "Fed holds rates"


async def test_order_is_newest_first_and_an_undated_story_sorts_last(
    tmp_path: Path, monkeypatch: Any
) -> None:
    feed = FakeFeed(
        {
            "SPY": [
                story("older", "https://e.test/old", published=NEWEST - timedelta(hours=3)),
                story("undated", "https://e.test/undated", published=None),
                story("newest", "https://e.test/new", published=NEWEST),
            ]
        }
    )
    async with news_client(tmp_path, monkeypatch, feed) as (client, _app):
        body = (await client.get(ROUTE)).json()

    assert [row["headline"] for row in body["items"]] == ["newest", "older", "undated"]
    assert body["items"][-1]["published_at"] is None


async def test_an_auto_written_ownership_post_is_not_a_headline(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The template posts are filings, not news: the desk must not lead with one."""

    feed = FakeFeed(
        {
            "SPY": [
                # Six shapes observed live, all within five minutes.
                story("Mn Services Vermogensbeheer B.V. Acquires 26,400 Shares of NVIDIA Corporation $NVDA", "https://e.test/13f-1"),
                story("NVIDIA Corporation $NVDA Shares Newly Acquired by Pacifica Partners Inc.", "https://e.test/13f-2"),
                story("Stableford Capital II LLC Sells 13,531 Shares of NVIDIA Corporation $NVDA", "https://e.test/13f-3"),
                story("49,999 Amazon.com, Inc. $AMZN Shares Acquired by Sumitomo Mitsui", "https://e.test/13f-4"),
                story("Apple Inc. $AAPL Stock: Kintegral Advisory LLC Boosts Holdings", "https://e.test/13f-5"),
                story("Invesco QQQ $QQQ Stock Sold by Elevation Capital Advisory LLC", "https://e.test/13f-6"),
                story("Nvidia falls as orders slow into the weekend", "https://e.test/real-1"),
                story("Microsoft acquires gaming studio in all-cash deal", "https://e.test/real-2"),
                story("Fed officials split on the pace of cuts, minutes show", "https://e.test/real-3"),
            ]
        }
    )
    async with news_client(tmp_path, monkeypatch, feed) as (client, _app):
        body = (await client.get(ROUTE)).json()

    headlines = [row["headline"] for row in body["items"]]
    # Same timestamp, so the tie-break is headline ascending — a total order, not
    # the order the fake happened to answer in.
    assert headlines == [
        "Fed officials split on the pace of cuts, minutes show",
        "Microsoft acquires gaming studio in all-cash deal",
        "Nvidia falls as orders slow into the weekend",
    ], headlines


async def test_a_link_that_is_not_http_is_not_published(
    tmp_path: Path, monkeypatch: Any
) -> None:
    feed = FakeFeed(
        {
            "SPY": [
                story("good", "https://e.test/good"),
                story("evil", "javascript:alert(1)"),
                story("relative", "/somewhere"),
                {"headline": "", "url": "https://e.test/blank", "published_at": None},
            ]
        }
    )
    async with news_client(tmp_path, monkeypatch, feed) as (client, _app):
        body = (await client.get(ROUTE)).json()

    assert [row["headline"] for row in body["items"]] == ["good"]


async def test_limit_slices_the_merged_list_and_count_follows(
    tmp_path: Path, monkeypatch: Any
) -> None:
    rows = [story(f"S{index}", f"https://e.test/{index}", published=NEWEST - timedelta(minutes=index)) for index in range(30)]
    feed = FakeFeed({"SPY": rows, "QQQ": []})
    async with news_client(tmp_path, monkeypatch, feed) as (client, _app):
        body = (await client.get(f"{ROUTE}?limit=5")).json()

    assert body["count"] == 5
    assert [row["headline"] for row in body["items"]] == ["S0", "S1", "S2", "S3", "S4"]


# ---------------------------------------------------------------------------
# Failure, coverage, cache
# ---------------------------------------------------------------------------


async def test_a_dead_feed_is_named_and_does_not_sink_the_document(
    tmp_path: Path, monkeypatch: Any
) -> None:
    feed = FakeFeed({"SPY": [story("One", "https://e.test/1")]})
    feed.failed = {"AAPL", "NVDA"}
    async with news_client(tmp_path, monkeypatch, feed) as (client, _app):
        response = await client.get(ROUTE)

    assert response.status_code == 200
    assert response.json()["provider_errors"] == ["AAPL", "NVDA"]


async def test_every_feed_down_is_503_not_an_empty_list(
    tmp_path: Path, monkeypatch: Any
) -> None:
    feed = FakeFeed({})
    feed.failed = {"SPY", "QQQ", "DIA", "IWM", "AAPL", "NVDA", "MSFT", "AMZN", "META", "GOOGL", "TSLA", "JPM"}
    async with news_client(tmp_path, monkeypatch, feed) as (client, _app):
        response = await client.get(ROUTE)

    assert response.status_code == 503
    assert response.json()["error"] == "news_unavailable"


async def test_two_reads_inside_the_window_ask_each_feed_once(
    tmp_path: Path, monkeypatch: Any
) -> None:
    feed = FakeFeed({"SPY": [story("One", "https://e.test/1")]})
    async with news_client(tmp_path, monkeypatch, feed) as (client, _app):
        first = await client.get(ROUTE)
        second = await client.get(ROUTE)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    # Twelve feeds, one build: the second read was answered from the merge.
    assert len(feed.calls) == 12, feed.calls


async def test_the_tag_revalidates_before_any_feed_is_read(
    tmp_path: Path, monkeypatch: Any
) -> None:
    feed = FakeFeed({"SPY": [story("One", "https://e.test/1")]})
    async with news_client(tmp_path, monkeypatch, feed) as (client, _app):
        first = await client.get(ROUTE)
        tag = first.headers["etag"]
        calls_after_first = len(feed.calls)
        second = await client.get(ROUTE, headers={"If-None-Match": tag})

    assert second.status_code == 304
    assert second.content == b""
    assert len(feed.calls) == calls_after_first, "a 304 must not read a feed"


async def test_head_carries_the_get_headers_and_length_without_a_body(
    tmp_path: Path, monkeypatch: Any
) -> None:
    feed = FakeFeed({"SPY": [story("One", "https://e.test/1")]})
    async with news_client(tmp_path, monkeypatch, feed) as (client, _app):
        get = await client.get(ROUTE)
        head = await client.head(ROUTE)

    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(get.content))
    assert head.headers["etag"] == get.headers["etag"]
    assert head.headers["cache-control"] == "no-cache"


async def test_the_body_is_valid_json_and_carries_the_twelve_feeds(
    tmp_path: Path, monkeypatch: Any
) -> None:
    feed = FakeFeed({"SPY": [story("One", "https://e.test/1")]})
    async with news_client(tmp_path, monkeypatch, feed) as (client, _app):
        response = await client.get(ROUTE)

    body = json.loads(response.content)
    assert body["symbols"] == [
        "SPY",
        "QQQ",
        "DIA",
        "IWM",
        "AAPL",
        "NVDA",
        "MSFT",
        "AMZN",
        "META",
        "GOOGL",
        "TSLA",
        "JPM",
    ]
