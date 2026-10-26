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
    FactObservation,
    fact_summary,
    latest_observation,
    observations_to_cells,
    parse_companyfacts,
)
from app.engine.model import Cell, CellStatus, Unit

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "sec_companyfacts_aapl.json"

#: The canonical metrics this slice promises. An engine key outside this tuple
#: has no us-gaap mapping, so no cell may appear for it.
CANONICAL_KEYS = (
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


def test_latest_observation_prefers_the_first_default_tag_that_has_evidence() -> None:
    """Documented precedence: tag order first, recency within the chosen tag."""

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
    assert latest.tag == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert latest.value == pytest.approx(111.0), (
        "the alias is older; recency only decides between rows of the chosen tag"
    )


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
    payload = document(
        {
            "OperatingIncomeLoss": [row("2024-12-31", 5.0, accn="a")],
            "Revenues": [row("2024-12-31", 7.0, accn="a")],
        }
    )

    summary = fact_summary(payload)

    assert set(summary) == {"revenue"}, (
        "operating income has no canonical engine key in this slice; it must not "
        f"leak into the summary: {sorted(summary)}"
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
