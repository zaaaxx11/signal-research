"""Contract for ``GET /market/chart/{symbol}`` — the OHLC series a browser draws.

What is pinned here, in the order the route computes it:

* **the document** — ``{symbol, range, interval, timezone, previous_close, bars,
  source, truncated}``, every bar ``{t, o, h, l, c, v}`` with ``t`` in epoch
  seconds and every price a float;
* **a bar that has no close is dropped, never zero-filled** — and neither is a
  bar whose close is zero, which is what a market-closed day looks like when a
  provider sends a quote row instead of a null (adversarial (a));
* **``previous_close`` is the previous *session*** — Yahoo's
  ``chartPreviousClose``, which on a Monday is Friday's close, and which for a
  long window is the close before the window rather than yesterday's
  (adversarial (b));
* **the interval defaults per range** (``1d``→``5m``, ``5d``→``15m``,
  ``1mo``/``6mo``/``1y``→``1d``) and a pair outside the legal table is a ``422
  invalid_interval`` that names what is legal — before any fetch;
* **the five-minute cache** — the second request is answered from the parsed
  series, proved by a fetch double that raises on its second call, and keyed by
  ``(symbol, range, interval)`` so one symbol's series can never answer another
  (adversarial (d));
* **the validator** — a weak ``W/"chart-…"`` tag, ``Cache-Control: no-cache``,
  a ``304`` with no fetch at all (the double raises and the answer is still
  ``304``), and a ``HEAD`` that carries the ``GET``'s length and none of its
  bytes;
* **an unknown symbol is a ``404 unknown_symbol``**, not an empty chart, and a
  symbol that is a path traversal or an injection attempt is the same ``404`` —
  never a ``500`` and never a provider request;
* **the budgets** — one year of daily bars under 60 KB, cold under 2 s, warm
  under 5 ms, ``304`` under 2 ms.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx2
import pytest

from tests import _market as M

_DOCUMENT_KEYS = frozenset(
    {
        "symbol",
        "range",
        "interval",
        "timezone",
        "previous_close",
        "bars",
        "source",
        "truncated",
    }
)
_BAR_KEYS = frozenset({"t", "o", "h", "l", "c", "v"})

#: 2024-11-01 14:30 UTC onward, five minutes apart. A past date on purpose: the
#: metrics parser drops a bar dated *today* while the US session is open, and the
#: parity case below feeds the same payload to both parsers.
BASE_T = 1_730_471_400
STEP_S = 300


def _payload(
    rows: list[tuple[float | None, float | None, float | None, float | None, float | None]],
    *,
    symbol: str = "AAPL",
    previous_close: float | None = 100.0,
    previous_key: str = "chartPreviousClose",
    timezone: str = "America/New_York",
    start: int = BASE_T,
    step: int = STEP_S,
) -> dict[str, Any]:
    """A Yahoo v8 chart body with ``rows`` as ``(open, high, low, close, volume)``.

    A ``None`` in a column is the provider's own null for that bar — the shape a
    halt or a market-closed day produces.
    """

    stamps: list[int] = []
    quote: dict[str, list[Any]] = {"open": [], "high": [], "low": [], "close": [], "volume": []}
    for index, (open_, high, low, close, volume) in enumerate(rows):
        stamps.append(start + index * step)
        quote["open"].append(open_)
        quote["high"].append(high)
        quote["low"].append(low)
        quote["close"].append(close)
        quote["volume"].append(volume)
    meta: dict[str, Any] = {
        "symbol": symbol,
        "exchangeTimezoneName": timezone,
        "dataGranularity": "5m",
    }
    if previous_close is not None:
        meta[previous_key] = previous_close
    return {
        "chart": {
            "result": [
                {
                    "meta": meta,
                    "timestamp": stamps,
                    "indicators": {"quote": [quote]},
                }
            ],
            "error": None,
        }
    }


def _bars(*closes: float, spread: float = 1.0) -> list[tuple[Any, ...]]:
    """``len(closes)`` well-formed bars around each close."""

    return [
        (close - spread, close + spread, close - spread, close, 1_000.0 + index)
        for index, close in enumerate(closes)
    ]


def _long(rows: int, *, start: float = 100.0) -> list[tuple[Any, ...]]:
    """``rows`` daily bars, one calendar day apart, drifting upward."""

    return [
        (start + i, start + i + 1.0, start + i - 1.0, start + i + 0.5, 1_000.0 + i)
        for i in range(rows)
    ]


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


async def test_the_document_and_its_bars_have_exactly_these_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(_bars(100.0, 101.5, 99.25)))
        response = await harness.get("/market/chart/AAPL")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == _DOCUMENT_KEYS, sorted(body)
    assert body["symbol"] == "AAPL"
    assert body["range"] == "1d"
    assert body["interval"] == "5m"
    assert body["timezone"] == "America/New_York"
    assert body["source"] == "yahoo_chart"
    assert body["truncated"] is False
    assert len(body["bars"]) == 3
    for bar in body["bars"]:
        assert set(bar) == _BAR_KEYS, sorted(bar)
        assert isinstance(bar["t"], int)
        for key in ("o", "h", "l", "c", "v"):
            assert isinstance(bar[key], float), (key, bar[key])
    assert [bar["t"] for bar in body["bars"]] == [BASE_T, BASE_T + 300, BASE_T + 600]
    assert [bar["c"] for bar in body["bars"]] == [100.0, 101.5, 99.25]


async def test_the_symbol_is_canonicalised_for_the_key_the_url_and_the_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(_bars(100.0), symbol="AAPL"))
        body = (await harness.get("/market/chart/aapl")).json()
        asked = harness.web.requests_for("yahoo")[0].url

    assert body["symbol"] == "AAPL"
    assert "/chart/AAPL?" in asked


@pytest.mark.parametrize("symbol", ["^GSPC", "BTC-USD", "EURUSD=X", "ES=F", "0005.HK"])
async def test_the_five_shapes_of_a_ticker_are_all_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, symbol: str
) -> None:
    """Indices, crypto, forex, futures and a foreign listing are one route."""

    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(_bars(100.0), symbol=symbol))
        response = await harness.get(f"/market/chart/{symbol}")

    assert response.status_code == 200, response.text
    assert response.json()["symbol"] == symbol


# ---------------------------------------------------------------------------
# Dropping, never zero-filling (adversarial (a))
# ---------------------------------------------------------------------------


async def test_a_bar_without_a_close_is_dropped_and_the_rest_survive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        (100.0, 101.0, 99.0, 100.5, 1_000.0),
        (100.5, 101.5, 100.0, None, 900.0),  # a halt: the provider sent no close
        (100.5, 102.0, 100.0, 101.0, 1_100.0),
        (None, None, None, None, None),  # an empty timestamp row
        (101.0, 103.0, 100.5, 102.5, 1_200.0),
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(rows))
        body = (await harness.get("/market/chart/AAPL")).json()

    assert [bar["t"] for bar in body["bars"]] == [BASE_T, BASE_T + 600, BASE_T + 1200]
    assert [bar["c"] for bar in body["bars"]] == [100.5, 101.0, 102.5]
    assert all(bar["c"] != 0.0 for bar in body["bars"])


async def test_a_market_closed_bar_with_a_zero_close_is_not_a_bar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A closed session arrives as zeros; a price of zero is not a price."""

    rows = [
        (100.0, 101.0, 99.0, 100.5, 1_000.0),
        (0.0, 0.0, 0.0, 0.0, 0.0),
        (100.5, 102.0, 100.0, 101.0, 1_100.0),
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(rows))
        body = (await harness.get("/market/chart/AAPL")).json()

    assert [bar["c"] for bar in body["bars"]] == [100.5, 101.0]
    assert len(body["bars"]) == 2


async def test_a_bar_whose_range_contradicts_its_close_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``high < close`` is a corrupt bar, and ``app.market.chart`` drops it too."""

    rows = [
        (100.0, 101.0, 99.0, 100.5, 1_000.0),
        (100.0, 99.0, 98.0, 100.5, 900.0),  # high below the close
        (100.5, 102.0, 100.0, 101.0, 1_100.0),
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(rows))
        body = (await harness.get("/market/chart/AAPL")).json()

    assert [bar["c"] for bar in body["bars"]] == [100.5, 101.0]


async def test_a_volume_of_null_is_zero_but_the_bar_stays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Volume is the one column a bar can live without; absence is 0 shares."""

    rows = [(100.0, 101.0, 99.0, 100.5, None)]
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(rows))
        body = (await harness.get("/market/chart/AAPL")).json()

    assert body["bars"][0]["v"] == 0.0


def test_the_series_parser_and_the_depth_parser_keep_the_same_bars() -> None:
    """One set of drop rules: the two parsers must not disagree about a payload.

    ``app.market.chart`` collapses each bar to a ``date``, which is why an
    intraday series cannot call it — so the route keeps its own loop but shares
    the coercion (:func:`app.market.chart._number`) and the invariants. This
    pins that the invariants really are the same: five daily bars, one with no
    close and one whose high contradicts its close, leave the same three for
    both parsers, and the metrics parser agrees on the window's extremes.
    """

    from datetime import UTC, datetime

    from app.api.chart import parse_chart_series
    from app.market.chart import parse_chart_metrics

    # A window ending two days ago: `parse_chart_metrics` trims a bar dated
    # today while the US session is open, and this case is about the drop rules.
    start = int(datetime.now(UTC).timestamp()) - 6 * 86_400
    payload = _payload(
        [
            (100.0, 101.0, 99.0, 100.5, 1_000.0),
            (100.5, 101.5, 100.0, None, 900.0),
            (100.5, 102.0, 100.0, 101.0, 1_100.0),
            (101.0, 100.0, 99.0, 101.5, 1_200.0),
            (101.5, 103.0, 101.0, 102.0, 1_300.0),
        ],
        start=start,
        step=86_400,
    )
    entry = payload["chart"]["result"][0]
    series = parse_chart_series(entry)
    metrics = parse_chart_metrics(entry)

    assert len(series.bars) == 3
    assert [bar["c"] for bar in series.bars] == [100.5, 101.0, 102.0]
    assert metrics["high_52w"] == 103.0
    assert metrics["low_52w"] == 99.0


# ---------------------------------------------------------------------------
# previous_close and timezone (adversarial (b))
# ---------------------------------------------------------------------------


async def test_previous_close_is_chart_previous_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(_bars(100.0), previous_close=97.25))
        body = (await harness.get("/market/chart/AAPL")).json()

    assert body["previous_close"] == 97.25


async def test_previous_close_falls_back_to_the_meta_previous_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json(
            "yahoo", _payload(_bars(100.0), previous_close=96.5, previous_key="previousClose")
        )
        body = (await harness.get("/market/chart/AAPL")).json()

    assert body["previous_close"] == 96.5


async def test_a_missing_previous_close_is_null_not_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(_bars(100.0), previous_close=None))
        body = (await harness.get("/market/chart/AAPL")).json()

    assert body["previous_close"] is None


async def test_the_timezone_is_the_exchange_s_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(_bars(100.0), timezone="Europe/London"))
        body = (await harness.get("/market/chart/EURUSD=X")).json()

    assert body["timezone"] == "Europe/London"


# ---------------------------------------------------------------------------
# The interval table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("range_", "interval"), [("1d", "5m"), ("5d", "15m"), ("1mo", "1d"), ("6mo", "1d"), ("1y", "1d")])
async def test_the_default_interval_follows_the_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, range_: str, interval: str
) -> None:
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(_bars(100.0)))
        body = (await harness.get("/market/chart/AAPL", range=range_)).json()
        asked = harness.web.requests_for("yahoo")[0].url

    assert body["range"] == range_
    assert body["interval"] == interval
    assert f"interval={interval}" in asked
    assert f"range={range_}" in asked


@pytest.mark.parametrize(
    ("range_", "interval"),
    [("1d", "1m"), ("1d", "1h"), ("5d", "5m"), ("1mo", "15m"), ("6mo", "1h"), ("1y", "1h")],
)
async def test_a_legal_interval_pair_is_honoured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, range_: str, interval: str
) -> None:
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(_bars(100.0)))
        body = (await harness.get("/market/chart/AAPL", range=range_, interval=interval)).json()
        asked = harness.web.requests_for("yahoo")[0].url

    assert body["interval"] == interval
    assert f"interval={interval}" in asked


@pytest.mark.parametrize(
    ("range_", "asked"),
    [("1d", "1d"), ("5d", "1m"), ("1mo", "1m"), ("1y", "5m"), ("1d", "bogus"), ("1d", "")],
)
async def test_an_illegal_interval_names_what_is_legal_and_costs_no_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, range_: str, asked: str
) -> None:
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(_bars(100.0)))
        response = await harness.get("/market/chart/AAPL", range=range_, interval=asked)

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "invalid_interval"
    assert asked in body["errorMessage"]
    assert "1d" in body["errorMessage"] or "5m" in body["errorMessage"]
    assert harness.web.count("yahoo") == 0


async def test_an_unknown_range_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(_bars(100.0)))
        response = await harness.get("/market/chart/AAPL", range="7y")

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_range"
    assert harness.web.count("yahoo") == 0


async def test_the_invalid_interval_message_lists_the_ranges_own_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``1d`` allows intraday only; the sentence says exactly which ones."""

    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(_bars(100.0)))
        body = (await harness.get("/market/chart/AAPL", range="1d", interval="1d")).json()

    assert "1m" in body["errorMessage"]
    assert "5m" in body["errorMessage"]
    assert "15m" in body["errorMessage"]
    assert "1h" in body["errorMessage"]


# ---------------------------------------------------------------------------
# The five-minute cache
# ---------------------------------------------------------------------------


async def test_the_second_request_is_answered_from_the_parsed_series(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = {"n": 0}

    def once(request: httpx2.Request) -> httpx2.Response:
        seen["n"] += 1
        if seen["n"] > 1:
            raise AssertionError("the five-minute cache did not answer the second request")
        return httpx2.Response(200, json=_payload(_bars(100.0, 101.0)))

    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.route("yahoo", once)
        first = await harness.get("/market/chart/AAPL")
        second = await harness.get("/market/chart/AAPL")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.headers["x-chart-cache"] == "miss"
    assert second.headers["x-chart-cache"] == "hit"
    assert first.content == second.content
    assert first.headers["etag"] == second.headers["etag"]
    assert seen["n"] == 1


async def test_the_cache_is_keyed_by_symbol_so_one_series_cannot_answer_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adversarial (d): a key that forgets the symbol serves AAPL for MSFT."""

    def by_symbol(request: httpx2.Request) -> httpx2.Response:
        symbol = request.url.path.rsplit("/", 1)[-1]
        price = 100.0 if symbol == "AAPL" else 200.0
        return httpx2.Response(200, json=_payload(_bars(price), symbol=symbol))

    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.route("yahoo", by_symbol)
        aapl = (await harness.get("/market/chart/AAPL")).json()
        msft = (await harness.get("/market/chart/MSFT")).json()
        again = (await harness.get("/market/chart/AAPL")).json()
        count = harness.web.count("yahoo")

    assert aapl["symbol"] == "AAPL" and aapl["bars"][0]["c"] == 100.0
    assert msft["symbol"] == "MSFT" and msft["bars"][0]["c"] == 200.0
    assert again["bars"][0]["c"] == 100.0
    assert count == 2


async def test_the_cache_is_keyed_by_range_and_interval_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(_bars(100.0)))
        await harness.get("/market/chart/AAPL", range="1d", interval="5m")
        await harness.get("/market/chart/AAPL", range="1d", interval="15m")
        await harness.get("/market/chart/AAPL", range="5d", interval="15m")

    assert harness.web.count("yahoo") == 3


async def test_the_cache_expires_after_five_minutes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = M.Clock(start=1_000.0)
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        from app.api.chart import ChartCache

        harness.app.state.chart_cache = ChartCache(ttl_s=300.0, clock=clock)
        harness.web.json("yahoo", _payload(_bars(100.0)))
        first = await harness.get("/market/chart/AAPL")
        second = await harness.get("/market/chart/AAPL")
        clock.advance(299.0)
        still = await harness.get("/market/chart/AAPL")
        clock.advance(2.0)
        expired = await harness.get("/market/chart/AAPL")

    assert [first.headers["x-chart-cache"], second.headers["x-chart-cache"]] == ["miss", "hit"]
    assert still.headers["x-chart-cache"] == "hit"
    assert expired.headers["x-chart-cache"] == "miss"
    assert harness.web.count("yahoo") == 2
    assert expired.headers["etag"] != first.headers["etag"]


# ---------------------------------------------------------------------------
# Validators: ETag, 304, HEAD
# ---------------------------------------------------------------------------


async def test_the_tag_is_weak_and_the_cache_control_is_no_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(_bars(100.0)))
        response = await harness.get("/market/chart/AAPL")

    assert response.headers["etag"].startswith('W/"chart-')
    assert response.headers["cache-control"] == "no-cache"


async def test_a_matching_tag_is_a_304_with_no_fetch_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The double raises on every call: a fetch would be a 500, not a 304."""

    def refuse(_request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("a 304 must not fetch")

    clock = M.Clock(start=1_000.0)
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        from app.api.chart import ChartCache

        harness.app.state.chart_cache = ChartCache(ttl_s=300.0, clock=clock)
        harness.web.json("yahoo", _payload(_bars(100.0)))
        first = await harness.get("/market/chart/AAPL")
        harness.web.route("yahoo", refuse)
        revalidated = await harness.client.get(
            "/market/chart/AAPL", headers={"if-none-match": first.headers["etag"]}
        )
        # A cold process — nothing cached — still answers the tag from the inputs
        # it was asked with, so a restart does not force a 200.
        harness.app.state.chart_cache = ChartCache(ttl_s=300.0, clock=clock)
        cold = await harness.client.get(
            "/market/chart/AAPL", headers={"if-none-match": first.headers["etag"]}
        )

    assert revalidated.status_code == 304
    assert revalidated.content == b""
    assert revalidated.headers["etag"] == first.headers["etag"]
    assert revalidated.headers["x-chart-cache"] == "hit"
    assert cold.status_code == 304
    assert cold.headers["x-chart-cache"] == "miss"
    assert harness.web.count("yahoo") == 1


async def test_head_answers_the_get_headers_and_none_of_the_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(_bars(100.0, 101.0)))
        got = await harness.get("/market/chart/AAPL")
        head = await harness.client.head("/market/chart/AAPL")
        revalidated = await harness.client.head(
            "/market/chart/AAPL", headers={"if-none-match": got.headers["etag"]}
        )

    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(got.content))
    assert head.headers["etag"] == got.headers["etag"]
    assert head.headers["cache-control"] == "no-cache"
    assert revalidated.status_code == 304


async def test_the_tag_moves_when_the_range_or_interval_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(_bars(100.0)))
        one = await harness.get("/market/chart/AAPL", range="1d")
        two = await harness.get("/market/chart/AAPL", range="5d")
        three = await harness.get("/market/chart/AAPL", range="1d", interval="15m")

    assert one.headers["etag"] != two.headers["etag"]
    assert one.headers["etag"] != three.headers["etag"]


# ---------------------------------------------------------------------------
# Unknown symbols and hostile symbols
# ---------------------------------------------------------------------------


async def test_an_unknown_symbol_is_a_404_not_an_empty_chart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.status("yahoo", 404)
        response = await harness.get("/market/chart/ZZZZNOPE")

    assert response.status_code == 404, response.text
    assert response.json()["error"] == "unknown_symbol"


async def test_a_provider_error_body_with_no_result_is_a_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "chart": {
            "result": None,
            "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"},
        }
    }
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", payload)
        response = await harness.get("/market/chart/ZZZZNOPE")

    assert response.status_code == 404
    assert response.json()["error"] == "unknown_symbol"


@pytest.mark.parametrize(
    "path",
    [
        "/market/chart/..%2F..%2Fetc%2Fpasswd",
        "/market/chart/%2E%2E%2Fetc%2Fpasswd",
        "/market/chart/AAPL%3BDROP",
        "/market/chart/AAPL;DROP",
        "/market/chart/..",
    ],
)
async def test_a_traversal_or_injection_symbol_is_a_404_never_a_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(_bars(100.0)))
        response = await harness.client.get(path)

    assert response.status_code == 404, (path, response.status_code, response.text)
    assert harness.web.count("yahoo") == 0


async def test_a_provider_outage_is_a_503_in_the_house_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.status("yahoo", 500)
        response = await harness.get("/market/chart/AAPL")

    assert response.status_code == 503
    assert response.json()["error"] == "chart_unavailable"


# ---------------------------------------------------------------------------
# Size, truncation, and the timing goals
# ---------------------------------------------------------------------------


async def test_a_year_of_daily_bars_is_under_sixty_kilobytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """251 sessions, real prices and volumes: the whole body, not just the bars."""

    rows = [
        (
            round(100.0 + i * 0.31, 2),
            round(101.0 + i * 0.31, 2),
            round(99.0 + i * 0.31, 2),
            round(100.4 + i * 0.31, 2),
            float(12_345_678 + i * 1_009),
        )
        for i in range(251)
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(rows, symbol="AAPL", previous_close=99.5))
        response = await harness.get("/market/chart/AAPL", range="1y")

    body = response.json()
    assert len(body["bars"]) == 251
    assert body["truncated"] is False
    assert len(response.content) < 60_000, len(response.content)


async def test_an_overlong_series_is_truncated_to_the_most_recent_bars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A year of hourly bars is ~1,750; the chart keeps the right edge."""

    from app.api.chart import MAX_BARS

    rows = [
        (
            round(100.0 + i * 0.01, 2),
            round(100.6 + i * 0.01, 2),
            round(99.6 + i * 0.01, 2),
            round(100.3 + i * 0.01, 2),
            float(1_000 + i),
        )
        for i in range(MAX_BARS + 200)
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(rows, step=3_600))
        body = (await harness.get("/market/chart/AAPL", range="1y", interval="1h")).json()

    assert body["truncated"] is True
    assert len(body["bars"]) == MAX_BARS
    assert body["bars"][-1]["t"] == BASE_T + (len(rows) - 1) * 3_600
    assert body["bars"][0]["t"] == BASE_T + 200 * 3_600


async def _fastest(
    client: Any,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    runs: int = 15,
) -> tuple[Any, float]:
    """The answer to ``path`` and the fastest of ``runs`` round trips, in ms.

    The fastest, not the median: this suite runs on a shared box, and a
    scheduler hiccup is not a regression. What the budget names is the cost of
    the path, which a single uninterrupted round trip does measure.
    """

    best = float("inf")
    response = None
    for _ in range(runs):
        mark = time.perf_counter()
        response = await client.get(path, params=params, headers=headers)
        best = min(best, (time.perf_counter() - mark) * 1_000.0)
    return response, best


async def test_cold_warm_and_304_stay_inside_the_budgets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold under 2 s; warm under 5 ms and 304 under 2 ms **above the floor**.

    Absolute milliseconds on a loaded machine measure the machine — the whole
    suite runs in one process, and two sibling timing tests in this repository
    have already failed at 5.3 ms against a 5 ms budget while the route itself
    costs half a millisecond. So the two tight budgets are measured as **deltas
    against the cheapest route in the same app** (``/market/calendar``: one
    ``stat``, one memo lookup, one response), timed in the same loop. A route
    that doubled its work would break the delta; a busy box would not.
    """

    rows = [
        (
            round(100.0 + i * 0.31, 2),
            round(101.0 + i * 0.31, 2),
            round(99.0 + i * 0.31, 2),
            round(100.4 + i * 0.31, 2),
            float(12_345_678 + i * 1_009),
        )
        for i in range(251)
    ]
    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(rows, symbol="AAPL"))
        started = time.perf_counter()
        first = await harness.get("/market/chart/AAPL", range="1y")
        cold_ms = (time.perf_counter() - started) * 1_000.0
        tag = first.headers["etag"]

        # The floor: the app's own cheapest document and its own cheapest 304.
        floor, floor_ms = await _fastest(harness.client, "/market/calendar")
        floor_tag = floor.headers["etag"]
        _, floor_304_ms = await _fastest(
            harness.client, "/market/calendar", headers={"if-none-match": floor_tag}
        )

        warm_response, warm_ms = await _fastest(
            harness.client, "/market/chart/AAPL", params={"range": "1y"}
        )
        answer, cheap_ms = await _fastest(
            harness.client,
            "/market/chart/AAPL",
            params={"range": "1y"},
            headers={"if-none-match": tag},
        )

    assert first.status_code == 200
    assert first.headers["x-chart-cache"] == "miss"
    assert warm_response.headers["x-chart-cache"] == "hit"
    assert answer.status_code == 304
    assert cold_ms < 2_000.0, f"cold {cold_ms:.1f} ms"
    assert warm_ms - floor_ms < 5.0, f"warm {warm_ms:.2f} ms over a {floor_ms:.2f} ms floor"
    assert cheap_ms - floor_304_ms < 2.0, (
        f"304 {cheap_ms:.2f} ms over a {floor_304_ms:.2f} ms floor"
    )


async def test_five_years_is_a_range_the_route_speaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``range=5y`` answers daily bars, and five years of them fit whole.

    A year of daily bars is ~252, so five years is ~1,260 — over the
    thousand-bar default. The long window has its own cap, and
    ``truncated`` stays false, or a reader would be told the series is
    whole while the oldest year was silently dropped.
    """

    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(_long(1_300)))
        response = await harness.get("/market/chart/AAPL", range="5y")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["range"] == "5y"
    assert body["interval"] == "1d"
    assert len(body["bars"]) == 1_300
    assert body["truncated"] is False


async def test_five_years_speaks_daily_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The provider accepts an hourly bar for at most two years, so ``5y``
    refuses ``1h`` by name rather than forwarding a pair that would fail
    upstream."""

    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(_bars(100.0)))
        response = await harness.get("/market/chart/AAPL", range="5y", interval="1h")

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_interval"
    assert "1d" in body["errorMessage"]
    assert harness.web.count("yahoo") == 0


async def test_the_invalid_range_message_names_five_years(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller who asked for ``7y`` is told the whole legal set, and the
    set now contains five years."""

    async with M.market_client(tmp_path, monkeypatch, rows=[], depth={}) as harness:
        harness.web.json("yahoo", _payload(_bars(100.0)))
        response = await harness.get("/market/chart/AAPL", range="7y")

    assert response.status_code == 422
    message = response.json()["errorMessage"]
    assert "5y" in message
    assert "1y" in message
