"""Multi-source consensus display (P10): one row per stock, no per-source columns.

``GET /screens`` used to answer with the raw TradingView snapshot. That is a
*selection*, not a verified price: one venue's scanner decided both the order
and every cell. This module turns the same twenty candidates into a **consensus
row** — the median of what the selection source and the live verifiers publish —
and states how much of the evidence agreed.

The rules that make a consensus row trustworthy
-----------------------------------------------

* **One row per stock, never one column group per source.** A caller asking
  "what is AAPL's price" must not have to decide which of ``yahoo.price``,
  ``nasdaq.price`` and ``tradingview.price`` is the answer. The row carries one
  ``price`` (the median) and a ``source_summary`` that says how many sources
  stood behind it and how far apart they were. Per-source cells would also make
  the table's shape depend on which verifiers happened to answer, which is a
  schema that changes with the weather.
* **Median, not mean.** One provider printing a stale or post-market price moves
  a mean; it cannot move a median of three. Ties (even counts) average the two
  middle values, which is the documented ``statistics.median`` behaviour.
* **Four live verifiers, two of them HTML pages.** Yahoo (one batch request) and
  Nasdaq (US, per symbol) are JSON; Finviz's quote page and Google Finance are
  page scrapes added as defaults in P10.1, because four independent prints are
  what make a median meaningful and a confidence honest. The HTML legs are
  bounded — at most :data:`MAX_WEB_CONCURRENCY` requests in flight, one timeout
  per symbol — and they publish only the cells their page really prints: price,
  change, market cap, plus volume where it is exact. A page that omits a cell
  leaves it null; nothing is ever filled with a zero.
* **Missing stays null.** A verifier that publishes no change (Nasdaq's info
  endpoint has no previous close) contributes nothing to the change consensus,
  and a field no source published stays ``None`` — never ``0``. ``source_count``
  records how many sources actually answered, so a one-source row cannot
  masquerade as a consensus.
* **The selection source is a source, not the answer.** TradingView's snapshot
  row is the ``tradingview`` observation. It costs zero extra requests (the scan
  that produced the candidates *is* TradingView), and it keeps a two-source
  region (Global, where Nasdaq is US-only) at a real median instead of a single
  guess.
* **Total failure is not a consensus.** If no verifier answered anything, the
  document is the raw TradingView snapshot with ``source="tradingview"``,
  ``degraded=true`` and the reason in ``verification_error``. Claiming
  ``source="multi"`` over a snapshot nobody verified would be the same lie as an
  empty CSV.

Cache
-----

A verified document is cached for :data:`CACHE_TTL_S` under
``(region, sort, screen_id)``. The screen id is part of the key on purpose: a
refreshed snapshot is a different twenty rows (new ids, new prices), so its
verification must not inherit the previous window's document. A read inside the
window sends **zero** verifier calls — the display is a five-minute picture, and
re-verifying it on every poll would turn a browse tab into a quote terminal.

A degraded document is deliberately **not** cached: a two-second network blip
must not pin a "tradingview only" page for five minutes, so the next request
retries the verification. Only the successful consensus is worth remembering.

Timestamps
----------

``as_of`` on the document and on every row dates the **snapshot** the candidates
came from; ``source_summary.verified_at`` dates the verification pass. Per-source
timestamps are not published, because that would be the per-source columns this
module exists to remove; a caller who needs provenance reads the two timestamps
and the agreement cell.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import math
import re
import statistics
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from html import unescape as _unescape
from typing import Any, Protocol

from app.config import Settings, get_settings
from app.market.quote import SourceQuote
from app.market.sources import (
    BROWSER_USER_AGENT,
    SYMBOL_RE,
    InvalidSymbol,
    NasdaqQuoteSource,
    QuoteSourceError,
    YahooQuoteSource,
)
from app.providers import http as provider_http
from app.screen.repository import SqliteScreenRepository, iso
from app.screen.snapshot import load_screen
from app.screen.universe import US_EXCHANGES

#: The document-level ``source`` a verified page publishes. It is deliberately
#: *not* stored in ``screens.source``: the stored snapshot is TradingView's, and
#: a symbols-mode selection resolves ticks against that snapshot and its source.
MULTI_SOURCE = "multi"
#: Where the candidates came from. Always published, on both the consensus page
#: and the degraded one, so "multi" never hides which universe was selected.
SELECTION_SOURCE = "tradingview"
#: The method string a client keys on when it wants to know *how* a row was built.
CONSENSUS_METHOD = "multi_source_consensus"
#: The sources a full consensus can draw on. Coverage is measured against this
#: tuple, so ``confidence`` cannot be inflated by adding verifiers later without
#: also widening the documented set — and it is *not* narrowed when a deployment
#: turns one leg off, because the denominator is the number of sources the
#: product claims to consult, not the number that happened to answer. Five since
#: P10.1: the selection snapshot plus four live verifiers.
VERIFICATION_SOURCES: tuple[str, ...] = (
    "tradingview",
    "yahoo",
    "nasdaq",
    "finviz",
    "google",
)

#: Same window as the snapshot (PLAN-screen.md §4: 300 s per screen).
CACHE_TTL_S = 300
#: How far a source's change may sit from the median and still count as agreeing.
#: Half a percentage point: live spreads on a liquid name are ~0.01 pp, so this
#: tolerates rounding while flagging a real disagreement.
AGREEMENT_TOLERANCE_PP = 0.5

#: TradingView exchange code (and MIC, where they differ) → Yahoo listing suffix,
#: for the venues whose suffix is not a guess. A listing Yahoo does not know
#: answers empty, which is dropped from the consensus; it is never turned into a
#: price. US venues need no suffix and are handled separately, and a venue absent
#: from this table is not asked at all rather than asked with an invented ticker.
#:
#: ``EURONEXT`` is deliberately absent: the code covers Paris, Amsterdam,
#: Brussels and Lisbon, and one suffix would send a Brussels ticker to Paris —
#: where a *different* company may hold it. A MIC-specific entry (``XPAR``) is
#: unambiguous; the venue group is not. Secondary German venues (``BX``,
#: ``MUN``, ``TRADEGATE``) are absent for the same reason: they print the XETR
#: line of a foreign issuer, which is not a price discovery venue.
YAHOO_SUFFIX_BY_EXCHANGE: Mapping[str, str] = {
    # -- TradingView's own codes (what ``build_universe`` keeps for non-US rows)
    "TSE": ".T",
    "JPX": ".T",
    "HKEX": ".HK",
    "SSE": ".SS",
    "SZSE": ".SZ",
    "TPEX": ".TWO",
    "TWSE": ".TW",
    "KRX": ".KS",
    "KOSDAQ": ".KQ",
    "LSE": ".L",
    "NSE": ".NS",
    "BSE": ".BO",
    "TSX": ".TO",
    "ASX": ".AX",
    "SIX": ".SW",
    "BMV": ".MX",
    "BVMF": ".SA",
    "TADAWUL": ".SR",
    # -- MICs, for a caller that normalised the venue before asking
    "XLON": ".L",
    "XETR": ".DE",
    "XPAR": ".PA",
    "XSWX": ".SW",
    "XAMS": ".AS",
    "XMAD": ".MC",
    "XTKS": ".T",
    "XHKG": ".HK",
    "XSHG": ".SS",
    "XSHE": ".SZ",
    "XNSE": ".NS",
    "XTSE": ".TO",
    "XASX": ".AX",
    "XKRX": ".KS",
    "XTAI": ".TW",
}

#: US venues in both spellings: the MIC that :func:`app.screen.universe.mic_for`
#: stores on a snapshot row, and the TradingView code the scanner prints. A bare
#: ticker is correct for either, so accepting both is robustness, not guessing.
US_VENUES: frozenset[str] = US_EXCHANGES | frozenset({"NASDAQ", "NYSE", "AMEX", "CBOE"})

#: Finviz's quote page. Not ``/quote.ashx``: that legacy path answers ``301`` and
#: the provider client deliberately does not follow redirects (02 §3.11 SSRF), so
#: the old URL is a guaranteed failure rather than a shorter one.
FINVIZ_HOST = "https://finviz.com"
FINVIZ_QUOTE_PATH = "/stock?t={ticker}"

GOOGLE_HOST = "https://www.google.com"
GOOGLE_QUOTE_PATH = "/finance/quote/{ticker}"

#: The HTML legs are page scrapes: slower than JSON and bot-walled, so a
#: twenty-symbol pass runs them at most three requests in flight — enough to fit
#: the display budget, few enough not to look like a crawler. The cap is part of
#: the contract, not a default a constructor may raise.
MAX_WEB_CONCURRENCY = 3

#: Venue → the exchange token Google prints in its quote path (``AAPL:NASDAQ``).
#: Only the three US venues whose Google spelling is not a guess: a CBOE listing
#: or a foreign venue is not asked at all rather than asked with an invented
#: suffix, the same rule as :data:`YAHOO_SUFFIX_BY_EXCHANGE`.
GOOGLE_EXCHANGE_BY_VENUE: Mapping[str, str] = {
    "XNAS": "NASDAQ",
    "NASDAQ": "NASDAQ",
    "XNYS": "NYSE",
    "NYSE": "NYSE",
    "XASE": "AMEX",
    "AMEX": "AMEX",
}

#: Numeric cells an observation can carry. Used by ``answered`` and by the
#: consensus, so a new field cannot be added in one place and forgotten in the
#: other.
_OBSERVATION_FIELDS: tuple[str, ...] = ("price", "change_pct", "volume", "market_cap_usd")


# ---------------------------------------------------------------------------
# Observations and verifiers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Observation:
    """One source's answer for one symbol, already in screen units.

    ``change_pct`` is a percent (``-0.59`` is a 0.59% decline), ``volume`` is
    shares, ``market_cap_usd`` is dollars. Any cell a provider did not publish is
    ``None``: an observation with no numbers at all is an EMPTY answer, not a
    zero-valued quote.
    """

    source: str
    price: float | None = None
    change_pct: float | None = None
    volume: float | None = None
    market_cap_usd: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("an observation needs a non-empty source name")
        object.__setattr__(self, "source", self.source.strip())
        for name in _OBSERVATION_FIELDS:
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool):
                raise TypeError(f"{name} must be a number, got {value!r}")
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{name} must be a number, got {value!r}") from None
            if not math.isfinite(number):
                raise ValueError(f"{name} must be finite, got {value!r}")
            object.__setattr__(self, name, number)

    @property
    def answered(self) -> bool:
        """Did this source publish at least one number for the symbol?"""

        return any(getattr(self, name) is not None for name in _OBSERVATION_FIELDS)


class Verifier(Protocol):
    """One independent source asked for the twenty displayed symbols.

    ``observe`` receives the candidate rows (screen shape: ``symbol``,
    ``exchange``, ``price``, ...) and returns one answer per symbol it could
    address. A symbol missing from the mapping is "this source does not cover
    it"; ``None`` is "it covers it and has no data". Raising means the source
    could not answer at all, and the service records that as a failure — it never
    becomes a null consensus value dressed up as agreement.
    """

    name: str

    async def observe(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Observation | None]: ...


class CallableVerifier:
    """Adapt a plain (async) function into a :class:`Verifier`.

    This is the injection point for a source this module does not ship (a
    broker's quote feed, a test double): the four defaults — Yahoo, Nasdaq,
    Finviz and Google — are classes because they own their transport, while any
    other leg is a function the deployment supplies. The name is validated like
    a built-in's, so two verifiers can never share a column of the summary.
    """

    def __init__(
        self,
        name: str,
        observe: Callable[
            [Sequence[Mapping[str, Any]]],
            Mapping[str, Observation | None] | Awaitable[Mapping[str, Observation | None]],
        ],
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("a verifier needs a non-empty name")
        if not callable(observe):
            raise TypeError("observe must be callable")
        self.name = name.strip()
        self._observe = observe

    async def observe(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Observation | None]:
        answer = self._observe(rows)
        if inspect.isawaitable(answer):
            answer = await answer
        if not isinstance(answer, Mapping):
            raise TypeError(
                f"verifier {self.name!r} must return a mapping, got {type(answer).__name__}"
            )
        return dict(answer)


def _number(value: Any) -> float | None:
    """A finite float, or ``None``. Never coerces a string or a bool into a price."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        return None
    return number if math.isfinite(number) else None


def _change_pct(quote: SourceQuote) -> float | None:
    """The provider's change in percent; ``None`` without a previous close."""

    if quote.prev_close is None or quote.prev_close <= 0:
        return None
    return (quote.last / quote.prev_close - 1.0) * 100.0


def yahoo_symbol(row: Mapping[str, Any]) -> str | None:
    """The ticker Yahoo needs for one row, or ``None`` when it cannot be known.

    A US listing is the bare ticker; a known foreign venue appends its Yahoo
    suffix. An unknown venue is not asked at all — guessing a suffix would send a
    request for a ticker that does not exist and, worse, could match a *different*
    company if it did.
    """

    symbol = str(row.get("symbol") or "").strip().upper()
    if not symbol or not SYMBOL_RE.match(symbol):
        return None
    exchange = str(row.get("exchange") or "").strip().upper()
    if exchange in US_VENUES:
        # Yahoo spells US class shares with a hyphen (BRK.B -> BRK-B); the
        # snapshot keeps the screen's dot spelling for the export join.
        if symbol.count(".") == 1:
            head, suffix = symbol.split(".", 1)
            if len(suffix) == 1 and suffix.isalpha():
                return f"{head}-{suffix}"
        return symbol
    suffix = YAHOO_SUFFIX_BY_EXCHANGE.get(exchange)
    if suffix is None:
        return None
    candidate = f"{symbol}{suffix}"
    return candidate if SYMBOL_RE.match(candidate) else None


class YahooVerifier:
    """Yahoo batch quotes: the primary independent check (up to 20 per request)."""

    name = "yahoo"

    def __init__(
        self, *, source: YahooQuoteSource | None = None, timeout_s: float = 10.0
    ) -> None:
        self._source = source or YahooQuoteSource(timeout_s=timeout_s)

    async def observe(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Observation | None]:
        # Provider ticker → row symbol. Two rows that map to one provider ticker
        # (a US line and a same-named foreign line) each get the answer; the row
        # symbol is the key the service addresses.
        wanted: dict[str, str] = {}
        for row in rows:
            provider = yahoo_symbol(row)
            symbol = str(row.get("symbol") or "").strip()
            if provider and symbol and provider not in wanted:
                wanted[provider] = symbol
        if not wanted:
            return {}

        # ``fetch_many`` raises only when every chunk failed; a per-symbol EMPTY
        # answer stays ``None``. That distinction is exactly what the service
        # needs to tell "Yahoo is down" from "Yahoo does not know this ticker".
        answers = await self._source.fetch_many(tuple(wanted))
        observations: dict[str, Observation | None] = {}
        for provider, symbol in wanted.items():
            quote = answers.get(provider)
            observations[symbol] = (
                None
                if quote is None
                else Observation(
                    source=self.name,
                    price=quote.last,
                    change_pct=_change_pct(quote),
                    volume=quote.volume,
                )
            )
        return observations


class NasdaqVerifier:
    """Nasdaq's per-symbol info endpoint; US listings only.

    Nasdaq is a *price* check, not a change check: ``/api/quote/{symbol}/info``
    publishes ``lastSalePrice`` and ``volume`` but no previous close, so its
    observation leaves ``change_pct`` null and the change consensus runs over the
    sources that actually publish one. A symbol Nasdaq does not cover (a foreign
    venue, or "not exists") is an EMPTY answer; only a request-level failure
    counts toward an outage.
    """

    name = "nasdaq"

    def __init__(
        self,
        *,
        source: NasdaqQuoteSource | None = None,
        timeout_s: float = 10.0,
        concurrency: int = 4,
        exchanges: frozenset[str] = US_VENUES,
    ) -> None:
        if not isinstance(concurrency, int) or concurrency < 1:
            raise ValueError(f"concurrency must be an int >= 1, got {concurrency!r}")
        self._source = source or NasdaqQuoteSource(timeout_s=timeout_s)
        self._concurrency = concurrency
        self._exchanges = frozenset(exchanges)

    async def observe(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Observation | None]:
        targets: list[str] = []
        for row in rows:
            if str(row.get("exchange") or "").strip().upper() not in self._exchanges:
                continue
            symbol = str(row.get("symbol") or "").strip().upper()
            if symbol and SYMBOL_RE.match(symbol) and symbol not in targets:
                targets.append(symbol)
        if not targets:
            return {}

        semaphore = asyncio.Semaphore(self._concurrency)
        answers: dict[str, Observation | None] = {}
        failures: list[BaseException] = []

        async def one(symbol: str) -> None:
            async with semaphore:
                try:
                    quote = await self._source.fetch(symbol)
                except InvalidSymbol:
                    # Not a Nasdaq ticker: an answer of "not covered".
                    answers[symbol] = None
                    return
                except Exception as exc:  # noqa: BLE001 - per-symbol failover
                    failures.append(exc)
                    return
                answers[symbol] = (
                    None
                    if quote is None
                    else Observation(
                        source=self.name,
                        price=quote.last,
                        change_pct=_change_pct(quote),
                        volume=quote.volume,
                    )
                )

        await asyncio.gather(*(one(symbol) for symbol in targets))
        if failures and len(failures) == len(targets):
            first = failures[0]
            raise QuoteSourceError(
                f"nasdaq failed for {len(targets)} symbol(s): "
                f"{type(first).__name__}: {first}",
                status=getattr(first, "status", None),
            ) from first
        return {symbol: answers.get(symbol) for symbol in targets}


# ---------------------------------------------------------------------------
# HTML quote pages: Finviz and Google Finance (P10.1)
# ---------------------------------------------------------------------------
#
# Both are server-rendered pages with one number each of price, change and market
# cap. The helpers below read the *visible* markup only: script/style/noscript
# blocks and comments are dropped before anything else, both because Google's
# quote page is 1.4 MB of mostly JavaScript and because a ``<div`` inside a
# script string would unbalance the nesting scan below.

_MARKUP_SCRIPTS = re.compile(
    r"<(script|style|noscript)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL
)
_MARKUP_COMMENTS = re.compile(r"<!--.*?-->", re.DOTALL)
_DIV_OPEN = re.compile(r"<div\b([^>]*)>", re.IGNORECASE)
_DIV_TAG = re.compile(r"<(/?)div\b[^>]*>", re.IGNORECASE)
_CLASS_ATTR = re.compile(r"""\bclass\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
_ANY_TAG = re.compile(r"<[^>]+>")
_PERCENT = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*%")
_UNIT_SCALE: Mapping[str, float] = {"": 1.0, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}

#: The alphabet a Finviz/Google equity ticker is made of. Deliberately narrower
#: than :data:`~app.market.sources.SYMBOL_RE`: ``^VIX`` and ``EURUSD=X`` are
#: provider symbols, but they are not equity quote pages, and constraining the
#: alphabet is what lets the URL be built without a percent-encoder (``urllib``
#: is a banned legacy transport in this tree, 05 §3.4 rule 3).
_WEB_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,15}$")

#: A 200 that is really a bot wall. The page answers 200 and says this instead of
#: a quote, so a parser that only reads numbers sees "no price" — a per-symbol
#: guess. The verifier turns the marker into a named failure instead.
_CHALLENGE_MARKERS: tuple[str, ...] = (
    "verify you are a human",
    "are you a robot",
    "unusual traffic",
    "captcha",
)


def _visible_markup(body: str) -> str:
    """Strip what a reader never sees, so the nesting scan cannot be fooled."""

    return _MARKUP_COMMENTS.sub(" ", _MARKUP_SCRIPTS.sub(" ", str(body or "")))


def _class_tokens(attributes: str) -> frozenset[str]:
    """The exact class tokens of one opening tag (never a substring match)."""

    match = _CLASS_ATTR.search(attributes)
    if match is None:
        return frozenset()
    raw = match.group(1) if match.group(1) is not None else match.group(2)
    return frozenset(token for token in raw.split() if token)


def _flat_text(fragment: str) -> str:
    """One element's text: tags dropped, entities decoded, whitespace collapsed."""

    return " ".join(_unescape(_ANY_TAG.sub(" ", fragment)).split())


def _div_block(markup: str, token: str, start: int = 0) -> tuple[str, int, int] | None:
    """``(inner_html, start, end)`` of the first ``<div>`` carrying ``token``.

    The close is found by counting nested ``<div>`` tags, not by a non-greedy
    regex: both pages nest divs inside the blocks worth reading (Google wraps the
    change arrow in one), and ``.*?</div>`` would stop at the inner close and
    silently truncate the cell.
    """

    for opening in _DIV_OPEN.finditer(markup, start):
        if token not in _class_tokens(opening.group(1)):
            continue
        depth = 1
        for tag in _DIV_TAG.finditer(markup, opening.end()):
            if tag.group(1):
                depth -= 1
                if depth == 0:
                    return (
                        markup[opening.end() : tag.start()],
                        opening.start(),
                        tag.end(),
                    )
            else:
                depth += 1
        return None
    return None


def _scaled_number(text: str) -> float | None:
    """``"$1,234.50"``/``"5074.76B"``/``"5.07T"`` → a float; anything else ``None``.

    A provider that prints ``"-"`` or ``"N/A"`` published no number, so the cell
    stays null: reading a dash as zero is the one thing this module must not do.
    """

    token = (
        str(text or "")
        .strip()
        .replace("$", "")
        .replace(",", "")
        .replace("\u2212", "-")
    )
    match = re.fullmatch(r"([-+]?\d+(?:\.\d+)?)\s*([KMBT]?)", token, re.IGNORECASE)
    if match is None:
        return None
    number = float(match.group(1)) * _UNIT_SCALE[match.group(2).upper()]
    return number if math.isfinite(number) else None


def _positive_number(text: str) -> float | None:
    """A price/cap/volume: positive, finite, or ``None``. Zero is never a quote."""

    number = _scaled_number(text)
    return number if number is not None and number > 0 else None


def _printed_percent(text: str) -> float | None:
    """The first ``x%`` in a cell, sign included (``(-0.14%)`` → ``-0.14``)."""

    match = _PERCENT.search(str(text or ""))
    return float(match.group(1)) if match is not None else None


def _quotes_a_challenge(body: str) -> bool:
    head = str(body or "")[:4000].lower()
    return any(marker in head for marker in _CHALLENGE_MARKERS)


def _finviz_fields(markup: str) -> dict[str, str]:
    """The Finviz snapshot table as ``label → value``; the first row wins.

    The table is a sequence of label cells and value cells; pairing them in order
    is what keeps ``Market Cap`` from absorbing ``Volume``. A label whose value
    cell starts after the *next* label is a half row, so the walk stops there
    rather than pairing across metrics.
    """

    fields: dict[str, str] = {}
    cursor = 0
    while True:
        label = _div_block(markup, "snapshot-td-label", cursor)
        if label is None:
            return fields
        value = _div_block(markup, "snapshot-td-content", label[2])
        if value is None:
            return fields
        following = _div_block(markup, "snapshot-td-label", label[2])
        if following is not None and following[1] < value[1]:
            return fields
        name = _flat_text(label[0]).strip()
        if name:
            fields.setdefault(name, _flat_text(value[0]).strip())
        cursor = value[2]


def parse_finviz_quote(body: str) -> Observation | None:
    """One Finviz quote page → one observation (``None`` without a price).

    The header prints the regular session first and the after-hours block second,
    in identical markup; the regular one is the price this screen is about, so an
    ``Aftermarket`` block is skipped rather than read. The snapshot table is the
    fallback (``Price``/``Change %``/``Prev Close``) and is where the market cap
    and the exact share volume come from.
    """

    markup = _visible_markup(body)
    fields = _finviz_fields(markup)

    price: float | None = None
    change: float | None = None
    cursor = 0
    while True:
        block = _div_block(markup, "quote-price", cursor)
        if block is None:
            break
        inner = block[0]
        session = _flat_text(inner).lower()
        if "aftermarket" in session or "after hours" in session:
            cursor = block[2]
            continue
        quote = re.search(
            r'<strong[^>]*class="[^"]*quote-price_price[^"]*"[^>]*>(.*?)</strong>',
            inner,
            re.IGNORECASE | re.DOTALL,
        ) or re.search(r"<strong\b[^>]*>(.*?)</strong>", inner, re.IGNORECASE | re.DOTALL)
        if quote is not None:
            price = _positive_number(_flat_text(quote.group(1)))
        change = _printed_percent(_flat_text(inner))
        break

    if price is None:
        price = _positive_number(fields.get("Price", ""))
    if change is None:
        # The snapshot cell prints "-0.14%"; a bare number is the same cell in
        # an older layout, so both spellings are read before the prev-close
        # derivation is used.
        change = _printed_percent(fields.get("Change %", ""))
        if change is None:
            change = _scaled_number(fields.get("Change %", ""))
        if change is None:
            previous = _positive_number(fields.get("Prev Close", ""))
            if price is not None and previous is not None:
                change = (price / previous - 1.0) * 100.0

    if price is None:
        return None
    return Observation(
        source="finviz",
        price=price,
        change_pct=change,
        volume=_positive_number(fields.get("Volume", "")),
        market_cap_usd=_positive_number(fields.get("Market Cap", "")),
    )


def _google_stat(markup: str, label: str) -> str:
    """The value beside one Google key-stat label (``Mkt. cap`` → ``"5.07T"``)."""

    cursor = 0
    while True:
        stat = _div_block(markup, "KxsRFb", cursor)
        if stat is None:
            return ""
        name = _div_block(stat[0], "SwQK7")
        value = _div_block(stat[0], "dO6ijd")
        if name is not None and value is not None:
            printed = _flat_text(name[0]).strip().lower().rstrip(".")
            if printed == label:
                return _flat_text(value[0]).strip()
        cursor = stat[2]


def parse_google_quote(body: str) -> Observation | None:
    """One Google Finance quote page → one observation (``None`` without a price).

    Google renders the regular session as ``div.N6SYTe`` (price) followed by
    ``div.DAicsd`` (change), then an after-hours block that reuses the very same
    classes. The change is therefore the first percentage inside the first
    ``DAicsd`` **after** the price — taking the first ``DAicsd`` on the page, or
    the last, reads the wrong session. Google prints the sign itself; the
    up/down class (``ougHge``/``ymyBi``) is only consulted when it does not.
    """

    markup = _visible_markup(body)
    price_block = _div_block(markup, "N6SYTe")
    if price_block is None:
        return None
    price = _positive_number(_flat_text(price_block[0]))
    if price is None:
        return None

    change: float | None = None
    change_block = _div_block(markup, "DAicsd", price_block[2])
    if change_block is not None:
        inner = change_block[0]
        match = _PERCENT.search(_flat_text(inner))
        if match is not None:
            change = float(match.group(1))
            if not match.group(1).startswith(("-", "+")):
                if re.search(r'class="[^"]*\bymyBi\b', inner):
                    change = -abs(change)
                elif re.search(r'class="[^"]*\bougHge\b', inner):
                    change = abs(change)

    market_cap = _positive_number(
        _google_stat(markup, "mkt. cap") or _google_stat(markup, "market cap")
    )
    return Observation(
        source="google",
        price=price,
        change_pct=change,
        market_cap_usd=market_cap,
    )


def _us_symbol(row: Mapping[str, Any]) -> str | None:
    """The bare ticker of a US listing, or ``None`` when the row is not one.

    Also ``None`` for a symbol outside the equity alphabet (an index, an FX
    pair): those are provider symbols, not share pages, and the URL is built
    verbatim from the answer.
    """

    if str(row.get("exchange") or "").strip().upper() not in US_VENUES:
        return None
    symbol = str(row.get("symbol") or "").strip().upper()
    if not symbol or not _WEB_SYMBOL_RE.match(symbol):
        return None
    return symbol


def finviz_symbol(row: Mapping[str, Any]) -> str | None:
    """The ticker Finviz needs: the bare US ticker, class shares hyphenated.

    Finviz prints ``BRK-B`` where TradingView prints ``BRK.B``; that translation
    is the whole mapping. A foreign venue is not asked at all — one suffix per
    exchange is a guess this module already refuses for Yahoo.
    """

    symbol = _us_symbol(row)
    return None if symbol is None else symbol.replace(".", "-")


def google_symbol(row: Mapping[str, Any]) -> str | None:
    """The ``TICKER:EXCHANGE`` token Google's quote path needs (``BRK.B:NYSE``).

    Class shares keep their dot here, and the venue becomes the suffix. Only the
    three US venues with a known Google spelling are asked: guessing one would
    fetch another company's page, not a missing one.
    """

    symbol = _us_symbol(row)
    if symbol is None:
        return None
    exchange = GOOGLE_EXCHANGE_BY_VENUE.get(
        str(row.get("exchange") or "").strip().upper()
    )
    return None if exchange is None else f"{symbol}:{exchange}"


class _PerSymbolQuoteVerifier:
    """One HTML page per symbol, at most ``concurrency`` requests in flight.

    The two scraping legs differ only in their symbol spelling, URL and parser,
    so the fan-out, the timeout and the outage rule live here:

    * **Bounded.** :data:`MAX_WEB_CONCURRENCY` pages in flight, and a constructor
      that asks for more is refused rather than quietly obeyed: twenty symbols
      are a display, not a crawl.
    * **Timed out per symbol.** ``wait_for`` around each fetch, so one hanging
      ticker cannot hold the whole verification past the display budget while
      the others wait behind it.
    * **A failure is not an empty answer.** One unknown ticker is ``None`` (not
      covered); only when *every* symbol failed does :meth:`observe` raise, which
      is what the service records as an outage instead of a null consensus.

    ``fetcher`` is the injection point: a test (or a deployment with its own
    transport) can replace the HTTP hop without rebuilding the class.
    """

    name = ""

    def __init__(
        self,
        *,
        timeout_s: float = 10.0,
        concurrency: int = MAX_WEB_CONCURRENCY,
        user_agent: str = BROWSER_USER_AGENT,
        fetcher: Callable[[str], Awaitable[Observation | None]] | None = None,
    ) -> None:
        if isinstance(concurrency, bool) or not isinstance(concurrency, int):
            raise TypeError(f"concurrency must be an int, got {concurrency!r}")
        if not 1 <= concurrency <= MAX_WEB_CONCURRENCY:
            raise ValueError(
                f"concurrency must be 1..{MAX_WEB_CONCURRENCY}, got {concurrency!r}"
            )
        if not timeout_s > 0:
            raise ValueError(f"timeout_s must be > 0, got {timeout_s!r}")
        if not user_agent or not str(user_agent).strip():
            raise ValueError("user_agent must be a non-empty string")
        if fetcher is not None and not callable(fetcher):
            raise TypeError("fetcher must be callable")
        self.timeout_s = float(timeout_s)
        self.concurrency = int(concurrency)
        self.user_agent = str(user_agent)
        self._fetcher = fetcher

    # -- per-provider bits -------------------------------------------------

    def ticker(self, row: Mapping[str, Any]) -> str | None:
        """The provider's spelling of this row's ticker (``None`` → not asked)."""

        raise NotImplementedError

    def url(self, ticker: str) -> str:
        raise NotImplementedError

    def parse(self, body: str) -> Observation | None:
        raise NotImplementedError

    # -- transport ---------------------------------------------------------

    def headers(self) -> dict[str, str]:
        return {
            "user-agent": self.user_agent,
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "accept-language": "en-US,en;q=0.9",
        }

    async def fetch(self, ticker: str) -> Observation | None:
        """One page → one observation; a non-200 is a failure, not a null quote."""

        if self._fetcher is not None:
            return await self._fetcher(ticker)
        async with provider_http.build_client(
            timeout_s=self.timeout_s, headers=self.headers()
        ) as client:
            response = await client.get(self.url(ticker))
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise QuoteSourceError(f"{self.name} answered {status}", status=status)
        body = str(getattr(response, "text", "") or "")
        observation = self.parse(body)
        if observation is None and _quotes_a_challenge(body):
            raise QuoteSourceError(
                f"{self.name} answered a bot challenge instead of a quote"
            )
        return observation

    # -- fan-out -----------------------------------------------------------

    async def observe(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Observation | None]:
        wanted: dict[str, str] = {}
        for row in rows:
            ticker = self.ticker(row)
            symbol = str(row.get("symbol") or "").strip()
            if ticker and symbol and ticker not in wanted:
                wanted[ticker] = symbol
        if not wanted:
            return {}

        semaphore = asyncio.Semaphore(self.concurrency)
        answers: dict[str, Observation | None] = {}
        failures: list[BaseException] = []

        async def one(ticker: str) -> None:
            async with semaphore:
                try:
                    observation = await asyncio.wait_for(
                        self.fetch(ticker), timeout=self.timeout_s
                    )
                except InvalidSymbol:
                    # Not this provider's ticker: an answer of "not covered".
                    answers[ticker] = None
                    return
                except Exception as exc:  # noqa: BLE001 - per-symbol failover
                    failures.append(exc)
                    return
                if observation is not None:
                    observation = replace(observation, source=self.name)
                answers[ticker] = observation

        await asyncio.gather(*(one(ticker) for ticker in wanted))
        if failures and len(failures) == len(wanted):
            first = failures[0]
            raise QuoteSourceError(
                f"{self.name} failed for {len(wanted)} symbol(s): "
                f"{type(first).__name__}: {str(first) or 'no detail'}",
                status=getattr(first, "status", None),
            ) from first
        return {symbol: answers.get(ticker) for ticker, symbol in wanted.items()}


class FinvizVerifier(_PerSymbolQuoteVerifier):
    """Finviz's quote page: price, change, volume and market cap for US listings."""

    name = "finviz"

    def ticker(self, row: Mapping[str, Any]) -> str | None:
        return finviz_symbol(row)

    def url(self, ticker: str) -> str:
        return f"{FINVIZ_HOST}{FINVIZ_QUOTE_PATH.format(ticker=ticker)}"

    def parse(self, body: str) -> Observation | None:
        return parse_finviz_quote(body)


class GoogleVerifier(_PerSymbolQuoteVerifier):
    """Google Finance's quote page: price, change and market cap, US listings."""

    name = "google"

    def ticker(self, row: Mapping[str, Any]) -> str | None:
        return google_symbol(row)

    def url(self, ticker: str) -> str:
        return f"{GOOGLE_HOST}{GOOGLE_QUOTE_PATH.format(ticker=ticker)}"

    def parse(self, body: str) -> Observation | None:
        return parse_google_quote(body)


def build_multi_source_verifiers(settings: Settings | None = None) -> tuple[Verifier, ...]:
    """The default verification set: Yahoo, Nasdaq, Finviz and Google.

    Yahoo (one batch request) and Nasdaq (US, per symbol) answer JSON and come
    first; Finviz and Google are page scrapes and run at
    :data:`MAX_WEB_CONCURRENCY`. ``MEE_SCREEN_HTML_VERIFY=false`` keeps the cheap
    pair only — a deployment that does not want to depend on HTML markup can say
    so without shrinking :data:`VERIFICATION_SOURCES`, so its confidence is still
    measured against the five documented sources rather than normalised up.
    ``MEE_SCREEN_MULTI_SOURCE=false`` returns no verifier at all, which makes the
    service answer the honest degraded TradingView document instead of claiming a
    consensus it never checked.
    """

    settings = settings or get_settings()
    if not settings.screen_multi_source:
        return ()
    timeout_s = settings.screen_verify_timeout_s
    verifiers: list[Verifier] = [
        YahooVerifier(
            source=YahooQuoteSource(
                batch_size=settings.quote_batch_size, timeout_s=timeout_s
            )
        ),
        NasdaqVerifier(source=NasdaqQuoteSource(timeout_s=timeout_s), timeout_s=timeout_s),
    ]
    if settings.screen_html_verify:
        concurrency = settings.screen_html_verify_concurrency
        verifiers.append(FinvizVerifier(timeout_s=timeout_s, concurrency=concurrency))
        verifiers.append(GoogleVerifier(timeout_s=timeout_s, concurrency=concurrency))
    return tuple(verifiers)


# ---------------------------------------------------------------------------
# Consensus
# ---------------------------------------------------------------------------


def selection_observation(row: Mapping[str, Any]) -> Observation:
    """The TradingView leg: the snapshot row the selection scan produced.

    This is not a second request. Re-scanning TradingView for twenty symbols
    would double the display budget to add a datapoint that is, by construction,
    the same endpoint's opinion — the candidate row *is* that opinion, and it is
    the one the ranking already used.
    """

    return Observation(
        source=SELECTION_SOURCE,
        price=_number(row.get("price")),
        change_pct=_number(row.get("change_pct")),
        volume=_number(row.get("volume")),
        market_cap_usd=_number(row.get("market_cap_usd")),
    )


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _short(text: str, limit: int = 300) -> str:
    """One provider's sentence, trimmed: an error cell is a diagnostic, not a dump."""

    collapsed = " ".join(str(text).split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def source_summary(
    observations: Sequence[Observation],
    *,
    verified_at: str,
    tolerance_pp: float = AGREEMENT_TOLERANCE_PP,
) -> dict[str, Any]:
    """How many sources stood behind a row, and how much they agreed.

    ``agreement`` is the share of **change** observations within ``tolerance_pp``
    of the median change: a source that publishes no change (Nasdaq) counts
    toward ``source_count`` but cannot agree or disagree about a number it never
    sent, so it is excluded from that ratio. ``spread_pp`` is the max−min change
    in percentage points (``None`` with fewer than two change observations — a
    spread of one value is not a measurement). ``confidence`` discounts agreement
    by coverage (``source_count / len(VERIFICATION_SOURCES)``), so three of the
    five documented sources in perfect agreement is 0.6 and four of five is 0.8 —
    never 1.0 for a page that five sources could have verified and only three did.
    """

    counted = [observation for observation in observations if observation.answered]
    changes = [
        observation.change_pct
        for observation in counted
        if observation.change_pct is not None
    ]
    spread_pp: float | None = None
    agreement: float | None = None
    confidence: float | None = None
    if changes:
        middle = statistics.median(changes)
        if len(changes) >= 2:
            spread_pp = round(max(changes) - min(changes), 4)
        agreeing = sum(1 for change in changes if abs(change - middle) <= tolerance_pp)
        agreement = round(agreeing / len(changes), 4)
        coverage = min(1.0, len(counted) / len(VERIFICATION_SOURCES))
        confidence = round(agreement * coverage, 4)
    return {
        "method": CONSENSUS_METHOD,
        "source_count": len(counted),
        "agreement": agreement,
        "spread_pp": spread_pp,
        "confidence": confidence,
        "verified_at": verified_at,
    }


def consensus_row(
    row: Mapping[str, Any],
    observations: Sequence[Observation],
    *,
    as_of: str,
    verified_at: str,
    tolerance_pp: float = AGREEMENT_TOLERANCE_PP,
) -> dict[str, Any]:
    """One stock row: one median per cell, plus the summary — never source columns.

    ``turnover``/``turnover_usd`` stay the snapshot's cells. Deriving a turnover
    from the consensus price and a five-minute-old volume would mix two clocks
    into one number, and PLAN-screen.md §8 #8 forbids inventing a USD turnover
    where FX is missing; the snapshot's own traded value is the auditable cell.
    """

    prices = [o.price for o in observations if o.price is not None]
    changes = [o.change_pct for o in observations if o.change_pct is not None]
    volumes = [o.volume for o in observations if o.volume is not None]
    caps = [o.market_cap_usd for o in observations if o.market_cap_usd is not None]
    return {
        "rank": int(row["rank"]),
        "symbol": row.get("symbol"),
        "name": row.get("name"),
        "exchange": row.get("exchange"),
        "country": row.get("country"),
        "sector": row.get("sector"),
        "price": _median(prices),
        "change_pct": _median(changes),
        "volume": _median(volumes),
        "turnover": row.get("turnover"),
        "turnover_usd": row.get("turnover_usd"),
        "market_cap_usd": _median(caps),
        "is_primary": bool(row.get("is_primary")),
        "as_of": as_of,
        "source_summary": source_summary(
            observations, verified_at=verified_at, tolerance_pp=tolerance_pp
        ),
    }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CacheKey:
    """A verified document is reusable only for this exact question."""

    region: str
    sort: str
    screen_id: str


@dataclass(frozen=True, slots=True)
class _Cached:
    expires_at: datetime
    document: dict[str, Any]


class MultiSourceScreen:
    """Serve one verified document per snapshot, cached for five minutes."""

    def __init__(
        self,
        *,
        repository: SqliteScreenRepository,
        verifiers: Sequence[Verifier] = (),
        cache_ttl_s: int = CACHE_TTL_S,
        tolerance_pp: float = AGREEMENT_TOLERANCE_PP,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if isinstance(cache_ttl_s, bool) or not isinstance(cache_ttl_s, int):
            raise TypeError(f"cache_ttl_s must be an int, got {cache_ttl_s!r}")
        if cache_ttl_s <= 0:
            raise ValueError(f"cache_ttl_s must be > 0, got {cache_ttl_s!r}")
        if tolerance_pp < 0:
            raise ValueError(f"tolerance_pp must be >= 0, got {tolerance_pp!r}")
        names: list[str] = []
        for verifier in verifiers:
            name = str(getattr(verifier, "name", "") or "").strip()
            if not name:
                raise ValueError("every verifier needs a non-empty name")
            if name in (MULTI_SOURCE, SELECTION_SOURCE):
                raise ValueError(
                    f"verifier name {name!r} is reserved; {SELECTION_SOURCE!r} is the "
                    f"selection leg and {MULTI_SOURCE!r} is the document source"
                )
            if name in names:
                raise ValueError(f"duplicate verifier name {name!r}")
            names.append(name)
        self._repository = repository
        self._verifiers: tuple[Verifier, ...] = tuple(verifiers)
        self._ttl_s = cache_ttl_s
        self._tolerance_pp = float(tolerance_pp)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cache: dict[CacheKey, _Cached] = {}
        self._locks: dict[CacheKey, asyncio.Lock] = {}

    @property
    def verifier_names(self) -> tuple[str, ...]:
        return tuple(str(verifier.name) for verifier in self._verifiers)

    # -- reads -------------------------------------------------------------

    async def load(
        self, region: str, sort: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        """The verified document for ``(region, sort)``; cache-first.

        ``load_screen`` still runs on every call: it is the cheap SQLite read
        that makes the 300 s snapshot window real (and refreshes the snapshot
        when it expired). Only the *verification* is cached, because that is the
        part that costs provider requests.
        """

        moment = now or self._clock()
        snapshot = await load_screen(
            region, sort, repository=self._repository, now=moment
        )
        key = CacheKey(
            region=region, sort=sort, screen_id=str(snapshot["screen_id"])
        )
        cached = self._fresh(key, moment)
        if cached is not None:
            return copy.deepcopy(cached)

        # One build per key: concurrent readers of the same expired window wait
        # for the first verification instead of stampeding the providers.
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._fresh(key, moment)
            if cached is not None:
                return copy.deepcopy(cached)
            document = await self._build(snapshot, moment)
            if document.get("source") == MULTI_SOURCE:
                self._cache[key] = _Cached(
                    expires_at=moment + timedelta(seconds=self._ttl_s),
                    document=document,
                )
            else:
                # A degraded answer is retried next time, not remembered.
                self._cache.pop(key, None)
            return copy.deepcopy(document)

    # -- cache -------------------------------------------------------------

    def _fresh(self, key: CacheKey, moment: datetime) -> dict[str, Any] | None:
        for cached_key, entry in list(self._cache.items()):
            if entry.expires_at <= moment:
                self._cache.pop(cached_key, None)
        # A lock is only useful while its key is cached or a build is running.
        # Pruning the empty ones keeps one lock per snapshot ever read from
        # accumulating in a long-lived process (288 a day at a five-minute TTL).
        for cached_key, lock in list(self._locks.items()):
            if cached_key not in self._cache and not lock.locked():
                self._locks.pop(cached_key, None)
        entry = self._cache.get(key)
        if entry is None or entry.expires_at <= moment:
            return None
        return entry.document

    # -- building ----------------------------------------------------------

    async def _build(
        self, snapshot: Mapping[str, Any], moment: datetime
    ) -> dict[str, Any]:
        rows = [dict(row) for row in snapshot.get("rows") or []]
        verified_at = iso(moment)
        observations, failures = await self._observe(rows)

        answered = any(
            observation is not None and observation.answered
            for answers in observations.values()
            for observation in answers.values()
        )
        if not answered:
            return self._degraded(snapshot, failures)

        as_of = str(snapshot["as_of"])
        consensus_rows = [
            self._row(row, observations, as_of=as_of, verified_at=verified_at)
            for row in rows
        ]
        return {
            **snapshot,
            "source": MULTI_SOURCE,
            "selection_source": SELECTION_SOURCE,
            "degraded": False,
            "verification_error": None,
            "returned": len(consensus_rows),
            "rows": consensus_rows,
        }

    def _row(
        self,
        row: Mapping[str, Any],
        observations: Mapping[str, Mapping[str, Observation | None]],
        *,
        as_of: str,
        verified_at: str,
    ) -> dict[str, Any]:
        symbol = str(row.get("symbol") or "").strip()
        legs = [selection_observation(row)]
        for answers in observations.values():
            observation = answers.get(symbol)
            if observation is not None and observation.answered:
                legs.append(observation)
        return consensus_row(
            row,
            legs,
            as_of=as_of,
            verified_at=verified_at,
            tolerance_pp=self._tolerance_pp,
        )

    async def _observe(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> tuple[dict[str, dict[str, Observation | None]], list[str]]:
        """Ask every verifier once; a broken source is a recorded failure."""

        if not rows:
            return {}, ["the snapshot has no rows to verify"]
        if not self._verifiers:
            return {}, [
                "no verifier is configured (MEE_SCREEN_MULTI_SOURCE is off?)"
            ]

        outcomes = await asyncio.gather(
            *(self._ask(verifier, rows) for verifier in self._verifiers),
            return_exceptions=True,
        )
        observations: dict[str, dict[str, Observation | None]] = {}
        failures: list[str] = []
        for verifier, outcome in zip(self._verifiers, outcomes, strict=True):
            name = str(verifier.name)
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            if isinstance(outcome, BaseException):
                failures.append(
                    f"{name}: {type(outcome).__name__}: {_short(str(outcome))}"
                )
                continue
            answers = dict(outcome)
            observations[name] = answers
            if not any(
                observation is not None and observation.answered
                for observation in answers.values()
            ):
                failures.append(f"{name}: answered nothing")
        return observations, failures

    @staticmethod
    async def _ask(
        verifier: Verifier, rows: Sequence[Mapping[str, Any]]
    ) -> dict[str, Observation | None]:
        answer = await verifier.observe(rows)
        if not isinstance(answer, Mapping):
            raise TypeError(
                f"verifier {verifier.name!r} must return a mapping, "
                f"got {type(answer).__name__}"
            )
        clean: dict[str, Observation | None] = {}
        for symbol, observation in answer.items():
            if observation is not None and not isinstance(observation, Observation):
                raise TypeError(
                    f"verifier {verifier.name!r} answered {symbol!r} with "
                    f"{type(observation).__name__}, expected Observation or None"
                )
            clean[str(symbol)] = observation
        return clean

    def _degraded(
        self, snapshot: Mapping[str, Any], failures: Sequence[str]
    ) -> dict[str, Any]:
        """The honest fallback: the TradingView snapshot, labelled as such."""

        document = dict(snapshot)
        # The fallback states the selection source it actually is; if a caller
        # ever seeds a snapshot from another provider, the document must not
        # relabel it as TradingView.
        source = str(snapshot.get("source") or SELECTION_SOURCE)
        document["source"] = source
        document["selection_source"] = source
        document["degraded"] = True
        document["verification_error"] = (
            "multi-source verification failed: "
            + ("; ".join(failures) if failures else "no observation was returned")
        )
        return document


__all__ = [
    "AGREEMENT_TOLERANCE_PP",
    "CACHE_TTL_S",
    "CONSENSUS_METHOD",
    "FINVIZ_HOST",
    "GOOGLE_EXCHANGE_BY_VENUE",
    "GOOGLE_HOST",
    "MAX_WEB_CONCURRENCY",
    "MULTI_SOURCE",
    "SELECTION_SOURCE",
    "US_VENUES",
    "VERIFICATION_SOURCES",
    "YAHOO_SUFFIX_BY_EXCHANGE",
    "CacheKey",
    "CallableVerifier",
    "FinvizVerifier",
    "GoogleVerifier",
    "MultiSourceScreen",
    "NasdaqVerifier",
    "Observation",
    "Verifier",
    "YahooVerifier",
    "build_multi_source_verifiers",
    "consensus_row",
    "finviz_symbol",
    "google_symbol",
    "parse_finviz_quote",
    "parse_google_quote",
    "selection_observation",
    "source_summary",
    "yahoo_symbol",
]
