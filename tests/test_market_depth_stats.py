"""Contract for ``GET /market/depth/stats`` — coverage, read from the store only.

The route answers an aggregate document (no symbol rows, no values), so a
monitoring scrape cannot be mistaken for a market read and never calls a
provider. A collector that is absent or has an empty store is a ``503`` in the
house envelope. When the filled-catalog count falls below
``settings.depth_coverage_floor`` the route emits exactly one WARNING per
degraded cycle — that is the journalctl line an operator greps for after a bad
night.

The fixture store uses catalog field names only; the coverage rules are:
``fields_filled`` counts distinct *catalog* keys with at least one value, the
per-symbol extremes count catalog cells, and ``by_source`` counts the same
cells by the source that supplied them.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Sequence

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api import market as market_api
from app.auth import ApiError
from app.config import Settings
from app.market.depth import DepthCollector, DepthCycleResult, DepthObservation

T0 = datetime(2026, 10, 29, 3, 0, tzinfo=UTC)


class _NoopSource:
    """A source that answers nothing; the tests merge into the store directly."""

    name = "noop"

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def fetch_many(self, symbols: Sequence[str]) -> dict[str, DepthObservation]:
        self.calls.append(tuple(symbols))
        return {}


def _collector(symbols: Sequence[str] = ("AAPL", "MSFT")) -> DepthCollector:
    return DepthCollector(symbols=symbols, sources=(_NoopSource(),), store_path=None)


def _merge(
    collector: DepthCollector,
    symbol: str,
    fields: dict[str, Any],
    *,
    source: str = "finviz",
    priority: int = 0,
    as_of: datetime = T0,
) -> None:
    collector.store.merge(
        DepthObservation(symbol=symbol, fields=fields, as_of=as_of, source=source),
        priority=priority,
    )


def _cycle(started_at: datetime) -> DepthCycleResult:
    """A finished cycle the route can date its warning by."""

    return DepthCycleResult(
        requested=2, returned=1, errors=0, started_at=started_at, seconds=0.1
    )


def _app(collector: DepthCollector | None, *, floor: int = 60) -> FastAPI:
    application = FastAPI()
    application.include_router(market_api.router)

    @application.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error, "errorMessage": exc.message},
        )

    application.state.runtime = SimpleNamespace(
        settings=SimpleNamespace(depth_coverage_floor=floor)
    )
    application.state.depth = collector
    return application


def test_stats_document_shape_and_numbers() -> None:
    """One aggregate document: counts, extremes, per-source cells, newest stamp."""

    collector = _collector()
    _merge(collector, "AAPL", {"pe": 28.0, "roe": 40.0, "market_cap": 3.0e12})
    _merge(collector, "MSFT", {"pe": 35.0, "beta": 1.1}, source="yahoo", priority=1)

    response = TestClient(_app(collector)).get("/market/depth/stats")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "as_of": T0.isoformat(),
        "symbols": 2,
        "fields_filled": 4,
        "median_fields_per_symbol": 2.5,
        "min_fields": 2,
        "max_fields": 3,
        "by_source": {"finviz": 3, "yahoo": 2},
    }


def test_stats_counts_only_catalog_keys() -> None:
    """A foreign key is not coverage: it is neither filled nor attributed."""

    collector = _collector(("AAPL",))
    _merge(collector, "AAPL", {"pe": 28.0, "cash_and_equivalents": 1.0})

    response = TestClient(_app(collector)).get("/market/depth/stats")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["fields_filled"] == 1
    assert body["min_fields"] == body["max_fields"] == 1
    assert "cash_and_equivalents" not in str(body)


def test_stats_is_a_503_when_the_collector_is_absent_or_empty() -> None:
    """No collector, and a collector with an empty store, both answer 503."""

    absent = TestClient(_app(None)).get("/market/depth/stats")
    assert absent.status_code == 503
    assert absent.json() == {
        "error": "depth_unavailable",
        "errorMessage": "depth collector is not running or has not completed a refresh",
    }

    empty = TestClient(_app(_collector())).get("/market/depth/stats")
    assert empty.status_code == 503
    assert empty.json()["error"] == "depth_unavailable"


def test_stats_reads_the_store_and_never_refreshes() -> None:
    """The route answers from memory: no source is asked, no cycle is started."""

    collector = _collector()
    _merge(collector, "AAPL", {"pe": 28.0})
    source = collector.sources[0]
    assert isinstance(source, _NoopSource)

    client = TestClient(_app(collector))
    assert client.get("/market/depth/stats").status_code == 200

    assert source.calls == [], "a stats read must never call a provider"
    assert collector.last_cycle is None, "a stats read must never run a cycle"


def test_one_warning_per_degraded_cycle(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A bad cycle is one journal line, not one per scrape."""

    collector = _collector()
    _merge(collector, "AAPL", {"pe": 28.0, "roe": 40.0})
    collector.last_cycle = _cycle(T0)
    client = TestClient(_app(collector))

    caplog.set_level(logging.WARNING, logger="mee.market.depth")
    first = client.get("/market/depth/stats")
    second = client.get("/market/depth/stats")

    assert first.status_code == 200
    assert second.status_code == 200
    degraded = [record for record in caplog.records if "coverage" in record.message]
    assert len(degraded) == 1, "one degraded cycle must produce exactly one warning"
    warning = degraded[0]
    assert warning.levelno == logging.WARNING
    assert "fields_filled=2" in warning.getMessage()
    assert "floor=60" in warning.getMessage()

    # The next cycle is degraded too, so it earns its own line.
    collector.last_cycle = _cycle(T0 + timedelta(days=1))
    assert client.get("/market/depth/stats").status_code == 200
    degraded = [record for record in caplog.records if "coverage" in record.message]
    assert len(degraded) == 2


def test_no_warning_when_coverage_meets_the_floor(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A healthy store is silent; the floor comes from the settings block."""

    collector = _collector()
    _merge(
        collector,
        "AAPL",
        {"pe": 28.0, "roe": 40.0, "market_cap": 3.0e12, "beta": 1.1},
    )
    client = TestClient(_app(collector, floor=4))

    caplog.set_level(logging.WARNING, logger="mee.market.depth")
    assert client.get("/market/depth/stats").status_code == 200

    assert [record for record in caplog.records if "coverage" in record.message] == []


def test_depth_coverage_floor_setting_defaults_to_sixty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The floor is a real setting: 60 unless the deployment overrides it."""

    monkeypatch.delenv("MEE_DEPTH_COVERAGE_FLOOR", raising=False)
    assert getattr(Settings(), "depth_coverage_floor", None) == 60

    monkeypatch.setenv("MEE_DEPTH_COVERAGE_FLOOR", "75")
    assert getattr(Settings(), "depth_coverage_floor", None) == 75
