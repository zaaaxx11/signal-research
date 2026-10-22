"""The ONLY declaration of providers and rung order (02 §3.4).

Adapters do not register themselves: import order must never change the ladder.
Phase 0 ships the fake provider only.
"""

from __future__ import annotations

from app.contracts import Capability
from app.providers.base import ProviderSpec


REGISTRY: tuple[ProviderSpec, ...] = ()


def ladder_for(capability: Capability) -> tuple[ProviderSpec, ...]:
    """Return the ordered rungs for one capability (T0 -> T1 -> T2)."""

    raise NotImplementedError(
        f"provider registry/ladder is not implemented (Phase 0 RED): ladder_for({capability!r})"
    )
