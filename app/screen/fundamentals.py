"""The fundamentals join: three filter columns read from the snapshot store.

Three Finviz keys the bulk scan cannot answer — ``fa_fpe`` (forward P/E),
``fa_ps`` (P/S) and ``fa_payoutratio`` (payout ratio) — have a real observation
in a different table. ``fundamentals_snapshots`` (:mod:`app.fundamentals.snapshot`,
P11A/P11C) holds 22 canonical fundamentals cells per symbol, refreshed on a 24-hour
window; the TradingView scan that builds a selection universe answers
``price_earnings_forward``, ``price_sales_ttm`` and ``payout_ratio`` with ``null``
for every row (live probe: 0 of 5,720 US rows, 0 of 96,278 global rows). This
module is the seam: it reads the store **once** per selection and copies the three
cells onto the universe rows by symbol.

Why a join and not a new scan column
------------------------------------
A multiplier cannot be copied from a column that is always empty. The choice is
between refusing the keys forever and reading the one table that really answers
them; the scan stays the owner of prices, technicals and the metrics it does
fill, and the snapshot store is the owner of ``pe_forward``/``ps``/``payout_ratio``.
No value is derived here and none is averaged: one cell, one source, copied.

Four rules the code has to earn
-------------------------------
* **One read per selection, never one per symbol.** The store's public read is
  :meth:`SqliteFundamentalsStore.rows` — a single ``SELECT`` over the whole table
  (3.9k rows live), filtered to the universe in memory. Calling ``get(symbol)``
  per row would send 5,700 queries for a US criteria selection; the join sends
  one. The read is bounded by the *store* (one row per symbol, 24h TTL), not by
  the universe, so a 96k-row global universe costs the same query.
* **Fresh only.** A row past ``expires_at`` is not evidence of anything this
  second; a stale snapshot is skipped exactly like a missing one. The response
  therefore never quotes yesterday's forward P/E as today's.
* **Null stays null, and a null never erases a value.** A cell the snapshot did
  not fill (a bank with no EBITDA, an ETF with no payout ratio) leaves ``None``
  on the row, and ``None`` matches no filter — unknown is not zero. When the scan
  *did* carry a number for the same column, the store's null does not overwrite
  it; when the store has a number, it wins, because it is the owner of these
  three columns.
* **No mutation.** Each row is copied once, so the caller's universe (and the
  cached scan page behind it) keeps exactly the cells it arrived with. That is
  what lets two selections share one page without one seeing the other's joins.

A store that cannot answer — not configured, or a database that has not run the
``0003_fundamentals`` migration — is **not** a failed selection: the join logs
the reason and returns the rows unenriched, so the endpoint keeps its 200 and the
three cells stay ``None`` (and their filters match nothing, which is the same
answer an all-null column gives). Failing the whole query because an optional
enrichment table is absent would take the selection down with it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Protocol, Sequence

from sqlalchemy.exc import OperationalError

from app.fundamentals.snapshot import (
    FundamentalsSchemaError,
    FundamentalsSnapshot,
    canonical_symbol,
    utc_now,
)

logger = logging.getLogger("mee.screen.fundamentals")

#: The canonical row columns this join owns, in Finviz panel order. Each is a
#: cell of the snapshot vocabulary (``FUNDAMENTAL_FIELDS``), stored full width —
#: ``None`` means "the provider has no value", not "not a fundamental column".
FUNDAMENTALS_ROW_FIELDS: tuple[str, ...] = ("pe_forward", "ps", "payout_ratio")

#: Finviz key → the row column this join fills. Published for the disposition
#: tests; :mod:`app.screen.filters` remains the one place a key gets its field
#: (``FILTER_CATALOG``), so this table can never become a second source of truth.
FUNDAMENTALS_FILTER_KEYS: Mapping[str, str] = MappingProxyType(
    {"fa_fpe": "pe_forward", "fa_ps": "ps", "fa_payoutratio": "payout_ratio"}
)


class SnapshotReader(Protocol):
    """The one read this module needs: every stored snapshot, in one call."""

    async def rows(self) -> Sequence[FundamentalsSnapshot]:  # pragma: no cover - protocol
        ...


def _row_key(row: Mapping[str, Any]) -> str | None:
    """The join key of a universe row, canonicalised the way the store stores it."""

    symbol = row.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        return None
    try:
        return canonical_symbol(symbol)
    except ValueError:
        return None


def fresh_snapshots(
    snapshots: Iterable[FundamentalsSnapshot], *, now: datetime | None = None
) -> dict[str, FundamentalsSnapshot]:
    """``symbol → snapshot`` for the rows still inside their window.

    Staleness is decided by ``expires_at`` (the fetch clock), never by ``as_of``:
    a snapshot built this morning from last quarter's filing is fresh data, and a
    row fetched four days ago is not, whatever date its cells carry.
    """

    moment = now or utc_now()
    fresh: dict[str, FundamentalsSnapshot] = {}
    for snapshot in snapshots:
        if snapshot.is_fresh(moment):
            fresh[snapshot.symbol] = snapshot
    return fresh


async def load_snapshots(
    store: SnapshotReader | None,
    symbols: Iterable[str],
    *,
    now: datetime | None = None,
) -> dict[str, FundamentalsSnapshot]:
    """Read the store once and keep the fresh rows the universe asks for.

    ``store=None`` and an unmigrated database are both "no enrichment", not
    errors: an optional table that an operator has not created yet must not turn
    a selection into a 500. The reason is logged once per call — an operator
    reading the log should see *why* forward P/E filters answer nothing.
    """

    wanted = set()
    for symbol in symbols:
        if isinstance(symbol, str) and symbol.strip():
            try:
                wanted.add(canonical_symbol(symbol))
            except ValueError:  # pragma: no cover - blank guarded above
                continue
    if store is None or not wanted:
        return {}

    try:
        stored = await store.rows()
    except (FundamentalsSchemaError, OperationalError) as exc:
        logger.warning(
            "fundamentals enrichment disabled for this selection: %s", exc
        )
        return {}

    return {
        symbol: snapshot
        for symbol, snapshot in fresh_snapshots(stored, now=now).items()
        if symbol in wanted
    }


def apply_fundamentals(
    rows: Sequence[Mapping[str, Any]], snapshots: Mapping[str, FundamentalsSnapshot]
) -> list[dict[str, Any]]:
    """Copy the three cells onto **copies** of ``rows``; missing stays missing.

    A row whose symbol the store does not carry keeps its own cells (``None`` on
    a parity row, because the scan answered null) and gains no value. The three
    columns are always present in the result, so a consumer can tell "the
    snapshot has no payout ratio for this symbol" from "this row predates the
    join" — the second is a bug, the first is an answer.
    """

    enriched: list[dict[str, Any]] = []
    for row in rows:
        joined = dict(row)
        key = _row_key(row)
        snapshot = snapshots.get(key) if key is not None else None
        for name in FUNDAMENTALS_ROW_FIELDS:
            current = joined.get(name)
            value = snapshot.fields.get(name) if snapshot is not None else None
            joined[name] = current if value is None else value
        enriched.append(joined)
    return enriched


async def enrich_rows(
    rows: Sequence[Mapping[str, Any]],
    store: SnapshotReader | None = None,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Join the fundamentals snapshot store onto a selection universe.

    This is the whole public path the API calls: one batch read, then a pure
    copy. ``store`` is the runtime's store (``Runtime.fundamentals_store``); a
    test injects a double with the same one-method surface.
    """

    if not rows:
        return []
    snapshots = await load_snapshots(store, (_row_key(row) or "" for row in rows), now=now)
    return apply_fundamentals(rows, snapshots)


__all__ = [
    "FUNDAMENTALS_FILTER_KEYS",
    "FUNDAMENTALS_ROW_FIELDS",
    "SnapshotReader",
    "apply_fundamentals",
    "enrich_rows",
    "fresh_snapshots",
    "load_snapshots",
]
