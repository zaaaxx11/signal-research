"""Choice filters: three Finviz keys compile their options into row predicates.

The contract (P11H): ``ta_perf``, ``ta_perf2`` and ``ta_volatility`` are the keys
whose *option vocabulary* is the filter. A criterion still reads
``{key, op: "eq", value: <option>}``; :mod:`app.screen.choice_filters` turns the
token into an ordinary ``field op number`` predicate over a column a parity row
really carries, and the request then runs through the same executor and the same
``apply_filters`` as every other criterion.

What this module pins, and why each claim needs a test:

* **the window map is Finviz's, not a guess** — ``1w10u`` is ``ret_1w <= -10``,
  not "week under +10%": the ``u`` side is the negative one, and the exact table
  for both performance keys and both volatility windows is asserted;
* **the vocabulary is the probe's** — every token in
  ``tests/fixtures/finviz_filters.json`` compiles or is refused by name, and a
  token outside it (``v2``, ``1w15u``, the panel's ``""``/``modal``) is a 422
  with its own code instead of being rounded to a neighbouring bucket;
* **the 3-year window is refused, not approximated** — no screen row carries a
  three-year return, so every ``3y…`` token names the missing ``ret_3y`` column
  and the nine windows that do compile;
* **the executor behaves** — a null cell matches no bucket, ``0`` is neither up
  nor down, thresholds are inclusive, ``total`` counts every match, and the
  response echoes both the Finviz key and the predicate it compiled to;
* **the API and the published schema say the same thing** — the 422 codes are
  switchable, and every option string in ``x-filter-keys`` parses back through
  the filter DSL into the identical predicate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.screen import choice_filters
from app.screen.choice_filters import (
    CHOICE_KEYS,
    CONTROL_TOKENS,
    UnknownChoiceOption,
    UnsupportedChoiceOption,
    compile_option,
    describe,
    fields_for,
    options_for,
)
from app.screen.filter_fields import (
    CHOICE_FIELDS,
    ROW_FIELDS,
    ChoiceFilterKey,
    binding,
    choice_keys,
    field_for,
)
from app.screen.filters import FIELDS, FILTER_CATALOG, parse_filters
from app.screen.selection import (
    SELECTION_REQUEST_SCHEMA,
    SelectionError,
    parse_selection,
    select_rows,
)
from app.screen.universe import CARRIED_FIELDS, PARITY_COLUMNS, build_universe
from tests import _screen as S

#: The moment the executor stamps on its rows; fixed so two runs are comparable.
AS_OF = "2026-10-23T12:00:00.000000+00:00"

#: The three keys this module is about, in panel order.
KEYS: tuple[str, ...] = ("ta_perf", "ta_perf2", "ta_volatility")

#: Finviz's ten performance windows and the row column each one reads. ``3y`` is
#: the only window no row carries: ``ret_3y`` lives on the deep/quote path.
WINDOWS: dict[str, str | None] = {
    "d": "change_pct",
    "1w": "ret_1w",
    "4w": "ret_1m",
    "13w": "ret_3m",
    "26w": "ret_6m",
    "ytd": "ret_ytd",
    "52w": "ret_1y",
    "3y": None,
    "5y": "ret_5y",
    "10y": "ret_10y",
}

#: A hand-checked sample of tokens → the predicate each one must compile to.
#: Both performance keys share the vocabulary, so the table is asserted for each.
PERFORMANCE_CASES: tuple[tuple[str, str, str, float], ...] = (
    ("dup", "change_pct", "gt", 0.0),
    ("ddown", "change_pct", "lt", 0.0),
    ("d5u", "change_pct", "lte", -5.0),
    ("d5o", "change_pct", "gte", 5.0),
    ("d15o", "change_pct", "gte", 15.0),
    ("1wup", "ret_1w", "gt", 0.0),
    ("1wdown", "ret_1w", "lt", 0.0),
    ("1w10u", "ret_1w", "lte", -10.0),
    ("1w30o", "ret_1w", "gte", 30.0),
    ("4w50u", "ret_1m", "lte", -50.0),
    ("4w50o", "ret_1m", "gte", 50.0),
    ("13w10u", "ret_3m", "lte", -10.0),
    ("13wup", "ret_3m", "gt", 0.0),
    ("26w75u", "ret_6m", "lte", -75.0),
    ("26w100o", "ret_6m", "gte", 100.0),
    ("ytd5u", "ret_ytd", "lte", -5.0),
    ("ytd100o", "ret_ytd", "gte", 100.0),
    ("52wup", "ret_1y", "gt", 0.0),
    ("52w500o", "ret_1y", "gte", 500.0),
    ("5y90u", "ret_5y", "lte", -90.0),
    ("5y1000o", "ret_5y", "gte", 1000.0),
    ("10y10u", "ret_10y", "lte", -10.0),
    ("10y1000o", "ret_10y", "gte", 1000.0),
)

#: One provider row per behaviour the executor has to tell apart.
ROWS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("UP", {"Perf.W": 12.0, "Perf.1M": 4.0, "Volatility.W": 6.5, "Volatility.M": 11.0}),
    ("CRASH", {"Perf.W": -12.0, "Perf.1M": -6.0, "Volatility.W": 2.0, "Volatility.M": 4.0}),
    ("EDGE", {"Perf.W": -10.0, "Volatility.W": 5.0}),
    ("FLAT", {"Perf.W": 0.0, "Perf.1M": 0.0, "Volatility.M": 9.0}),
    ("NONE", {}),
)


def provider_row(symbol: str, **cells: Any) -> dict[str, Any]:
    """One TradingView-shaped scan row, with the light cells the universe needs."""

    row: dict[str, Any] = {
        "symbol": symbol,
        "name": symbol,
        "description": f"{symbol} Holdings Inc.",
        "exchange": "NASDAQ",
        "country": "US",
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


def universe() -> list[dict[str, Any]]:
    """The five rows above, through the real universe builder (parity columns)."""

    return build_universe(
        [provider_row(symbol, **cells) for symbol, cells in ROWS],
        region="us",
        min_market_cap=0,
        columns=PARITY_COLUMNS,
        allow_any_subtype=True,
    )


def criteria(*filters: dict[str, Any], sort: str = "market_cap", limit: int = 20) -> dict[str, Any]:
    return {
        "mode": "criteria",
        "region": "us",
        "criteria": {"filters": list(filters), "sort": sort, "limit": limit},
    }


def run(
    *filters: dict[str, Any],
    rows: list[dict[str, Any]] | None = None,
    sort: str = "market_cap",
    limit: int = 20,
) -> dict[str, Any]:
    request = parse_selection(criteria(*filters, sort=sort, limit=limit))
    selection = select_rows(
        universe() if rows is None else rows,
        request,
        region="us",
        as_of=AS_OF,
    )
    return selection.as_document()


def symbols_of(document: dict[str, Any]) -> list[str]:
    return [row["symbol"] for row in document["rows"]]


def predicate(document: dict[str, Any], index: int = 0) -> dict[str, Any]:
    return document["filters"][index]


# ---------------------------------------------------------------------------
# The window map: ten windows, nine columns, one honest refusal
# ---------------------------------------------------------------------------


def test_the_three_keys_are_the_only_option_driven_keys() -> None:
    assert CHOICE_KEYS == KEYS
    assert choice_keys() == KEYS
    assert tuple(CHOICE_FIELDS) == KEYS
    for key in KEYS:
        assert FILTER_CATALOG[key].choice is True
        assert FILTER_CATALOG[key].field is None, "no single column names ten windows"
        assert key not in ROW_FIELDS
        assert binding(key).is_available is True
        assert binding(key).choice is True
        assert choice_filters.CHOICES[key].label == FILTER_CATALOG[key].label
    assert set(CHOICE_KEYS) == set(choice_filters.CHOICES)


def test_the_performance_windows_are_finviz_ten_with_one_column_missing() -> None:
    windows = {window.token: window.field for window in choice_filters.PERFORMANCE_WINDOWS}
    assert windows == WINDOWS
    assert list(windows) == ["d", "1w", "4w", "13w", "26w", "ytd", "52w", "3y", "5y", "10y"]
    assert [window.token for window in choice_filters.PERFORMANCE_WINDOWS if not window.is_carried] == [
        "3y"
    ], "the 3-year window is the only one the row cannot answer"
    assert fields_for("ta_perf") == tuple(
        field for field in WINDOWS.values() if field is not None
    )


def test_the_volatility_windows_are_the_weekly_and_the_monthly_column() -> None:
    windows = {window.token: window.field for window in choice_filters.VOLATILITY_WINDOWS}
    assert windows == {"wo": "volatility_w", "mo": "volatility_m"}
    assert fields_for("ta_volatility") == ("volatility_w", "volatility_m")


def test_every_choice_column_is_a_numeric_column_a_parity_row_carries() -> None:
    for key in KEYS:
        assert fields_for(key)
        for column in fields_for(key):
            assert FIELDS[column] == "number"
            assert column in CARRIED_FIELDS, f"{key} reads {column}, absent from a parity row"
    for token, compiled in choice_filters.choices_for("ta_perf").items():
        assert compiled.field in WINDOWS.values(), token


# ---------------------------------------------------------------------------
# The vocabulary: the probe's tokens, compiled or refused by name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", KEYS)
def test_the_vocabulary_is_the_catalogue_minus_the_panel_controls(key: str) -> None:
    tokens = options_for(key)
    expected = tuple(
        token for token in FILTER_CATALOG[key].options if token not in CONTROL_TOKENS
    )
    assert tokens == expected, "the probe's order, minus the UI controls"
    assert tokens and not set(tokens) & CONTROL_TOKENS
    assert set(choice_filters.choices_for(key)) | set(
        choice_filters.unsupported_for(key)
    ) == set(expected), "every catalogue token is compiled or refused, none invented"


def test_every_control_token_is_refused_not_ignored() -> None:
    assert CONTROL_TOKENS == {"", "modal", "modal_intraday"}
    for key in KEYS:
        for token in CONTROL_TOKENS:
            with pytest.raises(UnknownChoiceOption):
                compile_option(key, token)


@pytest.mark.parametrize("key", ("ta_perf", "ta_perf2"))
@pytest.mark.parametrize(("token", "field", "op", "value"), PERFORMANCE_CASES)
def test_a_performance_token_compiles_to_its_window_column(
    key: str, token: str, field: str, op: str, value: float
) -> None:
    compiled = compile_option(key, token)
    assert (compiled.field, compiled.op, compiled.value) == (field, op, value)


@pytest.mark.parametrize("key", ("ta_perf", "ta_perf2"))
def test_the_down_side_is_negative_and_the_over_side_is_positive(key: str) -> None:
    """``1w10u`` is week *down* 10% — the sign is the whole meaning of ``u``."""

    for token, compiled in choice_filters.choices_for(key).items():
        if token.endswith("up"):
            assert compiled.op == "gt" and compiled.value == 0.0
        elif token.endswith("down"):
            assert compiled.op == "lt" and compiled.value == 0.0
        elif token.endswith("u"):
            assert compiled.op == "lte" and compiled.value < 0, token
        else:
            assert token.endswith("o"), token
            assert compiled.op == "gte" and compiled.value > 0, token


@pytest.mark.parametrize("key", ("ta_perf", "ta_perf2"))
def test_performance_two_is_the_same_vocabulary_and_predicates(key: str) -> None:
    assert set(choice_filters.choices_for(key)) == set(
        choice_filters.choices_for("ta_perf")
    )
    assert dict(choice_filters.choices_for(key)) == dict(
        choice_filters.choices_for("ta_perf")
    ), "the twin dropdown compiles every token to the same predicate"
    assert set(choice_filters.unsupported_for(key)) == set(
        choice_filters.unsupported_for("ta_perf")
    ), "and refuses the same three-year tokens (each 422 naming its own key)"


@pytest.mark.parametrize(
    ("token", "field", "value"),
    [
        ("wo2", "volatility_w", 2.0),
        ("wo5", "volatility_w", 5.0),
        ("wo12", "volatility_w", 12.0),
        ("wo15", "volatility_w", 15.0),
        ("mo2", "volatility_m", 2.0),
        ("mo10", "volatility_m", 10.0),
        ("mo15", "volatility_m", 15.0),
    ],
)
def test_a_volatility_token_means_at_least_n_percent(
    token: str, field: str, value: float
) -> None:
    compiled = compile_option("ta_volatility", token)
    assert (compiled.field, compiled.op, compiled.value) == (field, "gte", value)


def test_every_volatility_token_is_a_gte_threshold_on_its_own_column() -> None:
    for token, compiled in choice_filters.choices_for("ta_volatility").items():
        prefix = "wo" if token.startswith("wo") else "mo"
        assert token == f"{prefix}{int(compiled.value)}"
        assert compiled.field == ("volatility_w" if prefix == "wo" else "volatility_m")
        assert compiled.op == "gte" and compiled.value >= 2.0
    assert not choice_filters.unsupported_for("ta_volatility"), "both windows have a column"


def test_the_three_year_window_is_refused_by_name_not_approximated() -> None:
    refused = choice_filters.unsupported_for("ta_perf")
    assert set(refused) == {token for token in options_for("ta_perf") if token.startswith("3y")}
    assert len(refused) == 17, "the probe's 3y tokens: 90u…10u, down, up, 10o…1000o"
    for token, reason in refused.items():
        with pytest.raises(UnsupportedChoiceOption) as caught:
            compile_option("ta_perf", token)
        assert caught.value.code == "unsupported_filter_option"
        assert token in str(caught.value)
        assert "3-year" in str(caught.value)
        assert "ret_3y" in str(caught.value), "the refusal names the column that would answer it"
        assert "5y" in str(caught.value), "and the windows that do compile"
        assert reason == str(caught.value)


def test_no_compiled_predicate_pretends_to_be_a_three_year_return() -> None:
    """A 3-year bucket must not land on ``ret_1y`` or ``ret_5y`` by accident."""

    for token, compiled in choice_filters.choices_for("ta_perf").items():
        if token.startswith("3y"):
            raise AssertionError(f"{token} compiled to {compiled}; the 3y window has no column")
    assert all(
        compiled.field != "ret_3y"
        for compiled in choice_filters.choices_for("ta_perf").values()
    ), "ret_3y is not a screen column; a predicate may not name it"


# ---------------------------------------------------------------------------
# Unknown, invented or malformed options are 422s with their own code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "option",
    [
        "v2",  # not a window
        "1w15u",  # valid grammar, not a Finviz bucket
        "wo11",  # valid grammar, not a Finviz bucket
        "1w10",  # no direction at all
        "1w 10u",  # whitespace inside the token
        " 1w10u",  # surrounding whitespace: strict, like symbols mode
        "2y10u",  # a window Finviz does not have
        10,  # a number is not a token
        None,
    ],
)
def test_an_unknown_option_is_refused_with_the_vocabulary_named(option: Any) -> None:
    with pytest.raises(UnknownChoiceOption) as caught:
        compile_option("ta_perf", option)
    error = caught.value
    assert error.code == "unknown_filter_option"
    message = str(error)
    assert "ta_perf" in message and "Performance" in message
    assert repr(option) in message, "the refusal names the token it refused"
    assert "x-filter-keys.ta_perf.choice.options" in message, (
        "and points at the published vocabulary instead of guessing"
    )


def test_the_describe_helpers_refuse_a_key_that_is_not_option_driven() -> None:
    for helper in (
        choice_filters.options_for,
        choice_filters.choices_for,
        choice_filters.unsupported_for,
        choice_filters.fields_for,
        describe,
    ):
        with pytest.raises(choice_filters.NotChoiceFilter) as caught:
            helper("fa_pe")
        assert caught.value.code == "not_a_choice_filter"
        assert "ta_perf" in str(caught.value)


def test_the_boot_gate_refuses_a_catalogue_flag_that_drifted(monkeypatch: Any) -> None:
    """``filter_fields`` reads ``spec.choice``; a silent False must be a boot error."""

    from dataclasses import replace

    drifted = dict(choice_filters.FILTER_CATALOG)
    drifted["ta_perf"] = replace(drifted["ta_perf"], choice=False)
    monkeypatch.setattr(choice_filters, "FILTER_CATALOG", drifted)
    with pytest.raises(RuntimeError, match="ta_perf"):
        choice_filters._validate()


def test_field_for_refuses_a_choice_key_instead_of_picking_a_window() -> None:
    """Answering with one column would silently choose a window nobody asked for."""

    with pytest.raises(ChoiceFilterKey) as caught:
        field_for("ta_perf")
    assert caught.value.code == "choice_filter_key"
    assert "ret_1w" in str(caught.value) and "ret_1m" in str(caught.value)
    assert field_for("ta_change") == "change_pct", "single-column keys still answer"


def test_a_key_with_a_single_column_is_not_an_option_filter() -> None:
    with pytest.raises(choice_filters.NotChoiceFilter) as caught:
        compile_option("cap", "mega")
    assert caught.value.code == "not_a_choice_filter"
    assert "ta_perf" in str(caught.value), "the refusal lists the option-driven keys"


def test_a_choice_key_without_eq_is_a_422_that_explains_the_shape() -> None:
    with pytest.raises(SelectionError) as caught:
        parse_selection(criteria({"key": "ta_perf", "op": "gte", "value": "1wup"}))
    assert caught.value.code == "invalid_filter"
    message = str(caught.value)
    assert "op 'eq'" in message
    assert "'1wup'" in message or "dup" in message, "the message shows a usable shape"

    with pytest.raises(SelectionError):
        parse_selection(criteria({"key": "ta_perf", "op": 7, "value": "1wup"}))


def test_an_unknown_option_reaches_the_validator_as_its_own_code() -> None:
    with pytest.raises(SelectionError) as caught:
        parse_selection(criteria({"key": "ta_volatility", "op": "eq", "value": "wo11"}))
    assert caught.value.code == "unknown_filter_option"
    assert caught.value.status_code == 422
    assert "wo11" in str(caught.value)


def test_a_three_year_option_reaches_the_validator_as_unsupported() -> None:
    with pytest.raises(SelectionError) as caught:
        parse_selection(criteria({"key": "ta_perf2", "op": "eq", "value": "3ydown"}))
    assert caught.value.code == "unsupported_filter_option"
    assert caught.value.status_code == 422
    assert "ret_3y" in str(caught.value)


# ---------------------------------------------------------------------------
# The executor: one bucket, one predicate, null never matches
# ---------------------------------------------------------------------------


def test_one_week_up_selects_only_the_positive_row() -> None:
    document = run({"key": "ta_perf", "op": "eq", "value": "1wup"})
    assert symbols_of(document) == ["UP"]
    assert document["total"] == 1
    assert document["universe_total"] == 5
    assert predicate(document) == {
        "key": "ta_perf",
        "field": "ret_1w",
        "op": "gt",
        "value": 0.0,
    }


def test_one_week_down_ten_selects_the_crash_and_the_boundary_row() -> None:
    document = run({"key": "ta_perf", "op": "eq", "value": "1w10u"})
    assert set(symbols_of(document)) == {"CRASH", "EDGE"}, (
        "EDGE is exactly -10, and the bucket boundary is inclusive"
    )
    assert document["total"] == 2
    assert predicate(document)["value"] == -10.0
    assert predicate(document)["op"] == "lte"


def test_flat_is_neither_up_nor_down() -> None:
    rows = [
        row for row in universe() if row["symbol"] == "FLAT"
    ]
    assert run({"key": "ta_perf", "op": "eq", "value": "1wup"}, rows=rows)["total"] == 0
    assert run({"key": "ta_perf", "op": "eq", "value": "1wdown"}, rows=rows)["total"] == 0
    assert run({"key": "ta_perf", "op": "eq", "value": "1w10u"}, rows=rows)["total"] == 0


def test_a_null_performance_cell_matches_no_bucket() -> None:
    """Unknown is not zero: a row the scan did not fill cannot join a bucket."""

    rows = [row for row in universe() if row["symbol"] == "NONE"]
    assert rows[0]["ret_1w"] is None
    for token in ("1wup", "1wdown", "1w10u", "1w10o"):
        document = run({"key": "ta_perf", "op": "eq", "value": token}, rows=rows)
        assert document["total"] == 0, f"{token} matched a null ret_1w"
        assert document["returned"] == 0


def test_the_bucket_and_another_filter_are_anded() -> None:
    rows = universe()
    assert all(row["sector"] == "Technology" for row in rows)
    document = run(
        {"key": "ta_perf", "op": "eq", "value": "1wdown"},
        {"key": "sec", "op": "eq", "value": "Technology"},
        rows=rows,
    )
    assert set(symbols_of(document)) == {"CRASH", "EDGE"}
    document = run(
        {"key": "ta_perf", "op": "eq", "value": "1wdown"},
        {"key": "sec", "op": "eq", "value": "Energy"},
        rows=rows,
    )
    assert document["total"] == 0


def test_total_counts_every_match_while_the_page_is_capped() -> None:
    document = run({"key": "ta_perf", "op": "eq", "value": "1wdown"}, limit=1)
    assert document["returned"] == 1
    assert document["total"] == 2, "the page is not the market"


def test_the_same_predicate_written_as_a_field_selects_the_same_rows() -> None:
    """``ta_perf: 1w10u`` is a spelling of ``ret_1w lte -10``, no more."""

    by_key = run({"key": "ta_perf", "op": "eq", "value": "1w10u"})
    by_field = run({"field": "ret_1w", "op": "lte", "value": -10})
    assert symbols_of(by_key) == symbols_of(by_field)
    assert by_key["total"] == by_field["total"]
    assert predicate(by_key)["field"] == predicate(by_field)["field"]
    assert predicate(by_key)["op"] == predicate(by_field)["op"]
    assert predicate(by_key)["value"] == predicate(by_field)["value"]


def test_performance_two_filters_the_same_column() -> None:
    first = run({"key": "ta_perf2", "op": "eq", "value": "1w10u"})
    second = run({"key": "ta_perf", "op": "eq", "value": "1w10u"})
    assert symbols_of(first) == symbols_of(second) == ["CRASH", "EDGE"]
    assert predicate(first)["key"] == "ta_perf2"
    assert predicate(first)["field"] == "ret_1w"


def test_volatility_week_and_month_select_on_their_own_column() -> None:
    week = run({"key": "ta_volatility", "op": "eq", "value": "wo6"})
    assert set(symbols_of(week)) == {"UP"}, "6.5 is over 6; 5.0 and 2.0 are not"
    assert predicate(week)["field"] == "volatility_w"

    month = run({"key": "ta_volatility", "op": "eq", "value": "mo9"})
    assert set(symbols_of(month)) == {"UP", "FLAT"}, "11.0 and 9.0 clear the 9 bar"
    assert predicate(month)["field"] == "volatility_m"
    assert predicate(month)["op"] == "gte"


def test_a_choice_selection_is_deterministic() -> None:
    first = run({"key": "ta_perf", "op": "eq", "value": "1wdown"})
    second = run({"key": "ta_perf", "op": "eq", "value": "1wdown"})
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_the_same_key_twice_is_still_an_and() -> None:
    document = run(
        {"key": "ta_perf", "op": "eq", "value": "1wdown"},
        {"key": "ta_perf", "op": "eq", "value": "1w10u"},
    )
    assert set(symbols_of(document)) == {"CRASH", "EDGE"}
    document = run(
        {"key": "ta_perf", "op": "eq", "value": "1wup"},
        {"key": "ta_perf", "op": "eq", "value": "1w10u"},
    )
    assert document["total"] == 0, "up and down-10 cannot both hold"


# ---------------------------------------------------------------------------
# The published schema is the same contract
# ---------------------------------------------------------------------------


def test_the_schema_publishes_every_option_and_its_predicate() -> None:
    artifact = Path(__file__).resolve().parents[1] / "schemas" / "stock_selection_request_v1.json"
    assert json.loads(artifact.read_text(encoding="utf-8")) == SELECTION_REQUEST_SCHEMA

    extension = SELECTION_REQUEST_SCHEMA["x-filter-keys"]
    published: dict[str, Any] = dict(extension)  # type: ignore[arg-type]
    for key in KEYS:
        entry = published[key]
        assert entry["field"] is None
        assert "reason" not in entry
        choice = entry["choice"]
        assert choice["fields"] == list(fields_for(key))
        assert choice["windows"] == {
            window.token: window.field
            for window in choice_filters.CHOICES[key].windows
        }
        assert set(choice["options"]) == set(choice_filters.choices_for(key))
        assert set(choice["unsupported"]) == {
            window.token
            for window in choice_filters.CHOICES[key].windows
            if window.missing is not None
        }
        assert extension[key]["choice"] == describe(key)


def test_every_published_option_expression_parses_back_to_the_same_predicate() -> None:
    """The schema does not publish a second grammar: it publishes DSL expressions."""

    extension = SELECTION_REQUEST_SCHEMA["x-filter-keys"]
    published: dict[str, Any] = dict(extension)  # type: ignore[arg-type]
    for key in KEYS:
        for token, expression in published[key]["choice"]["options"].items():
            assert parse_filters(expression) == (compile_option(key, token),)


def test_the_schema_publishes_the_choice_codes_vocabulary_and_counts() -> None:
    extension = SELECTION_REQUEST_SCHEMA["x-filter-keys"]
    published: dict[str, Any] = dict(extension)  # type: ignore[arg-type]
    chosen = [key for key, entry in published.items() if "choice" in entry]
    assert chosen == list(KEYS)
    mapped = [key for key, entry in published.items() if entry["field"] is not None]
    assert len(mapped) == 42
    refused = [
        key
        for key, entry in published.items()
        if entry["field"] is None and "choice" not in entry
    ]
    assert len(refused) == 37
    assert len(mapped) + len(chosen) + len(refused) == 82

    definitions: dict[str, Any] = dict(SELECTION_REQUEST_SCHEMA["$defs"])  # type: ignore[arg-type]
    description = definitions["filter"]["properties"]["key"]["description"]
    assert "the 3 that filter through their option vocabulary" in description


# ---------------------------------------------------------------------------
# The API: the same request, the same answer, the same codes
# ---------------------------------------------------------------------------


def api_body(*filters: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "criteria",
        "region": "us",
        "criteria": {"filters": list(filters), "sort": "market_cap", "limit": 20},
    }


def error_envelope(response: Any) -> dict[str, Any]:
    body = response.json()
    assert set(body) == {"error", "errorMessage"}, body
    return body


async def test_the_api_runs_a_choice_filter_and_echoes_the_predicate(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select", json=api_body({"key": "ta_perf", "op": "eq", "value": "dup"})
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] >= 1
    assert body["total"] >= body["returned"]
    assert body["filters"] == [
        {"key": "ta_perf", "field": "change_pct", "op": "gt", "value": 0.0}
    ]
    assert all(row["change_pct"] > 0 for row in body["rows"]), (
        "the fixture rows carry change, so a 'today up' bucket must select positives"
    )


async def test_the_api_refuses_an_unknown_option_with_a_switchable_code(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select", json=api_body({"key": "ta_perf", "op": "eq", "value": "v2"})
        )

    assert response.status_code == 422, response.text
    error = error_envelope(response)
    assert error["error"] == "unknown_filter_option"
    assert "v2" in error["errorMessage"]


async def test_the_api_refuses_the_three_year_window_by_name(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select", json=api_body({"key": "ta_perf2", "op": "eq", "value": "3y10u"})
        )

    assert response.status_code == 422, response.text
    error = error_envelope(response)
    assert error["error"] == "unsupported_filter_option"
    assert "ret_3y" in error["errorMessage"]


async def test_the_api_refuses_a_choice_key_with_a_numeric_operator(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select", json=api_body({"key": "ta_volatility", "op": "gte", "value": 5})
        )

    assert response.status_code == 422, response.text
    error = error_envelope(response)
    assert error["error"] == "invalid_filter"
    assert "op 'eq'" in error["errorMessage"]


async def test_the_schema_route_serves_the_option_vocabulary(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.get("/screens/select/schema")

    assert response.status_code == 200, response.text
    served = response.json()
    choice = served["x-filter-keys"]["ta_volatility"]["choice"]
    assert choice["options"]["mo15"] == "volatility_m gte 15.0"
    assert served["x-filter-keys"]["ta_perf"]["choice"]["unsupported"].keys() == {"3y"}
    assert served["x-filter-keys"]["ta_perf"]["choice"]["options"]["1w10u"] == (
        "ret_1w lte -10.0"
    )
