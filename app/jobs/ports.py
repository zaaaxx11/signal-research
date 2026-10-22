"""Ports for the job queue and the quote store (02 §4.1/§4.3).

These Protocols are the seam that keeps a future Postgres/Redis implementation
possible without rewriting the worker. They are signatures only — no logic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping, Protocol, Sequence

#: A job row as a plain mapping, so ports stay storage-agnostic.
JobRecord = dict[str, Any]


class JobRepository(Protocol):
    """CAS claim / lease / reap / terminal transitions."""

    async def create(
        self,
        *,
        params: Mapping[str, Any],
        api_key: str,
        service: str = "market_enrich",
        rows_target: int | None = None,
        retention_days: int = 7,
    ) -> JobRecord: ...

    async def get(self, job_id: str) -> JobRecord | None: ...

    async def claim(self, worker_id: str) -> JobRecord | None:
        """Atomically move one Pending job to Processing and bump attempts."""
        ...

    async def claim_job(self, job_id: str, worker_id: str) -> JobRecord | None:
        """Claim one specific job; ``None`` when another worker won."""
        ...

    async def complete(
        self,
        job_id: str,
        *,
        result_count: int,
        result_mode: str = "full",
        rows_unresolved: int = 0,
        usage: Mapping[str, Any] | None = None,
    ) -> JobRecord: ...

    async def fail(self, job_id: str, *, error: str, retryable: bool) -> JobRecord:
        """Terminal Failure, or requeue to Pending while attempts remain."""
        ...

    async def cancel(self, job_id: str) -> JobRecord:
        """Pending -> Failure; Processing -> set cancel flag; terminal -> 409."""
        ...

    async def retry(self, job_id: str) -> JobRecord:
        """Failure -> Pending with a fresh attempt budget (explicit retry only)."""
        ...

    async def reap_stale_jobs(
        self, *, now: datetime, lease_s: int | None = None
    ) -> list[str]:
        """Return orphaned Processing rows to Pending, or fail exhausted ones."""
        ...

    # -- artifacts written by the worker (02 §4.8) -------------------------

    async def record_artifact(
        self,
        job_id: str,
        *,
        kind: str,
        path: str,
        sha256: str | None,
        row_count: int,
        bytes_: int = 0,
    ) -> Mapping[str, Any]: ...

    async def artifacts(self, job_id: str) -> Sequence[Mapping[str, Any]]: ...

    async def artifact(self, job_id: str, kind: str) -> Mapping[str, Any] | None: ...

    async def set_usage(self, job_id: str, usage: Mapping[str, Any]) -> None: ...


class QuoteRepository(Protocol):
    """Single-use quotes frozen at estimate time."""

    async def create(
        self,
        *,
        api_key: str,
        plan: Mapping[str, Any],
        now: datetime,
        ttl_s: int = 900,
    ) -> dict[str, Any]: ...

    async def consume(
        self, quote_id: str, *, api_key: str, now: datetime
    ) -> dict[str, Any]:
        """CAS ``used_at WHERE used_at IS NULL``; 409 if already used/foreign."""
        ...

    async def attach_job(self, quote_id: str, job_id: str) -> dict[str, Any] | None:
        """Write ``quotes.job_id`` once the job the plan produced exists."""
        ...

    async def release(self, quote_id: str) -> dict[str, Any] | None:
        """Undo a consume when the job could not be created (compensation).

        Idempotent: a quote that is already ``Open`` is left alone. The caller
        invokes it from the failure path of ``create`` so a paid plan is never
        burned without a job (02 §4.3, Fase 2 gate 8).
        """
        ...

    async def close(self) -> None:
        """Release the storage engine held by this repository."""
        ...


class BatchRepository(Protocol):
    """Per-batch checkpoint rows (02 §4.2). Phase 3 deliverable."""

    async def ensure_batches(
        self, job_id: str, batches: Iterable[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Insert the plan, idempotent on ``UNIQUE(job_id, batch_index)``."""
        ...

    async def mark_batch(
        self,
        job_id: str,
        batch_index: int,
        *,
        status: str,
        row_count: int = 0,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Upsert one checkpoint; ``row_count`` is SET, never accumulated."""
        ...

    async def batches(self, job_id: str) -> list[dict[str, Any]]:
        """The plan in ``batch_index`` order."""
        ...


class ArtifactRepository(Protocol):
    """Export artifacts with sha256, unique per (job_id, kind) (02 §4.8)."""

    async def record(
        self, job_id: str, *, kind: str, path: str, sha256: str, row_count: int
    ) -> None: ...


class PhaseReport(Protocol):
    """Shape of a phase entry inside ``jobs.usage``."""

    def as_dict(self) -> Mapping[str, Any]: ...
