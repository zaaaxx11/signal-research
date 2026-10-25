"""P11G: the fundamentals join that makes ``fa_fpe``/``fa_ps``/``fa_payoutratio`` live.

The three keys were refused because the bulk scan answers ``null`` for
``price_earnings_forward``, ``price_sales_ttm`` and ``payout_ratio`` on every
row. The fundamentals snapshot store (P11A/P11C, 3.9k fresh symbols live) is a
real source for all three, so the selection now joins it onto the universe rows.

Each test pins one of the ways the join can be wrong:

* a cell copied from the wrong symbol, or copied when the store row is stale;
* a null cell that becomes ``0`` or that erases a value the scan carried;
* a per-symbol ``get()`` loop (5,700 queries for one US selection) instead of one
  read of the table;
* a row mutated in place, so the next selection sees the previous one's join;
* a selection that fails because the optional table was never migrated;
* the disposition drifting back to 39/43, or the published schema lagging the
  catalogue.

The API tests use the real ASGI app with a fake store injected at the runtime
seam, which is the same seam a deployment replaces with its own reader.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

from app.fundamentals.snapshot import (
    FUNDAMENTAL_FIELDS,
    FundamentalsSchemaError,
    FundamentalsSnapshot,
    SqliteFundamentalsStore,
)
from app.screen import fundamentals as joined
from app.screen.filter_fields import (
    CHOICE_FIELDS,
    ROW_FIELDS,
    UNAVAILABLE,
    binding,
    mapped_keys,
    reason_for,
    unavailable_keys,
)
from app.screen.filters import FIELDS, FILTER_CATALOG, apply_filters
from app.screen.selection import SELECTION_REQUEST_SCHEMA
from tests import _screen as S

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)

#: The three keys this pass moves from "refused" to "mapped", and their columns.
P11G: dict[str, str] = {
    "fa_fpe": "pe_forward",
    "fa_ps": "ps",
    "fa_payoutratio": "payout_ratio",
}


def snapshot(symbol: str, *, now: datetime = NOW, **cells: Any) -> FundamentalsSnapshot:
    """One stored snapshot: full width, only ``cells`` filled."""

    fields: dict[str, Any] = {name: None for name in FUNDAMENTAL_FIELDS}
    for name, value in cells.items():
        assert name in fields, f"{name} is not a fundamentals cell"
        fields[name] = None if value is None else float(value)
    return FundamentalsSnapshot(
        symbol=symbol,
        instrument_id=f"test:{symbol}",
        fields=fields,
        source="yahoo",
        as_of=now,
        fetched_at=now,
        expires_at=now + timedelta(hours=24),
    )


def universe_row(symbol: str, **cells: Any) -> dict[str, Any]:
    """A parity row as ``build_universe`` emits it: the two scan cells are null."""

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
        "pe_forward": None,
        "ps": None,
    }
    row.update(cells)
    return row


class FakeStore:
    """The one-method surface the join reads: ``rows()``, counted."""

    def __init__(self, *snapshots: FundamentalsSnapshot) -> None:
        self._snapshots = list(snapshots)
        self.reads = 0
        self.gets = 0

    async def rows(self) -> Sequence[FundamentalsSnapshot]:
        self.reads += 1
        return list(self._snapshots)

    async def get(self, symbol: str, **_: Any) -> FundamentalsSnapshot | None:  # pragma: no cover
        # Present so a per-symbol implementation would be *observable* rather
        # than invisible: the join must never call it.
        self.gets += 1
        return next((s for s in self._snapshots if s.symbol == symbol), None)


class BrokenStore:
    """A store whose read fails the way an unmigrated database fails."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def rows(self) -> Sequence[FundamentalsSnapshot]:
        raise self.error


# ---------------------------------------------------------------------------
# The join itself
# ---------------------------------------------------------------------------


async def test_the_join_copies_the_three_cells_by_symbol_and_leaves_the_rest_null() -> None:
    rows = [universe_row("AAPL"), universe_row("MSFT"), universe_row("TSM")]
    store = FakeStore(
        snapshot("AAPL", pe_forward=36.33, ps=10.87, payout_ratio=12.04),
        snapshot("MSFT", pe_forward=22.13),
    )

    enriched = await joined.enrich_rows(rows, store)

    aapl, msft, tsm = enriched
    assert (aapl["pe_forward"], aapl["ps"], aapl["payout_ratio"]) == (36.33, 10.87, 12.04)
    assert (msft["pe_forward"], msft["ps"], msft["payout_ratio"]) == (22.13, None, None), (
        "a cell the snapshot did not fill stays None, never 0"
    )
    assert (tsm["pe_forward"], tsm["ps"], tsm["payout_ratio"]) == (None, None, None), (
        "a symbol the store does not hold gains no value"
    )
    for row in enriched:
        assert row["price"] == 100.0, "the join must not disturb the cells it does not own"


async def test_the_join_reads_the_store_once_for_the_whole_universe() -> None:
    rows = [universe_row(symbol) for symbol in ("AAPL", "MSFT", "NVDA", "AMZN", "META")]
    store = FakeStore(*[snapshot(row["symbol"], pe_forward=10.0) for row in rows])

    await joined.enrich_rows(rows, store)

    assert store.reads == 1, "one batch read per selection, never one query per symbol"
    assert store.gets == 0, "a per-symbol get() loop is the N+1 this module exists to avoid"


def test_the_join_never_mutates_the_rows_it_reads() -> None:
    rows = [universe_row("AAPL")]
    before = json.dumps(rows, sort_keys=True)
    snapshots = {"AAPL": snapshot("AAPL", pe_forward=36.33, ps=10.87, payout_ratio=12.04)}

    enriched = joined.apply_fundamentals(rows, snapshots)

    assert json.dumps(rows, sort_keys=True) == before, (
        "the caller's universe (and the cached scan page behind it) must keep its cells"
    )
    assert enriched[0] is not rows[0], "a joined row is a copy, not the input object"
    assert enriched[0]["pe_forward"] == 36.33


def test_a_store_null_never_erases_a_value_the_scan_carried() -> None:
    # Not reachable from today's scan (it answers null), but the rule has to hold
    # if the provider ever starts filling the column: the store is an enrichment,
    # not the only source, and ``null`` means "no observation", not "no value".
    rows = [universe_row("AAPL", pe_forward=9.9)]
    enriched = joined.apply_fundamentals(rows, {"AAPL": snapshot("AAPL", ps=10.87)})
    assert enriched[0]["pe_forward"] == 9.9
    assert enriched[0]["ps"] == 10.87


def test_a_stale_snapshot_is_not_evidence() -> None:
    old = snapshot("AAPL", pe_forward=36.33)
    expired = FundamentalsSnapshot(
        symbol="AAPL",
        instrument_id=old.instrument_id,
        fields=old.fields,
        source=old.source,
        as_of=old.as_of,
        fetched_at=old.fetched_at,
        expires_at=NOW - timedelta(seconds=1),
    )
    rows = [universe_row("AAPL")]

    enriched = joined.apply_fundamentals(rows, joined.fresh_snapshots([expired], now=NOW))

    assert enriched[0]["pe_forward"] is None, (
        "a row past expires_at must not be quoted as today's forward P/E"
    )


def test_a_row_without_a_symbol_is_skipped_not_keyed_on_the_empty_string() -> None:
    rows = [{"symbol": "   ", "price": 1.0}, {"price": 2.0}]
    enriched = joined.apply_fundamentals(rows, {"": snapshot("SPY", pe_forward=1.0)})
    assert [row["pe_forward"] for row in enriched] == [None, None]


# ---------------------------------------------------------------------------
# Filtering on the joined cells
# ---------------------------------------------------------------------------


async def test_a_filled_cell_matches_and_a_null_cell_does_not() -> None:
    rows = [universe_row("AAPL"), universe_row("MSFT"), universe_row("TSM")]
    store = FakeStore(
        snapshot("AAPL", pe_forward=12.5, ps=3.25, payout_ratio=55.0),
        snapshot("MSFT", pe_forward=30.0, ps=11.0, payout_ratio=None),
    )
    enriched = await joined.enrich_rows(rows, store)

    cheap = apply_filters(enriched, [{"field": "pe_forward", "op": "lte", "value": 20}])
    small_ps = apply_filters(enriched, [{"field": "ps", "op": "lt", "value": 5}])
    payers = apply_filters(enriched, [{"field": "payout_ratio", "op": "gte", "value": 50}])

    assert [row["symbol"] for row in cheap] == ["AAPL"]
    assert [row["symbol"] for row in small_ps] == ["AAPL"]
    assert [row["symbol"] for row in payers] == ["AAPL"], (
        "MSFT has a null payout ratio, so it must not match a payout filter"
    )


async def test_the_finviz_keys_resolve_to_the_joined_columns() -> None:
    for key, field in P11G.items():
        found = binding(key)
        assert found.is_available is True
        assert found.field == field
        assert FIELDS[field] == "number"
        assert reason_for(key) is None

    rows = [universe_row("AAPL"), universe_row("MSFT")]
    enriched = await joined.enrich_rows(
        rows, FakeStore(snapshot("AAPL", pe_forward=12.5, ps=3.25, payout_ratio=55.0))
    )
    # Compile through the catalogue's own field, exactly as the selection does.
    compiled = {"field": binding("fa_fpe").field, "op": "lte", "value": 20}
    assert [row["symbol"] for row in apply_filters(enriched, [compiled])] == ["AAPL"]


# ---------------------------------------------------------------------------
# Disposition: 42 single-column keys + 3 option-driven, 37 refused
# ---------------------------------------------------------------------------


def test_the_catalogue_moved_to_forty_five_available_and_thirty_seven_refused() -> None:
    assert len(mapped_keys()) == 45
    assert len(unavailable_keys()) == 37
    assert len(mapped_keys()) + len(unavailable_keys()) == 82
    assert set(mapped_keys()) == set(ROW_FIELDS) | set(CHOICE_FIELDS)
    assert not set(mapped_keys()) & set(unavailable_keys())


def test_the_three_keys_name_a_column_and_their_refusals_are_gone() -> None:
    for key, field in P11G.items():
        assert FILTER_CATALOG[key].field == field
        assert ROW_FIELDS[key] == field
        assert key not in UNAVAILABLE
        assert field in joined.FUNDAMENTALS_ROW_FIELDS, (
            "the column must be one the join really writes onto the row"
        )


def test_the_refusals_that_remain_still_name_a_missing_metric() -> None:
    assert "answered null for all" in (reason_for("sh_short") or "")
    assert "answered null for all" in (reason_for("ipodate") or "")
    assert reason_for("fa_divgrowth"), "dividend growth is still not a payout ratio"
    for key, reason in UNAVAILABLE.items():
        assert reason.strip() and len(reason) > 20, f"{key}: {reason!r}"


# ---------------------------------------------------------------------------
# Degradation: no store, no table
# ---------------------------------------------------------------------------


async def test_a_missing_store_is_no_enrichment_not_an_error() -> None:
    rows = [universe_row("AAPL")]
    enriched = await joined.enrich_rows(rows, None)
    assert enriched[0]["pe_forward"] is None
    assert enriched[0]["symbol"] == "AAPL"


async def test_an_unmigrated_database_is_no_enrichment_not_an_error(tmp_path: Path) -> None:
    """The production failure mode: a service DB that never ran ``0003``."""

    stub = tmp_path / "stub.db"
    with closing(sqlite3.connect(stub)) as connection:
        # ``ensure_schema`` only migrates a database it created itself, so a jobs
        # table is exactly what a pre-P11A production file has.
        connection.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY)")
        connection.commit()

    store = SqliteFundamentalsStore(stub)
    try:
        assert await store._table_exists() is False  # noqa: SLF001 - the premise
        enriched = await joined.enrich_rows([universe_row("AAPL")], store)
    finally:
        await store.aclose()

    assert enriched[0]["pe_forward"] is None


async def test_a_store_that_raises_schema_error_degrades() -> None:
    enriched = await joined.enrich_rows(
        [universe_row("AAPL")],
        BrokenStore(FundamentalsSchemaError("no fundamentals_snapshots table")),
    )
    assert enriched[0]["pe_forward"] is None


async def test_an_empty_universe_costs_no_read() -> None:
    store = FakeStore(snapshot("AAPL", pe_forward=1.0))
    assert await joined.enrich_rows([], store) == []
    assert store.reads == 0


# ---------------------------------------------------------------------------
# The published schema
# ---------------------------------------------------------------------------


def test_the_schema_artifact_is_the_served_document_and_counts_the_joined_keys() -> None:
    artifact = ROOT / "schemas" / "stock_selection_request_v1.json"
    assert json.loads(artifact.read_text(encoding="utf-8")) == SELECTION_REQUEST_SCHEMA

    extension = SELECTION_REQUEST_SCHEMA["x-filter-keys"]
    published: dict[str, Any] = dict(extension)  # type: ignore[arg-type]
    mapped = {key: entry for key, entry in published.items() if entry["field"] is not None}
    chosen = {key: entry for key, entry in published.items() if "choice" in entry}
    refused = {
        key: entry
        for key, entry in published.items()
        if entry["field"] is None and "choice" not in entry
    }
    assert len(mapped) == 42 and len(chosen) == 3 and len(refused) == 37
    assert set(mapped) | set(chosen) == set(mapped_keys())
    assert set(refused) == set(unavailable_keys())
    for key, field in P11G.items():
        assert mapped[key]["field"] == field
        assert mapped[key]["kind"] == "number"
        assert "reason" not in mapped[key]
    predicate = SELECTION_REQUEST_SCHEMA["$defs"]["filter"]["properties"]  # type: ignore[index]
    description = predicate["key"]["description"]
    assert "The 42 that resolve to one row column" in description
    assert "the 3 that filter through their option vocabulary" in description
    assert "the 37 that are refused" in description


# ---------------------------------------------------------------------------
# The API: one fake store, the real route
# ---------------------------------------------------------------------------


def api_criteria(key: str, op: str, value: Any) -> dict[str, Any]:
    return {
        "mode": "criteria",
        "region": "us",
        "criteria": {"filters": [{"key": key, "op": op, "value": value}], "limit": 20},
    }


async def test_the_route_filters_on_forward_pe_joined_from_the_store(
    tmp_path: Path, monkeypatch: Any
) -> None:
    store = FakeStore(
        snapshot("AAPL", pe_forward=12.5, ps=3.25, payout_ratio=55.0),
        snapshot("MSFT", pe_forward=30.0, ps=11.0, payout_ratio=None),
    )
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        harness.runtime.fundamentals_store = store
        response = await harness.client.post(
            "/screens/select", json=api_criteria("fa_fpe", "lte", 20)
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1, (
        "only AAPL has a forward P/E in the store; every other row is null and "
        f"matches nothing (total={body['total']})"
    )
    assert [row["symbol"] for row in body["rows"]] == ["AAPL"]
    assert body["filters"][0]["field"] == "pe_forward"
    assert store.reads == 1


async def test_the_route_filters_on_payout_ratio_and_ps(
    tmp_path: Path, monkeypatch: Any
) -> None:
    store = FakeStore(
        snapshot("AAPL", pe_forward=12.5, ps=3.25, payout_ratio=55.0),
        snapshot("MSFT", ps=4.0, payout_ratio=20.0),
    )
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        harness.runtime.fundamentals_store = store
        ps = await harness.client.post("/screens/select", json=api_criteria("fa_ps", "lt", 5))
        payout = await harness.client.post(
            "/screens/select", json=api_criteria("fa_payoutratio", "gte", 50)
        )

    assert ps.status_code == 200, ps.text
    assert [row["symbol"] for row in ps.json()["rows"]] == ["AAPL", "MSFT"]
    assert ps.json()["total"] == 2
    assert payout.status_code == 200, payout.text
    assert [row["symbol"] for row in payout.json()["rows"]] == ["AAPL"]
    assert store.reads == 2, "one batch read per request, not one per row"


async def test_the_route_answers_without_a_store(tmp_path: Path, monkeypatch: Any) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        harness.runtime.fundamentals_store = None
        response = await harness.client.post(
            "/screens/select", json=api_criteria("fa_fpe", "lte", 20)
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 0, "unknown is not a match: no store means no forward P/E"
    assert body["rows"] == []


async def test_the_route_answers_with_the_real_empty_store(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """No fake: the runtime's own store over the (empty) migrated test database."""

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select", json=api_criteria("fa_payoutratio", "gte", 1)
        )

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0
