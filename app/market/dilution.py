"""The shares-outstanding history the dilution control would need — derived, not sourced.

**This is a derivation, not a new source.** It adds no network and no provider: it
reads the cached ``companyfacts`` document the ingest pass already wrote, through
:func:`app.engine.ingest.load_cached_companyfacts` — the same read-only loader the
rest of the engine uses — and composes the ``dei:EntityCommonStockSharesOutstanding``
rows into a timeseries. Nothing here fetches, writes or caches anything.

Why it exists
-------------
The screener's ``sh_dilution`` control ("Dilution Risk") is published
**unavailable** with this reason:

needs dilution wiring: app/market/dilution.py can derive a share-count series from the SEC companyfacts cache (dei:EntityCommonStockSharesOutstanding per filing), answering first/last/pct_change/count/as_of — wiring it into the scan needs symbol→CIK resolution and an engine cache-dir passthrough, both available, awaiting integration

That sentence is this module's whole contract, quoted verbatim so the code and the
document cannot describe different gaps. The scan carries one count; the filings
carry the series. This module reads the series. **It does not flip the control this
round** — wiring is a separate change, described below — because reading the series
is what has to exist, and be proven, first.

What the answer is
------------------
A filing series is a **timeseries**, so :class:`DilutionSeries` answers first, last,
``pct_change`` from first to last, the filing ``count`` those numbers came from,
``skipped`` (rows dropped and *counted*, so "5 rows, 3 of them garbage" reports
itself as such) and ``as_of`` — the **newest filing date**, never the clock the
question was asked at. Every number carries its filing count: ``count`` is always
``len(points)``.

The rules
---------
* **Missing is ``None`` plus a reason, never ``0.0``.** No cached document, no
  ``dei`` series, or every row unusable each produce their own
  :data:`REASON_NO_DOCUMENT` / :data:`REASON_NO_TAG` / :data:`REASON_ALL_UNUSABLE`
  sentence. An absent history is not a flat history.
* **One filed count is a level, not a trend.** ``first`` and ``last`` are still
  answered, ``count`` is 1, and ``pct_change`` is refused with
  :data:`REASON_ONE_POINT`: a change needs two filings, and inventing one from one
  row is the same invention the control refuses.
* **``pct_change`` is first→last, not min→max.** An issuer that issued shares and
  then bought some back ends above zero or below it depending on the endpoints;
  the peak is not the dilution, and the trough is not the recovery.
* **Recency, then restatement.** ``companyfacts`` repeats one period end across
  filings — the 10-K, the next year's comparative, a restatement — so rows collapse
  by period end and the newest ``filed`` wins, the accession breaking a tie the
  dates cannot separate. A restated period is that period again, not a second
  observation, and the choice is reproducible.
* **A share count must be a share count.** Rows are read only under the ``shares``
  unit: a count quoted in USD is not a count. A negative, zero, blank, non-numeric,
  non-finite or undated row is skipped — and counted in ``skipped`` — rather than
  admitted as a level or silently dropped.
* **Order is by period end**, whatever order the JSON rows arrive in, so a
  re-serialised document reads identically.

What a future wiring change would need
--------------------------------------
Flipping ``sh_dilution`` to available is **not** this round and is not this module's
call. It needs, in order:

1. **The document half** — ``app/screen/descriptive.py``: the ``Control`` for
   ``sh_dilution`` (~lines 1276-1290, ``available=False`` with
   ``reason=_DILUTION_REASON`` and ``_disabled_options(...)``), the
   :data:`~app.screen.descriptive._DILUTION_REASON` constant itself (~lines
   730-735, quoted verbatim above) and the ``VENDORED_KEYS`` note (~lines
   695-696). The control would take real options from
   ``app/screen/data/descriptive_options.json`` (which already publishes its
   vendor labels) instead of disabled ones.
2. **The refusal half** — ``app/screen/filter_fields.py``'s :data:`UNAVAILABLE`
   table and the screen taxonomy the tests lock (``tests/test_screen_taxonomy.py``,
   ``tests/test_screen_descriptive.py``) assert today that the key cannot run; the
   422 path and the filter compiler that maps a request onto a scan predicate have
   to learn the new options too.
3. **A caller with a CIK and the cache** — the scan path does not resolve symbols
   to CIKs, and the ``companyfacts`` cache lives wherever the ingest pass wrote it
   (``MEE_ENGINE_CACHE_DIR``). Someone must decide where that join happens; this
   module deliberately stops at ``(cache_dir, cik)``, the same shape as
   :func:`~app.engine.ingest.load_cached_companyfacts`, so no second identity or
   cache mechanism is invented here.
4. **The decision this module will not make** — *what counts as dilution risk.*
   Finviz's metric is described as "based on offering ability, cash need, overhang
   and dilution history"; this module answers only the history. The thresholds
   that turn ``pct_change`` into the select's option ids are a policy call for the
   wiring round, and until it is made the honest answer is the refusal above.

No I/O: this module parses a body somebody else cached. The only import from the
engine layer is the loader; the only fact it reads is the ``dei`` share count.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.engine.ingest import load_cached_companyfacts

#: The namespace of the cover-page share count. ``us-gaap`` and ``ifrs-full`` are
#: different vocabularies; the dilution series is a ``dei`` fact, which is why
#: :func:`~app.engine.facts.parse_companyfacts` (us-gaap only) is not the reader
#: here — this module parses the rows itself rather than re-scoping that one.
DEI_NAMESPACE = "dei"

#: The concept the published refusal names. The backticked spelling in the
#: refusal, ``dei:EntityCommonStockSharesOutstanding``, is the namespaced form
#: of this pair; the document's own key under ``facts["dei"]`` is the bare tag.
SHARES_TAG = "EntityCommonStockSharesOutstanding"

#: The only unit a share count is accepted in. A count quoted in USD is not a
#: count; accepting one would put a cash figure in a share series.
SHARES_UNIT = "shares"

#: No usable document on disk. Being on disk is not evidence — the loader's own
#: rule (see :func:`app.engine.ingest.load_cached_companyfacts`) — so an absent,
#: corrupt or foreign entry reads as this, never as a series with no rows in it.
REASON_NO_DOCUMENT = (
    "the ingest pass has cached no companyfacts document for this CIK"
)

#: A readable document that carries no share-count series: the filings simply do
#: not name the concept. This is a fact about the issuer, not a gap in the cache.
REASON_NO_TAG = (
    "the filings carry no dei EntityCommonStockSharesOutstanding series"
)

#: Every row the filings do carry is unusable — negative, zero, non-numeric,
#: non-finite, undated or not quoted in shares. The count of skipped rows travels
#: with the reason, because "we dropped everything" is only honest with its size.
REASON_ALL_UNUSABLE = (
    "every share-count row was unusable — negative, zero, non-numeric, "
    "non-finite, undated or not quoted in shares"
)

#: One filed count on file: first and last are answered, the trend is refused.
#: A change needs two filings, and computing one from one row is exactly the
#: invention :data:`app.screen.descriptive._DILUTION_REASON` refuses.
REASON_ONE_POINT = (
    "one filed share count is a level, not a series — a trend needs at least "
    "two filings"
)


@dataclass(frozen=True, slots=True)
class ShareCountPoint:
    """One filed share count: the value, the period it describes and its filing.

    ``period_end`` is the instant the count describes (the cover-page date) and
    ``filed`` the accession's filing date — the two dates the dedupe and the
    ``as_of`` rule read. ``accession`` is kept so a number can be traced back to
    exactly one filing, the same audit trail the rest of the SEC layer keeps.
    """

    period_end: str
    value: float
    filed: str
    accession: str


@dataclass(frozen=True, slots=True)
class DilutionSeries:
    """The shares-outstanding history of one issuer, or an honest absence.

    ``points`` is the deduplicated series, ordered oldest to newest; ``count`` is
    its length — the filing count behind ``first``/``last`` — and ``skipped`` the
    rows dropped as unusable, so a consumer can tell a clean series from one
    mined out of garbage. ``first``/``last`` are the series' endpoints, never
    ``min``/``max``; ``pct_change`` is ``(last − first) / first`` in percent, so
    positive means the count grew.

    ``as_of`` is the newest **filing** date on file, never the clock: the answer
    is only as fresh as the filing it came from. ``reason`` says why there is no
    series (``None`` when there is one, including the one-point case); ``trend_reason``
    says why there is no trend — a one-point series has a level but no change.
    """

    points: tuple[ShareCountPoint, ...]
    first: float | None
    last: float | None
    pct_change: float | None
    count: int
    skipped: int
    as_of: str | None
    reason: str | None
    trend_reason: str | None


def _text(value: Any) -> str:
    """The stripped string a JSON field holds, or ``""`` when it holds none."""

    return value.strip() if isinstance(value, str) else ""


def _finite_positive(value: Any) -> float | None:
    """``value`` as a finite float greater than zero, or ``None``.

    A quoted numeral is the same fact — the SEC parser accepts it, so this one
    does too. Everything else is unusable data rather than a level: ``bool`` is
    refused as a value (``True`` would read as one share), a blank or prose
    string, ``None``, a list, ``NaN`` and the infinities are not counts, and a
    zero or negative count is not a count either — a zero would also make the
    first→last change divide by nothing.
    """

    if isinstance(value, bool):
        return None
    number: float | None
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
    if number is None or not math.isfinite(number) or number <= 0:
        return None
    return number


def _tag_rows(payload: Any) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    """Every row the ``dei`` tag carries, as ``(unit, row)`` pairs.

    A body that is not shaped like ``{"facts": {"dei": {tag: {"units": ...}}}}``
    yields nothing — a document we do not recognise is not one we guess at.
    Rows come back with their unit attached, across every unit the tag was
    filed under, because a row filed under the *wrong* unit is still a row the
    filing produced: skipping it silently would let a USD-quoted count vanish
    from the series without a trace, while counting it as skipped is honest
    about what the document holds. Rows that are not mappings are not rows.
    """

    if not isinstance(payload, Mapping):
        return ()
    facts = payload.get("facts")
    if not isinstance(facts, Mapping):
        return ()
    namespace = facts.get(DEI_NAMESPACE)
    if not isinstance(namespace, Mapping):
        return ()
    body = namespace.get(SHARES_TAG)
    if not isinstance(body, Mapping):
        return ()
    units = body.get("units")
    if not isinstance(units, Mapping):
        return ()
    found: list[tuple[str, Mapping[str, Any]]] = []
    for raw_unit, rows in units.items():
        if not isinstance(raw_unit, str) or not raw_unit.strip():
            continue
        if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
            continue
        for row in rows:
            if isinstance(row, Mapping):
                found.append((raw_unit.strip(), row))
    return tuple(found)


def _point_from(row: Mapping[str, Any]) -> ShareCountPoint | None:
    """One usable point from one row, or ``None`` for a row the series cannot take.

    A period end is mandatory — an undated count cannot be placed on a timeline,
    and borrowing the filing date would date the value to the report instead of
    the instant it describes. The value must be a finite positive number
    (:func:`_finite_positive`). ``filed`` and ``accession`` are kept as text,
    blank when the row carried none, because they are the dedupe's tie-break
    rather than the point's proof of existence.
    """

    period_end = _text(row.get("end"))
    if not period_end:
        return None
    value = _finite_positive(row.get("val"))
    if value is None:
        return None
    return ShareCountPoint(
        period_end=period_end,
        value=value,
        filed=_text(row.get("filed")),
        accession=_text(row.get("accn")),
    )


def _restatement_key(point: ShareCountPoint) -> tuple[str, str]:
    """Which of two rows for one period end is the newer filing.

    The later ``filed`` wins — that is what makes a restatement replace the
    original — and the accession breaks the tie when the dates are equal, so the
    choice is reproducible rather than document-order-dependent. Both are plain
    strings compared lexicographically; ISO dates sort as dates.
    """

    return (point.filed, point.accession)


def _dedupe_by_period(
    points: list[ShareCountPoint],
) -> dict[str, ShareCountPoint]:
    """Collapse the rows to one per period end, newest filing surviving.

    ``companyfacts`` reports one instant across several filings — the original
    10-K, the next year's comparative, a correction — and each is the same fact
    again, not a second observation. Collapsing first is what keeps a
    restatement from double-counting a period the way it would double-count a
    quarter in the fundamentals mapper.
    """

    by_period: dict[str, ShareCountPoint] = {}
    for point in points:
        current = by_period.get(point.period_end)
        if current is None or _restatement_key(point) > _restatement_key(current):
            by_period[point.period_end] = point
    return by_period


def _absent(reason: str, *, skipped: int = 0) -> DilutionSeries:
    """A series with nothing in it and the reason why — never zeros.

    The one shape for every refusal: no counts, no change, no date. A caller
    that cannot take ``None`` for an answer still cannot mistake this for a
    flat history, because ``count`` is 0 and ``reason`` is set.
    """

    return DilutionSeries(
        points=(),
        first=None,
        last=None,
        pct_change=None,
        count=0,
        skipped=skipped,
        as_of=None,
        reason=reason,
        trend_reason=None,
    )


def extract_share_series(payload: Any) -> DilutionSeries:
    """The share-count series in one decoded ``companyfacts`` body, no I/O.

    Returns a :class:`DilutionSeries` — a **timeseries**, not a single figure:
    ``points`` deduplicated by period end and ordered oldest to newest,
    ``first``/``last`` its endpoints, ``pct_change`` measured first→last (never
    min→max: an issuer that issued and then repurchased ends where it ends),
    ``count`` the filings behind those numbers, ``skipped`` the rows dropped as
    unusable (each counted, so a series mined out of garbage reports itself as
    such), and ``as_of`` the newest **filing** date on file — the answer is only
    as fresh as its newest filing, whatever day it is read on.

    The edges are refusals, not fallbacks: a body with no ``dei`` series, or a
    readable body with no rows at all, is :data:`REASON_NO_TAG` with every field
    ``None`` (never ``0.0``); a body whose every row is negative, zero,
    non-numeric, non-finite, undated or wrongly-united is
    :data:`REASON_ALL_UNUSABLE` with the skipped count attached; and a single
    usable filing answers ``first``/``last``/``count`` but refuses the trend
    with :data:`REASON_ONE_POINT` — one count is a level, and a change needs two
    filings. This is the series the screener's published refusal exists to
    answer: "needs a dilution input (share-count history): the scan carries shares outstanding today, not the series, so a dilution *risk* would have to be invented — SEC `dei:EntityCommonStockSharesOutstanding` across filings would answer it"
    """

    rows = _tag_rows(payload)
    if not rows:
        return _absent(REASON_NO_TAG)

    usable: list[ShareCountPoint] = []
    skipped = 0
    for unit, row in rows:
        # The unit is checked, not assumed: a count quoted in USD is not a count.
        # The row still counts as skipped, because the filing did produce it.
        if unit != SHARES_UNIT:
            skipped += 1
            continue
        point = _point_from(row)
        if point is None:
            skipped += 1
        else:
            usable.append(point)
    if not usable:
        return _absent(REASON_ALL_UNUSABLE, skipped=skipped)

    ordered = sorted(_dedupe_by_period(usable).values(), key=lambda point: point.period_end)
    points = tuple(ordered)
    first = points[0].value
    last = points[-1].value
    # The newest point's own filing date is the series' as_of. When that row
    # carries no date (a hand-built or trimmed body), the greatest date on file
    # still answers "how fresh is this" without borrowing the clock.
    as_of = points[-1].filed or max((point.filed for point in points), default="") or None
    if len(points) < 2:
        return DilutionSeries(
            points=points,
            first=first,
            last=last,
            pct_change=None,
            count=len(points),
            skipped=skipped,
            as_of=as_of,
            reason=None,
            trend_reason=REASON_ONE_POINT,
        )

    change = (last - first) / first * 100.0
    return DilutionSeries(
        points=points,
        first=first,
        last=last,
        pct_change=change if math.isfinite(change) else None,
        count=len(points),
        skipped=skipped,
        as_of=as_of,
        reason=None,
        trend_reason=None,
    )


def shares_outstanding_series(cache_dir: Path, cik: Any) -> DilutionSeries:
    """The share-count series of one CIK, read from the ingest cache. No network.

    Takes ``(cache_dir, cik)`` — the same shape as
    :func:`app.engine.ingest.load_cached_companyfacts`, whose read-only, cache-
    first document is reused rather than re-fetched: a document the ingest pass
    already wrote answers the call, and no document produces
    :data:`REASON_NO_DOCUMENT` with every field ``None`` — an issuer we have no
    filings for is absent, never a flat history. The loader's own honesty rules
    are inherited whole: a body that is not this CIK's document is a miss, not
    evidence. A ``cik`` that is not a SEC CIK raises :class:`ValueError` before
    any read — a caller bug, not a data gap.

    Returns the same :class:`DilutionSeries` :func:`extract_share_series` builds
    from the body, up to and including every refusal — the one-point trend
    refusal included. This is the function that would feed the screener's
    published refusal: "needs a dilution input (share-count history): the scan carries shares outstanding today, not the series, so a dilution *risk* would have to be invented — SEC `dei:EntityCommonStockSharesOutstanding` across filings would answer it"
    once the wiring change (which files, and which threshold decision)
    described in the module docstring is made. It writes nothing and caches
    nothing on the way through.
    """

    payload = load_cached_companyfacts(Path(cache_dir), cik)
    if payload is None:
        return _absent(REASON_NO_DOCUMENT)
    return extract_share_series(payload)


__all__ = [
    "DEI_NAMESPACE",
    "REASON_ALL_UNUSABLE",
    "REASON_NO_DOCUMENT",
    "REASON_NO_TAG",
    "REASON_ONE_POINT",
    "SHARES_TAG",
    "SHARES_UNIT",
    "DilutionSeries",
    "ShareCountPoint",
    "extract_share_series",
    "shares_outstanding_series",
]
