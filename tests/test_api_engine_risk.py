"""Contract for ``GET /engine/risk/{symbol}`` — CAPM statistics against a benchmark.

The route is wiring: two daily series from the chart route's own reader and
cache, one pure computation, one document. These tests pin the wiring and the
document; the arithmetic itself is pinned in ``test_market_risk.py`` on synthetic
series whose answer is known in advance:

* the document's eight keys, exactly seven ``values``, and a ``units`` map that
  covers every one of them;
* ``range`` defaults to ``1y``, ``benchmark`` to ``^GSPC`` and ``risk_free`` to
  ``0.04``; each is a query parameter, and the body echoes what was used;
* ``as_of`` is the last **common** session, ``observations`` the count of daily
  returns the statistics saw;
* a doubled-benchmark symbol answers ``beta 2.0``, ``r_squared 1.0`` and
  ``alpha 0.04`` — the risk-free rate, by Jensen's identity;
* fewer than 60 overlapping returns is ``422 insufficient_history``, a flat
  benchmark is ``422 degenerate_benchmark``, and neither invents a number;
* a bad ``range`` / ``rf`` / ``benchmark`` is a ``422`` **before** any provider
  read, and an unusable symbol is ``404 unknown_symbol`` without one;
* a series the chart route already fetched inside its five-minute window is
  borrowed from that cache — one request per distinct symbol, not per route.

Every request is in-process (``httpx2.ASGITransport``) and the provider is
replaced at the one seam both routes read, so the suite never opens a socket.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest

from app.api.chart import ChartSeries
from app.auth import ApiError
from tests._support import api_client

DOCUMENT_KEYS: frozenset[str] = frozenset(
    {
        "symbol",
        "benchmark",
        "range",
        "as_of",
        "observations",
        "risk_free",
        "values",
        "units",
    }
)

VALUE_KEYS: frozenset[str] = frozenset(
    {"alpha", "beta", "correlation", "r_squared", "volatility", "sharpe", "max_drawdown"}
)

LAST_DAY = date(2026, 4, 8)

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
    days: list[date] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(days)


def _path(start: float, returns: Sequence[float]) -> list[float]:
    closes = [start]
    for value in returns:
        closes.append(closes[-1] * (1.0 + value))
    return closes


def _series(days: Sequence[date], closes: Sequence[float]) -> ChartSeries:
    """A chart series shaped exactly as :func:`app.api.chart.parse_chart_series` leaves it."""

    bars = tuple(
        {
            "t": int(datetime.combine(day, time(13, 30), tzinfo=UTC).timestamp()),
            "o": close,
            "h": close + 1.0,
            "l": close - 1.0,
            "c": close,
            "v": 1_000.0,
        }
        for day, close in zip(days, closes)
    )
    return ChartSeries(bars=bars, previous_close=closes[0], timezone="America/New_York")


def _benchmark_closes(count: int) -> list[float]:
    return _path(100.0, [_PATTERN[index % len(_PATTERN)] for index in range(count)])


def _doubled(days: Sequence[date], count: int) -> ChartSeries:
    """A symbol that moves exactly twice the benchmark's returns."""

    returns = [2.0 * _PATTERN[index % len(_PATTERN)] for index in range(count)]
    return _series(days, _path(50.0, returns))


class StubSeries:
    """The one provider seam: one canned series per symbol, and a ledger of calls."""

    def __init__(self, series: Mapping[str, ChartSeries]) -> None:
        self.series = dict(series)
        self.calls: list[tuple[str, str, str]] = []

    async def __call__(
        self, symbol: str, *, window: str, interval: str, limit: int = 1_000
    ) -> ChartSeries:
        self.calls.append((symbol, window, interval))
        if symbol not in self.series:
            raise AssertionError(f"unexpected provider read for {symbol}")
        return self.series[symbol]


@pytest.fixture()
def pair() -> tuple[list[date], StubSeries]:
    """121 sessions (~six months), the doubled symbol and the benchmark behind one stub."""

    days = _weekdays(LAST_DAY, 121)
    stub = StubSeries(
        {
            "AAPL": _doubled(days, 120),
            "^GSPC": _series(days, _benchmark_closes(120)),
            "SPY": _series(days, _benchmark_closes(120)),
        }
    )
    return days, stub


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


async def test_the_document_carries_the_risk_statistics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pair: tuple[list[date], StubSeries],
) -> None:
    days, stub = pair
    monkeypatch.setattr("app.api.chart.fetch_series", stub)

    async with api_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/risk/aapl")

    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body) == DOCUMENT_KEYS
    assert body["symbol"] == "AAPL"  # canonical, whatever case the caller typed
    assert body["benchmark"] == "^GSPC"
    assert body["range"] == "1y"
    assert body["as_of"] == LAST_DAY.isoformat()
    assert body["observations"] == 120
    assert body["risk_free"] == 0.04
    assert set(body["values"]) == VALUE_KEYS
    assert set(body["units"]) == VALUE_KEYS

    values = body["values"]
    assert values["beta"] == pytest.approx(2.0, abs=1e-12)
    assert values["correlation"] == pytest.approx(1.0, abs=1e-12)
    assert values["r_squared"] == pytest.approx(1.0, abs=1e-12)
    assert values["alpha"] == pytest.approx(0.04, abs=1e-12)  # rf, by Jensen
    assert values["volatility"] > 0.0
    assert values["sharpe"] is not None and math.isfinite(values["sharpe"])
    assert values["max_drawdown"] <= 0.0

    units = body["units"]
    assert units["beta"] == "ratio"
    assert units["alpha"] == "annual_fraction"

    # One request, two reads: the symbol and the default benchmark, both at the
    # chart route's own daily window.
    assert stub.calls == [("AAPL", "1y", "1d"), ("^GSPC", "1y", "1d")]


async def test_range_benchmark_and_risk_free_are_query_parameters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pair: tuple[list[date], StubSeries],
) -> None:
    _, stub = pair
    monkeypatch.setattr("app.api.chart.fetch_series", stub)

    async with api_client(tmp_path, monkeypatch) as client:
        response = await client.get(
            "/engine/risk/AAPL",
            params={"range": "6mo", "benchmark": "SPY", "rf": "0.06"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["range"] == "6mo"
    assert body["benchmark"] == "SPY"
    assert body["risk_free"] == 0.06
    # A doubled benchmark still earns exactly its beta, so alpha == the rf asked for.
    assert body["values"]["alpha"] == pytest.approx(0.06, abs=1e-12)
    assert stub.calls == [("AAPL", "6mo", "1d"), ("SPY", "6mo", "1d")]


async def test_the_chart_route_s_cached_series_is_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pair: tuple[list[date], StubSeries],
) -> None:
    _, stub = pair
    monkeypatch.setattr("app.api.chart.fetch_series", stub)

    async with api_client(tmp_path, monkeypatch) as client:
        chart = await client.get("/market/chart/AAPL", params={"range": "1y"})
        assert chart.status_code == 200, chart.text
        risk = await client.get("/engine/risk/AAPL")

    assert risk.status_code == 200, risk.text
    # The chart route paid for AAPL; the risk route pays only for the benchmark.
    assert stub.calls == [("AAPL", "1y", "1d"), ("^GSPC", "1y", "1d")]


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


async def test_short_history_is_insufficient_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    days = _weekdays(LAST_DAY, 21)  # a month: 20 returns, far short of 60
    stub = StubSeries(
        {
            "AAPL": _doubled(days, 20),
            "^GSPC": _series(days, _benchmark_closes(20)),
        }
    )
    monkeypatch.setattr("app.api.chart.fetch_series", stub)

    async with api_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/risk/AAPL")

    assert response.status_code == 422, response.text
    assert response.json()["error"] == "insufficient_history"


async def test_a_flat_benchmark_is_degenerate_benchmark(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    days = _weekdays(LAST_DAY, 121)
    stub = StubSeries(
        {
            "AAPL": _doubled(days, 120),
            "^GSPC": _series(days, [100.0] * 121),
        }
    )
    monkeypatch.setattr("app.api.chart.fetch_series", stub)

    async with api_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/risk/AAPL")

    assert response.status_code == 422, response.text
    assert response.json()["error"] == "degenerate_benchmark"


@pytest.mark.parametrize(
    ("path", "params", "error"),
    [
        ("/engine/risk/AAPL", {"range": "2y"}, "invalid_range"),
        ("/engine/risk/AAPL", {"range": "1d"}, "invalid_range"),
        ("/engine/risk/AAPL", {"rf": "abc"}, "invalid_risk_free"),
        ("/engine/risk/AAPL", {"rf": "nan"}, "invalid_risk_free"),
        ("/engine/risk/AAPL", {"benchmark": ""}, "invalid_benchmark"),
        ("/engine/risk/AAPL", {"benchmark": "../etc/passwd"}, "invalid_benchmark"),
    ],
)
async def test_a_bad_parameter_is_refused_before_any_provider_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pair: tuple[list[date], StubSeries],
    path: str,
    params: dict[str, str],
    error: str,
) -> None:
    _, stub = pair
    monkeypatch.setattr("app.api.chart.fetch_series", stub)

    async with api_client(tmp_path, monkeypatch) as client:
        response = await client.get(path, params=params)

    assert response.status_code == 422, response.text
    assert response.json()["error"] == error
    assert stub.calls == []


async def test_an_unusable_symbol_is_a_404_without_a_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pair: tuple[list[date], StubSeries],
) -> None:
    _, stub = pair
    monkeypatch.setattr("app.api.chart.fetch_series", stub)

    async with api_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/risk/AAPL;DROP")

    assert response.status_code == 404
    assert response.json()["error"] == "unknown_symbol"
    assert stub.calls == []


async def test_a_benchmark_the_provider_does_not_know_is_a_422(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pair: tuple[list[date], StubSeries],
) -> None:
    _, stub = pair
    monkeypatch.setattr("app.api.chart.fetch_series", stub)

    async def unknown(symbol: str, *, window: str, interval: str) -> ChartSeries:
        stub.calls.append((symbol, window, interval))
        if symbol == "NOPE":
            raise ApiError(
                status_code=404, error="unknown_symbol", message="no such symbol"
            )
        return stub.series[symbol]

    monkeypatch.setattr("app.api.chart.fetch_series", unknown)

    async with api_client(tmp_path, monkeypatch) as client:
        response = await client.get("/engine/risk/AAPL", params={"benchmark": "NOPE"})

    assert response.status_code == 422, response.text
    assert response.json()["error"] == "unknown_benchmark"
