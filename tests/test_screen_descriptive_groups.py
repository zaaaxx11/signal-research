"""T24 — every control says which tab it lives on and how it is answered.

The document is the panel's only source of what exists, so it must also say
*where* a control lives (its tab) and *how* its value is answered: read off the
scan row, joined in from the depth store, computed by a formula, waiting on a
named source, or unavailable outright. Those five words are the audit's, and
they are the whole vocabulary — a sixth would be a client-side invention.

A control's disposition and its availability are one fact stated twice, so the
tests pin them together: `unavailable` and `new_source` never carry a predicate,
`scan_row`/`join`/`derived` always carry at least one, and an option's
`predicate` is non-null exactly when its control is answerable **and** the
option itself compiles. The second half is not pedantry: `fa_divgrowth` is a
`join` whose nine `cy*` windows have no series and `ta_candlestick` is a `join`
whose nine unclassifiable shapes have no detector, so a rule stated per control
alone would have to call those families liars or their refusals invisible.

The disposition is also the schema's disposition: the twelve keys
`filter_fields` refuses are exactly the twelve the document calls unanswerable,
and every key the schema can filter is stated as answerable. One fact, one
owner, two published shapes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.screen import descriptive, filter_fields
from app.screen.filters import FILTER_CATALOG
from tests import _screen as S

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "research" / "finviz-ui-raw" / "views" / "v111-filters.json"

#: The five words, exactly: how a value is obtained.
DISPOSITIONS = {"scan_row", "join", "derived", "new_source", "unavailable"}

#: The three that answer a value today. `new_source` names what would unblock a
#: control; it does not mean the control runs.
ANSWERABLE = {"scan_row", "join", "derived"}


def _groups() -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = descriptive.document()["groups"]
    return groups


def _controls() -> list[dict[str, Any]]:
    return [control for group in _groups() for control in group["controls"]]


def _by_id() -> dict[str, dict[str, Any]]:
    return {control["id"]: control for control in _controls()}


def _probe_elite_keys() -> set[str]:
    """The controls the vendored probe flags ``isEliteOnly`` (the measurement)."""

    probe = json.loads(PROBE.read_text(encoding="utf-8"))
    return {
        key
        for key, spec in probe.items()
        if isinstance(spec, dict) and spec.get("isEliteOnly") is True
    }


# ---------------------------------------------------------------------------
# The five tabs
# ---------------------------------------------------------------------------


def test_the_document_has_five_tabs() -> None:
    doc = descriptive.document()
    assert [group["id"] for group in doc["groups"]] == [
        "descriptive",
        "fundamental",
        "technical",
        "news",
        "etf",
    ]
    # descriptive keeps its 25 controls (order, signal, 23 keys incl. sh_dilution)
    assert {group["id"]: len(group["controls"]) for group in doc["groups"]} == {
        "descriptive": 25,
        "fundamental": 37,
        "technical": 20,
        "news": 2,
        "etf": 1,
    }


def test_the_etf_tab_has_one_control_and_no_options() -> None:
    etf = descriptive.document()["groups"][4]
    assert len(etf["controls"]) == 1 and etf["controls"][0]["key"] == "etf_heldby"


# ---------------------------------------------------------------------------
# tab + disposition on every control
# ---------------------------------------------------------------------------


def test_every_control_states_its_tab_and_disposition() -> None:
    for group in _groups():
        for control in group["controls"]:
            assert control["tab"] == group["id"], (group["id"], control["id"])
            assert control["disposition"] in DISPOSITIONS, control["id"]
            if control["disposition"] == "unavailable":
                assert control["available"] is False and control["reason"], control["id"]


def test_a_control_sits_on_the_tab_the_catalogue_names() -> None:
    """The group's id is not the witness: the catalogue's `tab` is."""

    for control in _controls():
        key = control["key"]
        if key in FILTER_CATALOG:
            assert control["tab"] == FILTER_CATALOG[key].tab, control["id"]


def test_the_vocabulary_is_the_five_words_and_all_five_are_used() -> None:
    assert set(descriptive.DISPOSITIONS) == DISPOSITIONS
    # A word nothing uses is a word a client cannot test for.
    assert {control["disposition"] for control in _controls()} == DISPOSITIONS
    # Every catalogue key is stated exactly once, and no key is invented.
    assert set(descriptive.DISPOSITION_BY_KEY) == set(FILTER_CATALOG)


def test_a_disposition_and_its_availability_are_one_fact() -> None:
    for control in _controls():
        answerable = control["disposition"] in ANSWERABLE
        assert control["available"] is answerable, (
            control["id"],
            control["disposition"],
            control["available"],
        )


def test_every_option_predicate_is_the_answerability_it_claims() -> None:
    """predicate non-null iff the control answers values and the option compiles."""

    for control in _controls():
        answerable = control["disposition"] in ANSWERABLE
        predicates = [option["predicate"] is not None for option in control["options"]]
        if not answerable:
            assert not any(predicates), (
                control["id"],
                "a control whose disposition cannot answer publishes a predicate",
            )
        elif control["options"]:
            assert any(predicates), (
                control["id"],
                "an answerable control with options publishes none that compile",
            )
        for option in control["options"]:
            expected = answerable and option["available"]
            assert (option["predicate"] is not None) is expected, (
                control["id"],
                option["id"],
                option["predicate"],
            )
            if not option["available"]:
                assert option["reason"], (control["id"], option["id"])


def test_the_refused_keys_are_exactly_the_unanswerable_controls() -> None:
    """The schema's twelve refusals and the document's unanswerable words agree."""

    stated = {
        key
        for key, disposition in descriptive.DISPOSITION_BY_KEY.items()
        if disposition not in ANSWERABLE
    }
    assert stated == set(filter_fields.unavailable_keys())

    for key in filter_fields.unavailable_keys():
        assert descriptive.DISPOSITION_BY_KEY[key] in {"new_source", "unavailable"}, key
        assert (filter_fields.reason_for(key) or "").strip(), key


#: The six controls whose word is not the *source* of their row field: each is a
#: distance computed from the row's price and a level the field holds
#: (``(price / level − 1) × 100``, T22), so a scan or depth level does not make
#: the control a scan row or a join.
DISTANCE_CONTROLS = frozenset(
    {
        "ta_sma20",
        "ta_sma50",
        "ta_sma200",
        "ta_highlow50d",
        "ta_highlow52w",
        "ta_alltime",
    }
)


def test_a_disposition_follows_the_row_field_it_reads() -> None:
    """The 82-row table is hand-written; this is the measurement that keeps it true.

    A key whose column is a parity cell is a scan row, one whose column a join
    fills is a join, one built by :mod:`app.screen.derived` is derived. Only the
    six distance controls are exempt, and they are named above rather than
    excused in a comment.
    """

    from app.screen import facts, fundamentals, universe
    from app.screen.depth_overlay import SCAN_NULL_CELLS

    joined = (
        set(universe.DEPTH_FIELDS)
        | set(fundamentals.FUNDAMENTALS_ROW_FIELDS)
        | set(facts.FACTS_ROW_FIELDS)
        | set(SCAN_NULL_CELLS)
    )
    derived = set(universe.DERIVED_FIELDS)
    parity = set(universe.PARITY_FIELDS.values()) | set(universe.UNIVERSE_COLUMNS)

    for key, field in filter_fields.ROW_FIELDS.items():
        if key in DISTANCE_CONTROLS:
            assert descriptive.DISPOSITION_BY_KEY[key] == "derived", key
            continue
        if field in joined:
            expected = "join"
        elif field in derived:
            expected = "derived"
        else:
            assert field in parity, (key, field, "the field is from no known layer")
            expected = "scan_row"
        assert descriptive.DISPOSITION_BY_KEY[key] == expected, (key, field, expected)


# ---------------------------------------------------------------------------
# The elite tier: a refusal names the tier *and* the metric (D11)
# ---------------------------------------------------------------------------


def test_the_five_premium_controls_say_their_tier_and_only_one_is_refused() -> None:
    """Elite is a plan, not a source: four are built, one has no feed.

    The document must say both facts for every one of them: ``tier`` (premium,
    from the config) and ``available`` (true for the four the code answers,
    false for ``sh_trades``) — plus the reason the refused one gives, which is
    about the missing feed and never about the tier.
    """

    from app.screen import entitlements

    by_id = _by_id()
    for control_id in ("sh_trades", "ah_close", "ah_change", "news_keywords", "etf_heldby"):
        control = by_id[control_id]
        assert control["tier"] == "premium", control_id
        if control_id == "sh_trades":
            assert control["available"] is False, control_id
            assert control["disposition"] in {"new_source", "unavailable"}, control_id
            reason = control["reason"] or ""
            assert "no trade-count feed" in reason, (control_id, reason)
            assert "Finra publishes short-sale volume, not trade counts" in reason, reason
        else:
            assert control["available"] is True, control_id
            assert control["disposition"] in descriptive.ANSWERABLE_DISPOSITIONS, control_id
            assert control["reason"] is None, control_id
        assert entitlements.tier_for(control_id) == "premium"


def test_the_five_elite_controls_are_the_five_the_probe_flags() -> None:
    """The claim is measured, not remembered: the probe flags five controls."""

    assert _probe_elite_keys() == {
        "sh_trades",
        "ah_close",
        "ah_change",
        "news_keywords",
        "etf_heldby",
    }


# ---------------------------------------------------------------------------
# The route serves what the module builds
# ---------------------------------------------------------------------------


async def test_the_served_document_carries_the_same_metadata(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """`GET /screens/filters` is the document: no route-side copy to drift."""

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.get("/screens/filters")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == "screener-3"
    seen: set[str] = set()
    for group in body["groups"]:
        for control in group["controls"]:
            assert control["tab"] == group["id"], (group["id"], control["id"])
            assert control["disposition"] in DISPOSITIONS, control["id"]
            seen.add(control["disposition"])
    assert seen == DISPOSITIONS, "the served document uses all five words"
