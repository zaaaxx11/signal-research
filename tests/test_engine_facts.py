"""Contract for the SEC XBRL facts slice (``app.engine.facts``).

The slice answers one question: **what did the filing say, and how do we know?**
It is the layer between the raw ``companyfacts`` document and a :class:`Cell`,
so every rule here is about not inventing a number:

* a fact exists only when the document contains one — a tag with no usable
  number is **absent**, never ``0``;
* a value keeps the unit the filing reported (absolute USD, absolute shares);
  nothing is rescaled from "millions" and nothing is converted from EUR;
* the winner among several filings is decided by ``filed`` then ``period_end``,
  and the working row keeps the accession that produced it;
* the same fact filed twice is one observation, not two.

The fixture is the real trimmed ``companyfacts`` document for Apple (CIK
0000320193) that ``tests/test_sec_adapter.py`` also reads: real tags, real
accessions, real filing dates through the FY2026 Q3 10-Q. Expectations below
are read off that document, so a test failure means the parser disagrees with
EDGAR rather than with a hand-written stub.
"""

from __future__ import annotations

import json
import math
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from app.engine.facts import (
    DEFAULT_TAGS,
    FLOW_KEYS,
    FactObservation,
    fact_summary,
    fact_ttm_summary,
    latest_observation,
    observations_to_cells,
    parse_companyfacts,
)
from app.engine.model import Cell, CellStatus, Unit

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "sec_companyfacts_aapl.json"

#: The canonical metrics this slice promises. An engine key outside this tuple
#: has no us-gaap mapping, so no cell may appear for it.
#:
#: R4 appends the fifteen fundamentals keys below, in this exact order, after the
#: eleven keys the slice shipped with. The tuple is the oracle: it is written
#: down here rather than read from :data:`DEFAULT_TAGS`, so the map growing by a
#: key nobody asked for fails the contract instead of quietly extending it.
CANONICAL_KEYS = (
    # --- as shipped ---
    "revenue",
    "net_income",
    "eps_diluted",
    "assets",
    "liabilities",
    "equity",
    "cash_and_equivalents",
    "total_debt",
    "shares_outstanding",
    "operating_cash_flow",
    "capex",
    # --- R4 fundamentals depth ---
    "gross_profit",
    "operating_income",
    "cost_of_revenue",
    "eps_basic",
    "current_assets",
    "current_liabilities",
    "inventory",
    "receivables",
    "ppe",
    "long_term_debt",
    "retained_earnings",
    "dividends_paid",
    "buybacks",
    "depreciation_amortization",
    "interest_expense",
)

#: The R4 addition on its own: canonical key → us-gaap concepts, primary first.
#: Transcribed from the contract table, so a missing alias or a reordered pair is
#: a test failure rather than a comment nobody reads.
NEW_TAG_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gross_profit", ("GrossProfit",)),
    ("operating_income", ("OperatingIncomeLoss",)),
    ("cost_of_revenue", ("CostOfRevenue", "CostOfGoodsAndServicesSold")),
    ("eps_basic", ("EarningsPerShareBasic",)),
    ("current_assets", ("AssetsCurrent",)),
    ("current_liabilities", ("LiabilitiesCurrent",)),
    ("inventory", ("InventoryNet",)),
    ("receivables", ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent")),
    ("ppe", ("PropertyPlantAndEquipmentNet",)),
    ("long_term_debt", ("LongTermDebtNoncurrent",)),
    ("retained_earnings", ("RetainedEarningsAccumulatedDeficit",)),
    ("dividends_paid", ("PaymentsOfDividends", "PaymentsOfDividendsCommonStock")),
    ("buybacks", ("PaymentsForRepurchaseOfCommonStock",)),
    (
        "depreciation_amortization",
        ("DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet"),
    ),
    ("interest_expense", ("InterestExpense", "InterestExpenseNonoperating")),
)

#: R4 duration metrics: a TTM value is arithmetic, not the newest filing.
FLOW_ADDITIONS: frozenset[str] = frozenset(
    {
        "gross_profit",
        "operating_income",
        "cost_of_revenue",
        "eps_basic",
        "dividends_paid",
        "buybacks",
        "depreciation_amortization",
        "interest_expense",
    }
)

#: R4 instant metrics: a balance or a share count, never summed over a year.
INSTANT_ADDITIONS: frozenset[str] = frozenset(
    {
        "current_assets",
        "current_liabilities",
        "inventory",
        "receivables",
        "ppe",
        "long_term_debt",
        "retained_earnings",
    }
)

#: AAPL's latest filed 10-Q (FY2026 Q3) — the accession most cells come from.
LATEST_ACCESSION = "0000320193-26-000020"
LATEST_FILED = "2026-07-31"
LATEST_PERIOD_END = "2026-06-27"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def facts() -> dict[str, Any]:
    """A fresh copy of the trimmed live ``companyfacts`` document."""

    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def row(
    end: str,
    value: Any,
    *,
    accn: str = "0000320193-26-000020",
    form: str = "10-Q",
    filed: str = "2026-07-31",
    fy: Any = 2026,
    fp: Any = "Q3",
    **extra: Any,
) -> dict[str, Any]:
    """One ``units`` row shaped like EDGAR's."""

    record: dict[str, Any] = {
        "start": "2025-09-28",
        "end": end,
        "val": value,
        "accn": accn,
        "fy": fy,
        "fp": fp,
        "form": form,
        "filed": filed,
    }
    record.update(extra)
    return record


def concept(rows: list[dict[str, Any]], *, unit: str = "USD") -> dict[str, Any]:
    return {"label": "Test concept", "units": {unit: rows}}


def document(concepts: dict[str, Any]) -> dict[str, Any]:
    """A ``companyfacts`` body; a bare list of rows is wrapped in the concept shape."""

    us_gaap = {
        tag: value if isinstance(value, dict) else concept(value)
        for tag, value in concepts.items()
    }
    return {
        "cik": 320193,
        "entityName": "Test Inc.",
        "facts": {"us-gaap": us_gaap},
    }


def one(concepts: dict[str, Any]) -> tuple[FactObservation, ...]:
    return parse_companyfacts(document(concepts))


# ---------------------------------------------------------------------------
# DEFAULT_TAGS
# ---------------------------------------------------------------------------


def test_default_tags_map_every_canonical_key_to_us_gaap_names() -> None:
    assert tuple(DEFAULT_TAGS) == CANONICAL_KEYS, (
        "the tag map is the engine's vocabulary; a missing key means the metric "
        f"can never be filled from SEC: {sorted(DEFAULT_TAGS)}"
    )
    for key, tags in DEFAULT_TAGS.items():
        assert isinstance(tags, tuple) and tags, (key, tags)
        assert all(isinstance(tag, str) and tag.strip() == tag and tag for tag in tags), (
            key,
            tags,
        )
        assert len(set(tags)) == len(tags), (key, tags)


def test_default_tags_prefer_the_primary_concept_first() -> None:
    """Order is the documented tie-break between two present concepts."""

    assert DEFAULT_TAGS["revenue"][0] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert DEFAULT_TAGS["net_income"][0] == "NetIncomeLoss"
    assert DEFAULT_TAGS["equity"][0] == "StockholdersEquity"
    assert DEFAULT_TAGS["operating_cash_flow"][0] == (
        "NetCashProvidedByUsedInOperatingActivities"
    )
    assert DEFAULT_TAGS["capex"][0] == "PaymentsToAcquirePropertyPlantAndEquipment"


# ---------------------------------------------------------------------------
# parse_companyfacts — the real document
# ---------------------------------------------------------------------------


def test_parse_reads_the_apple_fixture() -> None:
    observations = parse_companyfacts(facts())

    assert observations, "the real companyfacts document produced no observation"
    assert all(isinstance(item, FactObservation) for item in observations)
    assert {item.unit for item in observations} == {"USD"}, (
        "the fixture is quoted in absolute USD; anything else means the unit "
        f"branch was misread: {sorted({item.unit for item in observations})}"
    )
    assert all(isinstance(item.value, float) for item in observations)
    assert all(math.isfinite(item.value) for item in observations)
    assert all(item.tag and item.period_end for item in observations)


def test_parse_keeps_the_filing_metadata_of_each_row() -> None:
    observations = parse_companyfacts(facts())
    revenue = [item for item in observations if item.tag ==
               "RevenueFromContractWithCustomerExcludingAssessedTax"]
    latest = max(revenue, key=lambda item: (item.filed, item.period_end))

    assert latest.value == pytest.approx(364_357_000_000)
    assert latest.period_end == LATEST_PERIOD_END
    assert latest.filed == LATEST_FILED
    assert latest.form == "10-Q"
    assert latest.accession == LATEST_ACCESSION
    assert latest.fiscal_year == 2026
    assert latest.fiscal_period == "Q3"


def test_parse_returns_a_deterministically_sorted_tuple() -> None:
    first = parse_companyfacts(facts())
    second = parse_companyfacts(facts())

    assert first == second, "two parses of one document disagreed"
    assert list(first) == sorted(
        first,
        key=lambda item: (
            item.tag,
            item.unit,
            item.start or "",
            item.period_end,
            item.accession,
            item.filed,
            item.value,
        ),
    )


def test_parse_is_independent_of_the_document_order() -> None:
    forward = one(
        {
            "Revenues": [row("2024-12-31", 1.0, accn="a")],
            "NetIncomeLoss": [row("2024-12-31", 2.0, accn="a")],
        }
    )
    backward = one(
        {
            "NetIncomeLoss": [row("2024-12-31", 2.0, accn="a")],
            "Revenues": [row("2024-12-31", 1.0, accn="a")],
        }
    )

    assert forward == backward


def test_parse_collapses_the_same_fact_filed_twice() -> None:
    observations = one(
        {
            "Revenues": [
                row("2024-12-31", 100.0, accn="a"),
                row("2024-12-31", 100.0, accn="a"),
                row("2024-12-31", 100.0, accn="b"),
            ]
        }
    )

    assert len(observations) == 2, "one fact filed under two accessions is two facts"


def test_parse_collapses_a_repeated_identity_even_when_the_value_differs() -> None:
    """The identity is (tag, unit, period_end, accession); first row wins."""

    observations = one(
        {
            "Revenues": [
                row("2024-12-31", 100.0, accn="a"),
                row("2024-12-31", 999.0, accn="a"),
            ]
        }
    )

    assert len(observations) == 1
    assert observations[0].value == pytest.approx(100.0), (
        "the parser must not silently pick the larger of two rows it cannot tell "
        "apart"
    )


# ---------------------------------------------------------------------------
# parse_companyfacts — tolerance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "not a document",
        42,
        {},
        {"facts": None},
        {"facts": []},
        {"facts": {}},
        {"facts": {"us-gaap": None}},
        {"facts": {"us-gaap": []}},
        {"facts": {"dei": {"EntityCommonStockSharesOutstanding": concept([row("2024-12-31", 1)])}}},
        {"facts": {"us-gaap": {"Revenues": 3}}},
        {"facts": {"us-gaap": {"Revenues": {"units": "USD"}}}},
        {"facts": {"us-gaap": {"Revenues": {"units": {"USD": "nope"}}}}},
        {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [None, 3, "x"]}}}}},
    ],
)
def test_parse_of_a_document_it_does_not_recognise_is_empty(payload: Any) -> None:
    assert parse_companyfacts(payload) == ()


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        None,
        "",
        "   ",
        "not a number",
        True,
        False,
        [],
        {},
    ],
)
def test_parse_skips_a_value_that_is_not_a_finite_number(value: Any) -> None:
    assert one({"Revenues": [row("2024-12-31", value)]}) == (), (
        f"{value!r} became a fact; a blank or non-finite cell is missing, not zero"
    )


def test_parse_keeps_the_usable_rows_around_a_bad_one() -> None:
    observations = one(
        {
            "Revenues": [
                row("2024-12-31", None, accn="bad"),
                row("2025-03-31", 10.0, accn="good"),
                row("2025-06-30", float("nan"), accn="bad"),
            ]
        }
    )

    assert [item.value for item in observations] == [pytest.approx(10.0)]


def test_parse_reads_a_numeric_string_but_not_a_word() -> None:
    observations = one(
        {
            "Revenues": [
                row("2024-12-31", "1234.5", accn="a"),
                row("2025-03-31", "twelve", accn="b"),
            ]
        }
    )

    assert len(observations) == 1
    assert observations[0].value == pytest.approx(1234.5)


def test_parse_skips_a_row_without_a_period_end() -> None:
    observations = one(
        {
            "Revenues": [
                row("", 1.0, accn="a"),
                row("2025-03-31", 2.0, accn="b"),
                {"val": 3.0, "accn": "c"},
            ]
        }
    )

    assert [item.period_end for item in observations] == ["2025-03-31"], (
        "a fact with no period cannot be dated, and an undated cell cannot carry "
        "provenance"
    )


def test_parse_accepts_a_shares_unit_and_keeps_it() -> None:
    observations = one(
        {
            "CommonStockSharesOutstanding": concept(
                [row("2024-12-31", 15_000_000_000, accn="a")], unit="shares"
            )
        }
    )

    assert len(observations) == 1
    assert observations[0].unit == "shares"


def test_parse_ignores_a_unit_the_engine_cannot_scale() -> None:
    payload = document(
        {
            "Revenues": {
                "units": {
                    "EUR": [row("2024-12-31", 1.0, accn="a")],
                    "pure": [row("2024-12-31", 2.0, accn="b")],
                }
            }
        }
    )

    assert parse_companyfacts(payload) == (), (
        "an EUR amount is not a USD amount; importing it without a rate would "
        "make the cell lie about its unit"
    )


def test_parse_tolerates_blank_optional_metadata() -> None:
    observations = one(
        {
            "Revenues": [
                {
                    "end": "2024-12-31",
                    "val": 1.0,
                    "fy": "not a year",
                    "fp": " ",
                    "form": "",
                    "filed": "",
                    "accn": "",
                    "frame": " ",
                }
            ]
        }
    )

    assert len(observations) == 1
    observation = observations[0]
    assert observation.fiscal_year is None
    assert observation.fiscal_period is None
    assert observation.form == ""
    assert observation.filed == ""
    assert observation.accession == ""
    assert observation.frame is None


def test_parse_keeps_the_frame_when_the_filing_has_one() -> None:
    observations = one({"Revenues": [row("2024-12-31", 1.0, frame="CY2024Q4")]})

    assert observations[0].frame == "CY2024Q4"


def test_parse_honours_a_custom_tag_map() -> None:
    payload = document(
        {
            "Revenues": [row("2024-12-31", 1.0, accn="a")],
            "NetIncomeLoss": [row("2024-12-31", 2.0, accn="a")],
        }
    )

    observations = parse_companyfacts(payload, tags={"revenue": ("Revenues",)})

    assert [item.tag for item in observations] == ["Revenues"]
    assert parse_companyfacts(payload, tags={}) == ()


def test_parse_joins_several_tags_for_one_canonical_key() -> None:
    payload = document(
        {
            "Revenues": [row("2024-12-31", 1.0, accn="a")],
            "SalesRevenueNet": [row("2025-03-31", 2.0, accn="b")],
        }
    )

    observations = parse_companyfacts(
        payload, tags={"revenue": ("Revenues", "SalesRevenueNet")}
    )

    assert sorted(item.tag for item in observations) == ["Revenues", "SalesRevenueNet"]


def test_fact_observation_is_a_frozen_value_object() -> None:
    observation = FactObservation(
        tag="Revenues",
        value=1.0,
        unit="USD",
        period_end="2024-12-31",
        fiscal_year=2024,
        fiscal_period="FY",
        form="10-K",
        filed="2025-02-01",
        accession="a",
        frame=None,
    )

    with pytest.raises(FrozenInstanceError):
        observation.value = 2.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# latest_observation
# ---------------------------------------------------------------------------


def test_latest_observation_is_the_newest_filing_of_the_apple_fixture() -> None:
    latest = latest_observation(parse_companyfacts(facts()), "revenue")

    assert latest is not None
    assert latest.value == pytest.approx(364_357_000_000)
    assert latest.period_end == LATEST_PERIOD_END
    assert latest.accession == LATEST_ACCESSION


def test_latest_observation_filters_by_form() -> None:
    observations = parse_companyfacts(facts())

    annual = latest_observation(observations, "revenue", forms=("10-K",))

    assert annual is not None
    assert annual.form == "10-K"
    assert annual.value == pytest.approx(416_161_000_000)
    assert annual.period_end == "2025-09-27"
    assert annual.filed == "2025-10-31"


def test_latest_observation_breaks_a_filed_tie_with_the_period_end() -> None:
    observations = one(
        {
            "Revenues": [
                row("2025-03-31", 10.0, accn="a", filed="2025-05-01"),
                row("2025-06-30", 20.0, accn="b", filed="2025-05-01"),
            ]
        }
    )

    latest = latest_observation(observations, "revenue")

    assert latest is not None and latest.value == pytest.approx(20.0)


def test_latest_observation_lets_a_later_restatement_win() -> None:
    observations = one(
        {
            "Revenues": [
                row("2025-06-30", 20.0, accn="original", filed="2025-07-31"),
                row("2025-06-30", 21.0, accn="restated", filed="2025-09-30"),
            ]
        }
    )

    latest = latest_observation(observations, "revenue")

    assert latest is not None and latest.value == pytest.approx(21.0)


def test_latest_observation_prefers_the_newest_filing_across_tags() -> None:
    """Recency is primary: a retired primary tag must not beat a current alias."""

    observations = one(
        {
            "Revenues": [row("2026-03-31", 999.0, accn="alias", filed="2026-05-01")],
            "RevenueFromContractWithCustomerExcludingAssessedTax": [
                row("2025-03-31", 111.0, accn="primary", filed="2025-05-01")
            ],
        }
    )

    latest = latest_observation(observations, "revenue")

    assert latest is not None
    assert latest.tag == "Revenues"
    assert latest.value == pytest.approx(999.0), (
        "the alias was filed a year later; tag order cannot revive the retired primary"
    )


def test_latest_observation_uses_tag_order_as_a_tiebreaker() -> None:
    """When recency is identical, the documented primary tag still wins."""

    observations = one(
        {
            "Revenues": [
                row("2026-03-31", 111.0, accn="same", filed="2026-05-01")
            ],
            "RevenueFromContractWithCustomerExcludingAssessedTax": [
                row("2026-03-31", 111.0, accn="same", filed="2026-05-01")
            ],
        }
    )

    latest = latest_observation(observations, "revenue")

    assert latest is not None
    assert latest.tag == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert latest.value == pytest.approx(111.0)


def test_latest_observation_falls_through_to_the_second_tag() -> None:
    observations = one({"Revenues": [row("2025-03-31", 111.0, accn="alias")]})

    latest = latest_observation(observations, "revenue")

    assert latest is not None
    assert latest.tag == "Revenues"


def test_latest_observation_without_evidence_is_none_not_zero() -> None:
    observations = parse_companyfacts(facts())

    assert latest_observation((), "revenue") is None
    assert latest_observation(observations, "assets") is None, (
        "Apple's trim has no Assets tag; a zero would be an invented balance sheet"
    )
    assert latest_observation(observations, "not_a_metric") is None
    assert latest_observation(observations, "") is None
    assert latest_observation(observations, "revenue", forms=("20-F",)) is None


def test_latest_observation_accepts_a_single_form_string() -> None:
    observations = parse_companyfacts(facts())

    annual = latest_observation(observations, "revenue", forms="10-K")

    assert annual is not None and annual.form == "10-K"


def test_latest_observation_matches_forms_case_insensitively() -> None:
    observations = one({"Revenues": [row("2025-03-31", 5.0, accn="a", form="10-k")]})

    latest = latest_observation(observations, "revenue", forms=("10-K",))

    assert latest is not None and latest.value == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# observations_to_cells
# ---------------------------------------------------------------------------


def test_cells_cover_the_apple_balance_sheet_and_cash_flow() -> None:
    """The real document's canonical cells, unchanged key set.

    R4 adds three cells to Apple because the trimmed document already carries
    ``OperatingIncomeLoss``, ``LongTermDebtNoncurrent`` and
    ``DepreciationDepletionAndAmortization``; a concept the map now names is a
    cell, so the oracle lists them instead of relaxing to a subset.
    """

    cells = observations_to_cells(parse_companyfacts(facts()))

    assert set(cells) == {
        "revenue",
        "net_income",
        "liabilities",
        "equity",
        "cash_and_equivalents",
        "total_debt",
        "operating_cash_flow",
        "capex",
        "operating_income",
        "long_term_debt",
        "depreciation_amortization",
    }, f"unexpected cell set: {sorted(cells)}"

    expected = {
        "revenue": 364_357_000_000,
        "net_income": 101_464_000_000,
        "liabilities": 275_746_000_000,
        "equity": 107_520_000_000,
        "cash_and_equivalents": 39_544_000_000,
        "total_debt": 82_300_000_000,
        "operating_cash_flow": 116_996_000_000,
        "capex": 6_799_000_000,
        "operating_income": 122_432_000_000,
        "long_term_debt": 71_340_000_000,
        "depreciation_amortization": 9_973_000_000,
    }
    for key, value in expected.items():
        cell = cells[key]
        assert isinstance(cell, Cell)
        assert cell.value == pytest.approx(value), (key, cell.value)
        assert cell.unit is Unit.USD, (key, cell.unit)
        assert cell.provenance is not None
        assert cell.provenance.status is CellStatus.OK
        assert cell.provenance.source == "sec"
        assert cell.provenance.as_of == LATEST_PERIOD_END, (
            f"{key}: as_of is the period the number describes, not the parse date"
        )
        assert cell.provenance.url is None, "the fact does not carry a document URL"
        assert len(cell.provenance.inputs) == 2


def test_a_cell_keeps_the_tag_and_accession_that_produced_it() -> None:
    cells = observations_to_cells(parse_companyfacts(facts()))
    revenue = cells["revenue"]

    assert revenue.provenance is not None
    assert revenue.provenance.inputs == (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        LATEST_ACCESSION,
    )
    assert cells["total_debt"].provenance is not None
    assert cells["total_debt"].provenance.inputs == ("LongTermDebt", LATEST_ACCESSION)


def test_a_missing_fact_is_omitted_and_never_zero() -> None:
    cells = observations_to_cells(parse_companyfacts(facts()))

    for key in ("assets", "eps_diluted", "shares_outstanding"):
        assert key not in cells, (
            f"{key} is not in the document, so it must not appear as a cell: "
            f"{cells.get(key)!r}"
        )
    assert all(cell.value != 0 for cell in cells.values()), (
        "no cell may carry a zero placeholder"
    )


def test_cells_are_empty_for_an_empty_parse() -> None:
    assert observations_to_cells(()) == {}
    assert observations_to_cells([]) == {}


def test_cells_omit_a_tag_the_document_leaves_empty() -> None:
    payload = document({"Assets": {"units": {"USD": []}}})

    assert fact_summary(payload) == {}


def test_cells_omit_a_tag_that_is_not_canonical() -> None:
    """A concept outside the map is evidence for nothing; it must not leak.

    R4 gave ``OperatingIncomeLoss`` a key of its own, so the example moved to a
    concept the map still does not name — the rule under test is unchanged.
    """

    payload = document(
        {
            "ResearchAndDevelopmentExpense": [row("2024-12-31", 5.0, accn="a")],
            "Revenues": [row("2024-12-31", 7.0, accn="a")],
        }
    )

    summary = fact_summary(payload)

    assert set(summary) == {"revenue"}, (
        "research and development has no canonical engine key in this slice; it "
        f"must not leak into the summary: {sorted(summary)}"
    )


def test_cells_use_the_shares_unit_for_a_share_count() -> None:
    payload = document(
        {
            "CommonStockSharesOutstanding": concept(
                [row("2024-12-31", 15_000_000_000, accn="a")], unit="shares"
            )
        }
    )

    summary = fact_summary(payload)

    assert summary["shares_outstanding"].unit is Unit.SHARES
    assert summary["shares_outstanding"].value == pytest.approx(15_000_000_000)


def test_a_per_share_amount_is_kept_but_not_called_usd() -> None:
    payload = document(
        {
            "EarningsPerShareDiluted": concept(
                [row("2024-12-31", 6.13, accn="a")], unit="USD/shares"
            )
        }
    )

    summary = fact_summary(payload)

    assert summary["eps_diluted"].value == pytest.approx(6.13)
    assert summary["eps_diluted"].unit is Unit.UNKNOWN, (
        "dollars per share is not a dollar amount; the engine has no per-share "
        "unit, and Unit.USD here would be a lie"
    )


def test_a_dollar_value_is_not_rescaled_to_millions() -> None:
    payload = document({"Revenues": [row("2024-12-31", 1_000, accn="a")]})

    assert fact_summary(payload)["revenue"].value == pytest.approx(1_000.0), (
        "companyfacts quotes absolute USD; a presentation that prints millions "
        "is the presentation's problem"
    )


def test_cells_skip_an_observation_without_a_date() -> None:
    observation = FactObservation(
        tag="Revenues",
        value=1.0,
        unit="USD",
        period_end="",
        fiscal_year=None,
        fiscal_period=None,
        form="",
        filed="",
        accession="a",
        frame=None,
    )

    assert observations_to_cells((observation,)) == {}, (
        "an undated value cannot carry provenance; it must not raise either"
    )


def test_cells_honour_a_custom_tag_map_and_form_filter() -> None:
    observations = parse_companyfacts(facts())

    only_annual = observations_to_cells(observations, forms=("10-K",))
    assert only_annual["revenue"].value == pytest.approx(416_161_000_000)

    aliased = observations_to_cells(observations, tags={"revenue": ("Revenues",)})
    assert "revenue" not in aliased, (
        "the Apple document has no Revenues tag; a custom map that points at an "
        f"absent concept must stay empty: {aliased}"
    )


# ---------------------------------------------------------------------------
# fact_summary
# ---------------------------------------------------------------------------


def test_fact_summary_is_parse_then_cells() -> None:
    payload = facts()

    assert fact_summary(payload) == observations_to_cells(parse_companyfacts(payload))


def test_fact_summary_of_a_malformed_document_is_empty() -> None:
    assert fact_summary({"facts": "nope"}) == {}
    assert fact_summary(None) == {}


def test_fact_summary_reports_the_latest_period_not_the_first() -> None:
    summary = fact_summary(facts())

    assert summary["revenue"].provenance is not None
    assert summary["revenue"].provenance.as_of == LATEST_PERIOD_END
    assert summary["revenue"].value == pytest.approx(364_357_000_000)
    assert summary["revenue"].value != pytest.approx(210_328_000_000), (
        "the 2024 six-month figure is the first row in the document, not the "
        "latest fact"
    )


# ---------------------------------------------------------------------------
# R4 — the fifteen fundamentals keys
# ---------------------------------------------------------------------------

#: Which new canonical key is projected into which engine unit when the filing
#: reports it. The second alias of a multi-tag group is used on purpose: the
#: fallback concept must reach the same cell as the primary one.
R4_UNIT_CASES: tuple[tuple[str, str, str, Unit], ...] = (
    ("gross_profit", "GrossProfit", "USD", Unit.USD),
    ("operating_income", "OperatingIncomeLoss", "USD", Unit.USD),
    ("cost_of_revenue", "CostOfGoodsAndServicesSold", "USD", Unit.USD),
    ("current_assets", "AssetsCurrent", "USD", Unit.USD),
    ("current_liabilities", "LiabilitiesCurrent", "USD", Unit.USD),
    ("inventory", "InventoryNet", "USD", Unit.USD),
    ("receivables", "ReceivablesNetCurrent", "USD", Unit.USD),
    ("ppe", "PropertyPlantAndEquipmentNet", "USD", Unit.USD),
    ("long_term_debt", "LongTermDebtNoncurrent", "USD", Unit.USD),
    ("retained_earnings", "RetainedEarningsAccumulatedDeficit", "USD", Unit.USD),
    ("dividends_paid", "PaymentsOfDividendsCommonStock", "USD", Unit.USD),
    ("buybacks", "PaymentsForRepurchaseOfCommonStock", "USD", Unit.USD),
    (
        "depreciation_amortization",
        "DepreciationAmortizationAndAccretionNet",
        "USD",
        Unit.USD,
    ),
    ("interest_expense", "InterestExpenseNonoperating", "USD", Unit.USD),
    ("eps_basic", "EarningsPerShareBasic", "USD/shares", Unit.UNKNOWN),
)


def test_r4_appends_exactly_fifteen_keys_after_the_shipped_ones() -> None:
    """Growth is append-only: the shipped keys keep their order, then come the new ones."""

    shipped = (
        "revenue",
        "net_income",
        "eps_diluted",
        "assets",
        "liabilities",
        "equity",
        "cash_and_equivalents",
        "total_debt",
        "shares_outstanding",
        "operating_cash_flow",
        "capex",
    )
    added = tuple(key for key, _tags in NEW_TAG_ALIASES)

    assert len(NEW_TAG_ALIASES) == 15, (
        f"R4 adds fifteen fact keys; the oracle lists {len(NEW_TAG_ALIASES)}"
    )
    assert CANONICAL_KEYS == shipped + added
    assert tuple(DEFAULT_TAGS) == CANONICAL_KEYS, (
        "the tag map must be exactly the oracle, in order: a key appended out of "
        f"turn is a different vocabulary: {sorted(DEFAULT_TAGS)}"
    )


@pytest.mark.parametrize(
    ("key", "tags"),
    NEW_TAG_ALIASES,
    ids=[key for key, _tags in NEW_TAG_ALIASES],
)
def test_r4_key_maps_to_the_contract_concepts(key: str, tags: tuple[str, ...]) -> None:
    """Each new key answers to the contract's concepts, primary first, in order."""

    mapped = DEFAULT_TAGS.get(key)

    assert mapped == tags, (
        f"{key} maps to {mapped!r}; the contract says {tags!r} (primary concept "
        "first, aliases after it)"
    )


@pytest.mark.parametrize(
    ("key", "tag", "xbrl_unit", "engine_unit"),
    R4_UNIT_CASES,
    ids=[key for key, _tag, _unit, _engine_unit in R4_UNIT_CASES],
)
def test_r4_fact_is_projected_into_the_unit_the_filing_reported(
    key: str, tag: str, xbrl_unit: str, engine_unit: Unit
) -> None:
    """A parsed R4 concept becomes one cell, in the unit its rows were quoted in."""

    payload = document(
        {tag: concept([row("2025-09-27", 1_234.5, accn="a")], unit=xbrl_unit)}
    )

    summary = fact_summary(payload)

    assert key in summary, (
        f"{tag} is reported in {xbrl_unit} but produced no {key} cell: "
        f"the summary holds {sorted(summary)}"
    )
    assert summary[key].value == pytest.approx(1_234.5)
    assert summary[key].unit is engine_unit, (
        f"{key} came out as {summary[key].unit!r}; a per-share amount is not a "
        "dollar amount and Unit.USD here would be a lie"
    )


def test_a_share_count_keeps_the_shares_unit_next_to_the_r4_money_cells() -> None:
    """The R4 vocabulary does not reclassify the share count it sits beside."""

    payload = document(
        {
            "GrossProfit": [row("2025-09-27", 3_000.0, accn="a")],
            "CommonStockSharesOutstanding": concept(
                [row("2025-09-27", 15_000_000_000, accn="a")], unit="shares"
            ),
        }
    )

    summary = fact_summary(payload)

    assert set(summary) == {"gross_profit", "shares_outstanding"}, sorted(summary)
    assert summary["gross_profit"].unit is Unit.USD
    assert summary["shares_outstanding"].unit is Unit.SHARES


@pytest.mark.parametrize(
    "tag",
    [
        "GrossProfitAdjusted",
        "AdjustedOperatingIncome",
        "CoreEarningsPerShareBasic",
        "ResearchAndDevelopmentExpense",
        "AssetsCurrentNoncurrent",
    ],
)
def test_a_concept_outside_the_r4_map_is_not_a_canonical_key(tag: str) -> None:
    """An invented or adjacent concept is not a licence to invent an engine key."""

    summary = fact_summary(document({tag: [row("2025-09-27", 5.0, accn="a")]}))

    assert summary == {}, (
        f"{tag} has no canonical key, so it must produce no cell; the summary "
        f"holds {sorted(summary)}"
    )


@pytest.mark.parametrize("key", sorted(FLOW_ADDITIONS))
def test_r4_duration_key_is_a_flow(key: str) -> None:
    """A duration metric outside ``FLOW_KEYS`` would report a quarter as a year."""

    assert key in FLOW_KEYS, (
        f"{key} is a duration metric; without FLOW_KEYS membership "
        "fact_ttm_summary hands back the newest 10-Q figure as if it were a "
        "trailing twelve months"
    )


@pytest.mark.parametrize("key", sorted(INSTANT_ADDITIONS))
def test_r4_balance_key_is_an_instant(key: str) -> None:
    """A balance is a point, not a period: summing it over a year is nonsense."""

    assert key not in FLOW_KEYS, (
        f"{key} is an instant; treating it as a flow would add last year's "
        "balance sheet to this quarter's"
    )


def test_r4_keys_are_each_classified_exactly_once() -> None:
    """Flow and instant are a partition of the new vocabulary, not overlapping sets."""

    new_keys = {key for key, _tags in NEW_TAG_ALIASES}

    assert FLOW_ADDITIONS.isdisjoint(INSTANT_ADDITIONS)
    assert FLOW_ADDITIONS | INSTANT_ADDITIONS == new_keys, (
        "unclassified or invented keys: "
        f"{sorted((FLOW_ADDITIONS | INSTANT_ADDITIONS) ^ new_keys)}"
    )


# ---------------------------------------------------------------------------
# R4 — flow arithmetic vs instant snapshots
# ---------------------------------------------------------------------------


def gross_profit_ytd_payload() -> dict[str, Any]:
    """Three ``GrossProfit`` rows: FY2025 annual, FY2026 YTD, FY2025 YTD comparative.

    The values are round numbers chosen so the TTM arithmetic is checkable by
    hand: ``195.2 + 140.0 - 126.0 = 209.2`` (USD billions), and only the middle
    row ends on the current period end the cell must be dated to.
    """

    return document(
        {
            "GrossProfit": [
                row(
                    "2025-09-27",
                    195_200_000_000,
                    start="2024-09-29",
                    accn="0000320193-25-000079",
                    form="10-K",
                    filed="2025-10-31",
                    fy=2025,
                    fp="FY",
                ),
                row(
                    "2025-06-28",
                    126_000_000_000,
                    start="2024-09-29",
                    accn="0000320193-25-000073",
                    form="10-Q",
                    filed="2025-08-01",
                    fy=2025,
                    fp="Q3",
                ),
                row(
                    "2026-06-27",
                    140_000_000_000,
                    start="2025-09-28",
                    accn="0000320193-26-000020",
                    form="10-Q",
                    filed="2026-07-31",
                    fy=2026,
                    fp="Q3",
                ),
            ]
        }
    )


def test_a_flow_fact_becomes_annual_plus_ytd_minus_prior_ytd() -> None:
    """``gross_profit`` is a duration: its cell is three filings, not the newest one."""

    summary = fact_ttm_summary(gross_profit_ytd_payload())

    assert "gross_profit" in summary, (
        "gross profit is in the vocabulary, so a document that reports it must "
        f"produce a cell; the summary holds {sorted(summary)}"
    )
    cell = summary["gross_profit"]

    assert cell.value == pytest.approx(209_200_000_000), (
        "TTM is annual + current YTD - prior-year YTD; a bare YTD or a bare "
        f"annual is a different number: {cell.value!r}"
    )
    assert cell.unit is Unit.USD
    assert cell.provenance is not None
    assert cell.provenance.as_of == "2026-06-27", (
        "a TTM cell is dated to the period end it reaches, not to the annual "
        f"report buried inside it: {cell.provenance.as_of!r}"
    )
    assert len(cell.provenance.inputs) == 6, (
        "the three filings that built the number must all be replayable from "
        f"provenance: {cell.provenance.inputs!r}"
    )
    assert cell.provenance.inputs == (
        "GrossProfit",
        "0000320193-25-000079",
        "GrossProfit",
        "0000320193-26-000020",
        "GrossProfit",
        "0000320193-25-000073",
    )


def test_fact_summary_and_fact_ttm_summary_are_distinct_entry_points() -> None:
    """The same document answers "what did it file" and "what is the run-rate"."""

    payload = gross_profit_ytd_payload()

    point_summary = fact_summary(payload)
    run_rate_summary = fact_ttm_summary(payload)

    assert "gross_profit" in point_summary, (
        f"no gross_profit cell in fact_summary: {sorted(point_summary)}"
    )
    assert "gross_profit" in run_rate_summary, (
        f"no gross_profit cell in fact_ttm_summary: {sorted(run_rate_summary)}"
    )

    point = point_summary["gross_profit"]
    run_rate = run_rate_summary["gross_profit"]

    assert point.value == pytest.approx(140_000_000_000), (
        "fact_summary reports the newest filing's own figure, never arithmetic"
    )
    assert run_rate.value == pytest.approx(209_200_000_000)
    assert point.value != run_rate.value
    assert point.provenance is not None and run_rate.provenance is not None
    assert point.provenance.as_of == run_rate.provenance.as_of == "2026-06-27"


def test_a_balance_fact_uses_the_newest_observation_and_no_ttm_arithmetic() -> None:
    """``current_assets`` is an instant: the newer 10-Q replaces the 10-K."""

    payload = document(
        {
            "AssetsCurrent": [
                row(
                    "2025-09-27",
                    133_000_000_000,
                    start=None,
                    accn="0000320193-25-000079",
                    form="10-K",
                    filed="2025-10-31",
                ),
                row(
                    "2026-06-27",
                    118_000_000_000,
                    start=None,
                    accn="0000320193-26-000020",
                    form="10-Q",
                    filed="2026-07-31",
                ),
            ]
        }
    )

    point = fact_summary(payload)
    run_rate = fact_ttm_summary(payload)

    assert "current_assets" in point, f"no current_assets cell: {sorted(point)}"
    assert "current_assets" in run_rate, f"no current_assets cell: {sorted(run_rate)}"
    assert point["current_assets"].value == pytest.approx(118_000_000_000), (
        "the newest balance wins; an older 10-K balance is history"
    )
    assert run_rate["current_assets"].value == pytest.approx(118_000_000_000), (
        "a balance has no duration, so TTM is the same snapshot — not "
        f"{run_rate['current_assets'].value!r}"
    )
    assert point["current_assets"].provenance is not None
    assert point["current_assets"].provenance.as_of == "2026-06-27"
    assert point["current_assets"].provenance.inputs == (
        "AssetsCurrent",
        "0000320193-26-000020",
    )


# ---------------------------------------------------------------------------
# R4 — point-in-time restatement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "tag"), [("revenue", "Revenues"), ("gross_profit", "GrossProfit")]
)
def test_a_later_filed_restatement_replaces_the_earlier_value(key: str, tag: str) -> None:
    """Same tag, same period, two accessions: the later ``filed`` is the truth."""

    payload = document(
        {
            tag: [
                row("2025-06-30", 100.0, accn="0000320193-25-000073", filed="2025-08-01"),
                row("2025-06-30", 91.0, accn="0000320193-25-000091", filed="2025-11-15"),
            ]
        }
    )

    summary = fact_summary(payload)

    assert key in summary, (
        f"{tag} produced no {key} cell, so the restatement rule cannot be "
        f"checked: {sorted(summary)}"
    )
    cell = summary[key]
    assert cell.value == pytest.approx(91.0), (
        f"{cell.value!r} is the original figure; a later accession restated it"
    )
    assert cell.provenance is not None
    assert cell.provenance.as_of == "2025-06-30", (
        "as_of is the period the number describes; borrowing the later filing "
        f"date would make a 2025-Q2 figure look current: {cell.provenance.as_of!r}"
    )
    assert cell.provenance.inputs == (tag, "0000320193-25-000091"), (
        "the cell must point at the accession that produced the value it holds"
    )


def test_restatement_wins_regardless_of_document_order() -> None:
    """Point-in-time is a property of the data, not of the JSON key order."""

    original = row("2025-06-30", 100.0, accn="0000320193-25-000073", filed="2025-08-01")
    restated = row("2025-06-30", 91.0, accn="0000320193-25-000091", filed="2025-11-15")

    forward = fact_summary(document({"Revenues": [original, restated]}))
    backward = fact_summary(document({"Revenues": [restated, original]}))

    assert forward == backward
    assert forward["revenue"].value == pytest.approx(91.0)
    assert backward["revenue"].value == pytest.approx(91.0)


def test_the_later_accession_wins_when_two_filings_share_a_filed_date() -> None:
    """Equal ``filed`` still has a deterministic, later-accession answer."""

    payload = document(
        {
            "Revenues": [
                row("2025-06-30", 100.0, accn="0000320193-26-000013", filed="2026-05-01"),
                row("2025-06-30", 104.0, accn="0000320193-26-000020", filed="2026-05-01"),
            ]
        }
    )

    cell = fact_summary(payload)["revenue"]

    assert cell.value == pytest.approx(104.0)
    assert cell.provenance is not None
    assert cell.provenance.inputs == ("Revenues", "0000320193-26-000020")
    assert cell.provenance.as_of == "2025-06-30"


# ---------------------------------------------------------------------------
# The string pool — one object per distinct value, in a bounded vocabulary
# ---------------------------------------------------------------------------


def test_the_audit_trail_shares_one_object_per_distinct_value() -> None:
    """A snapshot repeats the same accession across thousands of cells.

    Measured on 400 symbols: 28,580 ``inputs`` strings for 1,713 distinct values
    (94% duplication), each one its own allocation when a document is parsed row
    by row. Two cells built from the same filing must therefore point at the
    *same* string objects — the values are identical either way, so only the
    object count changes.
    """

    first = observations_to_cells(parse_companyfacts(facts()))
    second = observations_to_cells(parse_companyfacts(facts()))
    assert first == second, "sharing a string must not change a value"

    a = next(iter(first.values())).provenance.inputs
    b = next(iter(second.values())).provenance.inputs
    assert a == b
    assert a[0] is b[0] and a[1] is b[1], "a repeated tag/accession must be one object"


def test_the_pool_leaves_long_and_unusual_text_alone() -> None:
    """Only short, repeated tokens may enter the pool, or it would grow for nothing."""

    from app.engine.facts import _shared

    assert _shared("0001213900-26-050384") is _shared("0001213900-26-050384")
    assert _shared("") == ""
    long_text = "x" * 200
    assert _shared(long_text) is long_text, "a long value must not be interned"
