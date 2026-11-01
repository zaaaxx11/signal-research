"""Contract for ``GET /market/home`` — the dashboard's one request.

A home page paints seven blocks at once. Asking for them one at a time is seven
scans, seven universes and seven chances to describe a different market; asking
for them together is one document that is *honest about each block*:

* **every key is always present** and a block whose source failed is ``null``
  with its name in ``unavailable`` — never an empty list standing in for an
  error, because a client cannot tell ``[]`` from "nothing qualified";
* **the indices are five charters in a fixed order** (``^GSPC``, ``^IXIC``,
  ``^DJI``, ``^RUT``, ``^VIX``), each read through ``/market/chart``'s reader and
  cache, with the last 60 five-minute closes; a failed index is absent and named
  ``index:^GSPC``;
* **breadth counts the selector's universe** in one pass, and ``above_50`` /
  ``below_50`` read the depth store's ``sma50`` (a signed percent) — a row with
  no depth is *neither* above nor below, never below (adversarial (c): 1,359
  rows in the live universe carry no depth at all);
* **sparks are the eight highest-turnover rows** of that same universe, chosen
  deterministically per scan generation, with the last 30 closes from the same
  reader and the same 60 s cache;
* **movers reuse the signals builders** (``gainers``, ``new_high``,
  ``overbought``, ``new_low``), ten each, forty at most, every row naming its
  group;
* **map is ``/market/map`` truncated to the 600 largest tiles**, with
  ``{tiles, tiles_total}`` stating exactly what was cut — truncation drops whole
  trailing symbols inside a sector and never splits one;
* **news is the first three items of the market news merge** and **filings are
  the latest Form 4 per most-active symbol**, the block cached for fifteen
  minutes;
* **the document is memoised per (region, scan generation, depth identity, news
  bucket, index bucket)**, so a warm read is a dict lookup, a ``304`` costs one
  stat and no chart fetch, and two cold requests make one build.

The suite is hermetic: the scanner is a ``MockWeb`` over a handwritten scan
body, Yahoo chart bodies are fixtures, the news service is a fake and the Form 4
reader is a double — nothing reaches the network (``tests/conftest.py``).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest

from tests import _market as M
from tests import _screen as S
from tests._market import Clock

#: Every key the document carries, and every key one row of each block carries.
_DOCUMENT_KEYS = frozenset(
    {
        "as_of",
        "indices",
        "breadth",
        "sparks",
        "movers",
        "map",
        "news",
        "filings",
        "unavailable",
        "method",
    }
)
_INDEX_KEYS = frozenset({"symbol", "label", "price", "change_pct", "closes"})
_SPARK_KEYS = frozenset({"symbol", "name", "price", "change_pct", "volume", "closes"})
_MOVER_KEYS = frozenset({"symbol", "name", "price", "change_pct", "group"})
_BREADTH_KEYS = frozenset(
    {
        "universe",
        "advancing",
        "declining",
        "unchanged",
        "new_highs",
        "new_lows",
        "above_50",
        "below_50",
        "above_200",
        "below_200",
        "up_volume",
        "down_volume",
    }
)
_FILING_KEYS = frozenset({"symbol", "owner", "role", "filed"})
_NEWS_KEYS = frozenset({"headline", "url", "source", "published_at"})

#: The five charters, in the order the block publishes them.
_CHARTERS: tuple[tuple[str, str], ...] = (
    ("^GSPC", "S&P 500"),
    ("^IXIC", "Nasdaq Composite"),
    ("^DJI", "Dow Jones"),
    ("^RUT", "Russell 2000"),
    ("^VIX", "VIX"),
)

#: The four groups movers draws from, ten rows each at most.
_MOVER_GROUPS = ("gainers", "new_high", "overbought", "new_low")

#: The payload budget the route promises for the US tab.
_BUDGET = 60 * 1024

#: 2024-11-01 14:30 UTC onward, five minutes apart. A past session on purpose:
#: a holiday case has to show the *last* session's bars, not today's.
BASE_T = 1_730_471_400
STEP_S = 300


# ---------------------------------------------------------------------------
# A provider row, in the cells the scanner and the universe read
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Row:
    """One provider row with control over every cell these blocks read."""

    symbol: str
    cap: float | None = 1_000_000_000.0
    change: float | None = 1.0
    volume: float | None = 1_000.0
    turnover: float | None = 10_000.0
    sector: str = "Finance"
    high: float | None = 200.0
    low: float | None = 50.0
    rsi: float | None = 50.0
    price: float = 100.0
    description: str | None = None
    exchange: str = "NASDAQ"

    def cells(self) -> dict[str, Any]:
        return {
            "name": self.symbol,
            "description": self.description or f"{self.symbol} Corporation",
            "exchange": self.exchange,
            "country": "United States",
            "sector": self.sector,
            "type": "stock",
            "subtype": "common",
            "currency": "USD",
            "close": self.price,
            "change": self.change,
            "volume": self.volume,
            "Value.Traded": self.turnover,
            "market_cap_basic": self.cap,
            "price_52_week_high": self.high,
            "price_52_week_low": self.low,
            "RSI": self.rsi,
        }


def _scan_body(rows: Sequence[_Row], columns: Sequence[str]) -> dict[str, Any]:
    data = [
        {
            "s": f"{row.exchange}:{row.symbol}",
            "d": [row.cells().get(column) for column in columns],
        }
        for row in rows
    ]
    return {"totalCount": len(data), "data": data}


def _scanned(rows: Sequence[_Row]) -> Callable[[httpx2.Request], dict[str, Any]]:
    def respond(request: httpx2.Request) -> dict[str, Any]:
        body = json.loads(request.content)
        return _scan_body(rows, tuple(body["columns"]))

    return respond


def _series(count: int, *, start: float = 100.0, step: float = 0.5) -> list[float]:
    """``count`` closes, ascending, all strictly positive."""
    return [round(start + index * step, 2) for index in range(count)]


def _chart_payload(
    closes: Sequence[float],
    *,
    previous_close: float | None = None,
    start: int = BASE_T,
    step: int = STEP_S,
) -> dict[str, Any]:
    stamps = [start + index * step for index in range(len(closes))]
    meta: dict[str, Any] = {
        "exchangeTimezoneName": "America/New_York",
        "dataGranularity": "5m",
    }
    if previous_close is not None:
        meta["chartPreviousClose"] = previous_close
    return {
        "chart": {
            "result": [
                {
                    "meta": meta,
                    "timestamp": stamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": [close for close in closes],
                                "high": [close + 1.0 for close in closes],
                                "low": [close - 1.0 for close in closes],
                                "close": [close for close in closes],
                                "volume": [1_000.0 + index for index in range(len(closes))],
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }


def _chart_table(
    symbols: Sequence[str], *, bars: int = 79
) -> dict[str, dict[str, Any]]:
    return {
        symbol: _chart_payload(_series(bars, start=100.0 + index * 10), previous_close=100.0)
        for index, symbol in enumerate(symbols)
    }


def _chart_responder(
    table: Mapping[str, dict[str, Any] | None],
) -> Callable[[httpx2.Request], httpx2.Response]:
    """Answer Yahoo's chart endpoint from ``table``; an absent symbol is a 404."""

    def respond(request: httpx2.Request) -> httpx2.Response:
        symbol = request.url.path.rstrip("/").rsplit("/", 1)[-1]
        payload = table.get(symbol)
        if payload is None:
            return httpx2.Response(
                404,
                json={
                    "chart": {
                        "result": None,
                        "error": {"code": "Not Found", "description": "No data found"},
                    }
                },
            )
        return httpx2.Response(200, json=payload)

    return respond


class FakeNews:
    """A news service double: one document per symbol, or a total outage."""

    def __init__(
        self,
        *,
        fail: bool = False,
        fail_symbols: Sequence[str] = (),
        per_symbol: int = 3,
    ) -> None:
        self.fail = fail
        self.fail_symbols = set(fail_symbols)
        self.per_symbol = per_symbol
        self.calls: list[str] = []

    async def news(
        self, symbol: str, *, limit: int | None = None, now: Any = None
    ) -> dict[str, Any]:
        self.calls.append(symbol)
        if self.fail or symbol in self.fail_symbols:
            from app.news.service import AllNewsProvidersFailed

            raise AllNewsProvidersFailed(symbol, ["wire down"])
        index = len(self.calls)
        return {
            "symbol": symbol,
            "as_of": "2026-11-01T06:00:00+00:00",
            "items": [
                {
                    "headline": f"{symbol} headline {item}",
                    "url": f"https://news.example/{symbol.lower()}/{item}",
                    "source": "Wire",
                    "published_at": f"2026-11-01T0{6 + item}:00:00+00:00",
                }
                for item in range(self.per_symbol)
            ][: (limit or self.per_symbol)],
        }


def _fake_insider(
    *, filed: str = "2026-10-29", failures: Sequence[str] = ()
) -> Any:
    """A ``collect_insider`` double: one Form 4 row per symbol, or one failure.

    The returned callable carries ``calls`` — every symbol it was asked for —
    so a test can prove one build read one filing per most-active symbol.
    """

    failing = set(failures)
    calls: list[str] = []

    async def collect(
        symbol: str, cik: str, *, client: Any = None, cache_dir: Any = None, limit: int = 5
    ) -> tuple[tuple[SimpleNamespace, ...], tuple[str, ...]]:
        calls.append(symbol)
        if symbol in failing:
            return (), (f"{symbol}: body unreadable",)
        return (
            (
                SimpleNamespace(
                    owner_name=f"{symbol} Insider",
                    role="Officer: Chief Executive Officer",
                    filed=filed,
                ),
            ),
            (),
        )

    collect.calls = calls  # type: ignore[attr-defined]
    return collect


class _Master:
    """The identity master's only method these routes use."""

    def __init__(self, ciks: Mapping[str, str | None]) -> None:
        self._ciks = dict(ciks)

    def get(self, symbol: str) -> SimpleNamespace | None:
        if symbol not in self._ciks:
            return None
        return SimpleNamespace(symbol=symbol, cik=self._ciks[symbol])


# ---------------------------------------------------------------------------
# The harness: the real app, a mocked provider, a depth store, fake sources
# ---------------------------------------------------------------------------


@dataclass
class Harness:
    client: Any
    app: Any
    web: Any
    clock: Clock
    depth_path: Path
    news: FakeNews
    collected: list[str] = field(default_factory=list)

    async def get(self, path: str, **params: Any) -> Any:
        return await self.client.get(path, params=params or None)


@asynccontextmanager
async def home_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows: Sequence[_Row] | None = None,
    depth: Mapping[str, Mapping[str, Any]] | None = None,
    scan_status: int | None = None,
    charts: Mapping[str, Any] | None = None,
    news: FakeNews | None = None,
    ciks: Mapping[str, str | None] | None = None,
    insider: Any | None = None,
    scan_ttl: float = 5.0,
    clock: Clock | None = None,
) -> AsyncIterator[Harness]:
    """An ASGI client over the real app, with every external source doubled."""

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("MEE_PROVIDER_MODE", "live")
    monkeypatch.delenv("MEE_DEPTH", raising=False)
    monkeypatch.delenv("MEE_ENGINE_SNAPSHOT", raising=False)
    monkeypatch.delenv("MEE_ENGINE_SYMBOLS", raising=False)
    monkeypatch.delenv("MEE_ENGINE_CACHE_DIR", raising=False)

    depth_path = tmp_path / "depth.json"
    document: dict[str, Any] = {"as_of": "2026-11-01T05:52:34+00:00", "symbols": dict(depth or {})}
    depth_path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setenv("MEE_SCREEN_DEPTH_STORE", str(depth_path))
    monkeypatch.setenv("MEE_SCREEN_DEPTH", "true")

    web = S.MockWeb()
    if scan_status is not None:
        web.status("tradingview", scan_status)
    else:
        web.json("tradingview", _scanned(rows or ()))
    web.route("yahoo", _chart_responder(charts or {}))

    S.install_web(monkeypatch, web)

    from httpx2 import ASGITransport, AsyncClient

    from app.api.screens import ScanCache
    from app.db import ensure_schema_async
    from app.main import create_app

    application = create_app()
    await ensure_schema_async(Path(tmp_path / "mee.db"))
    test_clock = clock or Clock()
    application.state.scan_cache = ScanCache(ttl_s=scan_ttl, clock=test_clock)

    fake_news = news or FakeNews()
    application.state.news = fake_news

    from app.api.home import FilingsCache, HomeCaches, SeriesCache

    application.state.home_caches = HomeCaches(
        series=SeriesCache(clock=test_clock),
        filings=FilingsCache(clock=test_clock),
        clock=test_clock,
    )

    if ciks is not None:
        master = _Master(ciks)
        monkeypatch.setattr("app.api.home.security_master_of", lambda _request: master)
    if insider is not None:
        monkeypatch.setattr("app.api.home.collect_insider", insider)

    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport,
        base_url="http://mee.test",
        headers={"x-api-key": "home-key"},
    ) as client:
        yield Harness(
            client=client,
            app=application,
            web=web,
            clock=test_clock,
            depth_path=depth_path,
            news=fake_news,
        )


def _charts_for(rows: Sequence[_Row], *, bars: int = 79) -> dict[str, dict[str, Any]]:
    """Chart bodies for the five charters and every row a spark could pick."""

    symbols = [symbol for symbol, _label in _CHARTERS] + [row.symbol for row in rows]
    return _chart_table(symbols, bars=bars)


def _depth_for(rows: Sequence[_Row], *, above: int = 0, below: int = 0) -> dict[str, Any]:
    """``sma50`` for the first ``above + below`` rows; the rest carry no depth."""

    symbols = [row.symbol for row in rows]
    depth: dict[str, Any] = {}
    for index, symbol in enumerate(symbols[: above + below]):
        depth[symbol] = {"sma50": 1.5 if index < above else -1.5}
    return depth


def _small_rows(count: int = 20) -> list[_Row]:
    """``count`` rows: distinct caps, alternating changes, falling turnover."""

    rows: list[_Row] = []
    for index in range(count):
        rows.append(
            _Row(
                symbol=f"T{index:02d}",
                cap=1_000_000_000.0 + index * 1_000_000.0,
                change=(2.0 if index % 2 == 0 else -1.5),
                volume=500_000.0 - index * 1_000.0,
                turnover=90_000_000.0 - index * 1_000_000.0,
                high=200.0,
                low=10.0,
            )
        )
    return rows


def _mover_rows() -> list[_Row]:
    """Eighty rows: twenty for each of the four groups movers draws from.

    ``gainers`` needs a positive change, ``new_high`` a price at its 52-week
    high, ``overbought`` an RSI at or above 70 and ``new_low`` a price at its
    low, so each block gets twenty candidates and the ten-row cut is exercised.
    """

    rows: list[_Row] = []
    for index in range(20):
        rows.append(
            _Row(f"G{index:02d}", change=2.0, high=1_000.0, low=1.0, rsi=50.0,
                 turnover=50_000_000.0 - index * 1_000.0, volume=400_000.0 - index)
        )
        rows.append(
            _Row(f"H{index:02d}", change=-1.0, price=100.0, high=100.0, low=1.0, rsi=50.0,
                 turnover=40_000_000.0 - index * 1_000.0, volume=300_000.0 - index)
        )
        rows.append(
            _Row(f"O{index:02d}", change=-1.0, high=1_000.0, low=1.0, rsi=80.0,
                 turnover=30_000_000.0 - index * 1_000.0, volume=200_000.0 - index)
        )
        rows.append(
            _Row(f"L{index:02d}", change=-1.0, price=100.0, high=1_000.0, low=100.0, rsi=50.0,
                 turnover=20_000_000.0 - index * 1_000.0, volume=100_000.0 - index)
        )
    return rows


# ---------------------------------------------------------------------------
# Shape: the contract a client parses without looking anything up
# ---------------------------------------------------------------------------


async def test_the_document_has_every_key_and_each_block_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _small_rows(20)
    async with home_client(
        tmp_path,
        monkeypatch,
        rows=rows,
        depth=_depth_for(rows, above=6, below=4),
        charts=_charts_for(rows),
        ciks={row.symbol: "0000000001" for row in rows},
        insider=_fake_insider(),
    ) as harness:
        response = await harness.get("/market/home", region="us")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == _DOCUMENT_KEYS, sorted(body)
    assert body["as_of"]
    assert isinstance(body["method"], dict) and body["method"]
    assert body["unavailable"] == []

    assert len(body["indices"]) == 5
    assert all(set(row) == _INDEX_KEYS for row in body["indices"])
    assert all(isinstance(row["closes"], list) and row["closes"] for row in body["indices"])

    assert set(body["breadth"]) == _BREADTH_KEYS
    assert body["breadth"]["universe"] == len(rows)
    assert body["sparks"] and all(set(row) == _SPARK_KEYS for row in body["sparks"])
    assert body["movers"] and all(set(row) == _MOVER_KEYS for row in body["movers"])
    assert set(body["map"]) >= {"tiles", "tiles_total", "sectors"}
    assert body["news"] and all(set(row) == _NEWS_KEYS for row in body["news"])
    assert body["filings"] and all(set(row) == _FILING_KEYS for row in body["filings"])


async def test_the_indices_are_the_five_charters_in_their_published_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _small_rows(8)
    charts = _charts_for(rows, bars=79)
    async with home_client(
        tmp_path, monkeypatch, rows=rows, depth={}, charts=charts
    ) as harness:
        body = (await harness.get("/market/home", region="us")).json()

    assert [row["symbol"] for row in body["indices"]] == [symbol for symbol, _ in _CHARTERS]
    assert [row["label"] for row in body["indices"]] == [label for _symbol, label in _CHARTERS]

    for row in body["indices"]:
        closes = row["closes"]
        assert len(closes) == 60, "the block publishes the last 60 five-minute closes"
        assert row["price"] == closes[-1]
        assert row["change_pct"] == pytest.approx((closes[-1] / 100.0 - 1.0) * 100.0)


async def test_a_failed_index_is_absent_from_the_list_and_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _small_rows(8)
    charts = _charts_for(rows)
    charts.pop("^VIX")
    async with home_client(
        tmp_path, monkeypatch, rows=rows, depth={}, charts=charts
    ) as harness:
        response = await harness.get("/market/home", region="us")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["symbol"] for row in body["indices"]] == ["^GSPC", "^IXIC", "^DJI", "^RUT"]
    assert "index:^VIX" in body["unavailable"]


async def test_every_index_failing_leaves_null_not_an_empty_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _small_rows(4)
    async with home_client(
        tmp_path, monkeypatch, rows=rows, depth={}, charts={}
    ) as harness:
        response = await harness.get("/market/home", region="us")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["indices"] is None
    assert "indices" in body["unavailable"]


# ---------------------------------------------------------------------------
# Breadth: one pass over the selector's universe
# ---------------------------------------------------------------------------


async def test_breadth_partitions_the_universe_and_skips_missing_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        _Row("UP", change=2.0),
        _Row("DOWN", change=-1.0),
        _Row("FLAT", change=0.0),
        _Row("MISSING", change=None),
        _Row("HIGH", change=1.0, price=200.0, high=200.0),
        _Row("LOW", change=-2.0, price=50.0, low=50.0),
    ]
    async with home_client(
        tmp_path,
        monkeypatch,
        rows=rows,
        depth={},
        charts=_charts_for(rows),
    ) as harness:
        body = (await harness.get("/market/home", region="us")).json()

    breadth = body["breadth"]
    assert breadth["universe"] == 6
    assert breadth["advancing"] == 2
    assert breadth["declining"] == 2
    assert breadth["unchanged"] == 2, "a flat row and a null change both count as not moving"
    assert breadth["new_highs"] == 1
    assert breadth["new_lows"] == 1
    # Two thousand shares on each side: the flat row and the row with no change
    # have no side to attribute their volume to.
    assert breadth["up_volume"] == 2_000.0
    assert breadth["down_volume"] == 2_000.0


async def test_a_null_sma50_is_neither_above_nor_below(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adversarial (c): the depth-less rows must not be counted as below."""

    rows = [_Row(f"T{index:02d}") for index in range(10)]
    depth = _depth_for(rows, above=3, below=2)  # five rows carry no depth at all
    async with home_client(
        tmp_path, monkeypatch, rows=rows, depth=depth, charts=_charts_for(rows)
    ) as harness:
        body = (await harness.get("/market/home", region="us")).json()

    breadth = body["breadth"]
    assert breadth["above_50"] == 3
    assert breadth["below_50"] == 2
    assert breadth["above_50"] + breadth["below_50"] <= breadth["universe"]
    assert breadth["above_50"] + breadth["below_50"] == 5


async def test_breadth_agrees_with_the_maps_advancing_and_declining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One scan, one population: the two documents cannot disagree."""

    rows = _small_rows(30)
    async with home_client(
        tmp_path, monkeypatch, rows=rows, depth=_depth_for(rows), charts=_charts_for(rows)
    ) as harness:
        home = (await harness.get("/market/home", region="us")).json()
        treemap = (await harness.get("/market/map", region="us")).json()

    assert home["breadth"]["universe"] == treemap["universe_total"]
    assert home["breadth"]["advancing"] == sum(sector["adv"] for sector in treemap["sectors"])
    assert home["breadth"]["declining"] == sum(sector["dec"] for sector in treemap["sectors"])


# ---------------------------------------------------------------------------
# Sparks: the eight highest-turnover rows
# ---------------------------------------------------------------------------


async def test_sparks_are_the_eight_highest_turnover_rows_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _small_rows(20)
    async with home_client(
        tmp_path, monkeypatch, rows=rows, depth={}, charts=_charts_for(rows)
    ) as harness:
        body = (await harness.get("/market/home", region="us")).json()

    expected = [row.symbol for row in sorted(rows, key=lambda row: -row.turnover)[:8]]
    assert [row["symbol"] for row in body["sparks"]] == expected
    for row in body["sparks"]:
        assert len(row["closes"]) == 30, "a spark draws the last 30 closes"
        assert row["price"] == row["closes"][-1]
        assert row["volume"] == int(row["volume"])


async def test_a_failed_spark_series_is_absent_and_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _small_rows(12)
    charts = _charts_for(rows)
    missing = rows[0].symbol  # highest turnover, so it would be first
    charts.pop(missing)
    async with home_client(
        tmp_path, monkeypatch, rows=rows, depth={}, charts=charts
    ) as harness:
        body = (await harness.get("/market/home", region="us")).json()

    symbols = [row["symbol"] for row in body["sparks"]]
    assert missing not in symbols
    assert len(symbols) == 7
    assert f"spark:{missing}" in body["unavailable"]


# ---------------------------------------------------------------------------
# Movers: the signals builders, ten per group, forty at most
# ---------------------------------------------------------------------------


async def test_movers_are_at_most_forty_rows_and_name_their_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _mover_rows()
    async with home_client(
        tmp_path, monkeypatch, rows=rows, depth={}, charts=_charts_for(rows)
    ) as harness:
        body = (await harness.get("/market/home", region="us")).json()

    movers = body["movers"]
    assert len(movers) <= 40
    assert [row["group"] for row in movers] == [
        group for group in _MOVER_GROUPS for _ in range(10)
    ]
    counts: dict[str, int] = {}
    for row in movers:
        counts[row["group"]] = counts.get(row["group"], 0) + 1
    assert all(count <= 10 for count in counts.values())


async def test_movers_match_the_signals_groups_symbol_for_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The predicates are the signals module's, not a second copy of them."""

    rows = _mover_rows()
    async with home_client(
        tmp_path, monkeypatch, rows=rows, depth={}, charts=_charts_for(rows)
    ) as harness:
        home = (await harness.get("/market/home", region="us")).json()
        signals = (await harness.get("/market/signals", region="us", per="10")).json()

    published = {group["id"]: [row["symbol"] for row in group["rows"]] for group in signals["groups"]}
    for group in _MOVER_GROUPS:
        mine = [row["symbol"] for row in home["movers"] if row["group"] == group]
        assert mine == published[group][:10], group


# ---------------------------------------------------------------------------
# Map: 600 largest tiles of /market/map, never a split sector
# ---------------------------------------------------------------------------


async def test_the_map_is_the_six_hundred_largest_tiles_and_keeps_sectors_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = M.universe(900)
    async with home_client(
        tmp_path,
        monkeypatch,
        rows=[_Row(r.symbol, cap=r.cap, change=r.change, sector=r.sector or "Finance") for r in rows],
        depth={},
        charts={},
    ) as harness:
        home = (await harness.get("/market/home", region="us")).json()
        treemap = (await harness.get("/market/map", region="us")).json()

    block = home["map"]
    tiles = sum(len(sector["symbols"]) for sector in block["sectors"])
    assert tiles == 600 == block["tiles"]
    assert block["tiles_total"] == sum(len(sector["symbols"]) for sector in treemap["sectors"])
    assert block["tiles_total"] == treemap["mapped"] == 900

    full = {sector["id"]: sector for sector in treemap["sectors"]}
    assert [sector["id"] for sector in block["sectors"]] == [sector["id"] for sector in treemap["sectors"]]
    assert [sector["count"] for sector in block["sectors"]] == [
        sector["count"] for sector in treemap["sectors"]
    ]
    for sector in block["sectors"]:
        assert sector["symbols"] == full[sector["id"]]["symbols"][: len(sector["symbols"])], (
            "a truncation that splits a sector would drop a larger tile for a smaller one"
        )

    # The cut is at the 600th largest cap: everything kept is at least as big
    # as everything dropped.
    kept = {tile["s"] for sector in block["sectors"] for tile in sector["symbols"]}
    dropped = {
        tile["s"] for sector in treemap["sectors"] for tile in sector["symbols"]
    } - kept
    smallest_kept = min(
        tile["c"] for sector in block["sectors"] for tile in sector["symbols"]
    )
    assert all(tile["c"] <= smallest_kept for sector in treemap["sectors"] for tile in sector["symbols"] if tile["s"] in dropped)


# ---------------------------------------------------------------------------
# News and filings
# ---------------------------------------------------------------------------


async def test_news_is_the_first_forty_items_of_the_market_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _small_rows(8)
    async with home_client(
        tmp_path, monkeypatch, rows=rows, depth={}, charts=_charts_for(rows)
    ) as harness:
        home = (await harness.get("/market/home", region="us")).json()
        feed = (await harness.get("/market/news", limit="40")).json()

    assert len(home["news"]) == min(40, len(feed["items"]))
    for mine, theirs in zip(home["news"], feed["items"], strict=True):
        assert mine["headline"] == theirs["headline"]
        assert mine["url"] == theirs["url"]
        assert mine["published_at"] == theirs["published_at"]


async def test_filings_are_the_latest_form4_for_the_five_most_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _small_rows(20)
    ciks = {row.symbol: f"00000000{index:02d}" for index, row in enumerate(rows)}
    async with home_client(
        tmp_path,
        monkeypatch,
        rows=rows,
        depth={},
        charts=_charts_for(rows),
        ciks=ciks,
        insider=_fake_insider(filed="2026-10-29"),
    ) as harness:
        body = (await harness.get("/market/home", region="us")).json()

    most_active = [
        row.symbol for row in sorted(rows, key=lambda row: (-row.volume, row.symbol))[:5]
    ]
    assert [row["symbol"] for row in body["filings"]] == most_active
    assert all(row["filed"] == "2026-10-29" for row in body["filings"])
    assert body["filings"][0]["owner"] == f"{most_active[0]} Insider"
    assert body["filings"][0]["role"] == "Officer: Chief Executive Officer"


async def test_a_symbol_with_no_readable_filing_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _small_rows(20)
    ciks = {row.symbol: f"00000000{index:02d}" for index, row in enumerate(rows)}
    dead = [row.symbol for row in sorted(rows, key=lambda row: -row.volume)[:2]]
    async with home_client(
        tmp_path,
        monkeypatch,
        rows=rows,
        depth={},
        charts=_charts_for(rows),
        ciks=ciks,
        insider=_fake_insider(failures=dead),
    ) as harness:
        body = (await harness.get("/market/home", region="us")).json()

    assert len(body["filings"]) == 3
    assert not {row["symbol"] for row in body["filings"]} & set(dead)


async def test_every_filing_failing_is_null_and_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _small_rows(20)
    ciks = {row.symbol: "0000000001" for row in rows}
    async with home_client(
        tmp_path,
        monkeypatch,
        rows=rows,
        depth={},
        charts=_charts_for(rows),
        ciks=ciks,
        insider=_fake_insider(failures=[row.symbol for row in rows]),
    ) as harness:
        body = (await harness.get("/market/home", region="us")).json()

    assert body["filings"] is None
    assert "filings" in body["unavailable"]


# ---------------------------------------------------------------------------
# Adversarial: one dead source never takes the dashboard down
# ---------------------------------------------------------------------------


async def test_a_dead_scanner_leaves_null_blocks_and_still_answers_200(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _small_rows(8)
    async with home_client(
        tmp_path,
        monkeypatch,
        rows=rows,
        depth={},
        charts=_charts_for(rows),
        scan_status=500,
    ) as harness:
        response = await harness.get("/market/home", region="us")

    assert response.status_code == 200, response.text
    body = response.json()
    for name in ("breadth", "sparks", "movers", "map", "filings"):
        assert body[name] is None, name
        assert name in body["unavailable"], name
    assert body["indices"], "the chart reader has nothing to do with the scanner"
    assert body["news"], "the wires have nothing to do with the scanner"


async def test_a_dead_news_service_does_not_take_the_dashboard_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adversarial (a)."""

    rows = _small_rows(8)
    async with home_client(
        tmp_path,
        monkeypatch,
        rows=rows,
        depth={},
        charts=_charts_for(rows),
        news=FakeNews(fail=True),
    ) as harness:
        response = await harness.get("/market/home", region="us")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["news"] is None
    assert "news" in body["unavailable"]
    assert body["indices"] and body["breadth"] is not None


async def test_a_holiday_republishes_the_last_session_and_never_says_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adversarial (b): the bars are the last session's, and the body says so."""

    rows = _small_rows(4)
    charts = _charts_for(rows, bars=12)
    async with home_client(
        tmp_path, monkeypatch, rows=rows, depth={}, charts=charts
    ) as harness:
        response = await harness.get("/market/home", region="us")

    assert response.status_code == 200, response.text
    body = response.json()
    text = response.text.lower()
    assert "live" not in text, "a closed market must not be described as live"
    # The structure itself must never claim liveness, whatever a company or a
    # headline is called: the method block is the document speaking.
    assert "live" not in json.dumps(body["method"]).lower()
    assert body["method"]["session"]

    for row in body["indices"]:
        assert row["closes"] == _series(12, start=100.0 + [s for s, _ in _CHARTERS].index(row["symbol"]) * 10)
        assert len(row["closes"]) == 12, "a short session publishes what the provider has"


async def test_an_unknown_region_is_the_house_422(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _small_rows(2)
    async with home_client(
        tmp_path, monkeypatch, rows=rows, depth={}, charts=_charts_for(rows)
    ) as harness:
        response = await harness.get("/market/home", region="mars")

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_region"


# ---------------------------------------------------------------------------
# Caching, revalidation and the single build
# ---------------------------------------------------------------------------


async def test_two_reads_inside_the_bucket_call_each_source_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _small_rows(20)
    insider = _fake_insider()
    async with home_client(
        tmp_path,
        monkeypatch,
        rows=rows,
        depth=_depth_for(rows),
        charts=_charts_for(rows),
        ciks={row.symbol: "0000000001" for row in rows},
        insider=insider,
    ) as harness:
        first = await harness.get("/market/home", region="us")
        assert first.status_code == 200, first.text
        scans = harness.web.count("tradingview")
        charts = harness.web.count("yahoo")
        wires = len(harness.news.calls)
        filings = len(insider.calls)

        second = await harness.get("/market/home", region="us")

    assert second.status_code == 200, second.text
    assert second.content == first.content
    assert harness.web.count("tradingview") == scans == 1
    assert harness.web.count("yahoo") == charts == 13, "five indices and eight sparks"
    assert len(harness.news.calls) == wires == 12
    assert len(insider.calls) == filings == 5, "one Form 4 read per most-active symbol"
    assert scans and charts and wires and filings


async def test_the_tag_revalidates_in_one_stat_and_a_304_fetches_no_chart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _small_rows(20)
    async with home_client(
        tmp_path, monkeypatch, rows=rows, depth=_depth_for(rows), charts=_charts_for(rows)
    ) as harness:
        first = await harness.get("/market/home", region="us")
        assert first.status_code == 200, first.text
        charts = harness.web.count("yahoo")

        stats: list[str] = []
        real_stat = Path.stat

        def counting_stat(self: Path, *args: Any, **kwargs: Any) -> Any:
            stats.append(str(self))
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", counting_stat)
        # A revalidation must not rebuild process settings either: constructing
        # `Settings` costs ~8 ms, which is most of the 304 budget.
        monkeypatch.setattr(
            "app.config.get_settings",
            lambda *a, **k: pytest.fail("a 304 rebuilt the settings object"),
        )
        answer = await harness.client.get(
            "/market/home",
            params={"region": "us"},
            headers={"if-none-match": first.headers["etag"]},
        )
        monkeypatch.undo()

    assert answer.status_code == 304, answer.text
    assert answer.content == b""
    assert len(stats) == 1, f"a revalidation stat-ed {len(stats)} paths: {stats}"
    assert harness.web.count("yahoo") == charts, "a 304 paid a chart fetch"
    assert first.headers["cache-control"] == "no-cache"
    assert first.headers["etag"].startswith('W/"home-')


async def test_head_carries_the_get_length_and_none_of_its_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _small_rows(20)
    async with home_client(
        tmp_path, monkeypatch, rows=rows, depth=_depth_for(rows), charts=_charts_for(rows)
    ) as harness:
        get = await harness.get("/market/home", region="us")
        head = await harness.client.head("/market/home", params={"region": "us"})
        revalidated = await harness.client.head(
            "/market/home",
            params={"region": "us"},
            headers={"if-none-match": get.headers["etag"]},
        )

    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(get.content))
    assert head.headers["etag"] == get.headers["etag"]
    assert revalidated.status_code == 304


async def test_two_concurrent_cold_requests_make_one_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adversarial (d): single-flight, not two scans and two encodes."""

    rows = _small_rows(20)
    async with home_client(
        tmp_path,
        monkeypatch,
        rows=rows,
        depth=_depth_for(rows),
        charts=_charts_for(rows),
        ciks={row.symbol: "0000000001" for row in rows},
        insider=_fake_insider(),
    ) as harness:
        first, second = await asyncio.gather(
            harness.get("/market/home", region="us"),
            harness.get("/market/home", region="us"),
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.content == second.content
    assert harness.web.count("tradingview") == 1
    assert harness.web.count("yahoo") == 13
    assert len(harness.news.calls) == 12


async def test_the_document_stays_under_the_sixty_kilobyte_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The US tab, at the live tile count: 5,576 rows, 600 tiles, 13 series."""

    rows = M.universe(5_576)
    # Three of the live rows carry no change, which is why the map places 5,573
    # of 5,576 tiles — a difference the block has to publish, not hide.
    adapted = [
        _Row(r.symbol, cap=r.cap, change=None if index < 3 else r.change, sector=r.sector or "Finance")
        for index, r in enumerate(rows)
    ]
    depth = {
        row.symbol: {"sma50": 1.0 if index % 2 else -1.0}
        for index, row in enumerate(rows[:4_187])
    }
    async with home_client(
        tmp_path,
        monkeypatch,
        rows=adapted,
        depth=depth,
        charts=_charts_for([_Row(f"S{index:04d}") for index in range(8)], bars=79),
        ciks={f"S{index:04d}": "0000000001" for index in range(8)},
        insider=_fake_insider(),
    ) as harness:
        response = await harness.get("/market/home", region="us")
        treemap = (await harness.get("/market/map", region="us")).json()

    assert response.status_code == 200, response.text
    raw = len(response.content)
    with capsys.disabled():
        print(f"\nGET /market/home raw payload: {raw} bytes ({raw / 1024:.1f} KB)")
    assert raw <= _BUDGET, f"{raw} bytes exceeds the {_BUDGET}-byte budget"

    body = response.json()
    assert sum(len(sector["symbols"]) for sector in body["map"]["sectors"]) == 600
    assert body["map"]["tiles_total"] == treemap["mapped"] == 5_573
    assert body["breadth"]["universe"] == treemap["universe_total"] == 5_576
