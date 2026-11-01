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

from app.screen.filters import FIELDS
from app.screen.rank import ALIASES, ORDER_BY, order_metric, rank_rows
from app.screen.selection import SelectionError, parse_selection, select_rows

AS_OF = "2026-11-02T14:30:00.000000+00:00"


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


def test_a_text_field_is_not_an_order() -> None:
    for name in ("sector", "country", "symbol"):
        with pytest.raises(SelectionError) as caught:
            parse_selection(request(name))
        assert caught.value.code == "unknown_sort"
        assert name in str(caught.value)


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


def test_the_three_legacy_sorts_still_work() -> None:
    assert ALIASES == {
        "market_cap": "market_cap_usd",
        "gainers": "change_pct",
        "turnover": "turnover",
    }
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
