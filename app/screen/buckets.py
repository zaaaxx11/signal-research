"""Named server-side bucket predicates for the Descriptive filters (plan D3).

A bucket is an **id**, not a range: ``price:u5``, ``cap:mega``,
``earn:todaybefore``, ``idx:sp500``. The id is ``"<control>:<finviz-token>"``,
so it is traceable to the probe that produced the vocabulary and stable enough
to live in a URL.

Why a module and not ``field op value``
---------------------------------------
Three of the four predicate shapes below cannot be written as one comparison
between a row column and a constant, which is the whole justification for a
second compiler beside the filter DSL:

===============  ====================================  ==============================
shape            example                               why the DSL cannot
===============  ====================================  ==============================
column vs const  ``price < 5``                         it can — kept uniform anyway
two columns      ``target_price >= 1.5 * price``       needs two cells of one row
                 ``float_shares / shares_outstanding``
date vs a day    ``earn:today`` = New York's today      needs a clock and a timezone
set membership   ``idx:sp500``                         no list/set operator
===============  ====================================  ==============================

The vocabulary is the catalogue's
---------------------------------
Every family's tokens are read from :data:`app.screen.filters.FILTER_CATALOG` —
the probe's own option list — and never retyped here. A token whose shape the
band parser cannot read is an **import error**, not a silently missing bucket:
:func:`_validate` fails the process the same way ``filter_fields`` does, because
a family that is one option short would 422 a token Finviz draws in its panel.

Units come from the label, not the digits
-----------------------------------------
``sh_avgvol:u1000`` is "Under 1M", not "under 1,000 shares": Finviz's own range
suffix for that control is ``K``. ``sh_outstanding`` and ``sh_float`` are in
millions. ``fa_div``/``sh_short`` are percentage points (``0.47`` = 0.47%), the
provider's and the store's unit, so no scaling is applied there.

Unknown is not zero
-------------------
A null cell matches **no** bucket: a dividend yield nobody reported matches
neither ``none`` (0%) nor ``pos``. A percentage bucket whose two inputs are not
both present matches nothing rather than dividing by zero.

The clock
---------
``earn:today`` is New York's today, because an earnings date is a market day.
:class:`Context` is the only clock a predicate may read — it is built once per
answer from the answer's own ``as_of``, so two calls with one ``as_of`` agree
and a test never has to wait for midnight.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.screen import index_membership
from app.screen.filters import FILTER_CATALOG

#: The market's clock. An earnings date is a New York day; ``as_of`` is UTC.
NEW_YORK = ZoneInfo("America/New_York")

#: The reason the six session options cannot run: no collector stamps BMO/AMC.
SESSION_REASON = (
    "needs an earnings session stamp (before market open / after market close): "
    "the scan and the depth store carry the report *day* only, so a session "
    "bucket would have to guess which half of the day it is"
)


class BucketError(ValueError):
    """A bucket id that cannot be applied, carrying a stable machine code."""

    code = "invalid_bucket"


class UnknownBucket(BucketError):
    """The id names no bucket (a typo, or a token from another control)."""

    code = "unknown_bucket"


class UnavailableBucket(BucketError):
    """The bucket exists, but the metric it needs is not loaded.

    A real Finviz option whose source is missing is a 422 that names the source
    — never an empty page, which a caller reads as "no company is in the
    S&P 500".
    """

    code = "unavailable_bucket"


@dataclass(frozen=True, slots=True)
class Context:
    """The only clock a bucket may read: the answer's own read moment."""

    today: date

    @classmethod
    def at(cls, as_of: str) -> "Context":
        """``as_of`` (ISO-8601, the selection's own stamp) → New York's day.

        A naive stamp is read as UTC: every ``as_of`` the API emits carries an
        offset, and inventing a local zone for a naked one would make the same
        request answer differently on two hosts.
        """

        moment = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return cls(today=moment.astimezone(NEW_YORK).date())


@dataclass(frozen=True, slots=True)
class Bucket:
    """One named predicate: the id a URL carries, the sentence it compiles to."""

    id: str
    key: str
    label: str
    finviz: str | None
    predicate: Callable[[Mapping[str, Any], Context], bool]
    describe: str
    available: bool = True
    reason: str | None = None


# ---------------------------------------------------------------------------
# Reading a cell
# ---------------------------------------------------------------------------


def _number(value: Any) -> float | None:
    """A cell as a finite float, or ``None``. A null is unknown, never ``0``."""

    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


_OP_TEXT: Mapping[str, str] = MappingProxyType(
    {"eq": "==", "ne": "!=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}
)


def _test(actual: float, op: str, expected: Any) -> bool:
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "lt":
        return actual < expected
    if op == "lte":
        return actual <= expected
    if op == "gt":
        return actual > expected
    if op == "gte":
        return actual >= expected
    if op == "between":
        return expected[0] <= actual <= expected[1]
    if op == "span":
        return expected[0] <= actual < expected[1]
    raise RuntimeError(f"unknown comparison {op!r}")


def _fmt(value: float) -> str:
    """A threshold as the audit line prints it: ``5``, ``1.5``, ``200000000000``."""

    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return repr(value) if isinstance(value, float) else str(value)


def _describe(column: str, op: str, value: Any) -> str:
    """The published sentence: a reader can check the predicate without code."""

    if op == "between":
        return f"{column} between {_fmt(value[0])} and {_fmt(value[1])}"
    if op == "span":
        return f"{column} >= {_fmt(value[0])} and {column} < {_fmt(value[1])}"
    return f"{column} {_OP_TEXT[op]} {_fmt(value)}"


def _column_predicate(
    column: str, op: str, value: Any
) -> Callable[[Mapping[str, Any], Context], bool]:
    """``row[column] op value``; a null cell matches nothing."""

    def predicate(row: Mapping[str, Any], context: Context) -> bool:
        number = _number(row.get(column))
        if number is None:
            return False
        return _test(number, op, value)

    return predicate


# ---------------------------------------------------------------------------
# Bands: Finviz's range tokens
# ---------------------------------------------------------------------------


def _band(token: str, scale: float = 1.0) -> tuple[str, Any] | None:
    """``u5`` → ``("lt", 5)``; ``o1.5`` → ``("gt", 1.5)``; ``1to5`` → ``("between", (1, 5))``."""

    hit = re.fullmatch(r"(u|o)(\d+(?:\.\d+)?)", token)
    if hit:
        return ("lt" if hit.group(1) == "u" else "gt"), float(hit.group(2)) * scale
    span = re.fullmatch(r"(\d+(?:\.\d+)?)to(\d+(?:\.\d+)?)", token)
    if span:
        return "between", (
            float(span.group(1)) * scale,
            float(span.group(2)) * scale,
        )
    return None


def _compact(value: float) -> str:
    """``1000000`` → ``1M`` (Finviz's own label vocabulary)."""

    for factor, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= factor:
            return f"{value / factor:g}{unit}"
    return f"{value:g}"


def _band_label(
    op: str, value: Any, *, prefix: str = "", suffix: str = ""
) -> str:
    def show(number: float) -> str:
        return f"{prefix}{_compact(number)}{suffix}"

    if op == "lt":
        return f"Under {show(value)}"
    if op == "gt":
        return f"Over {show(value)}"
    low, high = value
    return f"{show(low)} to {show(high)}"


#: The probe's named options, in panel order: ``""`` is "Any" and ``frange`` /
#: ``modal`` are Finviz's Custom width, which no named bucket can express.
def _tokens(key: str) -> tuple[str, ...]:
    spec = FILTER_CATALOG[key]
    return tuple(
        token for token in spec.options if token and token not in ("frange", "modal")
    )


_CONTROL_KEYS: dict[str, str] = {
    "price": "sh_price",
    "cap": "cap",
    "fa_div": "fa_div",
    "sh_short": "sh_short",
    "an_recom": "an_recom",
    "earn": "earningsdate",
    "sh_avgvol": "sh_avgvol",
    "sh_relvol": "sh_relvol",
    "sh_outstanding": "sh_outstanding",
    "sh_float": "sh_float",
    "targetprice": "targetprice",
    "idx": "idx",
}

#: control id → the Finviz filter key it serves. The one table that names the
#: namespaces; ``selection`` and the descriptive document both read it.
CONTROL_KEYS: Mapping[str, str] = MappingProxyType(_CONTROL_KEYS)

#: key → control id (the inverse of :data:`CONTROL_KEYS`).
_KEYS_BY_CONTROL: Mapping[str, str] = MappingProxyType(
    {key: control for control, key in _CONTROL_KEYS.items()}
)

#: The filter keys that answer a bucket token, in panel order.
KEYS: tuple[str, ...] = tuple(_CONTROL_KEYS.values())


def control_for(key: object) -> str | None:
    """The namespace ``key``'s buckets live under, or ``None``."""

    return _KEYS_BY_CONTROL.get(key) if isinstance(key, str) else None


# ---------------------------------------------------------------------------
# The families
# ---------------------------------------------------------------------------


def _numeric(
    control: str,
    key: str,
    column: str,
    *,
    scale: float = 1.0,
    prefix: str = "",
    suffix: str = "",
    labels: Mapping[str, str] | None = None,
    tokens: Sequence[str] | None = None,
) -> tuple[Bucket, ...]:
    """A mechanical family: every token in ``tokens`` is a band over ``column``."""

    family: list[Bucket] = []
    for token in (_tokens(key) if tokens is None else tokens):
        band = _band(token, scale)
        if band is None:  # pragma: no cover - _validate() is the real gate
            raise RuntimeError(f"{key}: token {token!r} is not a band")
        op, value = band
        family.append(
            Bucket(
                id=f"{control}:{token}",
                key=key,
                label=(labels or {}).get(token)
                or _band_label(op, value, prefix=prefix, suffix=suffix),
                finviz=token,
                predicate=_column_predicate(column, op, value),
                describe=_describe(column, op, value),
            )
        )
    return tuple(family)


#: ``cap``: six partition bands plus eight overlapping ones, in billions.
_CAP_BANDS: Mapping[str, tuple[float, float]] = MappingProxyType(
    {
        "mega": (200e9, math.inf),
        "large": (10e9, 200e9),
        "mid": (2e9, 10e9),
        "small": (300e6, 2e9),
        "micro": (50e6, 300e6),
        "nano": (0.0, 50e6),
    }
)
_CAP_OVER: Mapping[str, float] = MappingProxyType(
    {
        "largeover": 10e9,
        "midover": 2e9,
        "smallover": 300e6,
        "microover": 50e6,
    }
)
_CAP_UNDER: Mapping[str, float] = MappingProxyType(
    {
        "largeunder": 200e9,
        "midunder": 10e9,
        "smallunder": 2e9,
        "microunder": 300e6,
    }
)


def _cap_family() -> tuple[Bucket, ...]:
    """Market cap: the six named bands partition; ``over``/``under`` overlap."""

    labels = {
        "mega": "Mega ($200bln and more)",
        "large": "Large ($10bln to $200bln)",
        "mid": "Mid ($2bln to $10bln)",
        "small": "Small ($300mln to $2bln)",
        "micro": "Micro ($50mln to $300mln)",
        "nano": "Nano (under $50mln)",
        "largeover": "+Large (over $10bln)",
        "midover": "+Mid (over $2bln)",
        "smallover": "+Small (over $300mln)",
        "microover": "+Micro (over $50mln)",
        "largeunder": "-Large (under $200bln)",
        "midunder": "-Mid (under $10bln)",
        "smallunder": "-Small (under $2bln)",
        "microunder": "-Micro (under $300mln)",
    }
    column = "market_cap_usd"
    family: list[Bucket] = []
    for token in _tokens("cap"):
        if token in _CAP_BANDS:
            low, high = _CAP_BANDS[token]
            if high == math.inf:
                op, value = "gte", low
            elif low == 0.0:
                op, value = "lt", high
            else:
                op, value = "span", (low, high)
        elif token in _CAP_OVER:
            op, value = "gt", _CAP_OVER[token]
        elif token in _CAP_UNDER:
            op, value = "lt", _CAP_UNDER[token]
        else:  # pragma: no cover - _validate() is the real gate
            raise RuntimeError(f"cap: token {token!r} has no band")
        family.append(
            Bucket(
                id=f"cap:{token}",
                key="cap",
                label=labels[token],
                finviz=token,
                predicate=_column_predicate(column, op, value),
                describe=_describe(column, op, value),
            )
        )
    return tuple(family)


def _dividend_family() -> tuple[Bucket, ...]:
    """Dividend yield in the provider's percent; ``none`` is exactly 0."""

    labels = {
        "none": "None (0%)",
        "pos": "Positive (>0%)",
        "high": "High (>5%)",
        "veryhigh": "Very High (>10%)",
    }
    fixed: Mapping[str, tuple[str, float]] = MappingProxyType(
        {
            "none": ("eq", 0.0),
            "pos": ("gt", 0.0),
            "high": ("gt", 5.0),
            "veryhigh": ("gt", 10.0),
        }
    )
    column = "dividend_yield"
    family: list[Bucket] = []
    for token in _tokens("fa_div"):
        if token in fixed:
            op, value = fixed[token]
        else:
            band = _band(token)
            if band is None:  # pragma: no cover - _validate() is the real gate
                raise RuntimeError(f"fa_div: token {token!r} has no band")
            op, value = band
        family.append(
            Bucket(
                id=f"fa_div:{token}",
                key="fa_div",
                label=labels.get(token) or _band_label(op, value, suffix="%"),
                finviz=token,
                predicate=_column_predicate(column, op, value),
                describe=_describe(column, op, value),
            )
        )
    return tuple(family)


def _short_family() -> tuple[Bucket, ...]:
    """Short float, percentage points: ``low`` is Finviz's "Low (<5%)"."""

    labels = {"low": "Low (<5%)", "high": "High (>20%)"}
    fixed: Mapping[str, tuple[str, float]] = MappingProxyType(
        {"low": ("lt", 5.0), "high": ("gt", 20.0)}
    )
    column = "short_float"
    family: list[Bucket] = []
    for token in _tokens("sh_short"):
        if token in fixed:
            op, value = fixed[token]
        else:
            band = _band(token)
            if band is None:  # pragma: no cover - _validate() is the real gate
                raise RuntimeError(f"sh_short: token {token!r} has no band")
            op, value = band
        family.append(
            Bucket(
                id=f"sh_short:{token}",
                key="sh_short",
                label=labels.get(token) or _band_label(op, value, suffix="%"),
                finviz=token,
                predicate=_column_predicate(column, op, value),
                describe=_describe(column, op, value),
            )
        )
    return tuple(family)


#: Analyst recommendation on Finviz's 1..5 score (``recom_finviz``), where 1 is
#: a strong buy and 5 a strong sell. Each label is a half-point band; the
#: "or better"/"or worse" labels are the cumulative side of the same scale.
_RECOM_BANDS: Mapping[str, tuple[str, Any]] = MappingProxyType(
    {
        "strongbuy": ("lt", 2.0),
        "buybetter": ("lte", 2.0),
        "buy": ("between", (1.5, 2.5)),
        "holdbetter": ("lte", 3.0),
        "hold": ("between", (2.5, 3.5)),
        "holdworse": ("gte", 2.5),
        "sell": ("between", (3.5, 4.5)),
        "sellworse": ("gte", 3.5),
        "strongsell": ("gte", 4.5),
    }
)
_RECOM_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "strongbuy": "Strong Buy (1)",
        "buybetter": "Buy or better",
        "buy": "Buy",
        "holdbetter": "Hold or better",
        "hold": "Hold",
        "holdworse": "Hold or worse",
        "sell": "Sell",
        "sellworse": "Sell or worse",
        "strongsell": "Strong Sell (5)",
    }
)


def _recom_family() -> tuple[Bucket, ...]:
    column = "recom_finviz"
    family: list[Bucket] = []
    for token in _tokens("an_recom"):
        if token not in _RECOM_BANDS:  # pragma: no cover - _validate() gates it
            raise RuntimeError(f"an_recom: token {token!r} has no band")
        op, value = _RECOM_BANDS[token]
        family.append(
            Bucket(
                id=f"an_recom:{token}",
                key="an_recom",
                label=_RECOM_LABELS[token],
                finviz=token,
                predicate=_column_predicate(column, op, value),
                describe=_describe(column, op, value),
            )
        )
    return tuple(family)


# -- target price: the rows own price is the denominator --------------------


def _target_predicate(op: str, pct: float) -> Callable[[Mapping[str, Any], Context], bool]:
    """``aN`` = at least N% above the row's own price; ``bN`` = at least N% below."""

    def predicate(row: Mapping[str, Any], context: Context) -> bool:
        price = _number(row.get("price"))
        target = _number(row.get("target_price"))
        if price is None or target is None or price <= 0:
            return False
        if op == "a":
            return target >= price * (1 + pct / 100)
        if op == "b":
            return target <= price * (1 - pct / 100)
        if op == "above":
            return target > price
        return target < price

    return predicate


def _target_family() -> tuple[Bucket, ...]:
    family: list[Bucket] = []
    for token in _tokens("targetprice"):
        if token in ("above", "below"):
            op, pct = token, 0.0
            label = "Above Price" if token == "above" else "Below Price"
            describe = f"target_price {'>' if token == 'above' else '<'} price"
        else:
            hit = re.fullmatch(r"(a|b)(\d+(?:\.\d+)?)", token)
            if hit is None:  # pragma: no cover - _validate() gates it
                raise RuntimeError(f"targetprice: token {token!r} has no band")
            op, pct = hit.group(1), float(hit.group(2))
            side = "Above" if op == "a" else "Below"
            label = f"{pct:g}% {side} Price"
            compare = ">=" if op == "a" else "<="
            factor = f"1 + {pct:g}/100" if op == "a" else f"1 - {pct:g}/100"
            describe = f"target_price {compare} price * ({factor})"
        family.append(
            Bucket(
                id=f"targetprice:{token}",
                key="targetprice",
                label=label,
                finviz=token,
                predicate=_target_predicate(op, pct),
                describe=describe,
            )
        )
    return tuple(family)


# -- float: absolute shares and the percent of shares outstanding -----------


def _ratio_predicate(op: str, pct: float) -> Callable[[Mapping[str, Any], Context], bool]:
    """``float_shares / shares_outstanding * 100 op pct``; unknown matches nothing."""

    def predicate(row: Mapping[str, Any], context: Context) -> bool:
        float_shares = _number(row.get("float_shares"))
        outstanding = _number(row.get("shares_outstanding"))
        if float_shares is None or outstanding is None or outstanding <= 0:
            return False
        return _test(float_shares / outstanding * 100, op, pct)

    return predicate


def _float_family() -> tuple[Bucket, ...]:
    """34 options: 16 absolute (millions) then 18 share-of-outstanding bands."""

    family: list[Bucket] = list(
        _numeric(
            "sh_float",
            "sh_float",
            "float_shares",
            scale=1e6,
            tokens=tuple(token for token in _tokens("sh_float") if not token.endswith("p")),
        )
    )
    for token in _tokens("sh_float"):
        if not token.endswith("p"):
            continue
        band = _band(token[:-1])
        if band is None:  # pragma: no cover - _validate() gates it
            raise RuntimeError(f"sh_float: token {token!r} has no band")
        op, value = band
        family.append(
            Bucket(
                id=f"sh_float:{token}",
                key="sh_float",
                label=_band_label(op, value, suffix="%"),
                finviz=token,
                predicate=_ratio_predicate(op, value),
                describe=_describe("float_shares / shares_outstanding * 100", op, value),
            )
        )
    return tuple(family)


# -- earnings dates: the market's day, not UTC's ----------------------------


def _epoch_day(value: float) -> date | None:
    """Epoch seconds → the UTC date the collector means.

    ``app.market.yahoo_batch._epoch_date`` writes a provider stamp as
    ``YYYY-MM-DD`` in UTC, so the bucket reader uses the same rule: the stamp is
    a day label, not an instant to be re-zoned. Re-zoning it into New York would
    move a midnight-UTC report date back one day.
    """

    try:
        return datetime.fromtimestamp(value, UTC).date()
    except (OverflowError, OSError, ValueError):
        return None


def day(value: Any, *, today: date) -> date | None:
    """One ``earnings_date`` cell as a date, or ``None`` when it is not one.

    Three spellings are read: an epoch integer (the **scan** column), an ISO
    date (the depth store) and the page's ``Sep 28`` / ``Sep 28 BMO`` (the same
    store, the other provider). A year-less month-day is the next occurrence on
    or after ``today`` — ``app.api.calendar.parse_earnings_date``'s rule, copied
    rather than imported because production may not import the HTTP layer; a
    drift test in ``tests/test_screen_buckets.py`` asserts the two agree.
    """

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return moment.astimezone(NEW_YORK).date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return _epoch_day(float(value)) if math.isfinite(float(value)) else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?", text)
    if iso is not None:
        try:
            return date(*(int(part) for part in iso.groups()))
        except ValueError:
            return None
    month_day = re.fullmatch(r"([A-Za-z]{3,9})\.? (\d{1,2})(?: (?:BMO|AMC))?", text)
    if month_day is None:
        return None
    month = _MONTHS.get(month_day.group(1)[:3].casefold())
    if month is None:
        return None
    day_of_month = int(month_day.group(2))
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day_of_month)
        except ValueError:
            return None
        if candidate >= today:
            return candidate
    return None


#: Month names as a table, not ``strptime("%b")``: ``%b`` reads the process
#: locale, and a host with ``LANG=fr_FR`` would parse "mai" and refuse "may".
_MONTHS: Mapping[str, int] = MappingProxyType(
    {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
)


def _monday(today: date) -> date:
    """The Monday of ``today``'s ISO week (``date.weekday`` is Monday == 0)."""

    return today - timedelta(days=today.weekday())


def _in_window(token: str, moment: date, today: date) -> bool:
    monday = _monday(today)
    if token == "today":
        return moment == today
    if token == "tomorrow":
        return moment == today + timedelta(days=1)
    if token == "yesterday":
        return moment == today - timedelta(days=1)
    if token == "nextdays5":
        return today <= moment <= today + timedelta(days=5)
    if token == "prevdays5":
        return today - timedelta(days=5) <= moment <= today
    if token == "thisweek":
        return monday <= moment <= monday + timedelta(days=6)
    if token == "nextweek":
        return monday + timedelta(days=7) <= moment <= monday + timedelta(days=13)
    if token == "prevweek":
        return monday - timedelta(days=7) <= moment <= monday - timedelta(days=1)
    if token == "thismonth":
        return (moment.year, moment.month) == (today.year, today.month)
    raise RuntimeError(f"earningswindow: token {token!r} has no window")


def _earnings_predicate(token: str) -> Callable[[Mapping[str, Any], Context], bool]:
    def predicate(row: Mapping[str, Any], context: Context) -> bool:
        moment = day(row.get("earnings_date"), today=context.today)
        if moment is None:
            return False
        return _in_window(token, moment, context.today)

    return predicate


#: Finviz's six session options: real options, no source.
_SESSION_TOKENS: frozenset[str] = frozenset(
    {
        "todaybefore",
        "todayafter",
        "tomorrowbefore",
        "tomorrowafter",
        "yesterdaybefore",
        "yesterdayafter",
    }
)

_EARNINGS_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "today": "Today",
        "todaybefore": "Today Before Market Open",
        "todayafter": "Today After Market Close",
        "tomorrow": "Tomorrow",
        "tomorrowbefore": "Tomorrow Before Market Open",
        "tomorrowafter": "Tomorrow After Market Close",
        "yesterday": "Yesterday",
        "yesterdaybefore": "Yesterday Before Market Open",
        "yesterdayafter": "Yesterday After Market Close",
        "nextdays5": "Next 5 Days",
        "prevdays5": "Previous 5 Days",
        "thisweek": "This Week",
        "nextweek": "Next Week",
        "prevweek": "Previous Week",
        "thismonth": "This Month",
    }
)

_EARNINGS_DESCRIBE: Mapping[str, str] = MappingProxyType(
    {
        "today": "earnings_date == New York today",
        "tomorrow": "earnings_date == New York today + 1",
        "yesterday": "earnings_date == New York today - 1",
        "nextdays5": "New York today <= earnings_date <= New York today + 5",
        "prevdays5": "New York today - 5 <= earnings_date <= New York today",
        "thisweek": "earnings_date in this ISO week",
        "nextweek": "earnings_date in the next ISO week",
        "prevweek": "earnings_date in the previous ISO week",
        "thismonth": "earnings_date in this calendar month",
    }
)


def _earnings_family() -> tuple[Bucket, ...]:
    family: list[Bucket] = []
    for token in _tokens("earningsdate"):
        label = _EARNINGS_LABELS.get(token, token)
        if token in _SESSION_TOKENS:
            family.append(
                Bucket(
                    id=f"earn:{token}",
                    key="earningsdate",
                    label=label,
                    finviz=token,
                    predicate=lambda row, context: False,
                    describe=f"unavailable: {SESSION_REASON}",
                    available=False,
                    reason=SESSION_REASON,
                )
            )
            continue
        if token not in _EARNINGS_DESCRIBE:  # pragma: no cover - _validate() gates it
            raise RuntimeError(f"earningsdate: token {token!r} has no window")
        family.append(
            Bucket(
                id=f"earn:{token}",
                key="earningsdate",
                label=label,
                finviz=token,
                predicate=_earnings_predicate(token),
                describe=_EARNINGS_DESCRIBE[token],
            )
        )
    return tuple(family)


# -- index membership: a set test over a cell selection attaches -------------


def _membership_predicate(token: str) -> Callable[[Mapping[str, Any], Context], bool]:
    """``token in row["index_members"]``; unknown membership matches nothing."""

    def predicate(row: Mapping[str, Any], context: Context) -> bool:
        raw = row.get("index_members")
        if isinstance(raw, str):
            values = [part.strip().casefold() for part in raw.split(",") if part.strip()]
        elif isinstance(raw, (list, tuple, set, frozenset)):
            values = [str(part).strip().casefold() for part in raw]
        else:
            return False
        return token in values

    return predicate


def _idx_family() -> tuple[Bucket, ...]:
    family: list[Bucket] = []
    for token in _tokens("idx"):
        label = index_membership.LABELS[token]
        family.append(
            Bucket(
                id=f"idx:{token}",
                key="idx",
                label=label,
                finviz=token,
                predicate=_membership_predicate(token),
                describe=f"{token!r} in index_members",
            )
        )
    return tuple(family)


#: control id → a callable that answers *why the control cannot run right now*
#: (``None`` = it can). Availability is a runtime fact, not an import-time one:
#: installing or removing the membership file must move the answer without a
#: restart, which is why the descriptive document re-reads this on every build.
_AVAILABILITY: Mapping[str, Callable[[], str | None]] = MappingProxyType(
    {
        "idx": lambda: None if index_membership.available() else index_membership.REASON,
    }
)


def _current(item: Bucket) -> Bucket:
    """The bucket as it stands *now*: an unavailable control says why."""

    probe = _AVAILABILITY.get(item.id.split(":", 1)[0])
    reason = probe() if probe is not None else None
    if reason is None:
        return item
    return replace(item, available=False, reason=reason)


def _build_registry() -> dict[str, tuple[Bucket, ...]]:
    registry = {
        "price": _numeric(
            "price", "sh_price", "price", prefix="$"
        ),
        "cap": _cap_family(),
        "fa_div": _dividend_family(),
        "sh_short": _short_family(),
        "an_recom": _recom_family(),
        "earn": _earnings_family(),
        "sh_avgvol": _numeric(
            "sh_avgvol", "sh_avgvol", "avg_volume_30d", scale=1e3
        ),
        "sh_relvol": _numeric("sh_relvol", "sh_relvol", "rel_volume_10d"),
        "sh_outstanding": _numeric(
            "sh_outstanding", "sh_outstanding", "shares_outstanding", scale=1e6
        ),
        "sh_float": _float_family(),
        "targetprice": _target_family(),
        "idx": _idx_family(),
    }
    return registry


_REGISTRY: dict[str, tuple[Bucket, ...]] = _build_registry()

#: id (case-folded) → bucket, the lookup table :func:`bucket` reads.
_BY_ID: Mapping[str, Bucket] = MappingProxyType(
    {item.id.casefold(): item for family in _REGISTRY.values() for item in family}
)

#: filter key → its family, in panel order. Keyed by the *key* the request
#: names (``sh_price``), not by the control its ids are namespaced by (``price``).
_BY_KEY: Mapping[str, tuple[Bucket, ...]] = MappingProxyType(
    {_CONTROL_KEYS[control]: family for control, family in _REGISTRY.items()}
)


def _validate() -> None:
    """Fail at import: every probe option must resolve, and no id may collide."""

    for control, key in _CONTROL_KEYS.items():
        family = _REGISTRY.get(control)
        if not family:
            raise RuntimeError(f"bucket control {control!r} has no family")
        expected = _tokens(key)
        got = tuple(item.finviz for item in family)
        if got != expected:
            missing = sorted(set(expected) - set(got))
            extra = sorted(set(got) - set(expected))
            raise RuntimeError(
                f"bucket family {control!r} does not cover the probe's options: "
                f"missing={missing}, extra={extra}"
            )
        for item in family:
            if item.key != key:
                raise RuntimeError(f"bucket {item.id!r} names key {item.key!r}, expected {key!r}")
            if not item.available and not item.reason:
                raise RuntimeError(f"unavailable bucket {item.id!r} has no reason")
            if item.available and item.reason is not None:
                raise RuntimeError(f"available bucket {item.id!r} carries a reason")
    if len(_BY_ID) != sum(len(family) for family in _REGISTRY.values()):
        raise RuntimeError("two buckets share an id")


_validate()


def for_key(key: object) -> tuple[Bucket, ...]:
    """The bucket family of ``key``; a key with no vocabulary answers ``()``.

    ``()`` is the honest answer for ``sh_curvol`` (the probe's only named option
    is "Any") and for every control this plan does not compile. It is *not* a
    substitute for a refusal: :func:`bucket` raises on an id it does not know.
    """

    family = _BY_KEY.get(key, ()) if isinstance(key, str) else ()
    return tuple(_current(item) for item in family)


def bucket(identifier: object) -> Bucket:
    """The bucket ``identifier`` names, or a :class:`BucketError`.

    The lookup case-folds the whole id, so ``PRICE:U5`` and ``idx:SP500`` are
    the same bucket; the canonical spelling is what the envelope echoes.
    """

    if not isinstance(identifier, str) or not identifier.strip():
        raise UnknownBucket(
            identifier,
            f"bucket id must be a non-empty string, got {identifier!r}",
        )
    found = _BY_ID.get(identifier.strip().casefold())
    if found is None:
        control = identifier.split(":", 1)[0].casefold()
        family = _REGISTRY.get(control)
        if family:
            known = ", ".join(item.id for item in family)
            raise UnknownBucket(
                identifier,
                f"unknown bucket {identifier!r} for control {control!r}; "
                f"accepted ids: {known}",
            )
        raise UnknownBucket(
            identifier,
            f"unknown bucket {identifier!r}: ids are namespaced "
            f"('<control>:<finviz-token>'), and the controls are "
            f"{', '.join(_CONTROL_KEYS)}",
        )
    current = _current(found)
    if not current.available:
        raise UnavailableBucket(
            identifier,
            f"bucket {current.id!r} ({current.label}) cannot be applied: it {current.reason}",
        )
    return current


# ---------------------------------------------------------------------------
# The executor
# ---------------------------------------------------------------------------


def _read(criterion: Any, name: str) -> Any:
    """One attribute of a criterion, from a mapping or a dataclass instance."""

    if isinstance(criterion, Mapping):
        return criterion.get(name)
    return getattr(criterion, name, None)


def _bucket_of(criterion: Any) -> Bucket | None:
    """A criterion that carries no field is a bucket token, or it is invalid."""

    if _read(criterion, "field") is not None:
        return None
    return bucket(_read(criterion, "value"))


def apply_bucket_filters(
    rows: Sequence[Mapping[str, Any]],
    criteria: Sequence[Any],
    *,
    as_of: str,
) -> list[dict[str, Any]]:
    """AND every bucket criterion over ``rows``; DSL criteria are ignored.

    ``selection`` splits its criteria first (``field`` vs no ``field``), so this
    function only ever sees the bucket half. Accepting both shapes (a
    :class:`app.screen.selection.Criterion` and the published ``{key, field, op,
    value}`` mapping) keeps the module importable from either layer without a
    cycle.

    The clock is built **once** per call from ``as_of``, so every row of one
    answer is judged against one day even if the call runs across midnight.
    """

    compiled = [
        found
        for found in (_bucket_of(criterion) for criterion in criteria)
        if found is not None
    ]
    if not compiled:
        return [dict(row) for row in rows]
    context = Context.at(as_of)
    return [
        dict(row)
        for row in rows
        if all(item.predicate(row, context) for item in compiled)
    ]


__all__ = [
    "Bucket",
    "BucketError",
    "CONTROL_KEYS",
    "Context",
    "KEYS",
    "NEW_YORK",
    "SESSION_REASON",
    "UnavailableBucket",
    "UnknownBucket",
    "apply_bucket_filters",
    "bucket",
    "control_for",
    "day",
    "for_key",
]
