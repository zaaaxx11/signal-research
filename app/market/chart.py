"""Chart metrics: pure formulas, no network.

The chart endpoint is the cheap path for performance/technical fields. It uses
raw split-adjusted ``close`` (not ``adjclose``) because that is what the Finviz
comparison reproduces; every function here is independent of the depth collector
so the formulas can be tested from a fixture.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class ChartBar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _bars(entry: Mapping[str, Any]) -> list[ChartBar]:
    timestamps = entry.get("timestamp") or []
    quote = ((entry.get("indicators") or {}).get("quote") or [{}])[0]
    if not isinstance(quote, Mapping):
        return []
    keys = ("open", "high", "low", "close", "volume")
    values = {key: quote.get(key) or [] for key in keys}
    out: list[ChartBar] = []
    for index, stamp in enumerate(timestamps):
        if not isinstance(stamp, (int, float)):
            continue
        row = {}
        for key in keys:
            series = values[key]
            row[key] = _number(series[index]) if index < len(series) else None
        if any(row[key] is None for key in ("open", "high", "low", "close")):
            continue
        open_, high, low, close = row["open"], row["high"], row["low"], row["close"]
        if high < max(open_, close) or low > min(open_, close):
            continue
        out.append(
            ChartBar(
                day=datetime.fromtimestamp(float(stamp), UTC).date(),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=row["volume"] or 0.0,
            )
        )
    return out


def _shift_months(moment: date, months: int) -> date:
    year = moment.year + (moment.month - 1 + months) // 12
    month = (moment.month - 1 + months) % 12 + 1
    days = (31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[month - 1]
    return date(year, month, min(moment.day, days))


def _anchor(bars: Sequence[ChartBar], target: date) -> ChartBar | None:
    for bar in reversed(bars):
        if bar.day <= target:
            return bar
    return None


def _return_pct(bars: Sequence[ChartBar], target: date) -> float | None:
    anchor = _anchor(bars, target)
    if anchor is None or not anchor.close:
        return None
    return (bars[-1].close / anchor.close - 1.0) * 100.0


def _percent(close: float, base: float | None) -> float | None:
    if base is None or base == 0:
        return None
    return (close / base - 1.0) * 100.0


def _rsi(closes: Sequence[float], window: int = 14) -> float | None:
    if len(closes) <= window:
        return None
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = sum(gains[:window]) / window
    avg_loss = sum(losses[:window]) / window
    for gain, loss in zip(gains[window:], losses[window:]):
        avg_gain = (avg_gain * (window - 1) + gain) / window
        avg_loss = (avg_loss * (window - 1) + loss) / window
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _atr(bars: Sequence[ChartBar], window: int = 14) -> float | None:
    if len(bars) <= window:
        return None
    ranges: list[float] = []
    for index in range(1, len(bars)):
        current, previous = bars[index], bars[index - 1]
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    value = sum(ranges[:window]) / window
    for item in ranges[window:]:
        value = (value * (window - 1) + item) / window
    return value


def parse_chart_metrics(
    entry: Mapping[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """Return canonical depth fields from one Yahoo chart response."""

    bars = _bars(entry)
    if not bars:
        return {}
    clock = now or datetime.now(UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    # A bar dated today is incomplete while the US session is still running.
    # The collector runs pre-open in production, so this branch is mostly a
    # guard for manual probes.
    if bars[-1].day == clock.date() and clock.hour < 21:
        bars = bars[:-1]
    if not bars:
        return {}

    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars]
    out: dict[str, Any] = {}
    for window, field in ((5, "perf_1w"), (21, "perf_4w"), (63, "perf_13w"), (126, "perf_26w")):
        if len(closes) > window and closes[-1 - window]:
            out[field] = (closes[-1] / closes[-1 - window] - 1.0) * 100.0

    for months, field in ((12, "perf_52w"), (36, "perf_3y"), (60, "perf_5y"), (120, "perf_10y")):
        value = _return_pct(bars, _shift_months(clock.date(), -months))
        if value is not None:
            out[field] = value
    previous_year = [bar for bar in bars if bar.day.year < clock.year]
    if previous_year and previous_year[-1].close:
        out["perf_ytd"] = (closes[-1] / previous_year[-1].close - 1.0) * 100.0

    for window in (20, 50, 200):
        if len(closes) >= window:
            base = sum(closes[-window:]) / window
            out[f"sma{window}"] = _percent(closes[-1], base)
    rsi = _rsi(closes)
    if rsi is not None:
        out["rsi"] = rsi
    atr = _atr(bars)
    if atr is not None:
        out["atr"] = atr
    for window, field in ((5, "volatility_1w"), (21, "volatility_4w")):
        if len(bars) >= window:
            out[field] = sum(
                (bar.high - bar.low) / bar.close * 100.0
                for bar in bars[-window:]
                if bar.close
            ) / window
    if volumes:
        window = min(65, len(volumes))
        average = sum(volumes[-window:]) / window
        out["average_volume"] = average
        if average and volumes[-1]:
            out["relative_volume"] = volumes[-1] / average

    cutoff = clock.date() - timedelta(days=365)
    year_bars = [bar for bar in bars if bar.day > cutoff]
    if year_bars:
        high = max(bar.high for bar in year_bars)
        low = min(bar.low for bar in year_bars)
        out["high_52w"] = high
        out["low_52w"] = low
        if low:
            out["week_52_range"] = (high - low) / low * 100.0
    last50 = bars[-50:]
    if last50:
        out["high_50d"] = max(bar.high for bar in last50)
        out["low_50d"] = min(bar.low for bar in last50)

    if len(bars) >= 2:
        previous, current = bars[-2], bars[-1]
        if previous.close:
            out["gap"] = (current.open - previous.close) / previous.close * 100.0
        if current.open:
            out["change_from_open"] = (current.close - current.open) / current.open * 100.0

    return {key: value for key, value in out.items() if value is not None}


__all__ = ["ChartBar", "parse_chart_metrics"]
