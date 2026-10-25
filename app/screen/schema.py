"""The public JSON Schema for ``GET /screens``.

The response is a display document, not a generic dump: exactly twenty rows,
each carrying its snapshot timestamp, and a `total` that describes the full
post-filter/post-dedupe universe. The schema is served at ``/screens/schema``
and written to ``schemas/screen_response_v1.json`` so a client can validate a
response without importing Python.

P10 added a second document under the same envelope. ``source="multi"`` is the
verified consensus: the same twenty rows, each cell the median of the sources
that answered, plus a ``source_summary`` per row (and ``selection_source`` /
``degraded`` / ``verification_error`` at the top). ``source="tradingview"`` —
explicitly requested, or served as an honest fallback when verification failed —
is the raw snapshot and carries no ``source_summary``. Both validate against this
schema: the P10 keys are additive and optional, so a client written for the raw
document keeps working, and one that reads ``source_summary`` gets a fixed
six-key object when it is present.
"""

from __future__ import annotations

SCREEN_RESPONSE_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://ztockz.local/schemas/screen-response-v1.json",
    "title": "ScreenResponseV1",
    "description": (
        "Top 20 of a five-minute screen snapshot. `rows` is always the top 20 "
        "of the requested region/sort; `total` is the full post-filter/post-dedupe "
        "universe, which may be larger than 20."
    ),
    "type": "object",
    "additionalProperties": False,
    "required": [
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
    ],
    "properties": {
        "screen_id": {
            "type": "string",
            "description": "Opaque snapshot id; the same value for every read inside the 300 s window.",
        },
        "region": {"type": "string", "enum": ["us", "global", "us_etf"]},
        "sort": {"type": "string", "enum": ["market_cap", "turnover", "gainers"]},
        "source": {
            "type": "string",
            "enum": ["multi", "tradingview"],
            "description": (
                "`multi` is the verified consensus (P10 default); `tradingview` is "
                "the raw snapshot, either requested explicitly or served degraded "
                "when no verifier answered."
            ),
        },
        "selection_source": {
            "type": "string",
            "enum": ["tradingview"],
            "description": (
                "Present on multi-source documents: the source the twenty "
                "candidates were selected from. Always TradingView."
            ),
        },
        "degraded": {
            "type": "boolean",
            "description": (
                "Present on multi-source documents. True means verification failed "
                "and the raw snapshot is being served; never true with source=multi."
            ),
        },
        "verification_error": {
            "type": ["string", "null"],
            "description": (
                "Present on multi-source documents: why verification fell back to "
                "the snapshot. Null on a healthy consensus."
            ),
        },
        "as_of": {"type": "string", "format": "date-time"},
        "refreshed_at": {"type": "string", "format": "date-time"},
        "next_refresh_at": {
            "type": ["string", "null"],
            "format": "date-time",
            "description": "When the snapshot becomes refreshable; null only for an incomplete stored row.",
        },
        "stale": {
            "type": "boolean",
            "description": "True when a refresh was blocked and the previous snapshot is being served honestly.",
        },
        "total": {
            "type": "integer",
            "minimum": 0,
            "description": "Full post-filter/post-dedupe universe size, not the page size.",
        },
        "returned": {
            "type": "integer",
            "minimum": 0,
            "maximum": 20,
            "description": "Rows in this response; always 20 unless the universe is smaller.",
        },
        "rows": {
            "type": "array",
            "maxItems": 20,
            "items": {"$ref": "#/$defs/row"},
        },
    },
    "$defs": {
        "nullableNumber": {"type": ["number", "null"]},
        "sourceSummary": {
            "type": "object",
            "additionalProperties": False,
            "description": (
                "How a consensus row was produced (P10). Present only when "
                "source=multi; an unverified snapshot row has no summary."
            ),
            "required": [
                "method",
                "source_count",
                "agreement",
                "spread_pp",
                "confidence",
                "verified_at",
            ],
            "properties": {
                "method": {"type": "string", "const": "multi_source_consensus"},
                "source_count": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Sources that published at least one number for the row.",
                },
                "agreement": {
                    "type": ["number", "null"],
                    "minimum": 0,
                    "maximum": 1,
                    "description": (
                        "Share of change observations within the tolerance of the "
                        "median change; null when no source published a change."
                    ),
                },
                "spread_pp": {
                    "type": ["number", "null"],
                    "minimum": 0,
                    "description": (
                        "max−min change in percentage points; null with fewer than "
                        "two change observations."
                    ),
                },
                "confidence": {
                    "type": ["number", "null"],
                    "minimum": 0,
                    "maximum": 1,
                    "description": "Agreement discounted by source coverage.",
                },
                "verified_at": {"type": "string", "format": "date-time"},
            },
        },
        "row": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "rank",
                "symbol",
                "logo",
                "name",
                "exchange",
                "country",
                "sector",
                "price",
                "change_pct",
                "volume",
                "turnover",
                "turnover_usd",
                "market_cap_usd",
                "is_primary",
                "as_of",
            ],
            "properties": {
                "rank": {"type": "integer", "minimum": 1, "maximum": 20},
                "symbol": {"type": "string", "minLength": 1},
                "logo": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "Guaranteed brand mark for the instrument. A vendored asset "
                        "or an offline monogram data URI; never null."
                    ),
                },
                "name": {"type": ["string", "null"]},
                "exchange": {"type": ["string", "null"]},
                "country": {"type": ["string", "null"]},
                "sector": {"type": ["string", "null"]},
                "price": {"$ref": "#/$defs/nullableNumber"},
                "change_pct": {"$ref": "#/$defs/nullableNumber"},
                "volume": {"$ref": "#/$defs/nullableNumber"},
                "turnover": {"$ref": "#/$defs/nullableNumber"},
                "turnover_usd": {
                    "$ref": "#/$defs/nullableNumber",
                    "description": "Null for non-USD listings until an FX rate is applied; never invented.",
                },
                "market_cap_usd": {"$ref": "#/$defs/nullableNumber"},
                "is_primary": {"type": "boolean"},
                "as_of": {"type": "string", "format": "date-time"},
                "source_summary": {
                    "$ref": "#/$defs/sourceSummary",
                    "description": (
                        "Present on a multi-source row (source=multi): the medians "
                        "that built it and how much the sources agreed. Never "
                        "per-source cells."
                    ),
                },
            },
        }
    },
}

__all__ = ["SCREEN_RESPONSE_SCHEMA"]
