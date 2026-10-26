"""One shared SEC rate limit for every engine request.

EDGAR asks automated clients to stay at or below 10 requests/second. Per-call
pacing is not enough: two routes can each be polite and still exceed the ceiling
together. This module owns a single process-wide token bucket and a ``sec_get``
helper, so every SEC fetch — submissions, companyfacts, N-PORT XML, EFTS,
insider XML, the fund map — passes the same gate.

Configuration is ``MEE_SEC_RATE_LIMIT`` (requests/second). ``0``/``off``/empty
disables the gate (the test suite does this so thousands of mocked requests do
not sleep); an unparseable value falls back to 10, never to unlimited.

Contract: ``briefs/market-engine/R7-CONTRACT.md`` §7.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Callable

DEFAULT_SEC_RATE = 10.0


class AsyncRateLimiter:
    """A token bucket with an injectable clock and sleep for deterministic tests."""

    def __init__(
        self,
        *,
        rate: float = DEFAULT_SEC_RATE,
        burst: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = float(rate)
        self.burst = float(burst if burst is not None else max(1.0, rate))
        if self.burst <= 0:
            raise ValueError("burst must be positive")
        self._clock = clock
        self._sleep = sleep
        self._tokens = self.burst
        self._last = clock()

    async def acquire(self, tokens: float = 1.0) -> None:
        """Wait until ``tokens`` are available, then take them.

        No lock is used: a single event loop runs this coroutine, and every
        await yields before the next acquire can inspect the bucket.  A
        request larger than the burst is capped at the burst (it cannot be
        satisfied otherwise) rather than waiting forever.
        """

        need = min(float(tokens), self.burst)
        if need <= 0:
            return
        while True:
            now = self._clock()
            elapsed = max(0.0, now - self._last)
            self._last = now
            self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
            if self._tokens >= need:
                self._tokens -= need
                return
            await self._sleep((need - self._tokens) / self.rate)


#: The process-wide limiter and the rate it was built for.  Rebuilt only when
#: the configured rate changes, so two routes cannot end up with two buckets.
_LIMITER: AsyncRateLimiter | None = None
_LIMITER_RATE: float | None = None


def configured_rate() -> float | None:
    """The configured rate, or ``None`` when the gate is disabled.

    ``MEE_SEC_RATE_LIMIT`` is a request/second number.  Disabled spellings are
    explicit (0/off/none/false/empty); an unparseable value falls back to the
    SEC default of 10 — garbage must never turn into unlimited traffic.
    """

    raw = os.environ.get("MEE_SEC_RATE_LIMIT")
    if raw is None:
        return DEFAULT_SEC_RATE
    value = raw.strip().lower()
    if value in {"", "0", "off", "none", "false"}:
        return None
    try:
        rate = float(value)
    except ValueError:
        return DEFAULT_SEC_RATE
    return rate if rate > 0 else None


def sec_limiter() -> AsyncRateLimiter | None:
    """The process-wide limiter, rebuilt only when the rate changes."""

    global _LIMITER, _LIMITER_RATE
    rate = configured_rate()
    if rate is None:
        return None
    if _LIMITER is None or _LIMITER_RATE != rate:
        _LIMITER = AsyncRateLimiter(rate=rate, burst=max(1.0, int(rate)))
        _LIMITER_RATE = rate
    return _LIMITER


def reset_sec_limiter() -> None:
    """Forget the singleton (tests and config reloads)."""

    global _LIMITER, _LIMITER_RATE
    _LIMITER = None
    _LIMITER_RATE = None


async def sec_get(client: Any, url: str, **kwargs: Any) -> Any:
    """Acquire one SEC token, then perform the GET."""

    limiter = sec_limiter()
    if limiter is not None:
        await limiter.acquire()
    return await client.get(url, **kwargs)


__all__ = [
    "AsyncRateLimiter",
    "DEFAULT_SEC_RATE",
    "configured_rate",
    "reset_sec_limiter",
    "sec_get",
    "sec_limiter",
]
