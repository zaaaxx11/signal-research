"""Index membership for the ``idx`` filter: four lists, one stamped snapshot.

Why a vendored file
-------------------
No provider column carries index membership. ``idx`` was one of the 32 refused
Finviz keys precisely because a market-cap threshold is not a membership test.
The depth store does carry a comma string per symbol (``app.market.official``
derives it from SSGA, Nasdaq and iShares holdings), but it only covers the
symbols that store holds — and a membership filter must never answer "not in
the S&P 500" for a company nobody collected.

So the four lists are **vendored** as one stamped snapshot written by
``scripts/index-membership.py``. The runtime only reads it:

* :func:`load` — the snapshot, or an unavailable one (``as_of is None``);
* :func:`members` — one index's tickers, refused when there is no snapshot;
* :func:`indices_for` — one symbol's labels, in panel order;
* :func:`enrich_rows` — the ``index_members`` cell the ``idx`` bucket reads.

Two states, never confused
--------------------------
``index_members`` is ``None`` when membership is **unknown** (no snapshot) and a
list — possibly empty — when the snapshot was read and the symbol belongs to
none of the four. An empty list from an absent snapshot would read as "in no
index", which is exactly the fabricated answer this module exists to avoid.

Ticker spellings
----------------
The product writes ``BRK.B`` (Finviz) and some providers write ``BRK-B``; both
resolve to the same membership because :func:`_key` folds ``-`` and `` `` to
``.``. The snapshot is stored with whatever spelling its source used, and the
lookup is spelling-insensitive in both directions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

#: The four indices the Finviz ``idx`` control offers, in panel order.
INDICES: tuple[str, ...] = ("sp500", "ndx", "dji", "rut")

#: Human labels, copied from the probe (the bucket and the document publish them).
LABELS: Mapping[str, str] = MappingProxyType(
    {"sp500": "S&P 500", "ndx": "NASDAQ 100", "dji": "DJIA", "rut": "RUSSELL 2000"}
)

#: The plausible size of each list. A truncated fetch must fail the build instead
#: of shipping a 12-name S&P 500: the range is wide enough for reconstitution and
#: narrow enough that a parse that lost the table cannot pass.
PLAUSIBLE: Mapping[str, tuple[int, int]] = MappingProxyType(
    {
        "sp500": (480, 520),
        "ndx": (95, 110),
        "dji": (30, 30),
        "rut": (1700, 2200),
    }
)

#: Where the vendored snapshot lives (``pyproject.toml`` ships ``app.screen``'s
#: ``data/*.json``, so an installed wheel carries it too).
DATA_PATH = Path(__file__).with_name("data") / "index_membership.json"

#: The sentence a 422 / the descriptive document repeats when there is no file.
REASON = (
    "needs the vendored index-membership snapshot "
    "(app/screen/data/index_membership.json): the file is missing, unstamped or "
    "implausible, so index membership is unknown"
)


class SnapshotMissing(RuntimeError):
    """The membership snapshot is absent, so no membership answer exists."""


def _key(symbol: object) -> str | None:
    """``BRK-B``/``brk b`` → ``BRK.B``; a non-string or blank → ``None``."""

    if not isinstance(symbol, str):
        return None
    text = symbol.strip().upper()
    if not text:
        return None
    for separator in ("-", " "):
        text = text.replace(separator, ".")
    return text


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One stamped membership list. ``as_of is None`` means "not available"."""

    as_of: str | None = None
    source: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    indices: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: ``BRK.B`` → the indices it belongs to, built once in ``__post_init__``.
    _lookup: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        lookup: dict[str, list[str]] = {}
        for name in INDICES:
            for symbol in self.indices.get(name, ()):
                key = _key(symbol)
                if key is None:
                    continue
                names = lookup.setdefault(key, [])
                if name not in names:
                    names.append(name)
        ordered = {
            key: tuple(name for name in INDICES if name in names)
            for key, names in lookup.items()
        }
        object.__setattr__(self, "_lookup", MappingProxyType(ordered))

    @property
    def available(self) -> bool:
        """A stamped snapshot with at least one non-empty list."""

        return bool(self.as_of) and any(self.indices.get(name) for name in INDICES)

    @property
    def counts(self) -> dict[str, int]:
        """``{"sp500": 503, ...}`` — the lists' own lengths, never a claim.

        An unavailable snapshot answers ``{}``: four zeros would be a statement
        about membership, and there is no snapshot to make one from.
        """

        if not self.available:
            return {}
        return {name: len(self.indices.get(name, ())) for name in INDICES}

    def indices_for(self, symbol: object) -> tuple[str, ...]:
        key = _key(symbol)
        return self._lookup.get(key, ()) if key is not None else ()


def parse(document: object, *, today: date | None = None) -> Snapshot:
    """A snapshot document → :class:`Snapshot`; anything malformed is unavailable.

    A half-read file is not a smaller truth: one bad field makes the whole
    snapshot unavailable, because a membership list with a missing index would
    answer "not a member" for every company in it.
    """

    if not isinstance(document, Mapping):
        return Snapshot()
    stamp = document.get("as_of")
    if not isinstance(stamp, str) or not stamp.strip():
        return Snapshot()
    try:
        moment = date.fromisoformat(stamp.strip())
    except ValueError:
        return Snapshot()
    reference = today or date.today()
    if moment > reference:
        return Snapshot()
    raw = document.get("indices")
    if not isinstance(raw, Mapping):
        return Snapshot()
    indices: dict[str, tuple[str, ...]] = {}
    for name in INDICES:
        values = raw.get(name)
        if values is None:
            continue
        if not isinstance(values, (list, tuple)):
            return Snapshot()
        tickers: list[str] = []
        seen: set[str] = set()
        for value in values:
            key = _key(value)
            if key is None or key in seen:
                continue
            seen.add(key)
            tickers.append(key)
        if tickers:
            indices[name] = tuple(tickers)
    if not indices:
        return Snapshot()
    source = document.get("source")
    sources: dict[str, Mapping[str, Any]] = {}
    if isinstance(source, Mapping):
        for name, entry in source.items():
            if isinstance(name, str) and isinstance(entry, Mapping):
                sources[name] = MappingProxyType(dict(entry))
    return Snapshot(
        as_of=moment.isoformat(),
        source=MappingProxyType(sources),
        indices=MappingProxyType(indices),
    )


_CACHE: Snapshot | None = None


def load(path: Path | str | None = None) -> Snapshot:
    """The snapshot; ``path`` reads that file instead of the vendored one.

    A missing, unreadable or malformed file is an **unavailable** snapshot, never
    an exception at import: a checkout without the file must still serve the
    routes, with ``idx`` disabled and its reason published.
    """

    global _CACHE
    if path is not None:
        return _read(Path(path))
    if _CACHE is None:
        _CACHE = _read(DATA_PATH)
    return _CACHE


def _read(path: Path) -> Snapshot:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Snapshot()
    return parse(document)


def available() -> bool:
    """Is there a usable snapshot right now? (The one gate every caller asks.)"""

    return load().available


def members(index: object) -> tuple[str, ...]:
    """One index's tickers in the snapshot's order; refuses without a snapshot."""

    snapshot = load()
    if not snapshot.available:
        raise SnapshotMissing(
            f"no index-membership snapshot: cannot answer {index!r}. {REASON}"
        )
    name = str(index).strip().casefold() if isinstance(index, str) else ""
    if name not in INDICES:
        raise SnapshotMissing(
            f"unknown index {index!r}; expected one of {', '.join(INDICES)}"
        )
    return snapshot.indices.get(name, ())


def indices_for(symbol: object) -> tuple[str, ...]:
    """The labels ``symbol`` belongs to, in panel order (``()`` when none)."""

    return load().indices_for(symbol)


def enrich_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Copy ``index_members`` onto every row, **internal only**.

    ``None`` when the snapshot is absent (membership unknown), a list otherwise.
    The cell is never a catalog column and never projectable through ``columns``
    (the 116-column freeze): it exists for the ``idx`` bucket and nothing else.
    """

    snapshot = load()
    if not snapshot.available:
        return [{**row, "index_members": None} for row in rows]
    return [
        {**row, "index_members": list(snapshot.indices_for(row.get("symbol")))}
        for row in rows
    ]


__all__ = [
    "DATA_PATH",
    "INDICES",
    "LABELS",
    "PLAUSIBLE",
    "REASON",
    "Snapshot",
    "SnapshotMissing",
    "available",
    "enrich_rows",
    "indices_for",
    "load",
    "members",
    "parse",
]
