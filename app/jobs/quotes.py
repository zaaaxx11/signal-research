"""Single-use quote store (02 §4.3).

A quote is a frozen plan with an expiry and a ``used_at`` CAS column. It is not
a reservation: consuming it twice must be impossible, and a foreign owner must
get the same 409 as a replay so quote ids cannot be probed.

Check order in :meth:`consume` is deliberate and is not the same as "the
cheapest query first":

1. **ownership** — a quote id that belongs to someone else answers ``409``
   before anything else, otherwise a stranger could learn whether an id exists,
   is used, or is expired;
2. **single use** — ``used_at IS NOT NULL`` is ``409``; a frozen plan is spent
   once, even if the caller retries with the same body;
3. **expiry** — ``410`` only for the owner, and only for an unspent quote,
   because 410 says "this plan existed and is gone".

The actual mutation is one CAS statement (``WHERE used_at IS NULL``); the reads
before it are diagnostics, never the decision.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.contracts import QuotePlanV1
from app.db import build_engine, ensure_schema_async
from app.jobs.errors import QuoteAlreadyUsed, QuoteExpired
from app.jobs.ports import QuoteRepository
from app.jobs.repository import iso

_LOCK_RETRIES = 8
_LOCK_BACKOFF_S = 0.02


class SqliteQuoteRepository(QuoteRepository):
    """Single-use quotes on the same SQLite file as the jobs."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._engine: AsyncEngine | None = None
        self._ready = asyncio.Lock()

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            self._engine = build_engine(self.db_path)
        return self._engine

    async def _ready_engine(self) -> AsyncEngine:
        async with self._ready:
            await ensure_schema_async(self.db_path)
        return self.engine

    async def _one(
        self, sql: str, params: Mapping[str, Any] | None = None
    ) -> dict[str, Any] | None:
        engine = await self._ready_engine()
        for attempt in range(_LOCK_RETRIES):
            try:
                async with engine.begin() as connection:
                    result = await connection.execute(text(sql), dict(params or {}))
                    row = result.mappings().first()
                    return dict(row) if row is not None else None
            except OperationalError as exc:
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                if attempt == _LOCK_RETRIES - 1:
                    raise
                await asyncio.sleep(_LOCK_BACKOFF_S * (attempt + 1))
        return None

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    # -- write -------------------------------------------------------------

    async def create(
        self,
        *,
        api_key: str,
        plan: Mapping[str, Any],
        now: datetime,
        ttl_s: int = 900,
    ) -> dict[str, Any]:
        """Freeze a plan into a quote. Validation happens **before** the insert."""

        if ttl_s <= 0:
            raise ValueError("ttl_s must be > 0")
        frozen = QuotePlanV1.model_validate(plan)
        payload = frozen.model_dump(mode="json")
        expires = now + timedelta(seconds=ttl_s)

        record = await self._one(
            """
            INSERT INTO quotes (id, api_key, plan, status, job_id, created_at, expires_at, used_at)
            VALUES (:id, :api_key, :plan, 'Open', NULL, :created_at, :expires_at, NULL)
            RETURNING *
            """,
            {
                "id": str(uuid.uuid4()),
                "api_key": api_key,
                "plan": json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
                "created_at": iso(now),
                "expires_at": iso(expires),
            },
        )
        if record is None:  # pragma: no cover
            raise QuoteAlreadyUsed("quote insert returned no row")
        return _decoded(record)

    # -- read / consume ----------------------------------------------------

    async def get(self, quote_id: str) -> dict[str, Any] | None:
        record = await self._one("SELECT * FROM quotes WHERE id = :id", {"id": quote_id})
        return _decoded(record) if record is not None else None

    async def consume(self, quote_id: str, *, api_key: str, now: datetime) -> dict[str, Any]:
        """CAS ``used_at`` and return the frozen plan; 409 / 410 otherwise."""

        current = await self.get(quote_id)
        if current is None:
            # Same answer as "already used": an unknown id must not be an oracle.
            raise QuoteAlreadyUsed(f"quote {quote_id} is not usable")
        if current["api_key"] != api_key:
            raise QuoteAlreadyUsed(f"quote {quote_id} is not usable")
        if current["used_at"] is not None:
            raise QuoteAlreadyUsed(f"quote {quote_id} was already used")
        if now > datetime.fromisoformat(current["expires_at"]):
            raise QuoteExpired(f"quote {quote_id} expired at {current['expires_at']}")

        consumed = await self._one(
            """
            UPDATE quotes
               SET used_at = :now, status = 'Used'
             WHERE id = :id AND api_key = :api_key AND used_at IS NULL
            RETURNING *
            """,
            {"id": quote_id, "api_key": api_key, "now": iso(now)},
        )
        if consumed is None:
            # Lost the race to another consumer: that is a replay, not a 410.
            raise QuoteAlreadyUsed(f"quote {quote_id} was already used")
        return _decoded(consumed)


def _decoded(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy with ``plan`` as a dict (storage keeps it as JSON text)."""

    decoded = dict(record)
    plan = decoded.get("plan")
    if isinstance(plan, str):
        decoded["plan"] = json.loads(plan)
    return decoded


async def open_quote_repository(db_path: str | Path) -> QuoteRepository:
    """Open the quote store for ``db_path`` (schema applied on first use)."""

    return SqliteQuoteRepository(db_path)


__all__ = ["SqliteQuoteRepository", "open_quote_repository"]
