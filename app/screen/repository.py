"""``screens`` + ``screen_rows``: one snapshot, twenty displayed rows.

The storage decision is the product decision (22-screen-red.md §2):

* a snapshot lives ``SNAPSHOT_TTL_S`` seconds and is identified by
  ``(region, sort)``; two reads inside the window must return the **same**
  ``screen_id`` and send **zero** provider requests;
* ``screens.total_count`` remembers the size of the post-filter/post-dedupe
  universe, while ``screen_rows`` holds only the twenty rows on screen. A
  display that says ``total=23`` and serves 20 rows is honest; a table that
  stored 23 rows would make the export's on-demand fetch indistinguishable from
  a recycled display (and would grow to 81k rows per region every five
  minutes);
* the export's ``all`` scope therefore re-scans on demand instead of reading
  this table — see :mod:`app.screen.export`.

Timestamps are ISO-8601 UTC text with microseconds, exactly like the job store,
so ``expires_at > :now`` is both a lexical and a chronological comparison (the
TTL test ages a snapshot by writing those two columns directly).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import build_engine, ensure_schema_async

#: Snapshots this layer writes. The TTL is imported from the snapshot module by
#: callers; the default here matches the pinned 300 s contract.
SNAPSHOT_TTL_S = 300
SOURCE = "tradingview"

#: Columns of ``screen_rows``, in insert order. ``rank`` is the primary key's
#: second half, so ordering a read is a property of the schema.
ROW_COLUMNS: tuple[str, ...] = (
    "screen_id",
    "rank",
    "instrument_id",
    "symbol",
    "name",
    "exchange",
    "country",
    "sector",
    "price",
    "change_pct",
    "volume",
    "turnover",
    "turnover_usd",
    "market_cap_usd",
    "is_primary",
)

#: SQLite is single-writer; a second connection waits, and a wait longer than
#: ``busy_timeout`` is retried rather than surfaced as a failed request.
_LOCK_RETRIES = 8
_LOCK_BACKOFF_S = 0.02


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(moment: datetime) -> str:
    """Aware UTC with microseconds: lexical order equals chronological order."""

    if moment.tzinfo is None:
        raise ValueError("naive datetimes are not allowed in the screen store")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def screen_id(*, region: str, sort: str, now: datetime) -> str:
    """A sortable, collision-free id: ``scr_<region>_<stamp>_<rand>``.

    The region and the sort are legible in the id (an operator reading a log or
    a bundle directory should not have to join a table to know what it is), and
    the random tail keeps two refreshes in the same microsecond apart.
    """

    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%S%f")
    return f"scr_{region}_{sort}_{stamp}_{uuid.uuid4().hex[:8]}"


def filters_hash(*, region: str, sort: str) -> str:
    """The frozen filter identity of a snapshot (region + sort + rule version).

    A snapshot is only reusable for the exact same question; hashing the recipe
    keeps two different filters from sharing a row when the rule set grows.
    """

    return f"{region}:{sort}:v1"


class SqliteScreenRepository:
    """Screen snapshots on the same SQLite file as the job store."""

    def __init__(
        self, db_path: str | Path, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.db_path = Path(db_path)
        self._clock = clock or utc_now
        self._engine: AsyncEngine | None = None
        self._ready = asyncio.Lock()

    # -- infrastructure ----------------------------------------------------

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            self._engine = build_engine(self.db_path)
        return self._engine

    async def _ready_engine(self) -> AsyncEngine:
        async with self._ready:
            await ensure_schema_async(self.db_path)
        return self.engine

    async def _all(
        self, sql: str, params: Mapping[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        engine = await self._ready_engine()
        for attempt in range(_LOCK_RETRIES):
            try:
                async with engine.begin() as connection:
                    result = await connection.execute(text(sql), dict(params or {}))
                    return [dict(row) for row in result.mappings().all()]
            except OperationalError as exc:
                if not _is_lock_error(exc):
                    raise
                if attempt == _LOCK_RETRIES - 1:
                    raise
                await asyncio.sleep(_LOCK_BACKOFF_S * (attempt + 1))
        return []  # pragma: no cover - the loop returns or raises

    async def _one(
        self, sql: str, params: Mapping[str, Any] | None = None
    ) -> dict[str, Any] | None:
        rows = await self._all(sql, params)
        return rows[0] if rows else None

    async def aclose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    # -- writes ------------------------------------------------------------

    async def save(
        self,
        *,
        region: str,
        sort: str,
        as_of: datetime,
        total_count: int,
        rows: Sequence[Mapping[str, Any]],
        source: str = SOURCE,
        ttl_s: int = SNAPSHOT_TTL_S,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Insert one snapshot and its displayed rows in a single transaction.

        All-or-nothing matters: a ``screens`` row whose ``screen_rows`` are
        missing would render an empty page that claims ``total=23``.
        """

        moment = now or self._clock()
        created = iso(moment)
        expires = iso(moment + timedelta(seconds=ttl_s))
        identifier = screen_id(region=region, sort=sort, now=moment)
        record = {
            "id": identifier,
            "region": region,
            "sort": sort,
            "filters_hash": filters_hash(region=region, sort=sort),
            "as_of": iso(as_of),
            "total_count": int(total_count),
            "source": source,
            "created_at": created,
            "expires_at": expires,
        }

        engine = await self._ready_engine()
        for attempt in range(_LOCK_RETRIES):
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            """
                            INSERT INTO screens (
                                id, region, sort, filters_hash, as_of, total_count,
                                source, created_at, expires_at
                            ) VALUES (
                                :id, :region, :sort, :filters_hash, :as_of, :total_count,
                                :source, :created_at, :expires_at
                            )
                            """
                        ),
                        record,
                    )
                    for row in rows:
                        await connection.execute(
                            text(
                                """
                                INSERT INTO screen_rows (
                                    screen_id, rank, instrument_id, symbol, name, exchange,
                                    country, sector, price, change_pct, volume, turnover,
                                    turnover_usd, market_cap_usd, is_primary
                                ) VALUES (
                                    :screen_id, :rank, :instrument_id, :symbol, :name, :exchange,
                                    :country, :sector, :price, :change_pct, :volume, :turnover,
                                    :turnover_usd, :market_cap_usd, :is_primary
                                )
                                """
                            ),
                            _row_params(identifier, row),
                        )
                return record
            except OperationalError as exc:
                if not _is_lock_error(exc):
                    raise
                if attempt == _LOCK_RETRIES - 1:
                    raise
                await asyncio.sleep(_LOCK_BACKOFF_S * (attempt + 1))
        raise AssertionError("unreachable")  # pragma: no cover

    # -- reads -------------------------------------------------------------

    async def fresh(
        self, region: str, sort: str, *, now: datetime | None = None
    ) -> dict[str, Any] | None:
        """The snapshot still inside its window, newest first, or ``None``."""

        moment = now or self._clock()
        return await self._one(
            """
            SELECT * FROM screens
             WHERE region = :region AND sort = :sort AND expires_at > :now
             ORDER BY created_at DESC, id DESC
             LIMIT 1
            """,
            {"region": region, "sort": sort, "now": iso(moment)},
        )

    async def latest(self, region: str, sort: str) -> dict[str, Any] | None:
        """The newest snapshot whatever its age (the stale-fallback candidate)."""

        return await self._one(
            """
            SELECT * FROM screens
             WHERE region = :region AND sort = :sort
             ORDER BY created_at DESC, id DESC
             LIMIT 1
            """,
            {"region": region, "sort": sort},
        )

    async def get(self, identifier: str) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM screens WHERE id = :id", {"id": identifier})

    async def rows(self, identifier: str) -> list[dict[str, Any]]:
        """The displayed rows, ordered by rank (the 20 rows, never the universe)."""

        return await self._all(
            "SELECT * FROM screen_rows WHERE screen_id = :id ORDER BY rank",
            {"id": identifier},
        )

    async def expired(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        """Snapshots past their window (a sweeper's candidate list)."""

        return await self._all(
            "SELECT * FROM screens WHERE expires_at <= :now ORDER BY created_at",
            {"now": iso(now or self._clock())},
        )

    async def purge(self, identifiers: Iterable[str]) -> int:
        """Delete snapshots and their rows; returns the number of snapshots gone."""

        ids = [str(identifier) for identifier in identifiers]
        if not ids:
            return 0
        engine = await self._ready_engine()
        deleted = 0
        async with engine.begin() as connection:
            for identifier in ids:
                await connection.execute(
                    text("DELETE FROM screen_rows WHERE screen_id = :id"), {"id": identifier}
                )
                result = await connection.execute(
                    text("DELETE FROM screens WHERE id = :id"), {"id": identifier}
                )
                deleted += int(result.rowcount or 0)
        return deleted


def _row_params(identifier: str, row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "screen_id": identifier,
        "rank": int(row.get("rank") or 0),
        "instrument_id": row.get("instrument_id"),
        "symbol": str(row.get("symbol") or ""),
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
        "is_primary": 1 if row.get("is_primary") else 0,
    }


def _is_lock_error(exc: OperationalError) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message


__all__ = [
    "ROW_COLUMNS",
    "SNAPSHOT_TTL_S",
    "SOURCE",
    "SqliteScreenRepository",
    "filters_hash",
    "iso",
    "screen_id",
    "utc_now",
]
