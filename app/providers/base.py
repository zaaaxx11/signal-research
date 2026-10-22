"""Provider vocabulary: capability, spec, verdict, observation (02 §3.4).

``Capability`` is re-exported from :mod:`app.contracts` — one vocabulary, not
two. Adapters never register themselves; only ``registry.py`` declares order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.contracts import Capability

__all__ = ["Capability", "ProviderSpec", "Verdict"]


@dataclass(frozen=True)
class ProviderSpec:
    """Static, non-secret description of a provider rung."""

    name: str
    capabilities: frozenset[Capability]
    cost_tier: str
    trust: int
    delay: str
    auth: str | None
    rate_limit: tuple[int, float]
    supports: Callable[[str, str], bool]
    schema_version: str


@dataclass(frozen=True)
class Verdict:
    """Content-based verdict, never a status-code restatement."""

    ok: bool
    kind: str
    detail: str | None = None
