"""Universe: filter, cross-listing dedupe, MIC mapping and tab ownership.

The scanner answers with what the venue lists; the universe answers with what a
*screen* means. Four decisions, each a gate in PLAN-screen.md §2/§8:

1. **Common stock only.** ``type=stock`` and ``subtype=common``; an ETF, a REIT,
   a warrant and a preferred share are different instruments with different
   risk, and a default equity screen that quietly mixes them is not a screen.
2. **At least USD 100 million of market cap by default, boundary included.**
   ``>=``, not ``>``: the contract names a floor, and a company worth exactly the
   floor is inside it. A row whose cap the provider did not send is dropped —
   "unknown" is not "large enough". ``min_market_cap`` lowers the floor for a
   nano-cap view (``0`` admits every priced common stock); "unknown" still loses
   even at zero, because a missing cap is not a cap of zero.
3. **No OTC/pink sheet**, whatever the cap: those listings are out of scope.
4. **One issuer, one row.** NVDA trades on NASDAQ, SIX, BX, MUN and TRADEGATE;
   a universe that keeps five rows for one company both distorts the ranking and
   makes the two tabs overlap. The key is the normalised issuer description plus
   the market cap (the same company reported by different venues agrees on
   both), and the representative is the US listing when one exists, otherwise
   the most traded one.

Tab ownership follows from the representative, not from a second scan: an
issuer that has a US listing belongs to the ``us`` tab, and ``global`` carries
only issuers **without** one. That is what makes "the two tabs share no symbol"
a property of the data model instead of a filter applied twice.

``turnover_usd`` is only filled for USD listings. Converting a CHF turnover with
an invented rate would be a fabricated number (PLAN §8 #8), so a non-USD row
keeps ``turnover_usd = None`` and its local ``turnover``.

Parity metrics
--------------

The light row carries thirteen columns; a Finviz parity screen needs to filter
on P/E, beta, RSI, the moving averages and so on. :data:`PARITY_FIELDS` is the
one table that says which **provider** column becomes which **canonical** row
field, and :data:`PARITY_COLUMNS` is that table's provider side in the order the
scan asks for them. ``build_universe(..., columns=PARITY_COLUMNS)`` copies a
cell **verbatim** into its field: a number stays a number, and a cell the
provider did not send — or sent as something that is not a finite number — stays
``None``. It is never coerced to ``0`` (a missing P/E is not a P/E of zero) and
never derived from another cell (a computed ratio would be a number nobody can
trace back to the scan).

Two of those fields publish a *window* in their name, because the provider's
lookback is not Finviz's: ``beta_1y`` (Finviz beta is 5-year monthly) and
``avg_volume_30d`` / ``rel_volume_10d``. Naming them after the provider's window
is the honest option; calling them ``beta`` or ``avg_volume`` would claim a
methodology the row does not have. The five light metrics (``close``,
``change``, ``volume``, ``Value.Traded``, ``market_cap_basic``) are *not* in
:data:`PARITY_FIELDS` — the light path already normalises them, and one owner per
field beats two.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

from app.screen.scanner import SCAN_COLUMNS

#: PLAN-screen.md §2: market cap floor, inclusive.
MIN_MARKET_CAP_USD = 100_000_000

REGIONS: tuple[str, ...] = ("us", "global")

#: TradingView exchange code → MIC. Only US venues are mapped; every other
#: exchange keeps the code the provider printed rather than a guessed MIC.
MIC_BY_EXCHANGE: dict[str, str] = {
    "NASDAQ": "XNAS",
    "NYSE": "XNYS",
    "AMEX": "XASE",
}
US_EXCHANGES: frozenset[str] = frozenset(MIC_BY_EXCHANGE.values())
#: OTC/pink sheet listings, in provider and MIC spelling. Out of scope.
OTC_EXCHANGES: frozenset[str] = frozenset(
    {"OTC", "OTCBB", "OTCQX", "OTCQB", "PNK", "PINK", "GREY", "OTCMKTS"}
)

#: The provider's own vocabulary for "common stock".
COMMON_TYPE = "stock"
COMMON_SUBTYPE = "common"

#: Provider column → canonical row field, for the metrics the light row does not
#: carry. The provider names are TradingView's scanner vocabulary, verified live
#: against ``scanner.tradingview.com/america/scan``; the canonical names are what
#: :data:`app.screen.filters.FIELDS` and :mod:`app.screen.filter_fields` filter
#: on. Every entry is copied cell-for-cell, so a ``None`` here is a metric the
#: scan always sends (a price multiple can still be null per row, which is what
#: ``PARITY_NULLABLE`` records).
PARITY_FIELDS: Mapping[str, str] = {
    # -- valuation multiples
    "price_earnings_ttm": "pe_ttm",
    "price_earnings_forward": "pe_forward",
    "price_earnings_growth_ttm": "peg",
    "price_sales_ttm": "ps",
    "price_book_fq": "pb",
    "price_free_cash_flow_ttm": "pfcf",
    "enterprise_value_ebitda_ttm": "ev_ebitda",
    # -- profitability and leverage
    "return_on_equity": "roe",
    "return_on_assets": "roa",
    "return_on_invested_capital": "roic",
    "current_ratio": "current_ratio",
    "quick_ratio": "quick_ratio",
    "debt_to_equity": "debt_to_equity",
    "gross_margin": "gross_margin",
    "operating_margin": "oper_margin",
    "net_margin": "profit_margin",
    # -- forward estimates (levels, not growth rates)
    "earnings_per_share_forecast_next_fy": "eps_forecast_next_fy",
    "earnings_per_share_forecast_next_fq": "eps_forecast_next_fq",
    "revenue_forecast_next_fy": "revenue_forecast_next_fy",
    # -- share structure and trading depth
    "total_shares_outstanding": "shares_outstanding",
    "float_shares_outstanding": "float_shares",
    "short_float": "short_float",
    "average_volume_10d_calc": "avg_volume_10d",
    "average_volume_30d_calc": "avg_volume_30d",
    "relative_volume_10d_calc": "rel_volume_10d",
    # -- analyst and event data
    "Recommend.All": "recommendation",
    "price_target_average": "target_price",
    "earnings_release_next_date": "earnings_date",
    "ipo_date": "listing_date",
    # -- technicals
    "SMA20": "sma_20",
    "SMA50": "sma_50",
    "SMA200": "sma_200",
    "RSI": "rsi_14",
    "beta_1_year": "beta_1y",
    "price_52_week_high": "high_52w",
    "price_52_week_low": "low_52w",
    "Volatility.D": "volatility_d",
    "Volatility.W": "volatility_w",
    "Volatility.M": "volatility_m",
    "Perf.W": "ret_1w",
    "Perf.1M": "ret_1m",
    "Perf.3M": "ret_3m",
    "Perf.6M": "ret_6m",
    "Perf.YTD": "ret_ytd",
    "Perf.Y": "ret_1y",
    "Perf.5Y": "ret_5y",
    "Perf.10Y": "ret_10y",
}

#: Columns a parity scan asks for: the light fields first (so one request still
#: serves the browse path), then every parity metric. Built from
#: :data:`app.screen.scanner.SCAN_COLUMNS` rather than retyped, and deduplicated
#: in first-seen order — ``RSI``/``beta_1_year``/``price_52_week_*`` are already
#: in the light block and must not be requested twice.
PARITY_COLUMNS: tuple[str, ...] = tuple(
    dict.fromkeys(tuple(SCAN_COLUMNS) + tuple(PARITY_FIELDS))
)

#: Metrics the provider can answer with ``null`` for a given row, and the four it
#: answered with ``null`` for **every** row when probed live (US: 0 of 5,720;
#: global: 0 of 96,278). They stay in the table so a later provider fix fills them
#: without a rename, but no filter key maps to them: a filter on an always-null
#: column could only ever return an empty screen, which reads as "no matches".
PARITY_NULLABLE: frozenset[str] = frozenset(
    {"pe_forward", "ps", "short_float", "listing_date"}
)


#: Fields a universe row carries. ``rank`` is added later by
#: :func:`app.screen.rank.rank_rows`, so ordering stays a separate decision.
UNIVERSE_COLUMNS: tuple[str, ...] = (
    "symbol",
    "name",
    "exchange",
    "country",
    "sector",
    "price",
    "change_pct",
    "volume",
    "turnover",
    "turnover_usd",
    "market_cap_usd",
    "currency",
    "is_primary",
)


def mic_for(exchange: Any) -> str:
    """The exchange code as a MIC, mapping only the venues we really know."""

    code = str(exchange or "").strip().upper()
    return MIC_BY_EXCHANGE.get(code, code)


def issuer_key(description: Any, market_cap: float | None) -> tuple[str, int]:
    """The dedupe key: normalised issuer name + a million-dollar cap bucket.

    Two listings of one issuer report the same cap with floating noise
    (Tencent: ``...763.4693`` vs ``...763.46924``), so exact float equality
    fails and the same company enters the top 20 twice. The cap is therefore
    bucketed to USD 1M. Share-class suffixes (``Class A``/``Class H``) are
    removed for the same reason: ICBC's A and H lines are one issuer.

    Documented limitation: two different companies sharing a name *and* a
    million-dollar cap would be merged. ISIN/FIGI are not in the scan payload,
    so a key that pretends to have them would be worse.
    """

    name = " ".join(str(description or "").casefold().replace("\u00a0", " ").split())
    name = name.strip(" .,;")
    # "industrial and commercial bank ... class h", "Novartis AG TEMP" and
    # "Nestle S.A. ADR" are the same issuer in another line, not a new company.
    import re

    suffix = re.compile(
        r"\s+(?:class\s+[a-z]|temp|temporary|reg|registered|adr|gdr|"
        r"spon\s+adr|sponsored\s+adr)\b\.?$"
    )
    while True:
        cleaned = suffix.sub("", name).strip(" .,;")
        if cleaned == name:
            break
        name = cleaned
    return name, int(round(float(market_cap or 0.0) / 1_000_000.0))


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _metric(value: Any) -> float | None:
    """A parity cell as a row value: a finite float, or ``None``.

    ``NaN``/``inf`` are unknown, not numbers (they compare false in every
    direction and would make a filter silently drop or keep a row at random),
    and a string that is not a number stays ``None`` instead of becoming ``0``.
    """

    number = _number(value)
    if number is None or not math.isfinite(number):
        return None
    return number


def parity_pairs(columns: Sequence[str] | None) -> tuple[tuple[str, str], ...]:
    """The ``(provider column, canonical field)`` pairs a scan asked for.

    ``columns=None`` is the light row: no parity metric is copied, so the
    browse/export path is byte-identical to what it was before the table
    existed. A column with no entry in :data:`PARITY_FIELDS` is a *state*
    column (``type``, ``subtype``, ``currency``...) and is not copied.
    """

    if columns is None:
        return ()
    wanted = frozenset(columns)
    return tuple(
        (column, field) for column, field in PARITY_FIELDS.items() if column in wanted
    )


def _normalize(
    row: Mapping[str, Any],
    *,
    min_market_cap: float,
    allow_any_subtype: bool = False,
    metrics: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any] | None:
    """One provider row → one candidate universe row, or ``None`` when filtered.

    The row keeps both the screen-shaped cells and the two private keys the
    dedupe needs (``_issuer`` and ``_order``); :func:`build_universe` strips them
    before returning.
    """

    if str(row.get("type") or "").strip().lower() != COMMON_TYPE:
        return None
    if not allow_any_subtype and str(row.get("subtype") or "").strip().lower() != COMMON_SUBTYPE:
        return None

    exchange = str(row.get("exchange") or "").strip().upper()
    if not exchange or exchange in OTC_EXCHANGES:
        return None

    market_cap = _number(row.get("market_cap_basic"))
    if market_cap is None or market_cap < min_market_cap:
        return None

    symbol = str(row.get("symbol") or "").strip()
    if not symbol:
        return None

    currency = str(row.get("currency") or "").strip().upper() or None
    turnover = _number(row.get("Value.Traded"))
    mic = mic_for(exchange)

    candidate: dict[str, Any] = {
        "symbol": symbol,
        "name": row.get("description") or row.get("name"),
        "exchange": mic,
        "country": row.get("country"),
        "sector": row.get("sector"),
        "price": _number(row.get("close")),
        "change_pct": _number(row.get("change")),
        "volume": _number(row.get("volume")),
        "turnover": turnover,
        "turnover_usd": turnover if currency == "USD" else None,
        "market_cap_usd": market_cap,
        "currency": currency,
        "is_primary": True,
    }
    for column, field in metrics:
        # A verbatim copy: the provider's number, or ``None`` when it sent none.
        candidate[field] = _metric(row.get(column))
    candidate["_issuer"] = issuer_key(row.get("description") or row.get("name"), market_cap)
    candidate["_order"] = 0
    return candidate


#: Where an issuer's price is discovered / most useful to a reader. A listing
#: on a major venue beats a cross-listing on an obscure one even when the
#: obscure venue reports a larger local turnover.
MAJOR_EXCHANGES: frozenset[str] = frozenset(
    {
        "XNAS",
        "XNYS",
        "XASE",
        "HKEX",
        "SSE",
        "SZSE",
        "TWSE",
        "TPEX",
        "KRX",
        "KOSDAQ",
        "TSE",
        "JPX",
        "EURONEXT",
        "XETR",
        "SIX",
        "LSE",
        "NSE",
        "BSE",
        "TSX",
        "ASX",
        "BVMF",
        "BMV",
        "TADAWUL",
    }
)


def _venue_priority(exchange: Any) -> int:
    code = str(exchange or "").upper()
    if code in US_EXCHANGES:
        return 3
    if code in MAJOR_EXCHANGES:
        return 2
    return 1


def _preferred(candidate: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    """Is ``candidate`` a better representative of the same issuer?

    US listing first (it is where the issuer's price is discovered), then a
    major venue over a cross-listing, then the largest traded value, then the
    earliest row: a tie never reshuffles between two runs of the same input.
    """

    candidate_priority = _venue_priority(candidate["exchange"])
    current_priority = _venue_priority(current["exchange"])
    if candidate_priority != current_priority:
        return candidate_priority > current_priority
    return (_number(candidate.get("turnover")) or 0.0) > (_number(current.get("turnover")) or 0.0)


def build_universe(
    rows: Iterable[Mapping[str, Any]],
    *,
    region: str,
    min_market_cap: float = MIN_MARKET_CAP_USD,
    columns: Sequence[str] | None = None,
    allow_any_subtype: bool = False,
) -> list[dict[str, Any]]:
    """Filter ``rows`` into the issuers of ``region`` (``us`` or ``global``).

    Dedupe runs **before** the tab split and over the whole input, so an issuer
    with a US listing cannot appear in the Global tab under a foreign venue.

    ``min_market_cap`` is the inclusive USD floor; the default keeps the
    documented USD 100 million screen. Passing ``0`` is the explicit nano-cap
    view: every common listing with a reported cap survives, while a row with no
    cap at all is still dropped (unknown is not zero).

    ``columns`` names the provider columns the scan asked for. When it includes
    parity metrics (:data:`PARITY_COLUMNS`), each row also carries their
    canonical fields, copied cell-for-cell by :func:`parity_pairs`. ``None`` is
    the light row, unchanged.

    ``allow_any_subtype`` relaxes the ``subtype=common`` gate, which is what a
    selection over the *whole* universe needs: the Finviz filter panel addresses
    a stock market, not only its common shares. ``type=stock`` is still
    enforced, so a fund or an ETF cannot enter through the wider door.
    """

    if region not in REGIONS:
        raise ValueError(f"unknown region {region!r}; expected one of {', '.join(REGIONS)}")

    floor = _number(min_market_cap)
    if floor is None or floor < 0:
        raise ValueError(
            f"min_market_cap must be a non-negative number, got {min_market_cap!r}"
        )

    metrics = parity_pairs(columns)
    chosen: dict[tuple[str, float], dict[str, Any]] = {}
    for order, row in enumerate(rows):
        candidate = _normalize(
            row,
            min_market_cap=floor,
            allow_any_subtype=allow_any_subtype,
            metrics=metrics,
        )
        if candidate is None:
            continue
        candidate["_order"] = order
        key = candidate["_issuer"]
        current = chosen.get(key)
        if current is None or _preferred(candidate, current):
            chosen[key] = candidate

    universe: list[dict[str, Any]] = []
    for candidate in chosen.values():
        is_us = candidate["exchange"] in US_EXCHANGES
        is_us_issuer = str(candidate.get("country") or "").strip().casefold() in {
            "united states",
            "united states of america",
            "usa",
            "us",
        }
        if region == "us" and not is_us:
            continue
        if region == "global" and (is_us or is_us_issuer):
            # The global endpoint returns foreign *listings* of US issuers
            # (NVDACL on BCS, GOOGL on BMV). Excluding only the US venue would
            # put those cross-listings in the Global tab; the issuer's country
            # is the tab's actual boundary.
            continue
        projected = {column: candidate.get(column) for column in UNIVERSE_COLUMNS}
        for _, field in metrics:
            projected[field] = candidate.get(field)
        universe.append(projected)
    return universe


__all__ = [
    "COMMON_SUBTYPE",
    "COMMON_TYPE",
    "MIC_BY_EXCHANGE",
    "MIN_MARKET_CAP_USD",
    "OTC_EXCHANGES",
    "PARITY_COLUMNS",
    "PARITY_FIELDS",
    "PARITY_NULLABLE",
    "REGIONS",
    "UNIVERSE_COLUMNS",
    "US_EXCHANGES",
    "build_universe",
    "issuer_key",
    "mic_for",
    "parity_pairs",
]
