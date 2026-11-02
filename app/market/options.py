"""Option chains: Cboe's delayed feed first, Yahoo + Black-Scholes second.

The ticker page's ladder needs three things a quote endpoint cannot give it: the
listed expiries, the strike ladder around the money, and a greek per contract.
This module is the one owner of that document. It reads Cboe's
``delayed_quotes`` feed, falls back to Yahoo's ``/v7/finance/options`` through
the depth client's cookie/crumb handshake, and answers from a five-minute disk
cache when a provider refuses.

**The delay is the product.** ``https://cdn.cboe.com`` publishes the chain
fifteen minutes behind the tape; the delay is stated in every 200 as
``delayed_minutes: 15`` rather than left for a caller to discover from a stale
price. A Yahoo fallback is a delayed feed too, so the field is constant: it
describes the product, not the wire that happened to answer.

**The OCC code is the only source of identity.** Cboe's ``option`` field is the
21-character symbol — ``AAPL261104C00275000`` — whose fixed fifteen-character
tail carries the expiry (``YYMMDD``), the right (``C``/``P``) and the strike in
thousandths of a dollar. The root is whatever precedes the tail, so ``BRKB`` and
an adjusted ``AAPL1`` both work without a table. A code that does not parse is
skipped: one malformed row must not take a chain down, and an invented strike
must never reach a client.

**A zero is not a number here.** Cboe prints ``iv: 0.0`` (and often
``last_trade_price: 0``) for a contract nobody has quoted. Both are published as
``null``; a zero implied volatility would be a claim about the future, and a
zero last price a claim about a trade that did not happen. A zero *bid* is a
real quote ("nobody bids") and is kept.

**Two transports, one client each.** Cboe is read through the shared HTTP seam
(:mod:`app.providers.http`) with a browser User-Agent, a ten-second timeout and
one in-flight request per symbol: a burst of five callers for ``AAPL`` spends one
Cboe request, not five. A ``429`` is retried after the ``Retry-After`` it names
(clamped to a minute, at most twice); if it survives the retries the cached copy
is served with ``source="cache"`` and the fallback is not spent. The redirect
``cdn.cboe.com`` answers with is followed **once**, and only to ``cboe.com``
(``follow_redirects`` stays off in :func:`app.providers.http.build_client`, so
the hop is explicit and allowlisted). Yahoo is read through
:class:`app.market.yahoo_depth.YahooDepthSource`, which already owns the
``fc.yahoo.com`` cookie, the crumb exchange and the one 401/403 renewal; Yahoo
publishes no greeks, so Black-Scholes computes them from the chain's own strike
and implied volatility.

**The cache is the fallback of last resort.** A chain lives for five minutes in
``<options dir>/<SYMBOL>.json`` — ``MEE_OPTIONS_CACHE_DIR``, else beside the
deployed depth store (``/home/ubuntu/ztockz-data/options`` on this box), else the
project's portable data root. A fresh copy is served without a request; an
expired copy is served when Cboe is throttled or both providers refuse. Either
way the document says ``source="cache"``, because the bytes came off the disk.
A chain whose every listed expiry has passed is not served at all: an old ladder
is honest only while it still describes something tradeable.

**Dates are the chain's own.** ``as_of`` is the timestamp the source published,
and ``days_to_maturity`` is calendar days from that date to the expiry, so a
Friday chain replayed on Monday does not gain two days of decay. The one place
the wall clock is consulted is cache freshness, and the one place the request's
own bounds are: ``expiries`` and ``strikes`` select from the chain, they never
refetch it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from app.market.depth import canonical_symbol
from app.market.yahoo_depth import YahooDepthSource
from app.providers import http as provider_http
from app.providers.adapters.yahoo import (
    BROWSER_USER_AGENT,
    HOST as YAHOO_HOST,
    OPTIONS_PATH,
)

logger = logging.getLogger("mee.market.options")

#: Cboe's delayed feed is fifteen minutes behind the tape, by contract.
DELAYED_MINUTES = 15

#: The chain's refresh window. An option ladder is a picture of one session, not
#: a historical document: five minutes matches the quote routes' own window and
#: is short enough that a stale strike cannot be mistaken for a live one.
CHAIN_TTL_S = 5 * 60.0

#: The documented Cboe endpoint. ``cdn.cboe.com`` answers ``307`` to
#: ``cdn-api.cboe.com``; the hop is followed explicitly, once (see
#: :func:`_redirect_target`).
CBOE_CHAIN_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"

#: The only host suffix a redirect may point at. A provider that redirects must
#: not be able to walk this read to an arbitrary host (02 §3.11 SSRF).
CBOE_REDIRECT_SUFFIX = ".cboe.com"

#: Cboe throttles bursty clients; ten seconds is the documented read ceiling.
CBOE_TIMEOUT_S = 10.0

#: Two retries at most: a throttled provider is not a broken one, but a ladder
#: is not worth a queue of retries either.
CBOE_MAX_RETRIES = 2

#: A ``Retry-After`` is honoured, but a provider asking for an hour must not pin
#: a request for an hour: the cached copy is a better answer than that wait.
RETRY_AFTER_MAX_S = 60.0

#: The rate the Black-Scholes fallback discounts at. A single documented
#: constant, not a curve: a modelled greek is a shape, and the caller can see
#: the IV it came from.
RISK_FREE_RATE = 0.04

#: How many expiries a request may ask for, and how many strikes on **each**
#: side of the money. Both bound the response, not the fetch: a live Cboe chain
#: carries every listed expiry and this module selects from it.
MAX_EXPIRIES = 8
MAX_STRIKES = 40

CALL = "call"
PUT = "put"

#: The three spellings of ``?right=`` — the two rights, and both.
RIGHTS: tuple[str, ...] = (CALL, PUT, "both")

#: Where the cache lives. The explicit override exists for tests and for a
#: deployment that keeps chains on a different volume; otherwise the directory
#: sits beside the depth store the rest of this box's data uses.
CACHE_DIR_ENV = "MEE_OPTIONS_CACHE_DIR"

#: A symbol that can safely become a filename: uppercase provider spelling
#: (``BRK-B``, ``BRK.B``, ``^GSPC``), never a path separator.
_SYMBOL_RE = re.compile(r"^[A-Z0-9^][A-Z0-9.^=-]{0,23}$")

#: One documented hop, absolute, https, and on Cboe's own domain.
_REDIRECT_RE = re.compile(r"^https://([A-Za-z0-9.-]+)(/[^\s]*)?$")

_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})

#: A stale crumb is auth plumbing, not a verdict about the symbol.
_AUTH_STATUS = frozenset({401, 403})

#: Cboe answers an underlying it does not list with an S3 ``AccessDenied`` 403,
#: not a 404 — both mean "no such chain", and neither may be read as "the
#: provider is down".
_CBOE_UNKNOWN_STATUS = frozenset({403, 404})

#: A Yahoo 403 is the crumb dance (``_AUTH_STATUS``); only a 404 is a verdict
#: about the symbol, and an auth failure must never become a 404.
_YAHOO_UNKNOWN_STATUS = frozenset({404})

#: The fixed tail of an OCC symbol: YYMMDD (6) + right (1) + strike (8).
_OCC_TAIL = 15

#: How many expiries the Yahoo fallback asks for when it has to walk the list.
#: One request per expiry, so the cap is a request budget, not a product limit.
YAHOO_EXPIRY_LIMIT = MAX_EXPIRIES


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Scalar readers
# ---------------------------------------------------------------------------


def _num(value: Any) -> float | None:
    """A finite float, or ``None`` for anything that is not a number."""

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Mapping):
        value = value.get("raw")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int(value: Any) -> int | None:
    """A count, or ``None``. Volumes and open interest are printed as floats."""

    number = _num(value)
    return None if number is None else int(number)


def _positive(value: Any) -> float | None:
    """A price that exists: a printed zero is "no trade", not a free option."""

    number = _num(value)
    return number if number is not None and number > 0 else None


def _volatility(value: Any) -> float | None:
    """An implied volatility as a fraction, or ``None`` when the source prints 0."""

    number = _num(value)
    return number if number is not None and number > 0 else None


def _round(value: float | None, digits: int = 6) -> float | None:
    """A published float, without the ``-0.0`` a rounded negative leaves behind."""

    if value is None:
        return None
    number = round(float(value), digits)
    return 0.0 if number == 0 else number


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _safe_symbol(raw: Any) -> str:
    """The provider spelling when it is filename-safe, else ``""``."""

    key = canonical_symbol(raw)
    return key if _SYMBOL_RE.match(key) else ""


# ---------------------------------------------------------------------------
# The OCC code
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OccQuote:
    """The three facts an OCC symbol carries, and the root it belongs to."""

    root: str
    expiry: date
    right: str
    strike: float


def parse_occ(code: Any) -> OccQuote:
    """``AAPL261104C00275000`` → root, expiry, right, strike.

    The tail is fixed width and read from the end, so the root needs no lookup
    table: it is every character before the fifteen-character suffix. A strike is
    thousandths of a dollar (``00275000`` → 275.000), and a date or a right that
    cannot exist raises :class:`ValueError` instead of producing a contract.
    """

    text = code.strip().upper() if isinstance(code, str) else ""
    if len(text) <= _OCC_TAIL:
        raise ValueError(f"not an OCC symbol: {code!r}")
    tail = text[-_OCC_TAIL:]
    root = text[:-_OCC_TAIL]
    date_text, right_letter, strike_text = tail[:6], tail[6], tail[7:]
    if not root.strip():
        raise ValueError(f"OCC symbol has no root: {code!r}")
    if right_letter not in {"C", "P"}:
        raise ValueError(f"OCC symbol has no right: {code!r}")
    if not date_text.isdigit() or not strike_text.isdigit():
        raise ValueError(f"OCC symbol has a non-numeric tail: {code!r}")
    year = 2000 + int(date_text[:2])
    month = int(date_text[2:4])
    day = int(date_text[4:6])
    strike = int(strike_text) / 1000.0
    if strike <= 0:
        raise ValueError(f"OCC symbol has a zero strike: {code!r}")
    try:
        expiry = date(year, month, day)
    except ValueError as exc:
        raise ValueError(f"OCC symbol has no such expiry: {code!r}") from exc
    return OccQuote(
        root=root,
        expiry=expiry,
        right=CALL if right_letter == "C" else PUT,
        strike=strike,
    )


# ---------------------------------------------------------------------------
# The chain's shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Contract:
    """One listed contract: a strike, a right, and whatever the source knows."""

    strike: float
    right: str
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    iv: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None

    def payload(self) -> dict[str, Any]:
        """The cached form: every published number, no derived field."""

        return {
            "strike": self.strike,
            "right": self.right,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "volume": self.volume,
            "open_interest": self.open_interest,
            "iv": self.iv,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "vega": self.vega,
        }

    def document(self, spot: float | None) -> dict[str, Any]:
        """The response form: the cached numbers plus moneyness."""

        out = self.payload()
        out["in_the_money"] = _in_the_money(self, spot)
        return out


@dataclass(frozen=True, slots=True)
class ExpiryChain:
    """Every listed contract for one expiration date."""

    expiry: date
    contracts: tuple[Contract, ...]


@dataclass(frozen=True, slots=True)
class Chain:
    """One underlying's whole listed ladder, as one source published it."""

    symbol: str
    as_of: datetime
    source: str
    underlying_price: float | None
    expiries: tuple[ExpiryChain, ...]


@dataclass(frozen=True, slots=True)
class FetchOutcome:
    """What one source had to say: a chain, "no such symbol", or "not now".

    The three are deliberately distinct. A refusal (a 500, an HTML challenge, a
    timeout) is not evidence about the symbol, and turning it into a 404 would
    make a provider outage look like a delisted ticker.
    """

    chain: Chain | None = None
    unknown: bool = False
    rate_limited: bool = False


class ChainError(Exception):
    """The chain cannot be published, and the route reports why."""


class UnknownSymbol(ChainError):
    """Neither source lists a chain for this symbol — or it is not a symbol."""


class ChainUnavailable(ChainError):
    """The providers refused and nothing usable is cached."""


def _contract_order(contract: Contract) -> tuple[float, int]:
    return (contract.strike, 0 if contract.right == CALL else 1)


def _in_the_money(contract: Contract, spot: float | None) -> bool | None:
    """A call is in the money below the underlying, a put above it.

    At the money is **not** in the money: the boundary has to fall somewhere,
    and "strike strictly beyond the spot" is the only reading that keeps a call
    and a put at the same strike from both claiming it.
    """

    if spot is None:
        return None
    return contract.strike < spot if contract.right == CALL else contract.strike > spot


# ---------------------------------------------------------------------------
# Cboe
# ---------------------------------------------------------------------------


def _cboe_stamp(raw: Any, now: datetime | None) -> datetime:
    """``2026-11-02 11:01:17`` read as UTC; the process clock when absent."""

    text = _text(raw)
    if text:
        try:
            moment = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            moment = None
        if moment is not None:
            return moment.replace(tzinfo=UTC)
    return now or _utcnow()


def _cboe_contract(occ: OccQuote, row: Mapping[str, Any]) -> Contract:
    return Contract(
        strike=occ.strike,
        right=occ.right,
        bid=_num(row.get("bid")),
        ask=_num(row.get("ask")),
        last=_positive(row.get("last_trade_price")),
        volume=_int(row.get("volume")),
        open_interest=_int(row.get("open_interest")),
        iv=_volatility(row.get("iv")),
        delta=_num(row.get("delta")),
        gamma=_num(row.get("gamma")),
        theta=_num(row.get("theta")),
        vega=_num(row.get("vega")),
    )


def parse_cboe(symbol: Any, payload: Any, *, now: datetime | None = None) -> Chain | None:
    """Cboe's ``data.options[]`` as one chain, or ``None`` when it carries none.

    The spot is the payload's own ``current_price`` (``close`` when the feed is
    between sessions); the clock is its ``timestamp``. A row whose OCC code does
    not parse is skipped, and a payload with no parsable row is not a chain.
    """

    key = _safe_symbol(symbol)
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not key or not isinstance(data, Mapping):
        return None
    rows = data.get("options")
    if not isinstance(rows, list) or not rows:
        return None

    groups: dict[date, list[Contract]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        try:
            occ = parse_occ(row.get("option"))
        except ValueError:
            continue
        groups.setdefault(occ.expiry, []).append(_cboe_contract(occ, row))
    if not groups:
        return None

    timestamp = payload.get("timestamp") if isinstance(payload, Mapping) else None
    return Chain(
        symbol=key,
        as_of=_cboe_stamp(timestamp, now),
        source="cboe",
        underlying_price=_positive(data.get("current_price"))
        or _positive(data.get("close")),
        expiries=tuple(
            ExpiryChain(
                expiry=expiry,
                contracts=tuple(sorted(groups[expiry], key=_contract_order)),
            )
            for expiry in sorted(groups)
        ),
    )


def _cboe_url(symbol: str) -> str:
    """Cboe addresses share classes with a dot: ``BRK-B`` → ``BRK.B``."""

    return CBOE_CHAIN_URL.format(symbol=symbol.replace("-", "."))


def _redirect_target(location: Any) -> str | None:
    """An absolute https location on ``cboe.com``, or ``None``.

    Relative locations are refused rather than resolved: the one hop this read
    allows is the documented ``cdn.cboe.com`` → ``cdn-api.cboe.com`` redirect,
    and accepting a relative one would mean re-implementing URL resolution for a
    hop that never uses it (``urllib.parse`` is banned by the import rules for
    being a legacy transport).
    """

    text = _text(location)
    match = _REDIRECT_RE.match(text) if text else None
    if match is None:
        return None
    host = match.group(1).lower()
    if not (host == "cboe.com" or host.endswith(CBOE_REDIRECT_SUFFIX)):
        return None
    return text


def _retry_after(response: Any) -> float:
    """The wait a ``429`` asks for, in seconds, clamped to :data:`RETRY_AFTER_MAX_S`."""

    headers = getattr(response, "headers", None)
    raw = headers.get("retry-after") if headers is not None else None
    delay = 0.0
    text = _text(raw)
    if text:
        try:
            delay = float(text)
        except ValueError:
            try:
                moment = parsedate_to_datetime(text)
            except (TypeError, ValueError):
                moment = None
            if moment is not None:
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=UTC)
                delay = (moment - _utcnow()).total_seconds()
    return min(max(delay, 0.0), RETRY_AFTER_MAX_S)


def _json_of(response: Any, *, what: str) -> Any:
    """A response body as JSON, or ``None`` — a challenge page is not a body."""

    try:
        return response.json()
    except Exception as exc:  # noqa: BLE001 - an unparsable body is a refusal
        logger.info("%s answered a body that is not JSON: %s", what, exc)
        return None


class CboeChainSource:
    """One ``delayed_quotes`` GET per symbol, with the delay stated downstream.

    ``client_factory`` and ``sleep`` exist for tests: the first lets a suite
    answer with ``httpx2.MockTransport`` instead of a socket, the second records
    a backoff without spending it. Production passes neither.
    """

    name = "cboe"

    def __init__(
        self,
        *,
        timeout_s: float = CBOE_TIMEOUT_S,
        client_factory: Callable[..., Any] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], datetime] | None = None,
        max_retries: int = CBOE_MAX_RETRIES,
        user_agent: str = BROWSER_USER_AGENT,
    ) -> None:
        self.timeout_s = float(timeout_s)
        self.client_factory = client_factory
        self._sleep = sleep or asyncio.sleep
        self._clock = clock or _utcnow
        self.max_retries = max(0, int(max_retries))
        self.user_agent = user_agent

    def headers(self) -> dict[str, str]:
        return {
            "user-agent": self.user_agent,
            "accept": "application/json,text/plain,*/*",
            "accept-language": "en-US,en;q=0.9",
        }

    def _client(self) -> Any:
        if self.client_factory is not None:
            return self.client_factory(timeout_s=self.timeout_s, headers=self.headers())
        # Module attribute, never a from-import: the suite patches this exact
        # attribute to install a MockTransport (app/providers/http.py).
        return provider_http.build_client(
            timeout_s=self.timeout_s, headers=self.headers()
        )

    async def fetch(self, symbol: Any) -> FetchOutcome:
        """The chain, or why there is none. Never raises for a provider answer."""

        key = _safe_symbol(symbol)
        if not key:
            return FetchOutcome(unknown=True)
        url = _cboe_url(key)
        retries_left = self.max_retries
        redirects_left = 1

        try:
            async with self._client() as client:
                while True:
                    response = await client.get(url)
                    status = int(getattr(response, "status_code", 0) or 0)
                    if status in _REDIRECT_STATUS:
                        headers = getattr(response, "headers", {})
                        target = _redirect_target(headers.get("location"))
                        if redirects_left <= 0 or target is None:
                            logger.info(
                                "cboe chain for %s redirected off the allowlist", key
                            )
                            return FetchOutcome()
                        url, redirects_left = target, redirects_left - 1
                        continue
                    if status == 429:
                        if retries_left <= 0:
                            logger.info("cboe chain for %s is rate limited", key)
                            return FetchOutcome(rate_limited=True)
                        retries_left -= 1
                        await self._sleep(_retry_after(response))
                        continue
                    break
        except Exception as exc:  # noqa: BLE001 - a transport failure is a refusal
            logger.info("cboe chain for %s failed: %s", key, exc)
            return FetchOutcome()

        if status == 200:
            chain = parse_cboe(
                key, _json_of(response, what=f"cboe chain for {key}"), now=self._clock()
            )
            return FetchOutcome(chain=chain, unknown=chain is None)
        if status in _CBOE_UNKNOWN_STATUS:
            return FetchOutcome(unknown=True)
        logger.info("cboe chain for %s answered %s", key, status)
        return FetchOutcome()


# ---------------------------------------------------------------------------
# Black-Scholes (Yahoo publishes no greeks)
# ---------------------------------------------------------------------------


def _norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def black_scholes_greeks(
    spot: float,
    strike: float,
    years: float,
    vol: float,
    *,
    rate: float = RISK_FREE_RATE,
    right: str = CALL,
) -> dict[str, float]:
    """Delta, gamma, vega and theta for one European contract.

    ``years`` is time to expiry in years and ``vol`` a fraction. The published
    conventions are the ones a reader can act on: **vega per volatility point**
    (a 1 % move in IV) and **theta per calendar day**. Inputs that cannot be
    priced — a zero strike, a matured expiry, no implied volatility — return
    ``{}`` so the caller publishes ``null`` rather than a fabricated shape.
    """

    if right not in (CALL, PUT):
        raise ValueError(f"right must be {CALL!r} or {PUT!r}; got {right!r}")
    if spot <= 0 or strike <= 0 or years <= 0 or vol <= 0:
        return {}
    sqrt_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * years) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    density = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)

    delta = _norm_cdf(d1) if right == CALL else _norm_cdf(d1) - 1.0
    gamma = density / (spot * vol * sqrt_t)
    vega = spot * density * sqrt_t / 100.0
    decay = -spot * density * vol / (2.0 * sqrt_t)
    carry = rate * strike * math.exp(-rate * years)
    theta = (
        (decay - carry * _norm_cdf(d2)) / 365.0
        if right == CALL
        else (decay + carry * _norm_cdf(-d2)) / 365.0
    )
    values = {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta}
    if not all(math.isfinite(value) for value in values.values()):
        return {}
    return values


# ---------------------------------------------------------------------------
# Yahoo
# ---------------------------------------------------------------------------


def _yahoo_result(payload: Any) -> Mapping[str, Any] | None:
    """``optionChain.result[0]`` when it is an object, else ``None``."""

    if not isinstance(payload, Mapping):
        return None
    chain = payload.get("optionChain")
    if not isinstance(chain, Mapping):
        return None
    result = chain.get("result")
    if isinstance(result, Mapping):
        return result
    if isinstance(result, list) and result and isinstance(result[0], Mapping):
        return result[0]
    return None


def _yahoo_stamp(result: Mapping[str, Any], now: datetime | None) -> datetime:
    """``quote.regularMarketTime`` (epoch seconds) as the chain's own clock."""

    quote = result.get("quote")
    raw = quote.get("regularMarketTime") if isinstance(quote, Mapping) else None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        try:
            return datetime.fromtimestamp(int(raw), UTC)
        except (OverflowError, OSError, ValueError):
            pass
    return now or _utcnow()


def _yahoo_block_date(block: Mapping[str, Any]) -> date | None:
    """The expiry a Yahoo block is dated for, or ``None`` when it carries none."""

    raw = block.get("expirationDate")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(int(raw), UTC).date()
    except (OverflowError, OSError, ValueError):
        return None


def _yahoo_contract(
    row: Mapping[str, Any],
    *,
    right: str,
    expiry: date,
    as_of: datetime,
    spot: float | None,
    rate: float,
) -> Contract | None:
    """One Yahoo contract, with greeks computed from its own implied vol.

    Yahoo ships ``bid``, ``ask``, ``lastPrice``, ``volume``, ``openInterest`` and
    ``impliedVolatility``, and no greek of any kind: the four numbers below are
    Black-Scholes on the chain's own strike and IV, at :data:`RISK_FREE_RATE`
    over the calendar days to expiry. A contract with no spot, no IV or a
    matured expiry publishes ``null`` greeks rather than a shape that cannot be
    priced.
    """

    strike = _num(row.get("strike"))
    if strike is None or strike <= 0:
        return None
    iv = _volatility(row.get("impliedVolatility"))
    years = max(0, (expiry - as_of.date()).days) / 365.0
    greeks = (
        black_scholes_greeks(spot, strike, years, iv, rate=rate, right=right)
        if spot is not None and iv is not None
        else {}
    )
    return Contract(
        strike=strike,
        right=right,
        bid=_num(row.get("bid")),
        ask=_num(row.get("ask")),
        last=_positive(row.get("lastPrice")),
        volume=_int(row.get("volume")),
        open_interest=_int(row.get("openInterest")),
        iv=iv,
        delta=greeks.get("delta"),
        gamma=greeks.get("gamma"),
        theta=greeks.get("theta"),
        vega=greeks.get("vega"),
    )


def parse_yahoo(
    symbol: Any,
    payload: Any,
    *,
    now: datetime | None = None,
    rate: float = RISK_FREE_RATE,
) -> Chain | None:
    """One or more Yahoo ``options`` blocks as a chain, or ``None``.

    A single payload may carry several dated blocks (the source merges the base
    answer and the dated ones before calling this), and Yahoo repeats a
    contract's right in its ``contractSymbol``. Contracts whose strike is
    missing or non-positive are skipped, exactly as Cboe's unparsable rows are.
    """

    key = _safe_symbol(symbol)
    result = _yahoo_result(payload)
    if not key or result is None:
        return None
    quote = result.get("quote")
    spot = _positive(quote.get("regularMarketPrice")) if isinstance(quote, Mapping) else None
    as_of = _yahoo_stamp(result, now)

    blocks = result.get("options")
    if not isinstance(blocks, list):
        return None

    # One block per expiry: a later block for the same date is the fresher one,
    # and merging by date keeps a contract from being listed twice.
    by_expiry: dict[date, Mapping[str, Any]] = {}
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        expiry = _yahoo_block_date(block) or as_of.date()
        by_expiry[expiry] = block

    groups: dict[date, list[Contract]] = {}
    for expiry, block in sorted(by_expiry.items()):
        for name, right in (("calls", CALL), ("puts", PUT)):
            rows = block.get(name)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                contract = _yahoo_contract(
                    row,
                    right=right,
                    expiry=expiry,
                    as_of=as_of,
                    spot=spot,
                    rate=rate,
                )
                if contract is not None:
                    groups.setdefault(expiry, []).append(contract)
    if not groups:
        return None

    return Chain(
        symbol=key,
        as_of=as_of,
        source="yahoo",
        underlying_price=spot,
        expiries=tuple(
            ExpiryChain(
                expiry=expiry,
                contracts=tuple(sorted(groups[expiry], key=_contract_order)),
            )
            for expiry in sorted(groups)
        ),
    )


class YahooChainSource(YahooDepthSource):
    """The dated chains for one symbol, on the depth client's transport.

    Everything transport-shaped is inherited: the browser UA, the cookie jar,
    the crumb exchange and the one 401/403 renewal, so this fallback cannot
    drift onto a second Yahoo path. This class adds the two URLs, the expiry
    walk (Yahoo answers only the nearest chain without ``?date=``) and the
    Black-Scholes projection.
    """

    name = "yahoo_options"

    def __init__(
        self,
        *,
        timeout_s: float = CBOE_TIMEOUT_S,
        client_factory: Callable[..., Any] | None = None,
        clock: Callable[[], datetime] | None = None,
        rate: float = RISK_FREE_RATE,
        expiry_limit: int = YAHOO_EXPIRY_LIMIT,
    ) -> None:
        super().__init__(
            timeout_s=timeout_s,
            include_timeseries=False,
            include_options=False,
            clock=clock,
        )
        self.client_factory = client_factory
        self.rate = float(rate)
        self.expiry_limit = max(1, int(expiry_limit))

    def _client(self) -> Any:
        if self.client_factory is not None:
            return self.client_factory(timeout_s=self.timeout_s, headers=self.headers())
        return provider_http.build_client(
            timeout_s=self.timeout_s, headers=self.headers()
        )

    @staticmethod
    def chain_url(symbol: str, *, crumb: str = "", date_epoch: int | None = None) -> str:
        """The chain URL, with the optional expiry date and the crumb."""

        url = f"{YAHOO_HOST}{OPTIONS_PATH.format(symbol=symbol)}"
        params: list[str] = []
        if date_epoch is not None:
            params.append(f"date={int(date_epoch)}")
        if crumb:
            params.append(f"crumb={crumb}")
        return f"{url}?{'&'.join(params)}" if params else url

    async def fetch(self, symbol: Any) -> FetchOutcome:
        """The chain across the nearest expiries, or why there is none."""

        key = _safe_symbol(symbol)
        if not key:
            return FetchOutcome(unknown=True)

        try:
            async with self._client() as client:
                self._crumb = await self._login(client)
                status, payload = await self._get_json(
                    client, self.chain_url(key, crumb=self._crumb)
                )
                if status in _AUTH_STATUS:
                    crumb = await self._renew_crumb(client, self._crumb)
                    if crumb:
                        status, payload = await self._get_json(
                            client, self.chain_url(key, crumb=crumb)
                        )
                if status in _YAHOO_UNKNOWN_STATUS:
                    return FetchOutcome(unknown=True)
                if status != 200:
                    logger.info("yahoo chain for %s answered %s", key, status)
                    return FetchOutcome()

                result = _yahoo_result(payload)
                if result is None:
                    # Yahoo answered with no chain at all: a real 404, not a hole.
                    return FetchOutcome(unknown=True)

                dates = _yahoo_dates(result)
                blocks: list[Mapping[str, Any]] = [
                    block
                    for block in (result.get("options") or [])
                    if isinstance(block, Mapping)
                ]
                for epoch, expiry in dates[: self.expiry_limit]:
                    if any(_yahoo_block_date(block) == expiry for block in blocks):
                        continue
                    status, dated = await self._get_json(
                        client, self.chain_url(key, crumb=self._crumb, date_epoch=epoch)
                    )
                    if status in _AUTH_STATUS:
                        crumb = await self._renew_crumb(client, self._crumb)
                        if crumb:
                            status, dated = await self._get_json(
                                client,
                                self.chain_url(key, crumb=crumb, date_epoch=epoch),
                            )
                    if status != 200:
                        # A partial ladder is honest; a fabricated one is not.
                        logger.info(
                            "yahoo chain for %s stopped at %s: %s", key, expiry, status
                        )
                        break
                    fetched = _yahoo_result(dated)
                    if fetched is not None:
                        for extra in fetched.get("options") or []:
                            if isinstance(extra, Mapping):
                                blocks.append(extra)

                merged = dict(result)
                merged["options"] = blocks
                chain = parse_yahoo(
                    key,
                    {"optionChain": {"result": [merged]}},
                    now=self._clock(),
                    rate=self.rate,
                )
        except Exception as exc:  # noqa: BLE001 - a transport failure is a refusal
            logger.info("yahoo chain for %s failed: %s", key, exc)
            return FetchOutcome()

        return FetchOutcome(chain=chain, unknown=chain is None)


def _yahoo_dates(result: Mapping[str, Any]) -> list[tuple[int, date]]:
    """``(epoch, date)`` for each listed expiry, nearest first, duplicates dropped."""

    raw = result.get("expirationDates")
    if not isinstance(raw, list):
        return []
    seen: dict[date, int] = {}
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        try:
            moment = datetime.fromtimestamp(int(value), UTC).date()
        except (OverflowError, OSError, ValueError):
            continue
        seen.setdefault(moment, int(value))
    return [(seen[moment], moment) for moment in sorted(seen)]


# ---------------------------------------------------------------------------
# Selection: the document the route returns
# ---------------------------------------------------------------------------


def _money_strike(group: ExpiryChain, spot: float | None) -> float | None:
    """The strike a ladder centres on: the underlying, or the chain's middle.

    A chain without a spot is still a chain — a cached copy can outlive the
    quote that produced it — and the median strike is the only centre that does
    not invent a price.
    """

    if spot is not None and spot > 0:
        return spot
    strikes = sorted({contract.strike for contract in group.contracts})
    if not strikes:
        return None
    middle = len(strikes) // 2
    if len(strikes) % 2:
        return strikes[middle]
    return (strikes[middle - 1] + strikes[middle]) / 2.0


def _selected_contracts(
    group: ExpiryChain,
    *,
    spot: float | None,
    strikes: int,
    rights: frozenset[str],
) -> list[Contract]:
    """The nearest ``strikes`` strikes on each side of the money, filtered by right.

    Symmetric by construction: ``strikes=2`` is two below and two above (plus a
    strike exactly at the money), never an odd window tilted by a tie. A ladder
    is read as a shape around the underlying, so an asymmetric centre would
    misstate where the money is.
    """

    ladder = sorted({c.strike for c in group.contracts if c.right in rights})
    if not ladder:
        return []
    money = _money_strike(group, spot)
    if money is None:  # pragma: no cover - the ladder is non-empty above
        return []
    below = [strike for strike in ladder if strike < money]
    above = [strike for strike in ladder if strike > money]
    chosen = set(below[-strikes:]) | set(above[:strikes])
    if money in ladder:
        chosen.add(money)
    return sorted(
        (
            contract
            for contract in group.contracts
            if contract.right in rights and contract.strike in chosen
        ),
        key=_contract_order,
    )


def select_chain(
    chain: Chain,
    *,
    expiries: int = 4,
    strikes: int = 12,
    right: str = "both",
    source: str | None = None,
) -> dict[str, Any]:
    """The response document: nearest expiries, nearest strikes, both rights.

    ``right`` is one of :data:`RIGHTS` and cases nothing else. ``expiries`` and
    ``strikes`` are bounds, not promises: a chain with four listed dates answers
    a request for eight with four. Every 200 carries ``delayed_minutes: 15``,
    ``source`` (``cboe``/``yahoo``/``cache``), the underlying and, per expiry,
    its calendar days to maturity.

    The calendar is the chain's own: ``as_of`` dates the document and the
    days-to-maturity arithmetic, so replaying a Friday chain on Monday does not
    silently add two days of decay to it.
    """

    if right not in RIGHTS:
        raise ValueError(f"right must be one of {', '.join(RIGHTS)}; got {right!r}")
    rights = frozenset({CALL, PUT} if right == "both" else {right})
    limit = max(1, int(expiries))
    width = max(1, int(strikes))
    today = chain.as_of.date()

    groups = sorted(
        (group for group in chain.expiries if group.expiry >= today),
        key=lambda group: group.expiry,
    )

    rendered: list[dict[str, Any]] = []
    for group in groups[:limit]:
        spot = _money_strike(group, chain.underlying_price)
        rendered.append(
            {
                "expiry": group.expiry.isoformat(),
                "days_to_maturity": max(0, (group.expiry - today).days),
                "contracts": [
                    contract.document(spot)
                    for contract in _selected_contracts(
                        group, spot=chain.underlying_price, strikes=width, rights=rights
                    )
                ],
            }
        )

    return {
        "symbol": chain.symbol,
        "as_of": chain.as_of.isoformat(),
        "delayed_minutes": DELAYED_MINUTES,
        "source": source or chain.source,
        "underlying_price": _round(chain.underlying_price),
        "expiries": rendered,
    }


# ---------------------------------------------------------------------------
# The disk cache
# ---------------------------------------------------------------------------


def options_dir() -> Path:
    """The chain directory: an explicit override, else beside the depth store.

    The deployment pins ``MEE_DEPTH_STORE_PATH`` (``/home/ubuntu/ztockz-data/
    depth.json``) rather than ``MEE_DATA_DIR``, so the depth store's directory
    is the evidence of where this box keeps its data — the same rule the company
    cache, the logos route and the canary already follow. With neither set, the
    project's portable ``data_dir()`` applies.
    """

    configured = os.environ.get(CACHE_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    store = os.environ.get("MEE_DEPTH_STORE_PATH", "").strip()
    if store:
        parent = Path(store).expanduser().parent
        if str(parent) not in ("", "."):
            return parent / "options"
    from app.engine.paths import data_dir

    return data_dir() / "options"


def cache_path(symbol: Any, directory: Path | None = None) -> Path | None:
    """``<options dir>/<SYMBOL>.json``, or ``None`` for an unsafe symbol."""

    key = _safe_symbol(symbol)
    if not key:
        return None
    return (directory or options_dir()) / f"{key}.json"


@dataclass(frozen=True, slots=True)
class CachedChain:
    """A chain read back from disk, with the file's own clock."""

    chain: Chain
    fetched_at: datetime


def _stamp(value: Any) -> datetime | None:
    """A timezone-aware ISO stamp, or ``None`` — a naive one cannot be aged."""

    text = _text(value)
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        return None
    return moment.astimezone(UTC)


def _day(value: Any) -> date | None:
    text = _text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _cached_contract(row: Mapping[str, Any]) -> Contract | None:
    strike = _num(row.get("strike"))
    right = _text(row.get("right"))
    if strike is None or strike <= 0 or right not in (CALL, PUT):
        return None
    return Contract(
        strike=strike,
        right=right,
        bid=_num(row.get("bid")),
        ask=_num(row.get("ask")),
        last=_positive(row.get("last")),
        volume=_int(row.get("volume")),
        open_interest=_int(row.get("open_interest")),
        iv=_volatility(row.get("iv")),
        delta=_num(row.get("delta")),
        gamma=_num(row.get("gamma")),
        theta=_num(row.get("theta")),
        vega=_num(row.get("vega")),
    )


def read_cache(path: Path) -> CachedChain | None:
    """A valid cached chain, or ``None`` for absent/corrupt/undated files."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, Mapping):
        return None
    fetched_at = _stamp(raw.get("fetched_at"))
    payload = raw.get("payload")
    symbol = _safe_symbol(raw.get("symbol"))
    if fetched_at is None or not isinstance(payload, Mapping) or not symbol:
        return None
    as_of = _stamp(payload.get("as_of"))
    groups_raw = payload.get("expiries")
    if as_of is None or not isinstance(groups_raw, list):
        return None

    groups: list[ExpiryChain] = []
    for group in groups_raw:
        if not isinstance(group, Mapping):
            continue
        expiry = _day(group.get("expiry"))
        rows = group.get("contracts")
        if expiry is None or not isinstance(rows, list):
            continue
        contracts = tuple(
            contract
            for contract in (
                _cached_contract(row) for row in rows if isinstance(row, Mapping)
            )
            if contract is not None
        )
        if contracts:
            groups.append(
                ExpiryChain(
                    expiry=expiry,
                    contracts=tuple(sorted(contracts, key=_contract_order)),
                )
            )
    if not groups:
        return None

    source = _text(raw.get("source")) or "cboe"
    return CachedChain(
        chain=Chain(
            symbol=symbol,
            as_of=as_of,
            source=source,
            underlying_price=_positive(payload.get("underlying_price")),
            expiries=tuple(sorted(groups, key=lambda group: group.expiry)),
        ),
        fetched_at=fetched_at,
    )


def write_cache(path: Path, chain: Chain, fetched_at: datetime) -> None:
    """Write the chain and its stamp atomically; a half file is never served."""

    body = json.dumps(
        {
            "symbol": chain.symbol,
            "fetched_at": fetched_at.isoformat(),
            "source": chain.source,
            "payload": {
                "as_of": chain.as_of.isoformat(),
                "underlying_price": chain.underlying_price,
                "expiries": [
                    {
                        "expiry": group.expiry.isoformat(),
                        "contracts": [
                            contract.payload() for contract in group.contracts
                        ],
                    }
                    for group in chain.expiries
                ],
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - cleanup of a failed write
            pass


def _fresh(fetched_at: datetime, now: datetime, ttl_s: float) -> bool:
    """Within the window, on the file's own clock."""

    return (now - fetched_at).total_seconds() <= ttl_s


def _usable(cached: CachedChain | None, now: datetime) -> bool:
    """A cached chain still describes something tradeable *today*.

    An old ladder is honest only while a listed expiry has not passed; once
    every date is in the past the file is dead weight and a live read (or a
    refusal) is the only answer left.
    """

    if cached is None:
        return False
    return any(group.expiry >= now.date() for group in cached.chain.expiries)


def _serveable(cached: CachedChain | None, now: datetime, ttl_s: float) -> bool:
    return cached is not None and _fresh(cached.fetched_at, now, ttl_s) and _usable(cached, now)


# ---------------------------------------------------------------------------
# The read: one in-flight request per symbol
# ---------------------------------------------------------------------------

#: ``symbol -> (loop, task)``. A burst of callers for one symbol shares a single
#: provider request; the loop is recorded so a task from a previous event loop
#: (a test process runs many) is never awaited on this one.
_INFLIGHT: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Task[tuple[Chain, str]]]] = {}


def _consume(task: asyncio.Task[Any]) -> None:
    """Retrieve a task's exception so a cancelled creator cannot leak a warning.

    The waiters still receive the exception; this callback only marks it as
    retrieved, and logs it so a read that failed while nobody was looking
    (a cancelled request) leaves a trace.
    """

    try:
        task.exception()
    except asyncio.CancelledError:
        logger.debug("option chain read was cancelled before it finished")
    except Exception as exc:  # noqa: BLE001 - the waiter re-raises the same error
        logger.debug("option chain read failed: %s", exc)


async def _single_flight(
    key: str, factory: Callable[[], Coroutine[Any, Any, tuple[Chain, str]]]
) -> tuple[Chain, str]:
    loop = asyncio.get_running_loop()
    entry = _INFLIGHT.get(key)
    if entry is not None:
        if entry[0] is not loop:
            # A previous loop's task is unreachable from here; drop it.
            _INFLIGHT.pop(key, None)
        elif not entry[1].done():
            return await asyncio.shield(entry[1])
    task: asyncio.Task[tuple[Chain, str]] = loop.create_task(factory())
    task.add_done_callback(_consume)
    _INFLIGHT[key] = (loop, task)
    try:
        return await asyncio.shield(task)
    finally:
        current = _INFLIGHT.get(key)
        if current is not None and current[1] is task:
            _INFLIGHT.pop(key, None)


async def _guard(source: Any, key: str) -> FetchOutcome:
    """One source's answer, with a crash treated as a refusal (never a 404)."""

    try:
        outcome = await source.fetch(key)
    except Exception as exc:  # noqa: BLE001 - a source failure is not a verdict
        logger.warning("option chain source for %s failed: %s", key, exc)
        return FetchOutcome()
    return outcome if isinstance(outcome, FetchOutcome) else FetchOutcome()


def _store(path: Path | None, chain: Chain, moment: datetime) -> None:
    if path is None:
        return
    try:
        write_cache(path, chain, moment)
    except OSError as exc:
        logger.warning("option chain cache write for %s failed: %s", chain.symbol, exc)


async def _resolve(
    key: str,
    *,
    path: Path | None,
    moment: datetime,
    ttl_s: float,
    cboe: Any,
    yahoo: Any,
) -> tuple[Chain, str]:
    """The chain to serve, and the wire it came from (``cache`` when it did not).

    Order of answers:

    1. a cache file inside ``ttl_s`` — served without a request;
    2. Cboe's chain — cached, ``source="cboe"``;
    3. the cached copy, when Cboe is rate limited (the fallback is not spent on
       a provider that just said "not now");
    4. Yahoo's chain — cached, ``source="yahoo"``;
    5. an expired cache file, when both admitted providers refused;
    6. :class:`UnknownSymbol` when a provider said the symbol has no chain, or
       :class:`ChainUnavailable` when both refused without saying anything.
    """

    cached = read_cache(path) if path is not None else None
    if _serveable(cached, moment, ttl_s):
        return cached.chain, "cache"  # type: ignore[union-attr]

    cboe_outcome = await _guard(cboe if cboe is not None else CboeChainSource(), key)
    if cboe_outcome.chain is not None:
        _store(path, cboe_outcome.chain, moment)
        return cboe_outcome.chain, "cboe"
    if cboe_outcome.rate_limited and _usable(cached, moment):
        return cached.chain, "cache"  # type: ignore[union-attr]

    yahoo_outcome = await _guard(yahoo if yahoo is not None else YahooChainSource(), key)
    if yahoo_outcome.chain is not None:
        _store(path, yahoo_outcome.chain, moment)
        return yahoo_outcome.chain, "yahoo"
    if _usable(cached, moment):
        return cached.chain, "cache"  # type: ignore[union-attr]
    if cboe_outcome.unknown or yahoo_outcome.unknown:
        raise UnknownSymbol(f"no listed option chain exists for {key!r}")
    raise ChainUnavailable(f"no option chain source answered for {key!r}")


async def collect_options(
    symbol: Any,
    *,
    expiries: int = 4,
    strikes: int = 12,
    right: str = "both",
    cboe: Any = None,
    yahoo: Any = None,
    now: datetime | None = None,
    ttl_s: float = CHAIN_TTL_S,
    directory: Path | None = None,
) -> dict[str, Any]:
    """The option ladder for one symbol, from the cache, Cboe, or Yahoo.

    ``cboe`` and ``yahoo`` are the two seams: any object with an asynchronous
    ``fetch(symbol) -> FetchOutcome``. Production leaves them out and gets the
    two sources above; a test injects a canned answer so no socket is opened.
    """

    key = _safe_symbol(symbol)
    if not key:
        raise UnknownSymbol(f"{symbol!r} is not a symbol with an option chain")
    if right not in RIGHTS:
        raise ValueError(f"right must be one of {', '.join(RIGHTS)}; got {right!r}")

    moment = now or _utcnow()
    path = cache_path(key, directory)
    cached = read_cache(path) if path is not None else None
    if _serveable(cached, moment, ttl_s):
        return select_chain(
            cached.chain,  # type: ignore[union-attr]
            expiries=expiries,
            strikes=strikes,
            right=right,
            source="cache",
        )

    chain, source = await _single_flight(
        key,
        lambda: _resolve(
            key, path=path, moment=moment, ttl_s=ttl_s, cboe=cboe, yahoo=yahoo
        ),
    )
    return select_chain(
        chain, expiries=expiries, strikes=strikes, right=right, source=source
    )


__all__ = [
    "CACHE_DIR_ENV",
    "CBOE_CHAIN_URL",
    "CBOE_MAX_RETRIES",
    "CBOE_TIMEOUT_S",
    "CHAIN_TTL_S",
    "DELAYED_MINUTES",
    "MAX_EXPIRIES",
    "MAX_STRIKES",
    "RETRY_AFTER_MAX_S",
    "RIGHTS",
    "RISK_FREE_RATE",
    "YAHOO_EXPIRY_LIMIT",
    "CachedChain",
    "Chain",
    "ChainError",
    "ChainUnavailable",
    "CboeChainSource",
    "Contract",
    "ExpiryChain",
    "FetchOutcome",
    "OccQuote",
    "UnknownSymbol",
    "YahooChainSource",
    "black_scholes_greeks",
    "cache_path",
    "collect_options",
    "options_dir",
    "parse_cboe",
    "parse_occ",
    "parse_yahoo",
    "read_cache",
    "select_chain",
    "write_cache",
]

