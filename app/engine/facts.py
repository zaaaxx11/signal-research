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

1. **Recency across aliases** — :data:`DEFAULT_TAGS` lists one or more us-gaap
   names per engine key, primary first. All aliases are candidates, and the
   newest filing wins: an issuer that stopped using a primary concept must not
   keep publishing its last 2022 value while a current alias exists. The tag
   order breaks ties only when two observations have the same filed date,
   period end, accession and value.
2. **Recency within a tag** — newest ``filed`` wins, then newest ``period_end``,
   then ``accession`` and ``value`` purely to make the choice reproducible. A
   later accession therefore beats the original filing it restates, which is
   the point of reading ``filed`` rather than ``end``.
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
that ends on the same day, using ``start``. :class:`FactObservation` keeps
``start`` so the parser does not collapse those two rows; :func:`fact_summary`
still answers "what did the filing report for this date", while
:func:`fact_ttm_summary` uses the duration to build a trailing-twelve-month
value. No provider adapter has to re-read the raw document for that arithmetic.

No I/O
------
This module performs no network or database access. It parses a body somebody
else fetched, which is what makes it testable against the real Apple document
in ``tests/fixtures/sec_companyfacts_aapl.json``.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
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
    # --- R4 fundamentals depth -------------------------------------------
    # Appended, never inserted: the order above is a published contract, and a
    # consumer that walks this mapping must keep seeing the shipped eleven keys
    # first. Each group is the contract's concept list, primary first, and no
    # alias was invented — a concept the filing does not name is a metric this
    # slice declines to guess at.
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    # Two ways to tag the same expense line: ``CostOfRevenue`` for a single
    # consolidated line, ``CostOfGoodsAndServicesSold`` for a split presenter.
    "cost_of_revenue": ("CostOfRevenue", "CostOfGoodsAndServicesSold"),
    "eps_basic": ("EarningsPerShareBasic",),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "inventory": ("InventoryNet",),
    # ``ReceivablesNetCurrent`` is the post-ASU-2016-13 spelling that folds
    # allowances into the net figure; the older accounts-only concept is the
    # primary because it is the one most issuers still tag.
    "receivables": ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent"),
    "ppe": ("PropertyPlantAndEquipmentNet",),
    "long_term_debt": ("LongTermDebtNoncurrent",),
    # A negative retained earnings is an accumulated deficit, not a parse
    # error: the concept is signed and the sign is kept.
    "retained_earnings": ("RetainedEarningsAccumulatedDeficit",),
    "dividends_paid": ("PaymentsOfDividends", "PaymentsOfDividendsCommonStock"),
    "buybacks": ("PaymentsForRepurchaseOfCommonStock",),
    "depreciation_amortization": (
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
    ),
    "interest_expense": ("InterestExpense", "InterestExpenseNonoperating"),
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
    start: str | None = None
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
        object.__setattr__(self, "tag", _shared(_text(self.tag)))
        object.__setattr__(self, "unit", _text(self.unit))
        object.__setattr__(self, "period_end", _text(self.period_end))
        object.__setattr__(self, "start", _optional_text(self.start))
        object.__setattr__(self, "form", _text(self.form))
        object.__setattr__(self, "filed", _text(self.filed))
        object.__setattr__(self, "accession", _shared(_text(self.accession)))
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


#: Longest string that may enter the intern pool. An accession is 20 characters
#: and a us-gaap tag stays under 70; a document URL or a free-text description is
#: well past this, and is exactly the kind of value that must stay out of the
#: pool (see :func:`_shared`).
_SHARED_MAX_LEN = 96


def _shared(text: str) -> str:
    """One object per distinct value, for a field that repeats row over row.

    A symbol's facts quote the same handful of strings thousands of times: the
    us-gaap ``tag`` comes from a vocabulary of a few dozen names, and the
    ``accession`` number, once filed, is cited again by every row of that filing
    and by every symbol whose cells were built from it. Parsed or restored row
    by row, each occurrence allocates its own copy, and the copies -- not the
    numbers -- are what the snapshot pays for (measured: 28,580 ``inputs``
    strings over 400 symbols for 1,713 distinct values, 94% duplication).
    Interning shares one object per distinct value, and the pool is bounded by
    that vocabulary, so nothing unbounded is kept alive. The value is identical
    either way; only the identity of the string object changes.

    Boundary rule -- what may enter the pool:

    * **Short, repeated tokens only**: ``tag``, ``accession`` and the elements of
      :attr:`~app.engine.model.Provenance.inputs`, which are exactly those two
      things. Each is at most :data:`_SHARED_MAX_LEN` characters and is drawn
      from a closed vocabulary (concepts, accession numbers).
    * **Never long or near-unique text**: a document ``url``, a free-text
      description or a ``segments`` blob is long and almost always distinct, so
      interning it would grow the pool for no reuse. Those are returned
      untouched.

    The empty string is returned as is: it is already a singleton.
    """

    if not text or len(text) > _SHARED_MAX_LEN:
        return text
    return sys.intern(text)


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
    """Newest observation across ``tag_names``, with tag order as a tie-break.

    Recency is the primary rule because issuers retire concepts: NVIDIA's
    ``RevenueFromContractWithCustomerExcludingAssessedTax`` ends in fiscal 2022
    while ``Revenues`` is current, and choosing the first tag with *any* row
    would publish a four-year-old revenue figure. A primary tag still wins when
    two aliases were filed on the same day and report the same period, so the
    preference remains deterministic where recency cannot decide.
    """

    preferred = {tag: index for index, tag in enumerate(tag_names)}
    candidates = [
        observation
        for observation in observations
        if observation.tag in preferred
        and (forms is None or observation.form.upper() in forms)
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda observation: (
            _recency(observation),
            -preferred[observation.tag],
        ),
    )


#: Metrics that are **flows** and can be converted to a trailing-twelve-month
#: value by period arithmetic. Everything else in :data:`DEFAULT_TAGS` is an
#: instant (a balance or a share count) and uses the newest observation.
#:
#: The R4 duration metrics join this set for one reason: an income-statement
#: figure reported in a 10-Q is a quarter or a year-to-date slice, so handing
#: back the newest observation as a "run rate" would publish three months as
#: twelve. Balances (``current_assets``, ``inventory``, ``long_term_debt`` …)
#: stay out: a point-in-time stock summed over a year is meaningless.
FLOW_KEYS: frozenset[str] = frozenset(
    {
        "revenue",
        "net_income",
        "eps_diluted",
        "operating_cash_flow",
        "capex",
        # --- R4 duration metrics ---
        "gross_profit",
        "operating_income",
        "cost_of_revenue",
        "eps_basic",
        "dividends_paid",
        "buybacks",
        "depreciation_amortization",
        "interest_expense",
    }
)

_ANNUAL_MIN_DAYS = 330
_ANNUAL_MAX_DAYS = 400
_YTD_MIN_DAYS = 150
_YTD_MAX_DAYS = 330
_PRIOR_MIN_GAP_DAYS = 330
_PRIOR_MAX_GAP_DAYS = 400
_PRIOR_DURATION_TOLERANCE_DAYS = 14


def _period_days(observation: FactObservation) -> int | None:
    """The duration of an observation in days, or ``None`` when it has no start.

    XBRL's ``start`` is what distinguishes a quarter from the year-to-date row
    that ends on the same date. A malformed date is not a duration: returning
    ``None`` keeps that row out of the arithmetic rather than guessing a
    quarter.
    """

    start = observation.start
    end = observation.period_end
    if not start or not end:
        return None
    try:
        first = date.fromisoformat(start)
        last = date.fromisoformat(end)
    except ValueError:
        return None
    days = (last - first).days
    return days if days > 0 else None


def _observation_cell(
    observation: FactObservation,
    *,
    as_of: str | None = None,
    value: float | None = None,
    inputs: tuple[str, ...] | None = None,
) -> Cell | None:
    """Project one observation (or a derived sum of observations) into a Cell.

    ``inputs`` is the audit trail. A single observation contributes its tag and
    accession; a TTM value contributes the annual, current and prior filings so
    the arithmetic can be replayed. The value is never re-dated: ``as_of`` is
    the period end of the observation the number describes.
    """

    date_of_value = as_of or observation.period_end or observation.filed
    if not date_of_value:
        return None
    published = observation.value if value is None else value
    trail = inputs or (observation.tag, observation.accession)
    return Cell(
        value=published,
        unit=_CELL_UNITS.get(observation.unit, Unit.UNKNOWN),
        provenance=Provenance(
            source=SOURCE_NAME,
            as_of=date_of_value,
            status=CellStatus.OK,
            url=None,
            inputs=tuple(_shared(item) for item in trail),
        ),
    )


def _ttm_cell(candidates: Sequence[FactObservation]) -> Cell | None:
    """A trailing-twelve-month Cell for one flow metric, or the best fallback.

    The preferred path is ``annual + year-to-date - prior-year year-to-date``:
    it uses exactly three filed observations, keeps each accession in the
    provenance, and works for issuers whose latest filing is a Q2/Q3 10-Q (the
    common case during the year). If the latest filing is itself an annual
    report, that annual value **is** the TTM. If the prior YTD row is absent,
    the annual value is returned rather than inventing a fourth quarter.
    """

    if not candidates:
        return None

    latest = max(candidates, key=_recency)
    latest_days = _period_days(latest)
    if latest_days is not None and _ANNUAL_MIN_DAYS <= latest_days <= _ANNUAL_MAX_DAYS:
        return _observation_cell(latest)

    annual = max(
        (
            observation
            for observation in candidates
            if (days := _period_days(observation)) is not None
            and _ANNUAL_MIN_DAYS <= days <= _ANNUAL_MAX_DAYS
        ),
        key=_recency,
        default=None,
    )
    ytd = max(
        (
            observation
            for observation in candidates
            if (days := _period_days(observation)) is not None
            and _YTD_MIN_DAYS <= days < _YTD_MAX_DAYS
        ),
        key=_recency,
        default=None,
    )
    if annual is None or ytd is None or annual.tag != ytd.tag:
        return _observation_cell(annual or latest)

    ytd_days = _period_days(ytd)
    prior = max(
        (
            observation
            for observation in candidates
            if observation.tag == ytd.tag
            and (days := _period_days(observation)) is not None
            and abs(days - (ytd_days or 0)) <= _PRIOR_DURATION_TOLERANCE_DAYS
            and (gap := _days_between(observation.period_end, ytd.period_end))
            is not None
            and _PRIOR_MIN_GAP_DAYS <= gap <= _PRIOR_MAX_GAP_DAYS
            and observation.period_end < ytd.period_end
        ),
        key=_recency,
        default=None,
    )
    if prior is None:
        return _observation_cell(annual)

    return _observation_cell(
        ytd,
        value=annual.value + ytd.value - prior.value,
        inputs=(
            annual.tag,
            annual.accession,
            ytd.tag,
            ytd.accession,
            prior.tag,
            prior.accession,
        ),
    )


def _days_between(first: str, second: str) -> int | None:
    """Calendar days from ``first`` to ``second``, or ``None`` on bad text."""

    try:
        return (date.fromisoformat(second) - date.fromisoformat(first)).days
    except ValueError:
        return None


def ttm_observations_to_cells(
    observations: Sequence[FactObservation],
    *,
    tags: Mapping[str, tuple[str, ...]] | None = None,
    forms: Sequence[str] | None = None,
) -> dict[str, Cell]:
    """One Cell per canonical key, with flows expressed as TTM.

    Instant metrics use the newest observation; flow metrics use
    :func:`_ttm_cell`. A missing value is still omitted, never zero, and a
    cell's ``as_of`` is still the period end of the number it contains — a TTM
    built from three filings is dated to the current YTD end.
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
        tag_names = _tag_names(group)
        candidates = [
            observation
            for observation in items
            if observation.tag in tag_names
            and (allowed_forms is None or observation.form.upper() in allowed_forms)
        ]
        if not candidates:
            continue
        if key in FLOW_KEYS:
            cell = _ttm_cell(candidates)
        else:
            cell = _observation_cell(max(candidates, key=_recency))
        if cell is not None:
            cells[key] = cell
    return cells


def fact_ttm_summary(
    payload: Any,
    *,
    tags: Mapping[str, tuple[str, ...]] | None = None,
    forms: Sequence[str] | None = None,
) -> dict[str, Cell]:
    """``ttm_observations_to_cells(parse_companyfacts(payload))``.

    The convenience entry point for ingestion. Use :func:`fact_summary` when
    the question is "what did the latest filing say on this date"; use this
    when the question is "what is the current run-rate".
    """

    return ttm_observations_to_cells(
        parse_companyfacts(payload, tags=tags),
        tags=tags,
        forms=forms,
    )


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
    seen: set[tuple[str, str, str, str, str]] = set()

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
                start = _text(entry.get("start"))
                identity = (tag, unit, start, period_end, accession)
                if identity in seen:
                    continue
                seen.add(identity)
                observations.append(
                    FactObservation(
                        tag=tag,
                        value=value,
                        unit=unit,
                        period_end=period_end,
                        start=start or None,
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
            observation.start or "",
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

    The winner is the newest filing across every alias in the canonical key's
    group, with :data:`DEFAULT_TAGS` order as the tie-break. A raw us-gaap tag
    outside the map is also accepted, so a caller can read a concept this slice
    has no canonical key for; if neither matches anything the answer is
    ``None``. There is no fallback value and no zero.

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

    The winner is chosen by :func:`latest_observation`'s rules (newest filing across aliases; tag order is a tie-break). Provenance is the filing, not the parse: ``source="sec"``,
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
                inputs=tuple(
                    _shared(item)
                    for item in (observation.tag, observation.accession)
                ),
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
    "FLOW_KEYS",
    "FactObservation",
    "UNIT_PER_SHARE",
    "UNIT_SHARES",
    "UNIT_USD",
    "US_GAAP_NAMESPACE",
    "fact_summary",
    "fact_ttm_summary",
    "latest_observation",
    "observations_to_cells",
    "parse_companyfacts",
    "ttm_observations_to_cells",
]
