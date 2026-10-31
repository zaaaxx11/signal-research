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
import logging
import os
from collections.abc import Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from app import __version__
from app.api import discovery, health, market, markets, news, providers, requests, screens
from app.api import engine as engine_api
from app.api import logos as logos_api
from app.auth import ApiError
from app.config import Settings, get_settings
from app.config_guard import check_startup
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

logger = logging.getLogger("mee.web")


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

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def _decode_once(raw: str) -> str | None:
    """Decode ``%XX`` escapes exactly once, or ``None`` when the path is malformed.

    ``urllib.parse.unquote`` is what this wants, and the import-direction rule
    keeps ``urllib`` out of ``app/`` (it is the root of the legacy transports).
    Fifteen lines of dependency-free decoding are a smaller price than an
    exception to a rule that exists for a reason.

    A malformed escape answers ``None`` and the caller refuses the path. That is
    the whole point: a decoder that guesses — by dropping the ``%``, or by
    leaving the sequence alone — is a traversal bug with a friendly face, and
    the two layers below this one disagree about what the path means.
    """

    if "%" not in raw:
        return raw

    out = bytearray()
    index = 0
    while index < len(raw):
        char = raw[index]
        if char != "%":
            out.extend(char.encode("utf-8"))
            index += 1
            continue
        escape = raw[index + 1 : index + 3]
        if len(escape) != 2 or not set(escape) <= _HEX_DIGITS:
            return None
        out.append(int(escape, 16))
        index += 3
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError:
        return None


class _SpaStaticFiles(StaticFiles):
    """Static files with a single-page-app fallback, scoped to the app.

    Three behaviours, each chosen against a specific failure mode:

    1. **A deep link serves ``index.html``.** ``/app/screener/value`` is a
       client route; there is no file by that name, so the 404 becomes the
       shell and the client takes over.
    2. **A missing *asset* stays a 404.** The classic Vite-behind-a-subpath bug
       is a chunk requested from the wrong base; if this fallback answered it
       with HTML, the browser would receive a 200 and fail with
       ``Unexpected token '<'`` — a misleading error two layers from the cause.
       A path whose last segment has an extension is therefore never the SPA.
    3. **``/app/api/…`` is never the SPA.** No API lives under the app prefix;
       a request that lands there is a client bug, and it must fail as a 404
       rather than render a shell that looks successful to ``fetch()``. The same
       applies to every name the API already owns at the root (``/app/screens``,
       ``/app/health``…): those segments belong to the API, so the mount will not
       invent a client route inside one of them. A 200 HTML answer to a mistaken
       API call is the hardest kind of bug to see.

    Directory escape is not this class's job: ``StaticFiles.lookup_path``
    resolves the candidate with ``realpath`` and refuses anything whose common
    path is not the served directory. This class must not undo that, so it
    refuses to fall back for any request whose shape looks like an escape —
    a dot segment, a repeated slash, a backslash, a NUL, or a percent escape
    that survives one decode — because the fallback would otherwise turn a
    blocked traversal into a 200 shell, which hides an attack the operator
    should see in the log.

    The decision is taken on the **raw** scope path, not on the ``path``
    argument: ``StaticFiles.get_path`` runs ``os.path.normpath``, which
    collapses ``//`` and ``..`` before this method could see them. That
    normalisation is why ``/app//etc/passwd`` used to arrive here as the
    perfectly ordinary client route ``etc/passwd``.
    """

    #: First path segments reserved for something other than the SPA. They are
    #: the API's own top-level namespaces (``app/main.py`` mounts them at the
    #: root) plus the build's own output directory, so a client route may not
    #: claim one by accident.
    reserved_segments: frozenset[str] = frozenset(
        {
            "api",
            "assets",
            "engine",
            "export",
            "health",
            "market",
            "markets",
            "news",
            "providers",
            "requests",
            "screens",
            "sources",
            "symbols",
        }
    )

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or not self._is_client_route(path, scope):
                raise
            return await super().get_response("index.html", scope)

    @staticmethod
    def _raw_path(path: str, scope: Mapping[str, object] | None) -> str:
        """The request path as it arrived, with the mount prefix removed.

        ``scope["path"]`` is the path the server decoded *once*; ``root_path``
        is the prefix this mount consumed. Together they are the only honest
        view of what was asked for. The ``path`` argument is used only when
        there is no scope (a direct call from a test), and then it is treated
        as the raw path.
        """

        if scope is None:
            return "/" + path.lstrip("/")

        root = str(scope.get("root_path") or "")
        raw = str(scope.get("path") or "")
        if root and raw.startswith(root):
            return raw[len(root):] or "/"
        if path:
            return "/" + path.lstrip("/")
        return raw or "/"

    def _is_client_route(
        self, path: str, scope: Mapping[str, object] | None = None
    ) -> bool:
        """Whether a 404 for this request should be answered with the shell.

        Everything is decided after **one** decode of the raw path, because
        that is exactly what the server did to the URL: a second decode here
        turns ``%252e%252e`` into ``..`` and makes the shape visible, while a
        path that still carries a ``%`` after that decode is the signature of
        double encoding and is refused rather than interpreted.

        What is left is the client's own vocabulary: word slugs, no extension
        (a last segment with a dot is an asset), and never a name the API
        already owns at the root.
        """

        decoded = _decode_once(self._raw_path(path, scope))
        if decoded is None:  # a malformed escape is not a client route
            return False

        if not decoded.startswith("/"):
            return False
        # NUL truncates or splits a path in every layer below this one; a
        # backslash is a separator for the filesystems this may run on; a
        # residual ``%`` is an encoding this method refused to resolve.
        if "\x00" in decoded or "\\" in decoded or "%" in decoded:
            return False

        body = decoded[1:]
        if "//" in body:
            return False
        # One trailing slash is the same route (browsers and bookmarks disagree
        # about it); two are a normalisation probe and stay refused.
        if body.endswith("/"):
            body = body[:-1]
        if not body:
            # The prefix itself, which ``html=True`` already answers with the
            # shell. Reaching the fallback here means index.html is missing.
            return False

        segments = body.split("/")
        # ``.`` and ``..`` are the traversal spellings; a leading dot is a
        # hidden file (``.git/config``, ``.env``) and never a route; an empty
        # segment here is a doubled slash the check above already refused.
        if any(
            segment in {"", ".", ".."} or segment.startswith(".")
            for segment in segments
        ):
            return False

        # Case-insensitive on purpose: ``/app/HEALTH`` and ``/app/health`` are
        # the same namespace, and a case-sensitive check is a coin flip.
        if segments[0].lower() in self.reserved_segments:
            return False

        # A last segment with a dot is a file request (asset, source map,
        # favicon), not a client route. Client routes here are word slugs.
        return "." not in segments[-1]


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
    # The load just verified this exact file with the same loader /health uses;
    # record that verdict so the first poll does not re-hash 23 MB.
    from app.api.health import prime_snapshot_verdict

    prime_snapshot_verdict(path)
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
        application.state.depth_sources = tuple(
            source.name for source in collector.sources
        )
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


_LOG_CONFIGURED = False


def _configure_logging() -> None:
    """Give the ``mee`` logger tree a level and one handler.

    Under uvicorn nothing configures this tree, so the root logger stays at
    WARNING and every `logger.info` in the codebase is dropped. A source that
    failed, a cycle that overran, a snapshot that no longer verified — all of it
    was written and none of it reached the journal. The level is configurable
    (`MEE_LOG_LEVEL`), the handler is added once, and propagation is off so a
    root handler added later cannot print the same line twice.
    """

    global _LOG_CONFIGURED
    if _LOG_CONFIGURED:
        return

    level_name = os.environ.get("MEE_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    logger = logging.getLogger("mee")
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(levelname)s %(name)s %(message)s")
        )
        logger.addHandler(handler)
    logger.propagate = False
    _LOG_CONFIGURED = True


def _log_web_ui(state: health.WebUiState) -> None:
    """Say once, at startup, whether ``/app`` can answer — and how to fix it.

    The mount used to be skipped in silence: the API came up, the container's
    healthcheck (which watched the engine, not the shell) stayed green, and
    every ``/app`` URL answered 404. One line at boot is the cheapest place to
    say otherwise, and it names the command rather than the symptom.

    ``ERROR`` when the deployment says it serves a UI (``MEE_SERVE_UI=1``, which
    the image sets) and there is nothing to serve; ``WARNING`` in auto mode,
    where a developer's unbuilt tree is a normal state. The happy path logs
    nothing: a line per boot that always says "fine" is a line nobody reads.
    """

    if not state.enabled:
        logger.debug("web UI is switched off; GET /app answers 404")
        return
    if not state.index.is_file():
        logger.log(
            logging.ERROR if state.required else logging.WARNING,
            "web UI is not mounted: %s is missing, so GET /app answers 404. "
            "Rebuild it with: cd web && %s",
            state.index,
            "npm run build",
        )
        return
    try:
        built = state.index.stat().st_mtime
    except OSError:  # vanished between the is_file() above and here
        return
    if state.newest_source_mtime is not None and state.newest_source_mtime > built:
        logger.warning(
            "web UI is stale: %s is older than %s by %ds. Rebuild it with: "
            "cd web && npm run build",
            state.index,
            state.newest_source,
            int(state.newest_source_mtime - built),
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application from settings (no I/O at import time)."""

    _configure_logging()
    #: A mistyped variable is invisible by construction: pydantic-settings
    #: resolves the names it models and ignores every other key, so a stray
    #: ``MEE_DEPTH_UNIVERSE`` sat next to ``MEE_DEPTH_UNIVERSE_PATH`` and the
    #: collector quietly ran on its default universe. Name each offending key
    #: once, before anything reads the environment and builds on the wrong
    #: value. Warning by default: a stray shell variable must not take a
    #: serving process down, so refusing to start stays an opt-in
    #: (``MEE_STRICT_ENV=1``).
    check_startup()
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
    mount_router(application, discovery.router)
    mount_router(application, news.router)
    #: ``GET /logos/{symbol}``: the brand mark on its own URL, so a 100-row
    #: projection carries none of its bytes and every row still has a ``src``.
    mount_router(application, logos_api.router)
    mount_router(application, sources_router)
    mount_router(application, engine_api.router)

    ui_dir = Path(__file__).resolve().parent.parent / "ui"
    if ui_dir.is_dir():
        application.mount("/ui", StaticFiles(directory=ui_dir, html=True), name="ui")

        @application.get("/", include_in_schema=False)
        async def _ui_root() -> RedirectResponse:
            return RedirectResponse(url="/ui/")

    #: The new interface (plan §3): a Vite build in ``web/dist``, mounted at
    #: ``/app`` until parity, when ``/`` learns to redirect here (D9).
    #:
    #: Conditional on the build artefact, not on the source tree: ``dist/`` is
    #: generated by ``npm run build`` and is not committed, so a checkout that
    #: has never built must still import this module and serve the API. The
    #: alternative — ``check_dir=True`` against a missing directory — makes an
    #: unbuilt tree unstartable, which is a build-order trap rather than a
    #: safeguard.
    #:
    #: The same resolution is the health check's evidence
    #: (``app.api.health.resolve_web_ui``): one object says where the shell is,
    #: whether this deployment promises one, and what this process mounted. Two
    #: resolutions would be two answers, and the second is always the one that
    #: disagrees.
    web = health.resolve_web_ui()
    web_mounted = web.index.is_file()
    if web_mounted:

        # GET *and* HEAD: FastAPI 0.159 does not add HEAD for a GET route (the
        # way Starlette's Route does), and a monitor or cache issuing HEAD on
        # the entry point should not get a 405 for a document it can GET.
        @application.api_route(
            "/app", methods=["GET", "HEAD"], include_in_schema=False
        )
        async def _app_root() -> FileResponse:
            """Answer the bare prefix with the shell, not a trailing-slash 307.

            ``StaticFiles(html=True)`` would redirect ``/app`` to ``/app/``.
            Both are correct URLs, but a redirect on the entry point is one
            more thing that can be cached differently from the asset URLs it
            hands out, so the canonical entry is served directly.
            """

            return FileResponse(web.index, media_type="text/html")

        # Added after the exact route above on purpose: Starlette matches in
        # registration order, and the mount would otherwise claim "/app" and
        # answer it with the redirect this route exists to avoid.
        application.mount(
            "/app", _SpaStaticFiles(directory=web.dist_dir, html=True), name="app"
        )

    #: Frozen at build of this app: ``mounted`` is the only field a later
    #: filesystem check cannot reconstruct, and it is what makes a
    #: ``npm run build`` after boot visible as a 404 instead of a healthy shell.
    application.state.web_ui = replace(web, mounted=web_mounted)
    _log_web_ui(application.state.web_ui)

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
