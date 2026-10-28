"""SEC bulk facts → Ztockz screener fields.

The bulk quarterly dataset (``app.engine.bulk_financials``) gives every filer's
tagged numbers for the price of one download. This module is the translation
layer: **which catalog column does a streamed ``num.txt`` row fill, and when is
the honest answer "nothing"?**

The vocabulary
--------------
:data:`FIELDS` is the published set — the fifteen fundamentals columns the
catalog already carries (``sales`` … ``shares_outstanding``) plus the two
price-dependent ones (``market_cap``, ``pe``). :data:`TAG_MAP` maps each field
to the SEC XBRL concepts that may answer it, primary first, using the same
concept groups the engine's ``companyfacts`` slice uses, so a bulk value and a
``companyfacts`` value for one symbol are the same number from the same
vocabulary. Fields that are not a single reported concept (the margins, the
ratios, the per-share derivations) are arithmetic on selected facts and are
listed in :data:`DERIVED_FIELDS`.

The rules
---------
* **Flows come from an annual duration.** A ``qtrs=4`` row is a full year; a
  nine-month YTD or a single quarter published as a "TTM" would be three
  months wearing twelve months' clothes. Instants (balances, share counts)
  come from ``qtrs=0``, newest period end first.
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
* **Price is the caller's.** ``market_cap`` and ``pe`` need a quote; without
  one they are absent rather than stale.

No network, no disk: the module reads :class:`~app.engine.bulk_financials.Fact`
values somebody else streamed. Contract: ``briefs/market-engine`` keyless plan,
phase 3, task 3.2.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
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
    ),
    "total_debt": ("LongTermDebt", "LongTermDebtNoncurrent"),
    "dividend": ("CommonStockDividendsPerShareDeclared",),
    "shares_outstanding": (
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
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


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _selected(facts: list[Any], name: str) -> float | None:
    """The winning value for one mapped field, or ``None``.

    Candidates are the field's concepts, in the field's unit, undimensioned,
    with the duration the field's class demands (annual for a flow, instant
    for a balance). The winner is the newest ``ddate``; a tie is broken by the
    later ``filed`` — the restatement rule — and then by the accession, so two
    runs of one dataset always agree.
    """

    tags = TAG_MAP[name]
    uom = UOM_BY_FIELD[name]
    annual = name in FLOW_FIELDS
    wanted_qtrs = _ANNUAL_QTRS if annual else _INSTANT_QTRS

    best: Any = None
    best_key: tuple[str, str, str] | None = None
    for item in facts:
        if item.tag not in tags or item.uom != uom or item.segments:
            continue
        if item.qtrs != wanted_qtrs:
            continue
        key = (item.ddate, item.filed, item.adsh)
        if best_key is None or key > best_key:
            best = item
            best_key = key
    return None if best is None else best.value


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    """``None`` when either side is missing or the denominator is zero."""

    if numerator is None or denominator is None or denominator == 0:
        return None
    value = numerator / denominator
    return value if math.isfinite(value) else None


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
    none.
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

    computed: dict[str, float | None] = {
        "sales": sales,
        "net_income": net_income,
        "eps": eps,
        "gross_margin": _ratio(selected["gross_profit"], sales),
        "operating_margin": _ratio(selected["operating_income"], sales),
        "net_margin": _ratio(net_income, sales),
        "roa": _ratio(net_income, selected["assets"]),
        "roe": _ratio(net_income, selected["equity"]),
        "current_ratio": _ratio(current_assets, selected["current_liabilities"]),
        # The quick ratio drops inventory; without the inventory line it
        # cannot be built, and half a ratio is not a ratio.
        "quick_ratio": _ratio(
            None if current_assets is None or inventory is None else current_assets - inventory,
            selected["current_liabilities"],
        ),
        "debt_to_equity": _ratio(selected["total_debt"], selected["equity"]),
        "book_per_share": _ratio(selected["equity"], shares),
        "cash_per_share": _ratio(selected["cash_and_equivalents"], shares),
        "dividend": selected["dividend"],
        "shares_outstanding": shares,
        "market_cap": None if quote is None or shares is None else quote * shares,
        "pe": None if quote is None or not eps else quote / eps,
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
    "INSTANT_FIELDS",
    "TAG_MAP",
    "UOM_BY_FIELD",
    "fundamentals_for_cik",
    "fundamentals_for_symbol",
    "map_fundamentals",
]
