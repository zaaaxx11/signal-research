"""P11B — three filter keys answered by a *derived* row column, not a new scan.

Contract (PLAN-screen P11B, "jalur 2": derive from data already carried):

* ``an_recom`` → ``recom_finviz`` = ``3 - 2 * recommendation``: Finviz's 1..5
  analyst score built from the provider's -1..+1 ``Recommend.All``;
* ``ta_changeopen`` → ``change_from_open`` =
  ``((1 + change_pct/100) / (1 + gap_pct/100) - 1) * 100``: the percent change
  from the session open. The previous close cancels between the two
  percentages, so no open price is requested or invented;
* ``ta_highlow52w`` → ``pct_from_52w_high`` = ``(price / high_52w - 1) * 100``:
  the percent distance from the 52-week high, **one direction only** — the low
  side stays the absolute ``low_52w`` and no low-side distance is fabricated.

Every value is checked for the three things that make a computed column honest:
the formula is the published one (exact, not approximate), a missing input
propagates ``None`` (never ``0``, never ``inf``, never a fallback to a nearby
cell), and a real zero is still a zero. The provider identity behind
``change_from_open`` is pinned from a live probe of three US symbols
(2026-10-24) and replayed here — the suite stays zero-egress.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.screen import derived
from app.screen.filter_fields import (
    ROW_FIELDS,
    UNAVAILABLE,
    binding,
    mapped_keys,
    unavailable_keys,
)
from app.screen.filters import FIELDS, FILTER_CATALOG, compile_filter
from app.screen.selection import parse_selection, select_rows
from app.screen.universe import (
    CARRIED_FIELDS,
    DERIVED_FIELDS,
    PARITY_COLUMNS,
    PARITY_FIELDS,
    UNIVERSE_COLUMNS,
    build_universe,
)
from app.screen.scanner import SCAN_COLUMNS

AS_OF = "2026-10-24T12:00:00.000000+00:00"

#: ``key -> (row field, input columns)``: the three mappings this pass adds.
DERIVED_KEYS: dict[str, tuple[str, tuple[str, ...]]] = {
    "an_recom": ("recom_finviz", ("recommendation",)),
    "ta_changeopen": ("change_from_open", ("price", "change_pct", "gap_pct")),
    "ta_highlow52w": ("pct_from_52w_high", ("price", "high_52w")),
}

#: A live probe of the provider answered these for three US symbols on
#: 2026-10-24 (``close``, ``change``, ``gap``, ``open``, ``Recommend.All``,
#: ``price_52_week_high``, plus the provider's own ``change_from_open``, which
#: the scan never asks for and only this test compares against). The numbers are
#: pinned so replaying them needs no socket: the suite is zero-egress.
LIVE_PROBE: tuple[dict[str, Any], ...] = (
    {
        "symbol": "AAPL",
        "close": 347.725,
        "change": -0.14215151341106327,
        "gap": 0.2570214232381775,
        "open": 349.115,
        "recommendation": 0.5575757575757576,
        "high_52w": 351.73,
        "provider_change_from_open": -0.3981496068630641,
    },
    {
        "symbol": "NVDA",
        "close": 210.96,
        "change": -0.5890391593233119,
        "gap": -0.5749022194995518,
        "open": 210.99,
        "recommendation": -0.46969696969696967,
        "high_52w": 243.37,
        "provider_change_from_open": -0.014218683349922335,
    },
    {
        "symbol": "JPM",
        "close": 331.58,
        "change": -1.4738218339573412,
        "gap": -1.0518809056872942,
        "open": 333.0,
        "recommendation": -0.4242424242424242,
        "high_52w": 366.5,
        "provider_change_from_open": -0.4264264264264312,
    },
)


# ---------------------------------------------------------------------------
# Rows and the selection path
# ---------------------------------------------------------------------------


def provider_row(symbol: str, **cells: Any) -> dict[str, Any]:
    """One scanner row: a fully reported US common stock plus ``cells``."""

    row: dict[str, Any] = {
        "symbol": symbol,
        "name": symbol,
        "description": f"{symbol} Holdings Inc.",
        "exchange": "NASDAQ",
        "country": "United States",
        "sector": "Technology",
        "type": "stock",
        "subtype": "common",
        "currency": "USD",
        "close": 100.0,
        "change": 1.0,
        "volume": 1_000_000.0,
        "Value.Traded": 100_000_000.0,
        "market_cap_basic": 1_000_000_000.0,
    }
    row.update(cells)
    return row


def parity_universe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A criteria selection's universe: whole market, parity columns."""

    return build_universe(
        rows,
        region="us",
        min_market_cap=0,
        columns=PARITY_COLUMNS,
        allow_any_subtype=True,
    )


def criteria(*filters: dict[str, Any]) -> dict[str, Any]:
    return {"mode": "criteria", "region": "us", "criteria": {"filters": list(filters)}}


def run(payload: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    request = parse_selection(payload)
    return select_rows(rows, request, region="us", as_of=AS_OF).as_document()


def symbols_of(document: dict[str, Any]) -> list[str]:
    return [row["symbol"] for row in document["rows"]]


# ---------------------------------------------------------------------------
# The formulas, exactly
# ---------------------------------------------------------------------------


def test_the_analyst_score_is_the_published_linear_map() -> None:
    """``3 - 2 * score``, endpoints and interior, with no rounding."""

    assert derived.recom_finviz(1.0) == 1.0, "Recommend.All +1 (strong buy) is Finviz 1"
    assert derived.recom_finviz(0.0) == 3.0, "a neutral mean rating is Finviz 3 (hold)"
    assert derived.recom_finviz(-1.0) == 5.0, "Recommend.All -1 (sell) is Finviz 5"
    assert derived.recom_finviz(0.5) == 2.0
    assert derived.recom_finviz(-0.5) == 4.0
    # No rounding: a live 0.5576 stays 1.8848 instead of collapsing to 2.
    assert derived.recom_finviz(0.5575757575757576) == 3.0 - 2.0 * 0.5575757575757576
    assert derived.recom_finviz(0.5575757575757576) != 2.0
    assert DERIVED_FIELDS["recom_finviz"] == "3 - 2 * recommendation"


def test_the_change_from_open_is_the_closed_form_of_close_over_open() -> None:
    """The formula in the table is the arithmetic the row actually gets."""

    close, change, gap = 100.0, 1.0, 0.25
    expected = ((1.0 + change / 100.0) / (1.0 + gap / 100.0) - 1.0) * 100.0
    assert derived.change_from_open(close, change, gap) == expected
    # 1.01 / 1.0025 = 1.0074813..., so the move from the open is 0.748%.
    assert derived.change_from_open(close, change, gap) == pytest.approx(0.7481296758104738)
    assert (
        DERIVED_FIELDS["change_from_open"]
        == "((1 + change_pct / 100) / (1 + gap_pct / 100) - 1) * 100"
    )


def test_pct_from_52w_high_is_the_percent_distance_from_the_high() -> None:
    assert derived.pct_from_52w_high(347.725, 351.73) == pytest.approx(-1.1386574929633464)
    assert derived.pct_from_52w_high(351.73, 351.73) == 0.0, "at a new high the distance is zero"
    assert derived.pct_from_52w_high(105.0, 100.0) == pytest.approx(5.0), (
        "a price above a stale 52-week high is +5%, not clamped to 0"
    )
    assert DERIVED_FIELDS["pct_from_52w_high"] == "(price / high_52w - 1) * 100"


@pytest.mark.parametrize("probe", LIVE_PROBE, ids=[row["symbol"] for row in LIVE_PROBE])
def test_the_change_from_open_matches_the_providers_own_column(probe: dict[str, Any]) -> None:
    """The derivation is the provider's definition, not a plausible estimate.

    Live probe, 2026-10-24: the provider reports ``change_from_open`` itself, and
    the value this row computes from close/change/gap agrees to a float
    round-trip (<= 1e-12 relative). No open column is requested by the scan.
    """

    ours = derived.change_from_open(probe["close"], probe["change"], probe["gap"])
    provider = probe["provider_change_from_open"]
    assert ours is not None
    assert ours == pytest.approx(provider, rel=1e-12), probe["symbol"]
    assert abs(ours - provider) < 1e-12, probe["symbol"]


@pytest.mark.parametrize("probe", LIVE_PROBE, ids=[row["symbol"] for row in LIVE_PROBE])
def test_the_three_live_symbols_replay_into_their_derived_columns(probe: dict[str, Any]) -> None:
    """A live row, put through the public path, carries all three columns.

    ``(close / open - 1) * 100`` is the independent check: the provider's own
    open from the probe, not a column the screen asks for.
    """

    row = provider_row(
        probe["symbol"],
        close=probe["close"],
        change=probe["change"],
        gap=probe["gap"],
        **{"Recommend.All": probe["recommendation"], "price_52_week_high": probe["high_52w"]},
    )
    universe = parity_universe([row])
    assert len(universe) == 1
    carried = universe[0]

    assert carried["change_from_open"] == pytest.approx(
        (probe["close"] / probe["open"] - 1.0) * 100.0, rel=1e-9
    )
    assert carried["recom_finviz"] == pytest.approx(
        3.0 - 2.0 * probe["recommendation"], rel=1e-12
    )
    assert carried["pct_from_52w_high"] == pytest.approx(
        (probe["close"] / probe["high_52w"] - 1.0) * 100.0, rel=1e-12
    )
    for field in DERIVED_FIELDS:
        assert carried[field] is not None, f"{probe['symbol']} lost {field}"
        assert isinstance(carried[field], float), (
            f"{field} must be a number on the row, got {type(carried[field]).__name__}"
        )


# ---------------------------------------------------------------------------
# Null propagation: unknown is not zero
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing", [None, float("nan"), float("inf"), float("-inf"), "34.5", True])
def test_a_missing_or_non_numeric_input_is_unknown(missing: Any) -> None:
    assert derived.recom_finviz(missing) is None
    assert derived.change_from_open(100.0, missing, 0.2) is None
    assert derived.change_from_open(100.0, 1.0, missing) is None
    assert derived.change_from_open(missing, 1.0, 0.2) is None
    assert derived.pct_from_52w_high(missing, 110.0) is None
    assert derived.pct_from_52w_high(100.0, missing) is None


@pytest.mark.parametrize(
    ("price", "change", "gap"),
    [
        (100.0, 1.0, -100.0),  # open would be zero
        (100.0, 1.0, -150.0),  # open would be negative
        (100.0, -100.0, 0.0),  # previous close would be zero
        (100.0, -250.0, 0.0),  # previous close would be negative
        (0.0, 1.0, 0.2),  # no price
        (-5.0, 1.0, 0.2),  # not a price
    ],
)
def test_an_invalid_denominator_is_unknown_not_zero_or_inf(
    price: float, change: float, gap: float
) -> None:
    assert derived.change_from_open(price, change, gap) is None


@pytest.mark.parametrize(("price", "high"), [(100.0, 0.0), (100.0, -1.0), (0.0, 100.0), (-1.0, 100.0)])
def test_a_non_positive_52w_high_or_price_is_unknown(price: float, high: float) -> None:
    assert derived.pct_from_52w_high(price, high) is None


@pytest.mark.parametrize("score", [-1.5, 1.0001, 7.0, -2.0, 2.1])
def test_a_rating_outside_the_providers_domain_is_unknown(score: float) -> None:
    """``3 - 2 * 7 = -11`` is not a Finviz score; refuse it instead of inventing one."""

    assert derived.recom_finviz(score) is None


def test_a_real_zero_is_reported_as_zero() -> None:
    """A flat session, a new high and a neutral rating are measurements, not nulls."""

    assert derived.change_from_open(100.0, 0.0, 0.0) == 0.0
    assert derived.change_from_open(100.0, 2.5, 0.0) == pytest.approx(2.5), (
        "a zero gap means the open is the previous close: change from open equals change"
    )
    assert derived.pct_from_52w_high(100.0, 100.0) == 0.0
    assert derived.recom_finviz(0.0) == 3.0


def test_a_missing_gap_does_not_fall_back_to_the_change() -> None:
    """The row has ``change_pct`` but no gap: the answer is unknown, never change_pct."""

    row = provider_row("NOGAP", gap=None, change=4.2)
    carried = parity_universe([row])[0]

    assert carried["change_pct"] == 4.2
    assert carried["gap_pct"] is None
    assert carried["change_from_open"] is None, (
        "a change from open needs the open side; reporting change_pct here would be a fake value"
    )


def test_a_row_without_the_inputs_carries_null_not_zero() -> None:
    """No recommendation, no gap, no 52-week high → three nulls on the row."""

    row = provider_row("BARE", **{"Recommend.All": None, "gap": None, "price_52_week_high": None})
    carried = parity_universe([row])[0]

    for field in DERIVED_FIELDS:
        assert field in carried, f"{field} must be present on a parity row, even when unknown"
        assert carried[field] is None, f"{field} became {carried[field]!r} for an unknown input"


# ---------------------------------------------------------------------------
# The three keys are live
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("key", "field", "inputs"), [(k, f, i) for k, (f, i) in DERIVED_KEYS.items()])
def test_each_key_resolves_to_its_derived_column(key: str, field: str, inputs: tuple[str, ...]) -> None:
    assert ROW_FIELDS[key] == field
    assert FILTER_CATALOG[key].field == field
    assert binding(key).field == field
    assert binding(key).is_available is True
    assert binding(key).kind == "number", "all three are numeric keys, not option tokens"
    assert key not in UNAVAILABLE
    assert key in mapped_keys()
    assert key not in unavailable_keys()
    assert FIELDS[field] == "number"
    assert derived.DERIVED_INPUTS[field] == inputs


@pytest.mark.parametrize("key", sorted(DERIVED_KEYS))
def test_a_derived_key_refuses_text_operators(key: str) -> None:
    """A numeric key, not Finviz's option vocabulary: ``contains`` cannot apply."""

    field = ROW_FIELDS[key]
    with pytest.raises(ValueError, match="needs a text field"):
        compile_filter({"field": field, "op": "contains", "value": "buy"})


@pytest.mark.parametrize(
    ("key", "op", "value", "expected"),
    [
        ("an_recom", "lt", 2.0, ["BUY"]),
        ("ta_changeopen", "gt", 0.0, ["BUY"]),
        ("ta_highlow52w", "gte", -3.0, ["BUY"]),
    ],
)
def test_a_derived_key_selects_over_the_parity_universe(
    key: str, op: str, value: float, expected: list[str]
) -> None:
    """Not just present on the row — the request path (key → column) filters it."""

    rows = [
        # +0.9 mean rating → Finviz 1.2; opens flat and closes +1.2%; 2% under its high.
        provider_row(
            "BUY",
            change=1.2,
            gap=0.0,
            **{"Recommend.All": 0.9, "price_52_week_high": 102.0},
        ),
        # -0.9 → 4.8 (a sell); gaps up 3% then fades to +1.2%; 30% under its high.
        provider_row(
            "SELL",
            change=1.2,
            gap=3.0,
            **{"Recommend.All": -0.9, "price_52_week_high": 142.85714285714286},
        ),
        # Nothing reported: in the universe, never in an answer.
        provider_row(
            "NULL",
            change=None,
            gap=None,
            **{"Recommend.All": None, "price_52_week_high": None},
        ),
    ]
    universe = parity_universe(rows)
    assert len(universe) == 3

    document = run(criteria({"key": key, "op": op, "value": value}), universe)

    assert symbols_of(document) == expected, f"{key} did not select {expected}"
    assert document["total"] == 1
    assert document["universe_total"] == 3, "the null row is in the universe, not in the answer"
    assert document["filters"][0]["field"] == ROW_FIELDS[key]
    assert document["filters"][0]["key"] == key


def test_the_light_row_gains_no_derived_column() -> None:
    """The browse/export path (``columns=None``) stays exactly ``UNIVERSE_COLUMNS``."""

    light = build_universe([provider_row("LIGHT", gap=0.3)], region="us")

    assert len(light) == 1
    assert set(light[0]) == set(UNIVERSE_COLUMNS)
    for field in DERIVED_FIELDS:
        assert field not in light[0]


def test_the_derived_columns_are_not_provider_columns() -> None:
    """No extra column is requested, so a parity scan costs what it did before."""

    assert set(DERIVED_FIELDS).isdisjoint(PARITY_FIELDS)
    assert set(DERIVED_FIELDS).isdisjoint(PARITY_COLUMNS), (
        "a derived column must never be sent to the scanner as a column name"
    )
    assert set(PARITY_FIELDS) <= set(PARITY_COLUMNS)
    assert len(SCAN_COLUMNS) == 17, "the browse scan keeps its 17 columns"
    assert set(DERIVED_FIELDS) <= CARRIED_FIELDS
    assert set(DERIVED_FIELDS).isdisjoint(UNIVERSE_COLUMNS)


def test_one_direction_only_for_the_52_week_extreme() -> None:
    """The high side is derived; the low side stays an absolute price on purpose."""

    assert "pct_from_52w_high" in FIELDS
    assert "pct_from_52w_low" not in FIELDS, (
        "a low-side distance would be an invented metric: the row carries the absolute low"
    )
    carried = parity_universe([provider_row("LOW", price_52_week_low=60.0, price_52_week_high=120.0)])[0]

    assert carried["low_52w"] == 60.0
    assert carried["high_52w"] == 120.0
    assert carried["pct_from_52w_high"] == pytest.approx((100.0 / 120.0 - 1.0) * 100.0)
