"""``GET /health`` — process liveness only (02 §3.2)."""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. Readiness (DB, providers) is added with the DB layer."""

    return {"status": "ok", "version": __version__}
