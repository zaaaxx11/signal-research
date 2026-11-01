"""``GET /market/news``: the market's headlines, merged from a dozen wires.

``GET /news/{symbol}`` answers "what is being written about this company". This
route answers a different question — "what is being written about the market" —
and it answers it from the same service, one symbol at a time, because that is
the only shape a news feed has: a query, not a river.

Why these symbols
-----------------
The list is fixed and small on purpose: four index funds (SPY, QQQ, DIA, IWM)
and eight of the largest companies. A fixed list makes the page stable — the
same twelve feeds every time — and it keeps the cost bounded: twelve service
reads, each of which is cached for fifteen minutes by the service itself, so a
warm market read is a merge over cached pools and touches no network.

Merging is the only judgement here
----------------------------------
The same story arrives on several feeds under the same URL. It is published
once, with every symbol that carried it, because a reader does not need the
same headline four times to learn it is about Apple and the S&P.

* Dedupe key is the URL, normalised by dropping its fragment. Two *different*
  URLs are two rows even when the words match: two outlets writing the same
  sentence are two pieces of evidence, not one.
* A URL that is not absolute http(s) is dropped rather than published. The feed
  is a stranger's text; a `javascript:` href in a list of links is a weapon.
* Order is publication time descending, then headline, then URL — a total order,
  so two reads of one payload cannot disagree. A story without a timestamp sorts
  last: "unknown" is not "newest", and the page must not date it today.
* Nothing is invented: an item with no headline or no usable URL is not a row.

A symbol whose every provider failed does not sink the document — the other
eleven are still news. The failures are named in ``provider_errors`` so the page
can say "some feeds did not answer" instead of pretending the list is complete.
Only when **every** symbol fails is the answer 503: an empty 200 would claim the
market said nothing when the truth is that nobody could be asked.

Caching mirrors the service's own: the merged document is memoised per (limit,
60-second bucket), and the ETag is derived from that revision, so a second read
inside the window is a 304 decided before any work.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping, Sequence

from fastapi import APIRouter, Query, Request

from app.auth import ApiError
from app.news.service import AllNewsProvidersFailed, InvalidNewsSymbol

router = APIRouter(tags=["news"])

#: The feeds the page is built from: the four broad index funds and eight of the
#: largest companies. Fixed, not derived from a scan: the page should read the
#: same on a quiet Saturday as on a busy Tuesday.
MARKET_SYMBOLS: tuple[str, ...] = (
    "SPY",
    "QQQ",
    "DIA",
    "IWM",
    "AAPL",
    "NVDA",
    "MSFT",
    "AMZN",
    "META",
    "GOOGL",
    "TSLA",
    "JPM",
)

#: Headlines requested per symbol. Twelve feeds times twenty is far more than a
#: page shows; the merge truncates. Small enough to keep a cold build quick.
PER_SYMBOL = 20

#: How many rows the document may carry.
MAX_ITEMS = 200

#: The merged document is memoised for this long. The service already caches
#: each symbol for fifteen minutes; this bucket only avoids re-merging on every
#: keystroke of a page that reloads.
CACHE_TTL_S = 60

#: A row: one story, with every symbol whose feed carried it.
Row = dict[str, Any]


def _http_url(raw: object) -> str | None:
    """The URL, if it is an absolute http(s) address; otherwise ``None``.

    Deliberately textual: ``app/api`` may not import a URL/transport library
    (the tree's import rule), and the check needed here is narrow — a scheme, a
    host, and no control characters. A scheme other than http(s) is refused
    outright, because this document is rendered as anchors.
    """

    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or any(char.isspace() for char in text):
        return None
    lowered = text.lower()
    for scheme in ("https://", "http://"):
        if lowered.startswith(scheme):
            rest = text[len(scheme) :]
            return text if rest and rest[0] not in "/?#" else None
    return None


def _dedupe_key(url: str) -> str:
    """The URL without its fragment: one story, one row."""

    return url.split("#", 1)[0]


def news_service_of(request: Request) -> Any:
    """The app's news service.

    Mirrors ``app/api/news.py``'s accessor rather than importing it: a route
    module may not import another route module (the tree's import rule), and the
    service itself is attached by ``create_app`` to ``app.state.news``.
    """

    service = getattr(request.app.state, "news", None)
    if service is None:
        from app.news.service import build_news_service

        service = build_news_service()
        request.app.state.news = service
    return service


def _moment(value: object) -> datetime | None:
    """ISO-8601 → an aware datetime, or ``None``.

    A naive timestamp is read as UTC: every wire we consume publishes UTC, and a
    local-time guess would silently move a story across midnight.
    """

    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _items(document: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    raw = document.get("items")
    return raw if isinstance(raw, list) else ()


def merge(documents: Iterable[tuple[str, Mapping[str, Any]]]) -> list[Row]:
    """One story per URL, with the symbols that carried it, newest first."""

    merged: dict[str, Row] = {}
    for symbol, document in documents:
        for item in _items(document):
            headline = item.get("headline")
            url = _http_url(item.get("url"))
            if not isinstance(headline, str) or not headline.strip() or url is None:
                continue
            key = _dedupe_key(url)
            at = _moment(item.get("published_at"))
            existing = merged.get(key)
            if existing is None:
                merged[key] = {
                    "headline": headline.strip(),
                    "url": url,
                    "source": item.get("source") if isinstance(item.get("source"), str) else None,
                    "published_at": at,
                    "symbols": [symbol],
                }
                continue
            # The same story on a second feed: keep the earliest timestamp we
            # were told (that is when it was first published) and add the symbol.
            if at is not None and (existing["published_at"] is None or at < existing["published_at"]):
                existing["published_at"] = at
            if symbol not in existing["symbols"]:
                existing["symbols"].append(symbol)

    rows = list(merged.values())
    rows.sort(
        key=lambda row: (
            row["published_at"] is None,
            -(row["published_at"].timestamp()) if row["published_at"] else 0.0,
            row["headline"],
            row["url"],
        )
    )
    for row in rows:
        row["symbols"].sort()
        moment: datetime | None = row["published_at"]
        row["published_at"] = moment.isoformat() if moment is not None else None
    return rows


def _document(
    rows: Sequence[Row], *, limit: int, failures: Sequence[str], moment: datetime
) -> dict[str, Any]:
    selected = list(rows[:limit])
    return {
        "as_of": moment.isoformat(),
        "symbols": list(MARKET_SYMBOLS),
        "count": len(selected),
        "items": selected,
        "provider_errors": list(failures),
    }


class _MarketNewsCache:
    """One merged document per (limit, bucket); a bucket is 60 seconds wide."""

    def __init__(self, ttl_s: int = CACHE_TTL_S) -> None:
        self._ttl_s = int(ttl_s)
        self._entries: dict[tuple[int, int], dict[str, Any]] = {}

    def bucket(self, moment: datetime) -> int:
        return int(moment.timestamp()) // self._ttl_s

    def get(self, limit: int, moment: datetime) -> dict[str, Any] | None:
        return self._entries.get((limit, self.bucket(moment)))

    def put(self, limit: int, moment: datetime, document: dict[str, Any]) -> None:
        bucket = self.bucket(moment)
        # Keep the map bounded: a long-running process only needs the live
        # buckets, and an old one can never be asked for again.
        self._entries = {key: value for key, value in self._entries.items() if key[1] >= bucket}
        self._entries[(limit, bucket)] = document


def market_news_cache_of(request: Request) -> _MarketNewsCache:
    cache = getattr(request.app.state, "market_news_cache", None)
    if cache is None:
        cache = _MarketNewsCache()
        request.app.state.market_news_cache = cache
    return cache


async def _read(feed: Any, symbol: str) -> tuple[str, dict[str, Any] | None]:
    """One symbol's document, or ``None`` when its providers all failed."""

    try:
        document = await feed.news(symbol, limit=PER_SYMBOL)
    except (AllNewsProvidersFailed, InvalidNewsSymbol):
        # A feed that could not answer is data about coverage, not a crash: the
        # other symbols are still news, and this one is named in the document.
        return symbol, None
    return symbol, document if isinstance(document, dict) else None


async def _build(feed: Any, limit: int, moment: datetime) -> dict[str, Any]:
    results = await asyncio.gather(*(_read(feed, symbol) for symbol in MARKET_SYMBOLS))
    documents: list[tuple[str, dict[str, Any]]] = []
    failures: list[str] = []
    for symbol, document in results:
        if document is None:
            failures.append(symbol)
        else:
            documents.append((symbol, document))

    if not documents:
        raise ApiError(
            status_code=503,
            error="news_unavailable",
            message="every news feed failed; the market did not say nothing, nobody could be asked",
        )

    return _document(merge(documents), limit=limit, failures=failures, moment=moment)


def _revision(limit: int, cache: _MarketNewsCache, moment: datetime) -> str:
    return f"news-{limit}-{cache.bucket(moment)}"


async def _answer(request: Request, limit: int, *, body: bool) -> Any:
    from fastapi.responses import JSONResponse, Response

    moment = datetime.now(UTC)
    cache = market_news_cache_of(request)
    tag = f'W/"{_revision(limit, cache, moment)}"'
    headers = {"ETag": tag, "Cache-Control": "no-cache"}

    if request.headers.get("if-none-match") == tag:
        return Response(status_code=304, headers=headers)

    document = cache.get(limit, moment)
    if document is None:
        document = await _build(news_service_of(request), limit, moment)
        cache.put(limit, moment, document)

    if not body:
        import json

        payload = json.dumps(document, separators=(",", ":")).encode()
        headers["Content-Length"] = str(len(payload))
        return Response(status_code=200, headers=headers, media_type="application/json")

    return JSONResponse(document, headers=headers)


@router.get("/market/news")
async def market_news(
    request: Request,
    limit: int = Query(
        default=60,
        ge=1,
        le=MAX_ITEMS,
        description=f"How many headlines to return (1..{MAX_ITEMS}).",
    ),
) -> Any:
    """The market's headlines: newest first, one row per story."""

    return await _answer(request, limit, body=True)


@router.head("/market/news", include_in_schema=False)
async def market_news_head(
    request: Request,
    limit: int = Query(default=60, ge=1, le=MAX_ITEMS),
) -> Any:
    """The same headers and the same length as GET, without the body."""

    return await _answer(request, limit, body=False)


__all__ = [
    "MARKET_SYMBOLS",
    "MAX_ITEMS",
    "PER_SYMBOL",
    "_MarketNewsCache",
    "market_news",
    "market_news_cache_of",
    "market_news_head",
    "merge",
    "router",
]
