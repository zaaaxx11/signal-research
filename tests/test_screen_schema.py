"""The browse JSON Schema is served, versioned and shape-locked."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests._support import api_client


async def test_schema_endpoint_returns_the_published_schema(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with api_client(tmp_path, monkeypatch) as client:
        response = await client.get("/screens/schema")

    assert response.status_code == 200, response.text
    schema = response.json()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"].endswith("/screen-response-v1.json")
    assert set(schema["required"]) == {
        "screen_id",
        "region",
        "sort",
        "source",
        "as_of",
        "refreshed_at",
        "next_refresh_at",
        "stale",
        "total",
        "returned",
        "rows",
    }
    assert schema["properties"]["rows"]["maxItems"] == 20
    row = schema["$defs"]["row"]
    assert row["additionalProperties"] is False
    assert "turnover_usd" in row["properties"]
    assert row["properties"]["turnover_usd"]["$ref"] == "#/$defs/nullableNumber"


def test_schema_is_also_a_repo_artifact() -> None:
    import json

    from app.screen.schema import SCREEN_RESPONSE_SCHEMA

    artifact = Path(__file__).resolve().parents[1] / "schemas" / "screen_response_v1.json"
    assert artifact.exists(), (
        "the schema clients consume must also exist as a file at "
        "schemas/screen_response_v1.json"
    )
    assert json.loads(artifact.read_text()) == SCREEN_RESPONSE_SCHEMA
