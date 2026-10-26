"""Engine catalog: the 116-column stock slice, frozen from Finviz evidence.

The catalog is not a design opinion. Finviz's screener answers exactly 116
stock columns; ``evidence/column_api_116.json`` (apiName/name/urlSort/category/
premium/sample) and ``evidence/api/screener-all-columns.json`` (one live row
with exactly those 116 keys) are the oracle. Every key here is one of them, in
the same order, and no other key is invented.

Scope of this slice:

* stocks only — the 74 ETF columns are deferred and deliberately absent. A
  missing ETF field is honest; a fabricated one is not.
* canonical units only — ``app.engine.catalog`` states the scaling the adapter
  owes before a provider value becomes a ``Cell``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.engine.catalog import (
    CATALOG,
    CATALOG_BY_KEY,
    CATALOG_VERSION,
    catalog_hash,
    get_metric,
)
from app.engine.model import MetricSpec, Unit

DATA_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "engine"
    / "data"
    / "finviz_columns_116.json"
)

#: The frozen 116 apiNames, in probe order.
EVIDENCE_API_NAMES: tuple[str, ...] = (
    "ticker", "company", "sector", "industry",
    "securityType", "country", "exchange", "index",
    "optionable", "shortable", "employees", "www",
    "earningsDate", "ipoDate", "marketCap", "enterpriseValue",
    "PE", "forwardPE", "PEG", "PS",
    "PB", "PC", "PFCF", "evEbitda",
    "evSales", "eps", "income", "sales",
    "estQ1", "epsYoY", "epsYoY1", "eps3Years",
    "eps5Years", "estLTGrowth", "sales3Years", "sales5Years",
    "epsQoQ", "salesQoQ", "epsYoYTTM", "salesYoYTTM",
    "epsSurprise", "revenueSurprise", "grossMargin", "operMargin",
    "netMargin", "roa", "roe", "roi",
    "curRatio", "quickRatio", "ltdebtEq", "debtEq",
    "book", "cashPerShare", "dividend", "dividendYield",
    "dividendTTM", "payoutRatio", "dividendExDate", "dividend1y",
    "dividend3y", "dividend5y", "sharesOutstanding2", "sharesFloat",
    "floatoutstandingpct", "insiderOwn", "insiderTrans", "instOwn",
    "instTrans", "shortInterestShare", "shortInterestRatio", "shortInterest",
    "perf1w", "perf4w", "perf13w", "perf26w",
    "perf52w", "perfYtd", "perf3y", "perf5y",
    "perf10y", "beta", "averageTrueRange", "volatility1w",
    "volatility4w", "sma20", "sma50", "sma200",
    "high50d", "low50d", "high52w", "low52w",
    "rsi", "highAt", "lowAt", "changeopen",
    "gap", "recom", "targetPrice", "newsUrl",
    "newsTitle", "newsTime", "wiimDailyDigest", "prevClose",
    "price", "change", "volume", "averageVolume",
    "relativeVolume", "open", "high", "low",
    "candlestick", "52Wrange", "order", "type",
)

CORE_API_NAMES: tuple[str, ...] = (
    "ticker",
    "company",
    "marketCap",
    "PE",
    "forwardPE",
    "eps",
    "sales",
    "price",
    "change",
    "volume",
)

CORE_KEYS: tuple[str, ...] = (
    "ticker",
    "company",
    "market_cap",
    "pe",
    "forward_pe",
    "eps",
    "sales",
    "price",
    "change",
    "volume",
)

#: Fields whose canonical unit is genuinely unclear, with the reason.
DOCUMENTED_UNKNOWN: dict[str, str] = {
    "candlestick": "numeric pattern code, not a measurement",
    "52Wrange": "composite {low, high} object, not one scalar",
}

#: Hand-checked units for the fields where a wrong unit would corrupt a screen.
EXPECTED_UNITS: dict[str, Unit] = {
    "ticker": Unit.TEXT,
    "company": Unit.TEXT,
    "marketCap": Unit.USD,
    "enterpriseValue": Unit.USD,
    "employees": Unit.COUNT,
    "earningsDate": Unit.DATE,
    "eps": Unit.USD,
    "income": Unit.USD,
    "sales": Unit.USD,
    "book": Unit.USD,
    "dividend": Unit.USD,
    "dividendYield": Unit.PERCENT,
    "payoutRatio": Unit.PERCENT,
    "sharesOutstanding2": Unit.SHARES,
    "sharesFloat": Unit.SHARES,
    "shortInterest": Unit.SHARES,
    "volume": Unit.SHARES,
    "averageVolume": Unit.SHARES,
    "PE": Unit.RATIO,
    "forwardPE": Unit.RATIO,
    "PEG": Unit.RATIO,
    "PS": Unit.RATIO,
    "PB": Unit.RATIO,
    "evEbitda": Unit.RATIO,
    "curRatio": Unit.RATIO,
    "debtEq": Unit.RATIO,
    "beta": Unit.RATIO,
    "rsi": Unit.RATIO,
    "recom": Unit.RATIO,
    "relativeVolume": Unit.RATIO,
    "grossMargin": Unit.PERCENT,
    "operMargin": Unit.PERCENT,
    "netMargin": Unit.PERCENT,
    "roa": Unit.PERCENT,
    "roe": Unit.PERCENT,
    "roi": Unit.PERCENT,
    "epsQoQ": Unit.PERCENT,
    "salesQoQ": Unit.PERCENT,
    "epsSurprise": Unit.PERCENT,
    "perf1w": Unit.PERCENT,
    "perfYtd": Unit.PERCENT,
    "sma200": Unit.PERCENT,
    "high52w": Unit.PERCENT,
    "lowAt": Unit.PERCENT,
    "change": Unit.PERCENT,
    "gap": Unit.PERCENT,
    "changeopen": Unit.PERCENT,
    "price": Unit.USD,
    "prevClose": Unit.USD,
    "open": Unit.USD,
    "high": Unit.USD,
    "low": Unit.USD,
    "targetPrice": Unit.USD,
    "averageTrueRange": Unit.USD,
}

CATEGORIES: frozenset[str] = frozenset(
    {
        "identification-classification",
        "valuation",
        "income-statements-ratios",
        "balance-sheet-ratios",
        "dividend",
        "ownership",
        "performance",
        "technical-indicators",
        "intraday",
        "sentiment-analyst-data",
        "news",
        "meta",
    }
)

REQUIRED_DATA_FIELDS: frozenset[str] = frozenset(
    {"apiName", "key", "label", "unit", "category", "views", "source_keys"}
)

_API_NAME_INDEX: dict[str, MetricSpec] = {
    spec.source_keys[0]: spec for spec in CATALOG
}


def _load_data() -> list[dict[str, object]]:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


# --- shape ---------------------------------------------------------------


def test_catalog_holds_exactly_the_116_evidence_columns() -> None:
    assert len(CATALOG) == 116
    assert tuple(spec.source_keys[0] for spec in CATALOG) == EVIDENCE_API_NAMES


def test_every_evidence_api_name_appears_exactly_once() -> None:
    seen = [key for spec in CATALOG for key in spec.source_keys]
    assert sorted(seen) == sorted(EVIDENCE_API_NAMES)
    assert len(seen) == len(set(seen)) == 116


def test_no_entry_smuggles_a_column_outside_the_evidence() -> None:
    declared = {key for spec in CATALOG for key in spec.source_keys}
    assert declared == set(EVIDENCE_API_NAMES)


def test_keys_are_unique_snake_case() -> None:
    keys = [spec.key for spec in CATALOG]
    assert len(keys) == len(set(keys)) == 116
    for key in keys:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", key), key
        assert "__" not in key
        assert not key.endswith("_")


def test_labels_are_nonempty_and_unambiguous() -> None:
    labels = [spec.label for spec in CATALOG]
    assert all(label.strip() for label in labels)
    assert len(labels) == len(set(labels)), "two columns must not share a label"


def test_entries_are_frozen_metric_specs() -> None:
    for spec in CATALOG:
        assert isinstance(spec, MetricSpec)
        with pytest.raises(FrozenInstanceError):
            spec.label = "mutated"  # type: ignore[misc]


# --- units ---------------------------------------------------------------


def test_every_unit_is_a_unit_member() -> None:
    for spec in CATALOG:
        assert isinstance(spec.unit, Unit), spec.key


@pytest.mark.parametrize(("api_name", "unit"), sorted(EXPECTED_UNITS.items()))
def test_units_are_honest_where_a_wrong_unit_would_corrupt_a_screen(
    api_name: str, unit: Unit
) -> None:
    assert _API_NAME_INDEX[api_name].unit is unit


def test_unknown_is_reserved_for_genuinely_unclear_fields() -> None:
    unknown = {spec.source_keys[0] for spec in CATALOG if spec.unit is Unit.UNKNOWN}
    assert unknown == set(DOCUMENTED_UNKNOWN)


def test_performance_and_volatility_columns_are_percent() -> None:
    for spec in CATALOG:
        api_name = spec.source_keys[0]
        if api_name.startswith(("perf", "volatility")):
            assert spec.unit is Unit.PERCENT, api_name


def test_ratio_columns_are_not_percentages() -> None:
    for spec in CATALOG:
        api_name = spec.source_keys[0]
        if api_name in {"PE", "forwardPE", "PS", "PB", "evEbitda", "evSales", "curRatio"}:
            assert spec.unit is Unit.RATIO, api_name


def test_million_scale_money_columns_are_usd_not_millions() -> None:
    for api_name in ("marketCap", "enterpriseValue", "income", "sales"):
        assert _API_NAME_INDEX[api_name].unit is Unit.USD, api_name


# --- identity and lookup -------------------------------------------------


def test_core_metrics_are_present_by_key_and_api_name() -> None:
    for key in CORE_KEYS:
        assert key in CATALOG_BY_KEY, key
    for api_name in CORE_API_NAMES:
        assert api_name in _API_NAME_INDEX, api_name


def test_catalog_by_key_maps_to_the_identical_objects() -> None:
    assert len(CATALOG_BY_KEY) == 116
    assert set(CATALOG_BY_KEY) == {spec.key for spec in CATALOG}
    for spec in CATALOG:
        assert CATALOG_BY_KEY[spec.key] is spec


def test_get_metric_returns_the_catalog_object() -> None:
    spec = get_metric("market_cap")
    assert spec is CATALOG_BY_KEY["market_cap"]
    assert (spec.label, spec.unit) == ("Market Cap", Unit.USD)


@pytest.mark.parametrize("key", ["", "not_a_column", "Market_Cap", "ticker "])
def test_get_metric_raises_key_error_for_anything_not_canonical(key: str) -> None:
    with pytest.raises(KeyError):
        get_metric(key)


def test_views_are_lowercase_unique_tuples() -> None:
    for spec in CATALOG:
        assert isinstance(spec.views, tuple), spec.key
        assert len(set(spec.views)) == len(spec.views), spec.key
        for view in spec.views:
            assert isinstance(view, str) and view and view == view.lower(), spec.key


# --- hash and version ----------------------------------------------------


def test_catalog_hash_is_stable_and_covers_every_key() -> None:
    digest = catalog_hash()
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert digest == catalog_hash()
    expected = hashlib.sha256("\n".join(sorted(CATALOG_BY_KEY)).encode("utf-8")).hexdigest()
    assert digest == expected


def test_catalog_hash_moves_when_a_key_is_added() -> None:
    base = catalog_hash()
    extended = hashlib.sha256(
        "\n".join(sorted([*CATALOG_BY_KEY, "a_column_that_is_not_real"])).encode("utf-8")
    ).hexdigest()
    assert extended != base


def test_catalog_version_is_a_pinned_iso_date() -> None:
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", CATALOG_VERSION)
    assert CATALOG_VERSION == "2026-10-26"


# --- the data file is the same contract ----------------------------------


def test_data_file_has_one_entry_per_column_with_the_required_shape() -> None:
    data = _load_data()
    assert isinstance(data, list)
    assert len(data) == 116
    for entry in data:
        assert set(entry) == REQUIRED_DATA_FIELDS, entry.get("apiName")
        assert entry["category"] in CATEGORIES, entry["apiName"]
        assert isinstance(entry["source_keys"], list)
        assert entry["source_keys"][0] == entry["apiName"]
        assert isinstance(entry["views"], list)
        assert Unit(entry["unit"]) in tuple(Unit)


def test_data_file_and_runtime_catalog_agree() -> None:
    data = _load_data()
    assert [entry["apiName"] for entry in data] == list(EVIDENCE_API_NAMES)
    assert [entry["key"] for entry in data] == [spec.key for spec in CATALOG]
    for entry, spec in zip(data, CATALOG, strict=True):
        assert entry["label"] == spec.label
        assert entry["unit"] == spec.unit.value
        assert tuple(entry["views"]) == spec.views
        assert tuple(entry["source_keys"]) == spec.source_keys


def test_blank_evidence_categories_are_filled_honestly() -> None:
    data = {entry["apiName"]: entry for entry in _load_data()}
    assert data["www"]["category"] == "identification-classification"
    assert data["52Wrange"]["category"] == "technical-indicators"
    assert data["order"]["category"] == "meta"
    assert data["type"]["category"] == "meta"


def test_the_documented_scale_and_the_deferred_etf_overlay_are_in_the_docstring() -> None:
    import app.engine.catalog as catalog_module

    doc = catalog_module.__doc__ or ""
    assert "116" in doc
    assert "1e6" in doc
    assert "ETF" in doc
