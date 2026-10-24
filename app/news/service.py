"""News enrichment (P12): Google News RSS + GDELT → one row per ticker.

A news panel is a *display*, so the same rule that governs ``GET /screens``
governs this module: **one consolidated row per symbol, never per-source
columns**. A caller asking "what is the news on RHI" gets eight headlines, one
``count``, one ``latest_published_at`` and one ``as_of`` — not a Google block and
a GDELT block whose shapes depend on which wire answered. Provenance survives
inside each headline (``url``, ``source``, ``source_url``), which is the level a
reader can actually audit.

What the service guarantees
---------------------------

* **Providers are injected.** :class:`NewsService` receives a sequence of
  :class:`NewsProvider`; the two shipped legs (:class:`GoogleNewsProvider`,
  :class:`GdeltNewsProvider`) are defaults built by
  :func:`build_news_providers`, never constructed inside a route. A test — or a
  deployment with its own wire — replaces the tuple and nothing else.
* **They are asked concurrently.** Every provider is one ``asyncio.gather``
  branch, so two wires cost one round trip, not two. Each branch is wrapped in
  ``wait_for``: a hanging wire cannot hold the document past ``timeout_s``.
* **A broken wire is a cell, not an exception.** A provider that raises is
  recorded in ``provider_errors`` (``{"provider", "error"}``) and the remaining
  providers still answer. Only when *every* provider failed does the service
  raise :class:`AllNewsProvidersFailed`, which the route renders as ``503`` — a
  200 with an empty list would claim "no news exists", which is exactly what an
  outage cannot say.
* **No news is an answer.** Two healthy providers that return nothing is a
  ``200`` with ``count=0``; it is cached like any other document, because
  "nothing happened" is a fact for the next fifteen minutes.
* **Dedupe by URL *and* headline.** The same story arrives twice — Google
  wraps it in a redirect URL, GDELT carries the publisher link — so one article
  must not occupy two of the eight rows. Duplicates are merged, not merely
  dropped: a missing timestamp or publisher is filled from the other wire.
* **Newest first, undated last.** ``published_at`` descending; an item with no
  timestamp (Google omits ``<pubDate>`` on some items) sorts after every dated
  one, and ``None`` is never turned into "now".
* **Eight items, fifteen minutes.** :data:`DEFAULT_ITEMS` matches the Finviz
  parity panel, and a document lives :data:`CACHE_TTL_S` seconds. A read inside
  the window sends **zero** provider requests — including a read with a
  different ``limit``, because the cache holds the merged pool and the limit is
  applied on read.

An outage is deliberately not cached: a two-second network blip must not pin a
"no news" answer for fifteen minutes.

Timestamps
----------

``as_of`` dates the document (when the pool was assembled); ``published_at`` on
each item dates the article. They are different clocks on purpose and neither
is derived from the other.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.config import Settings, get_settings
from app.market.sources import BROWSER_USER_AGENT, SYMBOL_RE
from app.news.rss import (
    DEFAULT_LIMIT as DEFAULT_ITEMS,
)
from app.news.rss import (
    GoogleNewsError,
    NewsItem,
    feed_url,
    parse_google_news,
)
from app.providers import http as provider_http

#: Fifteen minutes: a headline set is a slow-moving picture, and both wires are
#: rate-limited (GDELT answers 429 above one request per five seconds).
CACHE_TTL_S = 900
#: The merged pool one symbol may hold. ``limit`` slices it, so the second read
#: in the window — at any limit — never re-asks a provider.
MAX_POOL_ITEMS = 50
#: Per-provider HTTP read timeout; also the ceiling on one provider's branch.
DEFAULT_TIMEOUT_S = 10.0

#: GDELT DOC 2.0 API. ``artlist`` returns article metadata (title, url, domain,
#: seendate) as JSON; ``datedesc`` is the order this module wants anyway.
GDELT_HOST = "https://api.gdeltproject.org"
GDELT_PATH = "/api/v2/doc/doc"
GDELT_MAX_RECORDS = 25
GDELT_TIMESPAN = "7d"
#: GDELT prints ``20261023T120500Z`` (compact ISO-8601, seconds, UTC).
_GDELT_STAMP = "%Y%m%dT%H%M%SZ"

#: The query each wire is asked. A bare ticker is a search for a word, not for a
#: company ("RHI" is also a knitting brand), so the phrase names the asset.
_QUERY_SUFFIX = " stock"


class NewsProviderError(RuntimeError):
    """A news wire could not answer at all (HTTP failure, unusable payload)."""

    def __init__(self, detail: str, *, status: int | None = None) -> None:
        super().__init__(detail)
        self.status = status


class AllNewsProvidersFailed(RuntimeError):
    """Every configured provider failed; the document cannot be produced."""

    def __init__(self, symbol: str, failures: Sequence[str]) -> None:
        detail = "; ".join(failures) if failures else "no provider answered"
        super().__init__(f"every news provider failed for {symbol}: {detail}")
        self.symbol = symbol
        self.failures = tuple(failures)


class InvalidNewsSymbol(ValueError):
    """The path token is not a ticker this service will search for."""

    def __init__(self, token: str) -> None:
        super().__init__(f"invalid symbol {token!r}")
        self.token = token


def normalize_symbol(token: str) -> str:
    """``" rhi "`` → ``"RHI"``; anything outside :data:`SYMBOL_RE` is a 422.

    The same alphabet the quote engine accepts: one definition of "a symbol" in
    the tree, so ``/news/^GSPC`` is answerable and ``/news/../../etc`` is not.
    """

    symbol = str(token or "").strip().upper()
    if not symbol or not SYMBOL_RE.match(symbol):
        raise InvalidNewsSymbol(token)
    return symbol


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


class NewsProvider(Protocol):
    """One wire asked for one ticker's headlines.

    ``fetch`` returns the items it could read — an empty sequence is "this wire
    has no news for the symbol", which is an answer. Raising means the wire
    could not be read, and the service records it in ``provider_errors``.
    """

    name: str

    async def fetch(self, symbol: str) -> Sequence[NewsItem]: ...


class CallableNewsProvider:
    """Adapt a plain (async) function into a :class:`NewsProvider`.

    The injection point for a wire this module does not ship (a paid NewsAPI
    key, a test double). The name is validated like a built-in's so two
    providers can never share one cell of ``provider_errors``.
    """

    def __init__(
        self,
        name: str,
        fetch: Callable[[str], Sequence[NewsItem] | Awaitable[Sequence[NewsItem]]],
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("a news provider needs a non-empty name")
        if not callable(fetch):
            raise TypeError("fetch must be callable")
        self.name = name.strip()
        self._fetch = fetch

    async def fetch(self, symbol: str) -> Sequence[NewsItem]:
        answer = self._fetch(symbol)
        if isinstance(answer, Awaitable):
            answer = await answer
        return answer


def _query(symbol: str) -> str:
    return f"{symbol}{_QUERY_SUFFIX}"


_SAFE_QUERY = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~:")


def _quote(text: str) -> str:
    """RFC 3986 percent-encoding (``urllib`` is a banned transport here).

    The same alphabet :func:`app.news.rss.feed_url` uses, kept local so the two
    URLs are built without importing each other's private helpers.
    """

    encoded: list[str] = []
    for char in text:
        if char in _SAFE_QUERY:
            encoded.append(char)
        else:
            encoded.append("".join(f"%{byte:02X}" for byte in char.encode("utf-8")))
    return "".join(encoded)


def gdelt_url(
    query: str,
    *,
    max_records: int = GDELT_MAX_RECORDS,
    timespan: str = GDELT_TIMESPAN,
) -> str:
    """The GDELT DOC API URL for a phrase — a string, not a request."""

    if not query.strip():
        raise ValueError("query may not be blank")
    if max_records < 1:
        raise ValueError(f"max_records must be >= 1, got {max_records!r}")
    return (
        f"{GDELT_HOST}{GDELT_PATH}?query={_quote(query.strip())}"
        f"&mode=artlist&format=json&maxrecords={int(max_records)}"
        f"&timespan={_quote(timespan)}&sort=datedesc"
    )


def _looks_like_a_feed(document: str | bytes) -> bool:
    """Does the body claim to be RSS/Atom at all?

    Google answers a bot wall or a maintenance page with ``200`` and HTML. The
    XML parser happily reads that document and finds no ``<item>``, which would
    look exactly like "this ticker has no news" — the one thing a challenge page
    cannot say. So the root kind is checked before the parser is trusted.
    """

    head = (
        document.decode("utf-8", "replace")
        if isinstance(document, bytes)
        else str(document)
    )[:2000].lower()
    return any(marker in head for marker in ("<rss", "<feed", "<rdf:rss", "<channel"))


class GoogleNewsProvider:
    """Google News RSS search: headlines with ``<pubDate>`` and ``<source>``.

    The parser is pure and lives in :mod:`app.news.rss`; this class owns the one
    ``GET``. ``fetcher`` replaces the HTTP hop (a test, or a deployment with its
    own transport) without rebuilding the class.
    """

    name = "google_news"

    def __init__(
        self,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        user_agent: str = BROWSER_USER_AGENT,
        fetcher: Callable[[str], Awaitable[str | bytes]] | None = None,
        query: Callable[[str], str] | None = None,
    ) -> None:
        if not timeout_s > 0:
            raise ValueError(f"timeout_s must be > 0, got {timeout_s!r}")
        if fetcher is not None and not callable(fetcher):
            raise TypeError("fetcher must be callable")
        if query is not None and not callable(query):
            raise TypeError("query must be callable")
        self.timeout_s = float(timeout_s)
        self.user_agent = str(user_agent or BROWSER_USER_AGENT)
        self._fetcher = fetcher
        self._query = query or _query

    def url(self, symbol: str) -> str:
        """The RSS URL for one ticker (the phrase is ``"<SYMBOL> stock"``)."""

        return feed_url(self._query(symbol))

    def headers(self) -> dict[str, str]:
        return {
            "user-agent": self.user_agent,
            "accept": "application/rss+xml,application/xml;q=0.9,text/xml;q=0.8,*/*;q=0.5",
            "accept-language": "en-US,en;q=0.9",
        }

    async def document(self, symbol: str) -> str | bytes:
        """The raw feed body; a non-200 is a failure, never an empty feed."""

        if self._fetcher is not None:
            return await self._fetcher(symbol)
        async with provider_http.build_client(
            timeout_s=self.timeout_s, headers=self.headers()
        ) as client:
            response = await client.get(self.url(symbol))
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise NewsProviderError(f"{self.name} answered {status}", status=status)
        return getattr(response, "text", "") or ""

    async def fetch(self, symbol: str) -> Sequence[NewsItem]:
        body = await self.document(symbol)
        if not _looks_like_a_feed(body):
            raise NewsProviderError(
                f"{self.name} answered a document that is not a news feed"
            )
        try:
            return tuple(parse_google_news(body, limit=None))
        except GoogleNewsError as exc:
            raise NewsProviderError(f"{self.name} returned an unreadable feed: {exc}") from exc


def _gdelt_stamp(raw: str) -> datetime | None:
    """``"20261023T120500Z"`` (or ISO-8601) → aware UTC, else ``None``."""

    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, _GDELT_STAMP).replace(tzinfo=UTC)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


class GdeltError(ValueError):
    """The GDELT payload is not the JSON envelope this module can read."""


def parse_gdelt(document: str | bytes) -> list[NewsItem]:
    """One GDELT ``artlist`` payload → items (pure, no request).

    Only the cells GDELT really publishes are read: ``title`` → headline,
    ``url`` → url, ``domain`` → source, ``seendate`` → ``published_at``. There
    is no publisher *home* URL in the payload, so ``source_url`` stays ``None``
    — building ``https://{domain}`` would be inventing a link the wire never
    sent. An article without a title or a URL is dropped for the same reason the
    RSS parser drops one: a headline nobody can open is not a row.
    """

    text = document.decode("utf-8", "replace") if isinstance(document, bytes) else str(document)
    payload_text = text.strip()
    if not payload_text:
        raise GdeltError("empty response")
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        # GDELT answers a plain-text rate-limit notice instead of JSON when the
        # five-second rule is broken; that is a failure, not "no news".
        head = " ".join(payload_text.split())[:160]
        raise GdeltError(f"not a JSON payload: {head or exc}") from exc
    if not isinstance(payload, Mapping):
        raise GdeltError(f"expected a JSON object, got {type(payload).__name__}")
    articles = payload.get("articles")
    if articles is None:
        return []
    if not isinstance(articles, list):
        raise GdeltError(f"'articles' must be a list, got {type(articles).__name__}")

    items: list[NewsItem] = []
    seen: set[str] = set()
    for article in articles:
        if not isinstance(article, Mapping):
            continue
        headline = " ".join(str(article.get("title") or "").split())
        url = str(article.get("url") or "").strip()
        if not headline or not url or url in seen:
            continue
        seen.add(url)
        items.append(
            NewsItem(
                headline=headline,
                url=url,
                source=str(article.get("domain") or "").strip(),
                published_at=_gdelt_stamp(str(article.get("seendate") or "")),
                source_url=None,
            )
        )
    return items


class GdeltNewsProvider:
    """GDELT DOC 2.0 ``artlist``: article metadata as JSON, no key required.

    GDELT allows one request per five seconds. The service's fifteen-minute
    cache is what keeps the fleet inside that budget; this class does not add a
    retry, because a retried 429 is a second violation of the same rule.
    """

    name = "gdelt"

    def __init__(
        self,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        user_agent: str = BROWSER_USER_AGENT,
        fetcher: Callable[[str], Awaitable[str | bytes]] | None = None,
        query: Callable[[str], str] | None = None,
        max_records: int = GDELT_MAX_RECORDS,
        timespan: str = GDELT_TIMESPAN,
    ) -> None:
        if not timeout_s > 0:
            raise ValueError(f"timeout_s must be > 0, got {timeout_s!r}")
        if fetcher is not None and not callable(fetcher):
            raise TypeError("fetcher must be callable")
        if query is not None and not callable(query):
            raise TypeError("query must be callable")
        self.timeout_s = float(timeout_s)
        self.user_agent = str(user_agent or BROWSER_USER_AGENT)
        self.max_records = int(max_records)
        self.timespan = str(timespan)
        self._fetcher = fetcher
        self._query = query or _query

    def url(self, symbol: str) -> str:
        return gdelt_url(
            self._query(symbol), max_records=self.max_records, timespan=self.timespan
        )

    def headers(self) -> dict[str, str]:
        return {
            "user-agent": self.user_agent,
            "accept": "application/json,text/plain;q=0.8,*/*;q=0.5",
        }

    async def document(self, symbol: str) -> str | bytes:
        if self._fetcher is not None:
            return await self._fetcher(symbol)
        async with provider_http.build_client(
            timeout_s=self.timeout_s, headers=self.headers()
        ) as client:
            response = await client.get(self.url(symbol))
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise NewsProviderError(f"{self.name} answered {status}", status=status)
        return getattr(response, "text", "") or ""

    async def fetch(self, symbol: str) -> Sequence[NewsItem]:
        body = await self.document(symbol)
        try:
            return tuple(parse_gdelt(body))
        except GdeltError as exc:
            raise NewsProviderError(f"{self.name} returned an unreadable payload: {exc}") from exc


def build_news_providers(settings: Settings | None = None) -> tuple[NewsProvider, ...]:
    """The default wires: Google News RSS, plus GDELT unless it is turned off.

    ``MEE_NEWS_GDELT_ENABLED=false`` keeps the RSS leg only — for a deployment
    behind an egress proxy that GDELT rate-limits, or inside a five-second
    budget it cannot honour. The ordering is stable (RSS first) because it is
    also the tie-break order for two equally dated copies of one story.
    """

    settings = settings or get_settings()
    timeout_s = settings.news_timeout_s
    providers: list[NewsProvider] = [GoogleNewsProvider(timeout_s=timeout_s)]
    if settings.news_gdelt_enabled:
        providers.append(GdeltNewsProvider(timeout_s=timeout_s))
    return tuple(providers)


# ---------------------------------------------------------------------------
# Merge: dedupe by url and by headline
# ---------------------------------------------------------------------------


def _url_key(url: str) -> str:
    """A URL without its fragment: ``#section`` never names a different article."""

    return url.strip().split("#", 1)[0]


def _title_keys(headline: str) -> set[str]:
    """Dedupe keys for one headline — the full text and its ``" - Publisher"`` stem.

    Google strips the publisher suffix it appends (only when it matches
    ``<source>``); GDELT carries the title as the publisher wrote it, suffix and
    all. Registering the stem as well is what merges "Chip rally continues" with
    "Chip rally continues - CNBC" — the same story seen by two wires — without
    merging two different headlines that merely share a word.
    """

    def normalize(text: str) -> str:
        cleaned = "".join(
            char if char.isalnum() or char.isspace() else " " for char in text.casefold()
        )
        return " ".join(cleaned.split())

    keys = {normalize(headline)}
    stem, separator, _suffix = headline.rpartition(" - ")
    if separator and len(stem.strip()) >= 15:
        keys.add(normalize(stem))
    keys.discard("")
    return keys


def _merge_group(group: Sequence[NewsItem]) -> NewsItem:
    """One story from N copies: newest timestamp, first non-empty provenance.

    The headline and URL are the first copy's (the provider order is stable, so
    the rendering is reproducible); the timestamp is the **newest** claim, and
    an empty ``source``/``source_url`` is filled from a copy that had one.
    """

    keep = group[0]
    stamps = [item.published_at for item in group if item.published_at is not None]
    source = next((item.source for item in group if item.source), "")
    source_url = next((item.source_url for item in group if item.source_url), None)
    return replace(
        keep,
        source=source,
        source_url=source_url,
        published_at=max(stamps) if stamps else None,
    )


def merge_items(items: Sequence[NewsItem]) -> list[NewsItem]:
    """Collapse duplicate stories, preserving first-appearance order.

    A story can be reached by two keys at once (Google's redirect URL and its
    title, GDELT's publisher URL and the same title), so the groups are joined
    with a union-find: two keys that point at different groups mean the groups
    were always the same story.
    """

    parent: list[int] = []
    members: list[list[NewsItem]] = []
    first: dict[str, int] = {}

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for item in items:
        keys = _title_keys(item.headline) | {_url_key(item.url)}
        found = sorted({find(first[key]) for key in keys if key in first})
        if not found:
            group = len(members)
            parent.append(group)
            members.append([item])
        else:
            group = found[0]
            for other in found[1:]:
                parent[other] = group
                members[group].extend(members[other])
                members[other] = []
            members[group].append(item)
        for key in keys:
            first.setdefault(key, group)

    merged: list[NewsItem] = []
    for index, group in enumerate(members):
        if not group or find(index) != index:
            continue
        merged.append(_merge_group(group))
    return merged


def _sort_key(item: NewsItem) -> tuple[bool, float, str, str]:
    """Newest first; undated last; a stable tie-break so two runs agree."""

    stamp = item.published_at.timestamp() if item.published_at is not None else 0.0
    return (item.published_at is None, -stamp, item.headline.casefold(), item.url)


def order_items(items: Sequence[NewsItem]) -> list[NewsItem]:
    """``published_at`` descending, undated items last, deterministic ties."""

    return sorted(items, key=_sort_key)


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Cached:
    """One assembled pool, with the timestamp and errors that produced it."""

    symbol: str
    as_of: str
    expires_at: datetime
    items: tuple[NewsItem, ...]
    errors: tuple[dict[str, str], ...]

    def document(self, count: int) -> dict[str, Any]:
        """The response body: one consolidated row per symbol, sliced to ``count``."""

        items = self.items[:count]
        latest = next(
            (item.published_at for item in items if item.published_at is not None), None
        )
        return {
            "symbol": self.symbol,
            "as_of": self.as_of,
            "count": len(items),
            "latest_published_at": latest.isoformat() if latest is not None else None,
            "items": [item.as_dict() for item in items],
            "provider_errors": [dict(error) for error in self.errors],
        }


def _iso(moment: datetime) -> str:
    """Aware UTC with microseconds, so lexical order is chronological order."""

    if moment.tzinfo is None:
        raise ValueError("naive datetimes are not allowed in a news document")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _short(text: str, limit: int = 300) -> str:
    """One provider's sentence, trimmed: an error cell is a diagnostic, not a dump."""

    collapsed = " ".join(str(text).split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def _as_item(candidate: object) -> NewsItem | None:
    """Validate one provider answer into a usable item (``None`` when unusable).

    A provider may hand back the parser's frozen dataclass; anything else is a
    programming error and raises. Naive timestamps are anchored to UTC for the
    same reason the RSS parser anchors them: two wires must be comparable.
    """

    if not isinstance(candidate, NewsItem):
        raise TypeError(
            f"expected NewsItem, got {type(candidate).__name__}"
        )
    headline = " ".join(candidate.headline.split())
    url = candidate.url.strip()
    if not headline or not url:
        return None
    published = candidate.published_at
    if published is not None:
        published = (
            published.replace(tzinfo=UTC)
            if published.tzinfo is None
            else published.astimezone(UTC)
        )
    return replace(
        candidate,
        headline=headline,
        url=url,
        source=" ".join(candidate.source.split()),
        published_at=published,
    )


class NewsService:
    """Serve one consolidated news document per symbol, cached for fifteen minutes."""

    def __init__(
        self,
        providers: Sequence[NewsProvider] = (),
        *,
        cache_ttl_s: int = CACHE_TTL_S,
        limit: int = DEFAULT_ITEMS,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_items: int = MAX_POOL_ITEMS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if isinstance(cache_ttl_s, bool) or not isinstance(cache_ttl_s, int):
            raise TypeError(f"cache_ttl_s must be an int, got {cache_ttl_s!r}")
        if cache_ttl_s <= 0:
            raise ValueError(f"cache_ttl_s must be > 0, got {cache_ttl_s!r}")
        if isinstance(max_items, bool) or not isinstance(max_items, int) or max_items < 1:
            raise ValueError(f"max_items must be an int >= 1, got {max_items!r}")
        if not 1 <= int(limit) <= max_items:
            raise ValueError(f"limit must be 1..{max_items}, got {limit!r}")
        if not timeout_s > 0:
            raise ValueError(f"timeout_s must be > 0, got {timeout_s!r}")
        names: list[str] = []
        for provider in providers:
            name = str(getattr(provider, "name", "") or "").strip()
            if not name:
                raise ValueError("every news provider needs a non-empty name")
            if name in names:
                raise ValueError(f"duplicate news provider name {name!r}")
            names.append(name)
        self._providers: tuple[NewsProvider, ...] = tuple(providers)
        self._ttl_s = int(cache_ttl_s)
        self._default_limit = int(limit)
        self._timeout_s = float(timeout_s)
        self._max_items = int(max_items)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cache: dict[str, _Cached] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def provider_names(self) -> tuple[str, ...]:
        return tuple(str(provider.name) for provider in self._providers)

    @property
    def default_limit(self) -> int:
        return self._default_limit

    @property
    def cache_ttl_s(self) -> int:
        return self._ttl_s

    # -- reads -------------------------------------------------------------

    async def news(
        self,
        symbol: str,
        *,
        limit: int | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """One consolidated document for ``symbol``; cache-first.

        Raises :class:`InvalidNewsSymbol` for a token that is not a ticker and
        :class:`AllNewsProvidersFailed` when every wire failed. An empty list is
        a document, not an error.
        """

        key = normalize_symbol(symbol)
        count = self._count(limit)
        moment = now or self._clock()

        cached = self._fresh(key, moment)
        if cached is None:
            # One build per symbol: concurrent readers of an expired window wait
            # for the first pass instead of stampeding both wires.
            lock = self._locks.setdefault(key, asyncio.Lock())
            async with lock:
                cached = self._fresh(key, moment)
                if cached is None:
                    cached = await self._build(key, moment)
                    self._cache[key] = cached
        return cached.document(count)

    def _count(self, limit: int | None) -> int:
        if limit is None:
            return self._default_limit
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError(f"limit must be an int, got {limit!r}")
        if not 1 <= limit:
            raise ValueError(f"limit must be >= 1, got {limit!r}")
        return min(limit, self._max_items)

    # -- cache -------------------------------------------------------------

    def _fresh(self, key: str, moment: datetime) -> _Cached | None:
        for cached_key, entry in list(self._cache.items()):
            if entry.expires_at <= moment:
                self._cache.pop(cached_key, None)
        # A lock is only useful while its key is cached or a build is running;
        # pruning the empty ones keeps a long-lived process from accumulating one
        # lock per ticker ever read.
        for cached_key, lock in list(self._locks.items()):
            if cached_key not in self._cache and not lock.locked():
                self._locks.pop(cached_key, None)
        entry = self._cache.get(key)
        if entry is None or entry.expires_at <= moment:
            return None
        return entry

    # -- building ----------------------------------------------------------

    async def _build(self, symbol: str, moment: datetime) -> _Cached:
        if not self._providers:
            raise AllNewsProvidersFailed(
                symbol, ["no news provider is configured (MEE_NEWS_GDELT_ENABLED?)"]
            )

        outcomes = await asyncio.gather(
            *(self._ask(provider, symbol) for provider in self._providers),
            return_exceptions=True,
        )

        items: list[NewsItem] = []
        errors: list[dict[str, str]] = []
        answered = 0
        for provider, outcome in zip(self._providers, outcomes, strict=True):
            name = str(provider.name)
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            if isinstance(outcome, BaseException):
                detail = _short(str(outcome)) or "no detail"
                errors.append(
                    {"provider": name, "error": f"{type(outcome).__name__}: {detail}"}
                )
                continue
            answered += 1
            items.extend(outcome)

        if answered == 0:
            raise AllNewsProvidersFailed(
                symbol,
                [f"{error['provider']}: {error['error']}" for error in errors],
            )

        merged = merge_items(items)
        ordered = order_items(merged)[: self._max_items]
        return _Cached(
            symbol=symbol,
            as_of=_iso(moment),
            expires_at=moment + timedelta(seconds=self._ttl_s),
            items=tuple(ordered),
            errors=tuple(errors),
        )

    async def _ask(self, provider: NewsProvider, symbol: str) -> list[NewsItem]:
        """One provider's branch: timed out, validated, never mutating the wire."""

        answer = await asyncio.wait_for(provider.fetch(symbol), timeout=self._timeout_s)
        if isinstance(answer, (str, bytes)) or not isinstance(answer, Sequence):
            raise TypeError(
                f"news provider {provider.name!r} must return a sequence of NewsItem, "
                f"got {type(answer).__name__}"
            )
        cleaned: list[NewsItem] = []
        for candidate in answer:
            item = _as_item(candidate)
            if item is not None:
                cleaned.append(item)
        return cleaned


def build_news_service(settings: Settings | None = None) -> NewsService:
    """The process default: both wires, the settings' TTL, limit and timeout."""

    settings = settings or get_settings()
    return NewsService(
        providers=build_news_providers(settings),
        cache_ttl_s=settings.news_cache_ttl_s,
        limit=settings.news_limit,
        timeout_s=settings.news_timeout_s,
    )


__all__ = [
    "CACHE_TTL_S",
    "DEFAULT_ITEMS",
    "DEFAULT_TIMEOUT_S",
    "GDELT_HOST",
    "GDELT_MAX_RECORDS",
    "GDELT_PATH",
    "MAX_POOL_ITEMS",
    "AllNewsProvidersFailed",
    "CallableNewsProvider",
    "GdeltError",
    "GdeltNewsProvider",
    "GoogleNewsProvider",
    "InvalidNewsSymbol",
    "NewsProvider",
    "NewsProviderError",
    "NewsService",
    "build_news_providers",
    "build_news_service",
    "gdelt_url",
    "merge_items",
    "normalize_symbol",
    "order_items",
    "parse_gdelt",
]
