"""Contract for cross-source reconciliation (``app.engine.reconcile``).

Every test here is about one question: when two adapters disagree, does the
engine say so? A reconciler that quietly picks a winner is worse than no
reconciler, because the caller can no longer tell a contested number from a
settled one.
"""

from __future__ import annotations

import pytest

from app.engine.model import Cell, CellStatus, Provenance, Unit
from app.engine.reconcile import ReconcileError, ReconcileResult, choose


def cell(
    value: object,
    *,
    source: str = "sec",
    as_of: str = "2026-10-24",
    unit: Unit = Unit.USD,
    status: CellStatus = CellStatus.OK,
    url: str | None = None,
    inputs: tuple[str, ...] = (),
) -> Cell:
    return Cell(
        value=value,
        unit=unit,
        provenance=Provenance(
            source=source,
            as_of=as_of,
            status=status,
            url=url,
            inputs=inputs,
        ),
    )


def missing(
    *,
    source: str = "sec",
    as_of: str = "2026-10-24",
    unit: Unit = Unit.USD,
    status: CellStatus = CellStatus.ABSENT,
) -> Cell:
    return Cell(
        value=None,
        unit=unit,
        provenance=Provenance(source=source, as_of=as_of, status=status),
    )


def test_choose_prefers_the_highest_priority_source_that_has_a_value() -> None:
    vendor = cell(10.0, source="vendor")
    sec = cell(10.5, source="sec")

    result = choose(
        "market_cap",
        [("vendor", vendor), ("sec", sec)],
        priority=("sec", "vendor"),
    )

    assert isinstance(result, ReconcileResult)
    assert result.cell is sec
    assert result.cell.value == 10.5
    assert result.cell.provenance is not None
    assert result.cell.provenance.source == "sec"
    assert result.agreement is False
    assert result.conflict is True
    assert result.sources == ("vendor", "sec")


def test_priority_falls_through_a_source_that_only_has_an_absence() -> None:
    absent = missing(source="sec", status=CellStatus.NOT_REPORTED)
    finviz = cell(12.0, source="finviz")

    result = choose(
        "revenue",
        [("sec", absent), ("finviz", finviz)],
        priority=("sec", "finviz"),
    )

    assert result.cell is finviz
    assert result.cell.value == 12.0
    assert result.cell.provenance is not None
    assert result.cell.provenance.source == "finviz"
    assert result.agreement is True
    assert result.conflict is False
    assert result.delta == pytest.approx(0.0)


def test_agreement_holds_when_values_are_equal_within_tolerance() -> None:
    left = cell(10.0, source="sec")
    right = cell(10.0 + 5e-10, source="vendor")

    result = choose(
        "eps",
        [("sec", left), ("vendor", right)],
        priority=("sec", "vendor"),
    )

    assert result.cell is left
    assert result.agreement is True
    assert result.conflict is False
    assert result.delta == pytest.approx(5e-10, abs=1e-12)


def test_tolerance_is_an_explicit_boundary_not_a_vibe() -> None:
    left = cell(1.0, source="sec")
    right = cell(1.0 + 1e-6, source="vendor")

    strict = choose("pe", [("sec", left), ("vendor", right)], priority=("sec",), tolerance=1e-9)
    loose = choose("pe", [("sec", left), ("vendor", right)], priority=("sec",), tolerance=1e-5)

    assert strict.conflict is True
    assert strict.agreement is False
    assert loose.conflict is False
    assert loose.agreement is True


def test_conflict_still_returns_the_priority_value_instead_of_average() -> None:
    sec = cell(10.0, source="sec")
    vendor = cell(14.0, source="vendor")

    result = choose(
        "book_value",
        [("vendor", vendor), ("sec", sec)],
        priority=("sec", "vendor"),
    )

    assert result.cell is sec
    assert result.cell.value == 10.0
    assert result.conflict is True
    assert result.agreement is False
    assert result.delta == pytest.approx(4.0)


def test_delta_is_the_spread_of_absolute_magnitudes() -> None:
    short = cell(-5.0, source="vendor")
    long = cell(3.0, source="sec")

    result = choose(
        "net_income",
        [("vendor", short), ("sec", long)],
        priority=("sec", "vendor"),
    )

    assert result.cell is long
    assert result.conflict is True
    assert result.delta == pytest.approx(2.0)


def test_a_single_observation_agrees_with_itself() -> None:
    only = cell(42.0, source="sec")

    result = choose("shares_out", [("sec", only)], priority=("sec",))

    assert result.cell is only
    assert result.agreement is True
    assert result.conflict is False
    assert result.delta == pytest.approx(0.0)
    assert result.sources == ("sec",)


def test_all_absent_returns_a_null_cell_with_the_first_provenance() -> None:
    first = missing(source="sec", as_of="2026-10-24", status=CellStatus.NOT_REPORTED)
    second = missing(source="finviz", as_of="2026-10-23", status=CellStatus.NO_ESTIMATE)

    result = choose(
        "ebitda",
        [("sec", first), ("finviz", second)],
        priority=("sec", "finviz"),
    )

    assert result.cell.value is None
    assert result.cell.provenance is not None
    assert result.cell.provenance.status is CellStatus.ABSENT
    assert result.cell.provenance.source == "sec"
    assert result.cell.provenance.as_of == "2026-10-24"
    assert result.agreement is False
    assert result.conflict is False
    assert result.delta is None
    assert result.sources == ("sec", "finviz")


def test_no_observations_yields_an_honest_unknown_provenance() -> None:
    result = choose("ebitda", [], priority=("sec",))

    assert result.cell.value is None
    assert result.cell.unit is Unit.UNKNOWN
    assert result.cell.provenance is not None
    assert result.cell.provenance.source == "none"
    assert result.cell.provenance.as_of == "unknown"
    assert result.cell.provenance.status is CellStatus.ABSENT
    assert result.sources == ()
    assert result.agreement is False
    assert result.conflict is False
    assert result.delta is None


def test_unit_mismatch_degrades_to_a_null_cell_instead_of_raising() -> None:
    usd = cell(1_000_000.0, source="sec", unit=Unit.USD)
    shares = cell(5_000.0, source="vendor", unit=Unit.SHARES)

    result = choose(
        "market_cap",
        [("sec", usd), ("vendor", shares)],
        priority=("sec", "vendor"),
    )

    assert result.cell.value is None
    assert result.cell.provenance is not None
    assert result.cell.provenance.status is CellStatus.UNIT_MISMATCH
    assert result.cell.provenance.source == "sec"
    assert result.cell.provenance.as_of == "2026-10-24"
    assert result.agreement is False
    assert result.conflict is True
    assert result.delta is None
    assert result.sources == ("sec", "vendor")


def test_unit_mismatch_is_measured_on_valued_cells_only() -> None:
    absent = missing(source="vendor", unit=Unit.SHARES)

    result = choose(
        "market_cap",
        [("vendor", absent), ("sec", cell(10.0, source="sec", unit=Unit.USD))],
        priority=("vendor", "sec"),
    )

    assert result.cell.value == 10.0
    assert result.cell.unit is Unit.USD
    assert result.conflict is False


def test_empty_priority_is_rejected() -> None:
    with pytest.raises(ReconcileError, match="priority"):
        choose("market_cap", [("sec", cell(1.0))], priority=())


def test_blank_priority_source_is_rejected() -> None:
    with pytest.raises(ReconcileError, match="blank"):
        choose("market_cap", [("sec", cell(1.0))], priority=("sec", "   "))


def test_a_bare_string_is_not_accepted_as_a_priority_sequence() -> None:
    with pytest.raises(ReconcileError, match="sequence"):
        choose("market_cap", [("sec", cell(1.0))], priority="sec")  # type: ignore[arg-type]


def test_priority_names_are_matched_trimmed_and_case_insensitively() -> None:
    sec = cell(7.0, source="sec")

    result = choose("pe", [("sec", sec)], priority=("  SEC  ",))

    assert result.cell is sec


def test_text_values_compare_exactly_and_carry_no_delta() -> None:
    left = cell("Technology", source="sec", unit=Unit.TEXT)
    right = cell("Technology", source="vendor", unit=Unit.TEXT)

    agreed = choose(
        "sector",
        [("sec", left), ("vendor", right)],
        priority=("sec", "vendor"),
    )

    assert agreed.cell is left
    assert agreed.agreement is True
    assert agreed.conflict is False
    assert agreed.delta is None

    other = cell("Healthcare", source="vendor", unit=Unit.TEXT)
    disputed = choose(
        "sector",
        [("sec", left), ("vendor", other)],
        priority=("sec", "vendor"),
    )

    assert disputed.cell is left
    assert disputed.cell.value == "Technology"
    assert disputed.agreement is False
    assert disputed.conflict is True
    assert disputed.delta is None


def test_booleans_are_not_numbers() -> None:
    yes = cell(True, source="sec", unit=Unit.TEXT)
    no = cell(False, source="vendor", unit=Unit.TEXT)

    result = choose(
        "is_reit",
        [("sec", yes), ("vendor", no)],
        priority=("sec", "vendor"),
    )

    assert result.conflict is True
    assert result.delta is None


def test_a_boolean_does_not_agree_with_the_number_one() -> None:
    result = choose(
        "is_reit",
        [("sec", cell(True, source="sec", unit=Unit.TEXT)), ("vendor", cell(1.0, source="vendor"))],
        priority=("sec", "vendor"),
    )

    assert result.conflict is True
    assert result.delta is None


def test_the_chosen_provenance_is_preserved_exactly() -> None:
    derived = cell(
        4.2,
        source="vendor",
        as_of="2026-10-24T18:05:00Z",
        unit=Unit.RATIO,
        status=CellStatus.DERIVED,
        url="https://example.test/vendor/pe",
        inputs=("price.close", "eps.ttm"),
    )

    result = choose("pe", [("vendor", derived)], priority=("vendor",))

    assert result.cell is derived
    assert result.cell.provenance is derived.provenance
    assert result.cell.provenance == Provenance(
        source="vendor",
        as_of="2026-10-24T18:05:00Z",
        status=CellStatus.DERIVED,
        url="https://example.test/vendor/pe",
        inputs=("price.close", "eps.ttm"),
    )


def test_sources_are_deduped_in_supplied_order() -> None:
    result = choose(
        "revenue",
        [
            ("vendor", cell(1.0, source="vendor")),
            ("sec", cell(1.0, source="sec")),
            ("vendor", cell(2.0, source="vendor")),
        ],
        priority=("sec",),
    )

    assert result.sources == ("vendor", "sec")
    assert result.cell.value == 1.0
    assert result.conflict is True


def test_missing_values_are_never_replaced_with_zero() -> None:
    result = choose(
        "dividend",
        [("sec", missing(source="sec")), ("vendor", missing(source="vendor"))],
        priority=("sec", "vendor"),
    )

    assert result.cell.value is None
    assert result.cell.value != 0

    filled = choose(
        "dividend",
        [("sec", missing(source="sec")), ("vendor", cell(0.25, source="vendor"))],
        priority=("sec", "vendor"),
    )

    assert filled.cell.value == 0.25
    assert filled.cell.provenance is not None
    assert filled.cell.provenance.source == "vendor"


def test_an_unranked_source_is_used_rather_than_dropping_available_evidence() -> None:
    vendor = cell(9.0, source="vendor")

    result = choose(
        "revenue",
        [("vendor", vendor), ("sec", missing(source="sec"))],
        priority=("sec",),
    )

    assert result.cell is vendor
    assert result.sources == ("vendor", "sec")
