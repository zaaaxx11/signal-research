"""SQLite CAS job repository (02 §4.1, 05 §1.3).

RED skeleton: the factory raises ``NotImplementedError`` on purpose. The RED
suite (``tests/test_job_lifecycle.py``) fixes the semantics:

* ``claim`` is a single ``UPDATE ... RETURNING`` CAS on ``status='Pending'``;
* ``attempts`` increases exactly once per claim;
* ``reap_stale_jobs`` returns orphaned ``Processing`` rows to ``Pending`` and
  distinguishes "lease expired" from "attempts exhausted" from "cancel".
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from app.jobs.ports import JobRepository


async def open_repository(
    db_path: str | Path,
    *,
    max_attempts: int = 3,
    lease_s: int = 300,
    clock: Callable[[], datetime] | None = None,
) -> JobRepository:
    """Open (and migrate) the job store for ``db_path``.

    ``clock`` is the lease/reap time source (default: ``datetime.now(UTC)``).
    Tests inject a frozen clock so ``locked_at`` and ``reap`` are deterministic.
    """

    raise NotImplementedError(
        "SQLite CAS job repository is not implemented (Phase 0 RED): "
        f"open_repository({db_path!r}, max_attempts={max_attempts}, lease_s={lease_s})"
    )
