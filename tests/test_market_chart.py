"""Chart formulas: pure, deterministic, and independent of the network."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from app.market.chart import parse_chart_metrics
from app.market.depth import QUOTE_ONLY_FIELDS


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

QUARTER_MONTHS = (2, 5, 8, 11)


def _scheduled(
    start_year: int,
    end_year: int,
    amount: float,
    *,
    growth: float = 0.0,
    months: tuple[int, ...] = QUARTER_MONTHS,
    day: int = 15,
) -> list[dict[str, Any]]:
    """Quarterly cash dividends, optionally growing ``growth`` per year."""

    rows: list[dict[str, Any]] = []
    for year in range(start_year, end_year + 1):
        value = amount * (1.0 + growth) ** (year - start_year)
        for month in months:
            rows.append(
                {
                    "amount": round(value, 10),
                    "date": int(datetime(year, month, day, tzinfo=UTC).timestamp()),
                }
            )
    return rows


def _with_dividends(dividends: Any, *, splits: Any = None) -> dict[str, Any]:
    entry = _entry([100.0] * 400)
    events: dict[str, Any] = {"dividends": dividends}
    if splits is not None:
        events["splits"] = splits
    entry["events"] = events
    return entry


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


# -- dividend growth (events.dividends) -------------------------------------


def test_dividend_growth_1y_flat_is_zero() -> None:
    values = parse_chart_metrics(_with_dividends(_scheduled(2024, 2025, 1.0)), now=NOW)
    assert values["dividend_growth_1y"] == pytest.approx(0.0)


def test_dividend_growth_1y_is_ten_percent_when_payments_rise_ten_percent() -> None:
    rows = _scheduled(2024, 2024, 1.0) + _scheduled(2025, 2025, 1.1)
    values = parse_chart_metrics(_with_dividends(rows), now=NOW)
    assert values["dividend_growth_1y"] == pytest.approx(10.0, rel=1e-6)


def test_dividend_growth_3y_and_5y_are_calendar_year_cagr() -> None:
    # 2020 total is 1.00 and every later year is 10% higher.
    rows = _scheduled(2019, 2025, 1.0 / 1.1, growth=0.10)
    values = parse_chart_metrics(_with_dividends(rows), now=NOW)
    for field in ("dividend_growth_1y", "dividend_growth_3y", "dividend_growth_5y"):
        assert values[field] == pytest.approx(10.0, rel=1e-6)


def test_dividend_growth_ignores_prices_and_adjclose() -> None:
    rows = _scheduled(2019, 2025, 1.0 / 1.1, growth=0.10)
    entry = _with_dividends(rows)
    quote = entry["indicators"]["quote"][0]
    quote["adjclose"] = [0.01] * 400
    for key in ("open", "high", "low", "close"):
        quote[key] = [1e6] * 400
    values = parse_chart_metrics(entry, now=NOW)
    assert values["dividend_growth_5y"] == pytest.approx(10.0, rel=1e-6)


def test_dividend_growth_1y_needs_eight_payments() -> None:
    rows = _scheduled(2024, 2024, 1.0) + _scheduled(2025, 2025, 1.1)[:3]
    assert len(rows) == 7
    values = parse_chart_metrics(_with_dividends(rows), now=NOW)
    assert "dividend_growth_1y" not in values
    assert "dividend_growth_1y" not in parse_chart_metrics(
        _with_dividends(_scheduled(2025, 2025, 1.0)[:2]), now=NOW
    )


def test_dividend_growth_3y_needs_four_calendar_years() -> None:
    values = parse_chart_metrics(_with_dividends(_scheduled(2023, 2025, 1.0)), now=NOW)
    assert "dividend_growth_3y" not in values


def test_dividend_growth_5y_absent_with_only_five_years() -> None:
    # 2021..2025 is five calendar years: enough for 3y, one short for 5y.
    values = parse_chart_metrics(_with_dividends(_scheduled(2021, 2025, 1.0)), now=NOW)
    assert values["dividend_growth_3y"] == pytest.approx(0.0)
    assert "dividend_growth_5y" not in values


def test_dividend_growth_5y_needs_six_distinct_years() -> None:
    # Endpoint years 2020 and 2025 are present and positive, but the history
    # only carries three calendar years; a CAGR would be a curve fit, not data.
    rows = _scheduled(2019, 2020, 1.0) + _scheduled(2025, 2025, 2.0)
    values = parse_chart_metrics(_with_dividends(rows), now=NOW)
    assert "dividend_growth_5y" not in values


def test_dividend_growth_absent_when_prior_year_is_zero() -> None:
    rows = _scheduled(2024, 2024, 0.0) + _scheduled(2025, 2025, 1.0)
    values = parse_chart_metrics(_with_dividends(rows), now=NOW)
    assert not {
        "dividend_growth_1y",
        "dividend_growth_3y",
        "dividend_growth_5y",
    } & set(values)


def test_split_events_are_ignored_by_dividend_growth() -> None:
    rows = _scheduled(2024, 2025, 1.0)
    splits = [
        {
            "date": int(datetime(2025, 6, 2, tzinfo=UTC).timestamp()),
            "numerator": 4.0,
            "denominator": 1.0,
        }
    ]
    plain = parse_chart_metrics(_with_dividends(rows), now=NOW)
    adjusted = parse_chart_metrics(_with_dividends(rows, splits=splits), now=NOW)
    assert adjusted["dividend_growth_1y"] == pytest.approx(plain["dividend_growth_1y"])
    assert adjusted["dividend_growth_1y"] == pytest.approx(0.0)


def test_splits_without_dividends_produce_no_growth_fields() -> None:
    entry = _entry([100.0] * 400)
    entry["events"] = {
        "splits": [{"date": int(datetime(2025, 6, 2, tzinfo=UTC).timestamp()), "numerator": 2}]
    }
    values = parse_chart_metrics(entry, now=NOW)
    assert not {
        "dividend_growth_1y",
        "dividend_growth_3y",
        "dividend_growth_5y",
    } & set(values)


def test_dividend_growth_absent_without_events() -> None:
    values = parse_chart_metrics(_entry([100.0] * 400), now=NOW)
    assert not {
        "dividend_growth_1y",
        "dividend_growth_3y",
        "dividend_growth_5y",
    } & set(values)


def test_dividend_growth_fields_are_not_quote_only() -> None:
    rows = _scheduled(2019, 2025, 1.0, growth=0.10)
    values = parse_chart_metrics(_with_dividends(rows), now=NOW)
    fields = {"dividend_growth_1y", "dividend_growth_3y", "dividend_growth_5y"}
    assert fields <= set(values)
    assert not fields & QUOTE_ONLY_FIELDS
    assert not {"price", "change_pct", "volume", "prev_close"} & set(values)


def test_duplicate_dividend_rows_are_collapsed() -> None:
    rows = _scheduled(2024, 2024, 1.0) + _scheduled(2025, 2025, 1.1)
    rows.append(dict(rows[-1]))
    values = parse_chart_metrics(_with_dividends(rows), now=NOW)
    assert values["dividend_growth_1y"] == pytest.approx(10.0)


def test_same_day_regular_and_special_are_summed() -> None:
    rows = _scheduled(2024, 2024, 1.0) + _scheduled(2025, 2025, 1.1, months=(2, 5, 8))
    stamp = int(datetime(2025, 11, 15, tzinfo=UTC).timestamp())
    rows.append({"amount": 1.1, "date": stamp})
    rows.append({"amount": 0.11, "date": stamp})
    values = parse_chart_metrics(_with_dividends(rows), now=NOW)
    expected = ((3 * 1.1 + 1.21) / 4.0 - 1.0) * 100.0
    assert values["dividend_growth_1y"] == pytest.approx(expected)


def test_string_amounts_are_parsed_not_ignored() -> None:
    rows = _scheduled(2024, 2024, 1.0) + _scheduled(2025, 2025, 1.1)
    for row in rows:
        row["amount"] = str(row["amount"])
    values = parse_chart_metrics(_with_dividends(rows), now=NOW)
    assert values["dividend_growth_1y"] == pytest.approx(10.0)


def test_non_positive_amounts_are_not_payments() -> None:
    rows = _scheduled(2024, 2024, 1.0) + _scheduled(2025, 2025, 1.1)
    stamp = int(datetime(2025, 12, 15, tzinfo=UTC).timestamp())
    rows += [{"amount": -0.5, "date": stamp}, {"amount": 0.0, "date": stamp}]
    values = parse_chart_metrics(_with_dividends(rows), now=NOW)
    assert values["dividend_growth_1y"] == pytest.approx(10.0)


def test_only_invalid_amounts_leaves_the_metric_absent() -> None:
    rows = _scheduled(2024, 2024, 1.0) + _scheduled(2025, 2025, 1.1)
    for row in rows[-4:]:
        row["amount"] = -abs(row["amount"])
    values = parse_chart_metrics(_with_dividends(rows), now=NOW)
    assert "dividend_growth_1y" not in values


def test_huge_amounts_stay_finite_and_scale_free() -> None:
    rows = _scheduled(2024, 2024, 1e9) + _scheduled(2025, 2025, 1.1e9)
    stamp = int(datetime(2025, 12, 15, tzinfo=UTC).timestamp())
    rows += [{"amount": float("inf"), "date": stamp}, {"amount": float("nan"), "date": stamp}]
    values = parse_chart_metrics(_with_dividends(rows), now=NOW)
    assert values["dividend_growth_1y"] == pytest.approx(10.0)
    assert math.isfinite(values["dividend_growth_1y"])


def test_partial_current_year_is_not_the_latest_total() -> None:
    rows = _scheduled(2019, 2025, 1.0 / 1.1, growth=0.10)
    rows += _scheduled(2026, 2026, 100.0)
    values = parse_chart_metrics(_with_dividends(rows), now=NOW)
    assert values["dividend_growth_5y"] == pytest.approx(10.0, rel=1e-6)


def test_mid_year_history_start_leaves_the_cagr_absent() -> None:
    # 2020..2025 covers six calendar years, but the first payment is June 2020:
    # that year's total is truncated, so a 5y CAGR would overstate growth.
    rows = _scheduled(2020, 2025, 1.0, months=(6, 9, 12))
    values = parse_chart_metrics(_with_dividends(rows), now=NOW)
    assert values["dividend_growth_3y"] == pytest.approx(0.0)
    assert "dividend_growth_5y" not in values


def test_dividend_dates_use_utc_epoch_days() -> None:
    # 23:30 UTC on 2019-12-31 is still 2019, which is what lets the 5y base
    # year 2020 count as fully covered. A local-time conversion could shift it.
    rows = _scheduled(2020, 2025, 1.0)
    rows.append(
        {"amount": 0.5, "date": int(datetime(2019, 12, 31, 23, 30, tzinfo=UTC).timestamp())}
    )
    values = parse_chart_metrics(_with_dividends(rows), now=NOW)
    assert values["dividend_growth_5y"] == pytest.approx(0.0)


def test_unusable_dividend_timestamps_are_skipped() -> None:
    rows = _scheduled(2024, 2024, 1.0) + _scheduled(2025, 2025, 1.1)
    rows += [
        {"amount": 5.0, "date": 10**20},
        {"amount": 5.0, "date": "not-a-date"},
        {"amount": 5.0},
        "not-a-row",
    ]
    values = parse_chart_metrics(_with_dividends(rows), now=NOW)
    assert values["dividend_growth_1y"] == pytest.approx(10.0)


def test_dividend_mapping_keyed_by_epoch_is_read() -> None:
    rows = _scheduled(2024, 2024, 1.0) + _scheduled(2025, 2025, 1.1)
    mapping = {str(row["date"]): {"amount": row["amount"]} for row in rows}
    values = parse_chart_metrics(_with_dividends(mapping), now=NOW)
    assert values["dividend_growth_1y"] == pytest.approx(10.0)

