"""ASGI entrypoint.

One error envelope for every failure (02 §3.2): ``{"error", "errorMessage"}``.
FastAPI's own 422 stays untouched — it is the contract the clients already parse,
and it names the offending field, which is the useful half of a validation error.

The runtime is attached to ``app.state`` rather than imported as a module global:
the app is built from settings, so two apps in one process (the test suite does
exactly that) never share a database or an export directory.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import health, market, markets, news, providers, requests, screens
from app.api import engine as engine_api
from app.auth import ApiError
from app.config import Settings, get_settings
from app.engine.ingest import load_sec_dataset
from app.engine.service import EngineService
from app.export.writer import ExportFormatError, ExportFormatPending
from app.jobs.errors import JobError, QuoteError
from app.news.service import build_news_service
from app.runtime import Runtime
from app.sources.api import router as sources_router


def _envelope(error: str, message: str) -> dict[str, str]:
    return {"error": error, "errorMessage": message}


def _code(exc: BaseException) -> str:
    """``JobNotFound`` → ``job_not_found``; stable, greppable, no PII."""

    name = type(exc).__name__
    return "".join(
        f"_{char.lower()}" if char.isupper() and index else char.lower()
        for index, char in enumerate(name)
    )


def mount_router(application: FastAPI, router: APIRouter) -> None:
    """Attach ``router``'s path operations directly to the app.

    FastAPI 0.159 keeps ``include_router`` lazy: the app ends up holding an
    opaque ``_IncludedRouter`` whose ``path`` is ``None``, so anything that
    introspects ``app.routes[*].path`` (a deployment smoke check, an operator,
    the screen suite's mount assertion) sees an app with no ``/screens`` route
    even though requests would match. Appending the router's own ``APIRoute``
    objects is what ``include_router`` used to do before it became lazy: one
    flat, introspectable route table, and no dependency/tag context is lost
    because these routers declare neither.

    A router with include-level ``dependencies``/``tags`` must keep using
    ``include_router``; that context is exactly what a flat append drops.
    """

    for route in router.routes:
        application.router.routes.append(route)


@asynccontextmanager
async def _engine_lifespan(application: FastAPI):
    """Optionally load one SEC dataset before serving requests.

    The engine is deliberately empty by default: a process should not surprise
    its operator with an EDGAR fetch. Setting ``MEE_ENGINE_SYMBOLS`` (comma
    separated) opts in; ``MEE_ENGINE_CACHE_DIR`` chooses the per-CIK document
    cache. A failed load is recorded on ``state.engine_startup_error`` and the
    app still starts, because an unavailable enrichment source must degrade to
    the honest ``503`` route rather than make the whole API unstartable.
    """

    raw_symbols = os.environ.get("MEE_ENGINE_SYMBOLS", "")
    symbols = tuple(
        part.strip().upper() for part in raw_symbols.split(",") if part.strip()
    )
    if symbols:
        raw_cache = os.environ.get("MEE_ENGINE_CACHE_DIR", "").strip()
        cache_dir = Path(raw_cache).expanduser() if raw_cache else None
        try:
            dataset = await load_sec_dataset(symbols, cache_dir=cache_dir)
        except Exception as exc:  # noqa: BLE001 - startup must stay observable
            application.state.engine_startup_error = (
                f"{type(exc).__name__}: {exc}"
            )
        else:
            application.state.engine = EngineService.from_dataset(dataset)
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application from settings (no I/O at import time)."""

    runtime = Runtime(settings or get_settings())
    application = FastAPI(
        title="Ztockz",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        lifespan=_engine_lifespan,
    )
    application.state.runtime = runtime
    #: Read-only engine projections (catalog/identity). The full ingestion
    #: service will replace this object when the source adapters land; the
    #: route contract does not change.
    application.state.engine = EngineService()
    #: ``GET /market/quote`` builds its engine on first use (settings are the
    #: only input); tests and operators may replace it without a code path.
    application.state.quote_engine = None
    #: ``GET /news/{symbol}`` (P12): one service per application, so its
    #: fifteen-minute cache is a property of the process and not of a request.
    #: Built here rather than in the route for the same reason the verifiers are
    #: built in the runtime: a deployment's provider set comes from settings, and
    #: a test replaces the object on ``app.state`` instead of patching a global.
    application.state.news = build_news_service(runtime.settings)

    application.include_router(health.router)
    mount_router(application, market.router)
    application.include_router(markets.router)
    application.include_router(requests.router)
    application.include_router(providers.router)
    mount_router(application, screens.router)
    mount_router(application, news.router)
    mount_router(application, sources_router)
    mount_router(application, engine_api.router)

    ui_dir = Path(__file__).resolve().parent.parent / "ui"
    if ui_dir.is_dir():
        application.mount("/ui", StaticFiles(directory=ui_dir, html=True), name="ui")

        @application.get("/", include_in_schema=False)
        async def _ui_root() -> RedirectResponse:
            return RedirectResponse(url="/ui/")

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
