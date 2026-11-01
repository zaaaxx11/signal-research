"""P11J: ``screen_facts`` — the short/ownership cells, scraped once and joined once.

Three Finviz keys had no metric anywhere in the product: ``sh_short`` (Short
Float), ``sh_insiderown`` (Insider Ownership) and ``sh_instown`` (Institutional
Ownership). TradingView answers ``short_float`` with ``null`` for every row (live
probe: 0 of 5,720 US, 0 of 96,278 global) and the scan carries no ownership
column at all. The Finviz stock page does publish all three — live 2026-10-25 for
AAPL: ``Short Float 0.83%``, ``Insider Own 0.12%``, ``Inst Own 68.93%`` — and
:func:`app.providers.adapters.finviz.parse_facts` reads exactly those labels.

Each test pins one of the ways this can be wrong:

* a label read that borrows a neighbour (``Short Ratio``, ``Shs Float``,
  ``Insider Trans``), or a dash that becomes ``0``;
* a percentage scaled into a fraction (``0.83%`` read as 83%, or as 0.0083);
* a facts row that is partial (not full width), stale, or keyed to the wrong
  symbol;
* a per-symbol ``get()`` loop instead of one read of the table;
* a null that erases a value the scan carried, or a row mutated in place;
* a selection that fails because the optional table was never migrated;
* the disposition drifting back to 47/35, or the published schema lagging the
  catalogue.

The migration tests verify ``0005`` is **additive**: a database at ``0004``
keeps every ``fundamentals_snapshots`` row, gains an empty ``screen_facts``
table, and loses only that table on downgrade. The API tests use the real ASGI
app with a fake store injected at the runtime seam.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

import pytest
from alembic import command

from app.db import alembic_config, ensure_schema_async
from app.providers.adapters.finviz import FACT_FIELDS, parse_facts, parse_metrics
from app.screen import facts as F
from app.screen.filter_fields import (
    BUCKET_KEYS,
    CHOICE_FIELDS,
    ROW_FIELDS,
    binding,
    mapped_keys,
    reason_for,
    unavailable_keys,
)
from app.screen.filters import FIELDS, FILTER_CATALOG, apply_filters
from app.screen.selection import SELECTION_REQUEST_SCHEMA
from tests import _screen as S

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "finviz_stock_facts_aapl.html"
#: The wall clock at import time, truncated to the second. A fixed literal here
#: would expire the fixture's 24-hour TTL the day after it was written — the
#: join would then see every row as stale and silently return nothing.
NOW = datetime.now(UTC).replace(microsecond=0)

#: The three keys this pass moves from "refused" to "mapped", and their columns.
P11J: dict[str, str] = {
    "sh_short": "short_float",
    "sh_insiderown": "insider_own",
    "sh_instown": "inst_own",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def page(*rows: tuple[str, str]) -> str:
    """A Finviz-shaped page: one ``tr`` per label with the real class names."""

    cells = "\n".join(
        '<tr class="table-dark-row">\n'
        '<td class="snapshot-td2" align="left"><div class="snapshot-td-label">'
        f"{label}</div></td>"
        '<td class="snapshot-td2" align="left"><div class="snapshot-td-content">'
        f"<b>{value}</b></div></td>\n</tr>"
        for label, value in rows
    )
    return (
        '<!DOCTYPE html><html><body><div class="content">'
        '<table class="js-snapshot-table snapshot-table2">'
        f"{cells}</table></div></body></html>"
    )


def stored(symbol: str, *, now: datetime = NOW, **cells: Any) -> F.ScreenFacts:
    """One stored row: full width, only ``cells`` filled."""

    assert set(cells) <= set(F.FACTS_FIELDS), f"not a facts cell: {sorted(set(cells) - set(F.FACTS_FIELDS))}"
    return F.facts_from_values(symbol, cells, fetched_at=now, as_of=now)


def universe_row(symbol: str, **cells: Any) -> dict[str, Any]:
    """A parity row as ``build_universe`` emits it."""

    row: dict[str, Any] = {
        "symbol": symbol,
        "name": symbol,
        "exchange": "XNAS",
        "country": "United States",
        "sector": "Technology",
        "price": 100.0,
        "change_pct": 1.0,
        "volume": 1_000_000.0,
        "market_cap_usd": 1_000_000_000.0,
    }
    row.update(cells)
    return row


class FakeStore:
    """The one-method surface the join reads: ``rows()``, counted."""

    def __init__(self, *rows: F.ScreenFacts) -> None:
        self._rows = list(rows)
        self.reads = 0
        self.gets = 0

    async def rows(self) -> Sequence[F.ScreenFacts]:
        self.reads += 1
        return list(self._rows)

    async def get(self, symbol: str, **_: Any) -> F.ScreenFacts | None:  # pragma: no cover
        # Present so a per-symbol implementation would be *observable* rather
        # than invisible: the join must never call it.
        self.gets += 1
        return next((row for row in self._rows if row.symbol == symbol.upper()), None)


class BrokenStore:
    """A store whose read fails the way an unmigrated database fails."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def rows(self) -> Sequence[F.ScreenFacts]:
        raise self.error


async def open_store(db_path: Path) -> F.SqliteFactsStore:
    store = F.SqliteFactsStore(db_path)
    await store.ensure_ready()
    return store


# ---------------------------------------------------------------------------
# The Finviz page: labels, units, and nulls
# ---------------------------------------------------------------------------


def test_the_live_labels_are_read_as_percent_points_exactly_as_printed() -> None:
    """AAPL live 2026-10-25: 0.83%, 0.12%, 68.93% → 0.83, 0.12, 68.93."""

    body = FIXTURE.read_text(encoding="utf-8")

    assert parse_facts(body) == {
        "short_float": 0.83,
        "insider_own": 0.12,
        "inst_own": 68.93,
    }
    assert tuple(FACT_FIELDS) == F.FACTS_FIELDS, (
        "the adapter's label table and the store's vocabulary must be one list"
    )
    assert tuple(FACT_FIELDS) == ("short_float", "insider_own", "inst_own")


def test_the_numbers_are_not_scaled_into_fractions() -> None:
    """0.83% is stored as 0.83 — a fraction would publish 83%, or a 0.0083%."""

    values = parse_facts(FIXTURE.read_text(encoding="utf-8"))

    assert values["short_float"] == pytest.approx(0.83)
    assert values["short_float"] != pytest.approx(83.0)
    assert values["short_float"] != pytest.approx(0.0083)
    assert values["inst_own"] == pytest.approx(68.93)

    row = F.facts_from_values("AAPL", values, fetched_at=NOW, as_of=NOW)
    assert row.cells == {"short_float": 0.83, "insider_own": 0.12, "inst_own": 68.93}


def test_a_dash_na_blank_or_absent_label_is_no_value_never_zero() -> None:
    body = page(
        ("Short Float", "-"),
        ("Insider Own", "N/A"),
        ("Inst Own", ""),
        ("Market Cap", "5075.67B"),
    )
    assert parse_facts(body) == {}, "a dash is unknown, and unknown is not 0.0"

    assert parse_facts(page(("Short Float", "4.50%"))) == {"short_float": 4.5}
    assert parse_facts("") == {}
    assert parse_facts("<html><body>challenge</body></html>") == {}


def test_a_printed_zero_is_a_real_zero() -> None:
    """``0.00%`` is a measurement; only ``-``/``N/A``/absent are unknown."""

    facts = parse_facts(page(("Short Float", "0.00%"), ("Insider Own", "-")))
    assert facts == {"short_float": 0.0}
    row = F.facts_from_values("AAPL", facts, fetched_at=NOW, as_of=NOW)
    assert row.cells["short_float"] == 0.0
    assert row.cells["insider_own"] is None


def test_adjacent_labels_do_not_leak_into_the_vocabulary() -> None:
    """``Short Ratio``, ``Shs Float`` and the two transaction cells are not facts."""

    body = FIXTURE.read_text(encoding="utf-8")

    # The page really prints them (so this is a mapping decision, not a missing
    # cell), and the facts reader ignores every one of them.
    assert "2.74" in body and "14.58B" in body
    facts = parse_facts(body)
    assert set(facts) == set(F.FACTS_FIELDS)
    for label in ("short_ratio", "float_shares", "insider_trans", "inst_trans"):
        assert label not in facts


def test_the_valuation_reader_and_the_facts_reader_stay_separate() -> None:
    assert parse_metrics(FIXTURE.read_text(encoding="utf-8")) == {}, (
        "a facts page carries no valuation cell, so the valuation reader must "
        "answer zero rows rather than borrow one of these numbers"
    )
    valuation = page(("P/E", "39.86"), ("P/S", "10.87"), ("ROE", "148.75%"))
    assert parse_facts(valuation) == {}, (
        "a valuation page carries no short/ownership cell"
    )


# ---------------------------------------------------------------------------
# The row contract
# ---------------------------------------------------------------------------


def test_a_row_is_full_width_and_a_missing_cell_stays_null() -> None:
    row = stored("AAPL", short_float=0.83)

    assert row.cells == {"short_float": 0.83, "insider_own": None, "inst_own": None}
    assert row.field_count == 1 and row.complete is False
    assert row.as_dict()["fields"] == row.cells, (
        "the stored JSON is full width so a reader can tell no-value from no-key"
    )
    assert row.symbol == "AAPL"


@pytest.mark.parametrize(
    "cells",
    [
        {"short_float": True},
        {"short_float": "0.83%"},
        {"short_float": float("nan")},
        {"short_float": float("inf")},
    ],
)
def test_a_cell_that_is_not_a_finite_number_is_a_contract_error(cells: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        F.ScreenFacts(symbol="AAPL", fields=cells, fetched_at=NOW, as_of=NOW)


@pytest.mark.parametrize("symbol", ["", "   ", None])
def test_a_row_needs_a_symbol(symbol: Any) -> None:
    with pytest.raises(ValueError):
        F.ScreenFacts(symbol=symbol, fields={}, fetched_at=NOW, as_of=NOW)


def test_a_field_outside_the_vocabulary_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown facts field"):
        F.ScreenFacts(symbol="AAPL", fields={"short_ratio": 2.74})
    with pytest.raises(ValueError, match="unknown facts field"):
        F.facts_from_values("AAPL", {"short_ratio": 2.74})


def test_the_symbol_key_is_canonicalised() -> None:
    row = F.facts_from_values(" aapl ", {}, fetched_at=NOW, as_of=NOW)
    assert row.symbol == "AAPL", "the store keys one symbol one way"


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


async def test_the_store_round_trips_one_row_per_symbol(tmp_path: Path) -> None:
    store = await open_store(tmp_path / "facts.db")
    try:
        assert await store.upsert(stored("AAPL", short_float=0.83, insider_own=0.12)) is True
        assert await store.upsert(stored("MSFT", short_float=5.2, inst_own=71.0)) is True
        assert await store.upsert(stored("AAPL", short_float=0.84)) is True
        assert await store.count() == 2, "one row per symbol, not a history"

        aapl = await store.get("aapl", now=NOW)
        assert aapl is not None
        assert aapl.symbol == "AAPL"
        assert aapl.cells == {"short_float": 0.84, "insider_own": None, "inst_own": None}
        assert [row.symbol for row in await store.rows()] == ["AAPL", "MSFT"]
    finally:
        await store.aclose()


async def test_the_stored_json_is_the_full_vocabulary(tmp_path: Path) -> None:
    store = await open_store(tmp_path / "facts.db")
    db = tmp_path / "facts.db"
    try:
        await store.upsert(stored("AAPL", short_float=0.83))
    finally:
        await store.aclose()

    with closing(sqlite3.connect(db)) as connection:
        cells, count = connection.execute(
            "SELECT fields, field_count FROM screen_facts WHERE symbol = 'AAPL'"
        ).fetchone()
    assert json.loads(cells) == {
        "short_float": 0.83,
        "insider_own": None,
        "inst_own": None,
    }
    assert count == 1


async def test_an_older_fetch_cannot_wind_the_row_back(tmp_path: Path) -> None:
    store = await open_store(tmp_path / "facts.db")
    try:
        newer = stored("AAPL", short_float=1.5, now=NOW + timedelta(hours=1))
        older = stored("AAPL", short_float=9.9, now=NOW)
        assert await store.upsert(newer) is True
        assert await store.upsert(older) is False, "fetched_at is monotonic per symbol"

        row = await store.get("AAPL", now=NOW + timedelta(hours=2))
        assert row is not None and row.cells["short_float"] == 1.5
    finally:
        await store.aclose()


async def test_an_expired_row_is_stored_but_not_read_as_evidence(tmp_path: Path) -> None:
    store = await open_store(tmp_path / "facts.db")
    try:
        await store.upsert(stored("AAPL", short_float=0.83))
        later = NOW + timedelta(hours=25)
        assert await store.get("AAPL", now=later) is None
        kept = await store.get("AAPL", now=later, include_expired=True)
        assert kept is not None and kept.is_stale(later)
        assert [row.symbol for row in await store.rows()] == ["AAPL"]
        assert await F.load_facts(store, ["AAPL"], now=later) == {}
    finally:
        await store.aclose()


async def test_purge_expired_deletes_only_the_past_window(tmp_path: Path) -> None:
    store = await open_store(tmp_path / "facts.db")
    try:
        await store.upsert(stored("OLD", short_float=1.0, now=NOW - timedelta(days=2)))
        await store.upsert(stored("NEW", short_float=2.0))
        assert await store.purge_expired(now=NOW) == 1
        assert [row.symbol for row in await store.rows()] == ["NEW"]
    finally:
        await store.aclose()


async def test_a_database_without_0005_is_refused_with_the_command(tmp_path: Path) -> None:
    stub = tmp_path / "stub.db"
    with closing(sqlite3.connect(stub)) as connection:
        connection.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY)")
        connection.commit()

    store = F.SqliteFactsStore(stub)
    try:
        with pytest.raises(F.FactsSchemaError, match="upgrade head"):
            await store.ensure_ready()
    finally:
        await store.aclose()


# ---------------------------------------------------------------------------
# Migration and backward compatibility
# ---------------------------------------------------------------------------


async def test_migration_0005_is_the_head_and_creates_the_table(tmp_path: Path) -> None:
    db = tmp_path / "fresh.db"
    await ensure_schema_async(db)

    with closing(sqlite3.connect(db)) as connection:
        head = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        columns = {
            row[1]: (row[2], row[3])
            for row in connection.execute("PRAGMA table_info(screen_facts)").fetchall()
        }
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(screen_facts)").fetchall()
        }

    assert head == ("0005",)
    assert set(columns) == {
        "symbol",
        "source",
        "as_of",
        "fetched_at",
        "expires_at",
        "fields",
        "field_count",
        "updated_at",
    }
    assert columns["symbol"][1] == 1, "symbol is the primary key"
    assert columns["fields"][1] == 1, "fields is NOT NULL, with a '{}' default"
    assert "ix_screen_facts_expiry" in indexes


def _instrument_row(db: Path, symbol: str = "AAPL") -> None:
    """Insert one pre-0005 fundamentals row, the way ``0003``/``0004`` stored it."""

    with closing(sqlite3.connect(db)) as connection:
        connection.execute(
            """
            INSERT INTO fundamentals_snapshots (
                symbol, instrument_id, fields, field_sources, source, as_of,
                fetched_at, expires_at, field_count, complete, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                f"XNAS:{symbol}",
                json.dumps({"market_cap": 3.4e12, "pe_ttm": 28.5}),
                json.dumps({"market_cap": "yahoo", "pe_ttm": "yahoo"}),
                "yahoo",
                "2026-10-24T12:00:00.000000+00:00",
                "2026-10-24T12:00:00.000000+00:00",
                "2026-10-25T12:00:00.000000+00:00",
                2,
                0,
                "2026-10-24T12:00:00.000000+00:00",
            ),
        )
        connection.commit()


def _has_table(db: Path, name: str) -> bool:
    with closing(sqlite3.connect(db)) as connection:
        return (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
            is not None
        )


def test_0005_is_additive_and_a_legacy_fundamentals_row_survives(tmp_path: Path) -> None:
    db = tmp_path / "at0004.db"
    command.upgrade(alembic_config(db), "0004")
    _instrument_row(db)
    assert not _has_table(db, "screen_facts"), "the table arrives in 0005"

    command.upgrade(alembic_config(db), "0005")

    with closing(sqlite3.connect(db)) as connection:
        fundamentals = connection.execute(
            "SELECT symbol, fields, field_sources, source FROM fundamentals_snapshots"
        ).fetchall()
        facts = connection.execute("SELECT COUNT(*) FROM screen_facts").fetchone()[0]
        head = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert head == ("0005",)
    assert fundamentals == [
        (
            "AAPL",
            '{"market_cap": 3400000000000.0, "pe_ttm": 28.5}',
            '{"market_cap": "yahoo", "pe_ttm": "yahoo"}',
            "yahoo",
        )
    ], "0005 must not rewrite a single fundamentals cell"
    assert facts == 0, "the new table starts empty: no facts are invented by a migration"


def test_downgrade_to_0004_drops_only_the_facts_table(tmp_path: Path) -> None:
    db = tmp_path / "downgrade.db"
    command.upgrade(alembic_config(db), "0005")
    _instrument_row(db)
    with closing(sqlite3.connect(db)) as connection:
        connection.execute(
            "INSERT INTO screen_facts (symbol, source, as_of, fetched_at, expires_at, fields, field_count, updated_at)"
            " VALUES ('AAPL', 'finviz', '2026-10-25T12:00:00.000000+00:00',"
            " '2026-10-25T12:00:00.000000+00:00', '2026-10-26T12:00:00.000000+00:00',"
            " '{\"short_float\": 0.83, \"insider_own\": null, \"inst_own\": null}', 1,"
            " '2026-10-25T12:00:00.000000+00:00')"
        )
        connection.commit()

    command.downgrade(alembic_config(db), "0004")

    with closing(sqlite3.connect(db)) as connection:
        head = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        fundamentals = connection.execute(
            "SELECT COUNT(*) FROM fundamentals_snapshots"
        ).fetchone()[0]
    assert head == ("0004",)
    assert not _has_table(db, "screen_facts")
    assert fundamentals == 1, "a rollback loses facts, never fundamentals"


# ---------------------------------------------------------------------------
# The join
# ---------------------------------------------------------------------------


async def test_the_join_copies_the_three_cells_by_symbol_and_leaves_the_rest_null() -> None:
    rows = [universe_row("AAPL"), universe_row("MSFT"), universe_row("TSM")]
    store = FakeStore(
        stored("AAPL", short_float=0.83, insider_own=0.12, inst_own=68.93),
        stored("MSFT", short_float=5.2),
    )

    enriched = await F.enrich_rows(rows, store)

    aapl, msft, tsm = enriched
    assert (aapl["short_float"], aapl["insider_own"], aapl["inst_own"]) == (
        0.83,
        0.12,
        68.93,
    )
    assert (msft["short_float"], msft["insider_own"], msft["inst_own"]) == (
        5.2,
        None,
        None,
    ), "a cell the store did not fill stays None, never 0"
    assert (tsm["short_float"], tsm["insider_own"], tsm["inst_own"]) == (
        None,
        None,
        None,
    ), "a symbol the store does not hold gains no value"
    for row in enriched:
        assert row["price"] == 100.0, "the join must not disturb the cells it does not own"


async def test_the_join_reads_the_store_once_for_the_whole_universe() -> None:
    rows = [universe_row(symbol) for symbol in ("AAPL", "MSFT", "NVDA", "AMZN")]
    store = FakeStore(*[stored(row["symbol"], short_float=1.0) for row in rows])

    await F.enrich_rows(rows, store)

    assert store.reads == 1, "one batch read per selection, never one query per symbol"
    assert store.gets == 0, "a per-symbol get() loop is the N+1 this module exists to avoid"


def test_the_join_never_mutates_the_rows_it_reads() -> None:
    rows = [universe_row("AAPL")]
    before = json.dumps(rows, sort_keys=True)
    facts = {"AAPL": stored("AAPL", short_float=0.83, insider_own=0.12, inst_own=68.93)}

    enriched = F.apply_facts(rows, facts)

    assert json.dumps(rows, sort_keys=True) == before, (
        "the caller's universe (and the cached scan page behind it) must keep its cells"
    )
    assert enriched[0] is not rows[0], "a joined row is a copy, not the input object"
    assert enriched[0]["short_float"] == 0.83


def test_a_store_null_never_erases_a_value_the_scan_carried() -> None:
    # Not reachable from today's scan (short_float is null there), but the rule
    # has to hold if the provider ever fills the column: the store is an
    # enrichment, and null means "no observation", not "no value".
    rows = [universe_row("AAPL", short_float=4.4)]
    enriched = F.apply_facts(rows, {"AAPL": stored("AAPL", insider_own=0.12)})
    assert enriched[0]["short_float"] == 4.4
    assert enriched[0]["insider_own"] == 0.12


def test_a_stale_row_is_not_evidence() -> None:
    stale = F.ScreenFacts(
        symbol="AAPL",
        fields={"short_float": 0.83},
        source="finviz",
        as_of=NOW,
        fetched_at=NOW,
        expires_at=NOW - timedelta(seconds=1),
    )
    rows = [universe_row("AAPL")]

    enriched = F.apply_facts(rows, F.fresh_facts([stale], now=NOW))

    assert enriched[0]["short_float"] is None, (
        "a row past expires_at must not be quoted as today's short float"
    )


def test_a_row_without_a_symbol_is_skipped_not_keyed_on_the_empty_string() -> None:
    rows = [{"symbol": "   ", "price": 1.0}, {"price": 2.0}]
    enriched = F.apply_facts(rows, {"": stored("SPY", short_float=1.0)})
    assert [row["short_float"] for row in enriched] == [None, None]


async def test_a_missing_store_is_no_enrichment_not_an_error() -> None:
    enriched = await F.enrich_rows([universe_row("AAPL")], None)
    assert enriched[0]["short_float"] is None
    assert enriched[0]["symbol"] == "AAPL"


async def test_a_store_that_raises_schema_error_degrades() -> None:
    enriched = await F.enrich_rows(
        [universe_row("AAPL")],
        BrokenStore(F.FactsSchemaError("no screen_facts table")),
    )
    assert enriched[0]["short_float"] is None


async def test_an_unmigrated_database_is_no_enrichment_not_an_error(tmp_path: Path) -> None:
    stub = tmp_path / "stub.db"
    with closing(sqlite3.connect(stub)) as connection:
        connection.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY)")
        connection.commit()

    store = F.SqliteFactsStore(stub)
    try:
        enriched = await F.enrich_rows([universe_row("AAPL")], store)
    finally:
        await store.aclose()

    assert enriched[0]["short_float"] is None


async def test_an_empty_universe_costs_no_read() -> None:
    store = FakeStore(stored("AAPL", short_float=1.0))
    assert await F.enrich_rows([], store) == []
    assert store.reads == 0


# ---------------------------------------------------------------------------
# Filtering: the percent cells behave like Finviz's buckets
# ---------------------------------------------------------------------------


async def test_a_filled_cell_matches_and_a_null_cell_does_not() -> None:
    rows = [universe_row("AAPL"), universe_row("MSFT"), universe_row("TSM")]
    store = FakeStore(
        stored("AAPL", short_float=0.83, insider_own=0.12, inst_own=68.93),
        stored("MSFT", short_float=5.2, insider_own=None, inst_own=71.0),
    )
    enriched = await F.enrich_rows(rows, store)

    cheap_short = apply_filters(enriched, [{"field": "short_float", "op": "lt", "value": 1}])
    insider = apply_filters(enriched, [{"field": "insider_own", "op": "lt", "value": 1}])
    institutions = apply_filters(enriched, [{"field": "inst_own", "op": "gte", "value": 70}])

    assert [row["symbol"] for row in cheap_short] == ["AAPL"]
    assert [row["symbol"] for row in insider] == ["AAPL"], (
        "MSFT has a null insider ownership, so it must not match an ownership filter"
    )
    assert [row["symbol"] for row in institutions] == ["MSFT"]


async def test_the_percent_threshold_is_read_as_percent_not_as_a_fraction() -> None:
    """``sh_short lt 1`` means below 1%, so 0.83% matches and a 5.2% row does not."""

    rows = [universe_row("AAPL"), universe_row("MSFT")]
    enriched = await F.enrich_rows(
        rows,
        FakeStore(stored("AAPL", short_float=0.83), stored("MSFT", short_float=5.2)),
    )
    compiled = {"field": binding("sh_short").field, "op": "lt", "value": 1.0}
    assert [row["symbol"] for row in apply_filters(enriched, [compiled])] == ["AAPL"]


def test_the_facts_keys_resolve_to_the_joined_columns() -> None:
    assert dict(F.FACTS_FILTER_KEYS) == P11J
    for key, field in P11J.items():
        found = binding(key)
        assert found.is_available is True
        assert found.field == field
        assert FIELDS[field] == "number"
        assert reason_for(key) is None
        assert ROW_FIELDS[key] == field
        assert FILTER_CATALOG[key].field == field
        assert field in F.FACTS_ROW_FIELDS, (
            "the column must be one the join really writes onto the row"
        )


# ---------------------------------------------------------------------------
# Disposition: 47 single-column keys + 3 option-driven, 32 refused
# ---------------------------------------------------------------------------


def test_the_catalogue_moved_to_fifty_available_and_thirty_two_refused() -> None:
    assert len(mapped_keys()) == 51
    assert len(unavailable_keys()) == 31
    assert len(mapped_keys()) + len(unavailable_keys()) == 82
    assert set(mapped_keys()) == set(ROW_FIELDS) | set(CHOICE_FIELDS) | set(BUCKET_KEYS)
    assert not set(mapped_keys()) & set(unavailable_keys())
    for key in P11J:
        assert key not in unavailable_keys()


def test_the_schema_artifact_is_the_served_document_and_counts_the_facts_keys() -> None:
    artifact = ROOT / "schemas" / "stock_selection_request_v1.json"
    assert json.loads(artifact.read_text(encoding="utf-8")) == SELECTION_REQUEST_SCHEMA

    extension = SELECTION_REQUEST_SCHEMA["x-filter-keys"]
    published: dict[str, Any] = dict(extension)  # type: ignore[arg-type]
    mapped = {key: entry for key, entry in published.items() if entry["field"] is not None}
    chosen = {key: entry for key, entry in published.items() if "choice" in entry}
    bucketed = {key: entry for key, entry in published.items() if entry.get("bucket") is True}
    refused = {
        key: entry
        for key, entry in published.items()
        if entry["field"] is None and "choice" not in entry and not entry.get("bucket")
    }
    assert len(mapped) == 47 and len(chosen) == 3 and len(refused) == 31
    assert set(bucketed) == set(BUCKET_KEYS), "the flag is the buckets table"
    assert {"idx"} <= set(bucketed), "the one bucket-only key publishes no column"
    assert set(mapped) | set(chosen) | set(bucketed) == set(mapped_keys())
    assert set(refused) == set(unavailable_keys())
    for key, field in P11J.items():
        assert mapped[key]["field"] == field
        assert mapped[key]["kind"] == "number"
        assert "reason" not in mapped[key]

    predicate = SELECTION_REQUEST_SCHEMA["$defs"]["filter"]["properties"]  # type: ignore[index]
    description = predicate["key"]["description"]
    assert "The 47 that resolve to one row column" in description
    assert "the 31 that are refused" in description
    assert "(idx) that filters through a named bucket" in description


# ---------------------------------------------------------------------------
# The API: one fake store, the real route
# ---------------------------------------------------------------------------


def api_criteria(key: str, op: str, value: Any) -> dict[str, Any]:
    return {
        "mode": "criteria",
        "region": "us",
        "criteria": {"filters": [{"key": key, "op": op, "value": value}], "limit": 20},
    }


async def test_the_route_filters_on_short_float_joined_from_the_facts_store(
    tmp_path: Path, monkeypatch: Any
) -> None:
    store = FakeStore(
        stored("AAPL", short_float=0.83),
        stored("MSFT", short_float=5.2),
    )
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        harness.runtime.facts_store = store
        response = await harness.client.post(
            "/screens/select", json=api_criteria("sh_short", "lt", 1)
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1, (
        "only AAPL has a short float below 1%; every other row is null and "
        f"matches nothing (total={body['total']})"
    )
    assert [row["symbol"] for row in body["rows"]] == ["AAPL"]
    assert body["filters"][0]["field"] == "short_float"
    assert store.reads == 1


async def test_the_route_filters_on_insider_and_institutional_ownership(
    tmp_path: Path, monkeypatch: Any
) -> None:
    store = FakeStore(
        stored("AAPL", insider_own=0.12, inst_own=68.93),
        stored("MSFT", insider_own=None, inst_own=71.0),
    )
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        harness.runtime.facts_store = store
        insider = await harness.client.post(
            "/screens/select", json=api_criteria("sh_insiderown", "lt", 1)
        )
        institutions = await harness.client.post(
            "/screens/select", json=api_criteria("sh_instown", "gte", 70)
        )

    assert insider.status_code == 200, insider.text
    assert [row["symbol"] for row in insider.json()["rows"]] == ["AAPL"]
    assert insider.json()["total"] == 1
    assert institutions.status_code == 200, institutions.text
    assert [row["symbol"] for row in institutions.json()["rows"]] == ["MSFT"]
    assert institutions.json()["total"] == 1
    assert store.reads == 2, "one batch read per request, not one per row"


async def test_the_route_answers_without_a_store(tmp_path: Path, monkeypatch: Any) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        harness.runtime.facts_store = None
        response = await harness.client.post(
            "/screens/select", json=api_criteria("sh_short", "lt", 1)
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 0, "unknown is not a match: no store means no short float"
    assert body["rows"] == []


async def test_the_route_answers_with_the_real_empty_store(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """No fake: the runtime's own store over the (empty) migrated test database."""

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select", json=api_criteria("sh_insiderown", "gte", 0)
        )

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0
