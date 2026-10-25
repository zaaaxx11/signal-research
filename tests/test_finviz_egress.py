"""P11K — Finviz egress ladder: direct → Jina (free) → free proxy → paid proxy.

The Finviz page is the only source of the three short/ownership cells, and a
datacenter IP is refused by Cloudflare for a large share of symbols (the P11J
full fill: 578 of ~3,900 blocked). Refusing the **exit** is not a verdict about
the **symbol**: the same page answers 200 through the reader or a proxy. This
suite locks the transport policy:

* the order is ``direct → r.jina.ai → free proxy → paid proxy``;
* only an exit refusal climbs — ``401``/``403``/``429``, a 200 Cloudflare
  challenge, and a markdown reader body; ``404``/``5xx``/``schema_drift`` are
  answers about the page and stop the walk;
* the reader is asked for **HTML** (``X-Return-Format: html``) with no proxy,
  and its bytes are parsed as Finviz's page — ``source`` stays ``finviz``;
* nothing is parsed from a challenge or a markdown body (zero observations,
  never a zero value);
* a direct refusal cools the direct rung, so the next symbol starts higher;
* ``build_adapters`` gives Finviz the same pool object as Yahoo.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

import pytest

from app.contracts import Capability
from app.providers import http as provider_http
from app.providers.adapters import build_adapters
from app.providers.adapters.finviz import FACT_FIELDS, FinvizAdapter, parse_facts
from app.providers.base import InstrumentRef
from app.providers.proxy_pool import ProxyPool

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "finviz_stock_aapl.html"
FACTS_FIXTURE = ROOT / "tests" / "fixtures" / "finviz_stock_facts_aapl.html"
#: The valuation page and the short/ownership page, one after the other: the
#: egress rung must deliver the *whole* page, so the body that proves it carries
#: both a snapshot valuation block and the three facts labels.
PAGE = FIXTURE.read_text(encoding="utf-8") + FACTS_FIXTURE.read_text(encoding="utf-8")

AAPL = InstrumentRef.from_symbol("AAPL", venue="XNAS")
MSFT = InstrumentRef.from_symbol("MSFT", venue="XNAS")

#: A Cloudflare interstitial as the live site serves it: 200 status on the
#: challenge hop, and markers the adapter must not read as a page.
CHALLENGE = (
    "<!DOCTYPE html><html><head><title>Just a moment...</title></head>"
    "<body><div id='cf-chl-widget'>challenge-platform</div></body></html>"
)

#: What ``r.jina.ai`` answers when ``X-Return-Format: html`` is ignored: a
#: readable summary that carries no snapshot table.
MARKDOWN = (
    "Title: AAPL - Finviz\n\n"
    "URL Source: http://finviz.com/stock?t=AAPL\n\n"
    "Markdown Content:\nShort Float 0.83%\nInsider Own 0.12%\nInst Own 68.93%\n"
)

EXPECTED_FACTS = {"short_float": 0.83, "insider_own": 0.12, "inst_own": 68.93}


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class _Response:
    """The four attributes an adapter reads off a provider response."""

    def __init__(self, status: int, *, text: str = "") -> None:
        self.status_code = status
        self.content = text.encode("utf-8")
        self.headers = {"content-type": "text/html"}


class _Client:
    def __init__(
        self,
        proxy: str | None,
        headers: dict[str, str],
        responder: Callable[[str | None, str], _Response],
        calls: list[dict[str, Any]],
    ) -> None:
        self.proxy = proxy
        self.headers = headers
        self._responder = responder
        self._calls = calls

    async def __aenter__(self) -> "_Client":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def get(self, url: str) -> _Response:
        self._calls.append(
            {"proxy": self.proxy, "url": url, "headers": dict(self.headers)}
        )
        return self._responder(self.proxy, url)


def install(
    monkeypatch: pytest.MonkeyPatch,
    responder: Callable[[str | None, str], _Response],
    calls: list[dict[str, Any]],
) -> None:
    """Patch the one HTTP seam; every request is recorded in ``calls``."""

    def build_client(**kwargs: Any) -> _Client:
        return _Client(
            kwargs.get("proxy"), dict(kwargs.get("headers") or {}), responder, calls
        )

    monkeypatch.setattr(provider_http, "build_client", build_client)


def is_jina(url: str) -> bool:
    return url.startswith("https://r.jina.ai/")


def fetch(adapter: FinvizAdapter, instrument: InstrumentRef = AAPL) -> dict[str, Any]:
    return asyncio.run(
        adapter.fetch(Capability.FUNDAMENTALS, instrument, set(FACT_FIELDS))
    )


# ---------------------------------------------------------------------------
# Order: direct → Jina → free → paid
# ---------------------------------------------------------------------------


def test_direct_blocked_climbs_to_the_free_reader_and_parses_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def responder(proxy: str | None, url: str) -> _Response:
        return _Response(200, text=PAGE) if is_jina(url) else _Response(403, text=CHALLENGE)

    install(monkeypatch, responder, calls)
    adapter = FinvizAdapter()

    raw = fetch(adapter)

    assert [call["proxy"] for call in calls] == [None, None], (
        "the reader is a free rung with its own egress: no proxy is spent on it"
    )
    assert calls[0]["url"].startswith("https://finviz.com/stock?t=AAPL")
    assert calls[1]["url"].startswith("https://r.jina.ai/http://finviz.com/stock?t=AAPL")
    assert int(raw["status"]) == 200
    assert raw["egress"] == "jina"
    assert parse_facts(raw["body"].decode("utf-8")) == EXPECTED_FACTS
    assert adapter.verdict(raw, AAPL).ok is True


def test_the_reader_is_asked_for_html_not_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def responder(proxy: str | None, url: str) -> _Response:
        if is_jina(url):
            return _Response(200, text=PAGE)
        return _Response(403, text=CHALLENGE)

    install(monkeypatch, responder, calls)

    raw = fetch(FinvizAdapter())

    assert raw["egress"] == "jina"
    jina_call = next(call for call in calls if is_jina(call["url"]))
    assert jina_call["proxy"] is None, "the reader must not ride a proxy"
    assert jina_call["headers"].get("x-return-format") == "html"
    assert "html" in jina_call["headers"].get("accept", "")


def test_jina_blocked_climbs_to_the_free_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def responder(proxy: str | None, url: str) -> _Response:
        if is_jina(url):
            return _Response(200, text=CHALLENGE)
        if proxy is None:
            return _Response(403, text=CHALLENGE)
        return _Response(200, text=PAGE)

    install(monkeypatch, responder, calls)
    pool = ProxyPool(free_urls=["http://proxy-a:1"], free_attempts=1)
    adapter = FinvizAdapter(proxy_pool=pool, proxy_attempts=1)

    raw = fetch(adapter)

    assert [(call["proxy"], is_jina(call["url"])) for call in calls] == [
        (None, False),  # direct
        (None, True),  # r.jina.ai
        ("http://proxy-a:1", False),  # free proxy
    ], "order must be direct → Jina → free proxy"
    assert raw["egress"] == "http://proxy-a:1"
    assert parse_facts(raw["body"].decode("utf-8")) == EXPECTED_FACTS


def test_the_paid_rung_comes_after_every_free_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def responder(proxy: str | None, url: str) -> _Response:
        if is_jina(url):
            return _Response(429)
        if proxy == "http://paid:9":
            return _Response(200, text=PAGE)
        return _Response(403, text=CHALLENGE)

    install(monkeypatch, responder, calls)
    pool = ProxyPool(
        free_urls=["http://free:1"],
        paid_urls=["http://paid:9"],
        free_attempts=1,
    )
    adapter = FinvizAdapter(proxy_pool=pool, proxy_attempts=1)

    raw = fetch(adapter)

    assert [call["proxy"] for call in calls] == [
        None,
        None,
        "http://free:1",
        "http://paid:9",
    ]
    assert raw["egress"] == "http://paid:9"


def test_a_free_proxy_that_blocks_falls_through_to_the_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def responder(proxy: str | None, url: str) -> _Response:
        if is_jina(url):
            return _Response(429)
        if proxy in (None, "http://dead:1"):
            return _Response(403, text=CHALLENGE)
        return _Response(200, text=PAGE)

    install(monkeypatch, responder, calls)
    pool = ProxyPool(free_urls=["http://dead:1", "http://live:2"], free_attempts=2)
    adapter = FinvizAdapter(proxy_pool=pool, proxy_attempts=2)

    raw = fetch(adapter)

    assert "http://dead:1" in [call["proxy"] for call in calls]
    assert "http://live:2" in [call["proxy"] for call in calls]
    assert raw["egress"] == "http://live:2"
    assert int(raw["status"]) == 200


def test_a_dead_proxy_is_cooled_and_the_second_symbol_rotates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def responder(proxy: str | None, url: str) -> _Response:
        if is_jina(url):
            return _Response(429)
        if proxy in (None, "http://a:1"):
            return _Response(403, text=CHALLENGE)
        return _Response(200, text=PAGE)

    install(monkeypatch, responder, calls)
    pool = ProxyPool(free_urls=["http://a:1", "http://b:2"], free_attempts=2)
    adapter = FinvizAdapter(proxy_pool=pool, proxy_attempts=2)

    first = fetch(adapter, AAPL)
    second = fetch(adapter, MSFT)

    assert [call["proxy"] for call in calls][:4] == [
        None,
        None,
        "http://a:1",
        "http://b:2",
    ]
    assert first["egress"] == "http://b:2"
    second_proxies = [call["proxy"] for call in calls][4:]
    assert second["egress"] == "http://b:2", (
        f"the cooled proxy must not be the next symbol's first choice: {second_proxies}"
    )
    assert "http://a:1" not in second_proxies


# ---------------------------------------------------------------------------
# Refusals and what is never parsed
# ---------------------------------------------------------------------------


def test_all_rungs_failing_returns_the_last_refusal_and_no_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def responder(proxy: str | None, url: str) -> _Response:
        if is_jina(url):
            return _Response(429)
        return _Response(403, text=CHALLENGE)

    install(monkeypatch, responder, calls)
    pool = ProxyPool(free_urls=["http://p1:1"], free_attempts=1)
    adapter = FinvizAdapter(proxy_pool=pool, proxy_attempts=1)

    raw = fetch(adapter)
    verdict = adapter.verdict(raw, AAPL)

    assert int(raw["status"]) == 403
    assert raw["egress"] == "http://p1:1"
    assert verdict.ok is False
    assert adapter.parse(raw, AAPL) == [], "a refused body is never parsed"
    assert parse_facts(raw["body"].decode("utf-8")) == {}


def test_a_200_challenge_body_is_blocked_and_never_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = {
        "status": 200,
        "headers": {"content-type": "text/html"},
        "body": CHALLENGE.encode("utf-8"),
        "url": "https://finviz.com/stock?t=AAPL",
        "capability": "fundamentals",
        "egress": "direct",
    }
    adapter = FinvizAdapter()

    verdict = adapter.verdict(raw, AAPL)

    assert verdict.ok is False and verdict.kind == "blocked"
    assert adapter.parse(raw, AAPL) == []
    assert parse_facts(CHALLENGE) == {}


def test_markdown_from_the_reader_is_not_a_page_and_never_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def responder(proxy: str | None, url: str) -> _Response:
        if is_jina(url):
            return _Response(200, text=MARKDOWN)
        return _Response(403, text=CHALLENGE)

    install(monkeypatch, responder, calls)
    adapter = FinvizAdapter()

    raw = fetch(adapter)
    verdict = adapter.verdict(raw, AAPL)

    assert raw["egress"] == "jina"
    assert verdict.ok is False
    assert verdict.kind == "schema_drift"
    assert "markdown" in str(verdict.detail).lower()
    assert adapter.parse(raw, AAPL) == []


def test_a_404_is_an_answer_about_the_symbol_and_does_not_climb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    pool = ProxyPool(free_urls=["http://proxy-a:1"], free_attempts=1)
    install(monkeypatch, lambda _proxy, _url: _Response(404), calls)
    adapter = FinvizAdapter(proxy_pool=pool, proxy_attempts=1)

    raw = fetch(adapter)

    assert len(calls) == 1, f"a 404 must not spend a reader or proxy request: {calls}"
    assert adapter.verdict(raw, AAPL).kind == "not_found"


def test_an_unreachable_reader_leaves_the_direct_refusal_standing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def responder(proxy: str | None, url: str) -> _Response:
        if is_jina(url):
            raise RuntimeError("reader down")
        return _Response(403, text=CHALLENGE)

    install(monkeypatch, responder, calls)
    adapter = FinvizAdapter()

    raw = fetch(adapter)

    assert raw["egress"] == "direct"
    assert int(raw["status"]) == 403
    assert adapter.verdict(raw, AAPL).kind == "authwall"


def test_a_direct_refusal_cools_the_direct_rung_for_the_next_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def responder(proxy: str | None, url: str) -> _Response:
        if is_jina(url):
            return _Response(200, text=PAGE)
        return _Response(403, text=CHALLENGE)

    install(monkeypatch, responder, calls)
    adapter = FinvizAdapter(direct_cooldown_s=600)

    first = fetch(adapter, AAPL)
    assert first["egress"] == "jina"
    calls.clear()

    second = fetch(adapter, MSFT)

    assert second["egress"] == "jina"
    assert calls and is_jina(calls[0]["url"]), (
        f"a cooling direct rung must not be tried again: {calls}"
    )
    assert all(not call["url"].startswith("https://finviz.com/") for call in calls)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_build_adapters_gives_finviz_the_same_pool_as_yahoo() -> None:
    pool = ProxyPool(free_urls=["http://proxy-a:1"], free_attempts=2)

    adapters = build_adapters(proxy_pool=pool, proxy_attempts=2, direct_cooldown_s=600)

    assert adapters["finviz"].proxy_pool is pool, (
        "one pool, one cooldown table: a proxy failed by Yahoo must stay cooled "
        "for Finviz too"
    )
    assert adapters["yahoo"].proxy_pool is pool
    assert adapters["finviz"].proxy_attempts == 2
    assert adapters["finviz"].direct_cooldown_s == 600
    # A rung with no IP wall keeps its constructor contract (no proxy hop).
    assert not hasattr(adapters["stooq"], "proxy_pool")


def test_the_observations_still_say_finviz_not_jina(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    install(
        monkeypatch,
        lambda _proxy, url: _Response(200, text=PAGE) if is_jina(url) else _Response(403),
        calls,
    )

    raw = fetch(FinvizAdapter())
    observations = FinvizAdapter().parse(raw, AAPL)

    assert raw["egress"] == "jina"
    assert observations, "the reader's HTML is the page and must parse"
    assert {item["source"] for item in observations} == {"finviz"}
    assert {item["trust"] for item in observations} == {3}
