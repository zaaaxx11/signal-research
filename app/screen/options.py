"""The enumerations a filter picker is built from: industry, country, venue, sector.

The screener asks for values it cannot enumerate itself. ``sec`` shipped
``options: []`` and a client drew a disabled control; ``industry`` and ``geo``
were free text over a vocabulary of 129 and 55 provider spellings that a reader
had to type exactly, because ``eq`` on a text column is case-sensitive and
``"semiconductors"`` matches nothing at all. This module is the census that
replaces the guessing — and the one place the *sector* taxonomy is written down
for a document that may not scan.

Two readers, one vocabulary
---------------------------
A row carries the provider's own strings: ``sector`` is its twenty-one-name
taxonomy (``Electronic Technology``), ``industry`` its SIC-shaped description
(``Semiconductors``), ``country`` the listing's country, and ``exchange`` the
MIC the universe layer mapped the venue to (``XNAS``). Every option this module
publishes is one of those cells, **verbatim**: the option's ``id`` is the value a
request sends, and its ``predicate`` is the value the comparison runs with — the
label itself, never a lower-cased or slugged variant, because the row would not
match it.

The sector labels are also needed by ``GET /screens/filters``, which is built
from memory and may not scan (a 40 ms panel that becomes a 2 s one is a
regression, and a deployment without a provider would serve no document at all).
So :data:`SECTOR_LABELS` vendors the taxonomy, and
``tests/test_screen_options.py`` pins it against the same reader the market
routes count with (:func:`app.api.sectors.sector_ids` over the live
distribution) plus this module's own census. The inline list carries its counts
too: ``GET /screens/filters`` reads the same sector population through
``app.api.sectors`` and publishes each label with the rows behind it, so a client
needs no second request. This route remains the standalone census — the same
numbers, dated by ``as_of``, for a picker that asks only for one key.

The census is a memo, not a snapshot
------------------------------------
One scan per request would make a picker that filters as you type a provider call
per keystroke. The census is therefore memoised for :data:`MEMO_TTL_S` — an hour,
because a sector does not move between 09:31 and 10:30 — per key, and per app
(``app.state``), so a test builds a fresh app and gets a fresh memo. It is not a
snapshot: past the hour the next request re-reads the universe, and ``as_of``
says when the numbers were taken.

A failed scan is never memoised: the route raises ``503 scanner_unavailable``
before anything is stored, because an hour of "no sectors" would outlive the
outage that caused it.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Callable

__all__ = [
    "EXCHANGE_NAMES",
    "FIELD_BY_KEY",
    "MEMO_ATTRIBUTE",
    "MEMO_TTL_S",
    "OPTION_KEYS",
    "SECTOR_LABELS",
    "OptionMemo",
    "UnknownOptionKey",
    "document",
    "enumerate_options",
    "memo",
    "moment",
    "option_key",
]

#: The four enumerations, in the order a panel draws them.
OPTION_KEYS: tuple[str, ...] = ("industry", "country", "exchange", "sector")

#: key → the row column it enumerates. One table, so a new key is one entry and
#: the census cannot count a column the picker does not send.
FIELD_BY_KEY: Mapping[str, str] = MappingProxyType(
    {
        "industry": "industry",
        "country": "country",
        "exchange": "exchange",
        "sector": "sector",
    }
)

#: The provider's twenty-one sector labels, in the order a menu draws them.
#:
#: Vendored, not derived: ``GET /screens/filters`` is built from memory (see the
#: module docstring), and this is the only stable *vocabulary* the census needs
#: offline. It is not a second copy of a live list — the live list is the counts,
#: and those stay behind ``/market/sectors`` and ``/screens/options/sector``.
#: Measured 2026-11-01: these twenty-one names hold every row of the 5,576-row US
#: universe, which is what makes the sector counts add up to the total. A
#: provider rename fails ``tests/test_screen_options.py`` instead of silently
#: shipping twenty names and a menu entry nobody can select.
SECTOR_LABELS: tuple[str, ...] = (
    "Commercial Services",
    "Communications",
    "Consumer Durables",
    "Consumer Non-Durables",
    "Consumer Services",
    "Distribution Services",
    "Electronic Technology",
    "Energy Minerals",
    "Finance",
    "Government",
    "Health Services",
    "Health Technology",
    "Industrial Services",
    "Miscellaneous",
    "Non-Energy Minerals",
    "Process Industries",
    "Producer Manufacturing",
    "Retail Trade",
    "Technology Services",
    "Transportation",
    "Utilities",
)

#: MIC → the venue a reader recognises. The row carries the code (that is what
#: ``exch`` compares), so the option's ``id`` and ``predicate`` are the code and
#: its ``label`` is this name beside it — a picker that searched only for
#: "NASDAQ" would not find ``XNAS``.
EXCHANGE_NAMES: Mapping[str, str] = MappingProxyType(
    {"XASE": "AMEX", "XCBO": "CBOE", "XNAS": "NASDAQ", "XNYS": "NYSE"}
)

#: How long one census answers without re-reading the universe, in seconds.
#:
#: An hour is a data claim, not a cache default: a sector does not move within a
#: session, and the numbers are published with the moment they were taken
#: (``as_of``), so a client can always tell how old they are. Expiry is hard —
#: there is no stale-while-revalidate — because the point of the number is that
#: it is the current universe's.
MEMO_TTL_S = 3600.0

#: Where the per-app memo lives on ``app.state``.
MEMO_ATTRIBUTE = "screen_options_memo"


class UnknownOptionKey(ValueError):
    """A key no enumeration owns, carrying the stable machine code (404)."""

    code = "unknown_option_key"


def option_key(identifier: object) -> str:
    """``identifier`` as one of :data:`OPTION_KEYS`, or :class:`UnknownOptionKey`.

    Case-folded on the way in (``Sector`` is ``sector``) and *canonical* on the
    way out, so two spellings of one key are one memo entry and one document.
    """

    if not isinstance(identifier, str):
        raise UnknownOptionKey(
            identifier,
            f"an option key must be a string, got {type(identifier).__name__}",
        )
    token = identifier.strip().casefold()
    if token not in OPTION_KEYS:
        raise UnknownOptionKey(
            identifier,
            f"unknown option key {identifier!r}; expected one of "
            f"{', '.join(OPTION_KEYS)}",
        )
    return token


def _option_id(label: str) -> str:
    """The value a request sends for ``label``.

    For the three text columns the wire value *is* the row's cell; the venue's is
    the MIC, which is the same string. Nothing is slugged: a slug would be a
    second vocabulary, and the filter compares the cell.
    """

    return label


def _option_label(key: str, label: str) -> str:
    """What the menu draws for ``label`` — the row's cell, or a named venue."""

    if key == "exchange":
        name = EXCHANGE_NAMES.get(label)
        return f"{name} ({label})" if name else label
    return label


def _cell(value: Any) -> str | None:
    """A row cell as the string it carries, or ``None`` when it carries none.

    No trimming and no case folding: the predicate this becomes is compared with
    ``eq`` against the cell itself, so a "cleaned" label would be an option that
    matches no row.
    """

    if not isinstance(value, str):
        return None
    return value if value.strip() else None


def enumerate_options(key: str, rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One census: every value ``key``'s column holds, with its row count.

    Pure and order-stable: labels sort alphabetically (a picker is searched by
    name, and two calls over one population must diff cleanly), and a row whose
    cell is missing or blank is counted in no option — it is a row the provider
    did not classify, which is not a value a filter could send.

    ``count`` is a real row count over the rows given; the caller passes the
    universe it actually read.
    """

    canonical = option_key(key)
    column = FIELD_BY_KEY[canonical]
    counts: dict[str, int] = {}
    for row in rows:
        label = _cell(row.get(column))
        if label is not None:
            counts[label] = counts.get(label, 0) + 1
    return [
        {
            "id": _option_id(label),
            "label": _option_label(canonical, label),
            "predicate": label,
            "count": counts[label],
        }
        for label in sorted(counts, key=lambda item: (item.casefold(), item))
    ]


def document(key: str, rows: Iterable[Mapping[str, Any]], *, as_of: str) -> dict[str, Any]:
    """The route's body: ``{key, options: [{id, label, predicate, count}], as_of}``.

    ``as_of`` is passed in rather than read here so the memo stores the moment
    the numbers were taken, not the moment they were replayed.
    """

    canonical = option_key(key)
    return {
        "key": canonical,
        "options": enumerate_options(canonical, rows),
        "as_of": as_of,
    }


def moment() -> str:
    """The census moment, second resolution, UTC."""

    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class Entry:
    """One memoised census and the clock reading it was stored at."""

    document: Mapping[str, Any]
    stored_at: float


class OptionMemo:
    """One census per key, good for :data:`MEMO_TTL_S` seconds.

    Mirrors :class:`app.api.screens.ScanCache`'s expiry rule exactly: an entry
    answers only while ``clock() - stored_at < ttl_s``, and at ``ttl_s == 0``
    nothing is ever served. A hit is the **same document object** — a memo, not a
    rebuild — so ``as_of`` cannot drift between two reads of one census.
    """

    __slots__ = ("_clock", "_counts", "_entries", "_ttl")

    def __init__(
        self,
        *,
        ttl_s: float = MEMO_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_s < 0:
            raise ValueError(f"ttl_s must be zero or positive, got {ttl_s!r}")
        self._ttl = float(ttl_s)
        self._clock = clock
        self._entries: dict[str, Entry] = {}
        self._counts = {"hit": 0, "miss": 0}

    def stats(self) -> dict[str, int]:
        """How the census requests were answered (a counter, not a document)."""

        return dict(self._counts)

    def get(self, key: str) -> Mapping[str, Any] | None:
        """The fresh census for ``key``, or ``None``. Expired entries are dropped."""

        entry = self._entries.get(key)
        if entry is None:
            return None
        if self._clock() - entry.stored_at >= self._ttl:
            self._entries.pop(key, None)
            return None
        self._counts["hit"] += 1
        return entry.document

    def put(self, key: str, document: Mapping[str, Any]) -> Mapping[str, Any]:
        """Memoise ``document`` under ``key`` and answer it."""

        self._counts["miss"] += 1
        self._entries[key] = Entry(document=document, stored_at=self._clock())
        return document


def memo(
    state: Any,
    *,
    ttl_s: float = MEMO_TTL_S,
    clock: Callable[[], float] = time.monotonic,
) -> OptionMemo:
    """The app's census memo, created on first use (per app, not per process).

    The getattr/setattr pair has no ``await`` between them, so on one event loop
    it is atomic: two concurrent first requests cannot create two memos.
    """

    existing = getattr(state, MEMO_ATTRIBUTE, None)
    if isinstance(existing, OptionMemo):
        return existing
    created = OptionMemo(ttl_s=ttl_s, clock=clock)
    setattr(state, MEMO_ATTRIBUTE, created)
    return created
