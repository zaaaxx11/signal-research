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
with whatever the store had. The one documented exception is
:data:`SCAN_NULL_CELLS`: the columns the bulk scan writes as ``None`` for
**every** row because the provider does not publish them at all. There a ``None``
is not an observation, it is the scan saying it cannot answer — which is why
``ps`` and ``short_float`` were null on all 4,217 symbols while the depth store
held both.

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
from types import MappingProxyType
from typing import Any

from app.engine.catalog import CATALOG
from app.screen.columns import CATALOGUE_COLUMNS, CATALOGUE_KEY_SET

__all__ = ["SCAN_NULL_CELLS", "SCREEN_ALIASES", "overlay"]

#: depth key -> screen filter field, for measurements the two vocabularies spell
#: differently. The depth store speaks the catalog (``pe``, ``rsi``, ``beta``);
#: ``/screens/select`` filters on its own names (``pe_ttm``, ``rsi_14``,
#: ``beta_1y``). Without this table the cell lands under a name the filter never
#: reads — the store is full and the screen still answers nothing.
#:
#: This is **not** a second table: it is the projection of
#: :data:`app.screen.columns.CATALOGUE_COLUMNS` onto the catalogue keys the two
#: vocabularies spell differently. The overlay needs exactly that half (a store
#: cell → the column a filter reads) and nothing else; the full table is the one
#: place a name's meaning is stated, and ``test_one_table_is_the_only_mapping``
#: fails if this projection and that table ever disagree.
#:
#: ``average_volume`` maps to the **30-day** window, not the ten-day one: the
#: catalogue sources it from the provider's multi-week ``averageVolume``, and
#: reading ``avg_volume_10d`` for it answered a different question (the audit's
#: F6). ``avg_volume_10d`` stays its own cell under its own name.
SCREEN_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        key: field
        for key, field in CATALOGUE_COLUMNS.items()
        if key in CATALOGUE_KEY_SET and field != key
    }
)

#: Columns the bulk scan requests and then answers ``null`` for on **every** row,
#: because the provider has no such cell: forward P/E, P/S, payout ratio, short
#: float, enterprise value, cash per share and the two ownership shares. A ``None``
#: in one of these is the scan stating it cannot answer, not a measurement — so
#: the overlay treats it as a gap. Four of them (``ps``, ``short_float``,
#: ``enterprise_value``, ``cash_per_share``) were the audit's "null despite having
#: data" bugs: the store held the number and presence-only filling kept the null.
#:
#: Every other column keeps the strict rule: a ``None`` there is an observation
#: the row owns (a company with no earnings has no meaningful P/E, and the store's
#: copy must not overwrite that silence with a number).
SCAN_NULL_CELLS: frozenset[str] = frozenset(
    {
        "ps",
        "pe_forward",
        "payout_ratio",
        "short_float",
        "enterprise_value",
        "cash_per_share",
        "insider_own",
        "inst_own",
        # The parity scan copies the provider's ``ipo_date`` into
        # ``listing_date`` and answers null on every row (0 of 5,720 US, 0 of
        # 96,278 global when probed live), while the depth store measures the
        # real date on all 4,217 rows. Same shape as P/S and short float: the
        # None is the scan saying it has no cell, so the store's value is the
        # observation. ``ipodate`` filters this column.
        "listing_date",
    }
)


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


def _fillable(row: Mapping[str, Any], key: str) -> bool:
    """Is ``key`` a gap this store may fill?

    Absent is always a gap. Present-but-``None`` is a gap only for
    :data:`SCAN_NULL_CELLS`, the columns the scan answers ``null`` for on every
    row: there the ``None`` is "the provider has no such cell", and keeping it
    while the store holds the number is the bug that made P/S and short float
    permanently null.
    """

    if key not in row:
        return True
    return row[key] is None and key in SCAN_NULL_CELLS


def overlay(
    rows: Iterable[Mapping[str, Any]],
    depth: Mapping[str, Mapping[str, Any]],
    *,
    default_ticker_key: str = "ticker",
    aliases: Mapping[str, str] | None = None,
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
                if not _fillable(merged, key) or key not in entry:
                    continue
                value = entry[key]
                if value is not None:
                    merged[key] = value
            for source_key, screen_key in (aliases or {}).items():
                if not _fillable(merged, screen_key) or source_key not in entry:
                    continue
                value = entry[source_key]
                if value is not None:
                    merged[screen_key] = value
        merged_rows.append(merged)
    return merged_rows
