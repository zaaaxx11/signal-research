"""Live realtime quote sources (P8): Yahoo batch first, Nasdaq per symbol.

This is the only module that knows these two endpoints. It takes its HTTP client
from :mod:`app.providers.http` (the one seam, so ``httpx2`` stays in one file)
and returns :class:`~app.market.quote.SourceQuote` values — never a bare float.
A provider that omits a field leaves it ``None``; a provider that does not know
a symbol answers ``None``; nothing here ever reports ``0`` for "unknown".

**Yahoo** ``/v7/finance/quote`` answers up to :data:`MAX_BATCH` symbols per
request and prints ``regularMarketPrice``, ``regularMarketPreviousClose``,
``regularMarketVolume`` and ``regularMarketTime`` (epoch seconds). It answers
``401 Unauthorized`` to an anonymous client and ``429`` to a Linux Chrome UA;
the Windows UA and the ``fc.yahoo.com`` → ``/v1/test/getcrumb`` cookie dance are
part of the protocol, not cosmetics. The ``(cookies, crumb)`` pair is cached on
the source and replayed on the next poll (live: ~1.5 s → ~0.1 s), and a rejected
token buys one fresh handshake and one retry.

**Nasdaq** ``/api/quote/{symbol}/info`` is the fallback, one request per symbol,
US listings only. Its numbers are currency-formatted strings (``"$231.50"``,
``"28,960,975.244719"``) and its timestamp is a human sentence
(``"Oct 23, 2026 3:48 PM ET"``); ``isRealTime`` is the provider's own claim and
is carried through as :attr:`SourceQuote.realtime`. The endpoint wants an
``Origin``/``Referer`` pair, which :meth:`NasdaqQuoteSource.headers` sends.

A non-200, a body that is not JSON or a missing envelope raises
:class:`QuoteSourceError`: the engine records an ERROR attempt and fails over.
"Symbol not exists" is **not** an error — it is ``None`` (EMPTY), and a symbol
the whole market does not know must not read as a zero-priced quote.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from itertools import batched
from typing import Any, Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from app.config import Settings
from app.market.quote import QuoteEngine, QuoteSource, SourceQuote
from app.providers import http as provider_http

YAHOO_HOST = "https://query1.finance.yahoo.com"
YAHOO_QUOTE_PATH = "/v7/finance/quote"
YAHOO_COOKIE_URL = "https://fc.yahoo.com"
YAHOO_CRUMB_PATH = "/v1/test/getcrumb"

NASDAQ_HOST = "https://api.nasdaq.com"
NASDAQ_QUOTE_PATH = "/api/quote/{symbol}/info?assetclass=stocks"
NASDAQ_ORIGIN = "https://www.nasdaq.com"

#: Yahoo's documented ceiling for one v7 quote request. The legacy single-poll
#: path uses 20; the batch QuoteHub path has been live-tested at 400. Keeping the
#: two limits separate prevents a change to the hub from silently changing the
#: P8 watchlist contract.
MAX_BATCH = 20

#: Verified bulk ceiling for the quote hub (400 symbols, 11 requests for 4,217).
MAX_BULK_BATCH = 400

#: The Windows UA is the one Yahoo answers ``200`` to; Linux Chrome gets ``429``.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

#: Provider traffic is one small JSON body; 10 s is generous for both hosts.
DEFAULT_TIMEOUT_S = 10.0

#: What v7 is asked for. Only these columns are parsed, so only these are paid for.
YAHOO_FIELDS: tuple[str, ...] = (
    "symbol",
    "regularMarketPrice",
    "regularMarketPreviousClose",
    "regularMarketVolume",
    "regularMarketTime",
    "currency",
)

#: ``AAPL``, ``BRK-B``, ``2330.TW``, ``^GSPC``, ``EURUSD=X``: letters, digits and
#: the five separators providers actually use. Whitespace is never a symbol.
SYMBOL_RE = re.compile(r"^[A-Z0-9^][A-Z0-9.^=_-]{0,31}$")

#: Nasdaq renders Eastern time; the ET wall clock is what the string means.
_EASTERN = ZoneInfo("America/New_York")
_NASDAQ_STAMP_FORMATS = ("%b %d, %Y %I:%M %p ET", "%b %d, %Y %I:%M %p")

#: A crumb is a short URL-safe token. Anything else is a challenge page.
_CRUMB_RE = re.compile(r"^[A-Za-z0-9._~-]{1,32}$")

#: Characters that are legal in a provider symbol but must be escaped in a query.
_QUERY_ESCAPES = {"^": "%5E", "=": "%3D"}


class QuoteSourceError(RuntimeError):
    """A source could not answer at all (HTTP failure, drift, broken transport).

    ``status`` is the HTTP status when there was one; the engine records
    ``getattr(exc, "status", None)`` only through the message, so it is for
    diagnostics and retry decisions, not control flow.
    """

    def __init__(self, detail: str, *, status: int | None = None) -> None:
        super().__init__(detail)
        self.status = status



def describe_crumb_refusal(body: str) -> str:
    """A short, safe description of what the crumb endpoint answered.

    The body is quoted because the whole point is that it is prose, not a
    token; it is trimmed so a consent page cannot flood a log line.
    """

    text = " ".join(str(body or "").split())[:80]
    lowered = text.lower()
    if "too many requests" in lowered or "rate limit" in lowered or "throttl" in lowered:
        return f"throttled ('{text}')"
    if not text:
        return "empty body"
    return f"not a token ('{text}')"


class CrumbRefused(QuoteSourceError):
    """Yahoo's crumb endpoint answered without a token.

    The 2026-10 incident: a throttled ``getcrumb`` answers ``200`` with the
    literal body ``Too Many Requests``. Every parser rejected it (so the string
    never reached a query), but none said why — the batch looked like "no
    cookie" instead of "this IP is throttled", and the next retry was spent on
    the same wall. Named, so a caller can tell refusal from absence.
    """

class InvalidSymbol(ValueError):
    """A request named something that is not a provider symbol."""

    def __init__(self, token: str) -> None:
        super().__init__(f"invalid symbol {token!r}")
        self.token = token


# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------


def _check(token: str) -> str:
    if not SYMBOL_RE.match(token):
        raise InvalidSymbol(token)
    return token


def parse_symbol_list(text: str) -> tuple[str, ...]:
    """Split ``"aapl, MSFT,AAPL"`` into ``("AAPL", "MSFT")``.

    Empty input is an empty tuple (the route turns that into a 422). Empty
    entries inside a non-empty list (``"AAPL,,MSFT"`` or a trailing comma) are
    an :class:`InvalidSymbol`: a caller who typed ``AAPL,`` did not ask for a
    symbol named ``""``, and silently dropping it would hide the mistake.
    """

    if not isinstance(text, str):
        raise TypeError(f"symbols must be a string, got {type(text).__name__}")
    if not text.strip():
        return ()
    ordered: list[str] = []
    for raw in text.split(","):
        token = raw.strip().upper()
        if not token:
            raise InvalidSymbol("")
        if token not in ordered:
            ordered.append(_check(token))
    return tuple(ordered)


def clean_symbols(symbols: Iterable[str]) -> tuple[str, ...]:
    """Normalize an already-split sequence; order preserved, duplicates dropped."""

    ordered: list[str] = []
    for raw in symbols:
        if not isinstance(raw, str):
            raise InvalidSymbol(str(raw))
        token = raw.strip().upper()
        if not token:
            raise InvalidSymbol("")
        if token not in ordered:
            ordered.append(_check(token))
    return tuple(ordered)


def _encoded(symbol: str) -> str:
    return "".join(_QUERY_ESCAPES.get(character, character) for character in symbol)


# ---------------------------------------------------------------------------
# Number and timestamp parsing (both providers print strings)
# ---------------------------------------------------------------------------


def _number(value: Any) -> float | None:
    """A finite float, or ``None``. ``"$1,234.50"``/``"N/A"`` are both handled."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip().replace("$", "").replace(",", "").replace("%", "")
        if not text or text.upper() in {"N/A", "NA", "--", "-"}:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        return None
    if not math.isfinite(number):
        return None
    return number


def _positive(value: Any) -> float | None:
    number = _number(value)
    return None if number is None or number <= 0 else number


def _non_negative(value: Any) -> float | None:
    number = _number(value)
    return None if number is None or number < 0 else number


def _epoch(value: Any) -> datetime | None:
    number = _number(value)
    if number is None:
        return None
    try:
        return datetime.fromtimestamp(number, UTC)
    except (OverflowError, OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Yahoo
# ---------------------------------------------------------------------------


def parse_yahoo_quotes(
    payload: Any, symbols: Sequence[str]
) -> dict[str, SourceQuote | None]:
    """Map a ``/v7/finance/quote`` body to one answer per requested symbol.

    A symbol missing from ``result`` is ``None`` (Yahoo simply omits tickers it
    does not know). A row without a usable price or timestamp is ``None`` too:
    the next source is allowed to answer, and the absent value never becomes
    ``0``.
    """

    envelope = payload.get("quoteResponse") if isinstance(payload, Mapping) else None
    if not isinstance(envelope, Mapping):
        raise QuoteSourceError("yahoo body has no quoteResponse envelope")
    rows = envelope.get("result")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise QuoteSourceError("yahoo quoteResponse.result is not a list")

    by_symbol: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        name = str(row.get("symbol") or "").strip().upper()
        if name:
            by_symbol[name] = row

    answers: dict[str, SourceQuote | None] = {}
    for raw in symbols:
        key = str(raw).strip().upper()
        row = by_symbol.get(key)
        answers[key] = _yahoo_quote(row) if row is not None else None
    return answers


def _yahoo_quote(row: Mapping[str, Any]) -> SourceQuote | None:
    price = _positive(row.get("regularMarketPrice"))
    if price is None:
        return None
    stamp = _epoch(row.get("regularMarketTime"))
    if stamp is None:
        return None
    previous = next(
        (
            value
            for value in (
                _positive(row.get("regularMarketPreviousClose")),
                _positive(row.get("previousClose")),
                _positive(row.get("chartPreviousClose")),
            )
            if value is not None
        ),
        None,
    )
    return SourceQuote(
        last=price,
        ts_exchange=stamp,
        prev_close=previous,
        volume=_non_negative(row.get("regularMarketVolume")),
    )


class YahooQuoteSource:
    """Batch quotes from Yahoo's unofficial v7 endpoint.

    The P8 watchlist construction uses 20 symbols per request; the QuoteHub
    construction uses 400 (verified live). The ceiling is a constant, not a
    guess, and a caller cannot exceed it silently.
    """

    name = "yahoo"

    def __init__(
        self,
        *,
        batch_size: int = MAX_BATCH,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        user_agent: str = BROWSER_USER_AGENT,
        bulk: bool = False,
    ) -> None:
        if isinstance(batch_size, bool) or not isinstance(batch_size, int):
            raise TypeError(f"batch_size must be an int, got {batch_size!r}")
        ceiling = MAX_BULK_BATCH if bulk else MAX_BATCH
        if not 1 <= batch_size <= ceiling:
            raise ValueError(f"batch_size must be 1..{ceiling}, got {batch_size!r}")
        if not timeout_s > 0:
            raise ValueError(f"timeout_s must be > 0, got {timeout_s!r}")
        if not user_agent or not str(user_agent).strip():
            raise ValueError("user_agent must be a non-empty string")
        self.batch_size = batch_size
        self.timeout_s = float(timeout_s)
        self.user_agent = str(user_agent)
        #: ``(cookies, crumb)`` from the last successful handshake. Yahoo accepts
        #: the pair on a brand-new client, which saves two round-trips per poll
        #: (~1.5 s → ~0.1 s live). A rejected token falls back to a fresh
        #: handshake, so a stale cache costs one retry, never a failed quote.
        self._auth: tuple[dict[str, str], str] | None = None

    # -- requests ----------------------------------------------------------

    def headers(self) -> dict[str, str]:
        return {
            "user-agent": self.user_agent,
            "accept": "application/json,text/plain,*/*",
            "accept-language": "en-US,en;q=0.9",
        }

    def url(self, symbols: Sequence[str], crumb: str = "") -> str:
        joined = ",".join(_encoded(symbol) for symbol in symbols)
        url = (
            f"{YAHOO_HOST}{YAHOO_QUOTE_PATH}?symbols={joined}"
            f"&fields={','.join(YAHOO_FIELDS)}"
        )
        return f"{url}&crumb={crumb}" if crumb else url

    async def _crumb(self, client: Any) -> str:
        """Warm the cookie jar, then exchange it for a crumb (``""`` on refusal).

        A refusal is not fatal: the data request will answer ``401`` on its own
        and the caller retries once with a fresh handshake before giving up.
        """

        try:
            await client.get(YAHOO_COOKIE_URL)
            response = await client.get(f"{YAHOO_HOST}{YAHOO_CRUMB_PATH}")
        except Exception as exc:  # noqa: BLE001 - warm-up must not become the error
            raise QuoteSourceError(
                f"yahoo crumb warm-up failed: {type(exc).__name__}: {exc}"
            ) from exc
        token = str(getattr(response, "text", "") or "").strip()
        if _CRUMB_RE.match(token):
            return token
        raise CrumbRefused(
            f"yahoo crumb endpoint refused the handshake: {describe_crumb_refusal(token)}"
        )

    async def _authorize(self, client: Any) -> str:
        """Run the handshake and remember ``(cookies, crumb)`` for the next poll."""

        try:
            crumb = await self._crumb(client)
        except CrumbRefused as exc:
            logger.warning("yahoo quote source running unauthenticated: %s", exc)
            crumb = ""
        if crumb:
            self._auth = (dict(client.cookies), crumb)
        return crumb

    async def _fetch_chunk(
        self, client: Any, symbols: Sequence[str], crumb: str
    ) -> dict[str, SourceQuote | None]:
        url = self.url(symbols, crumb)
        response = await client.get(url)
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise QuoteSourceError(f"yahoo answered {status}", status=status)
        try:
            payload = response.json()
        except ValueError as exc:
            raise QuoteSourceError(f"yahoo body is not JSON: {exc}") from exc
        return parse_yahoo_quotes(payload, symbols)

    async def fetch_many(self, symbols: Sequence[str]) -> dict[str, SourceQuote | None]:
        """One answer per symbol, in chunks of at most ``batch_size``.

        A chunk that fails does not discard the chunks that succeeded: those
        symbols answer ``None`` and the engine falls back per symbol. Only when
        **every** chunk failed does the whole source fail over — one bad symbol
        must not turn a twenty-symbol request into twenty Nasdaq requests, but a
        dead source must not look like an empty one either.
        """

        wanted = clean_symbols(symbols)
        if not wanted:
            return {}

        answers: dict[str, SourceQuote | None] = {}
        failures: list[QuoteSourceError] = []
        async with provider_http.build_client(
            timeout_s=self.timeout_s, headers=self.headers()
        ) as client:
            crumb = ""
            if self._auth is not None:
                cookies, crumb = self._auth
                client.cookies.update(cookies)
            else:
                crumb = await self._authorize(client)
            for chunk in batched(wanted, self.batch_size):
                try:
                    answers.update(await self._fetch_chunk(client, chunk, crumb))
                except QuoteSourceError as exc:
                    if exc.status in (401, 403) and crumb:
                        # A stale crumb is auth plumbing, not a verdict about the
                        # symbols: one fresh handshake, then one retry.
                        crumb = await self._authorize(client)
                        try:
                            answers.update(await self._fetch_chunk(client, chunk, crumb))
                            continue
                        except QuoteSourceError as retried:
                            failures.append(retried)
                            continue
                    failures.append(exc)

        if failures and not answers:
            first = failures[0]
            raise QuoteSourceError(
                f"yahoo failed for {len(wanted)} symbol(s) in {len(failures)} "
                f"request(s): {first}",
                status=first.status,
            ) from first
        return {key: answers.get(key) for key in wanted}


# ---------------------------------------------------------------------------
# Nasdaq
# ---------------------------------------------------------------------------


def parse_nasdaq_quote(payload: Any) -> SourceQuote | None:
    """One ``/api/quote`` body → one answer (``None`` when Nasdaq has no data)."""

    if not isinstance(payload, Mapping):
        raise QuoteSourceError("nasdaq body is not a JSON object")
    data = payload.get("data")
    if data is None:
        return None  # "Symbol not exists." — an answer, not an error
    if not isinstance(data, Mapping):
        raise QuoteSourceError("nasdaq data is not an object")
    primary = data.get("primaryData")
    if primary is None:
        return None
    if not isinstance(primary, Mapping):
        raise QuoteSourceError("nasdaq data.primaryData is not an object")

    price = _positive(primary.get("lastSalePrice"))
    if price is None:
        return None
    stamp = _nasdaq_stamp(primary.get("lastTradeTimestamp"))
    if stamp is None:
        # A quote without its own clock may not exist (02 §4.6); saying "now"
        # would make a delayed print look realtime.
        raise QuoteSourceError("nasdaq lastTradeTimestamp is unparseable")
    realtime = primary.get("isRealTime")
    return SourceQuote(
        last=price,
        ts_exchange=stamp,
        volume=_non_negative(primary.get("volume")),
        realtime=realtime if isinstance(realtime, bool) else None,
    )


def _nasdaq_stamp(value: Any) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _epoch(value)
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in _NASDAQ_STAMP_FORMATS:
        try:
            naive = datetime.strptime(text, pattern)
        except ValueError:
            continue
        return naive.replace(tzinfo=_EASTERN).astimezone(UTC)
    return None


class NasdaqQuoteSource:
    """Per-symbol fallback over Nasdaq's unofficial ``/api/quote`` endpoint."""

    name = "nasdaq"

    def __init__(
        self,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        user_agent: str = BROWSER_USER_AGENT,
    ) -> None:
        if not timeout_s > 0:
            raise ValueError(f"timeout_s must be > 0, got {timeout_s!r}")
        if not user_agent or not str(user_agent).strip():
            raise ValueError("user_agent must be a non-empty string")
        self.timeout_s = float(timeout_s)
        self.user_agent = str(user_agent)

    def headers(self) -> dict[str, str]:
        return {
            "user-agent": self.user_agent,
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9",
            "origin": NASDAQ_ORIGIN,
            "referer": f"{NASDAQ_ORIGIN}/",
        }

    def url(self, symbol: str) -> str:
        return f"{NASDAQ_HOST}{NASDAQ_QUOTE_PATH.format(symbol=_encoded(symbol))}"

    async def fetch(self, symbol: str) -> SourceQuote | None:
        key = _check(str(symbol).strip().upper())
        async with provider_http.build_client(
            timeout_s=self.timeout_s, headers=self.headers()
        ) as client:
            response = await client.get(self.url(key))
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise QuoteSourceError(f"nasdaq answered {status}", status=status)
        try:
            payload = response.json()
        except ValueError as exc:
            raise QuoteSourceError(f"nasdaq body is not JSON: {exc}") from exc
        return parse_nasdaq_quote(payload)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def build_quote_engine(
    settings: Settings, *, clock: Callable[[], datetime] | None = None
) -> QuoteEngine:
    """The production engine: Yahoo batch first, Nasdaq per-symbol fallback.

    ``QuoteSource("yahoo", fetch_many=...)`` deliberately has no ``fetch``: a
    sync ``poll`` records a clear ERROR for it instead of one HTTP request per
    symbol, and the route uses the batch path, which is what makes a hundred
    symbols five requests rather than a hundred.
    """

    yahoo = YahooQuoteSource(
        batch_size=settings.quote_batch_size, timeout_s=settings.quote_timeout_s
    )
    nasdaq = NasdaqQuoteSource(timeout_s=settings.quote_timeout_s)
    return QuoteEngine(
        [
            # ``accept_stale``: a closed market's last print is the answer.
            # Without it, every stale Yahoo row would cost one Nasdaq request
            # (~2 s, US-only, delayed) that can never be fresher.
            QuoteSource("yahoo", fetch_many=yahoo.fetch_many, accept_stale=True),
            QuoteSource("nasdaq", nasdaq.fetch),
        ],
        stale_after_s=settings.quote_stale_after_s,
        clock=clock,
    )


__all__ = [
    "BROWSER_USER_AGENT",
    "DEFAULT_TIMEOUT_S",
    "MAX_BATCH",
    "MAX_BULK_BATCH",
    "NASDAQ_HOST",
    "SYMBOL_RE",
    "YAHOO_FIELDS",
    "YAHOO_HOST",
    "InvalidSymbol",
    "NasdaqQuoteSource",
    "QuoteSourceError",
    "YahooQuoteSource",
    "build_quote_engine",
    "clean_symbols",
    "parse_nasdaq_quote",
    "parse_symbol_list",
    "parse_yahoo_quotes",
]
