"""ETag on the data routes: a poll that changes nothing costs no body.

A watchlist client asks the same question every fifteen seconds and, most of
the time, the answer is identical. Without a validator the server re-serialises
and re-sends the same bytes; with one it answers `304 Not Modified` and an empty
body.

The validator is the store's **revision**, not a hash of the response: a depth
cycle bumps it when a cell actually changes, so the tag is correct without
walking 4,217 symbols, and a poll costs one integer comparison.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api import market as market_api
from app.api.cache import etag_for, matches
from app.auth import ApiError


# -- the helper ---------------------------------------------------------------


def test_an_etag_is_quoted_and_deterministic() -> None:
    first = etag_for("depth", 7, "AAPL,MSFT")
    assert first == etag_for("depth", 7, "AAPL,MSFT")
    assert first.startswith('"') and first.endswith('"')
    assert first != etag_for("depth", 8, "AAPL,MSFT"), "a new revision is a new tag"
    assert first != etag_for("depth", 7, "MSFT,AAPL"), "a different question is a new tag"
    assert first != etag_for("hub", 7, "AAPL,MSFT"), "namespaces never collide"


def test_if_none_match_accepts_the_forms_clients_send() -> None:
    tag = etag_for("depth", 3, "AAPL")
    assert matches(tag, tag)
    assert matches(tag, "*")
    assert matches(tag, f'"other", {tag}')
    assert matches(tag, f"W/{tag}"), "a weak comparison is still a match here"
    assert not matches(tag, '"stale"')
    assert not matches(tag, None)
    assert not matches(tag, "")


# -- the routes ---------------------------------------------------------------


class _Store:
    def __init__(self, revision: int = 1) -> None:
        self.revision = revision


class _Collector:
    def __init__(self, revision: int = 1) -> None:
        self.store = _Store(revision)

    @property
    def ready(self) -> bool:
        return True

    def document(self, symbols: Any) -> dict[str, Any]:
        wanted = [str(symbol) for symbol in symbols]
        return {
            "as_of": "2026-10-30T00:00:00+00:00",
            "symbols": {symbol: {"pe": 20.0} for symbol in wanted},
            "count": len(wanted),
            "missing": [],
        }


class _Hub:
    def __init__(self, revision: int = 1) -> None:
        self.store = _Store(revision)

    @property
    def ready(self) -> bool:
        return True

    def document(self, symbols: Any) -> dict[str, Any]:
        wanted = [str(symbol) for symbol in symbols]
        return {
            "as_of": "2026-10-30T00:00:00+00:00",
            "quotes": {symbol: {"last": 100.0} for symbol in wanted},
            "count": len(wanted),
            "missing": [],
        }


def _app(
    monkeypatch: pytest.MonkeyPatch, *, collector: Any = None, hub: Any = None
) -> FastAPI:
    app = FastAPI()
    app.include_router(market_api.router)
    app.state.runtime = SimpleNamespace(
        settings=SimpleNamespace(quote_max_symbols=100, public_mode=False)
    )
    # monkeypatch, not assignment: a module-level lambda outlives this file and
    # silently rewires every later test that touches these routes.
    monkeypatch.setattr(market_api, "depth_collector_of", lambda _request: collector)
    monkeypatch.setattr(market_api, "quote_hub_of", lambda _request: hub)

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error, "errorMessage": exc.message},
        )

    return app


def test_depth_answers_304_with_no_body_when_the_revision_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = _Collector(revision=5)
    client = TestClient(_app(monkeypatch, collector=collector))

    first = client.get("/market/depth?symbols=AAPL")
    assert first.status_code == 200
    tag = first.headers["etag"]
    assert tag
    assert "cache-control" in first.headers
    assert first.json()["symbols"]["AAPL"]["pe"] == 20.0

    second = client.get("/market/depth?symbols=AAPL", headers={"If-None-Match": tag})
    assert second.status_code == 304
    assert second.content == b"", "a 304 carries no body"
    assert second.headers["etag"] == tag


def test_a_new_revision_defeats_the_cached_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = _Collector(revision=5)
    client = TestClient(_app(monkeypatch, collector=collector))
    tag = client.get("/market/depth?symbols=AAPL").headers["etag"]

    collector.store.revision = 6
    fresh = client.get("/market/depth?symbols=AAPL", headers={"If-None-Match": tag})

    assert fresh.status_code == 200
    assert fresh.headers["etag"] != tag


def test_a_different_symbol_set_is_a_different_entity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(_app(monkeypatch, collector=_Collector(revision=5)))
    tag = client.get("/market/depth?symbols=AAPL").headers["etag"]

    other = client.get("/market/depth?symbols=MSFT", headers={"If-None-Match": tag})

    assert other.status_code == 200, "the tag named another question"


def test_a_restart_with_different_data_is_not_a_304(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Revision restarts at zero, so the identity needs the data's own clock.

    Without ``as_of`` in the key, a client holding ``r0`` from before a restart
    would be told "not modified" against a freshly seeded store that happens to
    be at revision zero again — and would keep serving the old values.
    """

    collector = _Collector(revision=0)
    client = TestClient(_app(monkeypatch, collector=collector))
    tag = client.get("/market/depth?symbols=AAPL").headers["etag"]

    collector.document = lambda symbols: {  # type: ignore[assignment]
        **_Collector.document(collector, symbols),
        "as_of": "2026-10-30T06:00:00+00:00",
    }
    fresh = client.get("/market/depth?symbols=AAPL", headers={"If-None-Match": tag})

    assert fresh.status_code == 200, "same revision, newer data is a new entity"
    assert fresh.headers["etag"] != tag


def test_an_unknown_tag_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(_app(monkeypatch, collector=_Collector(revision=5)))
    response = client.get(
        "/market/depth?symbols=AAPL", headers={"If-None-Match": '"nonsense"'}
    )
    assert response.status_code == 200


def test_the_hub_carries_the_same_discipline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(_app(monkeypatch, collector=_Collector(revision=2), hub=_Hub(revision=9)))

    hub = client.get("/market/hub?symbols=AAPL")
    assert hub.status_code == 200
    assert hub.headers["etag"]
    cached = client.get(
        "/market/hub?symbols=AAPL", headers={"If-None-Match": hub.headers["etag"]}
    )
    assert cached.status_code == 304
