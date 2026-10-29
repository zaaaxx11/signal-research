"""Yahoo v7 batch quote source: the five catalog keys the bench left on the floor.

Offline throughout. The one HTTP seam (``app.providers.http.build_client``) is
replaced by an ``httpx2.MockTransport`` that speaks Yahoo's three-request
protocol — ``fc.yahoo.com`` (cookie) → ``/v1/test/getcrumb`` (token) →
``/v7/finance/quote?symbols=…&crumb=…`` — so the suite-wide socket guard in
``tests/conftest.py`` never fires.

The verified probe values are pinned as literals (enrichment-plan probe E1):
NVDA first trade 1999-01-22, RDDT 2024-03-21, ARM 2023-09-14, AAPL earnings
2026-11-17, AAPL dividend ex-date 2026-10-01. The 27 keys the benchmark
already maps are pinned verbatim from ``scripts/fast_path_bench.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import httpx2
import pytest

from app.providers import http as provider_http

MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "market" / "yahoo_batch.py"

#: The benchmark's mapped keys (``scripts/fast_path_bench.py``), ported verbatim.
BENCHMARK_MAP: dict[str, str] = {
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
}

#: The five new keys (enrichment-plan step 1: +5 keys, +0.15 s per 100 symbols).
NEW_MAP: dict[str, str] = {
    "quoteType": "security_type",
    "fullExchangeName": "exchange",
    "firstTradeDateMilliseconds": "ipo_date",
    "earningsTimestamp": "earnings_date",
    "dividendDate": "dividend_ex_date",
}

#: Verified epoch values for the five conversions (probe E1). Epoch milliseconds
#: for the IPO stamps, epoch seconds for the earnings/dividend stamps.
NVDA_IPO_MS = 916_963_200_000  # 1999-01-22
RDDT_IPO_MS = 1_710_979_200_000  # 2024-03-21
ARM_IPO_MS = 1_694_649_600_000  # 2023-09-14
AAPL_EARNINGS_S = 1_794_873_600  # 2026-11-17
AAPL_DIVIDEND_S = 1_790_812_800  # 2026-10-01

#: Credentials a keyless source must never read.
KEY_ENV_VARS = (
    "FINNHUB_API_KEY",
    "POLYGON_API_KEY",
    "EODHD_API_KEY",
    "TIINGO_API_KEY",
    "TWELVE_DATA_API_KEY",
    "OPENFIGI_API_KEY",
    "YAHOO_API_KEY",
)


# ---------------------------------------------------------------------------
# The fake Yahoo
# ---------------------------------------------------------------------------


def payload(rows: Sequence[Mapping[str, Any]]) -> httpx2.Response:
    """A ``/v7/finance/quote`` body, as v7 prints it."""
    return httpx2.Response(
        200, json={"quoteResponse": {"result": list(rows), "error": None}}
    )


def quote(symbol: str, **fields: Any) -> dict[str, Any]:
    """One ``quoteResponse.result[]`` entry with the fields the tests care about."""
    row: dict[str, Any] = {
        "symbol": symbol,
        "currency": "USD",
        "regularMarketPrice": 231.5,
        "regularMarketPreviousClose": 229.0,
        "regularMarketChange": 2.5,
        "regularMarketChangePercent": 1.090512,
        "regularMarketVolume": 1_234_567,
        "regularMarketOpen": 230.0,
        "regularMarketDayHigh": 233.0,
        "regularMarketDayLow": 228.0,
        "averageDailyVolume3Month": 1_000_000,
        "fiftyDayAverage": 225.0,
        "twoHundredDayAverage": 200.0,
        "marketCap": 3_500_000_000_000,
        "trailingPE": 41.2,
        "longName": f"{symbol} Inc.",
    }
    row.update(fields)
    return row


class FakeYahoo:
    """A recording ``httpx2.MockTransport`` for Yahoo's three-request protocol.

    ``crumb`` is the token the handshake hands out: a string, a list consumed
    one entry per handshake (the last entry repeats), or a callable of the
    handshake number. ``answer`` serves the data requests: a callable of
    ``(symbols, attempt)`` (the last answer repeats).
    """

    def __init__(
        self,
        *,
        crumb: Any = "test-crumb",
        answer: Callable[[tuple[str, ...], int], httpx2.Response] | None = None,
    ) -> None:
        self.cookie_calls = 0
        self.crumb_calls = 0
        self.quote_calls: list[dict[str, Any]] = []
        self.crumb_cookies: list[str] = []
        self._crumb = crumb
        self._answer: Callable[[tuple[str, ...], int], httpx2.Response] = (
            answer
            if answer is not None
            else (lambda symbols, _attempt: payload([quote(symbol) for symbol in symbols]))
        )
        self.transport = httpx2.MockTransport(self)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "fc.yahoo.com":
            self.cookie_calls += 1
            return httpx2.Response(
                200, headers=[("set-cookie", "A1=fake-cookie; Path=/; Domain=.yahoo.com")]
            )
        if request.url.path == "/v1/test/getcrumb":
            self.crumb_calls += 1
            self.crumb_cookies.append(request.headers.get("cookie", ""))
            return httpx2.Response(200, text=self._crumb_text())
        if request.url.path == "/v7/finance/quote":
            symbols = tuple(
                token.strip()
                for token in (request.url.params.get("symbols") or "").split(",")
                if token.strip()
            )
            self.quote_calls.append(
                {
                    "symbols": symbols,
                    "crumb": request.url.params.get("crumb") or "",
                    "url": str(request.url),
                }
            )
            return self._answer(symbols, len(self.quote_calls))
        return httpx2.Response(404, json={"error": f"unrouted {request.url}"})

    def _crumb_text(self) -> str:
        if callable(self._crumb):
            return str(self._crumb(self.crumb_calls))
        if isinstance(self._crumb, (list, tuple)):
            tokens = list(self._crumb)
            return str(tokens[min(self.crumb_calls - 1, len(tokens) - 1)])
        return str(self._crumb)


# ---------------------------------------------------------------------------
# RED helpers: a missing module is a NotImplementedError, never an ImportError
# ---------------------------------------------------------------------------


def load_module() -> Any:
    if not MODULE_PATH.exists():
        raise NotImplementedError(f"{MODULE_PATH} does not exist yet (RED)")
    from app.market import yahoo_batch

    return yahoo_batch


def build_source(
    monkeypatch: pytest.MonkeyPatch, fake: FakeYahoo, **kwargs: Any
) -> Any:
    """Wire the source to ``fake`` through the one HTTP seam."""
    module = load_module()
    cls = getattr(module, "YahooBatchQuoteSource", None)
    if cls is None:
        raise NotImplementedError(
            "app.market.yahoo_batch.YahooBatchQuoteSource is missing (RED)"
        )

    def build_client(**_ignored: Any) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=fake.transport, timeout=30.0)

    monkeypatch.setattr(provider_http, "build_client", build_client)
    return cls(**kwargs)


# ---------------------------------------------------------------------------
# The map
# ---------------------------------------------------------------------------


def test_batch_map_keeps_the_benchmark_keys_and_adds_five() -> None:
    module = load_module()
    mapping = module.YAHOO_BATCH_MAP
    assert len(mapping) == len(BENCHMARK_MAP) + len(NEW_MAP) == 32
    for source, key in BENCHMARK_MAP.items():
        assert mapping.get(source) == key
    for source, key in NEW_MAP.items():
        assert mapping.get(source) == key


def test_the_constructor_refuses_absurd_arguments() -> None:
    cls = getattr(load_module(), "YahooBatchQuoteSource", None)
    if cls is None:
        raise NotImplementedError("YahooBatchQuoteSource is missing (RED)")
    with pytest.raises(ValueError):
        cls(batch_size=0)
    with pytest.raises(ValueError):
        cls(batch_size=401)
    with pytest.raises(TypeError):
        cls(batch_size=True)  # a bool is not a batch size
    with pytest.raises(ValueError):
        cls(timeout_s=0)
    with pytest.raises(ValueError):
        cls(pace_s=-0.1)
    with pytest.raises(ValueError):
        cls(user_agent="")


# ---------------------------------------------------------------------------
# The five new conversions (verified values, probe E1)
# ---------------------------------------------------------------------------


async def test_quote_type_becomes_security_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeYahoo(
        answer=lambda symbols, _n: payload(
            [
                quote("AAPL", quoteType="EQUITY"),
                quote("SPY", quoteType="ETF"),
                quote("OXYZ", quoteType="STRUCTURED_PRODUCT"),
            ]
        )
    )
    source = build_source(monkeypatch, fake)
    rows = await source.fetch_many(["AAPL", "SPY", "OXYZ"])
    assert rows["AAPL"]["security_type"] == "stock"
    assert rows["SPY"]["security_type"] == "etf"
    assert rows["OXYZ"]["security_type"] == "structured_product"


async def test_full_exchange_name_becomes_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeYahoo(
        answer=lambda symbols, _n: payload(
            [
                quote("AAPL", fullExchangeName="NasdaqGS"),
                quote("WMT", fullExchangeName="NYSE"),
            ]
        )
    )
    source = build_source(monkeypatch, fake)
    rows = await source.fetch_many(["AAPL", "WMT"])
    assert rows["AAPL"]["exchange"] == "NasdaqGS"
    assert rows["WMT"]["exchange"] == "NYSE"


async def test_first_trade_date_milliseconds_becomes_ipo_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeYahoo(
        answer=lambda symbols, _n: payload(
            [
                quote("NVDA", firstTradeDateMilliseconds=NVDA_IPO_MS),
                quote("RDDT", firstTradeDateMilliseconds=RDDT_IPO_MS),
                quote("ARM", firstTradeDateMilliseconds=ARM_IPO_MS),
                # A symbol Yahoo has no first trade for keeps the key absent.
                quote("NEWC"),
                quote("JUNK", firstTradeDateMilliseconds=0),
                quote("TEXT", firstTradeDateMilliseconds="not-a-number"),
            ]
        )
    )
    source = build_source(monkeypatch, fake)
    rows = await source.fetch_many(["NVDA", "RDDT", "ARM", "NEWC", "JUNK", "TEXT"])
    assert rows["NVDA"]["ipo_date"] == "1999-01-22"
    assert rows["RDDT"]["ipo_date"] == "2024-03-21"
    assert rows["ARM"]["ipo_date"] == "2023-09-14"
    assert "ipo_date" not in rows["NEWC"]
    assert "ipo_date" not in rows["JUNK"]
    assert "ipo_date" not in rows["TEXT"]


async def test_earnings_timestamp_becomes_earnings_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeYahoo(
        answer=lambda symbols, _n: payload(
            [
                quote("AAPL", earningsTimestamp=AAPL_EARNINGS_S),
                quote("NOEA"),
            ]
        )
    )
    source = build_source(monkeypatch, fake)
    rows = await source.fetch_many(["AAPL", "NOEA"])
    assert rows["AAPL"]["earnings_date"] == "2026-11-17"
    assert "earnings_date" not in rows["NOEA"]


async def test_dividend_date_becomes_dividend_ex_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeYahoo(
        answer=lambda symbols, _n: payload(
            [
                quote("AAPL", dividendDate=AAPL_DIVIDEND_S),
                quote("TSLA"),
            ]
        )
    )
    source = build_source(monkeypatch, fake)
    rows = await source.fetch_many(["AAPL", "TSLA"])
    assert rows["AAPL"]["dividend_ex_date"] == "2026-10-01"
    assert "dividend_ex_date" not in rows["TSLA"]


# ---------------------------------------------------------------------------
# The port: existing keys, derived fields, one handshake per pass
# ---------------------------------------------------------------------------


async def test_existing_keys_and_derived_fields_survive_the_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeYahoo()
    source = build_source(monkeypatch, fake)
    rows = await source.fetch_many(["AAPL"])
    row = rows["AAPL"]
    assert row["price"] == 231.5
    assert row["prev_close"] == 229.0
    assert row["change"] == 2.5
    assert row["change_pct"] == pytest.approx(1.090512)
    assert row["volume"] == 1_234_567
    assert row["open"] == 230.0
    assert row["high"] == 233.0
    assert row["low"] == 228.0
    assert row["market_cap"] == 3_500_000_000_000
    assert row["pe"] == 41.2
    assert row["company"] == "AAPL Inc."
    assert row["average_volume"] == 1_000_000
    # sma50/sma200 as percent distance, and relative volume, as the bench had them
    assert row["sma50"] == pytest.approx((231.5 - 225.0) / 225.0 * 100.0)
    assert row["sma200"] == pytest.approx((231.5 - 200.0) / 200.0 * 100.0)
    assert row["relative_volume"] == pytest.approx(1_234_567 / 1_000_000)


async def test_a_symbol_yahoo_omits_stays_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeYahoo(answer=lambda symbols, _n: payload([quote("AAPL"), quote("MSFT")]))
    source = build_source(monkeypatch, fake)
    rows = await source.fetch_many(["AAPL", "MSFT", "UNKNOWN"])
    assert set(rows) == {"AAPL", "MSFT"}


async def test_one_handshake_per_pass_and_the_crumb_rides_every_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeYahoo()
    source = build_source(monkeypatch, fake, pace_s=0.0)
    symbols = [f"S{index:04d}" for index in range(950)]
    rows = await source.fetch_many(symbols)
    assert len(rows) == 950
    # One cookie + one crumb for the whole pass, reused across all three batches.
    assert fake.cookie_calls == 1
    assert fake.crumb_calls == 1
    assert len(fake.quote_calls) == 3
    assert all(call["crumb"] == "test-crumb" for call in fake.quote_calls)
    # The cookie the warm-up set rides the crumb exchange.
    assert "A1=fake-cookie" in fake.crumb_cookies[0]


# ---------------------------------------------------------------------------
# Batching, auth retry, throttling, isolation
# ---------------------------------------------------------------------------


async def test_batches_split_at_400(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeYahoo()
    source = build_source(monkeypatch, fake, pace_s=0.0)
    symbols = [f"S{index:04d}" for index in range(950)]
    rows = await source.fetch_many(symbols)
    sizes = [len(call["symbols"]) for call in fake.quote_calls]
    assert sizes == [400, 400, 150]
    assert set(rows) == set(symbols)


async def test_a_401_buys_one_fresh_crumb_and_one_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def answer(symbols: tuple[str, ...], attempt: int) -> httpx2.Response:
        if attempt == 1:
            return httpx2.Response(401, json={"quoteResponse": {"result": None}})
        return payload([quote(symbol) for symbol in symbols])

    fake = FakeYahoo(crumb=["crumb-one", "crumb-two"], answer=answer)
    source = build_source(monkeypatch, fake, pace_s=0.0)
    symbols = [f"S{index:04d}" for index in range(500)]
    rows = await source.fetch_many(symbols)

    assert len(rows) == 500
    assert fake.cookie_calls == 2  # the first handshake, then one refresh
    assert fake.crumb_calls == 2
    assert [call["crumb"] for call in fake.quote_calls] == [
        "crumb-one",
        "crumb-two",
        "crumb-two",  # the refreshed crumb is reused, not re-fetched
    ]
    assert source.last_errors == {}


async def test_a_throttled_crumb_endpoint_answers_empty_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeYahoo(crumb="Too Many Requests")
    source = build_source(monkeypatch, fake, pace_s=0.0)
    rows = await source.fetch_many(["AAPL", "MSFT"])
    assert rows == {}
    assert fake.quote_calls == []  # nothing to ask with; no request is spent
    assert fake.crumb_calls == 1


async def test_one_failed_batch_does_not_sink_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def answer(symbols: tuple[str, ...], attempt: int) -> httpx2.Response:
        if attempt == 1:
            return httpx2.Response(500, json={"error": "boom"})
        return payload([quote(symbol) for symbol in symbols])

    fake = FakeYahoo(answer=answer)
    source = build_source(monkeypatch, fake, pace_s=0.0)
    symbols = [f"S{index:04d}" for index in range(500)]
    rows = await source.fetch_many(symbols)
    assert set(rows) == set(symbols[400:])  # only the second batch answered
    assert source.last_errors  # the lost batch is recorded, not silent


async def test_a_broken_body_costs_only_its_own_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def answer(symbols: tuple[str, ...], attempt: int) -> httpx2.Response:
        if attempt == 1:
            return httpx2.Response(200, html="<html>Please verify you are a human</html>")
        return payload([quote(symbol) for symbol in symbols])

    fake = FakeYahoo(answer=answer)
    source = build_source(monkeypatch, fake, pace_s=0.0)
    symbols = [f"S{index:04d}" for index in range(500)]
    rows = await source.fetch_many(symbols)
    assert set(rows) == set(symbols[400:])
    assert source.last_errors


# ---------------------------------------------------------------------------
# Symbols, keys, pacing
# ---------------------------------------------------------------------------


async def test_symbols_are_canonicalized_before_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeYahoo(
        answer=lambda symbols, _n: payload(
            [quote("BRK.B"), quote("AAPL"), quote("MSFT")]
        )
    )
    source = build_source(monkeypatch, fake, pace_s=0.0)
    rows = await source.fetch_many(["brk.b", "aapl", "BRK.B", "msft"])
    assert fake.quote_calls[0]["symbols"] == ("BRK-B", "AAPL", "MSFT")
    assert set(rows) == {"BRK-B", "AAPL", "MSFT"}


async def test_no_api_key_is_read_anywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    if not MODULE_PATH.exists():
        raise NotImplementedError(f"{MODULE_PATH} does not exist yet (RED)")
    source_text = MODULE_PATH.read_text(encoding="utf-8")
    for token in ("import os", "os.environ", "getenv", "API_KEY", "api_key", "ApiKey"):
        assert token not in source_text, f"keyless source must not contain {token!r}"
    for name in KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    fake = FakeYahoo()
    source = build_source(monkeypatch, fake)
    rows = await source.fetch_many(["AAPL"])
    assert rows["AAPL"]["price"] == 231.5


async def test_batches_are_paced_by_a_quarter_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_module()
    if not hasattr(module, "_pace"):
        raise NotImplementedError("app.market.yahoo_batch._pace is missing (RED)")
    gaps: list[float] = []

    async def record(seconds: float) -> None:
        gaps.append(seconds)

    monkeypatch.setattr(module, "_pace", record)
    fake = FakeYahoo()
    source = build_source(monkeypatch, fake, pace_s=0.25)
    assert module.PACE_S >= 0.25
    await source.fetch_many([f"S{index:04d}" for index in range(950)])
    assert gaps == [0.25, 0.25]  # between batches only, never before the first


async def test_an_empty_request_does_no_http(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeYahoo()
    source = build_source(monkeypatch, fake)
    assert await source.fetch_many([]) == {}
    assert fake.cookie_calls == 0
    assert fake.crumb_calls == 0
    assert fake.quote_calls == []
