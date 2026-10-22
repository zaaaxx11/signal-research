"""The ONLY declaration of providers and rung order (02 §3.4).

Adapters do not register themselves: import order must never change the ladder.
Phase 0 ships the fake provider only, so ``ladder_for(QUOTE)`` has exactly one
rung; the ordering rules are implemented now because a ladder whose order is
"whatever the tuple says" is how paid providers end up in front of free ones.

Ordering (four rules, 02 §3.4): cost tier first (T0 → T1 → T2), then name, with
an explicit assertion that a paid rung never precedes a free one.
"""

from __future__ import annotations

from app.contracts import Capability
from app.providers.adapters.fake import fake_spec
from app.providers.base import ProviderSpec

#: One declaration for the whole service. Later phases append real adapters.
REGISTRY: tuple[ProviderSpec, ...] = (fake_spec(),)

_TIER_ORDER: dict[str, int] = {"T0": 0, "T1": 1, "T2": 2}


def _rung_key(spec: ProviderSpec) -> tuple[int, str]:
    if spec.cost_tier not in _TIER_ORDER:
        raise ValueError(f"provider {spec.name!r} has unknown cost tier {spec.cost_tier!r}")
    return (_TIER_ORDER[spec.cost_tier], spec.name)


def ladder_for(capability: Capability) -> tuple[ProviderSpec, ...]:
    """Return the ordered rungs for one capability (T0 -> T1 -> T2)."""

    rungs = tuple(
        sorted(
            (spec for spec in REGISTRY if capability in spec.capabilities),
            key=_rung_key,
        )
    )
    tiers = [_TIER_ORDER[spec.cost_tier] for spec in rungs]
    if tiers != sorted(tiers):
        raise AssertionError(f"ladder for {capability.value} puts a paid rung before a free one")
    return rungs


def providers_for(*capabilities: Capability) -> tuple[ProviderSpec, ...]:
    """Union of the ladders for several capabilities, de-duplicated, order kept."""

    seen: dict[str, ProviderSpec] = {}
    for capability in capabilities:
        for spec in ladder_for(capability):
            seen.setdefault(spec.name, spec)
    return tuple(seen.values())


def capability_matrix() -> list[dict[str, object]]:
    """Non-secret view of the registry, used by ``GET /providers``."""

    return [
        {
            "provider": spec.name,
            "capabilities": sorted(capability.value for capability in spec.capabilities),
            "cost_tier": spec.cost_tier,
            "trust": spec.trust,
            "delay": spec.delay,
            "requires_key": spec.auth is not None,
            "schema_version": spec.schema_version,
        }
        for spec in sorted(REGISTRY, key=_rung_key)
    ]


__all__ = ["REGISTRY", "capability_matrix", "ladder_for", "providers_for"]
