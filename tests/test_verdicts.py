"""Phase-2 RED: verdicts come from the body, not from the status code.

Gate 6 (02 §5 Fase 2), and 02 §3.4 "Tabel verdict market":

* ``200`` + HTML challenge → ``blocked``, and the walk continues;
* ``200`` + ``{"chart": {"result": null}}`` → ``empty``, **not** retried, no rows;
* ``200`` + the promised path missing → ``schema_drift``, recorded in
  ``provider_health.last_verdict``, and no further rung is tried (retrying does
  not change a fact);
* ``404`` → ``not_found``, no row invented;
* a complete payload → ``ok`` with ``last_price`` parsed.

The bodies are the documented Yahoo ``/v8/finance/chart`` shape (01-providers
§Yahoo chart), so the test drives the real adapter through the real ladder over
a ``MockTransport`` — not a verdict helper written by the test.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.contracts import Capability
from tests._phase2 import (
    first_quote_spec,
    instrument,
    live_client,
    mock_web,
    yahoo_chart,
    yahoo_route,
)


async def _fetch(harness: Any, symbol: str = "AAPL") -> Any:
    ladder = harness.ladder()
    return await ladder.fetch(Capability.QUOTE, instrument(symbol), {"last_price"})


def _attempt(result: Any, index: int = 0) -> dict[str, Any]:
    assert result.attempts, "the ladder recorded no attempt"
    return result.attempts[index]


async def test_200_html_challenge_is_blocked_and_the_ladder_moves_on(
    tmp_path: Any, monkeypatch: Any, mock_web: Any
) -> None:
    for needle in ("yahoo", "stooq", "nasdaq"):
        mock_web.html(needle)

    async with live_client(tmp_path, monkeypatch, mock_web) as harness:
        result = await _fetch(harness)

        first = _attempt(result)
        assert first["provider"] == first_quote_spec().name
        assert first["verdict"] == "blocked", (
            "a 200 whose body is an HTML challenge is blocked, not ok (02 §3.4)"
        )
        assert first["attempts"] == 1
        assert first["requests"] == 1
        assert len(result.attempts) >= 2, (
            "blocked is retryable: the ladder must try the next rung, got "
            f"{[a['provider'] for a in result.attempts]}"
        )
        assert mock_web.count("yahoo") == 1, "a single fetch must not retry in-place"

        health = harness.health(first["provider"])
        assert health is not None, "no provider_health row for a failed rung (02 §4.9)"
        assert health["last_verdict"] == "blocked"
        assert health["failures"] >= 1


async def test_result_null_is_empty_and_is_not_retried(
    tmp_path: Any, monkeypatch: Any, mock_web: Any
) -> None:
    mock_web.json("yahoo", yahoo_chart("AAPL", empty=True))
    # Every other rung answers 404: the symbol genuinely has no data anywhere.
    for needle in ("stooq", "nasdaq", "finnhub", "polygon"):
        mock_web.status(needle, 404)

    async with live_client(tmp_path, monkeypatch, mock_web) as harness:
        result = await _fetch(harness)

    first = _attempt(result)
    assert first["provider"] == first_quote_spec().name
    assert first["verdict"] == "empty", (
        "`result: null` with a correct schema is empty, not ok and not schema_drift"
    )
    assert mock_web.count("yahoo") == 1, "empty is non-retryable: one request, not two"
    assert result.observations == [], "an empty payload must not produce a row"
    health = _health_or_none(tmp_path, first["provider"])
    assert health is not None and health["last_verdict"] == "empty"


async def test_missing_promised_field_is_schema_drift(
    tmp_path: Any, monkeypatch: Any, mock_web: Any
) -> None:
    # Same envelope, same 200, but ``meta.regularMarketPrice`` is absent: the
    # exact class of lie this verdict exists for.
    mock_web.json("yahoo", yahoo_chart("AAPL", price=None))
    for needle in ("stooq", "nasdaq", "finnhub", "polygon"):
        mock_web.status(needle, 404)

    async with live_client(tmp_path, monkeypatch, mock_web) as harness:
        result = await _fetch(harness)

        first = _attempt(result)
        assert first["verdict"] == "schema_drift", (
            f"a missing promised path is schema_drift, got {first['verdict']!r}"
        )
        assert mock_web.count("yahoo") == 1
        assert result.observations == []
        assert len(result.attempts) == 1, (
            "schema_drift is non-retryable: no later rung is tried (02 §3.4)"
        )

        health = harness.health(first["provider"])
        assert health is not None, "schema_drift must be recorded in provider_health"
        assert health["last_verdict"] == "schema_drift", (
            f"provider_health.last_verdict is {health['last_verdict']!r}, expected schema_drift"
        )


async def test_404_is_not_found_and_does_not_fabricate_a_row(
    tmp_path: Any, monkeypatch: Any, mock_web: Any
) -> None:
    mock_web.status("yahoo", 404)

    async with live_client(tmp_path, monkeypatch, mock_web) as harness:
        result = await _fetch(harness)

    first = _attempt(result)
    assert first["verdict"] == "not_found"
    assert result.observations == []
    assert len(result.attempts) == 1, (
        "not_found is non-retryable: the ladder must stop, not hammer the next rung"
    )
    assert mock_web.count("stooq") == 0


async def test_complete_payload_is_ok_and_parses_last_price(
    tmp_path: Any, monkeypatch: Any, mock_web: Any
) -> None:
    yahoo_route(mock_web)

    async with live_client(tmp_path, monkeypatch, mock_web) as harness:
        result = await _fetch(harness)

    provider = first_quote_spec().name
    assert result.verdict.ok is True, f"expected ok, got {result.attempts!r}"
    assert result.rung == provider
    assert _attempt(result)["verdict"] == "ok"
    prices = [item for item in result.observations if item["field"] == "last_price"]
    assert prices, f"last_price was not parsed: {result.observations!r}"
    assert prices[0]["value_num"] == pytest.approx(231.5)
    assert prices[0]["source"] == provider
    assert prices[0]["unit"] == "price:USD"

    health = _health_or_none(tmp_path, provider)
    assert health is not None and health["last_verdict"] == "ok"
    assert health["last_ok_at"] is not None


def _health_or_none(tmp_path: Any, provider: str) -> dict[str, Any] | None:
    """Read health outside an open harness (the DB is closed by then)."""

    from tests._phase2 import provider_health_row

    return provider_health_row(tmp_path / "mee.db", provider, "quote")
