"""``GET /screens`` and ``POST /screens/{id}/export``.

Browse is a display, so the route has one job: serve the snapshot document. It
never paginates (no ``cursor`` parameter exists) and it never lets ``limit``
widen the page — ``limit`` is accepted so a client that already sends it is not
broken by a 422, and ignored past :data:`~app.screen.snapshot.DISPLAY_ROWS`.

Export is the two-scope bundle and it runs as a **response background task**,
the same way ``POST /markets/enrich`` runs a job: the 202 body leaves first, the
work is attached to the response instead of a bare ``create_task`` (which can be
destroyed mid-write when the caller's loop goes away). Preflight happens before
a job row exists — an unknown screen is a 404, an unknown format a 422, and
neither leaves a Pending job behind to poll forever.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from app.api.markets import runtime_of
from app.auth import ApiError, owner_fingerprint
from app.export.manifest import file_sha256
from app.runtime import Runtime
from app.screen.export import (
    DATA_FORMATS,
    EXPORT_FORMATS,
    ScreenNotFound,
    UnknownScreenFormat,
    export_bundle,
)
from app.screen.schema import SCREEN_RESPONSE_SCHEMA
from app.screen.snapshot import DISPLAY_ROWS, load_screen
from app.screen.scanner import ScanError

router = APIRouter(prefix="/screens", tags=["screens"])

#: ``region`` is a closed vocabulary (PLAN-screen.md §2): ``idx`` is not a
#: smaller US, and a silently-ignored region would serve the wrong tab.
Region = Literal["us", "global"]
#: The three transparent sorts (PLAN §3).
Sort = Literal["market_cap", "turnover", "gainers"]

#: The job service name a client sees in ``GET /requests/{id}``.
SCREEN_EXPORT_SERVICE = "screen_export"


class ExportPayload(BaseModel):
    """``POST /screens/{id}/export`` body; an unknown knob is a 422."""

    model_config = ConfigDict(extra="forbid")

    formats: list[str] = Field(default_factory=lambda: list(DATA_FORMATS))


@router.get("")
async def browse(
    request: Request,
    region: Region = "us",
    sort: Sort = "market_cap",
    limit: int = DISPLAY_ROWS,
) -> dict[str, Any]:
    """Top 20 of the ``(region, sort)`` snapshot; ``total`` is the universe."""

    # ``limit`` is accepted for compatibility and deliberately ignored: the
    # display is the top 20 of a snapshot, and a knob that could widen it to 23
    # would turn a display into an unpaginated dump. Requesting limit=100 is not
    # an error — it just cannot buy more rows than the contract sells.
    del limit

    runtime = runtime_of(request)
    try:
        return await load_screen(region, sort, repository=runtime.screen_repository)
    except ScanError as exc:
        raise ApiError(
            status_code=503,
            error="scanner_unavailable",
            message=f"the scanner could not answer and no snapshot exists yet: {exc}",
        ) from exc


@router.get("/schema")
async def schema() -> dict[str, object]:
    """The JSON Schema for a browse response, versioned with the route."""

    return SCREEN_RESPONSE_SCHEMA


@router.post("/{screen_id}/export", status_code=202)
async def export(
    screen_id: str, payload: ExportPayload, request: Request
) -> Any:
    """Publish ``all`` + ``top20`` artifacts as a job; 202 + ``job_id``."""

    runtime = runtime_of(request)
    formats = tuple(payload.formats)
    unknown = [fmt for fmt in formats if fmt not in EXPORT_FORMATS]
    if not formats or unknown:
        raise ApiError(
            status_code=422,
            error="unknown_format",
            message=(
                f"unknown export format(s) {unknown or ['<none>']}; expected any of "
                f"{', '.join(EXPORT_FORMATS)}"
            ),
        )

    screen = await runtime.screen_repository.get(screen_id)
    if screen is None:
        raise ApiError(
            status_code=404,
            error="screen_not_found",
            message=f"screen {screen_id} does not exist",
        )

    job = await runtime.repository.create(
        params={
            "screen_id": screen_id,
            "region": screen["region"],
            "sort": screen["sort"],
            "formats": list(formats),
        },
        api_key=owner_fingerprint(request),
        service=SCREEN_EXPORT_SERVICE,
        rows_target=int(screen.get("total_count") or 0),
    )
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job["id"],
            "screen_id": screen_id,
            "status": "Pending",
            "formats": list(formats),
            "results_location": f"/requests/{job['id']}",
            "expires_at": job["expires_at"],
        },
        background=BackgroundTask(run_screen_export, runtime, job["id"], screen_id, formats),
    )


async def run_screen_export(
    runtime: Runtime, job_id: str, screen_id: str, formats: tuple[str, ...]
) -> None:
    """Write the bundle, register one artifact per scope+format, finish the job.

    The job record is the only thing a client polls, so every outcome has to
    land in it: a Success with eight artifacts, or a Failure with the sentence
    that explains why. Escaping as a 500 after the 202 was sent is not an
    outcome a client can act on.
    """

    jobs = runtime.repository
    try:
        await jobs.claim_job(job_id, worker_id="screen-export")
        bundle = await export_bundle(
            screen_id,
            repository=runtime.screen_repository,
            directory=runtime.export_dir / f"screen-{job_id}",
            formats=formats,
            deep_engine=runtime.screen_deep_engine,
            job_id=job_id,
        )
        for scope in bundle.scopes.values():
            for fmt, path in scope.files.items():
                await jobs.record_artifact(
                    job_id,
                    kind=f"{scope.scope}_{fmt}",
                    path=str(path),
                    sha256=file_sha256(path),
                    row_count=scope.row_count,
                    bytes_=Path(path).stat().st_size,
                )
        await jobs.complete(
            job_id,
            result_count=bundle.scopes["all"].row_count,
            result_mode="bundle",
            usage={
                "scopes": {
                    name: {
                        "row_count": scope.row_count,
                        "deep_rows": scope.deep_rows,
                        "manifest": scope.manifest_path.name,
                    }
                    for name, scope in bundle.scopes.items()
                },
                "as_of_achieved": bundle.as_of,
            },
        )
    except (ScreenNotFound, UnknownScreenFormat) as exc:
        # Raised before the plan is refused; a client error, not a retry.
        await jobs.fail(job_id, error=f"{type(exc).__name__}: {exc}", retryable=False)
    except Exception as exc:  # noqa: BLE001 - the job row is the error channel
        await jobs.fail(job_id, error=f"{type(exc).__name__}: {exc}", retryable=False)


__all__ = ["SCREEN_EXPORT_SERVICE", "browse", "export", "router", "run_screen_export"]
