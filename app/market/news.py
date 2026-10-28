"""Yahoo + Nasdaq RSS → the three news cells a depth row publishes.

``finviz-off-derived.md`` §9 measured the two feeds on ten symbols: 20 requests,
313 items, and **zero** items missing a title, link or ``pubDate``. Two measured
facts shape this module:

* **A link is not an identity.** Yahoo appends ``?.tsrc=rss`` to every article,
  and Nasdaq syndicates all of its items under ``www.nasdaq.com/articles/...``,
  so the same headline arrives through both feeds with two different URLs.
  Deduplication therefore keys on the *normalised title* (casefolded,
  non-alphanumerics removed, a trailing ``" - Publisher"`` dropped) and keeps
  the newest copy, exactly the rule the research found sufficient (7.7% overlap
  at title Jaccard ≥ 0.7).
* **A date is not optional.** ``news_time`` is one of the three cells, and
  ``<pubDate>`` is the only honest source for it. An item without a parseable
  RFC-822 date is dropped; it is never stamped with the fetch time.

Parsing is pure — the document is an argument, as in :mod:`app.news.rss` — so
the offline suite (which blocks sockets) can exercise the real feed shapes.
Fetching is a seam: a ``fetcher`` callable or an injected async client replaces
the HTTP hop entirely.

The source implements the depth protocol (:class:`DepthSourceProtocol`):
``name`` plus ``async fetch_many(symbols) -> {symbol: DepthObservation}``. It
emits only ``news_title`` / ``news_url`` / ``news_time``, taken from the newest
item after dedupe (``DEFAULT_LIMIT`` items are considered, one is published). A
multi-item panel belongs to ``GET /news/{symbol}``; duplicating a whole list
into a single-cell depth store would only bloat it.
"""

from __future__ import annotations

import asyncio
import html
import inspect
import logging
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree as ET

from app.market.depth import DepthObservation, canonical_symbol

logger = logging.getLogger("mee.news")

#: Research §9 saw 12–19 Yahoo items and a fixed 15 Nasdaq items per symbol.
#: Ten newest is enough for one depth cell and keeps the store from growing a
#: headline archive it was never meant to be.
DEFAULT_LIMIT = 10

#: Both feeds answered in ≤3.2 s in the probe; 10 s is the provider default.
DEFAULT_TIMEOUT_S = 10.0

#: Both hosts are plain RSS; one request per feed per symbol.
DEFAULT_CONCURRENCY = 4

YAHOO_FEED_URL = (
    "https://feeds.finance.yahoo.com/rss/2.0/headline"
    "?s={symbol}&region=US&lang=en-US"
)
NASDAQ_FEED_URL = "https://www.nasdaq.com/feed/rssoutbound?symbol={symbol}"

#: Feed order is the tie-breaker when both feeds carry the same headline at the
#: same timestamp: Yahoo is listed first and wins, deterministically.
FEED_NAMES: tuple[str, ...] = ("yahoo", "nasdaq")

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

#: ``?.tsrc=rss`` is Yahoo's tracking query; the key arrives as ``".tsrc"``.
#: Anything else in the query string is preserved — it may be a real permalink
#: parameter on another host.
_TRACKING_QUERY_KEYS = frozenset({".tsrc", "tsrc"})

#: Absolute http(s) URL with a non-empty host. A relative or ``javascript:``
#: link is not a headline a reader can open, so it is not a news row.
_ABSOLUTE_URL = re.compile(
    r"^(?P<scheme>https?)://(?P<host>[^/?#\s]+)(?P<rest>[^\s]*)$", re.IGNORECASE
)

#: ``"Headline - Publisher"``: the suffix is only removed for the dedupe key
#: when the tail looks like a name (≤40 chars, no digits). ``"Q4 2026"`` and
#: ``"S&P 500"`` therefore survive; ``" - Yahoo Finance"`` does not.
_PUBLISHER_SUFFIX = re.compile(r"\s+[-\u2013\u2014]\s+([^-\u2013\u2014]{1,40})$")
_HAS_DIGIT = re.compile(r"\d")
_WHITESPACE = re.compile(r"\s+")


class NewsFeedError(ValueError):
    """The document is not an RSS feed this parser can read."""


def yahoo_feed_url(symbol: str) -> str:
    """Yahoo headline feed for one symbol (``BRK-B`` form, no dot-class)."""

    return YAHOO_FEED_URL.format(symbol=canonical_symbol(symbol))


def nasdaq_feed_url(symbol: str) -> str:
    """Nasdaq outbound RSS for one symbol."""

    return NASDAQ_FEED_URL.format(symbol=canonical_symbol(symbol))


def feed_urls(symbol: str) -> tuple[tuple[str, str], ...]:
    """``((feed_name, url), ...)`` for one symbol, in :data:`FEED_NAMES` order."""

    key = canonical_symbol(symbol)
    return (("yahoo", yahoo_feed_url(key)), ("nasdaq", nasdaq_feed_url(key)))


@dataclass(frozen=True, slots=True)
class NewsHeadline:
    """One dated headline from one feed.

    ``published_at`` is always an aware UTC datetime: a feed that prints no
    date, or a date this parser cannot read, produces no headline at all rather
    than one with a guessed timestamp.
    """

    title: str
    url: str
    published_at: datetime
    feed: str

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("title must be non-empty")
        if not self.url:
            raise ValueError("url must be non-empty")
        if self.published_at.tzinfo is None or self.published_at.utcoffset() is None:
            raise ValueError("published_at must be timezone-aware")
        object.__setattr__(self, "published_at", self.published_at.astimezone(UTC))


def parse_pubdate(raw: Any) -> datetime | None:
    """RFC-822 ``<pubDate>`` (ISO-8601 tolerated) → aware UTC, else ``None``.

    ``parsedate_to_datetime`` returns a naive datetime for the RFC-822
    ``-0000`` zone, which means "UTC, zone unknown"; anchoring it to UTC keeps
    two feeds comparable instead of storing one naive stamp beside aware ones.
    """

    text = html.unescape(str(raw or "")).strip()
    if not text:
        return None
    parsed: datetime | None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def clean_url(raw: Any) -> str:
    """An absolute http(s) link with Yahoo's ``tsrc`` query removed.

    Built with plain string splitting on purpose: ``urllib`` is a banned
    transport in this codebase (``tests/_import_rules.py``), and this only
    drops a tracking parameter, so it must not re-encode the rest of the URL.
    """

    text = html.unescape(str(raw or "")).strip()
    match = _ABSOLUTE_URL.match(text)
    if not match:
        return ""
    rest = match.group("rest")
    fragment = ""
    if "#" in rest:
        rest, fragment = rest.split("#", 1)
    query = ""
    if "?" in rest:
        rest, query = rest.split("?", 1)
    kept = [
        part
        for part in query.split("&")
        if part and part.split("=", 1)[0].strip().lower() not in _TRACKING_QUERY_KEYS
    ]
    url = f"{match.group('scheme').lower()}://{match.group('host')}{rest}"
    if kept:
        url = f"{url}?{'&'.join(kept)}"
    if fragment:
        url = f"{url}#{fragment}"
    return url


def _local(tag: object) -> str:
    """Element name without its namespace: feeds vary, tag names do not."""

    return str(tag).rsplit("}", 1)[-1].lower()


def _child(node: ET.Element, name: str) -> ET.Element | None:
    for child in node:
        if _local(child.tag) == name:
            return child
    return None


def _text(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return html.unescape(node.text).strip()


def _title_text(node: ET.Element | None) -> str:
    """Unescape entities and collapse run-together whitespace in a headline."""

    if node is None or node.text is None:
        return ""
    return _WHITESPACE.sub(" ", html.unescape(node.text)).strip()


def _link(node: ET.Element) -> str:
    """``<link>`` first; a permalink ``<guid>`` only when the link is missing."""

    link = _text(_child(node, "link"))
    if link:
        return link
    guid = _child(node, "guid")
    if guid is None:
        return ""
    if (guid.get("isPermaLink") or "").strip().lower() == "false":
        return ""
    return _text(guid)


def title_key(title: str) -> str:
    """Dedupe key: casefolded alphanumerics, publisher suffix removed.

    ``"Apple  Beats Estimates! - Yahoo Finance"`` and
    ``"apple beats estimates"`` collapse to one key, which is what the measured
    7.7% cross-feed overlap needs.
    """

    text = html.unescape(str(title or "")).strip()
    suffix = _PUBLISHER_SUFFIX.search(text)
    if suffix is not None and not _HAS_DIGIT.search(suffix.group(1)):
        text = text[: suffix.start()].strip()
    return "".join(char for char in text.casefold() if char.isalnum())


def _headline(node: ET.Element, feed: str) -> NewsHeadline | None:
    title = _title_text(_child(node, "title"))
    if not title or not title_key(title):
        return None
    url = clean_url(_link(node))
    if not url:
        return None
    published = parse_pubdate(_text(_child(node, "pubdate")))
    if published is None:
        return None
    return NewsHeadline(title=title, url=url, published_at=published, feed=feed)


def _parse_document(document: str | bytes) -> ET.Element:
    if isinstance(document, str):
        # A decoded feed may still carry the UTF-8 BOM; ElementTree rejects it
        # before the XML declaration.
        document = document.lstrip("\ufeff")
    try:
        return ET.fromstring(document)
    except ET.ParseError as exc:
        raise NewsFeedError(f"not a readable RSS document: {exc}") from exc


def _collapse(headlines: Iterable[NewsHeadline]) -> list[NewsHeadline]:
    """One headline per normalised title, newest copy, newest first."""

    best: dict[str, NewsHeadline] = {}
    for headline in headlines:
        key = title_key(headline.title)
        current = best.get(key)
        if current is None or headline.published_at > current.published_at:
            best[key] = headline
    return sorted(best.values(), key=lambda item: item.published_at, reverse=True)


def parse_rss(
    document: str | bytes, *, feed: str = "rss", limit: int | None = None
) -> list[NewsHeadline]:
    """Parse one RSS document into dated headlines, newest first.

    Items without a usable title, an absolute link or a parseable ``pubDate``
    are dropped before the limit is applied, so ``limit=10`` means ten rows a
    reader can actually open. Duplicates by :func:`title_key` collapse to the
    newest copy. ``limit=None`` returns the whole channel.
    """

    if limit is not None and limit < 0:
        raise ValueError("limit must be >= 0 or None")
    if limit == 0:
        return []
    root = _parse_document(document)
    headlines: list[NewsHeadline] = []
    for node in root.iter():
        if _local(node.tag) != "item":
            continue
        headline = _headline(node, feed)
        if headline is not None:
            headlines.append(headline)
    selected = _collapse(headlines)
    return selected if limit is None else selected[:limit]


def parse_yahoo_rss(
    document: str | bytes, *, limit: int | None = None
) -> list[NewsHeadline]:
    """Yahoo's ``feeds.finance.yahoo.com`` RSS shape."""

    return parse_rss(document, feed="yahoo", limit=limit)


def parse_nasdaq_rss(
    document: str | bytes, *, limit: int | None = None
) -> list[NewsHeadline]:
    """Nasdaq's ``nasdaq.com/feed/rssoutbound`` RSS shape (HTML-escaped titles)."""

    return parse_rss(document, feed="nasdaq", limit=limit)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _response_body(response: Any) -> str | bytes:
    """Read a 200 response body, or raise so one feed counts as failed."""

    status = int(getattr(response, "status_code", 0) or 0)
    if status != 200:
        raise NewsFeedError(f"feed answered HTTP {status}")
    body = getattr(response, "text", None)
    if isinstance(body, (str, bytes)) and body:
        return body
    content = getattr(response, "content", b"")
    if isinstance(content, (str, bytes)) and content:
        return content
    raise NewsFeedError("feed answered an empty body")


class RssNewsSource:
    """Yahoo + Nasdaq RSS as one depth source, two feeds per symbol.

    ``fetcher`` receives a URL and returns the document (sync or awaitable);
    ``client`` is an already-built async HTTP client exposing ``get``. Passing
    neither builds the provider client per cycle. Either way a failing feed is
    logged and skipped: one 500 on Yahoo must not erase Nasdaq's headline.
    """

    name = "news"

    def __init__(
        self,
        *,
        name: str = "news",
        fetcher: Callable[[str], Any] | None = None,
        client: Any | None = None,
        limit: int = DEFAULT_LIMIT,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        concurrency: int = DEFAULT_CONCURRENCY,
        feeds: Sequence[str] = FEED_NAMES,
        user_agent: str = BROWSER_USER_AGENT,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if fetcher is not None and not callable(fetcher):
            raise TypeError("fetcher must be callable")
        if not str(name or "").strip():
            raise ValueError("name must be non-empty")
        if int(limit) < 1:
            raise ValueError("limit must be >= 1")
        if float(timeout_s) <= 0:
            raise ValueError("timeout_s must be > 0")
        ordered: list[str] = []
        for feed in feeds:
            feed_name = str(feed or "").strip().lower()
            if feed_name not in FEED_NAMES:
                raise ValueError(f"unknown feed {feed!r}; expected one of {FEED_NAMES}")
            if feed_name not in ordered:
                ordered.append(feed_name)
        if not ordered:
            raise ValueError("at least one feed is required")
        self.name = str(name).strip()
        self.limit = int(limit)
        self.timeout_s = float(timeout_s)
        self.concurrency = max(1, int(concurrency))
        self.feeds = tuple(ordered)
        self.user_agent = str(user_agent or BROWSER_USER_AGENT)
        self._fetcher = fetcher
        self._client = client
        self._clock = clock or _utcnow

    def urls(self, symbol: str) -> tuple[tuple[str, str], ...]:
        """``((feed, url), ...)`` for one symbol; input may be ``brk.b``."""

        key = canonical_symbol(symbol)
        builders = {"yahoo": yahoo_feed_url, "nasdaq": nasdaq_feed_url}
        return tuple((feed, builders[feed](key)) for feed in self.feeds)

    def headers(self) -> dict[str, str]:
        return {
            "user-agent": self.user_agent,
            "accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        }

    async def _fetch(self, url: str, client: Any | None) -> str | bytes:
        if self._fetcher is not None:
            body = self._fetcher(url)
            body = await body if inspect.isawaitable(body) else body
            if not isinstance(body, (str, bytes)):
                raise TypeError("fetcher must return str or bytes")
            return body
        if client is None:
            raise NewsFeedError("no fetcher, client or HTTP transport available")
        return _response_body(await client.get(url))

    async def _one(
        self, symbol: str, semaphore: asyncio.Semaphore, client: Any | None
    ) -> DepthObservation | None:
        key = canonical_symbol(symbol)
        headlines: list[NewsHeadline] = []
        for feed, url in self.urls(key):
            async with semaphore:
                try:
                    headlines.extend(parse_rss(await self._fetch(url, client), feed=feed))
                except Exception as exc:  # noqa: BLE001 - one feed is not the source
                    logger.info("%s news feed failed for %s: %s", feed, key, exc)
                    continue
        if not headlines:
            return None
        latest = _collapse(headlines)[: self.limit]
        if not latest:
            return None
        top = latest[0]
        return DepthObservation(
            symbol=key,
            # Exactly the three single cells the depth catalog owns; the panel
            # of headlines stays in app/news/, not in a one-row depth store.
            fields={
                "news_title": top.title,
                "news_url": top.url,
                "news_time": top.published_at.isoformat(),
            },
            # Merge recency is fetch recency (see depth.py); the article's own
            # time travels in news_time so a later cycle can replace the set.
            as_of=self._clock(),
            source=self.name,
        )

    async def fetch_many(
        self, symbols: Sequence[str]
    ) -> dict[str, DepthObservation]:
        ordered: list[str] = []
        for raw in symbols:
            symbol = canonical_symbol(raw)
            if symbol and symbol not in ordered:
                ordered.append(symbol)
        if not ordered:
            return {}
        semaphore = asyncio.Semaphore(self.concurrency)

        async def gather(client: Any | None) -> dict[str, DepthObservation]:
            results = await asyncio.gather(
                *(self._one(symbol, semaphore, client) for symbol in ordered)
            )
            return {
                observation.symbol: observation
                for observation in results
                if observation is not None
            }

        if self._fetcher is not None or self._client is not None:
            return await gather(self._client)
        from app.providers import http as provider_http

        async with provider_http.build_client(
            timeout_s=self.timeout_s, headers=self.headers()
        ) as client:
            return await gather(client)


__all__ = [
    "DEFAULT_CONCURRENCY",
    "DEFAULT_LIMIT",
    "DEFAULT_TIMEOUT_S",
    "FEED_NAMES",
    "NASDAQ_FEED_URL",
    "NewsFeedError",
    "NewsHeadline",
    "RssNewsSource",
    "YAHOO_FEED_URL",
    "clean_url",
    "feed_urls",
    "nasdaq_feed_url",
    "parse_nasdaq_rss",
    "parse_pubdate",
    "parse_rss",
    "parse_yahoo_rss",
    "title_key",
    "yahoo_feed_url",
]
