"""Fase 4 gate 3 — units are parsed and scaled, never coerced to zero.

Three separate questions, three separate answers:

* ``parse_number`` — does this provider string hold a number at all?
* ``to_percent`` — what percent does this value mean (0.0031 -> 0.31, 0.25 IV -> 25)?
* ``canonical`` — what do we store (yield in percent points, IV as a fraction)?

The failure this suite exists to prevent is 04 §1 D4/T4: a sentinel or a
mis-scaled unit becoming a number that looks plausible. ``"N/A"`` is not ``0``;
``0`` is only ever produced from a real zero.
"""

from __future__ import annotations

import pytest

from tests._phase4 import SENTINELS, units_module


# ---------------------------------------------------------------------------
# parse_number: the only door from provider text into a float
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", SENTINELS, ids=[repr(value) for value in SENTINELS])
def test_parse_number_never_turns_a_sentinel_into_zero(raw: object) -> None:
    assert units_module().parse_number(raw, unit="number") is None, (
        f"parse_number({raw!r}) must be None; a sentinel is an absence, not the value 0"
    )


def test_parse_number_rejects_free_text_instead_of_guessing() -> None:
    parse = units_module().parse_number
    for raw in ("abc", "n.a.", "not available", "TBD", "1.2.3"):
        assert parse(raw, unit="number") is None, raw


@pytest.mark.parametrize("raw", ["0", "0.0", "0.00", 0, 0.0], ids=repr)
def test_a_real_zero_stays_zero(raw: object) -> None:
    assert units_module().parse_number(raw, unit="number") == 0.0


def test_parse_number_reads_a_thousands_separator() -> None:
    assert units_module().parse_number("1,234.5", unit="number") == pytest.approx(1234.5)
    assert units_module().parse_number("1,234,567", unit="number") == pytest.approx(1234567.0)


def test_parse_number_strips_a_percent_suffix_without_rescaling() -> None:
    assert units_module().parse_number("12.5%", unit="percent") == pytest.approx(12.5)
    assert units_module().parse_number("-3.25 %", unit="percent") == pytest.approx(-3.25)


def test_parse_number_is_strict_about_the_sign_and_the_decimal_point() -> None:
    parse = units_module().parse_number
    assert parse("+7.5", unit="number") == pytest.approx(7.5)
    assert parse("-0.5", unit="number") == pytest.approx(-0.5)
    assert parse("1 234", unit="number") is None, "a space separator is not guessed"


# ---------------------------------------------------------------------------
# to_percent: what the reader sees
# ---------------------------------------------------------------------------


def test_dividend_yield_fraction_is_reported_as_a_percent() -> None:
    # 0.0031 as a fraction is 0.31%, the exact gate-3 example of 02 §5 Fase 4.
    assert units_module().to_percent(0.0031, field="dividend_yield") == pytest.approx(0.31)


def test_a_declared_percent_is_not_scaled_twice() -> None:
    assert units_module().to_percent(0.31, field="dividend_yield", unit="percent") == pytest.approx(0.31)
    assert units_module().to_percent(5.0, field="dividend_yield", unit="percent") == pytest.approx(5.0)


def test_a_declared_fraction_is_scaled_once() -> None:
    assert units_module().to_percent(0.0031, field="dividend_yield", unit="fraction") == pytest.approx(0.31)


def test_iv_fraction_is_reported_as_a_percent() -> None:
    assert units_module().to_percent(0.25, field="opt_iv_atm") == pytest.approx(25.0)


def test_iv_percent_is_reported_unchanged() -> None:
    # 25 already means 25%; multiplying again would claim 2500% volatility.
    assert units_module().to_percent(25, field="opt_iv_atm") == pytest.approx(25.0)
    assert units_module().to_percent(112.7, field="opt_iv_atm") == pytest.approx(112.7)


def test_iv_at_or_below_five_is_read_as_a_fraction() -> None:
    to_percent = units_module().to_percent
    assert to_percent(4.0, field="opt_iv_atm") == pytest.approx(400.0)
    assert to_percent(5.0, field="opt_iv_atm") == pytest.approx(500.0)
    assert to_percent(5.01, field="opt_iv_atm") == pytest.approx(5.01)


# ---------------------------------------------------------------------------
# canonical: what we store
# ---------------------------------------------------------------------------


def test_canonical_stores_dividend_yield_in_percent_points() -> None:
    assert units_module().canonical(0.0031, field="dividend_yield", unit="fraction") == pytest.approx(0.31)
    assert units_module().canonical(0.31, field="dividend_yield", unit="percent") == pytest.approx(0.31)


def test_canonical_stores_iv_as_a_fraction() -> None:
    assert units_module().canonical(25, field="opt_iv_atm") == pytest.approx(0.25)
    assert units_module().canonical(0.25, field="opt_iv_atm") == pytest.approx(0.25)
    assert units_module().canonical(112.7, field="opt_iv_atm") == pytest.approx(1.127)


@pytest.mark.parametrize("raw", SENTINELS, ids=[repr(value) for value in SENTINELS])
def test_a_sentinel_never_reaches_a_percent_column(raw: object) -> None:
    units = units_module()
    assert units.to_percent(raw, field="dividend_yield") is None
    assert units.canonical(raw, field="dividend_yield") is None
    assert units.to_percent(raw, field="opt_iv_atm") is None
    assert units.canonical(raw, field="opt_iv_atm") is None


def test_zero_is_only_produced_from_a_real_number() -> None:
    units = units_module()
    produced = [units.to_percent(raw, field="dividend_yield") for raw in SENTINELS]
    assert produced == [None] * len(SENTINELS), (
        f"a sentinel produced {produced}; only a real numeric zero may become 0"
    )
    assert units.to_percent("0", field="dividend_yield") == pytest.approx(0.0)
    assert units.canonical(0.0, field="opt_iv_atm") == pytest.approx(0.0)
