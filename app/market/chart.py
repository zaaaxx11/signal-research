"""Chart metrics: pure formulas, plus the Yahoo chart source that feeds them.

The chart endpoint is the cheap path for performance/technical fields. It uses
raw split-adjusted ``close`` (not ``adjclose``) because that is what the Finviz
comparison reproduces; every function here is independent of the depth
collector so the formulas can be tested from a fixture.

The same body also carries ``events.dividends``, which is where
``dividend_growth_1y/3y/5y`` come from — no extra request. Those three cells
are an **estimator**, not Finviz parity: Finviz prints the provider's own growth
rate, while these are computed from the cash amounts Yahoo reports, so specials
are summed with regulars, share-count changes are invisible, and a mid-year
history start can only truncate a calendar year (in which case the metric is
left absent rather than guessed). See :func:`_dividend_cagr`.

:class:`YahooChartDepthSource` requests that body over a **wide window** —
``period1`` eleven years back, not ``range=1y`` — and parses it with
:func:`parse_chart_metrics`, which is what fills ``perf_ytd``, ``perf_3y``,
``perf_5y``, ``perf_10y``, ``high_50d``, ``low_50d`` and the three dividend
growth cells from one response. ``wide=False`` keeps the legacy ``range=1y``
request for tests and back-compat; that narrow body cannot reach the
multi-year anchors. :func:`candlestick_pattern` adds the one label no provider
reports: a deterministic doji/hammer/engulfing heuristic over the last bars,
published as ``candlestick_pattern`` and documented as a heuristic wherever it
is consumed.

The chart window's low is **not** ``all_time_low``: a split-adjusted history
starts at the split price (MSFT's window low is 47.02 against Yahoo's 0.0885
all-time low), so neither all-time extreme is claimed from this module.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Mapping, Sequence

logger = logging.getLogger("mee.chart")


@dataclass(frozen=True)
class ChartBar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _bars(entry: Mapping[str, Any]) -> list[ChartBar]:
    timestamps = entry.get("timestamp") or []
    quote = ((entry.get("indicators") or {}).get("quote") or [{}])[0]
    if not isinstance(quote, Mapping):
        return []
    keys = ("open", "high", "low", "close", "volume")
    values = {key: quote.get(key) or [] for key in keys}
    out: list[ChartBar] = []
    for index, stamp in enumerate(timestamps):
        if not isinstance(stamp, (int, float)):
            continue
        row = {}
        for key in keys:
            series = values[key]
            row[key] = _number(series[index]) if index < len(series) else None
        if any(row[key] is None for key in ("open", "high", "low", "close")):
            continue
        open_, high, low, close = row["open"], row["high"], row["low"], row["close"]
        if high < max(open_, close) or low > min(open_, close):
            continue
        out.append(
            ChartBar(
                day=datetime.fromtimestamp(float(stamp), UTC).date(),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=row["volume"] or 0.0,
            )
        )
    return out


def _shift_months(moment: date, months: int) -> date:
    year = moment.year + (moment.month - 1 + months) // 12
    month = (moment.month - 1 + months) % 12 + 1
    days = (31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[month - 1]
    return date(year, month, min(moment.day, days))


#: How far back the default chart request reaches. Eleven years, not ten: a
#: ten-year anchor must land *inside* the returned bars, and a window that
#: starts exactly ten years back begins one bar late often enough that
#: ``perf_10y`` measured 0/38 filled, while the eleven-year start filled 9/10.
WIDE_WINDOW_YEARS = 11


def wide_window(now: datetime) -> tuple[int, int]:
    """``(period1, period2)`` epoch seconds for the default wide window.

    ``period1`` is midnight UTC on the calendar day :data:`WIDE_WINDOW_YEARS`
    before ``now``; ``period2`` is ``now`` itself. A naive ``now`` is read as
    UTC. The extra year over ten is deliberate slack for the anchor, not a
    request for more history than the metrics use.
    """
    moment = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    start = _shift_months(moment.date(), -12 * WIDE_WINDOW_YEARS)
    period1 = int(datetime.combine(start, datetime.min.time(), tzinfo=UTC).timestamp())
    return period1, int(moment.timestamp())


def _anchor(bars: Sequence[ChartBar], target: date) -> ChartBar | None:
    for bar in reversed(bars):
        if bar.day <= target:
            return bar
    return None


def _return_pct(bars: Sequence[ChartBar], target: date) -> float | None:
    anchor = _anchor(bars, target)
    if anchor is None or not anchor.close:
        return None
    return (bars[-1].close / anchor.close - 1.0) * 100.0


def _percent(close: float, base: float | None) -> float | None:
    if base is None or base == 0:
        return None
    return (close / base - 1.0) * 100.0


def _rsi(closes: Sequence[float], window: int = 14) -> float | None:
    if len(closes) <= window:
        return None
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = sum(gains[:window]) / window
    avg_loss = sum(losses[:window]) / window
    for gain, loss in zip(gains[window:], losses[window:]):
        avg_gain = (avg_gain * (window - 1) + gain) / window
        avg_loss = (avg_loss * (window - 1) + loss) / window
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _atr(bars: Sequence[ChartBar], window: int = 14) -> float | None:
    if len(bars) <= window:
        return None
    ranges: list[float] = []
    for index in range(1, len(bars)):
        current, previous = bars[index], bars[index - 1]
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    value = sum(ranges[:window]) / window
    for item in ranges[window:]:
        value = (value * (window - 1) + item) / window
    return value


#: Body as a share of the bar's high-low range at or below which the bar reads
#: as a doji. A zero body (open == close) always qualifies.
_DOJI_BODY_SHARE = 0.1


def candlestick_pattern(bars: Sequence[ChartBar]) -> str | None:
    """Classify the last one to three bars: ``doji``, ``hammer`` or ``engulfing``.

    This is a **heuristic label**, not a reported fact: no provider publishes
    it and the catalog carries it as a proxy. The rules are deterministic and
    applied in a fixed order, so the same bars always produce the same label:

    1. ``doji`` — the last bar's body is at most a tenth of its high-low
       range (indecision printed as a sliver);
    2. ``hammer`` — the last bar's lower shadow is at least twice its body
       and its upper shadow no larger than that body (a small body at the top
       of a long wick);
    3. ``engulfing`` — the last bar's body swallows the prior bar's body in
       the opposite direction, bullish or bearish; the label does not
       distinguish the two.

    ``None`` when no rule fires, and also for a single bar with no range at
    all (``high == low``), where every ratio above would divide by zero.
    """
    if not bars:
        return None
    last = bars[-1]
    span = last.high - last.low
    if span <= 0:
        return None
    body = abs(last.close - last.open)
    upper = last.high - max(last.open, last.close)
    lower = min(last.open, last.close) - last.low
    if body <= _DOJI_BODY_SHARE * span:
        return "doji"
    if lower >= 2.0 * body and upper <= body:
        return "hammer"
    if len(bars) >= 2:
        previous = bars[-2]
        previous_body = abs(previous.close - previous.open)
        bullish = last.close > last.open and previous.close < previous.open
        bearish = last.close < last.open and previous.close > previous.open
        if previous_body > 0 and (
            (bullish and last.open <= previous.close and last.close >= previous.open)
            or (bearish and last.open >= previous.close and last.close <= previous.open)
        ):
            return "engulfing"
    return None


def _epoch_day(value: Any) -> date | None:
    """Epoch seconds → UTC calendar day; ``None`` for anything unusable."""

    stamp = _number(value)
    if stamp is None:
        return None
    try:
        return datetime.fromtimestamp(stamp, UTC).date()
    except (OverflowError, OSError, ValueError):
        return None


def _dividend_payments(entry: Mapping[str, Any]) -> list[tuple[date, float]]:
    """Cash dividends from ``events.dividends``, ascending by ex-date.

    Yahoo ships the block either as a list of ``{"amount", "date"}`` items or as
    a mapping keyed by the epoch (its live v8 shape); both are read. Only the
    ``dividends`` block is touched — ``events.splits`` is never consulted, and
    the per-share amounts are already split-adjusted on Yahoo's side.

    Non-positive amounts are dropped (zero is not a payment and a negative
    amount is a data error), exact duplicates are collapsed, and distinct
    amounts on the same day are summed (a regular plus a special). A bad item
    is skipped, never coerced into a fake payment.
    """

    events = entry.get("events")
    if not isinstance(events, Mapping):
        return []
    raw = events.get("dividends")
    if isinstance(raw, Mapping):
        items: Sequence[Any] = [
            {"date": key, **(value if isinstance(value, Mapping) else {})}
            for key, value in raw.items()
        ]
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        items = raw
    else:
        return []

    by_day: dict[date, float] = {}
    seen: set[tuple[date, float]] = set()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        amount = _number(item.get("amount"))
        day = _epoch_day(item.get("date"))
        if amount is None or amount <= 0 or day is None:
            continue
        if (day, amount) in seen:
            continue
        seen.add((day, amount))
        by_day[day] = by_day.get(day, 0.0) + amount
    return sorted(by_day.items())


def _year_totals(payments: Sequence[tuple[date, float]]) -> dict[int, float]:
    """Total cash paid per calendar year."""

    totals: dict[int, float] = {}
    for day, amount in payments:
        totals[day.year] = totals.get(day.year, 0.0) + amount
    return totals


def _dividend_growth_1y(payments: Sequence[tuple[date, float]]) -> float | None:
    """Last four payments vs the four before them, in percent.

    Payment-count based, as the chart contract defines it: for a quarterly payer
    those eight payments span two years, while a monthly payer's eight span eight
    months. That is the proxy's known bias; the calendar-year 3y/5y figures below
    do not share it.
    """

    if len(payments) < 8:
        return None
    current = sum(amount for _, amount in payments[-4:])
    prior = sum(amount for _, amount in payments[-8:-4])
    if current <= 0 or prior <= 0:
        return None
    value = (current / prior - 1.0) * 100.0
    return value if math.isfinite(value) else None


def _dividend_cagr(
    payments: Sequence[tuple[date, float]], years: int, clock: datetime
) -> float | None:
    """Calendar-year dividend CAGR over ``years``, in percent.

    ``(latest_year_sum / base_year_sum) ** (1 / years) - 1`` uses the last
    **complete** calendar year, so a partial current year can neither raise nor
    lower the latest total. Three conditions keep the number honest:

    * both endpoint totals must be positive — a missing or zero year is absent,
      not ``0%`` and not ``-100%``;
    * at least ``years + 1`` distinct calendar years must carry payments;
    * the history must begin before the base year starts, because a mid-year
      start truncates that year's total and would inflate the rate.

    This is a proxy for Finviz's ``Dividend Gr. 3/5Y``: totals per calendar year,
    not the provider's per-share growth series.
    """

    totals = _year_totals(payments)
    latest_year = clock.year - 1
    base_year = latest_year - years
    latest = totals.get(latest_year, 0.0)
    base = totals.get(base_year, 0.0)
    if latest <= 0 or base <= 0:
        return None
    if len([year for year in totals if year <= latest_year]) < years + 1:
        return None
    if payments[0][0] >= date(base_year, 1, 1):
        return None
    value = ((latest / base) ** (1.0 / years) - 1.0) * 100.0
    return value if math.isfinite(value) else None


def parse_chart_metrics(
    entry: Mapping[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """Return canonical depth fields from one Yahoo chart response.

    The multi-year cells (``perf_ytd`` through ``perf_10y``) are only as wide
    as the body: a ``range=1y`` response cannot reach a three-year anchor, and
    a window that starts after the ten-year anchor leaves ``perf_10y`` absent
    rather than guessed. :class:`YahooChartDepthSource` therefore requests the
    wide window by default; see :func:`wide_window`.

    Dividend growth is read from the same body's ``events.dividends``:

    * ``dividend_growth_1y`` — the last four cash payments against the four
      before them, in percent;
    * ``dividend_growth_3y`` / ``dividend_growth_5y`` — calendar-year CAGR of
      total cash paid, using the last complete year and the year ``n`` before
      it.

    These are **estimators**, not Finviz parity: Finviz prints the provider's
    growth rate, while these sum the reported per-share amounts, so specials and
    regulars are mixed, buybacks and share-count changes are invisible, and a
    mid-year start leaves the 3y/5y cells absent. When there is not enough
    history the key is omitted rather than filled with ``0`` or a guess.

    ``candlestick_pattern`` is the one label no provider reports: a
    deterministic doji/hammer/engulfing heuristic over the last bars, absent
    when no rule fires. See :func:`candlestick_pattern`.
    """

    bars = _bars(entry)
    if not bars:
        return {}
    clock = now or datetime.now(UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    # A bar dated today is incomplete while the US session is still running.
    # The collector runs pre-open in production, so this branch is mostly a
    # guard for manual probes.
    if bars[-1].day == clock.date() and clock.hour < 21:
        bars = bars[:-1]
    if not bars:
        return {}

    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars]
    out: dict[str, Any] = {}
    for window, field in ((5, "perf_1w"), (21, "perf_4w"), (63, "perf_13w"), (126, "perf_26w")):
        if len(closes) > window and closes[-1 - window]:
            out[field] = (closes[-1] / closes[-1 - window] - 1.0) * 100.0

    for months, field in ((12, "perf_52w"), (36, "perf_3y"), (60, "perf_5y"), (120, "perf_10y")):
        value = _return_pct(bars, _shift_months(clock.date(), -months))
        if value is not None:
            out[field] = value
    previous_year = [bar for bar in bars if bar.day.year < clock.year]
    if previous_year and previous_year[-1].close:
        out["perf_ytd"] = (closes[-1] / previous_year[-1].close - 1.0) * 100.0

    for window in (20, 50, 200):
        if len(closes) >= window:
            base = sum(closes[-window:]) / window
            out[f"sma{window}"] = _percent(closes[-1], base)
    rsi = _rsi(closes)
    if rsi is not None:
        out["rsi"] = rsi
    atr = _atr(bars)
    if atr is not None:
        out["atr"] = atr
    for window, field in ((5, "volatility_1w"), (21, "volatility_4w")):
        if len(bars) >= window:
            out[field] = sum(
                (bar.high - bar.low) / bar.close * 100.0
                for bar in bars[-window:]
                if bar.close
            ) / window
    if volumes:
        window = min(65, len(volumes))
        average = sum(volumes[-window:]) / window
        out["average_volume"] = average
        if average and volumes[-1]:
            out["relative_volume"] = volumes[-1] / average

    cutoff = clock.date() - timedelta(days=365)
    year_bars = [bar for bar in bars if bar.day > cutoff]
    if year_bars:
        high = max(bar.high for bar in year_bars)
        low = min(bar.low for bar in year_bars)
        out["high_52w"] = high
        out["low_52w"] = low
        if low:
            out["week_52_range"] = (high - low) / low * 100.0
    last50 = bars[-50:]
    if last50:
        out["high_50d"] = max(bar.high for bar in last50)
        out["low_50d"] = min(bar.low for bar in last50)

    if len(bars) >= 2:
        previous, current = bars[-2], bars[-1]
        if previous.close:
            out["gap"] = (current.open - previous.close) / previous.close * 100.0
        if current.open:
            out["change_from_open"] = (current.close - current.open) / current.open * 100.0

    # A heuristic label over the last bars, not a provider cell: see
    # :func:`candlestick_pattern`. Absent when no rule fires.
    pattern = candlestick_pattern(bars)
    if pattern is not None:
        out["candlestick_pattern"] = pattern

    dividends = _dividend_payments(entry)
    if dividends:
        growth_1y = _dividend_growth_1y(dividends)
        if growth_1y is not None:
            out["dividend_growth_1y"] = growth_1y
        for years in (3, 5):
            growth = _dividend_cagr(dividends, years, clock)
            if growth is not None:
                out[f"dividend_growth_{years}y"] = growth

    return {key: value for key, value in out.items() if value is not None}


# ---------------------------------------------------------------------------
# Yahoo chart source (performance/technical)
# ---------------------------------------------------------------------------


class YahooChartDepthSource:
    """One wide-window daily chart per symbol; no crumb and no cookie.

    The default request is ``period1 = now - 11 years`` parsed with
    :func:`parse_chart_metrics`, which is what fills ``perf_ytd``,
    ``perf_3y``, ``perf_5y``, ``perf_10y``, ``high_50d``, ``low_50d`` and
    ``dividend_growth_1y/3y/5y`` from a single body. The eleven-year start
    (not ten) is what keeps the ten-year anchor inside the window; see
    :func:`wide_window`.

    ``wide=False`` restores the legacy ``range=1y`` request for tests and
    back-compat. That narrow body cannot reach the multi-year anchors, so it
    fills the short-window technicals only — which is exactly why the fast
    path dropped those ten keys before the wide window became the default.
    """

    name = "yahoo_chart"

    def __init__(
        self,
        *,
        concurrency: int = 2,
        timeout_s: float = 20.0,
        base_url: str = "https://query1.finance.yahoo.com",
        wide: bool = True,
    ) -> None:
        self.concurrency = max(1, int(concurrency))
        self.timeout_s = float(timeout_s)
        self.base_url = base_url.rstrip("/")
        self.wide = bool(wide)

    @staticmethod
    def url(
        symbol: str,
        base_url: str,
        *,
        wide: bool = True,
        now: datetime | None = None,
    ) -> str:
        """The chart URL for one symbol.

        ``wide`` picks the window: ``period1``/``period2`` spanning eleven
        years back from ``now`` (the default), or the legacy ``range=1y``.
        ``now`` is injectable so a test can pin the window; production lets
        it default to the current instant.
        """
        if wide:
            period1, period2 = wide_window(now or datetime.now(UTC))
            window = f"period1={period1}&period2={period2}"
        else:
            window = "range=1y"
        return (
            f"{base_url}/v8/finance/chart/{symbol}"
            f"?interval=1d&{window}&includePrePost=false&events=div%2Csplit"
        )

    async def fetch_many(
        self, symbols: Sequence[str]
    ) -> dict[str, Any]:
        from app.market.depth import (
            QUOTE_ONLY_FIELDS,
            DepthObservation,
            canonical_symbol,
        )
        from app.providers import http as provider_http
        from app.providers.adapters.yahoo import BROWSER_USER_AGENT

        semaphore = asyncio.Semaphore(self.concurrency)
        out: dict[str, Any] = {}
        headers = {"user-agent": BROWSER_USER_AGENT, "accept": "application/json"}

        async with provider_http.build_client(
            timeout_s=self.timeout_s, headers=headers
        ) as client:

            async def one(raw_symbol: str) -> None:
                symbol = canonical_symbol(raw_symbol)
                async with semaphore:
                    try:
                        response = await client.get(
                            self.url(symbol, self.base_url, wide=self.wide)
                        )
                        if int(getattr(response, "status_code", 0)) != 200:
                            return
                        payload = response.json()
                        result = (payload.get("chart") or {}).get("result") or []
                        if not result:
                            return
                        fields = parse_chart_metrics(result[0])
                        fields = {
                            key: value
                            for key, value in fields.items()
                            if key not in QUOTE_ONLY_FIELDS and value is not None
                        }
                        if fields:
                            out[symbol] = DepthObservation(
                                symbol=symbol,
                                fields=fields,
                                as_of=datetime.now(UTC),
                                source=self.name,
                            )
                    except Exception as exc:  # noqa: BLE001 - one symbol optional
                        logger.info("yahoo chart failed for %s: %s", symbol, exc)

            await asyncio.gather(*(one(symbol) for symbol in symbols))
        return out


__all__ = [
    "ChartBar",
    "WIDE_WINDOW_YEARS",
    "YahooChartDepthSource",
    "candlestick_pattern",
    "parse_chart_metrics",
    "wide_window",
]
