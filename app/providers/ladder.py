"""Capability-scoped ladder: is_available -> fetch -> verdict (02 §3.4)."""

from __future__ import annotations

from typing import Any

from app.contracts import Capability
from app.providers.base import Verdict


async def fetch(
    capability: Capability, symbols: list[str], fields: set[str]
) -> dict[str, Any]:
    """Walk the rungs; skip unavailable ones without sending a request."""

    raise NotImplementedError("provider ladder is not implemented (Phase 2 RED)")
