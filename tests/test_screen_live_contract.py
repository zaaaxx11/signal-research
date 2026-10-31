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


def test_same_issuer_with_float_noise_and_share_classes_is_one_row() -> None:
    rows = [
        _row(
            symbol="700",
            description="Tencent Holdings Ltd",
            exchange="HKEX",
            country="China",
            market_cap=439_493_657_763.4693,
        ),
        _row(
            symbol="TENCH/N",
            description="Tencent Holdings Ltd",
            exchange="BMV",
            country="China",
            market_cap=439_493_657_763.46924,
        ),
        _row(
            symbol="1398",
            description="Industrial and Commercial Bank of China Limited Class H",
            exchange="HKEX",
            country="China",
            market_cap=428_327_684_539.39276,
        ),
        _row(
            symbol="601398",
            description="Industrial and Commercial Bank of China Limited Class A",
            exchange="SSE",
            country="China",
            market_cap=428_327_684_539.3927,
        ),
        _row(
            symbol="NOVN",
            description="Novartis AG",
            exchange="SIX",
            country="Switzerland",
            market_cap=287_000_000_000.0,
        ),
        _row(
            symbol="NOVNEE",
            description="Novartis AG TEMP",
            exchange="SIX",
            country="Switzerland",
            market_cap=287_000_000_000.0,
        ),
    ]

    global_rows = build_universe(rows, region="global")
    assert sorted(row["symbol"] for row in global_rows) == ["1398", "700", "NOVN"]


def test_global_tickers_get_the_provider_suffix_before_deep_enrichment() -> None:
    from app.screen.symbols import provider_symbol

    assert provider_symbol("2330", "TWSE") == "2330.TW"
    assert provider_symbol("2222", "TADAWUL") == "2222.SR"
    assert provider_symbol("005930", "KRX") == "005930.KS"
    assert provider_symbol("ASML", "EURONEXT") == "ASML.AS"
    assert provider_symbol("AAPL", "XNAS") == "AAPL"
    assert provider_symbol("BRK.B", "XNYS") == "BRK.B"
    assert provider_symbol("SPY", "XCBO") == "SPY", "CBOE is a US venue: no suffix"
    assert provider_symbol("SPY", "CBOE") == "SPY"
    assert provider_symbol("UNKNOWN", "MYSTERY") == "UNKNOWN"
