"""Yahoo Finance, full surface: 14 ``quoteSummary`` modules + one timeseries.

The existing :class:`app.market.depth.YahooDepthSource` asks the adapter for four
modules and projects 21 columns. That is not Yahoo's limit, it is the adapter's
request. This module asks once for the fourteen modules that actually carry the
screener vocabulary, plus one ``fundamentals-timeseries`` call for the four
fiscal years and five quarters that ``quoteSummary`` no longer ships (the
statement history modules answer with ``endDate`` only in 2026).

Two Yahoo bugs are corrected at this boundary, and only here:

* ``financialData.debtToEquity`` is printed in **percent points** (AAPL 78.44),
  while the catalog stores a ratio (0.784). The parser divides by 100 exactly
  once. The source is named ``yahoo_full`` — not ``yahoo`` — so
  :class:`DepthObservation`'s legacy ``source == "yahoo"`` division does not
  fire a second time.
* ``summaryDetail.dividendYield`` is a **fraction** (0.0031), while the catalog
  stores percent points (0.31). The parser multiplies by 100 exactly once.

Everything else is a direct key read or a formula that uses only Yahoo's own
numbers. A field that cannot be computed honestly (``eps_past_5y``,
``sales_past_5y``: Yahoo dropped the ``-5y`` trend period) is simply absent; no
growth is invented from three fiscal years, and no ``0`` stands in for ``null``.
The same rule keeps ``dividend_growth_1y/3y/5y`` out of this source: the fourteen
modules carry no dividend history (``summaryDetail.fiveYearAvgDividendYield`` is
an average *yield*, not a growth rate) and this module issues no chart request,
so those three cells stay absent until the chart parser that owns them fills
them.

``summaryDetail.allTimeHigh``/``allTimeLow`` are published as absolute USD
levels, not as Finviz's distance-in-percent: the distance needs the live price,
and the live price is quote-hub owned — a depth row must not read one.

The output is filtered against :data:`app.market.depth_fields.CATALOG_FIELDS`
and against :data:`app.market.depth.QUOTE_ONLY_FIELDS`, so a provider key that
is not a catalog column can never reach the depth store.

``optionable`` is the one catalog cell that neither ``quoteSummary`` nor the
statement history can answer. It is bought with a single extra
``/v7/finance/options`` request, and only when ``include_options=True``: the
default stays at two body requests per symbol so an existing screener run pays
nothing for a cell it did not ask for. The expiry *list* is the evidence, not
the dated chain — a non-empty ``expirationDates`` is ``Yes``, an empty one is
``No``, and a refusal, a 429 or an unparseable body leaves the cell **absent**
instead of publishing a ``No`` that was really "nobody answered".
"""

from __future__ import annotations

from app.market.sources import CrumbRefused, describe_crumb_refusal

import asyncio
import logging
import math
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.market.depth import QUOTE_ONLY_FIELDS, DepthObservation, canonical_symbol
from app.market.depth_fields import CATALOG_FIELDS
from app.providers import http as provider_http
from app.providers.adapters.yahoo import (
    BROWSER_USER_AGENT,
    HOST as YAHOO_HOST,
    OPTIONS_PATH,
)

logger = logging.getLogger("mee.depth")

#: Cookie warm-up + crumb exchange. The host that sets the cookie is not the API
#: host; the token is minted on the API host and rides the data URL.
COOKIE_URL = "https://fc.yahoo.com"
CRUMB_PATH = "/v1/test/getcrumb"
QUOTE_SUMMARY_PATH = "/v10/finance/quoteSummary/{symbol}"
TIMESERIES_PATH = "/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}"

#: The fourteen modules the 116-column vocabulary is read from. Some of them
#: (``recommendationTrend``, ``institutionOwnership``) are requested for audit
#: completeness even though no catalog cell is projected from them; dropping a
#: module is a decision for a later phase, not a silent request optimisation.
QUOTE_SUMMARY_MODULES: tuple[str, ...] = (
    "price",
    "summaryDetail",
    "defaultKeyStatistics",
    "financialData",
    "assetProfile",
    "quoteType",
    "calendarEvents",
    "earnings",
    "earningsTrend",
    "earningsHistory",
    "recommendationTrend",
    "majorHoldersBreakdown",
    "netSharePurchaseActivity",
    "institutionOwnership",
)

#: One request carries every annual/quarterly/trailing series this phase needs.
TIMESERIES_TYPES: tuple[str, ...] = (
    "annualTotalRevenue",
    "annualDilutedEPS",
    "annualEBIT",
    "annualInvestedCapital",
    "annualLongTermDebt",
    "annualStockholdersEquity",
    "annualPretaxIncome",
    "annualTaxProvision",
    "quarterlyTotalRevenue",
    "quarterlyDilutedEPS",
    "trailingDilutedEPS",
    "trailingTotalRevenue",
)

#: ``period1`` = 2009-12-31. Four fiscal years always fit; asking for more than a
#: decade costs bytes and adds nothing to the formulas.
TIMESERIES_PERIOD1 = 1_262_304_000

#: A 401/403 is auth plumbing (a stale crumb), not a verdict about the symbol.
_AUTH_STATUS = frozenset({401, 403})

#: How far an annual anchor may sit from the exact anniversary. A fiscal year is
#: 52/53 weeks, so 75 days absorbs a shifted year end without reaching the next
#: one.
ANNUAL_TOLERANCE_DAYS = 75
#: Same idea for quarterly and trailing points.
QUARTER_TOLERANCE_DAYS = 45


# ---------------------------------------------------------------------------
# Scalar readers
# ---------------------------------------------------------------------------


def _block(modules: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = modules.get(name)
    return value if isinstance(value, Mapping) else {}


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _num(value: Any) -> float | None:
    """A Yahoo number, whether printed raw or wrapped as ``{"raw": ...}``."""

    if isinstance(value, Mapping):
        value = value.get("raw")
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: Any) -> str | None:
    if isinstance(value, Mapping):
        value = _first(value.get("raw"), value.get("fmt"))
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _pct(value: Any) -> float | None:
    """A Yahoo fraction → catalog percent points (×100, once)."""

    number = _num(value)
    return None if number is None else number * 100.0


def _date(value: Any) -> str | None:
    """Epoch seconds or an ISO string → ``YYYY-MM-DD``."""

    raw = value.get("raw") if isinstance(value, Mapping) else value
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if text.isdigit():
            raw = int(text)
        else:
            try:
                return date.fromisoformat(text[:10]).isoformat()
            except ValueError:
                return None
    try:
        epoch = int(raw)
    except (TypeError, ValueError):
        return None
    if epoch <= 0:
        return None
    try:
        return datetime.fromtimestamp(epoch, UTC).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _put(out: dict[str, Any], name: str, value: Any) -> None:
    if value is None or value == "":
        return
    if isinstance(value, float) and not math.isfinite(value):
        return
    out[name] = value


# ---------------------------------------------------------------------------
# quoteSummary
# ---------------------------------------------------------------------------


def quote_summary_result(payload: Any) -> Mapping[str, Any] | None:
    """``quoteSummary.result[0]`` when it is an object, else ``None``."""

    if not isinstance(payload, Mapping):
        return None
    summary = payload.get("quoteSummary")
    if not isinstance(summary, Mapping):
        return None
    result = summary.get("result")
    if isinstance(result, Mapping):
        return result
    if isinstance(result, list) and result and isinstance(result[0], Mapping):
        return result[0]
    return None


def _trend_entry(trend: Mapping[str, Any], period: str) -> Mapping[str, Any]:
    entries = trend.get("trend")
    if not isinstance(entries, list):
        return {}
    for entry in entries:
        if isinstance(entry, Mapping) and str(entry.get("period") or "").strip() == period:
            return entry
    return {}


def _estimate(entry: Mapping[str, Any]) -> Any:
    estimate = entry.get("earningsEstimate")
    return estimate.get("avg") if isinstance(estimate, Mapping) else None


def parse_quote_summary(modules: Mapping[str, Any] | None) -> dict[str, Any]:
    """Project the fourteen modules onto catalog names.

    Only names declared in the 116-column catalog are ever written. A key Yahoo
    adds tomorrow is ignored until someone classifies it.
    """

    if not isinstance(modules, Mapping):
        return {}

    price = _block(modules, "price")
    detail = _block(modules, "summaryDetail")
    stats = _block(modules, "defaultKeyStatistics")
    financial = _block(modules, "financialData")
    profile = _block(modules, "assetProfile")
    quote_type = _block(modules, "quoteType")
    calendar = _block(modules, "calendarEvents")
    trend = _block(modules, "earningsTrend")
    history = _block(modules, "earningsHistory")
    holders = _block(modules, "majorHoldersBreakdown")
    purchases = _block(modules, "netSharePurchaseActivity")

    out: dict[str, Any] = {}

    # -- identification ----------------------------------------------------
    _put(out, "ticker", _first(_text(quote_type.get("symbol")), _text(price.get("symbol"))))
    _put(
        out,
        "company",
        _first(
            _text(price.get("longName")),
            _text(price.get("shortName")),
            _text(quote_type.get("longName")),
        ),
    )
    _put(out, "sector", _text(profile.get("sector")))
    _put(out, "industry", _text(profile.get("industry")))
    _put(out, "country", _text(profile.get("country")))
    _put(out, "website", _text(profile.get("website")))
    _put(out, "employees", _num(profile.get("fullTimeEmployees")))
    _put(out, "security_type", _text(quote_type.get("quoteType")))
    _put(
        out,
        "exchange",
        _first(_text(price.get("exchangeName")), _text(quote_type.get("exchange"))),
    )
    _put(out, "ipo_date", _date(quote_type.get("firstTradeDateEpochUtc")))
    earnings_calendar = calendar.get("earnings")
    earnings_dates = (
        earnings_calendar.get("earningsDate")
        if isinstance(earnings_calendar, Mapping)
        else calendar.get("earningsDate")
    )
    if isinstance(earnings_dates, list) and earnings_dates:
        _put(out, "earnings_date", _date(earnings_dates[0]))

    # -- valuation ---------------------------------------------------------
    market_cap = _num(price.get("marketCap"))
    _put(out, "market_cap", market_cap)
    _put(out, "enterprise_value", _num(stats.get("enterpriseValue")))
    _put(out, "pe", _num(detail.get("trailingPE")))
    _put(out, "forward_pe", _num(detail.get("forwardPE")))
    _put(out, "peg", _num(_first(stats.get("pegRatio"), stats.get("trailingPegRatio"))))
    _put(out, "ps", _num(detail.get("priceToSalesTrailing12Months")))
    _put(out, "pb", _num(stats.get("priceToBook")))
    _put(out, "ev_ebitda", _num(stats.get("enterpriseToEbitda")))
    _put(out, "ev_sales", _num(stats.get("enterpriseToRevenue")))
    cash = _num(financial.get("totalCash"))
    if market_cap is not None and cash is not None and cash > 0:
        _put(out, "pc", market_cap / cash)
    free_cash = _num(financial.get("freeCashflow"))
    if market_cap is not None and free_cash is not None and free_cash > 0:
        _put(out, "pfcf", market_cap / free_cash)

    # -- income ------------------------------------------------------------
    _put(out, "eps", _num(stats.get("trailingEps")))
    _put(
        out,
        "net_income",
        _num(_first(financial.get("netIncomeToCommon"), stats.get("netIncomeToCommon"))),
    )
    _put(out, "sales", _num(financial.get("totalRevenue")))

    next_q = _trend_entry(trend, "0q")
    _put(out, "eps_next_q", _num(_first(_estimate(next_q), _estimate(_trend_entry(trend, "+1q")))))
    this_y = _trend_entry(trend, "0y")
    this_y_growth = _num(this_y.get("growth"))
    if this_y_growth is None:
        average = _num(_estimate(this_y))
        year_ago = _num(this_y.get("yearAgoEps"))
        if average is not None and year_ago:
            this_y_growth = average / year_ago - 1.0
    if this_y_growth is not None:
        _put(out, "eps_this_y", this_y_growth * 100.0)
    _put(out, "eps_next_y", _pct(_trend_entry(trend, "+1y").get("growth")))

    quarterly = history.get("history")
    if isinstance(quarterly, list) and quarterly and isinstance(quarterly[-1], Mapping):
        _put(out, "eps_surprise", _pct(quarterly[-1].get("surprisePercent")))

    _put(out, "gross_margin", _pct(financial.get("grossMargins")))
    _put(out, "operating_margin", _pct(financial.get("operatingMargins")))
    _put(out, "net_margin", _pct(financial.get("profitMargins")))

    # -- balance sheet -----------------------------------------------------
    _put(out, "roa", _pct(financial.get("returnOnAssets")))
    _put(out, "roe", _pct(financial.get("returnOnEquity")))
    _put(out, "current_ratio", _num(financial.get("currentRatio")))
    _put(out, "quick_ratio", _num(financial.get("quickRatio")))
    # Yahoo prints this one in percent points (78.44); the catalog stores 0.784.
    debt_to_equity = _num(financial.get("debtToEquity"))
    if debt_to_equity is not None:
        _put(out, "debt_to_equity", debt_to_equity / 100.0)
    _put(out, "book_per_share", _num(stats.get("bookValue")))
    _put(out, "cash_per_share", _num(financial.get("totalCashPerShare")))

    # -- dividend ----------------------------------------------------------
    _put(out, "dividend", _num(detail.get("dividendRate")))
    _put(
        out,
        "dividend_yield",
        _pct(_first(detail.get("dividendYield"), detail.get("trailingAnnualDividendYield"))),
    )
    _put(out, "dividend_ttm", _num(detail.get("trailingAnnualDividendRate")))
    _put(out, "payout_ratio", _pct(detail.get("payoutRatio")))
    _put(out, "dividend_ex_date", _date(detail.get("exDividendDate")))
    # ``dividend_growth_1y/3y/5y`` are deliberately not written here: quoteSummary
    # ships no dividend history and this source adds no chart request, so there is
    # no honest input for a growth rate. ``fiveYearAvgDividendYield`` is an average
    # yield, not a growth rate, and must not be repurposed as one.

    # -- ownership ---------------------------------------------------------
    shares = _num(stats.get("sharesOutstanding"))
    float_shares = _num(stats.get("floatShares"))
    _put(out, "shares_outstanding", shares)
    _put(out, "shares_float", float_shares)
    if float_shares is not None and shares is not None and shares > 0:
        _put(out, "float_pct", float_shares / shares * 100.0)
    _put(out, "insider_own", _pct(holders.get("insidersPercentHeld")))
    _put(out, "inst_own", _pct(holders.get("institutionsPercentHeld")))
    _put(out, "insider_trans", _pct(purchases.get("netPercentInsiderShares")))
    _put(out, "inst_trans", _pct(purchases.get("netInstBuyingPercent")))
    _put(out, "short_float", _pct(stats.get("shortPercentOfFloat")))
    _put(out, "short_ratio", _num(stats.get("shortRatio")))

    # -- price levels ------------------------------------------------------
    # These two are absolute USD prices in Yahoo's payload, so they are read as
    # levels and never converted into a percentage. Only ``summaryDetail`` is
    # consulted: a same-named key in another module must not shadow the owner.
    _put(out, "all_time_high", _num(detail.get("allTimeHigh")))
    _put(out, "all_time_low", _num(detail.get("allTimeLow")))

    # -- technical / sentiment --------------------------------------------
    _put(out, "beta", _num(_first(detail.get("beta"), stats.get("beta"))))
    _put(out, "recommendation", _num(financial.get("recommendationMean")))
    _put(out, "target_price", _num(financial.get("targetMeanPrice")))

    return out


# ---------------------------------------------------------------------------
# fundamentals-timeseries
# ---------------------------------------------------------------------------


def timeseries_series(payload: Any) -> dict[str, list[tuple[date, float]]]:
    """``type -> [(asOfDate, value), ...]`` ascending, one point per date.

    Yahoo answers either one block per type (``merge=false``) or a single merged
    block; both shapes are accepted, and a duplicated ``asOfDate`` keeps the
    later value rather than producing two anchors for the same fiscal year.
    """

    blocks: list[Any] = []
    if isinstance(payload, list):
        blocks = payload
    elif isinstance(payload, Mapping):
        container = payload.get("timeseries")
        if isinstance(container, Mapping):
            result = container.get("result")
            if isinstance(result, list):
                blocks = result
            elif isinstance(result, Mapping):
                blocks = [result]
        elif isinstance(container, list):
            blocks = container
        else:
            blocks = [payload]

    collected: dict[str, dict[date, float]] = {}
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        for name, points in block.items():
            if name in {"meta", "timestamp"} or not isinstance(points, list):
                continue
            bucket = collected.setdefault(str(name), {})
            for point in points:
                if not isinstance(point, Mapping):
                    continue
                day = _date(point.get("asOfDate"))
                value = _num(point.get("reportedValue"))
                if day is None or value is None:
                    continue
                bucket[date.fromisoformat(day)] = value

    series: dict[str, list[tuple[date, float]]] = {}
    for name, points in collected.items():
        if points:
            series[name] = sorted(points.items())
    return series


def _latest(points: Sequence[tuple[date, float]]) -> float | None:
    return points[-1][1] if points else None


def _anchor(
    points: Sequence[tuple[date, float]], target: date, tolerance_days: int
) -> float | None:
    """The value whose date sits nearest ``target``, inside the tolerance."""

    best: tuple[int, float] | None = None
    for day, value in points:
        distance = abs((day - target).days)
        if distance <= tolerance_days and (best is None or distance < best[0]):
            best = (distance, value)
    return None if best is None else best[1]


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        # 29 February: fall back to the 28th, the same clamp a fiscal calendar uses.
        return day.replace(year=day.year - years, day=28)


def _cagr(points: Sequence[tuple[date, float]], years: int) -> float | None:
    """``years``-year CAGR in percent, or ``None`` when it would be invented.

    A negative base (or a negative end point) makes a CAGR meaningless — the
    root of a negative ratio is not a growth rate — so it stays absent instead
    of being published as a number nobody can defend.
    """

    if len(points) < years + 1:
        return None
    last_day, last_value = points[-1]
    base = _anchor(points[:-1], _years_before(last_day, years), ANNUAL_TOLERANCE_DAYS)
    if base is None or base <= 0 or last_value <= 0:
        return None
    return ((last_value / base) ** (1.0 / years) - 1.0) * 100.0


def _year_over_year(
    points: Sequence[tuple[date, float]], tolerance_days: int = QUARTER_TOLERANCE_DAYS
) -> float | None:
    """Latest point against the point ~12 months earlier, in percent."""

    if len(points) < 2:
        return None
    last_day, last_value = points[-1]
    base = _anchor(
        points[:-1], last_day - timedelta(days=365), tolerance_days
    )
    if base is None or base == 0:
        return None
    return (last_value / base - 1.0) * 100.0


def _roic(series: Mapping[str, Sequence[tuple[date, float]]]) -> float | None:
    ebit = _latest(series.get("annualEBIT", ()))
    capital = _latest(series.get("annualInvestedCapital", ()))
    if ebit is None or capital is None or capital == 0:
        return None
    rate = 0.21
    pretax = _latest(series.get("annualPretaxIncome", ()))
    tax = _latest(series.get("annualTaxProvision", ()))
    if pretax is not None and pretax > 0 and tax is not None:
        candidate = tax / pretax
        if 0.0 <= candidate <= 1.0:
            rate = candidate
    return ebit * (1.0 - rate) / capital * 100.0


def _long_term_debt_to_equity(
    series: Mapping[str, Sequence[tuple[date, float]]]
) -> float | None:
    debt = _latest(series.get("annualLongTermDebt", ()))
    equity = _latest(series.get("annualStockholdersEquity", ()))
    if debt is None or equity is None or equity == 0:
        return None
    return debt / equity


def parse_timeseries(payload: Any) -> dict[str, Any]:
    """The eight growth/return cells the statement history can no longer fill."""

    series = timeseries_series(payload)
    out: dict[str, Any] = {}

    _put(out, "eps_past_3y", _cagr(series.get("annualDilutedEPS", ()), 3))
    _put(out, "sales_past_3y", _cagr(series.get("annualTotalRevenue", ()), 3))
    _put(out, "eps_qoq", _year_over_year(series.get("quarterlyDilutedEPS", ())))
    _put(out, "sales_qoq", _year_over_year(series.get("quarterlyTotalRevenue", ())))
    _put(out, "eps_yoy_ttm", _year_over_year(series.get("trailingDilutedEPS", ())))
    _put(out, "sales_yoy_ttm", _year_over_year(series.get("trailingTotalRevenue", ())))
    _put(out, "roic", _roic(series))
    _put(out, "lt_debt_to_equity", _long_term_debt_to_equity(series))

    # Never fabricated: Yahoo ships four fiscal years and dropped the -5y trend.
    return {name: value for name, value in out.items() if name in _TIMESERIES_FIELDS}


# ---------------------------------------------------------------------------
# v7 options: the expiry list is the whole answer
# ---------------------------------------------------------------------------


def _option_chain_result(payload: Any) -> tuple[bool, Mapping[str, Any] | None]:
    """``(answered, optionChain.result[0])``.

    ``answered`` distinguishes "Yahoo said there is no chain" (``result: []`` —
    a real ``No``) from "the envelope is missing" (nothing may be published).
    """

    if not isinstance(payload, Mapping):
        return False, None
    chain = payload.get("optionChain")
    if not isinstance(chain, Mapping):
        return False, None
    result = chain.get("result")
    if isinstance(result, Mapping):
        return True, result
    if isinstance(result, list):
        if not result:
            return True, None
        entry = result[0]
        return True, entry if isinstance(entry, Mapping) else None
    return False, None


def parse_optionable(payload: Any) -> dict[str, Any]:
    """``{"optionable": "Yes" | "No"}``, or ``{}`` when the body cannot say.

    Only the expiry list is read: a symbol with one expiry is as optionable as a
    symbol with twenty, and fetching the dated chain would spend a second
    request to learn nothing this cell needs.
    """

    answered, entry = _option_chain_result(payload)
    if not answered:
        return {}
    if entry is None:
        # ``optionChain.result`` is an empty list: Yahoo answered, no chain.
        return {"optionable": "No"}
    expiries = entry.get("expirationDates")
    if not isinstance(expiries, list):
        # A missing key is an absent fact, not evidence that there is no chain.
        return {}
    return {"optionable": "Yes" if expiries else "No"}


# ---------------------------------------------------------------------------
# What this source may publish
# ---------------------------------------------------------------------------

_QUOTE_SUMMARY_FIELDS = frozenset(
    {
        "ticker",
        "company",
        "sector",
        "industry",
        "country",
        "website",
        "employees",
        "security_type",
        "exchange",
        "ipo_date",
        "earnings_date",
        "market_cap",
        "enterprise_value",
        "pe",
        "forward_pe",
        "peg",
        "ps",
        "pb",
        "ev_ebitda",
        "ev_sales",
        "pc",
        "pfcf",
        "eps",
        "net_income",
        "sales",
        "eps_next_q",
        "eps_this_y",
        "eps_next_y",
        "eps_surprise",
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
        "dividend_yield",
        "dividend_ttm",
        "payout_ratio",
        "dividend_ex_date",
        "shares_outstanding",
        "shares_float",
        "float_pct",
        "insider_own",
        "inst_own",
        "insider_trans",
        "inst_trans",
        "short_float",
        "short_ratio",
        "all_time_high",
        "all_time_low",
        "beta",
        "recommendation",
        "target_price",
    }
)

_TIMESERIES_FIELDS = frozenset(
    {
        "eps_past_3y",
        "sales_past_3y",
        "eps_qoq",
        "sales_qoq",
        "eps_yoy_ttm",
        "sales_yoy_ttm",
        "roic",
        "lt_debt_to_equity",
    }
)

#: The one cell bought by the optional ``/v7/finance/options`` probe.
_OPTION_FIELDS = frozenset({"optionable"})

#: Everything the parsers can produce.
SUPPORTED_FIELDS: frozenset[str] = (
    _QUOTE_SUMMARY_FIELDS | _TIMESERIES_FIELDS | _OPTION_FIELDS
)

#: What may actually be published: supported ∩ catalog − quote-hub cells. The
#: two intersections are the contract, not a formality: a renamed feature field
#: or a stray price key is dropped here instead of reaching the store.
EMITTED_FIELDS: frozenset[str] = frozenset(
    name
    for name in SUPPORTED_FIELDS
    if name in CATALOG_FIELDS and name not in QUOTE_ONLY_FIELDS
)


# ---------------------------------------------------------------------------
# The source
# ---------------------------------------------------------------------------


class YahooDepthSource:
    """One cookie/crumb per batch; one quoteSummary + one timeseries per symbol.

    ``name`` is ``yahoo_full``. It is deliberately not ``yahoo``: the legacy
    :class:`DepthObservation` divides ``debt_to_equity`` by 100 for a source
    named ``yahoo``, and this parser already did that division once.
    """

    name = "yahoo_full"

    def __init__(
        self,
        *,
        concurrency: int = 2,
        timeout_s: float = 20.0,
        include_timeseries: bool = True,
        include_options: bool = False,
        request_delay_s: float = 0.0,
        symbol_timeout_s: float = 30.0,
        clock: Callable[[], datetime] | None = None,
        proxy: str | None = None,
    ) -> None:
        self.concurrency = max(1, int(concurrency))
        self.timeout_s = float(timeout_s)
        self.include_timeseries = bool(include_timeseries)
        self.include_options = bool(include_options)
        self.request_delay_s = max(0.0, float(request_delay_s))
        self.symbol_timeout_s = max(1.0, float(symbol_timeout_s))
        self._clock = clock or (lambda: datetime.now(UTC))
        #: Network exit for every request in the pass (``None`` = direct).
        #: ``YahooEgress`` supplies one when the direct crumb is throttled.
        self.proxy = proxy
        self._crumb = ""
        self._crumb_lock = asyncio.Lock()

    # -- requests ----------------------------------------------------------

    def headers(self) -> dict[str, str]:
        return {
            "user-agent": BROWSER_USER_AGENT,
            "accept": "application/json,text/plain,*/*",
            "accept-language": "en-US,en;q=0.9",
        }

    @staticmethod
    def quote_summary_url(symbol: str, crumb: str = "") -> str:
        url = (
            f"{YAHOO_HOST}{QUOTE_SUMMARY_PATH.format(symbol=symbol)}"
            f"?modules={','.join(QUOTE_SUMMARY_MODULES)}"
            "&corsDomain=finance.yahoo.com&formatted=false"
        )
        return f"{url}&crumb={crumb}" if crumb else url

    @staticmethod
    def options_url(symbol: str, crumb: str = "") -> str:
        """The expiry list only: no ``date=`` means Yahoo answers the front chain."""

        url = f"{YAHOO_HOST}{OPTIONS_PATH.format(symbol=symbol)}"
        return f"{url}?crumb={crumb}" if crumb else url

    def timeseries_url(
        self,
        symbol: str,
        crumb: str = "",
        *,
        period2: int | None = None,
    ) -> str:
        moment = (
            int(period2)
            if period2 is not None
            else int(self._clock().timestamp())
        )
        params = [
            f"symbol={symbol}",
            f"type={','.join(TIMESERIES_TYPES)}",
            f"period1={TIMESERIES_PERIOD1}",
            f"period2={moment}",
            "merge=false",
        ]
        if crumb:
            params.append(f"crumb={crumb}")
        return f"{YAHOO_HOST}{TIMESERIES_PATH.format(symbol=symbol)}?{'&'.join(params)}"

    async def _crumb_token(self, client: Any) -> str:
        """Exchange the cookie jar for a token (``""`` on refusal)."""

        try:
            response = await client.get(f"{YAHOO_HOST}{CRUMB_PATH}")
        except Exception:  # noqa: BLE001 - auth warm-up must not fail the batch
            return ""
        status = int(getattr(response, "status_code", 0) or 0)
        token = str(getattr(response, "text", "") or "").strip()
        if status != 200:
            raise CrumbRefused(
                "yahoo crumb endpoint refused the handshake: "
                + describe_crumb_refusal(f"HTTP {status}")
            )
        if not token or len(token) > 64:
            raise CrumbRefused(
                "yahoo crumb endpoint refused the handshake: "
                + describe_crumb_refusal(token)
            )
        # A crumb is a short base64-ish token; Yahoo's real tokens contain
        # ``/`` (``a0MnGz/haT1``), so the alphabet is URL-safe base64 plus the
        # RFC 3986 unreserved marks. An HTML challenge page, a space-bearing
        # ``Too Many Requests`` body, or any markup is still not a crumb and
        # must never be pasted into a query string.
        if not all(
            character.isalnum() or character in "._~+/=-" for character in token
        ):
            raise CrumbRefused(
                "yahoo crumb endpoint refused the handshake: "
                + describe_crumb_refusal(token)
            )
        return token

    async def _login(self, client: Any) -> str:
        """One cookie request, one crumb request; the token is reusable."""

        try:
            await client.get(COOKIE_URL)
        except Exception as exc:  # noqa: BLE001 - the data request reports 401 itself
            logger.debug("yahoo_full cookie warm-up failed: %s", exc)
        try:
            return await self._crumb_token(client)
        except CrumbRefused as exc:
            # Named, logged, and still non-fatal: the batch proceeds
            # unauthenticated exactly as before, but the journal now says why.
            logger.warning("yahoo depth running unauthenticated: %s", exc)
            return ""

    async def _renew_crumb(self, client: Any, stale: str) -> str:
        """A fresh token, unless another task already replaced ``stale``."""

        async with self._crumb_lock:
            if self._crumb and self._crumb != stale:
                return self._crumb
            token = await self._login(client)
            if token:
                self._crumb = token
            return token

    @staticmethod
    async def _get_json(client: Any, url: str) -> tuple[int, Any]:
        response = await client.get(url)
        status = int(getattr(response, "status_code", 0) or 0)
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001 - a challenge page is not a body
            payload = None
        return status, payload

    async def _get_json_paced(self, client: Any, url: str) -> tuple[int, Any]:
        """One paced GET; the daily full batch needs a stable arrival rate."""
        if self.request_delay_s:
            await asyncio.sleep(self.request_delay_s)
        return await self._get_json(client, url)

    async def _optionable_for(self, client: Any, symbol: str, crumb: str) -> str | None:
        """``Yes``/``No`` from the expiry list, or ``None`` when Yahoo would not say.

        A 401/403 is the stale-crumb dance, retried exactly once (the same rule
        the other two endpoints follow). 429/5xx/HTML are not evidence about the
        symbol: they leave the cell absent rather than publishing a false ``No``.
        """

        status, payload = await self._get_json_paced(
            client, self.options_url(symbol, crumb)
        )
        if status in _AUTH_STATUS:
            crumb = await self._renew_crumb(client, crumb)
            if crumb:
                status, payload = await self._get_json_paced(
                    client, self.options_url(symbol, crumb)
                )
        if status != 200:
            return None
        return parse_optionable(payload).get("optionable")

    # -- parsing -----------------------------------------------------------

    async def _fields_for(self, client: Any, symbol: str) -> dict[str, Any]:
        crumb = self._crumb
        status, payload = await self._get_json_paced(
            client, self.quote_summary_url(symbol, crumb)
        )
        if status in _AUTH_STATUS:
            crumb = await self._renew_crumb(client, crumb)
            if crumb:
                status, payload = await self._get_json_paced(
                    client, self.quote_summary_url(symbol, crumb)
                )

        modules = quote_summary_result(payload) if status == 200 else None
        fields = parse_quote_summary(modules) if modules is not None else {}
        if modules is None:
            return fields

        if self.include_timeseries:
            status, payload = await self._get_json_paced(
                client, self.timeseries_url(symbol, crumb)
            )
            if status in _AUTH_STATUS:
                crumb = await self._renew_crumb(client, crumb)
                if crumb:
                    status, payload = await self._get_json_paced(
                        client, self.timeseries_url(symbol, crumb)
                    )
            if status == 200:
                fields.update(parse_timeseries(payload))

        if self.include_options:
            # Third body request, after the statement history: the expiry list is
            # the last fact this row needs, so a refused quote never pays for it.
            optionable = await self._optionable_for(client, symbol, crumb)
            if optionable is not None:
                fields["optionable"] = optionable
        return fields

    # -- batch -------------------------------------------------------------

    async def fetch_many(
        self, symbols: Sequence[str]
    ) -> dict[str, DepthObservation]:
        wanted: list[str] = []
        seen: set[str] = set()
        for raw in symbols:
            symbol = canonical_symbol(raw)
            if symbol and symbol not in seen:
                seen.add(symbol)
                wanted.append(symbol)
        if not wanted:
            return {}

        out: dict[str, DepthObservation] = {}
        semaphore = asyncio.Semaphore(self.concurrency)

        async with provider_http.build_client(
            timeout_s=self.timeout_s, headers=self.headers(), proxy=self.proxy
        ) as client:
            # One handshake for the whole batch: three auth requests per symbol
            # would dwarf the two body requests that carry data.
            self._crumb = await self._login(client)

            async def one(symbol: str) -> None:
                async with semaphore:
                    try:
                        fields = await asyncio.wait_for(
                            self._fields_for(client, symbol),
                            timeout=self.symbol_timeout_s,
                        )
                    except Exception as exc:  # noqa: BLE001 - one symbol is optional
                        logger.info("yahoo_full depth failed for %s: %s", symbol, exc)
                        return
                    published = {
                        name: value
                        for name, value in fields.items()
                        if name in EMITTED_FIELDS and value is not None and value != ""
                    }
                    if not published:
                        return
                    out[symbol] = DepthObservation(
                        symbol=symbol,
                        fields=published,
                        # Depth recency is *fetch* recency; regularMarketTime can be
                        # the previous close and must not age the whole row.
                        as_of=self._clock(),
                        source=self.name,
                    )

            await asyncio.gather(*(one(symbol) for symbol in wanted))

        return out


__all__ = [
    "ANNUAL_TOLERANCE_DAYS",
    "COOKIE_URL",
    "EMITTED_FIELDS",
    "QUARTER_TOLERANCE_DAYS",
    "QUOTE_SUMMARY_MODULES",
    "SUPPORTED_FIELDS",
    "TIMESERIES_PERIOD1",
    "TIMESERIES_TYPES",
    "YahooDepthSource",
    "parse_optionable",
    "parse_quote_summary",
    "parse_timeseries",
    "quote_summary_result",
    "timeseries_series",
]
