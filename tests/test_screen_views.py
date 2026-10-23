"""P1 gate — six views, explicit columns, and a null that carries a reason.

Gate (PLAN-finviz-parity.md §1, §2, §6 #2):

* exactly six tabs, in Finviz's order, each with a **hand-written** ordered
  column tuple — a view is a decision, not a predicate over the metric table;
* the five specialist views partition the 71 quote metrics and ``overview``
  touches every one of them, so no metric is reachable only by scrolling;
* a rendered row keeps one cell per declared column: a missing value is ``None``
  plus a reason, never a dropped column and never ``0``;
* ``fill_rate`` is computed per view — the P1 gate ("field fill-rate per view ≥
  Finviz") — not as a per-row average that hides a hole.
"""

from __future__ import annotations

import json

import pytest

from app.market import fields as F
from app.screen import views as V
from tests._support import ROOT

QUOTE_FIXTURE = ROOT / "tests" / "fixtures" / "finviz_quote_metrics.json"
SNAPSHOT = F.parse_snapshot(json.loads(QUOTE_FIXTURE.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# The six views
# ---------------------------------------------------------------------------


def test_there_are_exactly_six_views_in_finviz_order() -> None:
    assert [view.name for view in V.VIEWS] == [
        "overview",
        "valuation",
        "ownership",
        "performance",
        "financial",
        "technical",
    ]
    assert V.VIEW_NAMES == tuple(view.name for view in V.VIEWS)


def test_every_view_has_a_hand_written_ordered_column_tuple() -> None:
    for view in V.VIEWS:
        assert isinstance(view.columns, tuple), (
            f"{view.name} must own a tuple of keys, not a derived sequence"
        )
        assert len(view.columns) >= 5, f"{view.name} is too small to be a view"
        assert len(set(view.columns)) == len(view.columns), f"{view.name} repeats a column"
        unknown = [key for key in view.columns if key not in F.METRIC_BY_KEY]
        assert unknown == [], f"{view.name} names metrics that do not exist: {unknown}"
        assert view.title and view.description, f"{view.name} needs a readable identity"


def test_the_specialist_views_partition_the_quote_metrics() -> None:
    owner: dict[str, str] = {}
    for name in V.SPECIALIST_VIEW_NAMES:
        for key in V.VIEW_COLUMNS[name]:
            assert key not in owner, (
                f"{key} is in both {owner[key]} and {name}; the specialist views "
                "must partition the table so every metric has one home"
            )
            owner[key] = name

    expected = set(F.METRIC_BY_KEY) - set(V.OVERVIEW_ONLY)
    assert set(owner) == expected, (
        "every metric except OVERVIEW_ONLY needs a specialist home; "
        f"missing={sorted(expected - set(owner))}"
    )


def test_index_is_declared_as_overview_only() -> None:
    assert V.OVERVIEW_ONLY == ("index",)
    assert "index" in V.VIEW_COLUMNS["overview"]
    for name in V.SPECIALIST_VIEW_NAMES:
        assert "index" not in V.VIEW_COLUMNS[name], (
            "index membership is a classification, not a measurement; it is "
            "declared overview-only instead of being parked in a wrong tab"
        )


def test_overview_touches_every_specialist_view() -> None:
    overview = set(V.VIEW_COLUMNS["overview"])
    for name in V.SPECIALIST_VIEW_NAMES:
        shared = overview & set(V.VIEW_COLUMNS[name])
        assert shared, f"overview ignores {name!r} entirely: {sorted(shared)}"


def test_every_renderable_metric_appears_in_a_view() -> None:
    shown: set[str] = set()
    for view in V.VIEWS:
        shown.update(view.columns)
    assert shown == set(F.METRIC_BY_KEY), (
        f"metrics with no view: {sorted(set(F.METRIC_BY_KEY) - shown)}"
    )


@pytest.mark.parametrize(
    ("name", "columns"),
    [
        (
            "overview",
            (
                "price",
                "change_pct",
                "volume",
                "market_cap",
                "pe",
                "forward_pe",
                "eps_ttm",
                "eps_next_y",
                "sales",
                "ps",
                "roe",
                "week52_high",
                "week52_low",
                "perf_ytd",
                "perf_year",
                "rsi_14",
                "atr_14",
                "rel_volume",
                "beta",
                "shares_outstanding",
                "shares_float",
                "insider_own",
                "inst_own",
                "employees",
                "ipo",
                "index",
                "option_short",
            ),
        ),
        (
            "valuation",
            (
                "market_cap",
                "enterprise_value",
                "pe",
                "forward_pe",
                "peg",
                "ps",
                "pb",
                "pc",
                "pfcf",
                "ev_ebitda",
                "ev_sales",
            ),
        ),
        (
            "ownership",
            (
                "insider_own",
                "insider_trans",
                "inst_own",
                "inst_trans",
                "shares_outstanding",
                "shares_float",
                "option_short",
            ),
        ),
        (
            "performance",
            (
                "perf_week",
                "perf_month",
                "perf_quarter",
                "perf_half_y",
                "perf_ytd",
                "perf_year",
                "perf_3y",
                "perf_5y",
                "perf_10y",
                "week52_high",
                "week52_low",
            ),
        ),
        (
            "financial",
            (
                "income",
                "sales",
                "book_per_share",
                "cash_per_share",
                "eps_ttm",
                "eps_next_y",
                "eps_next_q",
                "eps_this_y",
                "eps_next_y_growth",
                "eps_next_5y",
                "eps_past_3_5y",
                "sales_past_3_5y",
                "eps_yoy_ttm",
                "sales_yoy_ttm",
                "eps_qoq",
                "sales_qoq",
                "roa",
                "roe",
                "roic",
                "gross_margin",
                "oper_margin",
                "profit_margin",
                "quick_ratio",
                "current_ratio",
                "debt_to_equity",
                "lt_debt_to_equity",
                "employees",
                "ipo",
            ),
        ),
        (
            "technical",
            (
                "price",
                "prev_close",
                "change_pct",
                "volume",
                "avg_volume",
                "rel_volume",
                "atr_14",
                "rsi_14",
                "sma20",
                "sma50",
                "sma200",
                "volatility",
                "beta",
            ),
        ),
    ],
)
def test_the_columns_of_each_view_are_pinned(name: str, columns: tuple[str, ...]) -> None:
    assert V.columns(name) == columns, (
        f"{name} lost, gained or reordered a column; a view is a contract, so the "
        "diff has to be deliberate"
    )


def test_an_unknown_view_is_a_caller_error() -> None:
    with pytest.raises(ValueError, match="unknown view"):
        V.view("summary")
    with pytest.raises(ValueError, match="unknown view"):
        V.build_view("summary", {})
    with pytest.raises(ValueError, match="unknown view"):
        V.fill_rate("summary", {})


def test_a_view_may_not_repeat_a_column() -> None:
    with pytest.raises(RuntimeError, match="repeats a column"):
        V.ViewDefinition(
            name="broken", title="Broken", columns=("pe", "pe"), description=""
        )


def test_the_column_map_is_the_definitions() -> None:
    for view in V.VIEWS:
        assert V.VIEW_COLUMNS[view.name] == view.columns
        assert V.VIEW_BY_NAME[view.name] is view


# ---------------------------------------------------------------------------
# Rendering: one cell per declared column, null with a reason
# ---------------------------------------------------------------------------


def test_building_a_view_keeps_the_declared_column_order() -> None:
    row = V.build_view("valuation", SNAPSHOT)
    assert row.keys == V.columns("valuation")
    assert tuple(row.as_dict()) == V.columns("valuation")


def test_a_complete_snapshot_fills_every_view() -> None:
    for view in V.VIEWS:
        row = V.build_view(view.name, SNAPSHOT)
        assert row.total == len(view.columns)
        assert row.missing == (), f"{view.name} has holes: {row.missing}"
        assert row.fill_rate == 1.0, f"{view.name} fill-rate {row.fill_rate}"


def test_a_missing_snapshot_is_all_null_with_reasons() -> None:
    row = V.build_view("valuation", {})
    assert row.total == len(V.columns("valuation"))
    assert set(row.values.values()) == {None}, "a missing cell is null, never 0"
    assert row.missing_keys == V.columns("valuation")
    assert {reason for _key, reason in row.missing} == {F.MissingReason.ABSENT}
    assert row.fill_rate == 0.0


def test_a_partial_cell_keeps_its_value_and_is_not_counted_as_filled() -> None:
    cells = {"week52_high": F.parse_cell("week52_high", "348.36 N/A")}
    row = V.build_view("performance", cells)

    assert dict(row.cells)["week52_high"] == (pytest.approx(348.36), None)
    assert dict(row.missing)["week52_high"] is F.MissingReason.PARTIAL
    assert row.filled == 0, "a half cell is visible data, but it is not a filled column"
    assert row.total == len(V.columns("performance"))


def test_an_unparsed_cell_keeps_its_reason() -> None:
    cells = {"pe": F.parse_cell("pe", "abc")}
    row = V.build_view("valuation", cells)
    assert row.values["pe"] is None
    assert dict(row.missing)["pe"] is F.MissingReason.UNPARSED


def test_a_real_zero_counts_as_filled() -> None:
    cells = F.parse_snapshot([{"name": "Current Ratio", "raw": "0.00"}])
    row = V.build_view("financial", cells)

    assert row.values["current_ratio"] == pytest.approx(0.0)
    assert "current_ratio" not in row.missing_keys, "0 is a value; only None is a hole"
    assert row.filled == 1


def test_plain_values_are_accepted_for_a_canonical_row() -> None:
    row = V.build_view("technical", {"price": 347.79, "volume": None})
    assert row.values["price"] == pytest.approx(347.79)
    assert row.values["volume"] is None
    assert dict(row.missing)["volume"] is F.MissingReason.ABSENT
    assert row.filled == 1


def test_fill_rate_is_filled_over_declared() -> None:
    row = V.build_view("valuation", {"pe": 39.87, "pb": 47.25})
    assert row.total == 11
    assert row.filled == 2
    assert row.fill_rate == pytest.approx(2 / 11)
    assert V.fill_rate("valuation", {"pe": 39.87, "pb": 47.25}) == pytest.approx(2 / 11)


def test_a_row_keeps_its_values_in_view_order() -> None:
    payload = V.build_view("overview", SNAPSHOT).as_dict()
    assert list(payload) == list(V.columns("overview"))
    assert payload["price"] == pytest.approx(347.79)
    assert payload["index"] == ("DJIA", "NDX", "S&P 500"), (
        "a multi-part cell keeps its parts; the API layer decides how to render them"
    )
