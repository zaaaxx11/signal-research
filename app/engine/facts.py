"""SEC XBRL facts: the filing's numbers, still attached to their filing.

``companyfacts`` is not a fundamentals API. It is a pile of every us-gaap
concept an issuer has ever tagged, each row carrying the period it describes,
the accession that reported it and the date it was filed. This module turns
that pile into :class:`FactObservation` values and then into
:class:`~app.engine.model.Cell` objects, and it refuses to add anything the
document does not say.

What the value is
-----------------
* **Reported, absolute, unscaled.** ``companyfacts`` quotes ``USD`` in dollars
  and ``shares`` in shares. A value is never multiplied by a million because a
  screen prints millions; the scale belongs to the presentation layer. An
  amount quoted in another currency is dropped, not converted: without a rate
  the cell would lie about its unit.
* **Undated is unusable.** A row without ``end`` is skipped. A :class:`Cell`
  cannot exist without an ``as_of``, and borrowing the parse date would make a
  value look fresher than the filing.
* **Absent, never zero.** A concept missing from the document produces no
  observation and therefore no cell. There is no zero fallback anywhere in this
  module, and :func:`observations_to_cells` omits the key instead of emitting a
  placeholder.

Which fact wins
---------------
``companyfacts`` repeats the same concept many times: the same fiscal year
appears in the 10-K, again in the next year's comparatives, and again after a
restatement. The order is fixed and documented:

1. **Tag precedence** — :data:`DEFAULT_TAGS` lists one or more us-gaap names
   per engine key, primary first. The first tag with any usable row wins; an
   older row of the primary concept beats a newer row of an alias, because the
   alias is a fallback for issuers that do not use the primary concept at all.
2. **Recency** — within the chosen tag, the newest ``filed`` wins, then the
   newest ``period_end``, then ``accession`` and ``value`` purely to make the
   choice reproducible. A later accession therefore beats the original filing
   it restates, which is the point of reading ``filed`` rather than ``end``.
3. **Form filter** — ``forms=("10-K", "10-Q")`` restricts the field to periodic
   reports. The filter is opt-in: an 8-K or a 20-F row is a real fact, and the
   caller decides whether it belongs in its slice.

Duplicates
----------
The identity of an observation is ``(tag, unit, period_end, accession)``. The
same identity twice is one fact, and the **first row in document order** is
kept — the parser does not pick the larger of two rows it cannot tell apart.

One honest limitation: the duration is not modelled
---------------------------------------------------
XBRL distinguishes a three-month figure from the six-month year-to-date figure
that ends on the same day, using ``start``. :class:`FactObservation` follows
the engine's period-end vocabulary and keeps only ``end``, so when one
accession reports both durations for the same date the first row wins. This
slice answers "what did the filing report for this date", not "what is the
quarterly run-rate"; the TTM arithmetic lives in the provider adapter, which
reads ``start`` itself.

No I/O
------
This module performs no network or database access. It parses a body somebody
else fetched, which is what makes it testable against the real Apple document
in ``tests/fixtures/sec_companyfacts_aapl.json``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.engine.model import Cell, CellStatus, Provenance, Unit
from app.engine.sources.sec import SOURCE_NAME

#: The namespace this slice reads. ``dei`` (share counts on the cover page) and
#: ``ifrs-full`` are different vocabularies and are deliberately out of scope.
US_GAAP_NAMESPACE = "us-gaap"

#: ``companyfacts`` units that mean the same thing here as in the engine.
UNIT_USD = "USD"
UNIT_SHARES = "shares"
#: Per-share amounts (``eps_diluted``) are quoted as dollars per share. The
#: engine has no per-share unit, so the cell reports ``Unit.UNKNOWN`` with the
#: raw unit preserved on the observation.
UNIT_PER_SHARE = "USD/shares"

#: Units accepted at the parse boundary. ``EUR``, ``pure`` and every other
#: branch would need a conversion the document does not supply, so they are
#: skipped rather than relabelled.
ACCEPTED_UNITS: frozenset[str] = frozenset({UNIT_USD, UNIT_SHARES, UNIT_PER_SHARE})

#: Engine unit for each accepted XBRL unit. A per-share amount has no canonical
#: engine unit; :class:`Unit.UNKNOWN` says so instead of pretending it is USD.
_CELL_UNITS: Mapping[str, Unit] = {
    UNIT_USD: Unit.USD,
    UNIT_SHARES: Unit.SHARES,
}

#: Canonical engine key → us-gaap concepts that may answer it, primary first.
#:
#: The order is the tie-break documented in the module docstring, so it is part
#: of the contract: the first concept an issuer actually uses wins, and the
#: rest are fallbacks for issuers that tag the same economic fact differently.
#: ``total_debt`` is one reported concept (``LongTermDebt``, which includes the
#: current portion), not a sum: this slice does not add facts together.
DEFAULT_TAGS: Mapping[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "net_income": (
        "NetIncomeLoss",
        "ProfitLoss",
    ),
    "eps_diluted": (
        "EarningsPerShareDiluted",
        "EarningsPerShareBasicAndDiluted",
    ),
    "assets": ("Assets",),
    "liabilities": ("Liabilities",),
    "equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "cash_and_equivalents": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    "total_debt": (
        "LongTermDebt",
        "LongTermDebtNoncurrent",
    ),
    "shares_outstanding": (
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
    ),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "capex": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ),
}


@dataclass(frozen=True, slots=True)
class FactObservation:
    """One number exactly as one accession reported it.

    ``unit`` is the XBRL unit string (``"USD"``, ``"shares"``, ``"USD/shares"``),
    not the engine's :class:`~app.engine.model.Unit`: the raw vocabulary is what
    an audit needs, and the projection to a canonical unit happens in
    :func:`observations_to_cells`.

    ``filed`` and ``accession`` are what make a fact checkable; ``frame`` is
    present only when EDGAR supplied a calendar frame. Optional metadata is
    ``None`` (or empty) when the row did not carry it — never guessed.
    """

    tag: str
    value: float
    unit: str
    period_end: str
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    form: str = ""
    filed: str = ""
    accession: str = ""
    frame: str | None = None

    def __post_init__(self) -> None:
        # Construction is the parser's job, but a value object that silently
        # holds a string in a float field would poison every downstream sum.
        if isinstance(self.value, bool):
            raise TypeError("a fact value must be a number, not a bool")
        object.__setattr__(self, "value", float(self.value))
        object.__setattr__(self, "tag", _text(self.tag))
        object.__setattr__(self, "unit", _text(self.unit))
        object.__setattr__(self, "period_end", _text(self.period_end))
        object.__setattr__(self, "form", _text(self.form))
        object.__setattr__(self, "filed", _text(self.filed))
        object.__setattr__(self, "accession", _text(self.accession))
        object.__setattr__(self, "fiscal_period", _optional_text(self.fiscal_period))
        object.__setattr__(self, "frame", _optional_text(self.frame))
        if self.fiscal_year is not None:
            object.__setattr__(self, "fiscal_year", _optional_year(self.fiscal_year))


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


def _text(value: Any) -> str:
    """The stripped string a JSON field holds, or ``""`` when it holds none."""

    return value.strip() if isinstance(value, str) else ""


def _optional_text(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _optional_year(value: Any) -> int | None:
    """A fiscal year as a positive int, or ``None`` if the field is not one."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            number = int(text)
            return number if number > 0 else None
    return None


def _finite_number(value: Any) -> float | None:
    """``value`` as a finite float, or ``None`` if it is blank/infinite/text.

    EDGAR quotes numbers as JSON numbers, but a parser that only accepts
    ``int``/``float`` breaks on the occasional quoted numeral; a numeric string
    is the same fact. A blank string, a word, ``NaN`` and the infinities are
    not facts: a value that cannot be compared is missing, not zero.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _tag_names(group: Any) -> tuple[str, ...]:
    """Normalise one mapping value to a tuple of tag names.

    A bare string is accepted as a one-tag group so a caller cannot accidentally
    iterate the characters of ``"Revenues"``.
    """

    if isinstance(group, str):
        candidates: Sequence[Any] = (group,)
    elif isinstance(group, Sequence):
        candidates = group
    else:
        return ()
    names: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        name = candidate.strip()
        if name and name not in names:
            names.append(name)
    return tuple(names)


def _form_filter(forms: Sequence[str] | str | None) -> frozenset[str] | None:
    """The forms a caller asked for, upper-cased, or ``None`` for "any form"."""

    if forms is None:
        return None
    if isinstance(forms, str):
        forms = (forms,)
    return frozenset(
        form.strip().upper() for form in forms if isinstance(form, str) and form.strip()
    )


def _recency(observation: FactObservation) -> tuple[str, str, str, float]:
    """The deterministic "newest wins" key: filed, then period end, then id."""

    return (
        observation.filed,
        observation.period_end,
        observation.accession,
        observation.value,
    )


def _latest(
    observations: Sequence[FactObservation],
    tag_names: Sequence[str],
    forms: frozenset[str] | None,
) -> FactObservation | None:
    """Newest observation among ``tag_names`` in precedence order, or ``None``."""

    for tag in tag_names:
        candidates = [
            observation
            for observation in observations
            if observation.tag == tag
            and (forms is None or observation.form.upper() in forms)
        ]
        if candidates:
            return max(candidates, key=_recency)
    return None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_companyfacts(
    payload: Any,
    *,
    tags: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[FactObservation, ...]:
    """Extract every usable us-gaap observation from a ``companyfacts`` body.

    ``payload`` is the decoded document (this function never fetches). Anything
    that is not shaped like ``{"facts": {"us-gaap": {tag: {"units": ...}}}}``
    answers ``()``: a body we do not recognise is not a body we guess at.

    ``tags`` restricts the parse to the concepts a caller cares about, using the
    same canonical-key → concept-names mapping as :data:`DEFAULT_TAGS`. When it
    is ``None`` every us-gaap concept in the document is parsed, because a
    parser that silently drops concepts is a parser that silently loses
    metrics; the canonical selection happens in :func:`observations_to_cells`.

    Rows are skipped when they lack a tag, a finite value or a period end, and
    when their unit is not one this module can honour. The result is deduplicated
    on ``(tag, unit, period_end, accession)`` (first row in document order wins)
    and sorted by ``(tag, unit, period_end, accession, filed, value)``, so two
    parses of one document are equal tuples regardless of JSON key order.
    """

    if not isinstance(payload, Mapping):
        return ()
    facts = payload.get("facts")
    if not isinstance(facts, Mapping):
        return ()
    namespace = facts.get(US_GAAP_NAMESPACE)
    if not isinstance(namespace, Mapping):
        return ()

    allowed: frozenset[str] | None = None
    if tags is not None:
        allowed = frozenset(
            name for group in tags.values() for name in _tag_names(group)
        )

    observations: list[FactObservation] = []
    seen: set[tuple[str, str, str, str]] = set()

    for raw_tag, body in namespace.items():
        if not isinstance(raw_tag, str):
            continue
        tag = raw_tag.strip()
        if not tag:
            continue
        if allowed is not None and tag not in allowed:
            continue
        if not isinstance(body, Mapping):
            continue
        units = body.get("units")
        if not isinstance(units, Mapping):
            continue

        for raw_unit, rows in units.items():
            if not isinstance(raw_unit, str):
                continue
            unit = raw_unit.strip()
            if unit not in ACCEPTED_UNITS:
                continue
            if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
                continue

            for entry in rows:
                if not isinstance(entry, Mapping):
                    continue
                value = _finite_number(entry.get("val"))
                if value is None:
                    continue
                period_end = _text(entry.get("end"))
                if not period_end:
                    continue
                accession = _text(entry.get("accn"))
                identity = (tag, unit, period_end, accession)
                if identity in seen:
                    continue
                seen.add(identity)
                observations.append(
                    FactObservation(
                        tag=tag,
                        value=value,
                        unit=unit,
                        period_end=period_end,
                        fiscal_year=_optional_year(entry.get("fy")),
                        fiscal_period=_optional_text(entry.get("fp")),
                        form=_text(entry.get("form")),
                        filed=_text(entry.get("filed")),
                        accession=accession,
                        frame=_optional_text(entry.get("frame")),
                    )
                )

    observations.sort(
        key=lambda observation: (
            observation.tag,
            observation.unit,
            observation.period_end,
            observation.accession,
            observation.filed,
            observation.value,
        )
    )
    return tuple(observations)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def latest_observation(
    observations: Sequence[FactObservation],
    key: str,
    *,
    forms: Sequence[str] | None = None,
) -> FactObservation | None:
    """The newest observation for canonical ``key``, or ``None``.

    ``key`` is a :data:`DEFAULT_TAGS` key (``"revenue"``, ``"total_debt"``, …).
    A name outside the map is treated as a raw us-gaap tag, so a caller can read
    a concept this slice has no canonical key for; if neither matches anything
    the answer is ``None``. There is no fallback value and no zero.

    ``forms`` optionally restricts the field to a set of filing forms, matched
    case-insensitively; ``forms=()`` matches nothing, which is an explicit
    request rather than a typo to paper over.
    """

    if not isinstance(key, str):
        return None
    name = key.strip()
    if not name:
        return None
    tag_names = _tag_names(DEFAULT_TAGS.get(name.lower()))
    if not tag_names:
        tag_names = (name,)

    return _latest(tuple(observations), tag_names, _form_filter(forms))


def observations_to_cells(
    observations: Sequence[FactObservation],
    *,
    tags: Mapping[str, tuple[str, ...]] | None = None,
    forms: Sequence[str] | None = None,
) -> dict[str, Cell]:
    """One :class:`Cell` per canonical key that has a usable observation.

    The winner is chosen by :func:`latest_observation`'s rules (tag precedence,
    then recency). Provenance is the filing, not the parse: ``source="sec"``,
    ``as_of`` is the observation's ``period_end`` (falling back to ``filed``
    only if a hand-built observation has no period end), ``url=None`` because a
    companyfacts row carries no document URL, and ``inputs`` is
    ``(tag, accession)`` so the number can be traced back to one filing.

    A key with no observation is **omitted**. It is never a zero and never a
    null placeholder: a consumer must be able to tell "we did not find it" from
    "the company reported zero", and only omission is honest about that here.
    Keys whose observations all lack a date are omitted too, because
    :class:`~app.engine.model.Provenance` cannot exist without an ``as_of``.
    """

    mapping = DEFAULT_TAGS if tags is None else tags
    items = tuple(observations)
    allowed_forms = _form_filter(forms)

    cells: dict[str, Cell] = {}
    for raw_key, group in mapping.items():
        if not isinstance(raw_key, str):
            continue
        key = raw_key.strip().lower()
        if not key or key in cells:
            continue
        observation = _latest(items, _tag_names(group), allowed_forms)
        if observation is None:
            continue
        as_of = observation.period_end or observation.filed
        if not as_of:
            continue
        cells[key] = Cell(
            value=observation.value,
            unit=_CELL_UNITS.get(observation.unit, Unit.UNKNOWN),
            provenance=Provenance(
                source=SOURCE_NAME,
                as_of=as_of,
                status=CellStatus.OK,
                url=None,
                inputs=(observation.tag, observation.accession),
            ),
        )
    return cells


def fact_summary(
    payload: Any,
    *,
    tags: Mapping[str, tuple[str, ...]] | None = None,
    forms: Sequence[str] | None = None,
) -> dict[str, Cell]:
    """``observations_to_cells(parse_companyfacts(payload))``, spelled once.

    The convenience entry point for a caller that has a decoded
    ``companyfacts`` body and wants the canonical cells without holding the
    intermediate observations.
    """

    return observations_to_cells(
        parse_companyfacts(payload, tags=tags),
        tags=tags,
        forms=forms,
    )


__all__ = [
    "ACCEPTED_UNITS",
    "DEFAULT_TAGS",
    "FactObservation",
    "UNIT_PER_SHARE",
    "UNIT_SHARES",
    "UNIT_USD",
    "US_GAAP_NAMESPACE",
    "fact_summary",
    "latest_observation",
    "observations_to_cells",
    "parse_companyfacts",
]
