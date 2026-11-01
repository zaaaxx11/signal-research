"""After-hours close and change: the provider's post-market print, one stamp.

``ah_close`` and ``ah_change`` were refused as *Elite-only*. The metric was never
missing — Yahoo's v7 quote carries ``postMarketPrice`` and
``postMarketChangePercent`` in the same response the bulk scan already asks for —
but nothing wrote the two cells onto a row, so no filter could read them.

This module is the writer. It reads one stamped snapshot
(``app/screen/data/after_hours.json``, produced by ``scripts/after-hours.py``
through :class:`app.market.yahoo_batch.YahooBatchQuoteSource`) and attaches two
**internal** cells per row:

* ``post_market_close`` — the last after-hours print, in price units;
* ``post_market_change`` — its change against the regular close, in percent.

Neither is a catalog column: the 116-column catalogue is frozen, and these are
read only by the ``ah_close:*`` / ``ah_change:*`` buckets.

Absent is absent
----------------
A symbol with no post-market print gets **no cell**, and a bucket matches a null
cell never (:mod:`app.screen.buckets`' rule for every family). That is the
honest answer during a regular session: the provider publishes no post-market
price while the market is open, so the filter matches nothing rather than
inventing a value from the last session. :func:`reason` says so in the same
words the document publishes, and :func:`coverage` measures it (symbols asked,
symbols priced) instead of describing it.

Why a snapshot and not a live fetch
-----------------------------------
A selection filters the whole universe before it pages, so the cells have to
exist for every row before the query runs — fetching thousands of quotes inside
a request would turn a 300 ms answer into a minute and make the answer depend on
how fast the provider felt that second. The snapshot is one bulk pass
(``MAX_BULK_BATCH`` = 400 symbols per request), stamped in UTC, and its
``session`` field records which session it was captured in so a regular-session
capture can never be published as an after-hours answer.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

#: The market's clock for the session words.
NEW_YORK = ZoneInfo("America/New_York")

#: The two cells this module writes. Internal, never catalog columns.
CLOSE_CELL = "post_market_close"
CHANGE_CELL = "post_market_change"

#: ``YYYY-MM-DD`` the snapshot was captured on, and the sessions it can name.
SESSIONS: tuple[str, ...] = ("pre", "regular", "post", "closed")

#: Open and close of the regular session, New York time.
OPEN_AT = time(9, 30)
CLOSE_AT = time(16, 0)
#: The extended session the app calls "after hours" runs to 20:00.
EXTENDED_CLOSE_AT = time(20, 0)
#: Before this the pre-market session has not started.
PRE_OPEN_AT = time(4, 0)

#: Where the vendored snapshot lives (``pyproject`` ships ``data/*.json``).
DATA_PATH = Path(__file__).with_name("data") / "after_hours.json"

#: A deployment that keeps its snapshot elsewhere names it here.
PATH_ENV = "ZTOCKZ_AFTER_HOURS"

#: The sentence a 422 / the document repeats when there is no usable snapshot.
REASON = (
    "needs the after-hours snapshot (app/screen/data/after_hours.json): the file "
    "is missing, unstamped or holds no post-market print, so no row carries an "
    "after-hours close — rebuild it with python scripts/after-hours.py"
)

#: The sentence for a snapshot captured while the regular session was open: the
#: provider publishes no post-market price then, so nothing can match.
SESSION_REASON = (
    "the after-hours snapshot was captured during the regular session "
    "({captured_at}), when the provider publishes no postMarketPrice: the filter "
    "matches nothing until the after-hours session prints (rebuild the snapshot "
    "after 16:00 New York)"
)


@dataclass(frozen=True, slots=True)
class Print:
    """One symbol's post-market print: the price and its change in percent."""

    close: float
    change: float | None = None


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One stamped capture: when, in which session, and what it carries."""

    as_of: str | None
    captured_at: str | None
    session: str | None
    rows: Mapping[str, Print]
    source: Mapping[str, Any]

    @property
    def priced(self) -> int:
        return len(self.rows)

    @property
    def available(self) -> bool:
        """Can ``ah_close``/``ah_change`` answer today?

        Three facts, all measured: a stamp, a session that is not the regular
        one, and at least one print. A regular-session capture is *not*
        available — the provider returned no after-hours price, and a filter that
        matched rows from yesterday's session would be dating them wrongly.
        """

        return (
            self.as_of is not None
            and self.session in ("post", "pre", "closed")
            and bool(self.rows)
        )

    def reason(self) -> str | None:
        """Why the snapshot cannot answer, or ``None`` when it can."""

        if self.as_of is None:
            return REASON
        if self.session == "regular":
            return SESSION_REASON.format(captured_at=self.captured_at or "unknown")
        if not self.rows:
            return (
                "the after-hours snapshot carries no post-market print "
                f"({self.source.get('symbols', 0)} symbol(s) asked on {self.as_of}, "
                "0 answered with postMarketPrice), so every row matches no bucket"
            )
        return None


def _key(symbol: object) -> str | None:
    """A ticker as the snapshot stores it: upper case, ``-``/space folded to ``.``.

    Yahoo spells a US class share ``BRK-B`` where the screen writes ``BRK.B``;
    folding both directions is what lets one lookup answer for either.
    """

    if not isinstance(symbol, str):
        return None
    text = symbol.strip().upper().replace("-", ".").replace(" ", ".")
    return text or None


def _moment(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        moment = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _number(raw: object) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str):
        try:
            value = float(raw.strip())
        except ValueError:
            return None
    else:
        return None
    return value if value == value and abs(value) != float("inf") else None


def session_at(moment: datetime) -> str:
    """Which session ``moment`` falls in, New York time (weekends are closed).

    Exchange holidays are not modelled: a holiday capture would be named
    ``regular`` and its empty snapshot would then be refused by
    :meth:`Snapshot.reason`, which is the safe direction (an empty answer is
    reported, never published as an after-hours value).
    """

    local = moment.astimezone(NEW_YORK)
    if local.weekday() >= 5:
        return "closed"
    clock = local.time()
    if PRE_OPEN_AT <= clock < OPEN_AT:
        return "pre"
    if OPEN_AT <= clock < CLOSE_AT:
        return "regular"
    if CLOSE_AT <= clock < EXTENDED_CLOSE_AT:
        return "post"
    return "closed"


def parse(document: object, *, path: Path | None = None) -> Snapshot:
    """One snapshot object → :class:`Snapshot`; a malformed one has no stamp."""

    if not isinstance(document, Mapping):
        return Snapshot(as_of=None, captured_at=None, session=None, rows=MappingProxyType({}), source=MappingProxyType({}))
    raw_rows = document.get("rows")
    rows: dict[str, Print] = {}
    if isinstance(raw_rows, Mapping):
        for symbol, entry in raw_rows.items():
            key = _key(symbol)
            if key is None or not isinstance(entry, Mapping):
                continue
            close = _number(entry.get("close"))
            if close is None or close <= 0:
                continue
            rows[key] = Print(close=close, change=_number(entry.get("change")))
    as_of = document.get("as_of")
    stamp = as_of.strip() if isinstance(as_of, str) and as_of.strip() else None
    captured = document.get("captured_at")
    session = document.get("session")
    source = document.get("source")
    return Snapshot(
        as_of=stamp,
        captured_at=captured.strip() if isinstance(captured, str) else None,
        session=session.strip() if isinstance(session, str) else None,
        rows=MappingProxyType(rows),
        source=MappingProxyType(dict(source)) if isinstance(source, Mapping) else MappingProxyType({}),
    )


#: path (str) → (mtime_ns, size, Snapshot). One ``stat`` per call, so an
#: operator can replace the snapshot without a restart.
_CACHE: dict[str, tuple[int, int, Snapshot]] = {}


def configured_path() -> Path:
    override = os.environ.get(PATH_ENV, "").strip()
    return Path(override) if override else DATA_PATH


def load(path: Path | str | None = None) -> Snapshot:
    """The snapshot, or an unstamped one (never an exception at read time)."""

    target = Path(path) if path is not None else configured_path()
    try:
        stat = target.stat()
    except OSError:
        return Snapshot(
            as_of=None,
            captured_at=None,
            session=None,
            rows=MappingProxyType({}),
            source=MappingProxyType({"path": str(target)}),
        )
    cached = _CACHE.get(str(target))
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Snapshot(
            as_of=None,
            captured_at=None,
            session=None,
            rows=MappingProxyType({}),
            source=MappingProxyType({"path": str(target)}),
        )
    snapshot = parse(document, path=target)
    _CACHE[str(target)] = (stat.st_mtime_ns, stat.st_size, snapshot)
    return snapshot


def clear() -> None:
    _CACHE.clear()


def available() -> bool:
    return load().available


def reason() -> str | None:
    """Why the two buckets cannot run, or ``None`` when they can."""

    return load().reason()


def coverage() -> dict[str, Any]:
    """The measured block the filter document publishes.

    ``symbols`` is what the collector asked, ``priced`` how many printed an
    after-hours price, and ``session`` which session it was captured in — three
    numbers a reader can check against the file instead of trusting a sentence.
    """

    snapshot = load()
    return {
        "available": snapshot.available,
        "as_of": snapshot.as_of,
        "captured_at": snapshot.captured_at,
        "session": snapshot.session,
        "symbols": snapshot.source.get("symbols", 0),
        "priced": snapshot.priced,
        "reason": snapshot.reason(),
    }


def print_for(symbol: object) -> Print | None:
    """The post-market print for one ticker, or ``None`` (unknown is not zero)."""

    key = _key(symbol)
    if key is None:
        return None
    return load().rows.get(key)


def enrich_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Attach the two cells where the snapshot has a print; copy, never mutate.

    A row whose symbol has no print keeps **no** cell, which is what makes
    ``ah_close:u5`` match nothing for it — the same "unknown is not zero" rule
    every other bucket follows.
    """

    snapshot = load()
    if not snapshot.rows:
        return [dict(row) for row in rows]
    enriched: list[dict[str, Any]] = []
    for row in rows:
        copy = dict(row)
        found = snapshot.rows.get(_key(row.get("symbol")) or "")
        if found is not None:
            copy[CLOSE_CELL] = found.close
            if found.change is not None:
                copy[CHANGE_CELL] = found.change
        enriched.append(copy)
    return enriched


__all__ = [
    "CHANGE_CELL",
    "CLOSE_CELL",
    "DATA_PATH",
    "NEW_YORK",
    "PATH_ENV",
    "Print",
    "REASON",
    "SESSION_REASON",
    "SESSIONS",
    "Snapshot",
    "available",
    "clear",
    "configured_path",
    "coverage",
    "enrich_rows",
    "load",
    "parse",
    "print_for",
    "reason",
    "session_at",
]
