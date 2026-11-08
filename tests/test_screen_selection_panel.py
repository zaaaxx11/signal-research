"""Phase 0 of the interface plan: a 100-row panel the API can answer in one call.

The plan (``.hermes/plans/2026-10-30_ui-interface.md`` §2) needs five backend
facts before a React grid can be built against this endpoint:

* **one page of a hundred rows** — ``limit`` accepts up to ``PANEL_LIMIT = 100``
  and a larger value is clamped, not rejected, because the envelope reports the
  limit that was applied;
* **a deterministic order** — ties in the rank metric break on ``symbol``, so
  page N and page N+1 cannot repeat an issuer. Five pages of 20 (measured) once
  contained two symbols twice: the rank order was not stable across calls;
* **column projection at size** — 100 rows × 12 ``columns`` must be under
  60,000 bytes, which means the projected row carries its cells and nothing
  else: no per-row logo, no per-cell provenance;
* **one filter vocabulary** — a catalogue key (``pe``, ``roic``,
  ``market_cap``, ``dividend_yield``) filters exactly like the Finviz key it
  already means (``fa_pe`` …), resolved through the depth overlay's own
  ``SCREEN_ALIASES`` rather than a second table;
* **the contract bugs of the UI audit** (§2.7) are closed: ``columns`` is
  honoured in symbols mode, symbols mode takes a ``limit``, and the four
  always-null keys (``ps``, ``short_float``, ``enterprise_value``,
  ``cash_per_share``) are filled for a symbol whose store really has the data.

Every new name is read through :func:`_api`, so the RED failure for a missing
constant is an ``AssertionError``/``NotImplementedError`` in the test body — the
suite never fails on an import.

Evidence: ``research/ui-surface-audit.md`` §2.3–§2.7.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.screen import selection as selection_module
from app.screen.depth_overlay import SCREEN_ALIASES, overlay
from app.screen.filters import FIELDS
from app.screen.fundamentals import apply_view_fill
from app.screen.rank import rank_rows
from app.screen.selection import SelectionError, parse_selection, select_rows

#: The moment the executor stamps on its rows; fixed so two runs are comparable.
AS_OF = "2026-10-30T00:00:00.000000+00:00"

#: The twelve cells a "value" panel asks for (§6.1 of the plan). Long names on
#: purpose: the byte budget has to hold for the real view, not a toy one.
VALUE_COLUMNS = (
    "market_cap",
    "enterprise_value",
    "pe",
    "forward_pe",
    "peg",
    "ps",
    "pb",
    "pc",
    "pfcf",
    "ev_ebitda",
    "ev_sales",
    "target_price",
)


def _api(name: str) -> Any:
    """A name of ``app.screen.selection``, as a RED failure while it is missing."""

    try:
        return getattr(selection_module, name)
    except AttributeError:  # pragma: no cover - RED path
        raise NotImplementedError(
            f"app.screen.selection.{name} is not implemented yet (interface Phase 0 RED)"
        ) from None


def panel_limit() -> int:
    return int(_api("PANEL_LIMIT"))


def criteria(*filters: dict[str, Any], limit: int | None = None, **extra: Any) -> dict[str, Any]:
    block: dict[str, Any] = {"filters": list(filters)}
    if limit is not None:
        block["limit"] = limit
    return {"mode": "criteria", "region": "us", "criteria": block, **extra}


def run(payload: dict[str, Any], rows: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    request = parse_selection(payload)
    return select_rows(
        rows, request, region="us", as_of=AS_OF, **kwargs
    ).as_document()


def tied_rows(count: int = 120) -> list[dict[str, Any]]:
    """Rows whose rank metric is identical: the tie is the whole test."""

    return [
        {
            "rank": 0,
            "symbol": f"S{index:03d}",
            "name": f"Tied Issuer {index:03d}",
            "exchange": "XNAS",
            "country": "United States",
            "sector": "Technology",
            "price": 10.0 + index,
            "change_pct": 0.5,
            "volume": 1_000.0,
            "turnover": 10_000.0,
            "turnover_usd": 10_000.0,
            "market_cap_usd": 5_000_000_000.0,
            "is_primary": True,
            "pe_ttm": 12.0 + index,
            "roic": 20.0 - index / 10,
        }
        for index in range(count)
    ]


def rotated(rows: list[dict[str, Any]], by: int) -> list[dict[str, Any]]:
    """The same rows in a different arrival order (what a live scan does)."""

    return rows[by:] + rows[:by]


def projected_rows(count: int = 120) -> list[dict[str, Any]]:
    """Rows carrying the twelve value-view cells, with realistic float noise."""

    rows = []
    for index in range(count):
        row = tied_rows(count)[index]
        row.update(
            {
                "enterprise_value": 5_120_000_000.123456 + index,
                "pe_forward": 11.987654321 + index / 7,
                "peg": 1.2345678901 + index / 1000,
                "ps": 4.5678901234 + index / 100,
                "pb": 0.9876543210 + index / 200,
                "price_to_cash": 18.123456789 + index,
                "pfcf": 22.987654321 + index / 3,
                "ev_ebitda": 13.456789012 + index,
                "ev_to_sales": 3.4567890123 + index / 11,
                "target_price": 240.987654321 + index,
            }
        )
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# (1) PANEL_LIMIT = 100
# ---------------------------------------------------------------------------


def test_the_panel_limit_is_a_hundred_rows() -> None:
    assert panel_limit() == 100, (
        "the interface renders 100 rows per view; the API must sell the same page"
    )


def test_a_limit_above_the_panel_is_clamped_not_rejected() -> None:
    limit = panel_limit()
    assert parse_selection(criteria(limit=limit)).limit == limit
    assert parse_selection(criteria(limit=limit + 1)).limit == limit
    assert parse_selection(criteria(limit=5_000)).limit == limit
    assert parse_selection(criteria(limit=None)).limit == limit, (
        "the default page is the panel, not the old 20-row display"
    )


def test_a_hundred_rows_come_back_from_one_request() -> None:
    limit = panel_limit()
    document = run(criteria(limit=limit), tied_rows(120))
    assert document["limit"] == limit
    assert document["returned"] == limit, (
        f"one call must fill the panel, got {document['returned']} rows"
    )
    assert document["total"] == 120 > document["returned"]
    assert [row["rank"] for row in document["rows"]] == list(range(1, limit + 1))


def test_the_schema_publishes_the_panel_limit() -> None:
    schema = _api("SELECTION_REQUEST_SCHEMA")
    limit = schema["properties"]["criteria"]["properties"]["limit"]
    assert limit["default"] == panel_limit()
    assert "maximum" not in limit, "a larger limit is clamped, so the schema must allow it"
    assert str(panel_limit()) in limit["description"]


# ---------------------------------------------------------------------------
# (2) Stable order: ties break on symbol
# ---------------------------------------------------------------------------


def test_ties_are_ordered_by_symbol_not_by_arrival() -> None:
    rows = tied_rows(30)
    first = [row["symbol"] for row in rank_rows(rows, "market_cap")]
    second = [row["symbol"] for row in rank_rows(rotated(rows, 7), "market_cap")]
    assert first == second, (
        "equal metrics must not inherit the provider's arrival order: "
        f"{first[:5]} vs {second[:5]}"
    )
    assert first == sorted(first), "the tie-break is the symbol, ascending"


def test_one_page_holds_a_hundred_distinct_symbols() -> None:
    limit = panel_limit()
    document = run(criteria(limit=limit), tied_rows(120))
    symbols = [row["symbol"] for row in document["rows"]]
    assert len(symbols) == limit
    assert len(set(symbols)) == limit, f"a symbol repeated inside one page: {symbols}"


def test_page_boundaries_do_not_overlap() -> None:
    """The measured bug: five pages of 20 repeated two symbols. Never again."""

    limit = panel_limit()
    rows = tied_rows(120)
    first = run(criteria(limit=limit // 2), rows)["rows"]
    second = run({**criteria(limit=limit // 2), "criteria": {"filters": [], "limit": limit // 2, "offset": limit // 2}}, rows)["rows"]
    left = [row["symbol"] for row in first]
    right = [row["symbol"] for row in second]
    assert set(left).isdisjoint(right), f"pages overlap: {sorted(set(left) & set(right))}"
    assert [row["rank"] for row in second] == list(range(limit // 2 + 1, limit + 1)), (
        "the second page continues the numbering instead of restarting it"
    )


def test_three_identical_requests_return_the_same_symbol_sequence() -> None:
    limit = panel_limit()
    rows = tied_rows(120)
    sequences = [
        [row["symbol"] for row in run(criteria(limit=limit), rotated(rows, by))["rows"]]
        for by in (0, 13, 61)
    ]
    assert sequences[0] == sequences[1] == sequences[2], (
        "three consecutive requests must be byte-identical in order; the page "
        "boundaries depend on it"
    )
    assert len(sequences[0]) == limit


# ---------------------------------------------------------------------------
# (3) Column projection at panel size
# ---------------------------------------------------------------------------


def test_a_hundred_rows_of_twelve_columns_stay_under_sixty_kilobytes() -> None:
    """The panel budget — and the values that make the budget mean something.

    The old version asserted only the key set and the byte size. Its fixture wrote
    ``pe_forward``/``price_to_cash``/``ev_to_sales`` while the request asked for
    ``forward_pe``/``pc``/``ev_sales``, so five cells were null by construction and
    the budget passed on a fully-null panel (null is four bytes). A budget that a
    column of nothings satisfies is not a test of this feature, so the values are
    asserted too.
    """

    limit = panel_limit()
    rows = projected_rows(120)
    document = run(criteria(limit=limit, columns=list(VALUE_COLUMNS)), rows)
    encoded = json.dumps(document, separators=(",", ":"), allow_nan=False)
    size = len(encoded.encode("utf-8"))
    print(f"\n12 columns x {limit} rows = {size:,} B")  # measured, printed

    assert document["returned"] == limit
    # Every cell is filled: the resolver reads the field the fixture wrote.
    for row in document["rows"]:
        assert set(row) == {"symbol", "as_of", *VALUE_COLUMNS}
        missing = [name for name in VALUE_COLUMNS if row[name] is None]
        assert missing == [], (
            f"{row['symbol']} carries null {missing}: a 12-column panel of nulls "
            "would satisfy the byte budget for the wrong reason"
        )
    # Three known cells, checked against the fixture's own numbers.
    first = document["rows"][0]
    assert first["pe"] == pytest.approx(rows[0]["pe_ttm"])
    assert first["pc"] == pytest.approx(rows[0]["price_to_cash"])
    assert first["ev_sales"] == pytest.approx(rows[0]["ev_to_sales"])
    assert first["market_cap"] == pytest.approx(rows[0]["market_cap_usd"])
    assert size < 60_000, (
        f"12 columns x {limit} rows is {size:,} B; the panel budget is 60,000 B "
        "(the full document is ~162 KB at this size)"
    )


def test_a_projected_row_carries_its_cells_and_nothing_else() -> None:
    document = run(criteria(limit=3, columns=list(VALUE_COLUMNS)), projected_rows())
    assert set(document["rows"][0]) == {"symbol", *VALUE_COLUMNS, "as_of"}, (
        "a projection is exactly the requested cells: no logo, no per-cell "
        "provenance, no source metadata"
    )
    for row in document["rows"]:
        assert "logo" not in row and "source_summary" not in row


def test_a_requested_logo_column_is_addressable() -> None:
    """``logo`` is part of the row contract, so it must be askable by name."""

    request = parse_selection(criteria(limit=1, columns=["symbol", "logo"]))
    assert request.columns == ("symbol", "logo")


# ---------------------------------------------------------------------------
# (4) The ETag: a canonical request, not a body hash
# ---------------------------------------------------------------------------

SYMBOLS = {
    "mode": "symbols",
    "symbols": ["CHEAP", "RICH"],
    "source": "tradingview",
    "snapshot_id": "scr_us_market_cap_20261030T000000000000_deadbeef",
}


def tag(payload: dict[str, Any], *, revision: str = "rev-a") -> str:
    generate = _api("selection_etag")
    return generate(request=parse_selection(payload), revision=revision)


def test_the_etag_is_strong_and_quoted() -> None:
    value = tag({"mode": "criteria", "region": "us", "criteria": {}})
    assert value.startswith('"') and value.endswith('"'), (
        f"a strong ETag is a quoted string, got {value!r}"
    )
    assert not value.startswith("W/"), "the tag validates the representation, not a hint"


def test_semantically_identical_requests_share_a_tag() -> None:
    bare = {"mode": "criteria", "region": "us", "criteria": {}}
    explicit = {
        "mode": "criteria",
        "region": "us",
        "asset_class": "stock",
        "criteria": {
            "filters": [],
            "sort": "market_cap",
            "limit": 100,
            "offset": 0,
            "common_only": False,
        },
        "columns": None,
        "fill_wide": False,
    }
    assert tag(bare) == tag(explicit), (
        "omitted defaults and explicit defaults select the same rows, so they "
        "must not produce two cache entries"
    )
    assert tag({**bare, "criteria": {"limit": 500}}) == tag({**bare, "criteria": {"limit": 101}}), (
        "both clamp to the same page"
    )


def test_a_changed_question_changes_the_tag() -> None:
    base = {"mode": "criteria", "region": "us", "criteria": {"filters": [
        {"key": "fa_pe", "op": "lt", "value": 20}]}}
    looser = {"mode": "criteria", "region": "us", "criteria": {"filters": [
        {"key": "fa_pe", "op": "lt", "value": 30}]}}
    other_op = {"mode": "criteria", "region": "us", "criteria": {"filters": [
        {"key": "fa_pe", "op": "lte", "value": 20}]}}
    other_key = {"mode": "criteria", "region": "us", "criteria": {"filters": [
        {"key": "fa_ps", "op": "lt", "value": 20}]}}
    for index, payload in enumerate((looser, other_op, other_key), start=2):
        assert tag(payload) != tag(base), f"variant {index} produced the same tag"

    assert tag({**base, "region": "global"}) != tag(base)
    assert tag(SYMBOLS) != tag({**SYMBOLS, "symbols": ["RICH", "CHEAP"]}), (
        "symbols mode answers in request order, so the order is part of the question"
    )
    assert tag(base, revision="rev-b") != tag(base), (
        "a new data revision invalidates the cached answer"
    )


def test_the_default_template_is_canonical() -> None:
    """Where the canonical form is stated, so a new key cannot slip in unnoticed."""

    canonicalise = _api("canonical_request")
    form = canonicalise(parse_selection({"mode": "criteria", "region": "us", "criteria": {}}))
    assert set(form) == {
        "mode",
        "region",
        "asset_class",
        "signal",
        "filters",
        "sort",
        "limit",
        "offset",
        "symbols",
        "source",
        "snapshot_id",
        "view",
        "columns",
        "common_only",
        "fill_wide",
    }
    encode = _api("request_digest")
    assert len(encode(parse_selection({"mode": "criteria", "region": "us", "criteria": {}}))) == 32


# ---------------------------------------------------------------------------
# (5) One filter vocabulary
# ---------------------------------------------------------------------------


def filter_rows() -> list[dict[str, Any]]:
    return [
        {"symbol": "CHEAP", "sector": "Technology", "is_primary": True,
         "pe_ttm": 9.0, "pe_forward": 25.0, "roic": 30.0, "market_cap_usd": 2e11,
         "dividend_yield": 3.5, "ps": 1.1, "short_float": 2.0},
        {"symbol": "RICH", "sector": "Technology", "is_primary": True,
         "pe_ttm": 40.0, "pe_forward": 12.0, "roic": 5.0, "market_cap_usd": 5e9,
         "dividend_yield": 0.2, "ps": 9.0, "short_float": 12.0},
        {"symbol": "UNKNOWN", "sector": "Technology", "is_primary": True,
         "pe_ttm": None, "pe_forward": None, "roic": None, "market_cap_usd": None,
         "dividend_yield": None, "ps": None, "short_float": None},
    ]


@pytest.mark.parametrize(
    ("catalogue_key", "finviz_key", "op", "value"),
    [
        ("pe", "fa_pe", "lt", 20),
        ("ps", "fa_ps", "gt", 2),
        ("roic", "fa_roi", "gt", 10),
        ("market_cap", "cap", "gte", 1e10),
        ("dividend_yield", "fa_div", "gte", 1),
        ("short_float", "sh_short", "lt", 5),
        ("beta", "ta_beta", "lt", 2),
    ],
)
def test_a_catalogue_key_filters_like_the_finviz_key_it_means(
    catalogue_key: str, finviz_key: str, op: str, value: Any
) -> None:
    ours = run(criteria({"key": catalogue_key, "op": op, "value": value}), filter_rows())
    theirs = run(criteria({"key": finviz_key, "op": op, "value": value}), filter_rows())
    assert ours["total"] == theirs["total"], (
        f"key {catalogue_key!r} and key {finviz_key!r} mean the same column, "
        f"but returned {ours['total']} and {theirs['total']} rows"
    )
    assert [row["symbol"] for row in ours["rows"]] == [row["symbol"] for row in theirs["rows"]]


def test_the_average_volume_alias_answers_the_window_it_claims() -> None:
    """F6, behaviourally: ``average_volume`` and ``sh_avgvol`` select one row set.

    The old version of this test asserted ``criterion.field ==
    SCREEN_ALIASES[catalogue_key]`` — the table applied to itself — so the wrong
    mapping (``average_volume`` → ``avg_volume_10d``, a ten-day window answering a
    three-month question) passed by definition. This one asks the question a user
    asks: do the two spellings of "average volume" filter the same stocks?

    STEADY's ten-day window is quiet and its 30-day window is busy; SURGE is the
    reverse. A filter above five million must select SURGE under both names.
    """

    rows = [
        {
            "symbol": "STEADY",
            "sector": "Technology",
            "is_primary": True,
            "price": 10.0,
            "avg_volume_10d": 6_000_000.0,
            "avg_volume_30d": 1_000_000.0,
        },
        {
            "symbol": "SURGE",
            "sector": "Technology",
            "is_primary": True,
            "price": 10.0,
            "avg_volume_10d": 900_000.0,
            "avg_volume_30d": 6_000_000.0,
        },
    ]
    catalogue = run(criteria({"key": "average_volume", "op": "gt", "value": 5_000_000}), rows)
    finviz = run(criteria({"key": "sh_avgvol", "op": "gt", "value": 5_000_000}), rows)

    assert [row["symbol"] for row in catalogue["rows"]] == ["SURGE"], (
        "average_volume reads the ten-day window: it selected the stock whose "
        "spike is still in the ten-day mean and whose month is quiet"
    )
    assert [row["symbol"] for row in finviz["rows"]] == ["SURGE"]
    assert catalogue["total"] == finviz["total"]

    # ... and the ten-day cell is still its own column, not silently renamed.
    ten_day = parse_selection(criteria({"key": "avg_volume_10d", "op": "gt", "value": 5_000_000}))
    assert ten_day.filters[0].field == "avg_volume_10d"
    assert [row["symbol"] for row in run(
        criteria({"key": "avg_volume_10d", "op": "gt", "value": 5_000_000}), rows
    )["rows"]] == ["STEADY"]


def test_a_legacy_key_is_never_shadowed_by_an_alias() -> None:
    """``pe``, ``pe_ttm`` and ``forward_pe`` are three names for two questions."""

    pe = parse_selection(criteria({"key": "pe", "op": "lt", "value": 20})).filters[0]
    legacy = parse_selection(criteria({"key": "fa_pe", "op": "lt", "value": 20})).filters[0]
    column = parse_selection(criteria({"key": "pe_ttm", "op": "lt", "value": 20})).filters[0]
    forward = parse_selection(criteria({"key": "forward_pe", "op": "lt", "value": 20})).filters[0]

    assert pe.field == legacy.field == column.field == "pe_ttm"
    assert forward.field == "pe_forward", "forward P/E is a different metric, not an alias"
    assert {pe.field, column.field} != {forward.field}
    # The rows differ where the metrics differ, which is what makes the two
    # questions distinct rather than accidentally equal.
    cheap_ttm = run(criteria({"key": "pe", "op": "lt", "value": 20}), filter_rows())
    cheap_forward = run(criteria({"key": "forward_pe", "op": "lt", "value": 20}), filter_rows())
    assert [row["symbol"] for row in cheap_ttm["rows"]] == ["CHEAP"]
    assert [row["symbol"] for row in cheap_forward["rows"]] == ["RICH"]


def test_an_unknown_key_still_names_itself_with_the_old_envelope() -> None:
    with pytest.raises(SelectionError) as caught:
        parse_selection(criteria({"key": "pe_ratio", "op": "lt", "value": 20}))
    assert caught.value.code == "unknown_filter_key"
    assert "pe_ratio" in str(caught.value)
    from app.screen.filters import FILTER_KEYS

    assert f"is not one of the {len(FILTER_KEYS)} filter keys" in str(caught.value)


def test_a_catalogue_key_with_no_filterable_column_is_refused_by_name() -> None:
    """A silent empty result would read as 'no company has an EV'."""

    for key in ("enterprise_value", "cash_per_share", "employees"):
        with pytest.raises(SelectionError) as caught:
            parse_selection(criteria({"key": key, "op": "gte", "value": 1}))
        assert key in str(caught.value), f"the 422 must name {key!r}"
        assert caught.value.status_code == 422
        assert caught.value.code in {"unavailable_filter_key", "unknown_filter_key"}


def test_a_legacy_unavailable_key_keeps_its_own_refusal() -> None:
    """``theme`` still has no classification source; ``idx`` now has a bucket."""

    with pytest.raises(SelectionError) as caught:
        parse_selection(criteria({"key": "theme", "op": "eq", "value": "ai"}))
    assert caught.value.code == "unavailable_filter_key"
    assert "theme" in str(caught.value)


# ---------------------------------------------------------------------------
# (6) Contract bugs
# ---------------------------------------------------------------------------


def test_symbols_mode_honours_columns() -> None:
    request = parse_selection({**SYMBOLS, "columns": ["symbol", "roic", "pe_ttm"]})
    assert request.columns == ("symbol", "roic", "pe_ttm")
    document = select_rows(
        filter_rows(), request, region="us", as_of=AS_OF, snapshot_id=SYMBOLS["snapshot_id"]
    ).as_document()
    assert document["columns"] == ["symbol", "roic", "pe_ttm"]
    assert set(document["rows"][0]) == {"symbol", "roic", "pe_ttm", "as_of"}, (
        "a ticker lookup projects exactly like a query does"
    )


def test_symbols_mode_accepts_a_limit() -> None:
    request = parse_selection({**SYMBOLS, "limit": 1})
    assert request.limit == 1
    document = select_rows(
        filter_rows(), request, region="us", as_of=AS_OF, snapshot_id=SYMBOLS["snapshot_id"]
    ).as_document()
    assert document["limit"] == 1
    assert document["returned"] == 1
    assert document["total"] == 2, "the total still counts every ticker that resolved"


def test_symbols_limit_is_clamped_to_the_panel() -> None:
    assert parse_selection({**SYMBOLS, "limit": 500}).limit == panel_limit()


def test_symbols_mode_still_refuses_a_view_and_columns_together() -> None:
    with pytest.raises(SelectionError):
        parse_selection({**SYMBOLS, "view": "quality", "columns": ["symbol"]})


def test_a_criteria_selection_still_points_a_top_level_limit_at_criteria() -> None:
    with pytest.raises(SelectionError) as caught:
        parse_selection({**criteria(), "limit": 5})
    assert "'limit' belongs inside 'criteria'" in str(caught.value)


# ---------------------------------------------------------------------------
# The four always-null keys
# ---------------------------------------------------------------------------


def test_the_overlay_fills_a_cell_the_scan_answers_null_for() -> None:
    """A null the bulk scan writes for every row is a gap, not an observation."""

    rows = [{"symbol": "PSX", "ps": None, "short_float": None}]
    merged = overlay(rows, {"PSX": {"ps": 1.5, "short_float": 2.25}}, default_ticker_key="symbol")
    assert merged[0]["ps"] == 1.5, "the store has P/S and the scan never fills it"
    assert merged[0]["short_float"] == 2.25


def test_a_measured_zero_is_never_overwritten_by_the_overlay() -> None:
    rows = [{"symbol": "ZERO", "ps": 0.0, "short_float": 0.0}]
    merged = overlay(rows, {"ZERO": {"ps": 1.5, "short_float": 2.25}}, default_ticker_key="symbol")
    assert merged[0]["ps"] == 0.0, "0.0 is an observation; presence, not truthiness, wins"
    assert merged[0]["short_float"] == 0.0


def test_a_view_fill_never_erases_a_cell_the_row_already_carries() -> None:
    rows = [
        {
            "symbol": "NVDA",
            "enterprise_value": 5_100_000_000_000.0,
            "cash_per_share": 4.25,
            "book_per_share": 3.5,
        }
    ]
    filled = apply_view_fill(rows, {})
    assert filled[0]["enterprise_value"] == 5_100_000_000_000.0, (
        "apply_view_fill recomputes from the snapshot store; a missing snapshot "
        "must not erase the value the depth overlay supplied"
    )
    assert filled[0]["cash_per_share"] == 4.25
    assert filled[0]["book_per_share"] == 3.5


def test_a_view_fill_still_fills_a_cell_the_row_does_not_carry() -> None:
    rows = [{"symbol": "NVDA", "price": 200.0, "change_pct": 2.0}]
    filled = apply_view_fill(rows, {})
    assert filled[0]["prev_close"] == 200.0 / 1.02, "price/change still derives prev_close"
