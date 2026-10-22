"""``GET /requests``, export, cancel and retry (02 §3.2, §3.11).

Order of checks on every read is the security contract, not an optimisation:

1. **ownership** — a foreign id answers 404 exactly like an unknown id;
2. **expiry** — owner-only, because telling a stranger "expired" confirms the id;
3. **artifact lookup** — only then touch the filesystem.

The export route serves the artifact the worker wrote, never a re-render: a
download that re-runs enrichment would silently produce a *different* table from
the one the manifest describes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from app.api.markets import runtime_of
from app.auth import ApiError, owner_fingerprint
from app.export.writer import FORMATS, IMPLEMENTED_FORMATS
from app.jobs.repository import SUCCESS
from app.jobs.service import cancel_job, job_view, owned_job, retry_job

router = APIRouter(prefix="/requests", tags=["requests"])

MEDIA_TYPES: dict[str, str] = {
    "csv": "text/csv; charset=utf-8",
    "json": "application/json",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "parquet": "application/vnd.apache.parquet",
}


@router.get("/{request_id}")
async def get_request(request_id: str, request: Request) -> dict[str, Any]:
    """Return the job view; ownership is checked before expiry and file lookup."""

    runtime = runtime_of(request)
    return await job_view(
        request_id, repository=runtime.repository, api_key=owner_fingerprint(request)
    )


@router.get("/{request_id}/export/{fmt}")
async def export_request(request_id: str, fmt: str, request: Request) -> Any:
    """Serve one export artifact; ``410`` after expiry, ``422``/``501`` for fmt."""

    if fmt not in FORMATS:
        raise ApiError(
            status_code=422,
            error="unknown_format",
            message=f"unknown export format {fmt!r}; expected one of {', '.join(FORMATS)}",
        )
    if fmt not in IMPLEMENTED_FORMATS:
        raise ApiError(
            status_code=501,
            error="format_not_implemented",
            message=f"export format {fmt!r} is declared but its writer is a later phase",
        )

    runtime = runtime_of(request)
    job = await owned_job(
        request_id, repository=runtime.repository, api_key=owner_fingerprint(request)
    )

    expires_at = job.get("expires_at")
    if expires_at and datetime.now(UTC) > datetime.fromisoformat(expires_at):
        raise ApiError(
            status_code=410, error="gone", message=f"request {request_id} expired"
        )
    if job["status"] != SUCCESS:
        raise ApiError(
            status_code=409,
            error="not_ready",
            message=f"request {request_id} is {job['status']}; no artifact to download",
        )

    artifact = await runtime.repository.artifact(request_id, fmt)
    if artifact is None:
        raise ApiError(
            status_code=404,
            error="artifact_not_found",
            message=f"request {request_id} has no {fmt} artifact",
        )
    path = Path(artifact["path"])
    if not path.exists():
        raise ApiError(
            status_code=410,
            error="gone",
            message=f"the {fmt} artifact of request {request_id} was removed",
        )
    return FileResponse(
        path,
        media_type=MEDIA_TYPES[fmt],
        filename=f"{request_id}.{fmt}",
        headers={"ETag": f'"{artifact["sha256"]}"'} if artifact.get("sha256") else None,
    )


@router.post("/{request_id}/cancel", status_code=200)
async def cancel_request(request_id: str, request: Request) -> dict[str, Any]:
    """Cancel: Pending -> Failure; Processing -> flag; terminal -> 409."""

    runtime = runtime_of(request)
    api_key = owner_fingerprint(request)
    await owned_job(request_id, repository=runtime.repository, api_key=api_key)
    await cancel_job(request_id, repository=runtime.repository)
    return await job_view(request_id, repository=runtime.repository, api_key=api_key)


@router.post("/{request_id}/retry", status_code=200)
async def retry_request(request_id: str, request: Request) -> dict[str, Any]:
    """Retry only a Failure; a Success is never overwritten by a new guess."""

    runtime = runtime_of(request)
    api_key = owner_fingerprint(request)
    await owned_job(request_id, repository=runtime.repository, api_key=api_key)
    await retry_job(request_id, repository=runtime.repository)
    # The view is rendered before the background task runs, so the caller sees
    # Pending and the job then runs attached to this response (see api/markets).
    return JSONResponse(
        status_code=200,
        content=await job_view(request_id, repository=runtime.repository, api_key=api_key),
        background=BackgroundTask(runtime.run_inline, request_id),
    )
