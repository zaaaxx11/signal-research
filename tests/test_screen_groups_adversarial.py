"""T25 — adversarial sweep: every refusal is a 422 with one story and one code.

Two questions, both answered by measurement rather than by a table a reader has
to trust:

* **Does anything slip through?** A token from another control, a token on a
  refused key, a refused key's own vocabulary — each must be a 422 that names
  the thing that cannot run. The failure this prevents is the worst one a
  screener has: a filter that is silently ignored answers the *unfiltered*
  market, and a caller reads "no company matches" from a page that means "we
  did not look".
* **Do the three surfaces agree?** The document (what a client renders), the
  bucket registry (what the executor knows) and the HTTP answer must not tell
  three stories about one control. `sh_insidertrans` is the case that did: the
  key was refused as a *key* (`unavailable_filter_key`) while its 32 tokens were
  registered as refused *buckets* (`unavailable_bucket`). A client that switches
  on the code had to write two branches for one withdrawn control.

The resolution here: the **token** is the unit of refusal for a bucket-driven
key. `sh_insidertrans` keeps its ten-key refusal (it can answer no value at
all), so the schema and the document still publish it as unavailable — but a
request that names one of its tokens reaches the registry, and the registry's
code (`unavailable_bucket`) is what HTTP answers, with the registry's own
sentence. A request that names a *number* on that key is still a key-level
refusal, because a number is not one of its tokens.

The refusal texts are checked against the depth store, not against memory: a
sentence that says "no such column" about a cell the store fills is the exact
stale lie this task exists to delete.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.screen import buckets, descriptive, filter_fields, selection
from app.screen.filters import FILTER_CATALOG
from app.screen.selection import SelectionError, parse_selection
from tests import _screen as S

ROOT = Path(__file__).resolve().parents[1]
DEPTH = Path("/home/ubuntu/ztockz-data/depth.json")

#: The six keys that are still **refused**, in panel order — the assertion that
#: ends the argument. The order is the catalogue's
#: (`filter_fields.CATALOGUE`), not the plan's prose order: `unavailable_keys()`
#: promises panel order, so the test pins it.
#:
#: This list used to have ten members. ``ah_close``, ``ah_change``,
#: ``news_keywords`` and ``etf_heldby`` left it when **tier** was separated from
#: **availability**: the code answers them (the provider's post-market print, the
#: merged news corpus, the funds' own holdings files) and the plan gates them.
#: What remains is what nobody measures — no theme taxonomy, no print count, a
#: withdrawn insider flow, no 20-day window, no pattern engine.
REFUSED = (
    "sh_trades",
    "theme",
    "subtheme",
    "sh_insidertrans",
    "ta_highlow20d",
    "ta_pattern",
)

#: The four keys that are built, published with a predicate, and gated by tier.
#: A free plan gets a 403 naming them; a premium one gets rows.
GATED = ("ah_close", "ah_change", "news_keywords", "etf_heldby")

#: A request value each gated key accepts (the panel's own vocabulary shape).
GATED_VALUES = {
    "ah_close": "ah_close:u1",
    "ah_change": "ah_change:u",
    "news_keywords": "news_kw:earnings",
    "etf_heldby": "heldby:spy",
}

#: The codes a refusal may carry. Anything else is a bug wearing a 422.
REFUSAL_CODES = {"unavailable_filter_key", "unavailable_bucket", "unknown_bucket"}

#: Refusals the audit measured as *partial*: the cell exists and cannot be used
#: as the control needs it. The sentence must say so rather than claim the
#: deployment has no such column — the stale shape this task deletes.
#:
#: ``(cell, the word that says the cell is unusable)``: the second value is what
#: makes the claim checkable. A reason that merely *names* the cell could still
#: be lying about it ("`optionable` is empty"), so the sentence has to say what
#: the store actually holds.
PARTIAL = {
    "ta_pattern": ("candlestick_pattern", "flag"),
}


def _request(filters: list[dict[str, Any]], **criteria: Any) -> Any:
    return parse_selection(
        {
            "mode": "criteria",
            "region": "us",
            "criteria": {"filters": filters, **criteria},
        }
    )


def _select_body(filters: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "mode": "criteria",
        "region": "us",
        "criteria": {"limit": 5, "filters": filters},
    }


def _depth_cells() -> list[dict[str, Any]]:
    store = json.loads(DEPTH.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = list(store["symbols"].values())
    return rows


def _document_control(identifier: str) -> dict[str, Any]:
    """The control whose **key** or id is ``identifier`` (they differ: `price`)."""

    for group in descriptive.document()["groups"]:
        for control in group["controls"]:
            if control["key"] == identifier or control["id"] == identifier:
                return control
    raise AssertionError(f"the document has no control {identifier!r}")


# ---------------------------------------------------------------------------
# Nothing may be silently ignored
# ---------------------------------------------------------------------------


def test_a_token_from_another_control_is_refused() -> None:
    with pytest.raises(SelectionError, match="belongs to control"):
        _request([{"key": "ta_rsi", "op": "eq", "value": "pe:u15"}])


async def test_an_unavailable_token_is_422_and_never_an_unfiltered_page(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select",
            json=_select_body([{"key": "ta_pattern", "op": "eq", "value": "wedge"}]),
        )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "unavailable_filter_key"
    assert "total" not in body, "a refusal is never an empty page"


async def test_every_one_of_the_six_is_a_422_and_names_itself(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The document supplies the request: this is the panel's own vocabulary."""

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        for key in REFUSED:
            control = _document_control(key)
            value = control["options"][0]["id"] if control["options"] else key
            response = await harness.client.post(
                "/screens/select",
                json=_select_body([{"key": key, "op": "eq", "value": value}]),
            )
            body = response.json()
            assert response.status_code == 422, (key, value, response.text)
            assert body["error"] in REFUSAL_CODES, (key, value, body)
            assert "total" not in body, (key, "a refusal is never an empty page")
            assert key in body["errorMessage"], (key, value, body)


async def test_every_gated_key_is_403_without_a_plan_and_answers_with_one(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Tier is not a refusal: the same request is a 403 or a 200, never a 422."""

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        for key in GATED:
            request = _select_body([{"key": key, "op": "eq", "value": GATED_VALUES[key]}])
            denied = await harness.client.post("/screens/select", json=request)
            allowed = await harness.client.post(
                "/screens/select", json=request, headers={"X-Plan": "premium"}
            )
            assert denied.status_code == 403, (key, denied.text)
            assert denied.json()["error"] == "premium_required", (key, denied.json())
            assert key in denied.json()["errorMessage"], key
            assert "total" not in denied.json(), (key, "a 403 is never an empty page")
            assert allowed.status_code == 200, (key, allowed.text)


def test_the_six_refusals_are_exactly_the_six() -> None:
    assert set(filter_fields.unavailable_keys()) == set(REFUSED)
    assert filter_fields.unavailable_keys() == REFUSED, "panel order, not set order"
    for key in GATED:
        assert filter_fields.reason_for(key) is None, key
        assert filter_fields.binding(key).is_available is True, key
        assert _document_control(key)["available"] is True, key


# ---------------------------------------------------------------------------
# One control, three surfaces, one story
# ---------------------------------------------------------------------------


async def test_the_withdrawn_insider_control_answers_one_story(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """registry == document == schema == HTTP, for the one withdrawn control."""

    family = buckets.for_key("sh_insidertrans")
    assert len(family) == 32
    assert not any(item.available for item in family)
    reason = family[0].reason
    assert reason and all(item.reason == reason for item in family)

    # The registry: the token is a bucket and it is unavailable as a bucket.
    with pytest.raises(buckets.UnavailableBucket) as caught:
        buckets.bucket("insidertrans:pos")
    assert caught.value.code == "unavailable_bucket"
    assert reason in str(caught.value)

    # The schema: still one of the twelve, with the family's own sentence (one
    # owner — not a second copy that can drift) and no bucket flag: the key
    # advertises no vocabulary a client could send, because none of it runs.
    with pytest.raises(filter_fields.UnavailableFilterKey) as refusal:
        filter_fields.binding("sh_insidertrans")
    assert refusal.value.code == "unavailable_filter_key"
    assert reason in str(refusal.value)
    entry = filter_fields.BINDINGS["sh_insidertrans"]
    assert entry.is_available is False and entry.bucket is False
    assert entry.reason == reason
    assert "sh_insidertrans" in filter_fields.unavailable_keys()

    # The document: every option refused, none pretending to compile.
    control = _document_control("insidertrans")
    assert control["available"] is False
    assert control["disposition"] == "unavailable"
    assert control["reason"] == reason
    assert len(control["options"]) == len(family)
    for option in control["options"]:
        assert option["available"] is False and option["predicate"] is None
        assert option["reason"] == reason

    # HTTP: the token answers the registry's code, the registry's sentence.
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select",
            json=_select_body(
                [{"key": "sh_insidertrans", "op": "eq", "value": "insidertrans:pos"}]
            ),
        )
    body = response.json()
    assert response.status_code == 422, response.text
    assert body["error"] == "unavailable_bucket", body
    assert reason in body["errorMessage"], body
    assert "total" not in body


async def test_a_number_on_the_withdrawn_key_is_still_a_key_refusal(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A number is not one of the 32 tokens, so the key-level sentence answers."""

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select",
            json=_select_body([{"key": "sh_insidertrans", "op": "gte", "value": 50}]),
        )

    body = response.json()
    assert response.status_code == 422, response.text
    assert body["error"] == "unavailable_filter_key", body
    assert "mixture of ownership levels and junk" in body["errorMessage"]
    assert "total" not in body


# ---------------------------------------------------------------------------
# The sentences: true against the store, and naming the tier
# ---------------------------------------------------------------------------


def test_no_refusal_claims_a_missing_cell_the_depth_store_carries() -> None:
    rows = _depth_cells()
    for key, (cell, unusable) in PARTIAL.items():
        populated = sum(1 for row in rows if row.get(cell) not in (None, ""))
        assert populated > 0, (
            f"{cell} is empty in the depth store: this test no longer proves "
            "anything, and the refusal below must be re-measured"
        )
        reason = filter_fields.reason_for(key) or ""
        assert cell in reason, (
            key,
            f"the refusal must name the cell it cannot use ({cell})",
            reason,
        )
        assert unusable in reason, (
            key,
            f"the refusal must say what the cell holds ({unusable!r}), not just name it",
            reason,
        )


def test_the_three_stale_refusal_sentences_are_gone() -> None:
    """The exact claims the audit measured as false on 2026-11-01."""

    assert "the scan carries no such column" not in (filter_fields.reason_for("sh_opt") or "")
    assert "returns no pattern classification" not in (
        filter_fields.reason_for("ta_pattern") or ""
    )
    assert "can only return an empty screen" not in (filter_fields.reason_for("ipodate") or "")


def test_every_refusal_names_a_gap_a_reader_can_act_on() -> None:
    for key in REFUSED:
        reason = filter_fields.reason_for(key) or ""
        assert len(reason) > 60, (key, reason)
        assert any(
            marker in reason
            for marker in ("needs", "withdrew", "junk", "no trade-count feed")
        ), (key, reason)


def test_the_five_the_probe_flags_are_the_five_gated_features() -> None:
    """Elite is a **tier**: measured from the probe, assigned in one config file.

    The five controls the source panel flags ``isEliteOnly`` are exactly the five
    feature ids ``app/screen/data/entitlements.json`` can gate. Four are answered
    and gated; ``sh_trades`` is gated *and* refused, because its feed does not
    exist — and its sentence says the refusal is about the feed, not the tier.
    """

    from app.screen import entitlements

    probe = json.loads(
        (ROOT / "research" / "finviz-ui-raw" / "views" / "v111-filters.json").read_text(
            encoding="utf-8"
        )
    )
    elite = {
        key for key, spec in probe.items() if isinstance(spec, dict) and spec.get("isEliteOnly")
    }
    assert elite == set(entitlements.FEATURES)
    for key in set(REFUSED) - elite:
        assert "Elite" not in (filter_fields.reason_for(key) or ""), (
            key,
            "only the five the probe flags may claim the tier",
        )
    assert "Elite" not in (filter_fields.reason_for("sh_trades") or ""), (
        "the tier lives in the config now; the refusal is about the missing feed"
    )
    for key in GATED:
        assert entitlements.tier_for(key) in entitlements.TIERS, key


# ---------------------------------------------------------------------------
# The freezes this task must not move
# ---------------------------------------------------------------------------


def test_116_columns_are_still_116_and_carry_no_depth_only_names() -> None:
    from app.screen.views import ALL_COLUMNS

    assert len(ALL_COLUMNS) == 116
    assert "eps_this_y" in ALL_COLUMNS          # a catalogue column, not a new one
    assert "index_members" not in ALL_COLUMNS   # still internal-only


def test_the_column_cap_still_refuses_41() -> None:
    assert selection.COLUMN_LIMIT == 40


def test_no_catalogue_key_lost_its_binding_or_its_tab() -> None:
    assert len(FILTER_CATALOG) == 82
    assert len(filter_fields.CATALOGUE) == 82
    for key, spec in FILTER_CATALOG.items():
        assert spec.tab in {"descriptive", "fundamental", "technical", "news", "etf"}, key
