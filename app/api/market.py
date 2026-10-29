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

import logging
import math
import statistics
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from weakref import WeakKeyDictionary

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from app.auth import ApiError
from app.market.depth import DepthCollector, DepthStore
from app.market.depth_fields import CATALOG_FIELDS
from app.market.hub import QuoteHub
from app.market.quote import QuoteEngine, QuoteTick
from app.market.sources import InvalidSymbol, build_quote_engine, parse_symbol_list

router = APIRouter(prefix="/market", tags=["market"])

logger = logging.getLogger("mee.market.depth")


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


#: The last cycle this process warned about, per collector. Weak keys: a
#: replaced collector must not keep its entry (or a recycled ``id``) alive.
_warned_cycles: "WeakKeyDictionary[Any, str]" = WeakKeyDictionary()


def depth_stats(store: DepthStore) -> dict[str, Any]:
    """Coverage of one depth store, computed from memory alone.

    ``fields_filled`` counts distinct **catalog** keys that hold at least one
    value: a name outside ``app.market.depth_fields`` is not a catalog column,
    so counting it as coverage would overstate how much of the product's
    vocabulary is answerable. Per-symbol counts describe the same catalog
    cells, which is why ``min``/``max``/``median`` and the ``by_source``
    subtraction all refer to the same population.

    ``as_of`` is the newest cell clock in the store — the data's age, not the
    request's — and is ``None`` for an empty store. Nothing here touches a
    provider, a file or the event loop's clock.

    The counting is set algebra where possible: key-set intersections are
    C-level, so the cost is one copy of the symbol maps plus one pass over the
    cells for source attribution and the newest stamp. On the production store
    (4,217 symbols × ~90 cells) that is roughly 0.1–0.2 s, against a 13-minute
    daily crawl.
    """

    catalog = CATALOG_FIELDS
    rows = store.snapshot()
    fieldsets = list(rows.values())
    counts = [len(fields.keys() & catalog) for fields in fieldsets]
    filled = (
        set().union(*(fields.keys() for fields in fieldsets)) & catalog
        if fieldsets
        else set()
    )

    by_source: Counter[str] = Counter()
    newest: datetime | None = None
    for fields in fieldsets:
        for name, field in fields.items():
            if name not in catalog:
                continue
            source = field.source
            if source:
                by_source[source] += 1
            stamp = field.as_of
            if newest is None or stamp > newest:
                newest = stamp

    return {
        "as_of": newest.isoformat() if newest is not None else None,
        "symbols": len(rows),
        "fields_filled": len(filled),
        "median_fields_per_symbol": statistics.median(counts) if counts else 0,
        "min_fields": min(counts) if counts else 0,
        "max_fields": max(counts) if counts else 0,
        "by_source": dict(sorted(by_source.items())),
    }


def _coverage_floor(request: Request) -> int:
    """``settings.depth_coverage_floor``, with the product default as fallback."""

    runtime = getattr(request.app.state, "runtime", None)
    settings = getattr(runtime, "settings", None)
    raw = getattr(settings, "depth_coverage_floor", 60)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 60


def _warn_if_degraded(
    request: Request, collector: Any, document: dict[str, Any]
) -> None:
    """One WARNING per degraded cycle, never one per scrape.

    The cycle is identified by ``collector.last_cycle.started_at``: the same
    store read twice inside one cycle is the same night, while tomorrow's bad
    cycle is a new line. A store seeded before the first cycle has no stamp and
    is logged once under ``no-cycle``.
    """

    floor = _coverage_floor(request)
    filled = int(document["fields_filled"])
    if filled >= floor:
        return
    cycle = getattr(collector, "last_cycle", None)
    started = getattr(cycle, "started_at", None)
    marker = started.isoformat() if isinstance(started, datetime) else "no-cycle"
    if _warned_cycles.get(collector) == marker:
        return
    _warned_cycles[collector] = marker
    logger.warning(
        "depth coverage degraded: fields_filled=%d below floor=%d "
        "(symbols=%d, cycle=%s)",
        filled,
        floor,
        document["symbols"],
        marker,
    )


@dataclass
class _Bucket:
    """One caller's token bucket: fractional tokens left, and its last touch."""

    tokens: float
    last: float


class CallerRateLimiter:
    """A per-caller token bucket: ``limit`` requests per ``window_s`` seconds.

    One instance per process, on ``app.state.enrich_limiter``. ``check`` is
    synchronous and never awaits, so a plain lock makes it safe from the event
    loop and from a threadpool worker alike. The bucket map is pruned lazily:
    a caller idle for a whole window has refilled to the limit, so its state
    carries no information and is dropped — memory follows live traffic, not
    uptime.

    Over budget is answered, never queued: the route turns the refusal into a
    429 with ``Retry-After``. A *paid* caller that is told "come back in 40 s"
    is a working product; one that is silently made to wait holds a socket.
    """

    def __init__(
        self,
        *,
        limit: float,
        window_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if window_s <= 0:
            raise ValueError("window_s must be positive")
        self.limit = float(limit)
        self.window_s = float(window_s)
        #: Tokens per second; one request costs one token.
        self.rate = self.limit / self.window_s
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[str, _Bucket] = {}
        self._last_prune = clock()

    def __len__(self) -> int:
        """How many callers are currently tracked (bounded by pruning)."""

        with self._lock:
            return len(self._buckets)

    def check(self, caller: str) -> tuple[bool, float]:
        """Take one request from ``caller``'s bucket.

        Returns ``(True, 0.0)`` inside the budget, else ``(False, wait_s)``
        where ``wait_s`` is the time until one whole token exists again.
        """

        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(caller)
            if bucket is None:
                bucket = _Bucket(tokens=self.limit, last=now)
                self._buckets[caller] = bucket
            else:
                elapsed = max(0.0, now - bucket.last)
                bucket.tokens = min(self.limit, bucket.tokens + elapsed * self.rate)
                bucket.last = now
            self._prune(now)
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0.0
            return False, (1.0 - bucket.tokens) / self.rate

    def _prune(self, now: float) -> None:
        """Drop callers idle for a full window; at most one sweep per window."""

        if now - self._last_prune < self.window_s:
            return
        self._last_prune = now
        idle = [
            name
            for name, bucket in self._buckets.items()
            if now - bucket.last >= self.window_s
        ]
        for name in idle:
            del self._buckets[name]


def enrich_limiter_of(request: Request) -> CallerRateLimiter | None:
    """The process-owned per-caller bucket, rebuilt only when config changes.

    ``None`` means the gate is explicitly off (``enrich_rate_limit <= 0``).
    Like ``quote_engine_of``, a test (or an operator with a different policy)
    can replace ``app.state.enrich_limiter`` — with an injectable clock — without
    touching the route.
    """

    settings = request.app.state.runtime.settings
    limit = float(getattr(settings, "enrich_rate_limit", 10.0))
    window_s = float(getattr(settings, "enrich_rate_window_s", 60.0))
    if limit <= 0:
        return None
    limiter = getattr(request.app.state, "enrich_limiter", None)
    if (
        isinstance(limiter, CallerRateLimiter)
        and limiter.limit == limit
        and limiter.window_s == window_s
    ):
        return limiter
    limiter = CallerRateLimiter(limit=limit, window_s=window_s)
    request.app.state.enrich_limiter = limiter
    return limiter


def enrich_caller(request: Request, settings: Any) -> str:
    """Who a request is billed to: the key when one is configured, else host.

    A configured key is the paid identity — every caller presenting it shares
    that key's budget, which is exactly the unit a subscription sells. Without
    a key the route is a dev/open surface and the client host is the only
    identity there is (behind a reverse proxy, configure the key).
    """

    if getattr(settings, "enrich_api_key", None):
        return request.headers.get("x-enrich-key", "") or "anonymous"
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


def _rate_limited(limiter: CallerRateLimiter, retry_after_s: float) -> JSONResponse:
    """The 429: house envelope plus the one header a client should honour."""

    seconds = max(1, math.ceil(retry_after_s))
    return JSONResponse(
        status_code=429,
        content={
            "error": "enrich_rate_limited",
            "errorMessage": (
                f"on-demand enrichment allows {limiter.limit:g} requests per "
                f"{limiter.window_s:g}s per caller; retry in {seconds}s"
            ),
        },
        headers={"Retry-After": str(seconds)},
    )


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


@router.get("/depth/stats")
async def depth_stats_route(request: Request) -> dict[str, Any]:
    """Coverage of the depth store: counts and clocks, never a value.

    Read-only and provider-free: the document is assembled from the collector's
    in-memory store, so a monitoring scrape costs one pass over the symbols and
    cannot trigger a crawl. A collector that is absent or has not filled its
    store is ``503 depth_unavailable`` — "no coverage data yet" must not look
    like a store of zeros.

    A cycle that leaves fewer than ``depth_coverage_floor`` catalog fields
    filled is logged once at WARNING, with the cycle's own start stamp as the
    identity, so journalctl shows one line per bad night rather than one per
    scrape.
    """

    collector = depth_collector_of(request)
    if collector is None or not collector.ready:
        raise ApiError(
            503,
            "depth_unavailable",
            "depth collector is not running or has not completed a refresh",
        )
    document = depth_stats(collector.store)
    _warn_if_degraded(request, collector, document)
    return document


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


__all__ = [
    "CallerRateLimiter",
    "depth",
    "depth_collector_of",
    "depth_stats",
    "depth_stats_route",
    "enrich",
    "enrich_caller",
    "enrich_limiter_of",
    "hub_quote",
    "quote",
    "quote_engine_of",
    "quote_hub_of",
    "router",
]

@router.post("/enrich")
async def enrich(
    request: Request,
    body: dict[str, Any] = Body(...),
) -> Any:
    """Run the per-symbol enrichment on demand — the premium trigger.

    The rotation in :class:`~app.market.enrichment.EnrichmentStage` is the free
    baseline; this endpoint is the "do it now, for these symbols" path a paid
    tier will call. It bypasses the batch cursor and the per-cycle interval,
    fetches exactly what was asked, merges the result into the depth store and
    persists it, so the next ``GET /market/depth`` sees the values.

    **Body** ``{"symbols": ["AAPL"], "sources": ["yahoo", "news"]}``. Both keys
    are optional: ``symbols`` defaults to the next rotation slice, ``sources``
    defaults to both halves.

    **Access** is gated by ``settings.enrich_api_key``. When it is unset the
    route is open (dev); when set, ``X-Enrich-Key`` must match, and the
    mismatch is a **403** in the house envelope.

    **Budget** is ``enrich_rate_limit`` requests per ``enrich_rate_window_s``
    per caller (the key when configured, else the client host). Over budget is
    a **429** ``enrich_rate_limited`` with ``Retry-After`` — cheap, and checked
    *after* the key so a caller probing with wrong keys cannot exhaust itself.
    """

    settings = request.app.state.runtime.settings
    expected = getattr(settings, "enrich_api_key", None)
    if expected:
        supplied = request.headers.get("x-enrich-key", "")
        if not supplied or supplied != expected:
            raise ApiError(
                403,
                "enrich_forbidden",
                "a valid X-Enrich-Key is required for on-demand enrichment",
            )

    limiter = enrich_limiter_of(request)
    if limiter is not None:
        allowed, retry_after = limiter.check(enrich_caller(request, settings))
        if not allowed:
            return _rate_limited(limiter, retry_after)

    sources = body.get("sources") or ["yahoo", "news"]
    if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
        raise ApiError(422, "invalid_sources", "sources must be a list of strings")
    unknown = set(sources) - {"yahoo", "news"}
    if unknown:
        raise ApiError(
            422,
            "invalid_sources",
            f"unknown sources {sorted(unknown)}; expected 'yahoo' and/or 'news'",
        )

    raw_symbols = body.get("symbols")
    limit = int(getattr(settings, "enrich_max_symbols", 50))
    if raw_symbols is None:
        wanted: list[str] = []
    else:
        if not isinstance(raw_symbols, list) or not all(
            isinstance(item, str) for item in raw_symbols
        ):
            raise ApiError(422, "invalid_symbols", "symbols must be a list of strings")
        try:
            wanted = parse_symbol_list(",".join(raw_symbols))
        except InvalidSymbol as exc:
            raise ApiError(422, "invalid_symbols", str(exc)) from exc
        if len(wanted) > limit:
            raise ApiError(
                422,
                "too_many_symbols",
                f"{len(wanted)} symbols requested; the on-demand maximum is {limit}",
            )

    collector = depth_collector_of(request)
    if collector is None:
        raise ApiError(
            503,
            "depth_unavailable",
            "depth collector is not running, so there is nowhere to merge the result",
        )

    from app.market.enrichment import EnrichmentStage

    stage = EnrichmentStage(sources=tuple(sources))
    target = wanted or list(getattr(collector, "symbols", []) or [])
    if not target:
        raise ApiError(503, "depth_unavailable", "the collector carries no universe")
    if raw_symbols is None:
        # No explicit list: enrich the rotation's next slice rather than all of it.
        target = stage._take_batch(target) if stage.batch_size else target

    observations = await stage.run(target)

    merged = 0
    for symbol, fields in observations.items():
        if not fields:
            continue
        from datetime import UTC, datetime

        from app.market.depth import DepthObservation

        observation = DepthObservation(
            symbol=symbol,
            fields=fields,
            as_of=datetime.now(UTC),
            source="enrich",
        )
        # An explicit request is the freshest evidence there is: priority 0.
        merged += collector.store.merge(observation, priority=0)
    if merged:
        await collector.persist()

    return {
        "requested": len(target),
        "enriched": len(observations),
        "fields_merged": merged,
        "sources": list(sources),
        "errors": dict(getattr(stage, "last_errors", {}) or {}),
        "tier": "premium" if expected else "open",
    }

