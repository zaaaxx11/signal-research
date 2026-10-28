"""ASGI entrypoint.

One error envelope for every failure (02 §3.2): ``{"error", "errorMessage"}``.
FastAPI's own 422 stays untouched — it is the contract the clients already parse,
and it names the offending field, which is the useful half of a validation error.

The runtime is attached to ``app.state`` rather than imported as a module global:
the app is built from settings, so two apps in one process (the test suite does
exactly that) never share a database or an export directory.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
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
from app.engine.ingest import EngineDataset, load_sec_dataset
from app.engine.license import LicensePolicy
from app.engine.quotes import quote_fields
from app.engine.service import EngineService
from app.engine.store import EngineStore
from app.market.depth import DepthCollector, build_depth_collector
from app.market.hub import QuoteHub, build_quote_hub
from app.market.sources import build_quote_engine
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


#: The spellings of a boolean environment flag this process accepts. Anything
#: else — including an empty string — is false, so a typo cannot silently turn
#: a licence boundary or an external fetch on.
_TRUE_FLAGS = frozenset({"1", "true", "yes"})


def _env_flag(name: str) -> bool:
    """Whether ``name`` is set to one of the accepted true spellings."""

    return os.environ.get(name, "").strip().lower() in _TRUE_FLAGS


def _engine_policy(application: FastAPI) -> LicensePolicy:
    """The licence policy this process serves under.

    ``create_app`` builds the policy from ``MEE_ENGINE_PUBLIC_MODE`` and hangs it
    on the placeholder service, so the dataset-backed service built here must
    reuse it: replacing the service must never downgrade a public deployment to
    private. The lifespan is also called directly in tests and by tools with a
    bare ``state`` namespace, hence ``getattr`` rather than an attribute read;
    when no service exists yet the environment is the only source of truth.
    """

    service = getattr(application.state, "engine", None)
    policy = getattr(service, "policy", None)
    if isinstance(policy, LicensePolicy):
        return policy
    return LicensePolicy(public_mode=_env_flag("MEE_ENGINE_PUBLIC_MODE"))


def _configured_symbols() -> tuple[str, ...]:
    """``MEE_ENGINE_SYMBOLS`` as a canonical, de-duplicated tuple."""

    raw = os.environ.get("MEE_ENGINE_SYMBOLS", "")
    return tuple(
        part.strip().upper() for part in raw.split(",") if part.strip()
    )


def _configured_snapshot() -> Path | None:
    """``MEE_ENGINE_SNAPSHOT`` as a path, or ``None`` when unset."""

    raw = os.environ.get("MEE_ENGINE_SNAPSHOT", "").strip()
    return Path(raw).expanduser() if raw else None


def _cache_dir() -> Path | None:
    """``MEE_ENGINE_CACHE_DIR`` as a path, or ``None`` when unset."""

    raw = os.environ.get("MEE_ENGINE_CACHE_DIR", "").strip()
    return Path(raw).expanduser() if raw else None


def _record_startup_error(application: FastAPI, exc: BaseException) -> None:
    """Expose a startup failure on state without aborting the process.

    The exception class is kept in the text because ``RuntimeError`` and
    ``SnapshotError`` are different operator stories (a provider outage versus a
    snapshot that must be regenerated), and the route that degrades to ``503``
    has no other channel to say which one happened.
    """

    application.state.engine_startup_error = f"{type(exc).__name__}: {exc}"


def _load_snapshot(
    application: FastAPI, store: EngineStore, path: Path
) -> EngineDataset | None:
    """Load the configured snapshot; record the failure and fail closed.

    Failing closed is the point: when the operator points the process at a
    snapshot, a silent EDGAR fetch would replace evidence the deployment claims
    to be serving with a different dataset, and the difference would be
    invisible. ``None`` therefore means "no dataset", never "ingest instead".
    """

    try:
        dataset = store.load()
    except Exception as exc:  # noqa: BLE001 - startup must stay observable
        _record_startup_error(application, exc)
        return None
    application.state.engine_snapshot = str(path)
    return dataset


async def _load_ingested_dataset(
    application: FastAPI, symbols: tuple[str, ...], snapshot: Path | None
) -> EngineDataset | None:
    """Ingest ``symbols`` over EDGAR; persist the result when asked to.

    A failed ingestion is reported like any other startup failure. A snapshot
    that cannot be written after a successful ingest is *not* fatal: the process
    has a usable dataset in memory, and refusing to start because a side artefact
    could not be persisted would take a working API down for a caching problem.
    """

    try:
        dataset = await load_sec_dataset(symbols, cache_dir=_cache_dir())
    except Exception as exc:  # noqa: BLE001 - startup must stay observable
        _record_startup_error(application, exc)
        return None
    if snapshot is not None:
        try:
            EngineStore(snapshot).save(dataset)
        except Exception as exc:  # noqa: BLE001 - persistence is a side effect
            application.state.engine_snapshot_error = f"{type(exc).__name__}: {exc}"
    return dataset


async def _engine_quotes(
    application: FastAPI, symbols: tuple[str, ...]
) -> dict[str, dict[str, object]]:
    """Poll the opt-in quote engine; a failure is recorded, never raised.

    Quotes are an enrichment on top of a dataset that is already servable, so a
    provider outage degrades to "no prices this process" and must not make the
    API unstartable. The refresh happens after either source: a snapshot load is
    still allowed to attach live prices.

    An empty target is answered without building a transport at all: the SEC
    identity master can hold ten thousand symbols, and a startup must never turn
    "quotes on" into a bulk crawl. Callers pass fact-bearing symbols only.
    """

    if not symbols:
        return {}
    if not _env_flag("MEE_ENGINE_QUOTES"):
        return {}
    try:
        engine = build_quote_engine(application.state.runtime.settings)
        ticks = await engine.poll_many(symbols)
    except Exception as exc:  # noqa: BLE001 - quotes are optional
        application.state.engine_quote_error = f"{type(exc).__name__}: {exc}"
        return {}
    return {
        symbol: quote_fields(tick)
        for symbol, tick in ticks.items()
        if tick is not None
    }


def _quote_universe(
    settings: Settings, dataset: EngineDataset | None
) -> tuple[str, ...]:
    """Load the QuoteHub universe from an explicit file, env, or the dataset.

    Explicit configuration wins: an operator pointing at a curated liquid
    universe must not silently get the 10k SEC identity master instead. A JSON
    file may be a list of tickers, ``{"symbols": [...]}``, or the probe shape
    ``{"rows": [{"name": ...}]}``.
    """

    path = getattr(settings, "quote_universe_path", None)
    if path:
        raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        if isinstance(raw, Mapping):
            rows = raw.get("symbols")
            if rows is None:
                rows = [row.get("symbol") or row.get("name") for row in raw.get("rows", [])]
        else:
            rows = raw
        items = rows if isinstance(rows, (list, tuple)) else ()
        return tuple(
            str(item).strip().upper()
            for item in items
            if str(item or "").strip()
        )

    configured = os.environ.get("MEE_QUOTE_SYMBOLS", "")
    if configured.strip():
        return tuple(
            part.strip().upper() for part in configured.split(",") if part.strip()
        )

    if dataset is not None:
        master = getattr(dataset, "security_master", None)
        symbols = getattr(master, "symbols", None)
        if callable(symbols):
            resolved = symbols()
            if isinstance(resolved, Iterable) and not isinstance(resolved, (str, bytes)):
                return tuple(str(symbol) for symbol in resolved)
    return ()


async def _start_quote_hub(
    application: FastAPI, settings: Settings, dataset: EngineDataset | None
) -> QuoteHub | None:
    """Start the opt-in hub; a failure is reported, not fatal to the API."""

    if not getattr(settings, "quote_hub", False):
        return None
    try:
        symbols = _quote_universe(settings, dataset)
        if not symbols:
            application.state.quote_hub_error = (
                "QuoteHub is enabled but no universe was configured"
            )
            return None
        hub = build_quote_hub(settings, symbols)
        await hub.start()
        application.state.quote_hub = hub
        return hub
    except Exception as exc:  # noqa: BLE001 - optional enrichment must not kill startup
        application.state.quote_hub_error = f"{type(exc).__name__}: {exc}"
        return None


def _depth_universe(
    settings: Settings, dataset: EngineDataset | None
) -> tuple[str, ...]:
    """Depth universe: its own file/env, then the quote universe, then dataset."""

    path = getattr(settings, "depth_universe_path", None)
    if path is None:
        path = getattr(settings, "quote_universe_path", None)
    if path:
        raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        if isinstance(raw, Mapping):
            rows = raw.get("symbols")
            if rows is None:
                rows = [row.get("symbol") or row.get("name") for row in raw.get("rows", [])]
        else:
            rows = raw
        items = rows if isinstance(rows, (list, tuple)) else ()
        return tuple(
            str(item).strip().upper()
            for item in items
            if str(item or "").strip()
        )
    configured = os.environ.get("MEE_DEPTH_SYMBOLS", "")
    if configured.strip():
        return tuple(
            part.strip().upper() for part in configured.split(",") if part.strip()
        )
    if dataset is not None:
        master = getattr(dataset, "security_master", None)
        symbols = getattr(master, "symbols", None)
        if callable(symbols):
            resolved = symbols()
            if isinstance(resolved, Iterable) and not isinstance(resolved, (str, bytes)):
                return tuple(str(symbol) for symbol in resolved)
    return ()


async def _start_depth(
    application: FastAPI, settings: Settings, dataset: EngineDataset | None
) -> DepthCollector | None:
    """Start the opt-in daily depth collector; failures stay observable."""

    if not getattr(settings, "depth", False):
        return None
    try:
        symbols = _depth_universe(settings, dataset)
        if not symbols:
            application.state.depth_error = (
                "depth is enabled but no universe was configured"
            )
            return None
        collector = build_depth_collector(settings, symbols)
        await collector.start()
        application.state.depth = collector
        return collector
    except Exception as exc:  # noqa: BLE001 - optional enrichment must not kill startup
        application.state.depth_error = f"{type(exc).__name__}: {exc}"
        return None


@asynccontextmanager
async def _engine_lifespan(application: FastAPI):
    """Optionally load one SEC dataset before serving requests.

    The engine is deliberately empty by default: a process should not surprise
    its operator with an EDGAR fetch. Two opt-ins are supported, in this order:

    1. ``MEE_ENGINE_SNAPSHOT`` points at a file that exists — load it, and make
       no network call. A load failure is recorded on
       ``state.engine_startup_error`` and startup continues **without** falling
       back to ingestion.
    2. ``MEE_ENGINE_SYMBOLS`` (comma separated) — ingest from EDGAR;
       ``MEE_ENGINE_CACHE_DIR`` chooses the per-CIK document cache. If a
       snapshot path is configured, the result is written there.

    With neither, nothing is attached and the engine routes answer honestly.
    ``MEE_ENGINE_QUOTES`` opts into a quote snapshot after either source. A
    failed load is recorded on ``state.engine_startup_error`` and the app still
    starts, because an unavailable enrichment source must degrade to the honest
    ``503`` route rather than make the whole API unstartable.
    """

    dataset: EngineDataset | None = None
    quote_symbols: tuple[str, ...] = ()

    snapshot = _configured_snapshot()
    store = EngineStore(snapshot) if snapshot is not None else None
    if snapshot is not None and store is not None and store.exists():
        dataset = _load_snapshot(application, store, snapshot)
        if dataset is not None:
            # A snapshot defines its own universe, but a startup quote pass is
            # bounded to the symbols that actually carry facts: the identity
            # master is 10k+ rows, and polling all of them at boot would be a
            # bulk crawl, not an enrichment.
            quote_symbols = tuple(sorted(dataset.facts))
    else:
        symbols = _configured_symbols()
        if symbols:
            dataset = await _load_ingested_dataset(application, symbols, snapshot)
            if dataset is not None:
                quote_symbols = symbols

    if dataset is not None:
        quotes = await _engine_quotes(application, quote_symbols)
        application.state.engine = EngineService.from_dataset(
            dataset, quotes=quotes, policy=_engine_policy(application)
        )

    runtime = getattr(application.state, "runtime", None)
    settings = getattr(runtime, "settings", None)
    hub = None
    depth = None
    if settings is not None:
        hub = await _start_quote_hub(application, settings, dataset)
        depth = await _start_depth(application, settings, dataset)
    try:
        yield
    finally:
        if depth is not None:
            await depth.stop()
        if hub is not None:
            await hub.stop()
        if hasattr(application.state, "quote_hub"):
            application.state.quote_hub = None
        if hasattr(application.state, "depth"):
            application.state.depth = None


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
    #:
    #: The licence policy is built here, from the environment, because this is
    #: the composition root: the lifespan reuses it when it swaps in the
    #: dataset-backed service, so a public deployment is never silently
    #: downgraded to private. ``MEE_ENGINE_PUBLIC_MODE`` is the deploy switch.
    application.state.engine = EngineService(
        policy=LicensePolicy(public_mode=_env_flag("MEE_ENGINE_PUBLIC_MODE"))
    )
    #: ``GET /market/quote`` builds its engine on first use (settings are the
    #: only input); tests and operators may replace it without a code path.
    application.state.quote_engine = None
    #: The hub is started only when ``MEE_QUOTE_HUB=1``; routes answer 503 when
    #: it is absent rather than scraping on the request path.
    application.state.quote_hub = None
    #: Depth is a separate opt-in daily collector (``MEE_DEPTH=1``); its JSON
    #: store never blocks a quote request.
    application.state.depth = None
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
