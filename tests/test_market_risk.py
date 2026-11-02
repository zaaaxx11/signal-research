"""Contract for ``app.market.risk`` — CAPM statistics over dated closes.

The module is pure: two ``(date, close)`` series in, one
:class:`~app.market.risk.RiskStatistics` out. These tests pin the arithmetic on
synthetic series whose answer is known *before* the code runs, so a formula that
agrees with itself on noise still fails:

* a symbol that moves exactly twice the benchmark has ``beta == 2``,
  ``correlation == 1``, ``r_squared == 1`` — and, by Jensen's identity,
  ``alpha == risk_free`` (a doubled benchmark earns its beta and nothing more);
* the volatility and Sharpe definitions are restated by hand: population
  standard deviation of the **simple** daily returns, scaled by
  ``sqrt(252)``, with the risk-free rate divided by 252 to a daily rate;
* alignment is on **common dates only** — a symbol with extra sessions between
  benchmark dates must not contribute a return, and a duplicated date is one day;
* a flat benchmark has no beta and is a refusal, not an infinity;
* fewer than :data:`~app.market.risk.MIN_OBSERVATIONS` overlapping returns is a
  refusal with its own reason;
* max drawdown is the deepest peak-to-trough fall of the aligned symbol closes,
  signed and as a fraction;
* a symbol with zero return variance has no correlation and no Sharpe, and says
  so with ``0.0`` and ``None`` rather than inventing a number.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

import pytest

from app.market.risk import (
    DEFAULT_RISK_FREE,
    MIN_OBSERVATIONS,
    TRADING_DAYS,
    RiskError,
    align_closes,
    dated_closes,
    risk_statistics,
)

#: A Wednesday, so the fixture's calendar does not depend on a weekend edge.
LAST_DAY = date(2026, 4, 8)

#: Benchmark daily simple returns: mixed signs, no window flat, a real variance.
_PATTERN: tuple[float, ...] = (
    0.011,
    -0.014,
    0.004,
    -0.019,
    0.002,
    0.007,
    -0.012,
    0.016,
    -0.003,
)


def _weekdays(end: date, count: int) -> list[date]:
    """``count`` weekdays ending at ``end`` (inclusive), ascending."""

    days: list[date] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(days)


def _path(start: float, returns: list[float]) -> list[float]:
    """The closes implied by ``returns`` from ``start`` (one more close than returns)."""

    closes = [start]
    for value in returns:
        closes.append(closes[-1] * (1.0 + value))
    return closes


def _dated(days: list[date], closes: list[float]) -> list[tuple[date, float]]:
    return list(zip(days, closes))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _stdev(values: list[float]) -> float:
    """Population standard deviation — the sample the whole window is, not a draw."""

    mean = _mean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _benchmark(count: int) -> tuple[list[float], list[tuple[date, float]]]:
    returns = [_PATTERN[index % len(_PATTERN)] for index in range(count)]
    closes = _path(100.0, returns)
    return returns, _dated(_weekdays(LAST_DAY, count + 1), closes)


# ---------------------------------------------------------------------------
# Beta, correlation and alpha
# ---------------------------------------------------------------------------


def test_a_symbol_that_moves_twice_the_benchmark_has_beta_two() -> None:
    benchmark_returns, benchmark = _benchmark(120)
    symbol_returns = [2.0 * value for value in benchmark_returns]
    days = [day for day, _ in benchmark]
    symbol = _dated(days, _path(50.0, symbol_returns))

    stats = risk_statistics(symbol, benchmark)

    assert stats.observations == 120
    assert stats.as_of == LAST_DAY
    assert stats.beta == pytest.approx(2.0, abs=1e-12)
    assert stats.correlation == pytest.approx(1.0, abs=1e-12)
    assert stats.r_squared == pytest.approx(1.0, abs=1e-12)

    # Jensen: a doubled benchmark earns exactly its beta, so the only alpha left
    # is the risk-free rate itself.
    assert stats.alpha == pytest.approx(DEFAULT_RISK_FREE, abs=1e-12)

    # The definitions, restated by hand on the same returns.
    assert stats.volatility == pytest.approx(
        2.0 * _stdev(benchmark_returns) * math.sqrt(TRADING_DAYS)
    )
    assert stats.sharpe == pytest.approx(
        (2.0 * _mean(benchmark_returns) - DEFAULT_RISK_FREE / TRADING_DAYS)
        / (2.0 * _stdev(benchmark_returns))
        * math.sqrt(TRADING_DAYS)
    )
    assert stats.max_drawdown <= 0.0


def test_alpha_is_jensen_alpha_with_the_trading_day_risk_free_rate() -> None:
    benchmark_returns, benchmark = _benchmark(120)
    symbol_returns = [1.5 * value + 0.001 for value in benchmark_returns]
    symbol = _dated([day for day, _ in benchmark], _path(80.0, symbol_returns))

    stats = risk_statistics(symbol, benchmark)
    daily_rf = DEFAULT_RISK_FREE / TRADING_DAYS
    expected = (
        _mean(symbol_returns) - (daily_rf + 1.5 * (_mean(benchmark_returns) - daily_rf))
    ) * TRADING_DAYS

    assert stats.beta == pytest.approx(1.5, abs=1e-12)
    assert stats.alpha == pytest.approx(expected, abs=1e-12)
    assert stats.sharpe == pytest.approx(
        (_mean(symbol_returns) - daily_rf) / _stdev(symbol_returns) * math.sqrt(TRADING_DAYS)
    )


def test_a_higher_risk_free_rate_moves_alpha_by_beta_minus_one() -> None:
    benchmark_returns, benchmark = _benchmark(100)
    symbol = _dated(
        [day for day, _ in benchmark],
        _path(30.0, [2.0 * value for value in benchmark_returns]),
    )

    base = risk_statistics(symbol, benchmark)
    raised = risk_statistics(symbol, benchmark, risk_free=0.06)

    # alpha = mean(rs)*252 - beta*mean(rb)*252 + rf*(beta - 1): the risk-free
    # rate is only a cost to a portfolio less aggressive than the benchmark.
    assert base.beta == pytest.approx(2.0, abs=1e-12)
    assert raised.alpha - base.alpha == pytest.approx(0.02 * (base.beta - 1.0), abs=1e-12)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_a_flat_benchmark_has_no_beta_and_is_refused() -> None:
    benchmark = _dated(_weekdays(LAST_DAY, 81), [100.0] * 81)
    symbol = _dated(_weekdays(LAST_DAY, 81), _path(40.0, [0.001] * 80))

    with pytest.raises(RiskError) as caught:
        risk_statistics(symbol, benchmark)

    assert caught.value.reason == "degenerate_benchmark"
    assert "variance" in str(caught.value)


def test_sixty_overlapping_returns_is_the_boundary() -> None:
    days = _weekdays(LAST_DAY, MIN_OBSERVATIONS + 1)  # 60 returns: legal
    benchmark_returns = [_PATTERN[index % len(_PATTERN)] for index in range(MIN_OBSERVATIONS)]
    benchmark = _dated(days, _path(100.0, benchmark_returns))
    symbol = _dated(days, _path(10.0, [3.0 * value for value in benchmark_returns]))

    assert risk_statistics(symbol, benchmark).observations == MIN_OBSERVATIONS

    short_days = _weekdays(LAST_DAY, MIN_OBSERVATIONS)  # 59 returns: refused
    short_benchmark = _dated(
        short_days, _path(100.0, benchmark_returns[: MIN_OBSERVATIONS - 1])
    )
    short_symbol = _dated(
        short_days, _path(10.0, [3.0 * value for value in benchmark_returns[: MIN_OBSERVATIONS - 1]])
    )

    with pytest.raises(RiskError) as caught:
        risk_statistics(short_symbol, short_benchmark)
    assert caught.value.reason == "insufficient_history"
    assert str(MIN_OBSERVATIONS) in str(caught.value)


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------


def test_alignment_uses_only_dates_both_series_carry() -> None:
    days = _weekdays(LAST_DAY, 161)
    benchmark_days = days[::2]  # every other weekday: 81 closes, 80 returns
    benchmark_returns = [_PATTERN[index % len(_PATTERN)] for index in range(80)]

    # The symbol's close on each *common* date follows exactly 2x the benchmark.
    # On the days in between it does something violent — which must not become a
    # return, because the benchmark has no close to pair with it.
    common_closes = _path(100.0, [2.0 * value for value in benchmark_returns])
    symbol_by_day: dict[date, float] = {}
    for day in days:
        symbol_by_day[day] = 999.0
    for day, close in zip(benchmark_days, common_closes):
        symbol_by_day[day] = close
    # One day twice: a duplicated stamp is one session, and the last close wins.
    duplicated = benchmark_days[-1]
    symbol = _dated(
        [day for day in days] + [duplicated],
        [symbol_by_day[day] for day in days] + [common_closes[-1]],
    )
    benchmark = _dated(benchmark_days, _path(100.0, benchmark_returns))

    aligned = align_closes(symbol, benchmark)

    assert len(aligned) == len(benchmark_days)
    assert [day for day, _, _ in aligned] == benchmark_days

    stats = risk_statistics(symbol, benchmark)
    assert stats.observations == 80  # 81 common closes -> 80 returns
    assert stats.as_of == benchmark_days[-1]
    assert stats.beta == pytest.approx(2.0, abs=1e-12)
    assert stats.correlation == pytest.approx(1.0, abs=1e-12)


def test_alignment_is_sorted_by_date_whatever_order_it_was_given() -> None:
    _, benchmark = _benchmark(80)
    shuffled = list(reversed(benchmark))

    assert [day for day, _, _ in align_closes(shuffled, shuffled)] == [
        day for day, _ in benchmark
    ]


# ---------------------------------------------------------------------------
# Drawdown
# ---------------------------------------------------------------------------


def test_max_drawdown_is_the_deepest_peak_to_trough_over_the_window() -> None:
    days = _weekdays(LAST_DAY, 61)
    closes = [100.0] * 31 + [200.0, 100.0] + [100.0] * 28
    assert len(closes) == 61
    symbol = _dated(days, closes)
    benchmark_returns = [_PATTERN[index % len(_PATTERN)] for index in range(60)]
    benchmark = _dated(days, _path(100.0, benchmark_returns))

    stats = risk_statistics(symbol, benchmark)

    assert stats.max_drawdown == pytest.approx(-0.5, abs=1e-12)


def test_a_monotonic_rise_draws_down_nothing() -> None:
    days = _weekdays(LAST_DAY, 61)
    symbol = _dated(days, [100.0 + index for index in range(61)])
    benchmark_returns = [_PATTERN[index % len(_PATTERN)] for index in range(60)]
    benchmark = _dated(days, _path(100.0, benchmark_returns))

    assert risk_statistics(symbol, benchmark).max_drawdown == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Degenerate symbol
# ---------------------------------------------------------------------------


def test_a_flat_symbol_has_no_correlation_and_no_sharpe() -> None:
    days = _weekdays(LAST_DAY, 81)
    symbol = _dated(days, [50.0] * 81)
    benchmark_returns = [_PATTERN[index % len(_PATTERN)] for index in range(80)]
    benchmark = _dated(days, _path(100.0, benchmark_returns))

    stats = risk_statistics(symbol, benchmark)

    assert stats.beta == 0.0
    assert stats.correlation == 0.0
    assert stats.r_squared == 0.0
    assert stats.volatility == 0.0
    assert stats.sharpe is None
    assert stats.max_drawdown == 0.0
    # No return and a beta of zero leaves exactly -rf of annual alpha.
    assert stats.alpha == pytest.approx(-DEFAULT_RISK_FREE)


# ---------------------------------------------------------------------------
# Chart bars -> dated closes
# ---------------------------------------------------------------------------


def test_bar_dates_follow_the_exchange_calendar_and_a_zero_close_is_not_a_bar() -> None:
    # 22:00 UTC on 04-08 is 10:00 on 04-09 in Auckland: the bar belongs to the
    # session the venue opened, not to the UTC date.
    stamp = int(datetime(2026, 4, 8, 22, 0, tzinfo=UTC).timestamp())
    bars = [
        {"t": stamp, "c": 10.0},
        {"t": stamp + 86_400, "c": 0.0},  # a closed session's zero row
        {"t": None, "c": 12.0},  # a stamp no calendar can place
        {"c": 13.0},  # no stamp at all
    ]

    assert dated_closes(bars, "Pacific/Auckland") == [(date(2026, 4, 9), 10.0)]
