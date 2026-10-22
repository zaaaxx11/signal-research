"""Phase-0 job lifecycle RED suite (02 §3.9, §5 gate 3; 05 §1.3).

Semantics pinned here:

* ``Pending -> Processing -> Success`` with ``attempts`` incremented exactly
  once per claim and an exclusive claim (one job is never executed twice);
* non-retryable failure is terminal; retryable failure requeues until attempts
  are exhausted, then becomes a terminal Failure;
* cancel is immediate for Pending and a flag for Processing; terminal -> 409;
* ``reap_stale_jobs`` returns orphaned Processing rows to Pending, but turns
  them into Failure when attempts are exhausted or a cancel is pending — the
  three outcomes must be distinguishable.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.jobs.errors import JobTerminal
from app.jobs.repository import open_repository

T0 = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


async def _repo(tmp_path: Path, **kwargs: Any):
    """Open a repository with a frozen clock so leases are deterministic."""

    kwargs.setdefault("clock", lambda: T0)
    return await open_repository(tmp_path / "mee.db", **kwargs)


async def _new_job(repo: Any, **kwargs: Any) -> dict[str, Any]:
    return await repo.create(
        params={"market": {"kind": "watchlist", "symbols": ["AAPL"]}},
        api_key="sha256:owner",
        rows_target=1,
        **kwargs,
    )


async def test_pending_to_processing_to_success(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    job = await _new_job(repo)
    assert job["status"] == "Pending"
    assert job["attempts"] == 0
    assert job["worker_id"] is None

    claimed = await repo.claim("worker-1")
    assert claimed is not None, "a Pending job must be claimable"
    assert claimed["id"] == job["id"]
    assert claimed["status"] == "Processing"
    assert claimed["attempts"] == 1
    assert claimed["worker_id"] == "worker-1"
    assert claimed["locked_at"] is not None
    assert claimed["started_at"] is not None

    assert await repo.claim("worker-2") is None, "Processing is not claimable"

    done = await repo.complete(job["id"], result_count=1)
    assert done["status"] == "Success"
    assert done["result_count"] == 1
    assert done["finished_at"] is not None

    stored = await repo.get(job["id"])
    assert stored is not None and stored["status"] == "Success"


async def test_claim_is_exclusive_under_concurrency(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    job = await _new_job(repo)

    results = await asyncio.gather(*(repo.claim(f"worker-{index}") for index in range(5)))
    winners = [record for record in results if record is not None]
    assert len(winners) == 1, f"exactly one claimer may win, got {len(winners)}"
    assert winners[0]["id"] == job["id"]
    assert winners[0]["attempts"] == 1, "attempts must increase exactly once per claim"

    stored = await repo.get(job["id"])
    assert stored is not None
    assert stored["attempts"] == 1


async def test_non_retryable_failure_is_terminal(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    job = await _new_job(repo)
    await repo.claim("worker-1")

    failed = await repo.fail(job["id"], error="schema_drift: chart.result missing", retryable=False)
    assert failed["status"] == "Failure"
    assert failed["error"].startswith("schema_drift")
    assert failed["finished_at"] is not None
    assert await repo.claim("worker-2") is None, "Failure must not be reclaimable"


async def test_retryable_failure_requeues_while_attempts_remain(tmp_path: Path) -> None:
    repo = await _repo(tmp_path, max_attempts=3)
    job = await _new_job(repo)
    await repo.claim("worker-1")

    requeued = await repo.fail(job["id"], error="throttled: 429", retryable=True)
    assert requeued["status"] == "Pending", "a retryable failure with attempts left requeues"
    assert requeued["worker_id"] is None
    assert requeued["locked_at"] is None
    assert requeued["finished_at"] is None
    assert requeued["attempts"] == 1

    second = await repo.claim("worker-2")
    assert second is not None
    assert second["attempts"] == 2


async def test_attempts_exhausted_after_retryable_failures(tmp_path: Path) -> None:
    repo = await _repo(tmp_path, max_attempts=2)
    job = await _new_job(repo)

    await repo.claim("worker-1")
    await repo.fail(job["id"], error="timeout", retryable=True)  # attempts=1 -> Pending
    await repo.claim("worker-2")
    final = await repo.fail(job["id"], error="timeout", retryable=True)  # attempts=2 == max

    assert final["status"] == "Failure"
    assert "attempt" in final["error"].lower()
    assert final["finished_at"] is not None
    assert await repo.claim("worker-3") is None


async def test_cancel_pending_is_an_immediate_failure(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    job = await _new_job(repo)

    cancelled = await repo.cancel(job["id"])
    assert cancelled["status"] == "Failure"
    assert cancelled["error"] == "cancelled by client"
    assert cancelled["finished_at"] is not None
    assert cancelled["attempts"] == 0
    assert await repo.claim("worker-1") is None


async def test_cancel_processing_only_sets_the_flag(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    job = await _new_job(repo)
    await repo.claim("worker-1")

    flagged = await repo.cancel(job["id"])
    assert flagged["status"] == "Processing", "a running job is cancelled at a safe boundary"
    assert flagged["cancel_requested"] == 1

    final = await repo.fail(job["id"], error="cancelled by client", retryable=False)
    assert final["status"] == "Failure"
    assert final["error"] == "cancelled by client"


async def test_cancel_terminal_job_is_conflict(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    job = await _new_job(repo)
    await repo.claim("worker-1")
    await repo.complete(job["id"], result_count=1)

    with pytest.raises(JobTerminal) as caught:
        await repo.cancel(job["id"])
    assert caught.value.status_code == 409


async def test_reap_stale_returns_orphan_to_pending(tmp_path: Path) -> None:
    repo = await _repo(tmp_path, lease_s=300)
    job = await _new_job(repo)
    await repo.claim("worker-1")

    reaped = await repo.reap_stale_jobs(now=T0 + timedelta(seconds=301))
    assert reaped == [job["id"]]

    stored = await repo.get(job["id"])
    assert stored is not None
    assert stored["status"] == "Pending"
    assert stored["attempts"] == 1, "a reap is not a new attempt"
    assert stored["worker_id"] is None
    assert stored["locked_at"] is None

    reclaimed = await repo.claim("worker-2")
    assert reclaimed is not None
    assert reclaimed["attempts"] == 2


async def test_reap_keeps_a_live_lease_untouched(tmp_path: Path) -> None:
    repo = await _repo(tmp_path, lease_s=300)
    job = await _new_job(repo)
    await repo.claim("worker-1")

    reaped = await repo.reap_stale_jobs(now=T0 + timedelta(seconds=60))
    assert reaped == []
    stored = await repo.get(job["id"])
    assert stored is not None and stored["status"] == "Processing"


async def test_reap_makes_an_exhausted_job_fail_not_pending(tmp_path: Path) -> None:
    repo = await _repo(tmp_path, max_attempts=1, lease_s=300)
    job = await _new_job(repo)
    await repo.claim("worker-1")

    reaped = await repo.reap_stale_jobs(now=T0 + timedelta(seconds=3600))
    assert reaped == [job["id"]]
    stored = await repo.get(job["id"])
    assert stored is not None
    assert stored["status"] == "Failure", "exhausted attempts must not resurrect the job"
    assert "attempt" in stored["error"].lower()
    assert await repo.claim("worker-2") is None


async def test_reap_honours_a_pending_cancel(tmp_path: Path) -> None:
    repo = await _repo(tmp_path, max_attempts=5, lease_s=300)
    job = await _new_job(repo)
    await repo.claim("worker-1")
    await repo.cancel(job["id"])  # flag only, status stays Processing

    reaped = await repo.reap_stale_jobs(now=T0 + timedelta(seconds=3600))
    assert reaped == [job["id"]]
    stored = await repo.get(job["id"])
    assert stored is not None
    assert stored["status"] == "Failure"
    assert stored["error"] == "cancelled by client", "cancel is not 'attempts exhausted'"
