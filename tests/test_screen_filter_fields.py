"""The 82-key disposition after P11B, P11G, P11H, P11I and P11J: fourteen keys stop being refusals.

This module owns the *expectations* that changed when the derived columns landed
(:mod:`app.screen.derived`), when the fundamentals join landed
(:mod:`app.screen.fundamentals`), when the three option-driven keys got their
compiler (:mod:`app.screen.choice_filters`), when the join learned the two
ratios the snapshot stores only the inputs of (price/cash and EV/sales), and
when the facts store answered the three short/ownership keys
(:mod:`app.screen.facts`); the resolver mechanics themselves are pinned by
``tests/test_screen_selection.py`` and the row contract by
``tests/test_screen_parity.py``.

The claim is three-fold:

* the catalogue is still the probe's **82 keys**, and every one of them has
  exactly one disposition — a row column, an option vocabulary, or a named
  refusal;
* the split moved from 36/46 to **50 available / 32 refused**, and the only keys
  that moved are ``an_recom``, ``ta_changeopen``, ``ta_highlow52w`` (P11B, derived
  columns), ``fa_fpe``, ``fa_ps``, ``fa_payoutratio`` (P11G, joined from the
  fundamentals snapshot store), ``ta_perf``, ``ta_perf2``, ``ta_volatility``
  (P11H, one predicate per Finviz option token), ``fa_pc``, ``fa_evsales``
  (P11I, computed by the same join from the snapshot's balance-sheet cells) and
  ``sh_short``, ``sh_insiderown``, ``sh_instown`` (P11J, joined from the
  ``screen_facts`` store);
* each newly available key points at a column a real ``build_universe`` row
  carries (or one the join copies onto it), so "available" is a statement about
  the row, not about a table.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.screen import choice_filters
from app.screen.filter_fields import (
    BUCKET_KEYS,
    CATALOGUE,
    CHOICE_FIELDS,
    ROW_FIELDS,
    UNAVAILABLE,
    binding,
    choice_keys,
    mapped_keys,
    reason_for,
    unavailable_keys,
)
from app.screen.filters import FIELDS, FILTER_CATALOG, FILTER_KEYS
from app.screen.facts import FACTS_ROW_FIELDS
from app.screen.fundamentals import FUNDAMENTALS_ROW_FIELDS
from app.screen.selection import SELECTION_REQUEST_SCHEMA
from app.screen.universe import CARRIED_FIELDS, DERIVED_FIELDS, PARITY_COLUMNS, build_universe

#: The three keys P11B moved from "refused" to "mapped", and their columns.
P11B: dict[str, str] = {
    "an_recom": "recom_finviz",
    "ta_changeopen": "change_from_open",
    "ta_highlow52w": "pct_from_52w_high",
}

#: The three keys P11G moved, joined from the fundamentals snapshot store.
P11G: dict[str, str] = {
    "fa_fpe": "pe_forward",
    "fa_ps": "ps",
    "fa_payoutratio": "payout_ratio",
}

#: The two keys P11I moved: computed by the same join from snapshot cells.
P11I: dict[str, str] = {
    "fa_pc": "price_to_cash",
    "fa_evsales": "ev_to_sales",
}

#: The three keys P11J moved, joined from the ``screen_facts`` store.
P11J: dict[str, str] = {
    "sh_short": "short_float",
    "sh_insiderown": "insider_own",
    "sh_instown": "inst_own",
}

#: The three keys P11H made available through their option vocabulary, and the
#: columns each one reads. They have no single column by design.
P11H: dict[str, tuple[str, ...]] = {
    "ta_perf": (
        "change_pct",
        "ret_1w",
        "ret_1m",
        "ret_3m",
        "ret_6m",
        "ret_ytd",
        "ret_1y",
        "ret_5y",
        "ret_10y",
    ),
    "ta_perf2": (
        "change_pct",
        "ret_1w",
        "ret_1m",
        "ret_3m",
        "ret_6m",
        "ret_ytd",
        "ret_1y",
        "ret_5y",
        "ret_10y",
    ),
    "ta_volatility": ("volatility_w", "volatility_m"),
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


def test_the_disposition_is_fifty_one_available_and_thirty_one_refused() -> None:
    # 51 = 47 single-column keys + 3 option-driven keys + ``idx``, which is a
    # bucket-only key: Finviz's four index lists are membership tests, not a
    # column a scan carries (app.screen.index_membership).
    assert len(mapped_keys()) == 51
    assert len(unavailable_keys()) == 31
    assert len(mapped_keys()) + len(unavailable_keys()) == 82
    assert set(mapped_keys()) == set(ROW_FIELDS) | set(CHOICE_FIELDS) | set(BUCKET_KEYS)
    assert set(unavailable_keys()) == set(UNAVAILABLE)
    assert not set(mapped_keys()) & set(unavailable_keys()), "one disposition per key"
    assert choice_keys() == tuple(P11H), "panel order, three option-driven keys"


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


def test_the_index_key_is_a_bucket_and_not_a_column() -> None:
    """``idx`` is the one key answered by a set test instead of a row column.

    Plan T6 moved it out of the refused tuple and T11 pins the shape: a caller
    still sends ``{"key": "idx", "op": "eq", "value": "sp500"}``, the binding has
    no field, and the four options are bucket ids — not numbers a comparison
    could take.
    """

    from app.screen import buckets

    found = binding("idx")
    assert found.is_available is True
    assert found.field is None, "no row column carries index membership"
    assert found.kind is None
    assert found.bucket is True
    assert found.choice is False
    assert found.reason is None
    assert found.as_dict()["bucket"] is True
    assert "idx" not in ROW_FIELDS
    assert "idx" in mapped_keys() and "idx" not in unavailable_keys()
    assert BUCKET_KEYS == frozenset(buckets.KEYS)

    family = buckets.for_key("idx")
    assert [item.id for item in family] == ["idx:sp500", "idx:ndx", "idx:dji", "idx:rut"]
    assert all(item.key == "idx" for item in family)


def test_only_the_declared_keys_left_the_refusal_list() -> None:
    """No other key became available by accident."""

    moved = set(P11B) | set(P11G) | set(P11H) | set(P11I) | set(P11J)
    mapped = (
        set(mapped_keys()) - set(P11H) - set(P11B) - set(P11G) - set(P11I) - set(P11J)
    )
    assert len(mapped) == 37, (
        "the P7 disposition of 36 single-column keys stands, plus ``idx``, which "
        "is available through a bucket and no column at all"
    )
    refused = set(unavailable_keys())
    assert moved.isdisjoint(refused)
    # One more than the P11 moves: ``idx`` left the refusal list into a bucket.
    assert len(refused) == 46 - len(moved) - 1 == 31


def test_the_three_fact_keys_point_at_columns_the_join_really_writes() -> None:
    for key, field in P11J.items():
        assert FILTER_CATALOG[key].field == field
        assert ROW_FIELDS[key] == field
        assert key not in UNAVAILABLE
        assert reason_for(key) is None
        assert binding(key).is_available is True
        assert binding(key).field == field
        assert field in FACTS_ROW_FIELDS, "the column must be one the join writes"
        assert FIELDS[field] == "number"


def test_every_available_key_points_at_a_column_a_row_can_carry() -> None:
    carried = set(CARRIED_FIELDS) | set(FUNDAMENTALS_ROW_FIELDS) | set(FACTS_ROW_FIELDS)
    assert set(ROW_FIELDS.values()) <= carried, (
        f"keys point at columns no row carries: {sorted(set(ROW_FIELDS.values()) - carried)}"
    )
    columns = {column for columns in CHOICE_FIELDS.values() for column in columns}
    assert columns <= carried, (
        f"option-driven keys read columns no row carries: {sorted(columns - carried)}"
    )
    assert set(DERIVED_FIELDS) <= CARRIED_FIELDS


@pytest.mark.parametrize("key", sorted(P11H))
def test_an_option_driven_key_is_available_without_a_single_column(key: str) -> None:
    """A choice key filters — it just answers per option token, not per column."""

    assert FILTER_CATALOG[key].choice is True
    assert FILTER_CATALOG[key].field is None, "ten windows cannot be one column"
    assert ROW_FIELDS.get(key) is None
    assert CHOICE_FIELDS[key] == P11H[key]
    assert key not in UNAVAILABLE
    assert reason_for(key) is None
    assert binding(key).is_available is True
    assert binding(key).choice is True
    assert binding(key).field is None
    assert choice_filters.fields_for(key) == P11H[key]
    for column in P11H[key]:
        assert column in FIELDS and FIELDS[column] == "number"
        assert column in CARRIED_FIELDS, f"{key} reads {column}, which a parity row lacks"


@pytest.mark.parametrize("key", sorted(P11H))
def test_a_real_row_carries_every_choice_column(key: str) -> None:
    """Available is a claim about a row: build one and read each column off it."""

    cells: dict[str, Any] = {
        "Perf.W": 1.0,
        "Perf.1M": 2.0,
        "Perf.3M": 3.0,
        "Perf.6M": 4.0,
        "Perf.YTD": 5.0,
        "Perf.Y": 6.0,
        "Perf.5Y": 7.0,
        "Perf.10Y": 8.0,
        "Volatility.W": 2.5,
        "Volatility.M": 9.5,
    }
    universe = build_universe(
        [provider_row("LIVE", **cells)],
        region="us",
        min_market_cap=0,
        columns=PARITY_COLUMNS,
        allow_any_subtype=True,
    )

    assert len(universe) == 1
    for column in P11H[key]:
        assert column in universe[0], f"{key} reads {column}, absent from a parity row"
        assert universe[0][column] is not None


@pytest.mark.parametrize(("key", "field"), sorted({**P11G, **P11I}.items()))
def test_a_fundamentals_key_is_mapped_to_a_cell_the_join_writes(key: str, field: str) -> None:
    assert FILTER_CATALOG[key].field == field
    assert ROW_FIELDS[key] == field
    assert key not in UNAVAILABLE
    assert reason_for(key) is None
    assert binding(key).is_available is True
    assert binding(key).field == field
    assert field in FUNDAMENTALS_ROW_FIELDS
    assert FIELDS[field] == "number"


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


def test_the_published_schema_reports_the_three_keys_as_available() -> None:
    """The schema a client validates against is the same disposition, not a copy."""

    extension = SELECTION_REQUEST_SCHEMA["x-filter-keys"]
    published: dict[str, Any] = dict(extension)  # type: ignore[arg-type]
    assert SELECTION_REQUEST_SCHEMA["x-filter-key-count"] == 82

    for key, field in P11B.items():
        entry = published[key]
        assert entry["field"] == field
        assert entry["kind"] == "number"
        assert "reason" not in entry, f"{key} is mapped, so it must carry no refusal"
        assert entry["tab"] == FILTER_CATALOG[key].tab

    mapped = {key for key, entry in published.items() if entry["field"] is not None}
    chosen = {key for key, entry in published.items() if "choice" in entry}
    bucketed = {key for key, entry in published.items() if entry.get("bucket") is True}
    refused = {
        key
        for key, entry in published.items()
        if entry["field"] is None and "choice" not in entry and not entry.get("bucket")
    }
    assert mapped == set(ROW_FIELDS)
    assert chosen == set(P11H)
    assert bucketed == set(BUCKET_KEYS), "the bucket flag is the one buckets table"
    assert mapped | chosen | bucketed == set(mapped_keys())
    assert refused == set(unavailable_keys())

    for key, columns in P11H.items():
        entry = published[key]
        assert entry["field"] is None, "no single column could name the key"
        assert "kind" not in entry, "the value is an option token, not a number"
        assert "reason" not in entry, f"{key} is available, so it is not refused"
        assert entry["tab"] == FILTER_CATALOG[key].tab
        choice = entry["choice"]
        assert choice["fields"] == list(columns)
        assert set(choice["windows"].values()) - {None} == set(columns)
        assert choice["options"], f"{key} publishes no compiled option"


def test_the_published_choice_options_are_the_compiled_predicates() -> None:
    """Every published option string is the predicate the request would run."""

    extension = SELECTION_REQUEST_SCHEMA["x-filter-keys"]
    published: dict[str, Any] = dict(extension)  # type: ignore[arg-type]
    for key in P11H:
        options: dict[str, str] = published[key]["choice"]["options"]
        assert set(options) == set(choice_filters.choices_for(key))
        for token, expression in options.items():
            compiled = choice_filters.compile_option(key, token)
            assert expression == f"{compiled.field} {compiled.op} {compiled.value}"

    assert published["ta_perf"]["choice"]["options"]["1w10u"] == "ret_1w lte -10.0"
    assert published["ta_perf2"]["choice"]["options"]["dup"] == "change_pct gt 0.0"
    assert published["ta_volatility"]["choice"]["options"]["mo15"] == (
        "volatility_m gte 15.0"
    )
    unsupported = published["ta_perf"]["choice"]["unsupported"]
    assert set(unsupported) == {"3y"}, "the one window no row carries"
    assert "ret_3y" in unsupported["3y"]


def test_the_refusals_that_remain_are_the_ones_that_still_miss_a_metric() -> None:
    """The two shapes of refusal that survive: no column, or a column always null."""

    for key in ("theme", "sh_insidertrans", "ah_close", "ta_pattern", "etf_heldby"):
        assert reason_for(key), f"{key} has no metric; it must stay refused"
    assert "answered null for all" in (reason_for("ipodate") or "")
    for key in P11J:
        assert reason_for(key) is None, (
            f"{key} filters a facts column now (app.screen.facts); a refusal "
            "here would be a stale disposition"
        )
    for key in ("ta_perf", "ta_perf2", "ta_volatility"):
        assert reason_for(key) is None, (
            f"{key} filters through its option tokens now (app.screen.choice_filters); "
            "a refusal here would be a stale disposition"
        )
    # The one window the row cannot answer is refused per token, by name.
    unsupported: dict[str, str] = choice_filters.describe("ta_perf")["unsupported"]  # type: ignore[assignment]
    assert "ret_3y" in unsupported["3y"]
    with pytest.raises(choice_filters.UnsupportedChoiceOption):
        choice_filters.compile_option("ta_perf", "3y10u")
    for key, reason in UNAVAILABLE.items():
        assert reason.strip(), f"{key} needs a non-empty reason"
        assert len(reason) > 20, f"{key}'s reason is too short to be useful: {reason!r}"
