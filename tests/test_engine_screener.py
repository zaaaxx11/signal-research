"""Contract for the local screen engine (``app.engine.screener``).

A screen is a pure function over canonical rows: filters narrow, a sort orders,
pagination slices, and ``total`` reports how many rows matched **before** the
slice. These tests pin the parts a user would notice if they drifted:

* an unknown value is never a number — ``None``, a missing key and NaN/inf are
  excluded by every operator, ``ne`` included;
* nothing is coerced: a numeric string is text, and a numeric operator applied
  to text matches nothing instead of inventing a number;
* the order never depends on input order or dict insertion order;
* a typo is an error, never an empty screen;
* the row objects come back by identity, so a caller keeps its evidence.

The suite is deliberately data-light: rows are small dicts (or ``Cell``s built
by hand) and no provider, catalog file or network is read here.
"""

from __future__ import annotations

import random
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.engine.catalog import CATALOG_BY_KEY
from app.engine.model import Cell, CellStatus, Provenance, Unit
from app.engine.screener import (
    DEFAULT_FIELDS,
    DEFAULT_LIMIT,
    MAX_LIMIT,
    FilterSpec,
    ScreenError,
    ScreenQuery,
    ScreenResult,
    SortSpec,
    apply,
    compile_filter,
    parse_query,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def cell(value: object, *, unit: Unit = Unit.USD) -> Cell:
    """A real ``Cell``: non-null values carry provenance, null ones a reason."""

    if value is None:
        return Cell(
            value=None,
            unit=unit,
            provenance=Provenance(
                source="test", as_of="2026-10-26", status=CellStatus.ABSENT
            ),
        )
    return Cell(
        value=value,
        unit=unit,
        provenance=Provenance(source="test", as_of="2026-10-26"),
    )


def row(ticker: str, **fields: object) -> dict[str, object]:
    return {"ticker": ticker, **fields}


def tickers(result: ScreenResult) -> list[str]:
    return [str(item["ticker"]) for item in result.rows]


# ---------------------------------------------------------------------------
# The error contract
# ---------------------------------------------------------------------------


def test_screen_error_is_a_value_error_that_carries_a_code() -> None:
    error = ScreenError("unknown_field", "boom")
    assert isinstance(error, ValueError)
    assert error.code == "unknown_field"
    assert str(error) == "boom"


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ({"field": "", "op": "eq", "value": 1}, "unknown_field"),
        ({"field": "   ", "op": "eq", "value": 1}, "unknown_field"),
        ({"field": 5, "op": "eq", "value": 1}, "unknown_field"),
        ({"field": "market_cap", "op": "like", "value": 1}, "unknown_operator"),
        ({"field": "market_cap", "op": None, "value": 1}, "unknown_operator"),
        ({"field": "market_cap", "op": 5, "value": 1}, "unknown_operator"),
        ({"field": "market_cap", "op": "eq", "value": None}, "invalid_value"),
        ({"field": "market_cap", "op": "eq", "value": [1]}, "invalid_value"),
        ({"field": "market_cap", "op": "eq", "value": {"a": 1}}, "invalid_value"),
        ({"field": "market_cap", "op": "eq", "value": float("nan")}, "invalid_value"),
        ({"field": "market_cap", "op": "gt", "value": "10"}, "invalid_value"),
        ({"field": "market_cap", "op": "gt", "value": True}, "invalid_value"),
        ({"field": "price", "op": "between", "value": (1,)}, "invalid_value"),
        ({"field": "price", "op": "between", "value": (1, 2, 3)}, "invalid_value"),
        ({"field": "price", "op": "between", "value": (10, 1)}, "invalid_value"),
        ({"field": "price", "op": "between", "value": ("1", "2")}, "invalid_value"),
        ({"field": "price", "op": "between", "value": 5}, "invalid_value"),
        ({"field": "sector", "op": "in", "value": ()}, "invalid_value"),
        ({"field": "sector", "op": "in", "value": []}, "invalid_value"),
        ({"field": "sector", "op": "in", "value": 5}, "invalid_value"),
        ({"field": "sector", "op": "in", "value": [None]}, "invalid_value"),
        ({"field": "sector", "op": "contains", "value": 5}, "invalid_value"),
        ({"field": "sector", "op": "contains", "value": "   "}, "invalid_value"),
        ({"field": "market_cap", "op": "eq", "value": float("inf")}, "invalid_value"),
        ({"field": "market_cap", "op": "eq", "value": Decimal("NaN")}, "invalid_value"),
        ({"field": "market_cap", "op": "eq", "value": 1, "extra": 2}, "invalid_value"),
        ({"field": "market_cap", "op": "eq"}, "invalid_value"),
    ],
)
def test_compile_filter_rejects_bad_input_with_a_code(
    raw: object, code: str
) -> None:
    with pytest.raises(ScreenError) as excinfo:
        compile_filter(raw)
    assert excinfo.value.code == code


@pytest.mark.parametrize(
    "expression", ["", "   ", "market_cap", "market_cap ~ 5", "eq 5", "market_cap eq"]
)
def test_compile_filter_rejects_unparseable_expressions(expression: str) -> None:
    with pytest.raises(ScreenError) as excinfo:
        compile_filter(expression)
    assert excinfo.value.code == "invalid_value"


# ---------------------------------------------------------------------------
# compile_filter: forms, shorthand, idempotence
# ---------------------------------------------------------------------------


def test_compile_filter_accepts_every_documented_form() -> None:
    expected = FilterSpec(field="market_cap", op="gte", value=1000)
    assert (
        compile_filter({"field": "market_cap", "op": "gte", "value": 1000})
        == expected
    )
    assert compile_filter(("market_cap", "gte", 1000)) == expected
    assert compile_filter(["market_cap", "gte", 1000]) == expected
    assert compile_filter("market_cap gte 1000") == expected
    assert compile_filter(expected) == expected


def test_compile_filter_normalises_symbols_and_operator_case() -> None:
    assert compile_filter("market_cap >= 1000").op == "gte"
    assert (
        compile_filter({"field": "market_cap", "op": ">=", "value": 1000}).op
        == "gte"
    )
    assert (
        compile_filter({"field": "market_cap", "op": " GTE ", "value": 1000}).op
        == "gte"
    )
    assert compile_filter("price < 5").op == "lt"
    assert compile_filter("pe != 3").op == "ne"
    assert compile_filter("pe == 3").op == "eq"


def test_compile_filter_reads_the_string_shorthand_for_ranges_and_lists() -> None:
    between = compile_filter("price between 5..10")
    assert between.op == "between"
    assert between.value == (5, 10)

    members = compile_filter("exchange in (XNAS, XNYS)")
    assert members.op == "in"
    assert members.value == ("XNAS", "XNYS")

    quoted = compile_filter("sector eq 'Technology'")
    assert quoted.value == "Technology"

    numeric_members = compile_filter("pe in (5, 10)")
    assert numeric_members.value == (5, 10)


def test_compile_filter_is_idempotent() -> None:
    once = compile_filter("sector contains Tech")
    assert compile_filter(once) == once
    assert compile_filter(compile_filter(once)) == once


def test_filter_spec_validates_itself_when_built_directly() -> None:
    with pytest.raises(ScreenError) as excinfo:
        FilterSpec("pe", "like", 1)
    assert excinfo.value.code == "unknown_operator"

    with pytest.raises(ScreenError) as excinfo:
        FilterSpec("price", "between", (10, 5))
    assert excinfo.value.code == "invalid_value"

    with pytest.raises(ScreenError) as excinfo:
        FilterSpec("", "eq", 1)
    assert excinfo.value.code == "unknown_field"


def test_compile_filter_rejects_a_bare_string_as_the_vocabulary() -> None:
    with pytest.raises(ScreenError) as excinfo:
        compile_filter("pe eq 1", allowed_fields="pe")  # type: ignore[arg-type]
    assert excinfo.value.code == "invalid_value"


def test_compile_filter_uses_the_catalog_vocabulary_by_default() -> None:
    with pytest.raises(ScreenError) as excinfo:
        compile_filter("market_capp gte 1")
    assert excinfo.value.code == "unknown_field"
    assert "market_cap" in str(excinfo.value)

    custom = compile_filter("score_x gte 1", allowed_fields={"score_x"})
    assert custom.field == "score_x"
    assert compile_filter("score_x gte 1", allowed_fields=None).field == "score_x"


# ---------------------------------------------------------------------------
# parse_query
# ---------------------------------------------------------------------------


def test_parse_query_defaults_to_the_first_page_of_everything() -> None:
    query = parse_query()
    assert query == ScreenQuery()
    assert (query.filters, query.sort, query.limit, query.offset) == ((), None, 20, 0)
    assert parse_query({}).limit == DEFAULT_LIMIT
    assert parse_query(None) == ScreenQuery()


def test_parse_query_reads_filters_sort_and_window() -> None:
    query = parse_query(
        {
            "filters": [
                {"field": "market_cap", "op": "gte", "value": 1000},
                "sector eq Technology",
                ["pe", "between", [5, 10]],
            ],
            "sort": {"field": "market_cap", "descending": False},
            "limit": 25,
            "offset": 50,
        }
    )
    assert [spec.field for spec in query.filters] == ["market_cap", "sector", "pe"]
    assert query.filters[2].value == (5, 10)
    assert query.sort == SortSpec("market_cap", descending=False)
    assert (query.limit, query.offset) == (25, 50)


def test_parse_query_accepts_a_sort_string() -> None:
    assert parse_query({"sort": "-price"}).sort == SortSpec("price", descending=True)
    assert parse_query({"sort": "+price"}).sort == SortSpec("price", descending=False)
    assert parse_query({"sort": "price"}).sort == SortSpec("price", descending=True)


def test_parse_query_passes_a_screen_query_through() -> None:
    query = ScreenQuery(limit=5)
    assert parse_query(query) is query


def test_parse_query_rejects_unknown_keys_and_bad_windows() -> None:
    with pytest.raises(ScreenError) as excinfo:
        parse_query({"fitlers": []})
    assert excinfo.value.code == "invalid_value"

    for bad in (0, -1, 501, True, "20", 20.0, None):
        with pytest.raises(ScreenError) as excinfo:
            parse_query({"limit": bad})
        assert excinfo.value.code == "invalid_limit", bad

    for bad in (-1, True, "0", 1.5, None):
        with pytest.raises(ScreenError) as excinfo:
            parse_query({"offset": bad})
        assert excinfo.value.code == "invalid_limit", bad

    assert parse_query({"limit": MAX_LIMIT}).limit == MAX_LIMIT
    assert parse_query({"limit": 1}).limit == 1
    assert parse_query({"offset": 0}).offset == 0


@pytest.mark.parametrize("payload", [[], "market_cap", 5])
def test_parse_query_rejects_a_payload_that_is_not_a_mapping(payload: object) -> None:
    with pytest.raises(ScreenError) as excinfo:
        parse_query(payload)
    assert excinfo.value.code == "invalid_value"


def test_parse_query_rejects_a_filter_that_is_not_a_filter() -> None:
    for payload in (
        {"filters": 5},
        {"filters": [{"field": "pe"}]},
        {"filters": [("pe", "gt")]},
    ):
        with pytest.raises(ScreenError) as excinfo:
            parse_query(payload)
        assert excinfo.value.code == "invalid_value", payload


def test_a_screen_query_normalises_raw_filters_and_sort() -> None:
    query = ScreenQuery(
        filters=[{"field": "pe", "op": "gt", "value": 1}, "sector eq Technology"],
        sort="-market_cap",
    )
    assert isinstance(query.filters, tuple)
    assert [spec.field for spec in query.filters] == ["pe", "sector"]
    assert query.sort == SortSpec("market_cap", descending=True)


def test_query_objects_are_frozen() -> None:
    query = ScreenQuery()
    with pytest.raises(FrozenInstanceError):
        query.limit = 1  # type: ignore[misc]
    spec = FilterSpec("pe", "gt", 1)
    with pytest.raises(FrozenInstanceError):
        spec.field = "x"  # type: ignore[misc]
    sort = SortSpec("pe")
    with pytest.raises(FrozenInstanceError):
        sort.descending = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Reading a row: Cell, plain values, and the unknown
# ---------------------------------------------------------------------------


def test_cell_values_are_read_through_their_cell() -> None:
    rows = [
        row("AAA", price=cell(12.5)),
        row("BBB", price=cell(None)),
        row("CCC", price=8.0),
    ]
    result = apply(rows, ScreenQuery(filters=(FilterSpec("price", "gte", 10),)))
    assert tickers(result) == ["AAA"]
    assert result.rows[0] is rows[0]


@pytest.mark.parametrize(
    ("op", "value"),
    [
        ("eq", 5),
        ("ne", 5),
        ("gt", 1),
        ("gte", 1),
        ("lt", 10),
        ("lte", 10),
        ("between", (1, 10)),
        ("in", (1, 5, 10)),
        ("contains", "5"),
    ],
)
def test_a_missing_value_never_matches_any_operator(op: str, value: object) -> None:
    for unknown in (None, float("nan"), float("inf"), float("-inf"), Decimal("NaN")):
        rows = [row("AAA", market_cap=unknown)]
        query = ScreenQuery(filters=(FilterSpec("market_cap", op, value),))
        result = apply(rows, query)
        assert result.rows == (), (op, unknown)

    blank = [row("AAA")]
    result = apply(blank, ScreenQuery(filters=(FilterSpec("market_cap", op, value),)))
    assert result.rows == (), op


def test_unknown_is_not_zero() -> None:
    rows = [row("AAA", pe=8.0), row("BBB"), row("CCC", pe=None)]
    cheap = apply(rows, ScreenQuery(filters=(FilterSpec("pe", "lt", 10),)))
    assert tickers(cheap) == ["AAA"]
    assert cheap.total == 1


def test_zero_is_a_value_not_an_absence() -> None:
    rows = [row("AAA", change=0.0), row("BBB")]
    result = apply(rows, ScreenQuery(filters=(FilterSpec("change", "eq", 0.0),)))
    assert tickers(result) == ["AAA"]


def test_ne_does_not_leak_unknown_rows() -> None:
    rows = [row("AAA", pe=8.0), row("BBB"), row("CCC", pe=12.0)]
    result = apply(rows, ScreenQuery(filters=(FilterSpec("pe", "ne", 8.0),)))
    assert tickers(result) == ["CCC"]


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("op", "value", "expected"),
    [
        ("eq", 10.0, ["BBB"]),
        ("ne", 10.0, ["AAA", "CCC"]),
        ("gt", 10.0, ["CCC"]),
        ("gte", 10.0, ["BBB", "CCC"]),
        ("lt", 10.0, ["AAA"]),
        ("lte", 10.0, ["AAA", "BBB"]),
        ("between", (9.0, 10.0), ["BBB"]),
        ("in", (5.0, 30.0), ["AAA", "CCC"]),
    ],
)
def test_numeric_operators_are_inclusive_where_documented(
    op: str, value: object, expected: list[str]
) -> None:
    rows = [row("AAA", pe=5.0), row("BBB", pe=10.0), row("CCC", pe=30.0)]
    result = apply(rows, ScreenQuery(filters=(FilterSpec("pe", op, value),)))
    assert tickers(result) == expected


def test_text_equality_is_exact_and_contains_is_case_insensitive() -> None:
    rows = [
        row("AAA", sector="Technology"),
        row("BBB", sector="technology"),
        row("CCC", sector="Healthcare"),
    ]
    exact = apply(
        rows, ScreenQuery(filters=(FilterSpec("sector", "eq", "Technology"),))
    )
    assert tickers(exact) == ["AAA"]

    looser = apply(
        rows, ScreenQuery(filters=(FilterSpec("sector", "contains", "TECH"),))
    )
    assert tickers(looser) == ["AAA", "BBB"]

    wrong_case = apply(rows, ScreenQuery(filters=(FilterSpec("sector", "eq", "tech"),)))
    assert wrong_case.rows == ()


def test_a_comparison_never_coerces_text_into_a_number() -> None:
    rows = [row("AAA", market_cap="1000"), row("BBB", market_cap=2000)]
    numeric = apply(rows, ScreenQuery(filters=(FilterSpec("market_cap", "gt", 500),)))
    assert tickers(numeric) == ["BBB"]

    textual = apply(
        rows, ScreenQuery(filters=(FilterSpec("market_cap", "eq", "1000"),))
    )
    assert tickers(textual) == ["AAA"]


def test_numeric_operators_on_a_text_field_match_nothing() -> None:
    rows = [row("AAA", sector="Technology")]
    for op, value in (("gt", 1), ("lte", 5), ("between", (0, 5))):
        result = apply(rows, ScreenQuery(filters=(FilterSpec("sector", op, value),)))
        assert result.rows == (), op


def test_contains_on_a_number_matches_nothing() -> None:
    rows = [row("AAA", market_cap=1000)]
    result = apply(
        rows, ScreenQuery(filters=(FilterSpec("market_cap", "contains", "100"),))
    )
    assert result.rows == ()


def test_booleans_are_their_own_kind_and_never_equal_to_a_number() -> None:
    rows = [row("AAA", flag=True), row("BBB", flag=1), row("CCC", flag=False)]
    truthy = apply(
        rows,
        ScreenQuery(filters=(FilterSpec("flag", "eq", True),)),
        allowed_fields={"flag"},
    )
    assert tickers(truthy) == ["AAA"]

    one = apply(
        rows,
        ScreenQuery(filters=(FilterSpec("flag", "eq", 1),)),
        allowed_fields={"flag"},
    )
    assert tickers(one) == ["BBB"]


def test_in_matches_only_the_same_kind() -> None:
    rows = [row("AAA", pe=5.0), row("BBB", pe="5")]
    result = apply(rows, ScreenQuery(filters=(FilterSpec("pe", "in", (5, 7)),)))
    assert tickers(result) == ["AAA"]


def test_ne_across_kinds_is_not_evidence() -> None:
    rows = [row("AAA", pe="12.5")]
    result = apply(rows, ScreenQuery(filters=(FilterSpec("pe", "ne", 12.5),)))
    assert result.rows == ()


# ---------------------------------------------------------------------------
# The field vocabulary
# ---------------------------------------------------------------------------


def test_the_catalog_is_the_default_vocabulary() -> None:
    assert DEFAULT_FIELDS == frozenset(CATALOG_BY_KEY)
    assert {"ticker", "market_cap", "pe", "price"} <= DEFAULT_FIELDS


def test_an_unknown_field_is_an_error_not_an_empty_screen() -> None:
    rows = [row("AAA", market_cap=1.0)]
    with pytest.raises(ScreenError) as excinfo:
        apply(rows, ScreenQuery(filters=(FilterSpec("market_capp", "gte", 1),)))
    assert excinfo.value.code == "unknown_field"
    assert "market_cap" in str(excinfo.value)


def test_a_field_the_rows_carry_is_known_without_a_catalog_entry() -> None:
    rows = [row("AAA", score_x=5), row("BBB", score_x=1)]
    result = apply(rows, ScreenQuery(filters=(FilterSpec("score_x", "gte", 3),)))
    assert tickers(result) == ["AAA"]


def test_allowed_fields_narrows_the_vocabulary() -> None:
    rows = [row("AAA", market_cap=1.0)]
    with pytest.raises(ScreenError) as excinfo:
        apply(
            rows,
            ScreenQuery(filters=(FilterSpec("market_cap", "gte", 1),)),
            allowed_fields={"pe"},
        )
    assert excinfo.value.code == "unknown_field"

    result = apply(
        rows,
        ScreenQuery(filters=(FilterSpec("market_cap", "gte", 1),)),
        allowed_fields={"market_cap"},
    )
    assert result.total == 1


def test_allowed_fields_guards_the_sort_field_too() -> None:
    rows = [row("AAA", market_cap=1.0)]
    with pytest.raises(ScreenError) as excinfo:
        apply(rows, ScreenQuery(sort=SortSpec("nope")), allowed_fields={"market_cap"})
    assert excinfo.value.code == "unknown_field"


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


def test_sort_is_descending_by_default_and_missing_values_stay_last() -> None:
    rows = [
        row("AAA", market_cap=None),
        row("BBB", market_cap=10.0),
        row("CCC", market_cap=30.0),
        row("DDD", market_cap=20.0),
    ]
    result = apply(rows, ScreenQuery(sort=SortSpec("market_cap")))
    assert tickers(result) == ["CCC", "DDD", "BBB", "AAA"]


def test_ascending_sort_also_keeps_missing_values_last() -> None:
    rows = [
        row("AAA", market_cap=None),
        row("BBB", market_cap=10.0),
        row("CCC", market_cap=30.0),
        row("DDD", market_cap=20.0),
    ]
    result = apply(rows, ScreenQuery(sort=SortSpec("market_cap", descending=False)))
    assert tickers(result) == ["BBB", "DDD", "CCC", "AAA"]


def test_ties_break_on_ticker_in_both_directions() -> None:
    rows = [
        row("EEE", market_cap=10.0),
        row("AAA", market_cap=10.0),
        row("CCC", market_cap=10.0),
    ]
    descending = apply(rows, ScreenQuery(sort=SortSpec("market_cap")))
    assert tickers(descending) == ["AAA", "CCC", "EEE"]

    ascending = apply(rows, ScreenQuery(sort=SortSpec("market_cap", descending=False)))
    assert tickers(ascending) == ["AAA", "CCC", "EEE"]


def test_order_does_not_depend_on_input_order() -> None:
    rows = [
        row("AAA", market_cap=10.0),
        row("BBB", market_cap=None),
        row("CCC", market_cap=30.0),
        row("DDD", market_cap=10.0),
        row("EEE", market_cap=20.0),
    ]
    query = ScreenQuery(sort=SortSpec("market_cap"))
    first = tickers(apply(rows, query))
    for seed in range(5):
        shuffled = list(rows)
        random.Random(seed).shuffle(shuffled)
        assert tickers(apply(shuffled, query)) == first


def test_rows_without_a_ticker_still_have_a_total_order() -> None:
    rows = [
        {"market_cap": 10.0, "pe": 1},
        {"market_cap": 10.0, "pe": 2},
        {"market_cap": 10.0, "pe": 3},
    ]
    query = ScreenQuery(sort=SortSpec("market_cap"))
    first = [item["pe"] for item in apply(rows, query).rows]
    assert first == [1, 2, 3]
    assert [item["pe"] for item in apply(list(reversed(rows)), query).rows] == first


def test_text_sorting_is_case_insensitive_and_deterministic() -> None:
    rows = [
        row("AAA", sector="beta"),
        row("BBB", sector="Alpha"),
        row("CCC", sector="alpha"),
    ]
    result = apply(rows, ScreenQuery(sort=SortSpec("sector", descending=False)))
    assert tickers(result) == ["BBB", "CCC", "AAA"]


def test_a_key_the_row_does_not_have_sorts_with_the_missing_values() -> None:
    rows = [
        row("AAA"),
        row("BBB", market_cap=10.0),
        row("CCC", market_cap=None),
        row("DDD", market_cap=20.0),
    ]
    descending = apply(rows, ScreenQuery(sort=SortSpec("market_cap")))
    assert tickers(descending) == ["DDD", "BBB", "AAA", "CCC"]

    ascending = apply(rows, ScreenQuery(sort=SortSpec("market_cap", descending=False)))
    assert tickers(ascending) == ["BBB", "DDD", "AAA", "CCC"]


def test_mixed_kinds_in_one_field_do_not_raise() -> None:
    rows = [row("AAA", pe="n/a"), row("BBB", pe=10.0), row("CCC", pe=None)]
    result = apply(rows, ScreenQuery(sort=SortSpec("pe")))
    assert tickers(result) == ["BBB", "AAA", "CCC"]


def test_sorting_does_not_mutate_the_rows_or_the_input_list() -> None:
    rows = [row("AAA", market_cap=1.0), row("BBB", market_cap=2.0)]
    before = [dict(item) for item in rows]
    apply(rows, ScreenQuery(sort=SortSpec("market_cap")))
    assert rows == before


def test_no_sort_preserves_input_order() -> None:
    rows = [row("ZZZ", price=1.0), row("AAA", price=2.0)]
    assert tickers(apply(rows, ScreenQuery())) == ["ZZZ", "AAA"]


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_total_counts_every_match_before_the_page_is_cut() -> None:
    rows = [row(f"T{i}", price=float(i)) for i in range(10)]
    result = apply(
        rows,
        ScreenQuery(
            filters=(FilterSpec("price", "gte", 5.0),),
            sort=SortSpec("price", descending=False),
            limit=2,
            offset=1,
        ),
    )
    assert result.total == 5
    assert result.returned == 2
    assert (result.limit, result.offset) == (2, 1)
    assert tickers(result) == ["T6", "T7"]


def test_offset_past_the_end_is_an_empty_page_with_the_real_total() -> None:
    rows = [row(f"T{i}", price=float(i)) for i in range(5)]
    result = apply(rows, ScreenQuery(limit=2, offset=99))
    assert (result.rows, result.returned, result.total) == ((), 0, 5)


def test_the_default_limit_is_twenty_rows() -> None:
    rows = [row(f"T{i:02d}", price=float(i)) for i in range(30)]
    result = apply(rows, ScreenQuery())
    assert result.limit == DEFAULT_LIMIT == 20
    assert (result.total, result.returned) == (30, 20)


def test_five_hundred_is_the_largest_page() -> None:
    assert MAX_LIMIT == 500
    rows = [row(f"T{i}", price=1.0) for i in range(600)]
    result = apply(rows, ScreenQuery(limit=MAX_LIMIT))
    assert (result.returned, result.total) == (500, 600)


# ---------------------------------------------------------------------------
# Nothing is hidden, nothing is filled
# ---------------------------------------------------------------------------


def test_an_empty_query_returns_every_row_including_missing_values() -> None:
    rows = [row("AAA"), row("BBB", pe=None), row("CCC", pe=12.0)]
    result = apply(rows, ScreenQuery())
    assert result.total == 3
    assert tickers(result) == ["AAA", "BBB", "CCC"]
    assert all(a is b for a, b in zip(result.rows, rows, strict=True))


def test_paging_returns_the_row_objects_by_identity() -> None:
    rows = [row("AAA", market_cap=1.0), row("BBB", market_cap=2.0)]
    result = apply(rows, ScreenQuery(sort=SortSpec("market_cap")))
    assert result.rows[0] is rows[1]
    assert result.rows[1] is rows[0]


# ---------------------------------------------------------------------------
# applied_filters is the audit trail
# ---------------------------------------------------------------------------


def test_applied_filters_is_the_canonical_descriptor_list_in_order() -> None:
    query = ScreenQuery(
        filters=(
            compile_filter("market_cap gte 100"),
            compile_filter("sector eq 'Technology'"),
            compile_filter("price between 5..10"),
            compile_filter("exchange in (XNAS, XNYS)"),
        )
    )
    result = apply([row("AAA")], query)
    assert result.applied_filters == (
        "market_cap gte 100",
        "sector eq 'Technology'",
        "price between (5, 10)",
        "exchange in ('XNAS', 'XNYS')",
    )


def test_applied_filters_is_empty_when_nothing_was_filtered() -> None:
    assert apply([row("AAA")], ScreenQuery()).applied_filters == ()


# ---------------------------------------------------------------------------
# The result object and apply's entry points
# ---------------------------------------------------------------------------


def test_screen_result_reports_its_own_window() -> None:
    result = apply([row("AAA", price=1.0)], ScreenQuery(limit=5))
    assert result.returned == len(result.rows) == 1
    assert (result.limit, result.offset) == (5, 0)
    assert isinstance(result, ScreenResult)
    with pytest.raises(FrozenInstanceError):
        result.total = 0  # type: ignore[misc]


def test_apply_accepts_a_raw_payload_as_well_as_a_query() -> None:
    rows = [row("AAA", market_cap=1.0), row("BBB", market_cap=5.0)]
    result = apply(
        rows,
        {
            "filters": [{"field": "market_cap", "op": "gte", "value": 2}],
            "sort": {"field": "market_cap", "descending": False},
        },
    )
    assert tickers(result) == ["BBB"]


def test_apply_rejects_a_row_that_is_not_a_mapping() -> None:
    with pytest.raises(ScreenError) as excinfo:
        apply([row("AAA"), "not-a-row"], ScreenQuery())  # type: ignore[list-item]
    assert excinfo.value.code == "invalid_value"


def test_apply_rejects_a_query_of_the_wrong_type() -> None:
    with pytest.raises(ScreenError) as excinfo:
        apply([row("AAA")], 5)  # type: ignore[arg-type]
    assert excinfo.value.code == "invalid_value"


def test_a_sort_without_a_field_is_an_error() -> None:
    with pytest.raises(ScreenError) as excinfo:
        ScreenQuery(sort=SortSpec("   "))
    assert excinfo.value.code == "unknown_field"


def test_an_empty_universe_is_a_valid_empty_result() -> None:
    result = apply([], ScreenQuery())
    assert (result.rows, result.total, result.returned) == ((), 0, 0)


# ---------------------------------------------------------------------------
# Whole-screen determinism
# ---------------------------------------------------------------------------


def test_a_large_screen_is_exact_and_reproducible() -> None:
    rng = random.Random(20261026)
    rows = [
        row(
            f"T{index:03d}",
            market_cap=rng.choice([None, rng.uniform(1.0, 1000.0)]),
            pe=rng.choice([None, rng.uniform(1.0, 50.0)]),
        )
        for index in range(300)
    ]
    query = ScreenQuery(
        filters=(
            compile_filter("market_cap gte 250"),
            compile_filter("pe between 5..40"),
        ),
        sort=SortSpec("market_cap"),
        limit=25,
        offset=50,
    )
    first = apply(rows, query)

    expected = [
        item
        for item in rows
        if item["market_cap"] is not None
        and item["market_cap"] >= 250
        and item["pe"] is not None
        and 5 <= item["pe"] <= 40
    ]
    assert first.total == len(expected)

    whole = apply(
        rows, ScreenQuery(filters=query.filters, sort=query.sort, limit=MAX_LIMIT)
    )
    assert tickers(first) == tickers(whole)[50:75]
    caps = [item["market_cap"] for item in whole.rows]
    assert caps == sorted(caps, reverse=True)

    shuffled = list(rows)
    random.Random(7).shuffle(shuffled)
    assert tickers(apply(shuffled, query)) == tickers(first)
    assert apply(rows, query) == first
