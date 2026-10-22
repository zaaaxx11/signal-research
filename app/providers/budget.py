"""Non-blocking budget predicates and min-gap pacer (02 §3.5, 05 §1.5)."""

from __future__ import annotations


class Budget:
    """requests/credits/bytes/seconds; exhaustion means skip, not stop."""

    def allows(self, *, requests: int = 1, credits: int = 0) -> bool:
        raise NotImplementedError("Budget.allows is not implemented (Phase 2)")


class Pacer:
    """Min-gap pacing (not a token bucket: burst is a 429 generator)."""

    async def wait(self) -> None:
        raise NotImplementedError("Pacer.wait is not implemented (Phase 2)")
