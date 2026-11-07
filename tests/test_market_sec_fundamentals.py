"""Contract for SEC bulk facts → Ztockz screener fields (``app.market.sec_fundamentals``).

The slice answers one question: **which catalog column does a streamed
``num.txt`` row fill, and when is the honest answer "nothing"?** The mapping is
the whole contract, so every rule here is about not inventing a number:

* **A tag that is not in the document is an absent field**, never a zero and
  never a guess from an adjacent concept.
* **Flows come from an annual duration** (``qtrs=4``): a nine-month YTD or a
  single quarter published as a "TTM" would be three months wearing twelve
  months' clothes. **Instants come from ``qtrs=0``**, newest period end first.
* **Recency, then restatement.** The newest ``ddate`` wins; within one period
  the later ``filed`` (joined from ``sub.txt``) wins, so a restated comparative
  replaces the original figure.
* **Units are checked, not assumed.** A share count quoted in USD is not a
  share count; an EUR amount is not a USD amount; a dimensional (segment) row
  is not the consolidated fact.
* **Derived fields are arithmetic on selected facts only.** A missing input or
  a zero denominator makes the ratio absent — ``current_ratio`` for an issuer
  with no current liabilities is not ``inf``.
* **The valuation and growth block is arithmetic on the rest.** Enterprise
  value is market cap plus the long-term debt line, less cash; EV/EBITDA and
  P/FCF are the documented approximations; ROIC taxes EBIT at the filing's
  own effective rate, or 21% when the tax line is absent; a Q/Q or YoY figure
  needs two periods on file, and one period is no growth at all.

The fixture is the same two-issuer ZIP ``test_engine_bulk_financials`` builds
(Apple with a restatement, a dimensional row and a wrong-unit row; Microsoft
with comma-formatted values), plus hand-built :class:`Fact` rows for the
selection rules. No test reaches the network.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import pytest

from app.engine.bulk_financials import Fact, FactIndex
from app.market.sec_fundamentals import (
    DERIVED_FIELDS,
    FIELDS,
    FLOW_FIELDS,
    INSTANT_FIELDS,
    MAPPED_CONCEPTS,
    MUST_NOT_MAP,
    TAG_MAP,
    UOM_BY_FIELD,
    fundamentals_for_cik,
    fundamentals_for_symbol,
    map_fundamentals,
)

AAPL_CIK = "0000320193"
AAPL_K = "0000320193-25-000079"  # 10-K, filed 2025-10-31
AAPL_Q = "0000320193-26-000020"  # 10-Q, filed 2026-07-31
AAPL_K24 = "0000320193-24-000099"  # 10-K, filed 2024-11-01
AAPL_Q24 = "0000320193-24-000101"  # 10-Q, filed 2024-08-01
AAPL_Q2 = "0000320193-26-000010"  # 10-Q, filed 2026-05-01
AAPL_Q2R = "0000320193-26-000031"  # the restated Q2, filed 2026-08-14
AAPL_K26 = "0000320193-26-000090"  # 10-K, filed 2026-10-30
AAPL_K23 = "0000320193-23-000077"  # 10-K, filed 2023-11-02
AAPL_K22 = "0000320193-22-000082"  # 10-K, filed 2022-10-28
AAPL_K21 = "0000320193-21-000090"  # 10-K, filed 2021-10-29
AAPL_K20 = "0000320193-20-000096"  # 10-K, filed 2020-10-30
MSFT_ADSH = "0000789019-26-000045"


def fact(
    tag: str,
    value: float,
    *,
    ddate: str = "2025-09-27",
    qtrs: int | None = 4,
    uom: str = "USD",
    adsh: str = AAPL_K,
    filed: str = "2025-10-31",
    segments: str = "",
    version: str = "us-gaap/2026",
) -> Fact:
    """One streamed ``num.txt`` row, already joined to its filing."""

    return Fact(
        adsh=adsh,
        tag=tag,
        version=version,
        ddate=ddate,
        qtrs=qtrs,
        uom=uom,
        value=value,
        cik=AAPL_CIK,
        segments=segments,
        filed=filed,
    )


REVENUE = "RevenueFromContractWithCustomerExcludingAssessedTax"

#: The cash-flow, amortization and tax concepts the valuation and growth
#: block derives from. None is a published field; each is an input.
OCF = "NetCashProvidedByUsedInOperatingActivities"
CAPEX = "PaymentsToAcquirePropertyPlantAndEquipment"
DA = "DepreciationDepletionAndAmortization"
TAX = "IncomeTaxExpenseBenefit"
PRETAX = (
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"
)


def apple_facts() -> list[Fact]:
    """The Apple fixture: annual flows, instant balances, one restatement."""

    return [
        fact(REVENUE, 416_161_000_000),
        fact(REVENUE, 300_000_000_000, ddate="2026-06-27", qtrs=3, adsh=AAPL_Q, filed="2026-07-31"),
        fact(REVENUE, 94_000_000_000, ddate="2026-06-27", qtrs=1, adsh=AAPL_Q, filed="2026-07-31"),
        fact(REVENUE, 50_000_000, ddate="2026-06-27", qtrs=4, adsh=AAPL_Q, filed="2026-07-31", segments="SEG-1"),
        fact("NetIncomeLoss", 112_010_000_000),
        fact("NetIncomeLoss", 111_000_000_000, adsh=AAPL_Q, filed="2026-07-31"),
        # The bulk dataset quotes per-share concepts in plain USD.
        fact("EarningsPerShareDiluted", 7.46),
        fact("GrossProfit", 195_200_000_000),
        fact("OperatingIncomeLoss", 122_432_000_000),
        fact("Assets", 364_980_000_000, qtrs=0),
        fact("StockholdersEquity", 73_000_000_000, qtrs=0),
        fact("AssetsCurrent", 152_987_000_000, qtrs=0),
        fact("LiabilitiesCurrent", 176_000_000_000, qtrs=0),
        fact("InventoryNet", 7_000_000_000, qtrs=0),
        fact("CashAndCashEquivalentsAtCarryingValue", 30_000_000_000, qtrs=0),
        fact("LongTermDebtNoncurrent", 85_750_000_000, qtrs=0),
        fact("CommonStockSharesOutstanding", 14_800_000_000, qtrs=0, uom="shares"),
        fact("CommonStockSharesOutstanding", 999, ddate="2026-06-27", qtrs=0, uom="USD", adsh=AAPL_Q, filed="2026-07-31"),
        fact("CommonStockDividendsPerShareDeclared", 1.01),
        fact("Revenues", 123, ddate="2025-06-30", uom="EUR"),
    ]


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


def test_fields_are_exactly_the_catalog_columns() -> None:
    assert FIELDS == (
        "sales",
        "net_income",
        "eps",
        "gross_margin",
        "operating_margin",
        "net_margin",
        "roa",
        "roe",
        "current_ratio",
        "quick_ratio",
        "debt_to_equity",
        "book_per_share",
        "cash_per_share",
        "dividend",
        "shares_outstanding",
        "market_cap",
        "pe",
        "enterprise_value",
        "ev_sales",
        "ev_ebitda",
        "pfcf",
        "roic",
        "lt_debt_to_equity",
        "eps_qoq",
        "sales_qoq",
        "eps_yoy_ttm",
        "sales_yoy_ttm",
        "payout_ratio",
        "eps_past_3y",
        "sales_past_3y",
        "eps_past_5y",
        "sales_past_5y",
    )


def test_every_published_field_is_either_mapped_or_derived() -> None:
    direct = set(FIELDS) & set(TAG_MAP)
    assert direct == {
        "sales",
        "net_income",
        "eps",
        "dividend",
        "shares_outstanding",
    }, "the other twenty-seven fields are arithmetic on mapped inputs"


def test_the_tag_map_is_well_formed() -> None:
    assert TAG_MAP, "the map is the contract"
    for field, tags in TAG_MAP.items():
        assert isinstance(tags, tuple) and tags, (field, tags)
        assert all(isinstance(tag, str) and tag.strip() == tag and tag for tag in tags), (field, tags)
        assert len(set(tags)) == len(tags), (field, tags)


def test_the_primary_concepts_are_the_ones_the_engine_already_uses() -> None:
    assert TAG_MAP["sales"][0] == REVENUE
    assert TAG_MAP["net_income"][0] == "NetIncomeLoss"
    assert TAG_MAP["eps"][0] == "EarningsPerShareDiluted"
    assert TAG_MAP["shares_outstanding"][0] == "CommonStockSharesOutstanding"


def test_flow_and_instant_partition_the_mapped_fields() -> None:
    assert FLOW_FIELDS | INSTANT_FIELDS == set(TAG_MAP)
    assert FLOW_FIELDS.isdisjoint(INSTANT_FIELDS)


def test_every_mapped_field_declares_the_unit_its_rows_must_carry() -> None:
    assert set(UOM_BY_FIELD) == set(TAG_MAP)
    # The bulk dataset's vocabulary is USD and shares — per-share concepts
    # arrive quoted in plain USD (the per-share-ness is in the tag), so the
    # companyfacts "USD/shares" spelling is deliberately not accepted here.
    assert set(UOM_BY_FIELD.values()) == {"USD", "shares"}
    assert UOM_BY_FIELD["shares_outstanding"] == "shares"
    assert UOM_BY_FIELD["eps"] == "USD"
    assert UOM_BY_FIELD["dividend"] == "USD"
    assert UOM_BY_FIELD["sales"] == "USD"


# ---------------------------------------------------------------------------
# map_fundamentals — the Apple fixture
# ---------------------------------------------------------------------------


def test_the_apple_fixture_fills_every_field_it_can() -> None:
    fields = map_fundamentals(apple_facts())

    assert set(fields) == set(FIELDS)
    assert fields["sales"] == pytest.approx(416_161_000_000)
    assert fields["net_income"] == pytest.approx(111_000_000_000), "the restated comparative wins"
    assert fields["eps"] == pytest.approx(7.46)
    assert fields["dividend"] == pytest.approx(1.01)
    assert fields["shares_outstanding"] == pytest.approx(14_800_000_000)
    assert fields["gross_margin"] == pytest.approx(195_200_000_000 / 416_161_000_000)
    assert fields["operating_margin"] == pytest.approx(122_432_000_000 / 416_161_000_000)
    assert fields["net_margin"] == pytest.approx(111_000_000_000 / 416_161_000_000)
    assert fields["roa"] == pytest.approx(111_000_000_000 / 364_980_000_000)
    assert fields["roe"] == pytest.approx(111_000_000_000 / 73_000_000_000)
    assert fields["current_ratio"] == pytest.approx(152_987_000_000 / 176_000_000_000)
    assert fields["quick_ratio"] == pytest.approx((152_987_000_000 - 7_000_000_000) / 176_000_000_000)
    assert fields["debt_to_equity"] == pytest.approx(85_750_000_000 / 73_000_000_000)
    assert fields["book_per_share"] == pytest.approx(73_000_000_000 / 14_800_000_000)
    assert fields["cash_per_share"] == pytest.approx(30_000_000_000 / 14_800_000_000)


def test_price_dependent_fields_are_absent_without_a_price() -> None:
    fields = map_fundamentals(apple_facts())
    assert fields["market_cap"] is None
    assert fields["pe"] is None


def test_price_fills_market_cap_and_pe() -> None:
    fields = map_fundamentals(apple_facts(), price=250.0)
    assert fields["market_cap"] == pytest.approx(250.0 * 14_800_000_000)
    assert fields["pe"] == pytest.approx(250.0 / 7.46)


def test_the_result_is_deterministic() -> None:
    assert map_fundamentals(apple_facts()) == map_fundamentals(apple_facts())


# ---------------------------------------------------------------------------
# Selection rules
# ---------------------------------------------------------------------------


def test_a_flow_uses_the_annual_duration_not_the_newest_quarter() -> None:
    fields = map_fundamentals(apple_facts())
    assert fields["sales"] == pytest.approx(416_161_000_000), (
        "the nine-month YTD and the quarter are newer, but a duration is not a TTM"
    )


def test_an_instant_uses_the_newest_period_end() -> None:
    facts = [
        fact("Assets", 100.0, ddate="2024-09-28", qtrs=0),
        fact("Assets", 120.0, ddate="2025-09-27", qtrs=0),
    ]
    fields = map_fundamentals(facts)
    assert fields["roa"] is None, "no net income in this fixture"
    assert map_fundamentals(facts + [fact("NetIncomeLoss", 12.0)])["roa"] == pytest.approx(12.0 / 120.0)


def test_a_later_filed_restatement_replaces_the_original() -> None:
    facts = [
        fact("NetIncomeLoss", 100.0, adsh=AAPL_K, filed="2025-10-31"),
        fact("NetIncomeLoss", 91.0, adsh=AAPL_Q, filed="2026-07-31"),
    ]
    assert map_fundamentals(facts)["net_income"] == pytest.approx(91.0)

    backward = list(reversed(facts))
    assert map_fundamentals(backward)["net_income"] == pytest.approx(91.0), "order is not the rule"


def test_a_newer_period_beats_a_later_filed_older_period() -> None:
    facts = [
        fact("NetIncomeLoss", 100.0, ddate="2025-09-27", adsh=AAPL_K, filed="2025-10-31"),
        fact("NetIncomeLoss", 130.0, ddate="2026-06-27", adsh=AAPL_Q, filed="2026-07-31"),
    ]
    assert map_fundamentals(facts)["net_income"] == pytest.approx(130.0)


def test_a_dimensional_row_is_not_the_consolidated_fact() -> None:
    facts = [fact(REVENUE, 50_000_000, segments="SEG-1")]
    assert map_fundamentals(facts)["sales"] is None


def test_a_row_in_the_wrong_unit_is_not_the_fact() -> None:
    facts = [
        fact("CommonStockSharesOutstanding", 999, qtrs=0, uom="USD"),
        fact(REVENUE, 123, uom="EUR"),
    ]
    fields = map_fundamentals(facts)
    assert fields["shares_outstanding"] is None, "a share count quoted in USD is not a share count"
    assert fields["sales"] is None, "an EUR amount is not a USD amount"


def test_a_row_with_no_unit_is_not_the_fact() -> None:
    assert map_fundamentals([fact(REVENUE, 500.0, uom="")])["sales"] is None


def test_the_dei_share_count_is_the_fallback() -> None:
    facts = [fact("EntityCommonStockSharesOutstanding", 14_800_000_000, qtrs=0, uom="shares", version="dei/2026")]
    assert map_fundamentals(facts)["shares_outstanding"] == pytest.approx(14_800_000_000)


def test_an_alias_concept_fills_the_field_when_the_primary_is_absent() -> None:
    facts = [fact("Revenues", 250_000_000_000)]
    assert map_fundamentals(facts)["sales"] == pytest.approx(250_000_000_000)


# ---------------------------------------------------------------------------
# Absence
# ---------------------------------------------------------------------------


def test_a_missing_tag_is_an_absent_field_never_a_zero() -> None:
    fields = map_fundamentals([fact(REVENUE, 416_161_000_000)])
    assert fields["sales"] == pytest.approx(416_161_000_000)
    for key in FIELDS:
        if key != "sales":
            assert fields[key] is None, f"{key} has no evidence and must not be invented"


def test_no_facts_at_all_is_every_field_absent() -> None:
    fields = map_fundamentals([])
    assert fields == dict.fromkeys(FIELDS)


def test_a_negative_eps_stays_negative_and_the_pe_follows_it() -> None:
    facts = [fact("EarningsPerShareDiluted", -2.5)]
    fields = map_fundamentals(facts)
    assert fields["eps"] == pytest.approx(-2.5)
    assert fields["pe"] is None, "no price yet"
    priced = map_fundamentals(facts, price=100.0)
    assert priced["pe"] == pytest.approx(-40.0), "a loss-maker's P/E is negative, not absent"


def test_a_zero_denominator_makes_the_ratio_absent() -> None:
    facts = [
        fact("AssetsCurrent", 100.0, qtrs=0),
        fact("LiabilitiesCurrent", 0.0, qtrs=0),
        fact("InventoryNet", 20.0, qtrs=0),
    ]
    fields = map_fundamentals(facts)
    assert fields["current_ratio"] is None
    assert fields["quick_ratio"] is None


def test_quick_ratio_needs_the_inventory_line() -> None:
    facts = [
        fact("AssetsCurrent", 100.0, qtrs=0),
        fact("LiabilitiesCurrent", 50.0, qtrs=0),
    ]
    assert map_fundamentals(facts)["quick_ratio"] is None, "without inventory the quick ratio cannot be built"


@pytest.mark.parametrize("price", [0, -5.0, float("nan"), float("inf")])
def test_a_price_that_is_not_a_real_quote_is_refused(price: float) -> None:
    with pytest.raises(ValueError):
        map_fundamentals(apple_facts(), price=price)


@pytest.mark.parametrize("price", ["250", True, object()])
def test_a_price_that_is_not_a_number_is_refused(price: object) -> None:
    with pytest.raises(TypeError):
        map_fundamentals(apple_facts(), price=price)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Index integration — the same rules through a streamed ZIP
# ---------------------------------------------------------------------------


SUB_HEADER = ["adsh", "cik", "name", "form", "period", "fy", "fp", "filed"]
NUM_HEADER = ["adsh", "tag", "version", "ddate", "qtrs", "uom", "segments", "coreg", "value", "footnote"]


def _sub(adsh: str, cik: int, name: str, form: str, period: str, fy: int, fp: str, filed: str) -> list[str]:
    return [adsh, str(cik), name, form, period, str(fy), fp, filed]


def _num(adsh: str, tag: str, ddate: str, qtrs: object, uom: str, value: object) -> list[str]:
    return [adsh, tag, "us-gaap/2026", ddate, "" if qtrs is None else str(qtrs), uom, "", "", str(value), ""]


def _zip(tmp_path: Path) -> Path:
    subs = [
        _sub(AAPL_K, 320193, "Apple Inc.", "10-K", "20250927", 2025, "FY", "20251031"),
        _sub(AAPL_Q, 320193, "Apple Inc.", "10-Q", "20260630", 2026, "Q3", "20260731"),
        _sub(MSFT_ADSH, 789019, "Microsoft Corp", "10-Q", "20260630", 2026, "Q4", "20260729"),
    ]
    nums = [
        _num(AAPL_K, REVENUE, "20250927", 4, "USD", 416_161_000_000),
        _num(AAPL_K, "NetIncomeLoss", "20250927", 4, "USD", 112_010_000_000),
        _num(AAPL_Q, "NetIncomeLoss", "20250927", 4, "USD", 111_000_000_000),
        _num(AAPL_K, "EarningsPerShareDiluted", "20250927", 4, "USD", 7.46),
        _num(AAPL_K, "Assets", "20250927", 0, "USD", 364_980_000_000),
        _num(AAPL_K, "CommonStockSharesOutstanding", "20250927", 0, "shares", 14_800_000_000),
        _num(MSFT_ADSH, "Revenues", "20250630", 4, "USD", "1,234.5"),
    ]
    path = tmp_path / "2026q3.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("sub.txt", "\n".join(["\t".join(SUB_HEADER)] + ["\t".join(r) for r in subs]) + "\n")
        archive.writestr("num.txt", "\n".join(["\t".join(NUM_HEADER)] + ["\t".join(r) for r in nums]) + "\n")
    return path


def test_fundamentals_for_cik_reads_the_streamed_index(tmp_path: Path) -> None:
    index = FactIndex.build(_zip(tmp_path))
    fields = fundamentals_for_cik(index, AAPL_CIK, price=250.0)

    assert fields["sales"] == pytest.approx(416_161_000_000)
    assert fields["net_income"] == pytest.approx(111_000_000_000), "the later-filed restatement wins"
    assert fields["eps"] == pytest.approx(7.46)
    assert fields["roa"] == pytest.approx(111_000_000_000 / 364_980_000_000)
    assert fields["shares_outstanding"] == pytest.approx(14_800_000_000)
    assert fields["market_cap"] == pytest.approx(250.0 * 14_800_000_000)
    assert fields["pe"] == pytest.approx(250.0 / 7.46)
    assert fields["gross_margin"] is None, "Apple's fixture has no GrossProfit row"


def test_fundamentals_for_cik_of_an_unknown_issuer_is_all_absent(tmp_path: Path) -> None:
    index = FactIndex.build(_zip(tmp_path))
    assert fundamentals_for_cik(index, "0000000001") == dict.fromkeys(FIELDS)


def test_fundamentals_for_symbol_resolves_through_the_cik_map(tmp_path: Path) -> None:
    index = FactIndex.build(_zip(tmp_path))
    cik_by_symbol = {"AAPL": AAPL_CIK, "MSFT": "0000789019"}

    apple = fundamentals_for_symbol(index, "aapl", cik_by_symbol)
    assert apple["sales"] == pytest.approx(416_161_000_000)

    microsoft = fundamentals_for_symbol(index, "MSFT", cik_by_symbol)
    assert microsoft["sales"] == pytest.approx(1234.5), "the comma-formatted value parsed"
    assert microsoft["net_income"] is None

    unknown = fundamentals_for_symbol(index, "ZZZZ", cik_by_symbol)
    assert unknown == dict.fromkeys(FIELDS), "an unknown symbol is absent, not invented"


def test_the_snapshot_document_feeds_the_mapper_too(tmp_path: Path) -> None:
    from app.engine import bulk_financials as bf

    index = FactIndex.build(_zip(tmp_path))
    path = tmp_path / "facts.json"
    bf.save_snapshot(index, path)

    fields = fundamentals_for_cik(bf.load_snapshot(path), AAPL_CIK)
    assert fields["sales"] == pytest.approx(416_161_000_000)
    assert fields["net_income"] == pytest.approx(111_000_000_000)


def test_fundamentals_for_cik_does_not_leak_across_issuers(tmp_path: Path) -> None:
    index = FactIndex.build(_zip(tmp_path))

    microsoft = fundamentals_for_cik(index, "0000789019")
    assert microsoft["sales"] == pytest.approx(1234.5)
    assert microsoft["net_income"] is None, "Apple's NetIncomeLoss is not Microsoft's fact"

    apple = fundamentals_for_cik(index, AAPL_CIK)
    assert apple["sales"] == pytest.approx(416_161_000_000)


# ---------------------------------------------------------------------------
# Trailing twelve months
# ---------------------------------------------------------------------------
#
# A mid-year filer's 10-K is in the *previous* quarter's dataset while its
# 10-Q is in the current one, so the caller merges two quarters into one fact
# list. The TTM is annual + year-to-date − prior-year year-to-date, using the
# 10-Q's own comparative for the prior span.


def test_flow_rolls_the_annual_forward_to_a_trailing_twelve_months() -> None:
    """annual 400 + YTD 300 − prior YTD 280 = TTM 420, not the stale 400."""

    rows = [
        fact(REVENUE, 400_000_000_000, ddate="2025-09-27", qtrs=4),
        fact(REVENUE, 300_000_000_000, ddate="2026-06-27", qtrs=3, adsh=AAPL_Q, filed="2026-07-31"),
        fact(REVENUE, 280_000_000_000, ddate="2025-06-28", qtrs=3, adsh=AAPL_Q, filed="2025-08-01"),
    ]
    assert map_fundamentals(rows)["sales"] == pytest.approx(420_000_000_000)


def test_flow_prefers_the_annual_when_it_is_the_newest_filing() -> None:
    """A 10-K filed this quarter *is* the trailing twelve months."""

    rows = [
        fact(REVENUE, 300_000_000_000, ddate="2026-06-27", qtrs=3, adsh=AAPL_Q, filed="2026-07-31"),
        fact(REVENUE, 280_000_000_000, ddate="2025-06-28", qtrs=3, adsh=AAPL_Q, filed="2025-08-01"),
        fact(REVENUE, 410_000_000_000, ddate="2026-09-26", qtrs=4, filed="2026-10-30"),
    ]
    assert map_fundamentals(rows)["sales"] == pytest.approx(410_000_000_000)


def test_flow_without_an_annual_base_is_absent_not_a_nine_month_ttm() -> None:
    """No 10-K anywhere: a nine-month figure never wears twelve months' clothes."""

    rows = [
        fact(REVENUE, 300_000_000_000, ddate="2026-06-27", qtrs=3, adsh=AAPL_Q, filed="2026-07-31"),
        fact(REVENUE, 280_000_000_000, ddate="2025-06-28", qtrs=3, adsh=AAPL_Q, filed="2025-08-01"),
    ]
    assert map_fundamentals(rows)["sales"] is None


def test_flow_without_the_prior_year_comparative_falls_back_to_the_annual() -> None:
    """The 10-Q carried no comparative: the annual alone, never invented."""

    rows = [
        fact(REVENUE, 400_000_000_000, ddate="2025-09-27", qtrs=4),
        fact(REVENUE, 300_000_000_000, ddate="2026-06-27", qtrs=3, adsh=AAPL_Q, filed="2026-07-31"),
    ]
    assert map_fundamentals(rows)["sales"] == pytest.approx(400_000_000_000)


def test_flow_takes_the_longest_ytd_span_at_one_period_end() -> None:
    """A 10-Q publishes qtrs=1 and qtrs=3 at the same ddate; the nine months win."""

    rows = [
        fact(REVENUE, 400_000_000_000, ddate="2025-09-27", qtrs=4),
        fact(REVENUE, 100_000_000_000, ddate="2026-06-27", qtrs=1, adsh=AAPL_Q, filed="2026-07-31"),
        fact(REVENUE, 300_000_000_000, ddate="2026-06-27", qtrs=3, adsh=AAPL_Q, filed="2026-07-31"),
        fact(REVENUE, 280_000_000_000, ddate="2025-06-28", qtrs=3, adsh=AAPL_Q, filed="2025-08-01"),
    ]
    assert map_fundamentals(rows)["sales"] == pytest.approx(420_000_000_000)


def test_flow_ignores_a_prior_span_that_is_not_one_year_earlier() -> None:
    """A six-month-old qtrs=3 row is not the prior-year comparative."""

    rows = [
        fact(REVENUE, 400_000_000_000, ddate="2025-09-27", qtrs=4),
        fact(REVENUE, 300_000_000_000, ddate="2026-06-27", qtrs=3, adsh=AAPL_Q, filed="2026-07-31"),
        fact(REVENUE, 280_000_000_000, ddate="2025-12-27", qtrs=3, adsh=AAPL_Q, filed="2026-02-01"),
    ]
    assert map_fundamentals(rows)["sales"] == pytest.approx(400_000_000_000)


def test_flow_requires_the_same_tag_across_the_ttm_arithmetic() -> None:
    """Mixing a 10-K's Revenues with a 10-Q's contract-revenue tag is not a TTM."""

    rows = [
        fact("Revenues", 400_000_000_000, ddate="2025-09-27", qtrs=4),
        fact(REVENUE, 300_000_000_000, ddate="2026-06-27", qtrs=3, adsh=AAPL_Q, filed="2026-07-31"),
        fact(REVENUE, 280_000_000_000, ddate="2025-06-28", qtrs=3, adsh=AAPL_Q, filed="2025-08-01"),
    ]
    # The annual is a different tag, so the TTM is not built; the annual alone
    # is still the honest answer for the field.
    assert map_fundamentals(rows)["sales"] == pytest.approx(400_000_000_000)


def test_ttm_derived_fields_follow_the_rolled_forward_sales() -> None:
    """net_margin divides the TTM net income by the TTM sales, not a stale year."""

    rows = [
        fact(REVENUE, 400_000_000_000, ddate="2025-09-27", qtrs=4),
        fact(REVENUE, 300_000_000_000, ddate="2026-06-27", qtrs=3, adsh=AAPL_Q, filed="2026-07-31"),
        fact(REVENUE, 280_000_000_000, ddate="2025-06-28", qtrs=3, adsh=AAPL_Q, filed="2025-08-01"),
        fact("NetIncomeLoss", 100_000_000_000, ddate="2025-09-27", qtrs=4),
        fact("NetIncomeLoss", 80_000_000_000, ddate="2026-06-27", qtrs=3, adsh=AAPL_Q, filed="2026-07-31"),
        fact("NetIncomeLoss", 70_000_000_000, ddate="2025-06-28", qtrs=3, adsh=AAPL_Q, filed="2025-08-01"),
    ]
    fields = map_fundamentals(rows)
    assert fields["sales"] == pytest.approx(420_000_000_000)
    assert fields["net_income"] == pytest.approx(110_000_000_000)
    assert fields["net_margin"] == pytest.approx(110_000_000_000 / 420_000_000_000)


def test_instant_fields_are_untouched_by_the_ttm_rule() -> None:
    """A balance is a point in time; the TTM arithmetic never touches it."""

    rows = [
        fact("Assets", 364_980_000_000, ddate="2025-09-27", qtrs=0),
        fact("Assets", 370_000_000_000, ddate="2026-06-27", qtrs=0, adsh=AAPL_Q, filed="2026-07-31"),
    ]
    assert map_fundamentals(rows)["roa"] is None, "no flow to divide by"
    selected = fundamentals_for_cik(_index_of(rows), AAPL_CIK)
    assert selected["shares_outstanding"] is None


def _index_of(rows: list[Fact]) -> Any:
    """A one-issuer index over hand-built facts, for the cik entry points."""

    class _Index:
        def facts_for(self, cik: str) -> tuple[Fact, ...]:
            return tuple(rows) if cik == AAPL_CIK else ()

    return _Index()


# ---------------------------------------------------------------------------
# The tag extensions the coverage probe recommended
# ---------------------------------------------------------------------------
#
# research/sec-tag-coverage.md ranked every revenue-like concept observed
# across 500 universe CIKs. These are the five that answer a *total* top line
# for filers the original three concepts miss; the probe's must-not-add list
# is pinned by MUST_NOT_MAP.


def test_bank_net_revenue_fills_sales() -> None:
    """JPM's top line is RevenuesNetOfInterestExpense, not Revenues."""

    rows = [
        fact("RevenuesNetOfInterestExpense", 182_447_000_000, ddate="2025-12-31", qtrs=4),
        fact("NetIncomeLoss", 57_048_000_000, ddate="2025-12-31", qtrs=4),
    ]
    fields = map_fundamentals(rows)
    assert fields["sales"] == pytest.approx(182_447_000_000)
    assert fields["net_income"] == pytest.approx(57_048_000_000)


def test_including_assessed_tax_revenue_fills_sales() -> None:
    """CRWD/TJX/VLO file only the including-assessed-tax sibling."""

    rows = [
        fact(
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            60_372_000_000,
            ddate="2026-01-31",
            qtrs=4,
        ),
    ]
    assert map_fundamentals(rows)["sales"] == pytest.approx(60_372_000_000)


def test_utility_total_operating_revenue_fills_sales() -> None:
    """NEE/DUK/SRE file RegulatedAndUnregulatedOperatingRevenue."""

    rows = [
        fact("RegulatedAndUnregulatedOperatingRevenue", 28_100_000_000, ddate="2025-12-31", qtrs=4),
    ]
    assert map_fundamentals(rows)["sales"] == pytest.approx(28_100_000_000)


def test_ifrs_revenue_fills_sales_under_the_ifrs_taxonomy() -> None:
    """GFS is a 20-F filer; its Revenue row is tagged ifrs/2025."""

    rows = [
        fact("Revenue", 7_400_000_000, ddate="2025-12-31", qtrs=4, version="ifrs/2025"),
    ]
    assert map_fundamentals(rows)["sales"] == pytest.approx(7_400_000_000)


def test_a_company_extension_named_revenue_does_not_fill_sales() -> None:
    """2,272 private extensions carry revenue-like names; none may win."""

    rows = [
        fact("Revenue", 7_400_000_000, ddate="2025-12-31", qtrs=4, version="gfs/2025"),
    ]
    assert map_fundamentals(rows)["sales"] is None


def test_the_primary_concept_still_wins_over_the_extensions() -> None:
    """A us-gaap filer with both concepts keeps the excluding-assessed-tax one."""

    rows = [
        fact(REVENUE, 100_000_000_000, ddate="2025-12-31", qtrs=4),
        fact("RevenueFromContractWithCustomerIncludingAssessedTax", 108_000_000_000, ddate="2025-12-31", qtrs=4),
    ]
    assert map_fundamentals(rows)["sales"] == pytest.approx(100_000_000_000)


def test_must_not_map_documents_the_component_concepts() -> None:
    """The probe's must-not-add list is pinned in code, not just prose."""

    for tag in (
        "InterestIncomeExpenseNet",
        "InterestIncomeOperating",
        "NoninterestIncome",
        "PremiumsEarnedNet",
        "CostOfRevenue",
        "ComprehensiveIncomeNetOfTax",
    ):
        assert tag in MUST_NOT_MAP, f"{tag} must stay unmapped"
        assert tag not in TAG_MAP["sales"]
        assert tag not in TAG_MAP["net_income"]


def test_net_income_needs_no_extension() -> None:
    """The probe found no candidate that fixes a mapped CIK for net income."""

    assert TAG_MAP["net_income"] == ("NetIncomeLoss", "ProfitLoss")


# ---------------------------------------------------------------------------
# The valuation and growth block
# ---------------------------------------------------------------------------
#
# The enrichment probe measured these eleven columns as computable from the
# four cached SEC quarters: enterprise value at 98/100 universe symbols down
# to payout ratio at 50/100. Each is arithmetic on facts the mapper already
# selects, plus five new input concepts — operating cash flow, capital
# expenditure, depreciation and amortization, income tax and pre-tax income.


#: The eleven new published columns, in catalog order.
BLOCK_FIELDS = (
    "enterprise_value",
    "ev_sales",
    "ev_ebitda",
    "pfcf",
    "roic",
    "lt_debt_to_equity",
    "eps_qoq",
    "sales_qoq",
    "eps_yoy_ttm",
    "sales_yoy_ttm",
    "payout_ratio",
)


def _published(*names: str) -> None:
    """Every name the block publishes; RED until the mapper carries it."""

    for name in names:
        assert name in FIELDS, f"{name} is not a published field yet"


def deep_facts() -> list[Fact]:
    """One issuer carrying every input the valuation and growth block needs.

    The annual flows of two 10-Ks (the newest twelve months and the earlier
    window), two distinct quarters (the Q/Q pair), the instant balances, the
    share count and the declared dividend.
    """

    return [
        fact(REVENUE, 400_000_000_000, ddate="2025-09-27", qtrs=4),
        fact(REVENUE, 380_000_000_000, ddate="2024-09-28", qtrs=4, adsh=AAPL_K24, filed="2024-11-01"),
        fact(REVENUE, 95_000_000_000, ddate="2026-03-28", qtrs=1, adsh=AAPL_Q2, filed="2026-05-01"),
        fact(REVENUE, 105_000_000_000, ddate="2026-06-27", qtrs=1, adsh=AAPL_Q, filed="2026-07-31"),
        fact("EarningsPerShareDiluted", 8.00, ddate="2025-09-27", qtrs=4),
        fact("EarningsPerShareDiluted", 7.00, ddate="2024-09-28", qtrs=4, adsh=AAPL_K24, filed="2024-11-01"),
        fact("EarningsPerShareDiluted", 2.00, ddate="2026-03-28", qtrs=1, adsh=AAPL_Q2, filed="2026-05-01"),
        fact("EarningsPerShareDiluted", 2.20, ddate="2026-06-27", qtrs=1, adsh=AAPL_Q, filed="2026-07-31"),
        fact("OperatingIncomeLoss", 120_000_000_000, ddate="2025-09-27", qtrs=4),
        fact(OCF, 110_000_000_000, ddate="2025-09-27", qtrs=4),
        fact(CAPEX, 10_000_000_000, ddate="2025-09-27", qtrs=4),
        fact(DA, 20_000_000_000, ddate="2025-09-27", qtrs=4),
        fact(TAX, 15_000_000_000, ddate="2025-09-27", qtrs=4),
        fact(PRETAX, 60_000_000_000, ddate="2025-09-27", qtrs=4),
        fact("StockholdersEquity", 90_000_000_000, ddate="2026-06-27", qtrs=0),
        fact("LongTermDebtNoncurrent", 30_000_000_000, ddate="2026-06-27", qtrs=0),
        fact("CashAndCashEquivalentsAtCarryingValue", 25_000_000_000, ddate="2026-06-27", qtrs=0),
        fact("CommonStockSharesOutstanding", 5_000_000_000, ddate="2026-06-27", qtrs=0, uom="shares"),
        fact("CommonStockDividendsPerShareDeclared", 2.00, ddate="2025-09-27", qtrs=4),
    ]


def ttm_facts() -> list[Fact]:
    """A mid-year filer: two annuals and both nine-month year-to-date pairs.

    The caller merges the 10-K quarter with the 10-Q quarter, so the newest
    twelve months are 400 + 300 − 280 = 420 and the earlier twelve are
    380 + 280 − 270 = 390.
    """

    return [
        fact(REVENUE, 400_000_000_000, ddate="2025-09-27", qtrs=4),
        fact(REVENUE, 300_000_000_000, ddate="2026-06-27", qtrs=3, adsh=AAPL_Q, filed="2026-07-31"),
        fact(REVENUE, 280_000_000_000, ddate="2025-06-28", qtrs=3, adsh=AAPL_Q, filed="2025-08-01"),
        fact(REVENUE, 380_000_000_000, ddate="2024-09-28", qtrs=4, adsh=AAPL_K24, filed="2024-11-01"),
        fact(REVENUE, 270_000_000_000, ddate="2024-06-29", qtrs=3, adsh=AAPL_Q24, filed="2024-08-01"),
        fact("EarningsPerShareDiluted", 8.00, ddate="2025-09-27", qtrs=4),
        fact("EarningsPerShareDiluted", 6.00, ddate="2026-06-27", qtrs=3, adsh=AAPL_Q, filed="2026-07-31"),
        fact("EarningsPerShareDiluted", 5.50, ddate="2025-06-28", qtrs=3, adsh=AAPL_Q, filed="2025-08-01"),
        fact("EarningsPerShareDiluted", 7.00, ddate="2024-09-28", qtrs=4, adsh=AAPL_K24, filed="2024-11-01"),
        fact("EarningsPerShareDiluted", 4.80, ddate="2024-06-29", qtrs=3, adsh=AAPL_Q24, filed="2024-08-01"),
    ]


def test_the_block_is_published_and_derived() -> None:
    _published(*BLOCK_FIELDS)
    head = FIELDS[: -len(GROWTH_FIELDS)]
    assert head[-len(BLOCK_FIELDS) :] == BLOCK_FIELDS, "the block keeps its catalog order"
    assert FIELDS[-len(GROWTH_FIELDS) :] == GROWTH_FIELDS, "the growth block closes the catalog order"
    for name in BLOCK_FIELDS:
        assert name in DERIVED_FIELDS, f"{name} is arithmetic, not a reported concept"
        assert name not in TAG_MAP, f"{name} must not answer itself from one tag"


def test_the_new_input_concepts_are_mapped_as_flows() -> None:
    expected = {
        "operating_cash_flow": (OCF,),
        "capex": (CAPEX,),
        "depreciation_amortization": (DA,),
        "income_tax": (TAX,),
        "pretax_income": (PRETAX,),
    }
    for name, tags in expected.items():
        assert TAG_MAP.get(name) == tags, f"{name} is not mapped yet"
        assert name in FLOW_FIELDS, f"{name} is a duration, not an instant"
        assert name not in INSTANT_FIELDS
        assert UOM_BY_FIELD.get(name) == "USD"


def test_pretax_income_is_mapped_but_never_as_sales() -> None:
    """The probe's must-not-add verdict stays pinned; the concept still answers ROIC."""

    assert TAG_MAP.get("pretax_income") == (PRETAX,)
    assert PRETAX in MUST_NOT_MAP, "pre-tax income is not a top line"
    assert PRETAX not in TAG_MAP["sales"]
    assert PRETAX not in TAG_MAP["net_income"]


def test_the_deep_fixture_fills_the_whole_block() -> None:
    _published(*BLOCK_FIELDS)
    fields = map_fundamentals(deep_facts(), price=100.0)

    assert fields["market_cap"] == pytest.approx(500_000_000_000)
    assert fields["enterprise_value"] == pytest.approx(505_000_000_000)
    assert fields["ev_sales"] == pytest.approx(505_000_000_000 / 400_000_000_000)
    assert fields["ev_ebitda"] == pytest.approx(505_000_000_000 / 140_000_000_000)
    assert fields["pfcf"] == pytest.approx(5.0)
    assert fields["roic"] == pytest.approx(90_000_000_000 / 95_000_000_000 * 100)
    assert fields["lt_debt_to_equity"] == pytest.approx(30_000_000_000 / 90_000_000_000)
    assert fields["eps_qoq"] == pytest.approx(10.0)
    assert fields["sales_qoq"] == pytest.approx((105_000_000_000 / 95_000_000_000 - 1) * 100)
    assert fields["eps_yoy_ttm"] == pytest.approx((8.0 / 7.0 - 1) * 100)
    assert fields["sales_yoy_ttm"] == pytest.approx((400_000_000_000 / 380_000_000_000 - 1) * 100)
    assert fields["payout_ratio"] == pytest.approx(25.0)


def test_the_valuation_block_is_absent_without_a_price() -> None:
    _published("enterprise_value", "ev_sales", "ev_ebitda", "pfcf")
    fields = map_fundamentals(deep_facts())
    for name in ("enterprise_value", "ev_sales", "ev_ebitda", "pfcf"):
        assert fields[name] is None, f"{name} stands on the market cap"
    assert fields["roic"] == pytest.approx(90_000_000_000 / 95_000_000_000 * 100), (
        "a balance-sheet ratio needs no quote"
    )


def test_ev_ebitda_is_absent_without_the_amortization_line() -> None:
    _published("ev_ebitda")
    rows = [row for row in deep_facts() if row.tag != DA]
    fields = map_fundamentals(rows, price=100.0)
    assert fields["enterprise_value"] == pytest.approx(505_000_000_000)
    assert fields["ev_ebitda"] is None, "EBITDA without the D&A line is half a denominator"


def test_pfcf_is_absent_without_the_capex_line() -> None:
    _published("pfcf")
    rows = [row for row in deep_facts() if row.tag != CAPEX]
    fields = map_fundamentals(rows, price=100.0)
    assert fields["pfcf"] is None, "free cash flow needs both halves"


def test_roic_uses_the_filings_own_tax_rate() -> None:
    _published("roic")
    fields = map_fundamentals(deep_facts())
    # 15 / 60 = 25%, so NOPAT is 90 on an invested capital of 30 + 90 − 25.
    assert fields["roic"] == pytest.approx(90_000_000_000 / 95_000_000_000 * 100)


def test_roic_falls_back_to_the_statutory_rate() -> None:
    _published("roic")
    rows = [row for row in deep_facts() if row.tag not in (TAX, PRETAX)]
    fields = map_fundamentals(rows)
    # No tax line: 21%, so NOPAT is 94.8 on the same invested capital.
    assert fields["roic"] == pytest.approx(94_800_000_000 / 95_000_000_000 * 100)


def test_roic_is_absent_without_an_operating_income() -> None:
    _published("roic")
    rows = [row for row in deep_facts() if row.tag != "OperatingIncomeLoss"]
    fields = map_fundamentals(rows)
    assert fields["roic"] is None, "no EBIT, no return on invested capital"
    assert fields["ev_ebitda"] is None


def test_qoq_growth_compares_the_two_newest_quarters() -> None:
    _published("eps_qoq", "sales_qoq")
    fields = map_fundamentals(deep_facts())
    assert fields["eps_qoq"] == pytest.approx(10.0)
    assert fields["sales_qoq"] == pytest.approx((105_000_000_000 / 95_000_000_000 - 1) * 100)


def test_qoq_growth_is_absent_with_one_quarter() -> None:
    _published("eps_qoq", "sales_qoq")
    rows = [row for row in deep_facts() if row.ddate != "2026-03-28"]
    fields = map_fundamentals(rows)
    assert fields["eps_qoq"] is None, "one quarter is no growth at all"
    assert fields["sales_qoq"] is None


def test_a_restated_quarter_is_one_period_not_two() -> None:
    _published("eps_qoq")
    rows = deep_facts() + [
        fact("EarningsPerShareDiluted", 2.25, ddate="2026-06-27", qtrs=1, adsh=AAPL_Q2R, filed="2026-08-14"),
    ]
    fields = map_fundamentals(rows)
    assert fields["eps_qoq"] == pytest.approx((2.25 / 2.00 - 1) * 100), (
        "the later-filed restatement of the newest quarter wins"
    )


def test_yoy_ttm_compares_the_two_trailing_twelve_months() -> None:
    _published("eps_yoy_ttm", "sales_yoy_ttm")
    fields = map_fundamentals(ttm_facts())
    assert fields["sales"] == pytest.approx(420_000_000_000)
    assert fields["eps"] == pytest.approx(8.50)
    assert fields["sales_yoy_ttm"] == pytest.approx((420_000_000_000 / 390_000_000_000 - 1) * 100)
    assert fields["eps_yoy_ttm"] == pytest.approx((8.50 / 7.70 - 1) * 100)


def test_yoy_ttm_is_absent_without_the_earlier_window() -> None:
    _published("eps_yoy_ttm", "sales_yoy_ttm")
    # Drop the FY2024 annual and its own nine-month comparative: the earlier
    # window has no base year left to roll forward from.
    rows = [row for row in ttm_facts() if row.ddate >= "2025-01-01"]
    fields = map_fundamentals(rows)
    assert fields["sales"] == pytest.approx(420_000_000_000), "the newest window is intact"
    assert fields["sales_yoy_ttm"] is None, "the earlier twelve months are not on file"
    assert fields["eps_yoy_ttm"] is None


def test_yoy_ttm_of_a_ten_k_filer_is_the_previous_annual() -> None:
    """The newest filing is the 10-K, so both windows are annual rows."""

    _published("sales_yoy_ttm")
    rows = [
        fact(REVENUE, 410_000_000_000, ddate="2026-09-26", qtrs=4, adsh=AAPL_K, filed="2026-10-30"),
        fact(REVENUE, 400_000_000_000, ddate="2025-09-27", qtrs=4),
        fact(REVENUE, 300_000_000_000, ddate="2026-06-27", qtrs=3, adsh=AAPL_Q, filed="2026-07-31"),
        fact(REVENUE, 280_000_000_000, ddate="2025-06-28", qtrs=3, adsh=AAPL_Q, filed="2025-08-01"),
        fact(REVENUE, 380_000_000_000, ddate="2024-09-28", qtrs=4, adsh=AAPL_K24, filed="2024-11-01"),
        fact(REVENUE, 270_000_000_000, ddate="2024-06-29", qtrs=3, adsh=AAPL_Q24, filed="2024-08-01"),
    ]
    fields = map_fundamentals(rows)
    assert fields["sales"] == pytest.approx(410_000_000_000)
    assert fields["sales_yoy_ttm"] == pytest.approx((410_000_000_000 / 400_000_000_000 - 1) * 100), (
        "the current year's nine-month rows sit inside the newest window and must not leak into the earlier one"
    )


def test_payout_ratio_is_the_annual_dividend_over_the_annual_eps() -> None:
    _published("payout_ratio")
    fields = map_fundamentals(deep_facts())
    assert fields["payout_ratio"] == pytest.approx(25.0)


def test_payout_ratio_is_absent_without_the_dividend() -> None:
    _published("payout_ratio")
    rows = [row for row in deep_facts() if row.tag != "CommonStockDividendsPerShareDeclared"]
    fields = map_fundamentals(rows)
    assert fields["payout_ratio"] is None, "a non-payer is absent, not a zero payout"


def test_payout_ratio_is_absent_without_an_eps() -> None:
    _published("payout_ratio")
    rows = [row for row in deep_facts() if row.tag != "EarningsPerShareDiluted"]
    fields = map_fundamentals(rows)
    assert fields["payout_ratio"] is None, "a loss-maker's payout is not a number"


def test_a_missing_input_is_never_a_zero() -> None:
    _published(*BLOCK_FIELDS)
    # A quote and a top line, and nothing else: every column that needs a
    # second input is absent, never zero.
    fields = map_fundamentals(
        [
            fact(REVENUE, 400_000_000_000),
            fact("CommonStockSharesOutstanding", 5_000_000_000, qtrs=0, uom="shares"),
        ],
        price=100.0,
    )
    assert fields["market_cap"] == pytest.approx(500_000_000_000)
    for name in BLOCK_FIELDS:
        assert fields[name] is None, f"{name} has no evidence and must not be invented"


def test_ev_sales_is_absent_without_a_top_line() -> None:
    _published("ev_sales")
    rows = [row for row in deep_facts() if row.tag != REVENUE]
    fields = map_fundamentals(rows, price=100.0)
    assert fields["enterprise_value"] == pytest.approx(505_000_000_000)
    assert fields["ev_sales"] is None, "an enterprise value with no sales is not a zero"


# ---------------------------------------------------------------------------
# Debt vocabulary: the lease-inclusive spellings
# ---------------------------------------------------------------------------
#
# AVGO files LongTermDebtAndCapitalLeaseObligations, TSLA the finance-lease
# pair, and neither reaches the LongTermDebt/LongTermDebtNoncurrent pair the
# map carried — so enterprise_value and debt_to_equity were absent for them.


def test_capital_lease_debt_spelling_fills_total_debt() -> None:
    rows = [
        fact("LongTermDebtAndCapitalLeaseObligations", 12_000_000_000, qtrs=0),
        fact("StockholdersEquity", 70_000_000_000, qtrs=0),
        fact("CashAndCashEquivalentsAtCarryingValue", 15_000_000_000, qtrs=0),
        fact("CommonStockSharesOutstanding", 1_000_000_000, qtrs=0, uom="shares"),
    ]
    fields = map_fundamentals(rows, price=100.0)
    assert fields["debt_to_equity"] == pytest.approx(12_000_000_000 / 70_000_000_000)
    assert fields["enterprise_value"] == pytest.approx(
        100_000_000_000 + 12_000_000_000 - 15_000_000_000
    )


def test_finance_lease_debt_spelling_fills_total_debt() -> None:
    rows = [
        fact("LongTermDebtAndFinanceLeasesNoncurrent", 5_000_000_000, qtrs=0),
        fact("StockholdersEquity", 30_000_000_000, qtrs=0),
    ]
    fields = map_fundamentals(rows)
    assert fields["debt_to_equity"] == pytest.approx(5_000_000_000 / 30_000_000_000)


def test_the_long_term_pair_still_wins_over_the_lease_spellings() -> None:
    """A filer with both keeps the plain long-term debt figure."""

    rows = [
        fact("LongTermDebtNoncurrent", 8_000_000_000, qtrs=0),
        fact("LongTermDebtAndCapitalLeaseObligations", 9_500_000_000, qtrs=0),
        fact("StockholdersEquity", 40_000_000_000, qtrs=0),
    ]
    fields = map_fundamentals(rows)
    assert fields["debt_to_equity"] == pytest.approx(8_000_000_000 / 40_000_000_000)


def test_the_disposal_group_cash_spelling_fills_cash() -> None:
    """TSLA files the disposal-group spelling; the plain tags miss it."""

    rows = [
        fact(
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations",
            20_000_000_000,
            qtrs=0,
        ),
        fact("LongTermDebtAndFinanceLeasesNoncurrent", 5_000_000_000, qtrs=0),
        fact("CommonStockSharesOutstanding", 1_000_000_000, qtrs=0, uom="shares"),
    ]
    fields = map_fundamentals(rows, price=100.0)
    assert fields["cash_per_share"] == pytest.approx(20.0)
    assert fields["enterprise_value"] == pytest.approx(
        100_000_000_000 + 5_000_000_000 - 20_000_000_000
    )


# ---------------------------------------------------------------------------
# Long-horizon growth: the multi-year CAGR block
# ---------------------------------------------------------------------------
#
# A 3- or 5-year compound annual growth rate wants the newest annual
# (``qtrs=4``) row and the annual whose period end sits ~3 / ~5 years
# earlier. The bulk dataset only carries four quarters at a time, so the
# caller merges the extra cached quarters (2024q1, 2021q1) into one fact
# list. Two 10-Ks can carry the same fiscal year — the newest as the
# current year, the next one as a comparative — so the annual rows are
# deduplicated by period end before any window is taken; a raw sort
# double-counts the restated figure.


#: The four long-horizon growth columns, in catalog order.
GROWTH_FIELDS = (
    "eps_past_3y",
    "sales_past_3y",
    "eps_past_5y",
    "sales_past_5y",
)


def _published_growth(*names: str) -> None:
    """Every name the growth block publishes; RED until the mapper carries it."""

    for name in names:
        assert name in FIELDS, f"{name} is not a published field yet"


def annual_facts() -> list[Fact]:
    """Five fiscal years of annual rows: FY2020 … FY2025, late-September ends.

    Sales grow from 150bn to 400bn; EPS from 4.00 to 8.00. Both the 3-year
    anchor (FY2022) and the 5-year anchor (FY2020) are on file.
    """

    return [
        fact(REVENUE, 400_000_000_000, ddate="2025-09-27", qtrs=4),
        fact(REVENUE, 380_000_000_000, ddate="2024-09-28", qtrs=4, adsh=AAPL_K24, filed="2024-11-01"),
        fact(REVENUE, 300_000_000_000, ddate="2023-09-30", qtrs=4, adsh=AAPL_K23, filed="2023-11-02"),
        fact(REVENUE, 240_000_000_000, ddate="2022-09-24", qtrs=4, adsh=AAPL_K22, filed="2022-10-28"),
        fact(REVENUE, 190_000_000_000, ddate="2021-09-25", qtrs=4, adsh=AAPL_K21, filed="2021-10-29"),
        fact(REVENUE, 150_000_000_000, ddate="2020-09-26", qtrs=4, adsh=AAPL_K20, filed="2020-10-30"),
        fact("EarningsPerShareDiluted", 8.00, ddate="2025-09-27", qtrs=4),
        fact("EarningsPerShareDiluted", 7.00, ddate="2024-09-28", qtrs=4, adsh=AAPL_K24, filed="2024-11-01"),
        fact("EarningsPerShareDiluted", 6.00, ddate="2023-09-30", qtrs=4, adsh=AAPL_K23, filed="2023-11-02"),
        fact("EarningsPerShareDiluted", 5.00, ddate="2022-09-24", qtrs=4, adsh=AAPL_K22, filed="2022-10-28"),
        fact("EarningsPerShareDiluted", 4.50, ddate="2021-09-25", qtrs=4, adsh=AAPL_K21, filed="2021-10-29"),
        fact("EarningsPerShareDiluted", 4.00, ddate="2020-09-26", qtrs=4, adsh=AAPL_K20, filed="2020-10-30"),
    ]


def test_the_growth_block_is_published_and_derived() -> None:
    _published_growth(*GROWTH_FIELDS)
    assert FIELDS[-len(GROWTH_FIELDS) :] == GROWTH_FIELDS, "the block closes the catalog order"
    for name in GROWTH_FIELDS:
        assert name in DERIVED_FIELDS, f"{name} is arithmetic, not a reported concept"
        assert name not in TAG_MAP, f"{name} must not answer itself from one tag"


def test_the_three_year_cagr_compares_the_fy_minus_three_annual_to_the_newest() -> None:
    """Sales 400bn against the FY2022 annual of 240bn: (400/240)^(1/3) − 1."""

    _published_growth("sales_past_3y", "eps_past_3y")
    fields = map_fundamentals(annual_facts())
    assert fields["sales_past_3y"] == pytest.approx(((400_000_000_000 / 240_000_000_000) ** (1 / 3) - 1) * 100)
    assert fields["eps_past_3y"] == pytest.approx(((8.00 / 5.00) ** (1 / 3) - 1) * 100)


def test_the_five_year_cagr_compares_the_fy_minus_five_annual_to_the_newest() -> None:
    """Sales 400bn against the FY2020 annual of 150bn: (400/150)^(1/5) − 1."""

    _published_growth("sales_past_5y", "eps_past_5y")
    fields = map_fundamentals(annual_facts())
    assert fields["sales_past_5y"] == pytest.approx(((400_000_000_000 / 150_000_000_000) ** (1 / 5) - 1) * 100)
    assert fields["eps_past_5y"] == pytest.approx(((8.00 / 4.00) ** (1 / 5) - 1) * 100)


def test_a_restated_annual_is_one_period_not_two() -> None:
    """Two 10-Ks carry FY2025 — the newest as the current year, the next as a
    comparative. The period end dedupes to one row, the later filing wins, and
    the CAGR reads 410 (the restatement), not 810 (the double-count)."""

    _published_growth("sales_past_3y")
    rows = [
        fact(REVENUE, 400_000_000_000, ddate="2025-09-27", qtrs=4, adsh=AAPL_K, filed="2025-10-31"),
        fact(REVENUE, 410_000_000_000, ddate="2025-09-27", qtrs=4, adsh=AAPL_K26, filed="2026-10-30"),
        fact(REVENUE, 240_000_000_000, ddate="2022-09-24", qtrs=4, adsh=AAPL_K22, filed="2022-10-28"),
    ]
    fields = map_fundamentals(rows)
    assert fields["sales"] == pytest.approx(410_000_000_000), "the later-filed restatement wins"
    assert fields["sales_past_3y"] == pytest.approx(((410_000_000_000 / 240_000_000_000) ** (1 / 3) - 1) * 100), (
        "one FY2025 value, not the two rows summed"
    )


def test_the_three_year_cagr_is_absent_without_the_fy_minus_three_anchor() -> None:
    _published_growth("sales_past_3y", "eps_past_3y")
    rows = [row for row in annual_facts() if row.ddate != "2022-09-24"]
    fields = map_fundamentals(rows)
    assert fields["sales"] == pytest.approx(400_000_000_000), "the newest annual is intact"
    assert fields["sales_past_3y"] is None, "no FY2022 annual, no 3-year CAGR — never a shorter window"
    assert fields["eps_past_3y"] is None


def test_the_five_year_cagr_is_absent_without_the_fy_minus_five_anchor() -> None:
    _published_growth("sales_past_5y", "eps_past_5y")
    rows = [row for row in annual_facts() if row.ddate != "2020-09-26"]
    fields = map_fundamentals(rows)
    assert fields["sales_past_5y"] is None, "no FY2020 annual, no 5-year CAGR"
    assert fields["eps_past_5y"] is None
    assert fields["sales_past_3y"] == pytest.approx(((400_000_000_000 / 240_000_000_000) ** (1 / 3) - 1) * 100), (
        "the 3-year window is untouched by the missing 5-year anchor"
    )


def test_a_zero_or_negative_base_year_makes_the_cagr_absent() -> None:
    """A CAGR from zero or a loss is not a number; the field is absent."""

    _published_growth("sales_past_5y", "eps_past_3y")
    rows = [
        fact(REVENUE, 400_000_000_000, ddate="2025-09-27", qtrs=4),
        fact(REVENUE, 0.0, ddate="2020-09-26", qtrs=4, adsh=AAPL_K20, filed="2020-10-30"),
        fact("EarningsPerShareDiluted", 8.00, ddate="2025-09-27", qtrs=4),
        fact("EarningsPerShareDiluted", -5.00, ddate="2022-09-24", qtrs=4, adsh=AAPL_K22, filed="2022-10-28"),
    ]
    fields = map_fundamentals(rows)
    assert fields["sales_past_5y"] is None, "a zero base is no growth rate"
    assert fields["eps_past_3y"] is None, "a negative base is no growth rate"


def test_the_growth_block_is_absent_with_a_single_annual() -> None:
    _published_growth(*GROWTH_FIELDS)
    fields = map_fundamentals([fact(REVENUE, 400_000_000_000)])
    for name in GROWTH_FIELDS:
        assert fields[name] is None, f"{name} needs an anchor year on file"


def test_the_growth_block_leaves_the_ttm_fields_untouched() -> None:
    """The long-horizon CAGR reads annual rows only; the TTM pair is unchanged."""

    _published_growth(*GROWTH_FIELDS)
    fields = map_fundamentals(ttm_facts())
    assert fields["sales"] == pytest.approx(420_000_000_000)
    assert fields["eps"] == pytest.approx(8.50)
    assert fields["sales_yoy_ttm"] == pytest.approx((420_000_000_000 / 390_000_000_000 - 1) * 100)
    assert fields["eps_yoy_ttm"] == pytest.approx((8.50 / 7.70 - 1) * 100)
    for name in GROWTH_FIELDS:
        assert fields[name] is None, "the merged quarters carry no FY-3 or FY-5 annual"


def test_a_negative_ratio_has_no_real_root() -> None:
    """A loss in the base year and a profit in the newest is a sign change,
    not a compound growth rate — Python would answer a complex number."""

    rows = [
        fact("EarningsPerShareDiluted", -2.0, ddate="2022-12-31", qtrs=4),
        fact("EarningsPerShareDiluted", 6.0, ddate="2025-12-31", qtrs=4),
    ]
    assert map_fundamentals(rows)["eps_past_3y"] is None


# ---------------------------------------------------------------------------
# The external share-count reference (multi-class filers)
# ---------------------------------------------------------------------------
#
# META, GOOGL and BRK.B file ``CommonStockSharesOutstanding`` ONLY as
# dimensional rows — ``ClassOfStock=CommonClassA``/``CommonClassB`` with
# ``EquityComponents=CommonStock`` — and never an undimensioned consolidated
# count. ``_selected`` drops every dimensional row, so the filing alone cannot
# price the issuer: no shares, no market cap, no enterprise value. The caller
# may pass the batch quote's ``sharesOutstanding`` as a *reference*, which
# fills the column only when the filing has nothing. A filed figure always
# wins; a reference never overrides the document.

#: The real axis/value spelling of a class-share row, as ``num.txt`` carries it.
CLASS_A_SEGMENTS = "ClassOfStock=CommonClassA; EquityComponents=CommonStock"
CLASS_B_SEGMENTS = "ClassOfStock=CommonClassB; EquityComponents=CommonStock"


def _map_with_shares(
    facts: list[Fact], *, shares: Any, price: float | None = None
) -> dict[str, float | None]:
    """``map_fundamentals(..., shares=...)``, failing RED when the keyword is absent.

    A mapper that has not grown the parameter raises ``TypeError``; surfacing
    that as an ``AssertionError`` keeps the RED phase about the missing
    feature rather than about an import.
    """

    try:
        return map_fundamentals(facts, price=price, shares=shares)
    except TypeError as exc:
        if "unexpected keyword argument" in str(exc):
            raise AssertionError(
                f"map_fundamentals does not accept shares= yet: {exc}"
            ) from exc
        raise


def multi_class_facts() -> list[Fact]:
    """A filer whose every share count is dimensional — the META/GOOGL shape."""

    return [
        fact(REVENUE, 164_501_000_000),
        fact("NetIncomeLoss", 62_360_000_000),
        fact("EarningsPerShareDiluted", 23.86),
        fact("Assets", 366_020_000_000, qtrs=0),
        fact("StockholdersEquity", 182_637_000_000, qtrs=0),
        fact("LongTermDebtNoncurrent", 28_000_000_000, qtrs=0),
        fact("CashAndCashEquivalentsAtCarryingValue", 30_000_000_000, qtrs=0),
        # Two classes, both dimensional: 2.2bn + 0.3bn = the 2.5bn the quote
        # reports. Neither row is a consolidated count, so neither may be
        # summed into one either — the reference is the only honest source.
        fact(
            "CommonStockSharesOutstanding",
            2_200_000_000,
            qtrs=0,
            uom="shares",
            segments=CLASS_A_SEGMENTS,
        ),
        fact(
            "CommonStockSharesOutstanding",
            300_000_000,
            qtrs=0,
            uom="shares",
            segments=CLASS_B_SEGMENTS,
        ),
    ]


def test_a_multi_class_filer_cannot_price_itself_from_its_filing() -> None:
    """The gap under repair: no consolidated count, so no market cap, no EV."""

    fields = map_fundamentals(multi_class_facts(), price=500.0)
    assert fields["shares_outstanding"] is None, (
        "a class-share row is a segment, not the consolidated count"
    )
    assert fields["market_cap"] is None
    assert fields["enterprise_value"] is None


def test_the_share_reference_fills_market_cap_and_enterprise_value() -> None:
    """The batch quote's count stands in when the filing has none."""

    fields = _map_with_shares(multi_class_facts(), price=500.0, shares=2_500_000_000)
    assert fields["shares_outstanding"] == pytest.approx(2_500_000_000)
    assert fields["market_cap"] == pytest.approx(500.0 * 2_500_000_000)
    assert fields["enterprise_value"] == pytest.approx(
        500.0 * 2_500_000_000 + 28_000_000_000 - 30_000_000_000
    ), "market cap plus the long-term debt line, less cash"
    assert fields["ev_sales"] == pytest.approx(
        (500.0 * 2_500_000_000 + 28_000_000_000 - 30_000_000_000) / 164_501_000_000
    )
    assert fields["book_per_share"] == pytest.approx(182_637_000_000 / 2_500_000_000)
    assert fields["cash_per_share"] == pytest.approx(30_000_000_000 / 2_500_000_000)
    assert fields["pe"] == pytest.approx(500.0 / 23.86)


def test_a_filed_share_count_wins_over_the_reference() -> None:
    """A filed figure is never overridden by the outside reference."""

    fields = _map_with_shares(apple_facts(), price=250.0, shares=1_000_000.0)
    assert fields["shares_outstanding"] == pytest.approx(14_800_000_000), (
        "the 10-K's consolidated count, not the 1m reference"
    )
    assert fields["market_cap"] == pytest.approx(250.0 * 14_800_000_000)
    assert fields["book_per_share"] == pytest.approx(73_000_000_000 / 14_800_000_000)


def test_a_share_reference_alone_does_not_touch_a_filed_count() -> None:
    """No price, no market cap — but the filed count is still the filed count."""

    fields = _map_with_shares(apple_facts(), shares=1.0)
    assert fields["shares_outstanding"] == pytest.approx(14_800_000_000)
    assert fields["market_cap"] is None, "a count without a quote is not a market cap"


@pytest.mark.parametrize("shares", [0, -5.0, float("nan"), float("inf"), True])
def test_a_share_reference_that_is_not_a_real_count_is_refused(shares: float) -> None:
    """A quote-less count must be a positive finite number — even when unused."""

    with pytest.raises(ValueError):
        _map_with_shares(multi_class_facts(), price=500.0, shares=shares)
    with pytest.raises(ValueError):
        # Apple's fixture carries a filed count: the reference is still checked.
        _map_with_shares(apple_facts(), price=250.0, shares=shares)


@pytest.mark.parametrize("shares", ["2500000000", object()])
def test_a_share_reference_that_is_not_a_number_is_refused(shares: object) -> None:
    with pytest.raises(TypeError):
        _map_with_shares(multi_class_facts(), price=500.0, shares=shares)


def test_the_cik_entry_points_forward_the_share_reference() -> None:
    """``fundamentals_for_cik``/``_symbol`` take the same fallback."""

    index = _index_of(multi_class_facts())
    expected_ev = 500.0 * 2_500_000_000 + 28_000_000_000 - 30_000_000_000

    for call in (
        lambda: fundamentals_for_cik(index, AAPL_CIK, price=500.0, shares=2_500_000_000),
        lambda: fundamentals_for_symbol(
            index, "aapl", {"AAPL": AAPL_CIK}, price=500.0, shares=2_500_000_000
        ),
    ):
        try:
            fields = call()
        except TypeError as exc:
            if "unexpected keyword argument" in str(exc):
                raise AssertionError(f"the entry point ignores shares=: {exc}") from exc
            raise
        assert fields["enterprise_value"] == pytest.approx(expected_ev)
        assert fields["shares_outstanding"] == pytest.approx(2_500_000_000)


def test_the_mapped_concept_set_is_derived_from_the_tag_map() -> None:
    """The index filter is the mapper's own vocabulary, never a second list.

    ``MAPPED_CONCEPTS`` decides which XBRL concepts the bulk depth index keeps,
    so a tag that is in ``TAG_MAP`` but missing from the set would be filtered
    out of the index and the field it answers would silently read as absent —
    the exact failure mode a hand-written list produces.
    """

    expected = frozenset(tag for tags in TAG_MAP.values() for tag in tags)
    assert MAPPED_CONCEPTS == expected
    assert MAPPED_CONCEPTS, "an empty filter would build an index with no facts"
    # A concept the mapper refuses must not buy memory in the index either.
    assert not (MAPPED_CONCEPTS & frozenset(MUST_NOT_MAP.values()))
