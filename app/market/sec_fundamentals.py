"""SEC bulk facts → Ztockz screener fields.

The bulk quarterly dataset (``app.engine.bulk_financials``) gives every filer's
tagged numbers for the price of one download. This module is the translation
layer: **which catalog column does a streamed ``num.txt`` row fill, and when is
the honest answer "nothing"?**

The vocabulary
--------------
:data:`FIELDS` is the published set — the fifteen fundamentals columns the
catalog already carries (``sales`` … ``shares_outstanding``), the two
price-dependent ones (``market_cap``, ``pe``) and the eleven valuation and
growth columns the enrichment probe measured as computable from four cached
quarters (``enterprise_value`` … ``payout_ratio``). :data:`TAG_MAP` maps each
field to the SEC XBRL concepts that may answer it, primary first, using the
same concept groups the engine's ``companyfacts`` slice uses, so a bulk value
and a ``companyfacts`` value for one symbol are the same number from the same
vocabulary. Fields that are not a single reported concept (the margins, the
ratios, the per-share derivations) are arithmetic on selected facts and are
listed in :data:`DERIVED_FIELDS`.

The rules
---------
* **Flows are annual, or rolled forward to a trailing twelve months.** A
  ``qtrs=4`` row is a full year and wins outright. When the issuer's newest
  filing is a 10-Q, the annual row lives in the *previous* quarter's dataset,
  so the caller merges two quarters into one fact list and the TTM is
  ``annual + year-to-date − prior-year year-to-date`` using the 10-Q's own
  comparative. A nine-month figure never passes itself off as twelve months:
  without the annual base or the comparative the answer is the annual alone,
  and with neither it is absent. Instants (balances, share counts) come from
  ``qtrs=0``, newest period end first.
* **Recency, then restatement.** The newest ``ddate`` wins; within one period
  the later ``filed`` — joined from ``sub.txt`` — wins, so a restated
  comparative replaces the original figure. The accession is the final
  tie-break, which makes the choice reproducible.
* **Units are checked, not assumed.** A share count quoted in USD is not a
  share count; an EUR amount is not a USD amount; a dimensional (segment) row
  is not the consolidated fact; a blank unit is nothing at all. The unit
  vocabulary is the bulk dataset's own: per-share concepts (``eps``,
  ``dividend``) are quoted in plain ``USD``, because the per-share-ness is in
  the tag — ``companyfacts``' ``USD/shares`` spelling never appears here.
* **Absent, never zero.** A concept the filing does not name produces ``None``.
  A derived field with a missing input or a zero denominator is ``None`` too —
  ``current_ratio`` for an issuer with no current liabilities is not ``inf``.
* **The valuation block is arithmetic on the rest.** ``enterprise_value`` is
  market cap plus the long-term debt line the catalog carries, less cash, so
  ``ev_ebitda`` and ``pfcf`` are the documented approximations (LT debt only;
  free cash flow is operating cash flow less capital expenditure), not a
  provider's exact figure. ``roic`` taxes EBIT at the filing's own effective
  rate and falls back to 21% when the tax line is absent. Every one of them
  is absent when an input is absent — never zero.
* **Growth needs two periods.** A quarter-over-quarter figure wants two
  distinct quarters, a restatement of one quarter counting as that quarter
  again; a year-over-year TTM wants the trailing twelve months *and* the
  twelve months that ended a year earlier, the second built by the same
  roll-forward over the rows that end at least a year before the newest
  period. One period on file is no growth at all.
* **Price is the caller's.** ``market_cap`` and ``pe`` need a quote; without
  one they are absent rather than stale — and so is every valuation column
  that stands on the market cap.

No network, no disk: the module reads :class:`~app.engine.bulk_financials.Fact`
values somebody else streamed. Contract: ``briefs/market-engine`` keyless plan,
phase 3, task 3.2.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

#: The published fields, in catalog order. A value of ``None`` is an absent
#: field — never a zero, never a guess.
FIELDS: tuple[str, ...] = (
    "sales",
    "net_income",
    "eps",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "roa",
    "roe",
    "current_ratio",
    "quick_ratio",
    "debt_to_equity",
    "book_per_share",
    "cash_per_share",
    "dividend",
    "shares_outstanding",
    "market_cap",
    "pe",
    "enterprise_value",
    "ev_sales",
    "ev_ebitda",
    "pfcf",
    "roic",
    "lt_debt_to_equity",
    "eps_qoq",
    "sales_qoq",
    "eps_yoy_ttm",
    "sales_yoy_ttm",
    "payout_ratio",
)

#: Ztockz field → SEC XBRL concepts that may answer it, primary first.
#:
#: The concept groups are the ones the engine's ``companyfacts`` slice already
#: maps, so the same economic fact reaches the same column whichever document
#: it came from. Entries that are not themselves published fields
#: (``gross_profit``, ``assets``, …) exist because a derived column needs
#: their inputs; they never appear in the result.
TAG_MAP: Mapping[str, tuple[str, ...]] = {
    "sales": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        # The including-assessed-tax sibling is these filers' only total
        # revenue concept (CRWD, TJX, VLO — 16 CIKs on the merged quarters).
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        # Banks report their top line net of interest expense (JPM, MS, GS —
        # 8 CIKs); the gross interest lines are deliberately absent, see
        # ``MUST_NOT_MAP``.
        "RevenuesNetOfInterestExpense",
        # Utilities' total operating revenue (NEE, DUK, SRE — 6 CIKs).
        "RegulatedAndUnregulatedOperatingRevenue",
        # IFRS concepts filed by 20-F filers (GFS, AU — 1 CIK each). Guarded
        # on the taxonomy by ``IFRS_ONLY_TAGS`` so a company-specific
        # extension named ``Revenue`` cannot win a lookup.
        "Revenue",
        "RevenueFromContractsWithCustomers",
    ),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "eps": ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "assets": ("Assets",),
    "equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "inventory": ("InventoryNet",),
    "cash_and_equivalents": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        # The disposal-group spellings TSLA and other restructurers file.
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations",
        "CashAndCashEquivalentsAtCarryingValueIncludingDisposalGroupAndDiscontinuedOperations",
    ),
    "total_debt": (
        "LongTermDebt",
        "LongTermDebtNoncurrent",
        # The lease-inclusive spellings: AVGO files the capital-lease pair,
        # TSLA the finance-lease pair, and neither reaches the plain tags.
        "LongTermDebtAndCapitalLeaseObligations",
        "LongTermDebtAndCapitalLeaseObligationsCurrent",
        "LongTermDebtAndFinanceLeasesNoncurrent",
        "LongTermDebtAndFinanceLeasesCurrent",
        # Last resort: the combined long- and short-term figure.
        "DebtLongtermAndShorttermCombinedAmount",
    ),
    "dividend": ("CommonStockDividendsPerShareDeclared",),
    "shares_outstanding": (
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
    ),
    # Inputs the valuation and growth block derives from. None of them is a
    # published field, so none reaches the result; the enrichment probe
    # measured each concept's fill rate across the four cached quarters
    # (operating cash flow 963 rows, capex 728, D&A 1,906, income tax 2,571).
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "depreciation_amortization": ("DepreciationDepletionAndAmortization",),
    "income_tax": ("IncomeTaxExpenseBenefit",),
    # Pre-tax income is a component of the income statement, which is why
    # ``MUST_NOT_MAP`` keeps it out of ``sales``; ROIC divides the tax line by
    # it to learn the effective rate.
    "pretax_income": (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    ),
}

#: Fields selected from an annual (``qtrs=4``) duration row: an income- or
#: cash-flow-statement figure is a period, and the period the catalog wants is
#: a year.
FLOW_FIELDS: frozenset[str] = frozenset(
    {
        "sales",
        "net_income",
        "eps",
        "gross_profit",
        "operating_income",
        "dividend",
        # Cash-flow, amortization and tax lines are periods like any other,
        # so the same annual-or-TTM rule selects them.
        "operating_cash_flow",
        "capex",
        "depreciation_amortization",
        "income_tax",
        "pretax_income",
    }
)

#: Fields selected from an instant (``qtrs=0``) row: a balance or a share count
#: is a point in time, and summing one over a year is nonsense.
INSTANT_FIELDS: frozenset[str] = frozenset(
    {
        "shares_outstanding",
        "assets",
        "equity",
        "current_assets",
        "current_liabilities",
        "inventory",
        "cash_and_equivalents",
        "total_debt",
    }
)

#: The XBRL unit each field's rows must be quoted in. The vocabulary is the
#: **bulk dataset's**, not ``companyfacts``': per-share concepts (EPS, the
#: declared dividend) arrive quoted in plain ``USD`` — the per-share-ness is
#: carried by the tag — while share counts arrive in ``shares``. Accepting a
#: ``USD/shares`` spelling here would accept a row the dataset never writes.
UOM_BY_FIELD: Mapping[str, str] = {
    **{name: "USD" for name in TAG_MAP},
    "shares_outstanding": "shares",
}

#: The published fields that are arithmetic on selected facts rather than a
#: single reported concept.
DERIVED_FIELDS: frozenset[str] = frozenset(FIELDS) - frozenset(TAG_MAP)

#: ``qtrs`` values this module selects on.
_ANNUAL_QTRS = 4
_INSTANT_QTRS = 0
#: A single quarter: the Q/Q growth pair's unit of account.
_QUARTER_QTRS = 1
#: A year-to-date span: one to three quarters, never the annual itself.
_YTD_QTRS = frozenset({1, 2, 3})
#: The prior-year comparative sits 350–380 days before the current YTD end;
#: a 53-week year and a leap February both live inside that window.
_PRIOR_MIN_GAP_DAYS = 350
_PRIOR_MAX_GAP_DAYS = 380
#: The statutory rate ROIC falls back to when the filing carries no tax line
#: (a loss-maker, an REIT, a partnership). The effective rate is the filing's
#: own tax over pre-tax income whenever both are on file.
_DEFAULT_TAX_RATE = 0.21
#: Concepts that are only valid under the IFRS taxonomy. The bulk dataset's
#: ``version`` column carries the taxonomy (``ifrs/2025`` against
#: ``us-gaap/2026``), and thousands of company-specific extensions carry
#: revenue-like names — a bare ``Revenue`` entry would let a private
#: extension win a lookup, so these two are guarded on the taxonomy.
IFRS_ONLY_TAGS: frozenset[str] = frozenset(
    {"Revenue", "RevenueFromContractsWithCustomers"}
)

#: Concepts that look like a top line but are a component, a cost line or a
#: different statement's figure. Documented so a later "coverage" pass does
#: not add them: each would fill ``sales`` with a partial number.
#: ``research/sec-tag-coverage.md`` carries the evidence per tag.
MUST_NOT_MAP: Mapping[str, str] = {
    "InterestIncomeExpenseNet": "net interest income — one component of a bank's net revenue",
    "InterestAndDividendIncomeOperating": "gross bank interest and dividend income, before interest expense",
    "InterestIncomeOperating": "gross bank interest income; exceeds JPM's net revenue",
    "NoninterestIncome": "bank non-interest component",
    "PremiumsEarnedNet": "insurance premiums only; an insurer's revenue also carries investment income",
    "RegulatedOperatingRevenue": "utility component, not the total",
    "CostOfRevenue": "a cost line, not revenue",
    "CostOfSales": "a cost line (the IFRS spelling)",
    "ComprehensiveIncomeNetOfTax": "includes other comprehensive income, which net income excludes",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": "pre-tax income",
    "NetIncomeLossAvailableToCommonStockholdersBasic": "after preferred dividends — a per-share denominator",
}


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _fact_key(item: Any) -> tuple[str, str, str]:
    """Recency order: period end, then filing date, then accession."""

    return (item.ddate, item.filed, item.adsh)


def _newest(rows: list[Any], qtrs: int) -> Any:
    """The newest row with exactly this ``qtrs`` duration, or ``None``."""

    best: Any = None
    best_key: tuple[str, str, str] | None = None
    for item in rows:
        if item.qtrs != qtrs:
            continue
        key = _fact_key(item)
        if best_key is None or key > best_key:
            best = item
            best_key = key
    return best


def _newest_ytd(rows: list[Any]) -> Any:
    """The newest year-to-date row: newest period end, then longest span.

    A 10-Q publishes the same period end at several durations (a three-month
    quarter and the nine-month year-to-date share ``ddate``); the longest span
    is the one the TTM arithmetic wants, so ``qtrs`` breaks the period tie.
    """

    best: Any = None
    best_key: tuple[str, int, str, str] | None = None
    for item in rows:
        if item.qtrs not in _YTD_QTRS:
            continue
        key = (item.ddate, item.qtrs, item.filed, item.adsh)
        if best_key is None or key > best_key:
            best = item
            best_key = key
    return best


def _prior_year_ytd(rows: list[Any], ytd: Any) -> Any:
    """The same tag and span one year earlier — the 10-Q's own comparative."""

    best: Any = None
    best_key: tuple[str, str, str] | None = None
    for item in rows:
        if item.tag != ytd.tag or item.qtrs != ytd.qtrs:
            continue
        gap = _days_between(item.ddate, ytd.ddate)
        if gap is None or not _PRIOR_MIN_GAP_DAYS <= gap <= _PRIOR_MAX_GAP_DAYS:
            continue
        key = _fact_key(item)
        if best_key is None or key > best_key:
            best = item
            best_key = key
    return best


def _days_between(first: str, second: str) -> int | None:
    """``second - first`` in days; ``None`` when either date is unparseable."""

    try:
        return (date.fromisoformat(second) - date.fromisoformat(first)).days
    except (TypeError, ValueError):
        return None


def _flow_value(rows: list[Any]) -> float | None:
    """A flow field's value: the annual, or the annual rolled forward to TTM.

    A ``qtrs=4`` row is a full year and wins outright. When the issuer's
    newest filing is a 10-Q — the common case mid-year — the annual row lives
    in the *previous* quarter's dataset, so the caller merges two quarters
    into one fact list. The trailing twelve months are then

        annual + year-to-date − prior-year year-to-date

    using the 10-Q's own comparative for the prior span. If the annual base
    or the comparative is missing the answer is the annual alone, and with
    neither it is ``None`` — never a nine-month figure wearing twelve months'
    clothes.
    """

    if not rows:
        return None

    newest = max(rows, key=_fact_key)
    if newest.qtrs == _ANNUAL_QTRS:
        return newest.value

    ytd = _newest_ytd(rows)
    annual = _newest(rows, _ANNUAL_QTRS)
    if ytd is None:
        return None if annual is None else annual.value

    prior = _prior_year_ytd(rows, ytd)
    if prior is None:
        return None if annual is None else annual.value

    # The base year must be the same concept as the YTD pair. A company that
    # switched revenue tags between its 10-K and its 10-Q gets the annual
    # alone rather than arithmetic across two different concepts.
    same_tag = _newest([row for row in rows if row.tag == ytd.tag], _ANNUAL_QTRS)
    if same_tag is None:
        return None if annual is None else annual.value
    return same_tag.value + ytd.value - prior.value


def _instant_value(rows: list[Any]) -> float | None:
    """The newest instant (``qtrs=0``) row's value, or ``None``."""

    best = _newest(rows, _INSTANT_QTRS)
    return None if best is None else best.value


def _rows_for(facts: list[Any], name: str) -> list[Any]:
    """The candidate rows for one mapped field.

    A candidate carries one of the field's concepts, in the field's unit,
    undimensioned. An IFRS-only concept is honoured under the IFRS taxonomy
    alone: a company-specific extension named ``Revenue`` is not the IFRS
    concept, and letting it win would be a private tag answering a public
    field.
    """

    tags = TAG_MAP[name]
    uom = UOM_BY_FIELD[name]
    rows: list[Any] = []
    for item in facts:
        if item.tag not in tags or item.uom != uom or item.segments:
            continue
        if item.tag in IFRS_ONLY_TAGS and not str(
            getattr(item, "version", "") or ""
        ).startswith("ifrs/"):
            continue
        rows.append(item)
    return rows


def _selected(facts: list[Any], name: str) -> float | None:
    """The winning value for one mapped field, or ``None``.

    A flow field takes :func:`_flow_value` — the annual, or the annual rolled
    forward to a trailing twelve months; an instant field takes the newest
    balance. The winner is the newest ``ddate``; a tie is broken by the later
    ``filed`` — the restatement rule — and then by the accession, so two runs
    of one dataset always agree.
    """

    rows = _rows_for(facts, name)
    if not rows:
        return None
    if name in FLOW_FIELDS:
        return _flow_value(rows)
    return _instant_value(rows)


def _ttm_pair(rows: list[Any]) -> tuple[float | None, float | None]:
    """The trailing twelve months, and the twelve months a year before them.

    The newest window is :func:`_flow_value`'s answer. The earlier window is
    the same arithmetic over the rows that end at least a year before the
    newest period — a mid-year filer's prior-year comparative, the annual
    behind it and that annual's own comparative; a 10-K filer's earlier
    window is simply the previous annual. The current window's own rows sit
    inside the year (an annual is at most nine months ahead of its last
    10-Q), so the cut never leaks one window into the other.
    """

    newest = _flow_value(rows)
    if not rows:
        return newest, None
    anchor = max(rows, key=_fact_key).ddate
    earlier: list[Any] = []
    for item in rows:
        gap = _days_between(item.ddate, anchor)
        if gap is not None and gap >= _PRIOR_MIN_GAP_DAYS:
            earlier.append(item)
    return newest, _flow_value(earlier) if earlier else None


def _quarterly_pair(rows: list[Any]) -> tuple[float, float] | None:
    """The two newest distinct quarterly values, newest first.

    A 10-Q republishes one period at several durations; only the three-month
    row is the quarter. A restatement of a quarter is that quarter again, not
    a second period, so the rows collapse by period end before the pair is
    taken — and a single quarter on file is no pair at all.
    """

    by_period: dict[str, Any] = {}
    for item in rows:
        if item.qtrs != _QUARTER_QTRS:
            continue
        current = by_period.get(item.ddate)
        if current is None or _fact_key(item) > _fact_key(current):
            by_period[item.ddate] = item
    if len(by_period) < 2:
        return None
    ordered = sorted(by_period.values(), key=_fact_key, reverse=True)
    return ordered[0].value, ordered[1].value


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    """``None`` when either side is missing or the denominator is zero."""

    if numerator is None or denominator is None or denominator == 0:
        return None
    value = numerator / denominator
    return value if math.isfinite(value) else None


def _pct(value: float | None) -> float | None:
    """A ratio as percentage points; an absent ratio stays absent."""

    return None if value is None else value * 100.0


def _change(pair: tuple[float | None, float | None] | None) -> float | None:
    """Growth in percentage points from a ``(newest, prior)`` pair.

    A missing half — or a prior period of zero — is no growth at all.
    """

    if pair is None:
        return None
    newest, prior = pair
    ratio = _ratio(newest, prior)
    return None if ratio is None else (ratio - 1.0) * 100.0


def _checked_price(price: float | None) -> float | None:
    """A quote is a positive finite number, or nothing at all."""

    if price is None:
        return None
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        raise TypeError(f"price must be a number or None, got {price!r}")
    value = float(price)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"price must be a positive finite number, got {price!r}")
    return value


# ---------------------------------------------------------------------------
# The valuation and growth block
# ---------------------------------------------------------------------------


def _enterprise_value(
    market_cap: float | None, total_debt: float | None, cash: float | None
) -> float | None:
    """Market cap plus the long-term debt line, less cash.

    The debt is the catalog's ``total_debt`` — ``LongTermDebt`` or its
    noncurrent sibling — so this is the documented approximation of a full
    net-debt calculation, not a provider's exact figure.
    """

    if market_cap is None or total_debt is None or cash is None:
        return None
    return market_cap + total_debt - cash


def _ebitda(
    operating_income: float | None, depreciation: float | None
) -> float | None:
    """Operating income plus depreciation and amortization."""

    if operating_income is None or depreciation is None:
        return None
    return operating_income + depreciation


def _free_cash_flow(
    operating_cash_flow: float | None, capex: float | None
) -> float | None:
    """Operating cash flow less capital expenditure."""

    if operating_cash_flow is None or capex is None:
        return None
    return operating_cash_flow - capex


def _tax_rate(income_tax: float | None, pretax_income: float | None) -> float:
    """The filing's own effective rate, or the statutory default.

    A filer with no tax line — a loss-maker, an REIT, a partnership — still
    has capital to earn on, so the rate falls back to 21% rather than the
    return falling away with it.
    """

    rate = _ratio(income_tax, pretax_income)
    return _DEFAULT_TAX_RATE if rate is None else rate


def _invested_capital(
    total_debt: float | None, equity: float | None, cash: float | None
) -> float | None:
    """Debt plus equity, less cash — the capital the return is earned on."""

    if total_debt is None or equity is None or cash is None:
        return None
    return total_debt + equity - cash


# ---------------------------------------------------------------------------
# The mapping
# ---------------------------------------------------------------------------


def map_fundamentals(
    facts: Iterable[Any], *, price: float | None = None
) -> dict[str, float | None]:
    """Map one issuer's streamed facts onto the catalog's fundamentals columns.

    ``facts`` is one issuer's rows — ``FactIndex.facts_for(cik)`` — and may be
    any iterable. Every key of :data:`FIELDS` is present in the result; a
    field with no evidence is ``None``. ``price`` fills ``market_cap`` and
    ``pe``; without it both are absent, because a stale quote is worse than
    none — and so is every valuation column that stands on the market cap.
    """

    rows = list(facts)
    quote = _checked_price(price)
    selected = {name: _selected(rows, name) for name in TAG_MAP}

    sales = selected["sales"]
    net_income = selected["net_income"]
    eps = selected["eps"]
    shares = selected["shares_outstanding"]
    current_assets = selected["current_assets"]
    inventory = selected["inventory"]
    total_debt = selected["total_debt"]
    cash = selected["cash_and_equivalents"]
    equity = selected["equity"]
    operating_income = selected["operating_income"]
    dividend = selected["dividend"]

    # The growth columns read the same candidate rows the published fields
    # read, so a concept swap moves both together.
    eps_rows = _rows_for(rows, "eps")
    sales_rows = _rows_for(rows, "sales")
    eps_now, eps_before = _ttm_pair(eps_rows)
    sales_now, sales_before = _ttm_pair(sales_rows)

    market_cap = None if quote is None or shares is None else quote * shares
    enterprise_value = _enterprise_value(market_cap, total_debt, cash)
    ebitda = _ebitda(operating_income, selected["depreciation_amortization"])
    free_cash_flow = _free_cash_flow(
        selected["operating_cash_flow"], selected["capex"]
    )
    tax_rate = _tax_rate(selected["income_tax"], selected["pretax_income"])
    nopat = (
        None if operating_income is None else operating_income * (1.0 - tax_rate)
    )

    computed: dict[str, float | None] = {
        "sales": sales,
        "net_income": net_income,
        "eps": eps,
        "gross_margin": _ratio(selected["gross_profit"], sales),
        "operating_margin": _ratio(operating_income, sales),
        "net_margin": _ratio(net_income, sales),
        "roa": _ratio(net_income, selected["assets"]),
        "roe": _ratio(net_income, equity),
        "current_ratio": _ratio(current_assets, selected["current_liabilities"]),
        # The quick ratio drops inventory; without the inventory line it
        # cannot be built, and half a ratio is not a ratio.
        "quick_ratio": _ratio(
            None if current_assets is None or inventory is None else current_assets - inventory,
            selected["current_liabilities"],
        ),
        "debt_to_equity": _ratio(total_debt, equity),
        "book_per_share": _ratio(equity, shares),
        "cash_per_share": _ratio(cash, shares),
        "dividend": dividend,
        "shares_outstanding": shares,
        "market_cap": market_cap,
        "pe": None if quote is None or not eps else quote / eps,
        "enterprise_value": enterprise_value,
        "ev_sales": _ratio(enterprise_value, sales),
        "ev_ebitda": _ratio(enterprise_value, ebitda),
        "pfcf": _ratio(market_cap, free_cash_flow),
        "roic": _pct(_ratio(nopat, _invested_capital(total_debt, equity, cash))),
        "lt_debt_to_equity": _ratio(total_debt, equity),
        "eps_qoq": _change(_quarterly_pair(eps_rows)),
        "sales_qoq": _change(_quarterly_pair(sales_rows)),
        "eps_yoy_ttm": _change((eps_now, eps_before)),
        "sales_yoy_ttm": _change((sales_now, sales_before)),
        "payout_ratio": _pct(_ratio(dividend, eps)),
    }
    return {name: computed[name] for name in FIELDS}


def fundamentals_for_cik(
    index: Any, cik: str, *, price: float | None = None
) -> dict[str, float | None]:
    """The fundamentals of one CIK, read from a :class:`FactIndex`-like object.

    An unknown CIK has no facts, so every field is absent — the same answer a
    filer with no usable concepts gets.
    """

    return map_fundamentals(index.facts_for(cik), price=price)


def fundamentals_for_symbol(
    index: Any,
    symbol: str,
    cik_by_symbol: Mapping[str, str],
    *,
    price: float | None = None,
) -> dict[str, float | None]:
    """The fundamentals of one symbol, resolved through a symbol → CIK map.

    The map is the caller's (the engine snapshot owns symbol → CIK); a symbol
    it does not carry is absent rather than guessed at.
    """

    key = symbol.strip().upper() if isinstance(symbol, str) else ""
    cik = cik_by_symbol.get(key) if isinstance(cik_by_symbol, Mapping) else None
    if cik is None:
        return dict.fromkeys(FIELDS)
    return fundamentals_for_cik(index, cik, price=price)


__all__ = [
    "DERIVED_FIELDS",
    "FIELDS",
    "FLOW_FIELDS",
    "IFRS_ONLY_TAGS",
    "INSTANT_FIELDS",
    "MUST_NOT_MAP",
    "TAG_MAP",
    "UOM_BY_FIELD",
    "fundamentals_for_cik",
    "fundamentals_for_symbol",
    "map_fundamentals",
]
