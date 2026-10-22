"""``GET /providers`` — capability matrix without credentials (02 §3.2)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("")
async def list_providers() -> list[dict[str, object]]:
    """Generated from the registry, never from a hard-coded list."""

    raise NotImplementedError("GET /providers is not implemented (Phase 2)")
