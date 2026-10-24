"""Stock selection — the 82-key gate, the validator, the executor and the schema.

Contract (PLAN-finviz-parity.md §6 gate 3, "filter parity: 82/82 keys; filter
expression deterministik"):

* every one of the 82 Finviz filter keys either resolves to a row column or is
  **refused by name** — 39 map (7 light columns + 29 parity metrics the bulk scan
  copies + 3 derived columns computed from them), 43 are refused with the metric
  they need, and none is silently ignored, because an ignored filter returns the
  unfiltered screen as if it matched;
* a selection is one of two modes and never a mixture: ``criteria``
  (filters + sort + limit over a region) or ``symbols`` (tickers + source +
  snapshot_id);
* an unknown field, an unknown operator and an unavailable key are all 422s with
  a sentence that names the offender; a *known* key with a Finviz option token
  (``mega``, ``u5``) is refused too — the option vocabulary is not interpreted;
* ``limit`` cannot widen the page: it is clamped to 20, and ``total`` counts every
  post-filter match rather than the rows that fit;
* same rows + same request = same document, twice, without touching the input;
* the request schema served by the route, the schema constant and
  ``schemas/stock_selection_request_v1.json`` are one document.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.screen import selection as selection_module
from app.screen.filter_fields import (
    CATALOGUE,
    ROW_FIELDS,
    UNAVAILABLE,
    UnavailableFilterKey,
    UnknownFilterKey,
    binding,
    field_for,
    is_available,
    mapped_keys,
    reason_for,
    unavailable_keys,
)
from app.screen.filters import FIELDS, FILTER_CATALOG, OPERATORS
from app.screen.rank import SORTS
from app.screen.selection import (
    DISPLAY_LIMIT,
    MODES,
    SELECTION_REQUEST_SCHEMA,
    SOURCES,
    SelectionError,
    parse_selection,
    select_rows,
)
from app.screen.snapshot import DISPLAY_ROWS, display_row
from app.screen.universe import DERIVED_FIELDS, PARITY_FIELDS, REGIONS, build_universe
from tests import _screen as S

FIXTURE = S.FIXTURES / "selection_request.json"

#: The moment the executor stamps on its rows; fixed so two runs are comparable.
AS_OF = "2026-10-23T12:00:00.000000+00:00"

#: Hand-counted from ``screen_scanner_us.json`` after the universe rules
#: (common stock, USD 100M floor, dedupe): the US tab holds 23 issuers.
US_UNIVERSE_TOTAL = 23

#: Every US row whose sector the provider prints as "Technology".
US_TECHNOLOGY = ["AAPL", "NVDA", "MSFT", "ASML", "ORCL", "AMD", "CRM"]


def universe(region: str = "us") -> list[dict[str, Any]]:
    return build_universe(S.scan_rows(region), region=region)


def fixture_payload() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def criteria(
    *filters: dict[str, Any],
    sort: str = "market_cap",
    limit: int = DISPLAY_LIMIT,
    region: str = "us",
) -> dict[str, Any]:
    block: dict[str, Any] = {"filters": list(filters), "sort": sort, "limit": limit}
    return {"mode": "criteria", "region": region, "criteria": block}


def run(payload: dict[str, Any], rows: list[dict[str, Any]] | None = None, *, region: str = "us", snapshot_id: str | None = None) -> dict[str, Any]:
    request = parse_selection(payload)
    selection = select_rows(
        rows if rows is not None else universe(region),
        request,
        region=region,
        as_of=AS_OF,
        snapshot_id=snapshot_id if snapshot_id is not None else request.snapshot_id,
    )
    return selection.as_document()


def symbols_of(document: dict[str, Any]) -> list[str]:
    return [row["symbol"] for row in document["rows"]]


def exactly_one(item: dict[str, Any]) -> bool:
    """A filter names a Finviz key xor a row column, never both and never neither."""

    return ("key" in item) != ("field" in item)


# ---------------------------------------------------------------------------
# 82 keys: a row column or a refusal that names the missing metric
# ---------------------------------------------------------------------------


def test_the_catalogue_has_eighty_two_keys_and_every_one_has_a_disposition() -> None:
    assert len(CATALOGUE) == 82, f"the Finviz catalogue is 82 keys, got {len(CATALOGUE)}"
    assert tuple(entry.key for entry in CATALOGUE) == tuple(FILTER_CATALOG), (
        "the binding catalogue must follow the probe's panel order"
    )
    assert set(mapped_keys()) | set(unavailable_keys()) == set(FILTER_CATALOG)
    assert not set(mapped_keys()) & set(unavailable_keys())
    assert len(mapped_keys()) == 39, (
        "seven light columns, the twenty-nine parity metrics a bulk scan copies "
        "and whose columns are really filled, and the three derived columns "
        "computed from those cells"
    )
    assert len(unavailable_keys()) == 43


def test_the_mapped_keys_are_the_columns_a_row_really_carries() -> None:
    light = {
        "exch": "exchange",
        "sec": "sector",
        "geo": "country",
        "cap": "market_cap_usd",
        "sh_curvol": "volume",
        "sh_price": "price",
        "ta_change": "change_pct",
    }
    assert {key: ROW_FIELDS[key] for key in light} == light, (
        "the seven light keys must keep the columns the browse row has always had"
    )
    beyond_light = {key: field for key, field in ROW_FIELDS.items() if key not in light}
    derived = {key: field for key, field in beyond_light.items() if field in DERIVED_FIELDS}
    parity = {key: field for key, field in beyond_light.items() if key not in derived}
    assert len(parity) == 29
    assert set(parity.values()) <= set(PARITY_FIELDS.values()), (
        "every copied key must point at a provider column the parity scan requests; "
        f"not carried: {sorted(set(parity.values()) - set(PARITY_FIELDS.values()))}"
    )
    assert derived == {
        "an_recom": "recom_finviz",
        "ta_changeopen": "change_from_open",
        "ta_highlow52w": "pct_from_52w_high",
    }, (
        "the three derived keys are the documented exception (app.screen.derived): "
        "an identity over cells the row already carries, not a provider copy"
    )
    for key in mapped_keys():
        assert binding(key).field == FILTER_CATALOG[key].field
        assert binding(key).field in FIELDS
        assert is_available(key) is True
        assert reason_for(key) is None


@pytest.mark.parametrize("key", list(unavailable_keys()))
def test_every_unavailable_key_is_refused_with_a_reason_not_ignored(key: str) -> None:
    spec = FILTER_CATALOG[key]
    with pytest.raises(UnavailableFilterKey) as caught:
        binding(key)
    error = caught.value
    assert error.code == "unavailable_filter_key"
    message = str(error)
    assert key in message, f"the refusal must name the key, got {message!r}"
    assert spec.label in message, f"the refusal must name the Finviz label, got {message!r}"
    assert UNAVAILABLE[key] in message, "the refusal must carry the reason"
    assert "refused instead of ignored" in message
    with pytest.raises(UnavailableFilterKey):
        field_for(key)


def test_a_key_outside_the_catalogue_is_unknown_and_lists_what_works() -> None:
    with pytest.raises(UnknownFilterKey) as caught:
        binding("fa_not_a_metric")
    assert caught.value.code == "unknown_filter_key"
    message = str(caught.value)
    assert "fa_not_a_metric" in message
    for key in mapped_keys():
        assert key in message, f"an unknown key should list the usable keys, missing {key}"
    assert is_available("fa_not_a_metric") is False


def test_reasons_name_the_metric_that_is_missing() -> None:
    assert "index membership" in (reason_for("idx") or "")
    assert "dividend" in (reason_for("fa_divgrowth") or "")
    assert "ten performance windows" in (reason_for("ta_perf") or "")
    for key in ("an_recom", "ta_changeopen", "ta_highlow52w"):
        assert reason_for(key) is None, (
            f"{key} filters a derived column now (app.screen.derived); a refusal "
            "here would be a stale disposition"
        )
    assert "answered null for all" in (reason_for("fa_fpe") or ""), (
        "a column that is null for every row cannot filter: refuse it, do not "
        "return an empty screen as if nothing matched"
    )
    assert "ETF holdings" in (reason_for("etf_heldby") or "")
    for key in unavailable_keys():
        reason = reason_for(key)
        assert reason and reason.strip(), f"{key} needs a reason"
        assert len(reason) > 20, f"{key}'s reason is too short to be useful: {reason!r}"
    for key in (
        "fa_pe",
        "ta_rsi",
        "ta_beta",
        "ta_sma200",
        "industry",
        "fa_div",
        "ta_gap",
        "ta_averagetruerange",
        "an_recom",
        "ta_changeopen",
        "ta_highlow52w",
    ):
        assert reason_for(key) is None, f"{key} filters a row column; it is not refused"


def test_no_placeholder_field_is_ever_offered() -> None:
    for key, spec in FILTER_CATALOG.items():
        entry = binding(key) if is_available(key) else None
        if spec.field is None:
            assert entry is None, f"{key} has no column but resolved to one"
        else:
            assert entry is not None and entry.field == spec.field


def test_the_boot_gate_refuses_a_key_with_no_disposition(monkeypatch: Any) -> None:
    """The coverage check must bite: an unclassified key is a boot error."""

    from types import MappingProxyType

    import app.screen.filter_fields as fields

    chopped = MappingProxyType(
        {key: reason for key, reason in fields.UNAVAILABLE.items() if key != "ta_perf"}
    )
    monkeypatch.setattr(fields, "UNAVAILABLE", chopped)
    with pytest.raises(RuntimeError, match="ta_perf"):
        fields._validate()


def test_the_boot_gate_refuses_a_mapping_that_drifted_from_the_catalogue(
    monkeypatch: Any,
) -> None:
    """A key may not claim a column the catalogue does not carry."""

    from types import MappingProxyType

    import app.screen.filter_fields as fields

    drifted = MappingProxyType({**dict(fields.ROW_FIELDS), "fa_pe": "pe_ratio"})
    monkeypatch.setattr(fields, "ROW_FIELDS", drifted)
    with pytest.raises(RuntimeError, match="drifted"):
        fields._validate()


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def test_the_fixture_parses_into_a_criteria_request() -> None:
    request = parse_selection(fixture_payload())
    assert request.mode == "criteria"
    assert request.is_criteria and not request.is_symbols
    assert request.region == "us"
    assert request.sort == "market_cap"
    assert request.limit == DISPLAY_LIMIT
    assert [criterion.field for criterion in request.filters] == [
        "market_cap_usd",
        "sector",
    ]
    assert request.filters[0].key == "cap"
    assert request.filters[0].op == "gte"
    assert request.filters[0].value == 1_000_000_000.0
    assert request.filters[1].key is None
    assert request.filters[1].value == "Technology"
    assert request.symbols == () and request.source is None and request.snapshot_id is None


def test_an_absent_filter_list_is_the_identity() -> None:
    request = parse_selection({"mode": "criteria", "region": "us", "criteria": {}})
    assert request.filters == ()
    assert request.sort == "market_cap"
    assert request.limit == DISPLAY_LIMIT


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"mode": "query"},
        {"mode": 7},
    ],
)
def test_a_missing_or_unknown_mode_is_rejected(payload: dict[str, Any]) -> None:
    with pytest.raises(SelectionError) as caught:
        parse_selection(payload)
    assert caught.value.code == "invalid_selection"
    assert caught.value.status_code == 422


def test_an_unknown_top_level_knob_is_rejected_not_ignored() -> None:
    payload = {"mode": "criteria", "region": "us", "criteria": {}, "typo": True}
    with pytest.raises(SelectionError) as caught:
        parse_selection(payload)
    assert "'typo'" in str(caught.value)


def test_a_misplaced_limit_is_pointed_at_criteria() -> None:
    payload = {"mode": "criteria", "region": "us", "criteria": {}, "limit": 5}
    with pytest.raises(SelectionError) as caught:
        parse_selection(payload)
    assert "'limit' belongs inside 'criteria'" in str(caught.value)


def test_criteria_mode_refuses_the_symbols_knobs() -> None:
    payload = {
        "mode": "criteria",
        "region": "us",
        "criteria": {},
        "symbols": ["AAPL"],
        "source": "tradingview",
        "snapshot_id": "scr_x",
    }
    with pytest.raises(SelectionError) as caught:
        parse_selection(payload)
    assert "mode 'criteria'" in str(caught.value)


@pytest.mark.parametrize("region", [None, "idx", "US"])
def test_criteria_mode_needs_a_known_region(region: str | None) -> None:
    payload: dict[str, Any] = {"mode": "criteria", "criteria": {}}
    if region is not None:
        payload["region"] = region
    with pytest.raises(SelectionError) as caught:
        parse_selection(payload)
    assert caught.value.code == "invalid_selection"
    assert "region" in str(caught.value)


def test_an_unknown_sort_is_rejected() -> None:
    with pytest.raises(SelectionError) as caught:
        parse_selection(criteria(sort="momentum"))
    assert "unknown sort" in str(caught.value)
    assert ", ".join(SORTS) in str(caught.value)


@pytest.mark.parametrize("limit", [0, -1, True, "20", 2.5, []])
def test_a_limit_that_is_not_a_positive_integer_is_rejected(limit: Any) -> None:
    with pytest.raises(SelectionError) as caught:
        parse_selection(criteria(limit=limit))
    assert caught.value.code == "invalid_selection"
    assert "limit" in str(caught.value)


def test_a_limit_above_the_display_window_is_clamped_at_parse_time() -> None:
    assert parse_selection(criteria(limit=100)).limit == DISPLAY_LIMIT
    assert parse_selection(criteria(limit=DISPLAY_LIMIT)).limit == DISPLAY_LIMIT
    assert parse_selection(criteria(limit=1)).limit == 1


def symbols_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": "symbols",
        "symbols": ["NVDA", "AAPL"],
        "source": "tradingview",
        "snapshot_id": "scr_us_market_cap_20261023T120000000000_00000000",
    }
    payload.update(overrides)
    return payload


def test_a_symbols_selection_parses_and_keeps_request_order() -> None:
    request = parse_selection(symbols_payload(symbols=["CRM", "AAPL"]))
    assert request.is_symbols and not request.is_criteria
    assert request.symbols == ("CRM", "AAPL")
    assert request.source == "tradingview"
    assert request.snapshot_id and request.snapshot_id.startswith("scr_")
    assert request.filters == () and request.sort is None and request.region is None
    assert request.limit == DISPLAY_LIMIT, "a ticker list is capped like any display"


def test_symbols_mode_refuses_the_query_knobs() -> None:
    with pytest.raises(SelectionError) as caught:
        parse_selection(symbols_payload(region="us"))
    assert "mode 'symbols'" in str(caught.value)
    with pytest.raises(SelectionError):
        parse_selection(symbols_payload(criteria={"filters": []}))


@pytest.mark.parametrize(
    "override",
    [
        {"symbols": []},
        {"symbols": "AAPL"},
        {"symbols": [""]},
        {"symbols": ["AAPL", 7]},
        {"symbols": [" AAPL"]},
        {"symbols": ["AAPL", "AAPL"]},
        {"source": None},
        {"source": "yahoo"},
        {"snapshot_id": None},
        {"snapshot_id": ""},
        {"snapshot_id": 7},
    ],
)
def test_a_malformed_symbols_selection_is_rejected(override: dict[str, Any]) -> None:
    payload = symbols_payload(**override)
    if override.get("source", "tradingview") is None:
        del payload["source"]
    if override.get("snapshot_id", "x") is None:
        del payload["snapshot_id"]
    with pytest.raises(SelectionError) as caught:
        parse_selection(payload)
    assert caught.value.code == "invalid_selection"
    assert caught.value.status_code == 422


def test_a_filter_must_name_exactly_one_source() -> None:
    both = criteria({"key": "cap", "field": "market_cap_usd", "op": "gte", "value": 1})
    neither = criteria({"op": "gte", "value": 1})
    for payload in (both, neither):
        with pytest.raises(SelectionError) as caught:
            parse_selection(payload)
        assert "exactly one of 'key'" in str(caught.value)


def test_a_filter_item_cannot_carry_an_unknown_knob() -> None:
    payload = criteria({"field": "price", "op": "gt", "value": 1, "opper": "gt"})
    with pytest.raises(SelectionError) as caught:
        parse_selection(payload)
    assert "'opper'" in str(caught.value)
    assert "does not accept" in str(caught.value)


def test_a_filter_must_carry_op_and_value() -> None:
    with pytest.raises(SelectionError) as caught:
        parse_selection(criteria({"field": "price", "op": "gt"}))
    assert "value" in str(caught.value)


def test_unknown_field_and_unknown_operator_are_both_422() -> None:
    with pytest.raises(SelectionError) as field:
        parse_selection(criteria({"field": "pe_ratio", "op": "gte", "value": 5}))
    assert field.value.code == "invalid_filter"
    assert "unknown filter field" in str(field.value)

    with pytest.raises(SelectionError) as operator:
        parse_selection(criteria({"field": "price", "op": "approx", "value": 5}))
    assert operator.value.code == "invalid_filter"
    assert "unknown filter operator" in str(operator.value)
    assert operator.value.status_code == 422


def test_an_operator_that_cannot_apply_to_the_kind_is_rejected() -> None:
    with pytest.raises(SelectionError) as caught:
        parse_selection(criteria({"field": "sector", "op": "gte", "value": 5}))
    assert "needs a numeric field" in str(caught.value)


def test_an_unavailable_key_is_a_422_that_names_it() -> None:
    with pytest.raises(SelectionError) as caught:
        parse_selection(criteria({"key": "fa_divgrowth", "op": "lte", "value": 10}))
    assert caught.value.code == "unavailable_filter_key"
    message = str(caught.value)
    assert "fa_divgrowth" in message and "Dividend Growth" in message
    assert "cannot be applied" in message


def test_a_parity_key_now_filters_instead_of_being_refused() -> None:
    request = parse_selection(criteria({"key": "fa_pe", "op": "lte", "value": 10}))
    assert request.filters[0].field == "pe_ttm"
    assert request.filters[0].key == "fa_pe"
    assert request.filters[0].value == 10.0


@pytest.mark.parametrize(
    ("key", "field", "op", "value"),
    [
        ("industry", "industry", "eq", "Semiconductors"),
        ("fa_div", "dividend_yield", "gte", 2.5),
        ("ta_gap", "gap_pct", "lte", -0.5),
        ("ta_averagetruerange", "atr_14", "between", (1.0, 5.0)),
    ],
)
def test_the_four_probed_keys_parse_into_their_row_columns(
    key: str, field: str, op: str, value: Any
) -> None:
    """The request path, not just the catalogue: a key must reach its column."""

    criterion = parse_selection(criteria({"key": key, "op": op, "value": value})).filters[0]
    assert criterion.field == field
    assert criterion.key == key
    assert criterion.op == op


def test_a_finviz_option_token_is_not_interpreted_as_a_value() -> None:
    """``cap: mega`` is a UI bucket; the DSL wants the number the bucket means."""

    with pytest.raises(SelectionError) as caught:
        parse_selection(criteria({"key": "cap", "op": "gte", "value": "mega"}))
    assert caught.value.code == "invalid_filter"
    assert "expects a number" in str(caught.value)


def test_filters_parse_into_in_and_between_tuples() -> None:
    request = parse_selection(
        criteria(
            {"key": "exch", "op": "in", "value": ["XNAS", "XNYS"]},
            {"field": "price", "op": "between", "value": [10, 50]},
        )
    )
    assert request.filters[0].field == "exchange"
    assert request.filters[0].value == ("XNAS", "XNYS")
    assert request.filters[1].value == (10.0, 50.0)


# ---------------------------------------------------------------------------
# Executor — criteria
# ---------------------------------------------------------------------------


def test_criteria_returns_the_matches_and_the_post_filter_total() -> None:
    document = run(fixture_payload())

    assert document["mode"] == "criteria"
    assert document["region"] == "us"
    assert document["source"] == "tradingview"
    assert document["snapshot_id"] is None
    assert document["sort"] == "market_cap"
    assert document["limit"] == DISPLAY_LIMIT
    assert document["universe_total"] == US_UNIVERSE_TOTAL
    assert document["total"] == 7, "the post-filter total counts every match, not the page"
    assert document["returned"] == 7
    assert document["missing"] == [] and document["symbols"] == []
    assert symbols_of(document) == US_TECHNOLOGY
    assert [row["rank"] for row in document["rows"]] == list(range(1, 8))
    assert document["filters"] == [
        {"key": "cap", "field": "market_cap_usd", "op": "gte", "value": 1_000_000_000.0},
        {"key": None, "field": "sector", "op": "eq", "value": "Technology"},
    ]


def test_the_page_is_cut_after_the_total_is_counted() -> None:
    document = run(criteria({"key": "sec", "op": "eq", "value": "Technology"}, limit=2))

    assert document["returned"] == 2
    assert document["total"] == 7, f"a 2-row page still answers '7 stocks match', got {document['total']}"
    assert symbols_of(document) == US_TECHNOLOGY[:2]
    assert document["limit"] == 2


def test_a_large_limit_cannot_widen_the_page_past_twenty() -> None:
    document = run(criteria(limit=100))

    assert document["limit"] == DISPLAY_LIMIT, (
        "limit is clamped: the display window is 20 rows, and the envelope must "
        f"report the applied value, got {document['limit']}"
    )
    assert document["returned"] == DISPLAY_LIMIT
    assert document["total"] == US_UNIVERSE_TOTAL
    assert document["rows"][-1]["rank"] == DISPLAY_LIMIT


def test_no_filter_is_the_identity_on_the_universe() -> None:
    document = run(criteria(limit=23))
    assert document["universe_total"] == document["total"] == US_UNIVERSE_TOTAL
    assert document["returned"] == DISPLAY_LIMIT


def test_a_market_cap_threshold_selects_the_expected_issuers() -> None:
    document = run(
        criteria({"field": "market_cap_usd", "op": "gte", "value": 1_000_000_000_000})
    )
    assert symbols_of(document) == [
        "AAPL",
        "NVDA",
        "MSFT",
        "GOOGL",
        "AMZN",
        "NFLX",
        "META",
        "TSLA",
        "BRK.B",
    ]
    assert document["total"] == 9


def test_the_sort_orders_the_filtered_set_not_the_universe() -> None:
    document = run(
        criteria({"key": "sec", "op": "eq", "value": "Technology"}, sort="gainers")
    )
    assert symbols_of(document) == ["NVDA", "ASML", "AAPL", "ORCL", "MSFT", "CRM", "AMD"], (
        "gainers must order the seven Technology matches by change_pct"
    )


def test_filters_are_anded() -> None:
    document = run(
        criteria(
            {"key": "sec", "op": "eq", "value": "Technology"},
            {"field": "price", "op": "gte", "value": 500},
        )
    )
    assert symbols_of(document) == ["MSFT", "ASML"]
    assert document["total"] == 2


def test_i_keys_and_queries_produce_the_same_selection() -> None:
    by_key = run(criteria({"key": "cap", "op": "gte", "value": 3_000_000_000_000}))
    by_field = run(
        criteria({"field": "market_cap_usd", "op": "gte", "value": 3_000_000_000_000})
    )
    assert symbols_of(by_key) == symbols_of(by_field) == ["AAPL", "NVDA", "MSFT", "GOOGL"]
    assert by_key["total"] == by_field["total"] == 4


def test_an_unknown_value_never_satisfies_a_filter() -> None:
    rows = [
        {"symbol": "AAA", "price": 10.0, "sector": "Technology", "is_primary": True},
        {"symbol": "BBB", "price": None, "sector": "Technology", "is_primary": True},
    ]
    document = run(criteria({"key": "sh_price", "op": "gte", "value": 1}), rows=rows)
    assert symbols_of(document) == ["AAA"], "a null price is unknown, not a zero"
    assert document["total"] == 1
    assert document["universe_total"] == 2


def test_a_document_with_in_and_between_serialises_to_json() -> None:
    """``in``/``between`` values are tuples internally and arrays on the wire."""

    document = run(
        criteria(
            {"key": "exch", "op": "in", "value": ["XNAS"]},
            {"field": "price", "op": "between", "value": [100, 400]},
        )
    )
    assert symbols_of(document) == ["AAPL", "NVDA", "GOOGL", "AMZN", "TSLA", "AMD"]
    assert document["total"] == 6
    assert document["filters"][0]["value"] == ["XNAS"]
    assert document["filters"][1]["value"] == [100.0, 400.0]
    encoded = json.dumps(document)
    assert '"value": ["XNAS"]' in encoded
    assert json.loads(encoded) == document


def test_rows_are_display_shaped_and_dated() -> None:
    document = run(criteria({"key": "sec", "op": "eq", "value": "Technology"}, limit=1))
    row = document["rows"][0]
    expected = set(display_row({"rank": 1}, as_of=AS_OF))
    assert set(row) == expected, f"a selection row is a browse row, got {sorted(row)}"
    assert row["as_of"] == AS_OF
    assert row["is_primary"] is True


# ---------------------------------------------------------------------------
# Executor — symbols
# ---------------------------------------------------------------------------


def test_symbols_resolve_in_request_order_and_name_the_missing_ones() -> None:
    snapshot = "scr_us_market_cap_20261023T120000000000_deadbeef"
    document = run(
        {"mode": "symbols", "symbols": ["CRM", "AAPL", "NOPE"], "source": "tradingview", "snapshot_id": snapshot},
        snapshot_id=snapshot,
    )

    assert document["mode"] == "symbols"
    assert document["snapshot_id"] == snapshot
    assert document["sort"] is None
    assert document["limit"] == DISPLAY_LIMIT
    assert symbols_of(document) == ["CRM", "AAPL"], "request order, not universe order"
    assert document["missing"] == ["NOPE"], "an unknown ticker is named, never an empty row"
    assert document["total"] == 2 and document["returned"] == 2
    assert [row["rank"] for row in document["rows"]] == [1, 2]
    assert document["rows"][0]["as_of"] == AS_OF


def test_a_symbol_list_longer_than_the_window_is_capped_but_counted() -> None:
    everything = [row["symbol"] for row in universe()]
    document = run(
        {
            "mode": "symbols",
            "symbols": everything,
            "source": "tradingview",
            "snapshot_id": "scr_any",
        },
        snapshot_id="scr_any",
    )
    assert document["returned"] == DISPLAY_LIMIT
    assert document["total"] == US_UNIVERSE_TOTAL
    assert document["missing"] == []


def test_symbols_mode_without_a_snapshot_is_a_programming_error() -> None:
    request = parse_selection(symbols_payload())
    with pytest.raises(ValueError):
        select_rows(universe(), request, region="us", as_of=AS_OF)


# ---------------------------------------------------------------------------
# Determinism and purity
# ---------------------------------------------------------------------------


def test_two_identical_selections_produce_identical_documents() -> None:
    payload = fixture_payload()
    first = run(payload)
    second = run(payload)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_filter_order_does_not_change_the_result() -> None:
    technology = {"key": "sec", "op": "eq", "value": "Technology"}
    expensive = {"field": "price", "op": "gte", "value": 500}
    first = run(criteria(technology, expensive))
    second = run(criteria(expensive, technology))
    assert symbols_of(first) == symbols_of(second)
    assert first["total"] == second["total"]


def test_the_executor_does_not_mutate_its_input() -> None:
    rows = universe()
    before = copy.deepcopy(rows)
    run(criteria({"key": "sec", "op": "eq", "value": "Technology"}), rows=rows)
    assert rows == before, "select_rows must not rank, filter or date the caller's rows in place"


def test_the_universe_is_not_the_display_table() -> None:
    """A 20-row display is not a universe: the total has to see all 23."""

    rows = universe()
    assert len(rows) == US_UNIVERSE_TOTAL > DISPLAY_LIMIT
    document = run(criteria(), rows=rows)
    assert document["universe_total"] == 23 and document["total"] == 23


def test_the_regions_and_the_global_universe_work_too() -> None:
    document = run(
        criteria({"key": "geo", "op": "eq", "value": "JP"}, region="global"),
        region="global",
    )
    assert document["region"] == "global"
    assert symbols_of(document) == ["7203", "6758"]
    assert document["total"] == 2


# ---------------------------------------------------------------------------
# The published schema
# ---------------------------------------------------------------------------


def test_the_schema_is_the_repo_artifact() -> None:
    artifact = Path(__file__).resolve().parents[1] / "schemas" / "stock_selection_request_v1.json"
    assert artifact.exists(), (
        "the request schema clients validate against must exist at "
        "schemas/stock_selection_request_v1.json"
    )
    assert json.loads(artifact.read_text(encoding="utf-8")) == SELECTION_REQUEST_SCHEMA


def test_the_schema_publishes_all_eighty_two_keys_with_their_disposition() -> None:
    extension = SELECTION_REQUEST_SCHEMA["x-filter-keys"]
    assert isinstance(extension, dict)
    assert SELECTION_REQUEST_SCHEMA["x-filter-key-count"] == 82
    assert list(extension) == list(FILTER_CATALOG), "panel order, same as the catalogue"
    mapped = {key: entry for key, entry in extension.items() if entry["field"] is not None}
    refused = {key: entry for key, entry in extension.items() if entry["field"] is None}
    assert set(mapped) == set(mapped_keys()), (
        "the schema must publish exactly the keys that can filter a row today"
    )
    assert set(refused) == set(unavailable_keys())
    for key, entry in mapped.items():
        assert entry["field"] == FILTER_CATALOG[key].field
        assert entry["tab"] == FILTER_CATALOG[key].tab
        column = FILTER_CATALOG[key].field
        assert column is not None
        assert entry["kind"] == FIELDS[column]
        assert "reason" not in entry
    for key, entry in refused.items():
        assert entry["reason"] == UNAVAILABLE[key]
        assert entry["tab"] == FILTER_CATALOG[key].tab
        assert "kind" not in entry


def test_the_schema_vocabularies_are_the_code_vocabularies() -> None:
    properties = SELECTION_REQUEST_SCHEMA["properties"]
    assert properties["mode"]["enum"] == list(MODES)
    assert properties["region"]["enum"] == list(REGIONS)
    assert properties["criteria"]["properties"]["sort"]["enum"] == list(SORTS)
    assert properties["criteria"]["properties"]["limit"]["minimum"] == 1
    assert properties["criteria"]["properties"]["limit"]["default"] == DISPLAY_LIMIT
    assert "maximum" not in properties["criteria"]["properties"]["limit"], (
        "a limit above 20 is clamped, not rejected, so the schema must not forbid it"
    )
    assert properties["symbols"]["uniqueItems"] is True
    assert properties["source"]["enum"] == list(SOURCES)

    definition = SELECTION_REQUEST_SCHEMA["$defs"]["filter"]
    assert definition["properties"]["op"]["enum"] == sorted(OPERATORS)
    assert definition["properties"]["field"]["enum"] == sorted(FIELDS)
    assert definition["additionalProperties"] is False
    assert definition["oneOf"] == [{"required": ["key"]}, {"required": ["field"]}]


def test_the_schema_requires_what_the_validator_requires() -> None:
    """The mode branches are the validator's own rules, written down."""

    branches = {branch["if"]["properties"]["mode"]["const"]: branch for branch in SELECTION_REQUEST_SCHEMA["allOf"]}
    assert set(branches) == set(MODES)
    criteria_required = branches["criteria"]["then"]["required"]
    symbols_required = branches["symbols"]["then"]["required"]
    assert criteria_required == ["region", "criteria"]
    assert symbols_required == ["symbols", "source", "snapshot_id"]

    full_criteria = fixture_payload()
    for name in criteria_required:
        payload = copy.deepcopy(full_criteria)
        del payload[name]
        with pytest.raises(SelectionError, match="criteria|region"):
            parse_selection(payload)

    full_symbols = symbols_payload()
    for name in symbols_required:
        payload = copy.deepcopy(full_symbols)
        del payload[name]
        with pytest.raises(SelectionError):
            parse_selection(payload)


def test_the_fixture_obeys_the_published_schema() -> None:
    """A structural check: no JSON Schema engine, but the same closed vocabularies."""

    payload = fixture_payload()
    schema = SELECTION_REQUEST_SCHEMA
    assert set(payload) <= set(schema["properties"])
    assert payload["mode"] in schema["properties"]["mode"]["enum"]
    assert payload["region"] in schema["properties"]["region"]["enum"]

    block = payload["criteria"]
    assert set(block) <= set(schema["properties"]["criteria"]["properties"])
    assert block["sort"] in schema["properties"]["criteria"]["properties"]["sort"]["enum"]
    assert isinstance(block["limit"], int) and block["limit"] >= 1

    values = schema["$defs"]["filter"]["properties"]
    for item in block["filters"]:
        assert set(item) == {"key", "op", "value"} or set(item) == {"field", "op", "value"}, item
        assert exactly_one(item)
        assert item["op"] in values["op"]["enum"]
        if "field" in item:
            assert item["field"] in values["field"]["enum"]
        else:
            assert item["key"] in schema["x-filter-keys"]


def test_the_schema_is_versioned_and_self_describing() -> None:
    assert SELECTION_REQUEST_SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert str(SELECTION_REQUEST_SCHEMA["$id"]).endswith("/stock-selection-request-v1.json")
    assert SELECTION_REQUEST_SCHEMA["title"] == "StockSelectionRequestV1"
    assert SELECTION_REQUEST_SCHEMA["additionalProperties"] is False
    assert SELECTION_REQUEST_SCHEMA["required"] == ["mode"]


def test_the_display_limit_is_the_browse_window() -> None:
    assert DISPLAY_LIMIT == DISPLAY_ROWS == 20
    assert selection_module.DISPLAY_LIMIT == 20, (
        "the selection page and the browse page must be the same window"
    )
