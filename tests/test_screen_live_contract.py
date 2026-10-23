"""Live-contract regressions found after Screen S0 GREEN.

These are not shape tests of the fixtures: they pin the two facts a mocked
scanner cannot prove by itself.

1. TradingView has no ``markets=["global"]`` value. Sending it answers an empty
   page; the Global tab must omit the key and let the universe layer remove
   US issuers. (Observed live: 0 rows before, 20 rows after.)
2. The global endpoint returns foreign *listings* of US issuers (NVDACL on BCS,
   GOOGL on BMV). Filtering only the US venue puts those cross-listings in the
   Global tab; the issuer's country is the tab boundary.

Both failures were invisible to the fixture suite because the fixtures already
had a non-empty global page. They are cheap to keep as regressions.
"""

from __future__ import annotations

from typing import Any

from app.screen.scanner import scan_body
from app.screen.universe import build_universe


def _row(
    *,
    symbol: str,
    description: str,
    exchange: str,
    country: str,
    market_cap: float = 1_000_000_000.0,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "description": description,
        "exchange": exchange,
        "country": country,
        "type": "stock",
        "subtype": "common",
        "currency": "USD",
        "close": 10.0,
        "change": 1.0,
        "volume": 1000.0,
        "Value.Traded": 10_000.0,
        "market_cap_basic": market_cap,
    }


def test_global_scan_omits_the_nonexistent_global_market_filter() -> None:
    assert "markets" not in scan_body("global"), (
        "TradingView answers an empty page for markets=['global']; omit the key "
        "and let the local universe split the tabs"
    )
    assert scan_body("us")["markets"] == ["america"]


def test_global_tab_excludes_a_us_issuers_foreign_listing() -> None:
    rows = [
        _row(
            symbol="NVDACL",
            description="NVIDIA Corporation",
            exchange="BCS",
            country="United States",
            market_cap=5_114_261_161_804.0,
        ),
        _row(
            symbol="NESN",
            description="Nestle S.A.",
            exchange="SIX",
            country="Switzerland",
            market_cap=300_000_000_000.0,
        ),
    ]

    assert [row["symbol"] for row in build_universe(rows, region="global")] == ["NESN"]
    assert build_universe(rows, region="us") == []
