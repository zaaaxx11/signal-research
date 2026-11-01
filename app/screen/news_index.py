"""News keywords over the product's own merged corpus, not a live feed.

``news_keywords`` was refused as *Elite-only* and "one headline per symbol is not
an article corpus". Half of that was a tier, and the other half was out of date:
the product already merges a real corpus (:mod:`app.news.service` — Google News,
GDELT and the two RSS wires, deduped by title and URL — and
``GET /market/news`` serves it). What was missing was a **store** the filter
could query without a provider round trip, which is what this module reads:
``app/screen/data/news_corpus.json``, written by ``scripts/news-corpus.py``.

The token is free text, and it is validated
-------------------------------------------
``news_kw:<word>`` is not a bucket with a fixed vocabulary — a keyword search
over free text is the point. It is still constrained: letters, digits, spaces
and hyphens, at most :data:`MAX_LENGTH` characters
(:data:`PATTERN`). Anything else is a 422 ``invalid_news_keyword`` that publishes
the pattern, because a token carrying a quote, a colon or a regex metacharacter
is either a typo or an attempt to reach the matcher itself.

Matching is whole-word and case-insensitive
-------------------------------------------
The corpus is tokenised once (casefolded, split on anything that is not a letter
or a digit), so ``semiconductor`` matches ``Semiconductor`` and
``semiconductors`` does **not** match ``semiconductor``. A multi-word query
(``ai chips``, ``supply-chain``) is an **AND** over its words: every word must
appear in the headline. That is the only reading of a space-separated keyword
box that does not quietly turn "supply chain" into "supply OR chain". A word the
corpus never uses matches nobody — an empty answer, not an error.

One index, rebuilt when the corpus moves
----------------------------------------
:func:`index` builds the inverted index lazily and caches it against the file's
stamp, so a selection costs one ``stat`` per call and a corpus rebuild is live.
Tests point :data:`PATH_ENV` at a fixture corpus; nothing here fetches.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

#: The alphabet a keyword may use: letters, digits, spaces and hyphens, at most
#: 40 characters. Published in the document, enforced here, and the only thing a
#: client needs to validate the box it draws.
PATTERN = r"[A-Za-z0-9][A-Za-z0-9 -]{0,39}"

#: The compiled form of :data:`PATTERN`, anchored: a *full* match or nothing.
KEYWORD_RE = re.compile(PATTERN)

#: The longest keyword the pattern allows (mirrors the ``{0,39}`` above).
MAX_LENGTH = 40

#: Where the vendored corpus lives (``pyproject`` ships ``data/*.json``).
DATA_PATH = Path(__file__).with_name("data") / "news_corpus.json"

#: A deployment that keeps its corpus elsewhere names it here.
PATH_ENV = "ZTOCKZ_NEWS_CORPUS"

#: The sentence a 422 / the document repeats when there is no usable corpus.
REASON = (
    "needs the merged news corpus (app/screen/data/news_corpus.json): the file is "
    "missing, unstamped or empty, so no headline exists to search — rebuild it "
    "with python scripts/news-corpus.py"
)

#: The letters/digits split the index uses. ``re`` rather than ``str.isalnum``
#: so an accented or CJK headline tokenises the same way on every host.
_WORD_RE = re.compile(r"[a-z0-9]+")


class KeywordError(ValueError):
    """A keyword the control cannot accept, carrying a stable machine code."""

    code = "invalid_news_keyword"


@dataclass(frozen=True, slots=True)
class Corpus:
    """One stamped corpus: the headlines, and the facts that date them."""

    as_of: str | None
    captured_at: str | None
    items: tuple[Mapping[str, Any], ...]
    source: Mapping[str, Any]

    @property
    def available(self) -> bool:
        return self.as_of is not None and bool(self.items)

    @property
    def symbols(self) -> frozenset[str]:
        return frozenset(
            str(item.get("symbol", "")).strip().upper()
            for item in self.items
            if str(item.get("symbol", "")).strip()
        )


def parse(document: object) -> Corpus:
    """One corpus object → :class:`Corpus`; a malformed one has no stamp."""

    if not isinstance(document, Mapping):
        return Corpus(as_of=None, captured_at=None, items=(), source=MappingProxyType({}))
    raw_items = document.get("items")
    items: list[Mapping[str, Any]] = []
    if isinstance(raw_items, Sequence) and not isinstance(raw_items, (str, bytes)):
        for entry in raw_items:
            if not isinstance(entry, Mapping):
                continue
            title = entry.get("title")
            symbol = entry.get("symbol")
            if not isinstance(title, str) or not title.strip():
                continue
            if not isinstance(symbol, str) or not symbol.strip():
                continue
            items.append(
                MappingProxyType(
                    {
                        "symbol": symbol.strip().upper(),
                        "title": title.strip(),
                        "published": entry.get("published"),
                        "source": entry.get("source"),
                    }
                )
            )
    as_of = document.get("as_of")
    captured = document.get("captured_at")
    source = document.get("source")
    return Corpus(
        as_of=as_of.strip() if isinstance(as_of, str) and as_of.strip() else None,
        captured_at=captured.strip() if isinstance(captured, str) else None,
        items=tuple(items),
        source=MappingProxyType(dict(source)) if isinstance(source, Mapping) else MappingProxyType({}),
    )


@dataclass(frozen=True, slots=True)
class Index:
    """The inverted index: word → the symbols whose headline used it."""

    as_of: str | None
    symbols: frozenset[str]
    words: Mapping[str, frozenset[str]]
    headlines: int

    def match(self, keyword: str) -> frozenset[str]:
        """The symbols whose headline carries **every** word of ``keyword``."""

        parts = normalize(keyword)
        if not parts:
            return frozenset()
        found: set[str] | None = None
        for word in parts:
            hits = set(self.words.get(word, frozenset()))
            found = hits if found is None else found & hits
            if not found:
                return frozenset()
        return frozenset(found or ())


def normalize(keyword: object) -> tuple[str, ...]:
    """The words of one keyword, lower-case, in order — or a :class:`KeywordError`.

    The check is a **full match** on :data:`PATTERN` after collapsing runs of
    whitespace, so ``"  ai  chips "`` is accepted as ``("ai", "chips")`` while
    ``"ai;drop"`` is refused: a semicolon cannot reach the matcher.
    """

    if not isinstance(keyword, str):
        raise KeywordError(f"a news keyword must be a string, got {type(keyword).__name__}")
    text = " ".join(keyword.split())
    if not text:
        raise KeywordError("a news keyword cannot be empty")
    if len(text) > MAX_LENGTH:
        raise KeywordError(
            f"news keyword {text[:20]!r}… is {len(text)} characters; the maximum "
            f"is {MAX_LENGTH}"
        )
    if KEYWORD_RE.fullmatch(text) is None:
        raise KeywordError(
            f"news keyword {text!r} is outside the accepted alphabet: letters, "
            f"digits, spaces and hyphens only (pattern {PATTERN})"
        )
    words = tuple(_WORD_RE.findall(text.casefold()))
    if not words:
        raise KeywordError(f"news keyword {text!r} carries no letters or digits")
    return words


def _key(symbol: object) -> str | None:
    if not isinstance(symbol, str):
        return None
    text = symbol.strip().upper().replace("-", ".").replace(" ", ".")
    return text or None


_CACHE: dict[str, tuple[int, int, Corpus, Index]] = {}


def configured_path() -> Path:
    override = os.environ.get(PATH_ENV, "").strip()
    return Path(override) if override else DATA_PATH


def load(path: Path | str | None = None) -> Corpus:
    """The corpus, or an unstamped one (never an exception at read time)."""

    return _read(Path(path) if path is not None else configured_path())[0]


def _read(target: Path) -> tuple[Corpus, Index]:
    try:
        stat = target.stat()
    except OSError:
        empty = Corpus(as_of=None, captured_at=None, items=(), source=MappingProxyType({"path": str(target)}))
        return empty, Index(as_of=None, symbols=frozenset(), words=MappingProxyType({}), headlines=0)
    cached = _CACHE.get(str(target))
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2], cached[3]
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        empty = Corpus(as_of=None, captured_at=None, items=(), source=MappingProxyType({"path": str(target)}))
        return empty, Index(as_of=None, symbols=frozenset(), words=MappingProxyType({}), headlines=0)
    corpus = parse(document)
    index = build(corpus)
    _CACHE[str(target)] = (stat.st_mtime_ns, stat.st_size, corpus, index)
    return corpus, index


def build(corpus: Corpus) -> Index:
    """Tokenise every headline once: word → symbols, plus the symbol set."""

    words: dict[str, set[str]] = {}
    symbols: set[str] = set()
    for item in corpus.items:
        key = _key(item.get("symbol"))
        if key is None:
            continue
        symbols.add(key)
        for word in _WORD_RE.findall(str(item.get("title", "")).casefold()):
            words.setdefault(word, set()).add(key)
    return Index(
        as_of=corpus.as_of,
        symbols=frozenset(symbols),
        words=MappingProxyType({word: frozenset(found) for word, found in words.items()}),
        headlines=len(corpus.items),
    )


def clear() -> None:
    _CACHE.clear()


def index(path: Path | str | None = None) -> Index:
    """The inverted index for the configured corpus (built once per stamp)."""

    return _read(Path(path) if path is not None else configured_path())[1]


def available() -> bool:
    return load().available


def reason() -> str | None:
    """Why ``news_keywords`` cannot run, or ``None`` when it can."""

    corpus = load()
    if not corpus.available:
        return REASON
    return None


def symbols_for(keyword: object) -> frozenset[str]:
    """The symbols one keyword matches, or :class:`KeywordError`."""

    words = normalize(keyword)
    return index().match(" ".join(words))


def coverage() -> dict[str, Any]:
    """The measured block the filter document publishes."""

    corpus = load()
    built = index()
    return {
        "available": corpus.available,
        "as_of": corpus.as_of,
        "captured_at": corpus.captured_at,
        "headlines": built.headlines,
        "words": len(built.words),
        "symbols": len(built.symbols),
        "reason": reason(),
    }


__all__ = [
    "DATA_PATH",
    "Index",
    "KEYWORD_RE",
    "KeywordError",
    "MAX_LENGTH",
    "PATH_ENV",
    "PATTERN",
    "REASON",
    "Corpus",
    "available",
    "build",
    "clear",
    "configured_path",
    "coverage",
    "index",
    "load",
    "normalize",
    "parse",
    "reason",
    "symbols_for",
]
