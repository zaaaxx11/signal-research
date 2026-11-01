"""Universe: filter, cross-listing dedupe, MIC mapping and tab ownership.

The scanner answers with what the venue lists; the universe answers with what a
*screen* means. Four decisions, each a gate in PLAN-screen.md §2/§8:

1. **One instrument family per tab.** The stock tabs are ``type=stock`` (with
   ``subtype=common`` on the browse display); the ``us_etf`` tab is
   ``type=fund`` + ``subtype=etf``. An ETF, a REIT, a warrant and a preferred
   share are different instruments with different risk, and a default equity
   screen that quietly mixes them is not a screen. The ETF tab is a region
   rather than a flag so a snapshot, an export and a selection all name the same
   universe.
2. **At least USD 100 million of market cap by default, boundary included.**
   ``>=``, not ``>``: the contract names a floor, and a company worth exactly the
   floor is inside it. A row whose cap the provider did not send is dropped —
   "unknown" is not "large enough". ``min_market_cap`` lowers the floor for a
   nano-cap view (``0`` admits every priced common stock); "unknown" still loses
   even at zero, because a missing cap is not a cap of zero. The floor belongs to
   the *stock* screen; the ETF universe has none (``min_market_cap=None``) and
   keeps an unreported cap as unknown.
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
cell **verbatim** into its field: a number stays a number, a label stays a
string (``industry`` is the one text field, :data:`PARITY_TEXT_FIELDS`), and a
cell the provider did not send — or sent as something that is not a finite
number — stays ``None``. It is never coerced to ``0`` (a missing P/E is not a
P/E of zero) and never replaced by a value derived from another cell: a computed
ratio nobody can trace back to the scan is not a copy. The three columns in
:data:`DERIVED_FIELDS` (below) are the one deliberate exception, and each of them
names the row cells it reads.

Two of those fields publish a *window* in their name, because the provider's
lookback is not Finviz's: ``beta_1y`` (Finviz beta is 5-year monthly) and
``avg_volume_30d`` / ``rel_volume_10d``. Three publish their *unit*: ``gap_pct``
and ``dividend_yield`` are the provider's percent (``-0.57`` is a 0.57% gap
down, ``0.47`` is a 0.47% yield — not a 47% yield), and ``atr_14`` is a price,
not a percent. Naming them after the provider's window or unit is the honest
option; calling them ``beta`` or ``avg_volume`` would claim a methodology the
row does not have. The five light metrics (``close``,
``change``, ``volume``, ``Value.Traded``, ``market_cap_basic``) are *not* in
:data:`PARITY_FIELDS` — the light path already normalises them, and one owner per
field beats two.

Derived metrics
---------------

Three Finviz filter keys ask a question the provider answers with a different
scale or shape, and each of them is answerable by *algebra* over cells the row
already carries. :data:`DERIVED_FIELDS` (from :mod:`app.screen.derived`) is the
one place allowed to compute instead of copy, and it is deliberately small:

* ``recom_finviz`` = ``3 - 2 * recommendation`` — the published map from the
  provider's -1..+1 mean rating to Finviz's 1..5 analyst score;
* ``change_from_open`` = ``((1 + change_pct/100) / (1 + gap_pct/100) - 1) * 100``
  — the previous close cancels between the two percentages, so the change from
  the session open needs no open column;
* ``pct_from_52w_high`` = ``(price / high_52w - 1) * 100`` — **one direction**;
  the low side stays the absolute ``low_52w``, because a derived low distance
  would be a second invented metric, not a copy.

A derivation is allowed here only because it is an identity (no estimate, no
invented denominator), it requests no extra provider column, and its name states
the scale or the direction. A missing input still yields ``None``: a derived
column never manufactures a number, and never falls back to ``0``. Derived
columns are carried on the **parity** row only — ``columns=None`` (the browse
and export path) is byte-identical to what it was before they existed. The
union a filter key may point at is :data:`CARRIED_FIELDS`.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

from app.screen.derived import (
    DERIVED_COLUMNS,
    DERIVED_FIELDS,
    DERIVED_INPUTS,
    derive_row,
)
from app.screen.scanner import (
    ASSET_CLASSES,
    DEFAULT_FLOOR,
    DEFAULT_STOCK_FLOOR,
    ETF_SUBTYPE,
    ETF_TYPE,
    REGIONS as SCAN_REGIONS,
    SCAN_COLUMNS,
    STOCK_TYPE,
    asset_class_for as _scan_asset_class_for,
)

#: PLAN-screen.md §2: market cap floor, inclusive. One number, imported from the
#: scanner instead of retyped, so the two layers cannot drift.
MIN_MARKET_CAP_USD = DEFAULT_STOCK_FLOOR

#: The three tabs the product ships: two stock tabs and the US ETF tab. The ETF
#: tab is a first-class region (not a sort flag) because every layer already
#: keys a snapshot by ``(region, sort)`` and because the instrument family is a
#: property of the tab a reader opens.
REGIONS: tuple[str, ...] = SCAN_REGIONS
ETF_REGION = "us_etf"


#: TradingView exchange code → MIC. Only US venues are mapped; every other
#: exchange keeps the code the provider printed rather than a guessed MIC.
MIC_BY_EXCHANGE: dict[str, str] = {
    "NASDAQ": "XNAS",
    "NYSE": "XNYS",
    "AMEX": "XASE",
    # CBOE lists ETFs too (IROC and others); leaving it unmapped made the ETF
    # universe silently drop ~1.7k live rows because ``is_us`` only knew the
    # three stock venues.
    "CBOE": "XCBO",
}
US_EXCHANGES: frozenset[str] = frozenset(MIC_BY_EXCHANGE.values())
#: OTC/pink sheet listings, in provider and MIC spelling. Out of scope.
OTC_EXCHANGES: frozenset[str] = frozenset(
    {"OTC", "OTCBB", "OTCQX", "OTCQB", "PNK", "PINK", "GREY", "OTCMKTS"}
)

#: The provider's own vocabulary for the two families. ``COMMON_TYPE`` keeps its
#: historical name (``stock``) so every existing caller reads unchanged.
COMMON_TYPE = STOCK_TYPE
COMMON_SUBTYPE = "common"


def asset_class_for(region: str, asset_class: str | None = None) -> str:
    """Resolve ``stock``/``etf`` for ``region`` — one rule, shared with the scanner.

    ``us_etf`` is the ETF tab, ``us``/``global`` default to stock, and an
    explicit family is honoured on ``us`` (the ETF universe without the tab
    alias). ``global`` + ``etf`` is refused: the ETF contract is US-only.
    """

    return _scan_asset_class_for(region, asset_class)


#: Provider column → canonical row field, for the metrics the light row does not
#: carry. The provider names are TradingView's scanner vocabulary, verified live
#: against ``scanner.tradingview.com/america/scan``; the canonical names are what
#: :data:`app.screen.filters.FIELDS` and :mod:`app.screen.filter_fields` filter
#: on. Every entry is copied cell-for-cell, so a ``None`` here is a metric the
#: scan always sends (a price multiple can still be null per row, which is what
#: ``PARITY_NULLABLE`` records).
#:
#: The three derived columns (``recom_finviz``, ``change_from_open``,
#: ``pct_from_52w_high``) are deliberately absent: this mapping's keys are sent to
#: the provider as column names, so a computed field would ask the scan for a
#: column that does not exist. They live in
#: :data:`app.screen.derived.DERIVED_FIELDS` and are computed from these copies,
#: on a parity row only.
PARITY_FIELDS: Mapping[str, str] = {
    # -- classification (a text label, copied verbatim: "Semiconductors")
    "industry": "industry",
    # -- fund size (ETFs report AUM, not market cap)
    "aum": "aum_usd",
    # -- valuation multiples
    "price_earnings_ttm": "pe_ttm",
    "price_earnings_forward": "pe_forward",
    "price_earnings_growth_ttm": "peg",
    "price_sales_ttm": "ps",
    "price_book_fq": "pb",
    "price_free_cash_flow_ttm": "pfcf",
    "enterprise_value_ebitda_ttm": "ev_ebitda",
    # -- dividends: the provider's percent, not a fraction (0.47 = 0.47%)
    "dividends_yield": "dividend_yield",
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
    # The provider's signed percent (-0.57 = a 0.57% gap down) and the 14-session
    # average true range in price units — not a percent.
    "gap": "gap_pct",
    "ATR": "atr_14",
    "Volatility.D": "volatility_d",
    "Volatility.W": "volatility_w",
    "Volatility.M": "volatility_m",
    "Perf.W": "ret_1w",
    "Perf.1M": "ret_1m",
    "Perf.3M": "ret_3m",
    "Perf.6M": "ret_6m",
    "Perf.YTD": "ret_ytd",
    "Perf.Y": "ret_1y",
    "Perf.3Y": "ret_3y",
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

#: The parity fields that are **labels**, not numbers. ``industry`` is the
#: provider's own classification string ("Semiconductors"); every other parity
#: field is a number, so :func:`_metric` would turn this one into ``None`` for
#: every row. A text cell is copied verbatim and trimmed, and an empty string is
#: unknown (``None``) rather than a label a ``eq`` filter could match.
PARITY_TEXT_FIELDS: frozenset[str] = frozenset({"industry"})

#: Metrics the provider can answer with ``null`` for a given row, and the four it
#: answered with ``null`` for **every** row when probed live (US: 0 of 5,720;
#: global: 0 of 96,278). They stay in the table so a later provider fix fills them
#: without a rename. **No filter key may treat these cells as evidence**: a filter
#: on an always-null column could only ever return an empty screen, which reads as
#: "no matches". ``pe_forward``/``ps`` and ``short_float`` are the exceptions that
#: prove the rule — their keys filter a real observation from another table:
#: :mod:`app.screen.fundamentals` joins forward P/E, P/S and payout ratio from the
#: fundamentals snapshot store, and :mod:`app.screen.facts` joins the float and
#: ownership percentages from the Finviz stock page. ``listing_date`` has no such
#: source and stays refused.
PARITY_NULLABLE: frozenset[str] = frozenset(
    {"pe_forward", "ps", "short_float", "listing_date"}
)


#: Cells the depth overlay copies onto a row under their **catalogue** name.
#: They are declared here rather than discovered from the store, because
#: ``CARRIED_FIELDS`` is what a filter key is validated against: a key that
#: points at one of these must be able to say *which* names are real, and a set
#: built from a live store file would make the answer depend on a machine.
#:
#: The declaration is checked from both ends by
#: ``tests/test_screen_depth_fields.py``: every name is a catalogue column, and a
#: real store fills every name for at least one symbol — so the set cannot claim
#: a cell the collector stopped writing. The last two are **text**
#: (``candlestick_pattern``, an ISO-8601 ``news_time``): they are bucket-only and
#: deliberately absent from :data:`app.screen.filters.FIELDS`.
DEPTH_FIELDS: frozenset[str] = frozenset(
    {
        # growth rates, percent points (18.52 = 18.52%)
        "eps_this_y",
        "eps_next_y",
        "eps_qoq",
        "eps_yoy_ttm",
        "eps_past_3y",
        "eps_past_5y",
        "eps_next_5y",
        "sales_qoq",
        "sales_yoy_ttm",
        "sales_past_3y",
        "sales_past_5y",
        # surprises, percent points
        "eps_surprise",
        "revenue_surprise",
        # leverage, a ratio (0.66 = 0.66x)
        "lt_debt_to_equity",
        # dividend growth, percent points, three windows only
        "dividend_growth_1y",
        "dividend_growth_3y",
        "dividend_growth_5y",
        # net insider/institutional change, percent points
        "insider_trans",
        "inst_trans",
        # price levels, price units
        "high_50d",
        "low_50d",
        "all_time_high",
        "all_time_low",
        # text: bucket-only, never a numeric filter field
        "candlestick_pattern",
        "news_time",
    }
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

#: Every column a row can carry: the light block, the parity copies, the three
#: derived columns and the depth cells the overlay copies under their catalogue
#: names. This is the union :mod:`app.screen.filter_fields` validates a filter
#: key against — a key may point at a light column, a provider metric, a derived
#: metric or a depth cell, and at nothing else.
CARRIED_FIELDS: frozenset[str] = (
    frozenset(UNIVERSE_COLUMNS)
    | frozenset(PARITY_FIELDS.values())
    | frozenset(DERIVED_FIELDS)
    | DEPTH_FIELDS
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
        r"\s+(?:class\s+[a-z]|new|temp|temporary|reg|registered|adr|gdr|"
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


def _text(value: Any) -> str | None:
    """A parity *label* cell as a row value: the provider's string, or ``None``.

    Trimmed, because the provider pads some labels; blank is unknown, not a
    label, so a filter can never match an empty industry by accident.
    """

    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


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
    min_market_cap: float | None,
    asset_class: str,
    allow_any_subtype: bool = False,
    metrics: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any] | None:
    """One provider row → one candidate universe row, or ``None`` when filtered.

    The row keeps both the screen-shaped cells and the two private keys the
    dedupe needs (``_issuer`` and ``_order``); :func:`build_universe` strips them
    before returning.
    """

    expected_type = STOCK_TYPE if asset_class == "stock" else ETF_TYPE
    if str(row.get("type") or "").strip().lower() != expected_type:
        return None
    if asset_class == "etf":
        # A fund tab is the ETF universe: closed-end funds, commodity pools and
        # preferred shares are other instruments with other risk.
        if str(row.get("subtype") or "").strip().lower() != ETF_SUBTYPE:
            return None
    elif not allow_any_subtype and str(row.get("subtype") or "").strip().lower() != COMMON_SUBTYPE:
        return None

    exchange = str(row.get("exchange") or "").strip().upper()
    if not exchange or exchange in OTC_EXCHANGES:
        return None

    market_cap = _number(row.get("market_cap_basic"))
    if asset_class == "etf" and market_cap is None:
        # Funds report AUM under ``aum`` and leave ``market_cap_basic`` empty.
        # AUM is the fund's size, the number a market-cap column means for an
        # ETF; using it is a documented mapping, not a fabricated market cap.
        market_cap = _metric(row.get("aum"))
    if min_market_cap is not None and (
        market_cap is None or market_cap < min_market_cap
    ):
        # A floor is a claim about size: a row with no cap cannot prove it fits.
        # With no floor (the ETF contract) an unreported cap is simply unknown
        # and the instrument stays, with ``market_cap_usd=None``.
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
        # A verbatim copy: the provider's number/label, or ``None`` when it sent
        # none. ``industry`` is the one label field; everything else is numeric.
        if field in PARITY_TEXT_FIELDS:
            candidate[field] = _text(row.get(column))
        else:
            candidate[field] = _metric(row.get(column))
    if metrics:
        # The three derived columns (app.screen.derived) exist only where their
        # inputs do: a parity scan. The light row must not grow a computed
        # metric, so ``columns=None`` skips this entirely and stays byte-identical.
        candidate.update(derive_row(candidate))
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
    min_market_cap: float | None | object = DEFAULT_FLOOR,
    columns: Sequence[str] | None = None,
    allow_any_subtype: bool = False,
    asset_class: str | None = None,
) -> list[dict[str, Any]]:
    """Filter ``rows`` into the issuers of ``region`` (``us``/``global``/``us_etf``).

    Dedupe runs **before** the tab split and over the whole input, so an issuer
    with a US listing cannot appear in the Global tab under a foreign venue.

    ``min_market_cap`` is the inclusive USD floor; the default keeps the
    documented USD 100 million screen for stocks. Passing ``0`` is the explicit
    nano-cap view: every common listing with a reported cap survives, while a
    row with no cap at all is still dropped (unknown is not zero). Passing
    ``None`` means *no floor at all* — the ETF default — and there a row whose
    cap the provider did not send stays with ``market_cap_usd=None``.

    ``asset_class`` selects the instrument family. It defaults to the region's
    own family (``us_etf`` → ``etf``, everything else → ``stock``), so every
    existing call is unchanged; an explicit ``"etf"`` on ``us`` builds the ETF
    universe without the tab alias. The family gates are structural: a stock
    screen never admits ``type=fund`` and the ETF screen never admits
    ``type=stock``; OTC stays out of both.

    ``columns`` names the provider columns the scan asked for. When it includes
    parity metrics (:data:`PARITY_COLUMNS`), each row also carries their
    canonical fields, copied cell-for-cell by :func:`parity_pairs`, plus the
    three derived columns of :data:`DERIVED_FIELDS` computed from those cells
    (``None`` wherever an input is missing). ``None`` is the light row,
    unchanged: no parity metric and no derived column is added.

    ``allow_any_subtype`` relaxes the ``subtype=common`` gate, which is what a
    selection over the *whole* stock universe needs: the Finviz filter panel
    addresses a stock market, not only its common shares. ``type=stock`` is
    still enforced, so a fund or an ETF cannot enter through the wider door; the
    flag has no meaning on the ETF tab, where ``subtype=etf`` is the definition.
    """

    if region not in REGIONS:
        raise ValueError(f"unknown region {region!r}; expected one of {', '.join(REGIONS)}")

    family = asset_class_for(region, asset_class)
    if min_market_cap is DEFAULT_FLOOR:
        floor: float | None = MIN_MARKET_CAP_USD if family == "stock" else None
    elif min_market_cap is None:
        floor = None
    else:
        floor = _number(min_market_cap)
        if floor is None or floor < 0:
            raise ValueError(
                f"min_market_cap must be a non-negative number, got {min_market_cap!r}"
            )
        if family == "etf" and floor == 0:
            # The ETF contract has no cap floor. Callers that pass the stock
            # sentinel (0 = admit nano caps) must not accidentally require a
            # reported cap: most ETFs have ``market_cap_basic=None``, and
            # ``cap is None`` would drop the entire tab.
            floor = None

    metrics = parity_pairs(columns)
    chosen: dict[tuple[str, float], dict[str, Any]] = {}
    for order, row in enumerate(rows):
        candidate = _normalize(
            row,
            min_market_cap=floor,
            asset_class=family,
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
        if region in ("us", ETF_REGION) and not is_us:
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
        if metrics:
            # Derived columns ride along with the parity row only, so a light
            # browse/export row keeps exactly ``UNIVERSE_COLUMNS``.
            for field in DERIVED_COLUMNS:
                projected[field] = candidate.get(field)
        universe.append(projected)
    return universe


def _validate_derived() -> None:
    """Fail at import if a derived column and the row disagree.

    A derived formula reads row columns, so an input that no row carries would
    silently compute ``None`` for every symbol — a filter that always returns an
    empty screen. A derived name that shadows a light column or a parity field
    would overwrite a provider cell. Both are boot errors, not runtime surprises.
    """

    known = CARRIED_FIELDS
    for field, inputs in DERIVED_INPUTS.items():
        unknown = [name for name in inputs if name not in known]
        if unknown:
            raise RuntimeError(
                f"derived field {field!r} reads columns no row carries: {unknown}"
            )
    provider_side = frozenset(UNIVERSE_COLUMNS) | frozenset(PARITY_FIELDS.values())
    shadowed = sorted(set(DERIVED_COLUMNS) & provider_side)
    if shadowed:
        raise RuntimeError(f"derived fields overwrite a copied cell: {shadowed}")


_validate_derived()


__all__ = [
    "ASSET_CLASSES",
    "CARRIED_FIELDS",
    "DEPTH_FIELDS",
    "COMMON_SUBTYPE",
    "COMMON_TYPE",
    "DERIVED_COLUMNS",
    "DERIVED_FIELDS",
    "DERIVED_INPUTS",
    "ETF_REGION",
    "ETF_SUBTYPE",
    "ETF_TYPE",
    "MIC_BY_EXCHANGE",
    "MIN_MARKET_CAP_USD",
    "OTC_EXCHANGES",
    "PARITY_COLUMNS",
    "PARITY_FIELDS",
    "PARITY_NULLABLE",
    "PARITY_TEXT_FIELDS",
    "REGIONS",
    "UNIVERSE_COLUMNS",
    "US_EXCHANGES",
    "asset_class_for",
    "build_universe",
    "derive_row",
    "issuer_key",
    "mic_for",
    "parity_pairs",
]
