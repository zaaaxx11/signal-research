"""The ONLY declaration of providers and rung order (02 §3.4).

Adapters do not register themselves: import order must never change the ladder.
Two declarations live here and nowhere else:

* :data:`REGISTRY` — every provider and what it can do. ``auth`` is the **name**
  of an environment variable; the value is read by the adapter at fetch time and
  never appears in a spec, a response, or a log line.
* :data:`LADDER_ORDER` — the explicit rung order **per capability**. Sorting the
  registry by ``(tier, name)`` is not the documented order: it yields
  ``nasdaq, stooq, yahoo`` for QUOTE, which puts the delayed rung first and
  inverts the ladder 02 §3.4 fixes as ``L1 yahoo, L2 stooq, L3 nasdaq``.

The four ordering rules of 02 §3.4 are enforced at import time: cache is ``L0``
and is not a provider rung, free before paid (``T0 → T1 → T2``), official before
unofficial at the same price (``sec_edgar`` before ``yahoo`` for fundamentals),
and fewer requests before more (not expressible as a field yet, so it is the
reason ``LADDER_ORDER`` is written by hand rather than computed).

A registry that drifts from its own order table is a bug, not a warning: the
module refuses to import (``_validate``), so ``/providers`` and the ladder can
never disagree about what exists.
"""

from __future__ import annotations

from typing import Callable

from app.contracts import Capability
from app.providers.base import ProviderSpec

_TIER_ORDER: dict[str, int] = {"T0": 0, "T1": 1, "T2": 2}


def _any(_asset_class: str, _mic: str) -> bool:
    return True


def _us_only(_asset_class: str, mic: str) -> bool:
    return mic in {"XNAS", "XNYS", "XASE"}


def _us_or_pl(_asset_class: str, mic: str) -> bool:
    return mic in {"XNAS", "XNYS", "XASE", "XWAR"}


def _spec(
    name: str,
    capabilities: tuple[Capability, ...],
    *,
    tier: str,
    trust: int,
    delay: str,
    auth: str | None = None,
    rate_limit: tuple[int, float] = (0, 0.0),
    supports: Callable[[str, str], bool] = _any,
) -> ProviderSpec:
    return ProviderSpec(
        name=name,
        capabilities=frozenset(capabilities),
        cost_tier=tier,
        trust=trust,
        delay=delay,
        auth=auth,
        rate_limit=rate_limit,
        supports=supports,
        # The schema version is part of the cache key: a shape change invalidates
        # cached rows instead of serving old-shaped data as new (02 §3.6).
        schema_version=f"{name}/v1",
    )


#: One declaration for the whole service. Later phases extend it here only.
REGISTRY: tuple[ProviderSpec, ...] = (
    # -- T0, official (regulator / exchange / issuer) -----------------------
    _spec(
        "sec_edgar",
        (Capability.REFERENCE, Capability.FUNDAMENTALS),
        tier="T0",
        trust=1,
        delay="eod",
    ),
    _spec(
        "yahoo",
        (
            Capability.QUOTE,
            Capability.EOD_BARS,
            Capability.FUNDAMENTALS,
            Capability.OPTIONS,
            Capability.CORPORATE_ACTIONS,
        ),
        tier="T0",
        trust=3,
        delay="realtime",
    ),
    _spec(
        "stooq",
        (Capability.QUOTE, Capability.EOD_BARS),
        tier="T0",
        trust=3,
        delay="delayed",
        rate_limit=(5, 1.0),
        supports=_us_or_pl,
    ),
    _spec(
        "nasdaq",
        (Capability.QUOTE,),
        tier="T0",
        trust=3,
        delay="delayed15",
        rate_limit=(10, 1.0),
        supports=_us_only,
    ),
    _spec("ecb", (Capability.FX,), tier="T0", trust=1, delay="eod"),
    _spec("coingecko", (Capability.REFERENCE, Capability.FX), tier="T0", trust=3, delay="realtime"),
    _spec("ishares", (Capability.CONSTITUENTS,), tier="T0", trust=2, delay="eod"),
    # -- T1, free key + quota ----------------------------------------------
    _spec(
        "openfigi",
        (Capability.REFERENCE,),
        tier="T1",
        trust=2,
        delay="eod",
        auth="OPENFIGI_API_KEY",
        rate_limit=(25, 60.0),
    ),
    _spec(
        "finnhub",
        (Capability.QUOTE, Capability.FUNDAMENTALS),
        tier="T1",
        trust=2,
        delay="realtime",
        auth="FINNHUB_API_KEY",
        rate_limit=(60, 60.0),
    ),
    # -- T2, paid -----------------------------------------------------------
    _spec(
        "polygon",
        (Capability.QUOTE, Capability.EOD_BARS, Capability.OPTIONS, Capability.FX),
        tier="T2",
        trust=2,
        delay="realtime",
        auth="POLYGON_API_KEY",
        rate_limit=(100, 60.0),
        supports=_us_only,
    ),
)

#: The synthetic provider is **not** a rung. ``MEE_PROVIDER_MODE=fake`` selects
#: it as the whole execution path (Phase 0/1 behaviour); listing it here would
#: advertise a synthetic price as a real source in ``/providers`` and, worse,
#: put it in front of a live ladder. ``app.providers.adapters.fake`` stays
#: importable for the worker; only ``REGISTRY`` lost it.

#: Explicit rung order per capability (02 §3.4). ``L0`` is the cache and is not
#: listed: a provider rung is ``L1..L<n>`` and ``int(L<n>) == index``.
LADDER_ORDER: dict[Capability, tuple[str, ...]] = {
    Capability.REFERENCE: ("sec_edgar", "coingecko", "openfigi"),
    Capability.QUOTE: ("yahoo", "stooq", "nasdaq", "finnhub", "polygon"),
    Capability.EOD_BARS: ("yahoo", "stooq", "polygon"),
    Capability.FUNDAMENTALS: ("sec_edgar", "yahoo", "finnhub"),
    Capability.OPTIONS: ("yahoo", "polygon"),
    Capability.CONSTITUENTS: ("ishares",),
    Capability.CORPORATE_ACTIONS: ("yahoo",),
    Capability.FX: ("ecb", "coingecko", "polygon"),
}

_BY_NAME: dict[str, ProviderSpec] = {spec.name: spec for spec in REGISTRY}


def _validate() -> None:
    """Refuse to import a registry whose order table and specs disagree."""

    names = [spec.name for spec in REGISTRY]
    if len(names) != len(set(names)):
        raise RuntimeError(f"duplicate provider names: {names}")

    for spec in REGISTRY:
        if spec.cost_tier not in _TIER_ORDER:
            raise RuntimeError(f"{spec.name}: unknown cost tier {spec.cost_tier!r}")

    for capability in Capability:
        order = LADDER_ORDER.get(capability)
        if not order:
            raise RuntimeError(f"{capability.value}: no rung declared (02 §5 Fase 2 gate 1)")
        declared = {spec.name for spec in REGISTRY if capability in spec.capabilities}
        if set(order) != declared:
            raise RuntimeError(
                f"{capability.value}: LADDER_ORDER and REGISTRY disagree: "
                f"order-only={sorted(set(order) - declared)}, "
                f"registry-only={sorted(declared - set(order))}"
            )
        if len(order) != len(set(order)):
            raise RuntimeError(f"{capability.value}: duplicate rung in LADDER_ORDER")
        tiers = [_TIER_ORDER[_BY_NAME[name].cost_tier] for name in order]
        if tiers != sorted(tiers):
            raise RuntimeError(
                f"{capability.value}: a paid rung precedes a cheaper one: "
                + " -> ".join(f"{name}({_BY_NAME[name].cost_tier})" for name in order)
            )
        if tiers[0] != 0:
            raise RuntimeError(
                f"{capability.value}: the first rung must be free, got "
                f"{order[0]}({_BY_NAME[order[0]].cost_tier})"
            )


_validate()


def ladder_for(capability: Capability) -> tuple[ProviderSpec, ...]:
    """Return the ordered rungs for one capability (``L1 .. L<n>``)."""

    return tuple(_BY_NAME[name] for name in LADDER_ORDER[capability])


def providers_for(*capabilities: Capability) -> tuple[ProviderSpec, ...]:
    """Union of the ladders for several capabilities, de-duplicated, order kept."""

    seen: dict[str, ProviderSpec] = {}
    for capability in capabilities:
        for spec in ladder_for(capability):
            seen.setdefault(spec.name, spec)
    return tuple(seen.values())


def provider_named(name: str) -> ProviderSpec | None:
    """Look a provider up by name (used by adapter wiring and ``/providers``)."""

    return _BY_NAME.get(name)


def capability_matrix() -> list[dict[str, object]]:
    """Non-secret view of the registry, used by ``GET /providers``.

    Every key is registry data. There is deliberately **no** ``auth`` key: the
    matrix says ``requires_key`` (a boolean), because the field name alone would
    be a credential-shaped column in a public response (02 §3.11).
    """

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
        for spec in REGISTRY
    ]


__all__ = [
    "LADDER_ORDER",
    "REGISTRY",
    "capability_matrix",
    "ladder_for",
    "provider_named",
    "providers_for",
]
