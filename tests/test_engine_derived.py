"""Contract for auditable derived metrics (``app.engine.derived``).

Every test here asks one question in a different way: given a
:class:`~app.engine.derived.DerivedValue`, can a reader tell exactly which
numbers produced it, and — when there is no number — exactly why there is none?

A derivation that returns ``0.0`` for a missing input, or a plausible multiple
for a negative denominator, is indistinguishable from a measurement. That is the
failure this module exists to prevent, so the tests below pin the reason
strings, the input trail and the no-zero-fallback rule as hard as they pin the
arithmetic.
"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from app.engine.derived import (
    ASSUMED_ZERO,
    FORMULAS,
    MISSING_INPUT,
    NEGATIVE_DENOMINATOR,
    NON_POSITIVE_INPUT,
    OVERFLOW,
    REASONS,
    WRONG_QUARTER_COUNT,
    ZERO_DENOMINATOR,
    DerivedValue,
    earnings_yield,
    enterprise_value,
    fcf,
    forward_pe,
    market_cap,
    pe_ratio,
    peg,
    price_to_book,
    price_to_sales,
    safe_ratio,
    ttm_sum,
)

# ---------------------------------------------------------------------------
# DerivedValue: the audit record itself
# ---------------------------------------------------------------------------


def test_derived_value_normalises_its_key_and_keeps_its_audit_trail() -> None:
    result = DerivedValue(
        key="  PE ",
        value=12.5,
        inputs=("price", "eps_ttm"),
        reason=None,
    )

    assert result.key == "pe"
    assert result.value == 12.5
    assert result.inputs == ("price", "eps_ttm")
    assert result.reason is None
    assert result.ok is True


def test_derived_value_accepts_an_integral_value_as_a_float() -> None:
    result = DerivedValue(key="pe", value=12, inputs=("price",), reason=None)

    assert isinstance(result.value, float)
    assert result.value == 12.0


def test_a_null_derived_value_must_carry_a_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        DerivedValue(key="pe", value=None, inputs=("price",), reason=None)


def test_a_null_derived_value_cannot_claim_an_assumption() -> None:
    with pytest.raises(ValueError, match="assumed_zero"):
        DerivedValue(key="pe", value=None, inputs=("price",), reason=ASSUMED_ZERO)


def test_a_present_derived_value_cannot_carry_a_missing_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        DerivedValue(
            key="pe",
            value=12.5,
            inputs=("price", "eps_ttm"),
            reason=MISSING_INPUT,
        )


def test_a_present_derived_value_may_carry_the_assumed_zero_caveat() -> None:
    result = DerivedValue(
        key="enterprise_value",
        value=115.0,
        inputs=("market_cap", "total_debt", "cash", "preferred", "minority"),
        reason=ASSUMED_ZERO,
    )

    assert result.ok is True
    assert result.assumed is True


def test_a_derived_value_must_be_finite() -> None:
    with pytest.raises(ValueError, match="finite"):
        DerivedValue(key="pe", value=math.inf, inputs=("price",), reason=None)


def test_a_derived_value_must_name_at_least_one_input() -> None:
    with pytest.raises(ValueError, match="input"):
        DerivedValue(key="pe", value=1.0, inputs=(), reason=None)


def test_a_derived_value_rejects_a_blank_input_name() -> None:
    with pytest.raises(ValueError, match="input"):
        DerivedValue(key="pe", value=1.0, inputs=("price", " "), reason=None)


def test_a_derived_value_rejects_duplicate_inputs() -> None:
    with pytest.raises(ValueError, match="input"):
        DerivedValue(key="pe", value=1.0, inputs=("price", "PRICE"), reason=None)


def test_a_derived_value_requires_a_key() -> None:
    with pytest.raises(ValueError, match="key"):
        DerivedValue(key=" ", value=1.0, inputs=("price",), reason=None)


# ---------------------------------------------------------------------------
# safe_ratio: the one place the denominator rule lives
# ---------------------------------------------------------------------------


def test_safe_ratio_divides_and_names_its_inputs() -> None:
    result = safe_ratio(
        10.0, 4.0, key="pe", inputs=("price", "eps_ttm")
    )

    assert result.key == "pe"
    assert result.value == pytest.approx(2.5)
    assert result.inputs == ("price", "eps_ttm")
    assert result.reason is None


def test_safe_ratio_preserves_a_negative_numerator() -> None:
    """Only the denominator is policed: a negative numerator is information."""

    result = safe_ratio(-6.0, 3.0, key="pfcf", inputs=("market_cap", "fcf"))

    assert result.value == pytest.approx(-2.0)
    assert result.reason is None


@pytest.mark.parametrize(
    ("numerator", "denominator"),
    [
        (None, 2.0),
        (2.0, None),
        (math.nan, 2.0),
        (2.0, math.nan),
        (math.inf, 2.0),
        (2.0, math.inf),
        (2.0, -math.inf),
        (True, 2.0),
        (2.0, False),
        ("2.0", 2.0),
        (2.0, "2.0"),
        (None, None),
    ],
)
def test_safe_ratio_reports_an_unreadable_input_as_missing(
    numerator: object, denominator: object
) -> None:
    result = safe_ratio(
        numerator,  # type: ignore[arg-type]
        denominator,  # type: ignore[arg-type]
        key="pe",
        inputs=("price", "eps_ttm"),
    )

    assert result.value is None
    assert result.reason == MISSING_INPUT


@pytest.mark.parametrize("denominator", [0.0, -0.0])
def test_safe_ratio_reports_a_zero_denominator(denominator: float) -> None:
    result = safe_ratio(10.0, denominator, key="pe", inputs=("price", "eps_ttm"))

    assert result.value is None
    assert result.reason == ZERO_DENOMINATOR


def test_safe_ratio_returns_no_multiple_for_a_negative_denominator() -> None:
    """A negative P/E is not a valuation multiple, so it is not a number."""

    result = safe_ratio(10.0, -2.0, key="pe", inputs=("price", "eps_ttm"))

    assert result.value is None
    assert result.reason == NEGATIVE_DENOMINATOR


def test_safe_ratio_reports_an_overflow_instead_of_returning_infinity() -> None:
    result = safe_ratio(1e308, 1e-308, key="pe", inputs=("price", "eps_ttm"))

    assert result.value is None
    assert result.reason == OVERFLOW


def test_safe_ratio_requires_an_input_trail() -> None:
    with pytest.raises(ValueError, match="input"):
        safe_ratio(10.0, 2.0, key="pe", inputs=())


# ---------------------------------------------------------------------------
# market_cap
# ---------------------------------------------------------------------------


def test_market_cap_multiplies_price_by_shares() -> None:
    result = market_cap(12.5, 8_000_000_000.0)

    assert result.key == "market_cap"
    assert result.value == pytest.approx(1.0e11)
    assert result.inputs == ("price", "shares")
    assert result.reason is None


def test_market_cap_accepts_decimal_inputs() -> None:
    result = market_cap(Decimal("12.5"), Decimal("2"))

    assert result.value == pytest.approx(25.0)
    assert result.reason is None


@pytest.mark.parametrize(
    ("price", "shares"),
    [
        (None, 10.0),
        (10.0, None),
        (math.nan, 10.0),
        (10.0, math.inf),
    ],
)
def test_market_cap_reports_a_missing_input(price: object, shares: object) -> None:
    result = market_cap(price, shares)  # type: ignore[arg-type]

    assert result.value is None
    assert result.reason == MISSING_INPUT


@pytest.mark.parametrize(
    ("price", "shares"),
    [(0.0, 10.0), (-1.0, 10.0), (10.0, 0.0), (10.0, -5.0)],
)
def test_market_cap_rejects_non_positive_inputs(price: float, shares: float) -> None:
    result = market_cap(price, shares)

    assert result.value is None
    assert result.reason == NON_POSITIVE_INPUT


def test_market_cap_reports_an_overflow() -> None:
    result = market_cap(1e308, 10.0)

    assert result.value is None
    assert result.reason == OVERFLOW


# ---------------------------------------------------------------------------
# enterprise_value
# ---------------------------------------------------------------------------


def test_enterprise_value_adds_debt_subtracts_cash_and_flags_assumed_zeros() -> None:
    result = enterprise_value(100.0, 20.0, 5.0)

    assert result.key == "enterprise_value"
    assert result.value == pytest.approx(115.0)
    assert result.inputs == (
        "market_cap",
        "total_debt",
        "cash",
        "preferred",
        "minority",
    )
    assert result.reason == ASSUMED_ZERO


def test_enterprise_value_with_every_component_reported_has_no_caveat() -> None:
    result = enterprise_value(
        market_cap=100.0,
        total_debt=20.0,
        cash=5.0,
        preferred=3.0,
        minority=2.0,
    )

    assert result.value == pytest.approx(120.0)
    assert result.reason is None


def test_an_explicit_zero_preferred_is_not_an_assumption() -> None:
    """Passing ``0.0`` is a claim; omitting the argument is an assumption."""

    result = enterprise_value(
        market_cap=100.0,
        total_debt=20.0,
        cash=5.0,
        preferred=0.0,
        minority=0.0,
    )

    assert result.value == pytest.approx(115.0)
    assert result.reason is None


@pytest.mark.parametrize(
    ("market_cap_value", "total_debt", "cash"),
    [
        (None, 20.0, 5.0),
        (100.0, None, 5.0),
        (100.0, 20.0, None),
        (math.nan, 20.0, 5.0),
        (100.0, math.inf, 5.0),
        (100.0, 20.0, -math.inf),
    ],
)
def test_enterprise_value_requires_market_cap_debt_and_cash(
    market_cap_value: object, total_debt: object, cash: object
) -> None:
    result = enterprise_value(
        market_cap_value,  # type: ignore[arg-type]
        total_debt,  # type: ignore[arg-type]
        cash,  # type: ignore[arg-type]
    )

    assert result.value is None
    assert result.reason == MISSING_INPUT


def test_enterprise_value_reports_an_unreadable_optional_input_as_missing() -> None:
    """A supplied but unreadable component is not the same as an omitted one."""

    result = enterprise_value(100.0, 20.0, 5.0, preferred=math.nan)

    assert result.value is None
    assert result.reason == MISSING_INPUT


def test_enterprise_value_reads_balances_as_reported() -> None:
    """A negative cash balance is unusual, not impossible; it is not policed."""

    result = enterprise_value(
        market_cap=100.0,
        total_debt=20.0,
        cash=-5.0,
        preferred=0.0,
        minority=0.0,
    )

    assert result.value == pytest.approx(125.0)
    assert result.reason is None


def test_enterprise_value_reports_an_overflow() -> None:
    result = enterprise_value(1e308, 1e308, 0.0, preferred=0.0, minority=0.0)

    assert result.value is None
    assert result.reason == OVERFLOW


# ---------------------------------------------------------------------------
# pe_ratio / forward_pe
# ---------------------------------------------------------------------------


def test_pe_ratio_divides_price_by_ttm_eps() -> None:
    result = pe_ratio(50.0, 2.0)

    assert result.key == "pe"
    assert result.value == pytest.approx(25.0)
    assert result.inputs == ("price", "eps_ttm")
    assert result.reason is None


def test_pe_ratio_is_null_for_zero_eps() -> None:
    result = pe_ratio(50.0, 0.0)

    assert result.value is None
    assert result.reason == ZERO_DENOMINATOR


def test_pe_ratio_is_null_for_negative_eps() -> None:
    result = pe_ratio(50.0, -2.0)

    assert result.value is None
    assert result.reason == NEGATIVE_DENOMINATOR


def test_pe_ratio_is_null_for_a_non_positive_price() -> None:
    for price in (0.0, -50.0):
        result = pe_ratio(price, 2.0)
        assert result.value is None
        assert result.reason == NON_POSITIVE_INPUT


def test_pe_ratio_reports_missing_inputs_before_domain_problems() -> None:
    result = pe_ratio(None, -2.0)

    assert result.value is None
    assert result.reason == MISSING_INPUT


def test_pe_ratio_reports_a_non_finite_eps_as_missing() -> None:
    result = pe_ratio(50.0, math.nan)

    assert result.value is None
    assert result.reason == MISSING_INPUT


def test_forward_pe_divides_price_by_forward_eps() -> None:
    result = forward_pe(100.0, 5.0)

    assert result.key == "forward_pe"
    assert result.value == pytest.approx(20.0)
    assert result.inputs == ("price", "eps_forward")
    assert result.reason is None


def test_forward_pe_is_null_for_non_positive_forward_eps() -> None:
    for eps in (0.0, -5.0):
        result = forward_pe(100.0, eps)
        assert result.value is None
        assert result.reason in {ZERO_DENOMINATOR, NEGATIVE_DENOMINATOR}


def test_forward_pe_is_null_for_a_non_positive_price() -> None:
    result = forward_pe(-100.0, 5.0)

    assert result.value is None
    assert result.reason == NON_POSITIVE_INPUT


# ---------------------------------------------------------------------------
# peg
# ---------------------------------------------------------------------------


def test_peg_divides_forward_pe_by_growth_percent() -> None:
    result = peg(25.0, 20.0)

    assert result.key == "peg"
    assert result.value == pytest.approx(1.25)
    assert result.inputs == ("forward_pe", "growth_pct")
    assert result.reason is None


def test_peg_is_null_for_zero_growth() -> None:
    result = peg(25.0, 0.0)

    assert result.value is None
    assert result.reason == ZERO_DENOMINATOR


def test_peg_is_null_for_negative_growth() -> None:
    result = peg(25.0, -10.0)

    assert result.value is None
    assert result.reason == NEGATIVE_DENOMINATOR


def test_peg_is_null_for_a_non_positive_forward_pe() -> None:
    for multiple in (0.0, -25.0):
        result = peg(multiple, 20.0)
        assert result.value is None
        assert result.reason == NON_POSITIVE_INPUT


def test_peg_reports_missing_inputs() -> None:
    result = peg(None, 20.0)

    assert result.value is None
    assert result.reason == MISSING_INPUT


# ---------------------------------------------------------------------------
# price_to_book / price_to_sales
# ---------------------------------------------------------------------------


def test_price_to_book_divides_price_by_book_per_share() -> None:
    result = price_to_book(100.0, 25.0)

    assert result.key == "pb"
    assert result.value == pytest.approx(4.0)
    assert result.inputs == ("price", "book_per_share")
    assert result.reason is None


def test_price_to_book_is_null_for_non_positive_book_value() -> None:
    for book in (0.0, -25.0):
        result = price_to_book(100.0, book)
        assert result.value is None
        assert result.reason in {ZERO_DENOMINATOR, NEGATIVE_DENOMINATOR}


def test_price_to_book_reports_a_missing_price() -> None:
    result = price_to_book(None, 25.0)

    assert result.value is None
    assert result.reason == MISSING_INPUT


def test_price_to_sales_divides_price_by_sales_per_share() -> None:
    result = price_to_sales(100.0, 10.0)

    assert result.key == "ps"
    assert result.value == pytest.approx(10.0)
    assert result.inputs == ("price", "sales_per_share")
    assert result.reason is None


def test_price_to_sales_is_null_for_non_positive_sales() -> None:
    for sales in (0.0, -10.0):
        result = price_to_sales(100.0, sales)
        assert result.value is None
        assert result.reason in {ZERO_DENOMINATOR, NEGATIVE_DENOMINATOR}


# ---------------------------------------------------------------------------
# fcf: the capex sign convention
# ---------------------------------------------------------------------------


def test_fcf_subtracts_a_positive_capex() -> None:
    result = fcf(100.0, 40.0)

    assert result.key == "fcf"
    assert result.value == pytest.approx(60.0)
    assert result.inputs == ("operating_cash_flow", "capex")
    assert result.reason is None


def test_fcf_subtracts_the_magnitude_of_a_negative_capex() -> None:
    """Cash-flow statements print capex as negative; the economics are the same."""

    positive = fcf(100.0, 40.0)
    negative = fcf(100.0, -40.0)

    assert negative.value == pytest.approx(60.0)
    assert negative.value == positive.value


def test_fcf_keeps_a_reported_zero_capex_as_a_real_zero() -> None:
    result = fcf(100.0, 0.0)

    assert result.value == pytest.approx(100.0)
    assert result.reason is None


def test_fcf_preserves_negative_operating_cash_flow() -> None:
    result = fcf(-50.0, 10.0)

    assert result.value == pytest.approx(-60.0)
    assert result.reason is None


@pytest.mark.parametrize(
    ("operating_cash_flow", "capex"),
    [
        (None, 40.0),
        (100.0, None),
        (math.nan, 40.0),
        (100.0, math.inf),
    ],
)
def test_fcf_never_treats_a_missing_capex_as_zero(
    operating_cash_flow: object, capex: object
) -> None:
    result = fcf(operating_cash_flow, capex)  # type: ignore[arg-type]

    assert result.value is None
    assert result.reason == MISSING_INPUT


def test_fcf_reports_an_overflow() -> None:
    result = fcf(-1e308, 1.7e308)

    assert result.value is None
    assert result.reason == OVERFLOW


# ---------------------------------------------------------------------------
# earnings_yield
# ---------------------------------------------------------------------------


def test_earnings_yield_is_the_inverse_of_pe_in_percent() -> None:
    result = earnings_yield(25.0)

    assert result.key == "earnings_yield"
    assert result.value == pytest.approx(4.0)
    assert result.inputs == ("pe",)
    assert result.reason is None


def test_earnings_yield_is_null_for_a_zero_pe() -> None:
    result = earnings_yield(0.0)

    assert result.value is None
    assert result.reason == ZERO_DENOMINATOR


def test_earnings_yield_is_null_for_a_negative_pe() -> None:
    result = earnings_yield(-25.0)

    assert result.value is None
    assert result.reason == NEGATIVE_DENOMINATOR


def test_earnings_yield_reports_a_missing_pe() -> None:
    result = earnings_yield(None)

    assert result.value is None
    assert result.reason == MISSING_INPUT


def test_earnings_yield_reports_an_overflow() -> None:
    result = earnings_yield(1e-320)

    assert result.value is None
    assert result.reason == OVERFLOW


# ---------------------------------------------------------------------------
# ttm_sum: exactly four quarters, never annualised
# ---------------------------------------------------------------------------


def test_ttm_sum_adds_exactly_four_quarters() -> None:
    result = ttm_sum([10.0, 20.0, 30.0, 40.0])

    assert result.key == "ttm_sum"
    assert result.value == pytest.approx(100.0)
    assert result.inputs == ("quarters",)
    assert result.reason is None


def test_ttm_sum_accepts_a_tuple_and_does_not_mutate_its_input() -> None:
    quarters = [10.0, 20.0, 30.0, 40.0]

    assert ttm_sum(tuple(quarters)).value == pytest.approx(100.0)
    assert ttm_sum(quarters).value == pytest.approx(100.0)
    assert quarters == [10.0, 20.0, 30.0, 40.0]


def test_ttm_sum_keeps_a_real_zero_and_accepts_losses() -> None:
    assert ttm_sum([0.0, 0.0, 0.0, 0.0]).value == 0.0
    assert ttm_sum([10.0, -30.0, 5.0, -5.0]).value == pytest.approx(-20.0)


@pytest.mark.parametrize(
    "quarters",
    [
        [],
        [10.0],
        [10.0, 20.0, 30.0],
        [10.0, 20.0, 30.0, 40.0, 50.0],
    ],
)
def test_ttm_sum_rejects_anything_but_four_quarters(
    quarters: list[float],
) -> None:
    result = ttm_sum(quarters)

    assert result.value is None
    assert result.reason == WRONG_QUARTER_COUNT


def test_ttm_sum_never_annualises_a_partial_year() -> None:
    result = ttm_sum([10.0, 10.0, 10.0, None])

    assert result.value is None
    assert result.reason == MISSING_INPUT
    assert result.value != pytest.approx(40.0)


@pytest.mark.parametrize(
    "missing",
    [None, math.nan, math.inf, -math.inf, True, "10.0"],
)
def test_ttm_sum_reports_an_unreadable_quarter_as_missing(missing: object) -> None:
    result = ttm_sum([10.0, 20.0, 30.0, missing])  # type: ignore[list-item]

    assert result.value is None
    assert result.reason == MISSING_INPUT


def test_ttm_sum_reports_an_overflow() -> None:
    result = ttm_sum([1.7e308, 1.7e308, 0.0, 0.0])

    assert result.value is None
    assert result.reason == OVERFLOW


# ---------------------------------------------------------------------------
# The documentation tables and the closed reason vocabulary
# ---------------------------------------------------------------------------

#: One happy-path call per public factory, so a new factory cannot ship without
#: a formula row a reviewer can check.
FACTORIES = [
    pytest.param(lambda: market_cap(10.0, 2.0), id="market_cap"),
    pytest.param(lambda: enterprise_value(10.0, 2.0, 1.0), id="enterprise_value"),
    pytest.param(lambda: pe_ratio(10.0, 2.0), id="pe_ratio"),
    pytest.param(lambda: forward_pe(10.0, 2.0), id="forward_pe"),
    pytest.param(lambda: peg(10.0, 5.0), id="peg"),
    pytest.param(lambda: price_to_book(10.0, 2.0), id="price_to_book"),
    pytest.param(lambda: price_to_sales(10.0, 2.0), id="price_to_sales"),
    pytest.param(lambda: fcf(10.0, 2.0), id="fcf"),
    pytest.param(lambda: earnings_yield(10.0), id="earnings_yield"),
    pytest.param(lambda: ttm_sum([1.0, 2.0, 3.0, 4.0]), id="ttm_sum"),
]


@pytest.mark.parametrize("factory", FACTORIES)
def test_every_factory_reports_a_documented_formula(factory: object) -> None:
    result = factory()  # type: ignore[operator]

    assert result.key in FORMULAS
    assert result.value is not None
    assert result.inputs
    assert result.reason is None or result.reason == ASSUMED_ZERO


def test_the_formula_table_covers_every_key() -> None:
    assert set(FORMULAS) == {
        "market_cap",
        "enterprise_value",
        "pe",
        "forward_pe",
        "peg",
        "pb",
        "ps",
        "fcf",
        "earnings_yield",
        "ttm_sum",
    }


def test_the_reason_vocabulary_is_closed() -> None:
    assert REASONS == {
        MISSING_INPUT,
        ZERO_DENOMINATOR,
        NEGATIVE_DENOMINATOR,
        NON_POSITIVE_INPUT,
        OVERFLOW,
        WRONG_QUARTER_COUNT,
        ASSUMED_ZERO,
    }


@pytest.mark.parametrize(
    "result",
    [
        pe_ratio(None, 1.0),
        pe_ratio(1.0, 0.0),
        pe_ratio(1.0, -1.0),
        pe_ratio(0.0, 1.0),
        safe_ratio(1e308, 1e-308, key="pe", inputs=("price", "eps_ttm")),
        ttm_sum([1.0, 2.0, 3.0]),
        ttm_sum([1.0, 2.0, 3.0, None]),
        enterprise_value(100.0, 20.0, 5.0),
    ],
)
def test_every_result_explains_itself_from_the_documented_vocabulary(
    result: DerivedValue,
) -> None:
    assert result.reason in REASONS


def test_a_missing_input_never_becomes_a_zero() -> None:
    """The one rule that makes absence distinguishable from a measurement."""

    null_results = [
        market_cap(None, 1.0),
        enterprise_value(None, 1.0, 1.0),
        pe_ratio(1.0, None),
        forward_pe(None, 1.0),
        peg(1.0, None),
        price_to_book(None, 1.0),
        price_to_sales(None, 1.0),
        fcf(None, 1.0),
        earnings_yield(None),
        ttm_sum([1.0, 2.0, None, 4.0]),
    ]

    for result in null_results:
        assert result.value is None
        assert result.reason is not None
