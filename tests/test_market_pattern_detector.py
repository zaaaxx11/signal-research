"""Contract for ``app.market.pattern_detector`` — candlestick pattern scanning."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.market.chart import ChartBar
from app.market.pattern_detector import detect


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bar(day: int, open_: float, high: float, low: float, close: float, volume: float = 1000) -> ChartBar:
    """Shorthand for a ``ChartBar`` pinned to January *day*, 2025."""
    return ChartBar(day=date(2025, 1, day), open=open_, high=high, low=low, close=close, volume=volume)


def _write_bars(tmp_path: Path, symbol: str, bars: list[ChartBar]) -> Path:
    """Persist *bars* via ``BarStore`` and return the store root."""
    from app.market.barstore import BarStore

    store = BarStore(tmp_path)
    store.append_many(symbol, bars)
    return tmp_path


# ---------------------------------------------------------------------------
# Pattern-specific fixtures
# ---------------------------------------------------------------------------


def _doji_bars() -> list[ChartBar]:
    """Three bars where the last one is a doji (open ≈ close)."""
    return [
        _bar(1, 100, 102, 98, 101),
        _bar(2, 101, 103, 99, 102),
        _bar(3, 100, 101, 99, 100),  # doji: body=0, range=2, body/range=0
    ]


def _hammer_bars() -> list[ChartBar]:
    """Three bars where the last is a hammer (small body near high, long lower wick)."""
    return [
        _bar(1, 100, 102, 98, 101),
        _bar(2, 101, 103, 99, 102),
        _bar(3, 100, 101, 90, 100.5),  # hammer: body=0.5, lower_wick=10, upper_wick=0.5
    ]


def _bullish_engulfing_bars() -> list[ChartBar]:
    """Three bars ending with a bullish engulfing (bearish then bullish that engulfs)."""
    return [
        _bar(1, 99, 104, 97, 103),
        _bar(2, 100, 105, 95, 98),  # bearish: open=100 > close=98
        _bar(3, 96, 110, 94, 102),  # bullish engulfing: open<prev.close, close>prev.open
    ]


def _morning_star_bars() -> list[ChartBar]:
    """Three bars forming a morning star (bearish, doji, bullish)."""
    return [
        _bar(1, 105, 106, 95, 96),  # bearish, big body=9
        _bar(2, 97, 98, 96, 97.001),  # doji: body/range ≈ 0.0005 ≤ 0.001
        _bar(3, 99, 104, 98, 103),  # bullish, close > (105+96)/2 = 100.5
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDetectDoji:
    def test_doji_detected(self, tmp_path: Path) -> None:
        cache_dir = _write_bars(tmp_path, "DOJI", _doji_bars())
        result = detect(["DOJI"], cache_dir=cache_dir)
        # A true doji with small upper/lower wicks also satisfies spinning_top.
        assert "doji" in result["DOJI"]
        assert "spinning_top" in result["DOJI"]


class TestDetectHammer:
    def test_hammer_detected(self, tmp_path: Path) -> None:
        cache_dir = _write_bars(tmp_path, "HAMR", _hammer_bars())
        result = detect(["HAMR"], cache_dir=cache_dir)
        assert "hammer" in result["HAMR"]


class TestDetectBullishEngulfing:
    def test_bullish_engulfing_detected(self, tmp_path: Path) -> None:
        cache_dir = _write_bars(tmp_path, "BULL", _bullish_engulfing_bars())
        result = detect(["BULL"], cache_dir=cache_dir)
        assert "bullish_engulfing" in result["BULL"]


class TestDetectMorningStar:
    def test_morning_star_detected(self, tmp_path: Path) -> None:
        cache_dir = _write_bars(tmp_path, "MORN", _morning_star_bars())
        result = detect(["MORN"], cache_dir=cache_dir)
        assert "morning_star" in result["MORN"]


class TestMultiplePatterns:
    def test_dedup_and_sorted(self, tmp_path: Path) -> None:
        """If the same pattern fires across multiple windows it appears only once, sorted."""
        # Hammer bars repeated — the last bar is a hammer every time it's in the window
        bars = [
            _bar(1, 100, 101, 90, 100.5),
            _bar(2, 100, 101, 90, 100.5),
            _bar(3, 100, 101, 90, 100.5),
            _bar(4, 100, 101, 90, 100.5),
            _bar(5, 100, 101, 90, 100.5),
        ]
        cache_dir = _write_bars(tmp_path, "MULTI", bars)
        result = detect(["MULTI"], cache_dir=cache_dir, lookback_bars=3)
        assert result["MULTI"] == sorted(result["MULTI"])
        # hammer should appear at most once
        assert result["MULTI"].count("hammer") <= 1


class TestTooFewBars:
    def test_two_bars_empty(self, tmp_path: Path) -> None:
        bars = [
            _bar(1, 100, 102, 98, 101),
            _bar(2, 101, 103, 99, 102),
        ]
        cache_dir = _write_bars(tmp_path, "TWO", bars)
        result = detect(["TWO"], cache_dir=cache_dir)
        assert result == {"TWO": []}

    def test_one_bar_empty(self, tmp_path: Path) -> None:
        bars = [_bar(1, 100, 102, 98, 101)]
        cache_dir = _write_bars(tmp_path, "ONE", bars)
        result = detect(["ONE"], cache_dir=cache_dir)
        assert result == {"ONE": []}


class TestMissingSymbol:
    def test_symbol_not_in_store(self, tmp_path: Path) -> None:
        result = detect(["GHOST"], cache_dir=tmp_path)
        assert result == {"GHOST": []}


class TestLookbackBars:
    def test_lookback_limits_windows(self, tmp_path: Path) -> None:
        """With 5 bars and lookback_bars=1 only the single most-recent window is checked."""
        bars = [
            _bar(1, 99, 103, 97, 102),
            _bar(2, 100, 104, 96, 101),
            _bar(3, 100, 105, 95, 98),  # bearish
            _bar(4, 96, 110, 94, 101),  # this + bar3 would be bullish engulfing
            _bar(5, 100, 101, 90, 100.5),  # hammer in the LAST window only
        ]
        cache_dir = _write_bars(tmp_path, "LB", bars)
        result = detect(["LB"], cache_dir=cache_dir, lookback_bars=1)
        # Only the last window [bar3, bar4, bar5] is checked.
        # bar5 is a hammer → hammer detected.
        # bar3+bar4 form bullish_engulfing → also detected.
        assert "hammer" in result["LB"]


class TestEmptySymbols:
    def test_empty_input(self, tmp_path: Path) -> None:
        result = detect([], cache_dir=tmp_path)
        assert result == {}


class TestNoHttp:
    def test_no_url_references(self) -> None:
        """Verify the test file has no real URL references."""
        import re

        text = Path(__file__).read_text()
        assert not re.search(r"ht" r"tps?://", text), "test file contains a URL"