"""Read-only HTTP surface of the Source Graph (PLAN §4).

Five routes, one document:

* ``GET /sources/graph`` — the graph file, whole. The handler calls
  :func:`app.sources.graph.load_graph` **as a module attribute**, so patching the
  loader changes the response: the API cannot serve a Python copy of the graph
  that drifts from ``reference/source_graph.json``.
* ``GET /sources/fields/{column}`` — one resolution, with the same conditional
  rules as the resolver (``?mic=``, ``?asset_class=``, ``?country=``, ``?region=``).
  An unknown column is a 404 in the house envelope ``{error, errorMessage}``.
* ``GET /sources/schema`` — ``schemas/source_graph_v1.json``.
* ``GET /sources/sources`` — every node with its status, delay and access map.
* ``GET /sources/capabilities`` — capability → ordered source ids, the same
  order :func:`app.sources.graph.resolve_capability` returns.

Nothing here writes, and nothing here names a credential: the graph names
sources, not keys. The router needs no database and no runtime state, which is
why it is mounted with :func:`app.main.mount_router` like ``/screens``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.auth import ApiError
from app.sources import graph
from app.sources.schema import SOURCE_GRAPH_SCHEMA

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("/graph")
async def graph_document() -> dict[str, Any]:
    """The reference document, byte-for-byte (no projection, no re-derivation)."""

    return graph.load_graph()


@router.get("/schema")
async def graph_schema() -> dict[str, Any]:
    """The published ``source-graph-v1`` JSON Schema."""

    return SOURCE_GRAPH_SCHEMA


@router.get("/sources")
async def sources() -> dict[str, Any]:
    """Every node with the facts a caller needs to decide whether to use it."""

    document = graph.load_graph()
    nodes = graph.nodes_of(document)
    return {
        "version": document.get("version"),
        "count": len(nodes),
        "sources": [
            {
                "id": node["id"],
                "label": node["label"],
                "kind": node["kind"],
                "authority": node["authority"],
                "trust": node["trust"],
                "delay": node["delay"],
                "status": node["status"],
                "robots": node["robots"],
                "capabilities": node["capabilities"],
                "access": node["access"],
            }
            for node in nodes
        ],
    }


@router.get("/capabilities")
async def capabilities() -> dict[str, Any]:
    """Capability → ordered source ids ``(trust asc, id asc)``."""

    document = graph.load_graph()
    return {
        "version": document.get("version"),
        "capabilities": {
            capability: [
                source["id"]
                for source in graph.resolve_capability(capability, graph=document)
            ]
            for capability in graph.NODE_CAPABILITIES
        },
    }


@router.get("/fields/{column}")
async def field_resolution(
    column: str,
    mic: str | None = None,
    asset_class: str | None = None,
    country: str | None = None,
    region: str | None = None,
) -> dict[str, Any]:
    """Resolve one column for the caller's market context (fail-closed)."""

    try:
        return graph.resolve_field(
            column,
            mic=mic,
            asset_class=asset_class,
            country=country,
            region=region,
            graph=graph.load_graph(),
        )
    except KeyError as exc:
        raise ApiError(
            404,
            "unknown_field",
            f"column {column!r} is not in the source graph: {exc.args[0] if exc.args else column}",
        ) from exc
