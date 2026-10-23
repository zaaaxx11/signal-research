"""The 300-second snapshot and the always-twenty-row browse document.

``GET /screens`` is a **display**, not a search: every read answers the top
``DISPLAY_ROWS`` of one snapshot, never a cursor and never a second page. Two
consequences the code has to earn:

* a snapshot is keyed by ``(region, sort)`` and lives ``SNAPSHOT_TTL_S`` = 300 s;
  a read inside the window sends **zero** provider requests, a read after it
  refreshes once. The three regions are ``us``, ``global`` and ``us_etf`` — the
  ETF tab is a region, not a flag, because the instrument family belongs to the
  question a reader is asking (see :mod:`app.screen.scanner`);
* ``total`` is the post-filter/post-dedupe universe size, so it is normal for
  ``total`` (23) to be larger than the rows served (20).

When a refresh is blocked, the previous snapshot is served with ``stale=true``
and its original ``as_of`` — the plan's rule is "no invented data", and a
five-minute-old price with an honest timestamp beats a 502. Without a previous
snapshot there is nothing honest to serve, so the block surfaces as an error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping

from app.screen import rank as ranking
from app.screen import universe as universes
from app.screen.repository import SqliteScreenRepository
from app.screen.scanner import FULL_SCAN_ROWS, ScanBlocked, ScanError, fetch_scan

#: The display window: five minutes (user revision of PLAN §4).
SNAPSHOT_TTL_S = 300
#: Rows served on every browse hit. Fixed; ``limit`` cannot widen it.
DISPLAY_ROWS = 20
#: Where the universe came from; written into ``screens.source`` and into every
#: exported row's ``source`` cell.
SOURCE = "tradingview"


@dataclass(frozen=True)
class Snapshot:
    """One stored snapshot and the rows it displays."""

    screen: dict[str, Any]
    rows: list[dict[str, Any]]
    stale: bool = False

    @property
    def screen_id(self) -> str:
        return str(self.screen["id"])

    @property
    def as_of(self) -> str:
        return str(self.screen["as_of"])


def display_row(row: Mapping[str, Any], *, as_of: str) -> dict[str, Any]:
    """One API row: the screen columns plus the snapshot's own timestamp.

    ``turnover_usd`` is carried even though the light export omits it: a client
    must be able to tell a CHF turnover from a USD one without guessing.
    """

    return {
        "rank": int(row["rank"]),
        "symbol": row.get("symbol"),
        "name": row.get("name"),
        "exchange": row.get("exchange"),
        "country": row.get("country"),
        "sector": row.get("sector"),
        "price": row.get("price"),
        "change_pct": row.get("change_pct"),
        "volume": row.get("volume"),
        "turnover": row.get("turnover"),
        "turnover_usd": row.get("turnover_usd"),
        "market_cap_usd": row.get("market_cap_usd"),
        "is_primary": bool(row.get("is_primary")),
        "as_of": as_of,
    }


def snapshot_document(snapshot: Snapshot) -> dict[str, Any]:
    """The ``GET /screens`` body. No ``next_cursor``: there is no page 2."""

    screen = snapshot.screen
    rows = [display_row(row, as_of=snapshot.as_of) for row in snapshot.rows]
    return {
        "screen_id": snapshot.screen_id,
        "region": screen["region"],
        "sort": screen["sort"],
        "source": screen.get("source") or SOURCE,
        "as_of": snapshot.as_of,
        "refreshed_at": screen.get("created_at") or snapshot.as_of,
        "next_refresh_at": screen.get("expires_at"),
        "stale": snapshot.stale,
        "total": int(screen.get("total_count") or 0),
        "returned": len(rows),
        "rows": rows,
    }


async def refresh_screen(
    region: str,
    sort: str,
    *,
    repository: SqliteScreenRepository,
    now: datetime | None = None,
    ttl_s: int = SNAPSHOT_TTL_S,
) -> Snapshot:
    """Scan once, build the universe, rank it, store the top 20.

    One provider request per refresh: the scan is bulk, and the deep enrichment
    of the displayed symbols is the export's job, not the browse path's.
    """

    moment = now or datetime.now(UTC)
    # One large request gives the exact post-filter total, not the first 5k
    # page's partial count. Observed live: 65k rows in ~7 s for Global.
    page = await fetch_scan(region, limit=FULL_SCAN_ROWS)
    universe = universes.build_universe(page.rows, region=region)
    ranked = ranking.rank_rows(universe, sort)
    displayed = ranking.top_rows(ranked, DISPLAY_ROWS)

    for row in displayed:
        row["instrument_id"] = f"{row['exchange']}:{row['symbol']}"

    screen = await repository.save(
        region=region,
        sort=sort,
        as_of=moment,
        total_count=len(ranked),
        rows=displayed,
        ttl_s=ttl_s,
        now=moment,
    )
    rows = await repository.rows(screen["id"])
    return Snapshot(screen=screen, rows=rows)


async def load_screen(
    region: str,
    sort: str,
    *,
    repository: SqliteScreenRepository,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Serve the fresh snapshot, refresh when expired, fall back to stale."""

    moment = now or datetime.now(UTC)
    fresh = await repository.fresh(region, sort, now=moment)
    if fresh is not None:
        return snapshot_document(
            Snapshot(screen=fresh, rows=await repository.rows(fresh["id"]))
        )

    previous = await repository.latest(region, sort)
    try:
        refreshed = await refresh_screen(region, sort, repository=repository, now=moment)
    except (ScanBlocked, ScanError):
        if previous is None:
            raise
        # Honest staleness: the old rows with their own as_of, marked stale.
        # Never a new timestamp over old prices.
        return snapshot_document(
            Snapshot(
                screen=previous,
                rows=await repository.rows(previous["id"]),
                stale=True,
            )
        )
    return snapshot_document(refreshed)


def next_refresh_at(screen: Mapping[str, Any]) -> str | None:
    """The stored expiry, rendered as-is (one TTL, one rendering)."""

    expires = screen.get("expires_at")
    return str(expires) if expires else None


__all__ = [
    "DISPLAY_ROWS",
    "SNAPSHOT_TTL_S",
    "SOURCE",
    "Snapshot",
    "display_row",
    "load_screen",
    "next_refresh_at",
    "refresh_screen",
    "snapshot_document",
]
