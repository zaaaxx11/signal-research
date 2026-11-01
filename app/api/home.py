"""``GET /market/home`` — the whole dashboard, in one request.

Why one document and not seven
------------------------------
A home page paints seven blocks at once: five indices, a breadth bar, a spark
strip, a movers table, a small treemap, three headlines and five Form 4 rows.
Asked for separately that is seven requests, several scans of 5,576 rows and —
the real problem — seven different moments: a scan that refreshes between the
breadth call and the treemap call makes the page contradict itself, and no
client can tell. This route answers them together, from **one scan, one
universe build, one document**, and it keeps every block honest about its own
source.

Every key is always present, and a failed block is ``null``
-----------------------------------------------------------
The contract is a shape a client can parse without a lookup: ``{as_of,
indices[], breadth{}, sparks[], movers{}, map{}, news[], filings[],
unavailable[], method}``. A block whose source failed is ``null`` — never an
empty list, because ``[]`` and "nobody could be asked" are different facts and
a client cannot tell them apart. The name of the failed block joins
``unavailable``; a single failed symbol joins it under its own name
(``index:^VIX``, ``spark:XYZ``) and the rest of the block is still published.
``breadth``, ``sparks``, ``movers``, ``map`` and ``filings`` all descend from one
source — the scan — so a dead scanner leaves all five null and the document
still answers ``200`` with its indices and its headlines. A dead news service
loses ``news`` and nothing else; that is the whole point of a document that
states its gaps.

What each block measures, and where it comes from
-------------------------------------------------
* ``indices`` — ``^GSPC``, ``^IXIC``, ``^DJI``, ``^RUT``, ``^VIX``, in that
  order, each ``{symbol, label, price, change_pct, closes}``. ``closes`` is the
  last :data:`CLOSES_INDEX` close points of a 1-day 5-minute series, read
  through :mod:`app.api.chart`'s reader and cache (not a second Yahoo client),
  and re-derived at most once per :data:`SERIES_TTL_S`. ``change_pct`` is
  measured against Yahoo's ``chartPreviousClose`` — the previous *session's*
  close — so on a Monday it is measured against Friday, which is the honest
  baseline. On a market holiday the provider answers the last session's bars;
  the document repeats them and never calls them live.
* ``breadth`` — one pass over the selector's universe, counted where the
  universe lives. ``advancing``/``declining``/``unchanged`` partition it (a
  ``null`` change is unknown, and the house rule counts it as *not moving*, not
  as a decline). ``new_highs``/``new_lows`` need a price and the 52-week extreme.
  ``above_50``/``below_50`` read ``sma_50``, which only the depth store carries
  and which is a **signed percent** (``(close / SMA50 - 1) * 100``); a row with
  no depth is **neither** above nor below, so the two counts need not sum to the
  universe and never count a missing measurement as a decline.
* ``sparks`` — the :data:`SPARK_COUNT` highest-turnover rows of that universe,
  ordered by dollar turnover descending then symbol, so the choice is
  deterministic for a given scan generation rather than a random sample that
  moves under a polling client. ``closes`` is the last :data:`CLOSES_SPARK`
  points of the same 1-day series, from the same reader and cache.
* ``movers`` — up to :data:`MOVERS_PER_GROUP` rows from each of ``gainers``,
  ``new_high``, ``overbought`` and ``new_low``, forty rows at most, every row
  naming its ``group``. The predicates and the ordering are
  :func:`app.api.signals.collect`'s, called once over the universe: a second
  copy of "price >= 0.999 * week52_high" here would drift from the signals page
  the first time either side was edited.
* ``map`` — :func:`app.api.map.build_map_document`'s sectors, truncated to the
  :data:`MAP_TILES` largest tiles with ``tiles``/``tiles_total`` stating the cut.
  Truncation is a **prefix per sector**: a sector's tiles are already ordered by
  cap, so the largest 600 overall are the first *k* of each sector and a tile is
  never split from its sector's prefix (dropping a bigger tile to keep a smaller
  one would make the treemap lie about the market's shape). ``tiles_total`` is
  the full map's ``mapped`` — 5,573 on the live US tab — not the universe, which
  is three larger: three rows carry no ``change_pct`` and cannot be drawn.
* ``news`` — the first :data:`NEWS_ITEMS` items of the market news merge,
  through :func:`app.api.market_news.merge` and its 60-second bucket, projected
  to ``{headline, url, source, published_at}``. The bucket is the same
  ``(limit=3)`` entry ``/market/news?limit=3`` reads, so the two cannot disagree
  about which three stories are first.
* ``filings`` — the latest Form 4 row per most-active symbol
  (``{symbol, owner, role, filed}``), read through
  :func:`app.engine.insider.collect_insider` — the same EDGAR reader
  ``/engine/insider/{symbol}`` calls — against the same identity master and the
  same document cache the route uses. A symbol whose filing cannot be read is
  absent; when every one fails the block is ``null``. The whole block is kept
  for :data:`FILINGS_TTL_S`, because a filing does not change every minute; a
  *failed* block is not kept, so a recovered EDGAR is seen on the next build.

Revalidation, and the memo behind it
------------------------------------
The document is memoised per **``(region, scan generation, depth identity, news
bucket, index bucket)``** — every input that can change a number in it. The
scan generation names exactly one scan page for the life of the process (a
re-scan moves it even when it returns the same rows); the depth identity is the
store's ``path|mtime_ns|size``, because the breadth's ``sma50`` counts move when
the collector rewrites the file and no scan changes; the news and index buckets
are 60-second windows, because the headline block and the chart series are read
once per minute. The validator is derived from that same revision, **before any
work**: a warm ``If-None-Match`` costs one ``stat`` of the depth store and a
dict lookup — no scan, no chart fetch, no map build, no encode. ``Cache-Control:
no-cache`` rides on both answers, and ``HEAD`` is its own route (a second method
on one ``api_route`` collides on the operation id) carrying the length the
``GET`` would have sent and none of its bytes.

Two cold requests make one build
--------------------------------
The memo's check-and-store is atomic, but a build ``await``s — so two requests
arriving together would both miss and both build. A per-application
:class:`asyncio.Lock` makes the second waiter re-check the memo after the first
finishes and answer from it; the scan itself is already single-flight inside
:class:`app.api.screens.ScanCache`. A degraded document (a dead scanner) is
deliberately **not** memoised: caching "we could not measure" would keep saying
it after the scanner recovered, and the next request must be free to try again.

The payload is bounded, and the bound is asserted
-------------------------------------------------
The US tab answers ~40 KB raw: 600 tiles are 18 KB of that, five index series
and eight sparks ~9 KB, forty movers ~5 KB, three headlines ~1.5 KB. The budget
is :data:`PAYLOAD_BUDGET` (60 KB) and ``tests/test_api_market_home.py`` asserts
the serialised length on the live tile count, so a block that grows past it
fails the suite rather than the client.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.api import alt as alt_api
from app.api import map as map_api
from app.api import market_news as news_api
from app.api.chart import (
    ChartSeries,
    chart_cache,
    chart_document,
    fetch_series,
)
from app.api.discovery import _universe_family
from app.api.sectors import (
    CachedDocument,
    encode,
    etag,
    identity,
    memo_for,
    moment,
    number,
    serve,
    stamp_for,
    universe_for,
)
from app.api.signals import _with_depth, collect
from app.auth import ApiError
from app.engine.insider import collect_insider
from app.screen.depth_overlay import _entry_for
from app.screen.depth_source import current_depth, resolve_store_path

logger = logging.getLogger("mee.api.home")

router = APIRouter(tags=["discovery"])

#: The charters the index strip publishes, in the only order it prints them.
INDEX_CHARTERS: tuple[tuple[str, str], ...] = (
    ("^GSPC", "S&P 500"),
    ("^IXIC", "Nasdaq Composite"),
    ("^DJI", "Dow Jones"),
    ("^RUT", "Russell 2000"),
    ("^VIX", "VIX"),
)

#: The one series both the index strip and the spark strip read: one session of
#: five-minute bars. A second window would be a second provider read per symbol
#: for a picture that is drawn at the same width.
SERIES_RANGE = "1d"
SERIES_INTERVAL = "5m"

#: How many closes an index publishes. Sixty five-minute bars is most of a
#: session (the provider sends ~79), which is what a strip can draw.
CLOSES_INDEX = 60

#: How many closes a spark publishes. A spark has no axis; thirty points is the
#: shape of a session and a third of the bytes.
CLOSES_SPARK = 30

#: How many sparks the strip carries: the eight highest-turnover rows.
SPARK_COUNT = 8

#: How many rows each movers group contributes, and how many groups there are.
MOVERS_PER_GROUP = 10
MOVER_GROUPS: tuple[str, ...] = ("gainers", "new_high", "overbought", "new_low")

#: How many tiles the dashboard's treemap carries, and therefore how big it is.
#: 600 is a treemap a phone can draw; the full 5,573-tile document stays on
#: ``/market/map`` for the page that wants it.
MAP_TILES = 600

#: How many headlines the dashboard shows, and how many Form 4 rows.
NEWS_ITEMS = 40
FILINGS_SYMBOLS = 5
#: Turnover leaders read for the two insider tables (one EDGAR read each).
INSIDER_SYMBOLS = 10
#: Rows the side tables show.
FUTURES_ROWS = 8
FOREX_ROWS = 8

#: How long a derived series (price, change, closes) is reused. It is the
#: document's index bucket as well: a re-read inside the same 60-second window
#: is the same bars, so the tag must not move.
SERIES_TTL_S = 60.0

#: How long the whole filings block is reused. Fifteen minutes is the news
#: service's own window; a Form 4 filing does not change on a faster clock.
FILINGS_TTL_S = 900.0

#: How many documents, series and filings blocks the process keeps. Each is a
#: memory budget for a handful of regions and generations, not a hit-rate goal.
MEMO_LIMIT = 8
SERIES_LIMIT = 32
FILINGS_LIMIT = 8

#: The document's shape and meaning, folded into the tag. Bump it when a key
#: changes or a number changes meaning: a tag that survives a shape change is a
#: ``304`` for a body the client cannot parse.
HOME_SHAPE = "1"

#: The raw payload budget for the US tab, asserted by the suite.
PAYLOAD_BUDGET = 60 * 1024

#: What ``method`` states, one line per block. It is data, not prose in a
#: docstring, because a client renders it beside the numbers and the words must
#: ship with them. No line says "live": a closed market repeats the last session
#: and the document must not claim otherwise.
METHOD: dict[str, str] = {
    "as_of": "the moment this document was built (UTC), not a quote timestamp",
    "indices": "Yahoo 1d/5m bars through /market/chart's reader; last 60 closes; 60 s cache",
    "breadth": "one pass over the selector's universe; sma50 from the depth store; null is neither side",
    "sparks": "the eight highest-turnover rows of that universe; last 30 closes",
    "movers": "the /market/signals rules: gainers, new_high, overbought, new_low; ten each",
    "map": "the 600 largest tiles of /market/map; whole symbols inside a sector",
    "news": "the first three items of the market news merge; 60 s bucket",
    "filings": "the latest Form 4 per most-active symbol; block cached 15 minutes",
    "session": "the last session the provider has bars for; a closed market repeats it",
}


# ---------------------------------------------------------------------------
# Small readers
# ---------------------------------------------------------------------------


def _symbol(value: Any) -> str | None:
    """A universe cell as a ticker, or ``None`` when it names nothing."""

    if not isinstance(value, str):
        return None
    token = value.strip()
    return token or None


def _plain(value: float | None) -> float | int | None:
    """A number for the document: an integral one prints as an integer."""

    if value is None:
        return None
    if value.is_integer() and abs(value) < 2**53:
        return int(value)
    return value


# ---------------------------------------------------------------------------
# Breadth: one pass, and a null is not a decline
# ---------------------------------------------------------------------------


def breadth_of(
    rows: Iterable[Mapping[str, Any]],
    *,
    sma50: Mapping[str, float | None] | None = None,
    sma200: Mapping[str, float | None] | None = None,
) -> dict[str, float]:
    """Count ``rows`` in one pass: the partition, the extremes and the 50-day.

    ``sma50`` is the depth store's **signed percent** distance from the 50-day
    average (``(close / SMA50 - 1) * 100``), keyed by ticker: it is *not* the
    scan's ``SMA50`` cell, which is the average's price **level** — a level
    compared against zero would count every listed company as above its average,
    which is why this block reads the store and not the row. ``above_50`` /
    ``below_50`` are then a sign test over the rows that carry the measurement.
    A row the store does not know is **neither**: it is not a company below its
    average, it is a company this deployment never measured, and counting it as
    below would make the depth collector's coverage look like a market crash.
    Exactly zero is neither too — a price at its average is not above it.

    ``new_highs``/``new_lows`` need both a price and the 52-week extreme, and
    the test is inclusive (a price *at* its high is a new high). A level of zero
    or below is refused rather than compared: it would admit every row.
    """

    levels = sma50 or {}
    universe = 0
    advancing = 0
    declining = 0
    unchanged = 0
    new_highs = 0
    new_lows = 0
    above = 0
    below = 0
    above_200 = 0
    below_200 = 0
    up_volume = 0.0
    down_volume = 0.0

    for row in rows:
        universe += 1
        change = number(row.get("change_pct"))
        if change is None:
            # Unknown is not a decline and not an advance; the partition has to
            # still add up to the universe, so it is counted as not moving.
            unchanged += 1
        elif change > 0:
            advancing += 1
        elif change < 0:
            declining += 1
        else:
            unchanged += 1

        # Volume follows the same direction as the count, but only for a row
        # whose side is known: an unknown change has no side to attribute its
        # shares to, and a missing volume is not zero shares traded.
        if change is not None and change != 0:
            volume = number(row.get("volume"))
            if volume is not None and volume > 0:
                if change > 0:
                    up_volume += volume
                else:
                    down_volume += volume

        price = number(row.get("price"))
        if price is not None and price > 0:
            high = number(row.get("high_52w"))
            if high is not None and high > 0 and price >= high:
                new_highs += 1
            low = number(row.get("low_52w"))
            if low is not None and low > 0 and price <= low:
                new_lows += 1

        symbol = row.get("symbol")
        if not isinstance(symbol, str):
            continue
        sma = number(levels.get(symbol))
        if sma is not None:
            if sma > 0:
                above += 1
            elif sma < 0:
                below += 1
        long_levels = sma200 or {}
        sma_long = number(long_levels.get(symbol))
        if sma_long is not None:
            if sma_long > 0:
                above_200 += 1
            elif sma_long < 0:
                below_200 += 1

    return {
        "universe": universe,
        "advancing": advancing,
        "declining": declining,
        "unchanged": unchanged,
        "new_highs": new_highs,
        "new_lows": new_lows,
        "above_50": above,
        "below_50": below,
        "above_200": above_200,
        "below_200": below_200,
        "up_volume": round(up_volume, 2),
        "down_volume": round(down_volume, 2),
    }


def sma_by_symbol(
    rows: Iterable[Mapping[str, Any]], *, field: str = "sma50", settings: Any = None
) -> dict[str, float]:
    """Every row's ``sma50`` percent from the depth store, keyed by ticker.

    One dict lookup per row through :func:`app.screen.depth_overlay._entry_for`,
    the overlay's own symbol resolution (exact spelling first, then the
    dotted↔hyphenated swap), so ``BRK.B`` joins the store's ``BRK-B`` here
    exactly as it does in a screen. A row with no store entry — 1,359 of the
    live 5,576 — simply has no key, which is what makes it *unknown* rather than
    below.

    ``settings`` is the application's own object (see :func:`settings_of`), so
    the path read here is the path the revision's identity was taken from.
    """

    table = current_depth(settings).symbols
    levels: dict[str, float] = {}
    if not table:
        return levels
    for row in rows:
        symbol = row.get("symbol")
        if not isinstance(symbol, str):
            continue
        entry = _entry_for(symbol, table)
        if entry is None:
            continue
        value = number(entry.get(field))
        if value is not None:
            levels[symbol] = value
    return levels


def sma50_by_symbol(
    rows: Iterable[Mapping[str, Any]], *, settings: Any = None
) -> dict[str, float]:
    """The 50-day distance for every row that has one (see :func:`sma_by_symbol`)."""

    return sma_by_symbol(rows, field="sma50", settings=settings)


# ---------------------------------------------------------------------------
# Sparks, movers and the truncated map
# ---------------------------------------------------------------------------


def spark_rows(
    rows: Iterable[Mapping[str, Any]], *, count: int = SPARK_COUNT
) -> list[Mapping[str, Any]]:
    """The ``count`` highest-turnover rows, dollar turnover descending then symbol.

    Deterministic on purpose: the strip is meant to be stable for one scan
    generation, so a client polling the dashboard does not watch its own strip
    reshuffle. A row with no dollar turnover cannot be ranked on a dollar scale
    (a non-USD listing keeps its local ``turnover`` and no ``turnover_usd``), so
    it is skipped rather than guessed at.
    """

    ranked: list[tuple[float, str, Mapping[str, Any]]] = []
    for row in rows:
        symbol = _symbol(row.get("symbol"))
        turnover = number(row.get("turnover_usd"))
        if symbol is None or turnover is None or turnover <= 0:
            continue
        ranked.append((turnover, symbol, row))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [row for _turnover, _symbol, row in ranked[: max(0, int(count))]]


def movers_of(groups: Sequence[Any]) -> list[dict[str, Any]]:
    """Up to forty rows from the four groups, each naming the group it came from.

    ``groups`` is :func:`app.api.signals.collect`'s result — the same pass the
    signals page publishes — so a mover is the same row, in the same order, as
    it appears on that page at ``per=10``. The projection keeps five keys: the
    dashboard row needs a symbol, a name, a price, a change and a group, and the
    volume cell is the signals table's business.
    """

    by_id = {result.rule.id: result for result in groups}
    rows: list[dict[str, Any]] = []
    for group in MOVER_GROUPS:
        result = by_id.get(group)
        if result is None:
            continue
        for row in result.rows[:MOVERS_PER_GROUP]:
            rows.append(
                {
                    "symbol": row.get("symbol"),
                    "name": row.get("name"),
                    "price": row.get("price"),
                    "change_pct": row.get("change_pct"),
                    "group": group,
                }
            )
    return rows


def group_rows(groups: Sequence[Any], group: str, *, limit: int) -> list[dict[str, Any]]:
    """``limit`` rows of one signals group, or none when it is not published."""

    for result in groups:
        if result.rule.id == group:
            return [dict(row) for row in result.rows[: max(0, int(limit))]]
    return []


def truncate_map(
    document: Mapping[str, Any], *, tiles: int = MAP_TILES
) -> dict[str, Any]:
    """``document`` with every sector's symbol list cut to the largest ``tiles``.

    The cut is taken over **all** tiles by published cap, then applied as a
    prefix per sector. Two properties matter and both are contracts:

    * *largest first*: a kept tile is never smaller than a dropped one, so the
      six hundred boxes drawn are the six hundred biggest companies the map can
      place;
    * *no split*: a sector keeps the first *k* of its own list, never a hole in
      the middle. The ranker breaks a tie at the published cap by the map's own
      order — sector order, then the sector's tile order — which is exactly what
      makes the per-sector cut a prefix; ranking a tie by symbol could keep a
      sector's third tile while dropping its second.

    A sector whose every tile fell outside the cut keeps its header (id, label,
    count, weighted value, advancing/declining) and publishes no tiles: the
    legend still says how big the sector is, and ``tiles``/``tiles_total`` state
    that this document is a cut of the whole.
    """

    sectors = list(document.get("sectors") or ())
    ranked: list[tuple[float, int, int]] = []
    for sector_index, sector in enumerate(sectors):
        for tile_index, tile in enumerate(sector.get("symbols") or ()):
            cap = number(tile.get("c"))
            ranked.append((-(cap if cap is not None else 0.0), sector_index, tile_index))
    ranked.sort()

    counts = [0] * len(sectors)
    for _cap, sector_index, _tile_index in ranked[: max(0, int(tiles))]:
        counts[sector_index] += 1

    truncated: list[dict[str, Any]] = []
    for sector_index, sector in enumerate(sectors):
        kept = counts[sector_index]
        truncated.append(
            {
                "id": sector.get("id"),
                "label": sector.get("label"),
                "count": sector.get("count"),
                "value": sector.get("value"),
                "adv": sector.get("adv"),
                "dec": sector.get("dec"),
                "symbols": list(sector.get("symbols") or ())[:kept],
            }
        )
    return {
        "tiles": sum(counts),
        "tiles_total": int(document.get("mapped") or 0),
        "universe_total": int(document.get("universe_total") or 0),
        "sectors": truncated,
    }


# ---------------------------------------------------------------------------
# Series: the chart reader, and a 60-second derivation on top of it
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    """One symbol's last price, its move and its closes — as the blocks publish it.

    ``price`` is ``None`` only for a series with no usable bar, which the caller
    treats as a failed read rather than as a zero: a chart that could not be
    drawn is not a price of nothing.
    """

    price: float | None
    change_pct: float | None
    closes: tuple[float, ...]


def point_of(series: ChartSeries) -> SeriesPoint:
    """A parsed chart series → the three numbers the strips publish.

    ``change_pct`` is measured against :attr:`ChartSeries.previous_close` — the
    previous **session's** close, which is the only baseline the provider states.
    A series without one publishes ``change_pct: null``: falling back to the
    first bar would call an intraday move a day's move.
    """

    closes = tuple(
        float(bar["c"])
        for bar in series.bars
        if bar.get("c") is not None and float(bar["c"]) > 0
    )
    price = closes[-1] if closes else None
    previous = series.previous_close
    change = (
        (price / previous - 1.0) * 100.0
        if price is not None and previous is not None and previous > 0
        else None
    )
    return SeriesPoint(price=price, change_pct=change, closes=closes)


class SeriesCache:
    """One derived series per symbol, for :data:`SERIES_TTL_S` seconds.

    The chart cache behind it already holds the parsed bars for five minutes;
    this layer exists because the *document's* index bucket is a minute: a
    rebuild inside one bucket must reuse the derived numbers, and a rebuild
    after it must be allowed to see new bars even though the chart cache would
    still answer. The clock is injectable so a test can advance the bucket.
    """

    __slots__ = ("_clock", "_entries", "_limit", "_ttl_s")

    def __init__(
        self,
        *,
        ttl_s: float = SERIES_TTL_S,
        clock: Callable[[], float] = time.time,
        limit: int = SERIES_LIMIT,
    ) -> None:
        self._ttl_s = float(ttl_s)
        self._clock = clock
        self._limit = max(1, int(limit))
        self._entries: dict[str, tuple[float, SeriesPoint]] = {}

    def get(self, symbol: str) -> SeriesPoint | None:
        """The fresh point for ``symbol``, or ``None`` when absent or expired."""

        entry = self._entries.get(symbol)
        if entry is None:
            return None
        stored_at, point = entry
        if self._clock() - stored_at >= self._ttl_s:
            del self._entries[symbol]
            return None
        return point

    def put(self, symbol: str, point: SeriesPoint) -> SeriesPoint:
        """Memoise ``point`` under ``symbol`` and answer it."""

        self._entries[symbol] = (self._clock(), point)
        while len(self._entries) > self._limit:
            self._entries.pop(next(iter(self._entries)))
        return point

    def bucket(self) -> int:
        """The 60-second window the clock is in — the document's index bucket."""

        return int(self._clock() // self._ttl_s)


class FilingsCache:
    """One filings block per symbol tuple, for :data:`FILINGS_TTL_S` seconds."""

    __slots__ = ("_clock", "_entries", "_limit", "_ttl_s")

    def __init__(
        self,
        *,
        ttl_s: float = FILINGS_TTL_S,
        clock: Callable[[], float] = time.time,
        limit: int = FILINGS_LIMIT,
    ) -> None:
        self._ttl_s = float(ttl_s)
        self._clock = clock
        self._limit = max(1, int(limit))
        self._entries: dict[tuple[str, ...], tuple[float, list[dict[str, Any]]]] = {}

    def get(self, key: tuple[str, ...]) -> list[dict[str, Any]] | None:
        """The fresh block for ``key``, or ``None`` when absent or expired."""

        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, rows = entry
        if self._clock() - stored_at >= self._ttl_s:
            del self._entries[key]
            return None
        return [dict(row) for row in rows]

    def put(self, key: tuple[str, ...], rows: Sequence[Mapping[str, Any]]) -> None:
        """Memoise ``rows`` under ``key`` (only called for a non-empty block)."""

        self._entries[key] = (self._clock(), [dict(row) for row in rows])
        while len(self._entries) > self._limit:
            self._entries.pop(next(iter(self._entries)))


@dataclass(slots=True)
class HomeCaches:
    """The three clocks and stores one document is assembled from."""

    series: SeriesCache
    filings: FilingsCache
    clock: Callable[[], float]


def caches_of(request: Request) -> HomeCaches:
    """The process's series and filings caches, created on first use."""

    caches = getattr(request.app.state, "home_caches", None)
    if isinstance(caches, HomeCaches):
        return caches
    caches = HomeCaches(series=SeriesCache(), filings=FilingsCache(), clock=time.time)
    request.app.state.home_caches = caches
    return caches


async def series_for(
    request: Request, caches: HomeCaches, symbol: str
) -> SeriesPoint | None:
    """One symbol's point, through the chart route's cache and reader.

    Three layers, cheapest first: this document's 60-second derivation, the
    chart route's five-minute parsed series, and — only when both miss — one
    provider read, stored in the chart cache with the body the chart route would
    have served, so the next ``GET /market/chart/{symbol}`` is a hit rather than
    a second fetch. A failed read is ``None``, never an empty series.
    """

    point = caches.series.get(symbol)
    if point is not None:
        return point

    charts = chart_cache(request)
    key = (symbol, SERIES_RANGE, SERIES_INTERVAL)
    cached = charts.get(key)
    if cached is None:
        try:
            series = await fetch_series(symbol, window=SERIES_RANGE, interval=SERIES_INTERVAL)
        except ApiError as exc:
            logger.warning("home: no %s series for %s: %s", SERIES_RANGE, symbol, exc.message)
            return None
        # Store the body the chart route would have written, so this read warms
        # that route too instead of leaving it to fetch the same bars again.
        body = encode(
            chart_document(
                symbol, window=SERIES_RANGE, interval=SERIES_INTERVAL, series=series
            )
        )
        cached = charts.put(key, series, body)

    point = point_of(cached.series)
    if point.price is None:
        logger.warning("home: the %s series for %s carried no bar", SERIES_RANGE, symbol)
        return None
    return caches.series.put(symbol, point)


async def series_points(
    request: Request, caches: HomeCaches, symbols: Sequence[str]
) -> dict[str, SeriesPoint | None]:
    """Every symbol's point, read concurrently; a failure is a ``None`` value."""

    if not symbols:
        return {}
    pairs = await asyncio.gather(*(series_for(request, caches, symbol) for symbol in symbols))
    return dict(zip(symbols, pairs, strict=True))


# ---------------------------------------------------------------------------
# Filings: the Form 4 reader the /engine route uses, one row per symbol
# ---------------------------------------------------------------------------


def security_master_of(request: Request) -> Any:
    """The identity master the ``/engine`` routes read, or ``None``.

    Mirrors ``app.api.engine.engine_service_of``'s lookup without importing that
    module: a route module may not import another one, and the master on
    ``app.state.engine`` is the object both need — the CIK is what turns a
    ticker into a registrant.
    """

    service = getattr(request.app.state, "engine", None)
    return getattr(service, "security_master", None)


def filing_cache_dir() -> Path | None:
    """``MEE_ENGINE_CACHE_DIR`` as a path, or ``None`` when unset.

    The same deployment setting ``/engine/insider`` reads for its per-CIK
    document cache (``app.api.engine._engine_cache_dir``), stated here because a
    route module may not import another route. Unset means "no disk cache": one
    request per filing and never a silent cache from a previous process.
    """

    raw = os.environ.get("MEE_ENGINE_CACHE_DIR", "").strip()
    return Path(raw).expanduser() if raw else None


async def filing_row(
    request: Request, symbol: str, *, master: Any, cache_dir: Path | None
) -> dict[str, Any] | None:
    """The newest Form 4 row for one symbol, or ``None`` when it cannot be read."""

    record = master.get(symbol) if master is not None else None
    cik = getattr(record, "cik", None)
    if not isinstance(cik, str) or not cik.strip():
        return None
    items, errors = await collect_insider(symbol, cik, cache_dir=cache_dir, limit=1)
    if not items:
        if errors:
            logger.warning("home: no Form 4 body for %s: %s", symbol, "; ".join(errors))
        return None
    latest = items[0]
    return {
        "symbol": symbol,
        "owner": getattr(latest, "owner_name", None),
        "role": getattr(latest, "role", None),
        "filed": getattr(latest, "filed", None),
    }


async def filings_block(
    request: Request, symbols: Sequence[str], caches: HomeCaches
) -> list[dict[str, Any]] | None:
    """The latest Form 4 row for each of ``symbols``; the whole block cached.

    The cache is keyed by the symbol tuple, so a re-scan that changes the
    most-active five is a different block rather than the previous five under a
    new name. A block that came back **empty** is not stored: fifteen minutes of
    "EDGAR could not be read" would outlive the outage that caused it.
    """

    key = tuple(symbols)
    cached = caches.filings.get(key)
    if cached is not None:
        return cached

    master = security_master_of(request)
    cache_dir = filing_cache_dir()
    rows = await asyncio.gather(
        *(
            filing_row(request, symbol, master=master, cache_dir=cache_dir)
            for symbol in symbols
        )
    )
    block = [row for row in rows if row is not None]
    if block:
        caches.filings.put(key, block)
    return block or None


# ---------------------------------------------------------------------------
# The side tables: contracts, pairs, new listings, insider rows
# ---------------------------------------------------------------------------


async def alt_rows(request: Request, name: str, count: int) -> list[dict[str, Any]] | None:
    """The first ``count`` rows of one `/market/alt` class, from its own cache.

    Same catalogue, same reader and same sixty-second class cache the alt route
    uses, so a dashboard read and an alt page read cannot disagree, and a second
    dashboard read inside the minute costs nothing.
    """

    try:
        state = alt_api.alt_state(request)
        cached = state.classes.get(name)
        if cached is None:
            measured = await alt_api.read_quotes(state, alt_api.CATALOGUE[name])
            cached = state.classes.put(
                name,
                (
                    measured[instrument.symbol]
                    for instrument in alt_api.CATALOGUE[name]
                    if instrument.symbol in measured
                ),
            )
        rows = [row.document() for row in cached.rows][:count]
    except (ApiError, OSError, TimeoutError, RuntimeError) as exc:
        # The futures wire is not the dashboard. A reader that cannot answer
        # costs this block, and the document names it in `unavailable`.
        logger.warning("home: the %s table could not be read: %s", name, exc)
        return None
    # An empty class is a quiet wire, not a broken one: the table says "no
    # prices" in its own line instead of the page calling the block unavailable.
    return rows


#: Form 4 transaction codes, as the table reads them. A code we do not know is
#: printed as itself rather than guessed at.
_TX_LABELS = {
    "P": "Buy",
    "S": "Sale",
    "A": "Grant",
    "M": "Exercise",
    "F": "Tax",
    "G": "Gift",
    "C": "Conversion",
    "D": "Disposition",
}


def _tx_label(code: Any) -> str | None:
    text = code.strip().upper() if isinstance(code, str) else ""
    return _TX_LABELS.get(text, text or None)


async def insider_documents(
    request: Request, symbols: Sequence[str], caches: HomeCaches
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
    """Two tables from one read: the latest filings, and the largest by value.

    ``symbols`` is the turnover leaders, so "top" means "largest transaction among
    the companies this page already looked at" — the caption says so rather than
    implying a market-wide sweep we did not run.
    """

    latest_key = ("insider-latest", *symbols)
    top_key = ("insider-top", *symbols)
    cached_latest = caches.filings.get(latest_key)
    cached_top = caches.filings.get(top_key)
    if cached_latest is not None and cached_top is not None:
        return cached_latest, cached_top

    master = security_master_of(request)
    cache_dir = filing_cache_dir()
    per_symbol = await asyncio.gather(
        *(
            _insider_rows_for(request, symbol, master=master, cache_dir=cache_dir)
            for symbol in symbols
        )
    )
    rows = [row for group in per_symbol for row in group]
    if not rows:
        return None, None

    rows.sort(key=lambda row: (row.get("date") or "", row.get("symbol") or ""), reverse=True)
    latest = rows[:10]
    top = sorted(
        (row for row in rows if isinstance(row.get("value"), (int, float))),
        key=lambda row: float(row["value"]),
        reverse=True,
    )[:6]
    caches.filings.put(latest_key, latest)
    caches.filings.put(top_key, top)
    return latest, top


async def _insider_rows_for(
    request: Request, symbol: str, *, master: Any, cache_dir: Path | None
) -> list[dict[str, Any]]:
    record = master.get(symbol) if master is not None else None
    cik = getattr(record, "cik", None)
    if not isinstance(cik, str) or not cik.strip():
        return []
    items, errors = await collect_insider(symbol, cik, cache_dir=cache_dir, limit=6)
    rows: list[dict[str, Any]] = []
    for item in items:
        shares = number(getattr(item, "shares", None))
        price = number(getattr(item, "price", None))
        value = shares * price if shares is not None and price is not None else None
        rows.append(
            {
                "symbol": symbol,
                "owner": getattr(item, "owner_name", None),
                "role": getattr(item, "role", None),
                "date": getattr(item, "transaction_date", None) or getattr(item, "filed", None),
                "transaction": _tx_label(getattr(item, "code", None)),
                "cost": price,
                "shares": shares,
                "value": round(value, 2) if value is not None else None,
            }
        )
    return rows


def ipos_of(rows: Iterable[Mapping[str, Any]], *, count: int = 20) -> list[dict[str, Any]]:
    """Recent listings from the scan's own ``ipo_date``, newest first.

    A row without a date is not a new listing, and a date in the future is a
    scheduled listing — both are skipped rather than shown as today's IPO.
    """

    today = datetime.now(UTC).date()
    found: list[tuple[str, dict[str, Any]]] = []
    for row in rows:
        raw = row.get("ipo_date")
        if not isinstance(raw, str) or len(raw) < 10:
            continue
        try:
            listed = datetime.fromisoformat(raw[:10]).date()
        except ValueError:
            continue
        if listed > today:
            continue
        symbol = _symbol(row.get("symbol"))
        if symbol is None:
            continue
        found.append(
            (
                raw[:10],
                {
                    "symbol": symbol,
                    "date": raw[:10],
                    "change_pct": _plain(number(row.get("change_pct"))),
                },
            )
        )
    found.sort(key=lambda pair: (pair[0], pair[1]["symbol"]), reverse=True)
    return [row for _date, row in found[:count]]


# ---------------------------------------------------------------------------
# News: the first three items of the market merge
# ---------------------------------------------------------------------------


async def news_block(request: Request) -> list[dict[str, Any]] | None:
    """The first :data:`NEWS_ITEMS` items of the market news merge.

    The document is the one ``/market/news?limit=3`` would answer — same service,
    same twelve feeds, same :func:`app.api.market_news.merge`, same 60-second
    bucket — so the dashboard and the news page cannot disagree about which
    three stories are first. A total outage (every wire failed) is the one case
    the merge refuses to answer, and it is answered here as ``None``: the
    dashboard loses a block, not a page.
    """

    at = datetime.now(UTC)
    cache = news_api.market_news_cache_of(request)
    document = cache.get(NEWS_ITEMS, at)
    if document is None:
        try:
            document = await news_api._build(
                news_api.news_service_of(request), NEWS_ITEMS, at
            )
        except ApiError as exc:
            logger.warning("home: the market news merge answered nothing: %s", exc.message)
            return None
        cache.put(NEWS_ITEMS, at, document)

    rows: list[dict[str, Any]] = []
    for item in (document.get("items") or ())[:NEWS_ITEMS]:
        published = item.get("published_at")
        rows.append(
            {
                "headline": item.get("headline"),
                "url": item.get("url"),
                "source": item.get("source"),
                "published_at": (
                    published.isoformat() if isinstance(published, datetime) else published
                ),
            }
        )
    return rows or None


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


def _indices(
    points: Mapping[str, SeriesPoint | None], unavailable: list[str]
) -> list[dict[str, Any]] | None:
    """The five charters that answered, in the published order, or ``None``."""

    rows: list[dict[str, Any]] = []
    for symbol, label in INDEX_CHARTERS:
        point = points.get(symbol)
        if point is None or point.price is None:
            unavailable.append(f"index:{symbol}")
            continue
        rows.append(
            {
                "symbol": symbol,
                "label": label,
                "price": point.price,
                "change_pct": point.change_pct,
                "closes": list(point.closes[-CLOSES_INDEX:]),
            }
        )
    if not rows:
        unavailable.append("indices")
        return None
    return rows


def _sparks(
    candidates: Sequence[Mapping[str, Any]],
    points: Mapping[str, SeriesPoint | None],
    unavailable: list[str],
) -> list[dict[str, Any]] | None:
    """The spark strip: one row per candidate whose series answered."""

    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        symbol = _symbol(candidate.get("symbol"))
        if symbol is None:
            continue
        point = points.get(symbol)
        if point is None or point.price is None:
            unavailable.append(f"spark:{symbol}")
            continue
        rows.append(
            {
                "symbol": symbol,
                "name": candidate.get("name"),
                "price": point.price,
                "change_pct": point.change_pct,
                "volume": _plain(number(candidate.get("volume"))),
                "closes": list(point.closes[-CLOSES_SPARK:]),
            }
        )
    if not rows:
        # Two different nothings: a universe with no turnover candidate leaves
        # an honest empty strip, while candidates whose every series failed is a
        # dead source and must be ``null`` with its name.
        if not candidates:
            return []
        unavailable.append("sparks")
        return None
    return rows


def home_document(
    *,
    as_of: str,
    indices: list[dict[str, Any]] | None,
    breadth: dict[str, float] | None,
    sparks: list[dict[str, Any]] | None,
    movers: list[dict[str, Any]] | None,
    map_block: dict[str, Any] | None,
    news: list[dict[str, Any]] | None,
    filings: list[dict[str, Any]] | None,
    unavailable: Sequence[str],
    futures: list[dict[str, Any]] | None = None,
    forex: list[dict[str, Any]] | None = None,
    ipos: list[dict[str, Any]] | None = None,
    insider_latest: list[dict[str, Any]] | None = None,
    insider_top: list[dict[str, Any]] | None = None,
    economic: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The answer, from blocks already built. Pure: the same blocks print the same body."""

    return {
        "as_of": as_of,
        "indices": indices,
        "breadth": breadth,
        "sparks": sparks,
        "movers": movers,
        "map": map_block,
        "news": news,
        "filings": filings,
        "futures": futures,
        "forex": forex,
        "ipos": ipos,
        "insider_latest": insider_latest,
        "insider_top": insider_top,
        "economic": economic,
        "unavailable": list(unavailable),
        "method": dict(METHOD),
    }


def home_revision(
    *,
    region: str,
    generation: int,
    depth: str,
    news_bucket: int,
    chart_bucket: int,
    shape: str = HOME_SHAPE,
) -> str:
    """The document's identity, from its inputs — never from its body.

    ``as_of`` is deliberately absent: it dates the build, and naming it would
    make two builds of one unchanged market different entities, so no warm
    client could ever revalidate.
    """

    return (
        "v1"
        f";region={region}"
        f";scan={int(generation)}"
        f";depth={depth}"
        f";news={int(news_bucket)}"
        f";chart={int(chart_bucket)}"
        f";shape={shape}"
    )


# ---------------------------------------------------------------------------
# Reading the inputs a revalidation needs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Revisions:
    """The generation and the buckets that name one build of the document."""

    generation: int | None
    depth: str
    news_bucket: int
    chart_bucket: int

    def key(self, region: str) -> tuple[Any, ...]:
        """The memo key for this region and these inputs."""

        return (
            region,
            self.generation,
            self.depth,
            self.news_bucket,
            self.chart_bucket,
        )


def settings_of(request: Request) -> Any:
    """The settings object this process was built with, or ``None``.

    ``app.config.get_settings()`` constructs a fresh ``Settings`` on every call
    (~8 ms of environment parsing), which is fine once per build and wrong on a
    revalidation path whose whole budget is a millisecond. The application's
    runtime already holds the settings it booted with; using them here also
    guarantees that the depth identity and the depth read resolve the same file.
    """

    runtime = getattr(request.app.state, "runtime", None)
    return getattr(runtime, "settings", None)


def revisions_of(request: Request, *, region: str, family: str, caches: HomeCaches) -> Revisions:
    """The current inputs, read cheaply: one stamp lookup, one stat, two clocks.

    ``stamp_for`` is read-only by contract (no scan, no counter), which is what
    lets a ``304`` be decided before any work. A ``None`` generation means a
    request has to scan, and then there is nothing to revalidate against: the
    answer cannot be proved unchanged from the inputs alone.
    """

    at = datetime.now(UTC)
    store = resolve_store_path(settings_of(request))
    return Revisions(
        generation=stamp_for(request, region=region, family=family),
        depth=identity(str(store) if store is not None else None),
        news_bucket=news_api.market_news_cache_of(request).bucket(at),
        chart_bucket=caches.series.bucket(),
    )


def _lock_of(request: Request) -> asyncio.Lock:
    """The application's build lock, created on first use."""

    lock = getattr(request.app.state, "home_lock", None)
    if isinstance(lock, asyncio.Lock):
        return lock
    lock = asyncio.Lock()
    request.app.state.home_lock = lock
    return lock


# ---------------------------------------------------------------------------
# The build
# ---------------------------------------------------------------------------


async def _page_or_none(request: Request, *, region: str, family: str) -> Any:
    """The selector's universe, or ``None`` when the scanner could not answer."""

    try:
        return await universe_for(request, region=region, family=family)
    except ApiError as exc:
        logger.warning("home: the scanner could not answer %s: %s", region, exc.message)
        return None


async def build_document(
    request: Request, *, region: str, family: str, caches: HomeCaches
) -> tuple[dict[str, Any], int | None, str]:
    """Assemble every block, overlapping every read that can overlap.

    Three sources are independent — the scan, the five index series and the news
    merge — so they start together. The two blocks that need the universe (the
    spark series and the filings) then start **as soon as the scan answers**,
    while the news merge is still waiting on its slowest wire: a cold build costs
    one news merge, not that merge plus a scan. The CPU work that needs the
    universe (the depth overlay, the signals pass, the treemap) happens while
    those two network reads are in flight.

    Returns ``(document, generation, scan)``: the generation the memo must be
    keyed by, and the ``X-Scan-Cache`` label for the response.
    """

    as_of = moment()
    unavailable: list[str] = []

    scan = asyncio.create_task(_page_or_none(request, region=region, family=family))
    index_series = asyncio.create_task(
        series_points(request, caches, [symbol for symbol, _label in INDEX_CHARTERS])
    )
    headlines = asyncio.create_task(news_block(request))

    page = await scan
    if page is None:
        # One source feeds five blocks; naming all five is the honest report of
        # what a dead scanner cost, and each block is null rather than empty.
        unavailable.extend(["breadth", "sparks", "movers", "map", "filings"])
        news = await headlines
        if news is None:
            unavailable.append("news")
        return (
            home_document(
                as_of=as_of,
                indices=_indices(await index_series, unavailable),
                breadth=None,
                sparks=None,
                movers=None,
                map_block=None,
                news=news,
                filings=None,
                unavailable=unavailable,
                futures=await alt_rows(request, "futures", FUTURES_ROWS),
                forex=await alt_rows(request, "forex", FOREX_ROWS),
                ipos=None,
                insider_latest=None,
                insider_top=None,
                economic=None,
            ),
            None,
            "miss",
        )

    universe = _with_depth(page.rows)
    groups = collect(universe, per=MOVERS_PER_GROUP)
    candidates = spark_rows(universe)
    active = [
        str(row["symbol"])
        for row in group_rows(groups, "most_active", limit=FILINGS_SYMBOLS)
        if row.get("symbol")
    ]

    # The remaining network reads start now, while the treemap is built.
    insider_symbols = [
        str(row.get("symbol"))
        for row in candidates[:INSIDER_SYMBOLS]
        if row.get("symbol")
    ]
    insider_work = asyncio.create_task(insider_documents(request, insider_symbols, caches))
    futures_work = asyncio.create_task(alt_rows(request, "futures", FUTURES_ROWS))
    forex_work = asyncio.create_task(alt_rows(request, "forex", FOREX_ROWS))
    spark_series = asyncio.create_task(
        series_points(request, caches, [_symbol(row.get("symbol")) or "" for row in candidates])
    )
    filings_work = asyncio.create_task(filings_block(request, active, caches))

    breadth = breadth_of(
        universe,
        sma50=sma50_by_symbol(universe, settings=settings_of(request)),
        sma200=sma_by_symbol(universe, field="sma200", settings=settings_of(request)),
    )
    # An empty table is a measurement — "nothing qualified today" — not a failed
    # source, so it is published as ``[]``; only a dead scan makes movers null.
    movers = movers_of(groups)
    map_block = truncate_map(
        map_api.build_map_document(
            universe, region=region, metric=map_api.METRIC_DEFAULT, as_of=as_of
        )
    )

    sparks = _sparks(candidates, await spark_series, unavailable)
    filings = await filings_work
    insider_latest, insider_top = await insider_work
    if insider_latest is None:
        unavailable.append("insider")
    futures_rows = await futures_work
    if futures_rows is None:
        unavailable.append("futures")
    forex_rows = await forex_work
    if forex_rows is None:
        unavailable.append("forex")
    # No IPO table: `ipo_date` from the scan is not a listing feed (it returned
    # PAXG, a gold token). A block without a source is a block that lies.
    ipos = None
    if filings is None:
        unavailable.append("filings")
    news = await headlines
    if news is None:
        unavailable.append("news")

    return (
        home_document(
            as_of=as_of,
            indices=_indices(await index_series, unavailable),
            breadth=breadth,
            sparks=sparks,
            movers=movers,
            map_block=map_block,
            news=news,
            filings=filings,
            futures=futures_rows,
            forex=forex_rows,
            ipos=ipos,
            insider_latest=insider_latest,
            insider_top=insider_top,
            economic=None,
            unavailable=unavailable,
        ),
        int(page.generation),
        page.scan,
    )


async def _home_answer(request: Request, region: str, *, head: bool) -> Response:
    """The document, its ``304``, or its ``HEAD`` — cheapest path first.

    The order is the promise: validate the region, read the inputs (one
    generation lookup, one stat, two clocks), answer from the memo if this exact
    revision is already built, and only then take the build lock. Inside the
    lock the inputs are read again — the request may have waited for a build
    that moved the generation — so a pair of cold requests does one build and
    the second is answered as ``shared``.
    """

    family = _universe_family(region, None)
    caches = caches_of(request)
    memo = memo_for(request, "home", limit=MEMO_LIMIT)

    revisions = revisions_of(request, region=region, family=family, caches=caches)
    if revisions.generation is not None:
        cached = memo.get(revisions.key(region))
        if cached is not None:
            return serve(request, cached, scan="hit", head=head)

    async with _lock_of(request):
        revisions = revisions_of(request, region=region, family=family, caches=caches)
        cached = memo.get(revisions.key(region))
        if cached is not None:
            return serve(request, cached, scan="shared", head=head)

        document, generation, scan = await build_document(
            request, region=region, family=family, caches=caches
        )
        if generation is not None:
            # The generation the *page* carries is the memo key, not the one
            # read before the scan: a cold request had none, and keying it as
            # "no generation" would store a perfectly good document where no
            # later request would ever look for it.
            revisions = Revisions(
                generation=generation,
                depth=revisions.depth,
                news_bucket=revisions.news_bucket,
                chart_bucket=revisions.chart_bucket,
            )
        answer = CachedDocument(
            body=encode(document),
            etag=etag(
                "home",
                home_revision(
                    region=region,
                    generation=generation if generation is not None else -1,
                    depth=revisions.depth,
                    news_bucket=revisions.news_bucket,
                    chart_bucket=revisions.chart_bucket,
                ),
            ),
            generation=generation if generation is not None else -1,
        )
        if generation is not None:
            # A degraded document is not memoised: caching "we could not
            # measure" would keep saying it after the scanner recovered, and
            # `stamp_for` would never move because nothing ever scans again.
            memo.put(revisions.key(region), answer)
        return serve(request, answer, scan=scan, head=head)


@router.get("/market/home")
async def market_home(request: Request, region: str = "us") -> Response:
    """The dashboard's seven blocks, in one document, revalidatable (GET).

    ``as_of`` is the moment the document was **built**, not the moment it was
    read: a warm read is the bytes the build produced. ``unavailable`` names
    every block — and every symbol — whose source failed; a block that failed is
    ``null``, never an empty list. ``method`` says what each number measures.
    """

    return await _home_answer(request, region, head=False)


@router.head("/market/home", include_in_schema=False)
async def market_home_head(request: Request, region: str = "us") -> Response:
    """The same headers as GET, no body: a monitor's question, not the document.

    Registered as its own route rather than as a second method on the GET one:
    FastAPI derives an operation id per route, and two methods on one route
    produce a duplicate id warning.
    """

    return await _home_answer(request, region, head=True)


__all__ = [
    "CLOSES_INDEX",
    "CLOSES_SPARK",
    "FILINGS_SYMBOLS",
    "FILINGS_TTL_S",
    "HOME_SHAPE",
    "INDEX_CHARTERS",
    "MAP_TILES",
    "METHOD",
    "MOVERS_PER_GROUP",
    "MOVER_GROUPS",
    "NEWS_ITEMS",
    "PAYLOAD_BUDGET",
    "SPARK_COUNT",
    "FilingsCache",
    "HomeCaches",
    "Revisions",
    "SeriesCache",
    "SeriesPoint",
    "breadth_of",
    "build_document",
    "caches_of",
    "filing_cache_dir",
    "filings_block",
    "group_rows",
    "home_document",
    "home_revision",
    "market_home",
    "market_home_head",
    "movers_of",
    "news_block",
    "point_of",
    "revisions_of",
    "router",
    "security_master_of",
    "series_for",
    "series_points",
    "settings_of",
    "sma50_by_symbol",
    "sma_by_symbol",
    "spark_rows",
    "truncate_map",
]
