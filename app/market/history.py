"""Provider-agnostic OHLCV technical indicators: pure formulas, no network.

The chart endpoint (``app.market.chart``) computes its performance and
technical fields from a Yahoo response with a clock in hand. This module
carries the same math in a form any OHLCV source can feed: a list of
:class:`Bar` in, a flat dict of indicator fields out. No clock, no
provider types, no I/O — so a collector can swap Yahoo for Stooq, a CSV,
or a database without changing a single number.

Formulas are ported verbatim from ``app.market.chart``:

* performance over 5/21/63/126/252-bar windows, in percent;
* SMA 20/50/200 as **percent distance** from the latest close, not price;
* RSI 14 with Wilder smoothing (seeded by a simple average of the first
  fourteen changes, then ``(avg * 13 + change) / 14``);
* ATR 14 over the classic true range ``max(high - low, |high -
  prev_close|, |low - prev_close|)``, Wilder-smoothed the same way;
* volatility as the average of ``(high - low) / close * 100`` over 5 and
  21 bars;
* 52-week high/low and range position, average volume over the last 65
  sessions with relative volume, gap and change-from-open.

Fields that cannot be computed from the bars alone are omitted rather
than guessed — the same contract ``parse_chart_metrics`` keeps. Two
deviations from the chart endpoint are forced by purity (no clock here):

* the 52-week window is the last **252 bars** instead of a
  365-calendar-day window anchored at "now", and ``perf_52w`` is that
  same 252-bar window instead of a date 12 months back;
* a single bar returns ``{}`` — with no second bar there is no change,
  gap, or range to measure, and every remaining field would be a
  self-ratio.

Dividend growth, YTD and multi-year performance, and the 50-day high/low
are deliberately out of scope: they need event data or a calendar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class Bar:
    """One trading session of OHLCV data, from any provider."""

    date: date
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


def _atr(bars: Sequence[Bar], window: int = 14) -> float | None:
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


def compute_technicals(bars: list[Bar]) -> dict:
    """Return the technical-indicator fields for one symbol's bar history.

    Pure function of ``bars``: the input is never mutated, no clock is
    consulted, and a field whose window has no data (or whose reference
    price is zero) is left out of the result entirely.
    """
    if len(bars) < 2:
        return {}
    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars]
    out: dict[str, Any] = {}

    for window, field in (
        (5, "perf_1w"),
        (21, "perf_4w"),
        (63, "perf_13w"),
        (126, "perf_26w"),
        (252, "perf_52w"),
    ):
        if len(closes) > window and closes[-1 - window]:
            out[field] = (closes[-1] / closes[-1 - window] - 1.0) * 100.0

    for window in (20, 50, 200):
        if len(closes) >= window:
            base = sum(closes[-window:]) / window
            if base:
                out[f"sma{window}"] = (closes[-1] / base - 1.0) * 100.0

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

    year_bars = bars[-252:]
    if year_bars:
        high = max(bar.high for bar in year_bars)
        low = min(bar.low for bar in year_bars)
        out["high_52w"] = high
        out["low_52w"] = low
        if low:
            out["week_52_range"] = (high - low) / low * 100.0

    previous, current = bars[-2], bars[-1]
    if previous.close:
        out["gap"] = (current.open - previous.close) / previous.close * 100.0
    if current.open:
        out["change_from_open"] = (current.close - current.open) / current.open * 100.0

    return {key: value for key, value in out.items() if value is not None}


def _result_entry(payload: Any) -> Mapping[str, Any] | None:
    """Locate the quote entry in a Yahoo v8 response (or a bare entry)."""
    if not isinstance(payload, Mapping):
        return None
    for source in (payload.get("chart"), payload):
        if not isinstance(source, Mapping):
            continue
        result = source.get("result")
        if isinstance(result, Sequence) and not isinstance(result, (str, bytes)):
            if not result:
                return None
            first = result[0]
            return first if isinstance(first, Mapping) else None
    if "timestamp" in payload or "indicators" in payload:
        return payload
    return None


def parse_bars(payload: dict) -> list[Bar]:
    """Convert a Yahoo v8 chart response into :class:`Bar` objects.

    Accepts the full response (``{"chart": {"result": [entry]}}``) or the
    bare result entry. Only ``indicators.quote`` is read — ``adjclose``
    is ignored, matching the chart endpoint's raw-close contract. Rows
    with a missing/non-finite OHLC value, a non-numeric timestamp, or a
    high/low that contradicts the open/close are skipped; a missing
    volume becomes ``0.0``.
    """
    entry = _result_entry(payload)
    if entry is None:
        return []
    timestamps = entry.get("timestamp") or []
    quote = ((entry.get("indicators") or {}).get("quote") or [{}])[0]
    if not isinstance(quote, Mapping):
        return []
    keys = ("open", "high", "low", "close", "volume")
    values = {key: quote.get(key) or [] for key in keys}
    out: list[Bar] = []
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
            Bar(
                date=datetime.fromtimestamp(float(stamp), UTC).date(),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=row["volume"] or 0.0,
            )
        )
    return out


__all__ = ["Bar", "compute_technicals", "parse_bars"]
