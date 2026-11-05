"""``GET /market/alt`` and ``GET /market/quotes`` — the markets the nav still owes.

Why these routes exist
----------------------
The selector's universe is US equities and funds. Crypto, futures and FX are not
in it — the provider that answers the scan does not carry them — so the nav's
remaining market tabs, the watchlist and the portfolio have no server-side
source at all. Yahoo's v8 chart endpoint does answer every one of them (a probe
on 2026-10-31 returned BTC-USD 77,318.72, ES=F 7,614.00 and EURUSD=X 1.1166),
but it is unofficial, needs a browser ``User-Agent``, and answers one symbol per
request. So the reading is done here, once per class, and published in the shape
a table wants.

The catalogue is data
---------------------
:data:`CRYPTO`, :data:`FUTURES` and :data:`FOREX` are curated tuples of
:class:`Instrument` — 122 crypto coins, 25 front-month contracts, 20 pairs —
rather than a list of whatever a scan happened to return. That is what makes
``universe_total`` a fact about the product (122) instead of a fact about an
outage (0), and it is why a symbol that fails is *missing from the rows* while
still being counted: **adversarial (b)**, an empty ``timestamp`` array, and a
fetch that never answered, are the same statement — no measurement — and never a
row with a zero price.

What a row means
----------------
``price`` is the **last non-zero close** in the series, walked backwards: the
provider sends a ``null`` for the bar that is still forming and a ``0.0`` for a
print that never happened (a halt, a session that did not open), and neither is
a price. ``change_pct`` is ``(price / previous - 1) * 100`` where ``previous`` is
the series' ``chartPreviousClose`` — the close of the **session before** the one
the chart covers. On a closed market that is exactly right: Saturday's futures
table shows Friday's close against Thursday's, so the move is the last session's
move and not a comparison against a week-old print (**adversarial (a)**).
``volume`` is the sum of the window's bar volumes — the session's total, which is
the number a table's volume column means; a window with no volume at all is
``0.0`` shares, which is what it says.

A row is published only when both numbers exist: a price with no previous close
can state a level but not a move, and this document's rows are ``price`` *and*
``change_pct``. Such a symbol is counted in ``universe_total`` and absent from
``rows``, exactly like a failed fetch.

One batched read per class, and a class-keyed cache
---------------------------------------------------
``/market/alt?class=crypto`` reads all 40 symbols through **one** transport call
(one client, bounded concurrency, one gather), parses each answer, and keeps the
class's rows for :data:`CACHE_TTL_S` seconds. A symbol the provider answers with
no bars at all — it does that intermittently for a live ticker — is asked once
more, so a transient empty body costs one extra read instead of a hole in the
table; a symbol it refuses is not retried, because a 404 is an answer. The cache
key is the class name — the whole key — so crypto's rows can never answer
``class=futures``, even when both requests are cold and in flight at once
(**adversarial (c)**). A second request inside the window makes **zero**
transport calls and re-encodes the cached rows in microseconds; ``X-Alt-Cache``
says ``hit`` or ``miss`` so that is observable without a clock.

``/market/quotes`` answers a watchlist: up to :data:`MAX_SYMBOLS` symbols,
comma-separated, de-duplicated and upper-cased, with one row each for the ones
that resolved and the rest listed in ``missing`` **in the order they were asked
for**. A US equity comes from the selector's scan rows — one pass over the
universe, memoised per scan generation, because those rows already carry a last
price and a change (**adversarial (d)**: a thousand-symbol watchlist must not
become a thousand HTTP requests). A symbol whose suffix names another market
(``-USD``, ``=F``, ``=X``) is measured through the same reader ``/market/alt``
uses, and only those symbols reach the transport. Nothing is invented: a symbol
the scan does not carry and the provider does not answer is ``missing``.

Revalidation
------------
The validator is derived from the answer's **inputs**, before the answer exists::

    W/"alt-<sha256(v1;class=…;per=…;epoch=<n>;shape=1)>"
    W/"qts-<sha256(v1;symbols=…;scan=<generation>;epoch=<n>;shape=1)>"

``epoch`` is the 60-second window the cache itself turns over, so a warm
``If-None-Match`` is a ``304`` with no provider call — including on a process
that has never fetched this class, which is why a restart does not force a
``200``. Past the window the tag moves and the client gets fresh numbers instead
of a stale ``304``. ``Cache-Control: no-cache`` rides on both answers, and
``HEAD`` is its own route (a second method on one ``api_route`` collides on the
operation id) answering the same headers plus the length the ``GET`` would carry,
with no bytes.

The symbol list is part of the quotes tag in the caller's order, not sorted:
``rows`` and ``missing`` follow the request, so two orders are two documents.

The transport is injected
-------------------------
:class:`HttpTransport` is the only object here that opens a socket, and
:class:`AltState` takes a replacement through its constructor — the application
holds one on ``app.state.alt``, so a test installs a counting double and the
suite stays socket-free. The default reads ``app.providers.http.build_client`` as
a module attribute, which is the seam ``tests._phase2.install_web`` replaces.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from app.api.cache import not_modified
from app.api.discovery import _universe_family
from app.api.sectors import (
    BodyMemo,
    CachedDocument,
    encode,
    etag,
    memo_for,
    moment,
    stamp_for,
    universe_for,
)
from app.auth import ApiError
from app.market.chart import _number
from app.market.sources import BROWSER_USER_AGENT

router = APIRouter(tags=["discovery"])

#: The provider and the endpoint, stated once.
BASE_URL = "https://query1.finance.yahoo.com"

#: The three markets this route speaks, in the order a menu prints them.
CLASSES: tuple[str, ...] = ("crypto", "futures", "forex")

#: What the prices are measured from. Published as ``method`` so a client never
#: has to guess whether a row is a last trade, a midpoint or a settlement.
METHOD = "last_5m_close_vs_chart_previous_close"

#: How long a class's rows are served from memory. A minute is short enough that
#: a reader who reloads sees the tape move and long enough that a polling table
#: costs one provider read per class per minute.
CACHE_TTL_S = 60.0

#: The page size asked for and the largest one answered. ``per`` is clamped into
#: ``1..PER_MAX`` rather than refused: a caller who asks for 500 rows of a
#: 40-symbol class gets the 40 it has, and ``per`` says what was applied.
PER_DEFAULT = 50
PER_MAX = 200

#: How many symbols one ``/market/quotes`` call may name. A watchlist is tens of
#: names; fifty is a page of them, and the cap keeps one call from becoming a
#: provider read of a thousand series.
MAX_SYMBOLS = 50

#: How many symbol keys the quote cache and the equity index keep. Insertion
#: order is the eviction order: a memory budget, not a hit-rate target.
CACHE_LIMIT = 256
EQUITY_INDEX_LIMIT = 4
QUOTES_MEMO_LIMIT = 32

#: The documents' shapes and meanings, folded into their tags. Bump one when a
#: key changes or a number changes meaning.
ALT_SHAPE = "1"
QUOTES_SHAPE = "1"

#: A provider read is a few kilobytes over TLS; eight seconds leaves room for a
#: cold connection without holding a class's table behind a dead one.
FETCH_TIMEOUT_S = 8.0

#: How many series one class read asks for at a time. Forty at once is a burst a
#: free endpoint can throttle; twelve measured ~1 s for 94 symbols on 2026-10-31
#: and stays inside the four-second budget for a cold class.
CONCURRENCY = 12

#: How many failed reads one transport keeps in its ledger. A symbol the provider
#: refused is not a silent hole — the reason is kept, bounded, for whoever asks.
FAILURE_LEDGER = 64

#: How many times one symbol is asked for in a single measurement. The second
#: attempt exists because the endpoint intermittently answers a live symbol with
#: an empty ``timestamp`` array; a third would be a retry storm, not a recovery.
READ_ATTEMPTS = 2

#: The population an equity quote is read from: the selector's US stock universe,
#: the same scan ``/market/map`` and ``/market/groups`` share.
REGION = "us"
FAMILY = _universe_family(REGION, None)

#: The currency a row carries when the provider names none. Every instrument in
#: the catalogue is quoted in dollars, so this is a fallback, not a guess.
DEFAULT_CURRENCY = "USD"

#: What a ticker may look like: letters, digits, and the four punctuation marks
#: the vocabulary uses — ``^GSPC``, ``BTC-USD``, ``EURUSD=X``, ``0005.HK``.
#: Requiring at least one alphanumeric is what refuses ``..``.
_SYMBOL = re.compile(r"^[A-Za-z0-9.^=_-]{1,24}$")


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Instrument:
    """One symbol the class route knows by name, and the currency it trades in."""

    symbol: str
    name: str
    currency: str = DEFAULT_CURRENCY


def _catalogue(*pairs: tuple[str, str]) -> tuple[Instrument, ...]:
    """A tuple of instruments from ``(symbol, name)`` pairs, in menu order."""

    return tuple(Instrument(symbol=symbol, name=name) for symbol, name in pairs)


#: 122 crypto coins, largest first. Curated, not derived: a coin that delists
#: leaves a hole in ``measured`` (visible) rather than a hole in the list.
CRYPTO: tuple[Instrument, ...] = _catalogue(
    ("BTC-USD", "Bitcoin"),
    ("ETH-USD", "Ethereum"),
    ("USDT-USD", "Tether"),
    ("BNB-USD", "BNB"),
    ("SOL-USD", "Solana"),
    ("XRP-USD", "XRP"),
    ("USDC-USD", "USD Coin"),
    ("ADA-USD", "Cardano"),
    ("DOGE-USD", "Dogecoin"),
    ("TRX-USD", "TRON"),
    ("AVAX-USD", "Avalanche"),
    ("LINK-USD", "Chainlink"),
    ("DOT-USD", "Polkadot"),
    ("TON-USD", "Toncoin"),
    ("SHIB-USD", "Shiba Inu"),
    ("LTC-USD", "Litecoin"),
    ("BCH-USD", "Bitcoin Cash"),
    ("XLM-USD", "Stellar"),
    ("ATOM-USD", "Cosmos"),
    ("ETC-USD", "Ethereum Classic"),
    ("XMR-USD", "Monero"),
    ("FIL-USD", "Filecoin"),
    ("ARB-USD", "Arbitrum"),
    ("OP-USD", "Optimism"),
    ("NEAR-USD", "NEAR Protocol"),
    ("ICP-USD", "Internet Computer"),
    ("INJ-USD", "Injective"),
    ("HBAR-USD", "Hedera"),
    ("VET-USD", "VeChain"),
    ("RENDER-USD", "Render"),
    ("AAVE-USD", "Aave"),
    ("MKR-USD", "Maker"),
    ("ALGO-USD", "Algorand"),
    ("SAND-USD", "The Sandbox"),
    ("TIA-USD", "Celestia"),
    ("SEI-USD", "Sei"),
    ("LDO-USD", "Lido DAO"),
    ("WLD-USD", "Worldcoin"),
    ("JUP-USD", "Jupiter"),
    ("ONDO-USD", "Ondo"),
    ("ZEC-USD", "Zcash"),
    ("DAI-USD", "Dai"),
    ("UNI-USD", "Uniswap"),
    ("SUI-USD", "Sui"),
    ("TAO-USD", "Bittensor"),
    ("PEPE-USD", "Pepe"),
    ("DASH-USD", "Dash"),
    ("APT-USD", "Aptos"),
    ("PYTH-USD", "Pyth"),
    ("CRV-USD", "Curve"),
    ("FET-USD", "Fetch.ai"),
    ("XTZ-USD", "Tezos"),
    ("BONK-USD", "Bonk"),
    ("GRT-USD", "The Graph"),
    ("RUNE-USD", "THORChain"),
    ("EIGEN-USD", "EigenLayer"),
    ("WIF-USD", "dogwifcoin"),
    ("IMX-USD", "Immutable"),
    ("DYDX-USD", "dYdX"),
    ("GALA-USD", "Gala"),
    ("BAND-USD", "Band Protocol"),
    ("XAI-USD", "Xai"),
    ("DYM-USD", "Dymension"),
    ("EOS-USD", "EOS"),
    ("HYPE-USD", "Hyperliquid"),
    ("USDS-USD", "USDS"),
    ("WBT-USD", "WhiteBIT Coin"),
    ("LEO-USD", "LEO Token"),
    ("USDE-USD", "Ethena USDe"),
    ("RAIN-USD", "Rain"),
    ("CC-USD", "Canton"),
    ("USD1-USD", "USD1"),
    ("XAUT-USD", "Tether Gold"),
    ("USDG-USD", "Global Dollar"),
    ("QNT-USD", "Quant"),
    ("PYUSD-USD", "PayPal USD"),
    ("RLUSD-USD", "Ripple USD"),
    ("CRO-USD", "Cronos"),
    ("USDY-USD", "Ondo US Dollar Yield"),
    ("OKB-USD", "OKB"),
    ("USYC-USD", "Circle USYC"),
    ("ENA-USD", "Ethena"),
    ("BUIDL-USD", "BlackRock USD Institutional Digital Liquidity Fund"),
    ("M-USD", "MemeCore"),
    ("EURSAFO-USD", "Spiko Amundi Overnight Swap Fund (EUR)"),
    ("PAXG-USD", "PAX Gold"),
    ("USDF-USD", "Falcon USD"),
    ("PUMP-USD", "Pump.fun"),
    ("ASTER-USD", "Aster"),
    ("BTW-USD", "Bitway"),
    ("U-USD", "United Stables"),
    ("SKY-USD", "Sky"),
    ("USDD-USD", "USDD"),
    ("MORPHO-USD", "Morpho"),
    ("WLFI-USD", "World Liberty Financial"),
    ("MNT-USD", "Mantle"),
    ("HTX-USD", "HTX DAO"),
    ("BFUSD-USD", "BFUSD"),
    ("BGB-USD", "Bitget Token"),
    ("USDGO-USD", "USDGO"),
    ("JST-USD", "JUST"),
    ("VVV-USD", "Venice Token"),
    ("KCS-USD", "KuCoin"),
    ("GT-USD", "Gate"),
    ("POL-USD", "POL (ex-MATIC)"),
    ("OUSD-USD", "Open USD"),
    ("PI-USD", "Pi Network"),
    ("LIT-USD", "Lighter"),
    ("KAS-USD", "Kaspa"),
    ("NEXO-USD", "NEXO"),
    ("GHO-USD", "GHO"),
    ("ZRO-USD", "LayerZero"),
    ("STX-USD", "Stacks"),
    ("NIGHT-USD", "Midnight"),
    ("USTB-USD", "Invesco Short Duration US Government Securities Fund"),
    ("CAKE-USD", "PancakeSwap"),
    ("XDC-USD", "XDC Network"),
    ("PEARL-USD", "Pearl"),
    ("JAAA-USD", "Janus Henderson Anemoy AAA CLO Fund"),
    ("BDX-USD", "Beldex"),
    ("USD0-USD", "Usual USD"),
    ("ETHFI-USD", "Ether.fi"),
)

#: 25 front-month contracts — the equity indices, energy, metals, grains, the
#: rates complex and the three FX contracts a "futures" tab actually lists.
#: ``=F`` is the provider's continuous front-month quote, so "front-month" is
#: the provider's own roll, not a month this file pins.
FUTURES: tuple[Instrument, ...] = _catalogue(
    ("ES=F", "E-mini S&P 500"),
    ("NQ=F", "E-mini Nasdaq 100"),
    ("YM=F", "E-mini Dow"),
    ("RTY=F", "E-mini Russell 2000"),
    ("CL=F", "Crude Oil WTI"),
    ("BZ=F", "Brent Crude Oil"),
    ("NG=F", "Natural Gas"),
    ("HO=F", "Heating Oil"),
    ("RB=F", "RBOB Gasoline"),
    ("GC=F", "Gold"),
    ("SI=F", "Silver"),
    ("HG=F", "Copper"),
    ("PL=F", "Platinum"),
    ("ZC=F", "Corn"),
    ("ZS=F", "Soybean"),
    ("ZW=F", "Wheat"),
    ("ZL=F", "Soybean Oil"),
    ("ZM=F", "Soybean Meal"),
    ("ZN=F", "10-Year T-Note"),
    ("ZB=F", "30-Year T-Bond"),
    ("ZF=F", "5-Year T-Note"),
    ("ZT=F", "2-Year T-Note"),
    ("6E=F", "Euro FX"),
    ("6J=F", "Japanese Yen FX"),
    ("6B=F", "British Pound FX"),
)

#: 20 FX pairs: the seven majors, the crosses a table lists, and two dollar
#: pairs that price an emerging and a Nordic currency.
FOREX: tuple[Instrument, ...] = _catalogue(
    ("EURUSD=X", "Euro / US Dollar"),
    ("GBPUSD=X", "British Pound / US Dollar"),
    ("USDJPY=X", "US Dollar / Japanese Yen"),
    ("USDCHF=X", "US Dollar / Swiss Franc"),
    ("USDCAD=X", "US Dollar / Canadian Dollar"),
    ("AUDUSD=X", "Australian Dollar / US Dollar"),
    ("NZDUSD=X", "New Zealand Dollar / US Dollar"),
    ("EURGBP=X", "Euro / British Pound"),
    ("EURJPY=X", "Euro / Japanese Yen"),
    ("GBPJPY=X", "British Pound / Japanese Yen"),
    ("AUDJPY=X", "Australian Dollar / Japanese Yen"),
    ("EURCHF=X", "Euro / Swiss Franc"),
    ("EURAUD=X", "Euro / Australian Dollar"),
    ("EURCAD=X", "Euro / Canadian Dollar"),
    ("GBPCHF=X", "British Pound / Swiss Franc"),
    ("CADJPY=X", "Canadian Dollar / Japanese Yen"),
    ("CHFJPY=X", "Swiss Franc / Japanese Yen"),
    ("NZDJPY=X", "New Zealand Dollar / Japanese Yen"),
    ("USDMXN=X", "US Dollar / Mexican Peso"),
    ("USDSEK=X", "US Dollar / Swedish Krona"),
)

#: The catalogue, by class name — the one place a class is defined.
CATALOGUE: dict[str, tuple[Instrument, ...]] = {
    "crypto": CRYPTO,
    "futures": FUTURES,
    "forex": FOREX,
}

#: Every catalogued instrument by symbol, so a batch quote names ``BTC-USD``
#: "Bitcoin" without the caller having to know which class it came from.
BY_SYMBOL: dict[str, Instrument] = {
    instrument.symbol: instrument
    for instruments in CATALOGUE.values()
    for instrument in instruments
}


# ---------------------------------------------------------------------------
# One measured quote
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AltQuote:
    """One symbol's last price, its move against the previous session, its volume."""

    symbol: str
    name: str
    price: float
    change_pct: float
    volume: float
    currency: str

    def document(self) -> dict[str, Any]:
        """The row ``/market/alt`` publishes, in the order a table reads it."""

        return {
            "symbol": self.symbol,
            "name": self.name,
            "price": self.price,
            "change_pct": self.change_pct,
            "volume": self.volume,
            "currency": self.currency,
        }


def _column(entry: Mapping[str, Any], key: str) -> Sequence[Any]:
    """One of the provider's parallel columns, or an empty sequence.

    The payload aligns ``timestamp`` with ``open``/``high``/``low``/``close``/
    ``volume`` **by index**, so a column that is not a sequence (a null, a
    mapping, a string) cannot be aligned at all and reads as absent.
    """

    series = entry.get(key)
    if isinstance(series, Sequence) and not isinstance(series, (str, bytes)):
        return series
    return ()


def _previous_close(meta: Mapping[str, Any]) -> float | None:
    """Yahoo's previous-session close, from the three spellings it uses.

    ``chartPreviousClose`` is the one this route publishes: the close immediately
    before the window the chart covers. ``previousClose`` and
    ``regularMarketPreviousClose`` are the fallbacks for a body that omits it. A
    value that is not strictly positive is not a close, so it is skipped rather
    than published as ``0.0`` — and a symbol with no usable reference has no
    move, so it is not a row.
    """

    for key in ("chartPreviousClose", "previousClose", "regularMarketPreviousClose"):
        value = _number(meta.get(key))
        if value is not None and value > 0:
            return value
    return None


def _text(value: Any) -> str | None:
    """A provider string, trimmed, or ``None`` when it is not one."""

    if isinstance(value, str):
        token = value.strip()
        return token or None
    return None


def _last_close(entry: Mapping[str, Any]) -> tuple[float, float] | None:
    """The last bar with a usable close, as ``(close, window volume)``.

    Walked **backwards** because the provider's tail is where the nulls are: the
    bar that is still forming has no close, and a closed print is ``0.0``. Both
    are skipped, so the price is the last one that actually traded rather than
    the last index. An empty ``timestamp`` array has no bar at all and answers
    ``None`` — **adversarial (b)** — while the volume is the sum over the whole
    window, which is the session's total a table's volume column means.
    """

    stamps = _column(entry, "timestamp")
    if not stamps:
        return None
    indicators = entry.get("indicators")
    indicators = indicators if isinstance(indicators, Mapping) else {}
    quotes = indicators.get("quote")
    quote = quotes[0] if isinstance(quotes, Sequence) and quotes else None
    quote = quote if isinstance(quote, Mapping) else {}
    closes = _column(quote, "close")
    volumes = _column(quote, "volume")

    price: float | None = None
    for index in range(len(stamps) - 1, -1, -1):
        close = _number(closes[index]) if index < len(closes) else None
        if close is None or close <= 0:
            continue
        price = close
        break
    if price is None:
        return None

    total = 0.0
    for index in range(len(stamps)):
        volume = _number(volumes[index]) if index < len(volumes) else None
        if volume is not None and volume > 0:
            total += volume
    return price, round(total, 4)


def parse_quote(instrument: Instrument, entry: Mapping[str, Any] | None) -> AltQuote | None:
    """One Yahoo v8 chart result → one quote, or ``None`` when unusable.

    ``None`` means "no measurement": no result, no timestamp, no positive close,
    or no previous close to compare against. The caller counts such a symbol in
    ``universe_total`` and leaves it out of ``rows`` — never a zero row.
    """

    if not isinstance(entry, Mapping):
        return None
    meta = entry.get("meta")
    meta = meta if isinstance(meta, Mapping) else {}
    last = _last_close(entry)
    if last is None:
        return None
    price, volume = last
    previous = _previous_close(meta)
    if previous is None:
        return None

    change = round((price / previous - 1.0) * 100.0, 4)
    return AltQuote(
        symbol=instrument.symbol,
        name=instrument.name
        or _text(meta.get("shortName"))
        or _text(meta.get("longName"))
        or instrument.symbol,
        price=price,
        # `or 0.0` folds -0.0, which serialises as "-0.0" and reads as a bug.
        change_pct=change or 0.0,
        volume=volume,
        currency=_text(meta.get("currency")) or instrument.currency,
    )


# ---------------------------------------------------------------------------
# The documents
# ---------------------------------------------------------------------------


def class_document(
    name: str, rows: Sequence[AltQuote], *, per: int, as_of: str
) -> dict[str, Any]:
    """The class table. Pure: the same rows print the same bytes.

    ``measured`` counts the rows this answer **publishes** — after ``per`` — so
    ``len(rows) == measured`` always, and ``universe_total`` is the size of the
    catalogue. A reader can therefore see "40 symbols exist, 38 measured" without
    summing anything.
    """

    published = list(rows[:per])
    return {
        "as_of": as_of,
        "class": name,
        # The catalogue, not the cache: 40 means forty symbols were asked about,
        # whether or not the provider answered.
        "universe_total": len(CATALOGUE[name]),
        "measured": len(published),
        "per": per,
        "method": METHOD,
        "rows": [row.document() for row in published],
    }


def quotes_document(
    rows: Sequence[Mapping[str, Any]], missing: Sequence[str], *, as_of: str
) -> dict[str, Any]:
    """The batch answer: what resolved, in request order, and what did not."""

    return {
        "as_of": as_of,
        "rows": [dict(row) for row in rows],
        "missing": list(missing),
    }


# ---------------------------------------------------------------------------
# The cache and the process's state
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CachedQuotes:
    """One measured set and the moment it was measured."""

    rows: tuple[AltQuote, ...]
    stored_at: float


class AltCache:
    """Measured rows per key, for :data:`CACHE_TTL_S` seconds.

    The clock is injectable (and wall time by default) because the validator's
    epoch is derived from it: a test advances a minute and watches both the
    cache and the tag turn over. Insertion order is the eviction order, so the
    bound is a memory budget. ``get`` and ``put`` contain no ``await``: on one
    event loop a check and a store are atomic, so a warm key is never fetched
    twice by a request that arrives while another is reading it.
    """

    __slots__ = ("_clock", "_entries", "_limit", "_ttl_s")

    def __init__(
        self, *, ttl_s: float = CACHE_TTL_S, clock: Any = time.time, limit: int = CACHE_LIMIT
    ) -> None:
        self._ttl_s = float(ttl_s)
        self._clock = clock
        self._limit = max(1, int(limit))
        self._entries: dict[str, CachedQuotes] = {}

    def get(self, key: str) -> CachedQuotes | None:
        """The fresh rows for ``key``, or ``None`` when absent or expired."""

        entry = self._entries.get(key)
        if entry is None:
            return None
        if self._clock() - entry.stored_at >= self._ttl_s:
            del self._entries[key]
            return None
        return entry

    def put(self, key: str, rows: Iterable[AltQuote]) -> CachedQuotes:
        """Memoise ``rows`` under ``key`` and answer the entry."""

        entry = CachedQuotes(rows=tuple(rows), stored_at=self._clock())
        self._entries[key] = entry
        while len(self._entries) > self._limit:
            self._entries.pop(next(iter(self._entries)))
        return entry

    def epoch(self) -> int:
        """The cache window the clock is in — the validator's time input."""

        return int(self._clock() // self._ttl_s)


class Transport(Protocol):
    """One batched read: ``symbol -> chart body``, ``None`` for a failed symbol."""

    async def payloads(self, symbols: Sequence[str]) -> Mapping[str, Any]:
        """Answer one provider body per symbol, in one call."""


class AltState:
    """Everything the two routes remember, owned by the application.

    One object rather than four attributes because the pieces share a clock: the
    class cache's epoch is the validator's time input, and a test that moves that
    clock must move it for the symbol cache too. ``transport`` is the injected
    seam — a counting double under test, :class:`HttpTransport` in production.
    """

    __slots__ = ("_equities", "classes", "clock", "symbols", "transport")

    def __init__(
        self,
        *,
        clock: Any = time.time,
        ttl_s: float = CACHE_TTL_S,
        transport: Any = None,
        limit: int = CACHE_LIMIT,
    ) -> None:
        self.clock = clock
        self.classes = AltCache(ttl_s=ttl_s, clock=clock, limit=limit)
        self.symbols = AltCache(ttl_s=ttl_s, clock=clock, limit=limit)
        self.transport = transport if transport is not None else HttpTransport()
        #: ``generation -> {symbol: row}`` for the equity quotes. One pass over the
        #: selector's universe per scan, and never more than a few generations.
        self._equities: dict[int, dict[str, Mapping[str, Any]]] = {}

    def epoch(self) -> int:
        """The validator's time input: the class cache's 60-second window."""

        return self.classes.epoch()

    def equities(self, generation: int) -> dict[str, Mapping[str, Any]] | None:
        """The symbol index built from scan ``generation``, or ``None``."""

        return self._equities.get(int(generation))

    def remember_equities(
        self, generation: int, index: dict[str, Mapping[str, Any]]
    ) -> None:
        """Keep ``index`` for ``generation``, evicting the oldest past the bound."""

        self._equities[int(generation)] = index
        while len(self._equities) > EQUITY_INDEX_LIMIT:
            self._equities.pop(next(iter(self._equities)))


def alt_state(request: Request) -> AltState:
    """The process's state, created on first use (one per app, not global)."""

    state = getattr(request.app.state, "alt", None)
    if isinstance(state, AltState):
        return state
    state = AltState()
    request.app.state.alt = state
    return state


# ---------------------------------------------------------------------------
# The provider
# ---------------------------------------------------------------------------


class HttpTransport:
    """Yahoo v8 chart reads: one client, one bounded gather, one call per batch.

    This is the only object in the module that opens a socket. It is a transport
    rather than a bare function so a test can substitute one through
    :class:`AltState` and the suite never leaves the process. A per-symbol
    failure — a 404 for a delisted ticker, a timeout, a body that is not JSON —
    is ``None`` for that symbol, not an exception: one dead ticker must not
    cost the other thirty-nine. It is not silent either: the reason lands in
    :attr:`failures`, a bounded ledger, so "the provider refused this symbol"
    is a fact a caller can read rather than a hole in the table. A failure to
    build the client at all *is* raised, because "we could not ask" is not
    "there is nothing".
    """

    __slots__ = (
        "_base_url",
        "_client_factory",
        "_concurrency",
        "_semaphore",
        "_timeout_s",
        "failures",
    )

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        timeout_s: float = FETCH_TIMEOUT_S,
        concurrency: int = CONCURRENCY,
        client_factory: Any = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = float(timeout_s)
        self._concurrency = max(1, int(concurrency))
        self._client_factory = client_factory
        self._semaphore = asyncio.Semaphore(self._concurrency)
        #: ``(symbol, reason)`` for the last :data:`FAILURE_LEDGER` failed reads.
        self.failures: list[tuple[str, str]] = []

    def url(self, symbol: str) -> str:
        """The provider request for one symbol's last session, five minutes a bar.

        ``includePrePost=false`` is deliberate: a table's price is the session's,
        and a pre-market print outside it would disagree with the open the rest
        of the product quotes.
        """

        return (
            f"{self._base_url}/v8/finance/chart/{symbol}"
            "?range=1d&interval=5m&includePrePost=false"
        )

    async def payloads(self, symbols: Sequence[str]) -> Mapping[str, Any]:
        """One body per symbol, read concurrently, ``None`` where a read failed."""

        wanted = list(symbols)
        if not wanted:
            return {}
        # The HTTP seam is imported here rather than at module scope so the route
        # module stays cheap to import, and the user agent comes from the
        # **market** layer: ``app.api`` may see the provider registry but never an
        # adapter (``tests/_import_rules.py`` rule 8).
        from app.providers import http as provider_http

        factory = self._client_factory or provider_http.build_client
        headers = {"user-agent": BROWSER_USER_AGENT, "accept": "application/json"}
        async with factory(timeout_s=self._timeout_s, headers=headers) as client:
            answers = await asyncio.gather(
                *(self._one(client, symbol) for symbol in wanted)
            )
        return dict(zip(wanted, answers, strict=True))

    def _note(self, symbol: str, reason: Any) -> None:
        """Keep one failed read in :attr:`failures`, bounded, with its reason."""

        self.failures.append((symbol, str(reason)))
        del self.failures[:-FAILURE_LEDGER]

    async def _one(self, client: Any, symbol: str) -> Any:
        """One symbol's body, or ``None`` for any failure at all."""

        async with self._semaphore:
            try:
                response = await client.get(self.url(symbol))
            except Exception as exc:  # noqa: BLE001 - a dead symbol is not a dead class
                self._note(symbol, exc)
                return None
            status = int(getattr(response, "status_code", 0))
            if status != 200:
                self._note(symbol, f"status {status}")
                return None
            try:
                return response.json()
            except Exception as exc:  # noqa: BLE001 - an unreadable body is one lost row
                self._note(symbol, exc)
                return None


def _result(payload: Any) -> Mapping[str, Any] | None:
    """The first ``chart.result`` entry of a provider body, or ``None``.

    Yahoo nests one result per request: ``{"chart": {"result": [...], "error":
    null}}``. A body with an error, no result, or a result that is not a mapping
    is a symbol the provider could not answer.
    """

    chart = payload.get("chart") if isinstance(payload, Mapping) else None
    chart = chart if isinstance(chart, Mapping) else {}
    result = chart.get("result")
    if not isinstance(result, Sequence) or isinstance(result, (str, bytes)) or not result:
        return None
    entry = result[0]
    return entry if isinstance(entry, Mapping) else None


async def _ask(
    state: AltState, instruments: Sequence[Instrument]
) -> tuple[dict[str, AltQuote], list[Instrument]]:
    """One transport call; the quotes it produced and the symbols worth asking again.

    A symbol the provider answered with a body it could not use — the endpoint
    intermittently returns a live symbol with an empty ``timestamp`` array — is
    returned as retryable. A symbol the provider *refused* (``None``: a 404, a
    timeout, a body that is not JSON) is not: that is an answer, and asking twice
    would only double the cost of a delisted ticker.
    """

    try:
        payloads = await state.transport.payloads([i.symbol for i in instruments])
    except Exception as exc:  # noqa: BLE001 - "we could not ask" is not "nothing"
        raise ApiError(
            status_code=503,
            error="alt_unavailable",
            message=f"the quote provider could not be reached: {exc}",
        ) from exc
    bodies = payloads if isinstance(payloads, Mapping) else {}
    measured: dict[str, AltQuote] = {}
    retryable: list[Instrument] = []
    for instrument in instruments:
        body = bodies.get(instrument.symbol)
        quote = parse_quote(instrument, _result(body))
        if quote is not None:
            measured[quote.symbol] = quote
        elif body is not None:
            retryable.append(instrument)
    return measured, retryable


async def read_quotes(
    state: AltState,
    instruments: Sequence[Instrument],
    *,
    attempts: int = READ_ATTEMPTS,
) -> dict[str, AltQuote]:
    """Measure ``instruments``: one batched read, plus one recovery read.

    The first read is the whole class in a single transport call. Whatever came
    back empty is asked for once more — the provider does intermittently answer a
    live symbol with no bars, and losing 10 % of a table to that would be a
    defect dressed as a fact. A denied symbol is not asked twice, so a delisted
    ticker costs one request, and the count of attempts is bounded by
    ``attempts`` so a broken provider cannot turn a table into a retry storm.

    A transport that cannot be built or asked at all is a ``503
    alt_unavailable``: the table would otherwise claim the market has no prices.
    A single symbol's failed read is simply absent.
    """

    if not instruments:
        return {}
    measured: dict[str, AltQuote] = {}
    pending = list(instruments)
    for _ in range(max(1, int(attempts))):
        if not pending:
            break
        found, pending = await _ask(state, pending)
        measured.update(found)
    return measured


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


def alt_class(raw: Any) -> str:
    """``raw`` as one of :data:`CLASSES`, or ``422 unknown_class``.

    Refused before any cache read or provider call, so a typo costs nothing and
    the sentence names the three classes that would have worked.
    """

    token = raw.strip().casefold() if isinstance(raw, str) else ""
    if token in CLASSES:
        return token
    raise ApiError(
        status_code=422,
        error="unknown_class",
        message=f"class must be one of {', '.join(CLASSES)}; got {raw!r}",
    )


def clamp_per(raw: Any) -> int:
    """``raw`` as a page size inside ``1..PER_MAX``, or ``422 invalid_per``.

    Clamped rather than refused at the edges: ``per=500`` on a 40-symbol class
    is a request for everything, and ``per=0`` is a request for a page a client
    can still render. A value that is not a number at all is refused, because
    "thirty" is a typo, not a page size.
    """

    token = raw.strip() if isinstance(raw, str) else ""
    if not token:
        raise ApiError(
            status_code=422,
            error="invalid_per",
            message=f"per must be an integer between 1 and {PER_MAX}; got {raw!r}",
        )
    try:
        value = int(token)
    except ValueError:
        raise ApiError(
            status_code=422,
            error="invalid_per",
            message=f"per must be an integer between 1 and {PER_MAX}; got {raw!r}",
        ) from None
    return max(1, min(PER_MAX, value))


def is_ticker(token: str) -> bool:
    """Whether ``token`` has the shape of a ticker this product can ask about."""

    return bool(_SYMBOL.fullmatch(token)) and any(char.isalnum() for char in token)


def symbol_class(symbol: str) -> str:
    """Which market ``symbol`` belongs to, by the provider's own suffix.

    ``-USD`` is a crypto pair, ``=F`` a futures contract, ``=X`` a FX pair, and
    everything else is an equity read from the scan. The suffix is the provider's
    vocabulary, not a guess: those are the three exact shapes Yahoo prints.
    """

    if symbol.endswith("-USD"):
        return "crypto"
    if symbol.endswith("=F"):
        return "futures"
    if symbol.endswith("=X"):
        return "forex"
    return "stock"


def quote_symbols(raw: Any) -> list[str]:
    """``raw`` as the symbols of one batch, in request order, or ``422``.

    Splits on commas, trims, upper-cases and de-duplicates — the same symbol
    named twice is one row — then refuses a list that cannot be answered at all:
    nothing left after trimming, more than :data:`MAX_SYMBOLS` names, or not one
    token that has the shape of a ticker. A bad token *beside* good ones is not a
    refusal: it is ``missing``, which tells the caller which one was wrong.
    """

    text = raw if isinstance(raw, str) else ""
    tokens = [token.strip().upper() for token in text.split(",")]
    unique = list(dict.fromkeys(token for token in tokens if token))
    example = "AAPL,MSFT,BTC-USD"
    if not unique:
        raise ApiError(
            status_code=422,
            error="invalid_symbols",
            message=(
                f"symbols must be a comma-separated list of 1-{MAX_SYMBOLS} tickers "
                f"(for example {example}); got {raw!r}"
            ),
        )
    if len(unique) > MAX_SYMBOLS:
        raise ApiError(
            status_code=422,
            error="invalid_symbols",
            message=(
                f"at most {MAX_SYMBOLS} symbols per request; got {len(unique)}"
            ),
        )
    if not any(is_ticker(token) for token in unique):
        raise ApiError(
            status_code=422,
            error="invalid_symbols",
            message=(
                f"not one of {len(unique)} tokens is a ticker this route can look up "
                f"(for example {example}); got {raw!r}"
            ),
        )
    return unique


# ---------------------------------------------------------------------------
# Revisions
# ---------------------------------------------------------------------------


def alt_revision(name: str, per: int, epoch: int, shape: str = ALT_SHAPE) -> str:
    """The class answer's identity: which table, how long, in which window."""

    return f"v1;class={name};per={int(per)};epoch={int(epoch)};shape={shape}"


def quotes_revision(
    symbols: Sequence[str], generation: int, epoch: int, shape: str = QUOTES_SHAPE
) -> str:
    """The batch answer's identity: which symbols (in order), which scan, which window.

    ``symbols`` keeps the caller's order because ``rows`` and ``missing`` follow
    it: two orders are two documents, and a tag that ignored the order would let
    one answer for the other.
    """

    return (
        f"v1;symbols={','.join(symbols)}"
        f";scan={int(generation)}"
        f";epoch={int(epoch)}"
        f";shape={shape}"
    )


# ---------------------------------------------------------------------------
# The answers
# ---------------------------------------------------------------------------


def _iso(seconds: float) -> str:
    """An epoch second as the ISO instant the documents date themselves by."""

    return datetime.fromtimestamp(float(seconds), UTC).isoformat(timespec="seconds")


def _serve(
    request: Request, document: CachedDocument, *, cache_state: str, head: bool
) -> Response:
    """The ``200``, the ``304`` or the ``HEAD``, decided from the document's identity."""

    headers = {
        "ETag": f"W/{document.etag}",
        "Cache-Control": "no-cache",
        "X-Quotes-Cache": cache_state,
    }
    if not_modified(request, document.etag):
        return Response(status_code=304, headers=headers)
    if head:
        return Response(
            status_code=200,
            headers={**headers, "Content-Length": str(len(document.body))},
        )
    return Response(
        content=document.body, media_type="application/json", headers=headers
    )


async def _alt_answer(
    request: Request, class_: str, per: str, *, head: bool
) -> Response:
    """The class table, its ``304``, or its ``HEAD`` — the ``304`` before any read."""

    name = alt_class(class_)
    size = clamp_per(per)
    state = alt_state(request)
    cached = state.classes.get(name)

    # The tag names the inputs, not the bytes: hashing the body could only happen
    # after a fetch, which is exactly what a revalidation must not pay for.
    tag = etag("alt", alt_revision(name, size, state.epoch()))
    headers = {
        "ETag": f"W/{tag}",
        "Cache-Control": "no-cache",
        "X-Alt-Cache": "hit" if cached is not None else "miss",
    }
    if not_modified(request, tag):
        return Response(status_code=304, headers=headers)

    if cached is None:
        measured = await read_quotes(state, CATALOGUE[name])
        # Catalogue order, not provider answer order: the table reads largest
        # first because the list says so, and a missing symbol keeps its hole.
        cached = state.classes.put(
            name,
            (
                measured[instrument.symbol]
                for instrument in CATALOGUE[name]
                if instrument.symbol in measured
            ),
        )

    body = encode(
        class_document(name, cached.rows, per=size, as_of=_iso(cached.stored_at))
    )
    if head:
        return Response(
            status_code=200,
            headers={**headers, "Content-Length": str(len(body))},
        )
    return Response(content=body, media_type="application/json", headers=headers)


def _compose(
    wanted: Sequence[str],
    kinds: Mapping[str, str | None],
    index: Mapping[str, Mapping[str, Any]] | None,
    quotes: Mapping[str, AltQuote],
) -> tuple[list[dict[str, Any]], list[str]]:
    """The rows that resolved and the symbols that did not, both in request order."""

    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for symbol in wanted:
        kind = kinds[symbol]
        if kind is None:
            # Not a ticker at all: nothing can be looked up or fetched for it.
            missing.append(symbol)
            continue
        if kind == "stock":
            row = (index or {}).get(symbol)
            price = _number(row.get("price")) if isinstance(row, Mapping) else None
            if price is None or price <= 0:
                # A row with no price is a symbol we cannot quote, not a quote of
                # zero: the scan knows it exists, we cannot say at what.
                missing.append(symbol)
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "name": row.get("name") or symbol,
                    "price": price,
                    "change_pct": _number(row.get("change_pct")),
                    "currency": row.get("currency") or DEFAULT_CURRENCY,
                    "class": "stock",
                }
            )
            continue
        quote = quotes.get(symbol)
        if quote is None:
            missing.append(symbol)
            continue
        rows.append(
            {
                "symbol": quote.symbol,
                "name": quote.name,
                "price": quote.price,
                "change_pct": quote.change_pct,
                "currency": quote.currency,
                "class": kind,
            }
        )
    return rows, missing


async def _measure_symbols(state: AltState, symbols: Sequence[str]) -> dict[str, AltQuote]:
    """Measure ``symbols``, reusing the symbol cache for whatever is still fresh."""

    found: dict[str, AltQuote] = {}
    stale: list[str] = []
    for symbol in symbols:
        cached = state.symbols.get(symbol)
        if cached is not None and cached.rows:
            found[symbol] = cached.rows[0]
        else:
            stale.append(symbol)
    if stale:
        measured = await read_quotes(
            state,
            [
                BY_SYMBOL.get(symbol)
                or Instrument(symbol=symbol, name="", currency=DEFAULT_CURRENCY)
                for symbol in stale
            ],
        )
        for symbol, quote in measured.items():
            state.symbols.put(symbol, (quote,))
            found[symbol] = quote
    return found


async def _quotes_answer(request: Request, raw: str, *, head: bool) -> Response:
    """The batch answer, its ``304``, or its ``HEAD`` — nothing fetched for a ``304``."""

    wanted = quote_symbols(raw)
    state = alt_state(request)
    kinds = {symbol: (symbol_class(symbol) if is_ticker(symbol) else None) for symbol in wanted}
    alts = [symbol for symbol in wanted if kinds[symbol] not in (None, "stock")]
    equities = any(kind == "stock" for kind in kinds.values())

    # A generation read, not a scan: when no scan is warm this is ``None``, and a
    # tag built from it cannot match a client's tag from a warm process.
    stamp = stamp_for(request, region=REGION, family=FAMILY) if equities else 0
    epoch = state.epoch() if alts else 0
    tag = etag("qts", quotes_revision(wanted, -1 if stamp is None else stamp, epoch))
    memo: BodyMemo = memo_for(request, "quotes", limit=QUOTES_MEMO_LIMIT)
    key = (tuple(wanted), -1 if stamp is None else stamp, epoch)
    cached = memo.get(key)

    headers = {
        "ETag": f"W/{tag}",
        "Cache-Control": "no-cache",
        "X-Quotes-Cache": "hit" if cached is not None else "miss",
    }
    if not_modified(request, tag):
        return Response(status_code=304, headers=headers)

    if cached is None:
        index: dict[str, Mapping[str, Any]] | None = None
        if equities:
            if stamp is not None:
                index = state.equities(stamp)
            if index is None:
                page = await universe_for(request, region=REGION, family=FAMILY)
                index = {str(row.get("symbol") or ""): row for row in page.rows}
                state.remember_equities(page.generation, index)
                # The scan handed out its own generation; the memo key and the
                # tag must name the page the answer was actually built from.
                key = (tuple(wanted), page.generation, epoch)
                tag = etag("qts", quotes_revision(wanted, page.generation, epoch))
                headers["ETag"] = f"W/{tag}"

        quotes = await _measure_symbols(state, alts)
        rows, missing = _compose(wanted, kinds, index, quotes)
        cached = memo.put(
            key,
            CachedDocument(
                body=encode(quotes_document(rows, missing, as_of=moment())),
                etag=tag,
                generation=int(key[1]),
            ),
        )
    return _serve(request, cached, cache_state=headers["X-Quotes-Cache"], head=head)


# ---------------------------------------------------------------------------
# The routes
# ---------------------------------------------------------------------------


@router.get("/market/alt")
async def market_alt(
    request: Request,
    class_: str = Query("crypto", alias="class"),
    per: str = Query(str(PER_DEFAULT)),
) -> Response:
    """Crypto, futures or FX: one row per catalogued symbol (GET).

    ``price`` is the last non-zero close and ``change_pct`` is measured against
    the previous **session's** close, so a closed market answers with the last
    session's move. A symbol the provider could not answer is counted in
    ``universe_total`` and absent from ``rows`` — never a zero row.
    """

    return await _alt_answer(request, class_, per, head=False)


@router.head("/market/alt", include_in_schema=False)
async def market_alt_head(
    request: Request,
    class_: str = Query("crypto", alias="class"),
    per: str = Query(str(PER_DEFAULT)),
) -> Response:
    """The same headers as GET, no body: a monitor's question, not the table."""

    return await _alt_answer(request, class_, per, head=True)


@router.get("/market/quotes")
async def market_quotes(request: Request, symbols: str = Query("")) -> Response:
    """Up to fifty symbols, one call: equities from the scan, the rest from Yahoo (GET).

    ``rows`` and ``missing`` both follow the order the symbols were asked for; a
    symbol that resolves to nothing is listed in ``missing`` rather than invented.
    """

    return await _quotes_answer(request, symbols, head=False)


@router.head("/market/quotes", include_in_schema=False)
async def market_quotes_head(request: Request, symbols: str = Query("")) -> Response:
    """The same headers as GET, no body."""

    return await _quotes_answer(request, symbols, head=True)


__all__ = [
    "ALT_SHAPE",
    "BASE_URL",
    "CACHE_TTL_S",
    "CATALOGUE",
    "CLASSES",
    "CONCURRENCY",
    "CRYPTO",
    "AltCache",
    "AltQuote",
    "AltState",
    "CachedQuotes",
    "FOREX",
    "FUTURES",
    "HttpTransport",
    "Instrument",
    "MAX_SYMBOLS",
    "METHOD",
    "PER_DEFAULT",
    "PER_MAX",
    "QUOTES_SHAPE",
    "Transport",
    "alt_class",
    "alt_revision",
    "alt_state",
    "clamp_per",
    "class_document",
    "is_ticker",
    "market_alt",
    "market_alt_head",
    "market_quotes",
    "market_quotes_head",
    "parse_quote",
    "quote_symbols",
    "quotes_document",
    "quotes_revision",
    "read_quotes",
    "router",
    "symbol_class",
]
