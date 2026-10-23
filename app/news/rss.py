"""Google News RSS → typed headlines, parsed from a document, never fetched.

PLAN-finviz-parity.md §1 counts **8 news items per ticker** beside the 71 quote
metrics, and gate 6 wants every cell traceable. The traceable half of a
headline is *when* it was published and *who* published it, so this module
refuses to invent either:

* ``published_at`` comes from ``<pubDate>`` alone. No pubDate → ``None``; a
  feed read at 14:02 does not turn an undated article into a 14:02 article.
* ``source`` comes from the RSS ``<source>`` element alone. No element → ``""``,
  never the last word of the title guessed into a publisher name.
* ``url`` comes from ``<link>`` (or a permalink ``<guid>``). An item without a
  URL is dropped: a headline the reader cannot open is not a row in the list.

The document is an argument, not a request: fetching belongs to the provider
layer, so the parser is pure and the test suite (which blocks sockets) can
exercise the real feed format.

Google News prints ``"Headline - Publisher"`` in ``<title>`` and repeats the
publisher in ``<source>``; the suffix is removed only when it matches the
``<source>`` text exactly. The ``<description>`` cell is a list of related
articles in HTML and is ignored — it is not the headline.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Iterable
from xml.etree import ElementTree as ET

#: Finviz shows eight headlines per ticker; the default matches it.
DEFAULT_LIMIT = 8

FEED_ENDPOINT = "https://news.google.com/rss/search"


class GoogleNewsError(ValueError):
    """The document is not an RSS feed we can read."""


@dataclass(frozen=True, slots=True)
class NewsItem:
    """One headline, with the provenance a screen needs to show it honestly."""

    headline: str
    url: str
    source: str
    published_at: datetime | None = None
    source_url: str | None = None

    def as_dict(self) -> dict[str, object]:
        """JSON-shaped item: the timestamp is ISO-8601 UTC or ``None``."""

        return {
            "headline": self.headline,
            "url": self.url,
            "source": self.source,
            "published_at": (
                self.published_at.isoformat() if self.published_at is not None else None
            ),
            "source_url": self.source_url,
        }


def _local(tag: object) -> str:
    """The element name without its namespace (feeds vary, tags do not)."""

    return str(tag).rsplit("}", 1)[-1].lower()


def _child(node: ET.Element, name: str) -> ET.Element | None:
    for child in node:
        if _local(child.tag) == name:
            return child
    return None


def _text(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _headline(title: str, source: str) -> str:
    """Unescape the title and strip the ``" - Publisher"`` suffix Google adds."""

    text = html.unescape(title).strip()
    if source:
        suffix = f" - {source}"
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text


def _published_at(raw: str) -> datetime | None:
    """RFC-822 ``<pubDate>`` (or ISO-8601) → aware UTC datetime, else ``None``."""

    text = raw.strip()
    if not text:
        return None
    parsed: datetime | None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        # RFC-822 "-0000" means "UTC, zone unknown"; storing it naive would make
        # two feeds incomparable, so it is anchored to UTC.
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _item(node: ET.Element) -> NewsItem | None:
    title = _text(_child(node, "title"))
    if not title:
        return None

    link = _text(_child(node, "link"))
    if not link:
        guid = _child(node, "guid")
        if guid is not None and (guid.get("isPermaLink") or "").lower() != "false":
            link = _text(guid)
    if not link:
        return None

    source_node = _child(node, "source")
    source = html.unescape(_text(source_node)).strip()
    published = _published_at(_text(_child(node, "pubdate")))

    return NewsItem(
        headline=_headline(title, source) or html.unescape(title).strip(),
        url=link,
        source=source,
        published_at=published,
        source_url=(source_node.get("url") if source_node is not None else None) or None,
    )


def _parse_document(document: str | bytes) -> ET.Element:
    try:
        return ET.fromstring(document)
    except ET.ParseError as exc:
        raise GoogleNewsError(f"not a readable RSS document: {exc}") from exc


def _items(root: ET.Element) -> Iterable[ET.Element]:
    for node in root.iter():
        if _local(node.tag) == "item":
            yield node


def parse_google_news(
    document: str | bytes, *, limit: int | None = DEFAULT_LIMIT
) -> list[NewsItem]:
    """Parse a Google News RSS document into at most ``limit`` usable items.

    Items without a title or a URL are dropped before the limit is applied, so
    ``limit=8`` means eight headlines a reader can actually open. Items that
    repeat a URL already taken are collapsed: two rows pointing at one article
    are one headline. ``limit=None`` returns the whole channel.
    """

    if limit is not None and limit < 0:
        raise ValueError("limit must be >= 0 or None")
    if limit == 0:
        return []

    root = _parse_document(document)
    parsed: list[NewsItem] = []
    seen: set[str] = set()
    for node in _items(root):
        item = _item(node)
        if item is None or item.url in seen:
            continue
        seen.add(item.url)
        parsed.append(item)
        if limit is not None and len(parsed) >= limit:
            break
    return parsed


_SAFE_QUERY = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~:"
)


def _quote(text: str) -> str:
    """RFC 3986 percent-encoding (``urllib`` is a banned transport here).

    ``:`` stays literal so ``ceid=US:en`` keeps Google's own spelling.
    """

    encoded: list[str] = []
    for char in text:
        if char in _SAFE_QUERY:
            encoded.append(char)
        else:
            encoded.append("".join(f"%{byte:02X}" for byte in char.encode("utf-8")))
    return "".join(encoded)


def feed_url(query: str, *, hl: str = "en-US", gl: str = "US", ceid: str = "US:en") -> str:
    """The RSS URL for a search phrase — a string, not a request.

    The caller (a provider adapter) owns the ``GET``; building the URL here keeps
    the feed format and its parameters in one place.
    """

    if not query.strip():
        raise ValueError("query may not be blank")
    return (
        f"{FEED_ENDPOINT}?q={_quote(query.strip())}"
        f"&hl={_quote(hl)}&gl={_quote(gl)}&ceid={_quote(ceid)}"
    )


__all__ = [
    "DEFAULT_LIMIT",
    "FEED_ENDPOINT",
    "GoogleNewsError",
    "NewsItem",
    "feed_url",
    "parse_google_news",
]
