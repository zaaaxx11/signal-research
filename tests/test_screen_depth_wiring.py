"""The screener must read depth before it builds a universe.

Depth is merged into the provider rows *before* `build_universe`, not projected
onto the response afterwards. That ordering is the contract: the screener's
filter vocabulary already accepts fields the provider never sends
(`target_price` filters 3,877 of 5,600 symbols while every row shows `None`), so
a response-only patch would leave the filter and the table disagreeing — a bug
this integration is meant to remove, not add.

Depth must also be invisible when it is absent: no store, an empty store, or
`MEE_SCREEN_DEPTH=0` has to leave today's rows byte-identical.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.screen import depth_source
from app.screen import snapshot as snapshot_module
from app.screen.scanner import ScanPage
from app.screen.snapshot import Snapshot, refresh_screen

ROWS: list[dict[str, Any]] = [
    {
        "name": "AAPL",
        "description": "Apple Inc.",
        "symbol": "AAPL",
        "exchange": "NASDAQ",
        "venue": "NASDAQ",
        "country": "US",
        "sector": "Technology",
        "type": "stock",
        "subtype": "common",
        "currency": "USD",
        "close": 230.0,
        "change": 1.2,
        "volume": 50_000_000,
        "Value.Traded": 11_500_000_000,
        "market_cap_basic": 3_400_000_000_000,
        "RSI": 55.0,
        "beta_1_year": 1.1,
        "price_52_week_high": 260.0,
        "price_52_week_low": 160.0,
    },
    {
        "name": "MSFT",
        "description": "Microsoft Corporation",
        "symbol": "MSFT",
        "exchange": "NASDAQ",
        "venue": "NASDAQ",
        "country": "US",
        "sector": "Technology Services",
        "type": "stock",
        "subtype": "common",
        "currency": "USD",
        "close": 420.0,
        "change": -0.4,
        "volume": 20_000_000,
        "Value.Traded": 8_400_000_000,
        "market_cap_basic": 3_100_000_000_000,
        "RSI": 48.0,
        "beta_1_year": 0.9,
        "price_52_week_high": 470.0,
        "price_52_week_low": 320.0,
    },
]


class _Repository:
    def __init__(self) -> None:
        self.saved: dict[str, Any] | None = None

    async def save(self, **kwargs: Any) -> dict[str, Any]:
        self.saved = kwargs
        return {"id": "screen-1", **kwargs}

    async def rows(self, identifier: str) -> list[dict[str, Any]]:
        return list((self.saved or {}).get("rows") or [])


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _scan(region: str, *, limit: int = 0) -> ScanPage:
        return ScanPage(
            total_count=len(ROWS), rows=[dict(row) for row in ROWS], received=len(ROWS)
        )

    monkeypatch.setattr(snapshot_module, "fetch_scan", _scan)


def _depth(**fields: Any) -> depth_source.DepthSnapshot:
    return depth_source.DepthSnapshot(
        as_of="2026-10-30T00:00:00+00:00",
        symbols={"AAPL": fields},
        loaded_at=0.0,
    )


def _rows_by_symbol(repository: _Repository) -> dict[str, dict[str, Any]]:
    saved = (repository.saved or {}).get("rows") or []
    return {row["symbol"]: dict(row) for row in saved}


def test_depth_fields_reach_the_stored_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        snapshot_module,
        "current_depth",
        lambda: _depth(pe=21.3, inst_own=66.3, target_price=250.0),
    )
    repository = _Repository()

    result = asyncio.run(
        refresh_screen("us", "market_cap", repository=repository)  # type: ignore[arg-type]
    )

    assert isinstance(result, Snapshot)
    apple = _rows_by_symbol(repository)["AAPL"]
    assert apple["pe"] == 21.3
    assert apple["target_price"] == 250.0
    # the provider's own columns survive untouched
    assert apple["market_cap_usd"] == 3_400_000_000_000 or apple["market_cap_usd"] > 0


def test_the_provider_value_wins_over_depth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fill-missing-only: depth never overwrites what the scan reported."""

    monkeypatch.setattr(
        snapshot_module,
        "current_depth",
        lambda: _depth(sector="WRONG", market_cap=1.0),
    )
    repository = _Repository()
    asyncio.run(
        refresh_screen("us", "market_cap", repository=repository)  # type: ignore[arg-type]
    )

    apple = _rows_by_symbol(repository)["AAPL"]
    assert apple["sector"] == "Technology"
    assert apple["market_cap_usd"] > 1_000_000_000


def test_an_empty_depth_snapshot_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(snapshot_module, "current_depth", depth_source.empty_snapshot)
    repository = _Repository()

    asyncio.run(
        refresh_screen("us", "market_cap", repository=repository)  # type: ignore[arg-type]
    )

    apple = _rows_by_symbol(repository)["AAPL"]
    assert "pe" not in apple
    assert "target_price" not in apple
    assert apple["sector"] == "Technology"


def test_a_broken_store_is_a_scan_not_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A screener request must never 500 because enrichment is unreadable."""

    broken = tmp_path / "depth.json"
    broken.write_text("{not json")
    depth_source.reset_cache()
    monkeypatch.setenv("MEE_SCREEN_DEPTH", "true")
    monkeypatch.setenv("MEE_SCREEN_DEPTH_STORE", str(broken))
    monkeypatch.setattr(
        snapshot_module, "current_depth", lambda: depth_source.current_depth()
    )
    repository = _Repository()

    asyncio.run(
        refresh_screen("us", "market_cap", repository=repository)  # type: ignore[arg-type]
    )

    apple = _rows_by_symbol(repository)["AAPL"]
    assert apple["symbol"] == "AAPL"
    assert "pe" not in apple


def test_the_flag_off_reads_no_file(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import Settings

    settings = Settings(screen_depth=False)
    assert depth_source.resolve_store_path(settings) is None
    assert depth_source.load_depth(depth_source.resolve_store_path(settings)).symbols == {}
