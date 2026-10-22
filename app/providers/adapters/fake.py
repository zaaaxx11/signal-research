"""Deterministic fake provider for Phase 0.

It must read ``tests/fixtures/fake_universe.json`` and never touch the network.
RED: the fetch/parse methods are stubs.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.contracts import Capability
from app.providers.base import ProviderSpec, Verdict

FAKE_NAME = "fake"


def fake_spec() -> ProviderSpec:
    """Provider spec for the fake rung (T0, trust 1, no key)."""

    raise NotImplementedError("fake provider spec is not implemented (Phase 0 RED)")


class FakeProvider:
    """In-process provider: same interface as a real adapter, zero egress."""

    name = FAKE_NAME

    async def fetch(
        self, capability: Capability, symbols: Sequence[str], fields: set[str]
    ) -> Mapping[str, Any]:
        raise NotImplementedError("FakeProvider.fetch is not implemented (Phase 0 RED)")

    def verdict(self, raw: Mapping[str, Any], symbol: str) -> Verdict:
        raise NotImplementedError("FakeProvider.verdict is not implemented (Phase 0 RED)")

    def parse(self, raw: Mapping[str, Any], symbol: str) -> list[dict[str, Any]]:
        raise NotImplementedError("FakeProvider.parse is not implemented (Phase 0 RED)")
