"""Annual statements table from SEC ``companyfacts``.

``facts.py`` answers "what is the latest/TTM value of one metric"; this module
answers "show me the income statement, balance sheet and cash-flow lines for the
last N annual periods". It is a different question: a table needs aligned
periods, a missing row must stay a visible absence, and a restatement must win
inside its own period without moving the period.

Only 10-K observations are candidates: a 10-Q is a different period and mixing
it into an annual column is the exact error this module exists to prevent.

The three decisions this slice makes
------------------------------------
* **A column is a period, not a filing year.** The period set is the union of
  ``period_end`` over the eligible observations, newest first, truncated to the
  requested number of columns. Every line array is exactly as long as that
  list, so a consumer can index a line by column without checking anything.
* **A gap is visible, never zero.** A line and period with no row is a null
  cell with ``status="not_reported"`` — the same information a zero would
  destroy. There is no filler anywhere in this module.
* **A flow needs a year.** Revenue, income and cash-flow rows are durations:
  XBRL quotes a quarter, a year-to-date slice and a full year that can all end
  on the same day, so a row only fills an annual cell when its ``start`` makes
  it 330..400 days long. Balance lines are instants and need only
  ``period_end``; demanding a start would erase every balance sheet.

Provenance is the filing: ``inputs`` is ``(us-gaap tag, accession)`` so a cell
can be traced to one document, and ``as_of`` is the **period end** the column
stands for — never the filing date, which is a different fact. The derived
``fcf`` line says ``source="derived"`` and is ``absent`` (not a negative CFO
run rate) when either reported input is missing.

Contract: ``briefs/market-engine/R5C-CONTRACT.md``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.engine.facts import DEFAULT_TAGS, FactObservation, parse_companyfacts
from app.engine.ingest import fetch_companyfacts
from app.engine.model import CellStatus
from app.engine.sources.sec import SOURCE_NAME

#: The one form an annual column may come from. ``10-Q``/``10-K/A`` rows are
#: parsed (the latter are restatements of the same annual period) but a
#: quarterly period must never become a column of this table.
ANNUAL_FORM = "10-K"

#: A flow row is annual when its duration is inside this window. 330..400 days
#: tolerates a 52/53-week fiscal year and the one-day drift a 4-4-5 calendar
#: produces, while still rejecting a 90-day quarter and a two-year stub.
ANNUAL_MIN_DAYS = 330
ANNUAL_MAX_DAYS = 400

#: Money is quoted in absolute dollars; the engine has no per-share unit, so a
#: per-share line says ``unknown`` rather than pretending to be USD.
UNIT_MONEY = "USD"
UNIT_PER_SHARE = "unknown"

#: The adapter key for a value a filing reported, and the one for a value this
#: module computed from reported values. Both are audit vocabulary, not labels.
DERIVED_SOURCE = "derived"


@dataclass(frozen=True, slots=True)
class _Line:
    """One cell row of the table: where it lives, which concepts answer it.

    ``flow`` is the eligibility rule, not a label: a flow needs a 330..400 day
    ``start``, an instant needs only a ``period_end``. ``unit`` is the contract's
    unit for the line, so a filing that tagged dollars per share as ``USD``
    cannot relabel the column.
    """

    statement: str
    name: str
    tags: tuple[str, ...]
    flow: bool
    unit: str


#: The table, in document order. Each line reuses :data:`DEFAULT_TAGS` so the
#: table's concepts and the fact slice's concepts cannot drift apart; the
#: contract's tag list for every line is exactly the published mapping.
_LINES: tuple[_Line, ...] = (
    _Line("income", "revenue", DEFAULT_TAGS["revenue"], True, UNIT_MONEY),
    _Line("income", "gross_profit", DEFAULT_TAGS["gross_profit"], True, UNIT_MONEY),
    _Line(
        "income",
        "operating_income",
        DEFAULT_TAGS["operating_income"],
        True,
        UNIT_MONEY,
    ),
    _Line("income", "net_income", DEFAULT_TAGS["net_income"], True, UNIT_MONEY),
    _Line("income", "eps_diluted", DEFAULT_TAGS["eps_diluted"], True, UNIT_PER_SHARE),
    _Line("balance", "assets", DEFAULT_TAGS["assets"], False, UNIT_MONEY),
    _Line("balance", "liabilities", DEFAULT_TAGS["liabilities"], False, UNIT_MONEY),
    _Line("balance", "equity", DEFAULT_TAGS["equity"], False, UNIT_MONEY),
    _Line(
        "balance",
        "cash_and_equivalents",
        DEFAULT_TAGS["cash_and_equivalents"],
        False,
        UNIT_MONEY,
    ),
    _Line("balance", "total_debt", DEFAULT_TAGS["total_debt"], False, UNIT_MONEY),
    _Line(
        "cash_flow",
        "operating_cash_flow",
        DEFAULT_TAGS["operating_cash_flow"],
        True,
        UNIT_MONEY,
    ),
    _Line("cash_flow", "capex", DEFAULT_TAGS["capex"], True, UNIT_MONEY),
)

#: Statement groups, in the order the document publishes them.
_STATEMENTS: tuple[str, ...] = ("income", "balance", "cash_flow")

#: The collector parses only concepts the table can use. A ``companyfacts``
#: document carries hundreds of concepts, and reading the ones no line reads
#: would cost time and memory for nothing.
_PARSE_TAGS: Mapping[str, tuple[str, ...]] = {line.name: line.tags for line in _LINES}


def build_statements(
    observations: Sequence[FactObservation], *, periods: int = 4
) -> dict[str, object]:
    """Build the annual income, balance and cash-flow table.

    ``periods`` bounds the columns, not the rows: every line is aligned to the
    returned ``periods`` list, and a line with nothing to say in a column still
    occupies that column as a ``not_reported`` null. ``periods <= 0`` is an
    empty table — the same shape an issuer with no annual facts gets — because
    inventing a column would be worse than answering nothing.

    The winner for one line and one period is the newest ``filed`` row, then
    the greatest ``accession``, then the greatest value: a restatement replaces
    the number while the column stays the period it restates. Tag order is the
    last tie-break so two aliases reporting the same value through the same
    accession still resolve deterministically.
    """

    limit = periods if isinstance(periods, int) and periods > 0 else 0
    buckets, rows_by_period = _eligible_observations(observations)
    period_ends = sorted(rows_by_period, reverse=True)[:limit]

    statements: dict[str, dict[str, list[dict[str, object]]]] = {
        statement: {} for statement in _STATEMENTS
    }
    for line in _LINES:
        statements[line.statement][line.name] = [
            _reported_cell(line, period_end, buckets.get((line.name, period_end), ()))
            for period_end in period_ends
        ]

    # ``fcf`` is the only line the filings do not report: it is derived from two
    # cells on this table, so it is built from them (not from raw observations)
    # and is therefore aligned by construction.
    cash_flow = statements["cash_flow"]
    cash_flow["fcf"] = [
        _fcf_cell(
            cash_flow["operating_cash_flow"][index],
            cash_flow["capex"][index],
            period_end,
        )
        for index, period_end in enumerate(period_ends)
    ]

    return {
        "as_of": period_ends[0] if period_ends else None,
        "periods": [
            {
                "period_end": period_end,
                "fiscal_year": _fiscal_year(rows_by_period[period_end]),
                "form": ANNUAL_FORM,
            }
            for period_end in period_ends
        ],
        "statements": statements,
    }


async def collect_statements(
    symbol: str,
    cik: str,
    *,
    client: Any = None,
    cache_dir: Path | None = None,
    periods: int = 4,
) -> dict[str, object]:
    """Fetch one issuer's ``companyfacts`` and build its annual table.

    The fetch goes through :func:`~app.engine.ingest.fetch_companyfacts`, so a
    ``cache_dir`` makes a repeated request free and a live body that is not a
    companyfacts document raises :class:`~app.engine.ingest.SecIngestError`.
    Nothing is swallowed here: the route is the layer that decides a failed
    collection is ``503 statements_unavailable``, and it can only do that if
    the failure reaches it with its type intact.

    ``symbol`` is attached to the document because the table itself is about
    periods, and a response without the instrument it describes is ambiguous
    once more than one issuer is in play.
    """

    payload = await fetch_companyfacts(cik, client=client, cache_dir=cache_dir)
    observations = parse_companyfacts(payload, tags=_PARSE_TAGS)
    return {"symbol": symbol, **build_statements(observations, periods=periods)}


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def _duration_days(observation: FactObservation) -> int | None:
    """The row's duration in days, or ``None`` when it has no usable start.

    ``start`` is what separates a quarter from the year-to-date row that ends
    on the same day. A missing or malformed date is not a duration: the row is
    unusable for an annual cell rather than assumed to be a year.
    """

    if not observation.start or not observation.period_end:
        return None
    try:
        start = date.fromisoformat(observation.start)
        end = date.fromisoformat(observation.period_end)
    except ValueError:
        return None
    days = (end - start).days
    return days if days > 0 else None


def _is_candidate(observation: FactObservation, *, flow: bool) -> bool:
    """May this observation fill a cell of a ``flow`` (or instant) line?"""

    if observation.form.strip().upper() != ANNUAL_FORM:
        return False
    if not observation.period_end:
        return False
    if not flow:
        return True
    days = _duration_days(observation)
    return days is not None and ANNUAL_MIN_DAYS <= days <= ANNUAL_MAX_DAYS


def _eligible_observations(
    observations: Sequence[FactObservation],
) -> tuple[
    dict[tuple[str, str], list[FactObservation]],
    dict[str, list[FactObservation]],
]:
    """Bucket annual candidates by ``(line, period_end)`` and by period.

    A row that no line reads — an unknown concept, a 10-Q, a quarter-long flow
    — contributes nothing here, and that is deliberate: an ineligible row must
    not create a column of its own either, or the table would show a period
    every line is null for.
    """

    lines_by_tag: dict[str, list[_Line]] = {}
    for line in _LINES:
        for tag in line.tags:
            lines_by_tag.setdefault(tag, []).append(line)

    buckets: dict[tuple[str, str], list[FactObservation]] = {}
    rows_by_period: dict[str, list[FactObservation]] = {}
    for observation in observations:
        if not isinstance(observation, FactObservation):
            continue
        for line in lines_by_tag.get(observation.tag, ()):
            if not _is_candidate(observation, flow=line.flow):
                continue
            buckets.setdefault(
                (line.name, observation.period_end), []
            ).append(observation)
            rows_by_period.setdefault(observation.period_end, []).append(observation)
    return buckets, rows_by_period


def _fiscal_year(rows: Sequence[FactObservation]) -> int | None:
    """The fiscal year the newest filing in ``rows`` asserts, or ``None``.

    The header describes the column, so the newest filing wins — a restated
    period is still the same fiscal year, and a row that carried no ``fy``
    cannot answer for one that did.
    """

    dated = [row for row in rows if row.fiscal_year is not None]
    if not dated:
        return None
    newest = max(dated, key=lambda row: (row.filed, row.accession, row.fiscal_year))
    return newest.fiscal_year


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------


def _reported_cell(
    line: _Line, period_end: str, candidates: Sequence[FactObservation]
) -> dict[str, object]:
    """The winning reported cell for one line/period, or a visible absence."""

    if not candidates:
        return _cell(
            None, unit=line.unit, as_of=period_end, status=CellStatus.NOT_REPORTED
        )
    preference = {tag: index for index, tag in enumerate(line.tags)}
    winner = max(
        candidates,
        key=lambda row: (row.filed, row.accession, row.value, -preference[row.tag]),
    )
    return _cell(
        winner.value,
        unit=line.unit,
        as_of=period_end,
        status=CellStatus.OK,
        inputs=(winner.tag, winner.accession),
    )


def _fcf_cell(
    operating_cash_flow: Mapping[str, object],
    capex: Mapping[str, object],
    period_end: str,
) -> dict[str, object]:
    """Free cash flow from two reported cells, or an ``absent`` null.

    ``capex`` is reported as a negative payment, so ``abs`` keeps the
    subtraction independent of the issuer's sign convention. A missing input
    makes the whole line absent: publishing CFO alone as "free cash flow" would
    overstate it by exactly the investment the company made.
    """

    operating = _number(operating_cash_flow.get("value"))
    spend = _number(capex.get("value"))
    if operating is None or spend is None:
        return _cell(None, unit=UNIT_MONEY, as_of=period_end, status=CellStatus.ABSENT)
    return _cell(
        operating - abs(spend),
        unit=UNIT_MONEY,
        as_of=period_end,
        status=CellStatus.DERIVED,
        source=DERIVED_SOURCE,
    )


def _cell(
    value: float | None,
    *,
    unit: str,
    as_of: str,
    status: CellStatus,
    source: str = SOURCE_NAME,
    inputs: Sequence[str] = (),
) -> dict[str, object]:
    """One cell in the document's wire shape.

    ``url`` is always ``None``: a ``companyfacts`` row names its accession but
    carries no document URL, and inventing one would be a link that may 404.
    """

    return {
        "value": value,
        "unit": unit,
        "provenance": {
            "source": source,
            "as_of": as_of,
            "status": status.value,
            "url": None,
            "inputs": list(inputs),
        },
    }


def _number(value: object) -> float | None:
    """``value`` as a finite float, or ``None`` when it is not a number.

    A bool is not a number here even though Python says it is an ``int``: a
    cell that somehow held ``True`` must read as missing, not as ``1.0``.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


__all__ = ["build_statements", "collect_statements"]
