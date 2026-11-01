"""The published JSON Schemas: served, versioned, and equal to what the code does.

Two documents, two promises:

* the **response** schema (``GET /screens/schema``) is shape-locked — the browse
  count is ``matched``, a row carries its brand mark, and the file at
  ``schemas/screen_response_v1.json`` is the same document;
* the **request** schema (``GET /screens/select/schema``) is the contract a
  client generates its filter picker from, so its filter vocabulary has to be
  the validator's vocabulary. F7 was the opposite: the document still said "one
  of the 82 Finviz filter keys" while the validator accepted 44 catalogue
  spellings (``pe``, ``roic``, ``market_cap``) that the document never named —
  a conforming client could not offer the whole point of the feature.

The filter tests below do not eyeball JSON. They ask :func:`parse_selection`
about every candidate key and compare the set it accepts with the set the
document publishes, so a key added to the validator without the schema (or a
schema entry the validator refuses) fails here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests._support import api_client

ROOT = Path(__file__).resolve().parents[1]
REQUEST_ARTIFACT = ROOT / "schemas" / "stock_selection_request_v1.json"


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
        "matched",
        "universe_total",
        "returned",
        "rows",
    }, (
        "the browse count is `matched`; `total` on /screens/select is a different "
        "number and one word for two numbers is how the client mislabelled both"
    )
    assert "total" not in schema["properties"], "the ambiguous name is gone"
    assert "logo" in schema["$defs"]["row"]["properties"], (
        "the row has always carried `logo`; a typed client generated from this "
        "schema would otherwise drop it"
    )
    assert schema["properties"]["rows"]["maxItems"] == 20
    row = schema["$defs"]["row"]
    assert row["additionalProperties"] is False
    assert "turnover_usd" in row["properties"]
    assert row["properties"]["turnover_usd"]["$ref"] == "#/$defs/nullableNumber"


def test_schema_is_also_a_repo_artifact() -> None:
    from app.screen.schema import SCREEN_RESPONSE_SCHEMA

    artifact = ROOT / "schemas" / "screen_response_v1.json"
    assert artifact.exists(), (
        "the schema clients consume must also exist as a file at "
        "schemas/screen_response_v1.json"
    )
    assert json.loads(artifact.read_text()) == SCREEN_RESPONSE_SCHEMA


# ---------------------------------------------------------------------------
# The request schema's filter vocabulary
# ---------------------------------------------------------------------------

#: The two codes that mean "this API has no such filter key". Every other
#: failure is about the *value* (an operator the column cannot take, a bad
#: option token), which is the validator accepting the key and refusing the
#: predicate — the distinction this test turns on.
_NOT_A_KEY = frozenset({"unknown_filter_key", "unavailable_filter_key"})


def _accepted_keys() -> set[str]:
    """Every ``key`` value :func:`parse_selection` accepts, found by asking it.

    The candidate set is the union of the three tables a key can come from (the
    82 Finviz keys, the panel catalogue, the row columns) **plus** whatever the
    document publishes, so a key present on one side only is still probed.
    """

    from app.engine.catalog import CATALOG_BY_KEY
    from app.screen.filter_fields import CATALOGUE
    from app.screen.filters import FIELDS
    from app.screen.selection import SelectionError, parse_selection

    published = json.loads(REQUEST_ARTIFACT.read_text(encoding="utf-8"))
    candidates = (
        set(CATALOG_BY_KEY)
        | {entry.key for entry in CATALOGUE}
        | set(FIELDS)
        | set(published["x-filter-keys"])
        | set(published["x-filter-aliases"])
    )

    accepted: set[str] = set()
    for key in sorted(candidates):
        body = {
            "mode": "criteria",
            "region": "us",
            "criteria": {
                "filters": [{"key": key, "op": "gt", "value": 1}],
            },
        }
        try:
            parse_selection(body)
        except SelectionError as exc:
            if exc.code not in _NOT_A_KEY:
                accepted.add(key)
        else:
            accepted.add(key)
    return accepted


def _published(document: dict[str, Any]) -> tuple[set[str], set[str]]:
    """``(usable, refused)``: the keys a filter may name, and the ones it may not."""

    keys = document["x-filter-keys"]
    usable = {
        key
        for key, entry in keys.items()
        if entry.get("field") is not None or "choice" in entry or entry.get("bucket")
    }
    refused = set(keys) - usable
    return usable, refused


def test_the_published_filter_vocabulary_is_the_validator_vocabulary() -> None:
    """F7: the document and the validator are one vocabulary, checked by asking.

    Adversarial note: adding a key to the validator (or to a catalogue table the
    validator reads) without publishing it fails here, because the accepted set
    is derived from the validator rather than from the document.
    """

    from app.screen.selection import SELECTION_REQUEST_SCHEMA

    document = json.loads(REQUEST_ARTIFACT.read_text(encoding="utf-8"))
    assert document == SELECTION_REQUEST_SCHEMA, (
        "schemas/stock_selection_request_v1.json and the served schema are one "
        "document; regenerate the artifact from the module"
    )

    usable, refused = _published(document)
    aliases = document["x-filter-aliases"]
    accepted = _accepted_keys()

    documented = usable | set(aliases)
    assert documented == accepted, (
        "the published filter vocabulary and the validator disagree — "
        f"accepted but not published: {sorted(accepted - documented)}; "
        f"published but refused: {sorted(documented - accepted)}"
    )
    assert accepted.isdisjoint(refused), (
        f"the document says these keys are refused, and the validator accepts "
        f"them: {sorted(accepted & refused)}"
    )

    assert document["x-filter-key-count"] == len(document["x-filter-keys"]) == 82, (
        "the Finviz table is still the 82 keys, with their disposition"
    )
    assert document["x-filter-alias-count"] == len(aliases), (
        "the alias count must be the size of the table it counts"
    )
    assert len(usable) + len(refused) == 82
    assert len(usable) + len(aliases) == len(accepted), (
        "the two tables together are exactly the keys a filter may name"
    )


def test_every_published_alias_resolves_to_a_row_column() -> None:
    """Each alias names the column it compiles to, and that column is real."""

    from app.screen.filters import FIELDS

    document = json.loads(REQUEST_ARTIFACT.read_text(encoding="utf-8"))
    aliases = document["x-filter-aliases"]
    assert aliases, "the catalogue spellings must be published, not just promised"
    for key, entry in aliases.items():
        assert entry["field"] in FIELDS, (
            f"alias {key!r} points at {entry['field']!r}, which is not a screen row "
            "column: a client would build a picker that filters nothing"
        )
        assert entry["field"] in document["$defs"]["filter"]["properties"]["field"]["enum"], (
            f"alias {key!r} resolves to a column the schema itself does not allow"
        )


def test_the_published_description_names_both_vocabularies() -> None:
    """The prose a human reads must not contradict the tables a machine reads."""

    document = json.loads(REQUEST_ARTIFACT.read_text(encoding="utf-8"))
    key_help = document["$defs"]["filter"]["properties"]["key"]["description"]
    assert str(document["x-filter-key-count"]) in key_help
    assert str(document["x-filter-alias-count"]) in key_help
    assert "x-filter-aliases" in key_help, (
        "a client author has to be told where the catalogue spellings live"
    )
    assert "82 filter keys" in document["description"]


# ---------------------------------------------------------------------------
# The descriptive sweep (plan T11)
# ---------------------------------------------------------------------------


def test_the_published_sort_vocabulary_is_the_catalog_and_its_aliases() -> None:
    """Every orderable column, both directions, plus the three legacy names."""

    from app.screen.rank import ALIASES, ORDER_BY

    document = json.loads(REQUEST_ARTIFACT.read_text(encoding="utf-8"))
    sort = document["properties"]["criteria"]["properties"]["sort"]
    enum = set(sort["enum"])

    assert set(ORDER_BY) <= enum, "every numeric catalog field is orderable"
    assert {f"-{name}" for name in ORDER_BY} <= enum, "a leading '-' is ascending"
    assert set(ALIASES) <= enum, "the three legacy names still validate"
    assert "sector" not in enum, "a text column is not an order"
    assert "marketcap" not in enum, "Finviz's own spelling is not our vocabulary"
    assert sort["default"] == "market_cap"
    assert "ascending" in sort["description"]

    # The artifact is the module's document, so the enum cannot have been
    # hand-edited into a wider or narrower list.
    from app.screen.selection import SELECTION_REQUEST_SCHEMA

    assert SELECTION_REQUEST_SCHEMA["properties"]["criteria"]["properties"]["sort"] == sort


def test_the_published_request_carries_the_signal() -> None:
    """``signal`` is a top-level criteria knob with the frozen 27 ids."""

    from app.screen.descriptive import SIGNALS
    from app.screen.selection import SELECTION_REQUEST_SCHEMA

    signal = SELECTION_REQUEST_SCHEMA["properties"]["signal"]
    assert signal["enum"] == [item.id for item in SIGNALS]
    assert len(signal["enum"]) == 27
    assert "criteria" in signal["description"], (
        "the description has to say a symbols selection does not take it"
    )
    assert "unavailable_signal" in signal["description"], (
        "a client author has to know an unavailable id is a 422"
    )
    # A criteria body that names it is valid; a symbols body that names it is not.
    symbols_branch = SELECTION_REQUEST_SCHEMA["allOf"][1]["then"]
    assert {"required": ["signal"]} in symbols_branch["not"]["anyOf"]


def test_the_index_key_is_published_as_a_bucket_not_a_column() -> None:
    """``idx`` has no column; the schema says so instead of promising a field."""

    document = json.loads(REQUEST_ARTIFACT.read_text(encoding="utf-8"))
    entry = document["x-filter-keys"]["idx"]

    assert entry == {
        "label": "Index",
        "tab": "descriptive",
        "field": None,
        "bucket": True,
    }
    assert document["x-filter-key-count"] == 82
    assert len(document["x-filter-keys"]) == 82
    # The eleven bucket-driven columns are still columns *and* buckets.
    assert document["x-filter-keys"]["sh_price"]["field"] == "price"
    assert document["x-filter-keys"]["sh_price"]["bucket"] is True


def test_the_served_schema_and_the_artifact_are_one_document() -> None:
    """The three new facts are in the file, not only in the module.

    The byte-for-byte test above is the gate; this one names *what* moved in
    the descriptive sweep, so a future re-dump that silently reverts one of the
    three fails with a sentence instead of a diff.
    """

    from app.screen.descriptive import SIGNALS
    from app.screen.rank import ALIASES, ORDER_BY
    from app.screen.selection import SELECTION_REQUEST_SCHEMA

    artifact = json.loads(REQUEST_ARTIFACT.read_text(encoding="utf-8"))
    assert artifact == SELECTION_REQUEST_SCHEMA

    sort_enum = set(artifact["properties"]["criteria"]["properties"]["sort"]["enum"])
    assert {*ORDER_BY, *ALIASES} <= sort_enum
    assert artifact["properties"]["signal"]["enum"] == [item.id for item in SIGNALS]
    assert artifact["x-filter-keys"]["idx"]["bucket"] is True
    assert artifact["x-filter-keys"]["idx"]["field"] is None
    assert artifact["x-filter-key-count"] == 82


async def test_the_served_request_schema_is_the_repo_artifact(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The route, the module and the file are one document (no fourth copy).

    Exact equality, including ``views``. The catalogue is *in* the served
    document (a top-level annotation beside ``x-filter-keys``), so the artifact
    published in ``schemas/`` carries it too and a consumer that loads the file
    gets exactly what the route answers. F3 was this test failing because the
    route had grown a key the artifact never got: "the artifact" and "the answer"
    were two documents with one name. Weakening the comparison to ignore
    ``views`` would have made the suite green and left that drift in place.
    """

    async with api_client(tmp_path, monkeypatch) as client:
        response = await client.get("/screens/select/schema")

    assert response.status_code == 200, response.text
    served = response.json()
    artifact = json.loads(REQUEST_ARTIFACT.read_text(encoding="utf-8"))
    assert served == artifact, (
        "the served schema and schemas/stock_selection_request_v1.json must be one "
        f"document; served has {sorted(set(served) - set(artifact))} extra and is "
        f"missing {sorted(set(artifact) - set(served))}"
    )
    assert served["x-filter-alias-count"] == len(served["x-filter-aliases"])
    assert served["views"], "a document with an empty view list is worse than none"


def test_the_artifact_is_generated_from_the_module() -> None:
    """Byte-for-byte parity, so the artifact cannot be hand-edited into drift.

    The file is the document serialised with the one encoder that writes it
    (``indent=2``, UTF-8, one trailing newline). A key added to the module and
    not regenerated here fails on the bytes, not on someone remembering to look.
    """

    from app.screen.selection import SELECTION_REQUEST_SCHEMA

    generated = json.dumps(SELECTION_REQUEST_SCHEMA, indent=2, ensure_ascii=False) + "\n"
    assert REQUEST_ARTIFACT.read_text(encoding="utf-8") == generated, (
        "schemas/stock_selection_request_v1.json is not what the module serialises: "
        "regenerate it with json.dumps(document, indent=2, ensure_ascii=False) + '\\n'"
    )


def test_the_view_catalogue_is_an_annotation_and_not_a_request_property() -> None:
    """``views`` is metadata: a body that carries it is still refused.

    The document has two readers — a validator (``properties``, ``$defs``,
    ``allOf``) and a client building its tabs (``views``). Putting the catalogue
    in ``properties`` would advertise an input the route rejects, so it stays a
    top-level keyword the validator ignores, exactly like ``x-filter-keys``.
    """

    from app.screen.views import view_catalogue

    document = json.loads(REQUEST_ARTIFACT.read_text(encoding="utf-8"))
    assert document["views"] == view_catalogue()
    assert "views" not in document["properties"], (
        "a request property named `views` would be an input the route refuses"
    )
    assert "views" not in document["required"]
    assert document["additionalProperties"] is False, (
        "the request body is still closed; `views` is not an escape hatch"
    )


async def test_the_served_views_are_the_module_catalogue(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The served catalogue is the module's catalogue, not a copy of a copy.

    The client renders tabs and asks for columns from this list. If the route
    ever served a hand-written table, a rename in :mod:`app.screen.views` would
    show up here as a mismatch instead of in production as a blank column.
    """

    from app.screen.views import VIEW_NAMES, view_catalogue

    async with api_client(tmp_path, monkeypatch) as client:
        response = await client.get("/screens/select/schema")

    assert response.status_code == 200, response.text
    served = response.json()["views"]

    assert served == view_catalogue(), "the route must serve the module's list verbatim"
    assert [view["name"] for view in served] == [*VIEW_NAMES, "all"], (
        "the nine canonical views in catalogue order, then the all-columns "
        "destination — a client's tab order is the server's order"
    )
    assert len(served) == 10

    seen: dict[str, str] = {}
    for view in served:
        assert set(view) == {"name", "label", "job", "columns"}
        assert view["label"] and view["job"]
        assert all(isinstance(key, str) and key for key in view["columns"])
        if view["name"] == "all":
            continue
        # Every non-`all` view is a scannable panel: 8–13 columns, never the
        # whole catalogue. `all` is the explicit destination, not a view.
        assert 8 <= len(view["columns"]) <= 13, (
            f"{view['name']} has {len(view['columns'])} columns; the panel "
            "budget is 9–13 (income is the 8-column exception)"
        )
        for key in view["columns"]:
            assert key not in seen, (
                f"{key!r} is in both {seen.get(key)!r} and {view['name']!r}"
            )
            seen[key] = view["name"]

