"""Contract for SEC-vs-Finviz reconciliation of one metric (``app.engine.reconcile``).

The engine's rule is that a disagreement is reported, never smoothed away. This
file proves it on the pair that matters in production: the SEC trailing-twelve-
month figure parsed out of ``companyfacts`` and a frozen vendor ("Finviz") print
of the same metric.

Everything here is offline and dateless-in-the-wall-clock sense:

* the SEC side is the real trimmed Apple ``companyfacts`` document that
  ``tests/test_engine_facts.py`` parses — no HTTP;
* the Finviz side is a **frozen literal** captured once, so the test asserts a
  number instead of whatever the vendor prints today;
* every cell carries a fixed ``as_of``; nothing reads ``datetime.now()``.

What the assertions pin down:

1. ``priority`` decides, not supply order — the SEC cell wins under
   ``("sec", "finviz")`` and the Finviz cell wins under the reverse;
2. a gap inside ``tolerance`` is agreement with a small ``delta``;
3. a moved vendor value is a conflict with a ``delta`` equal to the move, and
   the SEC cell is still the one returned;
4. the returned cell is the *same object* the caller supplied: reconciliation
   never rewrites the winner's provenance.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.engine.facts import fact_ttm_summary
from app.engine.model import Cell, CellStatus, Provenance, Unit
from app.engine.reconcile import ReconcileResult, choose

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "sec_companyfacts_aapl.json"

#: The SEC TTM sales figure the fixture produces, in absolute USD. Spelled out
#: so a parser change that silently starts reporting the nine-month YTD
#: (364,357,000,000) or the FY2025 annual (416,161,000,000) fails here.
SEC_SALES_TTM = 466_823_000_000.0

#: The period the SEC number describes. A TTM cell is dated to the current YTD
#: end, not to the filing date (2026-07-31).
SEC_AS_OF = "2026-06-27"

#: A frozen Finviz print of Apple sales. Captured once and never refreshed: a
#: test that follows the vendor is a test that cannot fail for the right reason.
FROZEN_FINVIZ_SALES = 466_823_000_000.0

#: The same vendor print as it appears after Finviz rounds to its own precision:
#: three million dollars under the filing, i.e. a rounding artefact rather than a
#: disagreement.
ROUNDED_FINVIZ_SALES = 466_820_000_000.0

#: The vendor figure after a visible revision — nine billion dollars above the
#: filing, far outside any rounding tolerance.
SHIFTED_FINVIZ_SALES = 475_823_000_000.0

#: The vendor's own "as of" stamp, frozen with the value.
FROZEN_AS_OF = "2026-07-31"

#: A tolerance a fundamentals desk would accept on a 467-billion-dollar figure
#: (about one percent), passed explicitly so the boundary is part of the test.
TOLERANCE = 5_000_000_000.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def facts() -> dict[str, Any]:
    """A fresh copy of the trimmed live ``companyfacts`` document for Apple."""

    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def sec_sales_cell() -> Cell:
    """The SEC TTM sales cell for AAPL, straight out of the filing parser."""

    summary = fact_ttm_summary(facts())

    assert "revenue" in summary, (
        "the Apple document reports revenue, so the facts slice must produce a "
        f"cell for it: {sorted(summary)}"
    )
    return summary["revenue"]


def vendor_cell(value: float, *, source: str = "finviz") -> Cell:
    """One frozen vendor observation of sales, in USD, at a fixed date."""

    return Cell(
        value=value,
        unit=Unit.USD,
        provenance=Provenance(
            source=source,
            as_of=FROZEN_AS_OF,
            status=CellStatus.OK,
            url=None,
            inputs=("Sales",),
        ),
    )


def reconcile(
    vendor_value: float, *, priority: tuple[str, ...] = ("sec", "finviz")
) -> ReconcileResult:
    """Reconcile the SEC TTM sales cell against a frozen vendor print."""

    return choose(
        "sales",
        [("sec", sec_sales_cell()), ("finviz", vendor_cell(vendor_value))],
        priority=priority,
        tolerance=TOLERANCE,
    )


# ---------------------------------------------------------------------------
# The SEC side is the TTM cell, not a period figure
# ---------------------------------------------------------------------------


def test_the_sec_side_of_the_comparison_is_the_ttm_cell() -> None:
    """If the SEC side were a quarter or a year, the comparison would be apples to oranges."""

    cell = sec_sales_cell()

    assert cell.value == pytest.approx(SEC_SALES_TTM), (
        "the reconciled SEC value must be annual + YTD - prior YTD; "
        f"{cell.value!r} is a period figure, not a run-rate"
    )
    assert cell.unit is Unit.USD
    assert cell.provenance is not None
    assert cell.provenance.source == "sec"
    assert cell.provenance.as_of == SEC_AS_OF, (
        f"the TTM cell is dated to the period end it reaches: {cell.provenance.as_of!r}"
    )
    assert len(cell.provenance.inputs) == 6, (
        "a TTM number built from three filings must keep all three accessions "
        f"in provenance: {cell.provenance.inputs!r}"
    )


# ---------------------------------------------------------------------------
# Priority decides
# ---------------------------------------------------------------------------


def test_priority_chooses_the_sec_cell_when_both_sources_agree() -> None:
    """Two sources, one number: the ranked source supplies the returned cell."""

    sec = sec_sales_cell()
    result = choose(
        "sales",
        [("sec", sec), ("finviz", vendor_cell(FROZEN_FINVIZ_SALES))],
        priority=("sec", "finviz"),
        tolerance=TOLERANCE,
    )

    assert result.cell is sec, "the reconciler must return the supplied cell untouched"
    assert result.cell.provenance is not None
    assert result.cell.provenance.source == "sec"
    assert result.cell.provenance.as_of == SEC_AS_OF
    assert result.cell.value == pytest.approx(SEC_SALES_TTM)
    assert result.sources == ("sec", "finviz")
    assert result.agreement is True
    assert result.conflict is False
    assert result.delta == pytest.approx(0.0)


def test_the_reverse_priority_chooses_the_vendor_cell() -> None:
    """Priority is a real ranking, not a decoration on supply order."""

    result = reconcile(FROZEN_FINVIZ_SALES, priority=("finviz", "sec"))

    assert result.cell.provenance is not None
    assert result.cell.provenance.source == "finviz"
    assert result.cell.provenance.as_of == FROZEN_AS_OF, (
        "the vendor's own stamp must survive reconciliation: the engine never "
        "re-dates a value it did not produce"
    )
    assert result.cell.value == pytest.approx(FROZEN_FINVIZ_SALES)
    assert result.agreement is True
    assert result.conflict is False


# ---------------------------------------------------------------------------
# Agreement, rounding and conflict
# ---------------------------------------------------------------------------


def test_a_rounding_gap_inside_tolerance_is_agreement_with_a_small_delta() -> None:
    """Finviz prints to its own precision; three million on 467 billion is noise."""

    result = reconcile(ROUNDED_FINVIZ_SALES)

    assert result.agreement is True
    assert result.conflict is False
    assert result.delta == pytest.approx(3_000_000.0), (
        "delta is the spread of the two values; it must stay visible even when "
        f"the sources agree: {result.delta!r}"
    )
    assert result.delta is not None and result.delta < TOLERANCE
    assert result.cell.provenance is not None
    assert result.cell.provenance.source == "sec", (
        "rounding agreement must not hand the cell to the lower-ranked source"
    )


def test_a_moved_vendor_value_is_a_conflict_with_a_delta_equal_to_the_move() -> None:
    """A nine-billion revision is a dispute, and the engine reports it as one."""

    result = reconcile(SHIFTED_FINVIZ_SALES)

    assert result.agreement is False
    assert result.conflict is True, (
        "the two sources differ by nine billion dollars; silence here would be "
        "the engine hiding a disagreement"
    )
    assert result.delta == pytest.approx(9_000_000_000.0)
    assert result.delta is not None and result.delta > TOLERANCE
    assert result.cell.provenance is not None
    assert result.cell.provenance.source == "sec", (
        "a conflict still returns the priority source's cell, not an average "
        f"and not the challenger: {result.cell.provenance.source!r}"
    )
    assert result.cell.value == pytest.approx(SEC_SALES_TTM), (
        "the returned value is the filing's, unchanged by the dispute"
    )
    assert result.cell.provenance.as_of == SEC_AS_OF


def test_the_conflict_never_rewrites_the_sec_provenance() -> None:
    """The dispute is metadata on the result, not a mutation of the evidence."""

    sec = sec_sales_cell()
    before = sec.provenance

    result = choose(
        "sales",
        [("sec", sec), ("finviz", vendor_cell(SHIFTED_FINVIZ_SALES))],
        priority=("sec", "finviz"),
        tolerance=TOLERANCE,
    )

    assert before is not None
    assert result.conflict is True
    assert result.cell is sec
    assert result.cell.provenance is before
    assert before.source == "sec"
    assert before.as_of == SEC_AS_OF
    # The annual 10-K is FY2025; the latest 10-Q re-files *both* YTD periods
    # (current and prior-year) under its own accession, which is why the prior
    # leg is not the 2025 10-Q's accession.
    assert before.inputs == (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "0000320193-25-000079",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "0000320193-26-000020",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "0000320193-26-000020",
    ), "the audit trail of the winner must be exactly what the parser wrote"


def test_tolerance_is_the_boundary_between_agreement_and_conflict() -> None:
    """The same pair of numbers is a dispute under a strict tolerance and not under a loose one."""

    gap = 3_000_000.0

    strict = choose(
        "sales",
        [("sec", sec_sales_cell()), ("finviz", vendor_cell(SEC_SALES_TTM + gap))],
        priority=("sec", "finviz"),
        tolerance=1.0,
    )
    loose = choose(
        "sales",
        [("sec", sec_sales_cell()), ("finviz", vendor_cell(SEC_SALES_TTM + gap))],
        priority=("sec", "finviz"),
        tolerance=TOLERANCE,
    )

    assert strict.conflict is True
    assert strict.agreement is False
    assert strict.delta == pytest.approx(gap)
    assert loose.conflict is False
    assert loose.agreement is True
    assert loose.delta == pytest.approx(gap)
