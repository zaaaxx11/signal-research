"""BarStore + patterns + backfill + nightly + bars API."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.api.market import bars as bars_route
from app.market.barstore import BarStore
from app.market.chart import ChartBar
from app.market.patterns import (
    ALL_PATTERNS,
    advance_block,
    bearish_abandoned_baby,
    bearish_belt_hold,
    bearish_engulfing,
    bearish_harami,
    bearish_marubozu,
    bullish_abandoned_baby,
    bullish_belt_hold,
    bullish_engulfing,
    bullish_harami,
    bullish_marubozu,
    dark_cloud_cover,
    delimiter,
    doji,
    evening_star,
    hammer,
    hanging_man,
    inverted_hammer,
    morning_star,
    piercing_pattern,
    shooting_star,
    spinning_top,
    three_black_crows,
    three_inside_down,
    three_inside_up,
    three_line_strike_bearish,
    three_line_strike_bullish,
    three_white_soldiers,
)
from app.market.patterns import detect_all as _detect_all
from tests._phase2 import MockWeb


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_bar(tmp_path: Path) -> BarStore:
    return BarStore(base_dir=tmp_path / "bars")


def _bar(
    day: date,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
) -> ChartBar:
    return ChartBar(
        day=day,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


# ---------------------------------------------------------------------------
# BarStore
# ---------------------------------------------------------------------------

class TestBarStore:
    def test_append_and_load(self, tmp_bar: BarStore) -> None:
        bar = _bar(date(2024, 1, 2), 100.0, 105.0, 99.0, 103.0)
        tmp_bar.append("AAPL", bar)
        loaded = tmp_bar.load("AAPL")
        assert len(loaded) == 1
        assert loaded[0].day == date(2024, 1, 2)
        assert loaded[0].close == 103.0

    def test_dedupe_on_append(self, tmp_bar: BarStore) -> None:
        b1 = _bar(date(2024, 1, 2), 100.0, 105.0, 99.0, 103.0)
        b2 = _bar(date(2024, 1, 3), 101.0, 106.0, 100.0, 104.0)
        tmp_bar.append("AAPL", b1)
        tmp_bar.append("AAPL", b1)  # duplicate
        tmp_bar.append("AAPL", b2)
        assert tmp_bar.count("AAPL") == 2

    def test_append_many_dedupe(self, tmp_bar: BarStore) -> None:
        bars = [
            _bar(date(2024, 1, 2), 100.0, 105.0, 99.0, 103.0),
            _bar(date(2024, 1, 3), 101.0, 106.0, 100.0, 104.0),
        ]
        tmp_bar.append_many("AAPL", bars)
        n = tmp_bar.append_many("AAPL", bars)  # all dupes
        assert n == 0
        assert tmp_bar.count("AAPL") == 2

    def test_symbols_and_count(self, tmp_bar: BarStore) -> None:
        tmp_bar.append("AAPL", _bar(date(2024, 1, 2), 100.0, 105.0, 99.0, 103.0))
        tmp_bar.append("MSFT", _bar(date(2024, 1, 2), 200.0, 205.0, 199.0, 203.0))
        assert set(tmp_bar.symbols()) == {"AAPL", "MSFT"}
        assert tmp_bar.count("AAPL") == 1

    def test_latest(self, tmp_bar: BarStore) -> None:
        bars = [
            _bar(date(2024, 1, 2), 100.0, 105.0, 99.0, 103.0),
            _bar(date(2024, 1, 3), 101.0, 106.0, 100.0, 104.0),
        ]
        tmp_bar.append_many("AAPL", bars)
        assert tmp_bar.latest("AAPL").day == date(2024, 1, 3)

    def test_empty_symbol(self, tmp_bar: BarStore) -> None:
        assert tmp_bar.load("UNKNOWN") == []
        assert tmp_bar.latest("UNKNOWN") is None
        assert tmp_bar.count("UNKNOWN") == 0


# ---------------------------------------------------------------------------
# Pattern detection — known OHLCV
# ---------------------------------------------------------------------------

class TestPatterns:
    def test_hammer(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 102.0, 103.0, 100.0, 100.0),
            _bar(date(2024, 1, 2), 100.0, 102.0, 95.0, 101.0),  # long lower wick
        ]
        assert hammer(bars) is True

    def test_hanging_man_requires_uptrend(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 98.0, 99.0, 97.0, 97.0),
            _bar(date(2024, 1, 2), 97.0, 99.0, 92.0, 98.0),
        ]
        assert hanging_man(bars) is False  # prior was bearish

    def test_inverted_hammer(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 102.0, 103.0, 100.0, 100.0),
            _bar(date(2024, 1, 2), 100.0, 107.0, 99.0, 101.0),  # long upper wick
        ]
        assert inverted_hammer(bars) is True

    def test_shooting_star(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 97.0, 100.0, 97.0, 99.0),  # bullish prior
            _bar(date(2024, 1, 2), 99.0, 106.0, 98.0, 100.0),  # long upper wick
        ]
        assert shooting_star(bars) is True

    def test_bullish_marubozu(self) -> None:
        bars = [_bar(date(2024, 1, 2), 100.0, 105.0, 100.0, 105.0)]
        assert bullish_marubozu(bars) is True

    def test_bearish_marubozu(self) -> None:
        bars = [_bar(date(2024, 1, 2), 105.0, 105.0, 100.0, 100.0)]
        assert bearish_marubozu(bars) is True

    def test_doji(self) -> None:
        bars = [_bar(date(2024, 1, 2), 105.0, 105.1, 104.9, 105.0)]
        assert doji(bars) is True

    def test_spinning_top(self) -> None:
        bars = [_bar(date(2024, 1, 2), 104.8, 106.0, 103.0, 105.2)]
        assert spinning_top(bars) is True

    def test_bullish_belt_hold(self) -> None:
        bars = [_bar(date(2024, 1, 2), 100.0, 105.0, 100.0, 105.0)]
        assert bullish_belt_hold(bars) is True

    def test_bearish_belt_hold(self) -> None:
        bars = [_bar(date(2024, 1, 2), 105.0, 105.0, 100.0, 100.0)]
        assert bearish_belt_hold(bars) is True

    def test_bullish_engulfing(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 105.0, 106.0, 103.0, 103.0),
            _bar(date(2024, 1, 2), 102.0, 106.0, 101.0, 106.0),
        ]
        assert bullish_engulfing(bars) is True

    def test_bearish_engulfing(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 103.0, 106.0, 102.0, 105.0),
            _bar(date(2024, 1, 2), 105.5, 106.0, 100.0, 100.0),
        ]
        assert bearish_engulfing(bars) is True

    def test_bullish_harami(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 105.0, 106.0, 103.0, 103.0),
            _bar(date(2024, 1, 2), 103.5, 104.0, 103.0, 103.8),
        ]
        assert bullish_harami(bars) is True

    def test_bearish_harami(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 103.0, 106.0, 102.0, 105.0),
            _bar(date(2024, 1, 2), 104.5, 105.0, 104.0, 104.2),
        ]
        assert bearish_harami(bars) is True

    def test_piercing_pattern(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 105.0, 106.0, 103.0, 103.0),
            _bar(date(2024, 1, 2), 102.0, 104.5, 101.0, 104.5),
        ]
        assert piercing_pattern(bars) is True

    def test_dark_cloud_cover(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 103.0, 106.0, 102.0, 105.0),
            _bar(date(2024, 1, 2), 106.5, 107.0, 103.0, 103.0),
        ]
        assert dark_cloud_cover(bars) is True

    def test_morning_star(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 105.0, 106.0, 103.0, 103.0),
            _bar(date(2024, 1, 2), 103.5, 103.6, 103.4, 103.5),  # doji
            _bar(date(2024, 1, 3), 103.2, 106.0, 103.0, 105.0),
        ]
        assert morning_star(bars) is True

    def test_evening_star(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 103.0, 106.0, 102.0, 105.0),
            _bar(date(2024, 1, 2), 105.5, 105.6, 105.4, 105.5),  # doji
            _bar(date(2024, 1, 3), 105.2, 105.5, 102.0, 102.0),
        ]
        assert evening_star(bars) is True

    def test_three_white_soldiers(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 100.0, 103.0, 100.0, 102.0),
            _bar(date(2024, 1, 2), 102.5, 105.0, 102.0, 104.0),
            _bar(date(2024, 1, 3), 104.5, 107.0, 104.0, 106.0),
        ]
        assert three_white_soldiers(bars) is True

    def test_three_black_crows(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 106.0, 106.0, 103.0, 105.0),
            _bar(date(2024, 1, 2), 105.0, 105.0, 102.0, 103.0),
            _bar(date(2024, 1, 3), 103.0, 103.0, 100.0, 101.0),
        ]
        assert three_black_crows(bars) is True

    def test_three_inside_up(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 105.0, 106.0, 103.0, 103.0),
            _bar(date(2024, 1, 2), 103.5, 104.0, 103.0, 103.8),
            _bar(date(2024, 1, 3), 103.2, 106.0, 103.0, 105.5),
        ]
        assert three_inside_up(bars) is True

    def test_three_inside_down(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 103.0, 106.0, 102.0, 105.0),
            _bar(date(2024, 1, 2), 104.5, 105.0, 104.0, 104.2),
            _bar(date(2024, 1, 3), 104.2, 104.5, 100.0, 100.0),
        ]
        assert three_inside_down(bars) is True

    def test_bullish_abandoned_baby(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 105.0, 106.0, 103.0, 103.0),
            _bar(date(2024, 1, 2), 102.0, 102.05, 101.95, 102.0),  # doji, gaps down
            _bar(date(2024, 1, 3), 103.2, 106.0, 103.0, 105.0),
        ]
        assert bullish_abandoned_baby(bars) is True

    def test_bearish_abandoned_baby(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 103.0, 106.0, 102.0, 105.0),
            _bar(date(2024, 1, 2), 106.5, 106.55, 106.45, 106.5),  # doji, gaps up
            _bar(date(2024, 1, 3), 102.0, 102.5, 100.0, 100.0),
        ]
        assert bearish_abandoned_baby(bars) is True

    def test_three_line_strike_bullish(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 105.0, 106.0, 103.0, 103.0),
            _bar(date(2024, 1, 2), 104.0, 105.0, 102.0, 102.0),
            _bar(date(2024, 1, 3), 103.0, 104.0, 101.0, 101.0),
            _bar(date(2024, 1, 4), 101.0, 106.0, 100.0, 105.0),
        ]
        assert three_line_strike_bullish(bars) is True

    def test_three_line_strike_bearish(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 103.0, 106.0, 102.0, 105.0),
            _bar(date(2024, 1, 2), 105.0, 107.0, 104.0, 106.0),
            _bar(date(2024, 1, 3), 106.0, 108.0, 105.0, 107.0),
            _bar(date(2024, 1, 4), 107.0, 108.0, 100.0, 100.0),
        ]
        assert three_line_strike_bearish(bars) is True

    def test_advance_block(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 100.0, 103.0, 100.0, 102.0),
            _bar(date(2024, 1, 2), 102.5, 104.0, 102.0, 103.5),
            _bar(date(2024, 1, 3), 103.5, 105.5, 103.0, 104.0),
        ]
        assert advance_block(bars) is True

    def test_delimiter(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 105.0, 107.0, 103.0, 103.0),
            _bar(date(2024, 1, 2), 103.0, 103.5, 101.5, 102.0),
            _bar(date(2024, 1, 3), 101.5, 101.8, 99.5, 100.7),
        ]
        assert delimiter(bars) is True

    def test_detect_all_returns_28(self) -> None:
        bars = [
            _bar(date(2024, 1, 1), 100.0, 103.0, 99.0, 102.0),
            _bar(date(2024, 1, 2), 102.0, 105.0, 101.0, 104.0),
            _bar(date(2024, 1, 3), 104.0, 107.0, 103.0, 106.0),
        ]
        result = _detect_all(bars)
        assert len(result) == 28
        assert all(isinstance(v, bool) for v in result.values())

    def test_all_patterns_in_all_patterns(self) -> None:
        assert len(ALL_PATTERNS) == 28


# ---------------------------------------------------------------------------
# MTD
# ---------------------------------------------------------------------------

class TestMTD:
    def test_mtd_positive(self) -> None:
        from app.api.market import _compute_mtd
        bars = [
            _bar(date(2024, 1, 2), 100.0, 105.0, 99.0, 103.0),
            _bar(date(2024, 1, 31), 103.0, 106.0, 102.0, 105.0),
            _bar(date(2024, 2, 1), 105.0, 108.0, 104.0, 110.0),
        ]
        # Prior month close was 105 (Jan 31), current is 110 => +4.76%
        mtd = _compute_mtd(bars, date(2024, 2, 1))
        assert mtd is not None
        assert abs(mtd - 4.7619) < 0.01

    def test_mtd_no_prior_month(self) -> None:
        from app.api.market import _compute_mtd
        bars = [_bar(date(2024, 2, 1), 100.0, 105.0, 99.0, 103.0)]
        assert _compute_mtd(bars, date(2024, 2, 1)) is None

    def test_mtd_zero_prior(self) -> None:
        from app.api.market import _compute_mtd
        bars = [
            _bar(date(2024, 1, 31), 100.0, 105.0, 99.0, 103.0),
            _bar(date(2024, 2, 1), 103.0, 108.0, 102.0, 110.0),
        ]
        # Manually set prior close to 0
        bars[0] = _bar(date(2024, 1, 31), 0.0, 0.0, 0.0, 0.0)
        assert _compute_mtd(bars, date(2024, 2, 1)) is None


# ---------------------------------------------------------------------------
# Backfill / nightly (integration)
# ---------------------------------------------------------------------------

class TestBackfillIntegration:
    def test_backfill_writes_bars(self, tmp_path: Path, monkeypatch: Any) -> None:
        """Backfill writes JSONL files for each symbol."""

        # Write a minimal top500
        top500 = tmp_path / "top500.json"
        top500.write_text(json.dumps(["AAPL", "MSFT"]), encoding="utf-8")
        monkeypatch.setenv("ZSTOCKZ_TOP500_PATH", str(top500))

        # Mock Yahoo chart response
        now = datetime.now(UTC)
        period2 = int(now.timestamp())
        period1 = int(datetime(now.year - 2, now.month, now.day, tzinfo=UTC).timestamp())

        def _response(request):
            url = str(request.url)
            symbol = "AAPL" if "AAPL" in url else "MSFT"
            ts = [
                int(datetime(now.year - 2, now.month, now.day, tzinfo=UTC).timestamp()),
                int(datetime(now.year - 1, now.month, now.day, tzinfo=UTC).timestamp()),
                int(datetime(now.year, now.month, now.day - 1, tzinfo=UTC).timestamp()),
            ]
            payload = {
                "chart": {
                    "result": [{
                        "meta": {"symbol": symbol},
                        "timestamp": ts,
                        "indicators": {
                            "quote": [{
                                "open": [100.0, 110.0, 120.0],
                                "high": [105.0, 115.0, 125.0],
                                "low": [99.0, 109.0, 119.0],
                                "close": [103.0, 113.0, 123.0],
                                "volume": [1e6, 1.1e6, 1.2e6],
                            }]
                        },
                    }]
                }
            }
            import httpx2
            return httpx2.Response(200, json=payload)

        web = MockWeb()
        web.route("query1.finance.yahoo.com", _response)

        from app.providers import http as provider_http
        monkeypatch.setattr(provider_http, "build_client", web.build_client)

        from app.market.backfill import run_backfill
        store = BarStore(base_dir=tmp_path / "bars")
        monkeypatch.setattr("app.market.backfill.BarStore", lambda **kw: store)

        summary = asyncio_run(run_backfill())
        assert summary["fetched"] == 2
        assert summary["bars"] >= 3  # at least 3 bars per symbol
        assert store.count("AAPL") >= 3
        assert store.count("MSFT") >= 3

    def test_nightly_appends_new_bar(self, tmp_path: Path, monkeypatch: Any) -> None:
        """Nightly appends exactly the new bar."""

        top500 = tmp_path / "top500.json"
        top500.write_text(json.dumps(["AAPL"]), encoding="utf-8")
        monkeypatch.setenv("ZSTOCKZ_TOP500_PATH", str(top500))

        store = BarStore(base_dir=tmp_path / "bars")

        # Seed 2 bars
        store.append_many("AAPL", [
            _bar(date(2024, 1, 1), 100.0, 105.0, 99.0, 103.0),
            _bar(date(2024, 1, 2), 103.0, 108.0, 102.0, 106.0),
        ])
        assert store.count("AAPL") == 2

        # Mock returns 3 bars (the existing 2 + 1 new)
        def _response(request):
            url = str(request.url)
            ts = [
                int(datetime(2024, 1, 1, tzinfo=UTC).timestamp()),
                int(datetime(2024, 1, 2, tzinfo=UTC).timestamp()),
                int(datetime(2024, 1, 3, tzinfo=UTC).timestamp()),
            ]
            payload = {
                "chart": {
                    "result": [{
                        "meta": {"symbol": "AAPL"},
                        "timestamp": ts,
                        "indicators": {
                            "quote": [{
                                "open": [100.0, 103.0, 106.0],
                                "high": [105.0, 108.0, 111.0],
                                "low": [99.0, 102.0, 105.0],
                                "close": [103.0, 106.0, 109.0],
                                "volume": [1e6, 1.1e6, 1.2e6],
                            }]
                        },
                    }]
                }
            }
            import httpx2
            return httpx2.Response(200, json=payload)

        web = MockWeb()
        web.route("query1.finance.yahoo.com", _response)

        from app.providers import http as provider_http
        monkeypatch.setattr(provider_http, "build_client", web.build_client)

        # Patch the BarStore in nightly_bars
        import app.market.nightly_bars as nightly_mod
        monkeypatch.setattr(nightly_mod, "BarStore", lambda **kw: store)

        summary = asyncio_run(nightly_mod.run_nightly())
        assert summary["appended"] == 1
        assert store.count("AAPL") == 3


# ---------------------------------------------------------------------------
# API route
# ---------------------------------------------------------------------------

class TestBarsApi:
    def test_bars_endpoint_returns_data(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """GET /market/bars returns bars from the store."""

        store = BarStore(base_dir=tmp_path / "bars")
        store.append_many("AAPL", [
            _bar(date(2024, 1, 2), 100.0, 105.0, 99.0, 103.0),
            _bar(date(2024, 1, 3), 103.0, 108.0, 102.0, 106.0),
        ])

        # Build a fake request/response
        from fastapi import Request
        from unittest.mock import MagicMock

        monkeypatch.setattr("app.api.market.BarStore", lambda **kw: store)

        request = MagicMock(spec=Request)
        request.app.state.runtime.settings.quote_max_symbols = 100

        response = MagicMock()

        result = asyncio_run(bars_route(
            request=request,
            response=response,
            symbols="AAPL",
            include_patterns=False,
            include_mtd=False,
        ))
        assert result["returned"] == 1
        assert "AAPL" in result["symbols"]
        assert len(result["symbols"]["AAPL"]["bars"]) == 2

    def test_bars_endpoint_with_patterns(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        store = BarStore(base_dir=tmp_path / "bars")
        store.append_many("AAPL", [
            _bar(date(2024, 1, 2), 100.0, 105.0, 99.0, 103.0),
            _bar(date(2024, 1, 3), 103.0, 108.0, 102.0, 106.0),
            _bar(date(2024, 1, 4), 106.0, 111.0, 105.0, 110.0),
        ])

        from unittest.mock import MagicMock

        monkeypatch.setattr("app.api.market.BarStore", lambda **kw: store)

        request = MagicMock()
        request.app.state.runtime.settings.quote_max_symbols = 100

        result = asyncio_run(bars_route(
            request=request,
            response=MagicMock(),
            symbols="AAPL",
            include_patterns=True,
            include_mtd=False,
        ))
        patterns = result["symbols"]["AAPL"]["chart_patterns"]
        assert isinstance(patterns, dict)
        assert len(patterns) == 28
        assert all(isinstance(v, bool) for v in patterns.values())

    def test_bars_endpoint_with_mtd(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        store = BarStore(base_dir=tmp_path / "bars")
        store.append_many("AAPL", [
            _bar(date(2024, 1, 2), 100.0, 105.0, 99.0, 103.0),
            _bar(date(2024, 1, 31), 103.0, 108.0, 102.0, 105.0),
            _bar(date(2024, 2, 1), 105.0, 108.0, 104.0, 107.0),
        ])

        from unittest.mock import MagicMock

        monkeypatch.setattr("app.api.market.BarStore", lambda **kw: store)

        request = MagicMock()
        request.app.state.runtime.settings.quote_max_symbols = 100

        result = asyncio_run(bars_route(
            request=request,
            response=MagicMock(),
            symbols="AAPL",
            include_patterns=False,
            include_mtd=True,
        ))
        mtd = result["symbols"]["AAPL"]["mtd"]
        assert mtd is not None
        # prior Jan close was 105, current Feb close is 107 => +1.9%
        assert abs(mtd - 1.905) < 0.01


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import asyncio


def asyncio_run(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already in a running loop (e.g., pytest-asyncio); create a task
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result()
