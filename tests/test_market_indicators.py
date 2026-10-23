"""P2 analytics: pure indicators against a golden bar fixture.

Two kinds of assertion live here:

* **Golden values.** ``tests/fixtures/bars_golden.json`` is a deterministic
  320-bar series (a documented formula, no provider bytes); the expected
  numbers below were computed by an independent reference implementation from
  the fixture file and are pinned as literals. A refactor that changes a
  definition must therefore fail here and explain itself.
* **Absence is not zero.** A short window, an empty series, a NaN, a flat
  benchmark: each returns ``None`` (or a measured ``0.0`` where zero is the
  truth), never a fabricated neutral value.

Parity with ``app.providers.adapters.yahoo`` is asserted on the same bars, so
the analytics layer and the provider adapter cannot drift into two different
definitions of RSI or volatility.
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.market import indicators as ind
from app.providers.adapters.yahoo import _atr as yahoo_atr
from app.providers.adapters.yahoo import _ema as yahoo_ema
from app.providers.adapters.yahoo import _rsi as yahoo_rsi
from app.providers.adapters.yahoo import _sma as yahoo_sma
from app.providers.adapters.yahoo import _volatility as yahoo_volatility

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "bars_golden.json"
GOLDEN: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
BARS: list[dict[str, Any]] = GOLDEN["bars"]
CLOSES: list[float] = [bar["close"] for bar in BARS]
BENCHMARK: list[float] = [bar["close"] for bar in GOLDEN["benchmark"]]

#: Independent reference values for the last bar of ``CLOSES``.
SMA_20 = 121.71845
SMA_50 = 121.442818
SMA_200 = 114.498194
EMA_12 = 118.26073291291442
EMA_26 = 120.4119629577859
RSI_14 = 15.61587010847461
ATR_14 = 2.7733620705781283
MACD_LINE = -2.1512300448714825
MACD_SIGNAL = -0.8461222601797278
MACD_HISTOGRAM = -1.3051077846917547
BOLL_MIDDLE = 121.71845
BOLL_UPPER = 130.09602697845864
BOLL_LOWER = 113.34087302154137
BOLL_WIDTH = 13.765500593309616
VOLATILITY_20 = 9.862639972246217
BETA_60 = 0.6471019997272002
MAX_DRAWDOWN = -11.073956015973064
CURRENT_DRAWDOWN = -10.455236858690341
DRAWDOWN_PEAK = 122.1307
DRAWDOWN_TROUGH = 108.606
PERFORMANCE = {
    "ret_1d": -0.37422906966424474,
    "ret_1w": -4.975125212039133,
    "ret_1m": -7.47804487153333,
    "ret_3m": 2.4529488518688636,
    "ret_6m": 3.731089300221768,
    "ret_1y": 11.190938511326864,
}

TOL = dict(rel=1e-12, abs=1e-12)


def _bars_for(prices: list[tuple[float, float, float]]) -> list[SimpleNamespace]:
    return [SimpleNamespace(high=high, low=low, close=close) for high, low, close in prices]


# ---------------------------------------------------------------------------
# Fixture integrity: a golden file that lies makes every test below worthless
# ---------------------------------------------------------------------------


def test_golden_fixture_is_complete_and_internally_consistent() -> None:
    assert GOLDEN["symbol"] == "GOLDEN"
    assert len(BARS) == 320
    assert len(BENCHMARK) == len(BARS)
    days = [date.fromisoformat(bar["date"]) for bar in BARS]
    assert days == sorted(days)
    assert len(set(days)) == len(days)
    assert days[0] == date(2024, 1, 2) and days[-1] == date(2025, 3, 24)
    assert all(day.weekday() < 5 for day in days)
    for bar in BARS:
        assert bar["low"] <= min(bar["open"], bar["close"])
        assert max(bar["open"], bar["close"]) <= bar["high"]
        assert bar["low"] > 0 and bar["close"] > 0
        assert isinstance(bar["volume"], int) and bar["volume"] > 0
    assert [bar["date"] for bar in GOLDEN["benchmark"]] == [bar["date"] for bar in BARS]


# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------


def test_sma_matches_the_golden_values() -> None:
    assert ind.sma(CLOSES, 20) == pytest.approx(SMA_20, **TOL)
    assert ind.sma(CLOSES, 50) == pytest.approx(SMA_50, **TOL)
    assert ind.sma(CLOSES, 200) == pytest.approx(SMA_200, **TOL)


def test_sma_is_the_mean_of_the_last_window_only() -> None:
    assert ind.sma([1.0, 2.0, 3.0, 4.0, 5.0], 3) == 4.0
    assert ind.sma([1.0, 2.0], 3) is None


def test_ema_matches_the_golden_values() -> None:
    assert ind.ema(CLOSES, 12) == pytest.approx(EMA_12, **TOL)
    assert ind.ema(CLOSES, 26) == pytest.approx(EMA_26, **TOL)


def test_ema_seeds_with_the_sma_then_applies_alpha() -> None:
    # seed = mean(1,2,3) = 2; alpha = 0.5 -> 3, then 4.
    assert ind.ema([1.0, 2.0, 3.0, 4.0, 5.0], 3) == 4.0
    assert ind.ema([1.0, 2.0], 3) is None


# ---------------------------------------------------------------------------
# RSI, ATR, MACD, Bollinger
# ---------------------------------------------------------------------------


def test_rsi_matches_the_golden_value() -> None:
    assert ind.rsi(CLOSES, 14) == pytest.approx(RSI_14, **TOL)


def test_rsi_bounds_are_measured_not_assumed() -> None:
    rising = [float(value) for value in range(1, 21)]
    falling = list(reversed(rising))
    assert ind.rsi(rising) == pytest.approx(100.0)
    assert ind.rsi(falling) == pytest.approx(0.0)
    # 0/0 is undefined; 50 is the neutral reading, not a fabricated 100.
    assert ind.rsi([50.0] * 20) == 50.0


def test_atr_matches_the_golden_value() -> None:
    assert ind.atr(BARS, 14) == pytest.approx(ATR_14, **TOL)


def test_atr_of_a_constant_two_point_range_is_two() -> None:
    bars = _bars_for([(11.0, 9.0, 10.0)] * 6)
    assert ind.atr(bars, 3) == 2.0


def test_macd_matches_the_golden_values() -> None:
    result = ind.macd(CLOSES, 12, 26, 9)
    assert result is not None
    assert result.macd_line == pytest.approx(MACD_LINE, **TOL)
    assert result.signal_line == pytest.approx(MACD_SIGNAL, **TOL)
    assert result.histogram == pytest.approx(MACD_HISTOGRAM, **TOL)
    assert result.histogram == pytest.approx(result.macd_line - result.signal_line, **TOL)


def test_macd_rejects_fast_not_shorter_than_slow() -> None:
    with pytest.raises(ValueError):
        ind.macd(CLOSES, 26, 12, 9)
    with pytest.raises(ValueError):
        ind.macd(CLOSES, 12, 12, 9)


def test_bollinger_matches_the_golden_values() -> None:
    result = ind.bollinger(CLOSES, 20, 2.0)
    assert result is not None
    assert result.middle == pytest.approx(BOLL_MIDDLE, **TOL)
    assert result.upper == pytest.approx(BOLL_UPPER, **TOL)
    assert result.lower == pytest.approx(BOLL_LOWER, **TOL)
    assert result.width_pct == pytest.approx(BOLL_WIDTH, rel=1e-11)


def test_bollinger_of_a_flat_series_has_zero_width_not_none() -> None:
    result = ind.bollinger([100.0] * 20, 20)
    assert result is not None
    assert (result.middle, result.upper, result.lower) == (100.0, 100.0, 100.0)
    assert result.width_pct == 0.0


# ---------------------------------------------------------------------------
# Dispersion and beta
# ---------------------------------------------------------------------------


def test_volatility_matches_the_golden_value() -> None:
    assert ind.volatility(CLOSES, 20) == pytest.approx(VOLATILITY_20, **TOL)


def test_volatility_of_a_flat_price_path_is_a_measured_zero() -> None:
    assert ind.volatility([100.0] * 21, 20) == 0.0


def test_beta_matches_the_golden_value() -> None:
    assert ind.beta(CLOSES, BENCHMARK, 60) == pytest.approx(BETA_60, **TOL)


def test_beta_of_a_series_against_itself_is_one() -> None:
    assert ind.beta(CLOSES, CLOSES, 60) == pytest.approx(1.0, **TOL)


def test_beta_with_a_flat_benchmark_is_unknown_not_zero() -> None:
    assert ind.beta(CLOSES, [100.0] * len(CLOSES), 60) is None


def test_beta_requires_aligned_series_instead_of_trimming() -> None:
    with pytest.raises(ValueError, match="aligned"):
        ind.beta(CLOSES, BENCHMARK[:-3], 60)


# ---------------------------------------------------------------------------
# Performance and drawdown
# ---------------------------------------------------------------------------


def test_performance_matches_the_golden_values() -> None:
    result = ind.performance(CLOSES)
    assert set(result) == {name for name, _ in ind.PERFORMANCE_LOOKBACKS}
    for name, expected in PERFORMANCE.items():
        assert result[name] == pytest.approx(expected, **TOL)
    # 320 bars cannot answer 3y/5y: the columns exist with None, they are not dropped.
    assert result["ret_3y"] is None and result["ret_5y"] is None


def test_performance_is_percent_points_over_bar_counts() -> None:
    result = ind.performance([100.0, 110.0, 121.0], [("ret_1d", 1), ("ret_2d", 2)])
    assert result == {"ret_1d": pytest.approx(10.0), "ret_2d": pytest.approx(21.0)}
    assert ind.performance([100.0], [("ret_1d", 1)]) == {"ret_1d": None}


def test_performance_rejects_duplicate_lookbacks() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        ind.performance(CLOSES, [("ret_1d", 1), ("ret_1d", 2)])


def test_drawdown_matches_the_golden_values() -> None:
    result = ind.drawdown(CLOSES)
    assert result is not None
    assert result.max_drawdown_pct == pytest.approx(MAX_DRAWDOWN, **TOL)
    assert result.current_drawdown_pct == pytest.approx(CURRENT_DRAWDOWN, **TOL)
    assert result.peak_close == pytest.approx(DRAWDOWN_PEAK, **TOL)
    assert result.trough_close == pytest.approx(DRAWDOWN_TROUGH, **TOL)


def test_drawdown_of_a_monotonic_rise_is_zero() -> None:
    result = ind.drawdown([100.0, 105.0, 110.0])
    assert result is not None
    assert result.max_drawdown_pct == 0.0
    assert result.current_drawdown_pct == 0.0


def test_drawdown_tracks_the_peak_of_the_worst_segment() -> None:
    result = ind.drawdown([100.0, 110.0, 99.0, 105.0])
    assert result is not None
    assert result.max_drawdown_pct == pytest.approx(-10.0)
    assert result.current_drawdown_pct == pytest.approx((105.0 / 110.0 - 1.0) * 100.0)
    assert result.peak_close == 110.0 and result.trough_close == 99.0


# ---------------------------------------------------------------------------
# Absence: insufficient history, bad input, no zero fabrication
# ---------------------------------------------------------------------------


def test_short_history_returns_none_not_zero() -> None:
    closes = CLOSES[:10]
    assert ind.sma(closes, 20) is None
    assert ind.ema(closes, 12) is None
    assert ind.rsi(closes, 14) is None
    assert ind.atr(BARS[:10], 14) is None
    assert ind.macd(closes) is None
    assert ind.bollinger(closes, 20) is None
    assert ind.volatility(closes, 20) is None
    assert ind.beta(closes, BENCHMARK[:10], 60) is None
    partial = ind.performance(closes)
    assert partial["ret_1d"] is not None and partial["ret_1w"] is not None
    assert partial["ret_1m"] is None and partial["ret_1y"] is None
    # drawdown is defined from the first bar; 10 bars are enough for it.
    assert ind.drawdown(closes) is not None


def test_empty_series_returns_none_or_empty_columns() -> None:
    assert ind.sma([], 5) is None
    assert ind.ema([], 5) is None
    assert ind.rsi([]) is None
    assert ind.atr([], 14) is None
    assert ind.macd([]) is None
    assert ind.bollinger([]) is None
    assert ind.volatility([]) is None
    assert ind.drawdown([]) is None
    assert ind.performance([]) == {name: None for name, _ in ind.PERFORMANCE_LOOKBACKS}


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_series_is_unknown_not_partially_averaged(bad: float) -> None:
    dirty = [*CLOSES[:30], bad]
    assert ind.sma(dirty, 20) is None
    assert ind.ema(dirty, 12) is None
    assert ind.rsi(dirty) is None
    assert ind.bollinger(dirty, 20) is None
    assert ind.volatility(dirty, 20) is None
    assert ind.drawdown(dirty) is None
    assert ind.performance(dirty)["ret_1d"] is None


def test_atr_rejects_a_broken_bar_envelope() -> None:
    inverted = _bars_for([(9.0, 11.0, 10.0)] * 6)
    assert ind.atr(inverted, 3) is None
    missing = [{"high": 11.0, "low": 9.0} for _ in range(6)]
    assert ind.atr(missing, 3) is None


def test_bars_are_duck_typed_for_mappings_and_objects() -> None:
    as_dicts = [{"high": bar["high"], "low": bar["low"], "close": bar["close"]} for bar in BARS]
    as_objects = [SimpleNamespace(**bar) for bar in as_dicts]
    assert ind.atr(as_dicts, 14) == ind.atr(BARS, 14)
    assert ind.atr(as_objects, 14) == ind.atr(BARS, 14)


@pytest.mark.parametrize(
    "call",
    [
        lambda: ind.sma(CLOSES, 0),
        lambda: ind.sma(CLOSES, -5),
        lambda: ind.sma(CLOSES, True),
        lambda: ind.ema(CLOSES, 0),
        lambda: ind.rsi(CLOSES, 0),
        lambda: ind.atr(BARS, 0),
        lambda: ind.bollinger(CLOSES, 20, 0.0),
        lambda: ind.bollinger(CLOSES, 20, float("nan")),
        lambda: ind.volatility(CLOSES, 20, 0.0),
        lambda: ind.volatility(CLOSES, 20, float("inf")),
        lambda: ind.beta(CLOSES, BENCHMARK, 0),
        lambda: ind.performance(CLOSES, [("", 1)]),
    ],
)
def test_bad_windows_raise_instead_of_returning_a_number(call: Any) -> None:
    with pytest.raises(ValueError):
        call()


# ---------------------------------------------------------------------------
# Provider-adapter parity: one definition, two call sites
# ---------------------------------------------------------------------------


def test_yahoo_adapter_agrees_with_the_analytics_layer_on_golden_bars() -> None:
    bars = [SimpleNamespace(high=b["high"], low=b["low"], close=b["close"]) for b in BARS]
    assert ind.sma(CLOSES, 20) == pytest.approx(yahoo_sma(CLOSES, 20), **TOL)
    assert ind.sma(CLOSES, 200) == pytest.approx(yahoo_sma(CLOSES, 200), **TOL)
    assert ind.ema(CLOSES, 12) == pytest.approx(yahoo_ema(CLOSES, 12), **TOL)
    assert ind.ema(CLOSES, 26) == pytest.approx(yahoo_ema(CLOSES, 26), **TOL)
    assert ind.rsi(CLOSES, 14) == pytest.approx(yahoo_rsi(CLOSES, 14), **TOL)
    assert ind.atr(bars, 14) == pytest.approx(yahoo_atr(bars, 14), **TOL)
    assert ind.volatility(CLOSES, 20) == pytest.approx(yahoo_volatility(CLOSES, 20), **TOL)


def test_indicators_are_pure_and_do_not_mutate_their_input() -> None:
    closes = list(CLOSES)
    snapshot = list(closes)
    assert ind.sma(closes, 20) == ind.sma(closes, 20)
    assert ind.rsi(closes) == ind.rsi(closes)
    assert ind.drawdown(closes) == ind.drawdown(closes)
    assert closes == snapshot
    assert math.isfinite(ind.sma(closes, 20) or 0.0)
