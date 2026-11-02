"""``GET /engine/risk/{symbol}`` — CAPM statistics against a benchmark.

The same daily series the chart draws, two of them, and one pure computation:
``alpha``, ``beta``, ``correlation``, ``r_squared``, ``volatility``, ``sharpe``
and ``max_drawdown`` over the dates both series carry. The arithmetic and its
alignment rule live in :mod:`app.market.risk`; this module is the wiring, the
parameter grammar and the one fetch path.

**One series, not two**

The series is read through the chart route's own reader and cache — the same
``(symbol, "1y", "1d")`` fetch ``GET /market/chart/{symbol}`` answers — so a
ticker page that drew its chart has already paid for its risk numbers, and the
benchmark costs one read per five-minute window rather than one per request. A
second fetch path would be a second opinion about what a daily close is.

**Parameters**

``range`` defaults to ``1y`` and speaks only windows whose bars are **daily**:
a five-minute series annualised by ``sqrt(252)`` would be a number about the
wrong thing entirely, so ``1d`` and ``5d`` are a ``422 invalid_range`` naming
what is legal. ``benchmark`` defaults to ``^GSPC`` and accepts any chartable
ticker (``SPY`` is the usual ETF proxy); a benchmark the provider cannot resolve
is ``422 unknown_benchmark``, not a 404 about the *symbol* that was asked for.
``rf`` defaults to ``0.04`` — a 4 % annual fraction, validated before any read.

**Refusals are about the data, not the server**

``422 insufficient_history`` when the two series share fewer than 60 daily
returns (a month of beta is an anecdote), ``422 degenerate_benchmark`` when the
benchmark did not move. ``as_of`` is the last **common** session and
``observations`` the number of returns that produced the numbers, so a document
can never claim a window it did not measure.
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Query, Request

from app.auth import ApiError
from app.market.risk import (
    DEFAULT_BENCHMARK,
    DEFAULT_RISK_FREE,
    RiskError,
    RiskStatistics,
    dated_closes,
    risk_statistics,
)

router = APIRouter(prefix="/engine", tags=["engine"])

#: The windows this route speaks. Daily bars only: every range here resolves to
#: the chart route's ``1d`` interval, which is what a daily-return statistic
#: needs. ``1d``/``5d`` are excluded because five-minute returns annualised by
#: ``sqrt(252)`` describe nothing.
RISK_RANGES: tuple[str, ...] = ("1mo", "3mo", "6mo", "1y", "5y", "max")

#: The window the caller gets when it names none.
DEFAULT_RANGE = "1y"

#: The interval behind every range above.
INTERVAL = "1d"

#: The widest annual risk-free rate a caller may state, as a fraction. Beyond
#: ``-1..1`` the number is not a rate in any currency, and the alpha it produces
#: would be arithmetic on a typo.
MAX_RISK_FREE = 1.0


def risk_range(raw: Any) -> str:
    """``raw`` as one of :data:`RISK_RANGES`, or ``422 invalid_range``.

    The refusal names the legal set, because a caller who sent ``1d`` is not
    wrong about the symbol — they are asking a daily-return question of an
    intraday window.
    """

    token = raw.strip() if isinstance(raw, str) else ""
    if token in RISK_RANGES:
        return token
    raise ApiError(
        status_code=422,
        error="invalid_range",
        message=(
            f"range must be one of {', '.join(RISK_RANGES)} (risk uses daily "
            f"bars); got {raw!r}"
        ),
    )


def risk_benchmark(raw: Any) -> str:
    """``raw`` as a canonical benchmark ticker, or ``422 invalid_benchmark``.

    Delegates the symbol grammar to the chart route — one definition of what a
    ticker may look like — but translates its ``404``: a benchmark that does not
    parse is a bad *parameter*, and saying ``unknown_symbol`` would leave the
    caller wondering which of the two symbols was meant.
    """

    from app.api import chart as chart_api

    if raw is None:
        return DEFAULT_BENCHMARK
    try:
        return chart_api.chart_symbol(raw)
    except ApiError as exc:
        raise ApiError(
            status_code=422,
            error="invalid_benchmark",
            message=f"benchmark must be a ticker such as ^GSPC or SPY; got {raw!r}",
        ) from exc


def risk_free_rate(raw: Any) -> float:
    """``raw`` as an annual fraction in ``-1..1``, or ``422 invalid_risk_free``.

    Parsed here rather than by FastAPI so a typo answers the house envelope with
    its own code, and validated **before** any provider read: a bad rate costs
    nothing.
    """

    if raw is None:
        return DEFAULT_RISK_FREE
    token = raw.strip() if isinstance(raw, str) else ""
    try:
        value = float(token)
    except (TypeError, ValueError):
        value = math.nan
    if not math.isfinite(value) or abs(value) > MAX_RISK_FREE:
        raise ApiError(
            status_code=422,
            error="invalid_risk_free",
            message=(
                f"rf must be a finite annual fraction within "
                f"-{MAX_RISK_FREE:g}..{MAX_RISK_FREE:g} (0.04 is 4 %); got {raw!r}"
            ),
        )
    return value


async def _series(request: Request, symbol: str, window: str) -> Any:
    """``symbol``'s daily series for ``window``, from the chart route's cache or reader."""

    from app.api import chart as chart_api

    cached = chart_api.chart_cache(request).get((symbol, window, INTERVAL))
    if cached is not None:
        return cached.series
    return await chart_api.fetch_series(symbol, window=window, interval=INTERVAL)


def risk_document(
    symbol: str,
    benchmark: str,
    window: str,
    *,
    risk_free: float,
    stats: RiskStatistics,
) -> dict[str, Any]:
    """The body from one computed window. Pure: the same statistics print the same bytes."""

    return {
        "symbol": symbol,
        "benchmark": benchmark,
        "range": window,
        "as_of": stats.as_of.isoformat(),
        "observations": stats.observations,
        "risk_free": risk_free,
        "values": stats.values(),
        "units": stats.units(),
    }


@router.get("/risk/{symbol}")
async def risk(
    symbol: str,
    request: Request,
    range_: str = Query(DEFAULT_RANGE, alias="range"),
    benchmark: str | None = Query(None),
    rf: str | None = Query(None),
) -> dict[str, Any]:
    """Alpha, beta, correlation, R², volatility, Sharpe and drawdown, one call.

    Every parameter is validated **before** a provider read, so a typo never
    costs a fetch. The symbol and the benchmark are each read once per
    five-minute window through the chart route's cache; the return is computed on
    the dates both series carry, and ``observations`` states how many that was.
    A window too short to measure or a benchmark that did not move is a ``422``
    with its own reason — never a ``0.0`` standing in for an unanswerable
    question.
    """

    from app.api import chart as chart_api

    wanted = chart_api.chart_symbol(symbol)
    window = risk_range(range_)
    benchmark_symbol = risk_benchmark(benchmark)
    rate = risk_free_rate(rf)

    ours = await _series(request, wanted, window)
    try:
        theirs = await _series(request, benchmark_symbol, window)
    except ApiError as exc:
        if exc.status_code == 404:
            raise ApiError(
                status_code=422,
                error="unknown_benchmark",
                message=(
                    f"the chart provider has no series for benchmark "
                    f"{benchmark_symbol}; try ^GSPC or SPY"
                ),
            ) from exc
        raise ApiError(
            status_code=503,
            error="benchmark_unavailable",
            message=f"the benchmark series for {benchmark_symbol} was unavailable: {exc.message}",
        ) from exc

    try:
        stats = risk_statistics(
            dated_closes(ours.bars, ours.timezone),
            dated_closes(theirs.bars, theirs.timezone),
            risk_free=rate,
        )
    except RiskError as exc:
        raise ApiError(status_code=422, error=exc.reason, message=str(exc)) from exc

    return risk_document(
        wanted, benchmark_symbol, window, risk_free=rate, stats=stats
    )


__all__ = [
    "DEFAULT_RANGE",
    "INTERVAL",
    "MAX_RISK_FREE",
    "RISK_RANGES",
    "risk",
    "risk_benchmark",
    "risk_document",
    "risk_free_rate",
    "risk_range",
    "router",
]
