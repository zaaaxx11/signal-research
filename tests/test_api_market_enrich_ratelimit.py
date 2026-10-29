"""Contract for the per-caller rate limit on ``POST /market/enrich``.

The route is the premium on-demand trigger: unauthenticated-in-dev, keyed in
production, and — before this contract — unbounded. A paid caller could loop it
and burn the Yahoo quota the daily rotation needs, so the budget is part of the
product, not an operational afterthought.

The rules these tests pin:

* ``enrich_rate_limit`` requests per ``enrich_rate_window_s`` seconds per
  caller; the first ``N`` pass and request ``N + 1`` answers **429** with a
  ``Retry-After`` header and the house envelope ``enrich_rate_limited``;
* the caller is the ``X-Enrich-Key`` value when ``enrich_api_key`` is set,
  else the client host — two callers never share a budget;
* a request refused for a bad key (403) costs no token: a caller probing the
  door cannot exhaust its own budget with wrong keys;
* the bucket refills over the window, so a throttled caller recovers without a
  restart;
* the limiter is process-owned (``app.state.enrich_limiter``) and prunes idle
  callers, so its map is bounded by live traffic, not by uptime.
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


class _Clock:
    """A monotonic clock a test can move by hand."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


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


def _settings(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "enrich_max_symbols": 50,
        "enrich_api_key": "secret",
        "enrich_rate_limit": 3.0,
        "enrich_rate_window_s": 60.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _app(
    *, collector: Any = None, settings: Any = None, limiter: Any = None
) -> FastAPI:
    app = FastAPI()
    app.include_router(market_api.router)

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error, "errorMessage": exc.message},
        )

    app.state.runtime = SimpleNamespace(settings=settings or _settings())
    app.state.depth = collector
    if limiter is not None:
        app.state.enrich_limiter = limiter
    return app


def _limiter(clock: _Clock, *, limit: float, window_s: float) -> Any:
    """Build the route's limiter with a deterministic clock.

    ``getattr`` + ``assert`` keeps the RED run an AssertionError (the contract
    is missing) rather than an ImportError at collection time.
    """

    builder = getattr(market_api, "CallerRateLimiter", None)
    assert builder is not None, "app.api.market must expose CallerRateLimiter"
    return builder(limit=limit, window_s=window_s, clock=clock)


def _caller(request: Any, settings: Any) -> str:
    resolver = getattr(market_api, "enrich_caller", None)
    assert resolver is not None, "app.api.market must expose enrich_caller"
    return resolver(request, settings)


def _wire(monkeypatch: pytest.MonkeyPatch, collector: Any) -> None:
    monkeypatch.setattr(market_api, "depth_collector_of", lambda _request: collector)


def _fake_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.market import enrichment

    class _Stage:
        def __init__(self, *, sources: Any = ("yahoo", "news"), **_: Any) -> None:
            self.sources = tuple(sources)
            self.last_errors: dict[str, str] = {}
            self.batch_size = None

        async def run(self, symbols: list[str]) -> dict[str, dict]:
            return {symbol: {"target_price": 1.0} for symbol in symbols}

        def _take_batch(self, symbols: list[str]) -> list[str]:
            return symbols[:2]

    monkeypatch.setattr(enrichment, "EnrichmentStage", _Stage)


def _post(client: TestClient, headers: dict[str, str] | None = None) -> Any:
    return client.post(
        "/market/enrich",
        json={"symbols": ["AAPL"], "sources": ["yahoo"]},
        headers=headers,
    )


def test_the_first_n_requests_pass_and_n_plus_one_is_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_stage(monkeypatch)
    clock = _Clock()
    collector = _Collector(["AAPL"])
    _wire(monkeypatch, collector)
    limiter = _limiter(clock, limit=3.0, window_s=60.0)
    client = TestClient(
        _app(
            collector=collector,
            settings=_settings(enrich_rate_limit=3.0),
            limiter=limiter,
        )
    )
    headers = {"X-Enrich-Key": "secret"}

    for _ in range(3):
        passed = _post(client, headers)
        assert passed.status_code == 200, passed.text

    refused = _post(client, headers)

    assert refused.status_code == 429
    body = refused.json()
    assert set(body) == {"error", "errorMessage"}
    assert body["error"] == "enrich_rate_limited"
    # 3 requests / 60 s → one token every 20 s. The clock has not moved.
    assert refused.headers["Retry-After"] == "20"
    assert collector.persisted == 3, "a refused request must not enrich"


def test_two_keyless_callers_have_independent_budgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_stage(monkeypatch)
    collector = _Collector(["AAPL"])
    _wire(monkeypatch, collector)
    clock = _Clock()
    app = _app(
        collector=collector,
        settings=_settings(enrich_api_key=None, enrich_rate_limit=2.0),
        limiter=_limiter(clock, limit=2.0, window_s=60.0),
    )
    alice = TestClient(app, client=("10.0.0.1", 40001))
    bob = TestClient(app, client=("10.0.0.2", 40002))

    assert _post(alice).status_code == 200
    assert _post(alice).status_code == 200
    assert _post(alice).status_code == 429

    # Bob's host is a different caller: Alice's exhaustion is not his.
    assert _post(bob).status_code == 200
    assert _post(bob).status_code == 200
    assert _post(bob).status_code == 429


def test_two_different_keys_have_independent_budgets() -> None:
    clock = _Clock()
    limiter = _limiter(clock, limit=2.0, window_s=60.0)

    assert limiter.check("key-alpha") == (True, 0.0)
    assert limiter.check("key-alpha") == (True, 0.0)
    allowed, retry_after = limiter.check("key-alpha")
    assert allowed is False
    assert retry_after > 0.0

    assert limiter.check("key-beta") == (True, 0.0), "a second key has its own bucket"


def test_the_key_header_names_the_caller_when_a_key_is_configured() -> None:
    settings = _settings(enrich_api_key="secret")
    keyed = SimpleNamespace(
        headers={"x-enrich-key": "secret"}, client=SimpleNamespace(host="10.0.0.1")
    )
    assert _caller(keyed, settings) == "secret"

    keyless = SimpleNamespace(
        headers={}, client=SimpleNamespace(host="10.0.0.7"), settings=settings
    )
    assert _caller(keyless, _settings(enrich_api_key=None)) == "10.0.0.7"


def test_a_rejected_key_does_not_consume_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_stage(monkeypatch)
    collector = _Collector(["AAPL"])
    _wire(monkeypatch, collector)
    clock = _Clock()
    client = TestClient(
        _app(
            collector=collector,
            settings=_settings(enrich_api_key="secret", enrich_rate_limit=2.0),
            limiter=_limiter(clock, limit=2.0, window_s=60.0),
        )
    )

    for _ in range(5):
        refused = _post(client, {"X-Enrich-Key": "wrong"})
        assert refused.status_code == 403
        assert refused.json()["error"] == "enrich_forbidden"

    # Two good requests still fit: the five 403s cost nothing.
    assert _post(client, {"X-Enrich-Key": "secret"}).status_code == 200
    assert _post(client, {"X-Enrich-Key": "secret"}).status_code == 200
    assert _post(client, {"X-Enrich-Key": "secret"}).status_code == 429


def test_the_bucket_refills_after_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_stage(monkeypatch)
    collector = _Collector(["AAPL"])
    _wire(monkeypatch, collector)
    clock = _Clock()
    client = TestClient(
        _app(
            collector=collector,
            settings=_settings(enrich_rate_limit=2.0, enrich_rate_window_s=60.0),
            limiter=_limiter(clock, limit=2.0, window_s=60.0),
        )
    )
    headers = {"X-Enrich-Key": "secret"}

    assert _post(client, headers).status_code == 200
    assert _post(client, headers).status_code == 200
    refused = _post(client, headers)
    assert refused.status_code == 429
    # 2 requests / 60 s → one token every 30 s.
    assert refused.headers["Retry-After"] == "30"

    clock.advance(60.0)

    assert _post(client, headers).status_code == 200
    assert _post(client, headers).status_code == 200
    assert _post(client, headers).status_code == 429


def test_the_limiter_is_process_owned_and_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_stage(monkeypatch)
    collector = _Collector(["AAPL"])
    _wire(monkeypatch, collector)
    app = _app(
        collector=collector, settings=_settings(enrich_rate_limit=1.0)
    )
    first = TestClient(app, client=("10.9.9.9", 50001))

    assert _post(first, {"X-Enrich-Key": "secret"}).status_code == 200
    owned = getattr(app.state, "enrich_limiter", None)
    assert owned is not None, "the bucket map must live on app.state"

    # A second client with the same identity shares the same one-token budget.
    second = TestClient(app, client=("10.9.9.9", 50002))
    assert _post(second, {"X-Enrich-Key": "secret"}).status_code == 429


def test_idle_buckets_are_pruned() -> None:
    clock = _Clock()
    limiter = _limiter(clock, limit=5.0, window_s=60.0)

    for index in range(200):
        assert limiter.check(f"caller-{index}") == (True, 0.0)
    assert len(limiter) == 200

    clock.advance(60.0)
    assert limiter.check("fresh") == (True, 0.0)

    # Every caller idle for a whole window is full again, so its state carries
    # no information: only the caller that just spoke is retained.
    assert len(limiter) == 1
