"""``GET /requests``, export, cancel and retry (02 §3.2)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/requests", tags=["requests"])


@router.get("/{request_id}")
async def get_request(request_id: str) -> dict[str, Any]:
    """Return the job view; ownership is checked before expiry and file lookup."""

    raise NotImplementedError("GET /requests/{id} is not implemented (Phase 0 RED)")


@router.get("/{request_id}/export/{fmt}")
async def export_request(request_id: str, fmt: str) -> Any:
    """Stream one export artifact; ``410`` after expiry, ``422`` for unknown fmt."""

    raise NotImplementedError("GET /requests/{id}/export/{fmt} is not implemented (Phase 0 RED)")


@router.post("/{request_id}/cancel", status_code=200)
async def cancel_request(request_id: str) -> dict[str, Any]:
    """Cancel: Pending -> Failure; Processing -> flag; terminal -> 409."""

    raise NotImplementedError("POST /requests/{id}/cancel is not implemented (Phase 0 RED)")


@router.post("/{request_id}/retry", status_code=200)
async def retry_request(request_id: str) -> dict[str, Any]:
    """Retry only a retryable Failure with enough artifacts to resume."""

    raise NotImplementedError("POST /requests/{id}/retry is not implemented (Phase 0 RED)")
