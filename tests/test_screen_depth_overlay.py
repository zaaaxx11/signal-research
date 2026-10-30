"""Screen depth overlay — the depth store fills a row's *missing* catalog cells.

The contract (one pure function, ``overlay``) answers five questions:

* **Fill-missing-only.** A row that already carries a cell — even carrying
  ``None`` — is the row's own answer and is left untouched. A depth value never
  silently overrides a provider cell.
* **Catalog-only.** The depth store also carries bookkeeping keys
  (``source_time``, ``conflict``, per-field provenance). Those are internal:
  they must not appear on a public screen row, so a key the catalog does not
  declare is dropped rather than copied.
* **``None`` is absence.** A depth cell of ``None`` means "unknown", and unknown
  must not become a key holding ``None`` (let alone ``0``): a filter that later
  reads the key would believe the field was observed.
* **Two spellings, one symbol.** The provider writes ``BRK.B``/``BF.B``; the
  depth store keys ``BRK-B``/``BF.B`` — so the lookup tries the exact spelling
  first and the dotted/hyphen swap second.
* **Rows are copied, never touched.** Every output row is a new dict carrying
  every original key unchanged; input rows are never mutated or aliased, and the
  output length and order equal the input's.
"""

from __future__ import annotations

import copy
import inspect
from types import MappingProxyType
from typing import Any

import pytest

from app.screen import depth_overlay


def _depth(symbol: str, **cells: Any) -> dict[str, Any]:
    """One depth store holding a single symbol, in the documented shape."""

    return {"as_of": "2026-10-30T00:00:00+00:00", "symbols": {symbol: cells}}


# ---------------------------------------------------------------------------
# Shape of the module — exactly one public function
# ---------------------------------------------------------------------------


def test_overlay_is_the_single_public_entry_point() -> None:
    assert depth_overlay.__all__ == ["SCAN_NULL_CELLS", "SCREEN_ALIASES", "overlay"], (
        "one function and the two tables it publishes: the alias map a filter "
        "resolver reads, and the columns the scan answers null for"
    )
    defined = sorted(
        name
        for name, value in vars(depth_overlay).items()
        if inspect.isfunction(value)
        and value.__module__ == depth_overlay.__name__
        and not name.startswith("_")
    )
    assert defined == ["overlay"], f"only overlay may be public, found {defined}"


# ---------------------------------------------------------------------------
# Rule 1 — fill-missing-only: a cell the row already carries always wins
# ---------------------------------------------------------------------------


def test_overlay_fills_only_the_cells_the_row_does_not_carry() -> None:
    row: dict[str, Any] = {"ticker": "AAPL", "pe": 15.0}
    depth = _depth("AAPL", pe=21.3, market_cap=4.1e12, roe=1.47)

    filled = depth_overlay.overlay([row], depth["symbols"])[0]

    assert filled["pe"] == 15.0, (
        "the row already answered pe=15.0; a depth value must never override a "
        f"provider cell, got {filled['pe']!r}"
    )
    assert filled["market_cap"] == 4.1e12
    assert filled["roe"] == 1.47


def test_overlay_leaves_a_none_the_row_already_carries_untouched() -> None:
    row: dict[str, Any] = {"ticker": "AAPL", "pe": None}
    filled = depth_overlay.overlay([row], _depth("AAPL", pe=21.3)["symbols"])[0]

    assert "pe" in filled, "the row's own key must not be dropped"
    assert filled["pe"] is None, (
        "a row carrying pe=None already owns that cell; the depth store must not "
        f"fill it, got {filled['pe']!r}"
    )


# ---------------------------------------------------------------------------
# Rule 2 — catalog-only: bookkeeping keys never reach a public row
# ---------------------------------------------------------------------------


def test_overlay_drops_depth_keys_the_catalog_does_not_declare() -> None:
    row: dict[str, Any] = {"ticker": "AAPL"}
    entry = {
        "pe": 21.3,
        "source_time": "2026-10-29T20:00:00+00:00",
        "conflict": "yahoo!=stockanalysis",
        "source": "yahoo",
        "confidence": 0.9,
    }
    filled = depth_overlay.overlay([row], {"AAPL": entry})[0]

    assert filled["pe"] == 21.3
    leaked = sorted(set(filled) - {"ticker", "pe"})
    assert leaked == [], f"non-catalog depth keys leaked onto the row: {leaked}"


# ---------------------------------------------------------------------------
# Rule 3 — a None depth value does not add the key at all
# ---------------------------------------------------------------------------


def test_overlay_does_not_add_a_key_whose_depth_value_is_none() -> None:
    row: dict[str, Any] = {"ticker": "AAPL"}
    filled = depth_overlay.overlay(
        [row], {"AAPL": {"pe": None, "market_cap": 4.1e12}}
    )[0]

    assert "pe" not in filled, (
        "a depth value of None means unknown; the key must be absent rather than "
        f"present holding None, got {filled.get('pe')!r}"
    )
    assert filled["market_cap"] == 4.1e12


def test_overlay_treats_zero_false_and_blank_as_real_observations() -> None:
    row: dict[str, Any] = {"ticker": "AAPL"}
    entry = {"pe": 0.0, "optionable": False, "industry": "", "dividend_yield": 0}
    filled = depth_overlay.overlay([row], {"AAPL": entry})[0]

    assert filled["pe"] == 0.0, "zero is a number, not an absence"
    assert filled["optionable"] is False
    assert filled["industry"] == ""
    assert filled["dividend_yield"] == 0


# ---------------------------------------------------------------------------
# Rule 4 — ticker resolution: strip/upper, then exact spelling, then the swap
# ---------------------------------------------------------------------------


def test_overlay_resolves_a_provider_dotted_ticker_to_a_hyphen_store_key() -> None:
    filled = depth_overlay.overlay([{"ticker": "BRK.B"}], _depth("BRK-B", pe=8.1)["symbols"])[0]

    assert filled["pe"] == 8.1, (
        "the provider writes BRK.B while the store keys BRK-B; without the swap "
        f"this row would stay empty, got {filled!r}"
    )


def test_overlay_resolves_a_provider_hyphen_ticker_to_a_dotted_store_key() -> None:
    filled = depth_overlay.overlay([{"ticker": "BF-B"}], _depth("BF.B", pe=17.4)["symbols"])[0]

    assert filled["pe"] == 17.4


def test_overlay_trims_and_upper_cases_the_row_ticker() -> None:
    rows = [{"ticker": " aapl "}, {"ticker": "Brk-B"}]
    depth = {"AAPL": {"pe": 21.3}, "BRK.B": {"pe": 8.1}}
    first, second = depth_overlay.overlay(rows, depth)

    assert first["pe"] == 21.3
    assert second["pe"] == 8.1


def test_overlay_prefers_the_exact_spelling_over_the_swapped_one() -> None:
    depth = {"ABC.D": {"pe": 1.0}, "ABC-D": {"pe": 2.0}}

    dotted, hyphen = depth_overlay.overlay(
        [{"ticker": "ABC.D"}, {"ticker": "ABC-D"}], depth
    )

    assert dotted["pe"] == 1.0, "the exact key must win over the swapped spelling"
    assert hyphen["pe"] == 2.0


# ---------------------------------------------------------------------------
# Rule 5 — a row whose ticker is missing, blank or not a string is unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "row",
    [
        {"symbol": "AAPL", "pe": 15.0},
        {"ticker": "", "pe": 15.0},
        {"ticker": "   ", "pe": 15.0},
        {"ticker": None, "pe": 15.0},
        {"ticker": 123, "pe": 15.0},
        {"ticker": 12.3, "pe": 15.0},
        {"ticker": ["AAPL"], "pe": 15.0},
        {"ticker": {"symbol": "AAPL"}, "pe": 15.0},
        {"ticker": b"AAPL", "pe": 15.0},
    ],
)
def test_overlay_returns_rows_with_an_unusable_ticker_unchanged(
    row: dict[str, Any],
) -> None:
    depth = {"AAPL": {"pe": 21.3, "market_cap": 4.1e12}, "123": {"pe": 9.9}}

    out = depth_overlay.overlay([row], depth)

    assert len(out) == 1
    assert out[0] == row, (
        "a row with a missing/blank/non-string ticker cannot name a symbol, so it "
        f"must come back unchanged, got {out[0]!r}"
    )
    assert out[0] is not row, "even an unchanged row is returned as a new dict"


# ---------------------------------------------------------------------------
# Rule 6 — a symbol absent from the store yields an equal copy
# ---------------------------------------------------------------------------


def test_overlay_returns_rows_for_unknown_symbols_unchanged_in_content() -> None:
    row: dict[str, Any] = {"ticker": "ZZZZ", "pe": 15.0, "weird_column": "kept"}
    out = depth_overlay.overlay([row], _depth("AAPL", pe=21.3)["symbols"])

    assert out[0] == row
    assert out[0] is not row


def test_overlay_returns_rows_unchanged_when_the_swapped_spelling_is_absent_too() -> None:
    row: dict[str, Any] = {"ticker": "BRK.B", "pe": 8.1}
    out = depth_overlay.overlay([row], _depth("BRK-C", pe=1.0)["symbols"])

    assert out[0] == row


# ---------------------------------------------------------------------------
# Rule 7 — an empty depth mapping still yields one equal copy per row
# ---------------------------------------------------------------------------


def test_overlay_with_an_empty_depth_returns_equal_copies() -> None:
    rows = [{"ticker": "AAPL", "pe": 15.0}, {"ticker": "MSFT"}]

    out = depth_overlay.overlay(rows, {})

    assert out == rows, "an empty store changes no content"
    assert len(out) == len(rows)
    for filled, original in zip(out, rows):
        assert filled is not original, "each output row must be a new dict"
        assert filled == original


# ---------------------------------------------------------------------------
# Copy discipline — never mutate, never alias
# ---------------------------------------------------------------------------


def test_overlay_does_not_mutate_or_alias_the_input_rows() -> None:
    row: dict[str, Any] = {"ticker": "AAPL", "pe": 15.0, "sector": "Technology"}
    before = copy.deepcopy(row)

    out = depth_overlay.overlay([row], _depth("AAPL", market_cap=4.1e12)["symbols"])

    assert row == before, f"the input row was mutated: {row!r} != {before!r}"
    assert out[0] is not row, "the output row must not alias the input row"


def test_mutating_an_output_row_cannot_reach_the_input_row() -> None:
    row: dict[str, Any] = {"ticker": "AAPL", "pe": 15.0}
    out = depth_overlay.overlay([row], _depth("AAPL", market_cap=4.1e12)["symbols"])

    out[0]["pe"] = 99.0
    out[0]["injected"] = True

    assert row["pe"] == 15.0
    assert "injected" not in row


def test_overlay_copies_every_original_key_and_value_verbatim() -> None:
    nested = {"low": 1.0, "high": 2.0}
    row: dict[str, Any] = {
        "ticker": "AAPL",
        "pe": None,
        "market_cap": None,
        "nested": nested,
        "flag": False,
        "count": 0,
    }

    filled = depth_overlay.overlay([row], _depth("AAPL", roe=1.47)["symbols"])[0]

    for key, value in row.items():
        assert key in filled, f"original key {key!r} disappeared"
        assert filled[key] == value, f"original value for {key!r} changed"
    assert filled["nested"] is nested, "a shallow copy keeps the original sub-object"
    assert filled["roe"] == 1.47


# ---------------------------------------------------------------------------
# Row shape — provider columns survive, order and count are preserved
# ---------------------------------------------------------------------------


def test_overlay_keeps_non_catalog_provider_columns() -> None:
    row: dict[str, Any] = {
        "symbol": "AAPL",
        "close": 189.1,
        "Value.Traded": 8.2e9,
        "market_cap_basic": 4.1e12,
        "currency": "USD",
    }
    filled = depth_overlay.overlay(
        [row], _depth("AAPL", pe=21.3)["symbols"], default_ticker_key="symbol"
    )[0]

    for key, value in row.items():
        assert filled[key] == value, f"provider column {key!r} must survive untouched"
    assert filled["pe"] == 21.3


def test_overlay_preserves_the_input_order_and_length() -> None:
    rows = [
        {"ticker": "MSFT"},
        {"ticker": "ZZZZ"},
        {"ticker": "AAPL"},
        {"ticker": "NVDA"},
    ]
    depth = {"AAPL": {"pe": 21.3}, "MSFT": {"pe": 34.0}, "NVDA": {"pe": 60.1}}

    out = depth_overlay.overlay(rows, depth)

    assert [row["ticker"] for row in out] == ["MSFT", "ZZZZ", "AAPL", "NVDA"], (
        "overlay must not sort, filter or reorder rows"
    )


def test_overlay_accepts_any_iterable_of_mappings() -> None:
    rows = ({"ticker": ticker} for ticker in ("AAPL", "MSFT"))
    out = depth_overlay.overlay(rows, {"AAPL": {"pe": 21.3}, "MSFT": {"pe": 34.0}})

    assert [row["pe"] for row in out] == [21.3, 34.0]


def test_overlay_accepts_a_mapping_that_is_not_a_dict() -> None:
    row = MappingProxyType({"ticker": "AAPL", "pe": 15.0})
    filled = depth_overlay.overlay([row], {"AAPL": {"pe": 21.3, "roe": 1.47}})[0]

    assert isinstance(filled, dict)
    assert filled == {"ticker": "AAPL", "pe": 15.0, "roe": 1.47}


def test_overlay_appends_filled_keys_in_a_store_independent_order() -> None:
    row: dict[str, Any] = {"ticker": "AAPL"}
    forward = depth_overlay.overlay([row], {"AAPL": {"pe": 21.3, "roe": 1.47}})[0]
    reverse = depth_overlay.overlay([row], {"AAPL": {"roe": 1.47, "pe": 21.3}})[0]

    assert forward == reverse
    assert list(forward) == list(reverse), (
        "the key order must depend on the catalog, not on the order the store "
        f"happened to serialise its entry: {list(forward)} vs {list(reverse)}"
    )
    assert list(forward)[:1] == ["ticker"], "original keys keep their position"


# ---------------------------------------------------------------------------
# Independence — duplicates, unrelated entries, custom ticker key
# ---------------------------------------------------------------------------


def test_overlay_handles_duplicate_rows_independently() -> None:
    row: dict[str, Any] = {"ticker": "AAPL"}
    out = depth_overlay.overlay([row, row], {"AAPL": {"pe": 21.3}})

    assert len(out) == 2
    assert out[0] == {"ticker": "AAPL", "pe": 21.3}
    assert out[1] == {"ticker": "AAPL", "pe": 21.3}
    assert out[0] is not out[1], "two rows must never share one output dict"
    assert row == {"ticker": "AAPL"}


def test_overlay_ignores_depth_entries_for_symbols_not_in_the_rows() -> None:
    rows = [{"ticker": "AAPL"}]
    depth = {"AAPL": {"pe": 21.3}, "MSFT": {"pe": 34.0}, "NVDA": {"pe": 60.1}}

    out = depth_overlay.overlay(rows, depth)

    assert len(out) == 1, "an unused depth entry must not become a row"
    assert out[0] == {"ticker": "AAPL", "pe": 21.3}


def test_overlay_reads_the_ticker_from_the_custom_key() -> None:
    row: dict[str, Any] = {"symbol": "AAPL", "ticker": "MSFT"}

    by_symbol = depth_overlay.overlay(
        [row], {"AAPL": {"pe": 21.3}}, default_ticker_key="symbol"
    )[0]
    by_ticker = depth_overlay.overlay(
        [row], {"MSFT": {"pe": 34.0}}, default_ticker_key="ticker"
    )[0]

    assert by_symbol["pe"] == 21.3
    assert by_ticker["pe"] == 34.0


def test_overlay_does_not_fall_back_to_another_key_name() -> None:
    row: dict[str, Any] = {"symbol": "AAPL", "pe": 15.0}
    out = depth_overlay.overlay([row], {"AAPL": {"pe": 21.3}})

    assert out[0] == row, (
        "the ticker key is named by default_ticker_key; a row without it is not "
        f"resolved through a guessed alternative, got {out[0]!r}"
    )


# ---------------------------------------------------------------------------
# Robustness — the store is data, so a broken cell is skipped, not trusted
# ---------------------------------------------------------------------------


def test_overlay_accepts_a_whole_store_as_well_as_its_symbols_table() -> None:
    row: dict[str, Any] = {"ticker": "BRK.B"}
    store = _depth("BRK-B", pe=8.1)

    from_table = depth_overlay.overlay([row], store["symbols"])[0]
    from_store = depth_overlay.overlay([row], store)[0]

    assert from_table["pe"] == 8.1
    assert from_store == from_table


def test_overlay_skips_a_non_mapping_entry_and_keeps_trying() -> None:
    row: dict[str, Any] = {"ticker": "BRK.B"}
    depth: dict[str, Any] = {"BRK-B": "corrupt", "BRK.B": {"pe": 8.1}}
    filled = depth_overlay.overlay([row], depth)[0]

    assert filled["pe"] == 8.1, "a corrupt entry is not an answer; the next spelling is"


def test_overlay_leaves_a_row_alone_when_its_entry_is_not_a_mapping() -> None:
    row: dict[str, Any] = {"ticker": "AAPL", "pe": 15.0}
    corrupt: dict[str, Any] = {"AAPL": None}
    out = depth_overlay.overlay([row], corrupt)

    assert out[0] == row

