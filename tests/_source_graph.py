"""Shared helpers and invalid-graph fixtures for the Source Graph SG0 RED suite.

Not a test module (leading underscore), so pytest never collects it. Every
contract this module pins is expanded in ``briefs/market-engine/26-source-graph-red.md``
and originates in ``PLAN-source-graph.md`` §2–§5.

Contracts pinned here
---------------------

* **One file is the graph.** ``reference/source_graph.json`` carries ``version``,
  ``nodes``, ``edges``, ``fields`` and ``screen_fields``. The API serves that
  file; it does not keep a second copy in Python.
* **92 fields, no orphans.** ``fields`` holds exactly
  :data:`app.contracts.CANONICAL_COLUMNS_V1`, in export order, each with
  ``column, group, type, primary, fallbacks, crosschecks, methodology``.
  ``group`` is the *plan* group of PLAN §3 (``reference``, ``quote``,
  ``eod_bars``, ``performance``, ``fundamentals``, ``options``, ``verify``),
  not the ten-column-group name of ``app.contracts``.
* **Candidate sources carry their own condition.** ``fallbacks`` and
  ``crosschecks`` are objects ``{"id", "when"?, "via"?, "beta_window"?}`` so
  the resolver can filter by ``mic``/``asset_class``/``country`` without
  reading edges. A predicate the caller cannot satisfy is **not** applied
  (fail-closed): ``resolve_field("last_price")`` without ``mic`` must not fall
  back to Finviz, because "US-only" has not been proven.
* **Screen-only fields are separate.** ``beta``, ``high_52w`` and ``low_52w``
  exist in the TradingView screen snapshot but are not among the 92 export
  columns, so they live in ``screen_fields`` (same entry shape, plus
  ``beta_window`` for ``beta``).
* **Nodes are the closed vocabulary of PLAN §2.** ``kind``, ``authority``,
  ``robots``, ``status``, ``delay`` and ``capabilities`` are enumerated;
  ``trust`` is 1..4 (1 official, 4 scrape, same scale as
  ``app.contracts.Observation``).
* **The validator is executable.** ``validate_graph`` rejects a derived_from
  cycle, a missing/blocked primary, a robots-disallowed primary, a duplicate
  fallback, TradingView's 1-year beta as beta primary and Finviz's screener as
  ``high_52w`` primary.

Invalid graphs are **mutations of the production file**, never a second
hand-written 92-field document: the defect under test stays the only
difference, so a RED failure names the rule rather than a fixture typo.
"""

from __future__ import annotations

import copy
import importlib
import json
from collections.abc import Iterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from app.contracts import CANONICAL_COLUMN_GROUPS, CANONICAL_COLUMNS_V1

ROOT = Path(__file__).resolve().parents[1]
GRAPH_PATH = ROOT / "reference" / "source_graph.json"
SCHEMA_PATH = ROOT / "schemas" / "source_graph_v1.json"

COLUMNS: tuple[str, ...] = tuple(CANONICAL_COLUMNS_V1)

#: Value of ``graph["version"]`` and of ``schemas/source_graph_v1.json``.
GRAPH_VERSION = "source-graph-v1"
SCHEMA_ID = "https://ztockz.local/schemas/source-graph-v1.json"

#: The conflict policy every resolution states (PLAN §1: never average).
POLICY = "do not average; primary wins; crosscheck is a verdict input"

# ---------------------------------------------------------------------------
# PLAN §3 — field groups over the 92 canonical columns
# ---------------------------------------------------------------------------

#: ``app.contracts`` groups → the coarser PLAN §3 group. The counts in
#: :data:`PLAN_GROUP_COUNTS` are the plan table's own arithmetic (18+15+12+9+
#: 22+10+6 = 92), so a mapping mistake fails here instead of silently shipping.
PLAN_GROUP_OF_GROUP: dict[str, str] = {
    "Identity": "reference",
    "Classification": "reference",
    "Quote": "quote",
    "Market/session": "quote",
    "EOD & technical": "eod_bars",
    "Performance": "performance",
    "Fundamentals": "fundamentals",
    "Statements": "fundamentals",
    "Options": "options",
    "Quality": "verify",
}

PLAN_GROUP_COUNTS: dict[str, int] = {
    "reference": 18,
    "quote": 15,
    "eod_bars": 12,
    "performance": 9,
    "fundamentals": 22,
    "options": 10,
    "verify": 6,
}

PLAN_GROUP_OF: dict[str, str] = {
    column: PLAN_GROUP_OF_GROUP[group]
    for group, columns in CANONICAL_COLUMN_GROUPS
    for column in columns
}

#: Fields of the TradingView screen snapshot that are not export columns.
SCREEN_FIELDS: tuple[str, ...] = ("beta", "high_52w", "low_52w")

#: Which node capability a primary must be allowed to use (PLAN §3).
#: ``performance`` is computed from EOD bars, ``verify`` has no provider access.
CAPABILITY_OF_PLAN_GROUP: dict[str, str | None] = {
    "reference": "reference",
    "quote": "quote",
    "eod_bars": "eod_bars",
    "performance": "eod_bars",
    "fundamentals": "fundamentals",
    "options": "options",
    "verify": None,
}

# ---------------------------------------------------------------------------
# Closed vocabularies (PLAN §2)
# ---------------------------------------------------------------------------

NODE_KINDS: frozenset[str] = frozenset(
    {
        "regulator",
        "exchange",
        "issuer",
        "vendor",
        "platform",
        "aggregator",
        "broker",
        "news",
        "derived",
    }
)
AUTHORITIES: frozenset[str] = frozenset(
    {"primary", "official", "licensed", "aggregator", "derived", "opinion"}
)
ROBOTS: frozenset[str] = frozenset({"allowed", "disallowed", "mixed", "unknown"})
STATUSES: frozenset[str] = frozenset({"active", "partial", "blocked", "unwired"})

#: Operational freshness of a source, not a free sentence (PLAN §3 table).
DELAYS: frozenset[str] = frozenset(
    {
        "realtime",
        "realtime_ish",
        "delayed_15m",
        "delayed",
        "eod",
        "near_realtime",
        "event",
        "job_time",
    }
)

#: ``Capability`` (8 members) plus the two graph-only kinds: ``news`` and
#: ``screener`` — a news wire is not a quote provider, and Finviz has a screener
#: that robots.txt forbids us from using (PLAN §3 Access).
NODE_CAPABILITIES: frozenset[str] = frozenset(
    {
        "reference",
        "quote",
        "eod_bars",
        "fundamentals",
        "options",
        "constituents",
        "corporate_actions",
        "fx",
        "news",
        "screener",
    }
)

RELATIONS: frozenset[str] = frozenset(
    {"provides", "fallback", "crosscheck", "derived_from", "same_as", "conflicts_with"}
)

FIELD_TYPES: frozenset[str] = frozenset(
    {"number", "string", "date", "timestamp", "boolean", "object"}
)

#: ``when`` is a predicate, never a sentence: these are the only keys the
#: resolver knows how to evaluate.
WHEN_KEYS: frozenset[str] = frozenset({"mic", "country", "asset_class", "region"})

US_MICS: frozenset[str] = frozenset({"XNAS", "XNYS", "XASE"})
#: A Tokyo listing: the "global" side of the Finviz-US-only gate.
GLOBAL_MIC = "XTKS"

TICKER = "last_price"

#: Nodes PLAN §3 selects (21 sources; §5's "seed 20" is a typo against its own
#: table). Every id must exist so a consumer never gets a dangling reference.
PLAN_SOURCE_IDS: tuple[str, ...] = (
    "sec_edgar",
    "yahoo",
    "tradingview",
    "finviz",
    "google_finance",
    "nasdaq",
    "barchart",
    "finnhub",
    "polygon",
    "stooq",
    "coingecko",
    "ecb",
    "ishares",
    "openfigi",
    "ibkr",
    "gdelt",
    "google_news_rss",
    "company_ir",
    "engine:technicals",
    "engine:performance",
    "engine:quality",
)

#: Substrings that must never appear in a graph, a schema or an API response:
#: a graph names *sources*, never credentials (PLAN §6).
SECRET_TOKENS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "api-key",
    "secret",
    "password",
    "passwd",
    "bearer",
    "authorization",
    "access_token",
    "refresh_token",
    "private_key",
    "client_secret",
    "x-api-key",
)


# ---------------------------------------------------------------------------
# Module loading with RED-shaped failures
# ---------------------------------------------------------------------------


def _load(module_name: str) -> Any:
    """Import a Source Graph module, turning a missing module into a RED failure."""

    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:  # pragma: no cover - RED path
        raise NotImplementedError(
            f"{module_name} is not implemented yet (Source Graph SG0 RED): {exc}"
        ) from exc


def _require(module: Any, *names: str) -> Any:
    missing = [name for name in names if not hasattr(module, name)]
    if missing:  # pragma: no cover - RED path
        raise NotImplementedError(
            f"{module.__name__} is missing " + ", ".join(missing) + " (Source Graph SG0 RED)"
        )
    if len(names) == 1:
        return getattr(module, names[0])
    return tuple(getattr(module, name) for name in names)


def graph_module() -> Any:
    """``app.sources.graph`` — load, validate, resolve."""

    module = _load("app.sources.graph")
    _require(
        module,
        "load_graph",
        "validate_graph",
        "resolve_field",
        "resolve_capability",
        "GraphInvalid",
    )
    return module


def schema_module() -> Any:
    """``app.sources.schema`` — the published JSON Schema object."""

    module = _load("app.sources.schema")
    _require(module, "SOURCE_GRAPH_SCHEMA")
    return module


def api_module() -> Any:
    """``app.sources.api`` — the read-only router."""

    module = _load("app.sources.api")
    _require(module, "router")
    return module


# ---------------------------------------------------------------------------
# Reading the production graph without importing the implementation
# ---------------------------------------------------------------------------


def graph_doc() -> dict[str, Any]:
    """The raw reference file, parsed — the oracle for shape and API equality."""

    if not GRAPH_PATH.exists():
        raise AssertionError(
            "reference/source_graph.json is missing: SG0 requires one machine-readable "
            "graph file (nodes, edges, 92 fields, screen_fields)"
        )
    document = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise AssertionError("reference/source_graph.json must contain a JSON object")
    return document


def schema_doc() -> dict[str, Any]:
    if not SCHEMA_PATH.exists():
        raise AssertionError(
            "schemas/source_graph_v1.json is missing: the graph schema must exist as a "
            "repo artifact, not only as a Python literal"
        )
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _mapping_entries(section: Any, *, label: str) -> list[dict[str, Any]]:
    """Accept a list of entries or a ``column -> entry`` mapping; reject the rest."""

    entries: list[dict[str, Any]] = []
    if isinstance(section, Mapping):
        for column, entry in section.items():
            if not isinstance(entry, Mapping):
                raise AssertionError(f"{label}[{column!r}] must be an object, got {type(entry).__name__}")
            merged = {"column": column, **entry}
            entries.append(dict(merged))
    elif isinstance(section, list):
        for entry in section:
            if not isinstance(entry, Mapping):
                raise AssertionError(f"every {label} entry must be an object, got {type(entry).__name__}")
            if "column" not in entry:
                raise AssertionError(f"{label} entry without a 'column' key: {entry!r}")
            entries.append(dict(entry))
    else:
        raise AssertionError(f"{label} must be a list or a mapping, got {type(section).__name__}")
    return entries


def fields(document: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """The 92 field resolutions (tolerates list or ``column -> entry`` shape)."""

    document = graph_doc() if document is None else document
    return _mapping_entries(document.get("fields"), label="fields")


def field_entry(column: str, document: Mapping[str, Any] | None = None) -> dict[str, Any]:
    for entry in fields(document):
        if entry["column"] == column:
            return entry
    raise AssertionError(f"reference/source_graph.json has no field entry for {column!r}")


def screen_fields(document: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    document = graph_doc() if document is None else document
    return _mapping_entries(document.get("screen_fields"), label="screen_fields")


def screen_field_entry(
    column: str, document: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    for entry in screen_fields(document):
        if entry["column"] == column:
            return entry
    raise AssertionError(f"reference/source_graph.json has no screen field {column!r}")


def nodes(document: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Source nodes; a ``id -> node`` mapping is tolerated like fields."""

    document = graph_doc() if document is None else document
    section = document.get("nodes")
    if isinstance(section, Mapping):
        return [{"id": node_id, **node} for node_id, node in section.items()]
    if isinstance(section, list):
        return [dict(node) for node in section]
    raise AssertionError(f"nodes must be a list or a mapping, got {type(section).__name__}")


def nodes_by_id(document: Mapping[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for node in nodes(document):
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            raise AssertionError(f"every node needs a non-empty string id, got {node!r}")
        if node_id in index:
            raise AssertionError(f"duplicate node id {node_id!r}")
        index[node_id] = node
    return index


def node_entry(node_id: str, document: Mapping[str, Any] | None = None) -> dict[str, Any]:
    try:
        return nodes_by_id(document)[node_id]
    except KeyError as exc:
        raise AssertionError(f"reference/source_graph.json has no node {node_id!r}") from exc


def edges(document: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    document = graph_doc() if document is None else document
    section = document.get("edges")
    if not isinstance(section, list):
        raise AssertionError(f"edges must be a list, got {type(section).__name__}")
    return [dict(edge) for edge in section]


def known_field_names(document: Mapping[str, Any] | None = None) -> set[str]:
    document = graph_doc() if document is None else document
    return {entry["column"] for entry in fields(document)} | {
        entry["column"] for entry in screen_fields(document)
    }


# ---------------------------------------------------------------------------
# Candidate entries: {"id", "when"?, "via"?, "beta_window"?}
# ---------------------------------------------------------------------------


def source_id(entry: Any) -> str:
    """Id of a fallback/crosscheck/capability entry (a bare string is tolerated)."""

    if isinstance(entry, str):
        return entry
    if isinstance(entry, Mapping):
        value = entry.get("id")
        if isinstance(value, str) and value:
            return value
    raise AssertionError(f"a source entry needs a string id, got {entry!r}")


def source_when(entry: Any) -> dict[str, Any]:
    if isinstance(entry, Mapping):
        when = entry.get("when") or {}
        if not isinstance(when, Mapping):
            raise AssertionError(f"'when' must be an object, got {entry!r}")
        return dict(when)
    return {}


def source_meta(entry: Any, key: str) -> Any:
    """Entry-level metadata (``via``, ``beta_window``), also read from ``when``."""

    if isinstance(entry, Mapping):
        if key in entry:
            return entry[key]
        return (entry.get("when") or {}).get(key)
    return None


def entry_applies(entry: Any, **context: Any) -> bool:
    """True when every predicate of ``entry`` is satisfied by ``context``.

    Fail-closed: a predicate whose context value is absent does not match, so a
    caller that did not say "US" never gets a US-only source.
    """

    for key, expected in source_when(entry).items():
        actual = context.get(key)
        if actual is None:
            return False
        allowed = expected if isinstance(expected, list | tuple | set | frozenset) else [expected]
        if actual not in allowed:
            return False
    return True


def expected_sources(entries: list[Any], **context: Any) -> list[str]:
    """Ids that apply, in declared order — the oracle for ``resolve_field``."""

    return [source_id(entry) for entry in entries if entry_applies(entry, **context)]


def all_candidate_ids(entry: Mapping[str, Any]) -> list[str]:
    return [source_id(item) for item in (entry.get("fallbacks") or [])] + [
        source_id(item) for item in (entry.get("crosschecks") or [])
    ]


# ---------------------------------------------------------------------------
# Invalid graphs — minimal mutations of the production document
# ---------------------------------------------------------------------------

#: ``kind -> substrings`` one of which must appear in the ``GraphInvalid`` message,
#: so a validator that raises for an unrelated reason cannot pass the test.
BAD_GRAPH_FRAGMENTS: dict[str, tuple[str, ...]] = {
    "derived_cycle": ("cycle",),
    "missing_primary": (TICKER, "primary"),
    "robots_disallowed_primary": (TICKER, "robots", "finviz_screener_bot"),
    "duplicate_fallback": ("duplicate", TICKER),
    "beta_tradingview_primary": ("beta",),
    "high52w_finviz_screener": ("high_52w", "52w"),
}


def _raw_entry(section: Any, column: str, *, label: str) -> dict[str, Any]:
    """The **live** entry for ``column`` — mutations must land in the document."""

    if isinstance(section, Mapping):
        entry = section.get(column)
        if not isinstance(entry, Mapping):
            raise AssertionError(f"{label} has no entry for {column!r}")
        return entry  # type: ignore[return-value]
    if isinstance(section, list):
        for entry in section:
            if isinstance(entry, Mapping) and entry.get("column") == column:
                return entry  # type: ignore[return-value]
        raise AssertionError(f"{label} has no entry for {column!r}")
    raise AssertionError(f"{label} must be a list or a mapping, got {type(section).__name__}")


def _raw_field(document: dict[str, Any], column: str) -> dict[str, Any]:
    return _raw_entry(document["fields"], column, label="fields")


def _raw_screen_field(document: dict[str, Any], column: str) -> dict[str, Any]:
    return _raw_entry(document["screen_fields"], column, label="screen_fields")


def _raw_node(document: dict[str, Any], node_id: str) -> dict[str, Any]:
    section = document["nodes"]
    if isinstance(section, Mapping):
        node = section.get(node_id)
        if not isinstance(node, Mapping):
            raise AssertionError(f"nodes has no entry for {node_id!r}")
        return node  # type: ignore[return-value]
    if isinstance(section, list):
        for node in section:
            if isinstance(node, Mapping) and node.get("id") == node_id:
                return node  # type: ignore[return-value]
        raise AssertionError(f"nodes has no entry for {node_id!r}")
    raise AssertionError(f"nodes must be a list or a mapping, got {type(section).__name__}")


def _add_node(document: dict[str, Any], node: dict[str, Any]) -> None:
    section = document["nodes"]
    if isinstance(section, list):
        section.append(node)
        return
    if isinstance(section, dict):
        section[node["id"]] = {key: value for key, value in node.items() if key != "id"}
        return
    raise AssertionError(f"nodes must be a list or a mapping, got {type(section).__name__}")


def bad_graph(kind: str) -> dict[str, Any]:
    """A copy of the production graph with exactly one contract violation."""

    document = copy.deepcopy(graph_doc())

    if kind == "derived_cycle":
        _raw_node(document, "engine:technicals").setdefault("derived_from", []).append(
            "engine:performance"
        )
        _raw_node(document, "engine:performance").setdefault("derived_from", []).append(
            "engine:technicals"
        )
        if not isinstance(document["edges"], list):
            raise AssertionError("edges must be a list")
        document["edges"].extend(
            [
                {
                    "from": "engine:technicals",
                    "to": "engine:performance",
                    "relation": "derived_from",
                },
                {
                    "from": "engine:performance",
                    "to": "engine:technicals",
                    "relation": "derived_from",
                },
            ]
        )
    elif kind == "missing_primary":
        _raw_field(document, TICKER)["primary"] = None
    elif kind == "robots_disallowed_primary":
        _add_node(
            document,
            {
                "id": "finviz_screener_bot",
                "label": "Finviz screener (robots disallowed)",
                "kind": "platform",
                "authority": "aggregator",
                "trust": 4,
                "delay": "delayed",
                "license": "free_limited",
                "robots": "disallowed",
                "status": "active",
                "access": {"quote": "robots_disallowed", "screener": "robots_disallowed"},
                "capabilities": ["quote", "screener"],
            },
        )
        _raw_field(document, TICKER)["primary"] = "finviz_screener_bot"
    elif kind == "duplicate_fallback":
        entry = _raw_field(document, TICKER)
        fallbacks = list(entry.get("fallbacks") or [])
        if not fallbacks:
            raise AssertionError("the production graph must give last_price a fallback to duplicate")
        entry["fallbacks"] = [fallbacks[0], fallbacks[0]]
    elif kind == "beta_tradingview_primary":
        beta = _raw_screen_field(document, "beta")
        # TradingView only knows beta_1_year; claiming the 5y canonical window
        # with a 1y source is the defect PLAN §3 note 1 forbids.
        beta["primary"] = "tradingview"
        beta["beta_window"] = "5y"
    elif kind == "high52w_finviz_screener":
        high = _raw_screen_field(document, "high_52w")
        high["primary"] = "finviz"
        high["methodology"] = "Finviz screener high52w column (percent distance, not a price)"
    else:  # pragma: no cover - programming error in the suite
        raise AssertionError(f"unknown bad_graph kind {kind!r}")

    return document


# ---------------------------------------------------------------------------
# ASGI harness
# ---------------------------------------------------------------------------


@asynccontextmanager
async def sources_client(tmp_path: Path, monkeypatch: Any) -> AsyncIterator[Any]:
    """An ASGI client wired to a temp database, with the sources router mounted."""

    api_module()  # RED: the router module must exist before the app is built

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))

    from httpx2 import ASGITransport, AsyncClient

    from app.main import create_app

    application = create_app()
    paths = {getattr(route, "path", "") for route in application.routes}
    if not any(path.startswith("/sources") for path in paths):
        raise NotImplementedError(
            "app.main.create_app() does not include the sources router (Source Graph SG0 RED): "
            "GET /sources/graph must be reachable from the ASGI app"
        )

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://mee.test") as client:
        yield client


def iter_strings(value: Any) -> Iterator[str]:
    """Every string in a JSON document (keys included), for secret scanning."""

    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_strings(item)
