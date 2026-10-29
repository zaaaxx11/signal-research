"""Contract for ``POST /market/enrich`` — the on-demand (premium) trigger.

The free baseline is the rotation: 500 symbols per cycle, cursor persisted.
This route is the paid shape: "do it now, for these symbols", bypassing the
cursor and the interval, merging into the depth store and persisting.

The rules the tests pin:

* an unset ``enrich_api_key`` leaves the route open (a dev box), a set one
  demands a matching ``X-Enrich-Key`` and answers 403 otherwise;
* ``sources`` is validated before any work is spent;
* the requested symbol list is capped, so a paid trigger cannot crawl the
  universe;
* the result is merged with priority 0 — an explicit request is the freshest
  evidence there is — and persisted;
* a collector that is not running is a 503, never a silent success.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api import market as market_api
from app.auth import ApiError
from app.market.depth import DepthObservation


class _Store:
    def __init__(self) -> None:
        self.merged: list[tuple[DepthObservation, int]] = []

    def merge(self, observation: DepthObservation, *, priority: int) -> int:
        self.merged.append((observation, priority))
        return len(observation.fields)


class _Collector:
    def __init__(self, symbols: list[str]) -> None:
        self.symbols = symbols
        self.store = _Store()
        self.persisted = 0

    async def persist(self) -> int:
        self.persisted += 1
        return 1


def _app(*, collector: Any = None, settings: Any = None) -> FastAPI:
    app = FastAPI()
    app.include_router(market_api.router)

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error, "errorMessage": exc.message},
        )
    app.state.runtime = SimpleNamespace(
        settings=settings or SimpleNamespace(enrich_max_symbols=50)
    )
    app.state.depth = collector
    return app


def _wire(monkeypatch: pytest.MonkeyPatch, collector: Any) -> None:
    """The route resolves the collector through this accessor; make it answer."""

    monkeypatch.setattr(
        market_api, "depth_collector_of", lambda _request: collector
    )


def _fake_stage(monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> None:
    from app.market import enrichment

    class _Stage:
        def __init__(self, *, sources: Any = ("yahoo", "news"), **_: Any) -> None:
            self.sources = tuple(sources)
            self.last_errors: dict[str, str] = {"yahoo": "429"} if fail else {}
            self.batch_size = None

        async def run(self, symbols: list[str]) -> dict[str, dict]:
            if fail:
                return {}
            return {symbol: {"target_price": 1.0} for symbol in symbols}

        def _take_batch(self, symbols: list[str]) -> list[str]:
            return symbols[:2]

    monkeypatch.setattr(enrichment, "EnrichmentStage", _Stage)


def test_an_explicit_request_merges_and_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_stage(monkeypatch)
    collector = _Collector(["AAPL", "MSFT"])
    _wire(monkeypatch, collector)
    client = TestClient(_app(collector=collector))

    response = client.post(
        "/market/enrich", json={"symbols": ["AAPL"], "sources": ["yahoo"]}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["requested"] == 1
    assert body["enriched"] == 1
    assert body["sources"] == ["yahoo"]
    assert collector.store.merged, "the value landed in the store"
    assert collector.store.merged[0][1] == 0, "priority 0: freshest evidence"
    assert collector.persisted == 1


def test_an_unset_key_leaves_the_route_open(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_stage(monkeypatch)
    collector = _Collector(["AAPL"])
    _wire(monkeypatch, collector)
    client = TestClient(_app(collector=collector))
    response = client.post("/market/enrich", json={"symbols": ["AAPL"]})
    assert response.status_code == 200
    assert response.json()["tier"] == "open"


def test_a_set_key_demands_the_header(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_stage(monkeypatch)
    settings = SimpleNamespace(enrich_max_symbols=50, enrich_api_key="secret")
    collector = _Collector(["AAPL"])
    _wire(monkeypatch, collector)
    client = TestClient(_app(collector=collector, settings=settings))

    missing = client.post("/market/enrich", json={"symbols": ["AAPL"]})
    assert missing.status_code == 403
    assert missing.json()["error"] == "enrich_forbidden"

    wrong = client.post(
        "/market/enrich", json={"symbols": ["AAPL"]}, headers={"X-Enrich-Key": "nope"}
    )
    assert wrong.status_code == 403

    right = client.post(
        "/market/enrich",
        json={"symbols": ["AAPL"]},
        headers={"X-Enrich-Key": "secret"},
    )
    assert right.status_code == 200
    assert right.json()["tier"] == "premium"


def test_unknown_sources_are_rejected_before_any_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_stage(monkeypatch)
    client = TestClient(_app(collector=_Collector(["AAPL"])))
    response = client.post(
        "/market/enrich", json={"symbols": ["AAPL"], "sources": ["bogus"]}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_sources"


def test_the_symbol_ceiling_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_stage(monkeypatch)
    settings = SimpleNamespace(enrich_max_symbols=2)
    client = TestClient(_app(collector=_Collector(["A", "B", "C"]), settings=settings))
    response = client.post("/market/enrich", json={"symbols": ["A", "B", "C"]})
    assert response.status_code == 422
    assert response.json()["error"] == "too_many_symbols"


def test_no_collector_is_a_503(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_stage(monkeypatch)
    client = TestClient(_app(collector=None))
    response = client.post("/market/enrich", json={"symbols": ["AAPL"]})
    assert response.status_code == 503
    assert response.json()["error"] == "depth_unavailable"


def test_a_refused_pass_reports_its_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_stage(monkeypatch, fail=True)
    collector = _Collector(["AAPL"])
    _wire(monkeypatch, collector)
    client = TestClient(_app(collector=collector))
    response = client.post("/market/enrich", json={"symbols": ["AAPL"]})
    assert response.status_code == 200
    assert response.json()["enriched"] == 0
    assert response.json()["errors"] == {"yahoo": "429"}
    assert collector.persisted == 0, "nothing merged, nothing written"
