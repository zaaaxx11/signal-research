from __future__ import annotations

import json
from pathlib import Path

from app.providers.proxy_pool import FREE, PAID, ProxyPool


def test_ranked_json_orders_by_score_and_skips_dead_entries(tmp_path: Path) -> None:
    path = tmp_path / "pool_ranked.json"
    path.write_text(
        json.dumps(
            {
                "proxies": [
                    {"proxy": "http://low:1", "score": 1, "ok": True},
                    {"proxy": "http://dead:1", "score": 99, "ok": False},
                    {"proxy": "http://high:1", "score": 10, "ok": True},
                    "http://plain:2",
                ]
            }
        ),
        encoding="utf-8",
    )

    pool = ProxyPool.from_file(path)

    assert pool.free_urls == ["http://high:1", "http://low:1", "http://plain:2"]
    assert pool.available(FREE)
    assert not pool.available(PAID)
    assert pool.acquire(FREE) == "http://high:1"
    assert pool.acquire(FREE) == "http://low:1"
    assert pool.acquire(FREE) == "http://plain:2"
    assert pool.acquire(FREE) == "http://high:1"


def test_failure_cools_a_proxy_and_success_clears_it() -> None:
    now = [0.0]
    pool = ProxyPool(
        free_urls=["http://a:1", "http://b:2"],
        cooldown_s=30,
        clock=lambda: now[0],
    )

    first = pool.acquire(FREE)
    pool.report_failure(first, FREE)
    now[0] = 10
    assert first not in pool._live(FREE)
    assert pool.acquire(FREE) == "http://b:2"
    now[0] = 31
    assert first in pool._live(FREE)
    pool.report_success(first, FREE)
    assert first in pool._live(FREE)


def test_text_file_and_paid_tier_are_loaded_without_guessing() -> None:
    path = Path("/tmp/meeproxies-test.txt")
    path.write_text("# comment\n1.2.3.4:8080\n\nhttp://5.6.7.8:3128\n", encoding="utf-8")
    try:
        pool = ProxyPool.from_file(path)
        assert pool.free_urls == ["http://1.2.3.4:8080", "http://5.6.7.8:3128"]
        assert not pool.paid_urls

        paid = ProxyPool(free_urls=["http://f:1"], paid_urls=["http://p:2"])
        assert paid.available(PAID)
        assert paid.acquire(PAID) == "http://p:2"
        assert paid.snapshot()["paid_urls"] == 1
    finally:
        path.unlink(missing_ok=True)


def test_empty_pool_is_honest_not_an_exception() -> None:
    pool = ProxyPool.from_file("/definitely/not/here.json")
    assert pool.free_urls == []
    assert not pool.available(FREE)
    assert pool.acquire(FREE) is None
    assert pool.acquire(PAID) is None
