"""RED contract for R7.5: one shared SEC rate limit.

The point is not "a limiter exists" — it is that every SEC request in the
process passes the *same* bucket. The tests pin three things:

1. the bucket's arithmetic with an injected clock (burst, wait, refill);
2. configuration: 10 by default, 0/off disables, garbage falls back to 10;
3. the ingest boundary calls ``sec_get`` instead of ``client.get``, so the
   limiter cannot be bypassed by a route that forgets to ask.
"""

from __future__ import annotations

import os
from typing import Any

import httpx2
import pytest

from app.engine import ingest, ratelimit


class FakeClock:
    """A monotonic clock that only moves when the limiter sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def limiter(rate: float = 2.0, burst: float = 2.0) -> tuple[ratelimit.AsyncRateLimiter, FakeClock]:
    clock = FakeClock()
    return (
        ratelimit.AsyncRateLimiter(
            rate=rate, burst=burst, clock=clock.clock, sleep=clock.sleep
        ),
        clock,
    )


async def test_burst_then_wait_then_refill() -> None:
    bucket, clock = limiter(rate=2.0, burst=2.0)

    await bucket.acquire()
    await bucket.acquire()
    assert clock.slept == [], "the burst must not sleep"

    await bucket.acquire()
    assert clock.slept == [0.5], "rate 2/s means a 0.5s wait for the third"

    clock.now += 1.0
    await bucket.acquire()
    assert clock.slept == [0.5], "a full second refills two tokens; no new wait"


async def test_default_burst_follows_the_rate() -> None:
    clock = FakeClock()
    bucket = ratelimit.AsyncRateLimiter(
        rate=4.0, clock=clock.clock, sleep=clock.sleep
    )
    for _ in range(4):
        await bucket.acquire()
    assert clock.slept == []
    await bucket.acquire()
    assert clock.slept == [0.25]


def test_configured_rate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    ratelimit.reset_sec_limiter()
    monkeypatch.delenv("MEE_SEC_RATE_LIMIT", raising=False)
    assert ratelimit.configured_rate() == 10.0

    for raw in ("0", "off", "OFF", "", "none"):
        monkeypatch.setenv("MEE_SEC_RATE_LIMIT", raw)
        assert ratelimit.configured_rate() is None

    monkeypatch.setenv("MEE_SEC_RATE_LIMIT", "2.5")
    assert ratelimit.configured_rate() == 2.5

    monkeypatch.setenv("MEE_SEC_RATE_LIMIT", "not-a-number")
    assert ratelimit.configured_rate() == 10.0, "garbage must never mean unlimited"

    monkeypatch.setenv("MEE_SEC_RATE_LIMIT", "-3")
    assert ratelimit.configured_rate() is None


def test_singleton_is_rebuilt_when_the_rate_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ratelimit.reset_sec_limiter()
    monkeypatch.setenv("MEE_SEC_RATE_LIMIT", "10")
    first = ratelimit.sec_limiter()
    assert first is ratelimit.sec_limiter()

    monkeypatch.setenv("MEE_SEC_RATE_LIMIT", "2")
    second = ratelimit.sec_limiter()
    assert second is not first
    assert second is not None and second.rate == 2.0

    monkeypatch.setenv("MEE_SEC_RATE_LIMIT", "0")
    assert ratelimit.sec_limiter() is None


async def test_sec_get_acquires_then_gets(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class FakeLimiter:
        async def acquire(self, tokens: float = 1.0) -> None:
            events.append(f"acquire:{tokens}")

    class FakeClient:
        async def get(self, url: str, **kwargs: Any) -> str:
            events.append(f"get:{url}")
            return "response"

    monkeypatch.setattr(ratelimit, "sec_limiter", lambda: FakeLimiter())
    result = await ratelimit.sec_get(
        FakeClient(), "https://data.sec.gov/x", headers={"a": "b"}
    )

    assert result == "response"
    assert events == ["acquire:1.0", "get:https://data.sec.gov/x"]


async def test_ingest_goes_through_sec_get(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_sec_get(client: Any, url: str, **kwargs: Any) -> Any:
        calls.append(url)
        return httpx2.Response(
            200, json={"ok": True}, request=httpx2.Request("GET", url)
        )

    monkeypatch.setattr(ingest, "sec_get", fake_sec_get)

    payload, text = await ingest._fetch_json(
        "https://data.sec.gov/submissions/CIK0000000001.json",
        client=object(),
        user_agent="ztockz-test/1.0 (test@example.com)",
    )

    assert payload == {"ok": True}
    assert calls == ["https://data.sec.gov/submissions/CIK0000000001.json"], (
        "the ingest boundary must use the shared limiter, not client.get"
    )
    assert text == '{"ok":true}'
