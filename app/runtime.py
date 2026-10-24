"""Composition root: one settings object, one repository, one task spawner.

``create_app()`` is called per process (and, in tests, per test case, because
``tests/_support.py`` points ``MEE_DB_PATH``/``MEE_EXPORT_DIR`` at a temp dir
before building the app). The runtime therefore owns the *path*, not a
connection: the engine is created lazily and the schema is applied on first use,
so importing ``app.main`` stays a network-free, disk-free act.

Phase 0 ran every job through the deterministic fake provider. Fase 2 adds the
registry ladder behind one knob (``MEE_PROVIDER_MODE``) and — critically — one
construction path: :meth:`Runtime.ladder` is what the worker uses and what the
Phase-2 suite builds, so a ladder that only a test can assemble fails the
job-level gates. The ladder's cache and health store are runtime-owned, which is
what makes "the second identical job sends zero requests" a property of the
process rather than of a test.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.jobs.ports import QuoteRepository
from app.jobs.quotes import SqliteQuoteRepository
from app.jobs.repository import SqliteJobRepository
from app.providers.adapters import ADAPTER_NAMES, build_adapters
from app.providers.base import ProviderSpec
from app.providers.budget import Budget
from app.providers.cache import ObservationCache
from app.providers.health import ProviderHealthStore
from app.providers.ladder import Ladder
from app.providers.registry import REGISTRY, capability_matrix, provider_named
from app.resolve.reference import Reference
from app.resolve.seed import seed_reference
from app.screen.deep import DEFAULT_CAPABILITIES, ScreenDeepEngine, build_screen_deep_engine
from app.screen.multi_source import MultiSourceScreen, build_multi_source_verifiers
from app.screen.repository import SqliteScreenRepository
from app.worker.runner import process_job

logger = logging.getLogger("mee.runtime")


def _now() -> datetime:
    return datetime.now(UTC)


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
        #: ``screens``/``screen_rows``: the browse display and the export's
        #: source of truth. One repository, so the API and the bundle can never
        #: disagree about which snapshot they are serving.
        self.screen_repository = SqliteScreenRepository(self.settings.db_path)
        #: P10: the default ``GET /screens`` document. The service owns its own
        #: 300 s verification cache (keyed by the snapshot id), so the cache is a
        #: property of the process rather than of a request — which is what makes
        #: "second read inside the window = zero verifier calls" true for the API
        #: and not just for a test. Verifiers are injected here, never built in a
        #: route, so a deployment can add Finviz/Google without touching the API.
        self.multi_source_screen = MultiSourceScreen(
            repository=self.screen_repository,
            verifiers=build_multi_source_verifiers(self.settings),
        )
        #: The deep half of the screen, injected here (never constructed inside
        #: a route): the export bundle calls it once with the 20 display symbols.
        #: Defaults to quote + EOD + fundamentals + options over this runtime's
        #: own ladder — the four capabilities PLAN-screen.md §6 budgets as
        #: "20 symbols × 4 capability ≈ 80 requests". The quote is the row's
        #: existence; the other three fill cells and fail in isolation.
        #: Tests replace it to record exactly what was enriched.
        self.screen_deep_engine: ScreenDeepEngine = build_screen_deep_engine(
            self.ladder, capabilities=DEFAULT_CAPABILITIES
        )
        #: Breaker bookkeeping, shared by every ladder this process builds.
        self.health = ProviderHealthStore(self.settings.db_path)
        #: L0 observation cache: process-lifetime, TTL + session-date keyed.
        self._cache = ObservationCache(clock=_now)
        self._adapters: dict[str, Any] | None = None
        #: Reference masters already copied into this database (path, fingerprint).
        self._seeded: set[tuple[str, str]] = set()
        self._tasks: set[asyncio.Task[Any]] = set()

    # -- provider wiring ---------------------------------------------------

    @property
    def provider_mode(self) -> str:
        """``fake`` (default) or ``live`` — the one switch between two paths."""

        return self.settings.provider_mode

    def adapters(self) -> dict[str, Any]:
        """Adapter instances, built once per process (stateless, no key read)."""

        if self._adapters is None:
            self._adapters = build_adapters()
        return self._adapters

    def ladder(
        self, *, budget: Budget | None = None, as_of: datetime | None = None
    ) -> Ladder:
        """The single construction path for a registry ladder (02 §5 Fase 2).

        ``budget`` is per job (the worker passes the request's), ``as_of`` is what
        the cache key buckets and what the session boundary is computed from. A
        test that omits both gets the default budget (paid rungs skipped unless
        credits were granted) and "now".
        """

        return Ladder(
            registry=REGISTRY,
            adapters=self.adapters(),
            budget=budget if budget is not None else Budget(),
            cache=self._cache,
            health=self.health,
            clock=_now,
            as_of=as_of,
        )

    async def provider_matrix(self) -> list[dict[str, Any]]:
        """``GET /providers``: the registry projection, availability and health."""

        health = await self.health.snapshot()
        rows: list[dict[str, Any]] = []
        for entry in capability_matrix():
            name = str(entry["provider"])
            spec = provider_named(name)
            rows.append(
                {
                    **entry,
                    "available": self.is_available(spec),
                    "health": _health_of(name, health),
                }
            )
        return rows

    @staticmethod
    def is_available(spec: ProviderSpec | None) -> bool:
        """Declared **and** implemented **and** (if keyed) credentialed.

        Reporting ``available=true`` for a provider whose adapter does not exist
        would be the same lie as an empty csv: a client plans around a rung that
        cannot answer.
        """

        if spec is None or spec.name not in ADAPTER_NAMES:
            return False
        if spec.auth is None:
            return True
        return bool((os.environ.get(spec.auth) or "").strip())

    # -- jobs --------------------------------------------------------------

    @property
    def export_dir(self) -> Path:
        directory = Path(self.settings.export_dir)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def spawn(self, job_id: str) -> asyncio.Task[Any]:
        """Run one job on this event loop and keep a strong reference to the task.

        Kept for callers that own a long-lived loop (a worker process, a script).
        The HTTP layer does **not** use it: a bare ``create_task`` whose result
        nobody awaits can be destroyed mid-transaction when the request's loop
        goes away, which leaves an open provider/storage connection behind. The
        API runs the job as a response background task instead, so the work is
        always observed to completion (02 §3.9: a job must reach a terminal state
        or be reaped, never just vanish).
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
            await process_job(
                job_id,
                repository=self.repository,
                export_dir=self.export_dir,
                ladder_factory=self.ladder,
                provider_mode=self.provider_mode,
            )
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
        await self.screen_repository.aclose()


def _health_of(name: str, health: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    """Aggregate a provider's ``(capability)`` rows into the ``/providers`` cell.

    Fase 2 records verdicts but never opens a circuit, so ``closed`` means "no row
    says otherwise", not "proven healthy"; ``failures`` is the number an operator
    actually reads.
    """

    rows = [row for (provider, _capability), row in health.items() if provider == name]
    if not rows:
        return {"state": "unknown", "failures": 0, "last_verdict": None, "last_ok_at": None}
    quote_row = next(
        (row for (provider, capability), row in health.items() if provider == name and capability == "quote"),
        rows[0],
    )
    oks = [row.get("last_ok_at") for row in rows if row.get("last_ok_at")]
    return {
        "state": "closed",
        "failures": sum(int(row.get("failures") or 0) for row in rows),
        "last_verdict": quote_row.get("last_verdict"),
        "last_ok_at": max(oks) if oks else None,
    }


__all__ = ["Runtime"]
