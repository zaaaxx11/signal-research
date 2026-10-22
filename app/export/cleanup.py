"""Retention sweep: files first, rows second, or not at all (02 §4.8).

Three rules, each of which has a cheap wrong implementation:

1. **30 days by default.** A job that expired yesterday at seven days is still
   inside the default window. Deleting it because ``expires_at`` passed turns
   the sweeper into a data-loss bug with a cron entry. A job is eligible only
   when it is expired **and** older than the policy's day floor — the two
   conditions answer different questions ("may I still serve this?" and "have I
   kept it long enough?").
2. **Only expired jobs.** A 90-day-old job whose expiry was extended is not
   garbage; age alone is not authority to delete.
3. **Files before rows, and never half of either.** The file is unlinked before
   the row that describes it, so a crash cannot leave an artifact nobody can
   find. The row deletions happen in **one** transaction, and a single failed
   unlink aborts the sweep before that transaction is ever opened: files may
   already be gone (the filesystem has no rollback), but no row is.
4. **The blast radius is validated first.** Every recorded artifact path must
   resolve inside the export root. One path outside it aborts the whole sweep —
   a store that points outside its own directory is a bug to report, not a file
   to delete.

Purges are appended to ``retention.custody.jsonl`` with the hash and size of
every file that was removed, because "we deleted it" without "what it was" is
not an audit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from app.export.manifest import CustodyLog, file_sha256

#: Policy default: a month of downloads after a job finished.
DEFAULT_RETENTION_DAYS = 30

#: One shared log per export directory (a per-job log would be deleted with the
#: job, taking the evidence of the deletion with it).
CUSTODY_NAME = "retention.custody.jsonl"


@dataclass(frozen=True)
class SweepReport:
    """What a sweep did, or would do (``dry_run``)."""

    purged: tuple[str, ...] = ()
    retained: tuple[str, ...] = ()
    aborted: bool = False
    dry_run: bool = False
    files_deleted: int = 0
    bytes_freed: int = 0
    failed: tuple[dict[str, Any], ...] = ()
    custody_path: Path = Path(CUSTODY_NAME)


async def sweep(
    repository: Any,
    *,
    directory: str | Path,
    now: datetime | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    dry_run: bool = False,
    unlink: Callable[[Any], None] | None = None,
) -> SweepReport:
    """Purge every job that is both expired and past the age floor."""

    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve()
    custody_path = root / CUSTODY_NAME
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("naive datetimes are not allowed in a retention sweep")
    if isinstance(retention_days, bool) or not isinstance(retention_days, int) or retention_days < 0:
        raise ValueError(f"retention_days must be an int >= 0, got {retention_days!r}")

    remove = unlink or os.unlink
    expired = await repository.expired_jobs(moment, retention_days)
    expired_ids = tuple(str(job["id"]) for job in expired)
    artifacts = await repository.all_artifacts()

    unsafe = [
        {
            "job_id": str(entry.get("job_id")),
            "path": str(entry.get("path")),
            "error": "artifact path is outside the export root",
        }
        for entry in artifacts
        if _within(resolved_root, entry.get("path")) is None
    ]
    if unsafe:
        return SweepReport(
            purged=(),
            retained=expired_ids,
            aborted=True,
            dry_run=dry_run,
            failed=tuple(unsafe),
            custody_path=custody_path,
        )

    by_job: dict[str, list[Mapping[str, Any]]] = {}
    for entry in artifacts:
        by_job.setdefault(str(entry.get("job_id")), []).append(entry)

    planned: list[tuple[str, Path, str, int]] = []
    for job_id in expired_ids:
        for entry in by_job.get(job_id, ()):  # a job with no artifact row has nothing to unlink
            path = _within(resolved_root, entry.get("path"))
            if path is None or not path.exists():
                continue
            planned.append((job_id, path, file_sha256(path), path.stat().st_size))

    if dry_run:
        return SweepReport(
            purged=expired_ids,
            retained=(),
            aborted=False,
            dry_run=True,
            custody_path=custody_path,
        )

    deleted: list[tuple[str, Path, str, int]] = []
    failed: list[dict[str, Any]] = []
    for job_id, path, sha256, size in planned:
        try:
            remove(path)
        except OSError as exc:
            failed.append(
                {
                    "job_id": job_id,
                    "path": str(path),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            break
        deleted.append((job_id, path, sha256, size))

    if failed:
        return SweepReport(
            purged=(),
            retained=expired_ids,
            aborted=True,
            dry_run=False,
            files_deleted=len(deleted),
            bytes_freed=sum(size for *_rest, size in deleted),
            failed=tuple(failed),
            custody_path=custody_path,
        )

    purged: tuple[str, ...] = ()
    if expired_ids:
        # One transaction: a store that deletes half a job is worse than one
        # that deletes none, so the count can only be "all" or "none".
        count = await repository.purge_jobs(expired_ids)
        purged = expired_ids if count == len(expired_ids) else ()

    if purged:
        custody = CustodyLog(custody_path)
        for job_id in purged:
            files = [
                {"name": path.name, "sha256": sha256, "bytes": size}
                for owner, path, sha256, size in deleted
                if owner == job_id
            ]
            custody.append(
                "retention_purged",
                job_id=job_id,
                files=files,
                bytes_freed=sum(entry["bytes"] for entry in files),
            )

    return SweepReport(
        purged=purged,
        retained=tuple(job_id for job_id in expired_ids if job_id not in purged),
        aborted=False,
        dry_run=False,
        files_deleted=len(deleted),
        bytes_freed=sum(size for *_rest, size in deleted),
        custody_path=custody_path,
    )


def _within(root: Path, raw: Any) -> Path | None:
    """Resolve ``raw`` and return it only when it lives under ``root``."""

    if raw is None or str(raw).strip() == "":
        return None
    candidate = Path(str(raw))
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved


__all__ = ["CUSTODY_NAME", "DEFAULT_RETENTION_DAYS", "SweepReport", "sweep"]
