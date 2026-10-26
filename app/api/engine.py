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

import json
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Request

from app.api.market import quote_engine_of
from app.auth import ApiError
from app.engine.model import Cell
from app.engine.quotes import (
    MAX_QUOTE_REFRESH_SYMBOLS,
    QUOTE_FIELDS,
    QuoteSnapshot,
    quote_fields,
    snapshot_from_ticks,
)
from app.engine.screener import ScreenError, ScreenQuery, ScreenResult, parse_query
from app.engine.service import EngineService, EngineUnavailable
from app.market.quote import QuoteTick

router = APIRouter(prefix="/engine", tags=["engine"])

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
