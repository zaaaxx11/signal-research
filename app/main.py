"""ASGI entrypoint.

One error envelope for every failure (02 §3.2): ``{"error", "errorMessage"}``.
FastAPI's own 422 stays untouched — it is the contract the clients already parse,
and it names the offending field, which is the useful half of a validation error.

The runtime is attached to ``app.state`` rather than imported as a module global:
the app is built from settings, so two apps in one process (the test suite does
exactly that) never share a database or an export directory.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.api import health, markets, providers, requests
from app.auth import ApiError
from app.config import Settings, get_settings
from app.export.writer import ExportFormatError, ExportFormatPending
from app.jobs.errors import JobError, QuoteError
from app.runtime import Runtime


def _envelope(error: str, message: str) -> dict[str, str]:
    return {"error": error, "errorMessage": message}


def _code(exc: BaseException) -> str:
    """``JobNotFound`` → ``job_not_found``; stable, greppable, no PII."""

    name = type(exc).__name__
    return "".join(
        f"_{char.lower()}" if char.isupper() and index else char.lower()
        for index, char in enumerate(name)
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application from settings (no I/O at import time)."""

    runtime = Runtime(settings or get_settings())
    application = FastAPI(
        title="Ztockz",
        version=__version__,
        docs_url=None,
        redoc_url=None,
    )
    application.state.runtime = runtime

    application.include_router(health.router)
    application.include_router(markets.router)
    application.include_router(requests.router)
    application.include_router(providers.router)

    @application.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content=_envelope(exc.error, exc.message)
        )

    @application.exception_handler(JobError)
    async def _job_error(_request: Request, exc: JobError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content=_envelope(_code(exc), str(exc))
        )

    @application.exception_handler(QuoteError)
    async def _quote_error(_request: Request, exc: QuoteError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content=_envelope(_code(exc), str(exc))
        )

    @application.exception_handler(ExportFormatError)
    async def _export_format_error(_request: Request, exc: ExportFormatError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content=_envelope("export_format_error", str(exc))
        )

    @application.exception_handler(ExportFormatPending)
    async def _export_format_pending(
        _request: Request, exc: ExportFormatPending
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content=_envelope("format_not_implemented", str(exc))
        )

    return application


app = create_app()
