"""Technical indicators: pure, deterministic, provider-agnostic.

Every expected value in this file is hand-computed from the fixture
definition itself (see the ``_*_fixture`` helpers and the ``MAIN_*``
constants) — no expectation is produced by calling the module under
test, so a wrong formula cannot validate itself.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from app.market.history import Bar, compute_technicals, parse_bars

START = date(2025, 1, 1)


def _stamp(day: date) -> int:
    return int(datetime.combine(day, datetime.min.time(), UTC).timestamp())


def _bars(
    closes: list[float],
    *,
    opens: list[float] | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[float] | None = None,
    start: date = START,
) -> list[Bar]:
    """Daily bars; every series defaults off ``closes`` (open=close, high=+1, low=-1)."""
    count = len(closes)
    opens = list(opens) if opens is not None else list(closes)
    highs = list(highs) if highs is not None else [value + 1.0 for value in closes]
    lows = list(lows) if lows is not None else [value - 1.0 for value in closes]
    volumes = list(volumes) if volumes is not None else [1000.0] * count
    return [
        Bar(
            date=start + timedelta(days=index),
            open=opens[index],
            high=highs[index],
            low=lows[index],
            close=closes[index],
            volume=volumes[index],
        )
        for index in range(count)
    ]


# -- fixtures -----------------------------------------------------------------
#
# Main fixture: 300 bars, flat at 100 except the last bar (close 110, open
# 105, high 111, low 104).  Every expected value below follows from that
# definition alone:
#   perf windows compare 110 against the 100 five bars back (and further);
#   SMA windows average 19/49/199 hundreds plus the final 110;
#   RSI sees 298 flat changes then one +10 gain (no losses at all);
#   ATR sees 298 true ranges of 2 (high-low 2 beats the 1-point gaps to the
#   previous close) then a final range of 11 (|111 - 100| beats high-low 7);
#   volatility averages (high-low)/close*100 = 2.0 per flat bar and
#   7/110*100 on the last bar;
#   the 52-week window is the last 252 bars, so all 300 highs/lows count;
#   gap = (105 - 100)/100, change-from-open = (110 - 105)/105.

MAIN_PERF = (110.0 / 100.0 - 1.0) * 100.0
MAIN_SMA20 = (110.0 / ((19 * 100.0 + 110.0) / 20.0) - 1.0) * 100.0
MAIN_SMA50 = (110.0 / ((49 * 100.0 + 110.0) / 50.0) - 1.0) * 100.0
MAIN_SMA200 = (110.0 / ((199 * 100.0 + 110.0) / 200.0) - 1.0) * 100.0
MAIN_ATR = (2.0 * 13.0 + 11.0) / 14.0
MAIN_VOL_1W = (4 * 2.0 + (7.0 / 110.0) * 100.0) / 5.0
MAIN_VOL_4W = (20 * 2.0 + (7.0 / 110.0) * 100.0) / 21.0
MAIN_RANGE_52W = (111.0 - 99.0) / 99.0 * 100.0
MAIN_CHANGE_FROM_OPEN = (110.0 - 105.0) / 105.0 * 100.0


def _main_fixture() -> list[Bar]:
    closes = [100.0] * 299 + [110.0]
    opens = [100.0] * 299 + [105.0]
    lows = [99.0] * 299 + [104.0]
    return _bars(closes, opens=opens, lows=lows)


def _rsi_fixture() -> list[Bar]:
    """16 bars: fourteen +1 changes, then one -14 change.

    Wilder: avg_gain 1.0 -> 13/14, avg_loss 0.0 -> 1.0, RS = 13/14,
    RSI = 100 - 100/(1 + 13/14) = 1300/27.  A simple average over all
    fifteen changes would give avg_gain = avg_loss = 14/15, RS = 1,
    RSI = 50.0 — the two must not be confused.
    """
    closes = [100.0 + float(index) for index in range(15)] + [100.0]
    return _bars(closes)


def _atr_fixture() -> list[Bar]:
    """16 bars whose true range is 2 (high-low) until a final bar whose
    range is 6 only because of the gap to the previous close."""
    closes = [100.0] * 16
    highs = [101.0] * 15 + [103.0]
    lows = [99.0] * 15 + [97.0]
    return _bars(closes, highs=highs, lows=lows)


def _volatility_fixture() -> list[Bar]:
    closes = [100.0] * 5
    highs = [101.0, 102.0, 103.0, 104.0, 105.0]
    lows = [99.0, 98.0, 97.0, 96.0, 95.0]
    return _bars(closes, highs=highs, lows=lows)


def _volume_fixture() -> list[Bar]:
    """300 bars: 235 sessions at 500, then 64 at 1000 and a final 2000.

    Only the last 65 sessions may feed the average, so the answer is
    (64 * 1000 + 2000) / 65 — not a blend of the older 500s.
    """
    closes = [100.0] * 300
    volumes = [500.0] * 235 + [1000.0] * 64 + [2000.0]
    return _bars(closes, volumes=volumes)


def _range_fixture() -> list[Bar]:
    """300 bars with a 150 high at index 10 (outside the 252-bar window)
    and a 50 low at index 100 (inside it)."""
    closes = [100.0] * 299 + [110.0]
    opens = [100.0] * 299 + [105.0]
    highs = [101.0] * 299 + [111.0]
    highs[10] = 150.0
    lows = [99.0] * 299 + [104.0]
    lows[100] = 50.0
    return _bars(closes, opens=opens, highs=highs, lows=lows)


def _gap_fixture() -> list[Bar]:
    return [
        Bar(date=START, open=0.0, high=0.0, low=0.0, close=0.0, volume=0.0),
        Bar(
            date=START + timedelta(days=1),
            open=10.0,
            high=12.0,
            low=8.0,
            close=11.0,
            volume=100.0,
        ),
    ]


def _zero_reference_fixture() -> list[Bar]:
    closes = [100.0] * 10
    closes[4] = 0.0
    return _bars(closes)


# -- Bar dataclass --------------------------------------------------------------


def test_bar_has_ohlcv_fields_in_order() -> None:
    assert [field.name for field in dataclasses.fields(Bar)] == [
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]


def test_bar_is_frozen() -> None:
    bar = _bars([100.0])[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        bar.close = 1.0  # type: ignore[misc]


# -- performance windows ---------------------------------------------------------


def test_perf_windows_are_bar_counted() -> None:
    values = compute_technicals(_main_fixture())
    for field in ("perf_1w", "perf_4w", "perf_13w", "perf_26w", "perf_52w"):
        assert values[field] == pytest.approx(MAIN_PERF)


def test_perf_1w_needs_six_bars() -> None:
    assert "perf_1w" not in compute_technicals(_bars([100.0] * 4 + [110.0]))
    six = compute_technicals(_bars([100.0] * 5 + [110.0]))
    assert six["perf_1w"] == pytest.approx(10.0)


def test_perf_skips_zero_reference_close() -> None:
    assert "perf_1w" not in compute_technicals(_zero_reference_fixture())


# -- SMA --------------------------------------------------------------------------


def test_sma_is_percent_distance_not_price() -> None:
    values = compute_technicals(_main_fixture())
    assert values["sma20"] == pytest.approx(MAIN_SMA20)
    assert values["sma50"] == pytest.approx(MAIN_SMA50)
    assert values["sma200"] == pytest.approx(MAIN_SMA200)
    assert values["sma20"] != pytest.approx(110.0)


def test_sma20_appears_at_exactly_twenty_bars() -> None:
    assert "sma20" not in compute_technicals(_bars([100.0] * 19))
    assert compute_technicals(_bars([100.0] * 20))["sma20"] == pytest.approx(0.0)


# -- RSI --------------------------------------------------------------------------


def test_rsi_uses_wilder_smoothing_not_simple_average() -> None:
    values = compute_technicals(_rsi_fixture())
    assert values["rsi"] == pytest.approx(1300.0 / 27.0)
    assert values["rsi"] != pytest.approx(50.0)


def test_rsi_is_100_when_there_are_no_losses() -> None:
    assert compute_technicals(_main_fixture())["rsi"] == pytest.approx(100.0)


def test_rsi_and_atr_need_fifteen_bars() -> None:
    short = compute_technicals(_bars([100.0] * 14))
    assert "rsi" not in short
    assert "atr" not in short
    fifteen = compute_technicals(_bars([100.0] * 15))
    assert fifteen["rsi"] == pytest.approx(100.0)
    assert fifteen["atr"] == pytest.approx(2.0)


# -- ATR --------------------------------------------------------------------------


def test_atr_uses_previous_close_gap_not_just_high_low() -> None:
    values = compute_technicals(_atr_fixture())
    assert values["atr"] == pytest.approx(16.0 / 7.0)
    assert values["atr"] != pytest.approx(34.0 / 15.0)


def test_atr_on_main_fixture() -> None:
    assert compute_technicals(_main_fixture())["atr"] == pytest.approx(MAIN_ATR)


# -- volatility -------------------------------------------------------------------


def test_volatility_is_average_daily_range_over_close() -> None:
    values = compute_technicals(_volatility_fixture())
    assert values["volatility_1w"] == pytest.approx(6.0)
    assert "volatility_4w" not in values


def test_volatility_on_main_fixture() -> None:
    values = compute_technicals(_main_fixture())
    assert values["volatility_1w"] == pytest.approx(MAIN_VOL_1W)
    assert values["volatility_4w"] == pytest.approx(MAIN_VOL_4W)


# -- volume -----------------------------------------------------------------------


def test_average_volume_uses_sixty_five_sessions() -> None:
    values = compute_technicals(_volume_fixture())
    assert values["average_volume"] == pytest.approx(66000.0 / 65.0)
    assert values["relative_volume"] == pytest.approx(65.0 / 33.0)


def test_average_volume_short_series_uses_every_bar() -> None:
    values = compute_technicals(_bars([100.0] * 10, volumes=[100.0] * 10))
    assert values["average_volume"] == pytest.approx(100.0)
    assert values["relative_volume"] == pytest.approx(1.0)


def test_zero_average_volume_has_no_relative_volume() -> None:
    values = compute_technicals(_bars([100.0] * 10, volumes=[0.0] * 10))
    assert values["average_volume"] == pytest.approx(0.0)
    assert "relative_volume" not in values


# -- 52-week range ----------------------------------------------------------------


def test_52w_window_is_the_last_252_bars() -> None:
    values = compute_technicals(_range_fixture())
    assert values["high_52w"] == pytest.approx(111.0)
    assert values["high_52w"] != pytest.approx(150.0)
    assert values["low_52w"] == pytest.approx(50.0)
    assert values["week_52_range"] == pytest.approx(122.0)


def test_52w_fields_on_main_fixture() -> None:
    values = compute_technicals(_main_fixture())
    assert values["high_52w"] == pytest.approx(111.0)
    assert values["low_52w"] == pytest.approx(99.0)
    assert values["week_52_range"] == pytest.approx(MAIN_RANGE_52W)


# -- gap / change from open ---------------------------------------------------------


def test_gap_and_change_from_open() -> None:
    values = compute_technicals(_main_fixture())
    assert values["gap"] == pytest.approx(5.0)
    assert values["change_from_open"] == pytest.approx(MAIN_CHANGE_FROM_OPEN)


def test_gap_absent_when_previous_close_is_zero() -> None:
    values = compute_technicals(_gap_fixture())
    assert "gap" not in values
    assert values["change_from_open"] == pytest.approx(10.0)
    assert "week_52_range" not in values
    assert values["high_52w"] == pytest.approx(12.0)
    assert values["low_52w"] == pytest.approx(0.0)
    assert values["average_volume"] == pytest.approx(50.0)
    assert values["relative_volume"] == pytest.approx(2.0)


# -- degenerate input ---------------------------------------------------------------


def test_empty_bar_list_returns_empty_dict() -> None:
    assert compute_technicals([]) == {}


def test_single_bar_returns_empty_dict() -> None:
    assert compute_technicals(_bars([100.0])) == {}


@pytest.mark.parametrize("count", [2, 3, 5, 10, 14])
def test_short_bar_lists_do_not_crash(count: int) -> None:
    values = compute_technicals(_bars([100.0] * count))
    assert isinstance(values, dict)
    for field in (
        "rsi",
        "atr",
        "sma20",
        "sma50",
        "sma200",
        "perf_4w",
        "perf_13w",
        "perf_26w",
        "perf_52w",
        "volatility_4w",
    ):
        assert field not in values


def test_compute_technicals_does_not_mutate_input() -> None:
    bars = _main_fixture()
    snapshot = list(bars)
    compute_technicals(bars)
    assert bars == snapshot


def test_main_fixture_emits_exactly_the_indicator_set() -> None:
    values = compute_technicals(_main_fixture())
    assert set(values) == {
        "perf_1w",
        "perf_4w",
        "perf_13w",
        "perf_26w",
        "perf_52w",
        "sma20",
        "sma50",
        "sma200",
        "rsi",
        "atr",
        "volatility_1w",
        "volatility_4w",
        "high_52w",
        "low_52w",
        "week_52_range",
        "average_volume",
        "relative_volume",
        "gap",
        "change_from_open",
    }


# -- parse_bars ----------------------------------------------------------------------


def _row(
    day: date,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    adjclose: float | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "date": day,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }
    if adjclose is not None:
        row["adjclose"] = adjclose
    return row


def _payload(rows: list[dict[str, Any]], *, wrap: bool = True) -> dict[str, Any]:
    timestamps = [_stamp(row["date"]) for row in rows]
    quote: dict[str, Any] = {
        "open": [row["open"] for row in rows],
        "high": [row["high"] for row in rows],
        "low": [row["low"] for row in rows],
        "close": [row["close"] for row in rows],
        "volume": [row["volume"] for row in rows],
    }
    indicators: dict[str, Any] = {"quote": [quote]}
    if any("adjclose" in row for row in rows):
        indicators["adjclose"] = [{"adjclose": [row.get("adjclose") for row in rows]}]
    entry = {
        "meta": {"symbol": "TEST", "dataGranularity": "1d"},
        "timestamp": timestamps,
        "indicators": indicators,
    }
    if not wrap:
        return entry
    return {"chart": {"result": [entry], "error": None}}


def test_parse_bars_reads_yahoo_v8_payload() -> None:
    rows = [
        _row(date(2025, 1, 1), 100.0, 104.0, 99.0, 102.0, 1000.0, adjclose=101.5),
        _row(date(2025, 1, 2), 102.0, 106.0, 101.0, 105.0, 1500.0, adjclose=104.5),
        _row(date(2025, 1, 3), 105.0, 107.0, 103.0, 103.0, 1200.0, adjclose=102.5),
    ]
    bars = parse_bars(_payload(rows))
    assert len(bars) == 3
    assert all(isinstance(bar, Bar) for bar in bars)
    assert [bar.date for bar in bars] == [
        date(2025, 1, 1),
        date(2025, 1, 2),
        date(2025, 1, 3),
    ]
    assert [bar.open for bar in bars] == [100.0, 102.0, 105.0]
    assert [bar.high for bar in bars] == [104.0, 106.0, 107.0]
    assert [bar.low for bar in bars] == [99.0, 101.0, 103.0]
    assert [bar.close for bar in bars] == [102.0, 105.0, 103.0]
    assert [bar.volume for bar in bars] == [1000.0, 1500.0, 1200.0]


def test_parse_bars_uses_raw_close_not_adjclose() -> None:
    rows = [_row(date(2025, 1, 1), 100.0, 101.0, 99.0, 100.0, 10.0, adjclose=1.0)]
    bars = parse_bars(_payload(rows))
    assert bars[0].close == pytest.approx(100.0)
    assert bars[0].close != pytest.approx(1.0)


def test_parse_bars_converts_epoch_timestamps_to_utc_dates() -> None:
    entry = {
        "timestamp": [
            int(datetime(2025, 3, 10, 14, 30, tzinfo=UTC).timestamp()),
            int(datetime(2025, 12, 31, 23, 30, tzinfo=UTC).timestamp()),
            1735689600.5,
        ],
        "indicators": {
            "quote": [
                {
                    "open": [10.0, 20.0, 30.0],
                    "high": [11.0, 21.0, 31.0],
                    "low": [9.0, 19.0, 29.0],
                    "close": [10.5, 20.5, 30.5],
                    "volume": [1.0, 2.0, 3.0],
                }
            ]
        },
    }
    bars = parse_bars({"chart": {"result": [entry]}})
    assert [bar.date for bar in bars] == [
        date(2025, 3, 10),
        date(2025, 12, 31),
        date(2025, 1, 1),
    ]


def test_parse_bars_skips_unusable_rows() -> None:
    entry = {
        "timestamp": [_stamp(date(2025, 1, 1)) + index * 86400 for index in range(5)],
        "indicators": {
            "quote": [
                {
                    "open": [100.0, 100.0, 100.0, 100.0, 100.0],
                    "high": [104.0, 104.0, 90.0, 104.0, 104.0],
                    "low": [99.0, 99.0, 99.0, 99.0, 99.0],
                    "close": [102.0, None, 100.0, 102.0, 102.0],
                    "volume": [1000.0, 1000.0, 1000.0, 1000.0, None],
                }
            ]
        },
    }
    entry["timestamp"][3] = "not-a-timestamp"
    bars = parse_bars({"chart": {"result": [entry]}})
    assert len(bars) == 2
    assert bars[0].date == date(2025, 1, 1)
    assert bars[1].date == date(2025, 1, 5)
    assert bars[1].volume == 0.0


def test_parse_bars_accepts_bare_result_entry() -> None:
    rows = [_row(date(2025, 1, 1), 100.0, 101.0, 99.0, 100.0, 10.0)]
    bars = parse_bars(_payload(rows, wrap=False))
    assert len(bars) == 1
    assert bars[0].close == pytest.approx(100.0)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"chart": {}},
        {"chart": {"result": []}},
        {"chart": {"result": [{}]}},
        {"chart": {"result": [{"timestamp": [1], "indicators": {}}]}},
        {"chart": {"error": "Not Found"}},
        "not-a-dict",
    ],
)
def test_parse_bars_returns_empty_for_unusable_payloads(payload: Any) -> None:
    assert parse_bars(payload) == []


def test_parse_then_compute_matches_direct_bars() -> None:
    fixture = _main_fixture()
    rows = [
        _row(bar.date, bar.open, bar.high, bar.low, bar.close, bar.volume)
        for bar in fixture
    ]
    parsed = compute_technicals(parse_bars(_payload(rows)))
    direct = compute_technicals(fixture)
    assert parsed == direct
    assert parsed["perf_1w"] == pytest.approx(10.0)
    assert parsed["rsi"] == pytest.approx(100.0)
    assert parsed["atr"] == pytest.approx(MAIN_ATR)
