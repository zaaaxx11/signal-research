"""The catalogue-key → row-field resolver: one table, three readers, real data.

What the audit found (``research/phase0-adversarial.md`` F1, F2, F5, F6) and what
these tests pin, in the order the findings are numbered:

* **F1** the nine canonical views named 100 catalogue keys and 62 of them had no
  entry in ``VIEW_FIELDS``, so they rendered ``null`` forever with the value on
  the row. ``test_no_canonical_view_column_is_unmapped`` counts the holes;
  ``test_income_and_profile_answer_every_cell_from_the_store`` and
  ``test_no_view_column_is_null_for_a_symbol_the_store_covers`` prove they are
  filled — against the real depth store (``/home/ubuntu/ztockz-data/depth.json``),
  not a fixture constant, and the store-based tests skip when it is absent.
* **F2** ``columns`` read the name verbatim while ``view`` went through a table,
  so the same twelve keys returned different numbers for ``pe``, ``market_cap``,
  ``pc`` and ``ev_sales``. ``test_view_and_columns_are_the_same_panel_on_twenty_rows``
  compares the two spellings cell by cell on twenty real rows.
* **F5** three alias tables had drifted (``perf_1w`` filtered, ``perf_4w`` was a
  422). ``test_the_four_performance_windows_filter`` and
  ``test_one_table_is_the_only_mapping`` pin the collapse.
* **F6** ``average_volume`` read a ten-day window for a multi-week column. The
  behaviour is covered in ``tests/test_screen_selection_panel.py`` (two spellings
  must select one row set); this file pins the mapping and the ten-day cell.

The adversarial requirements from the brief are the last four tests: an honestly
absent metric renders ``None`` for the symbols that lack it, an absent metric is
never ``0``, the frozen ``valuation`` tab keeps its old numbers, and a row that
carries only the catalogue's spelling is read identically by all three paths — a
test that fails if any of them bypasses the resolver.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

import pytest

from app.screen import columns as column_map
from app.screen import views as V
from app.screen.depth_overlay import SCREEN_ALIASES, overlay
from app.screen.fundamentals import apply_view_fill
from app.screen.selection import VIEW_FIELDS, parse_selection, select_rows

#: The store the live service reads. ``ZTOCKZ_DATA_DIR`` overrides it the way the
#: service's own configuration does; a checkout without the store skips these
#: tests instead of failing, because the store is data, not a build artefact.
DATA_DIR = Path(os.environ.get("ZTOCKZ_DATA_DIR", "/home/ubuntu/ztockz-data"))
DEPTH_FILE = DATA_DIR / "depth.json"

#: The moment every row is stamped with, fixed so two runs compare byte for byte.
AS_OF = "2026-10-30T00:00:00.000000+00:00"

#: The session cells the *scan* owns: the store does not carry them and no other
#: source does either. A row built from the store alone has these three holes by
#: construction, which is exactly what the audit's F1 fix must keep honest.
SESSION_CELLS = ("open", "high", "low")

#: The columns of the ``value`` view — the panel the audit compared the two
#: spellings on (F2 repro C).
VALUE_COLUMNS: tuple[str, ...] = V.columns("value")


# ---------------------------------------------------------------------------
# The real store, and rows assembled the way the running service assembles them
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def store() -> Mapping[str, Mapping[str, Any]]:
    """The depth store's ``symbols`` table, or a skip when this box has none."""

    if not DEPTH_FILE.is_file():
        pytest.skip(f"no depth store at {DEPTH_FILE}")
    document = json.loads(DEPTH_FILE.read_text(encoding="utf-8"))
    symbols = document.get("symbols")
    if not isinstance(symbols, dict) or not symbols:
        pytest.skip(f"{DEPTH_FILE} holds no symbols")
    return symbols


def _present(entry: Mapping[str, Any], key: str) -> bool:
    return entry.get(key) is not None


def covered_symbols(
    store: Mapping[str, Mapping[str, Any]], *, columns: tuple[str, ...] = ()
) -> list[str]:
    """Store symbols whose entry carries every ``columns`` cell, fullest first."""

    wanted = columns or tuple(key for view in V.VIEWS for key in view.columns)
    scored = [
        (sum(1 for key in wanted if _present(entry, key)), symbol)
        for symbol, entry in store.items()
    ]
    best = max(score for score, _symbol in scored)
    return sorted(symbol for score, symbol in scored if score == best)


def pipeline_row(
    symbol: str,
    entry: Mapping[str, Any],
    *,
    price: float = 137.25,
    change_pct: float = 1.5,
) -> dict[str, Any]:
    """One row as ``POST /screens/select`` assembles it: scan cells + the store.

    The three session cells (price, change, volume) are the scan's own; the store
    does not carry them, so they are supplied here the way the provider would.
    Everything else comes from the real store entry through the real overlay and
    the real display fill — no hand-written metric values.
    """

    row: dict[str, Any] = {
        "symbol": symbol,
        "name": entry.get("company") or symbol,
        # The row's venue is the store's own label; the overlay never overwrites a
        # cell the row already carries, so feeding a made-up MIC here would make
        # the view legitimately disagree with the store.
        "exchange": entry.get("exchange") or "XNAS",
        "country": entry.get("country"),
        "sector": entry.get("sector"),
        "price": price,
        "change_pct": change_pct,
        "volume": 1_000_000.0,
        "turnover": price * 1_000_000.0,
        "turnover_usd": price * 1_000_000.0,
        "market_cap_usd": entry.get("market_cap"),
        "is_primary": True,
    }
    merged = overlay([row], {symbol: entry}, default_ticker_key="symbol", aliases=SCREEN_ALIASES)[0]
    return apply_view_fill([merged], {})[0]


def run(rows: list[dict[str, Any]], **payload: Any) -> dict[str, Any]:
    """One criteria selection over ``rows`` (the executor, not the HTTP route)."""

    body: dict[str, Any] = {
        "mode": "criteria",
        "region": "us",
        "criteria": {"filters": [], "sort": "market_cap", "limit": 100},
        **payload,
    }
    return select_rows(rows, parse_selection(body), region="us", as_of=AS_OF).as_document()


# ---------------------------------------------------------------------------
# F1 — the structural measure: no view column may map to nothing
# ---------------------------------------------------------------------------


def test_no_canonical_view_column_is_unmapped() -> None:
    """62 of 100 columns used to map to nothing. The number is zero now.

    The measure is independent of data: it asks the resolver, for every column the
    nine views declare, whether a row field exists for it. This is the check
    ``_validate_view_fields`` was missing (it proved every *entry* was real, never
    that every *column* had one).
    """

    declared = [column for view in V.VIEWS for column in view.columns]
    assert len(declared) == 100, "the nine canonical views declare 100 columns"

    unmapped = [
        name
        for name in declared
        if column_map.field_for(name) is None and name not in column_map.ABSENT_COLUMNS
    ]
    assert unmapped == [], (
        f"{len(unmapped)} of the 100 canonical view columns resolve to no row "
        f"field and would render null forever without saying why: {unmapped}"
    )

    # Three columns are *documented* absent — the session cells no store carries.
    # Stated as a number so a fourth cannot sneak in as a comment.
    absent = sorted({name for name in declared if name in column_map.ABSENT_COLUMNS})
    assert absent == sorted(SESSION_CELLS), absent
    for name in absent:
        assert column_map.absent_reason(name), f"{name} must say why it is absent"


def test_income_and_profile_answer_every_cell_from_the_store(
    store: Mapping[str, Mapping[str, Any]],
) -> None:
    """The two views the audit measured as 8/8 and 12/12 null, on real symbols."""

    for view_name, expected_count in (("income", 8), ("profile", 12)):
        columns = V.columns(view_name)
        assert len(columns) == expected_count
        symbols = covered_symbols(store, columns=columns)
        assert symbols, f"the store covers no symbol for {view_name}"
        symbol = symbols[0]
        entry = store[symbol]
        row = pipeline_row(symbol, entry)

        rendered = run([row], view=view_name)["rows"][0]
        nulls = [name for name in columns if rendered[name] is None]
        assert nulls == [], (
            f"view={view_name} still renders {len(nulls)}/{expected_count} null "
            f"for {symbol}, whose store entry carries them: {nulls}"
        )
        # ... and the numbers are the store's own, not a placeholder.
        for name in columns:
            field = column_map.field_for(name)
            assert field is not None
            stored = entry.get(name)
            if stored is None:  # a synonym's canonical cell was derived
                continue
            assert rendered[name] == stored, (
                f"{view_name}.{name} for {symbol} is {rendered[name]!r}, the store "
                f"says {stored!r}"
            )


def test_no_view_column_is_null_for_a_symbol_the_store_covers(
    store: Mapping[str, Mapping[str, Any]],
) -> None:
    """Every view answers every column for a symbol the store fully covers.

    The three session cells are the documented exception (see
    :data:`column_map.ABSENT_COLUMNS`): the store does not carry them and neither
    does the scan request them, so they are ``None`` — for every symbol, which is
    what makes that honest rather than a hole in this symbol.
    """

    all_columns = tuple(key for view in V.VIEWS for key in view.columns)
    symbol = covered_symbols(store, columns=all_columns)[0]
    row = pipeline_row(symbol, store[symbol])

    nulls: dict[str, list[str]] = {}
    for view in V.VIEWS:
        rendered = run([row], view=view.name)["rows"][0]
        holes = [name for name in view.columns if rendered[name] is None]
        if holes:
            nulls[view.name] = holes

    assert nulls == {"today": ["open", "high", "low"]}, (
        f"{symbol} is the store's fullest entry; the only holes may be the "
        f"session cells, got {nulls}"
    )
    total = len(all_columns)
    assert total - sum(len(v) for v in nulls.values()) == total - 3


# ---------------------------------------------------------------------------
# F2 — one panel, two spellings: `view` and `columns` must agree
# ---------------------------------------------------------------------------


def test_view_and_columns_are_the_same_panel_on_twenty_rows(
    store: Mapping[str, Mapping[str, Any]],
) -> None:
    """``view=value`` and ``columns=[value's own keys]``, cell by cell, 20 rows."""

    symbols = covered_symbols(store, columns=VALUE_COLUMNS)[:20]
    assert len(symbols) == 20, f"the store covers only {len(symbols)} symbols"
    rows = [pipeline_row(symbol, store[symbol]) for symbol in symbols]

    viewed = run(rows, view="value")["rows"]
    projected = run(rows, columns=list(VALUE_COLUMNS))["rows"]
    assert len(viewed) == len(projected) == 20

    for left, right in zip(viewed, projected):
        assert left["symbol"] == right["symbol"]
        for name in VALUE_COLUMNS:
            assert left[name] == right[name], (
                f"{left['symbol']}.{name}: view says {left[name]!r}, columns says "
                f"{right[name]!r} — one metric, two numbers"
            )
            assert left[name] is not None, f"{left['symbol']}.{name} is null in both"
        # A view row carries identity the projection does not, and nothing else.
        assert set(left) - set(right) == {"rank", "name"}


def test_columns_agree_on_the_four_renamed_keys(
    store: Mapping[str, Mapping[str, Any]],
) -> None:
    """``pe``/``pe_ttm``, ``market_cap``/``market_cap_usd``, ``pc``/``price_to_cash``,
    ``ev_sales``/``ev_to_sales``: two spellings, one number (audit F2 repro B).

    On the audited tree these pairs were 0.8 % apart, and ``price_to_cash`` /
    ``ev_to_sales`` were null on every row while their catalogue twins were
    filled. The resolver maps each pair to one field and the row harmoniser fills
    the canonical cell from the catalogue spelling, so both ends read one number.
    """

    pairs = (
        ("pe", "pe_ttm"),
        ("market_cap", "market_cap_usd"),
        ("pc", "price_to_cash"),
        ("ev_sales", "ev_to_sales"),
    )
    symbols = covered_symbols(store, columns=tuple(name for pair in pairs for name in pair))[:20]
    assert len(symbols) == 20
    rows = [pipeline_row(symbol, store[symbol]) for symbol in symbols]
    requested = [name for pair in pairs for name in pair]
    projected = run(rows, columns=requested)["rows"]

    for row in projected:
        for catalogue_key, screen_column in pairs:
            assert row[catalogue_key] == row[screen_column], (
                f"{row['symbol']}: {catalogue_key} {row[catalogue_key]!r} != "
                f"{screen_column} {row[screen_column]!r}"
            )
            assert row[catalogue_key] is not None, (
                f"{row['symbol']}.{catalogue_key} is null while the store holds it"
            )


def test_the_four_always_null_keys_are_filled_from_the_store(
    store: Mapping[str, Mapping[str, Any]],
) -> None:
    """``ps``, ``short_float``, ``enterprise_value``, ``cash_per_share`` — filled."""

    keys = ("ps", "short_float", "enterprise_value", "cash_per_share")
    symbols = covered_symbols(store, columns=keys)
    symbol = symbols[0]
    rendered = run([pipeline_row(symbol, store[symbol])], columns=list(keys))["rows"][0]
    nulls = [name for name in keys if rendered[name] is None]
    assert nulls == [], f"{symbol}'s store entry carries {keys} but {nulls} rendered null"


# ---------------------------------------------------------------------------
# F5 / F6 — the alias tables are one table, and the windows are what they say
# ---------------------------------------------------------------------------


def test_the_four_performance_windows_filter(store: Mapping[str, Mapping[str, Any]]) -> None:
    """``perf_1w`` filtered and ``perf_4w`` was a 422. All nine windows work now."""

    windows = {
        "perf_1w": "ret_1w",
        "perf_4w": "ret_1m",
        "perf_13w": "ret_3m",
        "perf_26w": "ret_6m",
        "perf_52w": "ret_1y",
        "perf_ytd": "ret_ytd",
        "perf_3y": "ret_3y",
        "perf_5y": "ret_5y",
        "perf_10y": "ret_10y",
    }
    for key, field in windows.items():
        request = parse_selection(
            {
                "mode": "criteria",
                "region": "us",
                "criteria": {"filters": [{"key": key, "op": "gt", "value": 0}]},
            }
        )
        assert request.filters[0].field == field, f"{key} must filter {field}"
        assert request.filters[0].key == key, "the envelope echoes the caller's key"

    # And it really filters: the store's own perf_4w values decide the answer.
    symbols = covered_symbols(store, columns=("perf_4w",))
    rows = [pipeline_row(symbol, store[symbol]) for symbol in symbols[:40]]
    positive = run(
        rows, criteria={"filters": [{"key": "perf_4w", "op": "gt", "value": 0}], "limit": 100}
    )
    expected = sum(1 for row in rows if (row.get("ret_1m") or 0) > 0)
    assert positive["total"] == expected
    assert positive["total"] > 0, "the sample must contain a month that went up"


def test_average_volume_is_the_month_window_not_the_ten_day_one() -> None:
    """F6: the mapping, stated once — and the ten-day cell still its own name."""

    assert column_map.field_for("average_volume") == "avg_volume_30d"
    assert column_map.field_for("avg_volume_10d") == "avg_volume_10d"
    assert "avg_volume_30d" in column_map.CATALOGUE_COLUMNS["average_volume"], (
        "average_volume must not resolve to the ten-day window"
    )
    # The same column Finviz's own key reads, so the two spellings agree.
    from app.screen.filter_fields import field_for as filter_field

    assert filter_field("sh_avgvol") == "avg_volume_30d"


def test_one_table_is_the_only_mapping() -> None:
    """The projections are derived, and no second literal table exists.

    ``SCREEN_ALIASES`` (the overlay's half), ``VIEW_FIELDS`` (the renderer's) and
    ``LEGACY_COLUMN_ALIASES`` (the frozen tabs') must all be projections of
    :data:`app.screen.columns.CATALOGUE_COLUMNS`. The last assertion greps the
    source: a literal catalogue→row-field mapping may only exist in
    ``columns.py``.
    """

    assert dict(SCREEN_ALIASES) == {
        key: field
        for key, field in column_map.CATALOGUE_COLUMNS.items()
        if key in column_map.CATALOGUE_KEY_SET and field != key
    }
    assert dict(VIEW_FIELDS) == dict(column_map.view_fields(tuple(VIEW_FIELDS)))
    assert all(column_map.field_for(name) == field for name, field in VIEW_FIELDS.items())
    for name, targets in V.LEGACY_COLUMN_ALIASES.items():
        assert targets == column_map.catalogue_keys_for(name)

    root = Path(__file__).resolve().parents[1]
    owners: dict[str, set[str]] = {}
    for path in (root / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in ('"average_volume": "avg_volume', '"pe": "pe_ttm"', '"pc": "price_to_cash"'):
            if needle in text:
                owners.setdefault(needle, set()).add(path.name)
    for needle, files in owners.items():
        assert files == {"columns.py"}, (
            f"{needle} appears in {sorted(files)}: the mapping has a second home"
        )
    assert column_map.field_for("average_volume") != "avg_volume_10d"


# ---------------------------------------------------------------------------
# Adversarial (a): an absent metric is null for the symbols that lack it
# ---------------------------------------------------------------------------


def test_a_metric_the_entry_lacks_renders_null_and_does_not_fail(
    store: Mapping[str, Mapping[str, Any]],
) -> None:
    """A thin entry renders nulls; the selection still answers its row."""

    thinnest = min(store, key=lambda symbol: sum(1 for value in store[symbol].values() if value is not None))
    entry = store[thinnest]
    row = pipeline_row(thinnest, entry)
    document = run([row], view="profile")
    assert document["returned"] == 1
    rendered = document["rows"][0]
    assert rendered["symbol"] == thinnest
    # Whatever the entry lacks is null; nothing raised, nothing was invented.
    missing = [name for name in V.columns("profile") if entry.get(name) is None and name != "company"]
    for name in missing:
        assert rendered[name] is None, f"{name} must be null, not a guess"

    # A symbol the store does not know at all is just as harmless.
    stranger: dict[str, Any] = {
        "symbol": "NOT-IN-THE-STORE",
        "name": "Uncovered Inc.",
        "price": 10.0,
        "change_pct": 0.5,
        "volume": 100.0,
        "market_cap_usd": 1_000_000.0,
    }
    document = run([stranger], view="income")
    assert document["returned"] == 1
    assert set(document["rows"][0][name] for name in V.columns("income")) == {None}


# ---------------------------------------------------------------------------
# Adversarial (b): absent is null, never zero
# ---------------------------------------------------------------------------


def test_absent_is_null_and_a_measured_zero_survives(
    store: Mapping[str, Mapping[str, Any]],
) -> None:
    """The resolver never manufactures a number, and never rounds a 0 away."""

    blank: dict[str, Any] = {"symbol": "BLANK", "rank": 1, "name": "Blank"}
    rendered = run([blank], view="income")["rows"][0]
    assert all(rendered[name] is None for name in V.columns("income")), (
        "an empty row must render null everywhere, not zeros"
    )
    for name in V.columns("income"):
        assert rendered[name] != 0
        assert rendered[name] != 0.0

    # A real zero in the store is a measurement and must survive.
    zero_symbol = next(
        (
            symbol
            for symbol, entry in store.items()
            if entry.get("dividend_ttm") == 0.0 and entry.get("dividend") is not None
        ),
        None,
    )
    if zero_symbol is None:  # pragma: no cover - depends on the store's contents
        pytest.skip("the store holds no measured zero dividend")
    zero_row = pipeline_row(zero_symbol, store[zero_symbol])
    rendered = run([zero_row], view="income")["rows"][0]
    assert rendered["dividend_ttm"] == 0.0, "0.0 is a value; the resolver must not swallow it"
    assert rendered["dividend"] is not None


# ---------------------------------------------------------------------------
# Adversarial (c): the frozen valuation tab keeps its old numbers
# ---------------------------------------------------------------------------


#: The ``valuation`` tab's column → row field contract as it stood before the
#: refactor (``VIEW_FIELDS`` at HEAD ``952fd8b``). The compatibility table has to
#: survive the move to one resolver, so the old targets are written down here as
#: the record the refactor is checked against.
FROZEN_VALUATION_FIELDS: Mapping[str, str] = {
    "market_cap": "market_cap_usd",
    "enterprise_value": "enterprise_value",
    "pe": "pe_ttm",
    "forward_pe": "pe_forward",
    "peg": "peg",
    "ps": "ps",
    "pb": "pb",
    "pc": "price_to_cash",
    "pfcf": "pfcf",
    "ev_ebitda": "ev_ebitda",
    "ev_sales": "ev_to_sales",
}


def test_the_frozen_valuation_tab_still_reads_its_old_fields(
    store: Mapping[str, Mapping[str, Any]],
) -> None:
    """``view=valuation`` must return exactly the numbers it always did."""

    assert tuple(FROZEN_VALUATION_FIELDS) == V.columns("valuation")
    for name, field in FROZEN_VALUATION_FIELDS.items():
        assert column_map.field_for(name) == field, (
            f"the compatibility tab renders {name} from {column_map.field_for(name)!r}, "
            f"it used to render {field!r}"
        )

    symbol = covered_symbols(store, columns=tuple(FROZEN_VALUATION_FIELDS))[0]
    row = pipeline_row(symbol, store[symbol])
    rendered = run([row], view="valuation")["rows"][0]
    for name, field in FROZEN_VALUATION_FIELDS.items():
        assert rendered[name] == row.get(field), (
            f"valuation.{name} reads {rendered[name]!r}, the old field {field} "
            f"holds {row.get(field)!r}"
        )


# ---------------------------------------------------------------------------
# Adversarial (d): all three paths use the resolver
# ---------------------------------------------------------------------------


def test_a_row_spelled_only_in_the_catalogue_is_read_by_all_three_paths() -> None:
    """A row carrying ``pe`` (not ``pe_ttm``) is read by view, columns *and* filter.

    This is the test that fails if one path bypasses the resolver: the row has no
    ``pe_ttm``, no ``price_to_cash`` and no ``market_cap_usd`` at all, so a
    renderer reading the name verbatim returns null, and a filter reading the
    canonical field matches nothing.
    """

    row: dict[str, Any] = {
        "symbol": "CATALOGUE",
        "rank": 1,
        "name": "Catalogue Spelling Inc.",
        "price": 50.0,
        "change_pct": 2.0,
        "volume": 1_000.0,
        "pe": 7.5,
        "market_cap": 2_000_000_000.0,
        "pc": 3.25,
        "ev_sales": 1.75,
        "net_income": 123_000_000.0,
        "dividend": 0.4,
    }

    viewed = run([row], view="value")["rows"][0]
    projected = run([row], columns=list(VALUE_COLUMNS))["rows"][0]
    assert viewed["pe"] == projected["pe"] == 7.5
    assert viewed["market_cap"] == projected["market_cap"] == 2_000_000_000.0
    assert viewed["pc"] == projected["pc"] == 3.25
    assert viewed["ev_sales"] == projected["ev_sales"] == 1.75

    cheap = run(
        [row],
        criteria={"filters": [{"key": "pe", "op": "lt", "value": 10}], "limit": 100},
    )
    assert cheap["total"] == 1, (
        "the filter read pe_ttm, the row spells pe: the resolver was bypassed"
    )
    # The same criterion written against the canonical field agrees, because the
    # row harmoniser put the catalogue's number where the filter looks.
    by_field = run(
        [row],
        criteria={"filters": [{"field": "pe_ttm", "op": "lt", "value": 10}], "limit": 100},
    )
    assert by_field["total"] == cheap["total"] == 1

    growth = run([row], view="growth")["rows"][0]
    assert growth["net_income"] == 123_000_000.0
