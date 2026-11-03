"""The Descriptive group: Finviz's signals, and the document that publishes them.

Two halves, one module, because they answer one question — *what can this panel
ask for?*

Signals
-------
A Finviz *signal* is not a column and not a range: it is a **population plus its
own order**. ``top-gainers`` is every row whose ``change_pct`` is positive,
highest first; ``new-high`` is every row trading at its 52-week high, by price.
The home page already publishes thirteen such tables
(:mod:`app.api.signals`); this module gives the screener the same vocabulary
under Finviz's own ids, so ``POST /screens/select`` can answer one.

Twenty-seven ids, ten of them computable
----------------------------------------
Finviz offers fifteen market signals and twelve chart patterns. Ten are real
populations over cells the scan carries (``top-gainers`` … ``new-low``); the
other seventeen are published **unavailable with the metric they need**, because
a signal that cannot filter must be a ``422`` naming itself and never an empty
page — a caller reads an empty page as "no stock is overbought".

Nine of the ten mirror a home-page rule exactly (``SIGNALS[i].home`` names it).
The tenth, ``most-increased-volume``, has no home table: it is the rows trading
above their usual pace, ordered by relative volume. The mirroring is not a
convention, it is a test: ``tests/test_screen_signals.py`` compares every
mirrored signal's ``criterion``, ``metric`` and direction with the home rule, so
one edit to a threshold cannot move one surface without the other. That test
imports :mod:`app.api.signals`; **this module may not** — production code is the
screen layer, and importing the HTTP layer from it would invert the dependency.

The document
------------
``GET /screens/filters`` serves :func:`document`: Finviz's five filter tabs —
the 25 Descriptive controls this module opens with, then the 37 Fundamental, 20
Technical, 2 News and 1 ETF keys the catalogue has always carried — each with
its options, whether it can run **today**, why not when it cannot, and the
predicate every option compiles to. Fourteen of them compile
through :mod:`app.screen.buckets` (the predicate *is* the bucket's audit line),
one through the exchange value vocabulary, one through the order vocabulary, one
through the signals above, two are free text, and the rest are published
disabled with the metric they would need. A client renders the panel from this
answer and never hardcodes a Finviz label, token or range again — the F1 bug
class. The document is built from memory (no scan, no store), so the route may
hash its own body; availability is re-read on every call, so installing the
membership snapshot moves both the answer and the ETag.

Every control also states its ``tab`` and its ``disposition`` — ``scan_row``,
``join``, ``derived``, ``new_source`` or ``unavailable`` — so a client groups the
panel without a label map and can say *where a number comes from* without
guessing. :data:`DISPOSITION_BY_KEY` is the one place that provenance is written,
keyed by catalogue key, and :func:`_validate_dispositions` checks it against
:mod:`app.screen.filter_fields` at import: the ten keys the schema refuses are
exactly the ten the document calls unanswerable, and every key the schema can
run is stated as answerable. One fact, two published shapes, no third copy.

Ordering is an ORDER_BY name, not a closure
-------------------------------------------
Each signal declares ``sort`` — the :mod:`app.screen.rank` spelling of its own
measure, with a leading ``-`` for ascending (``-change_pct`` for the losers,
whose direction *is* the signal). ``select`` uses it as the default order when
the request names none, so the page is ranked by the measure the population was
selected on, while an explicit ``sort`` still wins. Keeping the order a name
instead of a callable means the signal ordering runs through the same
``rank_rows`` every other selection uses — one ranking implementation, not two.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable

from app.screen import (
    after_hours,
    buckets,
    choice_filters,
    entitlements,
    etf_holdings,
    index_membership,
    news_index,
    options,
    pattern_overlay,
)
from app.screen.filters import FILTER_CATALOG, VALUE_ALIASES


def _number(value: Any) -> float | None:
    """A cell as a finite float, or ``None``. A null is unknown, never ``0``."""

    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _cell(row: Mapping[str, Any], *names: str) -> float | None:
    """The first readable spelling of one cell (the catalogue keeps synonyms)."""

    for name in names:
        number = _number(row.get(name))
        if number is not None:
            return number
    return None


@dataclass(frozen=True, slots=True)
class Signal:
    """One Finviz signal: its population, its order, and why it may not run.

    ``criterion`` is the sentence the document publishes and the home page
    publishes too (for the nine mirrored rules); ``metric`` names the cell the
    population is about; ``sort`` is the ORDER_BY name of its own order.
    ``accepts`` is the predicate over one row — for an unavailable signal it
    answers nothing, because there is no metric to read.
    """

    id: str
    label: str
    criterion: str
    metric: str
    sort: str
    accepts: Callable[[Mapping[str, Any]], bool]
    available: bool = True
    reason: str | None = None
    #: The id of the home-page rule this signal mirrors, when one exists. Read
    #: by the drift test only; nothing in production consults it.
    home: str | None = None


def _never(_row: Mapping[str, Any]) -> bool:
    """Unavailable signals accept nothing: a population with no source is empty."""

    return False


def _change_pct(row: Mapping[str, Any]) -> float | None:
    return _cell(row, "change_pct")


def _rsi(row: Mapping[str, Any]) -> float | None:
    return _cell(row, "rsi_14", "rsi")


def _volume(row: Mapping[str, Any]) -> float | None:
    return _cell(row, "volume")


def _volatility(row: Mapping[str, Any]) -> float | None:
    """``volatility_w``, or ``atr_14 / price * 100`` when it is missing.

    The fallback is expressed in the primary cell's unit (percent), so a row
    read through it sorts among rows that reported volatility instead of below
    every one of them — two units in one order is a table sorted by accident.
    """

    direct = _cell(row, "volatility_w", "volatility")
    if direct is not None:
        return direct
    atr = _cell(row, "atr_14")
    price = _cell(row, "price")
    if atr is None or price is None or price <= 0:
        return None
    return atr / price * 100.0


def _volume_ratio(row: Mapping[str, Any]) -> float | None:
    """``volume / average_volume``; undefined when either side is not positive."""

    volume = _volume(row)
    average = _cell(row, "avg_volume_30d", "average_volume")
    if volume is None or average is None or volume <= 0 or average <= 0:
        return None
    return volume / average


def _relative_volume(row: Mapping[str, Any]) -> float | None:
    return _cell(row, "rel_volume_10d", "relative_volume")


def _level(row: Mapping[str, Any], *names: str) -> float | None:
    level = _cell(row, *names)
    return level if level is not None and level > 0 else None


@dataclass(frozen=True, slots=True)
class _Reading:
    """The cells the predicates below read, taken off one row in one pass."""

    price: float | None
    change_pct: float | None
    volume: float | None
    rsi: float | None
    high: float | None
    low: float | None
    volatility: float | None


def _read(row: Mapping[str, Any]) -> _Reading:
    return _Reading(
        price=_cell(row, "price"),
        change_pct=_cell(row, "change_pct"),
        volume=_volume(row),
        rsi=_rsi(row),
        high=_level(row, "high_52w", "week52_high"),
        low=_level(row, "low_52w", "week52_low"),
        volatility=_volatility(row),
    )


def _risen(row: Mapping[str, Any]) -> bool:
    value = _read(row).change_pct
    return value is not None and value > 0


def _fallen(row: Mapping[str, Any]) -> bool:
    value = _read(row).change_pct
    return value is not None and value < 0


def _traded(row: Mapping[str, Any]) -> bool:
    return _volume(row) is not None


def _volatile(row: Mapping[str, Any]) -> bool:
    return _volatility(row) is not None


def _surging(row: Mapping[str, Any]) -> bool:
    value = _relative_volume(row)
    return value is not None and value > 1.0


def _unusual(row: Mapping[str, Any]) -> bool:
    value = _volume_ratio(row)
    return value is not None and value >= 3.0


def _overbought(row: Mapping[str, Any]) -> bool:
    value = _read(row).rsi
    return value is not None and value >= 70.0


def _oversold(row: Mapping[str, Any]) -> bool:
    value = _read(row).rsi
    return value is not None and value <= 30.0


def _at_high(row: Mapping[str, Any]) -> bool:
    reading = _read(row)
    if reading.price is None or reading.high is None or reading.price <= 0:
        return False
    return reading.price >= reading.high * 0.999


def _at_low(row: Mapping[str, Any]) -> bool:
    reading = _read(row)
    if reading.price is None or reading.low is None or reading.price <= 0:
        return False
    return reading.price <= reading.low * 1.001


#: The reason the five market signals without a source cannot run, per signal.
_NO_ANALYST = (
    "needs an analyst ratings source: a rating *revision* is not the "
    "recommendation level the scan carries, and this deployment has none"
)
_NO_SESSION = (
    "needs an earnings session stamp (before market open / after market close): "
    "the scan carries the report day only, so a session signal would have to "
    "guess which half of the day it is"
)
_NO_NEWS = (
    "needs the news store: the scan returns no news timestamp or keywords, so "
    "'major news' cannot be told from a quiet day"
)
_NO_PATTERN = (
    "needs pattern-detection wiring: the twelve chart-pattern Signal entries are "
    "wired to app/screen/pattern_overlay.py and answer through detect_patterns() "
    "— what remains is the cache_dir passthrough from selection.py so the "
    "detection runs at scan time instead of waiting for an external trigger"
)

SIGNALS: tuple[Signal, ...] = (
    # -- the fifteen market signals -----------------------------------------
    Signal(
        id="top-gainers",
        label="Top Gainers",
        criterion="change_pct > 0",
        metric="change_pct",
        sort="change_pct",
        accepts=_risen,
        home="gainers",
    ),
    Signal(
        id="top-losers",
        label="Top Losers",
        criterion="change_pct < 0",
        metric="change_pct",
        sort="-change_pct",
        accepts=_fallen,
        home="losers",
    ),
    Signal(
        id="most-active",
        label="Most Active",
        criterion="a reported volume, highest first",
        metric="volume",
        sort="volume",
        accepts=_traded,
        home="most_active",
    ),
    Signal(
        id="most-volatile",
        label="Most Volatile",
        criterion="volatility, highest first (atr_14 / price when volatility is null)",
        metric="volatility (or atr_14 and price)",
        sort="volatility_w",
        accepts=_volatile,
        home="most_volatile",
    ),
    Signal(
        id="most-increased-volume",
        label="Most Increased Volume",
        criterion="rel_volume_10d > 1",
        metric="rel_volume_10d",
        sort="rel_volume_10d",
        accepts=_surging,
    ),
    Signal(
        id="unusual-volume",
        label="Unusual Volume",
        criterion="volume / average_volume >= 3",
        metric="average_volume",
        sort="rel_volume_10d",
        accepts=_unusual,
        home="unusual_volume",
    ),
    Signal(
        id="overbought",
        label="Overbought (RSI ≥ 70)",
        criterion="rsi_14 >= 70",
        metric="rsi_14",
        sort="rsi_14",
        accepts=_overbought,
        home="overbought",
    ),
    Signal(
        id="oversold",
        label="Oversold (RSI ≤ 30)",
        criterion="rsi_14 <= 30",
        metric="rsi_14",
        sort="rsi_14",
        accepts=_oversold,
        home="oversold",
    ),
    Signal(
        id="new-high",
        label="New 52-Week Highs",
        criterion="price >= 0.999 * week52_high",
        metric="week52_high (or high_52w)",
        sort="price",
        accepts=_at_high,
        home="new_high",
    ),
    Signal(
        id="new-low",
        label="New 52-Week Lows",
        criterion="price <= 1.001 * week52_low",
        metric="week52_low (or low_52w)",
        sort="price",
        accepts=_at_low,
        home="new_low",
    ),
    Signal(
        id="upgrades",
        label="Upgrades",
        criterion="an analyst rating revision, which this deployment has no source for",
        metric="analyst ratings",
        sort="change_pct",
        accepts=_never,
        available=False,
        reason=_NO_ANALYST,
    ),
    Signal(
        id="downgrades",
        label="Downgrades",
        criterion="an analyst rating revision, which this deployment has no source for",
        metric="analyst ratings",
        sort="-change_pct",
        accepts=_never,
        available=False,
        reason=_NO_ANALYST,
    ),
    Signal(
        id="earnbefore",
        label="Earnings Before Market Open",
        criterion="an earnings report before the open, which needs a session stamp",
        metric="earnings session",
        sort="change_pct",
        accepts=_never,
        available=False,
        reason=_NO_SESSION,
    ),
    Signal(
        id="earnafter",
        label="Earnings After Market Close",
        criterion="an earnings report after the close, which needs a session stamp",
        metric="earnings session",
        sort="change_pct",
        accepts=_never,
        available=False,
        reason=_NO_SESSION,
    ),
    Signal(
        id="majornews",
        label="Major News",
        criterion="a material headline, which needs the news store",
        metric="news",
        sort="change_pct",
        accepts=_never,
        available=False,
        reason=_NO_NEWS,
    ),
    # -- the twelve chart patterns ------------------------------------------
    Signal(
        id="wedgeup",
        label="Wedge Up",
        criterion="a wedge-up chart pattern",
        metric="chart pattern",
        sort="change_pct",
        accepts=lambda row, pat="wedgeup": pattern_overlay.lookup(row.get("symbol", ""), pat),
    ),
    Signal(
        id="wedge",
        label="Wedge",
        criterion="a wedge chart pattern",
        metric="chart pattern",
        sort="change_pct",
        accepts=lambda row, pat="wedge": pattern_overlay.lookup(row.get("symbol", ""), pat),
    ),
    Signal(
        id="wedgedown",
        label="Wedge Down",
        criterion="a wedge-down chart pattern",
        metric="chart pattern",
        sort="change_pct",
        accepts=lambda row, pat="wedgedown": pattern_overlay.lookup(row.get("symbol", ""), pat),
    ),
    Signal(
        id="tri-ascending",
        label="Triangle Ascending",
        criterion="an ascending-triangle chart pattern",
        metric="chart pattern",
        sort="change_pct",
        accepts=lambda row, pat="tri-ascending": pattern_overlay.lookup(row.get("symbol", ""), pat),
    ),
    Signal(
        id="tri-descending",
        label="Triangle Descending",
        criterion="a descending-triangle chart pattern",
        metric="chart pattern",
        sort="change_pct",
        accepts=lambda row, pat="tri-descending": pattern_overlay.lookup(row.get("symbol", ""), pat),
    ),
    Signal(
        id="channelup",
        label="Channel Up",
        criterion="an up-channel chart pattern",
        metric="chart pattern",
        sort="change_pct",
        accepts=lambda row, pat="channelup": pattern_overlay.lookup(row.get("symbol", ""), pat),
    ),
    Signal(
        id="channel",
        label="Channel",
        criterion="a channel chart pattern",
        metric="chart pattern",
        sort="change_pct",
        accepts=lambda row, pat="channel": pattern_overlay.lookup(row.get("symbol", ""), pat),
    ),
    Signal(
        id="channeldown",
        label="Channel Down",
        criterion="a down-channel chart pattern",
        metric="chart pattern",
        sort="change_pct",
        accepts=lambda row, pat="channeldown": pattern_overlay.lookup(row.get("symbol", ""), pat),
    ),
    Signal(
        id="doubletop",
        label="Double Top",
        criterion="a double-top chart pattern",
        metric="chart pattern",
        sort="change_pct",
        accepts=lambda row, pat="doubletop": pattern_overlay.lookup(row.get("symbol", ""), pat),
    ),
    Signal(
        id="doublebottom",
        label="Double Bottom",
        criterion="a double-bottom chart pattern",
        metric="chart pattern",
        sort="change_pct",
        accepts=lambda row, pat="doublebottom": pattern_overlay.lookup(row.get("symbol", ""), pat),
    ),
    Signal(
        id="head-shoulders",
        label="Head & Shoulders",
        criterion="a head-and-shoulders chart pattern",
        metric="chart pattern",
        sort="change_pct",
        accepts=lambda row, pat="head-shoulders": pattern_overlay.lookup(row.get("symbol", ""), pat),
    ),
    Signal(
        id="head-shoulders-inverse",
        label="Head & Shoulders Inverse",
        criterion="an inverse head-and-shoulders chart pattern",
        metric="chart pattern",
        sort="change_pct",
        accepts=lambda row, pat="head-shoulders-inverse": pattern_overlay.lookup(row.get("symbol", ""), pat),
    ),
)

#: id → signal (case-folded lookup, like the bucket ids).
BY_ID: Mapping[str, Signal] = MappingProxyType(
    {item.id.casefold(): item for item in SIGNALS}
)

#: The ten ids this build can run, in panel order.
AVAILABLE: tuple[str, ...] = tuple(item.id for item in SIGNALS if item.available)


class SignalError(ValueError):
    """A signal id that cannot be applied, carrying a stable machine code."""

    code = "invalid_signal"


class UnknownSignal(SignalError):
    """No signal has that id (a typo, or a column name)."""

    code = "unknown_signal"


class UnavailableSignal(SignalError):
    """The signal exists, but the metric its population needs is not loaded."""

    code = "unavailable_signal"


def signal(identifier: object) -> Signal:
    """The signal ``identifier`` names, or a :class:`SignalError`.

    The refusal is the whole point: a signal that cannot filter must say so with
    the metric it needs, because a silently-ignored one returns the unfiltered
    screen and a caller reads that as the signal's population.
    """

    if not isinstance(identifier, str) or not identifier.strip():
        raise UnknownSignal(
            identifier, f"signal must be a non-empty string, got {identifier!r}"
        )
    found = BY_ID.get(identifier.strip().casefold())
    if found is None:
        raise UnknownSignal(
            identifier,
            f"unknown signal {identifier!r}; expected one of {', '.join(item.id for item in SIGNALS)}",
        )
    if not found.available:
        raise UnavailableSignal(
            identifier,
            f"signal {found.id!r} ({found.label}) cannot be applied: it {found.reason}",
        )
    return found


def default_sort(item: Signal) -> str:
    """The order a signal applies when the request names no ``sort``."""

    return item.sort


# ===========================================================================
# The document: one control per Finviz control (plan D1)
# ===========================================================================
#
# ``GET /screens/filters`` answers this half of the module. A client renders the
# Descriptive group from it and never hardcodes a label, an option or a Finviz
# token again: the panel that asks for ``price:u5`` and the server that compiles
# it read the same object.
#
# Two populations, deliberately different:
#
# * a **static** vocabulary (index, price bands, earnings dates, themes,
#   signals, order-by, the five venue tokens, the twenty-one sector names) ships
#   inline — it is the provider's, it changes when the provider changes, and it is
#   small enough to send;
# * a **live** population (how many rows each sector and each venue holds) is
#   *referenced* through ``options_sources`` for the venue half, because a count
#   belongs to the scan and this document is built from memory. ``sec`` is the
#   exception: its labels **and** their row counts ship inline, because the
#   route reads the same ``/market/sectors`` population the sector table counts
#   and hands the measurement in (:func:`document`); ``options_source`` stays on
#   the control so a client that wants the census can still find it. A taxonomy
#   is a vocabulary, a count is a measurement, and both are honest when dated by
#   the same build.
#   ``exch`` is the two-facts shape: the five *tokens* are Finviz's and ship inline
#   with the MIC each compiles to, while *how many rows each venue holds* is live
#   and is published by ``/market/exchanges``. Industry and country stay free text
#   (``contains``) — Finviz's 146 industry and 62 country labels are *their*
#   taxonomy, while the row carries the provider's string, so a select of Finviz
#   labels would return empty pages — and their **provider-side** enumerations are
#   published by ``/screens/options/industry`` and ``/screens/options/country``
#   (:mod:`app.screen.options`), which is what a combobox offers.

#: The version a client keys its cache on. A shape change moves it. The id is
#: the product's, not a vendor's: a client holding ``screener-3`` is holding
#: *this* panel, and the name says nothing about where the idea came from.
#:
#: P0.1 deliberately did **not** move it: no control, key, field or option id was
#: added, removed or renamed — ``sec`` has always carried an ``options`` array and
#: ``industry``/``geo`` have always carried an ``options_source`` field — so what
#: changed is one control's *content* (the provider's twenty-one labels instead of
#: an empty list), which the ``ETag`` already dates. A bump would throw away every
#: cached document for an additive answer.
SCHEMA_VERSION = "screener-3"

#: The five catalogue tabs, in Finviz's panel order, and the group each becomes.
TAB_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "descriptive": "Descriptive",
        "fundamental": "Fundamental",
        "technical": "Technical",
        "news": "News",
        "etf": "ETF",
    }
)

#: Where the live sector labels come from — the treemap's own route, whose ids
#: and labels are the ones a *row* carries (the provider's 21-name taxonomy).
#: The labels (and their counts) now ship inline in ``sec.options``; this source
#: stays published so a client can still reach the full census document.
SECTORS_SOURCE = (
    "sector labels with counts (live): the ids /market/sectors publishes are "
    "the row's own `sector` strings, so `sec` compares them with `in`; the "
    "labels and their row counts also ship inline in `sec.options`, and "
    "/screens/options/sector enumerates the same census"
)
EXCHANGES_SOURCE = (
    "venue codes with counts (live): /market/exchanges counts the rows each MIC "
    "holds, beside the four codes `exch` accepts"
)
INDUSTRY_SOURCE = (
    "industry labels with counts (live): /screens/options/industry enumerates "
    "the provider's own `industry` cells over the current universe, one option "
    "per spelling with its row count"
)
COUNTRIES_SOURCE = (
    "country labels with counts (live): /screens/options/country enumerates the "
    "provider's own `country` cells over the current universe, one option per "
    "spelling with its row count"
)

#: The vendored Finviz labels, written by ``scripts/descriptive-options.py``.
DATA_PATH = Path(__file__).with_name("data") / "descriptive_options.json"

#: The 23 Descriptive keys the probe has (the catalogue's 22 plus
#: ``sh_dilution``, which no filter row can answer).
VENDORED_KEYS: tuple[str, ...] = (
    "exch",
    "idx",
    "sec",
    "industry",
    "geo",
    "cap",
    "fa_div",
    "sh_short",
    "an_recom",
    "sh_opt",
    "earningsdate",
    "sh_avgvol",
    "sh_relvol",
    "sh_curvol",
    "sh_trades",
    "sh_price",
    "targetprice",
    "ipodate",
    "sh_outstanding",
    "sh_float",
    "theme",
    "subtheme",
    "sh_dilution",
)

#: Why the controls that still have no source of their own cannot run.
#: ``theme``, ``subtheme`` and ``sh_trades`` take their sentence from
#: :data:`app.screen.filter_fields.UNAVAILABLE` — the same table that makes the
#: 422 — so the document and the refusal cannot describe different gaps.
#: ``sh_opt`` and ``ipodate`` used to live here; both are bucket controls now,
#: because the depth store really does carry ``optionable``/``shortable`` and
#: ``ipo_date`` (see :mod:`app.screen.buckets`).
_DILUTION_REASON = (
    "the dilution module (app.market.dilution.py) and bucket definitions exist; "
    "wiring the scan to compute dilution values with CIK resolution and a cache "
    "dir is the next step"
)


@dataclass(frozen=True, slots=True)
class Option:
    """One pickable value, and the predicate it compiles to.

    ``id`` is what a request sends in ``value``: a bucket id (``price:u5``), an
    exchange alias (``nyse``), a sort name, a signal id, a sector label. A
    **disabled** option keeps its namespaced id (``theme:ai…``) for a stable UI
    key even though no value can be sent, and publishes ``predicate: null`` — an
    option that cannot filter must not look like one that can.

    ``token`` is the source panel's own spelling of the option (``u5``,
    ``mega``, ``prevweek``), published so a client can round-trip a label it was
    given without inventing an id. It is data about a vocabulary, never a name
    for this product.
    """

    id: str
    label: str
    token: str | None
    predicate: str | None
    available: bool = True
    reason: str | None = None
    order: str | None = None
    #: The row count behind this option, when the builder was handed a live
    #: population (``sec`` is the one control that publishes it). ``None`` — the
    #: key is then **absent** from the published mapping — means "this option is
    #: a vocabulary entry, not a measurement", never "zero rows": a zero would be
    #: indistinguishable from a sector nobody is in.
    count: int | None = None
    #: The plan this option needs, from ``app/screen/data/entitlements.json``.
    #: Assigned by :func:`_published` (an option is gated with its control), and
    #: always published: a client must be able to draw the lock without asking a
    #: second endpoint.
    tier: str = "free"

    def as_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "token": self.token,
            "predicate": self.predicate,
            "available": self.available,
            "reason": self.reason,
            "order": self.order,
            "tier": self.tier,
        }
        if self.count is not None:
            row["count"] = self.count
        return row


@dataclass(frozen=True, slots=True)
class Control:
    """One control of one tab.

    ``control`` says what the UI draws (``buckets``/``select``/``text``/
    ``number``/``order``/``signal``), ``form`` whether one value or several,
    ``op`` the operator a request uses, ``status`` whether it can run
    (``available``), cannot (``unavailable``) or is deliberately out of scope
    (``out_of_scope``, rendered exactly like unavailable).

    ``tab`` and ``disposition`` are the client's grouping facts and are filled by
    :func:`document`, not by the builders: the same ``_bucket_control`` serves a
    descriptive control and a fundamental one, so the tab is a property of where
    the control is published, and the disposition is read from
    :data:`DISPOSITION_BY_KEY` (one owner per key).
    """

    id: str
    label: str
    key: str | None
    control: str
    form: str
    op: str | None
    options: tuple[Option, ...] = ()
    available: bool = True
    reason: str | None = None
    status: str = "available"
    unit: str | None = None
    hint: str | None = None
    note: str | None = None
    options_source: str | None = None
    predicate_template: str | None = None
    tab: str | None = None
    disposition: str | None = None
    #: The validated input pattern of a ``text`` control (``news_keywords``): the
    #: alphabet and length a client may check before it sends. ``None`` for every
    #: control whose values are options.
    pattern: str | None = None
    #: The plan this control needs, from ``app/screen/data/entitlements.json``.
    #: **Tier is not availability**: a premium control is built, answerable and
    #: published with a predicate; a free caller asking it is a 403, not a 422.
    tier: str = "free"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "key": self.key,
            "tab": self.tab,
            "disposition": self.disposition,
            "control": self.control,
            "form": self.form,
            "op": self.op,
            "status": self.status,
            "available": self.available,
            "tier": self.tier,
            "reason": self.reason,
            "unit": self.unit,
            "hint": self.hint,
            "note": self.note,
            "options_source": self.options_source,
            "predicate_template": self.predicate_template,
            "pattern": self.pattern,
            "options": [option.as_dict() for option in self.options],
        }


_VENDORED: dict[str, Any] | None = None


def vendored() -> dict[str, Any]:
    """The vendored probe extract, read once (``pyproject`` ships it)."""

    global _VENDORED
    if _VENDORED is None:
        document = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        controls = document.get("controls")
        if not isinstance(controls, dict):
            raise RuntimeError(f"{DATA_PATH} carries no 'controls' object")
        _VENDORED = controls
    return _VENDORED


def _probe(control_key: str) -> dict[str, Any]:
    spec = vendored().get(control_key)
    if not isinstance(spec, dict):  # pragma: no cover - _validate() gates it
        raise RuntimeError(f"the vendored options carry no {control_key!r} control")
    return spec


def _disabled_options(
    control_key: str, *, control: str, reason: str
) -> tuple[Option, ...]:
    """The probe's options, greyed out: kept, never run.

    Each id is namespaced (``theme:artificialintelligence``) so a disabled
    control keeps a stable key for the panel, and each publishes
    ``predicate: None``: a value nobody can send must not advertise a filter.
    """

    spec = vendored().get(control_key)
    if spec is not None:
        pairs = [(str(option["token"]), str(option["label"])) for option in spec["options"]]
    else:
        # The other four tabs' labels live in the full probe extract
        # (``filter_options.json``); a key with no vocabulary at all
        # (``etf_heldby``) honestly offers nothing.
        pairs = list(buckets.labels_for(control_key).items())
    return tuple(
        Option(
            id=f"{control}:{token}",
            label=label,
            token=token,
            predicate=None,
            available=False,
            reason=reason,
        )
        for token, label in pairs
    )


def _bucket_control(
    control: str,
    key: str,
    label: str,
    *,
    unit: str | None = None,
    note: str | None = None,
) -> Control:
    """A control whose every option is a named predicate in ``buckets``."""

    family = buckets.for_key(key)
    options = tuple(
        Option(
            id=item.id,
            label=item.label,
            token=item.finviz,
            predicate=item.describe if item.available else None,
            available=item.available,
            reason=item.reason,
        )
        for item in family
    )
    available = any(option.available for option in options)
    reason = None if available else next(
        (option.reason for option in options if option.reason), None
    )
    return Control(
        id=control,
        label=label,
        key=key,
        control="buckets",
        form="single",
        op="eq",
        options=options,
        available=available,
        reason=reason,
        status="available" if available else "unavailable",
        unit=unit,
        note=note,
    )


def _after_hours_note() -> str:
    """The measured sentence an after-hours control publishes as its ``note``.

    The reason field is for a control that *cannot* run; this one can, and still
    has to say what "matched" means: only a row the snapshot priced, and a
    capture made while the regular session was open holds no post-market print at
    all, so nothing matches until the extended session prints.
    """

    block = after_hours.coverage()
    return (
        f"reads the provider's post-market print from the after-hours snapshot "
        f"({block['priced']} of {block['symbols']} symbol(s) priced, captured "
        f"{block['captured_at']} in the {block['session']} session, as_of "
        f"{block['as_of']}). A symbol the snapshot did not price carries no cell "
        "and matches no bucket; a snapshot captured while the regular session was "
        "open has no postMarketPrice at all, so the filter matches nothing and "
        "says so in `reason`"
    )


_EXCHANGE_UNAVAILABLE = (
    "no TXSE venue code: the scan's exchange vocabulary is XASE/XCBO/XNAS/XNYS, "
    "so a TXSE filter could only return an empty page"
)


def _exchange_control() -> Control:
    """The five venue tokens, with TXSE disabled (no MIC for it in the scan)."""

    options: list[Option] = []
    for spec in _probe("exch")["options"]:
        token = str(spec["token"])
        mic = VALUE_ALIASES.get("exchange", {}).get(token)
        if mic is None:
            options.append(
                Option(
                    id=token,
                    label=str(spec["label"]),
                    token=token,
                    predicate=None,
                    available=False,
                    reason=_EXCHANGE_UNAVAILABLE,
                )
            )
        else:
            options.append(
                Option(
                    id=token,
                    label=str(spec["label"]),
                    token=token,
                    predicate=f"exchange == '{mic}'",
                )
            )
    return Control(
        id="exch",
        label="Exchange",
        key="exch",
        control="select",
        form="multi",
        op="in",
        options=tuple(options),
        note="multi-select: one `in` value list, resolved through the alias table",
    )


def _reason(key: str) -> str:
    from app.screen.filter_fields import UNAVAILABLE

    return UNAVAILABLE[key]


def _sector_options(counts: Mapping[str, int] | None = None) -> tuple[Option, ...]:
    """The provider's 21 sector labels, with their row counts when one was read.

    ``sec`` used to ship ``options: []`` and a source URL, which the client drew
    as a disabled control. The taxonomy is the provider's and it does not move
    between requests, so it ships **inline**; when the route has read the
    universe it hands the counts in as well, because a menu that says "Finance"
    and a menu that says "Finance · 1,559" are the same list with the measurement
    attached, and the measurement is what lets a reader choose.

    ``counts is None`` is the no-population answer (a deployment that cannot
    scan, or a caller building the vocabulary alone): the labels, no counts. It
    is never a count of zero — unknown and empty are different facts.

    Each option's ``predicate`` is ``sector eq <label>``, the value ``sec``
    compiles to. ``eq`` is exact on a text column, so the label is the provider's
    own spelling, byte for byte: ``"Finance"`` is a row and ``"finance"`` is an
    empty page. The id is that same label, because that is what a request sends.
    """

    if counts is None:
        labels: list[tuple[str, int | None]] = [
            (label, None) for label in options.SECTOR_LABELS
        ]
    else:
        labels = [
            (label, count)
            for label, count in sorted(
                counts.items(), key=lambda item: (item[0].casefold(), item[0])
            )
        ]
    return tuple(
        Option(
            id=label,
            label=label,
            token=None,
            predicate=f"sector eq {label}",
            count=count,
        )
        for label, count in labels
    )


def _order_control() -> Control:
    """Order by: every numeric catalog field, both directions."""

    from app.screen.rank import ORDER_BY

    options = tuple(
        Option(
            id=name,
            label=name,
            token=None,
            predicate=f"order by {name} desc",
            order="desc",
        )
        for name in ORDER_BY
    )
    return Control(
        id="order",
        label="Order by",
        key=None,
        control="order",
        form="single",
        op=None,
        options=options,
        hint=(
            "a leading '-' orders ascending (-pe_ttm); "
            "the legacy names market_cap, turnover and gainers still resolve"
        ),
        note="the order is the request's `criteria.sort`, not a filter",
    )


def _signal_control() -> Control:
    """The 27 signal ids: ten run, seventeen say what they would need."""

    options: list[Option] = []
    for item in SIGNALS:
        if item.available:
            column = item.sort[1:] if item.sort.startswith("-") else item.sort
            direction = "asc" if item.sort.startswith("-") else "desc"
            options.append(
                Option(
                    id=item.id,
                    label=item.label,
                    token=None,
                    predicate=f"{item.criterion}; ordered by {column}, {direction}",
                    order=direction,
                )
            )
        else:
            options.append(
                Option(
                    id=item.id,
                    label=item.label,
                    token=None,
                    predicate=None,
                    available=False,
                    reason=item.reason,
                )
            )
    return Control(
        id="signal",
        label="Signal",
        key=None,
        control="signal",
        form="single",
        op=None,
        options=tuple(options),
        note="the request's top-level `signal`; an unavailable one is a 422",
    )


def _text_hint(column: str, endpoint: str) -> str:
    """The sentence a free-text control publishes: what it matches, and the census.

    A text control is still the escape hatch — it matches a substring
    case-insensitively, so a reader can find ``Semiconductors`` without spelling
    it exactly — but it is no longer the *only* way in: ``endpoint`` publishes the
    exact spellings the current universe carries, with counts, which is what a
    combobox offers.
    """

    return (
        f"free text, case-insensitive substring over the row's `{column}` cell "
        f"(the provider's label; the source panel's own list is a different "
        f"taxonomy); the exact values and their row counts are enumerated at "
        f"{endpoint}"
    )


def _controls(
    sector_counts: Mapping[str, int] | None = None,
) -> tuple[Control, ...]:
    """The 25 controls, in Finviz's panel order, with today's availability.

    ``sector_counts`` is the live half of ``sec`` — label → rows, from the same
    reader ``/market/groups`` counts with. ``None`` is the no-population answer
    (see :func:`_sector_options`); every other control is built from memory.
    """

    return (
        _order_control(),
        _signal_control(),
        _exchange_control(),
        _bucket_control("idx", "idx", "Index"),
        Control(
            id="sec",
            label="Sector",
            key="sec",
            control="select",
            form="multi",
            op="in",
            options=_sector_options(sector_counts),
            options_source="/market/sectors",
            predicate_template="sector in [<label>, ...]",
            note=(
                "the 21 labels are the provider's own `sector` cells, published "
                "here with the rows behind each one; every option's predicate "
                "is `sector eq <label>` (a text column compares it exactly), "
                "and the same counts are enumerated at `/screens/options/sector`"
            ),
        ),
        Control(
            id="industry",
            label="Industry",
            key="industry",
            control="text",
            form="single",
            op="contains",
            options_source="/screens/options/industry",
            hint=_text_hint("industry", "/screens/options/industry"),
        ),
        Control(
            id="geo",
            label="Country",
            key="geo",
            control="text",
            form="single",
            op="contains",
            options_source="/screens/options/country",
            hint=_text_hint("country", "/screens/options/country"),
        ),
        _bucket_control("cap", "cap", "Market Cap.", unit="usd"),
        _bucket_control("fa_div", "fa_div", "Dividend Yield", unit="percent"),
        _bucket_control("sh_short", "sh_short", "Short Float", unit="percent"),
        _bucket_control("an_recom", "an_recom", "Analyst Recom."),
        _bucket_control("sh_opt", "sh_opt", "Option/Short"),
        _bucket_control("earn", "earningsdate", "Earnings Date"),
        _bucket_control("sh_avgvol", "sh_avgvol", "Average Volume", unit="shares"),
        _bucket_control("sh_relvol", "sh_relvol", "Relative Volume"),
        Control(
            id="sh_curvol",
            label="Current Volume",
            key="sh_curvol",
            control="number",
            form="single",
            op="gte",
            options=(),
            unit="shares",
            hint="a number, e.g. 1000000: the source panel offers only a Custom width here",
        ),
        Control(
            id="sh_trades",
            label="Trades",
            key="sh_trades",
            control="number",
            form="single",
            op="gte",
            # One owner for the sentence: `filter_fields.UNAVAILABLE` is what the
            # 422 repeats, so the document reads the same string instead of a
            # paraphrase that can drift (and did, when the tier moved out of it).
            options=_disabled_options(
                "sh_trades", control="sh_trades", reason=_reason("sh_trades")
            ),
            available=False,
            reason=_reason("sh_trades"),
            status="out_of_scope",
            unit="prints",
        ),
        _bucket_control("price", "sh_price", "Price $", unit="usd"),
        _bucket_control("targetprice", "targetprice", "Target Price", unit="usd"),
        _bucket_control("ipodate", "ipodate", "IPO Date"),
        _bucket_control("sh_outstanding", "sh_outstanding", "Shares Outstanding", unit="shares"),
        _bucket_control("sh_float", "sh_float", "Float", unit="shares"),
        Control(
            id="theme",
            label="Theme",
            key="theme",
            control="select",
            form="single",
            op="eq",
            options=_disabled_options("theme", control="theme", reason=_reason("theme")),
            available=False,
            reason=_reason("theme"),
            status="unavailable",
        ),
        Control(
            id="subtheme",
            label="Sub-theme",
            key="subtheme",
            control="select",
            form="single",
            op="eq",
            options=_disabled_options(
                "subtheme", control="subtheme", reason=_reason("subtheme")
            ),
            available=False,
            reason=_reason("subtheme"),
            status="unavailable",
        ),
        _bucket_control("sh_dilution", "sh_dilution", "Dilution Risk", unit="percent"),
    )


# ---------------------------------------------------------------------------
# The four filter tabs: one control per catalogue key (T24's document half)
# ---------------------------------------------------------------------------
#
# Each key answers exactly one control, in three shapes:
#
# * a **bucket** control — the options are bucket ids, and every option carries
#   the predicate the bucket compiles to (or the reason it cannot);
# * a **choice** control — the performance/volatility keys, whose token names a
#   window and a direction; the options carry the compiled `field op value`;
# * a **disabled** control — a key with no family and no column, drawn with the
#   probe's own options and the sentence that says what it needs.
#
# The tab → key table is read from `FILTER_CATALOG`, never retyped: a key that
# moved tabs on Finviz's panel moves here, and the 82-key catalogue stays the
# one source of what exists.

#: tab → the catalogue keys that belong to it, in panel order.
GROUPS_BY_TAB: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        tab: tuple(key for key, spec in FILTER_CATALOG.items() if spec.tab == tab)
        for tab in TAB_LABELS
    }
)

#: How a control is answered — the audit's five words, and the whole vocabulary.
#: A sixth would be a client-side invention, so the set is published here and
#: pinned by `tests/test_screen_descriptive_groups.py`.
DISPOSITIONS: frozenset[str] = frozenset(
    {"scan_row", "join", "derived", "new_source", "unavailable"}
)

#: The three that answer a value today. `new_source` names what *would* unblock a
#: control and `unavailable` says the data cannot answer it; neither runs.
ANSWERABLE_DISPOSITIONS: frozenset[str] = frozenset({"scan_row", "join", "derived"})

#: The one place a control's provenance is stated: catalogue key → disposition.
#:
#: The word follows the **code**, not the plan's prose, because the document is a
#: product surface and a client groups its controls by it:
#:
#: * ``scan_row`` — the row field the predicate reads is a column the bulk scan
#:   writes (:data:`app.screen.universe.PARITY_FIELDS` or
#:   :data:`app.screen.universe.UNIVERSE_COLUMNS`). ``fa_pe`` is here: ``pe_ttm``
#:   is a scan parity cell (52.78 % non-null), even though the *tokens* are
#:   compiled against depth spellings;
#: * ``join`` — the value arrives from the depth store
#:   (:data:`app.screen.universe.DEPTH_FIELDS`, the overlay's
#:   ``SCAN_NULL_CELLS``) or from one of the joins the row layer owns
#:   (:data:`app.screen.fundamentals.FUNDAMENTALS_ROW_FIELDS`,
#:   :data:`app.screen.facts.FACTS_ROW_FIELDS`), or is a membership list
#:   (``idx``, ``etf_heldby``) or a corpus (``news_keywords``, the after-hours
#:   snapshot's two cells). The word says *where the value comes from*; the
#:   **tier** says whether the caller may ask for it, and the two are published
#:   side by side because they are different questions;
#: * ``derived`` — a formula over cells the row already carries: the SMA and
#:   range *distances* (``(price / level − 1) × 100``), the Finviz 1..5
#:   recommendation score, change-from-open and the signals;
#: * ``new_source`` — nothing here measures it and the source that would is
#:   named in the refusal (a control whose data nobody collects is this or
#:   ``unavailable``; **tier is not a refusal** any more — a premium control the
#:   code answers states ``join``/``scan_row`` like any other);
#: * ``unavailable`` — the cells exist and cannot answer the control (a text
#:   spelling, a vendor ``"Yes"``/``"No"`` string, a withdrawn insider flow) or
#:   no source is named at all.
DISPOSITION_BY_KEY: Mapping[str, str] = MappingProxyType(
    {
        # -- descriptive: the row's own columns, one membership join ----------
        "exch": "scan_row",
        "idx": "join",
        "sec": "scan_row",
        "industry": "scan_row",
        "geo": "scan_row",
        "cap": "scan_row",
        "fa_div": "scan_row",
        "sh_short": "join",
        "an_recom": "derived",
        "sh_opt": "join",
        "earningsdate": "scan_row",
        "sh_avgvol": "scan_row",
        "sh_relvol": "scan_row",
        "sh_curvol": "scan_row",
        "sh_trades": "new_source",
        "sh_price": "scan_row",
        "targetprice": "scan_row",
        "ipodate": "join",
        "sh_outstanding": "join",
        "sh_float": "scan_row",
        "theme": "new_source",
        "subtheme": "new_source",
        # -- fundamental: valuation -------------------------------------------
        "fa_pe": "scan_row",
        "fa_fpe": "join",
        "fa_peg": "scan_row",
        "fa_ps": "join",
        "fa_pb": "scan_row",
        "fa_pc": "join",
        "fa_pfcf": "scan_row",
        "fa_fcfmargin": "join",
        "fa_evebitda": "scan_row",
        "fa_evsales": "join",
        # -- fundamental: growth (every cell is a depth growth rate) ----------
        "fa_divgrowth": "join",
        "fa_epsyoy": "join",
        "fa_epsyoy1": "join",
        "fa_epsqoq": "join",
        "fa_epsyoyttm": "join",
        "fa_eps3years": "join",
        "fa_eps5years": "join",
        "fa_estltgrowth": "join",
        "fa_salesqoq": "join",
        "fa_salesyoyttm": "join",
        "fa_sales3years": "join",
        "fa_sales5years": "join",
        # -- fundamental: quality, leverage, ownership ------------------------
        "fa_epsrev": "join",
        "fa_roa": "scan_row",
        "fa_roe": "scan_row",
        "fa_roi": "scan_row",
        "fa_curratio": "scan_row",
        "fa_quickratio": "scan_row",
        "fa_ltdebteq": "join",
        "fa_debteq": "scan_row",
        "fa_grossmargin": "scan_row",
        "fa_opermargin": "scan_row",
        "fa_netmargin": "scan_row",
        "fa_payoutratio": "join",
        "sh_insiderown": "join",
        "sh_insidertrans": "unavailable",
        "sh_instown": "join",
        "sh_insttrans": "join",
        # -- technical: price, oscillators ------------------------------------
        "ta_perf": "scan_row",
        "ta_perf2": "scan_row",
        "ta_volatility": "scan_row",
        "ta_rsi": "scan_row",
        "ta_gap": "scan_row",
        "ta_change": "scan_row",
        # `change_from_open` is one of the three derived columns, not a parity
        # cell: the scan sends close/change/gap and app.screen.derived owns the
        # formula (T21's prose called it a parity cell; the code says otherwise).
        "ta_changeopen": "derived",
        "ta_beta": "scan_row",
        "ta_averagetruerange": "scan_row",
        # -- technical: trend and range (a distance over two cells) -----------
        "ta_sma20": "derived",
        "ta_sma50": "derived",
        "ta_sma200": "derived",
        "ta_highlow50d": "derived",
        "ta_highlow52w": "derived",
        "ta_alltime": "derived",
        "ta_candlestick": "join",
        # -- technical: out of scope ------------------------------------------
        "ta_highlow20d": "new_source",
        "ta_pattern": "new_source",
        # The extended session: a real provider cell (`postMarketPrice`) copied
        # onto the row by app.screen.after_hours, and gated by tier rather than
        # refused — the reason is published under the control's `note`, not
        # `reason`, because the code answers it.
        "ah_close": "join",
        "ah_change": "join",
        # -- news --------------------------------------------------------------
        "news_date": "join",
        # A corpus join (app.screen.news_index), like `idx` is a membership join.
        "news_keywords": "join",
        # -- etf ---------------------------------------------------------------
        # A holdings join (app.screen.etf_holdings): the fund's own file.
        "etf_heldby": "join",
    }
)

#: The three controls with no catalogue key of their own: `order` sorts scan
#: columns, `signal` is a derived predicate, and `sh_dilution` is the one probe
#: control the 82-key catalogue does not have (the probe has 83).
DISPOSITION_BY_CONTROL: Mapping[str, str] = MappingProxyType(
    {
        "order": "scan_row",
        "signal": "derived",
        "sh_dilution": "derived",
    }
)


def disposition_for(control: Control) -> str:
    """The disposition of one control: its key's row, or its id's for the three."""

    if control.key is not None:
        found = DISPOSITION_BY_KEY.get(control.key)
        if found is not None:
            return found
    found = DISPOSITION_BY_CONTROL.get(control.id)
    if found is None:
        raise RuntimeError(
            f"control {control.id!r} (key {control.key!r}) has no disposition: "
            "state how its value is answered in DISPOSITION_BY_KEY"
        )
    return found


def _choice_control(key: str, label: str) -> Control:
    """A key whose option vocabulary is the filter: one predicate per token."""

    spec = choice_filters.CHOICES[key]
    compiled = choice_filters.choices_for(key)
    unsupported = choice_filters.unsupported_for(key)
    options: list[Option] = []
    for token in spec.tokens:
        if token in compiled:
            predicate = compiled[token]
            options.append(
                Option(
                    id=token,
                    label=buckets.labels_for(key)[token],
                    token=token,
                    predicate=f"{predicate.field} {predicate.op} {predicate.value}",
                )
            )
        else:
            options.append(
                Option(
                    id=token,
                    label=buckets.labels_for(key)[token],
                    token=token,
                    predicate=None,
                    available=False,
                    reason=unsupported[token],
                )
            )
    return Control(
        id=key,
        label=label,
        key=key,
        control="choice",
        form="single",
        op="eq",
        options=tuple(options),
        unit=None,
    )


def _disabled_tab_control(key: str, label: str) -> Control:
    """A key no layer can answer: keeps its Finviz options, publishes no predicate."""

    reason = _reason(key)
    return Control(
        id=key,
        label=label,
        key=key,
        control="select",
        form="single",
        op="eq",
        options=_disabled_options(key, control=key, reason=reason),
        available=False,
        reason=reason,
        status="unavailable",
    )


def _news_keywords_control() -> Control:
    """``news_keywords``: free text over the merged corpus, with its alphabet.

    The control is a ``text`` box, not a select: a keyword is a value, and the
    vocabulary is every headline the corpus holds. ``pattern`` is published so a
    client can refuse a bad character before it costs a round trip — the same
    regex the server enforces (:data:`app.screen.news_index.PATTERN`).
    """

    corpus = news_index.coverage()
    available = bool(corpus["available"])
    reason = None if available else (corpus["reason"] or news_index.REASON)
    note = (
        f"free text: letters, digits, spaces and hyphens, at most "
        f"{news_index.MAX_LENGTH} characters; matched case-insensitively, whole "
        f"word, every word of the query must appear in the headline. Corpus: "
        f"{corpus['headlines']} headline(s) over {corpus['symbols']} symbol(s), "
        f"as_of {corpus['as_of']}"
        if available
        else None
    )
    return Control(
        id="news_keywords",
        label="News Keywords",
        key="news_keywords",
        control="text",
        form="single",
        op="eq",
        available=available,
        reason=reason,
        status="available" if available else "unavailable",
        note=note,
        pattern=news_index.PATTERN,
    )


def _heldby_control() -> Control:
    """``etf_heldby``: one option per fund, each with its own availability.

    A fund whose file did not answer keeps its option — the control offers four
    tokens, not four-minus-whatever the last run fetched — with
    ``available: false`` and the **measured** reason (the HTTP status and URL from
    the snapshot's source block).
    """

    funds = etf_holdings.funds()
    options = tuple(
        Option(
            id=f"heldby:{symbol.lower()}",
            label=f"Held by {symbol} — {fund.label}",
            token=symbol.lower(),
            predicate=(
                f"symbol in the {symbol} holdings file ({len(fund.members)} names)"
                if fund.available
                else None
            ),
            available=fund.available,
            reason=fund.reason,
        )
        for symbol, fund in funds.items()
    )
    available = any(option.available for option in options)
    reason = None if available else (
        next((option.reason for option in options if option.reason), None)
        or etf_holdings.REASON
    )
    return Control(
        id="etf_heldby",
        label="Held By",
        key="etf_heldby",
        control="select",
        form="single",
        op="eq",
        options=options,
        available=available,
        reason=reason,
        status="available" if available else "unavailable",
        note=(
            "one fund per request (`heldby:<etf>`); the members are the fund's own "
            "published holdings, vendored per stamp — a fund nobody could fetch "
            "answers `unavailable`, never a guessed list"
        ),
    )


def _tab_control(key: str) -> Control:
    """One catalogue key as one control, in the strongest shape it supports."""

    spec = FILTER_CATALOG[key]
    label = spec.label.replace("\n", " ")
    if key in choice_filters.CHOICES:
        return _choice_control(key, label)
    if key == "news_keywords":
        return _news_keywords_control()
    if key == "etf_heldby":
        return _heldby_control()
    control = buckets.control_for(key)
    if control is not None and (buckets.for_key(key) or key in buckets.DYNAMIC_KEYS):
        note = _after_hours_note() if key in ("ah_close", "ah_change") else None
        return _bucket_control(control, key, label, note=note)
    return _disabled_tab_control(key, label)


def _tab_controls(tab: str) -> tuple[Control, ...]:
    """Every control of ``tab``, in the catalogue's panel order."""

    return tuple(_tab_control(key) for key in GROUPS_BY_TAB[tab])


def membership() -> dict[str, Any]:
    """The snapshot block: what ``idx`` can answer today, and its stamp."""

    snapshot = index_membership.load()
    return {
        "available": snapshot.available,
        "as_of": snapshot.as_of,
        "counts": snapshot.counts,
        "reason": None if snapshot.available else index_membership.REASON,
    }


def _feature_of(control: Control) -> str | None:
    """The gateable feature this control is, or ``None`` for an ungated one."""

    for candidate in (control.key, control.id):
        if candidate in entitlements.FEATURES:
            return candidate
    return None


def _published(control: Control, tab: str) -> dict[str, Any]:
    """One control's published form: which tab, how it is answered, its tier.

    Filled here rather than in every builder because the builders are shared
    across tabs — the same `_bucket_control` serves `cap` (descriptive) and `pe`
    (fundamental) — so the tab is a fact about *where* the control is published,
    and `disposition_for` keeps the provenance in one table.

    The **tier** is read from ``app/screen/data/entitlements.json`` here, once per
    build, and copied onto every option: one edit to that file moves the control
    and all of its values, which is what makes a premium→free flip a data change.
    """

    feature = _feature_of(control)
    tier = (
        entitlements.tier_for(feature)
        if feature is not None
        else entitlements.default_tier()
    )
    row = replace(control, tab=tab, disposition=disposition_for(control)).as_dict()
    # Stamped on the published mapping rather than rebuilt per option: 2,218
    # dataclass copies per build was most of the document's cost, and the mapping
    # is the only shape a client ever sees.
    row["tier"] = tier
    for option in row["options"]:
        option["tier"] = tier
    return row


def document(sector_counts: Mapping[str, int] | None = None) -> dict[str, Any]:
    """The whole filter document — five tabs.

    Built from the catalogue, the bucket registry, the vendored labels and the
    membership snapshot; nothing here reads a store or a provider, which is why
    the route may hash its own body. Availability is re-read on every call
    (``buckets.for_key`` asks the snapshot), so installing or removing the
    membership file moves the answer — and therefore the tag — without a restart.

    ``sector_counts`` is the one **measured** input: label → rows for the live
    sector population, read by the route with the same
    :func:`app.api.sectors.tally_sectors` that ``/market/groups`` publishes. It
    is passed in rather than read here so this module stays pure and importable
    without a provider; ``None`` publishes the taxonomy without counts (an
    outage answers a usable menu, never a fabricated zero).

    Every control carries its ``tab`` and its ``disposition``: a client groups
    the panel by the first and renders the second as "where this number comes
    from" without hardcoding a label-to-tab map that would drift the first time
    Finviz moves a control.
    """

    return {
        "schema_version": SCHEMA_VERSION,
        "groups": [
            {
                "id": tab,
                "label": TAB_LABELS[tab],
                "controls": [
                    _published(control, tab)
                    for control in (
                        _controls(sector_counts)
                        if tab == "descriptive"
                        else _tab_controls(tab)
                    )
                ],
            }
            for tab in TAB_LABELS
        ],
        "membership": membership(),
        "after_hours": after_hours.coverage(),
        "news_corpus": news_index.coverage(),
        "etf_holdings": etf_holdings.coverage(),
        "entitlements": entitlements.describe(),
        "options_sources": {
            "/market/sectors": SECTORS_SOURCE,
            "/market/exchanges": EXCHANGES_SOURCE,
            "/screens/options/industry": INDUSTRY_SOURCE,
            "/screens/options/country": COUNTRIES_SOURCE,
        },
    }


_CONTROL_ORDER: tuple[str, ...] = (
    "order",
    "signal",
    "exch",
    "idx",
    "sec",
    "industry",
    "geo",
    "cap",
    "fa_div",
    "sh_short",
    "an_recom",
    "sh_opt",
    "earn",
    "sh_avgvol",
    "sh_relvol",
    "sh_curvol",
    "sh_trades",
    "price",
    "targetprice",
    "ipodate",
    "sh_outstanding",
    "sh_float",
    "theme",
    "subtheme",
    "sh_dilution",
)


#: The controls that publish their **taxonomy** inline and still point at a live
#: source for their **counts**. One key is on the list, and it is the exception the
#: rule below states: the provider's sector names are stable (so shipping them
#: costs nothing and unblocks the menu), while how many rows each holds moves with
#: every scan (so a copy would be a number nobody can date).
INLINE_TAXONOMY: frozenset[str] = frozenset({"sec"})


def _validate_document() -> None:
    """Fail at import: 25 controls, one owner per fact, no empty promise.

    Every check here is one a reader would otherwise have to make by hand: the
    control list is the frozen one, each control names a real catalogue key, a
    disabled control states why and publishes no predicate, a bucket control
    covers its family exactly, the sector control publishes the provider's
    twenty-one labels with the label *as* the predicate, and the vendored file
    carries the 23 descriptive keys — no more (a key the probe grew) and no fewer
    (a key it lost).
    """

    from app.screen.rank import ORDER_BY

    controls = _controls()
    if tuple(control.id for control in controls) != _CONTROL_ORDER:
        raise RuntimeError(
            "the descriptive group does not name the 25 controls in panel order: "
            f"{[control.id for control in controls]}"
        )

    vendored_controls = vendored()
    if set(vendored_controls) != set(VENDORED_KEYS):
        raise RuntimeError(
            "app/screen/data/descriptive_options.json is stale: "
            f"missing={sorted(set(VENDORED_KEYS) - set(vendored_controls))}, "
            f"extra={sorted(set(vendored_controls) - set(VENDORED_KEYS))}. "
            "Regenerate it with python scripts/descriptive-options.py"
        )

    for control in controls:
        if control.key is not None and control.key not in vendored_controls:
            raise RuntimeError(
                f"descriptive control {control.id!r} names key {control.key!r}, "
                "which the vendored vocabulary does not have"
            )
        if control.available:
            if control.reason is not None:
                raise RuntimeError(f"available control {control.id!r} carries a reason")
            if control.op is None and control.control not in ("order", "signal"):
                raise RuntimeError(f"control {control.id!r} names no operator")
            if (
                not control.options
                and not control.options_source
                and control.control not in ("text", "number")
            ):
                raise RuntimeError(
                    f"available control {control.id!r} offers nothing to send"
                )
        else:
            if not (control.reason and len(control.reason) > 20):
                raise RuntimeError(f"unavailable control {control.id!r} has no reason")
            if control.status == "available":
                raise RuntimeError(
                    f"control {control.id!r} is unavailable but publishes status 'available'"
                )
            for option in control.options:
                if option.predicate is not None:
                    raise RuntimeError(
                        f"disabled option {option.id!r} publishes a predicate it cannot run"
                    )
        if control.control == "buckets":
            family = buckets.for_key(control.key)
            if len(control.options) != len(family):
                raise RuntimeError(
                    f"bucket control {control.id!r} publishes {len(control.options)} "
                    f"options for {len(family)} buckets"
                )
        if control.options_source is not None and control.options:
            if control.id not in INLINE_TAXONOMY:
                raise RuntimeError(
                    f"control {control.id!r} copies a live population *and* points at it"
                )

    by_id = {control.id: control for control in controls}
    if tuple(option.id for option in by_id["order"].options) != ORDER_BY:
        raise RuntimeError("the order control is not the catalogue's numeric fields")
    if tuple(option.id for option in by_id["signal"].options) != tuple(
        item.id for item in SIGNALS
    ):
        raise RuntimeError("the signal control is not the signal vocabulary")

    sector = by_id["sec"]
    if tuple(option.label for option in sector.options) != options.SECTOR_LABELS:
        raise RuntimeError(
            "the sector control does not publish the provider's 21 labels in "
            f"panel order: {[option.label for option in sector.options]}"
        )
    for option in sector.options:
        if option.id != option.label or option.predicate != f"sector eq {option.label}":
            raise RuntimeError(
                f"sector option {option.id!r} publishes id/predicate "
                f"{option.id!r}/{option.predicate!r}: the label IS the wire value, "
                "byte for byte, and it compiles to `sector eq <label>`"
            )
        if option.count is not None:
            raise RuntimeError(
                f"sector option {option.id!r} carries a count the vocabulary "
                "builder never measured: counts are handed in by the route, "
                "never invented here"
            )
        if not option.available or option.token is not None:
            raise RuntimeError(
                f"sector option {option.id!r} is disabled or claims a Finviz token: "
                "the provider's label is a value, not a vendor token"
            )


_validate_document()


def _validate_groups() -> None:
    """Fail at import: the five tabs cover the 82-key catalogue exactly once.

    The Descriptive group is the 25 published controls above; the four filter
    tabs are **one control per catalogue key**, so their counts are the
    catalogue's own (37/20/2/1) and a key that belonged to no tab would be a
    filter no panel can draw.
    """

    expected = {tab: len(keys) for tab, keys in GROUPS_BY_TAB.items()}
    if expected != {
        "descriptive": 22,
        "fundamental": 38,
        "technical": 20,
        "news": 2,
        "etf": 1,
    }:
        raise RuntimeError(f"the catalogue's tab split moved: {expected}")
    covered = [
        key
        for tab in GROUPS_BY_TAB
        if tab != "descriptive"
        for key in GROUPS_BY_TAB[tab]
    ]
    expected_keys = set(FILTER_CATALOG) - set(GROUPS_BY_TAB["descriptive"])
    if sorted(covered) != sorted(expected_keys):
        raise RuntimeError(
            "the four filter tabs do not cover the catalogue's 60 non-descriptive "
            f"keys exactly once: missing={sorted(expected_keys - set(covered))}, "
            f"extra={sorted(set(covered) - expected_keys)}"
        )

    for tab in GROUPS_BY_TAB:
        if tab == "descriptive":
            continue
        for control in _tab_controls(tab):
            if control.key is None:
                raise RuntimeError(f"filter control {control.id!r} names no key")
            if control.available:
                if not control.options and control.control not in ("text", "number"):
                    raise RuntimeError(f"available control {control.id!r} offers nothing")
                for option in control.options:
                    if option.available and not option.predicate:
                        raise RuntimeError(
                            f"available option {option.id!r} publishes no predicate"
                        )
                    if not option.available and not option.reason:
                        raise RuntimeError(f"disabled option {option.id!r} has no reason")
            else:
                if not (control.reason and len(control.reason) > 20):
                    raise RuntimeError(f"unavailable control {control.id!r} has no reason")
                if any(option.predicate for option in control.options):
                    raise RuntimeError(
                        f"unavailable control {control.id!r} publishes a predicate"
                    )
            if control.control == "buckets":
                family = buckets.for_key(control.key)
                if len(control.options) != len(family):
                    raise RuntimeError(
                        f"bucket control {control.id!r} publishes {len(control.options)} "
                        f"options for {len(family)} buckets"
                    )


_validate_groups()


#: The controls whose availability is a **snapshot** fact rather than a code
#: fact: index membership, the after-hours capture, the news corpus and the ETF
#: holdings file. They are answerable by design (disposition ``join``) and a
#: build that ships without one of the files is not a broken disposition — it is
#: a deployment with that snapshot missing, which the document reports as
#: ``available: false`` with the snapshot's own sentence. Every other control's
#: availability is its code's and is checked exactly.
SNAPSHOT_KEYS: frozenset[str] = frozenset(
    {"idx", "ah_close", "ah_change", "news_keywords", "etf_heldby"}
)


def _validate_dispositions() -> None:
    """Fail at import: one disposition per control, agreeing with the schema.

    Three claims are checked, because each one has already been a bug somewhere
    else in this codebase:

    * every catalogue key states exactly one disposition, and every stated word
      is one of the five;
    * the document's disposition is the schema's — a key `filter_fields` refuses
      is never published as answerable, and a key it can run is never published
      as `new_source` or `unavailable` (one fact, two surfaces);
    * a control's `available` flag is its disposition's: `unavailable` and
      `new_source` controls do not run, and an answerable control does.
    """

    from app.screen import filter_fields

    missing = sorted(set(FILTER_CATALOG) - set(DISPOSITION_BY_KEY))
    extra = sorted(set(DISPOSITION_BY_KEY) - set(FILTER_CATALOG))
    if missing or extra:
        raise RuntimeError(
            "every catalogue key needs exactly one disposition: "
            f"missing={missing}, extra={extra}"
        )
    unknown = sorted(set(DISPOSITION_BY_KEY.values()) - DISPOSITIONS)
    if unknown:
        raise RuntimeError(f"dispositions outside the five-word vocabulary: {unknown}")

    refused = set(filter_fields.unavailable_keys())
    for key, word in DISPOSITION_BY_KEY.items():
        answerable = word in ANSWERABLE_DISPOSITIONS
        if answerable and key in refused:
            raise RuntimeError(
                f"key {key!r} is published as {word!r} but filter_fields refuses it"
            )
        if not answerable and key not in refused:
            raise RuntimeError(
                f"key {key!r} is published as {word!r} but filter_fields can run it"
            )

    for tab in TAB_LABELS:
        controls = _controls() if tab == "descriptive" else _tab_controls(tab)
        for control in controls:
            word = disposition_for(control)
            answerable = word in ANSWERABLE_DISPOSITIONS
            if control.available is not answerable:
                snapshot_missing = (
                    not control.available
                    and answerable
                    and control.key in SNAPSHOT_KEYS
                    and bool(control.reason)
                )
                if not snapshot_missing:
                    raise RuntimeError(
                        f"control {control.id!r} is available={control.available} but "
                        f"states disposition {word!r}"
                    )
            if not control.available and not control.reason:
                raise RuntimeError(f"disabled control {control.id!r} has no reason")


_validate_dispositions()


def _validate_tiers() -> None:
    """Fail at import: every published control and option states one tier.

    Four claims, each one a way this could rot:

    * the tier word is one of :data:`app.screen.entitlements.TIERS` — a typo in
      the JSON is a boot error, never a control nobody can pass;
    * an option's tier is its control's (an option is not sold separately);
    * every gateable feature is a control in the document, so the config cannot
      gate a key the panel does not draw;
    * a **premium control the code answers is still `available`** — the whole
      point of splitting tier from availability. Only ``sh_trades`` is both
      premium and refused, and its refusal names the missing feed.
    """

    published: dict[str, dict[str, Any]] = {}
    for tab in TAB_LABELS:
        controls = _controls() if tab == "descriptive" else _tab_controls(tab)
        for control in controls:
            row = _published(control, tab)
            published[row["id"]] = row
            if row["tier"] not in entitlements.TIERS:
                raise RuntimeError(
                    f"control {row['id']!r} publishes tier {row['tier']!r}, which is "
                    f"not one of {entitlements.TIERS}"
                )
            for option in row["options"]:
                if option["tier"] != row["tier"]:
                    raise RuntimeError(
                        f"option {option['id']!r} publishes tier {option['tier']!r} "
                        f"but its control {row['id']!r} is {row['tier']!r}"
                    )
    by_key = {row["key"]: row for row in published.values() if row["key"] is not None}
    missing = sorted(feature for feature in entitlements.FEATURES if feature not in by_key)
    if missing:
        raise RuntimeError(
            f"entitlements gates {missing}, but the document has no such control"
        )
    for feature in entitlements.FEATURES:
        row = by_key[feature]
        tier = entitlements.tier_for(feature)
        if row["tier"] != tier:
            raise RuntimeError(
                f"control {row['id']!r} publishes tier {row['tier']!r} but the "
                f"entitlements table says {tier!r}"
            )
        if (
            tier == entitlements.PREMIUM
            and row["disposition"] not in ANSWERABLE_DISPOSITIONS
            and feature != "sh_trades"
        ):
            raise RuntimeError(
                f"premium control {row['id']!r} is refused (disposition "
                f"{row['disposition']!r}): a tier gate must not hide code that "
                "answers — only a sourceless metric is unavailable"
            )


_validate_tiers()


def _validate() -> None:
    """Fail at import: the vocabulary is 27, ten run, and every order is a name."""

    from app.screen.rank import ORDER_BY

    if len(SIGNALS) != 27:
        raise RuntimeError(f"the signal vocabulary is 27 ids, got {len(SIGNALS)}")
    if len(BY_ID) != len(SIGNALS):
        raise RuntimeError("two signals share an id")
    for item in SIGNALS:
        if item.available and item.reason is not None:
            raise RuntimeError(f"available signal {item.id!r} carries a reason")
        if not item.available and not (item.reason and len(item.reason) > 20):
            raise RuntimeError(f"unavailable signal {item.id!r} has no usable reason")
        column = item.sort[1:] if item.sort.startswith("-") else item.sort
        if column not in ORDER_BY:
            raise RuntimeError(
                f"signal {item.id!r} orders by {item.sort!r}, which is not one of "
                f"the {len(ORDER_BY)} numeric catalog fields"
            )


_validate()


__all__ = [
    "AVAILABLE",
    "BY_ID",
    "Control",
    "EXCHANGES_SOURCE",
    "Option",
    "SCHEMA_VERSION",
    "SECTORS_SOURCE",
    "SIGNALS",
    "Signal",
    "SignalError",
    "UnavailableSignal",
    "UnknownSignal",
    "VENDORED_KEYS",
    "default_sort",
    "document",
    "membership",
    "signal",
    "vendored",
]
