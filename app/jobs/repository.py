"""SQLite CAS job repository (02 §4.1, 05 §1.3).

The queue is one table. There is no broker, so the four guarantees the worker
needs are expressed as statements, not as framework configuration:

* **exclusive claim** — a single ``UPDATE ... WHERE status='Pending'
  RETURNING *``; ``attempts`` is incremented by that same statement, so "claimed
  twice" and "counted twice" are the same failure and cannot diverge;
* **retryable failure requeues**, non-retryable failure is terminal, and
  exhausted attempts become terminal Failure **with the attempt count in the
  error** (an operator must be able to tell "gave up" from "provider said no");
* **cancel** is immediate for ``Pending`` and a flag for ``Processing`` — a
  running job is only stopped at a safe boundary, never mid-write;
* **reap** returns orphaned ``Processing`` rows to ``Pending`` and
  distinguishes *lease expired* from *attempts exhausted* from *cancel pending*.
  A reap is not a new attempt.

Timestamp columns are ISO-8601 UTC text, so ``locked_at < :cutoff`` is a lexical
comparison that is also a chronological one.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import build_engine, ensure_schema_async
from app.jobs.errors import JobError, JobNotFound, JobTerminal
from app.jobs.ports import JobRepository

PENDING = "Pending"
PROCESSING = "Processing"
SUCCESS = "Success"
FAILURE = "Failure"
TERMINAL = frozenset({SUCCESS, FAILURE})

#: Batch checkpoint states (02 §4.2). ``Skipped`` is deliberate, not an error.
BATCH_PENDING = "Pending"
BATCH_PROCESSING = "Processing"
BATCH_SUCCESS = "Success"
BATCH_FAILURE = "Failure"
BATCH_SKIPPED = "Skipped"
BATCH_STATUSES = frozenset(
    {BATCH_PENDING, BATCH_PROCESSING, BATCH_SUCCESS, BATCH_FAILURE, BATCH_SKIPPED}
)
BATCH_TERMINAL = frozenset({BATCH_SUCCESS, BATCH_FAILURE, BATCH_SKIPPED})

#: SQLite is single-writer: a second connection waits, and if the wait is
#: longer than ``busy_timeout`` we retry instead of failing the job.
_LOCK_RETRIES = 8
_LOCK_BACKOFF_S = 0.02


def utc_now() -> datetime:
    """Default clock: aware UTC, never naive."""

    return datetime.now(UTC)


def iso(moment: datetime) -> str:
    """Serialise a datetime so lexical order equals chronological order."""

    if moment.tzinfo is None:
        raise ValueError("naive datetimes are not allowed in the job store")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _dumps(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str)


class SqliteJobRepository(JobRepository):
    """Job store on one SQLite file (WAL, busy_timeout, FK on)."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        max_attempts: int = 3,
        lease_s: int = 300,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if lease_s <= 0:
            raise ValueError("lease_s must be > 0")
        self.db_path = Path(db_path)
        self.max_attempts = max_attempts
        self.lease_s = lease_s
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

    async def _one(self, sql: str, params: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        """Run one statement, returning the first row as a plain dict."""

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

    async def _all(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        engine = await self._ready_engine()
        for attempt in range(_LOCK_RETRIES):
            try:
                async with engine.begin() as connection:
                    result = await connection.execute(text(sql), dict(params or {}))
                    return [dict(row) for row in result.mappings().all()]
            except OperationalError as exc:
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                if attempt == _LOCK_RETRIES - 1:
                    raise
                await asyncio.sleep(_LOCK_BACKOFF_S * (attempt + 1))
        return []

    def _now_iso(self) -> str:
        return iso(self._clock())

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    # -- create / read -----------------------------------------------------

    async def create(
        self,
        *,
        params: Mapping[str, Any],
        api_key: str,
        service: str = "market_enrich",
        rows_target: int | None = None,
        retention_days: int = 7,
        expires_at: datetime | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert a ``Pending`` job with frozen params. Never trusts the caller's id."""

        now = self._clock()
        expires = expires_at or (now + timedelta(days=retention_days))
        record = await self._one(
            """
            INSERT INTO jobs (
                id, api_key, service, params, status, attempts, worker_id, locked_at,
                started_at, finished_at, cancel_requested, result_count, rows_target,
                rows_unresolved, result_mode, error, usage, expires_at, created_at, updated_at
            ) VALUES (
                :id, :api_key, :service, :params, 'Pending', 0, NULL, NULL,
                NULL, NULL, 0, 0, :rows_target,
                0, NULL, NULL, NULL, :expires_at, :now, :now
            )
            RETURNING *
            """,
            {
                "id": job_id or str(uuid.uuid4()),
                "api_key": api_key,
                "service": service,
                "params": _dumps(params),
                "rows_target": rows_target,
                "expires_at": iso(expires),
                "now": iso(now),
            },
        )
        if record is None:  # pragma: no cover - INSERT ... RETURNING always returns
            raise JobError("job insert returned no row")
        return record

    async def get(self, job_id: str) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM jobs WHERE id = :id", {"id": job_id})

    async def list_jobs(self, *, api_key: str, limit: int = 50) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT * FROM jobs WHERE api_key = :api_key ORDER BY created_at DESC LIMIT :limit",
            {"api_key": api_key, "limit": limit},
        )

    # -- transitions -------------------------------------------------------

    async def claim(self, worker_id: str) -> dict[str, Any] | None:
        """Atomically move the oldest ``Pending`` job to ``Processing``."""

        return await self._one(
            """
            UPDATE jobs
               SET status = 'Processing',
                   worker_id = :worker_id,
                   locked_at = :now,
                   started_at = COALESCE(started_at, :now),
                   attempts = attempts + 1,
                   updated_at = :now
             WHERE id = (
                     SELECT id FROM jobs WHERE status = 'Pending'
                     ORDER BY created_at, id LIMIT 1
                   )
               AND status = 'Pending'
            RETURNING *
            """,
            {"worker_id": worker_id, "now": self._now_iso()},
        )

    async def claim_job(self, job_id: str, worker_id: str) -> dict[str, Any] | None:
        """Claim one *specific* job (used by the in-process runner)."""

        return await self._one(
            """
            UPDATE jobs
               SET status = 'Processing',
                   worker_id = :worker_id,
                   locked_at = :now,
                   started_at = COALESCE(started_at, :now),
                   attempts = attempts + 1,
                   updated_at = :now
             WHERE id = :id AND status = 'Pending'
            RETURNING *
            """,
            {"id": job_id, "worker_id": worker_id, "now": self._now_iso()},
        )

    async def complete(
        self,
        job_id: str,
        *,
        result_count: int,
        result_mode: str = "full",
        rows_unresolved: int = 0,
        usage: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = await self._one(
            """
            UPDATE jobs
               SET status = 'Success',
                   result_count = :result_count,
                   result_mode = :result_mode,
                   rows_unresolved = :rows_unresolved,
                   usage = COALESCE(:usage, usage),
                   error = NULL,
                   finished_at = :now,
                   updated_at = :now
             WHERE id = :id AND status = 'Processing'
            RETURNING *
            """,
            {
                "id": job_id,
                "result_count": result_count,
                "result_mode": result_mode,
                "rows_unresolved": rows_unresolved,
                "usage": _dumps(usage) if usage is not None else None,
                "now": self._now_iso(),
            },
        )
        return await self._require(record, job_id, "complete")

    async def fail(self, job_id: str, *, error: str, retryable: bool) -> dict[str, Any]:
        """Terminal Failure, or requeue to Pending while attempts remain.

        A pending cancel always wins: the job fails at this safe boundary with
        the cancel reason instead of being resurrected for another attempt.
        """

        now = self._now_iso()
        record = await self._one(
            """
            UPDATE jobs
               SET status = CASE
                       WHEN cancel_requested = 1 THEN 'Failure'
                       WHEN :retryable = 1 AND attempts < :max_attempts THEN 'Pending'
                       ELSE 'Failure'
                   END,
                   error = CASE
                       WHEN cancel_requested = 1 THEN :error
                       WHEN :retryable = 1 AND attempts < :max_attempts THEN :error
                       WHEN :retryable = 1 THEN :error || ' (attempts exhausted: '
                            || attempts || '/' || :max_attempts || ')'
                       ELSE :error
                   END,
                   worker_id = CASE
                       WHEN cancel_requested = 0 AND :retryable = 1
                            AND attempts < :max_attempts THEN NULL
                       ELSE worker_id
                   END,
                   locked_at = CASE
                       WHEN cancel_requested = 0 AND :retryable = 1
                            AND attempts < :max_attempts THEN NULL
                       ELSE locked_at
                   END,
                   finished_at = CASE
                       WHEN cancel_requested = 0 AND :retryable = 1
                            AND attempts < :max_attempts THEN NULL
                       ELSE :now
                   END,
                   updated_at = :now
             WHERE id = :id AND status = 'Processing'
            RETURNING *
            """,
            {
                "id": job_id,
                "error": error,
                "retryable": 1 if retryable else 0,
                "max_attempts": self.max_attempts,
                "now": now,
            },
        )
        return await self._require(record, job_id, "fail")

    async def cancel(self, job_id: str) -> dict[str, Any]:
        """Pending -> immediate Failure; Processing -> flag only; terminal -> 409."""

        now = self._now_iso()
        record = await self._one(
            """
            UPDATE jobs
               SET status = CASE WHEN status = 'Pending' THEN 'Failure' ELSE status END,
                   error = CASE WHEN status = 'Pending' THEN 'cancelled by client' ELSE error END,
                   finished_at = CASE WHEN status = 'Pending' THEN :now ELSE finished_at END,
                   cancel_requested = 1,
                   updated_at = :now
             WHERE id = :id AND status IN ('Pending', 'Processing')
            RETURNING *
            """,
            {"id": job_id, "now": now},
        )
        if record is not None:
            return record

        current = await self.get(job_id)
        if current is None:
            raise JobNotFound(f"job {job_id} does not exist")
        raise JobTerminal(f"job {job_id} is {current['status']}; cancel is not allowed")

    async def reap_stale_jobs(
        self, *, now: datetime, lease_s: int | None = None
    ) -> list[str]:
        """Return orphaned ``Processing`` rows to ``Pending``; fail the rest."""

        lease = self.lease_s if lease_s is None else lease_s
        cutoff = iso(now - timedelta(seconds=lease))
        stamp = iso(now)
        engine = await self._ready_engine()

        async with engine.begin() as connection:
            stale = (
                await connection.execute(
                    text(
                        """
                        SELECT * FROM jobs
                         WHERE status = 'Processing'
                           AND locked_at IS NOT NULL
                           AND locked_at < :cutoff
                         ORDER BY locked_at, id
                        """
                    ),
                    {"cutoff": cutoff},
                )
            ).mappings().all()

            reaped: list[str] = []
            for row in stale:
                job_id = row["id"]
                if row["cancel_requested"]:
                    # A cancel is not "attempts exhausted": keep the reason exact.
                    await connection.execute(
                        text(
                            """
                            UPDATE jobs
                               SET status = 'Failure', error = 'cancelled by client',
                                   finished_at = :now, updated_at = :now
                             WHERE id = :id AND status = 'Processing'
                            """
                        ),
                        {"id": job_id, "now": stamp},
                    )
                elif row["attempts"] >= self.max_attempts:
                    await connection.execute(
                        text(
                            """
                            UPDATE jobs
                               SET status = 'Failure',
                                   error = 'lease expired (attempts exhausted: '
                                           || attempts || '/' || :max_attempts || ')',
                                   finished_at = :now, updated_at = :now
                             WHERE id = :id AND status = 'Processing'
                            """
                        ),
                        {"id": job_id, "now": stamp, "max_attempts": self.max_attempts},
                    )
                else:
                    await connection.execute(
                        text(
                            """
                            UPDATE jobs
                               SET status = 'Pending', worker_id = NULL, locked_at = NULL,
                                   cancel_requested = 0, updated_at = :now
                             WHERE id = :id AND status = 'Processing'
                            """
                        ),
                        {"id": job_id, "now": stamp},
                    )
                reaped.append(job_id)
            return reaped

    async def retry(self, job_id: str) -> dict[str, Any]:
        """Reset a ``Failure`` to ``Pending`` with a fresh attempt budget.

        Only an explicit client retry does this: a *retryable* failure requeues
        without resetting ``attempts`` (otherwise "attempts exhausted" could
        never be reached). The caller decides, the store does not guess.
        """

        record = await self._one(
            """
            UPDATE jobs
               SET status = 'Pending',
                   attempts = 0,
                   worker_id = NULL,
                   locked_at = NULL,
                   started_at = NULL,
                   finished_at = NULL,
                   cancel_requested = 0,
                   result_count = 0,
                   rows_unresolved = 0,
                   result_mode = NULL,
                   error = NULL,
                   updated_at = :now
             WHERE id = :id AND status = 'Failure'
            RETURNING *
            """,
            {"id": job_id, "now": self._now_iso()},
        )
        return await self._require(record, job_id, "retry")

    async def _require(
        self, record: dict[str, Any] | None, job_id: str, action: str
    ) -> dict[str, Any]:
        """Turn "0 rows updated" into the right typed error."""

        if record is not None:
            return record
        current = await self.get(job_id)
        if current is None:
            raise JobNotFound(f"job {job_id} does not exist")
        raise JobTerminal(f"cannot {action} job {job_id} in status {current['status']}")

    # -- auxiliary writers -------------------------------------------------

    async def set_usage(self, job_id: str, usage: Mapping[str, Any]) -> None:
        # Not ``_one``: a plain UPDATE returns no rows, and ``.mappings()`` on a
        # closed CursorResult raises. The statement is the whole contract here.
        engine = await self._ready_engine()
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE jobs SET usage = :usage, updated_at = :now WHERE id = :id"),
                {"id": job_id, "usage": _dumps(usage), "now": self._now_iso()},
            )

    async def record_rejections(
        self, job_id: str, rejections: Iterable[Mapping[str, Any]]
    ) -> int:
        """Replace this job's ``verification_rejections`` rows; return the count.

        **Set** semantics, not append: a retry re-verifies the same job and must
        not stack a second copy of every rejection. The table's column names are
        frozen by 02 §4.7 (``field``/``detail``); the verifier's ``column``/
        ``reason`` spelling is accepted on the way in and stored under the
        frozen names, so no third spelling ever appears in the database.
        """

        payload: list[dict[str, Any]] = []
        now = self._now_iso()
        for item in rejections:
            if not isinstance(item, Mapping):
                raise ValueError(f"a rejection must be a mapping, got {type(item).__name__}")
            column = item.get("column") or item.get("field")
            reason = item.get("reason") or item.get("detail")
            if not isinstance(column, str) or not column.strip():
                raise ValueError(f"rejection without a column: {dict(item)!r}")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"rejection without a reason: {dict(item)!r}")
            detail = str(item.get("detail") or reason).strip()
            if not detail.startswith(reason.strip()):
                detail = f"{reason.strip()}: {detail}"
            value_text = item.get("value_text")
            payload.append(
                {
                    "job_id": job_id,
                    "instrument_id": str(item.get("instrument_id") or ""),
                    "field": column.strip(),
                    "rule": str(item.get("rule") or "").strip(),
                    "value_text": None if value_text is None else str(value_text),
                    "detail": detail,
                    "created_at": now,
                }
            )

        engine = await self._ready_engine()
        for attempt in range(_LOCK_RETRIES):
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text("DELETE FROM verification_rejections WHERE job_id = :job_id"),
                        {"job_id": job_id},
                    )
                    for row in payload:
                        await connection.execute(
                            text(
                                """
                                INSERT INTO verification_rejections (
                                    job_id, instrument_id, field, rule, value_text, detail, created_at
                                ) VALUES (
                                    :job_id, :instrument_id, :field, :rule, :value_text, :detail, :created_at
                                )
                                """
                            ),
                            row,
                        )
                return len(payload)
            except OperationalError as exc:
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                if attempt == _LOCK_RETRIES - 1:
                    raise
                await asyncio.sleep(_LOCK_BACKOFF_S * (attempt + 1))
        return 0  # pragma: no cover - the loop returns or raises

    async def record_artifact(
        self,
        job_id: str,
        *,
        kind: str,
        path: str | Path,
        sha256: str | None,
        row_count: int,
        bytes_: int = 0,
    ) -> dict[str, Any]:
        """Upsert one artifact; ``UNIQUE(job_id, kind)`` replaces, never stacks."""

        record = await self._one(
            """
            INSERT INTO job_artifacts (id, job_id, kind, path, bytes, row_count, sha256, created_at)
            VALUES (:id, :job_id, :kind, :path, :bytes, :row_count, :sha256, :now)
            ON CONFLICT(job_id, kind) DO UPDATE SET
                path = excluded.path,
                bytes = excluded.bytes,
                row_count = excluded.row_count,
                sha256 = excluded.sha256,
                created_at = excluded.created_at
            RETURNING *
            """,
            {
                "id": str(uuid.uuid4()),
                "job_id": job_id,
                "kind": kind,
                "path": str(path),
                "bytes": bytes_,
                "row_count": row_count,
                "sha256": sha256,
                "now": self._now_iso(),
            },
        )
        if record is None:  # pragma: no cover
            raise JobError("artifact insert returned no row")
        return record

    async def artifacts(self, job_id: str) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT * FROM job_artifacts WHERE job_id = :id ORDER BY kind", {"id": job_id}
        )

    async def artifact(self, job_id: str, kind: str) -> dict[str, Any] | None:
        return await self._one(
            "SELECT * FROM job_artifacts WHERE job_id = :id AND kind = :kind",
            {"id": job_id, "kind": kind},
        )

    # -- retention (02 §4.8, Fase 5) ---------------------------------------

    async def expired_jobs(
        self, now: datetime, retention_days: int = 30
    ) -> list[dict[str, Any]]:
        """Jobs that may be purged: expired **and** older than the day floor.

        The two conditions answer different questions. ``expires_at`` says the
        job may no longer be served (downloads already answer ``410``); the
        floor says the deployment has kept the data long enough to let go of it.
        Dropping either one deletes something a caller still expects:

        * expiry alone destroys a seven-day job on day eight, even though the
          default policy is thirty days;
        * the floor alone destroys a job whose expiry was deliberately extended.

        ISO-8601 UTC text columns make ``<=`` both a lexical and a chronological
        comparison (``iso`` always writes microseconds and an offset).
        """

        if now.tzinfo is None:
            raise ValueError("naive datetimes are not allowed in retention")
        if (
            isinstance(retention_days, bool)
            or not isinstance(retention_days, int)
            or retention_days < 0
        ):
            raise ValueError(f"retention_days must be an int >= 0, got {retention_days!r}")
        floor = iso(now - timedelta(days=retention_days))
        return await self._all(
            """
            SELECT * FROM jobs
             WHERE expires_at IS NOT NULL
               AND expires_at <= :now
               AND created_at <= :floor
             ORDER BY created_at, id
            """,
            {"now": iso(now), "floor": floor},
        )

    async def all_artifacts(self) -> list[dict[str, Any]]:
        """Every artifact row (the sweeper validates its blast radius first)."""

        return await self._all("SELECT * FROM job_artifacts ORDER BY job_id, kind")

    async def purge_jobs(self, job_ids: Iterable[str]) -> int:
        """Delete the artifacts, rejections, batches and job rows in **one** transaction.

        ``observations`` is deliberately untouched: it is append-only with a
        separate retention policy (02 §4.6) and its ``job_id`` FK is
        ``ON DELETE SET NULL``, so the measurement survives the job that
        fetched it. A partial purge is not a state this method can return.
        """

        ids = [str(job_id) for job_id in job_ids]
        if not ids:
            return 0

        engine = await self._ready_engine()
        for attempt in range(_LOCK_RETRIES):
            try:
                async with engine.begin() as connection:
                    for job_id in ids:
                        for table in ("job_artifacts", "verification_rejections", "job_batches"):
                            await connection.execute(
                                text(f"DELETE FROM {table} WHERE job_id = :job_id"),
                                {"job_id": job_id},
                            )
                    deleted = 0
                    for job_id in ids:
                        result = await connection.execute(
                            text("DELETE FROM jobs WHERE id = :job_id"), {"job_id": job_id}
                        )
                        deleted += int(result.rowcount or 0)
                return deleted
            except OperationalError as exc:
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                if attempt == _LOCK_RETRIES - 1:
                    raise
                await asyncio.sleep(_LOCK_BACKOFF_S * (attempt + 1))
        return 0  # pragma: no cover - the loop returns or raises

    # -- batch checkpoints (02 §4.2, Fase 3) -------------------------------

    async def ensure_batches(
        self, job_id: str, batches: Iterable[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Insert the batch plan, idempotent on ``UNIQUE(job_id, batch_index)``.

        A retry re-uses the same checkpoint rows instead of stacking a second
        plan beside the first: ``Replaying a batch must not add a row`` is a
        property of the store, not of the caller's discipline.
        """

        plan = [self._batch_payload(job_id, batch) for batch in batches]
        if plan:
            engine = await self._ready_engine()
            async with engine.begin() as connection:
                for payload in plan:
                    await connection.execute(
                        text(
                            """
                            INSERT INTO job_batches (
                                id, job_id, batch_index, universe_slice,
                                symbols_expected, status, row_count
                            ) VALUES (
                                :id, :job_id, :batch_index, :universe_slice,
                                :symbols_expected, 'Pending', 0
                            )
                            ON CONFLICT(job_id, batch_index) DO NOTHING
                            """
                        ),
                        payload,
                    )
        return await self.batches(job_id)

    async def mark_batch(
        self,
        job_id: str,
        batch_index: int,
        *,
        status: str,
        row_count: int = 0,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Upsert one checkpoint with **SET** semantics.

        ``row_count`` is the number of unique rows the last execution
        contributed — never a running total. An accumulator would turn a retry
        into double counting, and a ``row_count`` that grows on replay makes the
        idempotency gate unobservable.
        """

        if status not in BATCH_STATUSES:
            raise ValueError(
                f"unknown batch status {status!r}; expected one of {sorted(BATCH_STATUSES)}"
            )
        if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0:
            raise ValueError(f"row_count must be an int >= 0, got {row_count!r}")

        record = await self._one(
            """
            UPDATE job_batches
               SET status = :status,
                   row_count = :row_count,
                   error = :error,
                   started_at = COALESCE(started_at, :now),
                   finished_at = CASE
                       WHEN :status IN ('Success', 'Failure', 'Skipped') THEN :now
                       ELSE finished_at
                   END
             WHERE job_id = :job_id AND batch_index = :batch_index
            RETURNING *
            """,
            {
                "job_id": job_id,
                "batch_index": int(batch_index),
                "status": status,
                "row_count": int(row_count),
                "error": error,
                "now": self._now_iso(),
            },
        )
        if record is None:
            raise JobNotFound(f"job {job_id} has no batch {batch_index}")
        return record

    async def batches(self, job_id: str) -> list[dict[str, Any]]:
        """The batch plan in execution order (``batch_index`` ascending)."""

        return await self._all(
            "SELECT * FROM job_batches WHERE job_id = :id ORDER BY batch_index",
            {"id": job_id},
        )

    @staticmethod
    def _batch_payload(job_id: str, batch: Mapping[str, Any]) -> dict[str, Any]:
        """Normalise one plan entry into an ``INSERT`` payload.

        Accepts either the plan shape (``universe_slice``) or the compact test
        shape (``symbols``), and derives ``symbols_expected`` when it is absent
        so the two forms cannot disagree about how many symbols a batch covers.
        """

        index = batch.get("batch_index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise ValueError(f"batch_index must be an int >= 0, got {index!r}")

        universe = batch.get("universe_slice")
        symbols = batch.get("symbols")
        if universe is None:
            universe = {"symbols": list(symbols or [])}
        if symbols is None and isinstance(universe, Mapping):
            symbols = universe.get("symbols") or []

        expected = batch.get("symbols_expected")
        if expected is None:
            expected = len(symbols or [])
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
            raise ValueError(f"symbols_expected must be an int >= 0, got {expected!r}")

        return {
            "id": str(uuid.uuid4()),
            "job_id": job_id,
            "batch_index": index,
            "universe_slice": _dumps(universe),
            "symbols_expected": expected,
        }


async def open_repository(
    db_path: str | Path,
    *,
    max_attempts: int = 3,
    lease_s: int = 300,
    clock: Callable[[], datetime] | None = None,
) -> JobRepository:
    """Open the job store for ``db_path``.

    ``clock`` is the lease/reap time source (default: ``datetime.now(UTC)``).
    Tests inject a frozen clock so ``locked_at`` and ``reap`` are deterministic.
    The schema is applied on first use (``app.db.ensure_schema``).
    """

    return SqliteJobRepository(
        db_path, max_attempts=max_attempts, lease_s=lease_s, clock=clock
    )


def parse_record_times(record: Mapping[str, Any]) -> dict[str, datetime | None]:
    """Decode the timestamp columns of a job row (for callers that need datetimes)."""

    return {
        name: _parse(record.get(name))
        for name in ("locked_at", "started_at", "finished_at", "expires_at", "created_at", "updated_at")
    }


__all__ = [
    "BATCH_FAILURE",
    "BATCH_PENDING",
    "BATCH_PROCESSING",
    "BATCH_SKIPPED",
    "BATCH_STATUSES",
    "BATCH_SUCCESS",
    "BATCH_TERMINAL",
    "FAILURE",
    "PENDING",
    "PROCESSING",
    "SUCCESS",
    "TERMINAL",
    "SqliteJobRepository",
    "iso",
    "open_repository",
    "parse_record_times",
    "utc_now",
]
