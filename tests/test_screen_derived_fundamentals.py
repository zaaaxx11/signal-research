"""P11I: the two ratios the fundamentals join computes from snapshot cells.

``fa_pc`` (Price/Cash) and ``fa_evsales`` (EV/Sales) were refused because the
bulk scan has no cash metric and no enterprise value. The fundamentals snapshot
store does not carry either ratio *as a cell* — it carries the inputs:
``market_cap``, ``cash_and_equivalents``, ``total_debt`` and ``revenue_ttm``.
:mod:`app.screen.fundamentals` therefore owns two identities:

    price_to_cash = market_cap / cash_and_equivalents
    ev_to_sales   = (market_cap + total_debt - cash_and_equivalents) / revenue_ttm

Every test below pins a way a computed ratio can lie:

* the wrong formula (``market_cap_usd`` instead of ``market_cap``, revenue that
  forgot the debt) — the formula tests assert the exact expression and use
  cells whose alternatives differ;
* a denominator of zero or a negative one, which must be **unknown**, never
  ``ZeroDivisionError``, ``0.0`` or ``inf``;
* an incomplete numerator (a symbol with a market cap and no ``total_debt`` is
  not a debt-free company), a non-finite cell and a cell that is not a number;
* a snapshot that is missing, stale, or keyed to another symbol — no value;
* a row or a snapshot mutated by the join, or snapshot inputs leaking onto the
  selection row;
* a key that resolves to a column the row never carries, or a disposition that
  drifted (45/37 was P11G; 44 single-column + 3 choice / 35 refused was P11I;
  this pass is 47 single-column + 3 choice / 32 refused after the facts join,
  and the published schema has to say the same thing).

The live checks at the end run the real filter DSL and the real ASGI route over
the joined rows, because "the column exists" is not the claim — "a filter on it
selects the right companies" is.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

import pytest

from app.fundamentals.snapshot import FUNDAMENTAL_FIELDS, FundamentalsSnapshot
from app.screen import fundamentals as joined
from app.screen.filter_fields import (
    CHOICE_FIELDS,
    ROW_FIELDS,
    UNAVAILABLE,
    binding,
    mapped_keys,
    reason_for,
    unavailable_keys,
)
from app.screen.filters import FIELDS, FILTER_CATALOG, apply_filters, compile_filter
from app.screen.selection import (
    SELECTION_REQUEST_SCHEMA,
    parse_selection,
    select_rows,
)
from tests import _screen as S

ROOT = Path(__file__).resolve().parents[1]

#: The two keys this pass moves from "refused" to "mapped", and their columns.
P11I: dict[str, str] = {
    "fa_pc": "price_to_cash",
    "fa_evsales": "ev_to_sales",
}

#: Cells whose alternatives would produce a different answer, so a test that
#: passes cannot be passing by accident:
#: ``market_cap / cash = 4.0`` while ``market_cap_usd / cash = 8.0``, and the
#: enterprise value includes ``debt`` while ``market_cap / revenue`` does not.
MARKET_CAP = 1_000_000_000_000.0
MARKET_CAP_USD = 2_000_000_000_000.0
CASH = 250_000_000_000.0
DEBT = 120_000_000_000.0
REVENUE = 400_000_000_000.0


def snapshot(
    symbol: str,
    *,
    expires_in: timedelta = timedelta(hours=24),
    **cells: Any,
) -> FundamentalsSnapshot:
    """One stored snapshot: full width, only ``cells`` filled."""

    moment = datetime.now(UTC)
    fields: dict[str, Any] = {name: None for name in FUNDAMENTAL_FIELDS}
    for name, value in cells.items():
        assert name in fields, f"{name} is not a fundamentals cell"
        fields[name] = value
    return FundamentalsSnapshot(
        symbol=symbol,
        instrument_id=f"test:{symbol}",
        fields=fields,
        source="yahoo",
        as_of=moment,
        fetched_at=moment,
        expires_at=moment + expires_in,
    )


def universe_row(symbol: str, **cells: Any) -> dict[str, Any]:
    """A parity row as ``build_universe`` emits it: price, cap, no fundamentals."""

    row: dict[str, Any] = {
        "symbol": symbol,
        "name": symbol,
        "exchange": "XNAS",
        "country": "United States",
        "sector": "Technology",
        "price": 100.0,
        "change_pct": 1.0,
        "volume": 1_000_000.0,
        "market_cap_usd": 1_000_000_000.0,
        "pe_forward": None,
        "ps": None,
        "payout_ratio": None,
    }
    row.update(cells)
    return row


def full_snapshot(symbol: str = "AAPL", **overrides: Any) -> FundamentalsSnapshot:
    """The four inputs, all present unless a test removes one."""

    cells: dict[str, Any] = {
        "market_cap": MARKET_CAP,
        "market_cap_usd": MARKET_CAP_USD,
        "cash_and_equivalents": CASH,
        "total_debt": DEBT,
        "revenue_ttm": REVENUE,
    }
    cells.update(overrides)
    return snapshot(symbol, **cells)


class FakeStore:
    """The one-method surface the join reads: ``rows()``, counted."""

    def __init__(self, *snapshots: FundamentalsSnapshot) -> None:
        self._snapshots = list(snapshots)
        self.reads = 0

    async def rows(self) -> Sequence[FundamentalsSnapshot]:
        self.reads += 1
        return list(self._snapshots)


# ---------------------------------------------------------------------------
# The formulas, exactly
# ---------------------------------------------------------------------------


def test_price_to_cash_is_market_cap_over_cash_and_not_market_cap_usd() -> None:
    cells = joined.derive_cells(full_snapshot())

    assert cells["price_to_cash"] == MARKET_CAP / CASH, (
        "the formula is market_cap / cash_and_equivalents, exactly — no rounding, "
        "no second market-cap column"
    )
    assert cells["price_to_cash"] == 4.0
    assert MARKET_CAP_USD / CASH == 8.0, "the alternative must differ for the test to bite"


def test_ev_to_sales_is_enterprise_value_over_revenue_and_includes_debt() -> None:
    cells = joined.derive_cells(full_snapshot())

    expected = (MARKET_CAP + DEBT - CASH) / REVENUE
    assert cells["ev_to_sales"] == expected, (
        "the numerator is market_cap + total_debt - cash_and_equivalents; dropping "
        "the debt would answer a different question"
    )
    assert cells["ev_to_sales"] == pytest.approx(2.175)
    assert cells["ev_to_sales"] != pytest.approx(MARKET_CAP / REVENUE), (
        "a market-cap/sales ratio is not EV/sales"
    )


def test_a_negative_enterprise_value_is_a_negative_multiple() -> None:
    # Net cash above the market cap: a real answer, and the Finviz key's own
    # vocabulary has a `negative` bucket for it.
    cash_rich = full_snapshot(cash_and_equivalents=MARKET_CAP + 2 * DEBT)
    cells = joined.derive_cells(cash_rich)

    assert cells["ev_to_sales"] == pytest.approx((MARKET_CAP + DEBT - (MARKET_CAP + 2 * DEBT)) / REVENUE)
    multiple = cells["ev_to_sales"]
    assert multiple is not None and multiple < 0
    assert cells["price_to_cash"] == pytest.approx(MARKET_CAP / (MARKET_CAP + 2 * DEBT))


def test_the_formula_tables_name_the_same_two_columns_and_only_snapshot_cells() -> None:
    assert set(joined.DERIVED_FORMULAS) == set(joined.DERIVED_ROW_FIELDS)
    assert set(joined.DERIVED_INPUTS) == set(joined.DERIVED_ROW_FIELDS)
    assert joined.FUNDAMENTALS_ROW_FIELDS == joined.JOINED_ROW_FIELDS + joined.DERIVED_ROW_FIELDS
    assert set(joined.JOINED_ROW_FIELDS) == {"pe_forward", "ps", "payout_ratio"}
    assert tuple(joined.DERIVED_ROW_FIELDS) == ("price_to_cash", "ev_to_sales")
    for name, inputs in joined.DERIVED_INPUTS.items():
        assert inputs, f"{name} reads no cell"
        assert set(inputs) <= set(FUNDAMENTAL_FIELDS), (
            f"{name} reads a cell the snapshot vocabulary does not have: "
            f"{sorted(set(inputs) - set(FUNDAMENTAL_FIELDS))}"
        )
        assert set(joined.DERIVED_DENOMINATORS[name]) <= set(inputs)
    assert joined.DERIVED_FORMULAS["price_to_cash"] == "market_cap / cash_and_equivalents"
    assert joined.DERIVED_FORMULAS["ev_to_sales"] == (
        "(market_cap + total_debt - cash_and_equivalents) / revenue_ttm"
    )


# ---------------------------------------------------------------------------
# Zero, negative, missing, null: unknown, never invented
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cash", [0.0, -1.0, -250_000_000_000.0])
def test_a_non_positive_cash_balance_makes_price_to_cash_unknown(cash: float) -> None:
    cells = joined.derive_cells(full_snapshot(cash_and_equivalents=cash))

    assert cells["price_to_cash"] is None, (
        "a ratio over zero (or negative) cash is not a measurement"
    )
    assert cells["ev_to_sales"] is not None, (
        "the cash balance is a numerator term for EV/sales, so that ratio survives"
    )


@pytest.mark.parametrize("revenue", [0.0, -400_000_000_000.0])
def test_a_non_positive_revenue_makes_ev_to_sales_unknown(revenue: float) -> None:
    cells = joined.derive_cells(full_snapshot(revenue_ttm=revenue))

    assert cells["ev_to_sales"] is None
    assert cells["price_to_cash"] == 4.0, "the cash ratio does not read revenue"


@pytest.mark.parametrize(
    ("missing", "price_to_cash_expected", "ev_to_sales_expected"),
    [
        ("market_cap", None, None),
        ("cash_and_equivalents", None, None),
        ("total_debt", 4.0, None),
        ("revenue_ttm", 4.0, None),
    ],
)
def test_a_missing_input_makes_only_the_ratio_that_needs_it_unknown(
    missing: str, price_to_cash_expected: float | None, ev_to_sales_expected: float | None
) -> None:
    cells = joined.derive_cells(full_snapshot(**{missing: None}))

    assert cells["price_to_cash"] == price_to_cash_expected
    assert cells["ev_to_sales"] == ev_to_sales_expected


@pytest.mark.parametrize(
    "value",
    [math.nan, math.inf, -math.inf, "123", True, object()],
)
def test_a_non_finite_or_non_numeric_cell_is_unknown(value: Any) -> None:
    # ``FundamentalsSnapshot`` refuses these on write, so this is the defensive
    # path for a cell that reached the table some other way: the ratio has to be
    # unknown — never ``inf``, never ``NaN``, never a coerced string.
    fields: dict[str, Any] = {name: None for name in FUNDAMENTAL_FIELDS}
    fields.update(
        {
            "market_cap": MARKET_CAP,
            "cash_and_equivalents": value,
            "total_debt": DEBT,
            "revenue_ttm": REVENUE,
        }
    )
    cells = joined.derive_cells(SimpleNamespace(fields=fields))  # type: ignore[arg-type]

    assert cells["price_to_cash"] is None
    assert cells["ev_to_sales"] is None, "cash is a numerator term too"


def test_an_empty_snapshot_answers_none_for_both() -> None:
    cells = joined.derive_cells(snapshot("EMPTY"))

    assert cells == {"price_to_cash": None, "ev_to_sales": None}


# ---------------------------------------------------------------------------
# The join onto selection rows
# ---------------------------------------------------------------------------


def test_the_join_writes_both_columns_and_leaks_no_snapshot_input() -> None:
    rows = [universe_row("AAPL")]
    enriched = joined.apply_fundamentals(rows, {"AAPL": full_snapshot()})

    row = enriched[0]
    assert row["price_to_cash"] == 4.0
    assert row["ev_to_sales"] == pytest.approx(2.175)
    for name in ("market_cap", "cash_and_equivalents", "total_debt", "revenue_ttm"):
        assert name not in row, (
            f"{name} is a snapshot input, not a selection cell: the join publishes "
            "the ratio, not the balance sheet"
        )
    assert row["price"] == 100.0, "the join must not disturb the cells it does not own"


def test_a_symbol_the_store_does_not_hold_gets_none_not_zero() -> None:
    rows = [universe_row("AAPL"), universe_row("TSM")]
    enriched = joined.apply_fundamentals(rows, {"AAPL": full_snapshot()})

    tsm = enriched[1]
    assert (tsm["price_to_cash"], tsm["ev_to_sales"]) == (None, None)


def test_the_two_columns_are_present_on_every_row_even_without_a_store() -> None:
    rows = [{"symbol": "AAPL", "price": 1.0}, {"price": 2.0}]
    enriched = joined.apply_fundamentals(rows, {})

    for row in enriched:
        assert "price_to_cash" in row and "ev_to_sales" in row
        assert row["price_to_cash"] is None and row["ev_to_sales"] is None


def test_a_stale_snapshot_computes_nothing() -> None:
    stale = snapshot(
        "AAPL",
        expires_in=timedelta(seconds=-1),
        market_cap=MARKET_CAP,
        cash_and_equivalents=CASH,
        total_debt=DEBT,
        revenue_ttm=REVENUE,
    )
    rows = [universe_row("AAPL")]

    enriched = joined.apply_fundamentals(rows, joined.fresh_snapshots([stale]))

    assert enriched[0]["price_to_cash"] is None
    assert enriched[0]["ev_to_sales"] is None, (
        "a balance sheet past its window is not evidence of today's ratio"
    )


def test_the_join_never_mutates_the_rows_or_the_snapshots() -> None:
    rows = [universe_row("AAPL"), universe_row("MSFT")]
    stored = full_snapshot()
    before_rows = json.dumps(rows, sort_keys=True)
    before_snapshot = json.dumps(stored.fields, sort_keys=True)

    enriched = joined.apply_fundamentals(rows, {"AAPL": stored})

    assert json.dumps(rows, sort_keys=True) == before_rows
    assert json.dumps(stored.fields, sort_keys=True) == before_snapshot
    assert enriched[0] is not rows[0], "a joined row is a copy, not the input object"


async def test_enrich_rows_reads_the_store_once_for_the_whole_universe() -> None:
    rows = [universe_row(symbol) for symbol in ("AAPL", "MSFT", "NVDA", "AMZN")]
    store = FakeStore(*[full_snapshot(row["symbol"]) for row in rows])

    enriched = await joined.enrich_rows(rows, store)

    assert store.reads == 1, "one batch read per selection, never one query per symbol"
    assert [row["price_to_cash"] for row in enriched] == [4.0, 4.0, 4.0, 4.0]


# ---------------------------------------------------------------------------
# The keys and the published schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("key", "field"), sorted(P11I.items()))
def test_the_two_keys_resolve_to_the_computed_columns(key: str, field: str) -> None:
    assert FILTER_CATALOG[key].field == field
    assert ROW_FIELDS[key] == field
    assert key not in UNAVAILABLE
    assert reason_for(key) is None
    assert binding(key).is_available is True
    assert binding(key).field == field
    assert joined.FUNDAMENTALS_FILTER_KEYS[key] == field
    assert field in joined.FUNDAMENTALS_ROW_FIELDS
    assert field in joined.DERIVED_ROW_FIELDS
    assert FIELDS[field] == "number"


def test_the_disposition_is_fifty_available_and_thirty_two_refused() -> None:
    assert len(mapped_keys()) == 50
    assert len(unavailable_keys()) == 32
    assert len(mapped_keys()) + len(unavailable_keys()) == 82
    assert set(mapped_keys()) == set(ROW_FIELDS) | set(CHOICE_FIELDS)
    for key in P11I:
        assert key not in unavailable_keys()


def test_the_published_schema_counts_and_names_the_two_new_columns() -> None:
    artifact = ROOT / "schemas" / "stock_selection_request_v1.json"
    assert json.loads(artifact.read_text(encoding="utf-8")) == SELECTION_REQUEST_SCHEMA

    published: dict[str, Any] = dict(SELECTION_REQUEST_SCHEMA["x-filter-keys"])  # type: ignore[arg-type]
    mapped = {key: entry for key, entry in published.items() if entry["field"] is not None}
    chosen = {key: entry for key, entry in published.items() if "choice" in entry}
    refused = {
        key: entry
        for key, entry in published.items()
        if entry["field"] is None and "choice" not in entry
    }
    assert len(mapped) == 47 and len(chosen) == 3 and len(refused) == 32
    assert set(mapped) | set(chosen) == set(mapped_keys())
    assert set(refused) == set(unavailable_keys())

    for key, field in P11I.items():
        assert mapped[key]["field"] == field
        assert mapped[key]["kind"] == "number"
        assert "reason" not in mapped[key], f"{key} is mapped, so it carries no refusal"

    description = SELECTION_REQUEST_SCHEMA["$defs"]["filter"]["properties"]["key"]["description"]  # type: ignore[index]
    assert "The 47 that resolve to one row column" in description
    assert "the 32 that are refused" in description


# ---------------------------------------------------------------------------
# A real filter over the joined rows: selection and API
# ---------------------------------------------------------------------------


def selection_payload(key: str, op: str, value: Any) -> dict[str, Any]:
    return {
        "mode": "criteria",
        "region": "us",
        "criteria": {"filters": [{"key": key, "op": op, "value": value}], "limit": 20},
    }


def test_the_selection_filters_on_price_to_cash_and_ev_to_sales() -> None:
    rows = [
        universe_row("CHEAP", price=10.0),
        universe_row("PRICEY", price=900.0),
        universe_row("LEVERED", price=50.0),
    ]
    enriched = joined.apply_fundamentals(
        rows,
        {
            # 1e12 / 2.5e11 = 4.0; a price/cash-per-share implementation would
            # read the row's price instead and answer a different number.
            "CHEAP": full_snapshot("CHEAP", cash_and_equivalents=2.5e11, total_debt=1e11),
            "PRICEY": full_snapshot("PRICEY", cash_and_equivalents=1e11, total_debt=0.0),
            "LEVERED": full_snapshot("LEVERED", cash_and_equivalents=2e10, total_debt=9e11),
        },
    )
    # CHEAP: 4.0 and (1e12 + 1e11 - 2.5e11) / 4e11 = 2.125
    # PRICEY: 10.0 and (1e12 + 0 - 1e11) / 4e11 = 2.25
    # LEVERED: 50.0 and (1e12 + 9e11 - 2e10) / 4e11 = 4.45

    cheap_cash = select_rows(
        enriched,
        parse_selection(selection_payload("fa_pc", "lte", 5)),
        region="us",
        as_of="2026-10-25T12:00:00+00:00",
    )
    cheap_ev = select_rows(
        enriched,
        parse_selection(selection_payload("fa_evsales", "lt", 2.2)),
        region="us",
        as_of="2026-10-25T12:00:00+00:00",
    )

    assert [row["symbol"] for row in cheap_cash.rows] == ["CHEAP"]
    assert cheap_cash.total == 1
    assert cheap_cash.filters[0].field == "price_to_cash"
    assert [row["symbol"] for row in cheap_ev.rows] == ["CHEAP"]
    assert cheap_ev.total == 1
    assert cheap_ev.filters[0].field == "ev_to_sales"


def test_an_unknown_ratio_matches_no_filter_including_ne() -> None:
    rows = [universe_row("NO_DEBT"), universe_row("COMPLETE")]
    enriched = joined.apply_fundamentals(
        rows,
        {
            "NO_DEBT": full_snapshot("NO_DEBT", total_debt=None),
            "COMPLETE": full_snapshot("COMPLETE"),
        },
    )

    missing = apply_filters(enriched, [{"field": "ev_to_sales", "op": "ne", "value": 0}])

    assert [row["symbol"] for row in missing] == ["COMPLETE"], (
        "a symbol with no enterprise value is unknown, and unknown is not 'different "
        "from zero'"
    )


def test_between_and_in_work_on_the_computed_columns() -> None:
    row = joined.apply_fundamentals([universe_row("AAPL")], {"AAPL": full_snapshot()})[0]

    between = compile_filter({"field": "ev_to_sales", "op": "between", "value": [2, 3]})
    membership = compile_filter({"field": "price_to_cash", "op": "in", "value": [3, 4, 5]})

    assert apply_filters([row], [between]) == [row]
    assert apply_filters([row], [membership]) == [row]


async def test_the_route_filters_on_price_to_cash_from_the_store(
    tmp_path: Path, monkeypatch: Any
) -> None:
    store = FakeStore(
        # 4.0
        full_snapshot("AAPL", cash_and_equivalents=2.5e11),
        # 10.0
        full_snapshot("MSFT", cash_and_equivalents=1e11),
        # 20.0
        full_snapshot("NVDA", cash_and_equivalents=5e10),
    )
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        harness.runtime.fundamentals_store = store
        response = await harness.client.post(
            "/screens/select", json=selection_payload("fa_pc", "lte", 5)
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["symbol"] for row in body["rows"]] == ["AAPL"], body
    assert body["total"] == 1, (
        "only AAPL has a price/cash under 5; the other 22 universe rows have no "
        f"snapshot and match nothing (total={body['total']})"
    )
    assert body["filters"][0]["field"] == "price_to_cash"
    assert body["filters"][0]["key"] == "fa_pc"
    assert store.reads == 1, "one batch read per request, not one per row"


async def test_the_route_filters_on_ev_to_sales_and_skips_an_incomplete_numerator(
    tmp_path: Path, monkeypatch: Any
) -> None:
    store = FakeStore(
        # complete: (1e12 + 1e11 - 2.5e11) / 4e11 = 2.175
        full_snapshot("AAPL"),
        # no total_debt: enterprise value unknown, so no EV/sales at all
        full_snapshot("MSFT", total_debt=None),
    )
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        harness.runtime.fundamentals_store = store
        response = await harness.client.post(
            "/screens/select", json=selection_payload("fa_evsales", "lte", 3)
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["symbol"] for row in body["rows"]] == ["AAPL"], body
    assert body["total"] == 1, (
        "MSFT has a market cap and a revenue but no debt figure, so its enterprise "
        "value is unknown rather than debt-free"
    )
    assert body["filters"][0]["field"] == "ev_to_sales"
    assert store.reads == 1


async def test_the_route_answers_zero_without_a_store(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        harness.runtime.fundamentals_store = None
        response = await harness.client.post(
            "/screens/select", json=selection_payload("fa_pc", "gte", 0)
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 0, "unknown is not a match: no store means no price/cash"
    assert body["rows"] == []


def test_a_snapshot_row_built_from_the_store_shape_answers_both_ratios() -> None:
    """The real store's serialised cell names, not the test helper's spelling."""

    payload: Mapping[str, Any] = {
        "cash_and_equivalents": 62_469_001_216.0,
        "market_cap": 5_094_050_889_728.0,
        "market_cap_usd": 5_094_050_889_728.0,
        "revenue_ttm": 302_970_011_648.0,
        "total_debt": 38_860_001_280.0,
    }
    stored = snapshot("NVDA", **dict(payload))
    cells = joined.derive_cells(stored)

    assert cells["price_to_cash"] == pytest.approx(
        5_094_050_889_728.0 / 62_469_001_216.0
    )
    assert cells["ev_to_sales"] == pytest.approx(
        (5_094_050_889_728.0 + 38_860_001_280.0 - 62_469_001_216.0)
        / 302_970_011_648.0
    )
