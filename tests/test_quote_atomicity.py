"""Phase-2 RED: quote consumption and job creation are one atomic step.

Phase 1 shipped ``consume`` → ``create`` → ``attach_job`` as three commits. A
failure between the first two burns a paid plan and leaves no job: the client
paid (in quote currency) for nothing, and the quote row says "Used".

The fault is injected **in the database**, not in a mock: a ``BEFORE INSERT``
trigger on ``jobs`` aborts every insert. Whatever the implementation is — one
transaction spanning ``quotes`` and ``jobs``, or consume + compensating release
— the observable invariant is the same and is asserted here:

* the quote is still ``Open`` with ``used_at IS NULL`` and no ``job_id``;
* no ``jobs`` row exists;
* after the trigger is dropped, the *same* quote can be spent successfully.

A mocked ``repository.create`` would only prove that a test double was called;
the trigger exercises the real storage path.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from tests._phase1 import enrich_with_quote_payload, estimate_payload
from tests._phase2 import (
    allow_job_inserts,
    count_jobs,
    fail_job_inserts,
    quote_client,
    quote_state,
)


async def _new_quote(client: Any) -> str:
    """A real single-use quote over a 2-symbol watchlist (Phase-1 path)."""

    response = await client.post(
        "/markets/estimate",
        json=estimate_payload(
            market={"kind": "watchlist", "symbols": ["AAPL", "BBCA.JK"]}
        ),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    quote_id = payload.get("quote_id")
    assert quote_id, f"/markets/estimate issued no quote: {payload}"
    return str(quote_id)


def _db(tmp_path: Path) -> Path:
    return tmp_path / "mee.db"


async def test_failed_job_insert_leaves_the_quote_unburned_and_no_job_row(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with quote_client(tmp_path, monkeypatch) as (client, _application):
        quote_id = await _new_quote(client)
        fail_job_inserts(_db(tmp_path))

        response = await client.post(
            "/markets/enrich",
            json=enrich_with_quote_payload(quote_id, **{"async": False}),
        )

        assert response.status_code >= 500, (
            "the injected insert failure must surface as a server error, got "
            f"{response.status_code}: {response.text}"
        )
        state = quote_state(_db(tmp_path), quote_id)
        assert state["used_at"] is None, (
            "the quote was burned although the job it paid for was never created"
        )
        assert state["status"] == "Open", f"quote status is {state['status']!r}, expected Open"
        assert state["job_id"] is None
        assert count_jobs(_db(tmp_path)) == 0, "a failed submit left a job row behind"


async def test_quote_can_be_used_after_a_failed_job_insert_is_repaired(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with quote_client(tmp_path, monkeypatch) as (client, _application):
        quote_id = await _new_quote(client)
        fail_job_inserts(_db(tmp_path))

        broken = await client.post(
            "/markets/enrich",
            json=enrich_with_quote_payload(quote_id, **{"async": False}),
        )
        assert broken.status_code >= 500, broken.text

        allow_job_inserts(_db(tmp_path))
        repaired = await client.post(
            "/markets/enrich",
            json=enrich_with_quote_payload(quote_id, **{"async": False}),
        )

        assert repaired.status_code == 200, (
            "the quote could not be reused after the transient failure: "
            f"{repaired.status_code} {repaired.text}"
        )
        body = repaired.json()
        assert body["id"]
        state = quote_state(_db(tmp_path), quote_id)
        assert state["used_at"] is not None, "the successful submit did not consume the quote"
        assert state["status"] == "Used"
        assert state["job_id"] == body["id"], "quotes.job_id does not point at the created job"
        assert count_jobs(_db(tmp_path)) == 1, "the repaired submit must create exactly one job"


async def test_two_concurrent_submits_of_one_quote_create_exactly_one_job(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with quote_client(tmp_path, monkeypatch) as (client, _application):
        quote_id = await _new_quote(client)
        payload = enrich_with_quote_payload(quote_id, **{"async": False})

        first, second = await asyncio.gather(
            client.post("/markets/enrich", json=payload),
            client.post("/markets/enrich", json=payload),
        )

        codes = sorted([first.status_code, second.status_code])
        assert codes == [200, 409], (
            "one quote must produce one 200 and one 409, got "
            f"{[first.status_code, second.status_code]}: {first.text} / {second.text}"
        )
        assert count_jobs(_db(tmp_path)) == 1, "two jobs were created from one quote"
        state = quote_state(_db(tmp_path), quote_id)
        assert state["used_at"] is not None
        assert state["status"] == "Used"
        winner = first.json() if first.status_code == 200 else second.json()
        assert state["job_id"] == winner["id"]


async def test_preflight_refusal_does_not_consume_the_quote(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with quote_client(tmp_path, monkeypatch) as (client, _application):
        quote_id = await _new_quote(client)

        response = await client.post(
            "/markets/enrich",
            json=enrich_with_quote_payload(
                quote_id, format=["xlsx"], **{"async": False}
            ),
        )

        assert response.status_code == 501, (
            "an unimplemented writer is a preflight refusal (501), not a spent quote: "
            f"{response.status_code} {response.text}"
        )
        state = quote_state(_db(tmp_path), quote_id)
        assert state["used_at"] is None
        assert state["status"] == "Open"
        assert count_jobs(_db(tmp_path)) == 0
