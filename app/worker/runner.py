"""``process_job`` — the ONE execution path (02 §3.1, 05 Langkah 5).

No second pipeline is allowed. The runner builds the orchestrator from
``enrich.phases.DEFAULT_PHASES``; a RED test asserts that the production
entrypoint (not just a unit) constructs it.
"""

from __future__ import annotations

from app.jobs.ports import JobRecord, JobRepository


async def process_job(job_id: str, *, repository: JobRepository) -> JobRecord:
    """Claim, enrich, verify, export, complete. One job, one path."""

    raise NotImplementedError("worker.process_job is not implemented (Phase 0 RED)")
