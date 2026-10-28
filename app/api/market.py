"""``GET /market/quote`` — the realtime watchlist read (P8).

One request, one document:

```json
{
  "as_of": "2026-10-23T14:30:02+00:00",
  "source": "yahoo",
  "stale": false,
  "latency_ms": 41.7,
  "requested": 2,
  "returned": 2,
  "quotes": [{"symbol": "AAPL", "last": 231.5, ...}],
  "missing": []
}
```

**The rules a caller may rely on**

* ``symbols`` is a comma-separated list, at most
  :attr:`~app.config.Settings.quote_max_symbols` (100) after dedupe. An empty
  or malformed list is a **422** in the house envelope; a request above the
  ceiling is a 422 naming the ceiling, not a silent truncation.
* ``quotes`` carries one object per symbol that a source answered, in request
  order. A symbol nobody answered is in ``missing`` — never a zero-priced
  quote, never a fabricated ``last``. When **nothing** could be answered the
  route answers **503** rather than ``200`` with an empty book. A symbol the
  sources answered on an earlier request but not on this one is served from the
  engine's recent cache, always with ``stale=true``; only a symbol with no
  answer and no cache is ``missing``.
* ``as_of`` is the newest ``ts_exchange`` in the document: the data's clock,
  not the server's. ``stale`` is true when any returned quote is older than
  the engine's threshold (5 s by default), so a caller cannot read a
  partly-stale document as fresh; every quote also carries its own ``stale``
  flag.
* ``source`` names the winning provider, or ``"mixed"`` when a failover put
  different symbols on different sources. ``latency_ms`` is the measured
  poll time (Yahoo batch + Nasdaq fallbacks), for the P2 ``p95 ≤ 2 s`` gate.

The engine is built lazily from settings and cached on ``app.state``; a test (or
an operator with a different feed) can replace ``app.state.quote_engine``
without touching the route.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Query, Request

from app.auth import ApiError
from app.market.depth import DepthCollector
from app.market.hub import QuoteHub
from app.market.quote import QuoteEngine, QuoteTick
from app.market.sources import InvalidSymbol, build_quote_engine, parse_symbol_list

router = APIRouter(prefix="/market", tags=["market"])


def quote_engine_of(request: Request) -> QuoteEngine:
    """The app's engine, built once per process from its own settings."""

    engine: QuoteEngine | None = getattr(request.app.state, "quote_engine", None)
    if engine is None:
        engine = build_quote_engine(request.app.state.runtime.settings)
        request.app.state.quote_engine = engine
    return engine


def quote_hub_of(request: Request) -> QuoteHub | None:
    """The process-owned QuoteHub, or ``None`` when the feature is disabled."""

    hub = getattr(request.app.state, "quote_hub", None)
    return hub if isinstance(hub, QuoteHub) else None


def depth_collector_of(request: Request) -> DepthCollector | None:
    """The process-owned depth collector, or ``None`` when disabled."""

    collector = getattr(request.app.state, "depth", None)
    return collector if isinstance(collector, DepthCollector) else None


def _document(tick: QuoteTick) -> dict[str, Any]:
    """One quote, every field named — a null is a fact, not an omission."""

    return {
        "symbol": tick.symbol,
        "last": tick.last,
        "prev_close": tick.prev_close,
        "change": tick.change,
        "change_pct": tick.change_pct,
        "volume": tick.volume,
        "direction": tick.direction.value,
        "velocity": tick.velocity,
        "ts_exchange": tick.ts_exchange.isoformat(),
        "ts_received": tick.ts_received.isoformat(),
        "source": tick.source,
        "stale": tick.stale,
    }


def _public_mode(request: Request) -> bool:
    """Whether this process serves a public product.

    The engine's policy is the one deployment-level licence switch, so this
    route reads it instead of keeping a second flag that could drift. A process
    with no engine service is a test/tool context and stays private.
    """

    service = getattr(request.app.state, "engine", None)
    policy = getattr(service, "policy", None)
    return bool(getattr(policy, "public_mode", False))


@router.get("/hub")
async def hub_quote(
    request: Request,
    symbols: str | None = Query(
        default=None,
        description="Comma-separated provider symbols, e.g. AAPL,MSFT",
    ),
) -> dict[str, Any]:
    """Read the last QuoteHub snapshot; this route never calls a provider.

    The public document carries values and the exchange clock only. ``source``
    and ``tier`` stay internal, as the product requested; a caller cannot infer
    which upstream supplied a cell.
    """

    try:
        wanted = parse_symbol_list(symbols or "")
    except InvalidSymbol as exc:
        raise ApiError(
            422,
            "invalid_symbols",
            f"{exc} — symbols are comma-separated provider tickers (AAPL,MSFT,BRK-B)",
        ) from exc
    if not wanted:
        raise ApiError(
            422,
            "invalid_symbols",
            "symbols is required: pass ?symbols=AAPL,MSFT",
        )

    limit = request.app.state.runtime.settings.quote_max_symbols
    if len(wanted) > limit:
        raise ApiError(
            422,
            "too_many_symbols",
            f"{len(wanted)} symbols requested after dedupe; the maximum is {limit}",
        )

    if _public_mode(request):
        raise ApiError(
            503,
            "license_unavailable",
            "realtime quotes come from internal sources that do not permit "
            "redistribution; this deployment is public and has no licensed "
            "quote source configured",
        )

    hub = quote_hub_of(request)
    if hub is None or not hub.ready:
        raise ApiError(
            503,
            "quotes_unavailable",
            "QuoteHub is not running or has not completed a refresh",
        )

    document = hub.document(wanted)
    if not document["quotes"]:
        raise ApiError(
            503,
            "quotes_unavailable",
            "QuoteHub has no quote for the requested symbols",
        )
    return {
        "as_of": document["as_of"],
        "requested": len(wanted),
        "returned": document["count"],
        "quotes": document["quotes"],
        "missing": document["missing"],
    }


@router.get("/depth")
async def depth(
    request: Request,
    symbols: str | None = Query(
        default=None,
        description="Comma-separated provider symbols, e.g. AAPL,MSFT",
    ),
) -> dict[str, Any]:
    """Read the last depth snapshot; no provider is called on this path."""

    try:
        wanted = parse_symbol_list(symbols or "")
    except InvalidSymbol as exc:
        raise ApiError(
            422,
            "invalid_symbols",
            f"{exc} — symbols are comma-separated provider tickers (AAPL,MSFT,BRK-B)",
        ) from exc
    if not wanted:
        raise ApiError(
            422,
            "invalid_symbols",
            "symbols is required: pass ?symbols=AAPL,MSFT",
        )
    limit = request.app.state.runtime.settings.quote_max_symbols
    if len(wanted) > limit:
        raise ApiError(
            422,
            "too_many_symbols",
            f"{len(wanted)} symbols requested after dedupe; the maximum is {limit}",
        )
    if _public_mode(request):
        raise ApiError(
            503,
            "license_unavailable",
            "depth sources are internal and do not permit redistribution; "
            "this deployment is public and has no licensed depth source configured",
        )
    collector = depth_collector_of(request)
    if collector is None or not collector.ready:
        raise ApiError(
            503,
            "depth_unavailable",
            "depth collector is not running or has not completed a refresh",
        )
    document = collector.document(wanted)
    if not document["symbols"]:
        raise ApiError(
            503,
            "depth_unavailable",
            "depth collector has no values for the requested symbols",
        )
    return {
        "as_of": document["as_of"],
        "requested": len(wanted),
        "returned": document["count"],
        "symbols": document["symbols"],
        "missing": document["missing"],
    }


@router.get("/quote")
async def quote(
    request: Request,
    symbols: str | None = Query(
        default=None,
        description="Comma-separated provider symbols, e.g. AAPL,MSFT",
    ),
) -> dict[str, Any]:
    """Realtime quotes for one watchlist request (see the module docstring)."""

    try:
        wanted = parse_symbol_list(symbols or "")
    except InvalidSymbol as exc:
        raise ApiError(
            422,
            "invalid_symbols",
            f"{exc} — symbols are comma-separated provider tickers (AAPL,MSFT,BRK-B)",
        ) from exc
    if not wanted:
        raise ApiError(
            422,
            "invalid_symbols",
            "symbols is required: pass ?symbols=AAPL,MSFT",
        )

    limit = request.app.state.runtime.settings.quote_max_symbols
    if len(wanted) > limit:
        raise ApiError(
            422,
            "too_many_symbols",
            f"{len(wanted)} symbols requested after dedupe; the maximum is {limit}",
        )

    if _public_mode(request):
        # Every quote rung is INTERNAL, so a public deployment cannot serve a
        # realtime print. Validation ran first: a typo stays a 422 and a licence
        # refusal cannot be mistaken for a malformed request.
        raise ApiError(
            503,
            "license_unavailable",
            "realtime quotes come from internal sources that do not permit "
            "redistribution; this deployment is public and has no licensed "
            "quote source configured",
        )

    engine = quote_engine_of(request)
    started = time.perf_counter()
    ticks = await engine.poll_many(wanted)
    latency_ms = round((time.perf_counter() - started) * 1000.0, 3)

    returned = [tick for tick in ticks.values() if tick is not None]
    if not returned:
        # An empty book is not a price list: 503 says "no source answered",
        # where a 200 with zeros would be a fabricated market.
        raise ApiError(
            503,
            "quotes_unavailable",
            "no quote source returned data for the requested symbols",
        )

    sources = {tick.source for tick in returned}
    return {
        "as_of": max(tick.ts_exchange for tick in returned).isoformat(),
        "source": next(iter(sources)) if len(sources) == 1 else "mixed",
        "stale": any(tick.stale for tick in returned),
        "latency_ms": latency_ms,
        "requested": len(wanted),
        "returned": len(returned),
        "quotes": [_document(tick) for tick in returned],
        "missing": [symbol for symbol, tick in ticks.items() if tick is None],
    }


__all__ = ["depth", "depth_collector_of", "hub_quote", "quote", "quote_engine_of", "quote_hub_of", "router"]
