"""Fase 4 gates 1, 2 and 4 — V1..V16 as a table with a pass and a fail fixture each.

Gate 1: every rule is registered, and a failing fixture produces ``None`` (or the
documented series/flag effect of 02 §3.7) plus a rejection naming the right
column and reason. Gate 2: the rules that the mutation list names (V1, V4) are
exercised by name, not only through the table. Gate 4: a semantic zero —
``change_pct = 0`` while the price moved — becomes ``None``, never ``0.0``.

The rule oracle is ``tests/fixtures/verification_rules_v1.json``: it is compared
against both the registry and the fixtures, so the table cannot drift from the
architecture document without a test going red.
"""

from __future__ import annotations

import copy

import pytest

from app.contracts import CANONICAL_COLUMNS_V1
from tests._phase4 import (
    CRITICAL_RULES,
    NOW,
    RULE_CASES,
    apply,
    apply_case,
    assert_rejection_shape,
    clean_context,
    clean_row,
    column_of,
    evaluate,
    fixture_table,
    registered_rules,
    rejection_for,
    severity_of,
)

CASE_IDS = [case.label for case in RULE_CASES]


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


def test_the_registry_declares_v1_to_v16_in_order() -> None:
    ids = [str(record["id"]) for record in registered_rules()]
    assert ids == [f"V{index}" for index in range(1, 17)], ids
    assert len(set(ids)) == 16


def test_every_rule_declares_canonical_columns_and_a_known_severity() -> None:
    for record in registered_rules():
        columns = tuple(record["columns"])
        assert columns, f"{record['id']} declares no column it can reject"
        unknown = sorted(set(columns) - set(CANONICAL_COLUMNS_V1))
        assert not unknown, f"{record['id']} names non-canonical column(s) {unknown}"
        assert record["severity"] in {"critical", "major"}, record


def test_the_registry_and_the_fixtures_agree_with_the_json_oracle() -> None:
    oracle = fixture_table()
    records = {str(record["id"]): record for record in registered_rules()}
    assert set(records) == set(oracle) == {f"V{index}" for index in range(1, 17)}

    for rule, entry in oracle.items():
        assert records[rule]["severity"] == entry["severity"], rule
        cases = [case for case in RULE_CASES if case.rule == rule]
        assert cases, f"{rule} has no fixture"
        assert {case.reason for case in cases} == set(entry["reasons"]), rule
        assert {case.column for case in cases} == {entry["column"]}, rule


def test_each_failing_fixture_names_a_column_the_rule_owns() -> None:
    owners = {str(record["id"]): set(record["columns"]) for record in registered_rules()}
    for case in RULE_CASES:
        assert case.column in owners[case.rule], (
            f"{case.rule} rejects {case.column!r} but does not declare it: {sorted(owners[case.rule])}"
        )


def test_severity_is_critical_only_for_the_documented_rules() -> None:
    for rule in (f"V{index}" for index in range(1, 17)):
        expected = "critical" if rule in CRITICAL_RULES else "major"
        assert severity_of(rule) == expected, rule
    with pytest.raises(ValueError):
        severity_of("V99")


# ---------------------------------------------------------------------------
# Gate 1: one passing and one failing fixture per rule
# ---------------------------------------------------------------------------


def test_a_fully_consistent_row_passes_all_sixteen_rules() -> None:
    rejections = evaluate(clean_row(), clean_context())
    assert rejections == [], f"the clean fixture must pass every rule; got {rejections}"


@pytest.mark.parametrize("case", RULE_CASES, ids=CASE_IDS)
def test_the_passing_fixture_produces_no_rejection(case) -> None:
    row, context = case.fixture(False)
    assert evaluate(row, context) == [], f"{case.rule} rejects a valid row"


@pytest.mark.parametrize("case", RULE_CASES, ids=CASE_IDS)
def test_the_failing_fixture_nulls_the_value_and_writes_a_rejection(case) -> None:
    row, context = case.fixture(True)
    applied, rejections = apply(row, context)

    record = rejection_for(rejections, case.rule, case.column)
    assert_rejection_shape(record, rule=case.rule, column=case.column, reason=case.reason)
    assert record.get("instrument_id") == row["instrument_id"], record
    assert record.get("value_text") is not None, "the rejected value must be preserved as text"

    if case.verify is not None:
        case.verify(applied)
    else:
        assert applied[case.column] is None, (
            f"{case.rule} must null {case.column}; got {applied[case.column]!r} — "
            "clamping to a plausible value is a contract violation"
        )
        assert applied[case.column] != 0.0, f"{case.rule} replaced a bad value with a semantic zero"


@pytest.mark.parametrize("case", RULE_CASES, ids=CASE_IDS)
def test_every_rejection_carries_the_documented_envelope(case) -> None:
    _applied, rejections = apply_case(case)
    record = rejection_for(rejections, case.rule, case.column)
    assert {"rule", "column", "reason"} <= set(record), record
    assert column_of(record) == case.column
    assert isinstance(record["reason"], str) and record["reason"].strip()


def test_apply_is_pure_and_leaves_the_input_row_untouched() -> None:
    case = RULE_CASES[0]
    row, context = case.fixture(True)
    snapshot = copy.deepcopy(row)
    apply(row, context)
    assert row == snapshot, "apply must not mutate the row it is given"

    rejected = evaluate(row, context)
    assert row == snapshot
    assert rejected, "evaluate must not repair the row it inspects"


def test_an_unknown_rule_set_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        from tests._phase4 import rules_module

        rules_module().evaluate(clean_row(), context=clean_context(), rule_set="v99")


# ---------------------------------------------------------------------------
# Gate 2/4: the rules the mutation list and the brief name explicitly
# ---------------------------------------------------------------------------


def test_v1_zero_change_pct_beside_a_real_move_becomes_none() -> None:
    row = clean_row(change_pct=0.0)
    applied, rejections = apply(row, clean_context())

    assert applied["change_pct"] is None, (
        "0.0 next to last=100 from prev=90 is a false datum, not a flat day"
    )
    assert rejection_for(rejections, "V1", "change_pct")["reason"] == "change_pct_mismatch"


def test_v1_accepts_a_derived_change_pct_within_two_basis_points() -> None:
    # 100/90 - 1 = 11.11111%; 11.13 is 0.019 pp away, 11.14 is 0.029 pp away.
    assert evaluate(clean_row(change_pct=11.13), clean_context()) == []
    assert rejection_for(evaluate(clean_row(change_pct=11.14), clean_context()), "V1", "change_pct")


def test_v1_is_skipped_when_the_inputs_cannot_derive_it() -> None:
    row = clean_row(last_price=None, prev_close=None, change_pct=None)
    assert evaluate(row, clean_context()) == []


def test_v4_market_cap_within_two_percent_of_price_times_shares() -> None:
    context = clean_context()
    assert evaluate(clean_row(market_cap=1.01e11), context) == []
    applied, rejections = apply(clean_row(market_cap=1.03e11), context)
    assert applied["market_cap"] is None
    assert applied["shares_outstanding"] == pytest.approx(1.0e9), (
        "the shares are retained; only the derived cap is rejected"
    )
    assert rejection_for(rejections, "V4", "market_cap")["reason"] == "market_cap_mismatch"


def test_v4_allows_five_percent_for_a_multi_class_issuer() -> None:
    row = clean_row(market_cap=1.04e11)
    assert rejection_for(evaluate(row, clean_context()), "V4", "market_cap"), (
        "4% is outside the default 2% band"
    )
    assert evaluate(row, clean_context(multi_class=True)) == [], (
        "a multi-class issuer compares against a combined cap, so 5% is allowed"
    )
    assert rejection_for(
        evaluate(clean_row(market_cap=1.07e11), clean_context(multi_class=True)), "V4", "market_cap"
    )


def test_v6_dividend_yield_is_a_percentage_between_zero_and_hundred() -> None:
    context = clean_context()
    assert evaluate(clean_row(dividend_yield=0.0, dividend_per_share=0.0), context) == [], (
        "a real zero yield is a value"
    )
    applied, rejections = apply(clean_row(dividend_yield=500.0), context)
    assert applied["dividend_yield"] is None
    assert rejection_for(rejections, "V6", "dividend_yield")["reason"] == "dividend_yield_unit"


def test_v6_dividend_yield_agrees_with_dps_over_price() -> None:
    context = clean_context()
    # 5.0 per share on 100.0 is 5.0% — consistent.
    assert evaluate(clean_row(dividend_yield=5.0, dividend_per_share=5.0), context) == []
    # 0.0031 is a fraction that leaked into a percent column: 3.1/1000 = 0.31%.
    applied, rejections = apply(
        clean_row(dividend_yield=0.0031, dividend_per_share=3.1, last_price=1000.0), context
    )
    assert applied["dividend_yield"] is None
    assert rejection_for(rejections, "V6", "dividend_yield")["reason"] == "dividend_yield_inconsistent"


def test_v7_accepts_a_fraction_and_normalises_a_percent() -> None:
    context = clean_context()
    assert evaluate(clean_row(opt_iv_atm=0.25), context) == []

    normalised, rejections = apply(clean_row(opt_iv_atm=25.0), context)
    assert rejections == [], "25 is a percent form of 0.25 and must be accepted"
    assert normalised["opt_iv_atm"] == pytest.approx(0.25), (
        "the stored IV is a fraction; percents are normalised, not clamped"
    )


def test_v7_rejects_zero_and_anything_above_five_as_a_fraction() -> None:
    context = clean_context()
    for raw in (0.0, -0.1, 600.0):
        applied, rejections = apply(clean_row(opt_iv_atm=raw), context)
        assert applied["opt_iv_atm"] is None, raw
        assert rejection_for(rejections, "V7", "opt_iv_atm")["reason"] == "iv_unit"
        assert applied["opt_delta_atm"] is None, "greeks follow the IV out (02 §3.7 V7)"


def test_v7_reads_a_value_between_five_and_a_hundred_as_percent() -> None:
    applied, rejections = apply(clean_row(opt_iv_atm=6.0), clean_context())
    assert rejections == [], "6.0 is 6% (a 0.06 fraction), not 600%"
    assert applied["opt_iv_atm"] == pytest.approx(0.06)


def test_v8_greek_signs_are_bands_not_suggestions() -> None:
    context = clean_context()
    assert evaluate(clean_row(), context) == []
    for column, value in (
        ("opt_delta_atm", 1.2),
        ("opt_delta_atm", -0.2),
        ("opt_gamma_atm", -0.01),
        ("opt_vega_atm", -1.0),
    ):
        applied, rejections = apply(clean_row(**{column: value}), context)
        assert applied[column] is None, (column, value)
        assert rejection_for(rejections, "V8", column)["reason"] == "greek_sign"


def test_v9_put_call_parity_is_thirty_basis_points_of_spot() -> None:
    context = clean_context()
    # r = 0, K = S = 100: |C - P| is the whole parity error.
    assert evaluate(clean_row(opt_call_price=5.2, opt_put_price=5.0), context) == [], (
        "0.20 is inside the 0.30 band"
    )
    applied, rejections = apply(clean_row(opt_call_price=5.4, opt_put_price=5.0), context)
    assert rejection_for(rejections, "V9", "opt_iv_atm")["reason"] == "parity_violation"
    assert applied["opt_iv_atm"] is None and applied["opt_call_price"] is None, (
        "an arbitrage-sized parity gap invalidates the whole chain block"
    )
    assert applied["opt_delta_atm"] is None


def test_v9_index_parity_is_fifteen_basis_points() -> None:
    row = clean_row(opt_call_price=5.2, opt_put_price=5.0)
    assert evaluate(row, clean_context()) == [], "0.20 passes for an equity at 0.30%"
    assert rejection_for(
        evaluate(row, clean_context(asset_class="index")), "V9", "opt_iv_atm"
    ), "0.20 violates the tighter 0.15% index band"


def test_v13_freshness_is_measured_against_the_session_not_the_wall_clock() -> None:
    # In session: 60 s. The 300 s quote in the table is stale for that reason.
    fresh = clean_row(quote_as_of="2026-10-22T14:59:30+00:00")
    assert evaluate(fresh, clean_context()) == []

    stale = clean_row(quote_as_of="2026-10-22T14:55:00+00:00")
    rejection = rejection_for(evaluate(stale, clean_context()), "V13", "quote_as_of")
    assert rejection["reason"] == "stale_quote"


def test_v13_off_session_quotes_are_fresh_until_one_hour_after_the_close() -> None:
    row = clean_row(
        market_state="closed",
        session_date="2026-10-22",
        quote_as_of="2026-10-22T20:30:00+00:00",
    )
    before = clean_context(
        now="2026-10-22T20:45:00+00:00",
        market_state="closed",
        session_close="2026-10-22T20:00:00+00:00",
    )
    assert evaluate(row, before) == [], (
        "a 15-minute-old quote 45 minutes after the close is not stale"
    )

    after = clean_context(
        now="2026-10-22T22:00:00+00:00",
        market_state="closed",
        session_close="2026-10-22T20:00:00+00:00",
    )
    assert rejection_for(evaluate(row, after), "V13", "quote_as_of")["reason"] == "stale_quote"


def test_v13_honours_the_request_max_staleness_when_the_client_sets_one() -> None:
    row = clean_row(quote_as_of="2026-10-21T20:00:00+00:00")  # 19 h old
    assert evaluate(row, clean_context(max_staleness_s=86400)) == [], (
        "the request's max_staleness_s is the client's tolerance and wins"
    )
    assert rejection_for(
        evaluate(row, clean_context(max_staleness_s=3600)), "V13", "quote_as_of"
    )


def test_v14_tolerance_is_per_asset_class_and_idx_is_seventy_five_basis_points() -> None:
    def candidates(second: float) -> dict[str, list[dict[str, object]]]:
        return {
            "last_price": [
                {"source": "yahoo", "value": 100.0, "trust": 1, "as_of": "2026-10-22T14:59:30+00:00"},
                {"source": "scrape", "value": second, "trust": 2, "as_of": "2026-10-22T14:59:30+00:00"},
            ]
        }

    # 0.6% divergence: too wide for a US listing (0.20%), inside IDX's 0.75%.
    row = clean_row()
    assert rejection_for(
        evaluate(row, clean_context(candidates=candidates(100.6))), "V14", "last_price"
    )
    idx = clean_context(venue="XIDX", candidates=candidates(100.6))
    assert evaluate(row, idx) == [], "IDX tolerates 0.75% between independent sources"

    # 0.8% breaks even IDX.
    assert rejection_for(
        evaluate(row, clean_context(venue="XIDX", candidates=candidates(100.8))), "V14", "last_price"
    )


def test_v14_keeps_the_highest_trust_value_and_flags_the_cell() -> None:
    context = clean_context(
        candidates={
            "last_price": [
                {"source": "yahoo", "value": 100.0, "trust": 1, "as_of": "2026-10-22T14:59:30+00:00"},
                {"source": "scrape", "value": 105.0, "trust": 4, "as_of": "2026-10-22T14:59:30+00:00"},
            ]
        }
    )
    applied, rejections = apply(clean_row(), context)
    assert applied["last_price"] == pytest.approx(100.0)
    assert applied["disputed"] is True
    assert rejection_for(rejections, "V14", "last_price")["reason"] == "provider_divergence"


def test_v15_volume_band_and_a_real_zero() -> None:
    context = clean_context()
    assert evaluate(clean_row(day_volume=0), context) == [], "a zero-volume session is a fact"

    applied, rejections = apply(clean_row(day_volume=-1), context)
    assert applied["day_volume"] is None
    assert rejection_for(rejections, "V15", "day_volume")["reason"] == "nonpositive"

    applied, rejections = apply(clean_row(day_volume=2_000_000_000_000), context)
    assert applied["day_volume"] is None
    assert rejection_for(rejections, "V15", "day_volume")["reason"] == "unit_anomaly"


def test_v15_nonpositive_price_is_rejected() -> None:
    applied, rejections = apply(clean_row(last_price=0.0, change_pct=None), clean_context())
    assert applied["last_price"] is None
    assert rejection_for(rejections, "V15", "last_price")["reason"] == "nonpositive"


def test_v16_future_as_of_is_rejected_beyond_five_minutes_of_clock_skew() -> None:
    note = "2026-10-22T15:04:00+00:00"  # 4 minutes ahead of NOW
    assert evaluate(clean_row(quote_as_of=note), clean_context()) == [], (
        "five minutes of clock skew are tolerated"
    )

    far = "2026-10-22T15:10:00+00:00"
    applied, rejections = apply(clean_row(quote_as_of=far), clean_context())
    assert applied["quote_as_of"] is None
    assert rejection_for(rejections, "V16", "quote_as_of")["reason"] == "future_as_of"


def test_v11_a_quote_in_another_currency_empties_the_price_columns() -> None:
    applied, rejections = apply(
        clean_row(quote_currency="EUR"), clean_context(quote_currency="USD")
    )
    assert applied["last_price"] is None
    assert applied["bid"] is None and applied["ask"] is None
    assert applied["day_low"] is None
    assert rejection_for(rejections, "V11", "last_price")["reason"] == "currency_mismatch"


def test_every_rejection_keeps_the_rejected_value_as_text() -> None:
    for case in RULE_CASES:
        row, context = case.fixture(True)
        _applied, rejections = apply(row, context)
        record = rejection_for(rejections, case.rule, case.column)
        assert str(record["value_text"]).strip(), record
