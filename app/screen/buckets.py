"""Named server-side bucket predicates for Finviz's filter tabs.

A bucket is an **id**, not a range: ``price:u5``, ``cap:mega``,
``earn:todaybefore``, ``idx:sp500``, ``pe:u15``, ``rsi:ob70``,
``divgrowth:3yo10``, ``highlow52w:nh``, ``news:today``. The id is
``"<control>:<finviz-token>"``, so it is traceable to the probe that produced
the vocabulary and stable enough to live in a URL.

Five predicate shapes, not one
------------------------------
The Descriptive families are the easy half (one token, one column). The
Fundamental, Technical and News families add the shapes a single comparison
cannot express, and each has its own builder:

====================  =================================  ==============================
shape                 example                             why the DSL cannot
====================  =================================  ==============================
one column             ``pe:u15`` → ``pe_ttm < 15``       it can — kept uniform anyway
direction, not range   ``gap:u0`` → ``gap_pct >= 0``      ``u`` is "up" here and "under" on ``sh_price``
distance from a level  ``highlow52w:nh``, ``sma20:pa10``   the row stores the *level*, not the distance
two cells              ``epsrev:bp``, ``sma20:sa50``       both surprise cells / both averages
a clock                ``news:today`` = New York's today   needs a calendar and a timezone
set membership         ``idx:sp500``                       no list/set operator
====================  =================================  ==============================

Four of these shapes cannot be written as one comparison between a row column
and a constant, which is the whole justification for a second compiler beside the
filter DSL.

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

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.screen import after_hours, etf_holdings, index_membership, news_index
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


class InvalidBucketToken(BucketError):
    """The token is a **value** the control cannot accept, not an unknown id.

    ``news_kw`` is free text with a validated alphabet and ``heldby`` names one
    of four funds; a token outside those rules is a 422 whose code says which
    rule it broke (``invalid_news_keyword``), rather than an ``unknown_bucket``
    that would list a vocabulary the control does not have.
    """

    code = "invalid_bucket_token"

    def __init__(self, identifier: object, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.identifier = identifier
        if code is not None:
            self.code = code


@dataclass(frozen=True, slots=True)
class Context:
    """The only clock a bucket may read: the answer's own read moment.

    ``today`` is that moment in New York (a market day); ``moment`` is the
    instant itself, which a news window needs because "the last 24 hours" is not
    a day. ``moment`` defaults to midnight New York on ``today`` so a caller that
    only knows the day keeps working, and no bucket ever guesses a second clock.
    """

    today: date
    moment: datetime | None = None

    def __post_init__(self) -> None:
        if self.moment is None:
            object.__setattr__(
                self,
                "moment",
                datetime(self.today.year, self.today.month, self.today.day, tzinfo=NEW_YORK),
            )

    @classmethod
    def at(cls, as_of: str) -> "Context":
        """``as_of`` (ISO-8601, the selection's own stamp) → the answer's clocks.

        A naive stamp is read as UTC: every ``as_of`` the API emits carries an
        offset, and inventing a local zone for a naked one would make the same
        request answer differently on two hosts.
        """

        moment = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return cls(today=moment.astimezone(NEW_YORK).date(), moment=moment.astimezone(UTC))


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
    """``u5`` → ``("lt", 5)``; ``o1.5`` → ``("gt", 1.5)``; ``1to5`` → ``("between", (1, 5))``.

    A threshold may be **negative**: ``fa_roa:u-50`` is "Under -50%", and the
    same shape appears on seven other controls (51 real tokens). Before that
    character was allowed the parser answered ``None``, so those families could
    not be built at all — an import error, not a missing bucket.
    """

    hit = re.fullmatch(r"(u|o)(-?\d+(?:\.\d+)?)", token)
    if hit:
        return (("lt" if hit.group(1) == "u" else "gt"), float(hit.group(2)) * scale)
    span = re.fullmatch(r"(-?\d+(?:\.\d+)?)to(-?\d+(?:\.\d+)?)", token)
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


# ---------------------------------------------------------------------------
# Labels come from the probe, never from a string typed here
# ---------------------------------------------------------------------------

#: The vendored Finviz vocabulary, written by ``scripts/filter-options.py``.
LABEL_PATH = Path(__file__).with_name("data") / "filter_options.json"


def _load_labels() -> Mapping[str, Mapping[str, str]]:
    """``key → token → Finviz's own label``, read once from the vendored probe."""

    document = json.loads(LABEL_PATH.read_text(encoding="utf-8"))
    controls = document.get("controls")
    if not isinstance(controls, dict):
        raise RuntimeError(f"{LABEL_PATH} carries no 'controls' object")
    return MappingProxyType(
        {
            key: MappingProxyType(
                {
                    str(option["token"]): str(option["label"])
                    for option in spec.get("options") or ()
                }
            )
            for key, spec in controls.items()
        }
    )


_LABELS: Mapping[str, Mapping[str, str]] = _load_labels()


def _label(key: str, token: str) -> str:
    """The label Finviz draws for ``token`` on ``key``.

    A missing label is an import error, not a token printed as its own name:
    the panel would show ``fa_roa:u-50`` where Finviz shows "Under -50%", and
    the document would be describing a vocabulary nobody can recognise.
    """

    labels = _LABELS.get(key)
    if labels is None:
        raise RuntimeError(
            f"{key}: no vendored vocabulary in {LABEL_PATH}; "
            "rerun python scripts/filter-options.py"
        )
    label = labels.get(token)
    if label is None:
        raise RuntimeError(
            f"{key}: token {token!r} has no label in {LABEL_PATH}; the probe "
            "grew an option and scripts/filter-options.py was not rerun"
        )
    return label


def labels_for(key: object) -> Mapping[str, str]:
    """Every label Finviz draws for ``key``'s options, or ``{}`` for a key we do not hold.

    The document's *disabled* controls need exactly this: their options cannot
    filter, but they still have to be drawn with Finviz's own words. A key the
    probe does not carry answers ``{}`` rather than raising — the caller is
    describing a vocabulary, not asserting one.
    """

    if not isinstance(key, str):
        return {}
    return _LABELS.get(key, {})


# ---------------------------------------------------------------------------
# Bands: one row per token whose meaning is not a bare ``u``/``o`` range
# ---------------------------------------------------------------------------

#: The eleven EPS/sales growth keys share this vocabulary verbatim: "Positive
#: Low (0-10%)" is a *band*, not a threshold, and "High" is > 25%.
_GROWTH: Mapping[str, tuple[str, Any]] = MappingProxyType(
    {
        "neg": ("lt", 0.0),
        "pos": ("gt", 0.0),
        "poslow": ("between", (0.0, 10.0)),
        "high": ("gt", 25.0),
    }
)

#: control key → token → ``(op, value)``. Thresholds are Finviz's own label
#: table (``Low (<15)`` for P/E, ``Low (<1)`` for PEG, ``Low (<3)`` for
#: Price/Cash); the digits in a label are the one place the meaning is stated.
_BANDS: Mapping[str, Mapping[str, tuple[str, Any]]] = MappingProxyType(
    {
        # -- valuation (T17) --------------------------------------------------
        "fa_pe": {
            "low": ("lt", 15.0),
            "profitable": ("gt", 0.0),
            "high": ("gt", 50.0),
        },
        "fa_fpe": {
            "low": ("lt", 15.0),
            "profitable": ("gt", 0.0),
            "high": ("gt", 50.0),
        },
        "fa_peg": {"low": ("lt", 1.0), "high": ("gt", 2.0)},
        "fa_ps": {"low": ("lt", 1.0), "high": ("gt", 10.0)},
        "fa_pb": {"low": ("lt", 1.0), "high": ("gt", 5.0)},
        "fa_pc": {"low": ("lt", 3.0), "high": ("gt", 50.0)},
        "fa_pfcf": {"low": ("lt", 15.0), "high": ("gt", 50.0)},
        "fa_evebitda": {
            "negative": ("lt", 0.0),
            "low": ("lt", 15.0),
            "profitable": ("gt", 0.0),
            "high": ("gt", 50.0),
        },
        "fa_evsales": {
            "negative": ("lt", 0.0),
            "low": ("lt", 1.0),
            "positive": ("gt", 0.0),
            "high": ("gt", 10.0),
        },
        # -- growth (T18) -----------------------------------------------------
        "fa_epsyoy": _GROWTH,
        "fa_epsyoy1": _GROWTH,
        "fa_epsqoq": _GROWTH,
        "fa_epsyoyttm": _GROWTH,
        "fa_eps3years": _GROWTH,
        "fa_eps5years": _GROWTH,
        "fa_estltgrowth": _GROWTH,
        "fa_salesqoq": _GROWTH,
        "fa_salesyoyttm": _GROWTH,
        "fa_sales3years": _GROWTH,
        "fa_sales5years": _GROWTH,
        # -- quality, leverage, ownership (T19) -------------------------------
        "fa_roa": {
            "pos": ("gt", 0.0),
            "neg": ("lt", 0.0),
            "verypos": ("gt", 15.0),
            "veryneg": ("lt", -15.0),
        },
        "fa_roe": {
            "pos": ("gt", 0.0),
            "neg": ("lt", 0.0),
            "verypos": ("gt", 30.0),
            "veryneg": ("lt", -15.0),
        },
        "fa_roi": {
            "pos": ("gt", 0.0),
            "neg": ("lt", 0.0),
            "verypos": ("gt", 25.0),
            "veryneg": ("lt", -10.0),
        },
        "fa_curratio": {"high": ("gt", 3.0), "low": ("lt", 1.0)},
        "fa_quickratio": {"high": ("gt", 3.0), "low": ("lt", 0.5)},
        "fa_ltdebteq": {"high": ("gt", 0.5), "low": ("lt", 0.1)},
        "fa_debteq": {"high": ("gt", 0.5), "low": ("lt", 0.1)},
        "fa_grossmargin": {"pos": ("gt", 0.0), "neg": ("lt", 0.0), "high": ("gt", 50.0)},
        "fa_opermargin": {
            "pos": ("gt", 0.0),
            "neg": ("lt", 0.0),
            "veryneg": ("lt", -20.0),
            "high": ("gt", 25.0),
        },
        "fa_netmargin": {
            "pos": ("gt", 0.0),
            "neg": ("lt", 0.0),
            "veryneg": ("lt", -20.0),
            "high": ("gt", 20.0),
        },
        "fa_payoutratio": {
            "none": ("eq", 0.0),
            "pos": ("gt", 0.0),
            "low": ("lt", 20.0),
            "high": ("gt", 50.0),
        },
        "sh_insiderown": {
            "low": ("lt", 5.0),
            "high": ("gt", 30.0),
            "veryhigh": ("gt", 50.0),
        },
        "sh_instown": {"low": ("lt", 5.0), "high": ("gt", 90.0)},
        "sh_insttrans": {
            "veryneg": ("lt", -20.0),
            "neg": ("lt", 0.0),
            "pos": ("gt", 0.0),
            "verypos": ("gt", 20.0),
        },
    }
)


def _never(row: Mapping[str, Any], context: Context) -> bool:
    """An unavailable bucket accepts nothing: it is a 422, not a predicate."""

    return False


def _family(
    control: str,
    key: str,
    column: str | None = None,
    *,
    bands: Mapping[str, tuple[str, Any]] | None = None,
    tokens: Sequence[str] | None = None,
    refuse: Mapping[str, str] | None = None,
    predicate: Callable[[str, str, Any], Callable[[Mapping[str, Any], Context], bool]]
    | None = None,
    describe: Callable[[str, str, Any], str] | None = None,
) -> tuple[Bucket, ...]:
    """One bucket per probe option of ``key``, in the probe's order.

    ``column`` + :func:`_band` is the common case (``pe:u5`` → ``pe_ttm < 5``).
    ``predicate`` replaces it where a token needs two cells (``a5h``), both
    cells of a pair (``epsrev:bp``) or a clock (``news:today``): it receives
    ``(token, op, value)`` and returns the row predicate. ``refuse`` is
    ``{token: reason}`` — the token is still a bucket (``available=False``), so
    the family covers the probe without pretending the token works.
    """

    special = _BANDS.get(key, {}) if bands is None else bands
    family: list[Bucket] = []
    for token in (_tokens(key) if tokens is None else tokens):
        if refuse is not None and token in refuse:
            family.append(
                Bucket(
                    id=f"{control}:{token}",
                    key=key,
                    label=_label(key, token),
                    finviz=token,
                    predicate=_never,
                    describe=f"unavailable: {refuse[token]}",
                    available=False,
                    reason=refuse[token],
                )
            )
            continue
        if token in special:
            op, value = special[token]
        else:
            band = _band(token)
            if band is None:
                raise RuntimeError(
                    f"{key}: token {token!r} has no band and no entry in _BANDS"
                )
            op, value = band
        family.append(
            Bucket(
                id=f"{control}:{token}",
                key=key,
                label=_label(key, token),
                finviz=token,
                predicate=(
                    predicate(token, op, value)
                    if predicate is not None
                    else _column_predicate(str(column), op, value)
                ),
                describe=(
                    describe(token, op, value)
                    if describe is not None
                    else _describe(str(column), op, value)
                ),
            )
        )
    return tuple(family)


_CONTROL_KEYS: dict[str, str] = {
    "price": "sh_price",
    "cap": "cap",
    "fa_div": "fa_div",
    "sh_short": "sh_short",
    "an_recom": "an_recom",
    "sh_opt": "sh_opt",
    "earn": "earningsdate",
    "sh_avgvol": "sh_avgvol",
    "sh_relvol": "sh_relvol",
    "sh_outstanding": "sh_outstanding",
    "sh_float": "sh_float",
    "targetprice": "targetprice",
    "ipodate": "ipodate",
    "idx": "idx",
    # -- fundamental: valuation (T17) --------------------------------------
    "pe": "fa_pe",
    "fpe": "fa_fpe",
    "peg": "fa_peg",
    "ps": "fa_ps",
    "pb": "fa_pb",
    "pc": "fa_pc",
    "pfcf": "fa_pfcf",
    "evebitda": "fa_evebitda",
    "evsales": "fa_evsales",
    # -- fundamental: growth (T18) -----------------------------------------
    "epsyoy": "fa_epsyoy",
    "epsyoy1": "fa_epsyoy1",
    "epsqoq": "fa_epsqoq",
    "epsytm": "fa_epsyoyttm",
    "eps3y": "fa_eps3years",
    "eps5y": "fa_eps5years",
    "epsnext5y": "fa_estltgrowth",
    "salesqoq": "fa_salesqoq",
    "salesttm": "fa_salesyoyttm",
    "sales3y": "fa_sales3years",
    "sales5y": "fa_sales5years",
    # -- fundamental: quality, leverage, ownership (T19) --------------------
    "divgrowth": "fa_divgrowth",
    "roa": "fa_roa",
    "roe": "fa_roe",
    "roi": "fa_roi",
    "curratio": "fa_curratio",
    "quickratio": "fa_quickratio",
    "ltdebteq": "fa_ltdebteq",
    "debteq": "fa_debteq",
    "grossmargin": "fa_grossmargin",
    "opermargin": "fa_opermargin",
    "netmargin": "fa_netmargin",
    "payout": "fa_payoutratio",
    "insiderown": "sh_insiderown",
    "insidertrans": "sh_insidertrans",
    "instown": "sh_instown",
    "insttrans": "sh_insttrans",
    # -- fundamental: surprise (T20) ---------------------------------------
    "epsrev": "fa_epsrev",
    # -- technical: price and oscillator (T21) -----------------------------
    "rsi": "ta_rsi",
    "gap": "ta_gap",
    "change": "ta_change",
    "changeopen": "ta_changeopen",
    "beta": "ta_beta",
    "atr": "ta_averagetruerange",
    # -- technical: trend and range (T22) ----------------------------------
    "sma20": "ta_sma20",
    "sma50": "ta_sma50",
    "sma200": "ta_sma200",
    "highlow50d": "ta_highlow50d",
    "highlow52w": "ta_highlow52w",
    "alltime": "ta_alltime",
    "candle": "ta_candlestick",
    # -- technical: the extended session (Elite in the source panel) --------
    # The post-market print is a real provider field; the two controls are
    # buckets like any other, gated by tier rather than refused.
    "ah_close": "ah_close",
    "ah_change": "ah_change",
    # -- news (T23) --------------------------------------------------------
    "news": "news_date",
}

#: The controls whose tokens are a **value** this registry cannot enumerate:
#: ``news_kw`` takes a validated keyword, ``heldby`` one of the four funds in
#: :mod:`app.screen.etf_holdings`. They are buckets (one token, one predicate,
#: ``op: eq``) resolved by :func:`bucket`'s dynamic hook instead of ``_BY_ID``;
#: they are kept out of :data:`_CONTROL_KEYS` because that table promises one
#: probe vocabulary per control and these two have none.
DYNAMIC_CONTROLS: Mapping[str, str] = MappingProxyType(
    {"news_kw": "news_keywords", "heldby": "etf_heldby"}
)

#: key → control id, the inverse of :data:`DYNAMIC_CONTROLS`.
_DYNAMIC_BY_KEY: Mapping[str, str] = MappingProxyType(
    {key: control for control, key in DYNAMIC_CONTROLS.items()}
)

#: The filter keys only a dynamic token can answer, in panel order.
DYNAMIC_KEYS: tuple[str, ...] = tuple(DYNAMIC_CONTROLS.values())

#: key → one token it accepts, for the 422 a **dynamic** key raises when the
#: value names no token at all (an empty string). A static family lists its ids;
#: a dynamic one cannot, so it shows one accepted shape instead of an IndexError.
_EXAMPLES: Mapping[str, str] = MappingProxyType(
    {"news_keywords": "news_kw:semiconductor", "etf_heldby": "heldby:spy"}
)


def example_token(key: object) -> str | None:
    """One accepted token of a dynamic key, or ``None`` for a static one."""

    return _EXAMPLES.get(key) if isinstance(key, str) else None

#: Controls whose whole family is published **refused**: ``insidertrans`` is a
#: vendor cell the product withdrew by name (:mod:`app.api.signals`). Every
#: member of the family is ``available=False`` and carries the same sentence, so
#: the control can answer no value at all and ``filter_fields`` keeps
#: ``sh_insidertrans`` in its refusal list.
#:
#: The family is registered like any other so ``insidertrans:pos`` reaches
#: :func:`bucket` and raises :class:`UnavailableBucket` (422
#: ``unavailable_bucket``) instead of being answered as an unknown id — that is
#: the one code the registry, the document and the HTTP answer share for this
#: control. What the withdrawn control does **not** do is answer the question
#: :data:`KEYS` asks ("which keys can a token filter?"), so it is published apart
#: in :data:`WITHDRAWN_KEYS` and ``filter_fields`` derives its refusal from the
#: family (:attr:`filter_fields.BUCKET_REFUSALS`) rather than keeping a second
#: paraphrase.
_WITHDRAWN: frozenset[str] = frozenset({"insidertrans"})

#: control → readable spelling → the canonical token it means. A synonym is a
#: **spelling**, not a second bucket: :func:`bucket` resolves it to the canonical
#: id, and the envelope echoes that id, so ``rsi:overbought`` and ``rsi:ob70``
#: are one question and therefore one ETag. Only tokens whose Finviz mnemonic is
#: unreadable get one; the fundamental bands (``pe:u15``) already read as prose.
_SYNONYMS: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "rsi": MappingProxyType({"overbought": "ob70", "oversold": "os30"}),
    }
)

#: control id → the Finviz filter key it serves. The one table that names the
#: namespaces; ``selection`` and the descriptive document both read it.
CONTROL_KEYS: Mapping[str, str] = MappingProxyType(_CONTROL_KEYS)

#: key → control id (the inverse of :data:`CONTROL_KEYS`).
_KEYS_BY_CONTROL: Mapping[str, str] = MappingProxyType(
    {key: control for control, key in _CONTROL_KEYS.items()}
)

#: The filter keys that answer a bucket token, in panel order. A **withdrawn**
#: control is not listed here — it answers no value, so it would lie about what a
#: token can filter — but it is not dropped either: :data:`WITHDRAWN_KEYS` names
#: it, and :func:`bucket` still refuses its tokens by name.
KEYS: tuple[str, ...] = tuple(
    key for control, key in _CONTROL_KEYS.items() if control not in _WITHDRAWN
)

#: The filter keys whose bucket family is registered but entirely refused, in
#: panel order. A request naming one of their tokens is a 422
#: ``unavailable_bucket`` that carries the family's reason; a request naming the
#: key itself is the key-level refusal (``unavailable_filter_key``), because a
#: key that can answer no value is not a filter.
WITHDRAWN_KEYS: tuple[str, ...] = tuple(
    _CONTROL_KEYS[control] for control in _CONTROL_KEYS if control in _WITHDRAWN
)


def control_for(key: object) -> str | None:
    """The namespace ``key``'s buckets live under, or ``None``.

    Two tables, one answer: the static registry (:data:`CONTROL_KEYS`) and the
    two dynamic controls (:data:`DYNAMIC_CONTROLS`), whose tokens are a value
    rather than a probe vocabulary.
    """

    if not isinstance(key, str):
        return None
    found = _KEYS_BY_CONTROL.get(key)
    return found if found is not None else _DYNAMIC_BY_KEY.get(key)


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


#: Analyst recommendation on Finviz's 1..5 score (``recom_score``), where 1 is
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
    column = "recom_score"
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
        # Both after-hours families read one snapshot: when it cannot answer (no
        # file, or captured during the regular session) the controls say so with
        # the module's measured sentence instead of matching nothing silently.
        "ah_close": lambda: after_hours.reason(),
        "ah_change": lambda: after_hours.reason(),
    }
)


def _current(item: Bucket) -> Bucket:
    """The bucket as it stands *now*: an unavailable control says why."""

    probe = _AVAILABILITY.get(item.id.split(":", 1)[0])
    reason = probe() if probe is not None else None
    if reason is None:
        return item
    return replace(item, available=False, reason=reason)


# -- dividend growth: the window is in the token, the threshold too ----------

#: Finviz's three dividend-growth windows, and the depth column each one owns.
#: ``cy1…cy9`` ("Growing 1+ Years") have **no** column: the store carries 1y,
#: 3y and 5y only, so those nine tokens are refused by name.
_DIVIDEND_COLUMNS: Mapping[str, str] = MappingProxyType(
    {
        "1y": "dividend_growth_1y",
        "3y": "dividend_growth_3y",
        "5y": "dividend_growth_5y",
    }
)

DIVIDEND_SERIES_REASON = (
    "no series for a 1-to-9-year dividend window: the depth store carries the "
    "1-year, 3-year and 5-year growth rates only, so a 'Growing N+ Years' test "
    "would have to invent the years it counts"
)


def _dividend_growth_family() -> tuple[Bucket, ...]:
    """``3yo10`` → ``dividend_growth_3y > 10``; ``cy9`` → unavailable, named.

    The token carries **two** facts — the window and the threshold — which is
    why this family is a small parser instead of a `_BANDS` row: a shared table
    could not say which of the three columns a token means.
    """

    family: list[Bucket] = []
    for token in _tokens("fa_divgrowth"):
        if token.startswith("cy"):
            family.append(
                Bucket(
                    id=f"divgrowth:{token}",
                    key="fa_divgrowth",
                    label=_label("fa_divgrowth", token),
                    finviz=token,
                    predicate=_never,
                    describe=f"unavailable: {DIVIDEND_SERIES_REASON}",
                    available=False,
                    reason=DIVIDEND_SERIES_REASON,
                )
            )
            continue
        hit = re.fullmatch(r"([135])y(pos|o(\d+))", token)
        if hit is None:  # pragma: no cover - _validate() gates the vocabulary
            raise RuntimeError(f"fa_divgrowth: token {token!r} has no window")
        column = _DIVIDEND_COLUMNS[f"{hit.group(1)}y"]
        op, value = ("gt", 0.0) if hit.group(2) == "pos" else ("gt", float(hit.group(3)))
        family.append(
            Bucket(
                id=f"divgrowth:{token}",
                key="fa_divgrowth",
                label=_label("fa_divgrowth", token),
                finviz=token,
                predicate=_column_predicate(column, op, value),
                describe=_describe(column, op, value),
            )
        )
    return tuple(family)


#: Why the insider-transaction control is refused, quoted from the product's
#: own withdrawal (:mod:`app.api.signals`): the data exists and is junk.
INSIDER_TRANSACTIONS_REASON = (
    "reads a mixture of ownership levels and junk (TSLA 76.0, META 45.9, "
    "GME 8.7): the depth cell is a vendor net-change nobody can read as a flow, "
    "so the product withdrew the control by name rather than publish an "
    "interpretation the data does not support"
)


def _withdrawn_family(control: str, key: str, reason: str) -> tuple[Bucket, ...]:
    """A complete family of refused buckets: every probe token, none usable."""

    return _family(control, key, None, refuse={token: reason for token in _tokens(key)})


# -- earnings & revenue surprise: one token, two cells -----------------------

#: ``bp``/``bm``/``bn`` mean "both positive / both met / both negative", so the
#: predicate compares the same operator against **both** surprise cells.
_SURPRISE_BANDS: Mapping[str, tuple[str, float]] = MappingProxyType(
    {"bp": ("gt", 0.0), "bm": ("eq", 0.0), "bn": ("lt", 0.0)}
)


def _surprise_predicate(op: str, value: float) -> Callable[[Mapping[str, Any], Context], bool]:
    """Both cells must be present and both must pass; unknown matches nothing."""

    def predicate(row: Mapping[str, Any], context: Context) -> bool:
        eps = _number(row.get("eps_surprise"))
        revenue = _number(row.get("revenue_surprise"))
        if eps is None or revenue is None:
            return False
        return _test(eps, op, value) and _test(revenue, op, value)

    return predicate


def _epsrev_family() -> tuple[Bucket, ...]:
    """Three tokens, each one comparison applied to both surprise cells."""

    family: list[Bucket] = []
    for token in _tokens("fa_epsrev"):
        if token not in _SURPRISE_BANDS:  # pragma: no cover - _validate() gates it
            raise RuntimeError(f"fa_epsrev: token {token!r} has no band")
        op, value = _SURPRISE_BANDS[token]
        family.append(
            Bucket(
                id=f"epsrev:{token}",
                key="fa_epsrev",
                label=_label("fa_epsrev", token),
                finviz=token,
                predicate=_surprise_predicate(op, value),
                describe=(
                    f"eps_surprise {_OP_TEXT[op]} {_fmt(value)} and "
                    f"revenue_surprise {_OP_TEXT[op]} {_fmt(value)}"
                ),
            )
        )
    return tuple(family)


# -- price direction: `u` is UP here, not "under" ---------------------------

def _direction_family(control: str, key: str, column: str) -> tuple[Bucket, ...]:
    """``ta_gap:u2`` is "Up 2 %", not ``gap < 2``.

    The same letter means *under* in ``sh_price:u5`` and *up* here; the probe's
    own labels are the proof (``u1`` = "Up 1 %", ``d1`` = "Down 1 %"). So these
    keys get their own parser and never pass through :func:`_band`, which would
    compile "Up 0 %" as ``gap < 0`` — the exact opposite of the predicate.
    """

    family: list[Bucket] = []
    for token in _tokens(key):
        if token in ("u", "d"):
            op, value = ("gt", 0.0) if token == "u" else ("lt", 0.0)
        else:
            hit = re.fullmatch(r"([ud])(\d+(?:\.\d+)?)", token)
            if hit is None:  # pragma: no cover - _validate() gates the vocabulary
                raise RuntimeError(f"{key}: token {token!r} is not a direction band")
            side, digits = hit.groups()
            number = float(digits)
            op, value = ("gte", number) if side == "u" else ("lte", -number)
        family.append(
            Bucket(
                id=f"{control}:{token}",
                key=key,
                label=_label(key, token),
                finviz=token,
                predicate=_column_predicate(column, op, value),
                describe=_describe(column, op, value),
            )
        )
    return tuple(family)


# -- RSI: four token kinds, one column --------------------------------------

#: ``ob`` overbought (>=), ``os`` oversold (<=), ``nob`` not overbought (<),
#: ``nos`` not oversold (>). The labels confirm the direction at every bound
#: ("Not Overbought (<60)", "Not Oversold (>50)"), and the strict/loose split is
#: the difference between 60.0 being overbought and not.
_RSI_OPS: Mapping[str, str] = MappingProxyType(
    {"ob": "gte", "os": "lte", "nob": "lt", "nos": "gt"}
)


def _rsi_family() -> tuple[Bucket, ...]:
    family: list[Bucket] = []
    for token in _tokens("ta_rsi"):
        hit = re.fullmatch(r"(ob|os|nob|nos)(\d+(?:\.\d+)?)", token)
        if hit is None:  # pragma: no cover - _validate() gates the vocabulary
            raise RuntimeError(f"ta_rsi: token {token!r} is not an RSI band")
        op = _RSI_OPS[hit.group(1)]
        value = float(hit.group(2))
        family.append(
            Bucket(
                id=f"rsi:{token}",
                key="ta_rsi",
                label=_label("ta_rsi", token),
                finviz=token,
                predicate=_column_predicate("rsi_14", op, value),
                describe=_describe("rsi_14", op, value),
            )
        )
    return tuple(family)


# -- a distance is measured from a level, and an implausible level is no measurement


def _level_of(row: Mapping[str, Any], level: str) -> tuple[float, float] | None:
    """``(price, level)`` when both are usable; ``None`` otherwise.

    A level more than ten times the price is not a measurement: 24 rows carry
    ``high_50d`` above 10x price (max 1,250x), and a distance computed from one
    would read "99.9% below the high" for a stock that is not.
    """

    price = _number(row.get("price"))
    anchor = _number(row.get(level))
    if price is None or anchor is None or price <= 0 or anchor <= 0:
        return None
    if anchor > price * 10:
        return None
    return price, anchor


def _distance_predicate(
    op: str, value: Any, level: str, *, side: str
) -> Callable[[Mapping[str, Any], Context], bool]:
    """``side="below"``: ``(1 - price/level)*100``; ``"above"``: ``(price/level - 1)*100``."""

    def predicate(row: Mapping[str, Any], context: Context) -> bool:
        reading = _level_of(row, level)
        if reading is None:
            return False
        price, anchor = reading
        # `(anchor - price) / anchor`, not `1 - price/anchor`: the two are the
        # same number in arithmetic and not in floating point, and 90 vs 100
        # must be exactly "10% below", not 9.999999999999998%.
        distance = (
            (anchor - price) / anchor * 100
            if side == "below"
            else (price - anchor) / anchor * 100
        )
        return _test(distance, op, value)

    return predicate


def _at_level_predicate(level: str, *, at: str) -> Callable[[Mapping[str, Any], Context], bool]:
    """``nh`` → ``price >= 0.999 * level``; ``nl`` → ``price <= 1.001 * level``.

    The epsilon is the one :mod:`app.api.signals` uses for its ``new_high`` /
    ``new_low`` tables, so the bucket and the home page cannot disagree about
    what "at the high" means.
    """

    def predicate(row: Mapping[str, Any], context: Context) -> bool:
        reading = _level_of(row, level)
        if reading is None:
            return False
        price, anchor = reading
        return price >= anchor * 0.999 if at == "high" else price <= anchor * 1.001

    return predicate


#: ``b5h`` / ``b0to5h`` / ``a10h``: below the high or above the low, by a
#: threshold or inside a [0, N) band.
_HIGHLOW_TOKEN = re.compile(r"(?P<side>[ab])(?P<band>0to)?(?P<pct>\d+)h")

ALL_TIME_REASON = (
    "the all-time-high cell is below the 52-week high on 22 rows and equal to the "
    "50-day high on 293 more, so a new-all-time-high test would admit rows that "
    "are nowhere near one; every other token reads a distance and survives a "
    "wrong level"
)


def _highlow_family(
    control: str,
    key: str,
    *,
    high: str,
    low: str,
    refuse: Mapping[str, str] | None = None,
) -> tuple[Bucket, ...]:
    """One token, two anchors: ``b`` is measured from the high, ``a`` from the low."""

    family: list[Bucket] = []
    for token in _tokens(key):
        if refuse is not None and token in refuse:
            family.append(
                Bucket(
                    id=f"{control}:{token}",
                    key=key,
                    label=_label(key, token),
                    finviz=token,
                    predicate=_never,
                    describe=f"unavailable: {refuse[token]}",
                    available=False,
                    reason=refuse[token],
                )
            )
            continue
        if token == "nh":
            predicate = _at_level_predicate(high, at="high")
            describe = f"price >= 0.999 * {high}"
        elif token == "nl":
            predicate = _at_level_predicate(low, at="low")
            describe = f"price <= 1.001 * {low}"
        else:
            hit = _HIGHLOW_TOKEN.fullmatch(token)
            if hit is None:  # pragma: no cover - _validate() gates the vocabulary
                raise RuntimeError(f"{key}: token {token!r} is not a high/low band")
            side = hit.group("side")
            value: Any = float(hit.group("pct"))
            op = "gte"
            if hit.group("band"):
                op, value = "span", (0.0, value)
            level = high if side == "b" else low
            direction = "below" if side == "b" else "above"
            compare = "1 - price /" if side == "b" else "price /"
            suffix = ") * 100" if side == "b" else " - 1) * 100"
            predicate = _distance_predicate(op, value, level, side=direction)
            describe = _describe(f"({compare} {level}{suffix}", op, value)
        family.append(
            Bucket(
                id=f"{control}:{token}",
                key=key,
                label=_label(key, token),
                finviz=token,
                predicate=predicate,
                describe=describe,
            )
        )
    return tuple(family)


# -- moving averages: a price distance, or a comparison between two averages --

CROSS_REASON = (
    "needs yesterday's price and yesterday's moving average: a cross is an event "
    "between two sessions, and no row carries the previous session"
)

SMA_COLUMNS: Mapping[str, str] = MappingProxyType(
    {"20": "sma_20", "50": "sma_50", "200": "sma_200"}
)


def _sma_distance(
    column: str, *, side: str, op: str, value: float
) -> Callable[[Mapping[str, Any], Context], bool]:
    def predicate(row: Mapping[str, Any], context: Context) -> bool:
        price = _number(row.get("price"))
        sma = _number(row.get(column))
        if price is None or sma is None or price <= 0 or sma <= 0:
            return False
        distance = (
            (price - sma) / sma * 100 if side == "above" else (sma - price) / sma * 100
        )
        return _test(distance, op, value)

    return predicate


def _column_compare(
    column: str, peer: str, op: str
) -> Callable[[Mapping[str, Any], Context], bool]:
    def predicate(row: Mapping[str, Any], context: Context) -> bool:
        left = _number(row.get(column))
        right = _number(row.get(peer))
        if left is None or right is None:
            return False
        return _test(left, op, right)

    return predicate


def _sma_family(control: str, key: str, column: str) -> tuple[Bucket, ...]:
    family: list[Bucket] = []
    for token in _tokens(key):
        if token in ("pa", "pb"):
            side = "above" if token == "pa" else "below"
            predicate = _sma_distance(column, side=side, op="gt", value=0.0)
            describe = f"price {'>' if side == 'above' else '<'} {column}"
        else:
            price_hit = re.fullmatch(r"p([ab])(\d+)", token)
            peer_hit = re.fullmatch(r"s([ab])(\d+)", token)
            if price_hit is not None:
                letter, digits = price_hit.groups()
                side = "above" if letter == "a" else "below"
                value = float(digits)
                predicate = _sma_distance(column, side=side, op="gte", value=value)
                expression = (
                    f"(price - {column}) / {column} * 100"
                    if letter == "a"
                    else f"({column} - price) / {column} * 100"
                )
                describe = _describe(expression, "gte", value)
            elif peer_hit is not None:
                side, digits = peer_hit.groups()
                peer = SMA_COLUMNS[digits]
                op = "gt" if side == "a" else "lt"
                predicate = _column_compare(column, peer, op)
                describe = _describe(column, op, peer)
            else:
                # ``pc``/``pca``/``pcb`` and the six ``crossNN[a|b]`` tokens.
                family.append(
                    Bucket(
                        id=f"{control}:{token}",
                        key=key,
                        label=_label(key, token),
                        finviz=token,
                        predicate=_never,
                        describe=f"unavailable: {CROSS_REASON}",
                        available=False,
                        reason=CROSS_REASON,
                    )
                )
                continue
        family.append(
            Bucket(
                id=f"{control}:{token}",
                key=key,
                label=_label(key, token),
                finviz=token,
                predicate=predicate,
                describe=describe,
            )
        )
    return tuple(family)


# -- candlestick: two classes we can name, nine we cannot --------------------

CANDLESTICK_REASON = (
    "no classifier for this shape in the depth store: it names doji, hammer and "
    "engulfing only (2,389 rows), so the other nine chart-pattern classes can "
    "never match a row — a bucket for them would be an empty page wearing a "
    "filter's name"
)

#: Finviz token → the classifier string the depth store writes.
CANDLESTICK_CLASSES: Mapping[str, str] = MappingProxyType({"h": "hammer", "d": "doji"})


def _candle_family(control: str, key: str) -> tuple[Bucket, ...]:
    family: list[Bucket] = []
    for token in _tokens(key):
        if token not in CANDLESTICK_CLASSES:
            family.append(
                Bucket(
                    id=f"{control}:{token}",
                    key=key,
                    label=_label(key, token),
                    finviz=token,
                    predicate=_never,
                    describe=f"unavailable: {CANDLESTICK_REASON}",
                    available=False,
                    reason=CANDLESTICK_REASON,
                )
            )
            continue
        wanted = CANDLESTICK_CLASSES[token]

        def predicate(
            row: Mapping[str, Any], context: Context, wanted: str = wanted
        ) -> bool:
            value = row.get("candlestick_pattern")
            if not isinstance(value, str) or not value.strip():
                return False
            return value.strip().casefold() == wanted

        family.append(
            Bucket(
                id=f"{control}:{token}",
                key=key,
                label=_label(key, token),
                finviz=token,
                predicate=predicate,
                describe=f"candlestick_pattern == {wanted!r}",
            )
        )
    return tuple(family)


# -- news: one headline per symbol, and a clock ------------------------------

#: Why the three sub-hour windows cannot run: the store is refreshed by a poll,
#: not by a feed, so a five-minute window is a statement about the poll.
NEWS_MINUTE_REASON = (
    "the depth store refreshes about every 13 minutes, so a five-minute window "
    "can only contain rows the poll happened to land on — the answer would look "
    "like news coverage and mean 'what we happened to fetch'"
)

#: The session close, in New York. "Aftermarket Today" means after this moment.
_CLOSE_HOUR = 16


def _news_moment(value: Any) -> datetime | None:
    """One ``news_time`` cell as an aware instant, or ``None``.

    The store writes ISO-8601 UTC (``2026-11-01T11:20:00+00:00``); a naive
    string is read as UTC for the same reason :meth:`Context.at` does — a naked
    stamp has no zone and inventing one would answer differently per host.
    """

    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value.strip():
        try:
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _close_of(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, _CLOSE_HOUR, tzinfo=NEW_YORK)


def _news_predicate(token: str) -> Callable[[Mapping[str, Any], Context], bool]:
    def predicate(row: Mapping[str, Any], context: Context) -> bool:
        moment = _news_moment(row.get("news_time"))
        stamp = context.moment
        if moment is None or stamp is None:
            return False
        local = moment.astimezone(NEW_YORK)
        yesterday = context.today - timedelta(days=1)
        if token == "today":
            return local.date() == context.today
        if token == "yesterday":
            return local.date() == yesterday
        if token == "sinceyesterday":
            return local.date() >= yesterday
        if token == "todayafter":
            return local.date() == context.today and local.hour >= _CLOSE_HOUR
        if token == "yesterdayafter":
            return local.date() == yesterday and local.hour >= _CLOSE_HOUR
        if token == "sinceyesterdayafter":
            return moment >= _close_of(yesterday)
        if token == "prevhours24":
            return moment >= stamp - timedelta(hours=24)
        if token == "prevdays7":
            return moment >= stamp - timedelta(days=7)
        if token == "prevmonth":
            # Finviz's "In the last month": 30 days, and the label says so.
            return moment >= stamp - timedelta(days=30)
        raise RuntimeError(f"news_date: token {token!r} has no window")

    return predicate


_NEWS_DESCRIBE: Mapping[str, str] = MappingProxyType(
    {
        "today": "news_time is on New York's today",
        "todayafter": "news_time is on New York's today, after 16:00",
        "sinceyesterday": "news_time is on New York's yesterday or today",
        "sinceyesterdayafter": "news_time >= yesterday 16:00 New York",
        "yesterday": "news_time is on New York's yesterday",
        "yesterdayafter": "news_time is on New York's yesterday, after 16:00",
        "prevhours24": "news_time >= the answer's moment - 24 hours",
        "prevdays7": "news_time >= the answer's moment - 7 days",
        "prevmonth": "news_time >= the answer's moment - 30 days (the label's month)",
    }
)

_NEWS_MINUTE_TOKENS: frozenset[str] = frozenset(
    {"prevminutes5", "prevminutes30", "prevhours1"}
)


def _news_family() -> tuple[Bucket, ...]:
    family: list[Bucket] = []
    for token in _tokens("news_date"):
        if token in _NEWS_MINUTE_TOKENS:
            family.append(
                Bucket(
                    id=f"news:{token}",
                    key="news_date",
                    label=_label("news_date", token),
                    finviz=token,
                    predicate=_never,
                    describe=f"unavailable: {NEWS_MINUTE_REASON}",
                    available=False,
                    reason=NEWS_MINUTE_REASON,
                )
            )
            continue
        if token not in _NEWS_DESCRIBE:  # pragma: no cover - _validate() gates it
            raise RuntimeError(f"news_date: token {token!r} has no window")
        family.append(
            Bucket(
                id=f"news:{token}",
                key="news_date",
                label=_label("news_date", token),
                finviz=token,
                predicate=_news_predicate(token),
                describe=_NEWS_DESCRIBE[token],
            )
        )
    return tuple(family)


# -- optionable / shortable: the vendor's Yes/No is a boolean, and two cells ---

#: ``sh_opt`` token → ``(optionable, shortable)``, where ``None`` means "this
#: token does not ask that cell". The eight tokens are the probe's; the mapping
#: is the 2x2 truth table read literally, not an interpretation of it.
_OPTION_SHORT_TOKENS: Mapping[str, tuple[bool | None, bool | None]] = MappingProxyType(
    {
        "option": (True, None),
        "short": (None, True),
        "notoption": (False, None),
        "notshort": (None, False),
        "optionshort": (True, True),
        "optionnotshort": (True, False),
        "notoptionshort": (False, True),
        "notoptionnotshort": (False, False),
    }
)

#: The two cells, and the sentence each token compiles to.
_OPTION_CELLS: tuple[str, str] = ("optionable", "shortable")


def _flag(value: Any) -> bool | None:
    """One yes/no cell as a boolean, or ``None`` when it says neither.

    Two writers fill these cells: the depth collector writes the vendor's
    strings (``"Yes"`` / ``"No"``) and the official Cboe path writes a bool. A
    blank, a missing key and a value that is neither is *unknown*, which matches
    no token — the 20 symbols in the store with neither cell must not answer
    ``notoption`` merely because the store is silent.
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().casefold()
        if text in {"yes", "y", "true", "1"}:
            return True
        if text in {"no", "n", "false", "0"}:
            return False
    return None


def _option_short_predicate(
    optionable: bool | None, shortable: bool | None
) -> Callable[[Mapping[str, Any], Context], bool]:
    """One token's truth-table row: each named cell must equal its verdict."""

    def predicate(row: Mapping[str, Any], context: Context) -> bool:
        for cell, expected in zip(_OPTION_CELLS, (optionable, shortable), strict=True):
            if expected is None:
                continue
            if _flag(row.get(cell)) is not expected:
                return False
        return True

    return predicate


def _option_short_describe(
    optionable: bool | None, shortable: bool | None
) -> str:
    """The audit line: which cells, and the verdict each one must carry."""

    clauses = [
        f"{cell} == {'Yes' if expected else 'No'}"
        for cell, expected in zip(_OPTION_CELLS, (optionable, shortable), strict=True)
        if expected is not None
    ]
    return " and ".join(clauses)


def _option_short_family() -> tuple[Bucket, ...]:
    family: list[Bucket] = []
    for token in _tokens("sh_opt"):
        verdicts = _OPTION_SHORT_TOKENS.get(token)
        if verdicts is None:  # pragma: no cover - _validate() gates the tokens
            raise RuntimeError(f"sh_opt: token {token!r} has no truth-table row")
        optionable, shortable = verdicts
        family.append(
            Bucket(
                id=f"sh_opt:{token}",
                key="sh_opt",
                label=_label("sh_opt", token),
                finviz=token,
                predicate=_option_short_predicate(optionable, shortable),
                describe=_option_short_describe(optionable, shortable),
            )
        )
    return tuple(family)


# -- IPO dates: two spellings, one date, fifteen windows ---------------------

#: Days in a nominal year, for the "last N years" / "more than N years" tokens.
#: A calendar year is 365.25 days on average; using 365 would drift a window by
#: two days over eight years, and the token is a *duration*, not a calendar
#: boundary (``earn:prevweek`` is a calendar week; this control never was).
_YEAR_DAYS = 365.25

#: ``ipodate`` token → ``("last", days)`` for "in the last N days",
#: ``("before", days)`` for "more than N days ago", or a named exact day.
_IPO_WINDOWS: Mapping[str, tuple[str, int] | str] = MappingProxyType(
    {
        "today": "today",
        "yesterday": "yesterday",
        "prevweek": ("last", 7),
        "prevmonth": ("last", 30),
        "prevquarter": ("last", 90),
        "prevyear": ("last", 365),
        "prev2yrs": ("last", round(2 * _YEAR_DAYS)),
        "prev3yrs": ("last", round(3 * _YEAR_DAYS)),
        "prev5yrs": ("last", round(5 * _YEAR_DAYS)),
        "more1": ("before", 365),
        "more5": ("before", round(5 * _YEAR_DAYS)),
        "more10": ("before", round(10 * _YEAR_DAYS)),
        "more15": ("before", round(15 * _YEAR_DAYS)),
        "more20": ("before", round(20 * _YEAR_DAYS)),
        "more25": ("before", round(25 * _YEAR_DAYS)),
    }
)

#: Month names, reused from the earnings reader (:data:`_MONTHS`): a table, not
#: ``strptime("%b")``, because ``%b`` reads the process locale.
_IPO_MONTH_DAY = re.compile(r"([A-Za-z]{3,9})\.? (\d{1,2}), (\d{4})")


def _ipo_day(value: Any) -> date | None:
    """One ``listing_date`` cell as a date, or ``None`` when it is not one.

    Two spellings are in the store and both are read: ``1999-11-18`` (4,188
    rows, the collector's ISO stamp) and ``Oct 18, 2016`` (29 rows, the vendor's
    own). The vendor spelling carries its year, so nothing is guessed: a
    year-less month-day would have to pick one, and an IPO date is never "the
    next occurrence".
    """

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # The catalogue documents ``listing_date`` as epoch seconds (the parity
        # scan's unit). It answers null on every live row today, so this branch
        # has no data to prove it — but a column whose documented unit is a
        # timestamp must not silently match nothing if the provider ever fills it.
        return _epoch_day(float(value)) if math.isfinite(float(value)) else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    spelled = _IPO_MONTH_DAY.fullmatch(text)
    if spelled is None:
        return None
    month = _MONTHS.get(spelled.group(1)[:3].casefold())
    if month is None:
        return None
    try:
        return date(int(spelled.group(3)), month, int(spelled.group(2)))
    except ValueError:
        return None


def _ipo_predicate(token: str) -> Callable[[Mapping[str, Any], Context], bool]:
    window = _IPO_WINDOWS[token]

    def predicate(row: Mapping[str, Any], context: Context) -> bool:
        moment = _ipo_day(row.get("listing_date"))
        if moment is None:
            return False
        if window == "today":
            return moment == context.today
        if window == "yesterday":
            return moment == context.today - timedelta(days=1)
        kind, days = window  # type: ignore[misc]
        if kind == "last":
            return context.today - timedelta(days=days - 1) <= moment <= context.today
        return moment < context.today - timedelta(days=days)

    return predicate


def _ipo_describe(token: str) -> str:
    window = _IPO_WINDOWS[token]
    if window == "today":
        return "listing_date == New York today"
    if window == "yesterday":
        return "listing_date == New York today - 1"
    kind, days = window  # type: ignore[misc]
    if kind == "last":
        return (
            f"listing_date >= today - {days - 1} days and listing_date <= today"
        )
    return f"listing_date < today - {days} days"


def _ipo_family() -> tuple[Bucket, ...]:
    family: list[Bucket] = []
    for token in _tokens("ipodate"):
        if token not in _IPO_WINDOWS:  # pragma: no cover - _validate() gates it
            raise RuntimeError(f"ipodate: token {token!r} has no window")
        family.append(
            Bucket(
                id=f"ipodate:{token}",
                key="ipodate",
                label=_label("ipodate", token),
                finviz=token,
                predicate=_ipo_predicate(token),
                describe=_ipo_describe(token),
            )
        )
    return tuple(family)


# -- after hours: the provider's post-market print, one cell per metric -------


def _ah_change_map(token: str) -> tuple[str, float]:
    """``u`` → up at all, ``u5`` → up 5 % or more, ``d5`` → down 5 % or more.

    The direction letter is not a range: on ``sh_price`` a ``u`` means *under*,
    here it means *up*. That is exactly why this family maps its own tokens
    instead of reusing :func:`_band`.
    """

    hit = re.fullmatch(r"([ud])(\d+(?:\.\d+)?)?", token)
    if hit is None:  # pragma: no cover - _validate() gates the vocabulary
        raise RuntimeError(f"ah_change: token {token!r} is not a direction band")
    side, digits = hit.group(1), hit.group(2)
    if digits is None:
        return ("gt", 0.0) if side == "u" else ("lt", 0.0)
    value = float(digits)
    return ("gte", value) if side == "u" else ("lte", -value)


def _ah_family(control: str, key: str, column: str) -> tuple[Bucket, ...]:
    family: list[Bucket] = []
    for token in _tokens(key):
        if control == "ah_change":
            op, value = _ah_change_map(token)
        else:
            band = _band(token)
            if band is None:  # pragma: no cover - _validate() gates the vocabulary
                raise RuntimeError(f"{key}: token {token!r} has no price band")
            op, value = band
        family.append(
            Bucket(
                id=f"{control}:{token}",
                key=key,
                label=_label(key, token),
                finviz=token,
                predicate=_column_predicate(column, op, value),
                describe=_describe(column, op, value),
            )
        )
    return tuple(family)


# -- held by and news keywords: tokens this registry cannot enumerate ---------
#
# Both controls take a **value**, not one of a fixed vocabulary: an ETF symbol
# and a free-text keyword. They are still buckets — one token, one server-side
# predicate, `op: eq` — so the executor needs no second path. What differs is
# resolution: instead of an id in `_BY_ID`, `bucket()` asks the control's
# resolver, which is also where the input rule lives (a 40-character keyword
# alphabet, one of four funds).


def _symbol(row: Mapping[str, Any]) -> str | None:
    """``BRK-B``/``BRK.B``/``BRK B`` → ``BRK.B``: one spelling for one company."""

    raw = row.get("symbol")
    if not isinstance(raw, str):
        return None
    text = raw.strip().upper().replace("-", ".").replace(" ", ".")
    return text or None


def _news_keyword_bucket(identifier: str) -> Bucket:
    """``news_kw:<word>`` → the symbols whose headline carries every word."""

    text = identifier.split(":", 1)[1] if ":" in identifier else ""
    try:
        words = news_index.normalize(text)
    except news_index.KeywordError as exc:
        raise InvalidBucketToken(identifier, str(exc), code=exc.code) from exc
    if not news_index.available():
        raise UnavailableBucket(identifier, news_index.reason() or news_index.REASON)
    keyword = " ".join(words)
    matched = news_index.symbols_for(keyword)

    def predicate(row: Mapping[str, Any], context: Context) -> bool:
        found = _symbol(row)
        return found is not None and found in matched

    return Bucket(
        id=f"news_kw:{keyword}",
        key="news_keywords",
        label=f"News contains {keyword!r}",
        finviz=None,
        predicate=predicate,
        describe=f"headline contains {keyword!r}",
    )


def _heldby_bucket(identifier: str) -> Bucket:
    """``heldby:<etf>`` → the fund's published constituents."""

    text = identifier.split(":", 1)[1].strip() if ":" in identifier else ""
    if not text:
        raise InvalidBucketToken(
            identifier,
            "heldby names no fund: send heldby:<etf>, one of "
            f"{', '.join(etf_holdings.ETFS)}",
            code="unknown_bucket",
        )
    symbol = text.upper()
    try:
        found = etf_holdings.members(symbol)
    except etf_holdings.UnknownEtf as exc:
        raise UnknownBucket(identifier, str(exc)) from exc
    except etf_holdings.UnavailableEtf as exc:
        raise UnavailableBucket(
            identifier, f"bucket {identifier!r} cannot be applied: {exc}"
        ) from exc

    def predicate(row: Mapping[str, Any], context: Context) -> bool:
        current = _symbol(row)
        return current is not None and current in found

    return Bucket(
        id=f"heldby:{symbol.lower()}",
        key="etf_heldby",
        label=f"Held by {symbol}",
        finviz=None,
        predicate=predicate,
        describe=f"symbol in the {symbol} holdings file ({len(found)} names)",
    )


#: control id → the resolver a dynamic token goes through. Declared here, after
#: both resolvers, because the table is read at import time.
_DYNAMIC_RESOLVERS: Mapping[str, Callable[[str], Bucket]] = MappingProxyType(
    {"news_kw": _news_keyword_bucket, "heldby": _heldby_bucket}
)


def _build_registry() -> dict[str, tuple[Bucket, ...]]:
    registry = {
        "price": _numeric(
            "price", "sh_price", "price", prefix="$"
        ),
        "cap": _cap_family(),
        "fa_div": _dividend_family(),
        "sh_short": _short_family(),
        "an_recom": _recom_family(),
        "sh_opt": _option_short_family(),
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
        "ipodate": _ipo_family(),
        "idx": _idx_family(),
        # -- fundamental: valuation (T17) --------------------------------------
        # Every column is a depth cell published under its screen field name
        # (``depth_overlay.SCREEN_ALIASES``); ``price_to_cash`` is the join's
        # computed cell, never the store's ``pc`` spelling.
        "pe": _family("pe", "fa_pe", "pe_ttm"),
        "fpe": _family("fpe", "fa_fpe", "pe_forward"),
        "peg": _family("peg", "fa_peg", "peg"),
        "ps": _family("ps", "fa_ps", "ps"),
        "pb": _family("pb", "fa_pb", "pb"),
        "pc": _family("pc", "fa_pc", "price_to_cash"),
        "pfcf": _family("pfcf", "fa_pfcf", "pfcf"),
        "evebitda": _family("evebitda", "fa_evebitda", "ev_ebitda"),
        "evsales": _family("evsales", "fa_evsales", "ev_to_sales"),
        # -- fundamental: growth (T18) ----------------------------------------
        # Percent points, as the depth store reports them (18.52 = 18.52%).
        "epsyoy": _family("epsyoy", "fa_epsyoy", "eps_this_y"),
        "epsyoy1": _family("epsyoy1", "fa_epsyoy1", "eps_next_y"),
        "epsqoq": _family("epsqoq", "fa_epsqoq", "eps_qoq"),
        "epsytm": _family("epsytm", "fa_epsyoyttm", "eps_yoy_ttm"),
        "eps3y": _family("eps3y", "fa_eps3years", "eps_past_3y"),
        "eps5y": _family("eps5y", "fa_eps5years", "eps_past_5y"),
        # The next-five-year *rate*, not the forward-EPS level the row also
        # carries: `eps_forecast_next_fy` is $4.20, this cell is 12.0%.
        "epsnext5y": _family("epsnext5y", "fa_estltgrowth", "eps_next_5y"),
        "salesqoq": _family("salesqoq", "fa_salesqoq", "sales_qoq"),
        "salesttm": _family("salesttm", "fa_salesyoyttm", "sales_yoy_ttm"),
        "sales3y": _family("sales3y", "fa_sales3years", "sales_past_3y"),
        "sales5y": _family("sales5y", "fa_sales5years", "sales_past_5y"),
        # -- fundamental: quality, leverage, ownership (T19) -------------------
        "divgrowth": _dividend_growth_family(),
        "roa": _family("roa", "fa_roa", "roa"),
        "roe": _family("roe", "fa_roe", "roe"),
        "roi": _family("roi", "fa_roi", "roic"),
        "curratio": _family("curratio", "fa_curratio", "current_ratio"),
        "quickratio": _family("quickratio", "fa_quickratio", "quick_ratio"),
        "ltdebteq": _family("ltdebteq", "fa_ltdebteq", "lt_debt_to_equity"),
        "debteq": _family("debteq", "fa_debteq", "debt_to_equity"),
        "grossmargin": _family("grossmargin", "fa_grossmargin", "gross_margin"),
        "opermargin": _family("opermargin", "fa_opermargin", "oper_margin"),
        "netmargin": _family("netmargin", "fa_netmargin", "profit_margin"),
        "payout": _family("payout", "fa_payoutratio", "payout_ratio"),
        "insiderown": _family("insiderown", "sh_insiderown", "insider_own"),
        "insidertrans": _withdrawn_family(
            "insidertrans", "sh_insidertrans", INSIDER_TRANSACTIONS_REASON
        ),
        "instown": _family("instown", "sh_instown", "inst_own"),
        "insttrans": _family("insttrans", "sh_insttrans", "inst_trans"),
        # -- fundamental: surprise (T20) ---------------------------------------
        "epsrev": _epsrev_family(),
        # -- technical: price and oscillator (T21) -----------------------------
        "rsi": _rsi_family(),
        "gap": _direction_family("gap", "ta_gap", "gap_pct"),
        "change": _direction_family("change", "ta_change", "change_pct"),
        "changeopen": _direction_family("changeopen", "ta_changeopen", "change_from_open"),
        "beta": _family("beta", "ta_beta", "beta_1y"),
        # Price units, not a percent: Finviz's labels ("Over 0.25") are price
        # units too, so no scaling is applied in either direction.
        "atr": _family("atr", "ta_averagetruerange", "atr_14"),
        # -- technical: trend and range (T22) ----------------------------------
        "sma20": _sma_family("sma20", "ta_sma20", "sma_20"),
        "sma50": _sma_family("sma50", "ta_sma50", "sma_50"),
        "sma200": _sma_family("sma200", "ta_sma200", "sma_200"),
        "highlow50d": _highlow_family(
            "highlow50d", "ta_highlow50d", high="high_50d", low="low_50d"
        ),
        "highlow52w": _highlow_family(
            "highlow52w", "ta_highlow52w", high="high_52w", low="low_52w"
        ),
        "alltime": _highlow_family(
            "alltime",
            "ta_alltime",
            high="all_time_high",
            low="all_time_low",
            refuse={
                "nh": ALL_TIME_REASON,
                "nl": ALL_TIME_REASON,
            },
        ),
        "candle": _candle_family("candle", "ta_candlestick"),
        # -- technical: the extended session (Elite in the source panel) --------
        # The cells are `post_market_close` / `post_market_change`, attached by
        # app.screen.after_hours. A row the snapshot did not price has no cell and
        # matches no bucket — during a regular session that is every row.
        "ah_close": _ah_family("ah_close", "ah_close", after_hours.CLOSE_CELL),
        "ah_change": _ah_family("ah_change", "ah_change", after_hours.CHANGE_CELL),
        # -- news (T23) --------------------------------------------------------
        "news": _news_family(),
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
    for control in _WITHDRAWN:
        family = _REGISTRY.get(control) or ()
        if not family:
            raise RuntimeError(f"withdrawn control {control!r} has no family to refuse by")
        if any(item.available for item in family):  # pragma: no cover - boot gate
            raise RuntimeError(
                f"withdrawn control {control!r} publishes an available bucket: a "
                "control the product withdrew must not filter a row"
            )
        if _CONTROL_KEYS.get(control) not in WITHDRAWN_KEYS:  # pragma: no cover - boot gate
            raise RuntimeError(
                f"withdrawn control {control!r} is not in WITHDRAWN_KEYS: its "
                "tokens would answer 'unknown id' instead of the registry's refusal"
            )
    if len(_BY_ID) != sum(len(family) for family in _REGISTRY.values()):
        raise RuntimeError("two buckets share an id")
    # The two dynamic controls: a namespace may not shadow a static one, and a
    # key of theirs must be a real catalogue key (they resolve `bucket`, so a
    # typo here would be an unreachable control instead of a boot error).
    for control, key in DYNAMIC_CONTROLS.items():
        if control in _REGISTRY:  # pragma: no cover - boot gate
            raise RuntimeError(
                f"dynamic control {control!r} shadows the static registry's namespace"
            )
        if key not in FILTER_CATALOG:  # pragma: no cover - boot gate
            raise RuntimeError(
                f"dynamic control {control!r} answers {key!r}, which is not a "
                "catalogue key"
            )
        if key in _CONTROL_KEYS:  # pragma: no cover - boot gate
            raise RuntimeError(
                f"dynamic control {control!r} answers key {key!r}, which a static "
                "family already claims"
            )


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
    # A dynamic control resolves before the static lookup: its namespace is not
    # in `_BY_ID` by construction, and its resolver owns both the input rule and
    # the snapshot check (a keyword outside the alphabet, a fund nobody fetched).
    dynamic = _DYNAMIC_RESOLVERS.get(identifier.strip().split(":", 1)[0].casefold())
    if dynamic is not None:
        return dynamic(identifier.strip())
    found = _BY_ID.get(identifier.strip().casefold())
    if found is None:
        control = identifier.split(":", 1)[0].casefold()
        token = identifier.split(":", 1)[1].strip().casefold() if ":" in identifier else ""
        canonical = _SYNONYMS.get(control, {}).get(token)
        if canonical is not None:
            found = _BY_ID.get(f"{control}:{canonical}".casefold())
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
    "DYNAMIC_CONTROLS",
    "DYNAMIC_KEYS",
    "InvalidBucketToken",
    "KEYS",
    "NEW_YORK",
    "SESSION_REASON",
    "UnavailableBucket",
    "UnknownBucket",
    "WITHDRAWN_KEYS",
    "apply_bucket_filters",
    "bucket",
    "control_for",
    "day",
    "example_token",
    "for_key",
    "labels_for",
]
