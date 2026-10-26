"""RED tests for R1 Task 2: public mode is a fail-closed licence boundary.

Public mode is SEC-only. EDGAR identity and facts may answer a public request,
but every quote rung (yahoo, stooq, nasdaq, finnhub, polygon) is ``INTERNAL``:
it may not be fetched and it may not be attached — not even when the caller
passes ``"refresh_quotes": false``. A query that filters **or sorts** on a quote
field is refused with ``503 license_unavailable`` before any fetch and without a
partial page; a query that touches no quote field is served with the frozen
``license_blocked`` sidecar and no quote cell on any row.

The transport seam is ``app.api.engine.quote_engine_of``: every fake below
records its calls, so "the quote engine was never asked" is asserted rather than
inferred. No network, no sleeps and no wall clock — tick timestamps are fixed
datetimes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx2 import ASGITransport, AsyncClient, Response

from app.engine.ingest import EngineDataset
from app.engine.license import LicensePolicy
from app.engine.model import Cell, CellStatus, Provenance, Unit
from app.engine.security import SecurityMaster
from app.engine.service import EngineService
from app.market.quote import QuoteTick

#: The four-symbol SEC identity fixture every test screens over.
_TICKERS_FIXTURE = (
    Path(__file__).with_name("fixtures") / "engine_sec_company_tickers_sample.json"
)

#: Fixed exchange/receive timestamps: a quote test must not read the wall clock.
_TICK_TS = datetime(2026, 10, 26, 14, 30, tzinfo=UTC)
_TICK_RECEIVED = datetime(2026, 10, 26, 14, 30, 1, tzinfo=UTC)

#: The observed quote vocabulary the contract calls ``QUOTE_FIELDS``; none of
#: these keys may appear on a public-mode row.
_QUOTE_ROW_KEYS: tuple[str, ...] = ("price", "change", "volume", "prev_close")


class _ForbiddenQuoteEngine:
    """A quote transport a public request must never reach.

    ``poll_many`` records the attempt *and* raises: the screen route swallows
    quote failures on purpose, so the recorded call — not the exception — is the
    assertion that proves the boundary was crossed.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def poll_many(self, symbols: list[str]) -> dict[str, QuoteTick | None]:
        """Record the forbidden poll and fail the request that caused it."""

        self.calls.append(tuple(symbols))
        raise AssertionError("public mode must not poll an internal quote source")


class _StubQuoteEngine:
    """A private-mode quote transport answering fixed prices and fixed times."""

    def __init__(self, prices: Mapping[str, float]) -> None:
        self._prices = dict(prices)
        self.calls: list[tuple[str, ...]] = []

    async def poll_many(self, symbols: list[str]) -> dict[str, QuoteTick | None]:
        """Return one fresh tick per requested symbol this stub knows."""

        requested = tuple(symbols)
        self.calls.append(requested)
        return {
            symbol: QuoteTick(
                symbol=symbol,
                last=self._prices[symbol],
                prev_close=self._prices[symbol] - 10.0,
                volume=1_000.0,
                ts_exchange=_TICK_TS,
                ts_received=_TICK_RECEIVED,
                source="yahoo",
            )
            for symbol in requested
            if symbol in self._prices
        }


def _dataset(
    *, facts: Mapping[str, Mapping[str, Cell]] | None = None
) -> EngineDataset:
    """Build the four-symbol SEC dataset, optionally carrying fact cells."""

    master = SecurityMaster.from_company_tickers(
        json.loads(_TICKERS_FIXTURE.read_text(encoding="utf-8")),
        as_of="2026-10-26",
    )
    return EngineDataset(
        fetched_at="2026-10-26T12:00:00+00:00",
        security_master=master,
        facts={} if facts is None else facts,
        errors={},
    )


def _public_service(
    *, facts: Mapping[str, Mapping[str, Cell]] | None = None
) -> EngineService:
    """One SEC-only service that declares itself a public deployment."""

    return EngineService.from_dataset(
        _dataset(facts=facts), policy=LicensePolicy(public_mode=True)
    )


def _private_service() -> EngineService:
    """The same dataset under the permissive private-research policy."""

    return EngineService.from_dataset(_dataset())


def _set_engine_env(base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the runtime db/exports at ``base`` and clear unrelated engine env."""

    monkeypatch.setenv("MEE_DB_PATH", str(base / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(base / "exports"))
    for name in (
        "MEE_ENGINE_SYMBOLS",
        "MEE_ENGINE_CACHE_DIR",
        "MEE_ENGINE_QUOTES",
        "MEE_ENGINE_SNAPSHOT",
    ):
        monkeypatch.delenv(name, raising=False)


def _create_app() -> FastAPI:
    """Build a fresh application (imported here so collection creates nothing)."""

    from app.main import create_app

    return create_app()


def _build_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Build an app with an isolated db and an explicitly private engine env."""

    _set_engine_env(tmp_path, monkeypatch)
    monkeypatch.delenv("MEE_ENGINE_PUBLIC_MODE", raising=False)
    return _create_app()


def _client(application: FastAPI) -> AsyncClient:
    """An in-process HTTP client bound to ``application`` (no sockets)."""

    return AsyncClient(
        transport=ASGITransport(app=application), base_url="http://mee.test"
    )


def _install_quote_engine(monkeypatch: pytest.MonkeyPatch, engine: Any) -> None:
    """Replace the route's quote transport at its documented seam."""

    monkeypatch.setattr("app.api.engine.quote_engine_of", lambda request: engine)


def _assert_license_refusal(
    response: Response, forbidden: _ForbiddenQuoteEngine
) -> dict[str, Any]:
    """Assert one fail-closed refusal: 503, frozen code, no rows and no fetch."""

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["error"] == "license_unavailable"
    message = body.get("errorMessage")
    assert isinstance(message, str) and message.strip(), (
        "a 503 must carry the standard {error, errorMessage} envelope"
    )
    assert "rows" not in body, "a licence refusal must not include a partial page"
    assert forbidden.calls == [], "a refused public query must not poll a quote source"
    return body


async def test_public_mode_serves_sec_rows_with_a_license_blocked_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-quote public query answers 200 from SEC identity, with no price."""

    application = _build_app(tmp_path, monkeypatch)
    application.state.engine = _public_service()
    forbidden = _ForbiddenQuoteEngine()
    _install_quote_engine(monkeypatch, forbidden)

    async with _client(application) as client:
        response = await client.post(
            "/engine/screen", json={"filters": [], "limit": 20, "offset": 0}
        )

    assert response.status_code == 200, response.text
    document = response.json()
    assert "quote_snapshot" in document, "a 200 screen must state its quote freshness"
    snapshot = document["quote_snapshot"]
    assert set(snapshot) == {
        "refreshed",
        "scope",
        "requested",
        "returned",
        "as_of",
        "stale",
        "reason",
        "filter_used_snapshot",
    }
    assert snapshot["reason"] == "license_blocked"
    assert snapshot["refreshed"] is False
    assert snapshot["scope"] == "none"
    assert snapshot["requested"] == 0
    assert snapshot["returned"] == 0
    assert snapshot["as_of"] is None
    assert snapshot["stale"] is None
    assert snapshot["filter_used_snapshot"] is False
    assert document["total"] == 4
    assert document["returned"] == 4
    assert len(document["rows"]) == 4
    for row in document["rows"]:
        for key in _QUOTE_ROW_KEYS:
            assert key not in row, f"public mode leaked the quote field {key!r}"
    assert forbidden.calls == [], "public mode must never poll an internal quote source"
    assert application.state.engine.policy.public_mode is True, (
        "a public deployment must not be silently downgraded to private"
    )


async def test_public_mode_refuses_a_price_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A filter on a quote field is 503 license_unavailable, with no page."""

    application = _build_app(tmp_path, monkeypatch)
    application.state.engine = _public_service()
    forbidden = _ForbiddenQuoteEngine()
    _install_quote_engine(monkeypatch, forbidden)

    async with _client(application) as client:
        response = await client.post(
            "/engine/screen",
            json={
                "filters": [{"field": "price", "op": "gte", "value": 1}],
                "limit": 20,
                "offset": 0,
            },
        )

    _assert_license_refusal(response, forbidden)


async def test_public_mode_refuses_a_sort_on_a_quote_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sorting by a quote field also uses it, so it is 503 license_unavailable."""

    application = _build_app(tmp_path, monkeypatch)
    application.state.engine = _public_service()
    forbidden = _ForbiddenQuoteEngine()
    _install_quote_engine(monkeypatch, forbidden)

    async with _client(application) as client:
        response = await client.post(
            "/engine/screen",
            json={
                "filters": [],
                "sort": {"field": "price", "descending": True},
                "limit": 20,
                "offset": 0,
            },
        )

    _assert_license_refusal(response, forbidden)


async def test_public_mode_refusal_survives_refresh_quotes_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disabling the refresh is not a way around the licence boundary."""

    application = _build_app(tmp_path, monkeypatch)
    application.state.engine = _public_service()
    forbidden = _ForbiddenQuoteEngine()
    _install_quote_engine(monkeypatch, forbidden)

    async with _client(application) as client:
        response = await client.post(
            "/engine/screen",
            json={
                "filters": [{"field": "price", "op": "gte", "value": 1}],
                "refresh_quotes": False,
                "limit": 20,
                "offset": 0,
            },
        )

    _assert_license_refusal(response, forbidden)


async def test_public_mode_env_wires_a_public_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MEE_ENGINE_PUBLIC_MODE=1 makes create_app() build a public policy."""

    _set_engine_env(tmp_path / "public", monkeypatch)
    monkeypatch.setenv("MEE_ENGINE_PUBLIC_MODE", "1")
    public_app = _create_app()

    async with _client(public_app) as client:
        public_response = await client.get("/engine/health")

    assert public_response.status_code == 200, public_response.text
    public_body = public_response.json()
    assert public_body["public_mode"] is True
    assert public_app.state.engine.policy.public_mode is True

    _set_engine_env(tmp_path / "private", monkeypatch)
    monkeypatch.delenv("MEE_ENGINE_PUBLIC_MODE", raising=False)
    private_app = _create_app()

    async with _client(private_app) as client:
        private_response = await client.get("/engine/health")

    assert private_response.status_code == 200, private_response.text
    assert private_response.json()["public_mode"] is False
    assert private_app.state.engine.policy.public_mode is False


async def test_private_mode_still_refreshes_quotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard: private mode keeps the page refresh and the price."""

    application = _build_app(tmp_path, monkeypatch)
    application.state.engine = _private_service()
    stub = _StubQuoteEngine({"AAPL": 200.0})
    _install_quote_engine(monkeypatch, stub)

    async with _client(application) as client:
        response = await client.post(
            "/engine/screen",
            json={
                "filters": [{"field": "ticker", "op": "eq", "value": "AAPL"}],
                "limit": 20,
                "offset": 0,
            },
        )

    assert response.status_code == 200, response.text
    document = response.json()
    assert "quote_snapshot" in document
    snapshot = document["quote_snapshot"]
    assert snapshot["refreshed"] is True
    assert snapshot["reason"] is None
    assert snapshot["scope"] == "page"
    assert snapshot["requested"] == 1
    assert snapshot["returned"] == 1
    assert snapshot["as_of"] == "2026-10-26T14:30:00+00:00"
    assert snapshot["stale"] is False
    assert stub.calls == [("AAPL",)]
    row = document["rows"][0]
    assert row["ticker"]["value"] == "AAPL"
    assert row["price"]["value"] == 200.0


async def test_public_mode_still_serves_sec_fact_cells(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only quote cells are blocked: an SEC fact still answers in public mode."""

    sales = Cell(
        value=466_823_000_000.0,
        unit=Unit.USD,
        provenance=Provenance(
            source="sec",
            as_of="2025-09-27",
            status=CellStatus.OK,
            url="https://www.sec.gov/Archives/edgar/data/320193/aapl-20250927.htm",
        ),
    )
    application = _build_app(tmp_path, monkeypatch)
    application.state.engine = _public_service(facts={"AAPL": {"revenue": sales}})
    forbidden = _ForbiddenQuoteEngine()
    _install_quote_engine(monkeypatch, forbidden)

    async with _client(application) as client:
        response = await client.post(
            "/engine/screen",
            json={
                "filters": [{"field": "ticker", "op": "eq", "value": "AAPL"}],
                "limit": 20,
                "offset": 0,
            },
        )

    assert response.status_code == 200, response.text
    document = response.json()
    assert "quote_snapshot" in document
    assert document["quote_snapshot"]["reason"] == "license_blocked"
    assert document["returned"] == 1
    row = document["rows"][0]
    assert row["ticker"]["value"] == "AAPL"
    assert "sales" in row, "the SEC fact must survive public mode"
    assert row["sales"]["value"] == 466_823_000_000.0
    assert row["sales"]["unit"] == "USD"
    assert row["sales"]["provenance"]["source"] == "sec"
    assert row["sales"]["provenance"]["as_of"] == "2025-09-27"
    assert "price" not in row
    assert forbidden.calls == []


async def test_public_mode_refuses_realtime_quotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/market/quote`` is the same licence boundary as the engine screen.

    The roadmap names both routes: a public deployment must not answer a
    Yahoo/Nasdaq print through the older watchlist endpoint while the engine
    route is fail-closed.
    """

    application = _build_app(tmp_path, monkeypatch)
    application.state.engine = _public_service()
    forbidden = _ForbiddenQuoteEngine()
    _install_quote_engine(monkeypatch, forbidden)

    async with _client(application) as client:
        response = await client.get("/market/quote?symbols=AAPL")

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["error"] == "license_unavailable"
    assert isinstance(body.get("errorMessage"), str) and body["errorMessage"].strip()
    assert forbidden.calls == [], "a refused public quote must not poll a source"
