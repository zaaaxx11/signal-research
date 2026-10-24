"""The 82-key disposition after P11B: three derived keys stop being refusals.

This module owns the *expectations* that changed when the derived columns landed
(:mod:`app.screen.derived`); the resolver mechanics themselves are pinned by
``tests/test_screen_selection.py`` and the row contract by
``tests/test_screen_parity.py``.

The claim is three-fold:

* the catalogue is still the probe's **82 keys**, and every one of them has
  exactly one disposition — a row column or a named refusal;
* the split moved from 36/46 to **39 mapped / 43 refused**, and the only keys
  that moved are ``an_recom``, ``ta_changeopen`` and ``ta_highlow52w``;
* each newly mapped key points at a column a real ``build_universe`` row
  carries, so "mapped" is a statement about the row, not about a table.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.screen.filter_fields import (
    CATALOGUE,
    ROW_FIELDS,
    UNAVAILABLE,
    binding,
    mapped_keys,
    reason_for,
    unavailable_keys,
)
from app.screen.filters import FIELDS, FILTER_CATALOG, FILTER_KEYS
from app.screen.selection import SELECTION_REQUEST_SCHEMA
from app.screen.universe import CARRIED_FIELDS, DERIVED_FIELDS, PARITY_COLUMNS, build_universe

#: The three keys this pass moved from "refused" to "mapped", and their columns.
P11B: dict[str, str] = {
    "an_recom": "recom_finviz",
    "ta_changeopen": "change_from_open",
    "ta_highlow52w": "pct_from_52w_high",
}


def provider_row(symbol: str, **cells: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": symbol,
        "name": symbol,
        "description": f"{symbol} Holdings Inc.",
        "exchange": "NASDAQ",
        "country": "United States",
        "sector": "Technology",
        "type": "stock",
        "subtype": "common",
        "currency": "USD",
        "close": 100.0,
        "change": 1.0,
        "volume": 1_000_000.0,
        "Value.Traded": 100_000_000.0,
        "market_cap_basic": 1_000_000_000.0,
    }
    row.update(cells)
    return row


def test_the_catalogue_is_still_the_probe_eighty_two_keys() -> None:
    assert len(FILTER_KEYS) == 82
    assert tuple(FILTER_CATALOG) == FILTER_KEYS
    assert tuple(entry.key for entry in CATALOGUE) == FILTER_KEYS, "panel order"


def test_the_disposition_is_thirty_nine_mapped_and_forty_three_refused() -> None:
    assert len(mapped_keys()) == 39
    assert len(unavailable_keys()) == 43
    assert len(mapped_keys()) + len(unavailable_keys()) == 82
    assert set(mapped_keys()) == set(ROW_FIELDS)
    assert set(unavailable_keys()) == set(UNAVAILABLE)
    assert not set(mapped_keys()) & set(unavailable_keys()), "one disposition per key"


@pytest.mark.parametrize(("key", "field"), sorted(P11B.items()))
def test_a_derived_key_is_mapped_not_refused(key: str, field: str) -> None:
    assert FILTER_CATALOG[key].field == field
    assert ROW_FIELDS[key] == field
    assert key not in UNAVAILABLE
    assert reason_for(key) is None
    assert binding(key).is_available is True
    assert binding(key).field == field
    assert field in DERIVED_FIELDS, "the column is one the derived table owns"
    assert FIELDS[field] == "number"


def test_only_those_three_keys_left_the_refusal_list() -> None:
    """No other key became available by accident."""

    mapped = set(mapped_keys()) - set(P11B)
    assert len(mapped) == 36, "the P7 disposition of 36 keys stands"
    refused = set(unavailable_keys())
    assert set(P11B).isdisjoint(refused)
    assert len(refused) == 46 - len(P11B)


def test_every_mapped_key_points_at_a_column_a_row_can_carry() -> None:
    assert set(ROW_FIELDS.values()) <= CARRIED_FIELDS, (
        f"keys point at columns no row carries: {sorted(set(ROW_FIELDS.values()) - CARRIED_FIELDS)}"
    )
    assert set(DERIVED_FIELDS) <= CARRIED_FIELDS


@pytest.mark.parametrize("key", sorted(P11B))
def test_a_real_row_carries_the_new_column(key: str) -> None:
    """Mapped is a claim about a row, so build one and read the field off it."""

    field = P11B[key]
    universe = build_universe(
        [
            provider_row(
                "LIVE",
                gap=0.5,
                **{"Recommend.All": 0.25, "price_52_week_high": 110.0},
            )
        ],
        region="us",
        min_market_cap=0,
        columns=PARITY_COLUMNS,
        allow_any_subtype=True,
    )

    assert len(universe) == 1
    assert field in universe[0], f"{key} is mapped but a parity row does not carry {field}"
    assert universe[0][field] is not None
    assert isinstance(universe[0][field], float)


def test_the_published_schema_reports_the_three_keys_as_mapped() -> None:
    """The schema a client validates against is the same disposition, not a copy."""

    published = SELECTION_REQUEST_SCHEMA["x-filter-keys"]
    assert SELECTION_REQUEST_SCHEMA["x-filter-key-count"] == 82

    for key, field in P11B.items():
        entry = published[key]
        assert entry["field"] == field
        assert entry["kind"] == "number"
        assert "reason" not in entry, f"{key} is mapped, so it must carry no refusal"
        assert entry["tab"] == FILTER_CATALOG[key].tab

    mapped = {key for key, entry in published.items() if entry["field"] is not None}
    refused = {key for key, entry in published.items() if entry["field"] is None}
    assert mapped == set(mapped_keys())
    assert refused == set(unavailable_keys())


def test_the_refusals_that_remain_are_the_ones_that_still_miss_a_metric() -> None:
    """The three shapes of refusal that survive: no column, many columns, always null."""

    for key in ("idx", "theme", "sh_insiderown", "ah_close", "ta_pattern", "etf_heldby"):
        assert reason_for(key), f"{key} has no metric; it must stay refused"
    assert "ten performance windows" in (reason_for("ta_perf") or "")
    assert "two windows" in (reason_for("ta_volatility") or "")
    assert "answered null for all" in (reason_for("fa_fpe") or "")
    for key, reason in UNAVAILABLE.items():
        assert reason.strip(), f"{key} needs a non-empty reason"
        assert len(reason) > 20, f"{key}'s reason is too short to be useful: {reason!r}"
