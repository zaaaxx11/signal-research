"""Free-text intent → anchors the resolver can prove (02 §3.3 step 1-3).

The parser answers three questions and refuses to answer a fourth:

1. **What asset class / sector was named?** from the keyword tables in the
   reference master, longest phrase first, on word boundaries — ``banking``
   must not match inside ``bank`` or vice versa.
2. **Which venue was named?** from ``jurisdictions`` (country aliases), venue
   names and MICs. A venue anchor is what stops "banks" from returning JPMorgan
   in an Indonesian universe.
3. **How many?** ``top 40`` is a size; a bare ``top``/``largest`` is a wish.
4. **What was not said** ("crypto", no venue) is *not* guessed. The intent
   carries ``wants_size`` and a missing anchor, and the resolver turns that into
   a narrowing question instead of a universe.

No I/O and no knowledge of instruments: this module only reads the keyword
tables, so it can be unit-tested without a market.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

from app.resolve.aliases import normalize

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.resolve.reference import Reference

#: ``top 40`` / ``top 1,000`` — the only numeric size form accepted.
_SIZE = re.compile(r"\btop\s+(\d{1,4})\b")
_SIZE_SEPARATORS = re.compile(r"(?<=\d)[,\s](?=\d{3}\b)")


@dataclass(frozen=True)
class Intent:
    """What one text query asserts, and what it leaves open."""

    text: str
    parts: tuple[str, ...]
    listing: bool
    venue: str | None = None
    sector: str | None = None
    asset_class: str | None = None
    countries: tuple[str, ...] = ()
    size: int | None = None
    wants_size: bool = False
    signals: tuple[str, ...] = ()

    @property
    def anchored(self) -> bool:
        """True when the query names a venue or a sector to expand."""

        return bool(self.venue or self.sector)

    def question_for_missing_anchor(self) -> str:
        return "Which venue should this universe come from?"


def parse(text: str, reference: Reference) -> Intent:
    """Parse one text query against the master's keyword tables."""

    raw = (text or "").strip()
    parts = tuple(part.strip() for part in raw.split(",") if part.strip())
    if len(parts) > 1:
        # A comma list is a list of names/tickers, not a sentence: do not infer
        # a sector from words that happen to appear in a company name.
        return Intent(text=raw, parts=parts, listing=True, signals=("list",))

    normalized = normalize(raw)
    venue, venue_signal = _venue(normalized, reference)
    sector = _first_phrase(normalized, reference.sector_keywords)
    asset_class = _first_phrase(normalized, reference.asset_class_keywords)
    size, wants_size = _size(normalized, reference)

    signals: list[str] = [signal for signal in (venue_signal,) if signal]
    if sector:
        signals.append(f"sector:{sector}")
    if asset_class:
        signals.append(f"asset_class:{asset_class}")
    if size is not None:
        signals.append(f"size:{size}")
    elif wants_size:
        signals.append("size:unspecified")

    countries: tuple[str, ...] = ()
    if venue:
        found = reference.venue(venue)
        if found is not None and found.country:
            countries = (found.country,)

    return Intent(
        text=raw,
        parts=(raw,),
        listing=False,
        venue=venue,
        sector=sector,
        asset_class=asset_class,
        countries=countries,
        size=size,
        wants_size=wants_size,
        signals=tuple(signals),
    )


# --------------------------------------------------------------------------- #
# Signal extraction
# --------------------------------------------------------------------------- #


def _phrase(needle: str, haystack: str) -> bool:
    """Whole-token containment: "id" must not match "dividend"."""

    if not needle:
        return False
    return re.search(rf"(?<![0-9a-z]){re.escape(needle)}(?![0-9a-z])", haystack) is not None


def _first_phrase(normalized: str, table: Mapping[str, tuple[str, ...]]) -> str | None:
    """The longest matching phrase wins (``banking`` before ``bank``)."""

    candidates: list[tuple[int, str, str]] = []
    for name, phrases in table.items():
        for phrase in phrases:
            key = normalize(phrase)
            if key and _phrase(key, normalized):
                candidates.append((-len(key), name, key))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def _venue(normalized: str, reference: Reference) -> tuple[str | None, str | None]:
    """Return ``(mic, signal)`` for the strongest venue anchor in the text."""

    hits: list[tuple[int, str, str]] = []  # (-len(alias), mic, signal)

    for mic in sorted({venue.mic for venue in reference.venues}):
        key = normalize(mic)
        if _phrase(key, normalized):
            hits.append((-len(key), mic, f"mic:{mic}"))

    for venue in reference.venues:
        key = normalize(venue.name)
        if key and _phrase(key, normalized):
            hits.append((-len(key), venue.mic, f"venue_name:{venue.name}"))

    for name, entry in sorted(reference.jurisdictions.items()):
        mic = str(entry.get("venue") or "").upper()
        if not mic:
            continue
        for alias in entry.get("aliases") or ():
            key = normalize(alias)
            if key and _phrase(key, normalized):
                hits.append((-len(key), mic, f"jurisdiction:{name}"))

    if not hits:
        return None, None
    hits.sort()
    return hits[0][1], hits[0][2]


def _size(normalized: str, reference: Reference) -> tuple[int | None, bool]:
    compact = _SIZE_SEPARATORS.sub("", normalized)
    found = _SIZE.search(compact)
    if found:
        value = int(found.group(1))
        return (value if value >= 1 else None), False

    for phrases in reference.size_keywords.values():
        if not isinstance(phrases, list):
            continue
        for phrase in phrases:
            key = normalize(str(phrase))
            if key and _phrase(key, normalized):
                return None, True
    return None, False


__all__ = ["Intent", "parse"]
