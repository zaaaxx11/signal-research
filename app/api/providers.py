"""``GET /providers`` — capability matrix without credentials (02 §3.2).

The response is generated from :data:`app.providers.registry.REGISTRY`, never
from a literal list, because a hand-maintained matrix is a document that drifts.
Nothing secret is projectable here by construction: ``ProviderSpec`` has no field
for a key, a URL or a header, and the projection carries ``requires_key`` (a
boolean) rather than the env-var name.

Two columns the registry alone cannot answer are added from the composition
root, not from a second declaration:

* ``available`` — declared **and** implemented **and** (when keyed) credentialed
  in this process. ``false`` is useful information: a client should not plan
  around a rung that cannot answer.
* ``health`` — ``provider_health`` for ``(provider, "quote")`` when a row exists.
  Fase 2 records verdicts but never opens a circuit, so ``closed`` means "nothing
  says otherwise", and ``failures`` is the number worth reading.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("")
async def list_providers(request: Request) -> list[dict[str, Any]]:
    """Generated from the registry, never from a hard-coded list."""

    # Imported inside the handler so the module stays import-cheap; the object is
    # the one ``create_app`` attached, not a module-level global.
    runtime = request.app.state.runtime
    return await runtime.provider_matrix()


__all__ = ["router"]
