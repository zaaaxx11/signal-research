"""Technical indicators: pure functions over close series and OHLC bars.

Nothing in this module fetches, caches, or looks at a clock. A function is a
value in, value out: the same bars always produce the same number, which is
what makes a golden fixture a test instead of a snapshot of the weather.

**Conventions**

* A price is in the instrument's quote currency; ``rsi``, ``volatility``,
  ``performance``, ``drawdown`` and Bollinger ``width_pct`` are **percent
  points** (``12.5`` is 12.5 %), matching the canonical columns and
  ``app.providers.adapters.yahoo``. ``atr`` is a price, ``beta`` is a ratio.
* Wilder's smoothing seeds with the mean of the first window, exactly like the
  provider adapter, so an indicator computed here and one computed there are
  the same number (the parity suite pins it).
* **Missing is ``None``, never ``0``.** A window that is not full yet, a
  non-finite input, or a degenerate sample (zero benchmark variance, flat
  benchmark) returns ``None``: the honest answer is "unknown", not a fabricated
  neutral value. A real zero (a flat price path has zero volatility) stays
  ``0.0``.
* A series containing ``NaN``/``inf`` is treated as unknown in full: a hole
  cannot be averaged away without inventing the missing bar.

Bars are duck-typed: any object with ``high``/``low``/``close`` attributes, or
a mapping with those keys, is a bar. The bar store may therefore own its own
record type without this module importing it (analytics must not depend on
storage).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

#: Sessions in a year for annualising volatility (matches the provider adapter).
TRADING_DAYS = 252.0

#: Canonical performance lookbacks: column name -> bars back from the last bar.
#: Bar counts, not calendar months: the date-anchored ``ret_ytd`` needs the bar
#: store's dates and is deliberately absent here.
PERFORMANCE_LOOKBACKS: tuple[tuple[str, int], ...] = (
    ("ret_1d", 1),
    ("ret_1w", 5),
    ("ret_1m", 21),
    ("ret_3m", 63),
    ("ret_6m", 126),
    ("ret_1y", 252),
    ("ret_3y", 756),
    ("ret_5y", 1260),
)


class BarLike(Protocol):
    """The minimum a bar must expose to price an ATR."""

    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class Macd:
    """MACD(``fast``, ``slow``, ``signal``) at the last bar."""

    macd_line: float
    signal_line: float
    histogram: float


@dataclass(frozen=True, slots=True)
class Bollinger:
    """Bollinger bands at the last bar; ``width_pct`` is percent of ``middle``."""

    middle: float
    upper: float
    lower: float
    width_pct: float | None


@dataclass(frozen=True, slots=True)
class Drawdown:
    """Deepest and current drawdown of one close series, in percent points.

    A drawdown is signed: ``-20.0`` means 20 % below the running peak, and
    ``0.0`` at a new high. ``peak_close``/``trough_close`` are the high-water
    mark and the low that produced ``max_drawdown_pct``.
    """

    max_drawdown_pct: float
    current_drawdown_pct: float
    peak_close: float
    trough_close: float


# ---------------------------------------------------------------------------
# Input hygiene
# ---------------------------------------------------------------------------


def _window(value: int, *, name: str = "window") -> int:
    """A window is a programmer decision: a bad one raises, it does not return 0."""

    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be an int >= 1, got {value!r}")
    return value


def _series(values: Sequence[float]) -> list[float] | None:
    """Floats, or ``None`` when any element is absent/non-finite/not numeric."""

    out: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        out.append(number)
    return out


def _bar_number(bar: BarLike | Mapping[str, Any], name: str) -> float | None:
    raw = bar.get(name) if isinstance(bar, Mapping) else getattr(bar, name, None)
    try:
        number = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
    if number is None or not math.isfinite(number):
        return None
    return number


def _bars(series: Sequence[BarLike | Mapping[str, Any]]) -> list[tuple[float, float, float]] | None:
    """``(high, low, close)`` per bar, or ``None`` if any bar is unusable."""

    out: list[tuple[float, float, float]] = []
    for bar in series:
        high = _bar_number(bar, "high")
        low = _bar_number(bar, "low")
        close = _bar_number(bar, "close")
        if high is None or low is None or close is None or high < low:
            return None
        out.append((high, low, close))
    return out


def _ema_series(values: Sequence[float], window: int) -> list[float] | None:
    """EMA seeded with the SMA of the first window; index 0 is bar ``window-1``."""

    if len(values) < window:
        return None
    alpha = 2.0 / (window + 1.0)
    ema = sum(values[:window]) / window
    out = [ema]
    for value in values[window:]:
        ema = value * alpha + ema * (1.0 - alpha)
        out.append(ema)
    return out


# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------


def sma(values: Sequence[float], window: int) -> float | None:
    """Arithmetic mean of the last ``window`` values, or ``None`` short of it."""

    length = _window(window)
    series = _series(values)
    if series is None or len(series) < length:
        return None
    return sum(series[-length:]) / length


def ema(values: Sequence[float], window: int) -> float | None:
    """Exponential mean: SMA seed, then ``alpha = 2 / (N + 1)``."""

    length = _window(window)
    series = _series(values)
    if series is None:
        return None
    out = _ema_series(series, length)
    return out[-1] if out else None


# ---------------------------------------------------------------------------
# Momentum and range
# ---------------------------------------------------------------------------


def rsi(closes: Sequence[float], window: int = 14) -> float | None:
    """Wilder's RSI in percent points.

    A perfectly flat window is ``50.0``: ``0/0`` is undefined, and returning
    100 would claim a window of pure gains that never happened.
    """

    length = _window(window)
    series = _series(closes)
    if series is None or len(series) < length + 1:
        return None
    changes = [cur - prev for prev, cur in zip(series[:-1], series[1:])]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = sum(gains[:length]) / length
    avg_loss = sum(losses[:length]) / length
    for gain, loss in zip(gains[length:], losses[length:]):
        avg_gain = (avg_gain * (length - 1) + gain) / length
        avg_loss = (avg_loss * (length - 1) + loss) / length
    if avg_loss == 0:
        return 50.0 if avg_gain == 0 else 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def atr(bars: Sequence[BarLike | Mapping[str, Any]], window: int = 14) -> float | None:
    """Wilder's ATR over true range, seeded with the first window's mean."""

    length = _window(window)
    series = _bars(bars)
    if series is None or len(series) < length + 1:
        return None
    ranges = [
        max(high - low, abs(high - prev_close), abs(low - prev_close))
        for (_, _, prev_close), (high, low, _) in zip(series[:-1], series[1:])
    ]
    value = sum(ranges[:length]) / length
    for true_range in ranges[length:]:
        value = (value * (length - 1) + true_range) / length
    return value


def macd(
    closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> Macd | None:
    """MACD line, its EMA signal, and the histogram at the last bar."""

    fast_n = _window(fast, name="fast")
    slow_n = _window(slow, name="slow")
    signal_n = _window(signal, name="signal")
    if fast_n >= slow_n:
        raise ValueError(f"fast ({fast_n}) must be shorter than slow ({slow_n})")
    series = _series(closes)
    if series is None:
        return None
    fast_series = _ema_series(series, fast_n)
    slow_series = _ema_series(series, slow_n)
    if fast_series is None or slow_series is None:
        return None
    offset = slow_n - fast_n
    line = [
        fast_series[offset + index] - value for index, value in enumerate(slow_series)
    ]
    signal_series = _ema_series(line, signal_n)
    if signal_series is None:
        return None
    return Macd(
        macd_line=line[-1],
        signal_line=signal_series[-1],
        histogram=line[-1] - signal_series[-1],
    )


def bollinger(
    closes: Sequence[float], window: int = 20, num_std: float = 2.0
) -> Bollinger | None:
    """SMA middle band and population-stdev bands; ``width_pct`` of the middle."""

    length = _window(window)
    if not isinstance(num_std, (int, float)) or isinstance(num_std, bool):
        raise ValueError(f"num_std must be a number > 0, got {num_std!r}")
    if not math.isfinite(float(num_std)) or num_std <= 0:
        raise ValueError(f"num_std must be a finite number > 0, got {num_std!r}")
    series = _series(closes)
    if series is None or len(series) < length:
        return None
    window_values = series[-length:]
    middle = sum(window_values) / length
    variance = sum((value - middle) ** 2 for value in window_values) / length
    deviation = math.sqrt(variance)
    upper = middle + float(num_std) * deviation
    lower = middle - float(num_std) * deviation
    width = (upper - lower) / middle * 100.0 if middle != 0 else None
    return Bollinger(middle=middle, upper=upper, lower=lower, width_pct=width)


# ---------------------------------------------------------------------------
# Dispersion and co-movement
# ---------------------------------------------------------------------------


def volatility(
    closes: Sequence[float], window: int = 20, trading_days: float = TRADING_DAYS
) -> float | None:
    """Annualised stdev of log returns over ``window`` returns, in percent.

    Population stdev, matching the provider adapter; a constant price path is
    ``0.0`` because that is a measured zero, not a missing value.
    """

    length = _window(window)
    if not isinstance(trading_days, (int, float)) or isinstance(trading_days, bool):
        raise ValueError(f"trading_days must be a number > 0, got {trading_days!r}")
    if not math.isfinite(float(trading_days)) or trading_days <= 0:
        raise ValueError(f"trading_days must be a finite number > 0, got {trading_days!r}")
    series = _series(closes)
    if series is None or len(series) < length + 1:
        return None
    window_values = series[-length - 1 :]
    if any(value <= 0 for value in window_values):
        return None
    returns = [
        math.log(cur / prev) for prev, cur in zip(window_values[:-1], window_values[1:])
    ]
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / len(returns)
    return math.sqrt(variance) * math.sqrt(float(trading_days)) * 100.0


def beta(
    asset_closes: Sequence[float],
    benchmark_closes: Sequence[float],
    window: int = 60,
) -> float | None:
    """CAPM beta of simple returns over the last ``window`` bars.

    The two series must be **aligned bar-for-bar** (same trading calendar, same
    length). A mismatch raises instead of trimming the longer one: silently
    comparing Tuesday's asset return with Monday's benchmark return produces a
    precise-looking number with no meaning. A flat benchmark (zero variance)
    has no beta and returns ``None``.
    """

    length = _window(window)
    asset = _series(asset_closes)
    benchmark = _series(benchmark_closes)
    if asset is None or benchmark is None:
        return None
    if len(asset) != len(benchmark):
        raise ValueError(
            "asset and benchmark series must be aligned: "
            f"{len(asset)} != {len(benchmark)} closes"
        )
    if len(asset) < length + 1:
        return None
    asset_window = asset[-length - 1 :]
    benchmark_window = benchmark[-length - 1 :]
    if any(value <= 0 for value in asset_window) or any(
        value <= 0 for value in benchmark_window
    ):
        return None
    asset_returns = [
        cur / prev - 1.0 for prev, cur in zip(asset_window[:-1], asset_window[1:])
    ]
    benchmark_returns = [
        cur / prev - 1.0 for prev, cur in zip(benchmark_window[:-1], benchmark_window[1:])
    ]
    asset_mean = sum(asset_returns) / length
    benchmark_mean = sum(benchmark_returns) / length
    covariance = (
        sum(
            (a - asset_mean) * (b - benchmark_mean)
            for a, b in zip(asset_returns, benchmark_returns)
        )
        / length
    )
    benchmark_variance = (
        sum((value - benchmark_mean) ** 2 for value in benchmark_returns) / length
    )
    if benchmark_variance == 0:
        return None
    return covariance / benchmark_variance


# ---------------------------------------------------------------------------
# Performance and drawdown
# ---------------------------------------------------------------------------


def performance(
    closes: Sequence[float], lookbacks: Sequence[tuple[str, int]] = PERFORMANCE_LOOKBACKS
) -> dict[str, float | None]:
    """Percent return over each bar-count lookback, keyed by canonical name.

    Always returns every requested key: a short history yields ``None`` for that
    horizon rather than dropping the column (a table with a missing cell is
    honest; a table with a silently absent column is not). ``ret_ytd`` is not
    here because it needs the calendar, not just closes.
    """

    names: set[str] = set()
    for name, bars in lookbacks:
        if not isinstance(name, str) or not name:
            raise ValueError(f"lookback name must be a non-empty string, got {name!r}")
        if name in names:
            raise ValueError(f"duplicate lookback name {name!r}")
        names.add(name)
        _window(bars, name=f"lookback {name}")
    series = _series(closes)
    out: dict[str, float | None] = {}
    for name, bars in lookbacks:
        if series is None or len(series) < bars + 1 or series[-bars - 1] <= 0:
            out[name] = None
        else:
            out[name] = (series[-1] / series[-bars - 1] - 1.0) * 100.0
    return out


def drawdown(closes: Sequence[float]) -> Drawdown | None:
    """Deepest and current drawdown of the series, signed in percent points."""

    series = _series(closes)
    if not series or any(value <= 0 for value in series):
        return None
    peak = series[0]
    peak_close = series[0]
    trough_close = series[0]
    max_drawdown = 0.0
    for value in series[1:]:
        if value > peak:
            peak = value
            continue
        drop = (value / peak - 1.0) * 100.0
        if drop < max_drawdown:
            max_drawdown = drop
            peak_close = peak
            trough_close = value
    current = (series[-1] / max(series) - 1.0) * 100.0
    return Drawdown(
        max_drawdown_pct=max_drawdown,
        current_drawdown_pct=current,
        peak_close=peak_close,
        trough_close=trough_close,
    )


__all__ = [
    "PERFORMANCE_LOOKBACKS",
    "TRADING_DAYS",
    "BarLike",
    "Bollinger",
    "Drawdown",
    "Macd",
    "atr",
    "beta",
    "bollinger",
    "drawdown",
    "ema",
    "macd",
    "performance",
    "rsi",
    "sma",
    "volatility",
]
