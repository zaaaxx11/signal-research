"""Screen F1 — the 82-key Finviz filter catalogue and the ``field op value`` DSL.

Gate (PLAN-finviz-parity.md §5 P0, §6 gates 3 and 6):

* the catalogue carries exactly the 82 keys the probe found, with the probe's
  label/tab/control/option vocabulary untouched — a missing key is a parity hole
  and an extra key is a filter Finviz does not have;
* every key names a real canonical row column or ``None``, never a placeholder:
  filtering on a metric the light universe does not carry must fail loudly
  instead of returning an empty page that reads as "no matches";
* the DSL supports ``eq/ne/gt/gte/lt/lte/in/between/contains``, rejects an
  unknown field or operator with ``ValueError``, and is deterministic: the same
  rows and filters always give the same rows in the same order;
* an absent value never satisfies a predicate, ``ne`` included — unknown is not
  a match and it is not a mismatch either;
* ``build_universe`` keeps the documented USD 100 million floor by default, and
  ``min_market_cap=0`` is the explicit nano-cap view that still refuses a row
  with no market cap at all.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.screen.filters import (
    FIELDS,
    FILTER_CATALOG,
    FILTER_KEYS,
    FILTER_TABS,
    UI_TYPES,
    Filter,
    FilterSpec,
    apply_filters,
    compile_filter,
    parse_filters,
)
from app.screen.universe import (
    DERIVED_FIELDS,
    MIN_MARKET_CAP_USD,
    PARITY_FIELDS,
    UNIVERSE_COLUMNS,
    build_universe,
)
from tests import _screen as S

CATALOG_FIXTURE = S.FIXTURES / "finviz_filters.json"

#: Universe-shaped rows: a fully reported US tech name, a fully reported foreign
#: energy name, and a tech name whose price and cap the provider did not send.
#: The third row is what proves "unknown never passes" instead of asserting it.
ROWS: list[dict[str, Any]] = [
    {
        "symbol": "AAA",
        "name": "Alpha Industries Inc.",
        "exchange": "XNAS",
        "country": "United States",
        "sector": "Technology",
        "price": 100.0,
        "change_pct": 2.5,
        "volume": 1_000_000.0,
        "turnover": 100_000_000.0,
        "turnover_usd": 100_000_000.0,
        "market_cap_usd": 5_000_000_000.0,
        "currency": "USD",
        "is_primary": True,
    },
    {
        "symbol": "BBB",
        "name": "Borealis Energy Corp.",
        "exchange": "XNYS",
        "country": "Canada",
        "sector": "Energy",
        "price": 50.0,
        "change_pct": -1.5,
        "volume": 2_000_000.0,
        "turnover": 80_000_000.0,
        "turnover_usd": None,
        "market_cap_usd": 800_000_000.0,
        "currency": "CAD",
        "is_primary": True,
    },
    {
        "symbol": "CCC",
        "name": "Cinder Systems Inc.",
        "exchange": "XNAS",
        "country": "United States",
        "sector": "Technology",
        "price": None,
        "change_pct": 0.0,
        "volume": 0.0,
        "turnover": None,
        "turnover_usd": None,
        "market_cap_usd": None,
        "currency": "USD",
        "is_primary": True,
    },
]


def probe() -> list[dict[str, Any]]:
    return json.loads(CATALOG_FIXTURE.read_text(encoding="utf-8"))


def symbols(rows: list[dict[str, Any]]) -> list[str]:
    return [row["symbol"] for row in rows]


def _clone(region: str, source: str, **overrides: Any) -> dict[str, Any]:
    row = dict(S.fixture_row(region, source))
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


def test_catalogue_has_the_eighty_two_probed_keys_in_panel_order() -> None:
    expected = [entry["key"] for entry in probe()]

    missing = sorted(set(expected) - set(FILTER_KEYS))
    extra = sorted(set(FILTER_KEYS) - set(expected))
    assert not missing and not extra, f"missing keys: {missing}; extra keys: {extra}"
    assert len(FILTER_KEYS) == len(set(FILTER_KEYS)) == 82, (
        f"the catalogue must hold 82 unique keys, got {len(FILTER_KEYS)} "
        f"({len(set(FILTER_KEYS))} unique)"
    )
    assert FILTER_KEYS == tuple(expected), (
        "the catalogue must keep the probe's panel order; a different order means "
        "the metadata was retyped instead of copied"
    )


def test_catalogue_metadata_matches_the_probe_verbatim() -> None:
    mismatches: list[str] = []
    for entry in probe():
        spec = FILTER_CATALOG[entry["key"]]
        assert isinstance(spec, FilterSpec)
        for attribute, expected in (
            ("label", entry["label"]),
            ("tab", entry["tab"]),
            ("ui_type", entry["uiType"]),
            ("options", tuple(entry.get("options") or ())),
        ):
            actual = getattr(spec, attribute)
            if actual != expected:
                mismatches.append(
                    f"{entry['key']}.{attribute}: catalogue={actual!r} probe={expected!r}"
                )
        assert spec.tab in FILTER_TABS, f"{spec.key} has unknown tab {spec.tab!r}"
        assert spec.ui_type in UI_TYPES, f"{spec.key} has unknown control {spec.ui_type!r}"

    assert mismatches == [], "catalogue drifted from the Finviz probe:\n" + "\n".join(
        mismatches
    )


def test_only_sourced_metrics_carry_a_row_field() -> None:
    mapped = {
        key: spec.field for key, spec in FILTER_CATALOG.items() if spec.field is not None
    }
    assert mapped == {
        # -- the seven light columns (P0)
        "exch": "exchange",
        "sec": "sector",
        "geo": "country",
        "cap": "market_cap_usd",
        "sh_price": "price",
        "ta_change": "change_pct",
        "sh_curvol": "volume",
        # -- parity metrics copied from the bulk scan (P5)
        "earningsdate": "earnings_date",
        "sh_avgvol": "avg_volume_30d",
        "sh_relvol": "rel_volume_10d",
        "targetprice": "target_price",
        "sh_outstanding": "shares_outstanding",
        "sh_float": "float_shares",
        "fa_pe": "pe_ttm",
        "fa_peg": "peg",
        "fa_pb": "pb",
        "fa_pfcf": "pfcf",
        "fa_evebitda": "ev_ebitda",
        "fa_roa": "roa",
        "fa_roe": "roe",
        "fa_roi": "roic",
        "fa_curratio": "current_ratio",
        "fa_quickratio": "quick_ratio",
        "fa_debteq": "debt_to_equity",
        "fa_grossmargin": "gross_margin",
        "fa_opermargin": "oper_margin",
        "fa_netmargin": "profit_margin",
        "ta_rsi": "rsi_14",
        "ta_sma20": "sma_20",
        "ta_sma50": "sma_50",
        "ta_sma200": "sma_200",
        "ta_beta": "beta_1y",
        # -- columns a live probe showed filled (P7)
        "industry": "industry",
        "fa_div": "dividend_yield",
        "ta_gap": "gap_pct",
        "ta_averagetruerange": "atr_14",
        # -- derived from the parity cells above, no new scan column (P11B)
        "an_recom": "recom_finviz",
        "ta_changeopen": "change_from_open",
        "ta_highlow52w": "pct_from_52w_high",
    }, (
        "a filter key may only name a column a row really carries (a light column "
        f"or a parity metric); got {mapped}"
    )
    assert set(mapped.values()) <= set(FIELDS), (
        f"catalogue names fields with no comparison kind: {set(mapped.values()) - set(FIELDS)}"
    )
    # The other half of the claim: a field a key filters must be a field a row
    # can carry — a light column, a parity metric copied from the scan, or a
    # derived column computed from those cells.
    carried = set(UNIVERSE_COLUMNS) | set(PARITY_FIELDS.values()) | set(DERIVED_FIELDS)
    assert set(mapped.values()) <= carried, (
        "filter keys point at fields no row carries: "
        f"{sorted(set(mapped.values()) - carried)}"
    )

    unsourced = [spec.key for spec in FILTER_CATALOG.values() if spec.field is None]
    assert len(unsourced) == 82 - len(mapped) == 43
    assert all(not FILTER_CATALOG[key].is_available for key in unsourced), (
        "a key without a metric must report is_available == False"
    )


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("operator", "value", "expected"),
    [
        ("eq", 100.0, ["AAA"]),
        ("ne", 100.0, ["BBB"]),
        ("gt", 50.0, ["AAA"]),
        ("gte", 50.0, ["AAA", "BBB"]),
        ("lt", 100.0, ["BBB"]),
        ("lte", 100.0, ["AAA", "BBB"]),
        ("in", [50.0, 100.0], ["AAA", "BBB"]),
        ("between", (50.0, 100.0), ["AAA", "BBB"]),
    ],
)
def test_numeric_operators(operator: str, value: Any, expected: list[str]) -> None:
    # CCC has price=None, so it is in no result — including ``ne``: an unknown
    # price is not a price that differs from 100.
    filtered = apply_filters(ROWS, Filter("price", operator, value))
    assert symbols(filtered) == expected


def test_text_operators_compare_identifiers_exactly_and_search_substrings() -> None:
    assert symbols(apply_filters(ROWS, Filter("exchange", "eq", "XNAS"))) == ["AAA", "CCC"]
    assert symbols(apply_filters(ROWS, Filter("exchange", "in", ("XNYS",)))) == ["BBB"]
    assert symbols(apply_filters(ROWS, Filter("country", "ne", "United States"))) == ["BBB"]
    assert symbols(apply_filters(ROWS, Filter("name", "contains", "alpha"))) == ["AAA"], (
        "contains must be case-insensitive; a human types lowercase"
    )
    assert symbols(apply_filters(ROWS, Filter("name", "contains", "INDUSTRIES"))) == ["AAA"]
    assert apply_filters(ROWS, Filter("sector", "eq", "technology")) == [], (
        "eq is an identity comparison: 'technology' is not the canonical 'Technology'"
    )


def test_boolean_field_compares_by_value_not_truthiness() -> None:
    assert symbols(apply_filters(ROWS, Filter("is_primary", "eq", True))) == [
        "AAA",
        "BBB",
        "CCC",
    ]
    assert apply_filters(ROWS, Filter("is_primary", "eq", False)) == []


def test_between_is_inclusive_on_both_bounds() -> None:
    assert symbols(apply_filters(ROWS, Filter("price", "between", (50.0, 100.0)))) == [
        "AAA",
        "BBB",
    ]
    assert symbols(apply_filters(ROWS, Filter("price", "between", (51.0, 100.0)))) == ["AAA"]


# ---------------------------------------------------------------------------
# The DSL itself
# ---------------------------------------------------------------------------


def test_string_dsl_compiles_to_the_same_filters_as_objects() -> None:
    parsed = parse_filters("price >= 50; sector eq Technology")
    assert parsed == (
        Filter("price", "gte", 50.0),
        Filter("sector", "eq", "Technology"),
    )
    assert symbols(apply_filters(ROWS, "price >= 50; sector eq Technology")) == ["AAA"]


def test_string_dsl_supports_in_between_and_contains() -> None:
    assert parse_filters("exchange in (XNAS, XNYS)")[0].value == ("XNAS", "XNYS")
    assert parse_filters("price between 5..10")[0].value == (5.0, 10.0)
    assert parse_filters("name contains Industries")[0].value == "Industries"
    assert parse_filters("price != 50")[0].op == "ne"
    assert parse_filters('sector = "Energy"')[0].value == "Energy"


def test_parse_filters_accepts_mappings_triples_and_objects() -> None:
    parsed = parse_filters(
        [
            {"field": "sector", "op": "eq", "value": "Technology"},
            Filter("price", "gte", 50),
            ("volume", "gt", 0),
        ]
    )
    assert parsed == (
        Filter("sector", "eq", "Technology"),
        Filter("price", "gte", 50.0),
        Filter("volume", "gt", 0.0),
    )


def test_no_filters_is_the_identity_in_input_order() -> None:
    assert parse_filters(None) == parse_filters([]) == parse_filters("") == ()
    assert apply_filters(ROWS) == ROWS
    assert [id(row) for row in apply_filters(ROWS)] == [id(row) for row in ROWS]


# ---------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec",
    [
        Filter("pe", "gt", 10),
        {"field": "pe", "op": "gt", "value": 10},
        "pe > 10",
        ("pe", "gt", 10),
    ],
)
def test_unknown_field_is_a_value_error(spec: Any) -> None:
    with pytest.raises(ValueError, match="unknown filter field 'pe'"):
        compile_filter(spec)


def test_unknown_operator_is_a_value_error() -> None:
    with pytest.raises(ValueError, match="unknown filter operator 'approximately'"):
        compile_filter(Filter("price", "approximately", 10))


def test_operator_that_cannot_apply_to_the_field_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="needs a numeric field"):
        compile_filter(Filter("sector", "gt", "Technology"))
    with pytest.raises(ValueError, match="needs a text field"):
        compile_filter(Filter("price", "contains", "10"))


def test_malformed_values_are_rejected_instead_of_matching_nothing() -> None:
    with pytest.raises(ValueError, match="between"):
        compile_filter(Filter("price", "between", (100.0, 50.0)))
    with pytest.raises(ValueError, match="between"):
        compile_filter(Filter("price", "between", (50.0,)))
    with pytest.raises(ValueError, match="'in'"):
        compile_filter(Filter("price", "in", 50.0))
    with pytest.raises(ValueError, match="expects a number"):
        compile_filter(Filter("price", "gte", "expensive"))


# ---------------------------------------------------------------------------
# Determinism, combinations and unknown values
# ---------------------------------------------------------------------------


def test_filters_are_anded_and_keep_input_order() -> None:
    technology = Filter("sector", "eq", "Technology")
    assert symbols(apply_filters(ROWS, [technology])) == ["AAA", "CCC"]
    assert symbols(apply_filters(list(reversed(ROWS)), [technology])) == ["CCC", "AAA"], (
        "the filter must preserve input order, not sort or reorder"
    )

    assert symbols(apply_filters(ROWS, [technology, Filter("price", "gte", 50.0)])) == ["AAA"]
    assert symbols(apply_filters(ROWS, [technology, Filter("country", "eq", "Canada")])) == [], (
        "filters are ANDed; a row satisfying one predicate must not pass"
    )


def test_application_is_deterministic_and_does_not_mutate_rows() -> None:
    filters = [Filter("sector", "eq", "Technology"), Filter("price", "lte", 100.0)]
    snapshot = [dict(row) for row in ROWS]

    first = apply_filters(ROWS, filters)
    second = apply_filters(ROWS, filters)

    assert symbols(first) == ["AAA"]
    assert first == second, "the same rows and filters must produce the same result"
    assert all(any(row is original for original in ROWS) for row in first), (
        "apply_filters must pass the original rows through, not copies"
    )
    assert ROWS == snapshot, "filtering must not mutate the input rows"


def test_an_unknown_value_never_satisfies_a_predicate() -> None:
    for operator, value in (
        ("eq", 100.0),
        ("ne", 100.0),
        ("gt", 0.0),
        ("lt", 10_000.0),
        ("in", (50.0, 100.0)),
        ("between", (0.0, 1_000.0)),
    ):
        assert "CCC" not in symbols(apply_filters(ROWS, Filter("price", operator, value))), (
            f"price=None satisfied {operator}; unknown is not a comparison result"
        )


def test_non_finite_values_are_unknown_not_numbers() -> None:
    nan_row = {**ROWS[0], "symbol": "NAN", "price": float("nan")}
    assert apply_filters([nan_row], Filter("price", "ne", 100.0)) == [], (
        "NaN != 100 is True in Python, but a NaN price is unknown, so it must not pass"
    )


# ---------------------------------------------------------------------------
# Universe floor
# ---------------------------------------------------------------------------


def test_default_universe_floor_is_still_the_documented_hundred_million() -> None:
    rows = S.scan_rows("us")
    default = build_universe(rows, region="us")
    explicit = build_universe(rows, region="us", min_market_cap=MIN_MARKET_CAP_USD)

    assert MIN_MARKET_CAP_USD == 100_000_000
    assert len(default) == S.US_TOTAL, (
        f"the default floor must keep {S.US_TOTAL} US issuers, got {len(default)}"
    )
    assert "TINY" not in symbols(default), "USD 99,999,999 is below the default floor"
    assert [(row["symbol"], row["exchange"]) for row in default] == [
        (row["symbol"], row["exchange"]) for row in explicit
    ], "passing the constant explicitly must equal the default"


def test_min_market_cap_zero_admits_nano_caps_without_touching_other_rules() -> None:
    rows = S.scan_rows("us")
    default = build_universe(rows, region="us")
    nano = build_universe(rows, region="us", min_market_cap=0)

    assert symbols(nano) != symbols(default)
    assert set(symbols(nano)) - set(symbols(default)) == {"TINY"}, (
        "the nano-cap view may only add the sub-floor common stock; got "
        f"{sorted(set(symbols(nano)) - set(symbols(default)))}"
    )
    assert len(nano) == S.US_TOTAL + 1
    for excluded in ("XYZQ", "SPY", "VNQ", "O", "WAR", "PFD"):
        assert excluded not in symbols(nano), (
            f"{excluded} is OTC, a fund or a non-common instrument; a lower cap "
            "floor must not widen those rules"
        )


def test_the_floor_applies_to_the_global_tab_too() -> None:
    rows = S.scan_rows("global")
    default = build_universe(rows, region="global")
    nano = build_universe(rows, region="global", min_market_cap=0)

    assert set(symbols(nano)) - set(symbols(default)) == {"SMALL"}
    assert len(nano) == S.GLOBAL_TOTAL + 1


def test_min_market_cap_zero_still_drops_a_row_without_a_cap() -> None:
    missing = _clone("us", "AAPL", symbol="NOCAP", market_cap_basic=None)
    assert build_universe([missing], region="us", min_market_cap=0) == [], (
        "unknown is not zero: a row without a cap cannot enter a screen whose floor is 0"
    )

    zero = _clone("us", "AAPL", symbol="ZEROCAP", market_cap_basic=0)
    assert symbols(build_universe([zero], region="us", min_market_cap=0)) == ["ZEROCAP"], (
        "the floor is inclusive, so a reported cap of zero is inside a floor of zero"
    )


def test_a_negative_or_non_numeric_floor_is_refused() -> None:
    with pytest.raises(ValueError, match="min_market_cap"):
        build_universe([], region="us", min_market_cap=-1)
    with pytest.raises(ValueError, match="min_market_cap"):
        build_universe([], region="us", min_market_cap="cheap")  # type: ignore[arg-type]
