"""The ticker page's statement table, built from SEC XBRL facts.

``app.engine.statements`` answers "one annual column per filing year, with the
provenance of every cell" — an audit table. This module answers the ticker
page's question instead: **ten annual columns and eight quarterly ones, every
line aligned to both**, a YoY column for the newest annual and a trailing
twelve months when the facts can build it. The two tables are different
products; neither is a re-shaping of the other.

Four decisions define the table
-------------------------------
* **A column is a period end, and only a filing of the right kind may create
  one.** Annual columns come from ``10-K``/``10-K/A`` rows; quarterly columns
  come from ``10-Q``/``10-Q/A`` rows whose flow duration is a quarter
  (75–110 days). A fiscal Q4 is never a 10-Q, so a quarterly quarter of the
  year is never invented from a full-year row — the annual column already
  carries it.
* **A flow row must be the right duration.** Revenue, income and cash-flow
  lines are periods: a 10-K row fills an annual cell only when its ``start``
  makes it 330–400 days long, and a 10-Q row fills a quarterly cell only when
  it is 75–110 days. Balance lines are instants and need only ``period_end``.
* **A gap is null, never zero.** Every line carries every period as a key; a
  line the filing does not tag is ``None`` in that column. There is no filler,
  no interpolation and no zero.
* **The period label follows the filing, not the row.** ``companyfacts`` puts
  the *filing's* ``fy``/``fp`` on every row, including prior-year comparatives,
  so a comparative labelled from its own row would be a year wrong. Labels are
  therefore read from the accession whose own period end the date is; a
  comparative falls back to the ISO end date when no such filing is on file.

TTM is the annual rolled forward with the year-to-date pair
-----------------------------------------------------------
A fiscal Q4 does not exist as a 10-Q, so "sum the last four quarters" is not
available for a December or September filer. The trailing twelve months are

    latest annual + year-to-date now − year-to-date a year earlier

using the 10-Q's own comparative. Without the annual base or the comparative
the TTM is ``None``: a nine-month figure wearing twelve months' clothes is the
error this rule exists to prevent.

Contract: ``.hermes/plans/2026-11-02_ticker-page.md`` (T1).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.engine.facts import FactObservation, parse_companyfacts
from app.market.sec_fundamentals import TAG_MAP

#: The document's own source name. It is a vocabulary word (the plan's
#: ``source: "sec-xbrl"``), not a provider label.
SOURCE = "sec-xbrl"

UNIT_MONEY = "USD"
UNIT_SHARES = "shares"
UNIT_PER_SHARE = "USD/shares"

#: The forms that may create an annual column, and a quarterly one. Amendments
#: are restatements of the same period, so they are candidates; the winner rule
#: (newest ``filed``) makes them replace a number without moving the column.
ANNUAL_FORMS = frozenset({"10-K", "10-K/A"})
QUARTERLY_FORMS = frozenset({"10-Q", "10-Q/A"})

#: A flow row is annual inside this window (52/53-week years drift a few days),
#: a quarter inside the narrower one. A six- or nine-month year-to-date row is
#: neither: it is the TTM arithmetic's input, never a column.
ANNUAL_MIN_DAYS, ANNUAL_MAX_DAYS = 330, 400
QUARTER_MIN_DAYS, QUARTER_MAX_DAYS = 75, 110
#: A year-to-date span: one to three quarters, never the annual itself.
YTD_MIN_DAYS, YTD_MAX_DAYS = 111, 330

#: The prior-year comparison sits this far before the current period end. A
#: 52/53-week calendar and a leap February both live inside the window.
PRIOR_MIN_DAYS, PRIOR_MAX_DAYS = 330, 400

#: The three concepts the statement table needs that the screener's field map
#: does not carry (it maps *screening* fields, not statement lines). Everything
#: else reuses :data:`~app.market.sec_fundamentals.TAG_MAP`, so one vocabulary
#: answers both products.
LIABILITIES_TAGS = ("Liabilities",)
INVESTING_TAGS = ("NetCashProvidedByUsedInInvestingActivities",)
FINANCING_TAGS = ("NetCashProvidedByUsedInFinancingActivities",)
#: ``PaymentsToAcquirePropertyPlantAndEquipment`` is the common spelling, but
#: NVDA and other manufacturers file ``PaymentsToAcquireProductiveAssets`` —
#: the same cash outflow under a broader name. Both are capex; the statement
#: line must not be empty for the largest semiconductor filer.
CAPEX_TAGS = (*TAG_MAP["capex"], "PaymentsToAcquireProductiveAssets")


@dataclass(frozen=True, slots=True)
class _Line:
    """One row of the table: which concepts answer it and how it is measured."""

    statement: str
    key: str
    label: str
    tags: tuple[str, ...]
    flow: bool
    unit: str


#: The table, in document order. ``income`` and ``balance`` and ``cash_flow``
#: are the plan's three panels; ``free_cash_flow`` is the one derived row.
_LINES: tuple[_Line, ...] = (
    _Line("income", "revenue", "Revenue", TAG_MAP["sales"], True, UNIT_MONEY),
    _Line("income", "gross_profit", "Gross profit", TAG_MAP["gross_profit"], True, UNIT_MONEY),
    _Line(
        "income",
        "operating_income",
        "Operating income",
        TAG_MAP["operating_income"],
        True,
        UNIT_MONEY,
    ),
    _Line("income", "net_income", "Net income", TAG_MAP["net_income"], True, UNIT_MONEY),
    _Line("income", "eps_diluted", "EPS (diluted)", TAG_MAP["eps"], True, UNIT_PER_SHARE),
    _Line(
        "balance",
        "cash_and_equivalents",
        "Cash and equivalents",
        TAG_MAP["cash_and_equivalents"],
        False,
        UNIT_MONEY,
    ),
    _Line("balance", "current_assets", "Current assets", TAG_MAP["current_assets"], False, UNIT_MONEY),
    _Line("balance", "total_assets", "Total assets", TAG_MAP["assets"], False, UNIT_MONEY),
    _Line(
        "balance",
        "current_liabilities",
        "Current liabilities",
        TAG_MAP["current_liabilities"],
        False,
        UNIT_MONEY,
    ),
    _Line("balance", "total_liabilities", "Total liabilities", LIABILITIES_TAGS, False, UNIT_MONEY),
    _Line("balance", "equity", "Shareholders' equity", TAG_MAP["equity"], False, UNIT_MONEY),
    _Line("balance", "long_term_debt", "Long-term debt", TAG_MAP["total_debt"], False, UNIT_MONEY),
    _Line(
        "balance",
        "shares_outstanding",
        "Shares outstanding",
        TAG_MAP["shares_outstanding"],
        False,
        UNIT_SHARES,
    ),
    _Line(
        "cash_flow",
        "operating_cash_flow",
        "Operating cash flow",
        TAG_MAP["operating_cash_flow"],
        True,
        UNIT_MONEY,
    ),
    _Line(
        "cash_flow",
        "investing_cash_flow",
        "Investing cash flow",
        INVESTING_TAGS,
        True,
        UNIT_MONEY,
    ),
    _Line(
        "cash_flow",
        "financing_cash_flow",
        "Financing cash flow",
        FINANCING_TAGS,
        True,
        UNIT_MONEY,
    ),
    _Line("cash_flow", "capex", "Capital expenditure", CAPEX_TAGS, True, UNIT_MONEY),
)

#: The concepts the cache parser keeps. Reading every concept in a
#: ``companyfacts`` document would cost seconds for tags no line reads.
PARSE_TAGS: Mapping[str, tuple[str, ...]] = {line.key: line.tags for line in _LINES}

#: Statement groups, in the order the ticker page paints them.
_STATEMENTS: tuple[str, ...] = ("income", "balance", "cash_flow")

#: The line derived from two reported ones. It is not in :data:`_LINES` because
#: it has no tags of its own.
_FCF = _Line("cash_flow", "free_cash_flow", "Free cash flow", (), True, UNIT_MONEY)


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------


def build_statement_table(
    observations: Sequence[FactObservation],
    *,
    annual_periods: int = 10,
    quarterly_periods: int = 8,
) -> dict[str, Any]:
    """Build the aligned annual/quarterly statement table.

    ``annual_periods`` and ``quarterly_periods`` bound the number of columns,
    not the rows: every line carries every returned period as a key, with
    ``None`` where the issuer did not report the concept. A non-positive bound
    is an empty table — the same shape an issuer with no facts gets.
    """

    annual_limit = annual_periods if isinstance(annual_periods, int) and annual_periods > 0 else 0
    quarter_limit = (
        quarterly_periods if isinstance(quarterly_periods, int) and quarterly_periods > 0 else 0
    )

    annual, quarter, ytd, annual_ends, quarter_ends = _bucket(observations)
    annual_ends = sorted(annual_ends, reverse=True)[:annual_limit]
    # An end that is an annual column cannot also be a quarterly one. A 10-Q
    # stub can end on the fiscal year end (a transition period); the 10-K
    # column already owns that date, and publishing it twice would put two
    # different labels on one end.
    quarter_ends = [end for end in sorted(quarter_ends, reverse=True) if end not in annual_ends][
        :quarter_limit
    ]

    labels = _filing_labels(observations, ANNUAL_FORMS, annual=True)
    quarter_labels = _filing_labels(observations, QUARTERLY_FORMS, annual=False)
    # One label namespace across both kinds: a quarter whose fiscal metadata is
    # missing falls back to its ISO end date, but an annual label and a
    # quarterly one must never collide in the ``values`` keys.
    used: set[str] = set()
    periods = _periods(annual_ends, "annual", labels, used) + _periods(
        quarter_ends, "quarterly", quarter_labels, used
    )
    label_of = {period["end"]: period["period"] for period in periods}
    order = [period["end"] for period in periods]

    statements: dict[str, list[dict[str, Any]]] = {
        statement: [] for statement in _STATEMENTS
    }
    reported: dict[str, dict[str, Any]] = {}
    for line in _LINES:
        series = _series(line, annual, quarter, ytd, annual_ends, quarter_ends)
        reported[line.key] = series
        statements[line.statement].append(_row(line, series, order, label_of))

    # ``free cash flow`` is the one row the filings do not report. It is built
    # from the two cells above, so it is aligned by construction and absent
    # (not a negative CFO run rate) when either input is missing.
    free = _free_cash_flow_series(
        reported["operating_cash_flow"], reported["capex"]
    )
    statements["cash_flow"].append(_row(_FCF, free, order, label_of))

    return {
        "as_of": max(order) if order else None,
        "periods": periods,
        "statements": statements,
    }


def statement_document(
    symbol: str,
    payload: Any,
    *,
    annual_periods: int = 10,
    quarterly_periods: int = 8,
) -> dict[str, Any] | None:
    """One issuer's table from a ``companyfacts`` body, or ``None``.

    ``None`` means the document carried no usable annual or quarterly fact:
    an issuer whose only filings are foreign-language 20-F forms is not an
    empty table, it is "no statements", and the route says ``404``.
    """

    observations = parse_companyfacts(payload, tags=PARSE_TAGS)
    table = build_statement_table(
        observations,
        annual_periods=annual_periods,
        quarterly_periods=quarterly_periods,
    )
    if not table["periods"]:
        return None
    return {"symbol": symbol, "source": SOURCE, **table}


# ---------------------------------------------------------------------------
# Buckets and periods
# ---------------------------------------------------------------------------


def _duration(observation: FactObservation) -> int | None:
    """The row's duration in days, or ``None`` when it has no usable start."""

    if not observation.start or not observation.period_end:
        return None
    try:
        start = date.fromisoformat(observation.start)
        end = date.fromisoformat(observation.period_end)
    except ValueError:
        return None
    days = (end - start).days
    return days if days > 0 else None


def _days_between(first: str, second: str) -> int | None:
    """``second - first`` in days; ``None`` when either date is unparseable."""

    try:
        return (date.fromisoformat(second) - date.fromisoformat(first)).days
    except ValueError:
        return None


def _bucket(
    observations: Sequence[FactObservation],
) -> tuple[
    dict[tuple[str, str], list[FactObservation]],
    dict[tuple[str, str], list[FactObservation]],
    dict[tuple[str, str], list[FactObservation]],
    set[str],
    set[str],
]:
    """Split observations into annual, quarterly and year-to-date candidates.

    ``annual_ends`` is every period end an annual cell may use; ``quarter_ends``
    is every end a *flow* quarter reported. An instant at a 10-Q end does not
    create a quarterly column of its own: a balance sheet comparative at the
    prior fiscal year end would otherwise appear as a quarter the issuer never
    reported.
    """

    lines_by_tag: dict[str, list[_Line]] = {}
    for line in _LINES:
        for tag in line.tags:
            lines_by_tag.setdefault(tag, []).append(line)

    annual: dict[tuple[str, str], list[FactObservation]] = {}
    quarter: dict[tuple[str, str], list[FactObservation]] = {}
    ytd: dict[tuple[str, str], list[FactObservation]] = {}
    annual_ends: set[str] = set()
    quarter_ends: set[str] = set()

    for observation in observations:
        if not isinstance(observation, FactObservation) or not observation.period_end:
            continue
        for line in lines_by_tag.get(observation.tag, ()):
            if observation.unit != line.unit:
                continue
            end = observation.period_end
            if observation.form in ANNUAL_FORMS:
                if line.flow:
                    days = _duration(observation)
                    if days is None or not ANNUAL_MIN_DAYS <= days <= ANNUAL_MAX_DAYS:
                        continue
                annual.setdefault((line.key, end), []).append(observation)
                annual_ends.add(end)
            elif observation.form in QUARTERLY_FORMS:
                if not line.flow:
                    quarter.setdefault((line.key, end), []).append(observation)
                    continue
                days = _duration(observation)
                if days is None:
                    continue
                if QUARTER_MIN_DAYS <= days <= QUARTER_MAX_DAYS:
                    quarter.setdefault((line.key, end), []).append(observation)
                    quarter_ends.add(end)
                elif YTD_MIN_DAYS <= days <= YTD_MAX_DAYS:
                    ytd.setdefault((line.key, end), []).append(observation)
    return annual, quarter, ytd, annual_ends, quarter_ends


def _filing_labels(
    observations: Sequence[FactObservation], forms: frozenset[str], *, annual: bool
) -> dict[str, str]:
    """The label each period end was first filed under, for one filing kind.

    ``companyfacts`` stamps the filing's ``fy``/``fp`` on every row, including
    prior-year comparatives, so a comparative cannot label itself. The filing's
    *own* period end is the maximum end in its accession; only that end earns
    the filing's label. If several filings claim an end (a restatement), the
    earliest filed one is the period's own name.

    ``forms`` keeps the two kinds apart: a 10-Q whose period end happens to
    equal a fiscal year end (a transition stub) must not rename the annual
    column with a ``fy``/``fp`` pair that belongs to a quarter.
    """

    by_accession: dict[str, list[FactObservation]] = {}
    for observation in observations:
        if not isinstance(observation, FactObservation):
            continue
        if observation.form in forms:
            by_accession.setdefault(observation.accession, []).append(observation)

    chosen: dict[str, tuple[str, str]] = {}
    for rows in by_accession.values():
        ends = [row.period_end for row in rows if row.period_end]
        if not ends:
            continue
        current = max(ends)
        fiscal_year = next((row.fiscal_year for row in rows if row.fiscal_year), None)
        fiscal_period = next((row.fiscal_period for row in rows if row.fiscal_period), None)
        if annual:
            label = str(fiscal_year) if fiscal_year else current[:4]
        else:
            digit = (
                str(fiscal_period).strip().upper()[-1:]
                if fiscal_period and str(fiscal_period).strip().upper()[-1:] in {"1", "2", "3", "4"}
                else ""
            )
            label = f"{fiscal_year}Q{digit}" if fiscal_year and digit else current
        filed = rows[0].filed or "9999-99-99"
        previous = chosen.get(current)
        if previous is None or filed < previous[0]:
            chosen[current] = (filed, label)
    return {end: label for end, (_, label) in chosen.items()}


def _periods(
    ends: Sequence[str],
    kind: str,
    labels: Mapping[str, str],
    used: set[str] | None = None,
) -> list[dict[str, Any]]:
    """One period column per end, labels unique across every column."""

    taken = used if used is not None else set()
    periods: list[dict[str, Any]] = []
    for end in ends:
        base = labels.get(end) or (end[:4] if kind == "annual" else end)
        label = base if base not in taken else f"{base}@{end}"
        taken.add(label)
        periods.append(
            {
                "period": label,
                "kind": kind,
                "end": end,
                # The two spellings the older statement document uses, so a
                # client that predates this table can still read the column
                # header without a second response.
                "period_end": end,
                "fiscal_year": _label_year(label, kind),
                "form": "10-K" if kind == "annual" else "10-Q",
            }
        )
    return periods


def _label_year(label: str, kind: str) -> int | None:
    """The fiscal year a period label asserts, or ``None``.

    ``2025`` is FY2025; ``2025Q3`` is the third quarter of FY2025. A label that
    is an ISO date (a quarter with no fiscal metadata) asserts no year — the
    date is the fact, and reading its calendar year back as a fiscal year would
    be a different statement.
    """

    if kind == "annual" and len(label) == 4 and label.isdigit():
        return int(label)
    if kind == "quarterly" and len(label) == 6 and label[:4].isdigit() and label[4] == "Q":
        return int(label[:4])
    return None


# ---------------------------------------------------------------------------
# Values, growth and TTM
# ---------------------------------------------------------------------------


def _winner(
    candidates: Sequence[FactObservation], tags: Sequence[str]
) -> FactObservation:
    """The newest filed row for one cell; ties fall to the primary concept."""

    preference = {tag: index for index, tag in enumerate(tags)}
    return max(
        candidates,
        key=lambda row: (row.filed, row.accession, row.value, -preference.get(row.tag, len(tags))),
    )


def _reported(
    buckets: Mapping[tuple[str, str], list[FactObservation]],
    key: str,
    tags: Sequence[str],
    ends: Sequence[str],
) -> dict[str, float]:
    values: dict[str, float] = {}
    for end in ends:
        candidates = buckets.get((key, end))
        if candidates:
            values[end] = _winner(candidates, tags).value
    return values


def _longest_ytd(
    buckets: Mapping[tuple[str, str], list[FactObservation]],
    key: str,
    tags: Sequence[str],
) -> dict[str, tuple[int, float]]:
    """The longest year-to-date row at each end: ``end → (days, value)``."""

    grouped: dict[str, list[FactObservation]] = {}
    for (line_key, end), candidates in buckets.items():
        if line_key == key:
            grouped.setdefault(end, []).extend(candidates)
    out: dict[str, tuple[int, float]] = {}
    for end, candidates in grouped.items():
        best: FactObservation | None = None
        best_days = -1
        for candidate in candidates:
            days = _duration(candidate) or -1
            if days > best_days or (
                days == best_days
                and best is not None
                and (candidate.filed, candidate.accession) > (best.filed, best.accession)
            ):
                best = candidate
                best_days = days
        if best is not None and best_days > 0:
            out[end] = (best_days, best.value)
    return out


def _series(
    line: _Line,
    annual: Mapping[tuple[str, str], list[FactObservation]],
    quarter: Mapping[tuple[str, str], list[FactObservation]],
    ytd: Mapping[tuple[str, str], list[FactObservation]],
    annual_ends: Sequence[str],
    quarter_ends: Sequence[str],
) -> dict[str, Any]:
    """One line's annual, quarterly and year-to-date values, plus its TTM."""

    annual_values = _reported(annual, line.key, line.tags, annual_ends)
    quarter_values = _reported(quarter, line.key, line.tags, quarter_ends)
    ytd_values = _longest_ytd(ytd, line.key, line.tags)
    return {
        "annual": annual_values,
        "quarter": quarter_values,
        "ytd": ytd_values,
        "ttm": _ttm(annual_values, ytd_values) if line.flow else None,
        "yoy": _yoy(annual_values, quarter_values),
    }


def _ttm(
    annual_values: Mapping[str, float],
    ytd_values: Mapping[str, tuple[int, float]],
) -> float | None:
    """The trailing twelve months, or ``None`` when the facts cannot build it."""

    latest_annual = max(annual_values) if annual_values else None
    latest_ytd = max(ytd_values) if ytd_values else None

    if latest_ytd is not None and (
        latest_annual is None or latest_ytd > latest_annual
    ):
        days_now, now = ytd_values[latest_ytd]
        # The annual base is the latest annual that ended before this YTD span:
        # a Q1 TTM needs the year that ended three months earlier, not twelve.
        base_end: str | None = None
        for end in annual_values:
            if end >= latest_ytd:
                continue
            gap = _days_between(end, latest_ytd)
            if gap is not None and 0 < gap <= ANNUAL_MAX_DAYS:
                if base_end is None or end > base_end:
                    base_end = end
        if base_end is None:
            return None

        prior: float | None = None
        best_distance = 10**9
        for end, (days, value) in ytd_values.items():
            gap = _days_between(end, latest_ytd)
            if gap is None or not PRIOR_MIN_DAYS <= gap <= PRIOR_MAX_DAYS:
                continue
            if abs(days - days_now) > 15:
                continue
            distance = abs(gap - 365)
            if distance < best_distance:
                prior = value
                best_distance = distance
        if prior is None:
            return None
        return annual_values[base_end] + now - prior

    if latest_annual is not None:
        return annual_values[latest_annual]
    return None


def _yoy(
    annual_values: Mapping[str, float],
    quarter_values: Mapping[str, float],
) -> float | None:
    """The newest annual against the annual a year before it.

    The annual series owns the column when it exists: a row that has both an
    annual and a quarterly series must not answer a mixed-basis percentage. A
    quarterly-only issuer (a stub with no 10-K yet) compares the newest quarter
    with the same quarter a year earlier. A missing prior, or a prior of zero,
    is no growth at all.
    """

    if annual_values:
        latest = max(annual_values)
        prior_end = _prior_year(annual_values, latest)
        return None if prior_end is None else _growth(annual_values[latest], annual_values[prior_end])
    if quarter_values:
        latest = max(quarter_values)
        prior_end = _prior_year(quarter_values, latest)
        return None if prior_end is None else _growth(quarter_values[latest], quarter_values[prior_end])
    return None


def _prior_year(values: Mapping[str, float], latest: str) -> str | None:
    """The latest period end one year before ``latest``, or ``None``."""

    best: str | None = None
    for end in values:
        if end >= latest:
            continue
        gap = _days_between(end, latest)
        if gap is None or not PRIOR_MIN_DAYS <= gap <= PRIOR_MAX_DAYS:
            continue
        if best is None or end > best:
            best = end
    return best


def _growth(value: float, prior: float) -> float | None:
    """Percent change against ``abs(prior)``; a zero prior has no growth."""

    if prior == 0:
        return None
    return (value - prior) / abs(prior) * 100.0


def _free_cash_flow_series(
    operating: Mapping[str, Any], capex: Mapping[str, Any]
) -> dict[str, Any]:
    """CFO less the absolute capex, computed cell by cell.

    ``capex`` is filed as a positive payment by some issuers and a negative one
    by others, so ``abs`` makes the subtraction independent of the sign
    convention. A missing half makes the cell ``None``: publishing CFO alone as
    free cash flow would overstate it by exactly the investment made.
    """

    def combine(left: Mapping[str, float], right: Mapping[str, float]) -> dict[str, float]:
        return {
            end: left[end] - abs(right[end])
            for end in left
            if end in right
        }

    annual = combine(operating["annual"], capex["annual"])
    quarter = combine(operating["quarter"], capex["quarter"])
    ytd = {
        end: (days, value - abs(capex["ytd"][end][1]))
        for end, (days, value) in operating["ytd"].items()
        if end in capex["ytd"]
    }
    return {
        "annual": annual,
        "quarter": quarter,
        "ytd": ytd,
        "ttm": _ttm(annual, ytd),
        "yoy": _yoy(annual, quarter),
    }


def _row(
    line: _Line,
    series: Mapping[str, Any],
    order: Sequence[str],
    label_of: Mapping[str, str],
) -> dict[str, Any]:
    """One wire row: every period key present, a null where nothing was filed."""

    values: dict[str, float | None] = {}
    for end in order:
        value = (
            series["annual"].get(end)
            if end in series["annual"]
            else series["quarter"].get(end)
        )
        values[label_of[end]] = value
    return {
        "key": line.key,
        "label": line.label,
        "unit": line.unit,
        "values": values,
        "yoy_pct": series["yoy"],
        "ttm": series["ttm"],
    }


__all__ = ["PARSE_TAGS", "SOURCE", "build_statement_table", "statement_document"]
