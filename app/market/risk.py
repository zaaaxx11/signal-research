"""CAPM risk statistics: alpha, beta, correlation, volatility, Sharpe, drawdown.

Two dated close series in, one :class:`RiskStatistics` out. The module is pure —
no clock, no network, no storage — so a fixture is a test rather than a snapshot
of the weather.

**Alignment comes first, and it is the whole correctness argument**

A symbol and a benchmark do not share a calendar: a holiday in one venue, a
listing that starts later, a bar the provider dropped. A return is therefore
computed only over dates **both** series carry: the closes are joined on the
date, sorted, and ``r_t = close_t / close_{t-1} - 1`` is read off the joined
sequence for each side. Two properties follow, and they are the reason the join
is not an optimisation:

* a move on a date the benchmark does not carry contributes **no** return — the
  asset's return over the next common date spans the gap for both sides, which is
  the only comparison that means anything;
* the symbol's return is never paired with a benchmark return from a different
  day, which would produce a precise-looking beta of nothing.

A duplicated date is one session and the **last** close wins (a rebuilt bar is
newer than the one it replaces). A close that is not finite and strictly positive
cannot produce a return, so that date is dropped from both sides — never
zero-filled.

**The statistics, stated once**

With ``n`` aligned closes there are ``m = n - 1`` returns. Fewer than
:data:`MIN_OBSERVATIONS` (60) of them is ``insufficient_history``: a beta over a
month is a rumour, and a refusal is more honest than a number with no sample
behind it.

* ``beta = cov(rs, rb) / var(rb)`` — population moments (the window *is* the
  sample), so a symbol that moves exactly twice the benchmark answers ``2.0``
  with no residual.
* ``alpha = (mean(rs) - (rf_d + beta * (mean(rb) - rf_d))) * 252`` — Jensen's
  alpha, annualised, with ``rf_d = risk_free / 252`` so the daily rate and the
  daily returns are on one scale. A doubled benchmark therefore answers exactly
  ``risk_free``: it earned its beta and nothing more.
* ``correlation = cov(rs, rb) / (sd(rs) * sd(rb))`` and ``r_squared`` its square.
  A symbol with zero return variance has **no** correlation: it is published as
  ``0.0`` (it does not co-move) rather than as a division by zero, and its Sharpe
  is ``None`` because a zero denominator has no ratio.
* ``volatility = sd(rs) * sqrt(252)``, ``sharpe = (mean(rs) - rf_d) / sd(rs) *
  sqrt(252)``.
* ``max_drawdown`` is the deepest peak-to-trough fall of the **aligned** symbol
  closes, signed and as a fraction (``-0.2`` is 20 % below the running peak).

A flat benchmark — zero variance — is ``degenerate_benchmark``: beta is a ratio
with a zero denominator, and no honest number exists.

**Scale.** ``alpha`` and ``volatility`` are annualised **fractions** (``0.0123``
is 1.23 % a year), ``max_drawdown`` a fraction, the rest ratios;
:data:`UNITS` states that for every published key, because a reader comparing
``0.04`` with ``4.0`` deserves to know which is which.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: Sessions in a year for annualising. The provider adapter and the indicators
#: module use the same constant; a second spelling would be a second answer.
TRADING_DAYS = 252.0

#: The fewest overlapping daily returns a statistic may be computed from. Sixty
#: sessions is roughly a quarter: the shortest window in which a beta is a
#: measurement rather than an anecdote.
MIN_OBSERVATIONS = 60

#: The default risk-free rate, as an annual fraction — 4 % a year. It is a
#: parameter, not a market fact: the caller states the rate its comparison uses.
DEFAULT_RISK_FREE = 0.04

#: The default benchmark: the S&P 500 index, the market portfolio a US equity's
#: beta is conventionally measured against.
DEFAULT_BENCHMARK = "^GSPC"

#: What every published number is in. One vocabulary for the whole document.
UNITS: dict[str, str] = {
    "alpha": "annual_fraction",
    "beta": "ratio",
    "correlation": "ratio",
    "r_squared": "ratio",
    "volatility": "annual_fraction",
    "sharpe": "ratio",
    "max_drawdown": "fraction",
}

#: One ``(day, close)`` observation. The route builds these from chart bars.
DatedClose = tuple[date, float]


class RiskError(ValueError):
    """A refusal with a machine-readable ``reason`` the route turns into a 422.

    ``insufficient_history`` and ``degenerate_benchmark`` are the caller's
    question being unanswerable from the data, not a server fault. A
    ``ValueError`` subclass because it is also the wrong input for the function.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class RiskStatistics:
    """One window's risk numbers, plus the sample they were measured on."""

    observations: int
    as_of: date
    alpha: float
    beta: float
    correlation: float
    r_squared: float
    volatility: float
    sharpe: float | None
    max_drawdown: float

    def values(self) -> dict[str, float | None]:
        """The seven published numbers, in the order the contract names them."""

        return {
            "alpha": self.alpha,
            "beta": self.beta,
            "correlation": self.correlation,
            "r_squared": self.r_squared,
            "volatility": self.volatility,
            "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown,
        }

    def units(self) -> dict[str, str]:
        """A fresh copy of :data:`UNITS` — a caller cannot mutate the vocabulary."""

        return dict(UNITS)


def _finite_positive(value: Any) -> float | None:
    """``value`` as a float a return can be computed from, or ``None``.

    A price is finite and strictly positive. ``bool`` is an ``int`` in Python and
    is refused on purpose: ``True`` is not a close.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _bar_day(stamp: Any, zone: str) -> date | None:
    """The exchange-local calendar day of an epoch-second bar stamp.

    A daily bar is stamped at the session's open, so a venue east of UTC —
    Auckland opens at 22:00 UTC the day before — would be dated one day early and
    move a return onto the wrong session. An unusable stamp is ``None`` (the bar
    cannot be placed on a calendar); an unknown zone name falls back to UTC,
    because the price is still a price even when the venue is not a name this
    process can resolve.

    This mirrors ``app.api.engine._bar_day``; it lives here because dating bars
    is part of what a *daily return* is, and a second definition in a route would
    be a second answer to "which day is this bar?".
    """

    try:
        moment = datetime.fromtimestamp(int(stamp), UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    try:
        return moment.astimezone(ZoneInfo(zone or "UTC")).date()
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return moment.date()


def dated_closes(bars: Sequence[Mapping[str, Any]], zone: str) -> list[DatedClose]:
    """Chart bars → one ``(day, close)`` per session, ascending by day.

    The bars are the chart route's own dicts (``{"t", "o", "h", "l", "c", "v"}``),
    already sanitised: a bar there is a price or it is absent. A bar whose stamp
    cannot be placed on a calendar is skipped rather than guessed at, and a
    non-numeric close is skipped for the same reason — the statistics must not be
    handed a fabricated observation.
    """

    points: list[DatedClose] = []
    for bar in bars:
        if not isinstance(bar, Mapping):
            continue
        day = _bar_day(bar.get("t"), zone)
        if day is None:
            continue
        close = _finite_positive(bar.get("c"))
        if close is None:
            continue
        points.append((day, close))
    points.sort(key=lambda point: point[0])
    return points


def _by_day(series: Sequence[DatedClose]) -> dict[date, float]:
    """``series`` as one close per day, sorted by day at the caller.

    Later duplicates replace earlier ones: a rebuilt bar is newer than the one it
    replaces, and a provider that sent two rows for one session sent one session.
    """

    closes: dict[date, float] = {}
    for row in series:
        try:
            day, close = row
        except (TypeError, ValueError):
            continue
        if not isinstance(day, date):
            continue
        value = _finite_positive(close)
        if value is None:
            continue
        closes[day] = value
    return closes


def align_closes(
    symbol: Sequence[DatedClose], benchmark: Sequence[DatedClose]
) -> tuple[tuple[date, float, float], ...]:
    """The dates both series carry, ascending, with each side's close.

    This is the only join in the module. Everything downstream reads it, so a
    statistic can never see a symbol return paired with a benchmark close from a
    different session.
    """

    ours = _by_day(symbol)
    theirs = _by_day(benchmark)
    return tuple(
        (day, ours[day], theirs[day]) for day in sorted(ours.keys() & theirs.keys())
    )


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _stdev(values: Sequence[float], mean: float) -> float:
    """Population standard deviation: the window is the whole sample."""

    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _drawdown(closes: Sequence[float]) -> float:
    """The deepest peak-to-trough fall, signed and as a fraction.

    The peak starts at the first close, so a window that opens below an earlier
    (unseen) high does not claim a drawdown it never showed.
    """

    peak = closes[0]
    deepest = 0.0
    for close in closes[1:]:
        if close > peak:
            peak = close
            continue
        fall = close / peak - 1.0
        if fall < deepest:
            deepest = fall
    return deepest


def risk_statistics(
    symbol: Sequence[DatedClose],
    benchmark: Sequence[DatedClose],
    *,
    risk_free: float = DEFAULT_RISK_FREE,
    minimum: int = MIN_OBSERVATIONS,
) -> RiskStatistics:
    """The CAPM statistics of ``symbol`` against ``benchmark`` over common dates.

    Raises :class:`RiskError` with ``reason="insufficient_history"`` when fewer
    than ``minimum`` overlapping returns exist, and with
    ``reason="degenerate_benchmark"`` when the benchmark's returns have no
    variance. Both are answers about the data, not about a bug.
    """

    if (
        isinstance(risk_free, bool)
        or not isinstance(risk_free, (int, float))
        or not math.isfinite(float(risk_free))
    ):
        raise ValueError(f"risk_free must be a finite number, got {risk_free!r}")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 2:
        raise ValueError(f"minimum must be an int >= 2, got {minimum!r}")

    aligned = align_closes(symbol, benchmark)
    observations = len(aligned) - 1
    if observations < minimum:
        raise RiskError(
            "insufficient_history",
            (
                f"risk needs at least {minimum} overlapping daily returns; "
                f"{len(aligned)} common sessions give {observations}"
            ),
        )

    symbol_returns = [
        current[1] / previous[1] - 1.0
        for previous, current in zip(aligned[:-1], aligned[1:])
    ]
    benchmark_returns = [
        current[2] / previous[2] - 1.0
        for previous, current in zip(aligned[:-1], aligned[1:])
    ]

    symbol_mean = _mean(symbol_returns)
    benchmark_mean = _mean(benchmark_returns)
    symbol_sd = _stdev(symbol_returns, symbol_mean)
    benchmark_sd = _stdev(benchmark_returns, benchmark_mean)

    # Population covariance: sum of centred products over the same divisor as the
    # variances, so cov/var is exactly the least-squares slope.
    covariance = (
        sum(
            (ours - symbol_mean) * (theirs - benchmark_mean)
            for ours, theirs in zip(symbol_returns, benchmark_returns)
        )
        / observations
    )
    benchmark_variance = benchmark_sd**2
    if benchmark_variance == 0:
        raise RiskError(
            "degenerate_benchmark",
            (
                "the benchmark's returns have zero variance over the aligned "
                "window, so beta is undefined"
            ),
        )

    beta = covariance / benchmark_variance
    daily_rf = float(risk_free) / TRADING_DAYS
    alpha = (
        symbol_mean - (daily_rf + beta * (benchmark_mean - daily_rf))
    ) * TRADING_DAYS
    volatility = symbol_sd * math.sqrt(TRADING_DAYS)

    if symbol_sd == 0:
        # No variation: it cannot co-move, and a Sharpe ratio has a zero divisor.
        correlation = 0.0
        sharpe: float | None = None
    else:
        correlation = max(-1.0, min(1.0, covariance / (symbol_sd * benchmark_sd)))
        sharpe = (symbol_mean - daily_rf) / symbol_sd * math.sqrt(TRADING_DAYS)

    return RiskStatistics(
        observations=observations,
        as_of=aligned[-1][0],
        alpha=alpha,
        beta=beta,
        correlation=correlation,
        r_squared=correlation * correlation,
        volatility=volatility,
        sharpe=sharpe,
        max_drawdown=_drawdown([close for _, close, _ in aligned]),
    )


__all__ = [
    "DEFAULT_BENCHMARK",
    "DEFAULT_RISK_FREE",
    "MIN_OBSERVATIONS",
    "TRADING_DAYS",
    "UNITS",
    "DatedClose",
    "RiskError",
    "RiskStatistics",
    "align_closes",
    "dated_closes",
    "risk_statistics",
]
