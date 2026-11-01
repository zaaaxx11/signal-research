"""The depth store's cells are filter fields, declared once (T20).

The overlay has copied thirty-odd depth cells onto the row since it existed,
under their catalogue names, but ``filter_fields`` refused any key that pointed
at one: ``universe.CARRIED_FIELDS`` only knew the light columns, the parity
copies and the three derived columns. The fix is a **declared set**
(:data:`app.screen.universe.DEPTH_FIELDS`) rather than a wider guess, and this
module checks the declaration from both ends:

* every declared name is a catalogue column (the row can actually carry it);
* a real depth store fills every declared name for at least one symbol (the
  declaration cannot outlive the collector);
* the 23 numeric ones are filter fields, while ``candlestick_pattern`` and
  ``news_time`` stay **out** of ``FIELDS`` — text is bucket-only, so no numeric
  operator can reach a string and answer nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.screen import filter_fields, selection, universe
from app.screen.columns import CATALOGUE_KEY_SET
from app.screen.filters import FIELDS

#: The store the collector writes; absent on a machine that has never scanned.
DEPTH_STORE = Path("/home/ubuntu/ztockz-data/depth.json")

AS_OF = "2026-11-01T20:00:00+00:00"

#: The numeric depth cells, in the order the addendum declares them.
NUMERIC_DEPTH: tuple[str, ...] = tuple(
    name for name in universe.DEPTH_FIELDS if name not in ("candlestick_pattern", "news_time")
)


def request(filters: list[dict[str, Any]]) -> Any:
    return selection.parse_selection(
        {"mode": "criteria", "region": "us", "criteria": {"filters": filters}}
    )


def test_the_depth_set_is_the_twenty_five_declared_cells() -> None:
    assert len(universe.DEPTH_FIELDS) == 25
    assert len(NUMERIC_DEPTH) == 23
    assert {"candlestick_pattern", "news_time"} <= set(universe.DEPTH_FIELDS)


def test_every_depth_field_is_a_catalog_column() -> None:
    assert universe.DEPTH_FIELDS <= CATALOGUE_KEY_SET


def test_the_depth_store_fills_every_declared_field() -> None:
    if not DEPTH_STORE.exists():
        pytest.skip(f"{DEPTH_STORE} is not on this machine")
    store = json.loads(DEPTH_STORE.read_text(encoding="utf-8"))["symbols"]
    missing = [
        name
        for name in universe.DEPTH_FIELDS
        if not any(
            (
                isinstance(row.get(name), (int, float))
                and not isinstance(row.get(name), bool)
                if name not in ("candlestick_pattern", "news_time")
                else isinstance(row.get(name), str) and row.get(name).strip()
            )
            for row in store.values()
        )
    ]
    assert missing == [], "a declared field the collector stopped writing"


def test_the_numeric_depth_fields_are_filter_fields_and_the_text_ones_are_not() -> None:
    for name in NUMERIC_DEPTH:
        assert FIELDS[name] == "number", name
    for name in ("candlestick_pattern", "news_time"):
        assert name not in FIELDS, "text is bucket-only: no numeric operator may reach it"
    assert universe.DEPTH_FIELDS <= universe.CARRIED_FIELDS


def test_lt_debteq_filters_the_ratio_column() -> None:
    assert filter_fields.binding("fa_ltdebteq").field == "lt_debt_to_equity"
    answer = selection.select_rows(
        [{"symbol": "X", "lt_debt_to_equity": 0.66}],
        request(
            [{"key": "fa_ltdebteq", "op": "eq", "value": "ltdebteq:o0.5"}]
        ),
        region="us",
        as_of=AS_OF,
    )
    assert answer.total == 1
    assert answer.filters[0].value == "ltdebteq:o0.5"

    numeric = selection.select_rows(
        [{"symbol": "X", "lt_debt_to_equity": 0.66}],
        request([{"key": "fa_ltdebteq", "op": "gt", "value": 0.5}]),
        region="us",
        as_of=AS_OF,
    )
    assert numeric.total == 1, "the same key still answers a plain number"


def test_epsrev_bp_needs_both_surprises() -> None:
    tokens = filter_fields.buckets.bucket("epsrev:bp")
    context = filter_fields.buckets.Context.at(AS_OF)
    assert tokens.predicate({"eps_surprise": 4.0, "revenue_surprise": 1.0}, context)
    assert not tokens.predicate({"eps_surprise": 4.0, "revenue_surprise": None}, context)
    assert not tokens.predicate({"eps_surprise": -4.0, "revenue_surprise": 1.0}, context)
    assert filter_fields.buckets.bucket("epsrev:bm").predicate(
        {"eps_surprise": 0.0, "revenue_surprise": 0.0}, context
    )
    assert filter_fields.buckets.bucket("epsrev:bn").predicate(
        {"eps_surprise": -1.0, "revenue_surprise": -2.0}, context
    )
