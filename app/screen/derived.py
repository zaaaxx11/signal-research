"""Three filter columns a parity row can compute from cells it already carries.

The parity rule is *copy verbatim*: the row carries the provider's number, and a
computed ratio would be a value nobody can trace back to the scan
(:mod:`app.screen.universe`). This module is the one documented exception, and
the three conditions a derivation must meet to earn it:

1. **It is an identity, not an estimate.** Every formula below is algebra over
   cells already on the row — no rate, no interpolation, no lookback the row
   does not have.   ``change_from_open`` was checked against the provider's own
   ``change_from_open`` column on three live US symbols (AAPL, NVDA, JPM, probe
   of 2026-10-24) and agrees to a float round-trip (~1e-15 relative), which is
   what makes it the provider's definition rather than a plausible-looking
   approximation.
2. **It asks the scan for nothing new.** ``build_universe(..., columns=
   PARITY_COLUMNS)`` already asks for ``Recommend.All``, ``gap`` and
   ``price_52_week_high``; a derived column is computed from those, so a parity
   scan is byte-identical in cost to the one before this module existed.
3. **The name states the transform or the scale.** ``recom_finviz`` says *which
   scale* (Finviz's 1..5, not the provider's -1..1 ``recommendation``),
   ``change_from_open`` says *which base* (the session open, not the previous
   close) and ``pct_from_52w_high`` says *which direction* (the high, never a
   low nobody derived).

The three formulas
------------------

``recom_finviz`` — Finviz's "Analyst Recom." is 1 (strong buy) .. 5 (strong
sell); TradingView's ``Recommend.All`` is a -1..+1 mean rating. ``3 - 2 * score``
is the published linear map, and it is applied without rounding: ``+1 -> 1``,
``0 -> 3``, ``-1 -> 5``, and a live ``0.5576`` becomes ``1.8848`` — between
"strong buy" and "buy". Rounding to the nearest integer would collapse two
whole buckets and turn ``< 2`` into a tie at the boundary. A score outside
-1..+1 is not a rating on the provider's own domain, so it is unknown
(``None``), not ``3 - 2 * 7 = -11``.

``change_from_open`` — the row carries ``price`` (the close), ``change_pct``
(close against the previous close, percent) and ``gap_pct`` (open against the
previous close, percent). Write the ratios: ``close / prev = 1 + c/100`` and
``open / prev = 1 + g/100``. The previous close cancels, so

    close / open = (1 + c/100) / (1 + g/100)
    change_from_open = (close / open - 1) * 100

No open price is needed and none is invented. The denominators are only valid
when both ratios are **positive**: ``gap_pct = -100`` would mean an open of
zero (and anything below it a negative one), which is not a price. In that case
the answer is ``None`` — an unknown percentage, not ``0.0`` and not ``inf``.
``price`` takes no part in the algebra but must be a positive price: a percent
move for an unpriced row is a statement about nothing.

``pct_from_52w_high`` — ``(price / high_52w - 1) * 100``: ``0`` at a new high,
``-8.4`` eight-point-four percent below it, positive only when the price is
above a stale 52-week high. **One direction is published on purpose.** Finviz's
``ta_highlow52w`` spans both extremes, while the row has the absolute
``high_52w`` and ``low_52w``; this module ships the high side only, because a
``pct_from_52w_low`` would be a second key nobody asked this pass to answer, and
inventing a low-side distance out of the high-side formula would be exactly the
fabrication the parity rule forbids. The absolute ``low_52w`` stays on the row
for whoever needs the low as a price.

Null policy
-----------

Every cell is read the way the rest of the screen reads one: a finite float, or
``None``. A missing cell, a ``NaN``/``inf``, a string, a bool and a
non-positive price are all *unknown* — the derived value is then ``None`` and a
filter drops the row, because "no change from open" and "no 52-week high" are
not measurements. Nothing here ever falls back to ``0.0``: a zero is a real
value (a flat open, a new high, a neutral rating) and the row only carries one
when the inputs really produce it.
"""

from __future__ import annotations

import math
from types import MappingProxyType
from typing import Any, Mapping

#: Derived field → the formula, written the way the docstring states it. This is
#: the table a reader (and a test) can check the implementation against, and the
#: reason a derived column can never be mistaken for a provider copy.
DERIVED_FIELDS: Mapping[str, str] = MappingProxyType(
    {
        "recom_finviz": "3 - 2 * recommendation",
        "change_from_open": "((1 + change_pct / 100) / (1 + gap_pct / 100) - 1) * 100",
        "pct_from_52w_high": "(price / high_52w - 1) * 100",
    }
)

#: Derived field → the **row columns** it reads, in evaluation order. Every entry
#: is a column the parity row already carries (a light column or a
#: :data:`app.screen.universe.PARITY_FIELDS` target); ``app.screen.universe``
#: fails at import if one is not.
DERIVED_INPUTS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "recom_finviz": ("recommendation",),
        "change_from_open": ("price", "change_pct", "gap_pct"),
        "pct_from_52w_high": ("price", "high_52w"),
    }
)

#: Declaration order, so a row and a test read the same sequence.
DERIVED_COLUMNS: tuple[str, ...] = tuple(DERIVED_FIELDS)

#: The domain of ``Recommend.All``: a mean rating from -1 (sell) to +1 (strong
#: buy). A cell outside it is not a rating and is refused rather than mapped.
RECOMMENDATION_MIN = -1.0
RECOMMENDATION_MAX = 1.0


def _cell(value: Any) -> float | None:
    """One normalised row cell: a finite float, or ``None``.

    The row is already normalised by :mod:`app.screen.universe` (``_metric``),
    so a string, a bool, ``NaN`` or ``inf`` reaching this point means the value
    was never a measurement — and it stays unknown instead of being coerced.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _price(value: Any) -> float | None:
    """A price cell: positive, finite, or ``None``. Zero and negatives are not prices."""

    number = _cell(value)
    if number is None or number <= 0.0:
        return None
    return number


def _result(value: float) -> float | None:
    """A computed value that overflowed into ``inf``/``NaN`` is unknown."""

    return value if math.isfinite(value) else None


def recom_finviz(score: Any) -> float | None:
    """``Recommend.All`` (-1..+1) → Finviz's 1..5 analyst score, or ``None``.

    ``+1 -> 1`` (strong buy), ``0 -> 3`` (hold), ``-1 -> 5`` (sell). No rounding:
    the score stays a number a ``between`` filter can cut anywhere.
    """

    rating = _cell(score)
    if rating is None or not (RECOMMENDATION_MIN <= rating <= RECOMMENDATION_MAX):
        return None
    return _result(3.0 - 2.0 * rating)


def change_from_open(price: Any, change_pct: Any, gap_pct: Any) -> float | None:
    """Percent change from the session open, derived from close/change/gap.

    The previous close cancels between ``change_pct`` and ``gap_pct``, so the
    result is the provider's own change-from-open without an open column. A
    denominator that is zero or negative (``gap_pct <= -100``, or a
    ``change_pct`` that implies a non-positive previous close) makes the
    percentage undefined: ``None``, never ``0.0``.
    """

    # ``close`` is an anchor, not a factor: the previous close cancels between
    # the two percentages below. An unpriced row still has no move to report.
    close = _price(price)
    change = _cell(change_pct)
    gap = _cell(gap_pct)
    if close is None or change is None or gap is None:
        return None
    close_ratio = 1.0 + change / 100.0
    open_ratio = 1.0 + gap / 100.0
    if close_ratio <= 0.0 or open_ratio <= 0.0:
        return None
    return _result((close_ratio / open_ratio - 1.0) * 100.0)


def pct_from_52w_high(price: Any, high_52w: Any) -> float | None:
    """Percent distance from the 52-week high: ``0`` at the high, negative below.

    One direction only: the low side has no derived column (see the module
    docstring). A missing or non-positive high is unknown.
    """

    close = _price(price)
    high = _price(high_52w)
    if close is None or high is None:
        return None
    return _result((close / high - 1.0) * 100.0)


def derive_row(row: Mapping[str, Any]) -> dict[str, float | None]:
    """Every derived column for ``row``, from cells the row already carries.

    Returns one key per :data:`DERIVED_FIELDS` entry, always present: a column
    whose inputs the provider did not send is ``None`` on the row, so a filter
    on it drops the row instead of raising ``KeyError`` or reading a zero.
    """

    return {
        "recom_finviz": recom_finviz(row.get("recommendation")),
        "change_from_open": change_from_open(
            row.get("price"), row.get("change_pct"), row.get("gap_pct")
        ),
        "pct_from_52w_high": pct_from_52w_high(row.get("price"), row.get("high_52w")),
    }


def _validate() -> None:
    """Fail at import if the three tables drift apart."""

    if tuple(DERIVED_FIELDS) != DERIVED_COLUMNS:
        raise RuntimeError("DERIVED_COLUMNS must follow DERIVED_FIELDS")
    if set(DERIVED_FIELDS) != set(DERIVED_INPUTS):
        raise RuntimeError(
            "every derived column needs its input list: "
            f"fields={sorted(DERIVED_FIELDS)}, inputs={sorted(DERIVED_INPUTS)}"
        )
    if set(derive_row({})) != set(DERIVED_FIELDS):
        raise RuntimeError("derive_row must answer every derived column, missing or not")
    for field, formula in DERIVED_FIELDS.items():
        if not formula.strip():
            raise RuntimeError(f"derived field {field!r} has an empty formula")
    for field, inputs in DERIVED_INPUTS.items():
        if not inputs:
            raise RuntimeError(f"derived field {field!r} reads no input")
        if field in inputs:
            raise RuntimeError(f"derived field {field!r} cannot be its own input")


_validate()

__all__ = [
    "DERIVED_COLUMNS",
    "DERIVED_FIELDS",
    "DERIVED_INPUTS",
    "RECOMMENDATION_MAX",
    "RECOMMENDATION_MIN",
    "change_from_open",
    "derive_row",
    "pct_from_52w_high",
    "recom_finviz",
]
