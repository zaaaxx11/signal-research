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
        if entry.get("field") is not None or "choice" in entry
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
    assert "82 Finviz" in document["description"]


async def test_the_served_request_schema_is_the_repo_artifact(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The route, the module and the file are one document (no fourth copy)."""

    async with api_client(tmp_path, monkeypatch) as client:
        response = await client.get("/screens/select/schema")

    assert response.status_code == 200, response.text
    served = response.json()
    assert served == json.loads(REQUEST_ARTIFACT.read_text(encoding="utf-8"))
    assert served["x-filter-alias-count"] == len(served["x-filter-aliases"])
