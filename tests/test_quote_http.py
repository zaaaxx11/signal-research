"""Phase-1 RED — the quote over HTTP: issued by estimate, spent by enrich.

Gate (02 §5 Fase 1 #4, §3.2, §4.3):

* ``/markets/estimate`` returns ``quote_id`` + ``quote_expires_at``;
* ``/markets/enrich`` with that id runs the **frozen plan**, not the caller's
  symbols (``job.params`` proves it);
* second use → **409**; foreign owner → **409** without burning the quote;
  past the TTL → **410**;
* a refused quote never leaves a job row behind: a 409/410 that creates a
  Pending job is a job the client will poll forever.

``quotes.expires_at`` is a NOT NULL column in the frozen schema (02 §4.3), so
the TTL case moves that timestamp into the past instead of sleeping, and the
"frozen plan won" assertions read ``jobs.params`` (02 §4.1) — the two places
where the contract is observable without inventing an API field.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tests._phase1 import (
    AS_OF,
    age_quote,
    count_jobs,
    enrich_with_quote_payload,
    estimate_payload,
    job_params,
    parse_dt,
    quote_plan,
    quote_row,
    reference_client,
)

OWNER = {"x-api-key": "key-owner"}
STRANGER = {"x-api-key": "key-stranger"}

FIELDS = ["instrument_id", "symbol", "mic", "currency", "last_price", "quote_as_of"]
FROZEN_SYMBOLS = ["AAPL", "MSFT"]


async def _quoted(
    client: Any, payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None
) -> dict[str, Any]:
    """Estimate a 2-symbol watchlist (as ``OWNER``) and return the response body."""

    body = payload or estimate_payload(
        market={"kind": "watchlist", "symbols": ["MSFT", "AAPL"]},
        fields=FIELDS,
        target_rows=None,
    )
    response = await client.post("/markets/estimate", json=body, headers=headers or OWNER)
    assert response.status_code == 200, response.text
    issued = response.json()
    assert issued["quote_id"], f"estimate did not issue a quote: {issued!r}"
    return issued


# ---------------------------------------------------------------------------
# Issue
# ---------------------------------------------------------------------------


async def test_estimate_issues_a_single_use_quote_with_expiry(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with reference_client(tmp_path, monkeypatch) as client:
        issued = await _quoted(client)

    quote_id = issued["quote_id"]
    assert isinstance(quote_id, str) and quote_id
    expires_at = parse_dt(issued["quote_expires_at"])
    now = datetime.now(timezone.utc)
    assert expires_at > now, f"a quote issued now must expire in the future: {expires_at}"
    assert expires_at <= now + timedelta(days=1), f"quote TTL is out of range: {expires_at}"

    row = quote_row(tmp_path, quote_id)
    assert row["status"] == "Open"
    assert row["used_at"] is None, "a freshly issued quote is unused"
    assert row["job_id"] is None
    assert str(row["api_key"]).startswith("sha256:"), "the owner must be a fingerprint, never a key"
    assert parse_dt(row["expires_at"]) == expires_at

    frozen = quote_plan(tmp_path, quote_id)
    assert frozen["universe"]["symbols"] == FROZEN_SYMBOLS, (
        f"the quote must freeze the plan order, got {frozen['universe']['symbols']!r}"
    )
    assert sorted(frozen["fields"]) == sorted(FIELDS)
    assert frozen["columns_version"] == "v1"


# ---------------------------------------------------------------------------
# Spend — the frozen plan wins over the caller's symbols
# ---------------------------------------------------------------------------


async def test_enrich_consumes_the_quote_and_uses_the_frozen_plan(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with reference_client(tmp_path, monkeypatch) as client:
        issued = await _quoted(client)
        quote_id = issued["quote_id"]

        # The caller presents a *different* universe and a different as_of.
        response = await client.post(
            "/markets/enrich", json=enrich_with_quote_payload(quote_id), headers=OWNER
        )
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]

        view = await client.get(f"/requests/{job_id}", headers=OWNER)
        assert view.status_code == 200, view.text
        view_body = view.json()

    params = job_params(tmp_path, job_id)
    market = params["market"]
    assert market["symbols"] == FROZEN_SYMBOLS, (
        f"the quote froze {FROZEN_SYMBOLS}, the caller asked for ['NVDA']; params={market!r}"
    )
    assert "NVDA" not in str(market), "the caller's symbols must not survive into the frozen plan"
    assert sorted(params["fields"]) == sorted(FIELDS), "fields must come from the frozen plan"
    assert parse_dt(params["as_of"]) == parse_dt(AS_OF), (
        "as_of must come from the quote's freshness, not from the caller"
    )

    row = quote_row(tmp_path, quote_id)
    assert row["used_at"] is not None, "a spent quote must be marked"
    assert row["job_id"] == job_id, "quotes.job_id is the link from plan to job (02 §4.3)"

    assert view_body["rows_target"] == len(FROZEN_SYMBOLS), view_body["rows_target"]


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


async def test_second_use_of_a_quote_is_409_and_creates_no_job(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with reference_client(tmp_path, monkeypatch) as client:
        quote_id = (await _quoted(client))["quote_id"]

        first = await client.post(
            "/markets/enrich", json=enrich_with_quote_payload(quote_id), headers=OWNER
        )
        assert first.status_code == 202, first.text
        jobs_after_first = count_jobs(tmp_path)

        replay = await client.post(
            "/markets/enrich", json=enrich_with_quote_payload(quote_id), headers=OWNER
        )

    assert replay.status_code == 409, replay.text
    body = replay.json()
    assert {"error", "errorMessage"} <= set(body), f"error envelope expected, got {body!r}"
    assert count_jobs(tmp_path) == jobs_after_first, "a 409 must not leave a second job behind"


async def test_foreign_owner_gets_409_without_burning_the_quote(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with reference_client(tmp_path, monkeypatch) as client:
        quote_id = (await _quoted(client))["quote_id"]

        stolen = await client.post(
            "/markets/enrich", json=enrich_with_quote_payload(quote_id), headers=STRANGER
        )
        assert stolen.status_code == 409, stolen.text
        assert {"error", "errorMessage"} <= set(stolen.json())
        assert count_jobs(tmp_path) == 0, "a foreign attempt must not create a job"
        assert quote_row(tmp_path, quote_id)["used_at"] is None, (
            "a failed ownership check must not spend the owner's quote"
        )

        honest = await client.post(
            "/markets/enrich", json=enrich_with_quote_payload(quote_id), headers=OWNER
        )

    assert honest.status_code == 202, honest.text


async def test_unknown_quote_id_is_409_not_404(tmp_path: Path, monkeypatch: Any) -> None:
    """An unknown id and a spent id must be indistinguishable (no oracle)."""

    async with reference_client(tmp_path, monkeypatch) as client:
        response = await client.post(
            "/markets/enrich",
            json=enrich_with_quote_payload("00000000-0000-4000-8000-000000000000"),
            headers=OWNER,
        )

    assert response.status_code == 409, response.text
    assert count_jobs(tmp_path) == 0


async def test_expired_quote_is_410(tmp_path: Path, monkeypatch: Any) -> None:
    async with reference_client(tmp_path, monkeypatch) as client:
        quote_id = (await _quoted(client))["quote_id"]
        age_quote(tmp_path, quote_id, seconds=120)

        response = await client.post(
            "/markets/enrich", json=enrich_with_quote_payload(quote_id), headers=OWNER
        )

    assert response.status_code == 410, response.text
    assert {"error", "errorMessage"} <= set(response.json())
    assert count_jobs(tmp_path) == 0, "an expired quote must not create a job"
    assert quote_row(tmp_path, quote_id)["used_at"] is None, "expiry is not consumption"
