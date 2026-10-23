"""Shared fixtures for the P8 realtime quote suite (``GET /market/quote``).

Not a test module (leading underscore), so pytest never collects it.

Contracts pinned here
---------------------

* **One HTTP seam.** Both sources must call ``app.providers.http.build_client``
  as a module attribute; :func:`tests._phase2.install_web` replaces that
  attribute with an ``httpx2.MockTransport``. A source that builds its own
  client raises the conftest egress guard instead of passing.
* **Yahoo's protocol is three requests.** ``fc.yahoo.com`` (cookie) →
  ``/v1/test/getcrumb`` (token) → ``/v7/finance/quote?symbols=...&crumb=...``.
  The responder below answers all three by path, so a test that only routes the
  data request fails loudly instead of silently leaking a socket.
* **Yahoo omits unknown symbols** from ``quoteResponse.result`` (probed live);
  **Nasdaq answers ``data: null``** for them. Both mean "no quote", never ``0``.
* **Payload shapes** come from live probes on 2026-10-23: v7 prints
  ``regularMarketPreviousClose``/``regularMarketVolume``/``regularMarketTime``
  (epoch seconds); Nasdaq prints ``"$231.50"``, ``"28,960,975.244719"``,
  ``"Oct 23, 2026 3:48 PM ET"`` and ``isRealTime``.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Collection, Mapping, Sequence
from zoneinfo import ZoneInfo

import httpx2

from tests._phase2 import MockWeb

YAHOO_API_HOST = "query1.finance.yahoo.com"
YAHOO_COOKIE_HOST = "fc.yahoo.com"
NASDAQ_HOST = "api.nasdaq.com"

EASTERN = ZoneInfo("America/New_York")

#: The crumb the fake handshake hands out; tests assert it rides the data URL.
TEST_CRUMB = "test-crumb-1"


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def now_ts() -> int:
    """Epoch seconds for "fresh": the suite runs on the real wall clock."""

    return int(time.time())


def yahoo_row(
    symbol: str,
    *,
    price: Any = 231.5,
    prev_close: Any = 229.0,
    volume: Any = 1_234_567,
    ts: int | float | None = None,
) -> dict[str, Any]:
    """One ``quoteResponse.result[]`` entry, as v7 prints it."""

    return {
        "symbol": symbol,
        "currency": "USD",
        "regularMarketPrice": price,
        "regularMarketPreviousClose": prev_close,
        "regularMarketVolume": volume,
        "regularMarketTime": int(now_ts() if ts is None else ts),
    }


def yahoo_body(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {"quoteResponse": {"result": list(rows), "error": None}}


def et_stamp(epoch: int | float) -> str:
    """The ET sentence Nasdaq publishes for an instant."""

    return (
        datetime.fromtimestamp(epoch, UTC)
        .astimezone(EASTERN)
        .strftime("%b %d, %Y %I:%M %p ET")
    )


def nasdaq_body(
    symbol: str,
    *,
    price: Any = "$231.50",
    volume: Any = "28,960,975.244719",
    ts: int | float | None = None,
    realtime: bool | None = True,
    data: bool = True,
) -> dict[str, Any]:
    """One ``/api/quote/{symbol}/info`` body, as Nasdaq prints it."""

    if not data:
        return {
            "data": None,
            "message": None,
            "status": {"rCode": 400, "bCodeMessage": [{"code": 1001}]},
        }
    primary: dict[str, Any] = {
        "lastSalePrice": price,
        "netChange": "-0.7823",
        "percentageChange": "-0.22%",
        "lastTradeTimestamp": et_stamp(now_ts() if ts is None else ts),
        "isRealTime": realtime,
        "volume": volume,
        "currency": None,
    }
    return {
        "data": {
            "symbol": symbol,
            "companyName": f"{symbol} Inc.",
            "primaryData": primary,
            "secondaryData": None,
            "marketStatus": "Open",
        },
        "message": None,
        "status": {"rCode": 200},
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def requested_symbols(request: httpx2.Request) -> list[str]:
    raw = request.url.params.get("symbols") or ""
    return [token.strip().upper() for token in raw.split(",") if token.strip()]


def yahoo_route(
    web: MockWeb,
    table: Mapping[str, Mapping[str, Any]],
    *,
    status: int = 200,
    fail_chunks: Collection[int] = (),
    crumb: str = TEST_CRUMB,
) -> None:
    """Serve the Yahoo handshake and one v7 body per request.

    ``table`` maps symbol → row; a symbol absent from the table is omitted from
    ``result`` exactly as Yahoo omits tickers it does not know. ``fail_chunks``
    is the set of zero-based data-request indexes answered ``500`` (used to pin
    the partial-batch policy).
    """

    seen = {"data": 0}

    def api(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/v1/test/getcrumb"):
            return httpx2.Response(200, text=crumb)
        index = seen["data"]
        seen["data"] += 1
        if status != 200:
            return httpx2.Response(
                status,
                json={"finance": {"result": None, "error": {"code": f"HTTP {status}"}}},
            )
        if index in fail_chunks:
            return httpx2.Response(
                500, json={"finance": {"result": None, "error": {"code": "boom"}}}
            )
        rows = [table[key] for key in requested_symbols(request) if key in table]
        return httpx2.Response(200, json=yahoo_body(rows))

    web.route(YAHOO_COOKIE_HOST, lambda _request: httpx2.Response(404, text=""))
    web.route(YAHOO_API_HOST, api)


def nasdaq_route(
    web: MockWeb,
    table: Mapping[str, Mapping[str, Any]],
    *,
    status: int = 200,
) -> None:
    """Serve one Nasdaq body per symbol path; unknown symbols answer ``data: null``."""

    def responder(request: httpx2.Request) -> httpx2.Response:
        if status != 200:
            return httpx2.Response(
                status, json={"data": None, "message": f"HTTP {status}"}
            )
        tail = request.url.path.split("/api/quote/", 1)[-1]
        symbol = tail.split("/", 1)[0].upper()
        return httpx2.Response(200, json=dict(table.get(symbol) or nasdaq_body(symbol, data=False)))

    web.route(NASDAQ_HOST, responder)


def yahoo_data_requests(web: MockWeb) -> list[Any]:
    """Only the ``/v7/finance/quote`` calls — not the cookie or crumb handshake."""

    return [
        record
        for record in web.requests
        if YAHOO_API_HOST == record.host and "/v7/finance/quote" in record.url
    ]


def crumb_requests(web: MockWeb) -> list[Any]:
    return [
        record
        for record in web.requests
        if YAHOO_API_HOST == record.host and record.url.endswith("/v1/test/getcrumb")
    ]


def nasdaq_requests(web: MockWeb) -> list[Any]:
    return [record for record in web.requests if record.host == NASDAQ_HOST]


def build_client_for(web: MockWeb) -> Any:
    """The one HTTP seam, forwarding the headers the sources set.

    ``MockWeb.build_client`` (the frozen Phase-2 helper) drops ``**kwargs``,
    which would make every ``user-agent``/``Origin`` assertion vacuously pass
    against httpx's default. This stand-in keeps the recording transport and
    passes the headers through, so a source that forgets the Windows UA fails
    its test instead of production.
    """

    def build_client(**kwargs: Any) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=web._transport,  # noqa: SLF001 - MockWeb exposes no transport getter
            headers=dict(kwargs.get("headers") or {}),
        )

    return build_client


def install_seam(monkeypatch: Any, web: MockWeb) -> None:
    """Point ``app.providers.http.build_client`` at ``web`` (headers included)."""

    from app.providers import http as provider_http

    monkeypatch.setattr(provider_http, "build_client", build_client_for(web))


# ---------------------------------------------------------------------------
# App harness
# ---------------------------------------------------------------------------


@dataclass
class Harness:
    """One app, one HTTP client, one recording fake transport."""

    client: Any
    application: Any
    runtime: Any
    web: MockWeb

    async def get(self, symbols: str | None = None) -> httpx2.Response:
        params = {} if symbols is None else {"symbols": symbols}
        return await self.client.get("/market/quote", params=params)

    @property
    def engine(self) -> Any:
        return self.application.state.quote_engine


@asynccontextmanager
async def market_client(
    tmp_path: Path,
    monkeypatch: Any,
    *,
    env: Mapping[str, str] | None = None,
) -> AsyncIterator[Harness]:
    """An app whose only HTTP client is the fake transport (no sockets, no DB)."""

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))
    for name, value in (env or {}).items():
        monkeypatch.setenv(name, value)

    web = MockWeb()
    install_seam(monkeypatch, web)

    from httpx2 import ASGITransport, AsyncClient

    from app.main import create_app

    application = create_app()
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://ztockz.test") as client:
        yield Harness(
            client=client, application=application, runtime=application.state.runtime, web=web
        )


__all__ = [
    "EASTERN",
    "Harness",
    "NASDAQ_HOST",
    "TEST_CRUMB",
    "YAHOO_API_HOST",
    "YAHOO_COOKIE_HOST",
    "build_client_for",
    "crumb_requests",
    "et_stamp",
    "install_seam",
    "market_client",
    "nasdaq_body",
    "nasdaq_requests",
    "nasdaq_route",
    "now_ts",
    "requested_symbols",
    "yahoo_body",
    "yahoo_data_requests",
    "yahoo_route",
    "yahoo_row",
]
