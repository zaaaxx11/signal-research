"""``POST /markets/{resolve,estimate,enrich}`` (02 §3.2).

``enrich`` binds ``MarketRequest`` (``extra="forbid"``) so an unknown knob, a
non-canonical field name or an unknown ``market.kind`` is rejected by pydantic
**before** a job row exists — a 422 must not leave a Pending job behind.

``resolve`` and ``estimate`` are the Phase-1 read paths:

* both read the reference master named by ``MEE_REFERENCE_PATH`` (never a
  hardcoded ticker list) and seed ``instruments`` once per database;
* both answer **200** for ambiguity/narrowing — a question about the universe
  is a product answer, not an HTTP error;
* only ``estimate`` freezes a plan and issues a single-use quote.

``async=false`` runs the job inline and answers ``200`` with the job view; the
default answers ``202`` and runs the job as a **response background task**:
the body leaves before the work starts, but the work is attached to the
response instead of a bare ``create_task``. That distinction is what stops a
job from being destroyed mid-transaction (with an open database connection)
when the caller's loop goes away — the failure mode a fire-and-forget task
produces under ASGI in tests and under a cancelled request in production.

Both paths call the same ``process_job``: "fast because it is small" is how a
second, untested pipeline appears.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask

from app.auth import ApiError, owner_fingerprint
from app.contracts import MarketRequest
from app.jobs.service import job_view, submit_enrich
from app.resolve import (
    build_estimate,
    build_eta,
    build_plan,
    build_quote_document,
    load_reference,
    resolve_market,
)
from app.resolve.reference import Reference, ReferenceError
from app.runtime import Runtime

router = APIRouter(prefix="/markets", tags=["markets"])


def runtime_of(request: Request) -> Runtime:
    """The runtime attached by ``create_app`` (composition root, not a global)."""

    runtime: Runtime = request.app.state.runtime
    return runtime


def _reference(runtime: Runtime) -> Reference:
    """Load the reference master, or answer 503 instead of a traceback."""

    try:
        return load_reference(runtime.settings.reference_path)
    except ReferenceError as exc:
        raise ApiError(
            status_code=503,
            error="reference_unavailable",
            message=f"the reference master is not usable: {exc}",
        ) from exc


def enrich_background(runtime: Runtime, job_id: str) -> BackgroundTask:
    """The 202 body is sent first; the job then runs as part of the response.

    ``run_inline`` never raises (``Runtime._run`` logs and the repository records
    the failure), so a background task cannot turn a sent 202 into a 500.
    """

    return BackgroundTask(runtime.run_inline, job_id)


@router.post("/enrich", status_code=202)
async def enrich(payload: MarketRequest, request: Request) -> Any:
    """Create an enrichment job: 202 + envelope (or 200 + view when inline)."""

    runtime = runtime_of(request)
    api_key = owner_fingerprint(request)
    job = await submit_enrich(
        payload,
        api_key=api_key,
        repository=runtime.repository,
        quotes=runtime.quotes,
    )

    if not payload.async_:
        await runtime.run_inline(job["id"])
        return JSONResponse(
            status_code=200,
            content=await job_view(job["id"], repository=runtime.repository, api_key=api_key),
        )

    return JSONResponse(
        status_code=202,
        content={
            "id": job["id"],
            "status": "Pending",
            "results_location": f"/requests/{job['id']}",
            "expires_at": job["expires_at"],
        },
        background=enrich_background(runtime, job["id"]),
    )


@router.post("/resolve", status_code=200)
async def resolve(payload: MarketRequest, request: Request) -> Any:
    """Dry-run resolver: instruments, honest ``unresolved``, or a question."""

    runtime = runtime_of(request)
    reference = _reference(runtime)
    resolution = resolve_market(reference, payload.market)
    await runtime.ensure_reference_seeded(reference)

    return {
        "spec": resolution.spec.model_dump(mode="json"),
        "instruments": resolution.rows(),
        "unresolved": list(resolution.unresolved),
        "narrowing": resolution.narrowing,
        # ``resolve`` never freezes anything; the keys are present (null) so a
        # client can branch on one envelope for resolve and estimate.
        "plan": None,
        "quote_id": None,
    }


@router.post("/estimate", status_code=200)
async def estimate(payload: MarketRequest, request: Request) -> Any:
    """Dry-run estimate + single-use quote (02 §3.2, Phase-1 gates 5-6).

    Two "no universe" answers are deliberately different:

    * ``narrowing.required`` — the query names a scope but no universe, so the
      body carries questions (HTTP 200, never a 4xx: 03 §3.4 calls this a
      clarification, and the frozen Phase-1 contract renders it as 200);
    * nothing resolved — the caller named explicit instruments and none exists.
      There is no question worth asking, so the body carries ``unresolved[]``
      and no plan. Neither case issues a quote.
    """

    runtime = runtime_of(request)
    reference = _reference(runtime)
    resolution = resolve_market(reference, payload.market)
    await runtime.ensure_reference_seeded(reference)

    warnings: list[str] = []
    if resolution.unresolved:
        warnings.append(
            "unresolved input(s) excluded from the plan: "
            + ", ".join(str(entry.get("input")) for entry in resolution.unresolved)
        )

    if resolution.needs_narrowing:
        return {
            "estimate": None,  # a row range over an unknown universe is a fabricated number
            "plan": None,
            "eta_seconds": None,
            "eta_basis": None,
            "warnings": warnings
            + ["the request needs narrowing; no plan was frozen and no quote was issued"],
            "narrowing": resolution.narrowing,
            "unresolved": [],
            "quote_id": None,
            "quote_expires_at": None,
            "credits": 0,
        }

    if not resolution.instruments:
        # Not narrowing: the caller named an explicit universe and none of it
        # exists. Issuing a quote over zero symbols would sell an empty plan.
        return {
            "estimate": None,
            "plan": None,
            "eta_seconds": None,
            "eta_basis": None,
            "warnings": warnings
            + ["nothing resolved; no plan was frozen and no quote was issued"],
            "narrowing": resolution.narrowing,
            "unresolved": list(resolution.unresolved),
            "quote_id": None,
            "quote_expires_at": None,
            "credits": 0,
        }

    plan = build_plan(resolution, payload)
    estimate = build_estimate(resolution, plan, payload)
    eta_seconds, eta_basis = build_eta(resolution, plan, payload)
    document = build_quote_document(resolution, plan, payload)

    quote = await runtime.quotes.create(
        api_key=owner_fingerprint(request),
        plan=document,
        now=datetime.now(UTC),
        ttl_s=runtime.settings.quote_ttl_s,
    )

    return {
        "estimate": estimate,
        "plan": plan,
        "eta_seconds": eta_seconds,
        "eta_basis": eta_basis,
        "warnings": warnings,
        "narrowing": resolution.narrowing,
        "unresolved": list(resolution.unresolved),
        "quote_id": quote["id"],
        # The stored expiry, verbatim: a quote has one TTL and one rendering.
        "quote_expires_at": quote["expires_at"],
        "credits": 0,
    }
