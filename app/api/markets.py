"""``POST /markets/{resolve,estimate,enrich}`` (02 §3.2).

``enrich`` is the only route implemented in Phase 0. It binds
``MarketRequest`` (``extra="forbid"``) so an unknown knob, a non-canonical field
name or an unknown ``market.kind`` is rejected by pydantic **before** a job row
exists — a 422 must not leave a Pending job behind.

``async=false`` runs the job inline and answers ``200`` with the job view; the
default answers ``202`` with the envelope and lets the runtime run it. Both paths
call the same ``process_job``: "fast because it is small" is how a second,
untested pipeline appears.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.auth import ApiError, owner_fingerprint
from app.contracts import MarketRequest
from app.jobs.service import job_view, submit_enrich
from app.runtime import Runtime

router = APIRouter(prefix="/markets", tags=["markets"])


def runtime_of(request: Request) -> Runtime:
    """The runtime attached by ``create_app`` (composition root, not a global)."""

    runtime: Runtime = request.app.state.runtime
    return runtime


@router.post("/enrich", status_code=202)
async def enrich(payload: MarketRequest, request: Request, response: Response) -> Any:
    """Create an enrichment job: 202 + envelope (or 200 + view when inline)."""

    runtime = runtime_of(request)
    api_key = owner_fingerprint(request)
    job = await submit_enrich(payload, api_key=api_key, repository=runtime.repository)

    if not payload.async_:
        await runtime.run_inline(job["id"])
        return JSONResponse(
            status_code=200,
            content=await job_view(job["id"], repository=runtime.repository, api_key=api_key),
        )

    runtime.spawn(job["id"])
    response.status_code = 202
    return {
        "id": job["id"],
        "status": "Pending",
        "results_location": f"/requests/{job['id']}",
        "expires_at": job["expires_at"],
    }


@router.post("/estimate", status_code=200)
async def estimate() -> Any:
    """Dry-run estimate + quote. Phase 1 deliverable."""

    raise ApiError(
        status_code=501,
        error="not_implemented",
        message="POST /markets/estimate arrives in Phase 1 (resolver + quote store)",
    )


@router.post("/resolve", status_code=200)
async def resolve() -> Any:
    """Dry-run resolver. Phase 1 deliverable."""

    raise ApiError(
        status_code=501,
        error="not_implemented",
        message="POST /markets/resolve arrives in Phase 1 (resolver)",
    )
