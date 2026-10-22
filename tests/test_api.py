"""Phase-0 API RED suite (02 §3.2, §5 gate 2/4).

Flow under test: ``POST /markets/enrich`` (fake provider) -> 202 Pending ->
``GET /requests/{id}`` -> Success -> ``GET /requests/{id}/export/csv`` with a
header that is exactly the 92 canonical columns.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from tests._support import COLUMNS, api_client, enrich_payload, wait_for_terminal


async def test_enrich_returns_202_with_a_pending_job(tmp_path: Path, monkeypatch: Any) -> None:
    async with api_client(tmp_path, monkeypatch) as client:
        response = await client.post("/markets/enrich", json=enrich_payload())

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["status"] == "Pending"
        assert body["id"]
        assert body["id"] in body["results_location"]
        assert body["expires_at"]


async def test_enrich_job_reaches_success_on_the_fake_provider(
    tmp_path: Path, monkeypatch: Any, block_outbound_network: list[str]
) -> None:
    async with api_client(tmp_path, monkeypatch) as client:
        created = await client.post("/markets/enrich", json=enrich_payload())
        assert created.status_code == 202, created.text
        job_id = created.json()["id"]

        final = await wait_for_terminal(client, job_id)

        assert final["status"] == "Success", final
        assert final["service"] == "market_enrich"
        assert final["result_count"] == 3
        assert final["rows_target"] == 3
        assert final["error"] is None
        assert final["files"]["csv"].endswith(f"/requests/{job_id}/export/csv")
        assert block_outbound_network == [], "the fake path must not touch the network"


async def test_enrich_rejects_an_unknown_knob_with_422(tmp_path: Path, monkeypatch: Any) -> None:
    async with api_client(tmp_path, monkeypatch) as client:
        payload = enrich_payload()
        payload["bogus_knob"] = True
        response = await client.post("/markets/enrich", json=payload)

        assert response.status_code == 422, response.text
        assert "bogus_knob" in response.text


async def test_enrich_rejects_an_unknown_field_name_with_422(tmp_path: Path, monkeypatch: Any) -> None:
    async with api_client(tmp_path, monkeypatch) as client:
        response = await client.post(
            "/markets/enrich", json=enrich_payload(fields=["last_price", "not_a_column"])
        )

        assert response.status_code == 422, response.text
        assert "not_a_column" in response.text


async def test_enrich_rejects_an_unknown_market_kind_with_422(tmp_path: Path, monkeypatch: Any) -> None:
    async with api_client(tmp_path, monkeypatch) as client:
        response = await client.post(
            "/markets/enrich", json=enrich_payload(market={"kind": "nonsense", "symbols": ["AAPL"]})
        )

        assert response.status_code == 422, response.text


async def test_export_csv_header_is_exactly_canonical_v1(tmp_path: Path, monkeypatch: Any) -> None:
    async with api_client(tmp_path, monkeypatch) as client:
        created = await client.post("/markets/enrich", json=enrich_payload())
        assert created.status_code == 202, created.text
        job_id = created.json()["id"]
        final = await wait_for_terminal(client, job_id)
        assert final["status"] == "Success", final

        response = await client.get(f"/requests/{job_id}/export/csv")

        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/csv")
        # RFC4180: a text cell may contain a comma, so parse instead of split().
        table = list(csv.reader(io.StringIO(response.text)))
        header = table[0]
        assert header == COLUMNS, f"header mismatch: got {len(header)} columns"
        assert len(header) == 92
        assert len(table) == 1 + 3, f"expected 3 data rows, got {len(table) - 1}"
        assert all(len(row) == 92 for row in table[1:]), [len(row) for row in table[1:]]
