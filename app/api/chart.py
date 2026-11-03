"""``GET /market/chart/{symbol}`` — the OHLC series a browser can draw.

Why this route exists
---------------------
Every other market route answers *about* a symbol: a change in percent, a
52-week high, a candlestick label. A chart needs the bars themselves, and the
browser cannot reach the provider: Yahoo's v8 endpoint is unofficial, needs a
browser ``User-Agent``, and sends a body whose ``timestamp`` array is aligned
positionally with five parallel columns. So the series is parsed here, once,
and published in the shape a canvas wants.

The shape, and the two decisions inside it
------------------------------------------
``{symbol, range, interval, timezone, previous_close, bars, source, truncated}``
with one bar ``{t, o, h, l, c, v}``. ``t`` is **epoch seconds** (what
``Date(t * 1000)`` takes, and what a JSON number costs least); every price and
the volume is a **float**, for the same reason. ``source`` is
:data:`SOURCE` — the provider and the endpoint, not a guess about the venue.

A bar survives only if it is a *price*. The provider sends a null row for a halt
or a session that never opened, and some feeds send zeros; a chart that drew
either would print a wick to zero that never happened. So a bar is dropped —
never zero-filled — when any of ``o/h/l/c`` is absent or non-finite, when any of
them is not strictly positive, or when ``high < max(open, close)`` or ``low >
min(open, close)`` (a corrupt row). Volume is the one column a bar can live
without: a null volume is ``0.0`` shares, which is what it says. **Adversarial
(a): a market-closed day therefore produces no bar at all, not a bar with a zero
close.**

``previous_close`` is Yahoo's ``chartPreviousClose`` — the close of the previous
*session*, and the label is deliberate: on a Monday a ``1d`` chart's previous
session is Friday, so it is **not** "yesterday's close"; for ``1mo``/``6mo``/
``1y``/``5y`` it is the close immediately before the window's first bar, which
is a month, a year or five years old. It is the baseline the series starts from,
published under one honest name rather than three convenient ones.
``previousClose`` is the fallback when ``chartPreviousClose`` is absent, and
``None`` when neither is a positive number — a missing previous close is
unknown, never ``0.0``.
**Adversarial (b).**

The interval is chosen per range, and the legal pairs are the provider's
-------------------------------------------------------------
``1d``→``5m``, ``5d``→``15m``, ``1mo``/``6mo``/``1y``/``5y``→``1d`` are the
defaults, and :data:`LEGAL_INTERVALS` is the whole truth about what a caller may
ask for:
a minute bar is only meaningful for one session, an hourly bar for at most two
years. An interval outside its range's table is a ``422 invalid_interval`` whose
sentence names the legal set — refused before any fetch, so a typo costs
nothing. Tokens are case-sensitive because the provider spells a month ``1mo``
and a minute ``1m``, and folding case would silently answer the wrong one.

The five-minute cache, and why the tag can be computed before the fetch
-----------------------------------------------------------------------
Parsed series are kept in-process for :data:`CACHE_TTL_S` seconds, keyed by
``(symbol, range, interval)`` — the full key, so one symbol's bars can never
answer another request (**adversarial (d)**). ``X-Chart-Cache`` states
``hit``/``miss``, so the behaviour is observable without a clock.

The validator must be a ``304`` *before* any fetch, which rules out hashing the
body: the body does not exist yet. It is therefore derived from the inputs that
decide the series — symbol, range, interval, and the ``300 s`` epoch the cache
itself turns over::

    W/"chart-<sha256(v1;symbol=…;range=…;interval=…;epoch=<n>;shape=1)>"

Within one epoch a warm ``If-None-Match`` is a ``304`` with no provider request —
including on a process that has never fetched this symbol, so a restart does not
force a ``200``. Past it the tag moves, and the client gets a fresh ``200``
rather than a stale ``304``. The tag is weak because ``as_of``-style time is not
in the body but the epoch is in the tag's inputs; a strong tag would have to be
a statement about bytes this route cannot see without fetching them.

``HEAD`` is its own route (a second method on one ``api_route`` collides on the
operation id) and answers the same headers plus the length the ``GET`` would
carry, with no bytes.

An unknown symbol is a ``404 unknown_symbol``
---------------------------------------------
Yahoo answers ``404`` with ``chart.error`` for a symbol it cannot resolve; that
becomes the house ``404``, not an empty chart, because "this ticker does not
exist" and "this ticker has no bars today" are different statements. The path
parameter is validated against :data:`_SYMBOL` before anything else, so
``../etc/passwd``, ``AAPL;DROP`` and ``..`` are the same ``404`` and never a
``500`` or a provider request.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from app.api.cache import not_modified
from app.api.sectors import encode, etag
from app.auth import ApiError
from app.market.chart import _number
from app.market.sources import BROWSER_USER_AGENT

router = APIRouter(tags=["discovery"])

#: The provider and the endpoint, stated once.
BASE_URL = "https://query1.finance.yahoo.com"

#: What ``source`` says the bars are.
SOURCE = "yahoo_chart"

#: The ranges this route speaks, in the only order a menu should print them.
#: ``5y`` closes the gap that made a five-year chart impossible here: the
#: provider accepts a five-year daily series, and ``app.api.risk`` has spoken
#: ``5y`` all along, so the chart was the one surface that could not show the
#: history every other page quotes.
RANGES: tuple[str, ...] = ("1d", "5d", "1mo", "6mo", "1y", "5y", "max")

#: The intervals the provider is asked for, in ascending coarseness.
INTERVALS: tuple[str, ...] = ("1m", "5m", "15m", "1h", "1d")

#: The interval each range answers when the caller names none.
DEFAULT_INTERVALS: dict[str, str] = {
    "1d": "5m",
    "5d": "15m",
    "1mo": "1d",
    "6mo": "1d",
    "1y": "1d",
    "5y": "1d",
    "max": "1d",
}

#: The legal intervals per range. A minute bar inside one session, a quarter-hour
#: bar inside a week, an hourly bar up to two years, a daily bar anywhere: every
#: pair here is one the provider accepts, and nothing outside it is forwarded.
#: ``5y`` speaks daily alone for that reason — the provider's hourly history
#: stops at two years, so a ``5y``+``1h`` pair is refused here rather than
#: failing upstream with an error a caller cannot read.
LEGAL_INTERVALS: dict[str, tuple[str, ...]] = {
    "1d": ("1m", "5m", "15m", "1h"),
    "5d": ("5m", "15m", "1h"),
    "1mo": ("15m", "1h", "1d"),
    "6mo": ("1h", "1d"),
    "1y": ("1h", "1d"),
    "5y": ("1d",),
    "max": ("1d",),
}

#: How long a parsed series is served from memory. Five minutes is a fifth of a
#: session and short enough that a reader who reloads after a coffee sees the
#: tape move; it is also the epoch the validator is bucketed to, so the tag and
#: the cache turn over together.
CACHE_TTL_S = 300.0

#: How many series the cache keeps. A body is up to ~120 KB (a thousand bars), so
#: this bound is a memory budget of a few megabytes, not a hit-rate target.
MEMO_LIMIT = 128

#: How many bars one document publishes. A year of hourly bars is ~1,750 and a
#: canvas cannot use them; the right edge — the most recent bars — is kept, and
#: ``truncated`` says that it happened rather than letting a client believe it
#: has the whole window.
MAX_BARS = 1000

#: The bars the long daily windows may carry. Five years of daily bars is
#: ~1,260 — over :data:`MAX_BARS`, which was sized for a year of *hourly* bars
#: (~1,750) where a canvas cannot use them all. Capping a five-year **daily**
#: series at a thousand would silently drop its oldest year and call the rest
#: "5y", so the long daily ranges get their own ceiling and every other range
#: keeps the default. A body of 1,300 bars is ~75 KB, inside the ~120 KB the
#: memo budget already prices.
BAR_LIMITS: dict[str, int] = {"5y": 1_500, "max": 3_000}


def bars_for(window: str) -> int:
    """How many bars ``window`` publishes: its own ceiling, or the default.

    Read once per request, before the fetch, so the cap is a stated decision
    rather than a surprise in ``truncated``.
    """

    return BAR_LIMITS.get(window, MAX_BARS)

#: The document's shape and meaning, folded into the tag.
CHART_SHAPE = "1"

#: A provider read is a few kilobytes over TLS; eight seconds leaves room for a
#: cold connection without holding a request open behind a dead one.
FETCH_TIMEOUT_S = 8.0

#: What a chartable ticker may look like: letters, digits, and the four
#: punctuation marks the vocabulary uses — ``^GSPC``, ``BTC-USD``, ``EURUSD=X``,
#: ``0005.HK``. Requiring at least one alphanumeric is what refuses ``..``.
_SYMBOL = re.compile(r"^[A-Za-z0-9.^=_-]{1,24}$")


# ---------------------------------------------------------------------------
# The parsed series
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChartSeries:
    """One symbol's bars, already sanitised, plus what the body dates them by.

    ``bars`` are dicts because that is what the document writes; they are built
    once per fetch and never mutated, which is why a frozen dataclass may hold
    them. ``truncated`` is a fact about this series, not about the request: it is
    set when the provider sent more bars than :data:`MAX_BARS` and the oldest
    were dropped.
    """

    bars: tuple[dict[str, Any], ...]
    previous_close: float | None
    timezone: str
    truncated: bool = False


def _column(quote: Mapping[str, Any], key: str) -> Sequence[Any]:
    """One of the provider's five parallel columns, or an empty sequence.

    The payload aligns ``open``/``high``/``low``/``close``/``volume`` with
    ``timestamp`` by **index**, so a column that is not a sequence (a null, a
    mapping, a string) cannot be aligned at all and reads as absent.
    """

    series = quote.get(key)
    if isinstance(series, Sequence) and not isinstance(series, (str, bytes)):
        return series
    return ()


def _previous_close(meta: Mapping[str, Any]) -> float | None:
    """Yahoo's previous-session close, from the three spellings it uses.

    ``chartPreviousClose`` is the one this route publishes and the one the
    endpoint always sends; ``previousClose`` and ``regularMarketPreviousClose``
    are the fallbacks for a body that omits it. A value that is not strictly
    positive is not a close, so it is skipped rather than published as ``0.0``.
    """

    for key in ("chartPreviousClose", "previousClose", "regularMarketPreviousClose"):
        value = _number(meta.get(key))
        if value is not None and value > 0:
            return value
    return None


def _timezone(meta: Mapping[str, Any]) -> str:
    """The exchange's IANA zone, or ``UTC`` when the body names none."""

    for key in ("exchangeTimezoneName", "exchangeTimezoneShortName"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "UTC"


def parse_chart_series(
    entry: Mapping[str, Any], *, limit: int = MAX_BARS
) -> ChartSeries:
    """One Yahoo v8 chart result → bars a canvas can draw.

    The drop rules are the ones :func:`app.market.chart.parse_chart_metrics`
    applies — the same coercion (:func:`app.market.chart._number`) and the same
    ``high``/``low`` consistency test — plus one this route needs and the depth
    metrics never did: a **non-positive** price is not a price, so the zero row a
    closed session can produce is dropped instead of drawn. That parser collapses
    each bar to a ``date``, which is exactly what an intraday series cannot use,
    so the loop is here and the invariants are shared.

    Bars stay in the order the provider sent them (ascending by ``t``); more than
    ``limit`` keeps the most recent ``limit`` and sets ``truncated``.
    """

    meta = entry.get("meta")
    meta = meta if isinstance(meta, Mapping) else {}

    stamps = _column(entry, "timestamp")
    indicators = entry.get("indicators")
    indicators = indicators if isinstance(indicators, Mapping) else {}
    quotes = indicators.get("quote")
    quote = quotes[0] if isinstance(quotes, Sequence) and quotes else None
    quote = quote if isinstance(quote, Mapping) else {}
    columns = {key: _column(quote, key) for key in ("open", "high", "low", "close", "volume")}

    bars: list[dict[str, Any]] = []
    for index, stamp in enumerate(stamps):
        moment = _number(stamp)
        if moment is None:
            continue
        prices = {
            key: (_number(columns[key][index]) if index < len(columns[key]) else None)
            for key in ("open", "high", "low", "close")
        }
        open_, high, low, close = (
            prices["open"],
            prices["high"],
            prices["low"],
            prices["close"],
        )
        if open_ is None or high is None or low is None or close is None:
            continue
        if min(open_, high, low, close) <= 0:
            continue
        if high < max(open_, close) or low > min(open_, close):
            continue
        volume = columns["volume"][index] if index < len(columns["volume"]) else None
        bars.append(
            {
                "t": int(moment),
                "o": float(open_),
                "h": float(high),
                "l": float(low),
                "c": float(close),
                "v": float(_number(volume) or 0.0),
            }
        )

    cap = max(0, int(limit))
    truncated = len(bars) > cap
    if truncated:
        # The right edge — the most recent bars — because that is what a chart
        # shows. ``bars[-0:]`` would keep everything while claiming it did not.
        bars = bars[len(bars) - cap :] if cap else []
    return ChartSeries(
        bars=tuple(bars),
        previous_close=_previous_close(meta),
        timezone=_timezone(meta),
        truncated=truncated,
    )


def chart_document(
    symbol: str, *, window: str, interval: str, series: ChartSeries
) -> dict[str, Any]:
    """The body, from one parsed series. Pure: the same series prints the same bytes.

    ``previous_close`` is Yahoo's ``chartPreviousClose``: the close of the
    previous **session** — Friday's on a Monday ``1d`` chart, and the close
    before the window's first bar on a long one. It is not "yesterday's close",
    and this is the only place its meaning is stated.
    """

    return {
        "symbol": symbol,
        "range": window,
        "interval": interval,
        "timezone": series.timezone,
        "previous_close": series.previous_close,
        "bars": list(series.bars),
        "source": SOURCE,
        "truncated": series.truncated,
    }


# ---------------------------------------------------------------------------
# The five-minute cache
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CachedChart:
    """One parsed series and the body it produced, dated by the cache's clock.

    The encoded body is kept because a warm answer is then a dict lookup and a
    ``Response``: the series is what the cache is *for* (``X-Chart-Cache: hit``
    means those bars were reused), and the bytes are how it is written.
    """

    series: ChartSeries
    body: bytes
    stored_at: float


class ChartCache:
    """One parsed series per ``(symbol, range, interval)``, for :data:`CACHE_TTL_S`.

    The clock is injectable (and wall time by default) because the validator's
    epoch is derived from it: a test can advance five minutes and watch both the
    cache and the tag turn over. Insertion order is the eviction order, so the
    bound is a memory budget. ``get`` and ``put`` contain no ``await``: on one
    event loop a check and a store are atomic, so two requests racing into a cold
    key cannot both fetch.
    """

    __slots__ = ("_clock", "_entries", "_limit", "_ttl_s")

    def __init__(
        self,
        *,
        ttl_s: float = CACHE_TTL_S,
        clock: Any = time.time,
        limit: int = MEMO_LIMIT,
    ) -> None:
        self._ttl_s = float(ttl_s)
        self._clock = clock
        self._limit = max(1, int(limit))
        self._entries: dict[tuple[str, str, str], CachedChart] = {}

    def get(self, key: tuple[str, str, str]) -> CachedChart | None:
        """The fresh series for ``key``, or ``None`` when it is absent or expired."""

        entry = self._entries.get(key)
        if entry is None:
            return None
        if self._clock() - entry.stored_at >= self._ttl_s:
            del self._entries[key]
            return None
        return entry

    def put(
        self, key: tuple[str, str, str], series: ChartSeries, body: bytes
    ) -> CachedChart:
        """Memoise ``series`` (and its ``body``) under ``key`` and answer it."""

        entry = CachedChart(series=series, body=body, stored_at=self._clock())
        self._entries[key] = entry
        while len(self._entries) > self._limit:
            self._entries.pop(next(iter(self._entries)))
        return entry

    def epoch(self) -> int:
        """The cache window the clock is in — the validator's time input."""

        return int(self._clock() // self._ttl_s)


def chart_cache(request: Request) -> ChartCache:
    """The process's series cache, created on first use (one per app, not global)."""

    cache = getattr(request.app.state, "chart_cache", None)
    if isinstance(cache, ChartCache):
        return cache
    cache = ChartCache()
    request.app.state.chart_cache = cache
    return cache


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


def chart_symbol(raw: Any) -> str:
    """``raw`` as a canonical ticker, or ``404 unknown_symbol``.

    A refusal here is a ``404`` rather than a ``422`` because the symbol is the
    resource: ``AAPL;DROP`` and ``..`` do not name one, and the provider is never
    asked about a string this route cannot chart.
    """

    token = raw.strip() if isinstance(raw, str) else ""
    if token and _SYMBOL.fullmatch(token) and any(char.isalnum() for char in token):
        return token.upper()
    raise ApiError(
        status_code=404,
        error="unknown_symbol",
        message=f"{raw!r} is not a symbol this route can chart",
    )


def chart_range(raw: Any) -> str:
    """``raw`` as one of :data:`RANGES`, or ``422 invalid_range``."""

    token = raw.strip() if isinstance(raw, str) else ""
    if token in RANGES:
        return token
    raise ApiError(
        status_code=422,
        error="invalid_range",
        message=f"range must be one of {', '.join(RANGES)}; got {raw!r}",
    )


def chart_interval(raw: Any, window: str) -> str:
    """``raw`` inside ``window``'s table, or that range's default when absent.

    The refusal names the legal set *for the range asked for*, because "1m is
    legal" is true of a session and false of a year, and a caller needs the
    answer to their own question.
    """

    legal = LEGAL_INTERVALS[window]
    if raw is None:
        # Absent, not blank: a caller who sends ``?interval=`` named a value and
        # gets the legal set back rather than a silent default.
        return DEFAULT_INTERVALS[window]
    token = raw.strip() if isinstance(raw, str) else ""
    if token in legal:
        return token
    raise ApiError(
        status_code=422,
        error="invalid_interval",
        message=(
            f"interval must be one of {', '.join(legal)} for range={window}; "
            f"got {raw!r}"
        ),
    )


def chart_url(symbol: str, *, window: str, interval: str, base_url: str = BASE_URL) -> str:
    """The provider request for one series.

    ``includePrePost=false`` is deliberate: a chart of the session is the
    session, and a pre-market print outside it would make the series' first bar
    disagree with the open the rest of the product quotes.
    """

    return (
        f"{base_url.rstrip('/')}/v8/finance/chart/{symbol}"
        f"?range={window}&interval={interval}&includePrePost=false"
    )


# ---------------------------------------------------------------------------
# The provider
# ---------------------------------------------------------------------------


async def fetch_series(
    symbol: str,
    *,
    window: str,
    interval: str,
    base_url: str = BASE_URL,
    timeout_s: float = FETCH_TIMEOUT_S,
    limit: int = MAX_BARS,
) -> ChartSeries:
    """One provider read, parsed. The only function here that touches the network.

    ``limit`` is ``window``'s own bar ceiling (:func:`bars_for`), passed in
    rather than looked up here so a caller that knows the window decides how
    much of it is published. A ``404`` — or a ``200`` whose ``chart.error``
    says the symbol is unknown — is ``404 unknown_symbol``; any other failure
    is ``503 chart_unavailable``, because "we could not ask" is not "there is
    nothing".
    """

    # The HTTP seam is imported here rather than at module scope so the route
    # module stays cheap to import, and the user agent comes from the **market**
    # layer: ``app.api`` may see the provider registry but never an adapter
    # (``tests/_import_rules.py`` rule 8), and this is the constant
    # ``app.api.market`` already speaks to Yahoo with.
    from app.providers import http as provider_http

    url = chart_url(symbol, window=window, interval=interval, base_url=base_url)
    headers = {"user-agent": BROWSER_USER_AGENT, "accept": "application/json"}
    try:
        async with provider_http.build_client(timeout_s=timeout_s, headers=headers) as client:
            response = await client.get(url)
    except Exception as exc:  # noqa: BLE001 - a transport failure is a 503, not a 500
        raise ApiError(
            status_code=503,
            error="chart_unavailable",
            message=f"the chart provider could not be reached for {symbol}: {exc}",
        ) from exc

    status = int(getattr(response, "status_code", 0))
    if status == 404:
        raise ApiError(
            status_code=404,
            error="unknown_symbol",
            message=f"the chart provider has no series for {symbol}",
        )
    if status != 200:
        raise ApiError(
            status_code=503,
            error="chart_unavailable",
            message=f"the chart provider answered {status} for {symbol}",
        )

    try:
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - an unreadable body is an outage
        raise ApiError(
            status_code=503,
            error="chart_unavailable",
            message=f"the chart provider's body for {symbol} was not JSON: {exc}",
        ) from exc
    chart = payload.get("chart") if isinstance(payload, Mapping) else None
    chart = chart if isinstance(chart, Mapping) else {}
    result = chart.get("result") or []
    error = chart.get("error")
    if error or not result or not isinstance(result[0], Mapping):
        # A delisted ticker answers here, and so does an empty result set: the
        # provider is saying it knows no such symbol, not that today had no bars.
        description = ""
        if isinstance(error, Mapping) and error.get("description"):
            description = f": {error.get('description')}"
        raise ApiError(
            status_code=404,
            error="unknown_symbol",
            message=f"the chart provider has no series for {symbol}{description}",
        )
    return parse_chart_series(result[0], limit=limit)


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


async def _chart_answer(
    request: Request, symbol: str, range_: str, interval: str | None, *, head: bool
) -> Response:
    """The series, its ``304``, or its ``HEAD`` — the ``304`` before any fetch."""

    wanted = chart_symbol(symbol)
    window = chart_range(range_)
    chosen = chart_interval(interval, window)

    cache = chart_cache(request)
    key = (wanted, window, chosen)
    cached = cache.get(key)

    # The tag names the inputs, not the bytes: hashing the body could only happen
    # after a fetch, which is exactly what a revalidation must not pay for.
    tag = etag(
        "chart",
        chart_revision(wanted, window, chosen, cache.epoch()),
    )
    headers = {
        "ETag": f"W/{tag}",
        "Cache-Control": "no-cache",
        "X-Chart-Cache": "hit" if cached is not None else "miss",
    }
    if not_modified(request, tag):
        return Response(status_code=304, headers=headers)

    if cached is None:
        series = await fetch_series(
            wanted, window=window, interval=chosen, limit=bars_for(window)
        )
        body = encode(
            chart_document(wanted, window=window, interval=chosen, series=series)
        )
        cached = cache.put(key, series, body)

    if head:
        return Response(
            status_code=200,
            headers={**headers, "Content-Length": str(len(cached.body))},
        )
    return Response(content=cached.body, media_type="application/json", headers=headers)


def chart_revision(
    symbol: str, window: str, interval: str, epoch: int, shape: str = CHART_SHAPE
) -> str:
    """The answer's identity: the series asked for, in the cache window it was asked.

    ``epoch`` is what makes a ``304`` honest: the same symbol/range/interval in a
    later five-minute window may be different bars, so it is a different entity
    and the client is told so with a ``200``.
    """

    return (
        "v1"
        f";symbol={symbol}"
        f";range={window}"
        f";interval={interval}"
        f";epoch={int(epoch)}"
        f";shape={shape}"
    )


@router.get("/market/chart/{symbol}")
async def market_chart(
    request: Request,
    symbol: str,
    range_: str = Query("1d", alias="range"),
    interval: str | None = Query(None),
) -> Response:
    """The OHLC series a browser draws (GET).

    ``previous_close`` is the previous **session's** close (Yahoo's
    ``chartPreviousClose``): Friday's on a Monday ``1d`` chart, and the close
    immediately before the window on a longer one — never "yesterday's close".
    A bar with no usable close is absent, not zero; ``truncated`` says the series
    kept only the most recent :data:`MAX_BARS` bars.
    """

    return await _chart_answer(request, symbol, range_, interval, head=False)


@router.head("/market/chart/{symbol}", include_in_schema=False)
async def market_chart_head(
    request: Request,
    symbol: str,
    range_: str = Query("1d", alias="range"),
    interval: str | None = Query(None),
) -> Response:
    """The same headers as GET, no body: a monitor's question, not the chart."""

    return await _chart_answer(request, symbol, range_, interval, head=True)


__all__ = [
    "BAR_LIMITS",
    "BASE_URL",
    "CACHE_TTL_S",
    "CHART_SHAPE",
    "DEFAULT_INTERVALS",
    "INTERVALS",
    "LEGAL_INTERVALS",
    "MAX_BARS",
    "MEMO_LIMIT",
    "RANGES",
    "SOURCE",
    "CachedChart",
    "ChartCache",
    "ChartSeries",
    "chart_cache",
    "bars_for",
    "chart_document",
    "chart_interval",
    "chart_range",
    "chart_revision",
    "chart_symbol",
    "chart_url",
    "fetch_series",
    "market_chart",
    "market_chart_head",
    "parse_chart_series",
    "router",
]
