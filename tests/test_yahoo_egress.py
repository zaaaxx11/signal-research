from __future__ import annotations

import asyncio
from typing import Any

import httpx2
import pytest

from app.contracts import Capability
from app.providers import http as provider_http
from app.providers.adapters.yahoo import YahooAdapter
from app.providers.base import InstrumentRef
from app.providers.proxy_pool import FREE, ProxyPool
from tests import _wiring as W

SYMBOL = InstrumentRef.from_symbol("AAPL", venue="XNAS")


class _Response:
    def __init__(self, status: int, *, text: str = "", body: Any = None) -> None:
        self.status_code = status
        self.text = text
        self.content = (
            text.encode("utf-8")
            if body is None
            else __import__("json").dumps(body).encode("utf-8")
        )
        self.headers = {"content-type": "application/json"}


class _Client:
    def __init__(self, proxy: str | None) -> None:
        self.proxy = proxy

    async def __aenter__(self) -> "_Client":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def get(self, url: str) -> _Response:
        if "fc.yahoo.com" in url:
            return _Response(200)
        if "/v1/test/getcrumb" in url:
            return _Response(200, text="crumb-token")
        if "/quoteSummary/" in url:
            if self.proxy is None:
                return _Response(401, body={"finance": {"error": "Invalid Crumb"}})
            return _Response(200, body=W.quote_summary())
        return _Response(404)


def _install(monkeypatch: Any, seen: list[str | None]) -> None:
    def build_client(**kwargs: Any) -> _Client:
        seen.append(kwargs.get("proxy"))
        return _Client(kwargs.get("proxy"))

    monkeypatch.setattr(provider_http, "build_client", build_client)


def test_direct_authwall_climbs_to_free_proxy_and_keeps_one_body(
    monkeypatch: Any,
) -> None:
    seen: list[str | None] = []
    _install(monkeypatch, seen)
    pool = ProxyPool(free_urls=["http://proxy-a:1", "http://proxy-b:2"], free_attempts=2)
    adapter = YahooAdapter(proxy_pool=pool, proxy_attempts=2)

    raw = asyncio.run(
        adapter.fetch(Capability.FUNDAMENTALS, SYMBOL, set(W.FUNDAMENTAL_COLUMNS))
    )

    assert int(raw["status"]) == 200
    assert seen[0] is None, "the direct rung is tried first while it is healthy"
    assert "http://proxy-a:1" in seen, f"free proxy was never tried: {seen}"
    assert pool.snapshot()["free_live"] == 2


def test_a_direct_429_cools_the_direct_rung_for_the_next_symbol(monkeypatch: Any) -> None:
    seen: list[str | None] = []

    def build_client(**kwargs: Any) -> _Client:
        seen.append(kwargs.get("proxy"))
        return _Client(kwargs.get("proxy"))

    monkeypatch.setattr(provider_http, "build_client", build_client)
    pool = ProxyPool(free_urls=["http://proxy-a:1"], free_attempts=1)
    adapter = YahooAdapter(
        proxy_pool=pool, proxy_attempts=1, direct_cooldown_s=600
    )

    # First symbol: direct 401 -> proxy 200. The direct egress is now cooling.
    first = asyncio.run(
        adapter.fetch(Capability.FUNDAMENTALS, SYMBOL, set(W.FUNDAMENTAL_COLUMNS))
    )
    assert int(first["status"]) == 200
    seen.clear()

    # Second symbol must start at the proxy, not spend another 401 on direct.
    second = asyncio.run(
        adapter.fetch(Capability.FUNDAMENTALS, SYMBOL, set(W.FUNDAMENTAL_COLUMNS))
    )
    assert int(second["status"]) == 200
    assert None not in seen, f"direct was retried while cooling: {seen}"
    assert "http://proxy-a:1" in seen


def test_a_dead_free_proxy_falls_through_to_the_next(monkeypatch: Any) -> None:
    seen: list[str | None] = []

    def build_client(**kwargs: Any) -> _Client:
        proxy = kwargs.get("proxy")
        seen.append(proxy)
        return _DeadThenGood(proxy)

    class _DeadThenGood(_Client):
        async def get(self, url: str) -> _Response:
            if "fc.yahoo.com" in url:
                return _Response(200)
            if "/v1/test/getcrumb" in url:
                return _Response(200, text="crumb-token")
            if "/quoteSummary/" in url:
                if self.proxy in (None, "http://dead:1"):
                    return _Response(401, body={"finance": {"error": "Invalid Crumb"}})
                return _Response(200, body=W.quote_summary())
            return _Response(404)

    monkeypatch.setattr(provider_http, "build_client", build_client)
    pool = ProxyPool(free_urls=["http://dead:1", "http://live:2"], free_attempts=2)
    adapter = YahooAdapter(proxy_pool=pool, proxy_attempts=2)

    raw = asyncio.run(
        adapter.fetch(Capability.FUNDAMENTALS, SYMBOL, set(W.FUNDAMENTAL_COLUMNS))
    )

    assert int(raw["status"]) == 200
    assert "http://dead:1" in seen and "http://live:2" in seen
    assert pool.snapshot()["free_live"] == 1


def test_all_egress_failures_return_the_last_refusal_not_a_fake_body(
    monkeypatch: Any,
) -> None:
    class _Always401(_Client):
        async def get(self, url: str) -> _Response:
            if "fc.yahoo.com" in url:
                return _Response(200)
            if "/v1/test/getcrumb" in url:
                return _Response(200, text="crumb-token")
            return _Response(401, body={"finance": {"error": "Invalid Crumb"}})

    def build_client(**kwargs: Any) -> _Always401:
        return _Always401(kwargs.get("proxy"))

    monkeypatch.setattr(provider_http, "build_client", build_client)
    pool = ProxyPool(free_urls=["http://p1:1", "http://p2:2"], free_attempts=2)
    adapter = YahooAdapter(proxy_pool=pool, proxy_attempts=2)

    raw = asyncio.run(
        adapter.fetch(Capability.FUNDAMENTALS, SYMBOL, set(W.FUNDAMENTAL_COLUMNS))
    )

    assert int(raw["status"]) == 401
    assert "quoteSummary" in raw["url"]
