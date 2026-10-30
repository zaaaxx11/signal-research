"""The fundamentals join: five filter columns read from the snapshot store.

Three Finviz keys the bulk scan cannot answer — ``fa_fpe`` (forward P/E),
``fa_ps`` (P/S) and ``fa_payoutratio`` (payout ratio) — have a real observation
in a different table. ``fundamentals_snapshots`` (:mod:`app.fundamentals.snapshot`,
P11A/P11C) holds 22 canonical fundamentals cells per symbol, refreshed on a 24-hour
window; the TradingView scan that builds a selection universe answers
``price_earnings_forward``, ``price_sales_ttm`` and ``payout_ratio`` with ``null``
for every row (live probe: 0 of 5,720 US rows, 0 of 96,278 global rows). This
module is the seam: it reads the store **once** per selection, copies the three
cells onto the universe rows by symbol, and — from the same snapshot — computes
the two ratios no stored cell holds: ``price_to_cash`` (``fa_pc``) and
``ev_to_sales`` (``fa_evsales``).

Why a join and not a new scan column
------------------------------------
A multiplier cannot be copied from a column that is always empty. The choice is
between refusing the keys forever and reading the one table that really answers
them; the scan stays the owner of prices, technicals and the metrics it does
fill, and the snapshot store is the owner of ``pe_forward``/``ps``/``payout_ratio``.
No copied value is derived here and none is averaged: one cell, one source,
copied.

Two cells are arithmetic, and only two
--------------------------------------
``price_to_cash = market_cap / cash_and_equivalents`` and
``ev_to_sales = (market_cap + total_debt - cash_and_equivalents) / revenue_ttm``
are identities over four snapshot cells — ``market_cap``,
``cash_and_equivalents``, ``total_debt``, ``revenue_ttm``. They are computed
here, not copied, because the store keeps the *inputs* of a ratio and not the
ratio itself: price-to-cash needs a cash-per-share denominator the scan has no
column for, and EV/sales needs enterprise value, which is the sum above rather
than a stored cell. Two formulas, each stated once in :data:`DERIVED_FORMULAS`,
and each computed only when every input is present:

* a cell that is missing, a string, a bool, ``NaN`` or ``inf`` is **unknown**,
  so the ratio is ``None`` — never ``0.0``, never ``inf``;
* a denominator at or below zero is not a ratio. ``cash_and_equivalents <= 0``
  and ``revenue_ttm <= 0`` both mean "no denominator", so the answer is unknown;
* the numerator must be complete. A symbol with a market cap and no
  ``total_debt`` has no enterprise value, so it has no EV/sales — treating
  absent debt as zero would be an assumption about the balance sheet, not a
  measurement of it;
* ``ev_to_sales`` **may be negative**, and that is an answer, not a bug: a
  company whose net cash exceeds its market cap has a negative enterprise value,
  and the Finviz key's own vocabulary carries a ``negative`` bucket
  (``app.screen.filters``).

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
  therefore never quotes yesterday's forward P/E as today's, and never computes
  a ratio from a balance sheet that has expired.
* **Null stays null, and a null never erases a value.** A cell the snapshot did
  not fill (a bank with no EBITDA, an ETF with no payout ratio) leaves ``None``
  on the row, and ``None`` matches no filter — unknown is not zero. When the scan
  *did* carry a number for the same column, the store's null does not overwrite
  it; when the store has a number, it wins, because it is the owner of these
  three columns. The two computed columns have exactly one source (the snapshot),
  so they are ``None`` whenever it cannot supply every input.
* **No mutation.** Each row is copied once, so the caller's universe (and the
  cached scan page behind it) keeps exactly the cells it arrived with. That is
  what lets two selections share one page without one seeing the other's joins.

A store that cannot answer — not configured, or a database that has not run the
``0003_fundamentals`` migration — is **not** a failed selection: the join logs
the reason and returns the rows unenriched, so the endpoint keeps its 200 and the
five cells stay ``None`` (and their filters match nothing, which is the same
answer an all-null column gives). Failing the whole query because an optional
enrichment table is absent would take the selection down with it.
"""

from __future__ import annotations

import logging
import math
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

#: The snapshot cells this join **copies** verbatim onto the row, in Finviz
#: panel order. Each is a cell of the snapshot vocabulary (``FUNDAMENTAL_FIELDS``),
#: stored full width — ``None`` means "the provider has no value", not "not a
#: fundamental column".
JOINED_ROW_FIELDS: tuple[str, ...] = ("pe_forward", "ps", "payout_ratio")

#: The row columns this join **computes** from snapshot cells, in Finviz panel
#: order. A ratio the store does not hold is arithmetic over the cells it does
#: hold — and nothing else: no estimate, no lookback, no second table.
DERIVED_ROW_FIELDS: tuple[str, ...] = ("price_to_cash", "ev_to_sales")

#: Every row column this module owns: the three copies followed by the two
#: computed ratios. This is the union :mod:`app.screen.filter_fields` checks a
#: filter key against, so a key can never point at a column no row carries.
FUNDAMENTALS_ROW_FIELDS: tuple[str, ...] = JOINED_ROW_FIELDS + DERIVED_ROW_FIELDS

#: Extra cells the full-columns view asks for. They are not filter columns and
#: stay out of the five-cell filter contract above; they are copies or exact
#: arithmetic from the same snapshot, used only to fill the display table.
VIEW_FILL_FIELDS: tuple[str, ...] = (
    "eps_ttm",
    "revenue_ttm",
    "net_income_ttm",
    "enterprise_value",
    "book_per_share",
    "cash_per_share",
    "prev_close",
    "eps_next_y_growth",
)

#: Finviz key → the row column this join fills. Published for the disposition
#: tests; :mod:`app.screen.filters` remains the one place a key gets its field
#: (``FILTER_CATALOG``), so this table can never become a second source of truth.
FUNDAMENTALS_FILTER_KEYS: Mapping[str, str] = MappingProxyType(
    {
        "fa_fpe": "pe_forward",
        "fa_ps": "ps",
        "fa_pc": "price_to_cash",
        "fa_evsales": "ev_to_sales",
        "fa_payoutratio": "payout_ratio",
    }
)

#: Computed column → its formula, written the way this module's docstring states
#: it. This is the table a reader (and the exact-formula test) checks the
#: implementation against, and the reason a computed cell can never be mistaken
#: for a provider copy.
DERIVED_FORMULAS: Mapping[str, str] = MappingProxyType(
    {
        "price_to_cash": "market_cap / cash_and_equivalents",
        "ev_to_sales": "(market_cap + total_debt - cash_and_equivalents) / revenue_ttm",
    }
)

#: Computed column → the **snapshot cells** it reads, in evaluation order. Every
#: one must be a finite number for the ratio to exist (the numerator has to be
#: complete), which is what keeps a missing ``total_debt`` from silently becoming
#: a debt-free company.
DERIVED_INPUTS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "price_to_cash": ("market_cap", "cash_and_equivalents"),
        "ev_to_sales": (
            "market_cap",
            "total_debt",
            "cash_and_equivalents",
            "revenue_ttm",
        ),
    }
)

#: Computed column → the cells that divide it. Each must be strictly positive:
#: a ratio over zero cash or zero revenue is not a measurement.
DERIVED_DENOMINATORS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "price_to_cash": ("cash_and_equivalents",),
        "ev_to_sales": ("revenue_ttm",),
    }
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


def _cell(value: Any) -> float | None:
    """One snapshot cell: a finite float, or ``None``.

    The store normalises its cells when it writes them, so a string, a bool,
    ``NaN`` or ``inf`` reaching this point was never a measurement — and it stays
    unknown instead of being coerced into one.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def derive_cells(snapshot: FundamentalsSnapshot) -> dict[str, float | None]:
    """The two computed columns for one stored snapshot; ``None`` where unknown.

    ``price_to_cash`` needs a positive cash balance and a market cap;
    ``ev_to_sales`` needs a complete enterprise value (market cap, total debt and
    cash — all three) over a positive revenue. A non-positive denominator is not
    a ratio, and a missing input is not a zero, so both cases answer ``None``.
    The enterprise value itself may be negative (net cash above the market cap)
    and that is reported as a negative multiple, exactly as the Finviz key's
    ``negative`` option describes it.
    """

    cells = snapshot.fields
    market_cap = _cell(cells.get("market_cap"))
    cash = _cell(cells.get("cash_and_equivalents"))
    debt = _cell(cells.get("total_debt"))
    revenue = _cell(cells.get("revenue_ttm"))

    price_to_cash = (
        market_cap / cash
        if market_cap is not None and cash is not None and cash > 0
        else None
    )
    enterprise_value = (
        market_cap + debt - cash
        if market_cap is not None and debt is not None and cash is not None
        else None
    )
    ev_to_sales = (
        enterprise_value / revenue
        if enterprise_value is not None and revenue is not None and revenue > 0
        else None
    )
    return {"price_to_cash": price_to_cash, "ev_to_sales": ev_to_sales}


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


def derive_view_cells(
    row: Mapping[str, Any], snapshot: FundamentalsSnapshot | None
) -> dict[str, float | None]:
    """Exact display-only arithmetic from the row and its snapshot.

    These fill the wide table without changing what a filter can address. A
    denominator is checked, a missing input stays unknown, and no provider value
    is invented. ``prev_close`` is an identity over the row's own price and
    change; the balance-sheet cells come from the snapshot.
    """

    cells = snapshot.fields if snapshot is not None else {}
    price = _cell(row.get("price"))
    change = _cell(row.get("change_pct"))
    market_cap = _cell(row.get("market_cap_usd")) or _cell(cells.get("market_cap"))
    cash = _cell(cells.get("cash_and_equivalents"))
    debt = _cell(cells.get("total_debt"))
    shares = _cell(row.get("shares_outstanding")) or _cell(cells.get("shares_outstanding"))
    pb = _cell(row.get("pb")) or _cell(cells.get("pb"))

    enterprise_value = (
        market_cap + debt - cash
        if market_cap is not None and debt is not None and cash is not None
        else None
    )
    book_per_share = price / pb if price is not None and pb is not None and pb > 0 else None
    cash_per_share = cash / shares if cash is not None and shares is not None and shares > 0 else None
    prev_close = (
        price / (1 + change / 100)
        if price is not None and change is not None and 1 + change / 100 > 0
        else None
    )
    eps_ttm = _cell(cells.get("eps_ttm"))
    eps_forward = _cell(cells.get("eps_forward"))
    eps_next_y_growth = (
        (eps_forward / eps_ttm - 1) * 100
        if eps_ttm is not None and eps_forward is not None and eps_ttm > 0
        else None
    )
    return {
        "enterprise_value": enterprise_value,
        "book_per_share": book_per_share,
        "cash_per_share": cash_per_share,
        "prev_close": prev_close,
        "eps_next_y_growth": eps_next_y_growth,
    }


def apply_fundamentals(
    rows: Sequence[Mapping[str, Any]], snapshots: Mapping[str, FundamentalsSnapshot]
) -> list[dict[str, Any]]:
    """Join the three copies and the two computed ratios onto **copies** of ``rows``.

    A row whose symbol the store does not carry keeps its own cells (``None`` on
    a parity row, because the scan answered null) and gains no value. The five
    columns are always present in the result, so a consumer can tell "the
    snapshot has no payout ratio for this symbol" from "this row predates the
    join" — the second is a bug, the first is an answer. The computed columns are
    written from the snapshot only: a copy whose inputs are missing stays
    ``None``, and no row and no snapshot is ever modified.
    """

    enriched: list[dict[str, Any]] = []
    for row in rows:
        joined = dict(row)
        key = _row_key(row)
        snapshot = snapshots.get(key) if key is not None else None
        for name in JOINED_ROW_FIELDS:
            current = joined.get(name)
            value = snapshot.fields.get(name) if snapshot is not None else None
            joined[name] = current if value is None else value
        computed = derive_cells(snapshot) if snapshot is not None else {}
        for name in DERIVED_ROW_FIELDS:
            joined[name] = computed.get(name)
        enriched.append(joined)
    return enriched


def apply_view_fill(
    rows: Sequence[Mapping[str, Any]], snapshots: Mapping[str, FundamentalsSnapshot]
) -> list[dict[str, Any]]:
    """Add the display-only wide-table cells after the filter contract is shaped.

    Kept separate from :func:`apply_fundamentals` on purpose: the five filter
    cells are a contract, while these extras only fill the All-columns display.
    The caller's rows and snapshots are never mutated.

    **A computed cell never erases a measured one.** ``enterprise_value``,
    ``cash_per_share`` and ``book_per_share`` are recomputed here from the
    snapshot, but the row may already carry them from the depth store (which
    measured them directly). Overwriting a value with the ``None`` of a partial
    snapshot is how the audit found three of those cells permanently null: the
    arithmetic had no inputs, so it erased the number that did.
    """

    filled: list[dict[str, Any]] = []
    for row in rows:
        joined = dict(row)
        key = _row_key(row)
        snapshot = snapshots.get(key) if key is not None else None
        for name in ("eps_ttm", "revenue_ttm", "net_income_ttm"):
            current = joined.get(name)
            value = snapshot.fields.get(name) if snapshot is not None else None
            joined[name] = current if value is None else value
        display = derive_view_cells(joined, snapshot)
        for name in ("enterprise_value", "book_per_share", "cash_per_share", "prev_close", "eps_next_y_growth"):
            value = display.get(name)
            current = joined.get(name)
            # Fill a gap, never clobber: the snapshot's arithmetic is a second
            # opinion, and a missing input is not an argument against the
            # measurement the row already holds.
            joined[name] = current if value is None else value
        filled.append(joined)
    return filled


async def enrich_rows(
    rows: Sequence[Mapping[str, Any]],
    store: SnapshotReader | None = None,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Join the fundamentals snapshot store onto a selection universe.

    This is the whole public path the API calls: one batch read, then a pure
    copy plus two identities. ``store`` is the runtime's store
    (``Runtime.fundamentals_store``); a test injects a double with the same
    one-method surface.
    """

    if not rows:
        return []
    snapshots = await load_snapshots(store, (_row_key(row) or "" for row in rows), now=now)
    return apply_view_fill(apply_fundamentals(rows, snapshots), snapshots)


__all__ = [
    "DERIVED_DENOMINATORS",
    "DERIVED_FORMULAS",
    "DERIVED_INPUTS",
    "DERIVED_ROW_FIELDS",
    "FUNDAMENTALS_FILTER_KEYS",
    "FUNDAMENTALS_ROW_FIELDS",
    "VIEW_FILL_FIELDS",
    "derive_view_cells",
    "JOINED_ROW_FIELDS",
    "SnapshotReader",
    "apply_fundamentals",
    "apply_view_fill",
    "derive_cells",
    "enrich_rows",
    "fresh_snapshots",
    "load_snapshots",
]
