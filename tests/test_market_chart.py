"""Chart formulas: pure, deterministic, and independent of the network."""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx2
import pytest

from app.market.chart import ChartBar, parse_chart_metrics
from app.market.depth import QUOTE_ONLY_FIELDS
from tests._market_quote import install_seam
from tests._phase2 import MockWeb


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


# -- the wide window (the ten keys the narrow path drops) ---------------------

#: The ten performance/dividend-growth cells a ``range=1y`` body cannot reach:
#: the multi-year anchors, the 50-day extremes and the dividend events.
WIDE_KEYS = (
    "perf_ytd",
    "perf_3y",
    "perf_5y",
    "perf_10y",
    "high_50d",
    "low_50d",
    "dividend_growth_1y",
    "dividend_growth_3y",
    "dividend_growth_5y",
)

CHART_BASE = "https://query1.finance.yahoo.com"


def _params(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


def _chart_source() -> type:
    """The chart source under test.

    A missing class is a RED failure with a readable message about the
    contract, not a bare ImportError that says nothing about what is wrong.
    """
    try:
        from app.market.chart import YahooChartDepthSource
    except ImportError as exc:  # pragma: no cover - RED path
        raise NotImplementedError(
            "app.market.chart.YahooChartDepthSource is missing: the wide-window "
            f"chart source must live beside parse_chart_metrics ({exc})"
        ) from exc
    return YahooChartDepthSource


def _candlestick() -> Any:
    """The candlestick classifier under test (same RED contract as above)."""
    try:
        from app.market.chart import candlestick_pattern
    except ImportError as exc:  # pragma: no cover - RED path
        raise NotImplementedError(
            "app.market.chart.candlestick_pattern is missing: the deterministic "
            f"doji/hammer/engulfing classifier ({exc})"
        ) from exc
    return candlestick_pattern


def _window_entry(start: date, end: date, *, last_close: float = 110.0) -> dict[str, Any]:
    """Daily bars from ``start`` to ``end``; every close is 100 but the last."""
    closes = [100.0] * ((end - start).days + 1)
    closes[-1] = last_close
    return _entry(closes, start=start)


def test_wide_window_url_carries_period1_eleven_years_back() -> None:
    url = _chart_source().url("AAPL", CHART_BASE, now=NOW)
    params = _params(url)
    assert params["interval"] == ["1d"]
    assert params["events"] == ["div,split"]
    assert "range" not in params, "the wide request is period-based, not range-based"
    start = datetime.fromtimestamp(int(params["period1"][0]), UTC)
    # Eleven years back, not ten: the extra year is what keeps the ten-year
    # anchor inside the returned bars.
    assert start == datetime(2015, 10, 28, tzinfo=UTC)
    assert int(params["period2"][0]) == int(NOW.timestamp())


def test_narrow_window_url_is_the_legacy_range_1y_request() -> None:
    url = _chart_source().url("AAPL", CHART_BASE, wide=False, now=NOW)
    params = _params(url)
    assert params["range"] == ["1y"]
    assert "period1" not in params
    assert params["interval"] == ["1d"]


def test_chart_source_defaults_to_the_wide_window() -> None:
    source = _chart_source()()
    assert source.wide is True
    assert "period1" in _params(source.url("AAPL", CHART_BASE, now=NOW))


def test_chart_source_fills_the_ten_keys_over_the_wide_window(
    monkeypatch: Any,
) -> None:
    today = datetime.now(UTC).date()
    year = datetime.now(UTC).year
    entry = _window_entry(today - timedelta(days=11 * 365 + 4), today - timedelta(days=1))
    entry["events"] = {"dividends": _scheduled(year - 7, year - 1, 1.0 / 1.1, growth=0.10)}

    web = MockWeb()
    web.route(
        "query1.finance.yahoo.com",
        lambda _request: httpx2.Response(
            200, json={"chart": {"result": [entry], "error": None}}
        ),
    )
    install_seam(monkeypatch, web)
    values = asyncio.run(_chart_source()(concurrency=1).fetch_many(["AAPL"]))

    assert values["AAPL"].source == "yahoo_chart"
    fields = values["AAPL"].fields
    assert set(WIDE_KEYS) <= set(fields)
    assert fields["perf_10y"] == pytest.approx(10.0)
    assert fields["dividend_growth_5y"] == pytest.approx(10.0, rel=1e-6)
    # The chart window's low is never an all-time low.
    assert "all_time_low" not in fields
    assert "all_time_high" not in fields

    params = _params(web.requests[0].url)
    assert "range" not in params
    period1 = datetime.fromtimestamp(int(params["period1"][0]), UTC).date()
    assert abs((period1 - (today - timedelta(days=11 * 365 + 3))).days) <= 2


def test_parse_chart_metrics_fills_the_ten_keys_a_narrow_window_drops() -> None:
    entry = _window_entry(date(2015, 10, 28), date(2026, 10, 27))
    entry["events"] = {"dividends": _scheduled(2019, 2025, 1.0 / 1.1, growth=0.10)}
    values = parse_chart_metrics(entry, now=NOW)
    assert set(WIDE_KEYS) <= set(values)
    assert values["perf_ytd"] == pytest.approx(10.0)
    assert values["perf_3y"] == pytest.approx(10.0)
    assert values["perf_5y"] == pytest.approx(10.0)
    assert values["perf_10y"] == pytest.approx(10.0)
    assert values["high_50d"] == pytest.approx(111.0)
    assert values["low_50d"] == pytest.approx(99.0)
    for field in ("dividend_growth_1y", "dividend_growth_3y", "dividend_growth_5y"):
        assert values[field] == pytest.approx(10.0, rel=1e-6)


def test_perf_10y_is_filled_when_the_window_reaches_the_anchor() -> None:
    # NOW - 120 months is 2016-10-28. A window that starts on or before that
    # date anchors; the eleven-year request starts 2015-10-28, a year early.
    wide = parse_chart_metrics(
        _window_entry(date(2015, 10, 28), date(2016, 12, 30)), now=NOW
    )
    assert wide["perf_10y"] == pytest.approx(10.0)


def test_perf_10y_is_absent_when_the_window_starts_after_the_anchor() -> None:
    # The range=10y miss, reproduced: the first bar lands one day past the
    # ten-year anchor, so no bar can anchor the return and the key is absent
    # rather than guessed.
    narrow = parse_chart_metrics(
        _window_entry(date(2016, 10, 29), date(2017, 12, 30)), now=NOW
    )
    assert "perf_10y" not in narrow
    # The parser is fine; only the ten-year anchor is unreachable.
    assert narrow["perf_1w"] == pytest.approx(10.0)


def test_chart_window_low_is_not_published_as_all_time_low() -> None:
    # A split-adjusted history starts at the split price (MSFT's window low is
    # 47.02 against Yahoo's 0.0885 all-time low), so the chart module claims
    # neither all-time extreme.
    values = parse_chart_metrics(
        _window_entry(date(2015, 10, 28), date(2026, 10, 27)), now=NOW
    )
    assert "all_time_low" not in values
    assert "all_time_high" not in values
    assert values["low_52w"] == pytest.approx(99.0)


# -- candlestick_pattern (a heuristic label, not a reported fact) -------------


def _bar(day: date, open_: float, high: float, low: float, close: float) -> ChartBar:
    return ChartBar(day=day, open=open_, high=high, low=low, close=close, volume=1000.0)


def test_candlestick_labels_a_doji() -> None:
    bars = [_bar(date(2026, 10, 26), 100.0, 101.0, 99.0, 100.1)]
    assert _candlestick()(bars) == "doji"


def test_candlestick_labels_a_hammer() -> None:
    bars = [_bar(date(2026, 10, 26), 100.0, 101.2, 97.0, 101.0)]
    assert _candlestick()(bars) == "hammer"


def test_candlestick_labels_a_bullish_engulfing() -> None:
    bars = [
        _bar(date(2026, 10, 25), 101.0, 101.5, 98.5, 99.0),
        _bar(date(2026, 10, 26), 98.5, 102.0, 98.0, 101.5),
    ]
    assert _candlestick()(bars) == "engulfing"


def test_candlestick_labels_a_bearish_engulfing() -> None:
    bars = [
        _bar(date(2026, 10, 25), 99.0, 101.5, 98.5, 101.0),
        _bar(date(2026, 10, 26), 101.5, 102.0, 98.0, 98.5),
    ]
    assert _candlestick()(bars) == "engulfing"


def test_candlestick_labels_nothing_on_a_plain_bar() -> None:
    bars = [
        _bar(date(2026, 10, 25), 100.0, 101.5, 98.5, 100.5),
        _bar(date(2026, 10, 26), 100.0, 102.0, 99.5, 101.0),
    ]
    assert _candlestick()(bars) is None


def test_candlestick_rules_are_applied_in_a_fixed_order() -> None:
    # A zero-body bar with a long lower shadow reads as a doji first.
    assert _candlestick()([_bar(date(2026, 10, 26), 100.0, 100.2, 97.0, 100.05)]) == "doji"
    # A hammer whose body also engulfs the prior bar is still a hammer.
    hammer_first = [
        _bar(date(2026, 10, 25), 102.0, 102.5, 100.0, 100.0),
        _bar(date(2026, 10, 26), 99.5, 103.0, 93.0, 102.5),
    ]
    assert _candlestick()(hammer_first) == "hammer"


def test_candlestick_needs_bars_and_a_nonzero_range() -> None:
    assert _candlestick()([]) is None
    flat = [_bar(date(2026, 10, 26), 100.0, 100.0, 100.0, 100.0)]
    assert _candlestick()(flat) is None


def test_parse_chart_metrics_labels_the_last_bar() -> None:
    # _entry defaults open == close, so the last bar is a zero-body doji.
    values = parse_chart_metrics(_entry([100.0] * 400), now=NOW)
    assert values.get("candlestick_pattern") == "doji"
    assert "candlestick_pattern" not in QUOTE_ONLY_FIELDS


def test_parse_chart_metrics_leaves_the_label_absent_without_a_pattern() -> None:
    closes = [100.0] * 400
    closes[-1] = 110.0
    opens = [100.0] * 400
    opens[-1] = 108.0
    highs = [101.0] * 400
    highs[-1] = 111.0
    lows = [99.0] * 400
    lows[-1] = 107.0
    entry = _entry(closes, opens=opens, highs=highs, lows=lows)
    values = parse_chart_metrics(entry, now=NOW)
    assert "candlestick_pattern" not in values

