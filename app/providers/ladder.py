"""Capability-scoped ladder: is_available → fetch → verdict (02 §3.4).

The ladder is a **domain policy**, not a retry decorator. Five rules, in order,
and each one has a test that fails if it is implemented as a comment:

1. **Cache first (L0).** A fresh observation is served without touching the
   transport. ``L0`` is not a provider rung: provider rungs are ``L1..L<n>`` and
   ``int(L<n>) == index``.
2. **Availability before the request.** A rung whose ``auth`` env var is absent,
   whose ``supports(asset_class, mic)`` says no, or that has no adapter is
   ``skipped`` with ``attempts=0`` and **zero** requests. The transport ledger —
   not a mock call count — is what proves it.
3. **Budget exhaustion is a skip, not a stop.** The paid rung reports
   ``skipped_by_budget`` and the walk continues; the job still finishes.
4. **The verdict comes from the body.** ``200`` + HTML → ``blocked`` (walk on),
   ``result: null`` → ``empty`` (stop: the schema is right, the data is absent),
   a missing promised path → ``schema_drift`` (stop: another provider will not fix
   a shape bug, and a retry storm hides it), ``404`` → ``not_found`` (stop).
5. **One request per attempt, one attempt record per rung considered.** The
   record carries the real numbers (``requests``, ``bytes``) so the meter and the
   report cannot disagree.

Every verdict is written to ``provider_health`` as it happens (02 §4.9), because
a breaker that only exists in one process is not a breaker.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Mapping, Sequence

from app.contracts import Capability
from app.providers.base import ProviderError, ProviderSpec, Verdict
from app.providers.budget import Budget
from app.providers.cache import ObservationCache
from app.providers.redact import redact

logger = logging.getLogger("mee.providers.ladder")

#: Cost tiers in walk order. The number is the ordering rule, not a label.
TIER_ORDER: dict[str, int] = {"T0": 0, "T1": 1, "T2": 2}

#: A ``T2`` request costs one credit (02 §3.5: 1 credit = 1 paid request).
CREDITS_PER_TIER: dict[str, int] = {"T0": 0, "T1": 0, "T2": 1}


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Rung:
    """One provider rung of a capability ladder (``index`` is 1-based)."""

    index: int
    label: str
    spec: ProviderSpec
    adapter: Any | None = None

    def is_available(self, env: Mapping[str, str] | None = None) -> bool:
        """True when this rung may send a request right now.

        An anonymous rung is always available; a keyed one is available only when
        its credential exists. The value is never read here — presence only.
        """

        if self.adapter is None:
            return False
        if self.spec.auth is None:
            return True
        source = env if env is not None else os.environ
        return bool((source.get(self.spec.auth) or "").strip())


@dataclass
class LadderResult:
    """What one ``fetch`` did: rows, which rung, why, and at what cost."""

    observations: list[dict[str, Any]] = field(default_factory=list)
    rung: str | None = None
    verdict: Verdict = field(default_factory=lambda: Verdict(False, "exhausted"))
    attempts: list[dict[str, Any]] = field(default_factory=list)
    skipped_by_budget: int = 0
    cache_hits: int = 0

    @property
    def requests(self) -> int:
        return sum(int(item.get("requests") or 0) for item in self.attempts)

    @property
    def bytes(self) -> int:
        return sum(int(item.get("bytes") or 0) for item in self.attempts)

    @property
    def verdicts(self) -> dict[str, int]:
        """Verdict histogram for ``usage.providers[name].verdicts``."""

        counts: dict[str, int] = {}
        for item in self.attempts:
            kind = str(item.get("verdict") or "unknown")
            counts[kind] = counts.get(kind, 0) + 1
        return counts


class Ladder:
    """Walk the rungs of one capability until a body answers or the walk ends."""

    def __init__(
        self,
        *,
        registry: Sequence[ProviderSpec],
        adapters: Mapping[str, Any],
        budget: Budget | None = None,
        cache: ObservationCache | None = None,
        health: Any | None = None,
        clock: Callable[[], datetime] | None = None,
        as_of: datetime | None = None,
    ) -> None:
        self._registry = tuple(registry)
        self._adapters = dict(adapters)
        self._budget = budget
        self._cache = cache
        self._health = health
        self._clock = clock or _now
        self._as_of = as_of

    # -- rungs -------------------------------------------------------------

    def rungs(self, capability: Capability) -> list[Rung]:
        """Ordered rungs for ``capability``, labelled ``L1..L<n>``.

        Order is cost tier first, then the position the registry declared. A
        registry that lists a paid rung before a free one is corrected here and
        the correction is visible in the response — never silently reordered
        between two calls.
        """

        candidates = [
            (position, spec)
            for position, spec in enumerate(self._registry)
            if capability in spec.capabilities
        ]
        candidates.sort(key=lambda item: (TIER_ORDER.get(item[1].cost_tier, 99), item[0]))
        return [
            Rung(index=index, label=f"L{index}", spec=spec, adapter=self._adapters.get(spec.name))
            for index, (_position, spec) in enumerate(candidates, start=1)
        ]

    # -- fetch -------------------------------------------------------------

    async def fetch(
        self, capability: Capability, instrument: Any, fields: set[str]
    ) -> LadderResult:
        """Serve ``fields`` for ``instrument`` from the first rung that can."""

        wanted = {str(name) for name in fields}
        as_of = self._as_of or self._clock()
        rungs = self.rungs(capability)
        result = LadderResult()

        if not rungs:
            result.verdict = Verdict(False, "exhausted", f"no rung for {capability.value}")
            return result

        cached = self._cache_hit(capability, instrument, wanted, as_of, rungs)
        if cached is not None:
            entry, rung = cached
            result.cache_hits = 1
            result.rung = rung.spec.name
            result.verdict = entry.verdict
            result.observations = [dict(item) for item in entry.observations]
            result.attempts.append(
                self._attempt(rung, "cache", attempts=0, requests=0, bytes_=0, capability=capability)
            )
            logger.info(
                "cache hit provider=%s rung=%s capability=%s fields=%d",
                rung.spec.name,
                rung.label,
                capability.value,
                len(wanted),
            )
            return result

        last: Verdict | None = None
        for rung in rungs:
            budget_credits = CREDITS_PER_TIER.get(rung.spec.cost_tier, 0)

            if not rung.is_available():
                result.attempts.append(
                    self._attempt(rung, "skipped", capability=capability)
                )
                logger.info(
                    "rung skipped provider=%s rung=%s capability=%s reason=unavailable",
                    rung.spec.name,
                    rung.label,
                    capability.value,
                )
                continue

            if not self._supports(rung, instrument):
                result.attempts.append(self._attempt(rung, "skipped", capability=capability))
                logger.info(
                    "rung skipped provider=%s rung=%s capability=%s reason=unsupported",
                    rung.spec.name,
                    rung.label,
                    capability.value,
                )
                continue

            if self._budget is not None and not self._budget.allows(
                requests=1, credits=budget_credits
            ):
                result.skipped_by_budget += 1
                result.attempts.append(
                    self._attempt(rung, "skipped_by_budget", capability=capability)
                )
                logger.info(
                    "rung skipped provider=%s rung=%s capability=%s reason=budget",
                    rung.spec.name,
                    rung.label,
                    capability.value,
                )
                continue

            verdict, size, raw = await self._call(rung, capability, instrument, wanted)
            if self._budget is not None:
                self._budget.spend(requests=1, credits=budget_credits, bytes_=size)
            result.attempts.append(
                self._attempt(
                    rung,
                    verdict.kind,
                    attempts=1,
                    requests=1,
                    bytes_=size,
                    capability=capability,
                )
            )
            last = verdict
            await self._note_health(rung, capability, verdict)

            if verdict.ok:
                observations = self._parse(rung, raw, instrument)
                if observations and self._cache is not None:
                    self._cache.put(
                        provider=rung.spec.name,
                        capability=capability,
                        instrument_id=instrument.instrument_id,
                        fields=sorted(wanted),
                        as_of=as_of,
                        schema_version=rung.spec.schema_version,
                        observations=observations,
                        verdict=verdict,
                        fetched_at=self._clock(),
                    )
                result.rung = rung.spec.name
                result.verdict = verdict
                result.observations = observations
                return result

            if verdict.terminal:
                # not_found / schema_drift / authwall / empty: another rung would
                # repeat the same fact, and a retry storm hides the bug.
                break

        result.verdict = last or Verdict(False, "exhausted", "every rung was skipped")
        return result

    # -- internals ---------------------------------------------------------

    def _cache_hit(
        self,
        capability: Capability,
        instrument: Any,
        fields: set[str],
        as_of: datetime,
        rungs: Sequence[Rung],
    ) -> tuple[Any, Rung] | None:
        """First fresh entry **in ladder order**: a cheap rung wins over a paid one."""

        if self._cache is None:
            return None
        for rung in rungs:
            entry = self._cache.get(
                provider=rung.spec.name,
                capability=capability,
                instrument_id=instrument.instrument_id,
                fields=sorted(fields),
                as_of=as_of,
                schema_version=rung.spec.schema_version,
            )
            if entry is not None:
                return entry, rung
        return None

    @staticmethod
    def _supports(rung: Rung, instrument: Any) -> bool:
        try:
            return bool(rung.spec.supports(instrument.asset_class, instrument.mic))
        except Exception:  # noqa: BLE001 - a predicate that throws must not stop the walk
            return False

    async def _call(
        self,
        rung: Rung,
        capability: Capability,
        instrument: Any,
        fields: set[str],
    ) -> tuple[Verdict, int, Mapping[str, Any] | None]:
        """One request through the rung's adapter: (verdict, body bytes, raw).

        Transport failures become verdicts, not exceptions: ``blocked``/
        ``5xx``/``timeout`` are retryable rungs (walk on), and the ladder never
        lets an adapter's socket error escape as a job failure.
        """

        adapter = rung.adapter
        if adapter is None:  # pragma: no cover - rungs() already filtered
            return Verdict(False, "5xx", "no adapter"), 0, None
        try:
            raw = await adapter.fetch(capability, instrument, fields)
        except ProviderError as exc:
            logger.info(
                "rung failed provider=%s rung=%s capability=%s verdict=%s",
                rung.spec.name,
                rung.label,
                capability.value,
                exc.verdict.kind,
            )
            return exc.verdict, 0, None
        except Exception as exc:  # noqa: BLE001 - taxonomy decides, not the type
            kind = "timeout" if "timeout" in type(exc).__name__.lower() else "5xx"
            logger.info(
                "rung failed provider=%s rung=%s capability=%s verdict=%s error=%s",
                rung.spec.name,
                rung.label,
                capability.value,
                kind,
                redact(f"{type(exc).__name__}: {exc}")[:200],
            )
            return Verdict(False, kind, f"{type(exc).__name__}: {exc}"), 0, None

        body = raw.get("body") or b"" if isinstance(raw, Mapping) else b""
        size = len(body) if isinstance(body, (bytes, bytearray)) else len(str(body))
        try:
            verdict = adapter.verdict(raw, instrument)
        except Exception as exc:  # noqa: BLE001 - a parser bug is schema drift
            verdict = Verdict(False, "schema_drift", f"verdict() raised {type(exc).__name__}")
        logger.info(
            "rung provider=%s rung=%s capability=%s verdict=%s attempts=1 requests=1 bytes=%d",
            rung.spec.name,
            rung.label,
            capability.value,
            verdict.kind,
            size,
        )
        return verdict, size, raw

    @staticmethod
    def _parse(
        rung: Rung, raw: Mapping[str, Any] | None, instrument: Any
    ) -> list[dict[str, Any]]:
        """Rows from a served body; a parser that throws yields no rows, not a lie."""

        if raw is None or rung.adapter is None:  # pragma: no cover - defensive
            return []
        try:
            rows = rung.adapter.parse(raw, instrument)
        except Exception:  # noqa: BLE001 - the body is not what the parser expected
            logger.warning(
                "parse failed provider=%s capability=%s",
                rung.spec.name,
                next(iter(rung.spec.capabilities)).value,
            )
            return []
        return [dict(row) for row in rows]

    async def _note_health(self, rung: Rung, capability: Capability, verdict: Verdict) -> None:
        if self._health is None:
            return
        try:
            await self._health.record(rung.spec.name, capability.value, verdict)
        except Exception:  # noqa: BLE001 - telemetry must never fail a job
            logger.warning(
                "provider_health update failed provider=%s capability=%s",
                rung.spec.name,
                capability.value,
            )

    @staticmethod
    def _attempt(
        rung: Rung,
        verdict: str,
        *,
        attempts: int = 0,
        requests: int = 0,
        bytes_: int = 0,
        capability: Capability | None = None,
    ) -> dict[str, Any]:
        """The pinned attempt record (see ``tests/_phase2.ATTEMPT_KEYS``)."""

        return {
            "rung": rung.label,
            "provider": rung.spec.name,
            "capability": (capability or next(iter(rung.spec.capabilities))).value,
            "verdict": verdict,
            "attempts": attempts,
            "requests": requests,
            "bytes": bytes_,
        }


__all__ = ["CREDITS_PER_TIER", "Ladder", "LadderResult", "Rung", "TIER_ORDER"]
