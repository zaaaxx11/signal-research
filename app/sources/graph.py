"""The Source Graph: one document, one validator, two deterministic resolvers.

``reference/source_graph.json`` is the graph. This module never keeps a second
copy of it: :func:`load_graph` parses the file and every read path (the API, the
CLI, the resolvers) goes through that function, so a doctored loader changes the
answer and the file and the service cannot drift.

What the graph answers, per canonical column: which source may fill it
(``primary``), in which order the substitutes may be tried (``fallbacks``),
which independent source may contradict it (``crosschecks``) and how the value
is computed (``methodology``). It stores no market value.

Resolution is *conditional and fail-closed*. A candidate carries a predicate
``when = {mic|country|asset_class|region: str | [str]}``; the predicate is
applied only when the caller supplied the fact it names. ``resolve_field(
"last_price")`` therefore never receives Finviz: "US-only" is unproven, and an
unproven predicate is not a licence. A source that is ``blocked``/``unwired``,
or whose ``access`` for the needed capability is ``robots_disallowed``/``none``,
is never offered by :func:`resolve_capability`.

Both resolvers are pure functions of (document, context): same input, same
output, in the declared order (no sorting of fallbacks — the order is the
contract; capability order is ``(trust, id)``).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn

from app.contracts import CANONICAL_COLUMN_GROUPS, CANONICAL_COLUMNS_V1

# ---------------------------------------------------------------------------
# Contract constants
# ---------------------------------------------------------------------------

#: Value of ``graph["version"]``; the schema publishes the same string.
GRAPH_VERSION = "source-graph-v1"

#: The conflict policy every resolution states. Never average two feeds: the
#: primary is the value, a cross-check is only an input to the verdict.
POLICY = "do not average; primary wins; crosscheck is a verdict input"

#: PLAN §3 groups, in table order. This is the graph's group vocabulary, which
#: is coarser than ``app.contracts``' ten export groups.
PLAN_GROUPS: tuple[str, ...] = (
    "reference",
    "quote",
    "eod_bars",
    "performance",
    "fundamentals",
    "options",
    "verify",
)

#: ``app.contracts`` group → PLAN §3 group. Counts: 18+15+12+9+22+10+6 = 92.
GROUP_OF_CANONICAL_GROUP: dict[str, str] = {
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

#: The node capability a field group needs from its primary. ``performance`` is
#: computed from EOD bars; ``verify`` reads the verification ledger, not a
#: provider, so it has no access requirement.
CAPABILITY_OF_PLAN_GROUP: dict[str, str | None] = {
    "reference": "reference",
    "quote": "quote",
    "eod_bars": "eod_bars",
    "performance": "eod_bars",
    "fundamentals": "fundamentals",
    "options": "options",
    "verify": None,
}

#: Screen-snapshot fields that are not among the 92 export columns.
SCREEN_FIELD_NAMES: tuple[str, ...] = ("beta", "high_52w", "low_52w")

NODE_KINDS: tuple[str, ...] = (
    "regulator",
    "exchange",
    "issuer",
    "vendor",
    "platform",
    "aggregator",
    "broker",
    "news",
    "derived",
)

AUTHORITIES: tuple[str, ...] = (
    "primary",
    "official",
    "licensed",
    "aggregator",
    "derived",
    "opinion",
)

ROBOTS: tuple[str, ...] = ("allowed", "disallowed", "mixed", "unknown")
STATUSES: tuple[str, ...] = ("active", "partial", "blocked", "unwired")

#: Operational freshness, not a sentence: comparable and renderable.
DELAYS: tuple[str, ...] = (
    "realtime",
    "realtime_ish",
    "delayed_15m",
    "delayed",
    "eod",
    "near_realtime",
    "event",
    "job_time",
)

#: ``Capability`` (8 members) plus the two graph-only kinds: a news wire is not
#: a quote provider, and the Finviz screener is a capability we may not use.
NODE_CAPABILITIES: tuple[str, ...] = (
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
)

RELATIONS: tuple[str, ...] = (
    "provides",
    "fallback",
    "crosscheck",
    "derived_from",
    "same_as",
    "conflicts_with",
)

FIELD_TYPES: tuple[str, ...] = (
    "number",
    "string",
    "date",
    "timestamp",
    "boolean",
    "object",
)

#: Every predicate the resolver knows how to evaluate. A ``when`` with another
#: key would be silently ignored, so the validator refuses it.
WHEN_KEYS: tuple[str, ...] = ("mic", "country", "asset_class", "region")

#: Bridge statuses: a source in one of these may fill or answer.
LIVE_STATUSES: frozenset[str] = frozenset({"active", "partial"})

#: ``access`` verdicts that forbid production use of a route.
BLOCKED_ACCESS: frozenset[str] = frozenset({"robots_disallowed", "none"})

#: The canonical (contract) group of every one of the 92 columns.
CANONICAL_GROUP_OF: dict[str, str] = {
    column: group for group, columns in CANONICAL_COLUMN_GROUPS for column in columns
}
PLAN_GROUP_OF: dict[str, str] = {
    column: GROUP_OF_CANONICAL_GROUP[group] for column, group in CANONICAL_GROUP_OF.items()
}

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = _REPO_ROOT / "reference" / "source_graph.json"


class GraphInvalid(ValueError):
    """The graph violates a contract rule; the message names the defect."""


# ---------------------------------------------------------------------------
# Loading and shape helpers
# ---------------------------------------------------------------------------


def default_path() -> Path:
    """``reference/source_graph.json`` of this checkout."""

    return DEFAULT_PATH


def load_graph(path: str | Path | None = None) -> dict[str, Any]:
    """Parse the graph document. The file is the only copy."""

    source = Path(path) if path is not None else DEFAULT_PATH
    document = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise GraphInvalid(f"{source} must contain a JSON object")
    return document


def _entries(section: Any, label: str) -> list[dict[str, Any]]:
    """Field/candidate sections as a list of objects (list or mapping shape)."""

    entries: list[dict[str, Any]] = []
    if isinstance(section, Mapping):
        for column, entry in section.items():
            if not isinstance(entry, Mapping):
                raise GraphInvalid(f"{label}[{column!r}] must be an object")
            entries.append({"column": column, **entry})
        return entries
    if isinstance(section, list):
        for entry in section:
            if not isinstance(entry, Mapping):
                raise GraphInvalid(f"every {label} entry must be an object")
            entries.append(dict(entry))
        return entries
    raise GraphInvalid(f"{label} must be a list or a mapping of column -> entry")


def nodes_of(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Source nodes as a list, tolerating the ``id -> node`` mapping shape."""

    section = document.get("nodes")
    if isinstance(section, Mapping):
        return [{"id": node_id, **node} for node_id, node in section.items()]
    if isinstance(section, list):
        return [dict(node) for node in section]
    raise GraphInvalid("nodes must be a list or a mapping of id -> node")


def _node_index(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for node in nodes_of(document):
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id.strip():
            raise GraphInvalid(f"every node needs a non-empty string id, got {node!r}")
        if node_id in index:
            raise GraphInvalid(f"duplicate node id {node_id!r}")
        index[node_id] = node
    return index


def _candidates(entry: Mapping[str, Any], key: str) -> list[Any]:
    value = entry.get(key)
    return list(value) if isinstance(value, list) else []


def candidate_id(entry: Any) -> str:
    """Id of a candidate entry ``{id, when?, via?, beta_window?}``."""

    if isinstance(entry, str):
        return entry
    if isinstance(entry, Mapping):
        value = entry.get("id")
        if isinstance(value, str) and value:
            return value
    raise GraphInvalid(f"a source entry needs a string id, got {entry!r}")


def candidate_when(entry: Any) -> dict[str, Any]:
    when = entry.get("when") if isinstance(entry, Mapping) else None
    if when is None:
        return {}
    if not isinstance(when, Mapping):
        raise GraphInvalid(f"'when' must be an object, got {entry!r}")
    return dict(when)


def candidate_meta(entry: Any, key: str) -> Any:
    """Entry-level metadata (``via``, ``beta_window``), also read from ``when``."""

    if isinstance(entry, Mapping):
        if key in entry:
            return entry[key]
        return (entry.get("when") or {}).get(key)
    return None


def _applies(entry: Any, context: Mapping[str, str]) -> bool:
    """True when **every** predicate of the entry is proven by ``context``.

    Fail-closed: an absent fact does not satisfy a predicate, so a caller that
    did not say "US" never receives a US-only source.
    """

    for key, expected in candidate_when(entry).items():
        actual = context.get(key)
        if actual is None:
            return False
        allowed = expected if isinstance(expected, list | tuple | set | frozenset) else [expected]
        if actual not in allowed:
            return False
    return True


def _source_view(source_id: str, index: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    node = index.get(source_id)
    if node is None:
        raise GraphInvalid(f"candidate {source_id!r} is not a node of the graph")
    return {
        "id": source_id,
        "trust": node["trust"],
        "delay": node["delay"],
        "status": node["status"],
    }


def _candidate_view(entry: Any, index: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    source_id = candidate_id(entry)
    view = _source_view(source_id, index)
    for key in ("when", "via", "beta_window"):
        value = candidate_meta(entry, key)
        if value is not None:
            view[key] = value
    return view


def _field_of(document: Mapping[str, Any], column: str) -> dict[str, Any] | None:
    for section in ("fields", "screen_fields"):
        if document.get(section) is None:
            continue
        for entry in _entries(document.get(section), section):
            if entry.get("column") == column:
                return entry
    return None


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------


def resolve_field(
    column: str,
    *,
    mic: str | None = None,
    asset_class: str | None = None,
    country: str | None = None,
    region: str | None = None,
    graph: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one column for an optional market context.

    Unknown column → ``KeyError`` (a typo is not a field). The returned
    ``fallbacks``/``crosschecks`` keep the declared order and contain only the
    entries whose ``when`` is fully satisfied by the supplied context.
    """

    document = load_graph() if graph is None else graph
    entry = _field_of(document, column)
    if entry is None:
        raise KeyError(f"{column!r} is not a column of the source graph {GRAPH_VERSION}")

    index = _node_index(document)
    context = {
        key: value
        for key, value in {
            "mic": mic,
            "asset_class": asset_class,
            "country": country,
            "region": region,
        }.items()
        if value is not None
    }

    resolution: dict[str, Any] = {
        "column": column,
        "group": entry.get("group"),
        "type": entry.get("type"),
        "primary": _source_view(candidate_id(entry.get("primary")), index),
        "fallbacks": [
            _candidate_view(item, index)
            for item in _candidates(entry, "fallbacks")
            if _applies(item, context)
        ],
        "crosschecks": [
            _candidate_view(item, index)
            for item in _candidates(entry, "crosschecks")
            if _applies(item, context)
        ],
        "policy": POLICY,
        "methodology": entry.get("methodology"),
        "context": context,
    }
    if "beta_window" in entry:
        resolution["beta_window"] = entry["beta_window"]
    return resolution


def resolve_capability(
    capability: str, *, graph: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Sources that may answer ``capability``, ordered ``(trust asc, id asc)``.

    ``blocked``/``unwired`` sources and routes the graph marks
    ``robots_disallowed``/``none`` are excluded: a capability list that contains
    a source we may not call is a licence we do not have.
    """

    if capability not in NODE_CAPABILITIES:
        raise KeyError(f"{capability!r} is not a source capability")

    document = load_graph() if graph is None else graph
    index = _node_index(document)
    sources: list[dict[str, Any]] = []
    for node_id, node in index.items():
        if capability not in (node.get("capabilities") or []):
            continue
        if node.get("status") not in LIVE_STATUSES:
            continue
        if (node.get("access") or {}).get(capability, "allowed") in BLOCKED_ACCESS:
            continue
        sources.append(_source_view(node_id, index))
    sources.sort(key=lambda source: (source["trust"], source["id"]))
    return sources


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _fail(message: str) -> NoReturn:
    raise GraphInvalid(message)


def _check_nodes(index: Mapping[str, dict[str, Any]]) -> None:
    required = {
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
    }
    for node_id, node in index.items():
        missing = sorted(required - set(node))
        if missing:
            _fail(f"node {node_id!r} is missing {missing}")
        if node["kind"] not in NODE_KINDS:
            _fail(f"node {node_id!r} has kind {node['kind']!r}, not one of {list(NODE_KINDS)}")
        if node["authority"] not in AUTHORITIES:
            _fail(f"node {node_id!r} has authority {node['authority']!r}")
        if node["robots"] not in ROBOTS:
            _fail(f"node {node_id!r} has robots {node['robots']!r}")
        if node["status"] not in STATUSES:
            _fail(f"node {node_id!r} has status {node['status']!r}")
        if node["delay"] not in DELAYS:
            _fail(
                f"node {node_id!r} delay {node['delay']!r} is not an operational freshness "
                f"value; expected one of {list(DELAYS)}"
            )
        if not isinstance(node["trust"], int) or not 1 <= node["trust"] <= 4:
            _fail(f"node {node_id!r} trust must be an int 1..4, got {node['trust']!r}")
        if not isinstance(node["license"], str) or not node["license"].strip():
            _fail(f"node {node_id!r} needs a non-empty license")
        access = node["access"]
        if not isinstance(access, Mapping) or not access:
            _fail(f"node {node_id!r} must declare access")
        capabilities = node["capabilities"]
        if not isinstance(capabilities, list) or not capabilities:
            _fail(f"node {node_id!r} must declare capabilities")
        unknown = sorted(set(capabilities) - set(NODE_CAPABILITIES))
        if unknown:
            _fail(f"node {node_id!r} declares unknown capabilities {unknown}")

        if node["kind"] == "derived":
            if node["authority"] != "derived":
                _fail(f"derived node {node_id!r} must have authority 'derived'")
            inputs = node.get("derived_from")
            if not isinstance(inputs, list) or not inputs:
                _fail(f"derived node {node_id!r} must name its inputs in derived_from")
            if node_id in inputs:
                _fail(f"derived node {node_id!r} derives from itself")
            for source in inputs:
                if source not in index:
                    _fail(f"derived node {node_id!r} derives from unknown node {source!r}")
            if not isinstance(node.get("methodology"), str) or not node["methodology"].strip():
                _fail(f"derived node {node_id!r} must name a methodology version")


def _check_derived_cycles(
    document: Mapping[str, Any], index: Mapping[str, dict[str, Any]]
) -> None:
    """Reject a ``derived_from`` cycle, whether the nodes or the edges declare it."""

    adjacency: dict[str, list[str]] = {node_id: [] for node_id in index}
    for node_id, node in index.items():
        for source in node.get("derived_from") or []:
            if isinstance(source, str) and source in index:
                adjacency[node_id].append(source)
    section = document.get("edges")
    if isinstance(section, list):
        for edge in section:
            if not isinstance(edge, Mapping) or edge.get("relation") != "derived_from":
                continue
            source, target = edge.get("from"), edge.get("to")
            if source in index and target in index and target not in adjacency[source]:
                adjacency[source].append(target)

    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node_id: str) -> None:
        state[node_id] = 1
        stack.append(node_id)
        for neighbour in adjacency.get(node_id, []):
            if state.get(neighbour) == 1:
                cycle = stack[stack.index(neighbour) :] + [neighbour]
                _fail("derived_from cycle: " + " -> ".join(cycle))
            if state.get(neighbour, 0) == 0:
                visit(neighbour)
        stack.pop()
        state[node_id] = 2

    for node_id in sorted(adjacency):
        if state.get(node_id, 0) == 0:
            visit(node_id)


def _primary_node(
    index: Mapping[str, dict[str, Any]], entry: Mapping[str, Any]
) -> dict[str, Any]:
    column = entry.get("column")
    primary = entry.get("primary")
    if not isinstance(primary, str) or not primary.strip():
        _fail(f"field {column!r} has no primary: every column needs one live source")
    node = index.get(primary)
    if node is None:
        _fail(f"field {column!r} names primary {primary!r}, which is not a node")
    if node["status"] not in LIVE_STATUSES:
        _fail(
            f"field {column!r} primary {primary!r} has status {node['status']!r}; "
            "blocked/unwired sources cannot fill a column"
        )
    if node["robots"] == "disallowed":
        _fail(
            f"field {column!r} primary {primary!r} is robots-disallowed: robots.txt "
            "forbids the access this column needs"
        )
    capability = CAPABILITY_OF_PLAN_GROUP.get(str(entry.get("group")))
    if capability is not None:
        access = (node.get("access") or {}).get(capability, "allowed")
        if access in BLOCKED_ACCESS:
            _fail(
                f"field {column!r} primary {primary!r} has access {access!r} for "
                f"capability {capability!r}"
            )
    return node


def _check_candidate_list(
    entry: Mapping[str, Any],
    key: str,
    index: Mapping[str, dict[str, Any]],
    primary: str,
) -> list[str]:
    column = entry.get("column")
    items = _candidates(entry, key)
    if not isinstance(entry.get(key), list):
        _fail(f"field {column!r} must declare {key} as a list")
    ids: list[str] = []
    for item in items:
        source_id = candidate_id(item)
        if source_id not in index:
            _fail(f"field {column!r} {key} names {source_id!r}, which is not a node")
        when = candidate_when(item)
        unknown = sorted(set(when) - set(WHEN_KEYS))
        if unknown:
            _fail(
                f"field {column!r} {key}[{source_id!r}] has predicates {unknown}; "
                f"the resolver only evaluates {list(WHEN_KEYS)}"
            )
        for predicate, expected in when.items():
            if not (
                isinstance(expected, str)
                or (
                    isinstance(expected, list)
                    and expected
                    and all(isinstance(value, str) for value in expected)
                )
            ):
                _fail(
                    f"field {column!r} {key}[{source_id!r}] predicate {predicate!r} "
                    "must be a string or a non-empty list of strings"
                )
        ids.append(source_id)
    if len(ids) != len(set(ids)):
        _fail(f"field {column!r} has duplicate {key[:-1]} entries: {ids}")
    if primary in ids:
        _fail(f"field {column!r} lists its primary {primary!r} as a {key[:-1]}")
    return ids


def _check_fields(
    document: Mapping[str, Any], index: Mapping[str, dict[str, Any]]
) -> dict[str, list[str]]:
    entries = _entries(document.get("fields"), "fields")
    columns = [entry.get("column") for entry in entries]
    expected = list(CANONICAL_COLUMNS_V1)
    if columns != expected:
        divergence = next(
            (
                position
                for position, (got, want) in enumerate(zip(columns, expected, strict=False))
                if got != want
            ),
            min(len(columns), len(expected)),
        )
        _fail(
            "fields must be exactly the 92 canonical columns in export order; "
            f"{len(columns)} entries, first divergence at index {divergence}: "
            f"{columns[divergence : divergence + 3]!r} != {expected[divergence : divergence + 3]!r}"
        )

    candidates: dict[str, list[str]] = {}
    for entry in entries:
        column = entry["column"]
        missing = sorted(
            {"column", "group", "type", "primary", "fallbacks", "crosschecks", "methodology"}
            - set(entry)
        )
        if missing:
            _fail(f"field {column!r} is missing {missing}")
        if entry["group"] != PLAN_GROUP_OF[column]:
            _fail(
                f"field {column!r} is in group {entry['group']!r}; PLAN §3 puts it in "
                f"{PLAN_GROUP_OF[column]!r}"
            )
        if entry["type"] not in FIELD_TYPES:
            _fail(f"field {column!r} has type {entry['type']!r}")
        if not isinstance(entry["methodology"], str) or not entry["methodology"].strip():
            _fail(f"field {column!r} needs a methodology sentence")
        _primary_node(index, entry)
        primary = candidate_id(entry["primary"])
        fallbacks = _check_candidate_list(entry, "fallbacks", index, primary)
        crosschecks = _check_candidate_list(entry, "crosschecks", index, primary)
        candidates[column] = [primary, *fallbacks, *crosschecks]
    return candidates


def _check_screen_fields(
    document: Mapping[str, Any], index: Mapping[str, dict[str, Any]]
) -> dict[str, list[str]]:
    entries = _entries(document.get("screen_fields"), "screen_fields")
    names = [entry.get("column") for entry in entries]
    if names != list(SCREEN_FIELD_NAMES):
        _fail(
            "screen_fields must be exactly "
            f"{list(SCREEN_FIELD_NAMES)}; got {names!r}"
        )

    candidates: dict[str, list[str]] = {}
    for entry in entries:
        column = entry["column"]
        missing = sorted(
            {"column", "group", "type", "primary", "fallbacks", "crosschecks", "methodology"}
            - set(entry)
        )
        if missing:
            _fail(f"screen field {column!r} is missing {missing}")
        if entry["group"] not in PLAN_GROUPS:
            _fail(f"screen field {column!r} has group {entry['group']!r}")
        if entry["type"] not in FIELD_TYPES:
            _fail(f"screen field {column!r} has type {entry['type']!r}")
        node = _primary_node(index, entry)
        primary = candidate_id(entry["primary"])

        # Column-specific rules run before the generic candidate checks: a graph
        # that puts the Finviz screener on high_52w must be rejected for *that*,
        # not for a side effect of the entry (e.g. primary also listed as a
        # fallback), otherwise the verdict names the wrong defect.
        if column == "beta":
            window = entry.get("beta_window")
            if window not in {"1y", "5y"}:
                _fail(
                    "beta must declare beta_window (1y or 5y): a beta without its "
                    "window cannot be compared"
                )
            if window != "5y":
                _fail(f"beta canonical window is 5y; the graph declares {window!r}")
            supported = node.get("beta_windows") or []
            if window not in supported:
                _fail(
                    f"beta primary {primary!r} cannot fill beta_window={window!r}: "
                    f"it computes {supported or 'no beta'}"
                )
            for item in [*_candidates(entry, "fallbacks"), *_candidates(entry, "crosschecks")]:
                if (
                    candidate_id(item) == "tradingview"
                    and candidate_meta(item, "beta_window") != "1y"
                ):
                    _fail(
                        "beta candidate 'tradingview' computes beta_1_year only and must "
                        "declare beta_window='1y' on its entry"
                    )

        if column in {"high_52w", "low_52w"}:
            if primary == "finviz":
                _fail(
                    f"{column} primary 'finviz' is the screener column: percent distance "
                    "from the extreme, not a price; use the quote page (via='quote')"
                )
            for item in [*_candidates(entry, "fallbacks"), *_candidates(entry, "crosschecks")]:
                if candidate_id(item) == "finviz" and candidate_meta(item, "via") != "quote":
                    _fail(
                        f"{column} may only use the Finviz quote page (via='quote'), "
                        "never the screener"
                    )

        fallbacks = _check_candidate_list(entry, "fallbacks", index, primary)
        crosschecks = _check_candidate_list(entry, "crosschecks", index, primary)
        candidates[column] = [primary, *fallbacks, *crosschecks]
    return candidates


def _check_edges(
    document: Mapping[str, Any],
    index: Mapping[str, dict[str, Any]],
    candidates: Mapping[str, list[str]],
) -> None:
    section = document.get("edges")
    if not isinstance(section, list):
        _fail("edges must be a list")
    for edge in section:
        if not isinstance(edge, Mapping):
            _fail(f"every edge must be an object, got {edge!r}")
        relation = edge.get("relation")
        if relation not in RELATIONS:
            _fail(f"edge {edge!r} has relation {relation!r}, not one of {list(RELATIONS)}")
        source, target = edge.get("from"), edge.get("to")
        if source not in index:
            _fail(f"edge from unknown node: {edge!r}")
        if target not in index:
            _fail(f"edge to unknown node: {edge!r}")
        if source == target:
            _fail(f"self edge: {edge!r}")
        field = edge.get("field")
        if field is not None and field not in candidates:
            _fail(f"edge names unknown field: {edge!r}")
        when = edge.get("when")
        if when is not None and not isinstance(when, str):
            if not isinstance(when, Mapping) or not when:
                _fail(f"edge 'when' must be a predicate object or a prose note: {edge!r}")
            unknown = sorted(set(when) - set(WHEN_KEYS))
            if unknown:
                _fail(f"edge {edge!r} has unknown predicates {unknown}")
        if relation in {"fallback", "crosscheck"}:
            if field is None:
                _fail(f"a {relation} edge is per-field and must name one: {edge!r}")
            if source not in candidates.get(str(field), []):
                _fail(
                    f"{relation} edge {source!r} for field {field!r} is not declared in "
                    "the field's candidate list"
                )
        if relation == "provides":
            if edge.get("capability") not in NODE_CAPABILITIES:
                _fail(f"provides edge must declare a known capability: {edge!r}")
            trust = edge.get("trust")
            if not isinstance(trust, int) or not 1 <= trust <= 4:
                _fail(f"provides edge must declare trust 1..4: {edge!r}")
        if relation == "derived_from" and index[str(source)]["kind"] != "derived":
            _fail(f"only a derived node can declare derived_from edges: {edge!r}")


def validate_graph(
    graph: Mapping[str, Any] | None = None, *, path: str | Path | None = None
) -> None:
    """Raise :class:`GraphInvalid` for the first contract violation found.

    ``graph=None`` validates the file at ``path`` (or the production graph).
    Returns ``None`` on success — there is no partial verdict.
    """

    document = load_graph(path) if graph is None else graph
    if not isinstance(document, Mapping):
        _fail("the graph document must be an object")

    if document.get("version") != GRAPH_VERSION:
        _fail(
            f"unsupported graph version {document.get('version')!r}; expected {GRAPH_VERSION!r}"
        )
    missing = sorted({"nodes", "edges", "fields", "screen_fields"} - set(document))
    if missing:
        _fail(f"the graph document is missing {missing}")

    index = _node_index(document)
    if not index:
        _fail("the graph has no nodes")
    _check_nodes(index)
    _check_derived_cycles(document, index)

    candidates = _check_fields(document, index)
    candidates.update(_check_screen_fields(document, index))
    _check_edges(document, index, candidates)

    policy = document.get("conflict_policy", POLICY)
    if policy != POLICY:
        _fail(
            f"conflict_policy {policy!r} contradicts the resolver policy {POLICY!r}: "
            "the file would describe a different conflict rule than the code applies"
        )


# ---------------------------------------------------------------------------
# CLI: ``python -m app.sources.graph --validate``
# ---------------------------------------------------------------------------


def _summary(document: Mapping[str, Any]) -> str:
    fields = _entries(document.get("fields"), "fields")
    screen = _entries(document.get("screen_fields"), "screen_fields")
    nodes = nodes_of(document)
    edges = document.get("edges") or []
    return (
        f"{document.get('version')}: {len(nodes)} nodes, {len(edges)} edges, "
        f"{len(fields)} fields, {len(screen)} screen fields"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.sources.graph",
        description="Validate or query the Source Graph document.",
    )
    parser.add_argument("--validate", action="store_true", help="validate the graph and exit")
    parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help="graph file (default: reference/source_graph.json)",
    )
    parser.add_argument(
        "--field", metavar="COLUMN", default=None, help="print one field resolution"
    )
    parser.add_argument(
        "--capability", metavar="NAME", default=None, help="print one capability list"
    )
    parser.add_argument("--mic", default=None, help="market context for --field")
    parser.add_argument("--asset-class", default=None, help="market context for --field")
    parser.add_argument("--country", default=None, help="market context for --field")
    args = parser.parse_args(argv)

    source = args.path or DEFAULT_PATH
    try:
        document = load_graph(source)
    except (OSError, ValueError) as exc:  # missing file, or not a JSON object
        print(f"source-graph INVALID: cannot read {source}: {exc}", file=sys.stderr)
        return 1

    if args.field or args.capability:
        try:
            if args.field:
                payload: Any = resolve_field(
                    args.field,
                    mic=args.mic,
                    asset_class=args.asset_class,
                    country=args.country,
                    graph=document,
                )
            else:
                payload = resolve_capability(args.capability, graph=document)
        except KeyError as exc:
            print(f"source-graph: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.validate:
        try:
            validate_graph(document)
        except GraphInvalid as exc:
            print(f"source-graph INVALID ({source}): {exc}", file=sys.stderr)
            return 1
        print(f"source-graph OK: {_summary(document)} ({source})")
        return 0

    print(f"source-graph: {_summary(document)} ({source})")
    print("use --validate to check the contract, --field/--capability to query it")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
