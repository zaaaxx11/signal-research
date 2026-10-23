"""Universe: filter, cross-listing dedupe, MIC mapping and tab ownership.

The scanner answers with what the venue lists; the universe answers with what a
*screen* means. Four decisions, each a gate in PLAN-screen.md §2/§8:

1. **Common stock only.** ``type=stock`` and ``subtype=common``; an ETF, a REIT,
   a warrant and a preferred share are different instruments with different
   risk, and a default equity screen that quietly mixes them is not a screen.
2. **At least USD 100 million of market cap, boundary included.** ``>=``, not
   ``>``: the contract names a floor, and a company worth exactly the floor is
   inside it. A row whose cap the provider did not send is dropped — "unknown"
   is not "large enough".
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
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

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


def issuer_key(description: Any, market_cap: float | None) -> tuple[str, float]:
    """The dedupe key: normalised issuer name + the market cap.

    Documented limitation (22-screen-red.md §4): two different companies that
    share a name *and* a market cap would be merged. ISIN/FIGI are not in the
    scan payload, so a key that pretends to have them would be worse.
    """

    name = " ".join(str(description or "").casefold().replace("\u00a0", " ").split())
    name = name.strip(" .,;")
    return name, float(market_cap or 0.0)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _normalize(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """One provider row → one candidate universe row, or ``None`` when filtered.

    The row keeps both the screen-shaped cells and the three private keys the
    dedupe needs (``_issuer``, ``_cap`` and ``_order``); :func:`build_universe`
    strips them before returning.
    """

    if str(row.get("type") or "").strip().lower() != COMMON_TYPE:
        return None
    if str(row.get("subtype") or "").strip().lower() != COMMON_SUBTYPE:
        return None

    exchange = str(row.get("exchange") or "").strip().upper()
    if not exchange or exchange in OTC_EXCHANGES:
        return None

    market_cap = _number(row.get("market_cap_basic"))
    if market_cap is None or market_cap < MIN_MARKET_CAP_USD:
        return None

    symbol = str(row.get("symbol") or "").strip()
    if not symbol:
        return None

    currency = str(row.get("currency") or "").strip().upper() or None
    turnover = _number(row.get("Value.Traded"))
    mic = mic_for(exchange)

    return {
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
        # -- private: dedupe bookkeeping, stripped before the row is returned
        "_issuer": issuer_key(row.get("description") or row.get("name"), market_cap),
        "_order": 0,
    }


def _preferred(candidate: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    """Is ``candidate`` a better representative of the same issuer?

    US listing first (it is where the issuer's price is discovered), then the
    largest traded value, then the earliest row: a tie never reshuffles between
    two runs of the same input.
    """

    candidate_us = candidate["exchange"] in US_EXCHANGES
    current_us = current["exchange"] in US_EXCHANGES
    if candidate_us != current_us:
        return candidate_us
    return (_number(candidate.get("turnover")) or 0.0) > (_number(current.get("turnover")) or 0.0)


def build_universe(rows: Iterable[Mapping[str, Any]], *, region: str) -> list[dict[str, Any]]:
    """Filter ``rows`` into the issuers of ``region`` (``us`` or ``global``).

    Dedupe runs **before** the tab split and over the whole input, so an issuer
    with a US listing cannot appear in the Global tab under a foreign venue.
    """

    if region not in REGIONS:
        raise ValueError(f"unknown region {region!r}; expected one of {', '.join(REGIONS)}")

    chosen: dict[tuple[str, float], dict[str, Any]] = {}
    for order, row in enumerate(rows):
        candidate = _normalize(row)
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
        universe.append(
            {column: candidate.get(column) for column in UNIVERSE_COLUMNS}
        )
    return universe


__all__ = [
    "COMMON_SUBTYPE",
    "COMMON_TYPE",
    "MIC_BY_EXCHANGE",
    "MIN_MARKET_CAP_USD",
    "OTC_EXCHANGES",
    "REGIONS",
    "UNIVERSE_COLUMNS",
    "US_EXCHANGES",
    "build_universe",
    "issuer_key",
    "mic_for",
]
