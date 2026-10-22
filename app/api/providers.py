"""``GET /providers`` — capability matrix without credentials (02 §3.2).

The response is generated from :data:`app.providers.registry.REGISTRY`, never
from a literal list, because a hand-maintained matrix is a document that drifts.
Nothing secret is projectable here by construction: ``ProviderSpec`` has no field
for a key, a URL or a header.

Circuit state (``provider_health``) lands with the breaker in Phase 2; until then
``available`` means "declared, and needs no credential this process lacks".
"""

from __future__ import annotations

from fastapi import APIRouter

from app.providers.registry import capability_matrix

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("")
async def list_providers() -> list[dict[str, object]]:
    """Generated from the registry, never from a hard-coded list."""

    return [
        {
            **entry,
            "available": True,
            "health": {"state": "unknown", "last_ok_at": None, "circuit_state": None},
        }
        for entry in capability_matrix()
    ]
