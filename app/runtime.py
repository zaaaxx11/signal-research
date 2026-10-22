"""Composition root: one settings object, one repository, one task spawner.

``create_app()`` is called per process (and, in tests, per test case, because
``tests/_support.py`` points ``MEE_DB_PATH``/``MEE_EXPORT_DIR`` at a temp dir
before building the app). The runtime therefore owns the *path*, not a
connection: the engine is created lazily and the schema is applied on first use,
so importing ``app.main`` stays a network-free, disk-free act.

Phase 0 runs jobs in-process. That is not a shortcut around the job store — the
runner still claims the row with the same CAS a real worker uses, and still
records artifacts and completes the job through the repository. When a real
worker process arrives (Phase 2), ``spawn`` is the only method that changes.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.jobs.ports import QuoteRepository
from app.jobs.quotes import SqliteQuoteRepository
from app.jobs.repository import SqliteJobRepository
from app.resolve.reference import Reference
from app.resolve.seed import seed_reference
from app.worker.runner import process_job

logger = logging.getLogger("mee.runtime")


class Runtime:
    """Process-wide collaborators, built from settings without touching disk."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.repository = SqliteJobRepository(
            self.settings.db_path,
            max_attempts=self.settings.max_attempts,
            lease_s=self.settings.job_lease_s,
        )
        self.quotes: QuoteRepository = SqliteQuoteRepository(self.settings.db_path)
        #: Reference masters already copied into this database (path, fingerprint).
        self._seeded: set[tuple[str, str]] = set()
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def export_dir(self) -> Path:
        directory = Path(self.settings.export_dir)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def spawn(self, job_id: str) -> asyncio.Task[Any]:
        """Run one job on this event loop and keep a strong reference to the task.

        A bare ``create_task`` whose result nobody holds can be garbage collected
        mid-flight; the set (plus the done-callback) is what makes this safe.
        """

        task = asyncio.create_task(self._run(job_id), name=f"mee-job-{job_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def run_inline(self, job_id: str) -> None:
        """Run one job and await it (the ``async=false`` path)."""

        await self._run(job_id)

    async def ensure_reference_seeded(self, reference: Reference) -> None:
        """Copy the reference master into ``instruments`` (idempotent, once).

        Keyed by ``(database, master fingerprint)`` so a long-lived process seeds
        each database once, and a master that changes on disk is re-seeded on
        the next request rather than silently serving the old rows.
        """

        key = (str(Path(self.settings.db_path).resolve()), reference.fingerprint)
        if key in self._seeded:
            return
        await seed_reference(self.settings.db_path, reference)
        self._seeded.add(key)

    async def _run(self, job_id: str) -> None:
        try:
            await process_job(job_id, repository=self.repository, export_dir=self.export_dir)
        except Exception:  # noqa: BLE001 - a background task must never vanish silently
            # The job row already records the failure when process_job could
            # reach the repository; this log covers the rest (e.g. a claim lost).
            logger.exception("job %s crashed outside the repository's failure path", job_id)

    async def drain(self) -> None:
        """Wait for the spawned tasks (used by tests and by graceful shutdown)."""

        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def aclose(self) -> None:
        await self.drain()
        await self.repository.close()
        await self.quotes.close()


__all__ = ["Runtime"]
