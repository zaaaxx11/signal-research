"""Per-cell provenance for fundamentals snapshots (P11F-A).

A snapshot already answers *who served this row*: ``source`` is one label and the
store keeps one row per symbol. That is enough to tell a 16-cell TradingView row
from a 22-cell Yahoo one, and it is not enough for the product this store feeds.
A screener that beats Finviz has to say **which source supplied each number**,
not which source won the row: two rows can carry the same P/E with no way to tell
a vendor's figure from an aggregator's, and a merged row — the whole point of a
second source — carries cells from several places at once, where a single label
is false about all but one of them.

The rules this module exists to keep:

* **One source per filled cell, and no source for an empty one.**
  ``snapshot.field_sources`` is a full-width 22-key mapping; a key whose value is
  ``None`` is an *unattributed* cell (a pre-0004 row, or a provider that did not
  say who it was), never a guess.
* **Exactness is an equality, not a subset.**
  ``{field for field, source in field_sources.items() if source}`` equals the
  filled cells. A filled cell with no record has an unknown origin, which
  :func:`field_source_counts` counts instead of hiding; a record with no cell
  describes a value the row does not carry, so it is dropped at construction.
* **The row label is a fallback, not the truth.** A row built from one source
  attributes its filled cells to that source — the same fact at cell
  granularity. ``multi`` is *not* a source: it says "several", so a merged row
  carries its per-cell map (which :func:`merge_snapshots` builds) instead.
* **Merge fills holes; it does not recount.** :data:`DEFAULT_PRIORITY` is the
  trust order — official filing, then vendor, then screen, then scrape. A
  lower-priority source may supply a cell the higher ones left ``None`` and may
  never replace a cell they filled: the first answer wins and the row stops being
  a lottery decided by whichever walk finished last.
* **``as_of`` is the newest contributing clock.** The merged row is dated by the
  freshest source that actually put a number in it; a source that contributed
  nothing does not date a row it is not part of.

Nothing here fetches, stores or migrates: it is the arithmetic over snapshots
that already exist, so a report and a merge cannot disagree about who said what.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from app.fundamentals.snapshot import (
    FUNDAMENTAL_FIELDS,
    MULTI_SOURCE,
    TTL_S,
    FundamentalsSnapshot,
)

#: The trust order a merge walks, highest first (02 §4.6's trust rungs in
#: practice): an official filing outranks a vendor feed, which outranks the
#: screen's own scan, which outranks a scrape. A source not named here sorts
#: after the named ones rather than being silently promoted.
DEFAULT_PRIORITY: tuple[str, ...] = ("sec_edgar", "yahoo", "finviz", "tradingview")


def source_label(value: Any) -> str | None:
    """A source name as it is stored: trimmed text, or ``None`` when it says nothing.

    Labels are compared verbatim (no case folding): ``Yahoo`` and ``yahoo`` are
    two spellings and this module does not decide they are one source. Producers
    spell their own labels — the adapters already agree on lower case.
    """

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def aggregate_source(sources: Iterable[Any]) -> str | None:
    """One label for a set of cell sources: the only one, ``multi``, or ``None``.

    ``None`` means "no cell carries a source", which is different from ``multi``
    ("more than one did"): the first is a gap, the second is a merged answer.
    """

    labels = {label for label in (source_label(value) for value in sources) if label}
    if not labels:
        return None
    if len(labels) == 1:
        return next(iter(labels))
    return MULTI_SOURCE


def filled_fields(snapshot: FundamentalsSnapshot) -> tuple[str, ...]:
    """The vocabulary cells that carry a value, in contract order."""

    return tuple(name for name in FUNDAMENTAL_FIELDS if snapshot.fields.get(name) is not None)


def cell_sources(snapshot: FundamentalsSnapshot) -> dict[str, str | None]:
    """The snapshot's full-width field→source map, re-keyed in contract order."""

    return {name: source_label(snapshot.field_sources.get(name)) for name in FUNDAMENTAL_FIELDS}


def unattributed_fields(snapshot: FundamentalsSnapshot) -> tuple[str, ...]:
    """Filled cells with **no** per-cell source: the gaps a report must show."""

    return tuple(
        name for name in filled_fields(snapshot) if source_label(snapshot.field_sources.get(name)) is None
    )


def priority_index(label: Any, priority: Sequence[str] = DEFAULT_PRIORITY) -> int:
    """Where ``label`` sits in ``priority``; unknown labels sort after every named one."""

    wanted = source_label(label)
    for index, name in enumerate(priority):
        if source_label(name) == wanted:
            return index
    return len(priority)


def by_priority(
    snapshots: Iterable[FundamentalsSnapshot],
    priority: Sequence[str] = DEFAULT_PRIORITY,
) -> list[FundamentalsSnapshot]:
    """``snapshots`` ordered by trust, stable on input order for equal sources.

    Unnamed sources land after the named ones, and a snapshot with no label at
    all lands last: a merge may still take a cell from it, but never ahead of a
    source the caller ranked.
    """

    items = list(snapshots)
    unnamed = len(priority) + 1

    def key(pair: tuple[int, FundamentalsSnapshot]) -> tuple[int, int]:
        position, snapshot = pair
        label = source_label(snapshot.source)
        return (priority_index(label, priority) if label else unnamed, position)

    return [snapshot for _, snapshot in sorted(enumerate(items), key=key)]


def merge_snapshots(
    snapshots: Iterable[FundamentalsSnapshot],
    priority: Sequence[str] = DEFAULT_PRIORITY,
    *,
    ttl_s: int = TTL_S,
) -> FundamentalsSnapshot | None:
    """Merge several sources for one symbol into one row, per-cell provenance kept.

    The rule is *fill, never overwrite*: sources are walked in ``priority`` order
    and a cell is taken only while it is still ``None``. So a Yahoo P/E survives a
    TradingView P/E, a backfilled ``pe_forward`` from a second source appears
    beside it, and a cell nobody served stays ``None`` — never ``0``, never a
    copy of a neighbouring cell.

    ``field_sources`` records who won each filled cell, so the merged row says
    ``source='multi'`` **and** which number came from where. ``as_of`` is the
    newest clock among the sources that actually contributed a cell; the fetch
    window restarts at the newest of those fetches (``fetched_at + ttl_s``).

    ``None`` comes back when nothing was merged — no snapshots, or every snapshot
    empty. A row of nulls would claim an answer nobody gave, which is the same
    rule the builder applies to an all-rungs-failed walk. Snapshots for different
    symbols are a caller error: one row is one symbol.
    """

    if ttl_s <= 0:
        raise ValueError("ttl_s must be positive")
    ordered = by_priority([snapshot for snapshot in snapshots if snapshot is not None], priority)
    if not ordered:
        return None
    symbol = ordered[0].symbol
    for snapshot in ordered:
        if snapshot.symbol != symbol:
            raise ValueError(
                f"cannot merge snapshots for {symbol!r} and {snapshot.symbol!r}: "
                "one merged row is one symbol"
            )

    cells: dict[str, Any] = {name: None for name in FUNDAMENTAL_FIELDS}
    sources: dict[str, str] = {}
    contributed: list[FundamentalsSnapshot] = []
    for snapshot in ordered:
        added = False
        for name in FUNDAMENTAL_FIELDS:
            if cells[name] is not None:
                continue  # a higher-priority source already answered this cell
            value = snapshot.fields.get(name)
            if value is None:
                continue
            cells[name] = value
            label = source_label(snapshot.field_sources.get(name))
            if label is not None:
                sources[name] = label
            added = True
        if added:
            contributed.append(snapshot)

    if not contributed:
        return None

    as_of = max(snapshot.as_of for snapshot in contributed)
    fetched_at = max(snapshot.fetched_at for snapshot in contributed)
    return FundamentalsSnapshot(
        symbol=symbol,
        instrument_id=contributed[0].instrument_id,
        fields={name: cells[name] for name in FUNDAMENTAL_FIELDS},
        source=aggregate_source(sources.values()),
        field_sources=sources,
        as_of=as_of,
        fetched_at=fetched_at,
        expires_at=_plus(fetched_at, ttl_s),
    )


def _plus(moment: datetime, ttl_s: int) -> datetime:
    return moment + timedelta(seconds=int(ttl_s))


def field_source_counts(snapshots: Iterable[FundamentalsSnapshot]) -> dict[str, Any]:
    """Cells per source, per field — the provenance block of a coverage report.

    ``unattributed`` is counted, never folded into a source or into ``multi``: a
    filled cell without a record is a gap, and a report that hides gaps is the
    claim this whole module exists to replace. Every one of the 22 fields gets a
    row, so a field's absence is visible as ``filled=0`` rather than missing from
    the table.
    """

    cells: dict[str, int] = {}
    per_field: dict[str, dict[str, Any]] = {
        name: {"filled": 0, "sources": {}, "unattributed": 0} for name in FUNDAMENTAL_FIELDS
    }
    unattributed = 0
    for snapshot in snapshots:
        for name in FUNDAMENTAL_FIELDS:
            if snapshot.fields.get(name) is None:
                continue
            row = per_field[name]
            row["filled"] += 1
            label = source_label(snapshot.field_sources.get(name))
            if label is None:
                row["unattributed"] += 1
                unattributed += 1
                continue
            row["sources"][label] = row["sources"].get(label, 0) + 1
            cells[label] = cells.get(label, 0) + 1
    for row in per_field.values():
        row["sources"] = dict(sorted(row["sources"].items()))
    return {
        "cells": dict(sorted(cells.items())),
        "attributed": sum(cells.values()),
        "unattributed": unattributed,
        "fields": per_field,
    }


__all__ = [
    "DEFAULT_PRIORITY",
    "aggregate_source",
    "by_priority",
    "cell_sources",
    "field_source_counts",
    "filled_fields",
    "merge_snapshots",
    "priority_index",
    "source_label",
    "unattributed_fields",
]
