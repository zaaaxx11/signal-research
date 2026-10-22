"""Phase runner with per-phase deadline and checkpoint (02 §3.5)."""

from __future__ import annotations

from typing import Any

from app.jobs.ports import JobRepository


async def run_phases(job_id: str, *, repository: JobRepository) -> dict[str, Any]:
    """Run DEFAULT_PHASES in order; a phase never starts work after its deadline."""

    raise NotImplementedError("enrichment orchestrator is not implemented (Phase 3 RED)")
