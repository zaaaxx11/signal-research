"""Alias normalisation and candidate lookup (02 §3.3, 03 §3.1).

Two rules from the architecture are implemented here and nowhere else:

* **an alias proposes an identity, it never becomes one.** :func:`match` returns
  *every* instrument an input could mean; the caller decides that one candidate
  is a resolution and more than one is ``ambiguous``. There is no "best match"
  function, because a best match is a coin flip with a confidence score.
* **normalisation is the same for storage and lookup.** ``instrument_aliases.
  alias_norm`` and the in-memory index both come from :func:`normalize`, so a
  seed and a query can never disagree about what "Johnson & Johnson" is.

``&`` becomes ``and`` on purpose: the master writes "Johnson & Johnson", a
human writes "Johnson and Johnson", and both must land on the same key.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps the import graph acyclic
    from app.resolve.reference import Reference

#: Everything that is not a letter or a digit separates tokens.
_SEPARATORS = re.compile(r"[^0-9a-z]+")

#: A shorter fragment matches too much (``v`` is a ticker *and* a letter).
_MIN_FUZZY_CHARS = 3

#: How a match was produced; the resolver turns this into a confidence.
MATCH_SYMBOL = "symbol"
MATCH_ALIAS = "alias"
MATCH_NAME = "name"
MATCH_NONE = "none"
MATCH_EMPTY = "empty"


def normalize(value: str | None) -> str:
    """Case-fold, spell out ``&``, and collapse punctuation to single spaces."""

    if not value:
        return ""
    text = value.casefold().replace("&", " and ")
    return " ".join(_SEPARATORS.sub(" ", text).split())


def match(reference: Reference, query: str) -> tuple[tuple[str, ...], str]:
    """Return ``(instrument_ids, how)`` for one input string.

    Order is deliberate: ticker, then exact alias, then substring of a name.
    A ticker is the only input that may be trusted without an anchor; an exact
    alias may still fan out to two share classes (that is the ambiguous case);
    a substring is the weakest evidence and only ever yields a candidate set.
    """

    raw = (query or "").strip()
    if not raw:
        return (), MATCH_EMPTY

    by_symbol = reference.symbol_index.get(raw.upper())
    if by_symbol:
        return by_symbol, MATCH_SYMBOL

    normalized = normalize(raw)
    if not normalized:
        return (), MATCH_EMPTY

    by_alias = reference.alias_index.get(normalized)
    if by_alias:
        return by_alias, MATCH_ALIAS

    if len(normalized) < _MIN_FUZZY_CHARS:
        return (), MATCH_NONE

    hits: set[str] = set()
    for instrument in reference.instruments:
        if normalized in normalize(instrument.name):
            hits.add(instrument.instrument_id)
            continue
        if any(normalized in normalize(alias) for alias in instrument.aliases):
            hits.add(instrument.instrument_id)
    if hits:
        return tuple(sorted(hits)), MATCH_NAME
    return (), MATCH_NONE


#: Confidence per match kind. Fuzzy evidence never claims certainty (02 §3.3).
CONFIDENCE: dict[str, float] = {
    MATCH_SYMBOL: 1.0,
    MATCH_ALIAS: 0.97,
    MATCH_NAME: 0.9,
    MATCH_NONE: 0.0,
    MATCH_EMPTY: 0.0,
}


__all__ = [
    "CONFIDENCE",
    "MATCH_ALIAS",
    "MATCH_EMPTY",
    "MATCH_NAME",
    "MATCH_NONE",
    "MATCH_SYMBOL",
    "match",
    "normalize",
]
