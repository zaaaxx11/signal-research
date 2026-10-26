from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.engine.facts import fact_summary, fact_ttm_summary, parse_companyfacts

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "sec_companyfacts_aapl.json"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_ttm_uses_annual_plus_ytd_minus_prior_ytd() -> None:
    cells = fact_ttm_summary(_payload())

    # FY2025 (416.161B) + 9M FY2026 (364.357B) - 9M FY2025 (313.695B).
    assert cells["revenue"].value == pytest.approx(466_823_000_000.0)
    assert cells["net_income"].value == pytest.approx(128_930_000_000.0)
    assert cells["operating_cash_flow"].value == pytest.approx(146_724_000_000.0)
    assert cells["capex"].value == pytest.approx(10_041_000_000.0)


def test_ttm_is_dated_to_the_current_period_and_keeps_the_formula_trail() -> None:
    cells = fact_ttm_summary(_payload())
    revenue = cells["revenue"]

    assert revenue.provenance is not None
    assert revenue.provenance.as_of == "2026-06-27"
    assert revenue.provenance.source == "sec"
    assert len(revenue.provenance.inputs) == 6, (
        "TTM provenance must name the annual, current and prior tag/accession pairs"
    )
    assert revenue.provenance.inputs[1] == "0000320193-25-000079"
    assert revenue.provenance.inputs[3] == "0000320193-26-000020"


def test_instant_metrics_keep_the_latest_observation() -> None:
    cells = fact_ttm_summary(_payload())
    equity = cells["equity"]

    assert equity.value == pytest.approx(107_520_000_000.0)
    assert equity.provenance is not None
    assert equity.provenance.as_of == "2026-06-27"
    assert equity.provenance.inputs == ("StockholdersEquity", "0000320193-26-000020")


def test_fact_summary_stays_the_latest_filing_while_ttm_is_the_run_rate() -> None:
    latest = fact_summary(_payload())["revenue"]
    ttm = fact_ttm_summary(_payload())["revenue"]

    assert latest.value == pytest.approx(364_357_000_000.0)
    assert ttm.value == pytest.approx(466_823_000_000.0)
    assert latest.value != ttm.value, (
        "the latest 9M YTD filing is not the trailing-twelve-month value"
    )


def test_ttm_omits_a_metric_it_cannot_build_instead_of_zero() -> None:
    payload = {"facts": {"us-gaap": {"Assets": {"units": {"USD": [
        {"end": "2026-06-27", "val": 1, "accn": "a", "filed": "2026-07-31"}
    ]}}}}}

    cells = fact_ttm_summary(payload)

    assert "revenue" not in cells
    assert cells["assets"].value == pytest.approx(1.0)


def test_ttm_parser_keeps_quarter_and_ytd_rows_distinct() -> None:
    observations = parse_companyfacts(_payload())
    revenue = [
        item
        for item in observations
        if item.tag == "RevenueFromContractWithCustomerExcludingAssessedTax"
        and item.period_end == "2026-06-27"
        and item.accession == "0000320193-26-000020"
    ]

    assert len(revenue) == 2
    assert {item.start for item in revenue} == {"2025-09-28", "2026-03-29"}
