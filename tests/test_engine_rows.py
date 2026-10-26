"""Contract for canonical row assembly (``app.engine.rows``).

A row is the engine's unit of evidence: one instrument, every value still
attached to the observation that produced it. This module answers four
questions about that assembly, and the tests below are organised the same way:

* **identity** — which fields come from the security record, and what happens
  to the ones the record does not carry (they are omitted, never invented);
* **quote** — which market numbers become cells, in which unit, dated by the
  quote rather than by the call;
* **facts** — which SEC concepts may fill a catalog column, and what a unit
  disagreement does (it raises, because the fact slice can only emit dollars,
  shares or ``UNKNOWN``, so a mismatch is a wiring bug, not a provider quirk);
* **derivations** — the auditable tail: a derived cell never replaces an
  observation, never carries a zero it did not compute, and names the inputs
  and the date of the number it divided.

The row is a plain ``dict[str, Cell]``: no presentation, no provider name in a
key, no formatting. Everything here is a pure function over hand-built value
objects, except one integration test that runs the real Apple ``companyfacts``
fixture through ``fact_summary`` and then through the assembler, so the two
slices are proven to fit together on a document EDGAR actually published.
"""

from __future__ import annotations

import json
import math
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

from app.engine.catalog import CATALOG_BY_KEY
from app.engine.facts import DEFAULT_TAGS, fact_summary
from app.engine.model import Cell, CellStatus, Provenance, SecurityRecord, Unit
from app.engine.rows import (
    FACT_TO_METRIC,
    IDENTITY_FIELDS,
    INPUT_SOURCES,
    QUOTE_FIELDS,
    ROW_EXTENSION_KEYS,
    RowBuildError,
    build_row,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "sec_companyfacts_aapl.json"

#: The date the caller observed the row. Provenance cannot exist without one,
#: so the assembler requires it instead of borrowing the clock.
DAY = "2026-10-24"
#: The quote's own timestamp, deliberately different from ``DAY`` so a test can
#: tell which date a cell actually carries.
QUOTE_DAY = "2026-10-23T20:00:00Z"
#: Period end of the hand-built SEC facts below.
PERIOD_END = "2025-09-27"

_REVENUE_TAG = "RevenueFromContractWithCustomerExcludingAssessedTax"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _record(**overrides: object) -> SecurityRecord:
    base: dict[str, object] = {
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "cik": "0000320193",
        "exchange": "NASDAQ",
        "mic": "XNAS",
        "asset_class": "stock",
        "currency": "USD",
        "country": "United States",
        "sector": "Technology",
        "industry": "Consumer Electronics",
    }
    base.update(overrides)
    return SecurityRecord(**base)  # type: ignore[arg-type]


def _fact(
    value: float,
    *,
    unit: Unit = Unit.USD,
    as_of: str = PERIOD_END,
    tag: str = _REVENUE_TAG,
    accession: str = "0000320193-25-000079",
) -> Cell:
    """One SEC cell shaped exactly like ``fact_summary`` emits it."""

    return Cell(
        value=value,
        unit=unit,
        provenance=Provenance(
            source="sec",
            as_of=as_of,
            status=CellStatus.OK,
            inputs=(tag, accession),
        ),
    )


def _null_cell(
    *,
    unit: Unit = Unit.USD,
    status: CellStatus = CellStatus.ABSENT,
    as_of: str = PERIOD_END,
    source: str = "sec",
) -> Cell:
    return Cell(
        value=None,
        unit=unit,
        provenance=Provenance(source=source, as_of=as_of, status=status),
    )


def _facts() -> dict[str, Cell]:
    """The SEC slice for one issuer, keyed the way ``fact_summary`` keys it."""

    return {
        "revenue": _fact(391_035_000_000.0),
        "net_income": _fact(93_736_000_000.0, tag="NetIncomeLoss"),
        "eps_diluted": _fact(
            6.16, unit=Unit.UNKNOWN, tag="EarningsPerShareDiluted"
        ),
        "shares_outstanding": _fact(
            14_840_000_000.0,
            unit=Unit.SHARES,
            as_of="2025-10-17",
            tag="CommonStockSharesOutstanding",
        ),
        "operating_cash_flow": _fact(
            111_482_000_000.0, tag="NetCashProvidedByUsedInOperatingActivities"
        ),
        "capex": _fact(
            12_715_000_000.0, tag="PaymentsToAcquirePropertyPlantAndEquipment"
        ),
    }


def _quote() -> dict[str, object]:
    return {
        "price": 262.82,
        "prev_close": 260.50,
        "change": 0.89,
        "volume": 38_200_000,
        "as_of": QUOTE_DAY,
        "eps_forward": 8.35,
        "growth": 9.5,
        "book_per_share": 4.25,
        "sales_per_share": 26.35,
    }


def _derived(row: dict[str, Cell]) -> dict[str, Cell]:
    """The cells the assembler derived, by key."""

    return {
        key: cell
        for key, cell in row.items()
        if cell.provenance is not None
        and cell.provenance.source == "derived"
    }


# ---------------------------------------------------------------------------
# The error type
# ---------------------------------------------------------------------------


def test_row_build_error_is_a_frozen_value_error_with_a_code() -> None:
    error = RowBuildError("unit_mismatch", "revenue cannot fill sales")

    assert isinstance(error, ValueError)
    assert error.code == "unit_mismatch"
    assert error.message == "revenue cannot fill sales"
    assert str(error) == "revenue cannot fill sales"
    with pytest.raises(FrozenInstanceError):
        error.code = "unknown_symbol"  # type: ignore[misc]


def test_row_build_error_codes_are_the_two_documented_ones() -> None:
    assert RowBuildError("unknown_symbol", "x").code == "unknown_symbol"
    assert RowBuildError("unit_mismatch", "y").code == "unit_mismatch"


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_identity_fields_become_text_cells_with_identity_provenance() -> None:
    row = build_row(_record(), {}, as_of=DAY)

    expected = {
        "ticker": "AAPL",
        "company": "Apple Inc.",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "country": "United States",
        "exchange": "NASDAQ",
    }
    assert set(row) == set(expected)
    for key, value in expected.items():
        cell = row[key]
        assert cell.value == value
        assert cell.unit is Unit.TEXT
        assert cell.provenance is not None
        assert cell.provenance.source == "identity"
        assert cell.provenance.as_of == DAY
        assert cell.provenance.status is CellStatus.OK
        assert cell.provenance.url is None
        assert cell.provenance.inputs == ()


def test_identity_omits_fields_the_record_does_not_carry() -> None:
    row = build_row(
        _record(
            cik=None,
            exchange=None,
            country=None,
            sector=None,
            industry=None,
        ),
        {},
        as_of=DAY,
    )

    assert set(row) == {"ticker", "company"}
    assert "mic" not in row


def test_identity_only_row_has_no_prices_or_derivations() -> None:
    row = build_row(_record(), {}, as_of=DAY)

    numeric = {
        key
        for key, cell in row.items()
        if cell.unit is not Unit.TEXT
    }
    assert numeric == set()
    assert _derived(row) == {}


def test_ticker_keeps_the_record_spelling() -> None:
    row = build_row(_record(symbol="BRK.B"), {}, as_of=DAY)

    assert row["ticker"].value == "BRK.B"


# ---------------------------------------------------------------------------
# The as-of date
# ---------------------------------------------------------------------------


def test_as_of_keyword_is_required() -> None:
    with pytest.raises(TypeError):
        build_row(_record(), {})  # type: ignore[call-arg]


@pytest.mark.parametrize("bad", ["", "   ", None, 20261024, b"2026-10-24"])
def test_as_of_must_be_a_non_blank_string(bad: object) -> None:
    with pytest.raises(ValueError):
        build_row(_record(), {}, as_of=bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Quote
# ---------------------------------------------------------------------------


def test_quote_fields_become_cells_with_quote_provenance() -> None:
    row = build_row(_record(), {}, _quote(), as_of=DAY)

    assert row["price"].value == 262.82
    assert row["prev_close"].value == 260.50
    assert row["change"].value == 0.89
    assert row["volume"].value == 38_200_000.0
    for key in ("price", "prev_close", "change", "volume"):
        cell = row[key]
        assert isinstance(cell.value, float)
        assert cell.provenance is not None
        assert cell.provenance.source == "quote"
        assert cell.provenance.as_of == QUOTE_DAY
        assert cell.provenance.status is CellStatus.OK


def test_quote_units_follow_the_catalog() -> None:
    row = build_row(_record(), {}, _quote(), as_of=DAY)

    assert row["price"].unit is Unit.USD
    assert row["prev_close"].unit is Unit.USD
    assert row["change"].unit is Unit.PERCENT
    assert row["volume"].unit is Unit.SHARES


def test_quote_without_as_of_falls_back_to_the_build_date() -> None:
    row = build_row(
        _record(), {}, {"price": 10.0}, as_of=DAY
    )

    assert row["price"].provenance is not None
    assert row["price"].provenance.as_of == DAY


def test_quote_as_of_must_be_text_to_be_used() -> None:
    row = build_row(
        _record(), {}, {"price": 10.0, "as_of": 20261023}, as_of=DAY
    )

    assert row["price"].provenance is not None
    assert row["price"].provenance.as_of == DAY


def test_quote_rejects_a_symbol_the_row_is_not() -> None:
    with pytest.raises(RowBuildError) as caught:
        build_row(_record(), {}, {"symbol": "MSFT", "price": 10.0}, as_of=DAY)

    assert caught.value.code == "unknown_symbol"
    assert "MSFT" in caught.value.message
    assert "AAPL" in caught.value.message


def test_quote_accepts_the_same_symbol_in_another_spelling() -> None:
    row = build_row(
        _record(symbol="BRK-B"), {}, {"symbol": "brk.b", "price": 470.0}, as_of=DAY
    )

    assert row["price"].value == 470.0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("price", 0.0),
        ("price", -1.0),
        ("price", math.nan),
        ("price", math.inf),
        ("price", "262.82"),
        ("price", True),
        ("price", None),
        ("prev_close", 0.0),
        ("prev_close", -3.0),
        ("market_cap", 0.0),
        ("market_cap", math.nan),
        ("volume", -1.0),
        ("volume", math.inf),
    ],
)
def test_unusable_quote_numbers_are_omitted(field: str, value: object) -> None:
    row = build_row(_record(), {}, {field: value}, as_of=DAY)

    assert field not in row


def test_signed_quote_fields_keep_real_zero_and_negative_values() -> None:
    row = build_row(
        _record(),
        {},
        {"change": -3.5, "volume": 0},
        as_of=DAY,
    )

    assert row["change"].value == -3.5
    assert row["change"].unit is Unit.PERCENT
    assert row["volume"].value == 0.0

    flat = build_row(_record(), {}, {"change": 0.0}, as_of=DAY)
    assert flat["change"].value == 0.0


def test_decimal_quote_values_are_narrowed_to_float() -> None:
    row = build_row(_record(), {}, {"price": Decimal("12.50")}, as_of=DAY)

    assert row["price"].value == 12.5
    assert isinstance(row["price"].value, float)


def test_no_quote_means_no_price_and_no_zero() -> None:
    row = build_row(_record(), _facts(), as_of=DAY)

    assert "price" not in row
    assert "prev_close" not in row
    assert all(cell.value != 0.0 for cell in row.values())


def test_quote_keys_outside_the_vocabulary_are_ignored() -> None:
    row = build_row(
        _record(),
        {},
        {"price": 10.0, "open": 9.5, "beta": 1.2, "ticker": "AAPL"},
        as_of=DAY,
    )

    assert "open" not in row
    assert "beta" not in row


def test_quote_field_with_no_value_is_not_a_cell() -> None:
    row = build_row(_record(), {}, {"price": 10.0}, as_of=DAY)

    assert set(row) == {
        "ticker",
        "company",
        "sector",
        "industry",
        "country",
        "exchange",
        "price",
    }


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------


def test_fact_cells_are_copied_with_their_own_provenance() -> None:
    facts = _facts()
    row = build_row(_record(), facts, as_of=DAY)

    assert row["sales"] is facts["revenue"]
    assert row["sales"].value == 391_035_000_000.0
    assert row["sales"].unit is Unit.USD
    assert row["sales"].provenance is not None
    assert row["sales"].provenance.source == "sec"
    assert row["sales"].provenance.as_of == PERIOD_END
    assert row["sales"].provenance.inputs == (_REVENUE_TAG, "0000320193-25-000079")

    assert row["net_income"] is facts["net_income"]
    assert row["shares_outstanding"] is facts["shares_outstanding"]


def test_fact_mapping_is_documented_and_injective() -> None:
    assert dict(FACT_TO_METRIC) == {
        "revenue": "sales",
        "net_income": "net_income",
        "eps_diluted": "eps",
        "shares_outstanding": "shares_outstanding",
    }
    assert len(set(FACT_TO_METRIC.values())) == len(FACT_TO_METRIC)
    for fact_key, target in FACT_TO_METRIC.items():
        assert fact_key in DEFAULT_TAGS
        assert target in CATALOG_BY_KEY
    # A fact target is written straight into the row, so it must not collide
    # with a field another layer already owns.
    assert set(FACT_TO_METRIC.values()).isdisjoint(IDENTITY_FIELDS)
    assert set(FACT_TO_METRIC.values()).isdisjoint(QUOTE_FIELDS)


def test_per_share_facts_keep_the_unknown_unit_bridge() -> None:
    row = build_row(_record(), _facts(), as_of=DAY)

    # ``facts`` reports USD/shares as Unit.UNKNOWN because the engine has no
    # per-share unit; the assembler copies that verdict instead of relabelling
    # a per-share amount as USD.
    assert row["eps"].value == 6.16
    assert row["eps"].unit is Unit.UNKNOWN


def test_facts_without_a_catalog_target_are_not_columns() -> None:
    facts = {
        "assets": _fact(100.0),
        "liabilities": _fact(40.0),
        "equity": _fact(60.0),
        "cash_and_equivalents": _fact(20.0),
        "total_debt": _fact(30.0),
    }
    row = build_row(_record(), facts, as_of=DAY)

    assert set(row) == {
        "ticker",
        "company",
        "sector",
        "industry",
        "country",
        "exchange",
    }


def test_a_fact_under_a_catalog_key_without_a_mapping_is_ignored() -> None:
    facts = {"price": _fact(10.0), "pe": _fact(18.0), "volume": _fact(500.0)}
    row = build_row(_record(), facts, as_of=DAY)

    assert "price" not in row
    assert "pe" not in row
    assert "volume" not in row


def test_null_fact_cells_are_skipped_not_published() -> None:
    facts = {"revenue": _null_cell()}
    row = build_row(_record(), facts, as_of=DAY)

    assert "sales" not in row


@pytest.mark.parametrize(
    ("fact_key", "unit"),
    [
        ("revenue", Unit.SHARES),
        ("net_income", Unit.PERCENT),
        ("shares_outstanding", Unit.USD),
        ("eps_diluted", Unit.SHARES),
        ("operating_cash_flow", Unit.SHARES),
        ("capex", Unit.SHARES),
    ],
)
def test_a_fact_in_the_wrong_unit_raises_unit_mismatch(
    fact_key: str, unit: Unit
) -> None:
    facts = {fact_key: _fact(100.0, unit=unit)}

    with pytest.raises(RowBuildError) as caught:
        build_row(_record(), facts, as_of=DAY)

    assert caught.value.code == "unit_mismatch"


def test_unit_mismatch_message_names_the_fact_and_the_target() -> None:
    facts = {"revenue": _fact(100.0, unit=Unit.SHARES)}

    with pytest.raises(RowBuildError) as caught:
        build_row(_record(), facts, as_of=DAY)

    message = caught.value.message
    assert "revenue" in message
    assert "sales" in message
    assert "shares" in message
    assert "USD" in message


def test_a_null_cell_with_the_wrong_unit_is_not_a_mismatch() -> None:
    facts = {"revenue": _null_cell(unit=Unit.SHARES)}
    row = build_row(_record(), facts, as_of=DAY)

    assert "sales" not in row


def test_a_non_cell_fact_value_is_a_type_error() -> None:
    with pytest.raises(TypeError):
        build_row(
            _record(),
            {"revenue": 391_035_000_000.0},  # type: ignore[dict-item]
            as_of=DAY,
        )


def test_wrong_argument_types_are_rejected() -> None:
    with pytest.raises(TypeError):
        build_row("AAPL", {}, as_of=DAY)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        build_row(_record(), [], as_of=DAY)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        build_row(_record(), {}, [], as_of=DAY)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, "391035000000", True])
def test_a_fact_that_is_not_a_finite_number_is_a_type_error(value: object) -> None:
    facts = {"revenue": _fact(1.0)}
    facts["revenue"] = Cell(
        value=value,
        unit=Unit.USD,
        provenance=Provenance(source="sec", as_of=PERIOD_END),
    )

    with pytest.raises(TypeError):
        build_row(_record(), facts, as_of=DAY)


def test_a_negative_fact_is_kept_as_reported() -> None:
    row = build_row(
        _record(), {"net_income": _fact(-5_000_000.0)}, as_of=DAY
    )

    assert row["net_income"].value == -5_000_000.0


# ---------------------------------------------------------------------------
# Derived metrics
# ---------------------------------------------------------------------------


def test_market_cap_is_derived_from_price_and_shares() -> None:
    row = build_row(_record(), _facts(), _quote(), as_of=DAY)

    cell = row["market_cap"]
    assert cell.value == pytest.approx(262.82 * 14_840_000_000.0)
    assert cell.unit is Unit.USD
    assert cell.provenance is not None
    assert cell.provenance.source == "derived"
    assert cell.provenance.status is CellStatus.DERIVED
    assert cell.provenance.inputs == ("price", "shares")
    assert cell.provenance.as_of == QUOTE_DAY


def test_market_cap_needs_both_inputs() -> None:
    without_shares = build_row(
        _record(),
        {"revenue": _fact(100.0)},
        {"price": 10.0, "as_of": QUOTE_DAY},
        as_of=DAY,
    )
    assert "market_cap" not in without_shares

    without_price = build_row(_record(), _facts(), as_of=DAY)
    assert "market_cap" not in without_price


def test_observed_market_cap_is_never_overwritten() -> None:
    row = build_row(
        _record(),
        _facts(),
        {**_quote(), "market_cap": 1.0},
        as_of=DAY,
    )

    cell = row["market_cap"]
    assert cell.value == 1.0
    assert cell.provenance is not None
    assert cell.provenance.source == "quote"
    assert cell.provenance.status is CellStatus.OK


def test_pe_is_derived_from_price_and_eps() -> None:
    row = build_row(_record(), _facts(), _quote(), as_of=DAY)

    cell = row["pe"]
    assert cell.value == pytest.approx(262.82 / 6.16)
    assert cell.unit is Unit.RATIO
    assert cell.provenance is not None
    assert cell.provenance.status is CellStatus.DERIVED
    assert cell.provenance.inputs == ("price", "eps_ttm")
    assert cell.provenance.as_of == QUOTE_DAY


@pytest.mark.parametrize("eps", [0.0, -1.5])
def test_pe_is_absent_for_a_non_positive_eps(eps: float) -> None:
    row = build_row(
        _record(),
        {"eps_diluted": _fact(eps, unit=Unit.UNKNOWN)},
        {"price": 10.0, "as_of": QUOTE_DAY},
        as_of=DAY,
    )

    assert row["eps"].value == eps
    assert "pe" not in row


def test_forward_pe_needs_a_forward_estimate() -> None:
    row = build_row(_record(), _facts(), _quote(), as_of=DAY)

    cell = row["forward_pe"]
    assert cell.value == pytest.approx(262.82 / 8.35)
    assert cell.unit is Unit.RATIO
    assert cell.provenance is not None
    assert cell.provenance.inputs == ("price", "eps_forward")

    # Trailing EPS must never stand in for a forward estimate.
    without = build_row(
        _record(),
        _facts(),
        {"price": 262.82, "as_of": QUOTE_DAY},
        as_of=DAY,
    )
    assert "eps" in without
    assert "forward_pe" not in without


def test_peg_needs_a_growth_rate() -> None:
    row = build_row(_record(), _facts(), _quote(), as_of=DAY)

    cell = row["peg"]
    assert cell.value == pytest.approx((262.82 / 8.35) / 9.5)
    assert cell.unit is Unit.RATIO
    assert cell.provenance is not None
    assert cell.provenance.inputs == ("forward_pe", "growth_pct")

    without_growth = build_row(
        _record(),
        _facts(),
        {**_quote(), "growth": None},
        as_of=DAY,
    )
    assert "forward_pe" in without_growth
    assert "peg" not in without_growth

    zero_growth = build_row(
        _record(), _facts(), {**_quote(), "growth": 0.0}, as_of=DAY
    )
    assert "peg" not in zero_growth


def test_pb_and_ps_need_their_per_share_inputs() -> None:
    row = build_row(_record(), _facts(), _quote(), as_of=DAY)

    assert row["pb"].value == pytest.approx(262.82 / 4.25)
    assert row["ps"].value == pytest.approx(262.82 / 26.35)
    assert row["pb"].unit is Unit.RATIO

    without = build_row(
        _record(),
        _facts(),
        {"price": 262.82, "as_of": QUOTE_DAY},
        as_of=DAY,
    )
    assert "pb" not in without
    assert "ps" not in without


def test_fcf_is_derived_from_the_cash_flow_facts() -> None:
    row = build_row(_record(), _facts(), _quote(), as_of=DAY)

    cell = row["fcf"]
    assert cell.value == pytest.approx(111_482_000_000.0 - 12_715_000_000.0)
    assert cell.unit is Unit.USD
    assert cell.provenance is not None
    assert cell.provenance.status is CellStatus.DERIVED
    assert cell.provenance.inputs == ("operating_cash_flow", "capex")
    # Dated by the cash-flow fact, not by the quote.
    assert cell.provenance.as_of == PERIOD_END


def test_fcf_needs_both_cash_flow_facts() -> None:
    row = build_row(
        _record(),
        {"operating_cash_flow": _fact(100.0)},
        _quote(),
        as_of=DAY,
    )
    assert "fcf" not in row


def test_earnings_yield_follows_the_pe() -> None:
    row = build_row(_record(), _facts(), _quote(), as_of=DAY)

    pe = 262.82 / 6.16
    cell = row["earnings_yield"]
    assert cell.value == pytest.approx((1.0 / pe) * 100.0)
    assert cell.unit is Unit.PERCENT
    assert cell.provenance is not None
    assert cell.provenance.inputs == ("pe",)
    assert cell.provenance.as_of == row["pe"].provenance.as_of

    without_pe = build_row(
        _record(),
        {"net_income": _fact(93_736_000_000.0, tag="NetIncomeLoss")},
        {"price": 262.82, "as_of": QUOTE_DAY},
        as_of=DAY,
    )
    assert "pe" not in without_pe
    assert "earnings_yield" not in without_pe


def test_derived_cells_carry_the_date_of_the_number_they_divided() -> None:
    row = build_row(_record(), _facts(), _quote(), as_of=DAY)

    for key in ("market_cap", "pe", "forward_pe", "peg", "pb", "ps"):
        assert row[key].provenance.as_of == QUOTE_DAY
    assert row["fcf"].provenance.as_of == PERIOD_END


def test_derived_cells_are_finite_and_never_null() -> None:
    row = build_row(_record(), _facts(), _quote(), as_of=DAY)

    assert set(_derived(row)) == {
        "market_cap",
        "pe",
        "forward_pe",
        "peg",
        "pb",
        "ps",
        "fcf",
        "earnings_yield",
    }
    for cell in _derived(row).values():
        assert isinstance(cell.value, float)
        assert math.isfinite(cell.value)
        assert cell.provenance.status is CellStatus.DERIVED


def test_a_reported_zero_share_count_produces_no_market_cap() -> None:
    row = build_row(
        _record(),
        {"shares_outstanding": _fact(0.0, unit=Unit.SHARES)},
        {"price": 10.0, "as_of": QUOTE_DAY},
        as_of=DAY,
    )

    assert row["shares_outstanding"].value == 0.0
    assert "market_cap" not in row


# ---------------------------------------------------------------------------
# Provenance, vocabulary and purity
# ---------------------------------------------------------------------------


def test_derived_cells_name_their_inputs_in_the_documented_vocabulary() -> None:
    row = build_row(_record(), _facts(), _quote(), as_of=DAY)

    names = {
        name
        for cell in _derived(row).values()
        for name in cell.provenance.inputs
    }
    assert names
    assert names <= set(INPUT_SOURCES)
    assert set(INPUT_SOURCES) == {
        "price",
        "shares",
        "eps_ttm",
        "eps_forward",
        "forward_pe",
        "growth_pct",
        "book_per_share",
        "sales_per_share",
        "operating_cash_flow",
        "capex",
        "pe",
    }


def test_row_sources_are_the_four_evidence_kinds() -> None:
    row = build_row(_record(), _facts(), _quote(), as_of=DAY)

    sources = {cell.provenance.source for cell in row.values()}
    assert sources == {"identity", "quote", "sec", "derived"}
    assert "finviz" not in sources


def test_only_the_documented_extension_keys_live_outside_the_catalog() -> None:
    row = build_row(_record(), _facts(), _quote(), as_of=DAY)

    assert ROW_EXTENSION_KEYS == frozenset({"fcf", "earnings_yield"})
    assert ROW_EXTENSION_KEYS.isdisjoint(CATALOG_BY_KEY)
    assert set(row) - CATALOG_BY_KEY.keys() == set(ROW_EXTENSION_KEYS)


def test_identity_and_quote_vocabularies_are_catalog_keys_in_the_right_unit() -> None:
    assert tuple(QUOTE_FIELDS) == (
        "price",
        "prev_close",
        "change",
        "volume",
        "market_cap",
    )
    for key in QUOTE_FIELDS:
        assert key in CATALOG_BY_KEY
    assert set(IDENTITY_FIELDS) <= set(CATALOG_BY_KEY)
    for key in IDENTITY_FIELDS:
        assert CATALOG_BY_KEY[key].unit is Unit.TEXT


def test_every_value_in_the_row_is_a_cell() -> None:
    row = build_row(_record(), _facts(), _quote(), as_of=DAY)

    assert isinstance(row, dict)
    assert all(isinstance(cell, Cell) for cell in row.values())
    assert all(cell.value is not None for cell in row.values())


def test_build_is_pure_and_repeatable() -> None:
    facts, quote = _facts(), _quote()

    first = build_row(_record(), facts, quote, as_of=DAY)
    second = build_row(_record(), facts, quote, as_of=DAY)

    assert first is not second
    assert first == second


def test_build_does_not_mutate_its_inputs() -> None:
    facts, quote = _facts(), _quote()
    facts_before = dict(facts)
    quote_before = dict(quote)

    build_row(_record(), facts, quote, as_of=DAY)

    assert facts == facts_before
    assert quote == quote_before


# ---------------------------------------------------------------------------
# The real Apple document
# ---------------------------------------------------------------------------


def test_real_companyfacts_flow_into_a_row_unchanged() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    facts = fact_summary(payload)
    row = build_row(
        _record(),
        facts,
        {"price": 262.82, "as_of": QUOTE_DAY},
        as_of=DAY,
    )

    # The trimmed Apple document carries revenue, net income and the two
    # cash-flow concepts, but neither a share count nor diluted EPS. The row
    # must reflect exactly that: nothing is invented to fill the gap.
    assert facts["revenue"] is row["sales"]
    assert facts["net_income"] is row["net_income"]
    for fact_key, target in FACT_TO_METRIC.items():
        if fact_key in facts:
            assert row[target] is facts[fact_key]
            assert row[target].provenance.as_of == facts[fact_key].provenance.as_of
            assert row[target].provenance.as_of != DAY
        else:
            assert target not in row

    assert row["fcf"].value == pytest.approx(
        facts["operating_cash_flow"].value - facts["capex"].value
    )
    assert row["fcf"].provenance.as_of == facts["operating_cash_flow"].provenance.as_of

    assert "market_cap" not in row  # no share count in this document
    assert "pe" not in row  # no diluted EPS in this document
    assert "forward_pe" not in row
    assert "earnings_yield" not in row
