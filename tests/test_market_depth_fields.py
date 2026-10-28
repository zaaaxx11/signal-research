"""The 116-column field registry: coverage, ownership, quality, units.

These tests are the contract that keeps a provider payload from inventing a
column: every catalog key has exactly one spec, and every spec points at a real
catalog key.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from typing import Any, get_args

import pytest

from app.market import depth_fields
from app.market.depth_fields import (
    CATALOG_FIELDS,
    CATALOG_PATH,
    FIELD_SPECS,
    QUOTE_OWNED_FIELDS,
    FieldSpec,
    Quality,
    field_names,
    field_spec,
    is_catalog_field,
)

CATALOG_UNITS = frozenset({"text", "count", "USD", "ratio", "pct", "shares", "date", "unknown"})


def catalog_rows() -> list[dict[str, Any]]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def catalog_keys() -> list[str]:
    return [str(row["key"]) for row in catalog_rows()]


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def test_registry_covers_the_catalog_exactly() -> None:
    keys = catalog_keys()

    assert len(keys) == 116
    assert len(set(keys)) == 116, "the catalog itself must not repeat a key"
    assert len(FIELD_SPECS) == 116
    assert set(FIELD_SPECS) == set(keys)
    assert CATALOG_FIELDS == frozenset(keys)
    assert len(CATALOG_FIELDS) == 116


def test_field_names_keep_catalog_order() -> None:
    assert field_names() == tuple(catalog_keys())


def test_every_spec_names_itself_and_declares_a_known_unit() -> None:
    for name, spec in FIELD_SPECS.items():
        assert isinstance(spec, FieldSpec)
        assert spec.name == name
        assert spec.unit in CATALOG_UNITS, f"{name} has unit {spec.unit!r}"
        assert spec.owner.strip(), f"{name} has no owner"


def test_quality_is_a_closed_vocabulary() -> None:
    allowed = set(get_args(Quality))
    assert allowed == {"exact", "equivalent", "definition", "proxy", "missing"}
    for name, spec in FIELD_SPECS.items():
        assert spec.quality in allowed, f"{name} has quality {spec.quality!r}"


def test_specs_are_frozen() -> None:
    spec = FIELD_SPECS["market_cap"]
    with pytest.raises(FrozenInstanceError):
        spec.name = "not_market_cap"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


def test_units_follow_the_catalog_except_the_documented_overrides() -> None:
    for row in catalog_rows():
        name = str(row["key"])
        spec = FIELD_SPECS[name]
        if name in {"high_52w", "low_52w", "all_time_high", "all_time_low"}:
            assert spec.unit == "USD", (
                f"{name} is a dollar level; the catalog's 'pct' is a display hint"
            )
            continue
        assert spec.unit == row["unit"], f"{name} drifted from the catalog unit"


def test_debt_and_dividend_units_are_ratios_and_percent_points() -> None:
    assert FIELD_SPECS["debt_to_equity"].unit == "ratio"
    assert FIELD_SPECS["dividend_yield"].unit == "pct"
    assert FIELD_SPECS["short_interest"].unit == "shares"
    assert FIELD_SPECS["market_cap"].unit == "USD"


# ---------------------------------------------------------------------------
# Ownership + quality decisions
# ---------------------------------------------------------------------------


def test_yahoo_owns_108_columns() -> None:
    owned = [spec for spec in FIELD_SPECS.values() if spec.owner == "yahoo"]
    assert len(owned) == 108, "the research probe measures Yahoo at 108/116"


def test_quote_hub_fields_are_not_yahoo_depth_fields() -> None:
    assert QUOTE_OWNED_FIELDS == {"price", "change", "volume", "prev_close"}
    for name in QUOTE_OWNED_FIELDS:
        assert FIELD_SPECS[name].owner == "quote"


def test_short_interest_belongs_to_finra() -> None:
    spec = FIELD_SPECS["short_interest"]
    assert spec.owner == "finra"
    assert spec.owner != "yahoo"


def test_pfcf_is_a_definition_not_an_exact_match() -> None:
    spec = FIELD_SPECS["pfcf"]
    assert spec.quality == "definition"
    assert spec.quality != "exact"
    assert spec.formula is not None
    assert "freeCashflow" in spec.formula


def test_the_five_unfillable_yahoo_columns_are_marked_missing() -> None:
    missing = {name for name, spec in FIELD_SPECS.items() if spec.quality == "missing"}
    assert missing == {
        "shortable",
        "eps_past_5y",
        "eps_next_5y",
        "sales_past_5y",
        "revenue_surprise",
    }


def test_quality_labels_match_the_research_verdicts() -> None:
    # A verdict (identical definition).
    for name in ("market_cap", "pe", "roe", "debt_to_equity", "dividend_yield", "employees"):
        assert FIELD_SPECS[name].quality == "exact", name
    # B verdict (equivalent, needs a note).
    for name in ("earnings_date", "pc", "ev_ebitda", "sales_qoq", "short_ratio"):
        assert FIELD_SPECS[name].quality == "equivalent", name
    # C verdict (filled, different definition).
    for name in ("peg", "roa", "roic", "quick_ratio", "lt_debt_to_equity", "payout_ratio"):
        assert FIELD_SPECS[name].quality == "definition", name
    # Undocumented estimator.
    for name in ("volatility_1w", "volatility_4w"):
        assert FIELD_SPECS[name].quality == "proxy", name


def test_generated_columns_have_no_provider_owner() -> None:
    assert FIELD_SPECS["daily_digest"].owner == "product"
    assert FIELD_SPECS["sort_order"].owner == "screener"
    assert FIELD_SPECS["row_type"].owner == "screener"


# ---------------------------------------------------------------------------
# Formulas
# ---------------------------------------------------------------------------


def test_derived_cells_carry_their_formula() -> None:
    for name in (
        "pc",
        "pfcf",
        "float_pct",
        "eps_past_3y",
        "sales_past_3y",
        "eps_qoq",
        "sales_qoq",
        "eps_yoy_ttm",
        "roic",
        "lt_debt_to_equity",
        "sma20",
        "week_52_range",
    ):
        spec = FIELD_SPECS[name]
        assert spec.formula, f"{name} is derived but has no formula"


def test_direct_cells_do_not_pretend_to_have_a_formula() -> None:
    for name in ("market_cap", "pe", "employees", "ticker", "shares_outstanding"):
        assert FIELD_SPECS[name].formula is None, name


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def test_field_spec_returns_the_spec_for_a_known_column() -> None:
    spec = field_spec("debt_to_equity")
    assert spec is not None
    assert spec.name == "debt_to_equity"
    assert spec.unit == "ratio"


def test_field_spec_returns_none_for_a_foreign_name() -> None:
    assert field_spec("regularMarketPrice") is None
    assert field_spec("not_a_column") is None
    assert field_spec("") is None
    assert field_spec(None) is None  # type: ignore[arg-type]


def test_membership_guard_matches_the_registry() -> None:
    assert is_catalog_field("market_cap")
    assert is_catalog_field("  pe  ")
    assert not is_catalog_field("change_pct")
    assert not is_catalog_field("priceToBook")


def test_catalog_path_points_at_the_read_only_json() -> None:
    assert CATALOG_PATH.name == "finviz_columns_116.json"
    assert CATALOG_PATH.exists()
    assert CATALOG_PATH == depth_fields.CATALOG_PATH
