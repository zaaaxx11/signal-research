"""Source Graph SG0 RED — the read-only API over the reference graph.

Contract (PLAN-source-graph.md §4):

* ``GET /sources/graph`` serves **the file**, not a second Python copy: the
  route must call ``app.sources.graph.load_graph()`` as a module attribute, so
  patching the loader changes the response.
* ``GET /sources/fields/{column}`` resolves one column and applies the same
  conditional rules as the resolver (``?mic=``), 404 with the ``{error,
  errorMessage}`` envelope for an unknown column.
* ``GET /sources/schema`` serves ``schemas/source_graph_v1.json``, which is
  also ``app.sources.schema.SOURCE_GRAPH_SCHEMA``.
* No response carries a credential: the graph names sources, not keys.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from tests import _source_graph as SG


def _doctor(document: dict[str, Any], label: str) -> dict[str, Any]:
    """Change one node label in place — shape-agnostic proof the file was read."""

    section = document["nodes"]
    first = section[0] if isinstance(section, list) else next(iter(section.values()))
    first["label"] = label
    return document


def _ids(resolution: dict[str, Any]) -> list[str]:
    return [
        resolution["primary"]["id"],
        *[SG.source_id(item) for item in resolution["fallbacks"]],
        *[SG.source_id(item) for item in resolution["crosschecks"]],
    ]


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


async def test_graph_endpoint_serves_the_reference_file(
    tmp_path: Path, monkeypatch: Any
) -> None:
    document = SG.graph_doc()

    async with SG.sources_client(tmp_path, monkeypatch) as client:
        response = await client.get("/sources/graph")

    assert response.status_code == 200, response.text
    assert response.json() == document, (
        "GET /sources/graph must be the reference document byte-for-byte; a partial "
        "projection would let the API and the file drift"
    )


async def test_graph_endpoint_reads_the_loader_not_a_python_copy(
    tmp_path: Path, monkeypatch: Any
) -> None:
    module = SG.graph_module()
    doctored = _doctor(copy.deepcopy(SG.graph_doc()), "Doctored Source (SG0 RED)")
    monkeypatch.setattr(module, "load_graph", lambda *args, **kwargs: copy.deepcopy(doctored))

    async with SG.sources_client(tmp_path, monkeypatch) as client:
        response = await client.get("/sources/graph")

    assert response.status_code == 200, response.text
    assert response.json() == doctored, (
        "the route answered from a Python literal instead of the loader; PLAN §5 gate 7 "
        "requires graph == file"
    )


async def test_graph_response_covers_the_92_fields(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with SG.sources_client(tmp_path, monkeypatch) as client:
        response = await client.get("/sources/graph")

    body = response.json()
    assert len(SG.fields(body)) == 92
    assert {entry["column"] for entry in SG.fields(body)} == set(SG.COLUMNS)


# ---------------------------------------------------------------------------
# Field resolution
# ---------------------------------------------------------------------------


async def test_field_endpoint_returns_the_last_price_resolution(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with SG.sources_client(tmp_path, monkeypatch) as client:
        response = await client.get("/sources/fields/last_price")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["column"] == "last_price"
    assert body["primary"]["id"] == "yahoo"
    assert isinstance(body["fallbacks"], list)
    assert isinstance(body["crosschecks"], list)
    assert body["policy"] == SG.POLICY


async def test_field_endpoint_applies_the_us_only_finviz_gate(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with SG.sources_client(tmp_path, monkeypatch) as client:
        us = await client.get("/sources/fields/last_price", params={"mic": "XNAS"})
        global_ = await client.get("/sources/fields/last_price", params={"mic": SG.GLOBAL_MIC})

    assert us.status_code == 200, us.text
    assert global_.status_code == 200, global_.text
    assert "finviz" in _ids(us.json())
    assert "finviz" not in _ids(global_.json()), (
        "the API must not hand a US-only source to a global resolution"
    )


async def test_unknown_field_is_404_with_the_error_envelope(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with SG.sources_client(tmp_path, monkeypatch) as client:
        response = await client.get("/sources/fields/not_a_column")

    assert response.status_code == 404, response.text
    body = response.json()
    assert set(body) == {"error", "errorMessage"}, body
    assert "not_a_column" in body["errorMessage"]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


async def test_schema_endpoint_serves_the_published_schema(
    tmp_path: Path, monkeypatch: Any
) -> None:
    artifact = SG.schema_doc()
    module_schema = SG.schema_module().SOURCE_GRAPH_SCHEMA

    assert module_schema == artifact, (
        "app.sources.schema.SOURCE_GRAPH_SCHEMA must equal schemas/source_graph_v1.json; "
        "two copies of a schema is two schemas"
    )

    async with SG.sources_client(tmp_path, monkeypatch) as client:
        response = await client.get("/sources/schema")

    assert response.status_code == 200, response.text
    assert response.json() == artifact
    assert artifact["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert artifact["$id"] == SG.SCHEMA_ID
    assert {"version", "nodes", "edges", "fields", "screen_fields"} <= set(
        artifact["required"]
    ), artifact.get("required")
    assert "fields" in artifact["properties"]


# ---------------------------------------------------------------------------
# Mount + secrets
# ---------------------------------------------------------------------------


async def test_the_three_read_routes_answer(tmp_path: Path, monkeypatch: Any) -> None:
    async with SG.sources_client(tmp_path, monkeypatch) as client:
        for path in ("/sources/graph", "/sources/schema", "/sources/fields/last_price"):
            response = await client.get(path)
            assert response.status_code == 200, (path, response.status_code, response.text)

    assert SG.api_module().router is not None


async def test_no_response_carries_a_credential(tmp_path: Path, monkeypatch: Any) -> None:
    async with SG.sources_client(tmp_path, monkeypatch) as client:
        graph_text = (await client.get("/sources/graph")).text
        schema_text = (await client.get("/sources/schema")).text

    for label, text in (("graph", graph_text), ("schema", schema_text)):
        lowered = text.lower()
        for token in SG.SECRET_TOKENS:
            assert token not in lowered, (
                f"the {label} response mentions {token!r}; a source graph names sources, "
                "never credentials"
            )
