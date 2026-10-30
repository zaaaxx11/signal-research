"""Depth overlay: the depth store fills a screen row's *missing* catalog cells.

Two stores answer two different questions. The screen row answers "what did the
scan say about this symbol"; the depth store (``data/depth.json``, shape
``{"as_of": ..., "symbols": {symbol: {cell: value}}}``) answers "what else do we
know about it" — 106 of the 116 catalog keys may appear there, gathered from
Yahoo, Finra, SEC and the quote hub on their own schedules. This module is the
one-way join between them, and it is deliberately narrow:

**Fill-missing-only.** The row keeps every cell it already carries, *presence*
being the test and not truthiness. ``pe=None`` on the row is a recorded unknown
that the row owns; ``pe`` absent is a gap. Filling on falsiness would be the
expensive bug: ``0.0``, ``False`` and ``""`` are observations, and a "fill when
falsy" overlay would overwrite a real zero P/E or a genuine "not optionable"
with whatever the store had.

**Catalog-only.** The store also carries bookkeeping — ``source_time``,
``source``, ``conflict``, per-field provenance — because the collector needs to
know where a cell came from and which source disagreed. A screen row is a
product surface: it speaks the catalog's 116 names and nothing else. A key the
catalog does not declare is dropped here rather than published, which is the
boundary between the store's vocabulary and the catalog's, not data loss.

**``None`` from the store is absence.** Adding the key holding ``None`` would
claim "observed, unknown", and a filter reading the key would believe the field
was measured. A depth cell of ``None`` adds nothing at all, and never a ``0``:
an unknown P/E is not a P/E of zero.

**Two spellings, one symbol.** The provider writes class shares with a dot
(``BRK.B``, ``BF.B``); the depth store keys them with a hyphen (``BRK-B``). The
row's ticker is trimmed and upper-cased, then looked up by its **exact**
spelling first and by the dotted↔hyphen swap second — exact first because a
store may legitimately hold both spellings for different lines, and guessing the
swap before trying the literal key would join the wrong symbol's numbers.

**Rows are copied, never touched.** Each output row is a new dict carrying every
original key unchanged (a mapping that is not a ``dict`` is copied into one);
the input is never mutated or aliased, duplicate rows are handled independently,
and the output has exactly the input's length and order. A row whose ticker is
missing, blank or not a string cannot name a symbol and comes back unchanged in
content; an empty store yields one equal copy per row.

The module is pure: no network, no file read, no clock, no cache. The catalog is
the only import, and it is a frozen lookup table built at process start.
``depth`` is the store's ``symbols`` table; a whole loaded store (with its
``as_of``) is accepted too, since that is what a caller reads from disk.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.engine.catalog import CATALOG

__all__ = ["overlay"]


def _spellings(symbol: str) -> tuple[str, ...]:
    """The store keys to try for one provider ticker, best first.

    ``BRK.B`` → ``("BRK.B", "BRK-B")`` and ``BRK-B`` → ``("BRK-B", "BRK.B")``.
    A ticker with neither separator has exactly one spelling, so a plain symbol
    costs one dict lookup instead of two.
    """

    candidates = [symbol]
    if "." in symbol:
        candidates.append(symbol.replace(".", "-"))
    if "-" in symbol:
        candidates.append(symbol.replace("-", "."))
    return tuple(dict.fromkeys(candidates))


def _symbols(depth: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any]:
    """The ``symbol → cells`` table, whether the caller passed it or the store.

    The store's documented shape nests the table under ``symbols`` next to
    ``as_of``; the function's parameter is that table itself. Unwrapping only
    when the ``as_of`` stamp is present keeps the two shapes apart without
    guessing: a symbols table's values are all cell mappings, and a ticker is
    never called ``SYMBOLS`` next to a store stamp.
    """

    if not isinstance(depth, Mapping):
        return {}
    if "as_of" in depth:
        nested = depth.get("symbols")
        if isinstance(nested, Mapping):
            return nested
    return depth


def _entry_for(ticker: Any, table: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The depth cells for one row's ticker, or ``None`` when it has none.

    The ticker must be text: a number or a list is not a symbol, and coercing it
    with ``str()`` would invent a lookup key (``123`` is not a ticker). A
    non-mapping entry is a corrupt cell, not an answer, so the other spelling is
    still tried.
    """

    if not isinstance(ticker, str):
        return None
    symbol = ticker.strip().upper()
    if not symbol:
        return None
    for spelling in _spellings(symbol):
        entry = table.get(spelling)
        if isinstance(entry, Mapping):
            return entry
    return None


def overlay(
    rows: Iterable[Mapping[str, Any]],
    depth: Mapping[str, Mapping[str, Any]],
    *,
    default_ticker_key: str = "ticker",
) -> list[dict[str, Any]]:
    """Return one new dict per row: the row's own cells plus the store's gaps.

    ``default_ticker_key`` names the row column that carries the symbol; it is a
    parameter because the universe row spells it ``ticker`` while a provider row
    spells it ``symbol``, and guessing a fallback would join the wrong column.

    Filled keys are appended in **catalog** order, so the output's key order
    depends on the frozen catalog and not on how the store happened to serialise
    its entry — two runs over the same data produce the same row shape.
    """

    table = _symbols(depth)
    merged_rows: list[dict[str, Any]] = []
    for row in rows:
        merged = dict(row)
        entry = _entry_for(row.get(default_ticker_key), table)
        if entry is not None:
            for spec in CATALOG:
                key = spec.key
                if key in merged or key not in entry:
                    continue
                value = entry[key]
                if value is not None:
                    merged[key] = value
        merged_rows.append(merged)
    return merged_rows
