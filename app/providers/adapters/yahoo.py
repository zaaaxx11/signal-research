"""Yahoo Finance — four capabilities on three unofficial endpoints.

``/v8/finance/chart`` (QUOTE, EOD_BARS), ``/v10/finance/quoteSummary``
(FUNDAMENTALS) and ``/v7/finance/options`` (OPTIONS). The endpoints answer
``200`` with three different bodies:

* a complete payload (a quote, a bar series, a chain);
* ``{"chart": {"result": null, "error": null}}`` for "nothing here" (``empty``);
* a consent/challenge HTML page (``blocked``, see ``_support``).

It also changes shape without changing the status code — ``meta`` losing
``regularMarketPrice`` is the canonical ``schema_drift`` of this project, and it
must never be read as "a quote of zero".

**Request budget.** The ``meta`` block carries the day's OHLC and the timestamp,
so a QUOTE is one request. EOD asks for five years of daily bars in the same
single request, because ``ma_200`` and ``ret_5y`` cannot be computed from a
one-day range. ``quoteSummary`` is one request after a cookie/crumb warm-up;
the options ladder is two (the expiry list, then the dated chain — without
``date`` Yahoo only ever answers the *nearest* chain, so a ~30 DTE expiry is
unreachable in one call).

**Auth.** ``quoteSummary`` and ``options`` answer ``401 Invalid Crumb`` to an
anonymous client. The dance is: GET ``fc.yahoo.com`` (the response sets the
``A1``/``A3`` cookies), then GET ``/v1/test/getcrumb`` for the token, then
append ``&crumb=<token>``. The warm-up rides the same client as the data
request so the cookie jar is shared; it never counts as a second *body*
request, and it is skipped for the chart endpoints, which need no crumb.

**Units.** Prices are ``price:<ISO-4217>``; returns, ``rsi_14``, ``roe``,
``dividend_yield``, ``payout_ratio`` and ``volatility_20d`` are percent points;
``opt_iv_atm`` is a **fraction** (V7); Greeks are computed here (Yahoo does not
publish them) with the V8 signs of a long call.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Mapping, Sequence

from app.contracts import Capability
from app.providers import http as provider_http
from app.providers.adapters._support import (
    _MISSING,
    HttpAdapter,
    as_float,
    envelope_verdict,
    json_of,
    observation,
    price_unit,
)
from app.providers.base import InstrumentRef, ProviderError, Verdict
from app.providers.proxy_pool import FREE, PAID, ProxyPool

HOST = "https://query1.finance.yahoo.com"
CHART_PATH = "/v8/finance/chart/{symbol}"
QUOTE_SUMMARY_PATH = "/v10/finance/quoteSummary/{symbol}"
OPTIONS_PATH = "/v7/finance/options/{symbol}"

#: Cookie warm-up + crumb exchange (01-providers §Yahoo note 2). The host that
#: sets the cookie is not the API host, which is why both are named here.
COOKIE_URL = "https://fc.yahoo.com"
CRUMB_URL = f"{HOST}/v1/test/getcrumb"

#: Query strings, one per chart capability. EOD needs the range that can fill
#: ``ma_200`` and ``ret_5y``; the quote rung keeps its intraday request.
QUOTE_QUERY = "interval=1m&range=1d&includePrePost=false&events=div%2Csplit"
EOD_QUERY = "interval=1d&range=5y&includePrePost=false&events=div%2Csplit"

#: The four modules the 22 fundamentals/statements columns are read from.
QUOTE_SUMMARY_MODULES: tuple[str, ...] = (
    "price",
    "summaryDetail",
    "defaultKeyStatistics",
    "financialData",
)

#: Yahoo answers ``429`` to a Linux Chrome UA and ``200`` to the identical
#: Windows UA (01-providers §Yahoo note 1). The string is part of the protocol.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

#: The documented ~30 DTE target for the ATM option block.
OPTIONS_DTE_TARGET = 30
TRADING_DAYS = 252.0

#: Fields this adapter can fill from the ``meta`` block. Declared so the ladder
#: can skip a rung that cannot serve the asked field set.
META_FIELDS: frozenset[str] = frozenset(
    {
        "last_price",
        "prev_close",
        "day_open",
        "day_high",
        "day_low",
        "day_volume",
        "quote_as_of",
        "currency",
    }
)

#: ``column -> (module, key, transform)``. ``percent`` multiplies by 100: Yahoo
#: reports ``dividendYield``/``payoutRatio``/``returnOnEquity`` as fractions and
#: the contract stores percent points (V6).
FUNDAMENTAL_SOURCES: dict[str, tuple[str, str, str]] = {
    "market_cap": ("price", "marketCap", "number"),
    "market_cap_usd": ("price", "marketCap", "number"),
    "shares_outstanding": ("defaultKeyStatistics", "sharesOutstanding", "number"),
    "float_shares": ("defaultKeyStatistics", "floatShares", "number"),
    "pe_ttm": ("summaryDetail", "trailingPE", "number"),
    "pe_forward": ("summaryDetail", "forwardPE", "number"),
    "peg": ("defaultKeyStatistics", "pegRatio", "number"),
    "pb": ("defaultKeyStatistics", "priceToBook", "number"),
    "ps": ("summaryDetail", "priceToSalesTrailing12Months", "number"),
    "ev_ebitda": ("defaultKeyStatistics", "enterpriseToEbitda", "number"),
    "eps_ttm": ("defaultKeyStatistics", "trailingEps", "number"),
    "eps_forward": ("defaultKeyStatistics", "forwardEps", "number"),
    "dividend_yield": ("summaryDetail", "dividendYield", "percent"),
    "payout_ratio": ("summaryDetail", "payoutRatio", "percent"),
    "revenue_ttm": ("financialData", "totalRevenue", "number"),
    "ebitda_ttm": ("financialData", "ebitda", "number"),
    "net_income_ttm": ("financialData", "netIncomeToCommon", "number"),
    "fcf_ttm": ("financialData", "freeCashflow", "number"),
    "total_debt": ("financialData", "totalDebt", "number"),
    "cash_and_equivalents": ("financialData", "totalCash", "number"),
    "roe": ("financialData", "returnOnEquity", "percent"),
    "debt_to_equity": ("financialData", "debtToEquity", "number"),
}

#: Money columns are amounts in the quote currency; the rest are scalar classes.
_MONEY_COLUMNS = frozenset(
    {
        "market_cap",
        "market_cap_usd",
        "revenue_ttm",
        "ebitda_ttm",
        "net_income_ttm",
        "fcf_ttm",
        "total_debt",
        "cash_and_equivalents",
    }
)
_PERCENT_COLUMNS = frozenset({"dividend_yield", "payout_ratio", "roe"})
_SHARE_COLUMNS = frozenset({"shares_outstanding", "float_shares"})

#: Where a column may live when its primary module omits it. Yahoo moves fields
#: between modules without notice; these are the *same* provider's same metric,
#: never a derived or invented number. ``financialData.netIncomeToCommon`` is
#: currently absent for AAPL while ``defaultKeyStatistics`` still carries it.
FUNDAMENTAL_FALLBACKS: dict[str, tuple[tuple[str, str], ...]] = {
    "net_income_ttm": (("defaultKeyStatistics", "netIncomeToCommon"),),
}


# ---------------------------------------------------------------------------
# Bars and derived statistics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Bar:
    """One surviving daily bar: the unit every EOD number is computed from."""

    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


def _chart_entry(payload: Any) -> Mapping[str, Any] | None:
    """``chart.result[0]`` when it is an object, else ``None``."""

    if not isinstance(payload, Mapping):
        return None
    chart = payload.get("chart")
    if not isinstance(chart, Mapping):
        return None
    result = chart.get("result")
    if isinstance(result, list) and result and isinstance(result[0], Mapping):
        return result[0]
    return None


def _bars(entry: Mapping[str, Any]) -> list[_Bar]:
    """The bars to keep: complete OHLC, ``low <= min(o,c) <= max(o,c) <= high``.

    V2: a bar that violates the envelope (or reports a non-positive close) is
    dropped, not repaired — ``bars_count`` follows the drop. A null volume is
    ``0`` volume, never a missing bar.
    """

    stamps = entry.get("timestamp") or []
    blocks = (entry.get("indicators") or {}).get("quote") or []
    block = blocks[0] if blocks and isinstance(blocks[0], Mapping) else {}
    opens = block.get("open") or []
    highs = block.get("high") or []
    lows = block.get("low") or []
    closes = block.get("close") or []
    volumes = block.get("volume") or []

    kept: list[_Bar] = []
    for index, stamp in enumerate(stamps):
        try:
            raw = (opens[index], highs[index], lows[index], closes[index])
        except IndexError:  # pragma: no cover - a truncated body is provider drift
            continue
        values = [as_float(item) for item in raw]
        if any(value is None for value in values):
            continue
        open_, high, low, close = (float(value) for value in values if value is not None)
        if not (low <= min(open_, close) and max(open_, close) <= high) or close <= 0:
            continue
        volume = as_float(volumes[index]) if index < len(volumes) else None
        try:
            moment = datetime.fromtimestamp(int(stamp), UTC)
        except (TypeError, ValueError, OSError):  # pragma: no cover - malformed stamp
            continue
        kept.append(
            _Bar(
                day=moment.date(),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=float(volume) if volume is not None else 0.0,
            )
        )
    return kept


def _sma(values: Sequence[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _ema(values: Sequence[float], window: int) -> float | None:
    """Seed with the SMA of the first window, then ``alpha = 2 / (N + 1)``."""

    if len(values) < window:
        return None
    alpha = 2.0 / (window + 1.0)
    ema = sum(values[:window]) / window
    for value in values[window:]:
        ema = value * alpha + ema * (1.0 - alpha)
    return ema


def _rsi(closes: Sequence[float], window: int = 14) -> float | None:
    """Wilder's RSI: seeded with the mean of the first window of changes."""

    if len(closes) < window + 1:
        return None
    changes = [cur - prev for prev, cur in zip(closes[:-1], closes[1:])]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = sum(gains[:window]) / window
    avg_loss = sum(losses[:window]) / window
    for gain, loss in zip(gains[window:], losses[window:]):
        avg_gain = (avg_gain * (window - 1) + gain) / window
        avg_loss = (avg_loss * (window - 1) + loss) / window
    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def _atr(series: Sequence[_Bar], window: int = 14) -> float | None:
    """Wilder's ATR over the true range, seeded with the first window's mean."""

    if len(series) < window + 1:
        return None
    ranges = [
        max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close))
        for prev, cur in zip(series[:-1], series[1:])
    ]
    atr = sum(ranges[:window]) / window
    for value in ranges[window:]:
        atr = (atr * (window - 1) + value) / window
    return atr


def _volatility(closes: Sequence[float], window: int = 20) -> float | None:
    """Population stdev of the last ``window`` log returns, annualised, in %."""

    if len(closes) < window + 1:
        return None
    returns = [
        math.log(cur / prev) for prev, cur in zip(closes[-window - 1 : -1], closes[-window:])
    ]
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / len(returns)
    return math.sqrt(variance) * math.sqrt(TRADING_DAYS) * 100.0


def _shift_months(moment: date, months: int) -> date:
    """Calendar month arithmetic, clamped to the target month's last day."""

    import calendar

    year = moment.year + (moment.month - 1 + months) // 12
    month = (moment.month - 1 + months) % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _anchor(series: Sequence[_Bar], target: date, *, tolerance_days: int = 10) -> _Bar | None:
    """Last bar at or before ``target``; the first bar only across a small gap.

    A 5-year chart starts a few days *after* the 60-month anchor, so ``ret_5y``
    would be empty for a purely calendar-based rule. The tolerance is a
    weekend/holiday gap, not "shorter history": anything wider stays ``None``.
    """

    earlier = [bar for bar in series if bar.day <= target]
    if earlier:
        return earlier[-1]
    first = series[0]
    if (first.day - target).days <= tolerance_days:
        return first
    return None


def _return_pct(series: Sequence[_Bar], target: date) -> float | None:
    anchor = _anchor(series, target)
    if anchor is None or anchor.close == 0:
        return None
    return (series[-1].close / anchor.close - 1.0) * 100.0


# ---------------------------------------------------------------------------
# Option pricing (Yahoo publishes no Greeks)
# ---------------------------------------------------------------------------


def _norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _norm_pdf(value: float) -> float:
    return math.exp(-0.5 * value * value) / math.sqrt(2.0 * math.pi)


def black_scholes_call(
    spot: float, strike: float, years: float, vol: float, rate: float = 0.0
) -> dict[str, float]:
    """Delta/Gamma/Vega/Theta of a long call (``T`` in years, ``vol`` a fraction).

    Convention (not a provider number, because Yahoo has none): ``T = dte/365``,
    ``r = 0``, vega per 1 volatility point, theta per calendar day. Only the
    signs and finiteness are contractual (V8); the model itself is free.
    """

    if spot <= 0 or strike <= 0 or years <= 0 or vol <= 0:
        return {}
    sqrt_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * years) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    delta = _norm_cdf(d1)
    gamma = _norm_pdf(d1) / (spot * vol * sqrt_t)
    vega = spot * _norm_pdf(d1) * sqrt_t / 100.0
    theta = (
        -spot * _norm_pdf(d1) * vol / (2.0 * sqrt_t)
        - rate * strike * math.exp(-rate * years) * _norm_cdf(d2)
    ) / 365.0
    values = {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta}
    if not all(math.isfinite(value) for value in values.values()):
        return {}
    return values


def _option_chain(payload: Any) -> Mapping[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    chain = payload.get("optionChain")
    if not isinstance(chain, Mapping):
        return None
    result = chain.get("result")
    if isinstance(result, list) and result and isinstance(result[0], Mapping):
        return result[0]
    return None


def _chain_clock(entry: Mapping[str, Any]) -> tuple[float | None, date]:
    """``(spot, session day)`` from the chain's own quote.

    DTE is measured from ``quote.regularMarketTime`` (the data's clock), never
    from the wall clock: a replay of a Friday chain on a Monday must not silently
    gain two days of decay.
    """

    quote = entry.get("quote") or {}
    spot = as_float(quote.get("regularMarketPrice"))
    stamp = quote.get("regularMarketTime")
    today = datetime.now(UTC).date()
    if isinstance(stamp, (int, float, str)):
        try:
            today = datetime.fromtimestamp(int(stamp), UTC).date()
        except (TypeError, ValueError, OSError):  # pragma: no cover - malformed stamp
            pass
    return spot, today


def _expiry_target(payload: Any) -> int | None:
    """The expiry epoch nearest :data:`OPTIONS_DTE_TARGET`, from the chain list."""

    entry = _option_chain(payload)
    if entry is None:
        return None
    expiries = [int(value) for value in (entry.get("expirationDates") or []) if value]
    if not expiries:
        return None
    _spot, today = _chain_clock(entry)
    return min(
        expiries,
        key=lambda value: abs(
            (datetime.fromtimestamp(value, UTC).date() - today).days - OPTIONS_DTE_TARGET
        ),
    )


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


class YahooAdapter(HttpAdapter):
    """Unofficial but free; trust 3 (aggregator), delay "realtime-ish"."""

    name = "yahoo"
    trust = 3
    capabilities = frozenset(
        {
            Capability.QUOTE,
            Capability.EOD_BARS,
            Capability.FUNDAMENTALS,
            Capability.OPTIONS,
        }
    )
    accept = "application/json,text/plain,*/*"

    def __init__(
        self,
        *,
        timeout_s: float = 20.0,
        proxy_pool: ProxyPool | None = None,
        proxy_attempts: int = 3,
        direct_cooldown_s: float = 600.0,
    ) -> None:
        super().__init__(timeout_s=timeout_s)
        self.proxy_pool = proxy_pool
        self.proxy_attempts = max(1, int(proxy_attempts))
        self.direct_cooldown_s = max(0.0, float(direct_cooldown_s))
        #: A direct 401/429 is a property of this egress, not this symbol. Once
        #: Yahoo refuses the direct wire, every later bulk symbol should start
        #: on the proxy rung instead of spending one blocked request each.
        self._direct_blocked_until = 0.0

    # -- requests ----------------------------------------------------------

    @staticmethod
    def _symbol(instrument: InstrumentRef) -> str:
        return str(instrument.symbol).replace(" ", "%20")

    def url(self, instrument: InstrumentRef) -> str:
        """The QUOTE URL (the rung this adapter was born as)."""

        return f"{HOST}{CHART_PATH.format(symbol=self._symbol(instrument))}?{QUOTE_QUERY}"

    def _url(self, capability: Capability, instrument: InstrumentRef) -> str:
        if capability is Capability.EOD_BARS:
            return f"{HOST}{CHART_PATH.format(symbol=self._symbol(instrument))}?{EOD_QUERY}"
        return self.url(instrument)

    def request_headers(self, instrument: InstrumentRef) -> dict[str, str]:
        """Browser-shaped headers: the Windows UA is what keeps Yahoo at 200."""

        headers = super().request_headers(instrument)
        headers["user-agent"] = BROWSER_USER_AGENT
        headers["accept-language"] = "en-US,en;q=0.9"
        return headers

    async def fetch(
        self, capability: Capability, instrument: InstrumentRef, fields: set[str]
    ) -> dict[str, Any]:
        """One *body* request per capability; the crumb dance is transport only."""

        if capability not in self.capabilities:
            raise ProviderError(
                Verdict(
                    ok=False,
                    kind="schema_drift",
                    detail=f"{self.name} does not implement {capability.value}",
                )
            )
        headers = self.request_headers(instrument)
        if capability is Capability.FUNDAMENTALS:
            return await self._fetch_fundamentals_with_egress(instrument, headers)
        async with provider_http.build_client(
            timeout_s=self.timeout_s, headers=headers
        ) as client:
            if capability is Capability.OPTIONS:
                return await self._fetch_options(client, instrument)
            url = self._url(capability, instrument)
            return await self._get(client, url, capability)

    def _egress_candidates(self) -> list[str | None]:
        """Direct (unless cooling), free attempts, then paid — zcrapers order."""

        candidates: list[str | None] = []
        if time.monotonic() >= self._direct_blocked_until:
            candidates.append(None)
        pool = self.proxy_pool
        if pool is not None:
            seen: set[str] = set()
            for _ in range(self.proxy_attempts):
                proxy = pool.acquire(FREE)
                if proxy and proxy not in seen:
                    seen.add(proxy)
                    candidates.append(proxy)
            if pool.available(PAID):
                proxy = pool.acquire(PAID)
                if proxy and proxy not in seen:
                    candidates.append(proxy)
        if not candidates:
            # No proxy configured and direct is cooling: still return the real
            # direct refusal instead of inventing an empty body.
            candidates.append(None)
        return candidates

    async def _fetch_fundamentals_with_egress(
        self, instrument: InstrumentRef, headers: dict[str, str]
    ) -> dict[str, Any]:
        """Try the egress ladder; the last refusal is the answer if all fail.

        The provider ladder above this sees one raw response, so a free-proxy
        success and a direct success are both ``yahoo`` observations. The proxy
        is transport, not a second source.
        """

        last: dict[str, Any] | None = None
        for proxy in self._egress_candidates():
            try:
                async with provider_http.build_client(
                    timeout_s=self.timeout_s,
                    headers=headers,
                    proxy=proxy,
                ) as client:
                    candidate = await self._fetch_fundamentals(client, instrument)
            except Exception as exc:  # noqa: BLE001 - one bad proxy must not stop the walk
                if self.proxy_pool is not None and proxy is not None:
                    self.proxy_pool.report_failure(proxy, FREE)
                last = {
                    "status": -1,
                    "headers": {},
                    "body": f"{type(exc).__name__}: {exc}".encode("utf-8"),
                    "url": f"{HOST}{QUOTE_SUMMARY_PATH.format(symbol=self._symbol(instrument))}",
                    "capability": Capability.FUNDAMENTALS.value,
                }
                continue

            status = int(candidate.get("status") or 0)
            if status == 200:
                if self.proxy_pool is not None and proxy is not None:
                    self.proxy_pool.report_success(proxy, FREE)
                return candidate

            last = candidate
            if proxy is None:
                if status in (401, 403, 429, 451):
                    self._direct_blocked_until = time.monotonic() + self.direct_cooldown_s
                continue
            if self.proxy_pool is not None:
                self.proxy_pool.report_failure(proxy, FREE)

        return last or {
            "status": -1,
            "headers": {},
            "body": b"no egress candidate",
            "url": f"{HOST}{QUOTE_SUMMARY_PATH.format(symbol=self._symbol(instrument))}",
            "capability": Capability.FUNDAMENTALS.value,
        }

    async def _get(self, client: Any, url: str, capability: Capability) -> dict[str, Any]:
        response = await client.get(url)
        return {
            "status": int(response.status_code),
            "headers": {
                str(key).lower(): str(value) for key, value in response.headers.items()
            },
            "body": response.content,
            "url": url,
            "capability": capability.value,
        }

    async def _crumb(self, client: Any) -> str:
        """Warm the cookie jar, then exchange it for a crumb (``""`` on refusal).

        A refusal is not an error here: the chart endpoints never need the token,
        and the data request will answer ``401`` on its own if Yahoo insists.
        The token guard keeps a challenge page or an HTML body from being pasted
        into a query string. Yahoo occasionally drops the cookie handshake
        (observed live as a one-off ``200`` with no crumb), so the exchange is
        attempted twice before giving up.
        """

        for _attempt in range(2):
            try:
                await client.get(COOKIE_URL)
                response = await client.get(CRUMB_URL)
            except Exception:  # noqa: BLE001 - auth warm-up must never fail the rung
                continue
            if int(getattr(response, "status_code", 0) or 0) != 200:
                continue
            token = str(getattr(response, "text", "") or "").strip()
            if not token or len(token) > 32:
                continue
            # A crumb is a short URL-safe token. Anything else (a challenge page,
            # an HTML body, a JSON error) is not a crumb and must not reach the
            # query string; the adapters may not import an URL encoder to "fix" it.
            if not all(character.isalnum() or character in "._-~" for character in token):
                continue
            return token
        return ""

    @staticmethod
    def _with_crumb(url: str, crumb: str) -> str:
        if not crumb:
            return url
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}crumb={crumb}"

    async def _fetch_fundamentals(
        self, client: Any, instrument: InstrumentRef
    ) -> dict[str, Any]:
        crumb = await self._crumb(client)
        url = (
            f"{HOST}{QUOTE_SUMMARY_PATH.format(symbol=self._symbol(instrument))}"
            f"?modules={','.join(QUOTE_SUMMARY_MODULES)}"
            "&corsDomain=finance.yahoo.com&formatted=false"
        )
        first = await self._get(
            client, self._with_crumb(url, crumb), Capability.FUNDAMENTALS
        )
        if int(first.get("status") or 0) in (401, 403):
            # Yahoo occasionally drops the cookie/crumb pair between the warm-up
            # and the data request (observed as a per-symbol authwall even at
            # concurrency 1). A stale crumb is auth plumbing, not a verdict about
            # the symbol: one fresh handshake is worth the retry. The bulk
            # snapshot treats a second 401 as a failed build, never as zero cells.
            crumb = await self._crumb(client)
            if crumb:
                first = await self._get(
                    client, self._with_crumb(url, crumb), Capability.FUNDAMENTALS
                )
        return first

    async def _fetch_options(self, client: Any, instrument: InstrumentRef) -> dict[str, Any]:
        """Expiry list first, then the dated chain (the only way to reach ~30 DTE)."""

        crumb = await self._crumb(client)
        base = f"{HOST}{OPTIONS_PATH.format(symbol=self._symbol(instrument))}"
        first = await self._get(client, self._with_crumb(base, crumb), Capability.OPTIONS)
        if int(first.get("status") or 0) in (401, 403):
            # A stale/absent crumb is worth one more handshake: the chain is the
            # only OPTIONS rung, and 401 here is auth plumbing, not a verdict
            # about the symbol. A test double answers 200, so this never fires
            # in the suite.
            crumb = await self._crumb(client)
            if crumb:
                first = await self._get(
                    client, self._with_crumb(base, crumb), Capability.OPTIONS
                )
        chosen = _expiry_target(json_of(first))
        if chosen is None:
            # No expiry list (authwall/empty): the first body is the answer, and
            # asking a second time would only spend a request on the same fact.
            return first
        dated = f"{base}?date={chosen}"
        return await self._get(client, self._with_crumb(dated, crumb), Capability.OPTIONS)

    # -- verdict -----------------------------------------------------------

    def verdict(self, raw: Mapping[str, Any], instrument: InstrumentRef) -> Verdict:
        envelope = envelope_verdict(raw)
        if envelope is not None:
            return envelope

        payload = json_of(raw)
        if not isinstance(payload, Mapping):
            return Verdict(False, "schema_drift", "body is not a JSON object")

        capability = str(raw.get("capability") or Capability.QUOTE.value)
        if capability == Capability.EOD_BARS.value:
            return self._verdict_eod(payload)
        if capability == Capability.FUNDAMENTALS.value:
            return self._verdict_fundamentals(payload)
        if capability == Capability.OPTIONS.value:
            return self._verdict_options(payload)
        return self._verdict_quote(payload)

    @staticmethod
    def _chart_error(payload: Mapping[str, Any]) -> Verdict | None:
        chart = payload.get("chart")
        if not isinstance(chart, Mapping):
            return Verdict(False, "schema_drift", "chart envelope missing")
        error = chart.get("error")
        if error:
            detail = str(error)
            if "not found" in detail.lower() or "no data" in detail.lower():
                return Verdict(False, "not_found", detail)
            return Verdict(False, "schema_drift", f"chart.error: {detail}")
        return None

    def _verdict_quote(self, payload: Mapping[str, Any]) -> Verdict:
        broken = self._chart_error(payload)
        if broken is not None:
            return broken
        chart = payload["chart"]
        result = chart.get("result")
        if result is None or result == []:
            # Correct schema, no data: a symbol with no quote, not a broken one.
            return Verdict(False, "empty", "chart.result is null")
        entry = _chart_entry(payload)
        if entry is None:
            return Verdict(False, "schema_drift", "chart.result[0] is not an object")
        meta = entry.get("meta")
        if not isinstance(meta, Mapping):
            return Verdict(False, "schema_drift", "chart.result[0].meta missing")
        if meta.get("regularMarketPrice") is None:
            # The promised path is absent while the envelope looks healthy.
            return Verdict(False, "schema_drift", "meta.regularMarketPrice missing")
        if meta.get("regularMarketTime") is None:
            return Verdict(False, "schema_drift", "meta.regularMarketTime missing")
        return Verdict(True, "ok")

    def _verdict_eod(self, payload: Mapping[str, Any]) -> Verdict:
        broken = self._chart_error(payload)
        if broken is not None:
            return broken
        entry = _chart_entry(payload)
        if entry is None:
            return Verdict(False, "empty", "chart.result is null")
        if not _bars(entry):
            return Verdict(False, "empty", "chart carries no complete daily bar")
        return Verdict(True, "ok")

    @staticmethod
    def _verdict_fundamentals(payload: Mapping[str, Any]) -> Verdict:
        summary = payload.get("quoteSummary")
        if not isinstance(summary, Mapping):
            return Verdict(False, "schema_drift", "quoteSummary envelope missing")
        result = summary.get("result")
        if not isinstance(result, list) or not result or not isinstance(result[0], Mapping):
            return Verdict(False, "empty", "quoteSummary.result is null")
        modules = result[0]
        if not any(isinstance(modules.get(name), Mapping) and modules.get(name) for name in QUOTE_SUMMARY_MODULES):
            return Verdict(False, "empty", "quoteSummary returned no requested module")
        return Verdict(True, "ok")

    @staticmethod
    def _verdict_options(payload: Mapping[str, Any]) -> Verdict:
        entry = _option_chain(payload)
        if entry is None:
            return Verdict(False, "empty", "optionChain.result is null")
        if not (entry.get("expirationDates") or []):
            return Verdict(False, "schema_drift", "optionChain has no expirationDates")
        if not (entry.get("options") or []):
            return Verdict(False, "empty", "optionChain has no chain block")
        return Verdict(True, "ok")

    # -- parse -------------------------------------------------------------

    def parse(
        self, raw: Mapping[str, Any], instrument: InstrumentRef
    ) -> Sequence[Mapping[str, Any]]:
        payload = json_of(raw)
        if not isinstance(payload, Mapping):
            return []
        capability = str(raw.get("capability") or Capability.QUOTE.value)
        if capability == Capability.EOD_BARS.value:
            return self._parse_eod(payload, instrument)
        if capability == Capability.FUNDAMENTALS.value:
            return self._parse_fundamentals(payload, instrument)
        if capability == Capability.OPTIONS.value:
            return self._parse_options(payload, instrument)
        return self._parse_quote(payload, instrument)

    # -- quote -------------------------------------------------------------

    def _meta(self, raw: Mapping[str, Any]) -> Any:
        payload = json_of(raw)
        if not isinstance(payload, Mapping):
            return _MISSING
        entry = _chart_entry(payload)
        if entry is None:
            return _MISSING
        meta = entry.get("meta")
        return meta if isinstance(meta, Mapping) else _MISSING

    def _parse_quote(
        self, payload: Mapping[str, Any], instrument: InstrumentRef
    ) -> Sequence[Mapping[str, Any]]:
        entry = _chart_entry(payload)
        if entry is None:
            return []
        meta = entry.get("meta")
        if not isinstance(meta, Mapping) or meta.get("regularMarketTime") is None:
            return []
        stamp = datetime.fromtimestamp(int(meta["regularMarketTime"]), UTC)
        currency = str(meta.get("currency") or instrument.currency)
        unit = price_unit(currency)
        out: list[dict[str, Any]] = []

        def cell(field_name: str, value: Any, *, kind: str = "num") -> None:
            if kind == "text":
                out.append(
                    observation(
                        instrument,
                        field_name,
                        value_text=str(value),
                        unit="text",
                        source=self.name,
                        trust=self.trust,
                        as_of=stamp,
                    )
                )
                return
            number = as_float(value)
            if number is None:
                return
            out.append(
                observation(
                    instrument,
                    field_name,
                    value_num=number,
                    unit=unit,
                    source=self.name,
                    trust=self.trust,
                    as_of=stamp,
                )
            )

        cell("last_price", meta.get("regularMarketPrice"))
        cell("prev_close", meta.get("chartPreviousClose", meta.get("previousClose")))
        cell("day_open", meta.get("regularMarketOpen"))
        cell("day_high", meta.get("regularMarketDayHigh"))
        cell("day_low", meta.get("regularMarketDayLow"))
        cell("day_volume", meta.get("regularMarketVolume"), kind="num")
        cell("quote_as_of", stamp.isoformat(timespec="seconds"), kind="text")
        # ``currency`` is an identity column; recording it as an observation keeps
        # the provenance of "USD" attached to the provider that said so.
        cell("currency", currency, kind="text")
        return out

    # -- EOD ---------------------------------------------------------------

    def _parse_eod(
        self, payload: Mapping[str, Any], instrument: InstrumentRef
    ) -> Sequence[Mapping[str, Any]]:
        """12 technical + 9 performance cells from one 5y daily chart body."""

        entry = _chart_entry(payload)
        if entry is None:
            return []
        series = _bars(entry)
        if not series:
            return []

        meta = entry.get("meta") or {}
        currency = str(meta.get("currency") or instrument.currency)
        price = price_unit(currency)
        stamp = _session_stamp(meta, series[-1].day)
        out: list[dict[str, Any]] = []

        def cell(field_name: str, value: Any, *, unit: str) -> None:
            if value is None:
                return
            if unit in {"date", "timestamp", "text"}:
                out.append(
                    observation(
                        instrument,
                        field_name,
                        value_text=str(value),
                        unit=unit,
                        source=self.name,
                        trust=self.trust,
                        as_of=stamp,
                    )
                )
                return
            number = as_float(value)
            if number is None:
                return
            out.append(
                observation(
                    instrument,
                    field_name,
                    value_num=number,
                    unit=unit,
                    source=self.name,
                    trust=self.trust,
                    as_of=stamp,
                )
            )

        closes = [bar.close for bar in series]
        volumes = [bar.volume for bar in series]
        last = series[-1].day

        cell("bars_count", len(series), unit="count")
        cell("first_bar_date", series[0].day.isoformat(), unit="date")
        cell("last_bar_date", last.isoformat(), unit="date")
        cell("ma_20", _sma(closes, 20), unit=price)
        cell("ma_50", _sma(closes, 50), unit=price)
        cell("ma_200", _sma(closes, 200), unit=price)
        cell("ema_12", _ema(closes, 12), unit=price)
        cell("ema_26", _ema(closes, 26), unit=price)
        cell("rsi_14", _rsi(closes), unit="pct")
        cell("atr_14", _atr(series), unit=price)
        cell("volatility_20d", _volatility(closes), unit="pct")
        cell("avg_volume_20d", sum(volumes[-20:]) / 20.0 if volumes else None, unit="shares")

        if len(closes) >= 2 and closes[-2]:
            cell("ret_1d", (closes[-1] / closes[-2] - 1.0) * 100.0, unit="pct")
        cell("ret_1w", _return_pct(series, last - timedelta(days=7)), unit="pct")
        cell("ret_1m", _return_pct(series, _shift_months(last, -1)), unit="pct")
        cell("ret_3m", _return_pct(series, _shift_months(last, -3)), unit="pct")
        cell("ret_6m", _return_pct(series, _shift_months(last, -6)), unit="pct")

        previous_year = [bar for bar in series if bar.day.year < last.year]
        cell(
            "ret_ytd",
            (closes[-1] / previous_year[-1].close - 1.0) * 100.0 if previous_year else None,
            unit="pct",
        )
        cell("ret_1y", _return_pct(series, _shift_months(last, -12)), unit="pct")
        cell("ret_3y", _return_pct(series, _shift_months(last, -36)), unit="pct")
        cell("ret_5y", _return_pct(series, _shift_months(last, -60)), unit="pct")
        return out

    # -- fundamentals ------------------------------------------------------

    def _parse_fundamentals(
        self, payload: Mapping[str, Any], instrument: InstrumentRef
    ) -> Sequence[Mapping[str, Any]]:
        summary = payload.get("quoteSummary")
        if not isinstance(summary, Mapping):
            return []
        result = summary.get("result")
        if not isinstance(result, list) or not result or not isinstance(result[0], Mapping):
            return []
        modules = result[0]
        price_block = modules.get("price") or {}
        currency = str(price_block.get("currency") or instrument.currency)
        stamp = _session_stamp(price_block, None)
        out: list[dict[str, Any]] = []

        for column, (module, key, transform) in FUNDAMENTAL_SOURCES.items():
            block = modules.get(module)
            value = as_float(_unwrapped(block.get(key))) if isinstance(block, Mapping) else None
            if value is None:
                for fallback_module, fallback_key in FUNDAMENTAL_FALLBACKS.get(column, ()):
                    other = modules.get(fallback_module)
                    if isinstance(other, Mapping):
                        value = as_float(_unwrapped(other.get(fallback_key)))
                        if value is not None:
                            break
            if value is None:
                continue
            if transform == "percent":
                value *= 100.0
            if column == "market_cap_usd" and currency.upper() != "USD":
                # No FX rung is wired here; inventing a rate would be worse than
                # an empty cell (02 §3.7 V11: never convert silently).
                continue
            if column in _MONEY_COLUMNS:
                unit = price_unit(currency)
            elif column in _PERCENT_COLUMNS:
                unit = "pct"
            elif column in _SHARE_COLUMNS:
                unit = "shares"
            else:
                unit = "ratio"
            out.append(
                observation(
                    instrument,
                    column,
                    value_num=value,
                    unit=unit,
                    source=self.name,
                    trust=self.trust,
                    as_of=stamp,
                )
            )
        return out

    # -- options -----------------------------------------------------------

    def _parse_options(
        self, payload: Mapping[str, Any], instrument: InstrumentRef
    ) -> Sequence[Mapping[str, Any]]:
        """The ATM call at the expiry nearest 30 DTE, with computed Greeks."""

        entry = _option_chain(payload)
        if entry is None:
            return []
        spot, today = _chain_clock(entry)
        if spot is None or spot <= 0:
            return []
        chosen = _expiry_target(payload)
        blocks = [block for block in (entry.get("options") or []) if isinstance(block, Mapping)]
        if not blocks:
            return []
        chain = next(
            (
                block
                for block in blocks
                if _epoch(block.get("expirationDate")) == chosen
            ),
            blocks[0],
        )
        expiry = _epoch(chain.get("expirationDate")) or chosen
        if expiry is None:
            return []
        expiry_day = datetime.fromtimestamp(int(expiry), UTC).date()
        dte = (expiry_day - today).days
        calls = [
            item
            for item in (chain.get("calls") or [])
            if isinstance(item, Mapping) and as_float(item.get("strike")) is not None
        ]
        if not calls:
            return []
        atm = min(calls, key=lambda item: abs(float(as_float(item.get("strike")) or 0.0) - spot))
        strike = float(as_float(atm.get("strike")) or 0.0)
        iv = as_float(atm.get("impliedVolatility"))
        price = price_unit(str((entry.get("quote") or {}).get("currency") or instrument.currency))
        stamp = _session_stamp(entry.get("quote") or {}, expiry_day)
        out: list[dict[str, Any]] = []

        def cell(field_name: str, value: Any, *, unit: str) -> None:
            if value is None:
                return
            if unit == "date":
                out.append(
                    observation(
                        instrument,
                        field_name,
                        value_text=str(value),
                        unit=unit,
                        source=self.name,
                        trust=self.trust,
                        as_of=stamp,
                    )
                )
                return
            number = as_float(value)
            if number is None:
                return
            out.append(
                observation(
                    instrument,
                    field_name,
                    value_num=number,
                    unit=unit,
                    source=self.name,
                    trust=self.trust,
                    as_of=stamp,
                )
            )

        cell("opt_expiry", expiry_day.isoformat(), unit="date")
        cell("opt_dte", dte, unit="count")
        cell("opt_strike_atm", strike, unit=price)
        cell("opt_iv_atm", iv, unit="ratio")
        cell("opt_open_interest_atm", atm.get("openInterest") or 0, unit="count")
        cell("opt_volume_atm", atm.get("volume") or 0, unit="count")

        if iv is not None and iv > 0 and dte > 0:
            greeks = black_scholes_call(spot, strike, dte / 365.0, float(iv))
            cell("opt_delta_atm", greeks.get("delta"), unit="ratio")
            cell("opt_gamma_atm", greeks.get("gamma"), unit="ratio")
            cell("opt_vega_atm", greeks.get("vega"), unit="ratio")
            cell("opt_theta_atm", greeks.get("theta"), unit="ratio")
        return out


def _epoch(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _unwrapped(value: Any) -> Any:
    """``quoteSummary`` may wrap a number as ``{"raw": ...}``; read through it."""

    if isinstance(value, Mapping):
        return value.get("raw")
    return value


def _session_stamp(block: Mapping[str, Any], fallback_day: date | None) -> datetime:
    """The data's own clock: ``regularMarketTime`` when present, else the session."""

    stamp = block.get("regularMarketTime")
    if isinstance(stamp, (int, float, str)):
        try:
            return datetime.fromtimestamp(int(stamp), UTC)
        except (TypeError, ValueError, OSError):  # pragma: no cover - malformed stamp
            pass
    if fallback_day is not None:
        return datetime.combine(fallback_day, datetime.min.time(), UTC)
    return datetime.now(UTC)


__all__ = [
    "BROWSER_USER_AGENT",
    "EOD_QUERY",
    "FUNDAMENTAL_SOURCES",
    "HOST",
    "META_FIELDS",
    "OPTIONS_DTE_TARGET",
    "QUOTE_SUMMARY_MODULES",
    "YahooAdapter",
    "black_scholes_call",
]
