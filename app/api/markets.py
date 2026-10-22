"""``POST /markets/{resolve,estimate,enrich}`` (02 §3.2)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/markets", tags=["markets"])


@router.post("/enrich", status_code=202)
async def enrich(payload: dict[str, Any]) -> dict[str, Any]:
    """Create an enrichment job and return 202 with the job envelope.

    RED: accepts a raw dict so the unimplemented path is visible; the
    implementation must bind ``MarketRequest`` (extra="forbid") here.
    """

    raise NotImplementedError("POST /markets/enrich is not implemented (Phase 0 RED)")


@router.post("/estimate", status_code=200)
async def estimate(payload: dict[str, Any]) -> dict[str, Any]:
    """Dry-run estimate + quote. Phase 1 deliverable; stub here for the surface."""

    raise NotImplementedError("POST /markets/estimate is not implemented (Phase 1)")


@router.post("/resolve", status_code=200)
async def resolve(payload: dict[str, Any]) -> dict[str, Any]:
    """Dry-run resolver. Phase 1 deliverable; stub here for the surface."""

    raise NotImplementedError("POST /markets/resolve is not implemented (Phase 1)")
