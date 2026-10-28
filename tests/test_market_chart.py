"""Chart formulas: pure, deterministic, and independent of the network."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from app.market.chart import parse_chart_metrics


def _entry(
    closes: list[float],
    *,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    opens: list[float] | None = None,
    volumes: list[float] | None = None,
    start: date = date(2025, 1, 1),
    adjclose: list[float] | None = None,
) -> dict[str, Any]:
    highs = highs or [value + 1 for value in closes]
    lows = lows or [value - 1 for value in closes]
    opens = opens or closes[:]
    volumes = volumes or [100.0 for _ in closes]
    timestamps = [
        int(datetime.combine(start + timedelta(days=index), datetime.min.time(), UTC).timestamp())
        for index in range(len(closes))
    ]
    quote: dict[str, Any] = {
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    }
    if adjclose is not None:
        quote["adjclose"] = adjclose
    return {
        "meta": {"symbol": "TEST"},
        "timestamp": timestamps,
        "indicators": {"quote": [quote]},
    }


NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def test_perf_1w_uses_five_bar_window() -> None:
    closes = [100.0] * 400
    closes[-1] = 105.0
    values = parse_chart_metrics(_entry(closes), now=NOW)
    assert values["perf_1w"] == pytest.approx(5.0)


def test_perf_uses_raw_close_not_adjclose() -> None:
    closes = [100.0] * 400
    closes[-1] = 110.0
    adjusted = [50.0] * 400
    adjusted[-1] = 55.0
    values = parse_chart_metrics(_entry(closes, adjclose=adjusted), now=NOW)
    assert values["perf_1w"] == pytest.approx(10.0)


def test_sma20_is_percent_distance_not_price() -> None:
    closes = [100.0] * 400
    closes[-1] = 120.0
    values = parse_chart_metrics(_entry(closes), now=NOW)
    expected = (120.0 / ((19 * 100.0 + 120.0) / 20) - 1) * 100
    assert values["sma20"] == pytest.approx(expected)
    assert values["sma20"] != pytest.approx(120.0)


def test_volatility_is_average_daily_range() -> None:
    closes = [100.0] * 400
    highs = [102.0] * 400
    lows = [98.0] * 400
    values = parse_chart_metrics(_entry(closes, highs=highs, lows=lows), now=NOW)
    assert values["volatility_1w"] == pytest.approx(4.0)
    assert values["volatility_4w"] == pytest.approx(4.0)


def test_average_volume_uses_sixty_five_sessions() -> None:
    closes = [100.0] * 400
    volumes = [1000.0] * 400
    values = parse_chart_metrics(_entry(closes, volumes=volumes), now=NOW)
    assert values["average_volume"] == pytest.approx(1000.0)
    assert values["relative_volume"] == pytest.approx(1.0)


def test_high_52w_is_price_and_covers_calendar_year() -> None:
    closes = [100.0] * 400
    highs = [101.0] * 400
    highs[-60] = 150.0
    values = parse_chart_metrics(_entry(closes, highs=highs), now=NOW)
    assert values["high_52w"] == pytest.approx(150.0)
    assert values["high_50d"] == pytest.approx(101.0)


def test_chart_does_not_emit_quote_only_fields() -> None:
    values = parse_chart_metrics(_entry([100.0] * 400), now=NOW)
    assert not {"price", "change_pct", "volume", "prev_close"} & set(values)


def test_today_partial_bar_is_dropped_before_market_close() -> None:
    closes = [100.0] * 400
    entry = _entry(closes)
    # Add one bar dated today with a huge high; it must not become high_52w.
    today = int(datetime(2026, 10, 28, 12, 0, tzinfo=UTC).timestamp())
    entry["timestamp"].append(today)
    for key, value in (("open", 100.0), ("low", 90.0), ("close", 100.0), ("volume", 10.0)):
        entry["indicators"]["quote"][0][key].append(value)
    entry["indicators"]["quote"][0]["high"].append(999.0)
    values = parse_chart_metrics(entry, now=NOW)
    assert values["high_52w"] < 999.0
