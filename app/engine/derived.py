"""Derived metrics that can be audited line by line.

The engine's provenance rules answer “where did this number come from?” for an
observed cell. A derived number owes a second answer: *which* observed numbers
produced it, and — when it has no value — *why*. This module puts that answer
in the value itself: every :class:`DerivedValue` carries the catalog-style key
it answers, the semantic names of the inputs it read, and either a finite value
or a reason from the closed vocabulary below.

What this module does not do
----------------------------
No network, no data source, no clock. A derivation never invents an as-of date,
a currency or a unit; it reads numbers the caller already owns. An input is a
finite float or nothing at all: ``None``, ``NaN``, ``±inf``, a ``bool`` and a
string (even ``"12.5"``) are all “not a measurement” and produce
:data:`MISSING_INPUT`. Nothing is parsed, because a provider's formatting is not
arithmetic, and nothing falls back to ``0.0``, because an assumed zero is
indistinguishable from a reported one once it is on the row.

``Decimal`` is accepted and narrowed to ``float``. The engine's value type is a
float, so the narrowing happens here, at the boundary, rather than silently
inside a formula.

The denominator rule
--------------------
:func:`safe_ratio` is the only place this module forms a ratio, and it refuses a
denominator that is zero **or negative**. That is an economic rule, not a
numerical convenience: a negative P/E is not a valuation multiple a reader can
compare with 18×; it is the arithmetic of a loss-making company wearing the
costume of a multiple. The same holds for PEG, P/B and P/S, whose denominators
(eps, growth, book value, sales) stop being meaningful at or below zero. The
caller gets no number and a reason saying which rule fired — never ``-12.5``.

The numerator's sign is deliberately **not** policed: the sign of a ratio is
information (a negative numerator is how a loss shows up in a margin), and the
functions whose numerator is a price validate it themselves. A numerator of
zero is a real zero and is returned.

Reason vocabulary and precedence
--------------------------------
The reasons are a closed set, exported as :data:`REASONS`:

``missing_input``
    An input was ``None``, non-finite, a bool or not a number at all.
``non_positive_input``
    A quantity that must be strictly positive (a price, a share count, a
    forward multiple used as a PEG numerator) was zero or negative.
``zero_denominator``
    The denominator was exactly zero.
``negative_denominator``
    The denominator was negative; no multiple exists.
``overflow``
    The arithmetic left the finite float range; ``inf`` is never returned.
``wrong_quarter_count``
    A TTM sum was handed anything other than exactly four quarters.
``assumed_zero``
    The *only* reason that accompanies a value: enterprise value defaulted an
    omitted optional component to zero (see below).

Precedence is fixed so two broken inputs cannot produce two stories: an
unreadable input first, then the positive-domain check, then the denominator,
then overflow.

The formulas
------------
The implementation is ordinary arithmetic — there is no ``eval`` and no formula
language. :data:`FORMULAS` repeats each identity as text so a reviewer (and the
test suite) can check the code against the documented formula:

==========================  ==================================================
key                         formula
==========================  ==================================================
``market_cap``              ``price * shares``
``enterprise_value``        ``market_cap + total_debt - cash + preferred + minority``
``pe``                      ``price / eps_ttm``
``forward_pe``              ``price / eps_forward``
``peg``                     ``forward_pe / growth_pct``
``pb``                      ``price / book_per_share``
``ps``                      ``price / sales_per_share``
``fcf``                     ``operating_cash_flow - abs(capex)``
``earnings_yield``          ``(1 / pe) * 100``
``ttm_sum``                 ``q1 + q2 + q3 + q4``
==========================  ==================================================

Enterprise value's documented assumption
-----------------------------------------
Market capitalisation, total debt and cash are **required**: an EV missing any
of them is ``None`` with ``missing_input``, because the missing piece could move
the answer in either direction. Preferred stock and minority interest are
optional, because the standard identity omits them for the many companies that
report neither. When one is omitted it is treated as zero and the result says
so: ``reason="assumed_zero"`` with the value still present. Passing ``0.0``
explicitly is a claim, not an assumption, and returns ``reason=None`` — which is
how a caller can tell an observed zero from a defaulted one without a second
field. Enterprise value is the only function here that returns a value and a
reason together. Balances are read as reported: a negative cash balance is
unusual, not impossible, and is not rewritten.

The capex sign convention
-------------------------
``fcf = operating_cash_flow - abs(capex)``. Providers disagree about capex's
sign: a cash-flow statement prints “purchases of property and equipment” as a
negative number, while a vendor's ``capital_expenditures`` field is often the
positive magnitude of the same spend. Both describe cash leaving the company, so
both subtract. A reported ``0.0`` is a real zero (no capital spend this period)
and returns operating cash flow unchanged. The limitation is explicit: this
reads capex as a spend, so a net *disposal* booked into the same field is not
recovered — that is a signed adjustment this identity does not claim to make.

TTM policy
----------
:func:`ttm_sum` accepts exactly four quarterly values and adds them. It does not
annualise: three quarters and a ``None`` is not “roughly a year”, it is
``missing_input``. The caller keeps the quarter identity and its provenance; the
sum is the only thing this module owns. ``math.fsum`` is used so the total does
not depend on summation order.

Units
-----
A value is expressed in the metric's canonical engine unit: ``USD`` for money,
``ratio`` for multiples, and percentage points for :func:`earnings_yield`
(``4.0`` means a 4% yield, matching ``Unit.PERCENT`` as the rest of the engine
prints it).
"""

from __future__ import annotations

import math
import numbers
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Mapping

#: An input was ``None``, non-finite, a bool, or not numeric at all.
MISSING_INPUT = "missing_input"
#: A quantity that must be strictly positive was zero or negative.
NON_POSITIVE_INPUT = "non_positive_input"
#: The denominator was exactly zero.
ZERO_DENOMINATOR = "zero_denominator"
#: The denominator was negative, so no multiple exists.
NEGATIVE_DENOMINATOR = "negative_denominator"
#: The arithmetic left the finite float range.
OVERFLOW = "overflow"
#: A TTM sum was handed anything other than exactly four quarters.
WRONG_QUARTER_COUNT = "wrong_quarter_count"
#: An optional enterprise-value component was omitted and defaulted to zero.
ASSUMED_ZERO = "assumed_zero"

#: The closed reason vocabulary. A reason outside it is a programming error, not
#: a new kind of absence.
REASONS: frozenset[str] = frozenset(
    {
        MISSING_INPUT,
        NON_POSITIVE_INPUT,
        ZERO_DENOMINATOR,
        NEGATIVE_DENOMINATOR,
        OVERFLOW,
        WRONG_QUARTER_COUNT,
        ASSUMED_ZERO,
    }
)

#: Key → the identity the implementation evaluates, in the module docstring's
#: words. A test walks every factory and fails if its key is missing here.
FORMULAS: Mapping[str, str] = MappingProxyType(
    {
        "market_cap": "price * shares",
        "enterprise_value": (
            "market_cap + total_debt - cash + preferred + minority"
        ),
        "pe": "price / eps_ttm",
        "forward_pe": "price / eps_forward",
        "peg": "forward_pe / growth_pct",
        "pb": "price / book_per_share",
        "ps": "price / sales_per_share",
        "fcf": "operating_cash_flow - abs(capex)",
        "earnings_yield": "(1 / pe) * 100",
        "ttm_sum": "q1 + q2 + q3 + q4",
    }
)

#: Parameter names per metric, so the audit trail cannot drift between the
#: function that reads an input and the record that names it.
_MARKET_CAP_INPUTS = ("price", "shares")
_ENTERPRISE_VALUE_INPUTS = (
    "market_cap",
    "total_debt",
    "cash",
    "preferred",
    "minority",
)
_PE_INPUTS = ("price", "eps_ttm")
_FORWARD_PE_INPUTS = ("price", "eps_forward")
_PEG_INPUTS = ("forward_pe", "growth_pct")
_PB_INPUTS = ("price", "book_per_share")
_PS_INPUTS = ("price", "sales_per_share")
_FCF_INPUTS = ("operating_cash_flow", "capex")
_EARNINGS_YIELD_INPUTS = ("pe",)
_TTM_INPUTS = ("quarters",)


@dataclass(frozen=True)
class DerivedValue:
    """One derived number, the inputs it read, and why it may be absent.

    The invariants mirror :class:`~app.engine.model.Cell`, adapted to a derived
    quantity:

    * a null value **must** carry a reason, so an empty derivation can never be
      mistaken for a measurement of zero;
    * a present value must be finite, and its reason is either ``None`` or
      :data:`ASSUMED_ZERO` — the one documented assumption this module makes;
    * ``inputs`` is a non-empty, duplicate-free trail of the semantic input
      names; a derivation that names no input is a guess.

    ``key`` is normalised to the catalog's lowercase snake_case, so a caller can
    join a derivation to a catalog row without rewriting it.
    """

    key: str
    value: float | None
    inputs: tuple[str, ...]
    reason: str | None

    def __post_init__(self) -> None:
        key = self.key.strip().lower() if isinstance(self.key, str) else ""
        if not key:
            raise ValueError("derived value key must not be blank")

        names = tuple(
            name.strip() if isinstance(name, str) else "" for name in self.inputs
        )
        if not names:
            raise ValueError("a derived value must name at least one input")
        if any(not name for name in names):
            raise ValueError("derived value inputs must not contain blank names")
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("derived value inputs must be unique")

        reason = self.reason.strip() if isinstance(self.reason, str) else None
        if not reason:
            reason = None

        if self.value is None:
            if reason is None:
                raise ValueError("a null derived value requires a reason")
            if reason == ASSUMED_ZERO:
                raise ValueError(
                    "a null derived value cannot claim assumed_zero"
                )
            if reason not in REASONS:
                raise ValueError(f"derived value reason {reason!r} is not documented")
            value: float | None = None
        else:
            if isinstance(self.value, bool) or not isinstance(
                self.value, numbers.Real
            ):
                raise ValueError("a derived value must be a finite number")
            try:
                number = float(self.value)
            except (TypeError, ValueError, OverflowError):
                raise ValueError(
                    "a derived value must be a finite number"
                ) from None
            if not math.isfinite(number):
                raise ValueError("a derived value must be a finite number")
            if reason is not None and reason != ASSUMED_ZERO:
                raise ValueError(
                    "a present derived value's reason may only be the "
                    "assumed_zero caveat"
                )
            value = number

        object.__setattr__(self, "key", key)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "inputs", names)
        object.__setattr__(self, "reason", reason)

    @property
    def ok(self) -> bool:
        """True when the derivation produced a finite value."""

        return self.value is not None

    @property
    def assumed(self) -> bool:
        """True when an optional input was defaulted to zero (see the docstring)."""

        return self.value is not None and self.reason == ASSUMED_ZERO


def _number(value: Any) -> float | None:
    """One input as a finite float, or ``None`` when it is not a measurement.

    ``bool`` is rejected on purpose: ``True`` is a flag, not a quantity.
    ``Decimal`` is accepted and narrowed; a string is not parsed.
    """

    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (numbers.Real, Decimal)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _null(key: str, inputs: tuple[str, ...], reason: str) -> DerivedValue:
    """An honestly empty derivation."""

    return DerivedValue(key=key, value=None, inputs=inputs, reason=reason)


def _present(
    key: str,
    inputs: tuple[str, ...],
    value: float,
    reason: str | None = None,
) -> DerivedValue:
    """A finite result, or :data:`OVERFLOW` when the arithmetic left the range."""

    if not math.isfinite(value):
        return _null(key, inputs, OVERFLOW)
    return DerivedValue(key=key, value=value, inputs=inputs, reason=reason)


def safe_ratio(
    numerator: float | None,
    denominator: float | None,
    *,
    key: str,
    inputs: tuple[str, ...],
) -> DerivedValue:
    """``numerator / denominator``, or ``None`` with the rule that refused it.

    A zero denominator has no value and a negative one is economically invalid
    for every multiple this module builds, so both are absent rather than a
    number with a sign a reader would have to un-learn. The numerator's sign is
    preserved: only the denominator is policed (see the module docstring).
    ``inputs`` names the two (or, for a constant numerator, the one) semantic
    inputs the caller read; the record refuses an empty trail.
    """

    top = _number(numerator)
    bottom = _number(denominator)
    if top is None or bottom is None:
        return _null(key, inputs, MISSING_INPUT)
    if bottom == 0.0:
        return _null(key, inputs, ZERO_DENOMINATOR)
    if bottom < 0.0:
        return _null(key, inputs, NEGATIVE_DENOMINATOR)
    return _present(key, inputs, top / bottom)


def market_cap(price: float | None, shares: float | None) -> DerivedValue:
    """Market capitalisation: ``price * shares``.

    Both inputs must be present and strictly positive; a zero price or a zero
    share count is not a company with a zero valuation, it is a row without a
    valuation.
    """

    price_value = _number(price)
    share_count = _number(shares)
    if price_value is None or share_count is None:
        return _null("market_cap", _MARKET_CAP_INPUTS, MISSING_INPUT)
    if price_value <= 0.0 or share_count <= 0.0:
        return _null("market_cap", _MARKET_CAP_INPUTS, NON_POSITIVE_INPUT)
    return _present("market_cap", _MARKET_CAP_INPUTS, price_value * share_count)


def enterprise_value(
    market_cap: float | None,
    total_debt: float | None,
    cash: float | None,
    preferred: float | None = None,
    minority: float | None = None,
) -> DerivedValue:
    """``market_cap + total_debt - cash + preferred + minority``.

    The first three components are required: omitting one would move the answer
    without saying so. ``preferred`` and ``minority`` are optional because most
    companies report neither; an omitted component becomes zero and the result
    carries :data:`ASSUMED_ZERO` alongside its value, so the assumption is
    visible. Passing ``0.0`` explicitly is a claim and leaves ``reason`` empty.
    A *supplied* but unreadable component is ``missing_input``, not an
    assumption. See the module docstring for why balances are not sign-policed.
    """

    cap = _number(market_cap)
    debt = _number(total_debt)
    cash_value = _number(cash)
    if cap is None or debt is None or cash_value is None:
        return _null("enterprise_value", _ENTERPRISE_VALUE_INPUTS, MISSING_INPUT)

    assumed = False
    if preferred is None:
        preferred_value = 0.0
        assumed = True
    else:
        preferred_value = _number(preferred)
        if preferred_value is None:
            return _null(
                "enterprise_value", _ENTERPRISE_VALUE_INPUTS, MISSING_INPUT
            )

    if minority is None:
        minority_value = 0.0
        assumed = True
    else:
        minority_value = _number(minority)
        if minority_value is None:
            return _null(
                "enterprise_value", _ENTERPRISE_VALUE_INPUTS, MISSING_INPUT
            )

    total = cap + debt - cash_value + preferred_value + minority_value
    return _present(
        "enterprise_value",
        _ENTERPRISE_VALUE_INPUTS,
        total,
        ASSUMED_ZERO if assumed else None,
    )


def pe_ratio(price: float | None, eps_ttm: float | None) -> DerivedValue:
    """Trailing P/E: ``price / eps_ttm``, absent when EPS is zero or negative."""

    price_value = _number(price)
    earnings = _number(eps_ttm)
    if price_value is None or earnings is None:
        return _null("pe", _PE_INPUTS, MISSING_INPUT)
    if price_value <= 0.0:
        return _null("pe", _PE_INPUTS, NON_POSITIVE_INPUT)
    return safe_ratio(price_value, earnings, key="pe", inputs=_PE_INPUTS)


def forward_pe(price: float | None, eps_forward: float | None) -> DerivedValue:
    """Forward P/E: ``price / eps_forward``, absent when forward EPS ≤ 0."""

    price_value = _number(price)
    earnings = _number(eps_forward)
    if price_value is None or earnings is None:
        return _null("forward_pe", _FORWARD_PE_INPUTS, MISSING_INPUT)
    if price_value <= 0.0:
        return _null("forward_pe", _FORWARD_PE_INPUTS, NON_POSITIVE_INPUT)
    return safe_ratio(
        price_value, earnings, key="forward_pe", inputs=_FORWARD_PE_INPUTS
    )


def peg(
    forward_pe: float | None, growth_pct: float | None
) -> DerivedValue:
    """PEG: ``forward_pe / growth_pct``, growth in percentage points.

    A zero or negative growth rate is not a growth denominator: the ratio would
    be infinite or would flip the meaning of the multiple.
    """

    multiple = _number(forward_pe)
    growth = _number(growth_pct)
    if multiple is None or growth is None:
        return _null("peg", _PEG_INPUTS, MISSING_INPUT)
    if multiple <= 0.0:
        return _null("peg", _PEG_INPUTS, NON_POSITIVE_INPUT)
    return safe_ratio(multiple, growth, key="peg", inputs=_PEG_INPUTS)


def price_to_book(
    price: float | None, book_per_share: float | None
) -> DerivedValue:
    """P/B: ``price / book_per_share``, absent for non-positive book value."""

    price_value = _number(price)
    book = _number(book_per_share)
    if price_value is None or book is None:
        return _null("pb", _PB_INPUTS, MISSING_INPUT)
    if price_value <= 0.0:
        return _null("pb", _PB_INPUTS, NON_POSITIVE_INPUT)
    return safe_ratio(price_value, book, key="pb", inputs=_PB_INPUTS)


def price_to_sales(
    price: float | None, sales_per_share: float | None
) -> DerivedValue:
    """P/S: ``price / sales_per_share``, absent for non-positive sales."""

    price_value = _number(price)
    sales = _number(sales_per_share)
    if price_value is None or sales is None:
        return _null("ps", _PS_INPUTS, MISSING_INPUT)
    if price_value <= 0.0:
        return _null("ps", _PS_INPUTS, NON_POSITIVE_INPUT)
    return safe_ratio(price_value, sales, key="ps", inputs=_PS_INPUTS)


def fcf(
    operating_cash_flow: float | None, capex: float | None
) -> DerivedValue:
    """Free cash flow: ``operating_cash_flow - abs(capex)``.

    ``abs`` is the documented sign bridge: a statement's negative “purchases of
    property and equipment” and a vendor's positive capital-expenditure
    magnitude describe the same outflow, so both subtract. A reported zero is a
    real zero. A missing capex is never read as zero (see the module docstring).
    """

    cash_from_operations = _number(operating_cash_flow)
    capital_spend = _number(capex)
    if cash_from_operations is None or capital_spend is None:
        return _null("fcf", _FCF_INPUTS, MISSING_INPUT)
    return _present(
        "fcf", _FCF_INPUTS, cash_from_operations - abs(capital_spend)
    )


def earnings_yield(pe: float | None) -> DerivedValue:
    """Earnings yield in percentage points: ``(1 / pe) * 100``.

    The inverse of a multiple, so it inherits the denominator rule: a zero or
    negative P/E has no yield. ``4.0`` means 4%, matching the engine's
    percentage convention.
    """

    multiple = _number(pe)
    if multiple is None:
        return _null("earnings_yield", _EARNINGS_YIELD_INPUTS, MISSING_INPUT)
    inverse = safe_ratio(
        1.0, multiple, key="earnings_yield", inputs=_EARNINGS_YIELD_INPUTS
    )
    if inverse.value is None:
        return inverse
    return _present(
        "earnings_yield", _EARNINGS_YIELD_INPUTS, inverse.value * 100.0
    )


def ttm_sum(quarters: Sequence[float | None]) -> DerivedValue:
    """The sum of exactly four quarterly values.

    Anything other than four values is :data:`WRONG_QUARTER_COUNT`; one
    unreadable quarter makes the whole sum :data:`MISSING_INPUT`. There is no
    annualisation of a partial year: three quarters are not “about twelve
    months”, and multiplying them by four would invent a number nobody
    reported. Quarter identity and provenance stay with the caller's cells;
    ``math.fsum`` keeps the total independent of summation order.
    """

    values = tuple(quarters)
    if len(values) != 4:
        return _null("ttm_sum", _TTM_INPUTS, WRONG_QUARTER_COUNT)

    readings = tuple(_number(value) for value in values)
    if any(reading is None for reading in readings):
        return _null("ttm_sum", _TTM_INPUTS, MISSING_INPUT)

    try:
        total = math.fsum(
            reading for reading in readings if reading is not None
        )
    except OverflowError:
        return _null("ttm_sum", _TTM_INPUTS, OVERFLOW)
    return _present("ttm_sum", _TTM_INPUTS, total)


__all__ = [
    "ASSUMED_ZERO",
    "DerivedValue",
    "FORMULAS",
    "MISSING_INPUT",
    "NEGATIVE_DENOMINATOR",
    "NON_POSITIVE_INPUT",
    "OVERFLOW",
    "REASONS",
    "WRONG_QUARTER_COUNT",
    "ZERO_DENOMINATOR",
    "earnings_yield",
    "enterprise_value",
    "fcf",
    "forward_pe",
    "market_cap",
    "pe_ratio",
    "peg",
    "price_to_book",
    "price_to_sales",
    "safe_ratio",
    "ttm_sum",
]
