"""Breaker bookkeeping in ``provider_health`` (02 §4.9).

Why the table and not a dictionary: a second worker must honour a provider that
is rate-limiting us, and a process restart must not erase the fact that a rung
has been failing since morning. The row is keyed ``(provider, capability)``
because Yahoo's chart endpoint can be healthy for QUOTE and broken for OPTIONS.

Fase 2 records **verdicts**, not circuit transitions: ``state`` stays ``closed``
and ``failures`` accumulates. Opening the circuit (``open`` → ``half_open``)
needs a cooldown policy and a request budget to spend on probes; that lands when
the orchestrator can afford it (02 §5 Fase 3). Recording ``last_verdict`` now is
what gate 6 asserts, and it is the half an operator actually reads.

Writes are best-effort **and loud**: a health row that cannot be written must
never fail a job, but it also must not be silent — it is logged on ``mee.*``.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from app.db import ensure_schema_async
from app.providers.base import Verdict

logger = logging.getLogger("mee.providers.health")

#: ``provider_health.state`` vocabulary (02 §4.9). Fase 2 only writes ``closed``.
STATES: tuple[str, ...] = ("closed", "open", "half_open")


class ProviderHealthStore:
    """One row per ``(provider, capability)``: failures, last verdict, last ok."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._ensured = False

    # -- plumbing ----------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _write(self, provider: str, capability: str, verdict: Verdict, at: str) -> None:
        with closing(self._connect()) as connection:
            if verdict.ok:
                connection.execute(
                    """
                    INSERT INTO provider_health
                        (provider, capability, state, failures, opened_at, cooldown_until,
                         last_verdict, last_ok_at)
                    VALUES (:provider, :capability, 'closed', 0, NULL, NULL, :verdict, :at)
                    ON CONFLICT(provider, capability) DO UPDATE SET
                        state = 'closed',
                        failures = 0,
                        opened_at = NULL,
                        cooldown_until = NULL,
                        last_verdict = excluded.last_verdict,
                        last_ok_at = excluded.last_ok_at
                    """,
                    {
                        "provider": provider,
                        "capability": capability,
                        "verdict": verdict.kind,
                        "at": at,
                    },
                )
            else:
                connection.execute(
                    """
                    INSERT INTO provider_health
                        (provider, capability, state, failures, opened_at, cooldown_until,
                         last_verdict, last_ok_at)
                    VALUES (:provider, :capability, 'closed', 1, NULL, NULL, :verdict, NULL)
                    ON CONFLICT(provider, capability) DO UPDATE SET
                        failures = provider_health.failures + 1,
                        last_verdict = excluded.last_verdict,
                        last_ok_at = provider_health.last_ok_at
                    """,
                    {
                        "provider": provider,
                        "capability": capability,
                        "verdict": verdict.kind,
                    },
                )
            connection.commit()

    async def _ensure(self) -> None:
        if not self._ensured:
            await ensure_schema_async(self.db_path)
            self._ensured = True

    # -- api ---------------------------------------------------------------

    async def record(
        self,
        provider: str,
        capability: str,
        verdict: Verdict,
        *,
        at: datetime | None = None,
    ) -> None:
        """Record one content verdict. Never raises: a job is worth more."""

        stamp = (at or datetime.now(UTC)).isoformat(timespec="microseconds")
        try:
            await self._ensure()
            await asyncio.to_thread(self._write, provider, capability, verdict, stamp)
        except (sqlite3.Error, OSError) as exc:  # pragma: no cover - defensive
            logger.warning(
                "provider_health write failed provider=%s capability=%s verdict=%s error=%s",
                provider,
                capability,
                verdict.kind,
                type(exc).__name__,
            )

    async def get(self, provider: str, capability: str) -> dict[str, Any] | None:
        rows = await self.snapshot()
        return rows.get((provider, capability))

    async def snapshot(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Every health row, keyed by ``(provider, capability)``; ``{}`` if none.

        A read must not create a database: ``/providers`` is a GET, and a GET that
        migrates the schema is a surprise. A missing file or table is therefore an
        empty snapshot, not an error.
        """

        def read() -> dict[tuple[str, str], dict[str, Any]]:
            if not self.db_path.exists():
                return {}
            try:
                with closing(self._connect()) as connection:
                    rows = connection.execute("SELECT * FROM provider_health").fetchall()
            except sqlite3.Error:
                return {}
            return {(row["provider"], row["capability"]): dict(row) for row in rows}

        return await asyncio.to_thread(read)

    async def state_of(self, provider: str, capability: str) -> dict[str, Any]:
        """The ``health`` object ``GET /providers`` reports for one rung."""

        row: Mapping[str, Any] | None = (await self.snapshot()).get((provider, capability))
        if row is None:
            return {"state": "unknown", "failures": 0, "last_verdict": None, "last_ok_at": None}
        state = str(row.get("state") or "unknown")
        return {
            "state": state if state in STATES else "unknown",
            "failures": int(row.get("failures") or 0),
            "last_verdict": row.get("last_verdict"),
            "last_ok_at": row.get("last_ok_at"),
        }


__all__ = ["STATES", "ProviderHealthStore"]
