"""Phase-0 contract RED suite (02 §3.8.1, §3.2, §3.4, §4.3, §4.6).

The 92-column table is extracted mechanically into ``tests/spec/columns_v1.json``
from 02-architecture §3.8.1. Every assertion compares code against that frozen
file — never against a second copy pasted into the test.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from pydantic import ValidationError

from app.contracts import (
    CANONICAL_COLUMNS_V1,
    CORE_COLUMNS,
    DATA_QUALITY_VALUES,
    Capability,
    MarketPlan,
    MarketRequest,
    Observation,
    QuotePlanV1,
)
from tests._support import COLUMNS, SPEC, enrich_payload, quote_plan


def _first_diff(actual: list[str], expected: list[str]) -> str:
    for index, (left, right) in enumerate(zip(actual, expected)):
        if left != right:
            return f"first divergence at index {index}: actual={left!r} expected={right!r}"
    return f"length differs: actual={len(actual)} expected={len(expected)}"


# ---------------------------------------------------------------------------
# Spec fixture integrity (must be green: it proves the oracle itself is sane)
# ---------------------------------------------------------------------------


def test_spec_fixture_is_internally_consistent() -> None:
    assert SPEC["version"] == "v1"
    assert SPEC["total"] == 92
    assert len(COLUMNS) == 92
    assert len(set(COLUMNS)) == 92
    assert sum(group["declared_count"] for group in SPEC["groups"]) == 92
    for group in SPEC["groups"]:
        assert len(group["columns"]) == group["declared_count"], group["name"]
        assert len(set(group["columns"])) == group["declared_count"], group["name"]
    assert set(SPEC["core_columns"]) <= set(COLUMNS)
    assert len(SPEC["core_columns"]) == 5
    assert SPEC["data_quality"] == ["identity_only", "quote_only", "partial", "complete"]
    assert not (set(SPEC["forbidden_columns"]) & set(COLUMNS))
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in COLUMNS)


# ---------------------------------------------------------------------------
# CANONICAL_COLUMNS_V1 / CORE_COLUMNS / data_quality
# ---------------------------------------------------------------------------


def test_canonical_columns_v1_is_exactly_the_frozen_spec() -> None:
    actual = list(CANONICAL_COLUMNS_V1)
    assert len(actual) == 92, f"CANONICAL_COLUMNS_V1 has {len(actual)} columns, expected 92"
    assert actual == COLUMNS, _first_diff(actual, COLUMNS)


def test_canonical_columns_v1_has_no_duplicates() -> None:
    actual = list(CANONICAL_COLUMNS_V1)
    assert len(set(actual)) == len(actual) == 92, (
        f"expected 92 unique columns, got {len(actual)} entries / {len(set(actual))} unique"
    )


def test_column_groups_match_declared_counts_and_order() -> None:
    actual = list(CANONICAL_COLUMNS_V1)
    offset = 0
    for group in SPEC["groups"]:
        size = group["declared_count"]
        chunk = actual[offset : offset + size]
        assert chunk == group["columns"], (
            f"group {group['name']!r} ({size}) mismatch: {_first_diff(chunk, group['columns'])}"
        )
        offset += size
    assert offset == 92


def test_core_columns_is_exactly_the_five_identity_fields() -> None:
    assert frozenset(CORE_COLUMNS) == frozenset(SPEC["core_columns"])


def test_data_quality_values_are_a_closed_enum() -> None:
    assert tuple(DATA_QUALITY_VALUES) == tuple(SPEC["data_quality"])


def test_provenance_and_signal_columns_never_become_column_93() -> None:
    contract = set(CANONICAL_COLUMNS_V1) | set(CORE_COLUMNS)
    leaked = contract & set(SPEC["forbidden_columns"])
    assert not leaked, f"non-contract columns leaked into the table: {sorted(leaked)}"


# ---------------------------------------------------------------------------
# Capability
# ---------------------------------------------------------------------------


def test_capability_has_exactly_the_eight_provider_capabilities() -> None:
    expected = {
        "reference",
        "quote",
        "eod_bars",
        "fundamentals",
        "options",
        "constituents",
        "corporate_actions",
        "fx",
    }
    actual = {member.value for member in Capability}
    assert actual == expected, f"missing={sorted(expected - actual)} extra={sorted(actual - expected)}"
    assert Capability("quote") is Capability.QUOTE
    assert isinstance(Capability.QUOTE, str)


# ---------------------------------------------------------------------------
# MarketRequest
# ---------------------------------------------------------------------------


def test_market_request_accepts_a_valid_enrich_payload() -> None:
    request = MarketRequest.model_validate(enrich_payload())
    dumped = request.model_dump()
    assert dumped["market"]["kind"] == "watchlist"
    assert dumped["max_staleness_s"] == 900
    assert request.async_ is True  # alias "async" is accepted, Python name is async_


def test_market_request_rejects_an_unknown_knob() -> None:
    MarketRequest.model_validate(enrich_payload())  # baseline must be valid

    payload = enrich_payload()
    payload["bogus_knob"] = True
    with pytest.raises(ValidationError) as caught:
        MarketRequest.model_validate(payload)
    errors = caught.value.errors()
    assert any(
        error["type"] == "extra_forbidden" and "bogus_knob" in error["loc"] for error in errors
    ), errors


def test_market_request_rejects_a_field_name_outside_the_92_columns() -> None:
    MarketRequest.model_validate(enrich_payload())  # baseline must be valid

    payload = enrich_payload(fields=["last_price", "not_a_canonical_column"])
    with pytest.raises(ValidationError) as caught:
        MarketRequest.model_validate(payload)
    assert "not_a_canonical_column" in str(caught.value)


def test_market_request_rejects_an_unknown_market_kind() -> None:
    MarketRequest.model_validate(enrich_payload())  # baseline must be valid

    payload = enrich_payload(market={"kind": "nonsense", "symbols": ["AAPL"]})
    with pytest.raises(ValidationError):
        MarketRequest.model_validate(payload)


def test_market_request_watchlist_requires_symbols() -> None:
    MarketRequest.model_validate(enrich_payload())  # baseline must be valid

    with pytest.raises(ValidationError):
        MarketRequest.model_validate(enrich_payload(market={"kind": "watchlist"}))


def test_market_request_is_frozen() -> None:
    request = MarketRequest.model_validate(enrich_payload())
    with pytest.raises(ValidationError):
        request.max_staleness_s = 1  # type: ignore[misc]


# ---------------------------------------------------------------------------
# MarketPlan
# ---------------------------------------------------------------------------


def _plan_payload(batch_size: int = 100) -> dict[str, Any]:
    return {
        "batches": [
            {
                "batch_index": 0,
                "universe_slice": {"symbols": [f"S{index:03d}" for index in range(batch_size)]},
                "symbols_expected": batch_size,
                "max_rows": batch_size,
            }
        ],
        "mode": "balanced",
        "target_rows": batch_size,
    }


def test_market_plan_accepts_batches_of_at_most_100_symbols() -> None:
    plan = MarketPlan.model_validate(_plan_payload(100))
    dumped = plan.model_dump()
    assert dumped["batches"][0]["symbols_expected"] == 100
    assert dumped["mode"] == "balanced"


def test_market_plan_rejects_a_batch_over_100_symbols() -> None:
    MarketPlan.model_validate(_plan_payload(100))  # baseline must be valid

    with pytest.raises(ValidationError):
        MarketPlan.model_validate(_plan_payload(101))


def test_market_plan_rejects_an_unknown_knob() -> None:
    MarketPlan.model_validate(_plan_payload(1))  # baseline must be valid

    payload = _plan_payload(1)
    payload["invented"] = 1
    with pytest.raises(ValidationError) as caught:
        MarketPlan.model_validate(payload)
    assert any(error["type"] == "extra_forbidden" for error in caught.value.errors())


# ---------------------------------------------------------------------------
# QuotePlanV1
# ---------------------------------------------------------------------------


def test_quote_plan_v1_freezes_universe_fields_freshness_budget_retention() -> None:
    plan = QuotePlanV1.model_validate(quote_plan())
    dumped = plan.model_dump()
    assert plan.columns_version == "v1"
    assert dumped["universe"]["symbols"] == ["AAPL", "BBCA.JK"]
    assert dumped["budget"]["requests"] == 100
    assert plan.retention_days == 7


def test_quote_plan_v1_requires_a_sha256_plan_hash() -> None:
    plan = QuotePlanV1.model_validate(quote_plan())
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", plan.plan_hash), plan.plan_hash


def test_quote_plan_v1_rejects_an_unknown_knob() -> None:
    QuotePlanV1.model_validate(quote_plan())  # baseline must be valid

    payload = quote_plan()
    payload["mutable"] = True
    with pytest.raises(ValidationError) as caught:
        QuotePlanV1.model_validate(payload)
    assert any(error["type"] == "extra_forbidden" for error in caught.value.errors())


def test_quote_plan_v1_is_frozen() -> None:
    plan = QuotePlanV1.model_validate(quote_plan())
    with pytest.raises(ValidationError):
        plan.columns_version = "v2"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------


def _observation_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "instrument_id": "XNAS:AAPL",
        "field": "last_price",
        "value_num": 190.12,
        "value_text": None,
        "unit": "price:USD",
        "source": "fake",
        "source_id": "fake:quote:0",
        "as_of": "2026-10-21T20:00:00Z",
        "fetched_at": "2026-10-22T12:38:36Z",
        "confidence": 1.0,
        "verdict": "ok",
        "trust": 3,
    }
    payload.update(overrides)
    return payload


def test_observation_accepts_a_complete_provenance_envelope() -> None:
    observation = Observation.model_validate(_observation_payload())
    assert observation.field == "last_price"
    assert observation.as_of is not None
    assert observation.trust == 3


def test_observation_requires_as_of() -> None:
    Observation.model_validate(_observation_payload())  # baseline must be valid

    payload = _observation_payload()
    del payload["as_of"]
    with pytest.raises(ValidationError) as caught:
        Observation.model_validate(payload)
    assert any("as_of" in error["loc"] for error in caught.value.errors())


def test_observation_field_must_be_one_of_the_92_canonical_columns() -> None:
    Observation.model_validate(_observation_payload())  # baseline must be valid

    with pytest.raises(ValidationError):
        Observation.model_validate(_observation_payload(field="not_a_canonical_column"))


def test_observation_rejects_an_unknown_unit() -> None:
    Observation.model_validate(_observation_payload())  # baseline must be valid

    with pytest.raises(ValidationError):
        Observation.model_validate(_observation_payload(unit="banana"))


def test_observation_trust_is_bounded_1_to_4() -> None:
    Observation.model_validate(_observation_payload())  # baseline must be valid

    for bad in (0, 5):
        with pytest.raises(ValidationError):
            Observation.model_validate(_observation_payload(trust=bad))


def test_observation_confidence_is_a_probability() -> None:
    Observation.model_validate(_observation_payload())  # baseline must be valid

    for bad in (-0.01, 1.01):
        with pytest.raises(ValidationError):
            Observation.model_validate(_observation_payload(confidence=bad))


def test_observation_rejects_an_unknown_verdict_and_extra_knob() -> None:
    Observation.model_validate(_observation_payload())  # baseline must be valid

    with pytest.raises(ValidationError):
        Observation.model_validate(_observation_payload(verdict="looks_fine"))
    with pytest.raises(ValidationError) as caught:
        Observation.model_validate(_observation_payload(invented=1))
    assert any(error["type"] == "extra_forbidden" for error in caught.value.errors())
