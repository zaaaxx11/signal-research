"""Provider-symbol mapping for the screen's deep half.

The screen scanner speaks exchange-local tickers (``2330`` on TWSE, ``2222`` on
TADAWUL, ``005930`` on KRX). Yahoo speaks suffixed tickers (``2330.TW``,
``2222.SR``, ``005930.KS``). Passing the raw scanner symbol to the deep engine
would ask Yahoo for a US ticker that does not exist and silently produce an
empty top-20 table, so the mapping lives here, in one table, and is applied
before the deep engine is called.

US venues are deliberately unsuffixed: ``AAPL`` is already the provider symbol,
and appending ``.US`` would be wrong.
"""

from __future__ import annotations

from typing import Any

#: TradingView exchange code → Yahoo suffix. Only venues whose suffix is stable
#: are listed; an unknown exchange is left alone rather than guessed.
YAHOO_SUFFIX: dict[str, str] = {
    "TWSE": ".TW",
    "TPEX": ".TWO",
    "TADAWUL": ".SR",
    "KRX": ".KS",
    "KOSDAQ": ".KQ",
    "EURONEXT": ".AS",
    "XETR": ".DE",
    "SIX": ".SW",
    "JPX": ".T",
    "TSE": ".T",
    "HKEX": ".HK",
    "SSE": ".SS",
    "SZSE": ".SZ",
    "NSE": ".NS",
    "BSE": ".BO",
    "LSE": ".L",
    "TSX": ".TO",
    "TSXV": ".V",
    "ASX": ".AX",
    "BVMF": ".SA",
    "BMV": ".MX",
    "OMXSTO": ".ST",
    "OMXCOP": ".CO",
    "OMXHEL": ".HE",
    "OSL": ".OL",
    "WSE": ".WA",
    "IST": ".IS",
    "TLV": ".TA",
    "JSE": ".JO",
    "SGX": ".SI",
    "MYX": ".KL",
    "SET": ".BK",
}

#: US venues in both spellings: the MIC that a snapshot row carries after
#: :func:`app.screen.universe.mic_for`, and the TradingView code the scanner
#: prints. ``XCBO`` is the fourth: CBOE lists stocks *and* 1,677 live ETFs, and
#: a set that stops at three is how an ETF tab loses a venue (research D8/D10).
US_EXCHANGES: frozenset[str] = frozenset(
    {"XNAS", "XNYS", "XASE", "XCBO", "NASDAQ", "NYSE", "AMEX", "CBOE"}
)


def provider_symbol(symbol: Any, exchange: Any = None) -> str:
    """Return the Yahoo-shaped ticker for one screen row.

    A symbol that already carries a suffix is returned unchanged. US venues are
    returned unchanged even when the scanner prints a bare ticker. An unknown
    exchange is returned unchanged: a guessed suffix is worse than a failed
    lookup with a visible reason.
    """

    text = str(symbol or "").strip()
    venue = str(exchange or "").strip().upper()
    if not text or venue in US_EXCHANGES or "." in text:
        return text
    return text + YAHOO_SUFFIX.get(venue, "")


__all__ = ["US_EXCHANGES", "YAHOO_SUFFIX", "provider_symbol"]
