"""Read-only ``/engine`` routes.

These routes expose the frozen catalog and the in-memory identity master without
inventing prices or fundamentals. ``POST /engine/screen`` reads a row provider
that was already loaded by ingestion; the route performs no network I/O of its
own, and the one fetch it may trigger — the opt-in quote refresh — is bounded,
licence-gated and reported. A deployment with no provider answers ``503
screen_unavailable`` rather than an empty screen.

**The screen's order of operations is a contract, not an implementation detail**

1. The body is validated first. A malformed filter is a ``422`` even when a
   fetch would have been skipped, because a typo must never look like an empty
   market.
2. A public deployment refuses a query that filters or sorts on an observed
   quote field with ``503 license_unavailable`` — before any poll, and without
   a partial page. Every quote rung is ``INTERNAL``; the caller cannot license
   one by asking for a refresh.
3. Only then does the screen run, and only then may a quote batch be polled.

Every ``200`` therefore carries a ``quote_snapshot`` sidecar stating whether a
refresh happened, which set it targeted, how many symbols were asked and
answered, the newest *exchange* print, and whether a quote-based filter or sort
had to read the pre-refresh snapshot. A quote outage degrades that sidecar to
``quote_unavailable`` and the rows to their un-refreshed values; it never turns
a working screen into a 5xx and never invents a price.

Unknown keys use the same ``{error, errorMessage}`` envelope as the rest of the
API. A security lookup against an empty master is a 404, not an empty 200:
"nobody loaded a master" must not look like "the market has no AAPL".
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.api.cache import cache_headers, not_modified
from app.api.market import quote_engine_of
from app.auth import ApiError
from app.engine.fund_series import fund_series_path, load_fund_series
from app.engine.ingest import load_cached_companyfacts
from app.engine.insider import FILINGS_TTL_S, collect_insider
from app.engine.model import Cell
from app.engine.nport import HoldingsNotFound, HoldingsUnavailable, collect_holdings
from app.engine.ops import cache_stats, dataset_metrics, zero_cache_stats
from app.engine.quotes import (
    MAX_QUOTE_REFRESH_SYMBOLS,
    QUOTE_FIELDS,
    QuoteSnapshot,
    quote_fields,
    snapshot_from_ticks,
)
from app.engine.screener import ScreenError, ScreenQuery, ScreenResult, parse_query
from app.engine.service import EngineService, EngineUnavailable
from app.engine.statements import collect_statements
from app.engine.statement_table import statement_document
from app.market.quote import QuoteTick
from app.screen import index_membership
from app.screen.scanner import ScanError
from app.screen.ticker import peer_document, profile_document

router = APIRouter(prefix="/engine", tags=["engine"])

#: When this process started serving, on the monotonic clock. Monotonic rather
#: than wall time because an uptime must not jump backwards when NTP steps the
#: host clock; module level rather than per-request because the answer is a
#: property of the process, not of the call.
_PROCESS_STARTED = time.monotonic()

#: Why a public deployment may not answer a quote-based query. Named once so the
#: refusal and its documentation cannot drift apart.
_LICENSE_MESSAGE = (
    "quote fields (price, change, volume, prev_close) come from internal "
    "sources that do not permit redistribution; this deployment is public, so "
    "it cannot filter or sort on them"
)


def engine_service_of(request: Request) -> EngineService:
    service = getattr(request.app.state, "engine", None)
    if isinstance(service, EngineService):
        return service
    return EngineService()


async def ticker_universe(request: Request) -> Sequence[Mapping[str, Any]]:
    """The US stock universe the screener scans, through its own scan cache.

    One function, imported at call time so this module's import graph does not
    depend on the selections router. A test can replace it with a fixture, and
    the routes below never touch a provider directly.
    """

    from app.api.screens import market_universe

    return await market_universe(request)


def index_members_for(symbol: str) -> list[str] | None:
    """The index labels the vendored snapshot holds for ``symbol``, for display.

    The snapshot stores tokens (``sp500``, ``ndx``); a header shows names
    (``S&P 500``, ``NASDAQ 100``), so the one mapping in
    :data:`app.screen.index_membership.LABELS` is applied here rather than in
    the client. ``None`` means the snapshot is unavailable (membership is
    unknown); an empty list means it was read and the symbol is in none of the
    four. The distinction is the one :mod:`app.screen.index_membership` exists
    to keep.
    """

    snapshot = index_membership.load()
    if not snapshot.available:
        return None
    return [
        index_membership.LABELS.get(name, name)
        for name in snapshot.indices_for(symbol)
    ]


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    document = engine_service_of(request).health()
    startup_error = getattr(request.app.state, "engine_startup_error", None)
    if startup_error is not None:
        document["startup_error"] = startup_error
    quote_error = getattr(request.app.state, "engine_quote_error", None)
    if quote_error is not None:
        document["quote_error"] = quote_error
    return document


@router.get("/metrics")
async def metrics(request: Request) -> dict[str, Any]:
    """The deployment's operational document: dataset, cache, boot, uptime.

    Always ``200`` while the process can answer. A metrics endpoint that failed
    when the engine failed would be blind exactly when an operator needs it, so
    an empty engine, a cache directory that does not exist and a failed boot are
    all described **inside** the document (``dataset: null``, a zero cache
    block, ``startup_error``) rather than turned into an error status.

    The document carries operational metadata only — counts, ages, booleans,
    one whole-second uptime — and never an absolute path or a cache filename.
    That is what makes it safe to serve in public mode: it states how much was
    cached, not what was licensed. The function's own shapes live in
    :mod:`app.engine.ops`, so the CLI and this route cannot disagree about what
    "the cache" means.
    """

    document = dataset_metrics(engine_service_of(request))
    cache_dir = _engine_cache_dir()
    document["cache"] = (
        cache_stats(cache_dir) if cache_dir is not None else zero_cache_stats()
    )
    document["startup_error"] = getattr(
        request.app.state, "engine_startup_error", None
    )
    document["process"] = {
        "uptime_seconds": max(0, int(time.monotonic() - _PROCESS_STARTED))
    }
    return document


@router.get("/catalog")
async def catalog(request: Request) -> dict[str, Any]:
    return engine_service_of(request).catalog()


@router.get("/catalog/{key}")
async def metric(key: str, request: Request) -> dict[str, Any]:
    document = engine_service_of(request).metric(key)
    if document is None:
        raise ApiError(
            404,
            "metric_not_found",
            f"no metric named {key!r}; GET /engine/catalog lists the 116 fields",
        )
    return document


@router.post("/screen")
async def screen(request: Request) -> dict[str, Any]:
    """Apply a local screen query and state the freshness of its prices.

    The response always names its quote story: ``license_blocked`` when a public
    deployment served SEC values only, ``refresh_disabled`` when the caller
    turned the poll off, ``no_provider`` when nothing backs the rows,
    ``quote_unavailable`` when a batch came back empty, and ``null`` when a
    refresh actually attached at least one price.
    """

    payload = await _screen_payload(request)
    refresh_quotes = bool(payload.pop("refresh_quotes", True))
    service = engine_service_of(request)

    try:
        query = parse_query(payload)
    except ScreenError as exc:
        raise ApiError(422, exc.code, str(exc)) from exc
    uses_quotes = service.uses_quote_fields(query)

    if uses_quotes and not service.quotes_admitted():
        raise ApiError(503, "license_unavailable", _LICENSE_MESSAGE)

    result = _screened(service, query)

    if not service.quotes_admitted():
        # No fetch and no quote cell: the row dicts are projected without the
        # observed fields so a service that had prices attached before the
        # policy was known cannot leak one into a public page.
        return EngineService.screen_document(
            _without_quote_cells(result),
            quote_snapshot=QuoteSnapshot(reason="license_blocked"),
        )

    if not refresh_quotes:
        return EngineService.screen_document(
            result,
            quote_snapshot=QuoteSnapshot(
                reason="refresh_disabled", filter_used_snapshot=uses_quotes
            ),
        )

    if service.dataset is None:
        return EngineService.screen_document(
            result,
            quote_snapshot=QuoteSnapshot(
                reason="no_provider", filter_used_snapshot=uses_quotes
            ),
        )

    result, snapshot = await _refreshed(
        request, service, query, result, uses_quotes=uses_quotes
    )
    return EngineService.screen_document(result, quote_snapshot=snapshot)


@router.get("/security/{symbol}")
async def security(symbol: str, request: Request) -> dict[str, Any]:
    document = engine_service_of(request).security(symbol)
    if document is None:
        raise ApiError(
            404,
            "security_not_found",
            f"security {symbol!r} is not in the loaded identity master",
        )
    return document


@router.get("/peers/{symbol}")
async def peers(symbol: str, request: Request) -> dict[str, Any]:
    """The subject's sector group, nearest market cap first, from the scan.

    The universe is the one ``/screens`` already scans (``ticker_universe``),
    so a header and a peer group are projections of the same rows. Ten rows by
    default; the fallback to the broader industry group is
    :func:`app.screen.ticker.peer_document`'s decision, not the route's.

    Three answers stay distinct: a blocked scan is ``503 scanner_unavailable``
    (no universe was read), a symbol outside the scan is ``404
    security_not_found``, and a known company with no sector and no industry is
    an honest empty ``200`` — the same three-way split the identity peer group
    uses.
    """

    try:
        rows = await ticker_universe(request)
    except ScanError as exc:
        raise ApiError(
            503,
            "scanner_unavailable",
            f"the scanner could not answer the peer group: {exc}",
        ) from exc
    document = peer_document(rows, symbol)
    if document is None:
        raise ApiError(
            404,
            "security_not_found",
            f"security {symbol!r} is not in the scanned universe",
        )
    return document


@router.get("/peers/{symbol}/industry")
async def peers_industry(symbol: str, request: Request, limit: str = "10") -> dict[str, Any]:
    """Who else shares this symbol's SEC industry, computed from identity.

    ``limit`` is taken as raw text and parsed here rather than as an ``int``
    query parameter for the same reason as ``/engine/insider``: a framework
    422 would bypass the house ``{error, errorMessage}`` envelope. The order is
    validation, then lookup, so an unusable limit is a ``422 invalid_limit``
    even for a symbol the master does not hold — a typo in the page size must
    never be reported as "no such company".

    The three answers are deliberately distinct: no master at all is
    ``503 screen_unavailable`` (nothing was ingested), a symbol outside the
    master is ``404 security_not_found``, and a known company with no industry
    or no industry-mates is an honest ``200`` with ``count: 0``. The route is
    read-only and performs no network I/O; ``as_of`` comes from the subject's
    own provenance, never from the request clock.
    """

    bounded = _peers_limit(limit)
    try:
        document = engine_service_of(request).peers(symbol, limit=bounded)
    except EngineUnavailable as exc:
        raise ApiError(503, exc.code, str(exc)) from exc
    if document is None:
        raise ApiError(
            404,
            "security_not_found",
            f"security {symbol!r} is not in the loaded identity master",
        )
    return document


@router.get("/profile/{symbol}")
async def profile(symbol: str, request: Request) -> dict[str, Any]:
    """The ticker page's header: one scan row, projected, nothing recomputed.

    Price, change, volume, market cap, sector, industry, the 52-week range,
    beta and RSI are the row's own cells from the same scan ``/screens`` uses.
    ``prev_close`` is the screener's existing identity over the row's price and
    change; ``index_membership`` is the vendored snapshot the ``idx`` filter
    reads. No quote is fetched and no indicator is recalculated here.

    A symbol outside the scan is ``404 security_not_found``; a blocked scan is
    ``503 scanner_unavailable``. A row whose cells are null returns them as
    null — a header must never turn an unobserved beta into ``0.0``.
    """

    try:
        rows = await ticker_universe(request)
    except ScanError as exc:
        raise ApiError(
            503,
            "scanner_unavailable",
            f"the scanner could not answer the header: {exc}",
        ) from exc
    target = str(symbol or "").strip().upper()
    row = next(
        (
            candidate
            for candidate in rows
            if str(candidate.get("symbol") or "").strip().upper() == target
        ),
        None,
    )
    if row is None:
        raise ApiError(
            404,
            "security_not_found",
            f"security {symbol!r} is not in the scanned universe",
        )
    return profile_document(row, index_members=index_members_for(target))


async def company_profile(symbol: str) -> Mapping[str, Any] | None:
    """The company block for one symbol, behind the seven-day disk cache.

    Imported at call time, like :func:`ticker_universe`: this module's import
    graph stays free of the Yahoo transport, and a test can replace the one
    function with a fixture instead of reaching for a socket.
    """

    from app.market.company import collect_company_profile

    return await collect_company_profile(symbol)


@router.get("/company/{symbol}")
async def company(symbol: str, request: Request) -> dict[str, Any]:
    """The ticker page's Company block: business summary plus key officers.

    One Yahoo ``quoteSummary`` ``assetProfile`` read, served for seven days from
    ``<data root>/company/{symbol}.json`` (``MEE_COMPANY_CACHE_DIR`` overrides
    the directory). A cache hit does not call the network; a Yahoo refusal with
    an expired copy on disk serves that copy and names it ``yahoo-cache-stale``
    in ``source``. With no paragraph on disk and no answer from Yahoo the route
    is ``404 no_company_profile`` — a company block is never fabricated, and a
    blank ``longBusinessSummary`` is not a description.
    """

    document = await company_profile(symbol)
    if document is None:
        raise ApiError(
            404,
            "no_company_profile",
            f"no company profile is available for {symbol!r}",
        )
    return dict(document)


@router.get("/insider/{symbol}")
async def insider(symbol: str, request: Request, limit: str = "5") -> dict[str, Any]:
    """Recent Form 4 filings for one issuer, read from EDGAR's own XML.

    ``limit`` is taken as raw text and parsed here rather than as an ``int``
    query parameter: FastAPI would answer a framework-shaped ``422`` for
    ``?limit=x``, while the house contract is the ``{error, errorMessage}``
    envelope with ``invalid_limit``. The bound is 1..20, so one request cannot
    become a crawl of an issuer's whole filing history.

    The route is deliberately linear about what it knows:

    1. an unusable ``limit`` is rejected before anything else, so a typo never
       looks like an empty filing history and never reaches the network;
    2. no identity master at all is ``503 screen_unavailable`` — "nothing was
       ingested" must not look like "this company has no insiders";
    3. a symbol outside the master is ``404 security_not_found``;
    4. a symbol the index knows without a CIK has no filings to ask for, and
       answers a truthful empty ``200`` naming ``no CIK`` instead of a 503.

    Only then does the collector run. A body that failed is reported in
    ``provider_errors`` and the readable rows still return; when every selected
    filing failed there is no document to serve and the answer is ``503
    insider_unavailable`` rather than an empty page that claims a quiet period.
    ``as_of`` is the newest ``filed`` among the returned rows, never the clock.
    """

    bounded = _insider_limit(limit)
    service = engine_service_of(request)
    master = service.security_master
    if master is None:
        raise ApiError(
            503,
            "screen_unavailable",
            "the engine has no identity master loaded yet; ingest a dataset first",
        )
    record = master.get(symbol)
    if record is None:
        raise ApiError(
            404,
            "security_not_found",
            f"security {symbol!r} is not in the loaded identity master",
        )
    if record.cik is None:
        return {
            "symbol": record.symbol,
            "as_of": None,
            "count": 0,
            "items": [],
            "provider_errors": ["no CIK"],
        }

    items, errors = await collect_insider(
        record.symbol,
        record.cik,
        cache_dir=_engine_cache_dir(),
        limit=bounded,
    )
    if not items and errors:
        raise ApiError(
            503,
            "insider_unavailable",
            "no Form 4 body could be read for this issuer: " + "; ".join(errors),
        )
    document = {
        "symbol": record.symbol,
        "as_of": max((item.filed for item in items), default=None),
        "count": len(items),
        "items": [item.document() for item in items],
        "provider_errors": list(errors),
    }
    return _cached_document(request, document)


def _cached_document(request: Request, document: Mapping[str, Any]) -> Response:
    """One filings document, with its validator and the filings TTL.

    The tag is a **strong** one over the bytes this answer ships, which is the
    honest kind for a document that is a pure function of the filings it read:
    two calls that read the same filings produce the same bytes and the same
    tag, and a filing that appears moves both. It is computed here rather than
    from the request's inputs because the inputs (an EDGAR index plus N bodies)
    are not cheaply hashable, and this body is a few kilobytes — hashing it is
    cheaper than a second read of anything.

    ``Cache-Control`` is :data:`app.engine.insider.FILINGS_TTL_S` — the window
    the dashboard's filings block already uses — rendered by the house helper so
    ``must-revalidate`` rides along: a client may keep the page, but it must ask
    before reusing it, and the ask is a ``304`` with no body. A refusal (404, 422
    or 503) is raised before this is reached and therefore carries no cache
    policy at all: "this company is not in the index" is a fact about the
    request, not a document worth storing.
    """

    body = json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    etag = f'"{hashlib.sha256(body).hexdigest()[:32]}"'
    headers = cache_headers(etag, max_age=int(FILINGS_TTL_S))
    if not_modified(request, etag):
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


def _insider_limit(raw: str) -> int:
    """The validated ``limit``, or ``422 invalid_limit``.

    Blank, non-numeric, zero and anything above twenty are the same refusal:
    the caller asked for a page this endpoint cannot define.
    """

    text = raw.strip() if isinstance(raw, str) else ""
    try:
        value = int(text)
    except ValueError as exc:
        raise ApiError(
            422, "invalid_limit", f"limit must be an integer 1..20, got {raw!r}"
        ) from exc
    if not 1 <= value <= 20:
        raise ApiError(
            422, "invalid_limit", f"limit must be an integer 1..20, got {raw!r}"
        )
    return value


def _peers_limit(raw: str) -> int:
    """The validated ``limit`` for the peer page, or ``422 invalid_limit``.

    Blank, non-numeric, zero and anything above fifty are one refusal: the
    caller asked for a page this endpoint cannot define. Fifty is inclusive
    because a group page is bounded by the identity master, not by a crawl.
    """

    text = raw.strip() if isinstance(raw, str) else ""
    try:
        value = int(text)
    except ValueError as exc:
        raise ApiError(
            422, "invalid_limit", f"limit must be an integer 1..50, got {raw!r}"
        ) from exc
    if not 1 <= value <= 50:
        raise ApiError(
            422, "invalid_limit", f"limit must be an integer 1..50, got {raw!r}"
        )
    return value


def _statements_periods(raw: str) -> int:
    """The validated ``periods`` for the statement table, or ``422 invalid_limit``.

    The bound is 1..10: one column is the minimum a table can have, and ten
    annual columns is more than any issuer's ``companyfacts`` carries in
    comparable shape. Blank, non-numeric, zero and anything above ten are one
    refusal — the caller asked for a table this endpoint cannot define — and it
    is raised before any identity lookup, so a typo in the column count is never
    reported as "no such company" and never reaches the network.
    """

    text = raw.strip() if isinstance(raw, str) else ""
    try:
        value = int(text)
    except ValueError as exc:
        raise ApiError(
            422, "invalid_limit", f"periods must be an integer 1..10, got {raw!r}"
        ) from exc
    if not 1 <= value <= 10:
        raise ApiError(
            422, "invalid_limit", f"periods must be an integer 1..10, got {raw!r}"
        )
    return value


def _holdings_limit(raw: str) -> int:
    """The validated ``limit`` for the ETF holdings page, or ``422 invalid_limit``.

    The bound is 1..500: a fund portfolio is roughly five hundred rows, so the
    page can be the whole book without becoming an unbounded document. Blank,
    non-numeric, zero and anything above five hundred are one refusal — the
    caller asked for a page this endpoint cannot define — and it is raised
    before any identity lookup or network call, so a typo in the page size is
    never reported as "no such company" and never costs an EDGAR request.
    """

    text = raw.strip() if isinstance(raw, str) else ""
    try:
        value = int(text)
    except ValueError as exc:
        raise ApiError(
            422, "invalid_limit", f"limit must be an integer 1..500, got {raw!r}"
        ) from exc
    if not 1 <= value <= 500:
        raise ApiError(
            422, "invalid_limit", f"limit must be an integer 1..500, got {raw!r}"
        )
    return value


def _engine_cache_dir() -> Path | None:
    """``MEE_ENGINE_CACHE_DIR`` as a path, or ``None`` when unset.

    Read per request rather than captured at import time: the directory is a
    deployment setting, and a test or operator may set it after this module was
    imported. Unset means "no disk cache", which is one request per filing and
    never a silent cache from a previous process. Shared by every engine route
    that reads an EDGAR document (insider filings, annual statements, ETF
    holdings): one setting, one cache directory, so a deployment configures it
    once.
    """

    raw = os.environ.get("MEE_ENGINE_CACHE_DIR", "").strip()
    return Path(raw).expanduser() if raw else None


@router.get("/fundamentals/{symbol}")
async def fundamentals(symbol: str, request: Request) -> dict[str, Any]:
    """One instrument's point-in-time document: fact cells plus derived cells.

    The document is dated by its newest provenance, not by the request, and
    carries no provider label beyond each cell's audit ``source``. An unknown
    symbol is a ``404`` (the identity master does not hold it) and a deployment
    with no row provider is a ``503 screen_unavailable`` — the same two house
    envelopes the rest of ``/engine`` uses, so "no such company" and "nothing
    loaded" can never be confused with an empty document.
    """

    try:
        document = engine_service_of(request).fundamentals(symbol)
    except EngineUnavailable as exc:
        raise ApiError(503, exc.code, str(exc)) from exc
    if document is None:
        raise ApiError(
            404,
            "security_not_found",
            f"security {symbol!r} is not in the loaded identity master",
        )
    return document


@router.get("/statements/{symbol}")
async def statements(symbol: str, request: Request) -> dict[str, Any]:
    """The ticker page's statement table: ten annual and eight quarterly columns.

    Read from the **cached** ``companyfacts`` document the ingest pass already
    wrote (``MEE_ENGINE_CACHE_DIR``); this route never fetches. That is a
    product decision, not an optimisation: a statement table is a historical
    document, and a ticker page must not wait on a 20 MB EDGAR body to paint
    one. A stale cache is handled by the ingest job that owns it.

    ``404 no_statements`` covers every way there is no table to serve: a symbol
    outside the identity master, a registrant with no CIK, no cached document
    for the CIK, and a document with no usable 10-K or 10-Q row. They are one
    answer because the page's next move is one: show "no statements", not retry
    a request that cannot succeed. A deployment with no identity master at all
    is still ``503 screen_unavailable`` — "nothing was ingested" is a different
    fact and must not look like "this company files nothing".
    """

    service = engine_service_of(request)
    master = service.security_master
    if master is None:
        raise ApiError(
            503,
            "screen_unavailable",
            "the engine has no identity master loaded yet; ingest a dataset first",
        )
    record = master.get(symbol)
    if record is None or record.cik is None:
        raise ApiError(404, "no_statements", f"no SEC facts are on file for {symbol!r}")

    cache_dir = _engine_cache_dir()
    payload = (
        None if cache_dir is None else load_cached_companyfacts(cache_dir, record.cik)
    )
    if payload is None:
        raise ApiError(404, "no_statements", f"no SEC facts are on file for {record.symbol}")

    document = statement_document(record.symbol, payload)
    if document is None:
        raise ApiError(404, "no_statements", f"no SEC facts are on file for {record.symbol}")
    return document


@router.get("/statements/{symbol}/annual")
async def statements_annual(
    symbol: str, request: Request, periods: str = "4"
) -> dict[str, Any]:
    """One issuer's annual income, balance and cash-flow table, from SEC facts.

    ``periods`` is taken as raw text and parsed here for the same reason as
    ``/engine/insider`` and ``/engine/peers``: a framework ``422`` would bypass
    the house ``{error, errorMessage}`` envelope. Validation is first, so an
    unusable column count is ``422 invalid_limit`` even for a symbol the master
    does not hold.

    The four answers are deliberately distinct, because two of them are easy to
    confuse and both would be wrong:

    * no identity master at all is ``503 screen_unavailable`` — nothing was
      ingested, so the engine cannot name an issuer;
    * a symbol outside the master is ``404 security_not_found``;
    * a known symbol without a CIK is an honest empty ``200`` naming
      ``no CIK`` — there is no registrant to ask, and no network call is made;
    * a CIK whose ``companyfacts`` cannot be read or parsed is ``503
      statements_unavailable``, never an empty table: "SEC did not answer" must
      not look like "this company reports nothing".

    Only the last case touches the network, and only through the cache-aware
    collector. The response is the collector's document — period columns,
    aligned lines and per-cell provenance — and is not re-shaped here, so the
    table has exactly one definition.
    """

    bounded = _statements_periods(periods)
    service = engine_service_of(request)
    master = service.security_master
    if master is None:
        raise ApiError(
            503,
            "screen_unavailable",
            "the engine has no identity master loaded yet; ingest a dataset first",
        )
    record = master.get(symbol)
    if record is None:
        raise ApiError(
            404,
            "security_not_found",
            f"security {symbol!r} is not in the loaded identity master",
        )
    if record.cik is None:
        return {
            "symbol": record.symbol,
            "as_of": None,
            "periods": [],
            "statements": {},
            "provider_errors": ["no CIK"],
        }

    try:
        return await collect_statements(
            record.symbol,
            record.cik,
            cache_dir=_engine_cache_dir(),
            periods=bounded,
        )
    except Exception as exc:  # noqa: BLE001 - every collection failure is one refusal
        # The class is named rather than swallowed: an operator reading the
        # response can tell a transport error from a body that was not a
        # companyfacts document, which is the difference between retrying and
        # investigating the CIK.
        raise ApiError(
            503,
            "statements_unavailable",
            f"SEC companyfacts could not be read for {record.symbol}: "
            f"{type(exc).__name__}: {exc}",
        ) from exc


@router.get("/etf/{symbol}/holdings")
async def etf_holdings(
    symbol: str, request: Request, limit: str = "50"
) -> dict[str, Any]:
    """One ETF's latest SEC N-PORT-P portfolio, largest positions first.

    ``limit`` is taken as raw text and parsed here for the same reason as
    ``/engine/insider`` and ``/engine/peers``: a framework-shaped ``422`` would
    bypass the house ``{error, errorMessage}`` envelope. The bound is 1..500 —
    a whole portfolio is ~500 rows, while one request must not become an
    unbounded document. Validation is first, so an unusable page size is
    ``422 invalid_limit`` even for a symbol the master does not hold: a typo in
    the page size must never be reported as "no such company".

    The four answers are deliberately distinct, because each wrong pairing
    would publish a different lie:

    * no identity master at all is ``503 screen_unavailable`` — nothing was
      ingested, so "no holdings" is not something this deployment knows;
    * a symbol outside the master is ``404 security_not_found``;
    * a known symbol without a CIK has no registrant to ask, so it is an honest
      empty ``200`` naming ``no CIK`` and the collector is never called;
    * a CIK with no NPORT-P filing is ``404 holdings_not_found``, while a body
      that could not be fetched or parsed is ``503 holdings_unavailable`` —
      "SEC filed no portfolio" and "SEC did not answer" are different facts.

    SEC is public, so this route may run in public mode: it reads a regulatory
    document about a fund, not a licensed quote. ``as_of`` and ``filed`` come
    from the filing itself, never from the request clock, and the collector's
    document is served unchanged so the page has exactly one definition.
    """

    bounded = _holdings_limit(limit)
    service = engine_service_of(request)
    master = service.security_master
    if master is None:
        raise ApiError(
            503,
            "screen_unavailable",
            "the engine has no identity master loaded yet; ingest a dataset first",
        )
    record = master.get(symbol)
    target = symbol.strip().upper()
    cache_dir = _engine_cache_dir()
    if record is None:
        # The operating-company index does not carry most ETFs (IVV and QQQ are
        # absent; SPY is a registrant).  The official ticker→series map is the
        # only place that knows their CIK, and a fund outside the screener
        # universe is still a legitimate holdings question.  Without the map
        # this branch is empty and the old 404 stands.
        series = (
            load_fund_series(fund_series_path(cache_dir)).get(target)
            if cache_dir is not None
            else None
        )
        if series is None:
            raise ApiError(
                404,
                "security_not_found",
                f"security {symbol!r} is not in the loaded identity master",
            )
        cik = series.cik
    else:
        target = record.symbol
        if record.cik is None:
            return {
                "symbol": record.symbol,
                "as_of": None,
                "filed": None,
                "accession": None,
                "count": 0,
                "holdings": [],
                "provider_errors": ["no CIK"],
            }
        cik = record.cik

    try:
        return await collect_holdings(
            target,
            cik,
            cache_dir=cache_dir,
            limit=bounded,
        )
    except HoldingsNotFound as exc:
        raise ApiError(
            404,
            "holdings_not_found",
            f"no N-PORT-P portfolio is on file for {target}: {exc}",
        ) from exc
    except HoldingsUnavailable as exc:
        raise ApiError(
            503,
            "holdings_unavailable",
            f"the N-PORT-P body could not be read for {target}: {exc}",
        ) from exc
    except Exception as exc:  # noqa: BLE001 - every collection failure is one refusal
        # The class is named rather than swallowed: an operator reading the
        # response can tell a transport error from a body that was not an
        # N-PORT document, which is the difference between retrying and
        # investigating the CIK. The route still never invents an empty page.
        raise ApiError(
            503,
            "holdings_unavailable",
            f"the N-PORT-P body could not be read for {target}: "
            f"{type(exc).__name__}: {exc}",
        ) from exc


async def _screen_payload(request: Request) -> dict[str, Any]:
    """Read the screen body as a JSON object; a bad body is a 422."""

    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ApiError(
            422,
            "invalid_json",
            f"the screen body is not valid JSON: {exc}",
        ) from exc
    if not isinstance(payload, dict):
        raise ApiError(422, "invalid_body", "the screen body must be a JSON object")
    return payload


def _screened(service: EngineService, query: ScreenQuery) -> ScreenResult:
    """Run one screen, mapping the engine's typed errors onto the envelope."""

    try:
        return service.screen(query)
    except ScreenError as exc:
        raise ApiError(422, exc.code, str(exc)) from exc
    except EngineUnavailable as exc:
        raise ApiError(503, exc.code, str(exc)) from exc


def _page_symbols(result: ScreenResult) -> tuple[str, ...]:
    """The tickers of the returned page, in row order, each named once.

    A row without a usable ticker symbol is skipped, not guessed at: polling a
    company name would cost a request and answer nothing.
    """

    symbols: list[str] = []
    seen: set[str] = set()
    for row in result.rows:
        raw = row.get("ticker")
        value = raw.value if isinstance(raw, Cell) else raw
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            symbols.append(value)
    return tuple(symbols)


def _without_quote_cells(result: ScreenResult) -> ScreenResult:
    """The same page with every observed quote field removed from its rows.

    Used only in public mode. Totals, ordering and the filter trail are
    unchanged: a public query cannot reference a quote field, so removing those
    cells cannot change which rows matched.
    """

    return ScreenResult(
        rows=tuple(
            {key: value for key, value in row.items() if key not in QUOTE_FIELDS}
            for row in result.rows
        ),
        total=result.total,
        returned=result.returned,
        limit=result.limit,
        offset=result.offset,
        applied_filters=result.applied_filters,
    )


async def _poll_quotes(
    request: Request, symbols: Sequence[str]
) -> tuple[dict[str, QuoteTick | None], dict[str, dict[str, object]]]:
    """Ask the quote engine for one batch; a failure is an empty batch.

    ``poll_many`` is third-party code and the only fetch this route performs, so
    an exception, a non-mapping answer or a malformed tick all degrade to "no
    quotes this request". A tick that is not a
    :class:`~app.market.quote.QuoteTick` is dropped rather than projected: the
    row assembler's quote vocabulary is defined for a *judged* tick, and guessing
    around that contract would publish an unjudged number.
    """

    try:
        engine = quote_engine_of(request)
        answer = await engine.poll_many(list(symbols))
        ticks = dict(answer)
        quotes = {
            symbol: quote_fields(tick)
            for symbol, tick in ticks.items()
            if isinstance(tick, QuoteTick)
        }
    except Exception:  # noqa: BLE001 - a quote outage must not kill the screen
        return {}, {}
    return ticks, quotes


async def _refreshed(
    request: Request,
    service: EngineService,
    query: ScreenQuery,
    result: ScreenResult,
    *,
    uses_quotes: bool,
) -> tuple[ScreenResult, QuoteSnapshot]:
    """Refresh the right symbol set, then screen again over the fresh values.

    ``uses_quotes`` chooses the target. A quote-based filter or sort decides
    *membership*, so the whole identity master is polled (``candidates``) and the
    authoritative screen runs **after** the merge; a page-scoped refresh would
    answer ``price >= 200`` from values that do not exist yet. Any other query
    polls the page it is about to return (``page``), which is the historical
    behaviour and the cheap one.

    A batch that answered nothing keeps the result the caller already has and
    says ``quote_unavailable``: an outage must not silently become "the market
    has no prices". An empty target set is not a failure either, so it reports
    no reason at all — nothing was attempted — but it still admits that a quote
    filter read a snapshot, because "there was nothing to refresh" is exactly
    the case where the un-refreshed values decided the page.

    A quote-based filter over more than :data:`MAX_QUOTE_REFRESH_SYMBOLS` is
    refused the same honest way: the identity master can hold ten thousand
    symbols, and one screen must not become a bulk quote crawl. The page is
    returned from the snapshot with ``quote_filter_capped`` and
    ``filter_used_snapshot=True`` so no caller can mistake it for a live filter.
    """

    scope = "candidates" if uses_quotes else "page"
    symbols = service.candidate_symbols() if uses_quotes else _page_symbols(result)
    if not symbols:
        return result, QuoteSnapshot(filter_used_snapshot=uses_quotes)
    if uses_quotes and len(symbols) > MAX_QUOTE_REFRESH_SYMBOLS:
        return result, QuoteSnapshot(
            scope=scope,
            requested=len(symbols),
            reason="quote_filter_capped",
            filter_used_snapshot=True,
        )

    ticks, quotes = await _poll_quotes(request, symbols)
    if not quotes:
        return result, QuoteSnapshot(
            scope=scope,
            requested=len(symbols),
            reason="quote_unavailable",
            filter_used_snapshot=uses_quotes,
        )

    refreshed = service.with_quotes(quotes)
    request.app.state.engine = refreshed
    return (
        _screened(refreshed, query),
        snapshot_from_ticks(ticks, scope=scope, requested=len(symbols)),
    )


__all__ = ["engine_service_of", "router"]
