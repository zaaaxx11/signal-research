"""Phase-2 RED: budget, observation cache and the usage meter.

Gate 3 (02 §5 Fase 2): an exhausted budget **skips** a rung and the fallback
still runs; the job stays ``Success`` with ``result_mode="partial"`` and
``skipped_by_budget`` on the phase report.
Gate 4: the second identical run makes **zero** provider requests, and a quote
cache is not served across a session boundary.
Gate 5: ``usage.requests_total > 0`` on a real fixture and ``bytes_total``
equals the sum of the response bodies the transport actually served — the
counter is incremented where the request happens, not reconstructed later.
"""

from __future__ import annotations

from typing import Any

from tests._phase2 import (
    AS_OF,
    NEXT_SESSION_AS_OF,
    first_quote_spec,
    live_client,
    make_budget,
    mock_web,
    yahoo_route,
)


def test_budget_counts_requests_and_credits_separately() -> None:
    budget = make_budget(requests=2, credits=1, seconds=60)

    assert budget.allows(requests=1, credits=0) is True
    budget.spend(requests=1, credits=0)
    assert budget.allows(requests=1, credits=0) is True
    budget.spend(requests=1, credits=1)

    assert budget.allows(requests=1, credits=0) is False, "the request budget is spent"
    assert budget.allows(requests=0, credits=1) is False, "the credit budget is spent"
    snapshot = budget.snapshot()
    assert snapshot["requests"] == 2
    assert snapshot["credits"] == 1


async def test_exhausted_request_budget_still_completes_the_job_partially(
    tmp_path: Any, monkeypatch: Any, mock_web: Any
) -> None:
    yahoo_route(mock_web)

    async with live_client(tmp_path, monkeypatch, mock_web) as harness:
        job = await harness.run_job(
            ["AAPL", "MSFT"], budget={"requests": 1, "credits": 0, "seconds": 120}
        )

    assert job["status"] == "Success", (
        "an exhausted budget is a partial result, not a failure (02 §3.9): " + str(job)
    )
    assert job["result_count"] == 1, f"expected the one affordable row, got {job['result_count']}"
    assert job["rows_unresolved"] == 1
    assert job["result_mode"] == "partial", (
        "Success with skipped_by_budget > 0 must be reported as partial (02 §3.5)"
    )
    assert len(mock_web.requests) == 1, (
        f"the second symbol must not spend a request: {[r.url for r in mock_web.requests]}"
    )
    quote_phase = job.get("phases", {}).get("quote", {})
    assert quote_phase.get("skipped_by_budget", 0) >= 1, (
        "the quote phase must report how many rungs the budget skipped: "
        f"{quote_phase!r}"
    )
    assert job["usage"]["requests_total"] == 1, (
        f"usage.requests_total must count real requests, got {job['usage']!r}"
    )


async def test_second_run_makes_zero_provider_requests(
    tmp_path: Any, monkeypatch: Any, mock_web: Any
) -> None:
    yahoo_route(mock_web)

    async with live_client(tmp_path, monkeypatch, mock_web) as harness:
        first = await harness.run_job(["AAPL", "MSFT"], as_of=AS_OF)
        assert first["status"] == "Success", first
        served_first = len(mock_web.requests)
        assert served_first >= 2, (
            "the first run did not reach a provider; the cache test would be vacuous: "
            f"{[r.url for r in mock_web.requests]}"
        )

        second = await harness.run_job(["AAPL", "MSFT"], as_of=AS_OF)

    assert second["status"] == "Success", (
        "a cache hit must still produce rows, not a Success with zero rows: " + str(second)
    )
    assert second["result_count"] == first["result_count"] == 2
    assert len(mock_web.requests) == served_first, (
        "the second identical run issued provider requests: "
        f"{[r.url for r in mock_web.requests[served_first:]]}"
    )
    assert second["usage"]["requests_total"] == 0
    assert second["usage"]["cache_hits"] >= 2, (
        f"the second run served no cache hits: {second['usage']!r}"
    )


async def test_quote_cache_not_served_across_session_boundary(
    tmp_path: Any, monkeypatch: Any, mock_web: Any
) -> None:
    yahoo_route(mock_web)

    async with live_client(tmp_path, monkeypatch, mock_web) as harness:
        first = await harness.run_job(["AAPL", "MSFT"], as_of=AS_OF)
        assert first["status"] == "Success", first
        served_first = len(mock_web.requests)
        assert served_first >= 2, "the first run did not reach a provider"

        # Same process, same fixture, next trading session: a quote cached in
        # the previous session must not be served, even though its TTL measured
        # from ``fetched_at`` is still fresh.
        second = await harness.run_job(["AAPL", "MSFT"], as_of=NEXT_SESSION_AS_OF)

    assert second["status"] == "Success", second
    assert len(mock_web.requests) > served_first, (
        "the previous session's quote was served in the next session: "
        f"0 new requests (cache_hits={second['usage'].get('cache_hits')})"
    )
    assert second["usage"]["requests_total"] >= 2, (
        "a new session must re-fetch both symbols: " + str(second["usage"])
    )


async def test_usage_meter_counts_requests_and_body_bytes(
    tmp_path: Any, monkeypatch: Any, mock_web: Any
) -> None:
    yahoo_route(mock_web)

    async with live_client(tmp_path, monkeypatch, mock_web) as harness:
        job = await harness.run_job(["AAPL"])

    assert job["status"] == "Success", job
    usage = job["usage"]
    assert usage["requests_total"] > 0, (
        "requests_total is 0 while a provider was called — the `requests_direct: 0` "
        f"failure class (02 §3.10): {usage!r}"
    )
    assert usage["requests_total"] == len(mock_web.requests), (
        "the meter must be incremented at the call site: "
        f"usage={usage['requests_total']} transport={len(mock_web.requests)}"
    )
    assert usage["bytes_total"] == mock_web.bytes_out_total, (
        "bytes_total must equal the body bytes actually read: "
        f"usage={usage['bytes_total']} transport={mock_web.bytes_out_total}"
    )
    assert usage["bytes_total"] > 0

    provider = first_quote_spec().name
    per_provider = job["providers"][provider]
    assert per_provider["requests"] == usage["requests_total"]
    assert per_provider["bytes"] == usage["bytes_total"]
