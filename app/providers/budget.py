"""Non-blocking budget predicates and min-gap pacer (02 §3.5, 05 §1.5).

Three ceilings, three units, and one rule that the whole ladder depends on:

* ``requests`` — real HTTP requests, counted at the call site;
* ``credits`` — paid units (a ``T2`` rung costs 1 credit per request);
* ``seconds`` — wall clock, so a phase that runs out of time stops *starting*
  work instead of being killed mid-write.

**Exhaustion is a skip, not a stop.** :meth:`Budget.allows` is a pure predicate:
the ladder asks before it sends anything, records ``skipped_by_budget`` for that
rung, and keeps walking. A budget object must never raise, sleep, or mutate on a
query — that is how "budget exhausted" turns into "job failed".

``snapshot()`` reports **spent** counters (not remaining), because that is what
``jobs.usage`` stores and what a reader compares against the transport ledger.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

__all__ = ["Budget", "Pacer"]


class Budget:
    """requests/credits/seconds; exhaustion means skip, not stop."""

    def __init__(
        self,
        *,
        requests: int = 5000,
        credits: int = 0,
        seconds: int = 1800,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if requests < 0:
            raise ValueError("budget.requests must be >= 0")
        if credits < 0:
            raise ValueError("budget.credits must be >= 0")
        if seconds <= 0:
            raise ValueError("budget.seconds must be > 0")
        self.requests_limit = requests
        self.credits_limit = credits
        self.seconds_limit = seconds
        self._clock = clock or time.monotonic
        self._start = self._clock()
        self._requests = 0
        self._credits = 0
        self._bytes = 0

    # -- predicates --------------------------------------------------------

    @property
    def seconds_left(self) -> float:
        """Wall clock remaining (never negative: a spent budget is 0, not -3)."""

        return max(0.0, self.seconds_limit - (self._clock() - self._start))

    def allows(self, *, requests: int = 1, credits: int = 0) -> bool:
        """True when the next attempt fits. Pure: no spend, no sleep, no raise."""

        if requests < 0 or credits < 0:
            raise ValueError("allows() takes non-negative amounts")
        if self._requests + requests > self.requests_limit:
            return False
        if self._credits + credits > self.credits_limit:
            return False
        return self.seconds_left > 0

    # -- mutation ----------------------------------------------------------

    def spend(self, *, requests: int = 1, credits: int = 0, bytes_: int = 0) -> None:
        """Record what one attempt actually cost (called at the request site)."""

        self._requests += max(0, requests)
        self._credits += max(0, credits)
        self._bytes += max(0, bytes_)

    def snapshot(self) -> dict[str, Any]:
        """Spent counters plus their ceilings (``jobs.usage`` shape)."""

        return {
            "requests": self._requests,
            "credits": self._credits,
            "bytes": self._bytes,
            "requests_limit": self.requests_limit,
            "credits_limit": self.credits_limit,
            "seconds_limit": self.seconds_limit,
            "seconds_left": round(self.seconds_left, 3),
        }


class Pacer:
    """Min-gap pacing (not a token bucket: burst is a 429 generator).

    ``rate_limit`` from :class:`app.providers.base.ProviderSpec` is
    ``(requests, per_seconds)``; a zero rate disables pacing.
    """

    def __init__(
        self,
        requests: int = 0,
        per_seconds: float = 0.0,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Any] | None = None,
    ) -> None:
        self.gap_s = (per_seconds / requests) if requests > 0 and per_seconds > 0 else 0.0
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._last: float | None = None

    async def wait(self) -> None:
        """Sleep only for the remainder of the gap; a no-op when unconfigured."""

        if self.gap_s <= 0:
            return
        now = self._clock()
        if self._last is not None:
            remaining = self.gap_s - (now - self._last)
            if remaining > 0:
                await self._sleep(remaining)
                now = self._clock()
        self._last = now
