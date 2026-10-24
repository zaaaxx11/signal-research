"""P12 RED — ``GET /news/{symbol}`` through the ASGI app.

The service contract lives in ``test_news_service.py``; this module pins the
**route**: the JSON shape, the three status codes, the injected service on
``app.state``, and the fifteen-minute cache surviving across requests.

* default: ``200`` + ``{symbol, as_of, count, latest_published_at, items,
  provider_errors}`` with eight headlines and **no per-source columns**;
* ``?limit=`` slices the same cached pool (1..50; 0 and 51 are 422);
* an invalid symbol is a ``422`` in the house ``{error, errorMessage}`` envelope,
  before any provider is called;
* two healthy wires with nothing to report answer ``200`` with ``count=0`` — an
  empty panel is not an error;
* one broken wire is a ``200`` with a ``provider_errors`` cell;
* every wire broken is a ``503`` (a 200 would claim "no news exists");
* a second request inside the window sends zero provider calls.

Every provider here is a fake; the suite's autouse guard in ``conftest.py``
fails the test if anything tries to open a socket.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Sequence

from app.news.rss import NewsItem
from app.news.service import NewsService

DOCUMENT_KEYS = {
    "symbol",
    "as_of",
    "count",
    "latest_published_at",
    "items",
    "provider_errors",
}
ITEM_KEYS = {"headline", "url", "source", "published_at", "source_url"}
ROUTE = "/news/{symbol}"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class FakeProvider:
    """A wire that answers a fixed list and counts every question."""

    def __init__(
        self,
        name: str,
        items: Sequence[NewsItem] = (),
        *,
        error: BaseException | None = None,
    ) -> None:
        self.name = name
        self._items = list(items)
        self._error = error
        self.calls: list[str] = []

    async def fetch(self, symbol: str) -> Sequence[NewsItem]:
        self.calls.append(symbol)
        if self._error is not None:
            raise self._error
        return list(self._items)


def item(
    headline: str,
    url: str,
    *,
    source: str = "Newswire",
    published: datetime | None = datetime(2026, 10, 23, 12, 0, tzinfo=UTC),
    source_url: str | None = None,
) -> NewsItem:
    return NewsItem(
        headline=headline,
        url=url,
        source=source,
        published_at=published,
        source_url=source_url,
    )


def headlines(count: int) -> list[NewsItem]:
    """``count`` distinct stories, newest first."""

    newest = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    return [
        item(f"Story {index}", f"https://e.test/{index}", published=newest - timedelta(minutes=index))
        for index in range(count)
    ]


@asynccontextmanager
async def news_client(
    tmp_path: Path,
    monkeypatch: Any,
    providers: Sequence[Any],
    **service_kwargs: Any,
) -> AsyncIterator[Any]:
    """The real app, with the news service replaced by injected providers."""

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))

    from httpx2 import ASGITransport, AsyncClient

    from app.main import create_app

    application = create_app()
    application.state.news = NewsService(providers, **service_kwargs)
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://mee.test") as client:
        yield client, application


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


async def test_the_route_is_mounted_in_the_flat_route_table(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with news_client(tmp_path, monkeypatch, [FakeProvider("google_news", headlines(1))]) as (
        _client,
        application,
    ):
        paths = {getattr(route, "path", None) for route in application.routes}

    assert ROUTE in paths, (
        "create_app mounts the news router flat so an operator (and this test) "
        f"can introspect it, got {sorted(path for path in paths if path)}"
    )


async def test_the_default_answer_is_one_row_of_eight_headlines(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with news_client(
        tmp_path,
        monkeypatch,
        [FakeProvider("google_news", headlines(10)), FakeProvider("gdelt", [])],
    ) as (client, _application):
        response = await client.get("/news/RHI")

    assert response.status_code == 200, response.text
    document = response.json()
    assert set(document) == DOCUMENT_KEYS
    assert document["symbol"] == "RHI"
    assert document["count"] == 8
    assert len(document["items"]) == 8
    assert document["latest_published_at"] == "2026-10-23T12:00:00+00:00"
    assert document["provider_errors"] == []
    for row in document["items"]:
        assert set(row) == ITEM_KEYS, (
            "each headline carries its own provenance; there is no per-source block"
        )
    assert "google_news" not in response.text and "gdelt" not in response.text


async def test_the_symbol_is_normalised_from_the_path(
    tmp_path: Path, monkeypatch: Any
) -> None:
    google = FakeProvider("google_news", headlines(1))
    async with news_client(tmp_path, monkeypatch, [google]) as (client, _application):
        response = await client.get("/news/rhi")

    assert response.status_code == 200
    assert response.json()["symbol"] == "RHI"
    assert google.calls == ["RHI"], "the service is asked for the ticker, not the raw token"


async def test_the_limit_query_slices_the_cached_pool(
    tmp_path: Path, monkeypatch: Any
) -> None:
    google = FakeProvider("google_news", headlines(12))
    async with news_client(tmp_path, monkeypatch, [google]) as (client, _application):
        wide = await client.get("/news/RHI", params={"limit": 8})
        narrow = await client.get("/news/RHI", params={"limit": 3})

    assert wide.json()["count"] == 8
    assert narrow.json()["count"] == 3
    assert narrow.json()["items"] == wide.json()["items"][:3]
    assert google.calls == ["RHI"], "the second read is a cache hit at a new limit"


async def test_the_configured_default_limit_is_honoured_when_limit_is_omitted(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """``MEE_NEWS_LIMIT`` sets the panel; the route must not hard-code eight."""

    google = FakeProvider("google_news", headlines(9))
    async with news_client(tmp_path, monkeypatch, [google], limit=5) as (client, _application):
        omitted = await client.get("/news/RHI")
        explicit = await client.get("/news/RHI", params={"limit": 3})

    assert omitted.json()["count"] == 5
    assert explicit.json()["count"] == 3
    assert google.calls == ["RHI"]


async def test_latest_published_at_is_the_first_item_s_timestamp(
    tmp_path: Path, monkeypatch: Any
) -> None:
    stories = headlines(4)
    async with news_client(tmp_path, monkeypatch, [FakeProvider("google_news", stories)]) as (
        client,
        _application,
    ):
        response = await client.get("/news/RHI", params={"limit": 2})

    document = response.json()
    assert document["latest_published_at"] == document["items"][0]["published_at"]
    assert document["count"] == 2


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_an_invalid_symbol_is_a_422_in_the_house_envelope(
    tmp_path: Path, monkeypatch: Any
) -> None:
    google = FakeProvider("google_news", headlines(1))
    async with news_client(tmp_path, monkeypatch, [google]) as (client, _application):
        response = await client.get("/news/AAPL%20X")

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "invalid_symbol"
    assert "AAPL X" in body["errorMessage"]
    assert google.calls == [], "a malformed ticker must not cost a provider request"


async def test_a_limit_outside_the_range_is_a_422(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with news_client(
        tmp_path, monkeypatch, [FakeProvider("google_news", headlines(1))]
    ) as (client, _application):
        zero = await client.get("/news/RHI", params={"limit": 0})
        too_wide = await client.get("/news/RHI", params={"limit": 51})
        not_a_number = await client.get("/news/RHI", params={"limit": "many"})

    assert zero.status_code == 422
    assert too_wide.status_code == 422
    assert not_a_number.status_code == 422


# ---------------------------------------------------------------------------
# Empty and failure
# ---------------------------------------------------------------------------


async def test_no_news_is_a_200_with_an_empty_panel(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with news_client(
        tmp_path,
        monkeypatch,
        [FakeProvider("google_news", []), FakeProvider("gdelt", [])],
    ) as (client, _application):
        response = await client.get("/news/ZZZZ")

    assert response.status_code == 200, response.text
    document = response.json()
    assert document["count"] == 0
    assert document["items"] == []
    assert document["latest_published_at"] is None
    assert document["provider_errors"] == []


async def test_one_broken_wire_is_reported_beside_the_news(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with news_client(
        tmp_path,
        monkeypatch,
        [
            FakeProvider("google_news", headlines(2)),
            FakeProvider("gdelt", error=RuntimeError("gdelt answered 429")),
        ],
    ) as (client, _application):
        response = await client.get("/news/RHI")

    assert response.status_code == 200, response.text
    document = response.json()
    assert document["count"] == 2
    assert document["provider_errors"] == [
        {"provider": "gdelt", "error": "RuntimeError: gdelt answered 429"}
    ]


async def test_every_wire_failing_is_a_503(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with news_client(
        tmp_path,
        monkeypatch,
        [
            FakeProvider("google_news", error=RuntimeError("google answered 503")),
            FakeProvider("gdelt", error=RuntimeError("gdelt answered 429")),
        ],
    ) as (client, _application):
        response = await client.get("/news/RHI")

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["error"] == "news_unavailable"
    assert "failed" in body["errorMessage"]
    assert "google answered 503" in body["errorMessage"]
    assert body["errorMessage"].startswith("every news provider failed for RHI")


# ---------------------------------------------------------------------------
# The cache is a property of the process
# ---------------------------------------------------------------------------


async def test_two_requests_inside_the_window_send_zero_provider_calls(
    tmp_path: Path, monkeypatch: Any
) -> None:
    google = FakeProvider("google_news", headlines(3))
    gdelt = FakeProvider("gdelt", [item("Wire story", "https://e.test/wire")])
    async with news_client(tmp_path, monkeypatch, [google, gdelt]) as (client, _application):
        first = await client.get("/news/RHI")
        second = await client.get("/news/RHI")

    assert first.status_code == second.status_code == 200
    assert google.calls == ["RHI"] and gdelt.calls == ["RHI"], (
        "the fifteen-minute cache is attached to the app, not to the request"
    )
    assert first.json() == second.json(), "a cached document is served verbatim"


async def test_the_cache_does_not_leak_between_symbols(
    tmp_path: Path, monkeypatch: Any
) -> None:
    google = FakeProvider("google_news", headlines(2))
    async with news_client(tmp_path, monkeypatch, [google]) as (client, _application):
        await client.get("/news/RHI")
        await client.get("/news/AAPL")

    assert google.calls == ["RHI", "AAPL"]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


async def test_the_document_is_served_by_the_service_the_app_built(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """``create_app`` attaches a NewsService; the route reads that object."""

    from app.main import create_app
    from app.news.service import NewsService as Service

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))

    application = create_app()

    assert isinstance(application.state.news, Service)
    assert application.state.news.default_limit == 8
    assert application.state.news.cache_ttl_s == 900
