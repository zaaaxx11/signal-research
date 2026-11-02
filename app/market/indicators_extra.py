"""Derived technicals the catalogue cannot carry as columns.

The catalogue is a frozen, versioned vocabulary: adding ``sma_13`` or
``perf_wtd`` to it would change every screen's column set and every stored
contract. These values are therefore **computed on read** from one daily close
series — the same series ``GET /market/chart/{symbol}`` already serves — and
published as a document beside the catalogue rather than inside it.

**What is new here, and what is not**

``sma``, ``ema`` and ``rsi`` are the implementations in
:mod:`app.market.indicators`, re-exported unchanged: one definition of Wilder's
smoothing in the codebase, not two that drift. This module adds

* :func:`momentum` — percent change over ``period`` **bars**;
* :func:`wtd_return` / :func:`mtd_return` — returns anchored to **calendar**
  boundaries (this week's Monday, this month's first day), never "the last five
  bars", because a four-day week and a holiday Monday are not five sessions;
* :func:`technicals` — the document one ticker page reads.

**Conventions, stated once**

* **EMA is seeded with the SMA of the first ``period`` closes** and then
  smoothed with ``alpha = 2 / (period + 1)``. Index 0 of the EMA series is bar
  ``period - 1``; every later close is folded in one at a time. A series exactly
  ``period`` long therefore has ``ema(period) == sma(period)`` — a seeded EMA
  has no opinion before its seed. This matches :mod:`app.market.indicators` and
  the Yahoo adapter, so the same bars produce the same number everywhere.
* **WTD** is the return from the last close **strictly before this week's
  Monday** to the last close at or before ``as_of``. **MTD** is the same from
  the last close strictly before the first of ``as_of``'s month. A series that
  does not reach back past the boundary returns ``None``: the missing Friday is
  unknown, and substituting the first bar of the week would silently redefine
  the window as "since the data started".
* **Missing is ``None``, never ``0``.** The document always carries every
  requested key — a short series leaves the cell null and names it in
  ``missing`` — because a table with a missing cell is honest and a table with
  a silently absent column is not.
* Momentum and the two calendar returns are **percent points**; RSI is an index
  on 0–100; the moving averages are **prices** in the instrument's quote
  currency. ``units`` says which is which for every key published.

The module is pure: no clock, no network, no storage. ``as_of`` is an argument,
so a fixture is a test rather than a snapshot of the weather.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

# The hygiene helpers are imported rather than copied: ``_series`` is what makes
# a NaN hole poison the whole sample, and a second copy of that rule would be a
# second answer to "what does a hole mean?".
from app.market.indicators import _series, _window
from app.market.indicators import ema, rsi, sma

#: Moving-average windows a ticker page prints by default. 13/21/100 are the
#: cells the catalogue lacks; 50/200 are here because an EMA set without them
#: would be a different indicator, not a shorter one.
DEFAULT_PERIODS: tuple[int, ...] = (13, 21, 50, 100, 200)

#: Wilder's default, and the period the published RSI table is stated at.
DEFAULT_RSI_PERIOD = 14

#: Ten sessions of price change: two trading weeks, short enough to react and
#: long enough not to be a single gap.
DEFAULT_MOMENTUM_PERIOD = 10

#: The longest window the document will compute. A year of daily bars is ~252,
#: so anything beyond this cannot be filled by the series the route reads and
#: would only spend arithmetic to return nulls.
MAX_PERIOD = 1000

#: How many SMA/EMA windows one request may ask for. Each window costs a pass
#: over the closes; eight is already more lines than a ticker page can show.
MAX_PERIODS = 8

#: What each published cell is measured in.
UNIT_PRICE = "price"
UNIT_PERCENT = "percent"
UNIT_INDEX = "index"
UNIT_LABEL = "label"


def _day(value: Any) -> date:
    """``value`` as a calendar day; a naive datetime is read as its date."""

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise ValueError(f"expected a date, got {value!r}")


def _periods(periods: Sequence[int]) -> tuple[int, ...]:
    """Validated, de-duplicated windows in the order asked for."""

    out: list[int] = []
    for period in periods:
        window = _window(period, name="period")
        if window > MAX_PERIOD:
            raise ValueError(f"period must be <= {MAX_PERIOD}, got {window}")
        if window in out:
            raise ValueError(f"duplicate period {window}")
        out.append(window)
    if not out:
        raise ValueError("at least one period is required")
    if len(out) > MAX_PERIODS:
        raise ValueError(f"at most {MAX_PERIODS} periods, got {len(out)}")
    return tuple(out)


def _threshold(value: float | None) -> float | None:
    """A non-negative, finite percent band, or ``None`` when not asked for."""

    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"momentum_threshold must be a number >= 0, got {value!r}")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"momentum_threshold must be a finite number >= 0, got {value!r}")
    return number


def _dated_points(
    series: Sequence[tuple[date, float]],
) -> list[tuple[date, float]] | None:
    """``(day, close)`` pairs, ascending, or ``None`` if any item is unusable.

    A hole cannot be skipped without inventing a session: the calendar return
    after it would compare a close with a base that is not the one the boundary
    names. So one bad item makes the whole series unknown, matching
    :func:`app.market.indicators._series`.
    """

    points: list[tuple[date, float]] = []
    for item in series:
        try:
            day, raw = item
        except (TypeError, ValueError):
            return None
        if not isinstance(day, (date, datetime)):
            return None
        try:
            close = float(raw)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(close):
            return None
        points.append((_day(day), close))
    points.sort(key=lambda point: point[0])
    return points


def _last_at_or_before(
    points: Sequence[tuple[date, float]], target: date
) -> tuple[date, float] | None:
    found: tuple[date, float] | None = None
    for point in points:
        if point[0] > target:
            break
        found = point
    return found


def _last_before(
    points: Sequence[tuple[date, float]], boundary: date
) -> tuple[date, float] | None:
    found: tuple[date, float] | None = None
    for point in points:
        if point[0] >= boundary:
            break
        found = point
    return found


def _calendar_return(
    points: Sequence[tuple[date, float]], boundary: date, as_of: date
) -> float | None:
    """Percent return from the close before ``boundary`` to the close at ``as_of``.

    ``None`` when either anchor is absent or the base is not a positive price.
    The base is **strictly** before the boundary: a bar dated on the boundary
    belongs to the new period and cannot be its own baseline.
    """

    current = _last_at_or_before(points, as_of)
    base = _last_before(points, boundary)
    if current is None or base is None or base[1] <= 0:
        return None
    value = (current[1] / base[1] - 1.0) * 100.0
    return value if math.isfinite(value) else None


# ---------------------------------------------------------------------------
# Momentum
# ---------------------------------------------------------------------------


def momentum(closes: Sequence[float], period: int = DEFAULT_MOMENTUM_PERIOD) -> float | None:
    """Percent change from ``period`` bars ago to the last close.

    Bars, not calendar days: the series is sessions, so ``period=10`` is ten
    trading days back. ``None`` when the series is shorter than that or the base
    close is not strictly positive (a return from zero has no meaning).
    """

    length = _window(period, name="period")
    series = _series(closes)
    if series is None or len(series) < length + 1:
        return None
    base = series[-1 - length]
    if base <= 0:
        return None
    value = (series[-1] / base - 1.0) * 100.0
    return value if math.isfinite(value) else None


def momentum_signal(value: float | None, threshold: float) -> str | None:
    """``up`` / ``down`` / ``flat``: momentum against a symmetric percent band.

    A value inside the band is ``flat`` — the threshold exists so a 0.1 % drift
    is not reported as a direction. ``None`` in, ``None`` out.
    """

    if value is None:
        return None
    if value > threshold:
        return "up"
    if value < -threshold:
        return "down"
    return "flat"


# ---------------------------------------------------------------------------
# Calendar returns
# ---------------------------------------------------------------------------


def wtd_return(
    series: Sequence[tuple[date, float]], as_of: date
) -> float | None:
    """Week-to-date percent return, anchored on this week's Monday.

    The base is the last close **strictly before** ``as_of``'s Monday — in a
    normal week, Friday's close — and the current value is the last close at or
    before ``as_of``. A weekend ``as_of`` therefore still measures the week that
    ended on Friday, and a Tuesday holiday does not shift the anchor.

    ``None`` when the series does not reach back past the Monday: the previous
    week's close is unknown, and starting from the week's first bar instead
    would answer a different question.
    """

    moment = _day(as_of)
    points = _dated_points(series)
    if points is None:
        return None
    monday = moment - timedelta(days=moment.weekday())
    return _calendar_return(points, monday, moment)


def mtd_return(
    series: Sequence[tuple[date, float]], as_of: date
) -> float | None:
    """Month-to-date percent return, anchored on the first of ``as_of``'s month.

    The base is the last close **strictly before** the first — the previous
    month's final session. ``None`` when the series does not reach back into the
    prior month, for the same reason :func:`wtd_return` refuses a short week.
    """

    moment = _day(as_of)
    points = _dated_points(series)
    if points is None:
        return None
    return _calendar_return(points, moment.replace(day=1), moment)


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


def technicals(
    closes: Sequence[float],
    days: Sequence[date] | None = None,
    *,
    periods: Sequence[int] = DEFAULT_PERIODS,
    rsi_period: int = DEFAULT_RSI_PERIOD,
    momentum_period: int = DEFAULT_MOMENTUM_PERIOD,
    momentum_threshold: float | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Every derived cell for one symbol's daily closes.

    ``closes`` and ``days`` are **aligned bar-for-bar**, ascending. A length
    mismatch raises: trimming the longer one would compare Tuesday's close with
    Monday's date and produce a precise-looking calendar return that is simply
    false. ``days`` may be ``None`` for a caller with no calendar, in which case
    ``perf_wtd``/``perf_mtd`` are null and named in ``missing``.

    ``as_of`` defaults to the last date in ``days``; it is never read from the
    clock, so a fixed series answers the same question forever. Every requested
    key is present in ``values`` — short windows are ``None`` and listed in
    ``missing``. ``momentum_threshold`` is opt-in: when given, the document adds
    ``momentum_{period}_signal`` (``up``/``down``/``flat``) and its unit.
    """

    windows = _periods(periods)
    rsi_window = _window(rsi_period, name="rsi_period")
    momentum_window = _window(momentum_period, name="momentum_period")
    threshold = _threshold(momentum_threshold)

    if days is not None and len(days) != len(closes):
        raise ValueError(
            "closes and days must be aligned: "
            f"{len(closes)} closes != {len(days)} days"
        )

    series = _series(closes)
    dated = _dated_points(tuple(zip(days, closes))) if days is not None else None
    clock = _day(as_of) if as_of is not None else (dated[-1][0] if dated else None)

    values: dict[str, float | str | None] = {}
    units: dict[str, str] = {}

    for period in windows:
        values[f"sma_{period}"] = sma(series or [], period)
        units[f"sma_{period}"] = UNIT_PRICE
    for period in windows:
        values[f"ema_{period}"] = ema(series or [], period)
        units[f"ema_{period}"] = UNIT_PRICE

    values[f"rsi_{rsi_window}"] = rsi(series or [], rsi_window)
    units[f"rsi_{rsi_window}"] = UNIT_INDEX

    momentum_key = f"momentum_{momentum_window}"
    momentum_value = momentum(series or [], momentum_window)
    values[momentum_key] = momentum_value
    units[momentum_key] = UNIT_PERCENT
    if threshold is not None:
        signal_key = f"{momentum_key}_signal"
        values[signal_key] = momentum_signal(momentum_value, threshold)
        units[signal_key] = UNIT_LABEL

    values["perf_wtd"] = (
        wtd_return(dated, clock) if dated is not None and clock is not None else None
    )
    units["perf_wtd"] = UNIT_PERCENT
    values["perf_mtd"] = (
        mtd_return(dated, clock) if dated is not None and clock is not None else None
    )
    units["perf_mtd"] = UNIT_PERCENT

    return {
        "as_of": clock.isoformat() if clock is not None else None,
        "series_length": len(series) if series is not None else 0,
        "values": values,
        "units": units,
        "missing": [key for key, value in values.items() if value is None],
    }


__all__ = [
    "DEFAULT_MOMENTUM_PERIOD",
    "DEFAULT_PERIODS",
    "DEFAULT_RSI_PERIOD",
    "MAX_PERIOD",
    "MAX_PERIODS",
    "mtd_return",
    "momentum",
    "momentum_signal",
    "rsi",
    "sma",
    "ema",
    "technicals",
    "wtd_return",
]
