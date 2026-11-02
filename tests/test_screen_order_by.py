"""Order-by: the catalog's numeric fields, both directions (plan T8).

Until now ``sort`` was a closed three-name vocabulary (``market_cap``,
``turnover``, ``gainers``) because the toolbar offered three buttons. The
descriptive panel has to order by any visible metric, so the vocabulary is
**derived** from :data:`app.screen.filters.FIELDS` — every ``number`` field —
rather than written down again, and the direction is a leading ``-``
(``-pe_ttm``), which is Finviz's own ``o=-marketcap``.

Three properties are the contract:

* the list cannot drift from the catalog: ``ORDER_BY`` *is* the numeric half of
  ``FIELDS``, sorted;
* a missing value sorts **last in both directions** — a null is not a small
  number, and an ascending sort that floats nulls to the top turns "cheapest"
  into "the ones nobody reported";
* the three legacy names stay aliases, so every link, the browse route and the
  export manifest keep working.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.screen.columns import ABSENT_COLUMNS, CATALOGUE_COLUMNS
from app.screen.filters import FIELDS
from app.screen.rank import ALIASES, CLIENT_ONLY_SORTS, ORDER_BY, order_metric, rank_rows
from app.screen.selection import SelectionError, parse_selection, select_rows
from tests._screen import default_screener_columns

AS_OF = "2026-11-02T14:30:00.000000+00:00"

#: Catalogue/UI spellings that name a column the ranker can compare. Derived
#: from the one column table and the field kinds, never retyped: a key is here
#: exactly when the row carries its column.
SORTABLE_CATALOGUE_KEYS: tuple[str, ...] = tuple(
    sorted(
        key
        for key, column in CATALOGUE_COLUMNS.items()
        if FIELDS.get(column) in ("number", "text")
    )
)

#: Catalogue keys whose column no row carries: a sort on one of these could
#: only be empty, so they stay refused by name rather than sorting as nothing.
UNSORTABLE_CATALOGUE_KEYS: tuple[str, ...] = tuple(
    sorted(
        key
        for key, column in CATALOGUE_COLUMNS.items()
        if FIELDS.get(column) not in ("number", "text")
    )
)


def request(sort: str | None = None) -> dict[str, Any]:
    block: dict[str, Any] = {"limit": 10}
    if sort is not None:
        block["sort"] = sort
    return {"mode": "criteria", "region": "us", "criteria": block}


def row(symbol: str, **cells: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"symbol": symbol, "name": f"{symbol} Inc"}
    base.update(cells)
    return base


def test_order_by_is_the_numeric_half_of_the_catalog() -> None:
    numeric = tuple(sorted(name for name, kind in FIELDS.items() if kind == "number"))
    assert ORDER_BY == numeric
    assert len(ORDER_BY) > 50, "the catalog carries dozens of numeric cells"
    assert "sector" not in ORDER_BY, "a text field is not an order"


@pytest.mark.parametrize("name", ["pe_ttm", "rsi_14", "dividend_yield", "volatility_w"])
def test_order_by_accepts_any_numeric_catalog_field(name: str) -> None:
    parsed = parse_selection(request(name))
    assert parsed.sort == name
    assert order_metric(name) == (name, False)


def test_a_text_field_is_an_order_too() -> None:
    """Text columns sort A-Z / Z-A; a typo is still refused."""

    for name in ("sector", "country", "symbol"):
        assert parse_selection(request(name)).sort == name

    with pytest.raises(SelectionError) as caught:
        parse_selection(request("countri"))
    assert caught.value.code == "unknown_sort"


def test_a_leading_minus_is_ascending() -> None:
    parsed = parse_selection(request("-pe_ttm"))
    assert parsed.sort == "-pe_ttm"
    assert order_metric("-pe_ttm") == ("pe_ttm", True)

    rows = [
        row("EXPENSIVE", pe_ttm=40.0),
        row("CHEAP", pe_ttm=8.0),
        row("MIDDLE", pe_ttm=19.0),
    ]
    selection = select_rows(rows, parsed, region="us", as_of=AS_OF)
    assert [entry["symbol"] for entry in selection.rows] == ["CHEAP", "MIDDLE", "EXPENSIVE"]


def test_missing_values_sort_last_both_ways() -> None:
    rows = [
        row("NONE", pe_ttm=None),
        row("HIGH", pe_ttm=40.0),
        row("LOW", pe_ttm=8.0),
    ]

    descending = rank_rows([dict(entry) for entry in rows], "pe_ttm")
    ascending = rank_rows([dict(entry) for entry in rows], "-pe_ttm")

    assert [entry["symbol"] for entry in descending] == ["HIGH", "LOW", "NONE"]
    assert [entry["symbol"] for entry in ascending] == ["LOW", "HIGH", "NONE"]
    assert [entry["rank"] for entry in descending] == [1, 2, 3]


def test_ties_still_break_on_the_symbol() -> None:
    rows = [row("ZZZ", pe_ttm=10.0), row("AAA", pe_ttm=10.0), row("MMM", pe_ttm=10.0)]

    ranked = rank_rows(rows, "pe_ttm")

    assert [entry["symbol"] for entry in ranked] == ["AAA", "MMM", "ZZZ"]


def test_the_three_legacy_sorts_still_resolve() -> None:
    """The browse route's three buttons keep working — now as ordinary aliases."""

    assert ALIASES["market_cap"] == "market_cap_usd"
    assert ALIASES["gainers"] == "change_pct"
    assert ALIASES["turnover"] == "turnover", "a self-map is the documented one"
    assert order_metric("market_cap") == ("market_cap_usd", False)
    assert order_metric("gainers") == ("change_pct", False)
    assert order_metric("turnover") == ("turnover", False)
    assert order_metric("-gainers") == ("change_pct", True)

    rows = [
        row("BIG", market_cap_usd=9e12, change_pct=1.0),
        row("SMALL", market_cap_usd=1e9, change_pct=9.0),
    ]
    parsed = parse_selection(request("gainers"))
    selection = select_rows(rows, parsed, region="us", as_of=AS_OF)
    assert [entry["symbol"] for entry in selection.rows] == ["SMALL", "BIG"]


def test_no_sort_is_still_the_market_cap_default() -> None:
    parsed = parse_selection(request())
    assert parsed.sort == "market_cap"
    assert order_metric(parsed.sort or "") == ("market_cap_usd", False)


def test_an_unknown_sort_names_the_accepted_ones() -> None:
    with pytest.raises(SelectionError) as caught:
        parse_selection(request("marketcap"))

    message = str(caught.value)
    assert "marketcap" in message
    assert "market_cap_usd" in message, "the message lists the catalog names"


def test_text_columns_sort_alphabetically_both_ways() -> None:
    """A country is not a number, but A-Z and Z-A are still orders."""

    rows = [
        {"symbol": "AAA", "country": "United States", "sector": "Finance"},
        {"symbol": "BBB", "country": "Japan", "sector": "Energy"},
        {"symbol": "CCC", "country": "Canada", "sector": "Finance"},
        {"symbol": "DDD", "country": None, "sector": "Energy"},
    ]

    desc = rank_rows(rows, "country")
    # Descending text: United States > Japan > Canada.
    assert [row["symbol"] for row in desc] == ["AAA", "BBB", "CCC", "DDD"]
    assert [row["rank"] for row in desc] == [1, 2, 3, 4]

    asc = rank_rows(rows, "-country")
    assert [row["symbol"] for row in asc] == ["CCC", "BBB", "AAA", "DDD"]
    # Missing stays last in both directions.
    assert asc[-1]["symbol"] == "DDD"


# ---------------------------------------------------------------------------
# The catalogue/UI spelling is a sort token too
#
# The live failure this section pins: the screener's headers send the *catalogue*
# key (``perf_4w``, ``pe``, ``sma50``) while the ranker speaks the row's own
# column names (``ret_1m``, ``pe_ttm``, ``sma_50``). Only three spellings were
# translated, so twelve of the thirteen default columns answered 422
# ``unknown_sort`` when a reader pressed a header.
# ---------------------------------------------------------------------------


def test_perf_4w_orders_by_the_one_month_return() -> None:
    """``perf_4w`` → ``ret_1m``: the exact token the live route refused."""

    assert order_metric("perf_4w") == ("ret_1m", False)
    assert order_metric("-perf_4w") == ("ret_1m", True)

    rows = [
        row("MID", ret_1m=3.0),
        row("TOP", ret_1m=12.0),
        row("BOTTOM", ret_1m=-4.0),
        row("NONE", ret_1m=None),
    ]

    descending = rank_rows(rows, "perf_4w")
    assert [entry["symbol"] for entry in descending] == ["TOP", "MID", "BOTTOM", "NONE"]

    ascending = rank_rows(rows, "-perf_4w")
    assert [entry["symbol"] for entry in ascending] == ["BOTTOM", "MID", "TOP", "NONE"], (
        "a leading '-' is lowest first, and a missing value stays last"
    )
    assert [entry["rank"] for entry in ascending] == [1, 2, 3, 4]


def test_perf_4w_survives_the_parser_and_the_selection_pipeline() -> None:
    parsed = parse_selection(request("-perf_4w"))
    assert parsed.sort == "-perf_4w", "the request echoes the spelling the caller used"

    rows = [row("B", ret_1m=2.0), row("A", ret_1m=1.0)]
    selection = select_rows(rows, parsed, region="us", as_of=AS_OF)
    assert [entry["symbol"] for entry in selection.rows] == ["A", "B"]


@pytest.mark.parametrize("key", SORTABLE_CATALOGUE_KEYS)
def test_every_catalogue_key_sorts_by_the_column_it_names(key: str) -> None:
    """Every key the UI can draw is a key a reader can press, both ways."""

    column = CATALOGUE_COLUMNS[key]
    assert order_metric(key) == (column, False)
    assert order_metric(f"-{key}") == (column, True)
    assert parse_selection(request(f"-{key}")).sort == f"-{key}"


@pytest.mark.parametrize("key", UNSORTABLE_CATALOGUE_KEYS)
def test_a_catalogue_key_with_no_row_column_is_still_refused(key: str) -> None:
    """No alias may invent a column: ``eps`` and ``news_url`` have no cell."""

    with pytest.raises(ValueError):
        order_metric(key)

    with pytest.raises(SelectionError) as caught:
        parse_selection(request(key))
    assert caught.value.code == "unknown_sort"


def test_the_alias_table_is_the_catalogue_not_a_second_vocabulary() -> None:
    """Structural: an alias lands on a comparable row column and never re-kinds
    a name the row already spells.

    That second clause is what stops a text field being sorted as a number: a
    synonym may rename ``perf_4w`` to ``ret_1m``, but it may not point a text
    column at a numeric one (or the reverse), because :func:`app.screen.rank._key`
    picks its comparison from the target's kind.
    """

    assert ALIASES["perf_4w"] == "ret_1m"
    assert ALIASES["ticker"] == "symbol"
    assert ALIASES["company"] == "name"

    for name, column in ALIASES.items():
        assert column in FIELDS, f"alias {name!r} points at {column!r}, not a row column"
        assert FIELDS[column] in ("number", "text"), (
            f"alias {name!r} points at {column!r} ({FIELDS[column]}), which is not comparable"
        )
        assert column not in ABSENT_COLUMNS, f"alias {name!r} points at an absent column"
        if name in FIELDS and name != column:
            assert FIELDS[name] == FIELDS[column], (
                f"alias {name!r} re-kinds an existing {FIELDS[name]} column as "
                f"{FIELDS[column]}: a text field would be sorted as a number"
            )

    renamed = {
        key
        for key, column in CATALOGUE_COLUMNS.items()
        if column != key and FIELDS.get(column) in ("number", "text")
    }
    assert renamed <= set(ALIASES), (
        "every catalogue spelling the row spells differently is translated: "
        f"missing {sorted(renamed - set(ALIASES))}"
    )


def test_a_text_alias_sorts_alphabetically_never_numerically() -> None:
    """``company`` → ``name`` is a text sort; the cells are never coerced."""

    assert order_metric("company") == ("name", False)
    assert order_metric("-ticker") == ("symbol", True)

    rows = [
        {"symbol": "AAA", "name": "9 Labs"},
        {"symbol": "BBB", "name": "Alpha"},
        {"symbol": "CCC", "name": "2 Beta"},
    ]

    descending = rank_rows(rows, "company")
    assert [entry["symbol"] for entry in descending] == ["BBB", "AAA", "CCC"], (
        "alphabetical, not numeric: a float() attempt would fail on every row and "
        "leave the symbol tie-break (AAA, BBB, CCC)"
    )
    ascending = rank_rows(rows, "-company")
    assert [entry["symbol"] for entry in ascending] == ["CCC", "AAA", "BBB"]


def test_the_row_number_is_refused_as_client_only() -> None:
    """``no`` is drawn from the page offset; the server says so instead of 500."""

    assert "no" in CLIENT_ONLY_SORTS, "the one client-only column is named"

    with pytest.raises(ValueError) as caught:
        order_metric("no")
    assert "client-only" in str(caught.value)
    assert "page offset" in str(caught.value)

    with pytest.raises(SelectionError) as refused:
        parse_selection(request("-no"))
    assert refused.value.code == "unknown_sort"
    assert "client-only" in str(refused.value)


@pytest.mark.parametrize("token", ["sectr", "marketcap", "perf_5w", "perf_4w_", "PE"])
def test_a_genuinely_unknown_token_is_still_unknown_sort(token: str) -> None:
    """Widening the vocabulary did not turn a typo into a silent market-cap sort."""

    with pytest.raises(ValueError):
        order_metric(token)

    with pytest.raises(SelectionError) as caught:
        parse_selection(request(token))
    assert caught.value.code == "unknown_sort"
    assert token in str(caught.value)


def test_every_default_screener_column_sorts_or_is_client_only() -> None:
    """The list under test is the client's own two arrays, not a hand copy.

    A default column the ranker cannot order is a header that answers 422 the
    moment a reader presses it — which is exactly the shipped bug.
    """

    columns = default_screener_columns()
    assert len(columns) >= 13, f"the shipped default set is missing columns: {columns}"
    assert columns[0] == "no", "the row number leads the table and is client-only"

    for key in columns:
        if key in CLIENT_ONLY_SORTS:
            with pytest.raises(ValueError):
                order_metric(key)
            continue
        column, ascending = order_metric(key)
        assert column in FIELDS, f"default column {key!r} resolves to {column!r}"
        assert order_metric(f"-{key}") == (column, not ascending)


def test_the_published_enum_lists_every_default_screener_column() -> None:
    """The schema a client validates against accepts the request it sends."""

    from app.screen.selection import SELECTION_REQUEST_SCHEMA

    document: Any = SELECTION_REQUEST_SCHEMA
    enum = set(document["properties"]["criteria"]["properties"]["sort"]["enum"])

    for key in default_screener_columns():
        if key in CLIENT_ONLY_SORTS:
            assert key not in enum, f"{key!r} is client-only and must stay unpublished"
            continue
        assert key in enum, f"the default column {key!r} is missing from the enum"
        assert f"-{key}" in enum, f"the ascending form of {key!r} is missing"
