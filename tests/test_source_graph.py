"""Source Graph SG0 RED — one machine-readable map of who may fill the 92 columns.

Contract (PLAN-source-graph.md §2–§5). The graph is a *decision map*, not a
provider ladder replacement and not a value store: for every canonical column
it names one primary, ordered fallbacks, independent cross-checks, the
methodology, and the access/licence constraints that make the choice legal.

What this file pins
-------------------

* ``reference/source_graph.json`` is the single document: ``version``, unique
  ``nodes``, valid ``edges``, exactly 92 ``fields`` in export order and the
  three screen-only fields (``beta``, ``high_52w``, ``low_52w``).
* Every field names a live primary (``active``/``partial``, never
  robots-disallowed for the access it needs), unique fallbacks that never
  repeat the primary, and independent cross-checks.
* Resolution is conditional, deterministic and **fail-closed**: Finviz is a
  US-only fallback/cross-check for ``last_price``; a Tokyo ``mic`` or a call
  with no market context never receives it.
* ``beta`` stores its window (canonical 5y) and TradingView's 1-year beta is
  never primary; the 52-week extremes are never taken from the Finviz screener
  (that column is a percent distance, not a price).
* ``validate_graph`` rejects the six defects PLAN §5 gate 2–5, 9–10 names.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest

from tests import _source_graph as SG


# ---------------------------------------------------------------------------
# 1. The document
# ---------------------------------------------------------------------------


def test_reference_graph_is_one_versioned_document() -> None:
    document = SG.graph_doc()

    assert document["version"] == SG.GRAPH_VERSION
    for key in ("nodes", "edges", "fields", "screen_fields"):
        assert key in document, f"the graph document must carry {key!r}"


def test_fields_are_exactly_the_92_canonical_columns_in_export_order() -> None:
    entries = SG.fields()

    assert len(entries) == 92
    assert [entry["column"] for entry in entries] == list(SG.COLUMNS), (
        "the graph must cover CANONICAL_COLUMNS_V1 one-to-one and in export order; "
        "a field set that drifts from the contract is a silent coverage hole"
    )


def test_field_groups_match_the_plan_group_table() -> None:
    declared = {entry["column"]: entry["group"] for entry in SG.fields()}

    assert declared == SG.PLAN_GROUP_OF, (
        "PLAN §3 groups the 92 columns as reference/quote/eod_bars/performance/"
        "fundamentals/options/verify; a column in the wrong group gets the wrong "
        "access rule"
    )
    assert Counter(SG.PLAN_GROUP_OF.values()) == Counter(SG.PLAN_GROUP_COUNTS)


def test_every_field_declares_a_primary_and_its_candidate_lists() -> None:
    required = {
        "column",
        "group",
        "type",
        "primary",
        "fallbacks",
        "crosschecks",
        "methodology",
    }
    for entry in SG.fields():
        missing = required - set(entry)
        assert not missing, f"{entry.get('column')!r} is missing {sorted(missing)}"
        assert isinstance(entry["primary"], str) and entry["primary"].strip(), entry["column"]
        assert isinstance(entry["fallbacks"], list), entry["column"]
        assert isinstance(entry["crosschecks"], list), entry["column"]
        assert entry["type"] in SG.FIELD_TYPES, (entry["column"], entry["type"])
        assert entry["methodology"].strip(), f"{entry['column']} needs a methodology sentence"

    for entry in SG.screen_fields():
        assert isinstance(entry.get("primary"), str) and entry["primary"].strip(), entry["column"]
        assert isinstance(entry.get("fallbacks"), list), entry["column"]
        assert isinstance(entry.get("crosschecks"), list), entry["column"]


def test_every_field_primary_is_a_live_allowed_node() -> None:
    index = SG.nodes_by_id()
    for entry in SG.fields():
        node = index.get(entry["primary"])
        assert node is not None, (
            f"{entry['column']} names primary {entry['primary']!r}, which is not a node"
        )
        assert node["status"] in {"active", "partial"}, (
            f"{entry['column']} primary {entry['primary']!r} has status {node['status']!r}; "
            "blocked/unwired sources cannot fill a column"
        )
        assert node["robots"] != "disallowed", (
            f"{entry['column']} primary {entry['primary']!r} is robots-disallowed"
        )
        capability = SG.CAPABILITY_OF_PLAN_GROUP.get(entry["group"])
        if capability is not None:
            access = node["access"].get(capability, "allowed")
            assert access not in {"robots_disallowed", "none"}, (
                f"{entry['column']} needs {capability!r} access from {entry['primary']!r}, "
                f"but the graph declares {access!r}"
            )


def test_fallbacks_are_unique_never_the_primary_and_resolvable() -> None:
    index = SG.nodes_by_id()
    for entry in SG.fields():
        ids = [SG.source_id(item) for item in entry["fallbacks"]]
        assert len(ids) == len(set(ids)), f"{entry['column']} repeats a fallback: {ids}"
        assert entry["primary"] not in ids, (
            f"{entry['column']} lists its primary {entry['primary']!r} as a fallback"
        )
        assert set(ids) <= set(index), (entry["column"], sorted(set(ids) - set(index)))


def test_crosschecks_are_independent_of_the_primary() -> None:
    index = SG.nodes_by_id()
    for entry in SG.fields():
        ids = [SG.source_id(item) for item in entry["crosschecks"]]
        assert len(ids) == len(set(ids)), f"{entry['column']} repeats a crosscheck: {ids}"
        assert entry["primary"] not in ids, (
            f"{entry['column']} cross-checks itself with its primary; a cross-check is "
            "only worth a verdict when it is a different upstream"
        )
        assert set(ids) <= set(index), (entry["column"], sorted(set(ids) - set(index)))


def test_last_price_has_an_independent_crosscheck() -> None:
    entry = SG.field_entry("last_price")

    assert SG.expected_sources(entry["crosschecks"], mic="XNAS"), (
        "PLAN §2: a price field needs at least one cross-check when one exists"
    )


# ---------------------------------------------------------------------------
# 2. Nodes
# ---------------------------------------------------------------------------


def test_node_ids_are_unique() -> None:
    index = SG.nodes_by_id()

    assert len(index) == len(SG.nodes())
    assert len(index) >= len(SG.PLAN_SOURCE_IDS), (
        f"PLAN §3 seeds {len(SG.PLAN_SOURCE_IDS)} sources, the graph has {len(index)}"
    )


def test_nodes_declare_the_full_envelope() -> None:
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
    for node in SG.nodes():
        node_id = node.get("id")
        missing = required - set(node)
        assert not missing, f"node {node_id!r} is missing {sorted(missing)}"
        assert node["kind"] in SG.NODE_KINDS, (node_id, node["kind"])
        assert node["authority"] in SG.AUTHORITIES, (node_id, node["authority"])
        assert node["robots"] in SG.ROBOTS, (node_id, node["robots"])
        assert node["status"] in SG.STATUSES, (node_id, node["status"])
        assert node["delay"] in SG.DELAYS, (
            f"{node_id} delay {node['delay']!r} is not an operational freshness value; "
            "a free sentence cannot be compared or rendered"
        )
        assert isinstance(node["trust"], int) and 1 <= node["trust"] <= 4, (
            node_id,
            node["trust"],
        )
        assert isinstance(node["license"], str) and node["license"].strip(), node_id
        assert isinstance(node["access"], dict) and node["access"], node_id
        assert isinstance(node["capabilities"], list) and node["capabilities"], node_id
        assert set(node["capabilities"]) <= SG.NODE_CAPABILITIES, (
            node_id,
            sorted(set(node["capabilities"]) - SG.NODE_CAPABILITIES),
        )


def test_plan_sources_are_present() -> None:
    index = SG.nodes_by_id()

    missing = [node_id for node_id in SG.PLAN_SOURCE_IDS if node_id not in index]
    assert missing == [], f"PLAN §3 sources missing from the graph: {missing}"


def test_access_limits_are_declared() -> None:
    index = SG.nodes_by_id()

    assert index["finviz"]["access"]["screener"] == "robots_disallowed", (
        "Finviz robots.txt forbids the screener; the graph must say so, not rely on "
        "a comment in a scraper"
    )
    assert index["finviz"]["access"]["quote"] == "allowed", (
        "the Finviz quote page is allowed — that is the only route a 52-week price "
        "may take"
    )
    assert index["tradingview"]["access"]["export"] == "none"
    assert index["yahoo"]["access"]["bulk"] == "limited"


def test_ibkr_is_unwired_and_never_a_primary() -> None:
    index = SG.nodes_by_id()

    assert index["ibkr"]["status"] == "unwired"
    primaries = {entry["primary"] for entry in SG.fields()} | {
        entry["primary"] for entry in SG.screen_fields()
    }
    assert "ibkr" not in primaries, (
        "a broker with no gateway (PLAN §6) cannot be a primary source"
    )


def test_derived_nodes_declare_inputs_and_methodology() -> None:
    index = SG.nodes_by_id()
    derived = {node_id: node for node_id, node in index.items() if node["kind"] == "derived"}

    assert {"engine:technicals", "engine:performance", "engine:quality"} <= set(derived)
    for node_id, node in derived.items():
        assert node["authority"] == "derived", node_id
        inputs = node.get("derived_from")
        assert isinstance(inputs, list) and inputs, f"{node_id} must name its inputs"
        assert set(inputs) <= set(index), (node_id, sorted(set(inputs) - set(index)))
        assert node_id not in inputs, f"{node_id} derives from itself"
        assert node["methodology"].strip(), f"{node_id} must name a methodology version"


# ---------------------------------------------------------------------------
# 3. Edges
# ---------------------------------------------------------------------------


def test_edges_are_structurally_valid() -> None:
    index = SG.nodes_by_id()
    names = SG.known_field_names()

    for edge in SG.edges():
        assert edge["relation"] in SG.RELATIONS, edge
        assert edge["from"] in index, f"edge from unknown node: {edge}"
        assert edge["to"] in index, f"edge to unknown node: {edge}"
        assert edge["from"] != edge["to"], f"self edge: {edge}"
        if edge.get("field") is not None:
            assert edge["field"] in names, f"edge names unknown field: {edge}"
        if edge["relation"] in {"fallback", "crosscheck"}:
            assert edge.get("field") in names, (
                f"a {edge['relation']} edge is per-field and must name one: {edge}"
            )
        if edge["relation"] == "derived_from":
            assert index[edge["from"]]["kind"] == "derived", (
                f"only a derived node can declare derived_from edges: {edge}"
            )


def test_provides_edges_declare_capability_and_trust() -> None:
    for edge in SG.edges():
        if edge["relation"] != "provides":
            continue
        assert edge.get("capability") in SG.NODE_CAPABILITIES, edge
        assert isinstance(edge.get("trust"), int) and 1 <= edge["trust"] <= 4, edge


def test_derived_from_edges_agree_with_nodes() -> None:
    declared = {
        (node["id"], source)
        for node in SG.nodes()
        if node["kind"] == "derived"
        for source in node.get("derived_from") or []
    }
    linked = {
        (edge["from"], edge["to"])
        for edge in SG.edges()
        if edge["relation"] == "derived_from"
    }

    assert linked == declared, (
        "the derived_from edges and the nodes' derived_from lists must describe the "
        f"same DAG; edges={sorted(linked)} nodes={sorted(declared)}"
    )


def test_edge_conditions_are_declared_in_a_checkable_form() -> None:
    for edge in SG.edges():
        if "when" not in edge:
            continue
        when = edge["when"]
        if isinstance(when, str):
            # PLAN §2 shows a prose predicate ("asset_class=equity and mic in US");
            # a note is allowed on an edge, but never a silent free-form verdict.
            assert when.strip(), f"empty when on {edge}"
            continue
        assert isinstance(when, dict) and when, edge
        assert set(when) <= SG.WHEN_KEYS, (
            f"unknown predicate in {edge}; the resolver only evaluates "
            f"{sorted(SG.WHEN_KEYS)}"
        )
        for key, value in when.items():
            assert isinstance(value, str) or (
                isinstance(value, list) and value and all(isinstance(v, str) for v in value)
            ), (key, value)


# ---------------------------------------------------------------------------
# 4. Resolution
# ---------------------------------------------------------------------------


def test_last_price_us_resolves_to_yahoo_with_finviz_as_fallback_and_crosscheck() -> None:
    graph = SG.graph_module()
    document = SG.graph_doc()

    resolution = graph.resolve_field("last_price", mic="XNAS", asset_class="equity")

    assert resolution["column"] == "last_price"
    assert resolution["primary"]["id"] == "yahoo"
    yahoo = SG.node_entry("yahoo", document)
    assert resolution["primary"]["trust"] == yahoo["trust"]
    assert resolution["primary"]["delay"] == yahoo["delay"]
    assert resolution["policy"] == SG.POLICY, (
        "a resolution must state the conflict policy: primary wins, never an average"
    )
    fallbacks = [SG.source_id(item) for item in resolution["fallbacks"]]
    crosschecks = [SG.source_id(item) for item in resolution["crosschecks"]]
    assert "finviz" in fallbacks, (
        "PLAN §2: Finviz is the US fallback for last_price, not a global one"
    )
    assert "finviz" in crosschecks, "PLAN §2: Finviz cross-checks the US last_price"
    assert fallbacks == SG.expected_sources(
        SG.field_entry("last_price", document)["fallbacks"],
        mic="XNAS",
        asset_class="equity",
    ), "fallback order is part of the contract; the resolver may not re-sort it"


def test_last_price_global_resolution_never_uses_finviz() -> None:
    graph = SG.graph_module()

    resolution = graph.resolve_field("last_price", mic=SG.GLOBAL_MIC)

    used = [
        resolution["primary"]["id"],
        *[SG.source_id(item) for item in resolution["fallbacks"]],
        *[SG.source_id(item) for item in resolution["crosschecks"]],
    ]
    assert resolution["primary"]["id"] == "yahoo"
    assert "finviz" not in used, (
        "Finviz is US-only (PLAN §5 gate 8); a XTKS resolution that lists it would "
        "put a US quote in a Tokyo row"
    )


def test_last_price_without_market_context_does_not_assume_us() -> None:
    graph = SG.graph_module()

    resolution = graph.resolve_field("last_price")

    fallbacks = [SG.source_id(item) for item in resolution["fallbacks"]]
    assert "finviz" not in fallbacks, (
        "resolution is fail-closed: without a mic the US-only predicate is unproven, "
        "so Finviz may not be used"
    )


def test_resolver_keeps_the_declared_us_fallback_order() -> None:
    graph = SG.graph_module()
    document = SG.graph_doc()
    expected = SG.expected_sources(
        SG.field_entry("last_price", document)["fallbacks"], mic="XNAS"
    )

    got = [SG.source_id(item) for item in graph.resolve_field("last_price", mic="XNAS")["fallbacks"]]

    assert got == expected
    assert len(got) >= 2, (
        "PLAN §2 gives last_price two US fallbacks (Finviz first, then delayed "
        f"TradingView); got {got}"
    )


def test_unknown_field_is_a_key_error() -> None:
    graph = SG.graph_module()

    with pytest.raises(KeyError) as excinfo:
        graph.resolve_field("definitely_not_a_column", mic="XNAS")

    assert "definitely_not_a_column" in str(excinfo.value)


def test_beta_declares_its_window_and_tradingview_1y_is_not_primary() -> None:
    entry = SG.screen_field_entry("beta")

    assert entry.get("beta_window") in {"1y", "5y"}, (
        "PLAN §3 note 1: beta is only comparable with its window; store beta_window"
    )
    assert entry["beta_window"] == "5y", (
        "the canonical beta is the 5-year window used by Yahoo/Finviz"
    )
    assert entry.get("primary") != "tradingview", (
        "TradingView only computes beta_1_year; it cannot fill a 5y beta column"
    )
    for item in [*entry["fallbacks"], *entry["crosschecks"]]:
        if SG.source_id(item) == "tradingview":
            assert SG.source_meta(item, "beta_window") == "1y", (
                "a TradingView beta is 1y and must say so on the entry"
            )


@pytest.mark.parametrize("column", ["high_52w", "low_52w"])
def test_52_week_extremes_never_come_from_the_finviz_screener(column: str) -> None:
    entry = SG.screen_field_entry(column)

    assert entry.get("primary") != "finviz", (
        "PLAN §3 note 2: the Finviz screener column is percent distance from the high, "
        "not a price"
    )
    for item in [*entry["fallbacks"], *entry["crosschecks"]]:
        if SG.source_id(item) == "finviz":
            assert SG.source_meta(item, "via") == "quote", (
                f"{column} may only use the Finviz quote page, never the screener"
            )


def test_capability_quote_is_ordered_by_trust_then_id() -> None:
    graph = SG.graph_module()
    index = SG.nodes_by_id()

    ids = [SG.source_id(item) for item in graph.resolve_capability("quote")]

    assert len(ids) == len(set(ids)), f"a source appears twice: {ids}"
    assert ids == sorted(ids, key=lambda node_id: (index[node_id]["trust"], node_id)), (
        "capability order must be deterministic: trust ascending, then id"
    )
    assert "yahoo" in ids and "finviz" in ids
    assert ids.index("yahoo") < ids.index("finviz"), (
        "Yahoo (trust 3) must be attempted before Finviz (trust 4)"
    )


def test_capability_resolution_excludes_blocked_and_unwired_sources() -> None:
    graph = SG.graph_module()
    index = SG.nodes_by_id()

    entries = graph.resolve_capability("quote")
    ids = [SG.source_id(item) for item in entries]

    assert "ibkr" not in ids, "ibkr is unwired (PLAN §3) and cannot answer a quote"
    assert "gdelt" not in ids, "news is not a quote capability"
    for item in entries:
        node = index[SG.source_id(item)]
        assert node["status"] in {"active", "partial"}, (node["id"], node["status"])


def test_capability_resolution_excludes_robots_disallowed_routes() -> None:
    graph = SG.graph_module()
    index = SG.nodes_by_id()

    assert index["finviz"]["access"]["screener"] == "robots_disallowed"
    assert "screener" in index["finviz"]["capabilities"], (
        "the graph must model the Finviz screener as a capability it may not use, "
        "otherwise the exclusion below proves nothing"
    )
    ids = [SG.source_id(item) for item in graph.resolve_capability("screener")]

    assert "finviz" not in ids


def test_resolvers_are_deterministic() -> None:
    graph = SG.graph_module()

    assert graph.load_graph() == SG.graph_doc()
    assert graph.resolve_field("last_price", mic="XNAS") == graph.resolve_field(
        "last_price", mic="XNAS"
    )
    assert graph.resolve_capability("quote") == graph.resolve_capability("quote")


# ---------------------------------------------------------------------------
# 5. Validator
# ---------------------------------------------------------------------------


def test_production_graph_validates_clean() -> None:
    graph = SG.graph_module()

    assert issubclass(graph.GraphInvalid, ValueError), (
        "GraphInvalid must be a ValueError so callers can catch one base class"
    )
    assert graph.validate_graph(SG.graph_doc()) is None


@pytest.mark.parametrize("kind", sorted(SG.BAD_GRAPH_FRAGMENTS))
def test_validator_rejects_a_broken_graph(kind: str) -> None:
    graph = SG.graph_module()
    document = SG.bad_graph(kind)

    with pytest.raises(graph.GraphInvalid) as excinfo:
        graph.validate_graph(document)

    message = str(excinfo.value).lower()
    assert any(fragment.lower() in message for fragment in SG.BAD_GRAPH_FRAGMENTS[kind]), (
        f"the {kind} graph was rejected, but the message does not name the defect: "
        f"{excinfo.value}"
    )


def test_reference_graph_and_schema_name_no_credentials() -> None:
    for label, document in (("graph", SG.graph_doc()), ("schema", SG.schema_doc())):
        text = json.dumps(document, sort_keys=True).lower()
        for token in SG.SECRET_TOKENS:
            assert token not in text, (
                f"the {label} artifact mentions {token!r}: a source graph names sources, "
                "never credentials (PLAN §6)"
            )
