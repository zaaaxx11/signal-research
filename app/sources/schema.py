"""The published JSON Schema of the Source Graph (``source-graph-v1``).

The graph is served at ``GET /sources/schema`` and written to
``schemas/source_graph_v1.json`` so a client can validate the document without
importing Python. The two are byte-for-byte the same document: this module
literal is the source and the artifact is dumped from it, so a schema that only
exists in a test or only in a file cannot diverge.

The schema is deliberately strict: ``additionalProperties: false`` on the
document, a node, an edge and a field entry means a typo (``primry``) is a
validation error and not a silently ignored key. The 92 columns are enumerated
so a client can prove coverage rather than trusting a count.
"""

from __future__ import annotations

from app.contracts import CANONICAL_COLUMNS_V1
from app.sources.graph import (
    AUTHORITIES,
    DELAYS,
    FIELD_TYPES,
    GRAPH_VERSION,
    NODE_CAPABILITIES,
    NODE_KINDS,
    PLAN_GROUPS,
    RELATIONS,
    ROBOTS,
    SCREEN_FIELD_NAMES,
    STATUSES,
    WHEN_KEYS,
)

__all__ = ["SOURCE_GRAPH_SCHEMA"]


def _candidate() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["id"],
        "properties": {
            "id": {
                "type": "string",
                "minLength": 1,
                "description": "Node id of a fallback or cross-check source.",
            },
            "when": {"$ref": "#/$defs/when"},
            "via": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "The route the value must be taken from. `quote` is the only legal "
                    "route for a Finviz 52-week extreme: the screener column is percent "
                    "distance, not a price."
                ),
                "examples": ["quote"],
            },
            "beta_window": {
                "type": "string",
                "enum": ["1y", "5y"],
                "description": "Window of a beta candidate; a 1y beta is not a 5y beta.",
            },
        },
    }


def _when() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "minProperties": 1,
        "description": (
            "Predicate a candidate applies under. Only these keys are evaluated; a "
            "predicate whose fact the caller did not supply is not applied (fail-closed)."
        ),
        "properties": {
            key: {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "minItems": 1, "items": {"type": "string"}},
                ]
            }
            for key in WHEN_KEYS
        },
    }


SOURCE_GRAPH_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://ztockz.local/schemas/source-graph-v1.json",
    "title": "SourceGraphV1",
    "description": (
        "Which source may fill each of the 92 canonical columns, in which order the "
        "substitutes may be tried, which independent source may contradict the value, "
        "and how it is computed. The graph stores no market value and no credential: "
        "it is a decision map, not a quote store."
    ),
    "type": "object",
    "additionalProperties": False,
    "required": ["version", "nodes", "edges", "fields", "screen_fields"],
    "properties": {
        "version": {"const": GRAPH_VERSION},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "conflict_policy": {
            "type": "string",
            "description": (
                "What the resolver does when two sources disagree. The only legal "
                "value is the primary-wins policy: two feeds are never averaged."
            ),
        },
        "provenance": {
            "type": "object",
            "description": "Metadata SG1 writes on every filled cell.",
            "additionalProperties": True,
        },
        "nodes": {
            "type": "array",
            "minItems": 1,
            "description": "The closed vocabulary of sources; ids are unique.",
            "items": {"$ref": "#/$defs/node"},
        },
        "edges": {
            "type": "array",
            "description": (
                "Relations between sources. A fallback or crosscheck edge is per-field "
                "and must name one of the fields; a derived_from edge must come from a "
                "derived node."
            ),
            "items": {"$ref": "#/$defs/edge"},
        },
        "fields": {
            "type": "array",
            "minItems": len(CANONICAL_COLUMNS_V1),
            "maxItems": len(CANONICAL_COLUMNS_V1),
            "description": (
                "Exactly the 92 canonical columns in export order, one entry each, so a "
                "coverage hole is a schema violation and not a missing row."
            ),
            "items": {"$ref": "#/$defs/field"},
        },
        "screen_fields": {
            "type": "array",
            "minItems": len(SCREEN_FIELD_NAMES),
            "maxItems": len(SCREEN_FIELD_NAMES),
            "description": (
                "Fields of the TradingView screen snapshot that are not export columns "
                "(beta with its window, and the two 52-week extremes)."
            ),
            "items": {"$ref": "#/$defs/screenField"},
        },
    },
    "$defs": {
        "when": _when(),
        "candidate": _candidate(),
        "node": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "id",
                "label",
                "kind",
                "authority",
                "trust",
                "delay",
                "license",
                "robots",
                "status",
                "access",
                "capabilities",
            ],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "label": {"type": "string", "minLength": 1},
                "kind": {"type": "string", "enum": list(NODE_KINDS)},
                "authority": {"type": "string", "enum": list(AUTHORITIES)},
                "trust": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 4,
                    "description": "1 official, 4 scrape — the Observation trust scale.",
                },
                "delay": {"type": "string", "enum": list(DELAYS)},
                "license": {"type": "string", "minLength": 1},
                "robots": {"type": "string", "enum": list(ROBOTS)},
                "status": {"type": "string", "enum": list(STATUSES)},
                "access": {
                    "type": "object",
                    "minProperties": 1,
                    "description": (
                        "Route -> verdict for this source. `robots_disallowed` and `none` "
                        "forbid production use of that route."
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "capabilities": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "enum": list(NODE_CAPABILITIES)},
                },
                "derived_from": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string"},
                    "description": "Inputs of a derived node; must be acyclic.",
                },
                "methodology": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Formula/version a derived node is computed with.",
                },
                "beta_windows": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["1y", "5y"]},
                    "description": "Beta windows this source actually computes.",
                },
                "notes": {"type": "string"},
            },
        },
        "edge": {
            "type": "object",
            "additionalProperties": False,
            "required": ["from", "to", "relation"],
            "properties": {
                "from": {"type": "string"},
                "to": {"type": "string"},
                "relation": {"type": "string", "enum": list(RELATIONS)},
                "field": {
                    "type": "string",
                    "description": "Required for a fallback or crosscheck edge.",
                },
                "capability": {"type": "string", "enum": list(NODE_CAPABILITIES)},
                "trust": {"type": "integer", "minimum": 1, "maximum": 4},
                "via": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "Route the value must come from; `quote` on a Finviz 52-week "
                        "edge records that the screener column is not a price."
                    ),
                },
                "when": {
                    "oneOf": [
                        {"$ref": "#/$defs/when"},
                        {"type": "string", "minLength": 1},
                    ],
                    "description": "Predicate object, or a prose note on the relation.",
                },
            },
        },
        "field": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "column",
                "group",
                "type",
                "primary",
                "fallbacks",
                "crosschecks",
                "methodology",
            ],
            "properties": {
                "column": {"type": "string", "enum": list(CANONICAL_COLUMNS_V1)},
                "group": {"type": "string", "enum": list(PLAN_GROUPS)},
                "type": {"type": "string", "enum": list(FIELD_TYPES)},
                "primary": {"type": "string", "minLength": 1},
                "fallbacks": {"type": "array", "items": {"$ref": "#/$defs/candidate"}},
                "crosschecks": {"type": "array", "items": {"$ref": "#/$defs/candidate"}},
                "methodology": {"type": "string", "minLength": 1},
            },
        },
        "screenField": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "column",
                "group",
                "type",
                "primary",
                "fallbacks",
                "crosschecks",
                "methodology",
            ],
            "properties": {
                "column": {"type": "string", "enum": list(SCREEN_FIELD_NAMES)},
                "group": {"type": "string", "enum": list(PLAN_GROUPS)},
                "type": {"type": "string", "enum": list(FIELD_TYPES)},
                "primary": {"type": "string", "minLength": 1},
                "fallbacks": {"type": "array", "items": {"$ref": "#/$defs/candidate"}},
                "crosschecks": {"type": "array", "items": {"$ref": "#/$defs/candidate"}},
                "methodology": {"type": "string", "minLength": 1},
                "beta_window": {
                    "type": "string",
                    "enum": ["1y", "5y"],
                    "description": "Canonical beta window; 5y for a comparable beta.",
                },
            },
        },
    },
}
