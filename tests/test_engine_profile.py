"""T3 — the header bundle, read from the same scan row the screener uses.

The bundle is a projection, not a calculation: price, change, volume, cap,
52-week range, beta and RSI are the row's own cells; ``prev_close`` is the one
identity the screener already derives (price / (1 + change/100)); index
membership comes from the vendored snapshot the ``idx`` filter reads.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.screen.ticker import profile_document


def row(**overrides: Any) -> dict[str, Any]:
    """One ``build_universe`` parity row, with every header cell present."""

    base: dict[str, Any] = {
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "exchange": "XNAS",
        "country": "United States",
        "sector": "Electronic Technology",
        "industry": "Electronic Computers",
        "price": 100.0,
        "change_pct": 2.5,
        "volume": 12_345_678.0,
        "market_cap_usd": 3_000_000_000_000.0,
        "high_52w": 120.0,
        "low_52w": 70.0,
        "beta_1y": 1.23,
        "rsi_14": 56.7,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# The bundle
# ---------------------------------------------------------------------------


def test_the_bundle_carries_every_header_cell() -> None:
    document = profile_document(row(), index_members=["sp500", "ndx"])

    assert document == {
        "symbol": "AAPL",
        "company": "Apple Inc.",
        "price": 100.0,
        "change_pct": 2.5,
        "volume": 12_345_678.0,
        "market_cap": 3_000_000_000_000.0,
        "prev_close": pytest.approx(100.0 / 1.025),
        "sector": "Electronic Technology",
        "industry": "Electronic Computers",
        "index_membership": ["sp500", "ndx"],
        "high_52w": 120.0,
        "low_52w": 70.0,
        "beta": 1.23,
        "rsi": 56.7,
    }


def test_prev_close_is_the_screener_identity() -> None:
    document = profile_document(row(price=80.0, change_pct=-20.0))
    assert document["prev_close"] == pytest.approx(100.0)


def test_a_negative_total_return_leaves_prev_close_null() -> None:
    # A change of -100% or worse has no positive previous close.
    document = profile_document(row(price=10.0, change_pct=-100.0))
    assert document["prev_close"] is None


def test_missing_cells_are_null_never_zero() -> None:
    document = profile_document(
        row(
            price=None,
            change_pct=None,
            volume=None,
            market_cap_usd=None,
            high_52w=None,
            low_52w=None,
            beta_1y=None,
            rsi_14=None,
            sector=None,
            industry=None,
        )
    )

    assert document["price"] is None
    assert document["change_pct"] is None
    assert document["volume"] is None
    assert document["market_cap"] is None
    assert document["prev_close"] is None
    assert document["high_52w"] is None
    assert document["low_52w"] is None
    assert document["beta"] is None
    assert document["rsi"] is None
    assert document["sector"] is None
    assert document["industry"] is None


def test_unknown_membership_is_null_not_an_empty_list() -> None:
    assert profile_document(row(), index_members=None)["index_membership"] is None


def test_no_index_membership_is_an_empty_list() -> None:
    assert profile_document(row(), index_members=[])["index_membership"] == []


# ---------------------------------------------------------------------------
# The scan the route reads
# ---------------------------------------------------------------------------


def _scan_row(symbol: str, cap: float) -> dict[str, Any]:
    return {
        "type": "stock",
        "subtype": "common",
        "exchange": "NASDAQ",
        "symbol": symbol,
        "description": f"{symbol} Corp",
        "country": "United States",
        "sector": "Electronic Technology",
        "industry": "Electronic Computers",
        "currency": "USD",
        "close": 100.0,
        "change": 1.0,
        "volume": 1_000_000.0,
        "Value.Traded": 1.0e8,
        "market_cap_basic": cap,
        "RSI": 55.0,
        "beta_1_year": 1.1,
        "price_52_week_high": 120.0,
        "price_52_week_low": 70.0,
    }


async def test_market_universe_asks_for_the_screener_parity_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One scan shape: parity columns, no cap floor, every common subtype.

    This is the scan ``/screens/select`` runs for a criteria query, so a ticker
    opened beside a screen shares the cached page instead of paying a second
    provider path.
    """

    from starlette.requests import Request

    from app.api import screens
    from app.main import create_app
    from app.screen.scanner import ScanPage
    from app.screen.universe import PARITY_COLUMNS

    calls: list[dict[str, Any]] = []

    async def fake_fetch(region: str, **kwargs: Any) -> ScanPage:
        calls.append({"region": region, **kwargs})
        return ScanPage(
            total_count=2,
            rows=[_scan_row("AAPL", 3e12), _scan_row("MSFT", 2e12)],
        )

    monkeypatch.setattr(screens, "fetch_scan", fake_fetch)
    application = create_app()
    request = Request({"type": "http", "app": application, "headers": []})

    rows = await screens.market_universe(request)

    assert [call["region"] for call in calls] == ["us"]
    assert calls[0]["columns"] == PARITY_COLUMNS
    assert calls[0]["min_market_cap"] is None
    assert calls[0]["subtype"] is None
    assert calls[0]["asset_class"] == "stock"
    assert {row["symbol"] for row in rows} == {"AAPL", "MSFT"}
    assert rows[0]["industry"] == "Electronic Computers"


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


@asynccontextmanager
async def route_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx2.AsyncClient]:
    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))

    from app.main import create_app

    application = create_app()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=application), base_url="http://mee.test"
    ) as client:
        yield client


def install(
    monkeypatch: pytest.MonkeyPatch,
    rows: Sequence[dict[str, Any]],
    members: Sequence[str] | None = None,
) -> None:
    async def universe(_request: Any) -> Sequence[dict[str, Any]]:
        return rows

    monkeypatch.setattr("app.api.engine.ticker_universe", universe)
    monkeypatch.setattr(
        "app.api.engine.index_members_for", lambda symbol: members
    )


async def test_profile_route_returns_the_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, [row()], members=["sp500"])

    async with route_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/profile/AAPL")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "AAPL"
    assert body["index_membership"] == ["sp500"]
    assert body["prev_close"] == pytest.approx(100.0 / 1.025)


async def test_profile_route_unknown_symbol_is_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, [row(symbol="MSFT")], members=None)

    async with route_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/profile/AAPL")

    assert response.status_code == 404, response.text
    assert response.json()["error"] == "security_not_found"


async def test_profile_route_scan_failure_is_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.screen.scanner import ScanError

    async def failing(_request: Any) -> Sequence[dict[str, Any]]:
        raise ScanError("scanner blocked")

    monkeypatch.setattr("app.api.engine.ticker_universe", failing)

    async with route_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/profile/AAPL")

    assert response.status_code == 503, response.text
    assert response.json()["error"] == "scanner_unavailable"
