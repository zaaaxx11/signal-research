"""Job service: submit / cancel / retry use-cases (02 §3.2)."""

from __future__ import annotations

from typing import Any

from app.contracts import MarketRequest
from app.jobs.ports import JobRecord, JobRepository


async def submit_enrich(
    request: MarketRequest, *, api_key: str, repository: JobRepository
) -> JobRecord:
    """Freeze the request into ``jobs.params`` and return the 202 envelope."""

    raise NotImplementedError("submit_enrich is not implemented (Phase 0 RED)")


async def cancel_job(job_id: str, *, repository: JobRepository) -> JobRecord:
    raise NotImplementedError("cancel_job is not implemented (Phase 0 RED)")


async def retry_job(job_id: str, *, repository: JobRepository) -> JobRecord:
    raise NotImplementedError("retry_job is not implemented (Phase 0 RED)")


async def job_view(job_id: str, *, repository: JobRepository) -> dict[str, Any]:
    raise NotImplementedError("job_view is not implemented (Phase 0 RED)")
