"""Provider adapters. Wiring lives here, order lives in ``registry.py``.

Registration rule (02 §3.4): an adapter never adds itself to a ladder — this
module maps *names the registry declared* to implementations, and the ladder
walks the registry. A registry name without an adapter is a rung the ladder
reports as ``skipped`` (honest: the capability is declared, the implementation
is not).

Fase 2 ships the five QUOTE rungs. ``sec_edgar``, ``ecb``, ``coingecko``,
``ishares`` and ``openfigi`` are declared for coverage but have no adapter yet,
so ``/providers`` marks them unavailable rather than promising data nobody wrote.
"""

from __future__ import annotations

from typing import Mapping

from app.providers.adapters._support import HttpAdapter
from app.providers.adapters.finnhub import FinnhubAdapter
from app.providers.adapters.nasdaq import NasdaqAdapter
from app.providers.adapters.polygon import PolygonAdapter
from app.providers.adapters.stooq import StooqAdapter
from app.providers.adapters.yahoo import YahooAdapter

#: Adapters implemented in this phase, keyed by the registry name they serve.
ADAPTER_CLASSES: tuple[type[HttpAdapter], ...] = (
    YahooAdapter,
    StooqAdapter,
    NasdaqAdapter,
    FinnhubAdapter,
    PolygonAdapter,
)

#: Names with an implementation: ``/providers`` uses this for ``available``.
ADAPTER_NAMES: frozenset[str] = frozenset(cls.name for cls in ADAPTER_CLASSES)


def build_adapters(*, timeout_s: float = 20.0) -> dict[str, HttpAdapter]:
    """One instance per implemented provider (fresh, stateless, no key read)."""

    return {cls.name: cls(timeout_s=timeout_s) for cls in ADAPTER_CLASSES}


def serve(adapters: Mapping[str, HttpAdapter], capability: str) -> dict[str, HttpAdapter]:
    """Filter a wiring map down to adapters that implement ``capability``."""

    return {
        name: adapter
        for name, adapter in adapters.items()
        if capability in {item.value for item in adapter.capabilities}
    }


__all__ = ["ADAPTER_CLASSES", "ADAPTER_NAMES", "HttpAdapter", "build_adapters", "serve"]
