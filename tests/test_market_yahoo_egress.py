"""Contract for the Yahoo egress ladder (``app.market.yahoo_egress``).

The crumb endpoint throttles an **IP**, not a cookie: once it answers
``429 Too Many Requests`` a fresh cookie jar does not help, because the refusal
is addressed to the network exit. That is what dropped the 4,217-symbol run
from 109/116 to 75/116 — every crumb-gated call went dark while the crumb-free
chart endpoint kept working.

The ladder answers one question: which exit's handshake actually succeeds?
No test reaches the network; ``build_client`` is replaced at its module seam.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.market import yahoo_egress
from app.market.yahoo_egress import YahooEgress


class FakeExit:
    """A transport that answers the crumb with a scripted token or refusal."""

    def __init__(self, token: str = "AbC123xYz", status: int = 200) -> None:
        self.token = token
        self.status = status
        self.calls: list[str] = []

    async def get(self, url: str) -> Any:
        self.calls.append(url)
        if url == yahoo_egress.CRUMB_URL:
            return httpx2.Response(self.status, text=self.token)
        return httpx2.Response(404, text="")

    async def __aenter__(self) -> "FakeExit":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _install(monkeypatch: pytest.MonkeyPatch, exits: dict[str | None, FakeExit]) -> None:
    """Route ``build_client(proxy=...)`` to the scripted exit."""

    def build_client(*, proxy: str | None = None, **_: Any) -> FakeExit:
        if proxy not in exits:
            raise httpx2.ConnectError(f"no route to {proxy or 'direct'}")
        return exits[proxy]

    monkeypatch.setattr(yahoo_egress.provider_http, "build_client", build_client)


def test_direct_wins_and_no_proxy_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    direct = FakeExit("DirectToken1")
    _install(monkeypatch, {None: direct, "http://p1:80": FakeExit("ProxyToken1")})

    egress = YahooEgress(proxies=["http://p1:80"])
    assert _run(egress.find_proxy()) is None
    assert egress.last_errors == {}


def test_a_refused_direct_exit_falls_to_the_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        {
            None: FakeExit("Too Many Requests", status=429),
            "http://p1:80": FakeExit("ProxyToken1"),
        },
    )
    egress = YahooEgress(proxies=["http://p1:80"])
    assert _run(egress.find_proxy()) == "http://p1:80"
    assert "direct" in egress.last_errors


def test_a_throttled_body_is_a_refusal_even_at_200(monkeypatch: pytest.MonkeyPatch) -> None:
    """Yahoo answers 200 with the literal body; it is not a token."""

    _install(
        monkeypatch,
        {
            None: FakeExit("Too Many Requests", status=200),
            "http://p1:80": FakeExit("ProxyToken1"),
        },
    )
    egress = YahooEgress(proxies=["http://p1:80"])
    assert _run(egress.find_proxy()) == "http://p1:80"


def test_an_html_challenge_is_not_a_crumb(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        {
            None: FakeExit("<html>challenge</html>", status=200),
            "http://p1:80": FakeExit("ProxyToken1"),
        },
    )
    egress = YahooEgress(proxies=["http://p1:80"])
    assert _run(egress.find_proxy()) == "http://p1:80"


def test_a_dead_proxy_is_recorded_and_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        {
            None: FakeExit("Too Many Requests", status=429),
            "http://dead:80": FakeExit("", status=0),
        },
    )
    egress = YahooEgress(proxies=["http://dead:80"])
    assert _run(egress.find_proxy()) is None
    assert "http://dead:80" in egress.last_errors


def test_no_working_exit_answers_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, {None: FakeExit("Too Many Requests", status=429)})
    egress = YahooEgress(proxies=["http://a:80", "http://b:80"])
    assert _run(egress.find_proxy()) is None
    assert len(egress.last_errors) == 3, "direct plus both proxies refused"


def test_max_attempts_bounds_the_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    exits: dict[str | None, FakeExit] = {None: FakeExit("Too Many Requests", status=429)}
    for index in range(1, 10):
        exits[f"http://p{index}:80"] = FakeExit("", status=0)
    _install(monkeypatch, exits)

    egress = YahooEgress(
        proxies=[f"http://p{i}:80" for i in range(1, 10)], max_attempts=3
    )
    assert _run(egress.find_proxy()) is None
    # direct + the first three proxies, not all nine.
    assert len(egress.last_errors) == 4


def test_no_proxies_configured_is_direct_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, {None: FakeExit("Too Many Requests", status=429)})
    egress = YahooEgress(proxies=[])
    assert _run(egress.find_proxy()) is None
    assert list(egress.last_errors) == ["direct"]


def test_constructor_validates_its_budget() -> None:
    with pytest.raises(ValueError):
        YahooEgress(max_attempts=0)
    with pytest.raises(ValueError):
        YahooEgress(timeout_s=0)


def _run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)
