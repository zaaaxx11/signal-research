"""Yahoo v7 batch quote: one request per 400 symbols, keyless.

The benchmark in ``scripts/fast_path_bench.py`` measured this endpoint as the
fast path's cheapest wide source — 100 symbols in 0.145 s, 92 keys in the
response, five of them catalog keys the benchmark's map left on the floor
(enrichment-plan probe E1). This module is that benchmark ported into a
production source: the same three-request protocol, the same row builder, and
the five new keys (``security_type``, ``exchange``, ``ipo_date``,
``earnings_date``, ``dividend_ex_date``) that step 1 of the plan promises for
zero extra requests.

**Protocol.** ``GET https://fc.yahoo.com`` sets the ``A1``/``A3`` cookies,
``GET /v1/test/getcrumb`` exchanges them for a token, and every data request
rides the same client (one cookie jar) with ``&crumb=<token>`` appended. One
handshake serves the whole pass; a 401/403 on a data request buys exactly one
fresh handshake and one retry, because a stale crumb is auth plumbing, not a
verdict about 400 symbols. The crumb endpoint throttles under aggressive use —
it answers ``200`` with the literal body ``Too Many Requests`` (probe E8) —
and a body that is not a short URL-safe token is never pasted into a query
string. Without a token there is nothing to ask with, so the pass answers
empty rather than spending 400-symbol requests on a guaranteed authwall.

**Budgets.** 400 symbols per request is the verified ceiling; requests are
paced ``PACE_S`` apart (12/12 handshakes survived 0.25 s spacing in probe E8);
batches run one at a time so the pacing is real. Nothing here reads a
credential — Yahoo's unofficial endpoints are keyless — and nothing here
raises for a provider failure: one bad batch costs its own symbols, a dead
Yahoo is an empty pass with :attr:`YahooBatchQuoteSource.last_errors` to say
why, and the symbols nobody answered simply stay absent from the result.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from itertools import batched
from typing import Any

from app.market.depth import canonical_symbol
from app.market.sources import BROWSER_USER_AGENT, QuoteSourceError, clean_symbols
from app.providers import http as provider_http

YAHOO_HOST = "https://query1.finance.yahoo.com"
YAHOO_QUOTE_PATH = "/v7/finance/quote"
YAHOO_COOKIE_URL = "https://fc.yahoo.com"
YAHOO_CRUMB_PATH = "/v1/test/getcrumb"

#: Verified ceiling for one v7 quote request (the bench and the QuoteHub both
#: measured 400; 4,217 symbols is 11 requests).
MAX_BATCH = 400

#: Minimum spacing between two batch requests. The crumb endpoint throttles
#: under aggressive use; 12 rapid handshakes at 0.25 s spacing answered 12/12
#: HTTP 200 (probe E8), so the gap is part of the protocol, not politeness.
PACE_S = 0.25

#: One v7 body is a few hundred kilobytes; 20 s read is the documented
#: provider ceiling and leaves room for the retry.
DEFAULT_TIMEOUT_S = 20.0

#: A crumb is a short URL-safe token. Anything else — a consent challenge, an
#: HTML body, the literal ``Too Many Requests`` — is not a crumb.
_CRUMB_RE = re.compile(r"^[A-Za-z0-9._~-]{1,32}$")

#: ``quoteType`` → catalog ``security_type``. The universe is 100% ``EQUITY``
#: (``stock``), so this table exists for the ETFs, funds and indices a wider
#: universe would carry; an unclassified token is lower-cased, never invented.
SECURITY_TYPES: dict[str, str] = {
    "EQUITY": "stock",
    "ETF": "etf",
    "MUTUALFUND": "fund",
    "CLOSED_END_FUND": "fund",
    "HEDGE_FUND": "fund",
    "INDEX": "index",
    "CURRENCY": "currency",
    "CRYPTOCURRENCY": "crypto",
    "FUTURE": "future",
    "OPTION": "option",
    "WARRANT": "warrant",
    "RIGHT": "right",
    "UNIT": "unit",
    "ADR": "adr",
    "REIT": "reit",
    "PREFERRED_STOCK": "preferred",
    "BOND": "bond",
    "CORPORATE_BOND": "bond",
    "MUNICIPAL_BOND": "bond",
    "TREASURY_BOND": "bond",
    "CONVERTIBLE_BOND": "bond",
}

#: v7 quote key → catalog key. The 27 keys the benchmark already mapped, plus
#: the five probe E1 found in the same response (enrichment-plan step 1).
YAHOO_BATCH_MAP: dict[str, str] = {
    "marketCap": "market_cap",
    "trailingPE": "pe",
    "forwardPE": "forward_pe",
    "priceToBook": "pb",
    "epsTrailingTwelveMonths": "eps",
    "epsForward": "eps_next_y",
    "epsCurrentYear": "eps_this_y",
    "dividendRate": "dividend",
    "dividendYield": "dividend_yield",
    "trailingAnnualDividendRate": "dividend_ttm",
    "bookValue": "book_per_share",
    "sharesOutstanding": "shares_outstanding",
    "averageDailyVolume3Month": "average_volume",
    "fiftyTwoWeekHigh": "high_52w",
    "fiftyTwoWeekLow": "low_52w",
    "fiftyTwoWeekRange": "week_52_range",
    "fiftyTwoWeekChangePercent": "perf_52w",
    "averageAnalystRating": "recommendation",
    "regularMarketPrice": "price",
    "regularMarketChange": "change",
    "regularMarketChangePercent": "change_pct",
    "regularMarketVolume": "volume",
    "regularMarketPreviousClose": "prev_close",
    "regularMarketOpen": "open",
    "regularMarketDayHigh": "high",
    "regularMarketDayLow": "low",
    "longName": "company",
    # -- the five new keys (probe E1: same response, zero extra requests) ----
    "quoteType": "security_type",
    "fullExchangeName": "exchange",
    "firstTradeDateMilliseconds": "ipo_date",
    "earningsTimestamp": "earnings_date",
    "dividendDate": "dividend_ex_date",
}


# ---------------------------------------------------------------------------
# Conversions for the five new keys
# ---------------------------------------------------------------------------


def _security_type(value: Any) -> str | None:
    """``quoteType`` → ``security_type``: ``EQUITY`` → ``stock``, etc."""
    raw = value.get("raw") if isinstance(value, Mapping) else value
    if raw is None:
        return None
    token = str(raw).strip()
    if not token:
        return None
    return SECURITY_TYPES.get(token.upper(), token.lower())


def _epoch_date(value: Any, *, millis: bool = False) -> str | None:
    """An epoch stamp → ``YYYY-MM-DD`` in UTC; a stamp that is not one → ``None``.

    ``firstTradeDateMilliseconds`` is milliseconds (``millis=True``);
    ``earningsTimestamp`` and ``dividendDate`` are seconds. A missing, zero or
    unparseable stamp leaves the catalog key absent — never a guessed date.
    """
    raw = value.get("raw") if isinstance(value, Mapping) else value
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            raw = float(text)
        except ValueError:
            return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    epoch = float(raw) / 1000.0 if millis else float(raw)
    if not math.isfinite(epoch) or epoch <= 0:
        return None
    try:
        return datetime.fromtimestamp(epoch, UTC).date().isoformat()
    except (OverflowError, OSError, ValueError):  # pragma: no cover - absurd stamp
        return None


def ipo_date(value: Any) -> str | None:
    """``firstTradeDateMilliseconds`` → ``ipo_date`` (verified: NVDA 1999-01-22)."""
    return _epoch_date(value, millis=True)


def earnings_date(value: Any) -> str | None:
    """``earningsTimestamp`` → ``earnings_date`` (verified: AAPL 2026-11-17)."""
    return _epoch_date(value)


def dividend_ex_date(value: Any) -> str | None:
    """``dividendDate`` → ``dividend_ex_date`` (verified: AAPL 2026-10-01)."""
    return _epoch_date(value)


#: The mapped keys whose value is not a pass-through. ``fullExchangeName``
#: needs no converter: the plan maps the key and keeps Yahoo's own spelling.
YAHOO_BATCH_CONVERTERS: dict[str, Callable[[Any], Any]] = {
    "quoteType": _security_type,
    "firstTradeDateMilliseconds": ipo_date,
    "earningsTimestamp": earnings_date,
    "dividendDate": dividend_ex_date,
}


def quote_row(entry: Mapping[str, Any]) -> dict[str, Any]:
    """One v7 quote → catalog-key dict: the mapped keys, then the derived ones.

    A key the quote does not carry stays absent; a converted value that does
    not parse stays absent. ``sma50``/``sma200`` are percent distance from the
    last price and ``relative_volume`` is against the 3-month average, both
    exactly as the benchmark computed them.
    """
    row: dict[str, Any] = {}
    for source, key in YAHOO_BATCH_MAP.items():
        value = entry.get(source)
        if value is None:
            continue
        convert = YAHOO_BATCH_CONVERTERS.get(source)
        if convert is not None:
            value = convert(value)
            if value is None:
                continue
        row[key] = value
    close = entry.get("regularMarketPrice")
    for source, key in (
        ("fiftyDayAverage", "sma50"),
        ("twoHundredDayAverage", "sma200"),
    ):
        average = entry.get(source)
        if close and average:
            row[key] = (close - average) / average * 100.0
    volume = entry.get("regularMarketVolume")
    average_volume = entry.get("averageDailyVolume3Month")
    if volume and average_volume:
        row["relative_volume"] = volume / average_volume
    return row


def _wanted(symbols: Sequence[str]) -> tuple[str, ...]:
    """Validated, canonical, de-duplicated symbols in the caller's order.

    The universe spells class shares ``BRK.B`` while Yahoo (and the depth
    store) spell them ``BRK-B``; canonicalising here keeps one row per share.
    """
    return tuple(
        dict.fromkeys(canonical_symbol(token) for token in clean_symbols(symbols))
    )


async def _pace(seconds: float) -> None:
    """One inter-batch gap; a seam so tests need not sleep the wall clock."""
    if seconds > 0:
        await asyncio.sleep(seconds)


# ---------------------------------------------------------------------------
# The source
# ---------------------------------------------------------------------------


class YahooBatchQuoteSource:
    """Yahoo v7 batch quotes mapped onto catalog keys — keyless, one pass.

    ``fetch_many`` answers a dict of canonical symbol → catalog-key dict for
    every symbol Yahoo answered; a symbol nobody answered is absent, never a
    row of zeroes. One cookie+crumb handshake per pass is reused across every
    batch request, a 401/403 buys one fresh handshake and one retry, and a
    batch that fails (HTTP error, broken body, timeout) costs only its own
    symbols — the rest of the pass survives. Provider failures are recorded
    in :attr:`last_errors` and never raised.
    """

    name = "yahoo_batch"

    def __init__(
        self,
        *,
        batch_size: int = MAX_BATCH,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        user_agent: str = BROWSER_USER_AGENT,
        pace_s: float = PACE_S,
    ) -> None:
        if isinstance(batch_size, bool) or not isinstance(batch_size, int):
            raise TypeError(f"batch_size must be an int, got {batch_size!r}")
        if not 1 <= batch_size <= MAX_BATCH:
            raise ValueError(f"batch_size must be 1..{MAX_BATCH}, got {batch_size!r}")
        if not timeout_s > 0:
            raise ValueError(f"timeout_s must be > 0, got {timeout_s!r}")
        if not pace_s >= 0:
            raise ValueError(f"pace_s must be >= 0, got {pace_s!r}")
        if not user_agent or not str(user_agent).strip():
            raise ValueError("user_agent must be a non-empty string")
        self.batch_size = batch_size
        self.timeout_s = float(timeout_s)
        self.user_agent = str(user_agent)
        self.pace_s = float(pace_s)
        #: Why a batch (or the handshake) owed nothing, for the most recent
        #: ``fetch_many`` call, keyed by the batch's first symbol (``"auth"``
        #: for the handshake). A clean pass leaves it empty.
        self.last_errors: dict[str, str] = {}

    # -- requests ----------------------------------------------------------

    def headers(self) -> dict[str, str]:
        """Browser-shaped headers: the Windows UA is what keeps Yahoo at 200."""
        return {
            "user-agent": self.user_agent,
            "accept": "application/json,text/plain,*/*",
            "accept-language": "en-US,en;q=0.9",
        }

    def url(self, symbols: Sequence[str], crumb: str = "") -> str:
        joined = ",".join(symbols)
        url = f"{YAHOO_HOST}{YAHOO_QUOTE_PATH}?symbols={joined}"
        return f"{url}&crumb={crumb}" if crumb else url

    async def _authorize(self, client: Any) -> str:
        """Warm the cookie jar, then exchange it for a crumb (``""`` on refusal).

        A refusal is not an exception: the throttled crumb endpoint answers
        ``200`` with the literal body ``Too Many Requests``, a consent
        challenge is not a token, and a transport that cannot reach either
        host has nothing to authenticate with. The caller decides what an
        unauthenticated pass is worth — here, nothing.
        """
        try:
            await client.get(YAHOO_COOKIE_URL)
            response = await client.get(f"{YAHOO_HOST}{YAHOO_CRUMB_PATH}")
        except Exception:  # noqa: BLE001 - auth plumbing is not the pass
            return ""
        status = int(getattr(response, "status_code", 0) or 0)
        token = str(getattr(response, "text", "") or "").strip()
        if status != 200 or not _CRUMB_RE.match(token):
            return ""
        return token

    async def _fetch_batch(
        self, client: Any, symbols: Sequence[str], crumb: str
    ) -> dict[str, dict]:
        """One batch request → canonical symbol → catalog-key row."""
        response = await client.get(self.url(symbols, crumb))
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise QuoteSourceError(f"yahoo answered {status}", status=status)
        try:
            body = response.json()
        except ValueError as exc:
            raise QuoteSourceError(f"yahoo body is not JSON: {exc}") from exc
        envelope = body.get("quoteResponse") if isinstance(body, Mapping) else None
        if not isinstance(envelope, Mapping):
            raise QuoteSourceError("yahoo body has no quoteResponse envelope")
        result = envelope.get("result")
        if result is None:
            result = []
        if not isinstance(result, list):
            raise QuoteSourceError("yahoo quoteResponse.result is not a list")
        answered: dict[str, dict] = {}
        for entry in result:
            if not isinstance(entry, Mapping):
                continue
            symbol = canonical_symbol(entry.get("symbol"))
            if not symbol:
                continue
            answered[symbol] = quote_row(entry)
        return answered

    async def fetch_many(self, symbols: Sequence[str]) -> dict[str, dict]:
        """Catalog-key rows for the symbols Yahoo answered; ``{}`` when none.

        Batches run one at a time, ``pace_s`` apart, each isolated: a failed
        batch is recorded in :attr:`last_errors` and its symbols stay absent
        while the other batches' answers survive. A 401/403 is the one
        retryable failure — one fresh handshake, then one retry of that batch,
        and the refreshed crumb is reused by the batches that follow.
        """
        wanted = _wanted(symbols)
        if not wanted:
            return {}
        self.last_errors = {}
        rows: dict[str, dict] = {}
        async with provider_http.build_client(
            timeout_s=self.timeout_s, headers=self.headers()
        ) as client:
            crumb = await self._authorize(client)
            if not crumb:
                # v7 answers 401 to an anonymous client: without a token every
                # batch request is a guaranteed authwall, so none is spent.
                self.last_errors["auth"] = "cookie/crumb handshake refused"
                return rows
            for index, group in enumerate(batched(wanted, self.batch_size)):
                if index:
                    await _pace(self.pace_s)
                try:
                    rows.update(await self._fetch_batch(client, group, crumb))
                except QuoteSourceError as exc:
                    if exc.status in (401, 403):
                        # A stale crumb is auth plumbing, not a verdict about
                        # the symbols: one fresh handshake, then one retry.
                        fresh = await self._authorize(client)
                        if fresh:
                            crumb = fresh
                            try:
                                rows.update(
                                    await self._fetch_batch(client, group, crumb)
                                )
                                continue
                            except QuoteSourceError as retried:
                                exc = retried
                    self.last_errors[group[0]] = str(exc)
        return rows


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "MAX_BATCH",
    "PACE_S",
    "SECURITY_TYPES",
    "YAHOO_BATCH_CONVERTERS",
    "YAHOO_BATCH_MAP",
    "YahooBatchQuoteSource",
    "quote_row",
]
