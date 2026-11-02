"""28 candlestick pattern detectors.

Every function takes exactly three :class:`~app.market.chart.ChartBar` objects
(oldest first) and returns ``True`` when the pattern is present, ``False``
otherwise.  All patterns are computable from OHLCV alone; no indicator is
required.
"""

from __future__ import annotations

from typing import Sequence

from app.market.chart import ChartBar


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _body(bar: ChartBar) -> float:
    return abs(bar.close - bar.open)


def _range(bar: ChartBar) -> float:
    return bar.high - bar.low


def _upper_wick(bar: ChartBar) -> float:
    return bar.high - max(bar.open, bar.close)


def _lower_wick(bar: ChartBar) -> float:
    return min(bar.open, bar.close) - bar.low


def _is_bullish(bar: ChartBar) -> bool:
    return bar.close > bar.open


def _is_bearish(bar: ChartBar) -> bool:
    return bar.close < bar.open


def _is_doji(bar: ChartBar, tol: float = 0.001) -> bool:
    return _range(bar) > 0 and _body(bar) / _range(bar) <= tol


def _is_marubozu_bullish(bar: ChartBar, tol: float = 0.05) -> bool:
    rng = _range(bar)
    if rng == 0:
        return False
    return (
        _is_bullish(bar)
        and _lower_wick(bar) / rng <= tol
        and _upper_wick(bar) / rng <= tol
    )


def _is_marubozu_bearish(bar: ChartBar, tol: float = 0.05) -> bool:
    rng = _range(bar)
    if rng == 0:
        return False
    return (
        _is_bearish(bar)
        and _lower_wick(bar) / rng <= tol
        and _upper_wick(bar) / rng <= tol
    )


def _is_spinning_top(bar: ChartBar) -> bool:
    rng = _range(bar)
    if rng == 0:
        return False
    b = _body(bar) / rng
    u = _upper_wick(bar) / rng
    l = _lower_wick(bar) / rng
    return b <= 0.3 and u >= 0.2 and l >= 0.2


# ---------------------------------------------------------------------------
# 1-bar patterns
# ---------------------------------------------------------------------------

def hammer(bars: Sequence[ChartBar]) -> bool:
    """Single-bar hammer: small body near high, long lower wick, little/no upper wick."""

    if len(bars) < 1:
        return False
    bar = bars[-1]
    rng = _range(bar)
    if rng == 0:
        return False
    b = _body(bar)
    l = _lower_wick(bar)
    u = _upper_wick(bar)
    return l >= 2 * b and u <= b and l >= rng * 0.6


def hanging_man(bars: Sequence[ChartBar]) -> bool:
    """Hanging man: same shape as hammer but in an uptrend (prior bar higher close)."""

    if len(bars) < 2:
        return False
    prior = bars[-2]
    bar = bars[-1]
    rng = _range(bar)
    if rng == 0:
        return False
    b = _body(bar)
    l = _lower_wick(bar)
    u = _upper_wick(bar)
    return (
        prior.close > prior.open
        and l >= 2 * b
        and u <= b
        and l >= rng * 0.6
    )


def inverted_hammer(bars: Sequence[ChartBar]) -> bool:
    """Inverted hammer: small body near low, long upper wick."""

    if len(bars) < 1:
        return False
    bar = bars[-1]
    rng = _range(bar)
    if rng == 0:
        return False
    b = _body(bar)
    u = _upper_wick(bar)
    l = _lower_wick(bar)
    return u >= 2 * b and l <= b and u >= rng * 0.6


def shooting_star(bars: Sequence[ChartBar]) -> bool:
    """Shooting star: same shape as inverted hammer but in an uptrend."""

    if len(bars) < 2:
        return False
    prior = bars[-2]
    bar = bars[-1]
    rng = _range(bar)
    if rng == 0:
        return False
    b = _body(bar)
    u = _upper_wick(bar)
    l = _lower_wick(bar)
    return (
        prior.close > prior.open
        and u >= 2 * b
        and l <= b
        and u >= rng * 0.6
    )


def bullish_marubozu(bars: Sequence[ChartBar]) -> bool:
    return len(bars) >= 1 and _is_marubozu_bullish(bars[-1])


def bearish_marubozu(bars: Sequence[ChartBar]) -> bool:
    return len(bars) >= 1 and _is_marubozu_bearish(bars[-1])


def doji(bars: Sequence[ChartBar]) -> bool:
    return len(bars) >= 1 and _is_doji(bars[-1])


def spinning_top(bars: Sequence[ChartBar]) -> bool:
    return len(bars) >= 1 and _is_spinning_top(bars[-1])


def bullish_belt_hold(bars: Sequence[ChartBar]) -> bool:
    """Bullish belt hold: long bullish candle with no lower wick, opens at low."""

    if len(bars) < 1:
        return False
    bar = bars[-1]
    return (
        _is_bullish(bar)
        and _lower_wick(bar) == 0
        and _body(bar) > 0
    )


def bearish_belt_hold(bars: Sequence[ChartBar]) -> bool:
    """Bearish belt hold: long bearish candle with no upper wick, opens at high."""

    if len(bars) < 1:
        return False
    bar = bars[-1]
    return (
        _is_bearish(bar)
        and _upper_wick(bar) == 0
        and _body(bar) > 0
    )


# ---------------------------------------------------------------------------
# 2-bar patterns
# ---------------------------------------------------------------------------

def bullish_engulfing(bars: Sequence[ChartBar]) -> bool:
    if len(bars) < 2:
        return False
    prev, curr = bars[-2], bars[-1]
    return (
        _is_bearish(prev)
        and _is_bullish(curr)
        and curr.open < prev.close
        and curr.close > prev.open
    )


def bearish_engulfing(bars: Sequence[ChartBar]) -> bool:
    if len(bars) < 2:
        return False
    prev, curr = bars[-2], bars[-1]
    return (
        _is_bullish(prev)
        and _is_bearish(curr)
        and curr.open > prev.close
        and curr.close < prev.open
    )


def bullish_harami(bars: Sequence[ChartBar]) -> bool:
    if len(bars) < 2:
        return False
    prev, curr = bars[-2], bars[-1]
    return (
        _is_bearish(prev)
        and _is_bullish(curr)
        and curr.open > prev.close
        and curr.close < prev.open
    )


def bearish_harami(bars: Sequence[ChartBar]) -> bool:
    if len(bars) < 2:
        return False
    prev, curr = bars[-2], bars[-1]
    return (
        _is_bullish(prev)
        and _is_bearish(curr)
        and curr.open < prev.close
        and curr.close > prev.open
    )


def piercing_pattern(bars: Sequence[ChartBar]) -> bool:
    """Bullish piercing: bearish candle then bullish that closes above prior midpoint."""

    if len(bars) < 2:
        return False
    prev, curr = bars[-2], bars[-1]
    midpoint = (prev.open + prev.close) / 2
    return (
        _is_bearish(prev)
        and _is_bullish(curr)
        and curr.open < prev.low
        and curr.close > midpoint
    )


def dark_cloud_cover(bars: Sequence[ChartBar]) -> bool:
    """Bearish dark cloud: bullish candle then bearish that closes below prior midpoint."""

    if len(bars) < 2:
        return False
    prev, curr = bars[-2], bars[-1]
    midpoint = (prev.open + prev.close) / 2
    return (
        _is_bullish(prev)
        and _is_bearish(curr)
        and curr.open > prev.high
        and curr.close < midpoint
    )


# ---------------------------------------------------------------------------
# 3-bar patterns
# ---------------------------------------------------------------------------

def morning_star(bars: Sequence[ChartBar]) -> bool:
    if len(bars) < 3:
        return False
    b1, b2, b3 = bars[-3], bars[-2], bars[-3 + 2]
    # Use correct variable for b3
    b3 = bars[-1]
    return (
        _is_bearish(b1)
        and _body(b1) > _range(b2)
        and _is_doji(b2)
        and _is_bullish(b3)
        and b3.close > (b1.open + b1.close) / 2
    )


def evening_star(bars: Sequence[ChartBar]) -> bool:
    if len(bars) < 3:
        return False
    b1, b2, b3 = bars[-3], bars[-2], bars[-1]
    return (
        _is_bullish(b1)
        and _body(b1) > _range(b2)
        and _is_doji(b2)
        and _is_bearish(b3)
        and b3.close < (b1.open + b1.close) / 2
    )


def three_white_soldiers(bars: Sequence[ChartBar]) -> bool:
    if len(bars) < 3:
        return False
    b1, b2, b3 = bars[-3], bars[-2], bars[-1]
    return (
        _is_bullish(b1)
        and _is_bullish(b2)
        and _is_bullish(b3)
        and b2.open > b1.open
        and b2.close > b1.close
        and b3.open > b2.open
        and b3.close > b2.close
    )


def three_black_crows(bars: Sequence[ChartBar]) -> bool:
    if len(bars) < 3:
        return False
    b1, b2, b3 = bars[-3], bars[-2], bars[-1]
    return (
        _is_bearish(b1)
        and _is_bearish(b2)
        and _is_bearish(b3)
        and b2.open < b1.open
        and b2.close < b1.close
        and b3.open < b2.open
        and b3.close < b2.close
    )


def three_inside_up(bars: Sequence[ChartBar]) -> bool:
    if len(bars) < 3:
        return False
    b1, b2, b3 = bars[-3], bars[-2], bars[-1]
    return (
        _is_bearish(b1)
        and _is_bullish(b2)
        and b2.open > b1.close
        and b2.close < b1.open
        and _is_bullish(b3)
        and b3.close > b1.open
    )


def three_inside_down(bars: Sequence[ChartBar]) -> bool:
    if len(bars) < 3:
        return False
    b1, b2, b3 = bars[-3], bars[-2], bars[-1]
    return (
        _is_bullish(b1)
        and _is_bearish(b2)
        and b2.open < b1.close
        and b2.close > b1.open
        and _is_bearish(b3)
        and b3.close < b1.open
    )


def bullish_abandoned_baby(bars: Sequence[ChartBar]) -> bool:
    if len(bars) < 3:
        return False
    b1, b2, b3 = bars[-3], bars[-2], bars[-1]
    return (
        _is_bearish(b1)
        and _is_doji(b2)
        and b2.high < min(b1.low, b3.low)
        and _is_bullish(b3)
    )


def bearish_abandoned_baby(bars: Sequence[ChartBar]) -> bool:
    if len(bars) < 3:
        return False
    b1, b2, b3 = bars[-3], bars[-2], bars[-1]
    return (
        _is_bullish(b1)
        and _is_doji(b2)
        and b2.low > max(b1.high, b3.high)
        and _is_bearish(b3)
    )


def three_line_strike_bullish(bars: Sequence[ChartBar]) -> bool:
    if len(bars) < 4:
        return False
    # 4-bar variant: 3 bearish then a long bullish that engulfs all three
    b1, b2, b3, b4 = bars[-4], bars[-3], bars[-2], bars[-1]
    return (
        _is_bearish(b1)
        and _is_bearish(b2)
        and _is_bearish(b3)
        and _is_bullish(b4)
        and b4.open <= max(b1.high, b2.high, b3.high)
        and b4.close >= min(b1.low, b2.low, b3.low)
    )


def three_line_strike_bearish(bars: Sequence[ChartBar]) -> bool:
    if len(bars) < 4:
        return False
    b1, b2, b3, b4 = bars[-4], bars[-3], bars[-2], bars[-1]
    return (
        _is_bullish(b1)
        and _is_bullish(b2)
        and _is_bullish(b3)
        and _is_bearish(b4)
        and b4.open >= min(b1.low, b2.low, b3.low)
        and b4.close <= max(b1.high, b2.high, b3.high)
    )


def advance_block(bars: Sequence[ChartBar]) -> bool:
    if len(bars) < 3:
        return False
    b1, b2, b3 = bars[-3], bars[-2], bars[-1]
    return (
        _is_bullish(b1)
        and _is_bullish(b2)
        and _is_bullish(b3)
        and b2.close > b1.close
        and b3.close > b2.close
        and _body(b2) < _body(b1)
        and _body(b3) < _body(b2)
        and _upper_wick(b3) > _upper_wick(b1)
    )


def delimiter(bars: Sequence[ChartBar]) -> bool:
    if len(bars) < 3:
        return False
    b1, b2, b3 = bars[-3], bars[-2], bars[-1]
    return (
        _is_bearish(b1)
        and _is_bearish(b2)
        and _is_bearish(b3)
        and b2.close < b1.close
        and b3.close < b2.close
        and _body(b2) < _body(b1)
        and _body(b3) < _body(b2)
    )


# ---------------------------------------------------------------------------
# Convenience: all 28 as a mapping
# ---------------------------------------------------------------------------

ALL_PATTERNS: dict[str, object] = {  # type: ignore[dict-item]
    "hammer": hammer,
    "hanging_man": hanging_man,
    "inverted_hammer": inverted_hammer,
    "shooting_star": shooting_star,
    "bullish_marubozu": bullish_marubozu,
    "bearish_marubozu": bearish_marubozu,
    "doji": doji,
    "spinning_top": spinning_top,
    "bullish_belt_hold": bullish_belt_hold,
    "bearish_belt_hold": bearish_belt_hold,
    "bullish_engulfing": bullish_engulfing,
    "bearish_engulfing": bearish_engulfing,
    "bullish_harami": bullish_harami,
    "bearish_harami": bearish_harami,
    "piercing_pattern": piercing_pattern,
    "dark_cloud_cover": dark_cloud_cover,
    "morning_star": morning_star,
    "evening_star": evening_star,
    "three_white_soldiers": three_white_soldiers,
    "three_black_crows": three_black_crows,
    "three_inside_up": three_inside_up,
    "three_inside_down": three_inside_down,
    "bullish_abandoned_baby": bullish_abandoned_baby,
    "bearish_abandoned_baby": bearish_abandoned_baby,
    "three_line_strike_bullish": three_line_strike_bullish,
    "three_line_strike_bearish": three_line_strike_bearish,
    "advance_block": advance_block,
    "delimiter": delimiter,
}


def detect_all(bars: Sequence[ChartBar]) -> dict[str, bool]:
    """Run every pattern against *bars*; return ``{name: bool}``."""

    return {name: fn(bars) for name, fn in ALL_PATTERNS.items()}


__all__ = [
    "ALL_PATTERNS",
    "detect_all",
    "hammer",
    "hanging_man",
    "inverted_hammer",
    "shooting_star",
    "bullish_marubozu",
    "bearish_marubozu",
    "doji",
    "spinning_top",
    "bullish_belt_hold",
    "bearish_belt_hold",
    "bullish_engulfing",
    "bearish_engulfing",
    "bullish_harami",
    "bearish_harami",
    "piercing_pattern",
    "dark_cloud_cover",
    "morning_star",
    "evening_star",
    "three_white_soldiers",
    "three_black_crows",
    "three_inside_up",
    "three_inside_down",
    "bullish_abandoned_baby",
    "bearish_abandoned_baby",
    "three_line_strike_bullish",
    "three_line_strike_bearish",
    "advance_block",
    "delimiter",
]
