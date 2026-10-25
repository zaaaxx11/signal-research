"""P11F-A RED — per-cell provenance: 22 cells, 22 sources, one honest merge.

``0003`` gave a snapshot one ``source`` per row. That says who won the symbol and
cannot say who supplied a number, which is the difference between a table and a
Finviz-beating one: two vendors can disagree about a P/E with nothing in the row
to tell them apart, and a merged row (the point of a second source) holds cells
from several places at once.

What this suite pins:

* every filled cell carries the source of **its own observation**
  (``observation["source"]``), and ``field_sources`` is full width — 22 keys,
  ``null`` where a cell is empty or its origin is unknown;
* the map is *exact*: the keys with a source are the filled cells, never a subset
  and never a record for a value the row does not carry;
* a snapshot built from one source attributes its cells to that source, while
  ``multi`` — a count, not a source — never labels a single cell;
* :func:`merge_snapshots` fills empty cells from lower-priority sources and never
  overwrites a filled one, takes ``as_of`` from the newest **contributing**
  source, and labels the row ``multi`` only when more than one source contributed;
* the store round-trips the map, a pre-0004 row (``field_sources IS NULL``) still
  reads with its row-level source, and a write against a ``0003`` database says
  what to run instead of dying on ``no such column``;
* ``0004_field_sources`` is additive: upgrading keeps every legacy row, and the
  coverage report counts cells per source per field — with ``unattributed``
  cells counted, never credited to a guess;
* the CLI report prints those counts.

No network: the fixtures are dicts and a temp SQLite file.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

import pytest

from app.fundamentals.provenance import (
    DEFAULT_PRIORITY,
    aggregate_source,
    by_priority,
    cell_sources,
    field_source_counts,
    merge_snapshots,
    priority_index,
    unattributed_fields,
)
from app.fundamentals.scan import SCAN_SOURCE, mapped_fields, snapshot_from_scan_row
from app.fundamentals.snapshot import (
    FUNDAMENTAL_FIELDS,
    MULTI_SOURCE,
    TTL_S,
    FundamentalsSchemaError,
    FundamentalsSnapshot,
    SqliteFundamentalsStore,
    apply_migrations,
    canonical_symbol,
    coverage_report,
    instrument_for,
    iso,
    snapshot_from_observations,
)
from tests.test_fundamentals_snapshot import NOW, FakeLadder, full_fields

FTECH = datetime(2026, 10, 24, 0, 0, tzinfo=UTC)

CLI_PATH = Path(__file__).resolve().parents[1] / "scripts" / "fundamentals-snapshot.py"


def _load_cli() -> Any:
    """Import ``scripts/fundamentals-snapshot.py`` without making it a package."""

    spec = importlib.util.spec_from_file_location("fundamentals_snapshot_cli_p11f", CLI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CLI = _load_cli()


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


def observe(
    field: str,
    value: Any,
    *,
    source: str | None = "yahoo",
    as_of: datetime = FTECH,
    symbol: str = "AAPL",
) -> dict[str, Any]:
    """One observation document in the shape the production adapters emit."""

    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    return {
        "instrument_id": instrument_for(symbol).instrument_id,
        "field": field,
        "value_num": float(value) if numeric else None,
        "value_text": value if isinstance(value, str) else None,
        "unit": "ratio",
        "source": source,
        "source_id": None,
        "as_of": as_of.isoformat(),
        "fetched_at": as_of.isoformat(),
        "confidence": 1.0,
        "verdict": "ok",
        "trust": 3,
        "raw_sha256": None,
        "job_id": None,
    }


def make_snapshot(
    symbol: str = "AAPL",
    *,
    source: str | None = "yahoo",
    fields: Mapping[str, Any] | None = None,
    field_sources: Mapping[str, Any] | None = None,
    fetched_at: datetime = NOW,
    as_of: datetime | None = None,
    ttl_s: int = TTL_S,
) -> FundamentalsSnapshot:
    """A snapshot with the clocks implied by ``fetched_at``."""

    return FundamentalsSnapshot(
        symbol=symbol,
        instrument_id=instrument_for(symbol).instrument_id,
        fields=dict(fields or {}),
        source=source,
        field_sources=dict(field_sources or {}),
        as_of=as_of or fetched_at,
        fetched_at=fetched_at,
        expires_at=fetched_at + timedelta(seconds=ttl_s),
    )


async def store_at(path: Path) -> SqliteFundamentalsStore:
    return SqliteFundamentalsStore(path, clock=lambda: NOW)


def legacy_row_insert(
    db: Path,
    *,
    symbol: str = "AAPL",
    fields: Mapping[str, Any],
    source: str | None,
    moment: datetime = NOW,
) -> None:
    """Write a row the way the ``0003`` schema did: no ``field_sources`` column.

    This is the backcompat case in its real shape — bytes written by the previous
    release, read by this one.
    """

    payload = {name: fields.get(name) for name in FUNDAMENTAL_FIELDS}
    filled = sum(1 for name in FUNDAMENTAL_FIELDS if payload[name] is not None)
    with closing(sqlite3.connect(db)) as connection:
        connection.execute(
            """
            INSERT INTO fundamentals_snapshots (
                symbol, instrument_id, fields, source, as_of, fetched_at,
                expires_at, field_count, complete, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                canonical_symbol(symbol),
                instrument_for(symbol).instrument_id,
                json.dumps(payload, sort_keys=True),
                source,
                iso(moment),
                iso(moment),
                iso(moment + timedelta(seconds=TTL_S)),
                filled,
                1 if filled == len(FUNDAMENTAL_FIELDS) else 0,
                iso(moment),
            ),
        )
        connection.commit()


# ---------------------------------------------------------------------------
# One observation, one source
# ---------------------------------------------------------------------------


def test_every_filled_cell_records_the_source_of_its_own_observation() -> None:
    """A walk that answered from two rungs says so per cell, not per row."""

    snapshot = snapshot_from_observations(
        [
            observe("market_cap", 3.4e12, source="yahoo"),
            observe("pe_ttm", 28.5, source="yahoo"),
            observe("revenue_ttm", 4.16e11, source="finviz"),
            observe("ebitda_ttm", None, source="finviz"),  # a null cell serves nothing
        ],
        symbol="aapl",
        instrument_id="XNAS:AAPL",
        source="yahoo",
        fetched_at=NOW,
    )

    assert snapshot.field_sources["market_cap"] == "yahoo"
    assert snapshot.field_sources["revenue_ttm"] == "finviz"
    assert snapshot.field_sources["ebitda_ttm"] is None
    # The map is full width: 22 keys, whichever cells were served.
    assert set(snapshot.field_sources) == set(FUNDAMENTAL_FIELDS)
    assert len(snapshot.field_sources) == 22


def test_provenance_is_exact_about_the_filled_cells() -> None:
    """Sources are an equality with the filled cells — never a subset."""

    snapshot = snapshot_from_observations(
        [
            observe("market_cap", 3.4e12),
            observe("pe_ttm", 28.5, source="finviz"),
            observe("eps_ttm", None),
            {"field": "not_a_canonical_column", "value_num": 1.0, "source": "yahoo"},
        ],
        symbol="AAPL",
        instrument_id="XNAS:AAPL",
        source="yahoo",
        fetched_at=NOW,
    )

    filled = {name for name in FUNDAMENTAL_FIELDS if snapshot.fields.get(name) is not None}
    recorded = {name for name, source in snapshot.field_sources.items() if source}
    assert recorded == filled == {"market_cap", "pe_ttm"}
    assert snapshot.field_sources["pe_ttm"] == "finviz"
    assert snapshot.fields.get("not_a_canonical_column") is None


def test_an_observation_without_a_source_falls_back_to_the_row_source() -> None:
    snapshot = snapshot_from_observations(
        [observe("market_cap", 3.4e12, source=None)],
        symbol="AAPL",
        instrument_id="XNAS:AAPL",
        source="yahoo",
        fetched_at=NOW,
    )

    assert snapshot.field_sources["market_cap"] == "yahoo"


def test_a_cell_with_no_source_at_all_stays_unattributed() -> None:
    """No record is a gap the report counts — not a label this module invents."""

    snapshot = snapshot_from_observations(
        [observe("market_cap", 3.4e12, source="")],
        symbol="AAPL",
        instrument_id="XNAS:AAPL",
        source=None,
        fetched_at=NOW,
    )

    assert snapshot.field_sources["market_cap"] is None
    assert unattributed_fields(snapshot) == ("market_cap",)


def test_the_row_source_labels_every_cell_of_a_single_source_row() -> None:
    """22 cells from one rung are 22 cells from that rung: same fact, finer grain."""

    snapshot = make_snapshot(fields=full_fields())

    assert set(snapshot.field_sources.values()) == {"yahoo"}
    assert cell_sources(snapshot) == {name: "yahoo" for name in FUNDAMENTAL_FIELDS}


def test_a_multi_label_never_stands_in_for_a_cell_source() -> None:
    """``multi`` says "several"; crediting every cell to it would say nothing."""

    snapshot = make_snapshot(source=MULTI_SOURCE, fields=full_fields())

    assert set(snapshot.field_sources.values()) == {None}
    assert len(unattributed_fields(snapshot)) == 22


def test_a_record_for_an_empty_cell_is_dropped() -> None:
    snapshot = make_snapshot(
        fields={"market_cap": 3.4e12},
        field_sources={"market_cap": "yahoo", "pe_ttm": "finviz"},
    )

    assert snapshot.field_sources["market_cap"] == "yahoo"
    assert snapshot.field_sources["pe_ttm"] is None, "no cell, so no provenance record"


def test_provenance_for_a_field_outside_the_vocabulary_is_a_caller_error() -> None:
    with pytest.raises(ValueError) as error:
        make_snapshot(fields={"market_cap": 3.4e12}, field_sources={"quote_as_of": "yahoo"})

    assert "quote_as_of" in str(error.value)


def test_the_row_document_carries_the_full_width_map() -> None:
    snapshot = make_snapshot(fields={"market_cap": 3.4e12})

    document = snapshot.as_dict()
    assert set(document["field_sources"]) == set(FUNDAMENTAL_FIELDS)
    assert document["field_sources"]["market_cap"] == "yahoo"
    assert document["field_sources"]["pe_ttm"] is None


# ---------------------------------------------------------------------------
# Merge: fill holes, never overwrite, keep the map
# ---------------------------------------------------------------------------


def test_merge_fills_empty_cells_from_a_lower_priority_source() -> None:
    yahoo = make_snapshot(
        fields={"market_cap": 3.4e12, "pe_ttm": 28.5},
        fetched_at=NOW - timedelta(hours=2),
    )
    finviz = make_snapshot(
        source="finviz",
        fields={"pe_ttm": 99.9, "ps": 7.2},
        fetched_at=NOW,
    )

    merged = merge_snapshots([finviz, yahoo])

    assert merged is not None
    assert merged.fields["market_cap"] == 3.4e12
    assert merged.fields["pe_ttm"] == 28.5, "a lower-priority source may not overwrite"
    assert merged.fields["ps"] == 7.2, "but it may fill a cell nobody else served"
    assert merged.field_sources["market_cap"] == "yahoo"
    assert merged.field_sources["pe_ttm"] == "yahoo"
    assert merged.field_sources["ps"] == "finviz"
    assert merged.source == MULTI_SOURCE


def test_merge_priority_is_a_parameter_not_a_guess() -> None:
    """The same two rows swap winners when the caller ranks them differently."""

    sec = make_snapshot(source="sec_edgar", fields={"revenue_ttm": 4.16e11})
    yahoo = make_snapshot(source="yahoo", fields={"revenue_ttm": 4.2e11})

    default = merge_snapshots([yahoo, sec])
    assert default is not None and default.fields["revenue_ttm"] == 4.16e11
    assert default.field_sources["revenue_ttm"] == "sec_edgar"

    flipped = merge_snapshots([yahoo, sec], priority=("yahoo", "sec_edgar"))
    assert flipped is not None and flipped.fields["revenue_ttm"] == 4.2e11
    assert flipped.field_sources["revenue_ttm"] == "yahoo"


def test_the_default_priority_is_the_trust_ladder() -> None:
    assert DEFAULT_PRIORITY == ("sec_edgar", "yahoo", "finviz", "tradingview")
    assert priority_index("sec_edgar") == 0
    assert priority_index("tradingview") == 3
    assert priority_index("mystery") == len(DEFAULT_PRIORITY)


def test_a_cell_nobody_served_stays_null() -> None:
    merged = merge_snapshots(
        [
            make_snapshot(fields={"market_cap": 3.4e12}),
            make_snapshot(source="finviz", fields={"pe_ttm": 28.5}),
            make_snapshot(source="tradingview", fields={"ps": None}),
        ]
    )

    assert merged is not None
    assert merged.field_count == 2
    assert merged.fields["ps"] is None
    assert merged.missing_fields == tuple(
        name for name in FUNDAMENTAL_FIELDS if name not in {"market_cap", "pe_ttm"}
    )
    assert merged.field_sources["ps"] is None


def test_merge_takes_the_newest_contributing_as_of() -> None:
    old = make_snapshot(
        fields={"market_cap": 3.4e12},
        fetched_at=NOW - timedelta(days=1),
        as_of=FTECH,
    )
    newer = make_snapshot(
        source="finviz",
        fields={"ps": 7.2},
        fetched_at=NOW,
        as_of=FTECH + timedelta(hours=6),
    )

    merged = merge_snapshots([old, newer])

    assert merged is not None
    assert merged.as_of == FTECH + timedelta(hours=6), "the data clock is the newest one"
    assert merged.fetched_at == NOW
    assert merged.expires_at == NOW + timedelta(seconds=TTL_S)


def test_a_source_that_contributed_nothing_does_not_date_the_row() -> None:
    contributor = make_snapshot(fields={"market_cap": 3.4e12}, fetched_at=NOW, as_of=FTECH)
    empty = make_snapshot(
        source="finviz", fields={}, fetched_at=NOW + timedelta(hours=1), as_of=NOW
    )

    merged = merge_snapshots([contributor, empty])

    assert merged is not None
    assert merged.as_of == FTECH
    assert merged.fetched_at == NOW
    assert merged.source == "yahoo", "one source supplied every cell"


def test_merge_labels_a_single_source_row_with_that_source() -> None:
    merged = merge_snapshots([make_snapshot(fields={"market_cap": 3.4e12})])

    assert merged is not None
    assert merged.source == "yahoo"
    assert merged.field_sources["market_cap"] == "yahoo"


def test_merge_of_nothing_is_none_not_a_row_of_nulls() -> None:
    assert merge_snapshots([]) is None
    assert merge_snapshots([make_snapshot(fields={})]) is None


def test_merge_refuses_two_symbols() -> None:
    with pytest.raises(ValueError) as error:
        merge_snapshots(
            [
                make_snapshot("AAPL", fields={"market_cap": 1.0}),
                make_snapshot("MSFT", source="finviz", fields={"ps": 2.0}),
            ]
        )

    assert "one symbol" in str(error.value)


def test_merge_requires_a_positive_ttl() -> None:
    with pytest.raises(ValueError):
        merge_snapshots([make_snapshot(fields={"market_cap": 1.0})], ttl_s=0)


def test_an_unnamed_source_sorts_after_the_named_ones() -> None:
    named = make_snapshot(source="tradingview", fields={"pe_ttm": 1.0})
    unknown = make_snapshot(source="mystery", fields={"pe_ttm": 2.0})

    assert [snapshot.source for snapshot in by_priority([unknown, named])] == [
        "tradingview",
        "mystery",
    ]
    merged = merge_snapshots([unknown, named])
    assert merged is not None and merged.fields["pe_ttm"] == 1.0


def test_merge_keeps_the_instrument_of_the_highest_priority_source() -> None:
    merged = merge_snapshots(
        [
            make_snapshot(source="finviz", fields={"ps": 7.2}),
            make_snapshot(source="yahoo", fields={"market_cap": 3.4e12}),
        ]
    )

    assert merged is not None
    assert merged.instrument_id == instrument_for("AAPL").instrument_id


def test_aggregate_source_says_multi_only_for_several_sources() -> None:
    assert aggregate_source([]) is None
    assert aggregate_source([None, " "]) is None
    assert aggregate_source(["yahoo", "yahoo", None]) == "yahoo"
    assert aggregate_source(["yahoo", "finviz"]) == MULTI_SOURCE


# ---------------------------------------------------------------------------
# The store: round trip, backcompat, coverage per source
# ---------------------------------------------------------------------------


async def test_upsert_and_read_round_trip_the_field_sources(tmp_path: Path) -> None:
    store = await store_at(tmp_path / "provenance.db")
    snapshot = make_snapshot(
        fields={"market_cap": 3.4e12, "pe_ttm": 28.5, "ps": 7.2},
        field_sources={"market_cap": "yahoo", "pe_ttm": "yahoo", "ps": "finviz"},
        source=MULTI_SOURCE,
    )

    assert await store.upsert(snapshot) is True
    stored = await store.get("AAPL", now=NOW)

    assert stored is not None
    assert dict(stored.field_sources) == dict(snapshot.field_sources)
    assert stored.field_sources["ps"] == "finviz"
    with closing(sqlite3.connect(tmp_path / "provenance.db")) as connection:
        raw = connection.execute(
            "SELECT field_sources FROM fundamentals_snapshots WHERE symbol = 'AAPL'"
        ).fetchone()[0]
    document = json.loads(raw)
    assert set(document) == set(FUNDAMENTAL_FIELDS), "stored full width, like fields"
    assert document["ps"] == "finviz"
    assert document["revenue_ttm"] is None
    await store.aclose()


async def test_a_newer_merge_replaces_the_provenance_it_replaces_the_cells_with(
    tmp_path: Path,
) -> None:
    store = await store_at(tmp_path / "replace.db")
    await store.upsert(make_snapshot(fields={"market_cap": 1.0}))

    merged = make_snapshot(
        fields={"market_cap": 2.0, "ps": 7.2},
        field_sources={"market_cap": "finviz", "ps": "finviz"},
        source="finviz",
        fetched_at=NOW + timedelta(minutes=5),
    )
    assert await store.upsert(merged) is True
    stored = await store.get("AAPL", now=NOW)

    assert stored is not None
    assert stored.fields["market_cap"] == 2.0
    assert set(source for source in stored.field_sources.values() if source) == {"finviz"}
    await store.aclose()


async def test_a_row_written_before_0004_reads_with_its_row_source(tmp_path: Path) -> None:
    """``field_sources IS NULL`` is a legacy row, not a corrupt one."""

    db = tmp_path / "legacy.db"
    store = await store_at(db)
    await store.ensure_ready()  # bootstraps at head, so the column exists
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("DELETE FROM fundamentals_snapshots")
        connection.commit()
    # Simulate the old bytes: the column exists but the row never set it.
    with closing(sqlite3.connect(db)) as connection:
        connection.execute(
            """
            INSERT INTO fundamentals_snapshots (
                symbol, instrument_id, fields, field_sources, source, as_of,
                fetched_at, expires_at, field_count, complete, updated_at
            ) VALUES ('MSFT', 'XNAS:MSFT', ?, NULL, 'yahoo', ?, ?, ?, 2, 0, ?)
            """,
            (
                json.dumps({name: (1.0 if name in {"market_cap", "pe_ttm"} else None) for name in FUNDAMENTAL_FIELDS}),
                iso(NOW),
                iso(NOW),
                iso(NOW + timedelta(seconds=TTL_S)),
                iso(NOW),
            ),
        )
        connection.commit()

    stored = await store.get("MSFT", now=NOW)

    assert stored is not None
    assert stored.source == "yahoo"
    assert stored.field_sources["market_cap"] == "yahoo"
    assert stored.field_sources["pe_ttm"] == "yahoo"
    assert stored.field_sources["ps"] is None
    await store.aclose()


async def test_a_legacy_row_with_no_source_stays_unattributed(tmp_path: Path) -> None:
    from alembic import command

    from app.db import alembic_config

    db = tmp_path / "anonymous.db"
    command.upgrade(alembic_config(db), "head")  # the column exists and stays NULL
    legacy_row_insert(db, fields={"market_cap": 1.0}, source=None)
    store = await store_at(db)
    await store.ensure_ready()

    stored = await store.get("AAPL", now=NOW)
    coverage = await store.coverage(now=NOW)

    assert stored is not None
    assert stored.field_sources["market_cap"] is None
    assert coverage["field_sources"]["unattributed"] == 1
    assert coverage["field_sources"]["cells"] == {}
    await store.aclose()


async def test_coverage_counts_cells_per_source_and_per_field(tmp_path: Path) -> None:
    store = await store_at(tmp_path / "coverage.db")
    await store.upsert(make_snapshot(fields=full_fields()))
    await store.upsert(
        make_snapshot(
            "MSFT",
            source=SCAN_SOURCE,
            fields=mapped_fields(
                {
                    "market_cap_basic": 3.0e12,
                    "price_earnings_ttm": 31.0,
                    "debt_to_equity": 0.5,
                }
            ),
        )
    )

    coverage = await store.coverage(now=NOW)
    block = coverage["field_sources"]

    assert block["cells"] == {"yahoo": 22, SCAN_SOURCE: 3}
    assert block["attributed"] == 25
    assert block["unattributed"] == 0
    assert block["fields"]["market_cap"] == {
        "filled": 2,
        "sources": {"tradingview": 1, "yahoo": 1},
        "unattributed": 0,
    }
    assert block["fields"]["pe_ttm"]["sources"] == {"tradingview": 1, "yahoo": 1}
    assert block["fields"]["pe_forward"]["sources"] == {"yahoo": 1}
    # The row histogram is a different question, and it still answers it.
    assert coverage["sources"] == {"tradingview": 1, "yahoo": 1}
    await store.aclose()


async def test_coverage_of_no_rows_has_an_empty_provenance_block() -> None:
    coverage = coverage_report([], now=NOW)

    assert coverage["field_sources"]["cells"] == {}
    assert coverage["field_sources"]["attributed"] == 0
    assert coverage["field_sources"]["unattributed"] == 0
    assert set(coverage["field_sources"]["fields"]) == set(FUNDAMENTAL_FIELDS)


def test_field_source_counts_measures_what_it_is_given() -> None:
    counted = field_source_counts(
        [
            make_snapshot(fields={"market_cap": 1.0, "pe_ttm": 2.0}),
            make_snapshot("MSFT", source=None, fields={"market_cap": 3.0}),
        ]
    )

    assert counted["cells"] == {"yahoo": 2}
    assert counted["unattributed"] == 1
    assert counted["fields"]["market_cap"] == {
        "filled": 2,
        "sources": {"yahoo": 1},
        "unattributed": 1,
    }


async def test_the_builder_records_the_rung_each_cell_came_from(tmp_path: Path) -> None:
    """End to end: a walk's observations carry their own sources into the row."""

    store = await store_at(tmp_path / "built.db")
    from app.fundamentals.snapshot import FundamentalsSnapshotBuilder

    ladder = FakeLadder({"AAPL": full_fields()})
    builder = FundamentalsSnapshotBuilder(ladder=ladder, store=store, clock=lambda: NOW)

    result = await builder.build("AAPL")
    stored = await store.get("AAPL", now=NOW)

    assert result.status == "complete" and result.written is True
    assert stored is not None
    # ``FakeLadder`` emits every observation with ``source='fake'``.
    assert set(stored.field_sources.values()) == {"fake"}
    await store.aclose()


# ---------------------------------------------------------------------------
# A scan row is a TradingView row, cell by cell
# ---------------------------------------------------------------------------


def test_a_scan_row_attributes_its_cells_to_tradingview() -> None:
    row = {
        "symbol": "AAPL",
        "market_cap_basic": 3.4e12,
        "price_earnings_ttm": 28.5,
        "debt_to_equity": 0.784,
        "total_revenue": None,
    }

    snapshot = snapshot_from_scan_row(row, fetched_at=NOW)

    assert snapshot.source == SCAN_SOURCE == "tradingview"
    assert snapshot.field_sources["market_cap"] == "tradingview"
    assert snapshot.field_sources["pe_ttm"] == "tradingview"
    assert snapshot.field_sources["debt_to_equity"] == "tradingview"
    assert snapshot.field_sources["revenue_ttm"] is None
    assert {name for name, source in snapshot.field_sources.items() if source} == {
        "market_cap",
        "pe_ttm",
        "debt_to_equity",
    }
    assert snapshot.source != MULTI_SOURCE


# ---------------------------------------------------------------------------
# 0003 → 0004: additive, and a legacy row survives it
# ---------------------------------------------------------------------------


async def test_0004_adds_the_column_without_rewriting_a_legacy_row(tmp_path: Path) -> None:
    from alembic import command

    from app.db import alembic_config

    db = tmp_path / "at0003.db"
    command.upgrade(alembic_config(db), "0003")
    legacy_row_insert(db, fields={"market_cap": 3.4e12, "pe_ttm": 28.5}, source="yahoo")
    store = await store_at(db)
    before = await store.get("AAPL", now=NOW)
    assert before is not None and before.field_sources["market_cap"] == "yahoo"

    assert apply_migrations(db) == "0004"

    with closing(sqlite3.connect(db)) as connection:
        columns = {
            row[1]: (row[2], row[3])
            for row in connection.execute(
                "PRAGMA table_info(fundamentals_snapshots)"
            ).fetchall()
        }
        row = connection.execute(
            "SELECT source, field_sources, field_count FROM fundamentals_snapshots"
        ).fetchone()
    assert columns["field_sources"] == ("TEXT", 0), "additive and nullable"
    assert row[0] == "yahoo"
    assert row[1] is None, "an old row keeps NULL: its per-cell origin was never recorded"
    assert row[2] == 2

    after = await store.get("AAPL", now=NOW)
    assert after is not None
    assert after.fields == before.fields
    assert after.field_sources == before.field_sources
    await store.aclose()


async def test_a_0003_database_reads_but_refuses_a_write(tmp_path: Path) -> None:
    """The column is newer than the table: say so, do not die on ``no such column``."""

    from alembic import command

    from app.db import alembic_config

    db = tmp_path / "old.db"
    command.upgrade(alembic_config(db), "0003")
    legacy_row_insert(db, fields={"market_cap": 3.4e12}, source="yahoo")
    store = await store_at(db)

    rows = await store.rows()
    assert len(rows) == 1 and rows[0].field_sources["market_cap"] == "yahoo"

    with pytest.raises(FundamentalsSchemaError) as error:
        await store.upsert(make_snapshot(fields={"market_cap": 1.0}))
    assert "field_sources" in str(error.value)
    assert "upgrade head" in str(error.value)
    await store.aclose()


async def test_downgrading_to_0003_drops_only_the_provenance(tmp_path: Path) -> None:
    from alembic import command

    from app.db import alembic_config

    db = tmp_path / "roundtrip.db"
    command.upgrade(alembic_config(db), "0004")
    legacy_row_insert(db, fields={"market_cap": 3.4e12}, source="yahoo")

    command.downgrade(alembic_config(db), "0003")

    with closing(sqlite3.connect(db)) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(fundamentals_snapshots)").fetchall()
        }
        indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(fundamentals_snapshots)").fetchall()
        }
        rows = connection.execute("SELECT COUNT(*) FROM fundamentals_snapshots").fetchone()
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert "field_sources" not in columns
    assert "ix_fundamentals_expiry" in indexes
    assert rows == (1,), "the row survived the rollback"
    assert revision == ("0003",)


# ---------------------------------------------------------------------------
# The CLI report
# ---------------------------------------------------------------------------


async def test_the_cli_report_prints_cells_by_source(tmp_path: Path) -> None:
    db = tmp_path / "cli.db"
    store = await store_at(db)
    await store.upsert(make_snapshot("AAPL", fields={"market_cap": 3.4e12, "pe_ttm": 28.5}))
    legacy_row_insert(db, symbol="MSFT", fields={"market_cap": 3.0e12}, source=None)
    coverage = await store.coverage(now=NOW)
    await store.aclose()

    text = CLI.render_human(
        db_path=db, revision="0004", report=None, coverage=coverage
    )

    assert "cells by source" in text
    assert "provenance: attributed=2 unattributed=1" in text
    assert "{'yahoo': 2}" in text, "two cells came from yahoo across both rows"
    # Per field, not per row: one yahoo cell and one legacy cell share market_cap.
    assert "yahoo=1 unattributed=1" in text
    assert "unattributed=1" in text, "a legacy cell is counted, not credited to yahoo"


async def test_the_cli_scan_report_keeps_the_row_histogram_and_adds_cell_counts(
    tmp_path: Path,
) -> None:
    """Scan mode reports the store's provenance beside the per-source row counts."""

    db = tmp_path / "cli_scan.db"
    store = await store_at(db)
    await store.upsert(
        make_snapshot("AAPL", source=SCAN_SOURCE, fields={"market_cap": 3.4e12})
    )
    coverage = await store.coverage(now=NOW)
    await store.aclose()

    from app.fundamentals.scan import ScanReport

    scan = ScanReport(
        region="us",
        rows=1,
        total_count=1,
        issuers=1,
        duplicates=0,
        columns=("market_cap_basic",),
        results=(),
        snapshots=(),
        fetched_at=NOW,
    )
    text = CLI.render_scan_human(db_path=db, revision="0004", scan=scan, coverage=coverage)

    assert "sources: {'tradingview': 1}" in text, "the row histogram is unchanged"
    assert "provenance: attributed=1 unattributed=0" in text
    assert "cells by source: {'tradingview': 1}" in text
