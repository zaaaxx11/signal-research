"""``GET /market/signals`` — the home page's thirteen tables, in one document.

Why one route and not thirteen
------------------------------
A home page shows the market's mood as a wall of small tables: gainers, losers,
new highs, new lows, overbought, oversold, unusual volume, most active, most
volatile, insider buying, insider selling. Asking the API once per table would
be eleven scans of 5,576 rows, eleven builds of the same universe and eleven
different populations if a scan happened to refresh between two of them. This
route answers them together: **one scan, one universe build, one pass over the
rows**, and a document every table reads from.

The population is the selector's, to the row
-------------------------------------------
:func:`app.screen.universe.build_universe` is the same function everywhere, but
its arguments are not: ``/market/breadth`` drops the cap floor entirely and
answers 5,678 rows, while ``POST /screens/select`` builds the stock tab with
``min_market_cap=0`` and answers 5,576. A signal table that counted 5,678 would
list names no screen can reproduce. This route copies the selector's call
argument for argument (the same floor sentinel, the same ``allow_any_subtype``,
the same ``PARITY_COLUMNS``) **and its scan key**, so a panel that opens the home
page and a screen pays one provider scan and the two cannot describe different
pages. ``tests/test_api_signals.py`` asserts ``universe_total`` against the
unfiltered selection, and the live run answers 5,576.

The rows carry depth, because two signals have no scan column
------------------------------------------------------------
``insider_trans`` does not exist in the bulk scan: its one source is the depth
store (Yahoo's net insider share change), which ``/screens/select`` merges with
:func:`app.screen.depth_overlay.overlay`. The same overlay runs here — one
batch read per universe, the same aliases, the same "a null in the scan is a
gap, a null from the store is nothing to add" rule — so the two insider tables
are computed from the data we actually have, and a deployment without a store
answers them **empty with a note**, never with a table of nulls. The
fundamentals and facts joins are *not* run: neither carries a cell any of these
thirteen signals reads, and paying two database reads to fill columns nobody
asks for is how a home page becomes slow.

The rules, and the edges they must not move
-------------------------------------------
Every rule is a predicate and an order, and the boundaries are the contract
because they are the only part a reader can check by hand:

* ``gainers`` — ``change_pct > 0``, descending;
* ``losers`` — ``change_pct < 0``, ascending (the direction *is* the signal);
* ``new_high`` — ``price >= 0.999 * week52_high`` (falling back to the
  ``high_52w`` spelling, which the column table resolves to the same row cell);
  a stock *at* its 52-week high qualifies, one 0.2 % below it does not. A level
  of zero or below is refused: multiplying a negative high would flip the
  comparison and admit a row that is nowhere near its high;
* ``new_low`` — ``price <= 1.001 * week52_low`` (``low_52w``), same guards;
* ``overbought`` — ``rsi_14 >= 70``; ``oversold`` — ``rsi_14 <= 30``;
* ``unusual_volume`` — ``volume / average_volume >= 3``, computed only when
  **both** are positive. A zero average is a guard, not a
  ``ZeroDivisionError``; a null average is unknown, not a ratio;
* ``most_active`` — every row with a reported ``volume``, descending. A
  reported ``0`` is an observation (the stock did not trade); a null is not;
* ``most_volatile`` — ``volatility`` descending, falling back to
  ``atr_14 / price`` **expressed in percent** (``* 100``) so the fallback is
  comparable to the primary cell, which the provider reports in percent. Two
  different units in one order is a table sorted by accident;
* ``insider_buying`` / ``insider_selling`` — **withdrawn**: ``insider_trans``
  is a vendor field whose own numbers cannot be read as a flow (TSLA 76.0,
  META 45.9, GME 8.7 — a mixture of ownership levels and junk), so a table
  claiming "insider buying" would publish an interpretation the data does not
  support. Both are ``available: false``; the Insider page reads real Form 4
  filings per company instead.
* ``upgrades`` / ``downgrades`` — **not computable**: no analyst-ratings source
  exists in this deployment. They are published ``available: false`` with the
  reason and no rows. Inventing them from ``recommendation`` would be inventing
  a revision nobody observed.

A metric that is ``null`` never qualifies for anything — it is unknown, not
zero — which is why a metric-less row belongs to the universe and to no table.

Order, ties, and the one pass
-----------------------------
The order is value descending, then **symbol ascending**, so two calls diff
cleanly and a client can render without re-sorting; the two signals whose value
is *falling* (losers, insider selling) ascend instead. ``new_high``/``new_low``/
``overbought``/``oversold`` name no order, so the house rule applies: their
metric, descending.

The single pass appends ``(value, symbol, name, price, change_pct, volume)``
tuples to one bucket per group and sorts the buckets at the end. It never sorts
a row in place, so the universe list a caller handed in comes back in the order
it went in (adversarial (d)) — the sort is on a copy of the numbers, not on the
shared page.

Memoised per generation, revalidated before any work
----------------------------------------------------
The whole document is built once per ``(region, per, scan generation)`` and kept
as its encoded bytes, so a warm read is a dict lookup and a ``Response`` — no
scan, no build, no pass. ``per`` is in the key because it is in the body and in
the tag: a memo keyed only on the region would answer a ``per=50`` request with
twenty rows and contradict its own validator. ``generation`` is the number
:meth:`app.api.screens.ScanCache.page` hands out with the page, so a re-scan
moves the key even when it returns the same rows.

The validator is derived from the answer's **inputs**, never from the body —
hashing the body could only happen after building it, which is exactly what a
revalidation must avoid::

    W/"sig-<sha256(region;per;scan=<gen>;total=<n>;vocab=<hash>)>"

``region``/``per`` name a different document; ``scan`` moves on every re-scan;
``total`` is insurance against a build-flag change that does not move the
generation; and ``vocab`` hashes the ``(id, label, availability, reason,
criterion, order)`` table, because a relabelled table changes the body while no
store moves. The tag is weak because the body carries ``as_of`` — the moment the
document was **built**, not the moment it was read — which the revision
deliberately leaves out: a strong tag would have to move with the clock and
would never match. A matching ``If-None-Match`` is a ``304`` with no body and no
build, and both answers carry ``Cache-Control: no-cache``.

``HEAD`` is registered as its own route rather than as a second method on the
GET one: FastAPI derives an operation id per route, and two methods on one
``api_route`` collide on it. It answers the same ``ETag``, ``Cache-Control`` and
``Content-Length`` with no body, and revalidates to ``304``.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.api.cache import not_modified
from app.api.discovery import _universe_family
from app.api.screens import _scan_cache, _scan_key
from app.auth import ApiError
from app.market.signal_aggregator import (
    is_earnings_today as _earnings_before_today,
    is_earnings_soon as _earnings_after_today,
    NEWS_UNAVAILABLE_REASON,
)
from app.screen.columns import value_for
from app.screen.depth_overlay import SCREEN_ALIASES, overlay
from app.screen.depth_source import current_depth
from app.screen.scanner import FULL_SCAN_ROWS, ScanError, fetch_scan
from app.screen.snapshot import SOURCE
from app.screen.universe import PARITY_COLUMNS, build_universe

router = APIRouter(tags=["discovery"])

#: What ``method`` states: one pass over the population the selector filters,
#: not a provider-side ranking and not a stored aggregate.
METHOD = "scan"

#: The default table length, and the clamp a caller's ``per`` is held to. Five
#: is the shortest table that still reads as a market; fifty is one panel of the
#: Finviz grid. Past either end the answer would stop being a *signal* table.
PER_DEFAULT = 20
PER_MIN = 5
PER_MAX = 50

#: How many documents the memo keeps. One entry is a few dozen kilobytes of
#: encoded body, and the bound stops an unusual ``per`` mix from growing it
#: without limit (a handful of generations × a handful of sizes).
MEMO_LIMIT = 64


# ---------------------------------------------------------------------------
# One row, read once
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Reading:
    """The cells every rule reads, taken off one universe row in one go.

    Two spellings are kept for the 52-week bounds because the catalogue maps
    ``week52_high`` and ``high_52w`` (``week52_low``/``low_52w``) to the same row
    field: the rule names both so the fallback is legible in the code, and
    :func:`app.screen.columns.value_for` — which owns the synonym table — answers
    whichever spelling the row actually carries.
    """

    symbol: str
    name: str | None
    price: float | None
    change_pct: float | None
    volume: float | None
    average_volume: float | None
    rsi: float | None
    week52_high: float | None
    high_52w: float | None
    week52_low: float | None
    low_52w: float | None
    volatility: float | None
    atr: float | None
    insider_trans: float | None
    earnings_date: Any | None


def _number(value: Any) -> float | None:
    """A cell as a finite float, or ``None`` when it is not a number.

    ``None`` is unknown, never zero: a stock with no P/E does not have a P/E of
    zero, and a stock with no 52-week high is not *at* one. ``NaN``/``inf`` are
    unknown too — they compare false in every direction and would make a
    predicate keep or drop a row at random.
    """

    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: Any) -> str | None:
    """A cell as trimmed text, or ``None`` when it is blank or not a string."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _read_row(row: Mapping[str, Any]) -> _Reading:
    """One universe row → the cells the rules need.

    Every cell is read through :func:`app.screen.columns.value_for`, never by a
    raw key: that function is the one owner of "``week52_high`` is the row's
    ``high_52w``" and "``average_volume`` is the 30-day window", and a raw
    ``row["high_52w"]`` here would be a second table that can drift from it.
    """

    return _Reading(
        symbol=str(value_for(row, "symbol") or "").strip(),
        name=_text(value_for(row, "name")),
        price=_number(value_for(row, "price")),
        change_pct=_number(value_for(row, "change_pct")),
        volume=_number(value_for(row, "volume")),
        average_volume=_number(value_for(row, "average_volume")),
        rsi=_number(value_for(row, "rsi_14")),
        week52_high=_number(value_for(row, "week52_high")),
        high_52w=_number(value_for(row, "high_52w")),
        week52_low=_number(value_for(row, "week52_low")),
        low_52w=_number(value_for(row, "low_52w")),
        volatility=_number(value_for(row, "volatility")),
        atr=_number(value_for(row, "atr_14")),
        insider_trans=_number(value_for(row, "insider_trans")),
        earnings_date=value_for(row, "earnings_date"),
    )


def _level(*candidates: float | None) -> float | None:
    """The first usable level among the spellings of one metric.

    A last-ditch ``or`` would turn a real ``0`` into ``None``; this keeps them
    apart, which matters because a level of zero is refused by the caller rather
    than silently replaced.
    """

    for candidate in candidates:
        if candidate is not None:
            return candidate
    return None


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SignalRule:
    """One table: what it measures, what it accepts, and how it is ordered.

    ``measure`` answers the sort value **only when every input the rule needs is
    present** — that is what makes the coverage count in an empty table's note
    honest ("0 of 5,576 rows carry insider_trans"), instead of counting rows
    that merely have a price. ``accepts`` then applies the threshold.
    """

    id: str
    label: str
    criterion: str
    metric: str
    measure: Callable[[_Reading], float | None]
    accepts: Callable[[_Reading, float], bool]
    order: str = "desc"
    available: bool = True
    reason: str | None = None


def _no_measure(_reading: _Reading) -> float | None:
    """Unavailable signals measure nothing: there is no source to read."""

    return None


def _never(_reading: _Reading, _value: float) -> bool:
    """Unavailable signals accept nothing: a table with no source has no rows."""

    return False


def _earnings_before_measure(reading: _Reading) -> float | None:
    """The row's earnings date as a sortable epoch float, when it is today.

    A row whose ``earnings_date`` is None is excluded; a date that parses
    but is not today returns None (not a match).  The returned float is the
    epoch seconds of the parsed date for deterministic ordering (descending
    = most recent first, which for a single day is a tie-break by symbol).
    """
    if reading.earnings_date is None:
        return None
    if not _earnings_before_today(reading.earnings_date):
        return None
    from datetime import datetime, timezone

    from app.market.signal_aggregator import earnings_date_for

    moment = earnings_date_for(reading.earnings_date)
    if moment is None:
        return None
    return datetime(moment.year, moment.month, moment.day, tzinfo=timezone.utc).timestamp()


def _earnings_before_accept(_reading: _Reading, _value: float) -> bool:
    return True  # already filtered in _earnings_before_measure


def _earnings_after_measure(reading: _Reading) -> float | None:
    """The row's earnings date as a sortable epoch float, when it is in the
    next two business days.  Today's dates are excluded (belongs in
    earnings_before)."""
    if reading.earnings_date is None:
        return None
    if _earnings_before_today(reading.earnings_date):
        return None
    if not _earnings_after_today(reading.earnings_date):
        return None
    from datetime import datetime, timezone

    from app.market.signal_aggregator import earnings_date_for

    moment = earnings_date_for(reading.earnings_date)
    if moment is None:
        return None
    return datetime(moment.year, moment.month, moment.day, tzinfo=timezone.utc).timestamp()


def _earnings_after_accept(_reading: _Reading, _value: float) -> bool:
    return True  # already filtered in _earnings_after_measure


def _change_pct(reading: _Reading) -> float | None:
    """The day's move, the measure behind both of the direction tables."""

    return reading.change_pct


def _risen(_reading: _Reading, value: float) -> bool:
    return value > 0


def _fallen(_reading: _Reading, value: float) -> bool:
    return value < 0


def _new_high_measure(reading: _Reading) -> float | None:
    """The price, when a usable 52-week high exists to compare it with."""

    price = reading.price
    high = _level(reading.week52_high, reading.high_52w)
    if price is None or high is None or price <= 0 or high <= 0:
        return None
    return price


def _new_high(_reading: _Reading, price: float) -> bool:
    high = _level(_reading.week52_high, _reading.high_52w)
    return high is not None and price >= high * 0.999


def _new_low_measure(reading: _Reading) -> float | None:
    price = reading.price
    low = _level(reading.week52_low, reading.low_52w)
    if price is None or low is None or price <= 0 or low <= 0:
        return None
    return price


def _new_low(_reading: _Reading, price: float) -> bool:
    low = _level(_reading.week52_low, _reading.low_52w)
    return low is not None and price <= low * 1.001


def _rsi(reading: _Reading) -> float | None:
    return reading.rsi


def _overbought(_reading: _Reading, value: float) -> bool:
    return value >= 70.0


def _oversold(_reading: _Reading, value: float) -> bool:
    return value <= 30.0


def _volume_ratio(reading: _Reading) -> float | None:
    """``volume / average_volume``, or ``None`` when the ratio is not defined.

    The denominator guard is the point: ``average_volume == 0`` is a store that
    knows nothing about the usual turnover, not a ratio of infinity, and it must
    not raise ``ZeroDivisionError`` on the request path.
    """

    volume = reading.volume
    average = reading.average_volume
    if volume is None or average is None or volume <= 0 or average <= 0:
        return None
    return volume / average


def _unusual(_reading: _Reading, value: float) -> bool:
    return value >= 3.0


def _volume(reading: _Reading) -> float | None:
    return reading.volume


def _always(_reading: _Reading, _value: float) -> bool:
    return True


def _volatility(reading: _Reading) -> float | None:
    """The volatility cell, or the ATR fallback expressed in the same unit.

    ``volatility`` (the provider's ``Volatility.W``) is a percent; ``atr_14`` is
    a price. ``atr_14 / price * 100`` is that price as a percent of the price,
    which is the one honest way to put the fallback in the same order as the
    primary cell. Without the ``* 100`` a row at 2 % ATR would sort below every
    row that reported 0.5 % volatility.
    """

    if reading.volatility is not None:
        return reading.volatility
    if reading.atr is None or reading.price is None or reading.price <= 0:
        return None
    return reading.atr / reading.price * 100.0


def _insider(reading: _Reading) -> float | None:
    return reading.insider_trans


def _bought(_reading: _Reading, value: float) -> bool:
    return value > 0


def _sold(_reading: _Reading, value: float) -> bool:
    return value < 0


#: The thirteen tables, in the order the document publishes them. The two at the
#: end are here to be *visible*: a client that renders the list gets "no analyst
#: ratings source" instead of a table that never appears and a question mark.
SIGNALS: tuple[SignalRule, ...] = (
    SignalRule(
        id="gainers",
        label="Top Gainers",
        criterion="change_pct > 0",
        metric="change_pct",
        measure=_change_pct,
        accepts=_risen,
    ),
    SignalRule(
        id="losers",
        label="Top Losers",
        criterion="change_pct < 0",
        metric="change_pct",
        measure=_change_pct,
        accepts=_fallen,
        order="asc",
    ),
    SignalRule(
        id="new_high",
        label="New 52-Week Highs",
        criterion="price >= 0.999 * week52_high",
        metric="week52_high (or high_52w)",
        measure=_new_high_measure,
        accepts=_new_high,
    ),
    SignalRule(
        id="new_low",
        label="New 52-Week Lows",
        criterion="price <= 1.001 * week52_low",
        metric="week52_low (or low_52w)",
        measure=_new_low_measure,
        accepts=_new_low,
    ),
    SignalRule(
        id="overbought",
        label="Overbought (RSI ≥ 70)",
        criterion="rsi_14 >= 70",
        metric="rsi_14",
        measure=_rsi,
        accepts=_overbought,
    ),
    SignalRule(
        id="oversold",
        label="Oversold (RSI ≤ 30)",
        criterion="rsi_14 <= 30",
        metric="rsi_14",
        measure=_rsi,
        accepts=_oversold,
    ),
    SignalRule(
        id="unusual_volume",
        label="Unusual Volume",
        criterion="volume / average_volume >= 3",
        metric="average_volume",
        measure=_volume_ratio,
        accepts=_unusual,
    ),
    SignalRule(
        id="most_active",
        label="Most Active",
        criterion="a reported volume, highest first",
        metric="volume",
        measure=_volume,
        accepts=_always,
    ),
    SignalRule(
        id="most_volatile",
        label="Most Volatile",
        criterion="volatility, highest first (atr_14 / price when volatility is null)",
        metric="volatility (or atr_14 and price)",
        measure=_volatility,
        accepts=_always,
    ),
    SignalRule(
        id="earnings_before",
        label="Earnings Today",
        criterion="earnings_date == today (New York time, with weekend fallback)",
        metric="earnings_date",
        measure=_earnings_before_measure,
        accepts=_earnings_before_accept,
    ),
    SignalRule(
        id="earnings_after",
        label="Earnings After",
        criterion="earnings_date in the next two New York business days",
        metric="earnings_date",
        measure=_earnings_after_measure,
        accepts=_earnings_after_accept,
    ),
    SignalRule(
        id="major_news",
        label="Major News",
        criterion="headline keyword match (merger, acquisition, FDA, lawsuit, …)",
        metric="headline text",
        measure=_no_measure,
        accepts=_never,
        available=False,
        reason=NEWS_UNAVAILABLE_REASON,
    ),
    SignalRule(
        id="insider_buying",
        label="Insider Buying",
        criterion="a company-level insider purchase, which this build reads from filings, not from insider_trans",
        metric="Form 4 purchases",
        measure=_no_measure,
        accepts=_never,
        available=False,
        reason="insider flow needs filings, not a vendor field",
    ),
    SignalRule(
        id="insider_selling",
        label="Insider Selling",
        criterion="a company-level insider sale, which this build reads from filings, not from insider_trans",
        metric="Form 4 sales",
        measure=_no_measure,
        accepts=_never,
        available=False,
        reason="insider flow needs filings, not a vendor field",
    ),
    SignalRule(
        id="upgrades",
        label="Upgrades",
        criterion="an analyst rating revision, which this deployment has no source for",
        metric="analyst ratings",
        measure=_no_measure,
        accepts=_never,
        available=False,
        reason="no analyst ratings source",
    ),
    SignalRule(
        id="downgrades",
        label="Downgrades",
        criterion="an analyst rating revision, which this deployment has no source for",
        metric="analyst ratings",
        measure=_no_measure,
        accepts=_never,
        available=False,
        reason="no analyst ratings source",
    ),
)

#: ``(id, label, available, reason, criterion, order)`` — the vocabulary half of
#: the ETag. Editing a label or a reason is a body change with no store behind
#: it, so the tag must move; a tag built from the data alone would 304 it away.
_VOCABULARY: tuple[tuple[str, str, bool, str | None, str, str], ...] = tuple(
    (rule.id, rule.label, rule.available, rule.reason, rule.criterion, rule.order)
    for rule in SIGNALS
)

#: The digest that rides in the tag as ``vocab=``.
_VOCAB_HASH: str = hashlib.sha256(
    json.dumps(_VOCABULARY, separators=(",", ":")).encode("utf-8")
).hexdigest()


# ---------------------------------------------------------------------------
# One pass over the universe
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GroupResult:
    """One table after the pass: its trimmed rows and the two counts a note needs.

    ``matched`` is how many rows qualified before the ``per`` cut (so the client
    and the tests can tell "twenty of twenty" from "twenty of nine hundred"),
    and ``coverage`` is how many rows carried the rule's inputs at all — the
    number that makes an empty table explain itself.
    """

    rule: SignalRule
    rows: tuple[dict[str, Any], ...]
    matched: int
    coverage: int


def _plain(value: float | None) -> float | int | None:
    """A number for the document: an integral one prints as an integer.

    ``volume: 1000`` reads like a volume; ``1000.0`` reads like a measurement.
    The value is unchanged and the ordering used the float.
    """

    if value is None:
        return None
    if value.is_integer() and abs(value) < 2**53:
        return int(value)
    return value


def _descending(entry: tuple[Any, ...]) -> tuple[float, str]:
    """Value descending, symbol ascending — the house order, as a sort key."""

    return (-entry[0], entry[1])


def _ascending(entry: tuple[Any, ...]) -> tuple[float, str]:
    """Value ascending, symbol ascending — for the two signals that fall."""

    return (entry[0], entry[1])


def collect(
    rows: Iterable[Mapping[str, Any]], *, per: int = PER_DEFAULT
) -> tuple[GroupResult, ...]:
    """Every group, from **one** pass over ``rows``.

    The pass reads each row once, evaluates the eleven computable rules, and
    appends the row's ordering tuple to each group's bucket. The buckets are
    sorted afterwards and trimmed to ``per``; ``rows`` itself is never sorted,
    indexed or mutated, so the universe a caller handed in comes back as it went
    in — the page is shared with the scan cache and with other readers.

    The unavailable rules take no part in the pass: they cannot match, so the
    loop skips them and their results keep ``matched == coverage == 0``.
    """

    limit = _clamp_per(per)
    buckets: dict[str, list[tuple[Any, ...]]] = {rule.id: [] for rule in SIGNALS}
    coverage: dict[str, int] = {rule.id: 0 for rule in SIGNALS}

    for row in rows:
        reading = _read_row(row)
        if not reading.symbol:
            continue
        for rule in SIGNALS:
            if not rule.available:
                continue
            value = rule.measure(reading)
            if value is None:
                continue
            coverage[rule.id] += 1
            if not rule.accepts(reading, value):
                continue
            buckets[rule.id].append(
                (
                    value,
                    reading.symbol,
                    reading.name,
                    reading.price,
                    reading.change_pct,
                    reading.volume,
                )
            )

    results: list[GroupResult] = []
    for rule in SIGNALS:
        entries = buckets[rule.id]
        entries.sort(key=_ascending if rule.order == "asc" else _descending)
        top = entries[:limit]
        results.append(
            GroupResult(
                rule=rule,
                rows=tuple(
                    {
                        "symbol": entry[1],
                        "name": entry[2],
                        "price": _plain(entry[3]),
                        "change_pct": _plain(entry[4]),
                        "volume": _plain(entry[5]),
                    }
                    for entry in top
                ),
                matched=len(entries),
                coverage=coverage[rule.id],
            )
        )
    return tuple(results)


def _clamp_per(per: int) -> int:
    """``per`` inside ``[PER_MIN, PER_MAX]``; the memo and the body agree on it."""

    return max(PER_MIN, min(PER_MAX, int(per)))


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


def _note(result: GroupResult, universe_total: int) -> str | None:
    """Why an empty table is empty — or ``None`` when it has rows.

    The sentence names the rule and the coverage, because "no rows matched" on
    its own cannot be told from "nobody measured this": a table of insider
    selling on a universe with no depth store is empty for a different reason
    than one where nobody sold.

    An unavailable table gets no note: its ``reason`` is the explanation, and two
    sentences saying the same thing in different words is how a document starts
    contradicting itself.
    """

    if not result.rule.available:
        return None
    if result.rows:
        return None
    return (
        f"no rows matched: {result.rule.criterion}; "
        f"{result.coverage} of {universe_total} rows carry {result.rule.metric}"
    )


def signals_document(
    results: Sequence[GroupResult],
    *,
    as_of: str,
    region: str,
    per: int,
    universe_total: int,
) -> dict[str, Any]:
    """The answer, from the pass's results. Pure: the same results print the same body."""

    groups = [
        {
            "id": result.rule.id,
            "label": result.rule.label,
            "available": result.rule.available,
            "reason": result.rule.reason,
            "note": _note(result, universe_total),
            "rows": [dict(row) for row in result.rows],
        }
        for result in results
    ]
    return {
        "as_of": as_of,
        "region": region,
        "universe_total": int(universe_total),
        "per": int(per),
        "groups": groups,
        "method": METHOD,
    }


def signals_revision(
    *,
    region: str,
    per: int,
    generation: int,
    universe_total: int,
    vocabulary: str = _VOCAB_HASH,
) -> str:
    """The document's identity, from its inputs — never from its body.

    ``as_of`` is deliberately absent: it is the build moment, and naming it would
    make two builds of one unchanged universe different entities, so no warm
    client could ever revalidate.
    """

    return (
        "v1"
        f";region={region}"
        f";per={int(per)}"
        f";scan={int(generation)}"
        f";total={int(universe_total)}"
        f";vocab={vocabulary}"
    )


def _etag(revision: str) -> str:
    """The quoted validator; the ``W/`` is added where the header is written."""

    return f'"sig-{hashlib.sha256(revision.encode("utf-8")).hexdigest()}"'


def _encode(document: Mapping[str, Any]) -> bytes:
    """The body as ``JSONResponse`` would encode it, once, for the memo."""

    return json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@dataclass(frozen=True, slots=True)
class SignalsAnswer:
    """One built document: its bytes, its validator and the facts behind them.

    Storing the encoded body is what makes the memo safe to share: bytes cannot
    be mutated by a caller the way a nested dict could, and a warm read hands
    them straight to the response without re-encoding 22 KB per poll.
    """

    body: bytes
    etag: str
    generation: int
    universe_total: int
    per: int


class SignalsMemo:
    """One document per ``(region, per, scan generation)``.

    The generation is part of the key, not a detail beside it: a memo keyed
    without it would answer yesterday's tables after a data refresh and would
    look perfectly healthy while doing so. Entries are inserted in order and the
    oldest is evicted past :data:`MEMO_LIMIT`, which bounds a query mix of a few
    regions and a few table lengths to a few dozen bodies.

    ``get``/``put`` deliberately contain no ``await``: on one event loop the
    check and the store are atomic, so two requests that race into a cold
    generation cannot both build the document.
    """

    __slots__ = ("_entries", "_limit")

    def __init__(self, *, limit: int = MEMO_LIMIT) -> None:
        self._limit = max(1, int(limit))
        self._entries: dict[tuple[str, int, int], SignalsAnswer] = {}

    def get(self, key: tuple[str, int, int]) -> SignalsAnswer | None:
        """The document for ``key``, or ``None`` when this generation is unbuilt."""

        return self._entries.get(key)

    def put(self, key: tuple[str, int, int], answer: SignalsAnswer) -> None:
        """Memoise ``answer``, evicting the oldest entry past the bound."""

        self._entries[key] = answer
        while len(self._entries) > self._limit:
            self._entries.pop(next(iter(self._entries)))


def _memo(request: Request) -> SignalsMemo:
    """The process's document memo, created on first use (one per app, not global)."""

    memo = getattr(request.app.state, "signals_memo", None)
    if isinstance(memo, SignalsMemo):
        return memo
    memo = SignalsMemo()
    request.app.state.signals_memo = memo
    return memo


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


def _per_value(raw: str) -> int:
    """``per`` as a clamped integer, or a ``422`` in the house envelope.

    The parameter is text, like every other parameter on this API, so a typo is
    refused with a sentence instead of being read as the default — and the
    refusal costs no scan.
    """

    token = raw.strip() if isinstance(raw, str) else ""
    try:
        value = int(token, 10)
    except ValueError:
        raise ApiError(
            status_code=422,
            error="invalid_per",
            message=(
                f"per must be an integer between {PER_MIN} and {PER_MAX}; got {raw!r}"
            ),
        ) from None
    return _clamp_per(value)


def _scan_key_for(region: str, family: str) -> tuple[str, ...]:
    """The scan key a **criteria selection** uses for the same tab.

    Shared verbatim with ``app.api.screens._scan_key`` and with
    ``/market/exchanges`` (same columns, same ``FULL_SCAN_ROWS``, same
    ``min_market_cap=None`` at the provider and ``subtype=None``), so this route
    and a selection read one cache entry: a page that opens both pays one
    provider scan, and the two cannot describe different pages.
    """

    return _scan_key(
        region=region,
        asset_class=family,
        source=SOURCE,
        columns=PARITY_COLUMNS,
        limit=FULL_SCAN_ROWS,
        min_market_cap=None,
        subtype=None,
    )


def _universe_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    region: str,
    family: str,
) -> list[dict[str, Any]]:
    """The selector's population, built with the selector's arguments.

    Copied from ``app.api.screens.select`` rather than approximated: the floor is
    ``0`` for stocks and ``None`` for the ETF tab, and ``allow_any_subtype`` is
    true because the selector addresses the whole stock market, not the common
    shares the browse page happens to show. Changing either would publish a
    population no screen can reproduce — the 5,678-vs-5,576 trap.
    """

    return build_universe(
        rows,
        region=region,
        min_market_cap=None if family == "etf" else 0,
        columns=PARITY_COLUMNS,
        allow_any_subtype=True,
        asset_class=family,
    )


def _with_depth(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``rows`` with the depth store's cells merged in, exactly as a screen sees them.

    ``insider_trans`` has no scan column at all, so this overlay is the only
    source the two insider tables have. It is the same function, aliases and
    ticker normalisation ``/screens/select`` uses — one owner — and an absent or
    empty store is not an error: every row simply keeps the cells it had.
    """

    snapshot = current_depth()
    symbols = getattr(snapshot, "symbols", None)
    if not symbols:
        return rows
    return overlay(rows, symbols, default_ticker_key="symbol", aliases=SCREEN_ALIASES)


def _answer(
    request: Request,
    answer: SignalsAnswer,
    *,
    scan: str,
    head: bool,
) -> Response:
    """The 200, or the 304 — decided from the answer's identity, not its body.

    Both answers carry the same ``ETag`` and ``Cache-Control: no-cache``; the
    ``304`` has no body at all. ``X-Scan-Cache`` is the same observable
    ``/screens/select`` emits (``hit``/``miss``/``shared``), so a client can tell
    a replayed scan from a fresh one without a second request.
    """

    headers = {
        "ETag": f"W/{answer.etag}",
        "Cache-Control": "no-cache",
        "X-Scan-Cache": scan,
    }
    if not_modified(request, answer.etag):
        return Response(status_code=304, headers=headers)
    if head:
        # A HEAD answers the question a monitor asks — is this current? — with
        # the headers and the length the GET would carry, and not a byte of the
        # document.
        return Response(
            status_code=200,
            headers={**headers, "Content-Length": str(len(answer.body))},
        )
    return Response(content=answer.body, media_type="application/json", headers=headers)


async def _signals_answer(
    request: Request, region: str, per_raw: str, *, head: bool
) -> Response:
    """The thirteen tables over the selector's universe, revalidatable.

    The parameters are validated before anything is scanned, so a refused query
    costs nothing and speaks the house envelope. The scan is the one a criteria
    selection uses; the universe is built with the selector's own arguments; the
    depth overlay is the selector's own; and the document is memoised per scan
    generation, so a warm read is a lookup and a revalidation is a comparison.
    """

    family = _universe_family(region, None)
    per = _per_value(per_raw)

    cache = _scan_cache(request)
    key = _scan_key_for(region, family)
    memo = _memo(request)

    # A revalidation is decided from the generation alone: `stamp` is read-only
    # (no scan, no build) and the memo holds this generation's document, so a
    # matching `If-None-Match` costs neither a universe nor a pass.
    stamp = cache.stamp(key)
    if stamp is not None:
        answer = memo.get((region, per, stamp))
        if answer is not None:
            return _answer(request, answer, scan="hit", head=head)

    try:
        page, scan, generation = await cache.page(
            key,
            lambda: fetch_scan(
                region,
                limit=FULL_SCAN_ROWS,
                columns=PARITY_COLUMNS,
                min_market_cap=None,
                subtype=None,
                asset_class=family,
            ),
        )
    except ScanError as exc:
        raise ApiError(
            status_code=503,
            error="scanner_unavailable",
            message=f"the scanner could not answer the signals request: {exc}",
        ) from exc

    memo_key = (region, per, generation)
    answer = memo.get(memo_key)
    if answer is None:
        universe = _with_depth(
            _universe_rows(page.rows, region=region, family=family)
        )
        document = signals_document(
            collect(universe, per=per),
            as_of=datetime.now(UTC).isoformat(timespec="seconds"),
            region=region,
            per=per,
            universe_total=len(universe),
        )
        answer = SignalsAnswer(
            body=_encode(document),
            etag=_etag(
                signals_revision(
                    region=region,
                    per=per,
                    generation=generation,
                    universe_total=len(universe),
                )
            ),
            generation=generation,
            universe_total=len(universe),
            per=per,
        )
        memo.put(memo_key, answer)
    return _answer(request, answer, scan=scan, head=head)


@router.get("/market/signals")
async def signals(request: Request, region: str = "us", per: str = "20") -> Response:
    """The home page's signal tables, revalidatable (GET)."""

    return await _signals_answer(request, region, per, head=False)


@router.head("/market/signals", include_in_schema=False)
async def signals_head(request: Request, region: str = "us", per: str = "20") -> Response:
    """The same headers as GET, no body: a monitor's question, not the document.

    Registered as its own route rather than as a second method on the GET one:
    FastAPI derives an operation id per route, and two methods on one route
    produce a duplicate id warning. It is ``include_in_schema=False`` because
    HEAD adds nothing to the contract a client reads.
    """

    return await _signals_answer(request, region, per, head=True)


__all__ = [
    "GroupResult",
    "MEMO_LIMIT",
    "METHOD",
    "PER_DEFAULT",
    "PER_MAX",
    "PER_MIN",
    "SIGNALS",
    "SignalRule",
    "SignalsAnswer",
    "SignalsMemo",
    "collect",
    "router",
    "signals",
    "signals_document",
    "signals_head",
    "signals_revision",
]
