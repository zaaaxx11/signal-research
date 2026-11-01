"""The descriptive document: one control per Finviz control, one source (T9).

``GET /screens/filters`` publishes Finviz's Descriptive group as data: every
control, its options, whether it can run today and why not, and **the predicate
each option compiles to**. The point is that a client never hardcodes a label, a
token or a range again — the F1 bug class, where the panel and the server
disagreed about a vocabulary.

This suite pins the document itself, without HTTP:

* **25 controls**, in Finviz's panel order, ids exactly as the plan's table;
* every option count is the **vendored probe's** count (read from
  ``research/`` here, in the test only — production may not);
* an unavailable control keeps its options (greyed), states the metric it needs
  and publishes **no predicate**: a control that cannot filter must not look
  like one that can;
* every bucket control resolves every one of its options through
  :mod:`app.screen.buckets`, so a missing predicate family is a red test and not
  a 422 nobody saw;
* the membership block is the loaded snapshot's, and the document never
  publishes the internal ``index_members`` cell.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.screen import buckets, descriptive, index_membership
from app.screen.rank import ORDER_BY

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "research" / "finviz-ui-raw" / "views" / "v111-filters.json"

#: The 25 controls, in Finviz's panel order (plan §1).
CONTROL_IDS: tuple[str, ...] = (
    "order",
    "signal",
    "exch",
    "idx",
    "sec",
    "industry",
    "geo",
    "cap",
    "fa_div",
    "sh_short",
    "an_recom",
    "sh_opt",
    "earn",
    "sh_avgvol",
    "sh_relvol",
    "sh_curvol",
    "sh_trades",
    "price",
    "targetprice",
    "ipodate",
    "sh_outstanding",
    "sh_float",
    "theme",
    "subtheme",
    "sh_dilution",
)

#: The six controls that cannot run, and the one that is out of scope.
UNAVAILABLE: tuple[str, ...] = (
    "sh_opt",
    "sh_trades",
    "ipodate",
    "theme",
    "subtheme",
    "sh_dilution",
)

#: Control → the probe key whose options it publishes.
PROBE_KEY: dict[str, str] = {
    "exch": "exch",
    "idx": "idx",
    "cap": "cap",
    "fa_div": "fa_div",
    "sh_short": "sh_short",
    "an_recom": "an_recom",
    "sh_opt": "sh_opt",
    "earn": "earningsdate",
    "sh_avgvol": "sh_avgvol",
    "sh_relvol": "sh_relvol",
    "sh_trades": "sh_trades",
    "price": "sh_price",
    "targetprice": "targetprice",
    "ipodate": "ipodate",
    "sh_outstanding": "sh_outstanding",
    "sh_float": "sh_float",
    "theme": "theme",
    "subtheme": "subtheme",
    "sh_dilution": "sh_dilution",
}

#: The eighteen counts the probe and the document must agree on (plan §0.4).
OPTION_COUNTS: dict[str, int] = {
    "price": 39,
    "cap": 14,
    "fa_div": 14,
    "sh_short": 14,
    "an_recom": 9,
    "earn": 15,
    "sh_avgvol": 18,
    "sh_relvol": 16,
    "sh_float": 34,
    "targetprice": 14,
    "ipodate": 15,
    "sh_outstanding": 16,
    "exch": 5,
    "idx": 4,
    "theme": 40,
    "subtheme": 268,
    "sh_dilution": 5,
    "signal": 27,
}

#: The controls the document compiles through :mod:`app.screen.buckets`.
BUCKET_CONTROLS: dict[str, str] = {
    "idx": "idx",
    "cap": "cap",
    "fa_div": "fa_div",
    "sh_short": "sh_short",
    "an_recom": "an_recom",
    "earn": "earningsdate",
    "sh_avgvol": "sh_avgvol",
    "sh_relvol": "sh_relvol",
    "price": "sh_price",
    "targetprice": "targetprice",
    "sh_outstanding": "sh_outstanding",
    "sh_float": "sh_float",
}


def probe() -> dict[str, Any]:
    return json.loads(PROBE.read_text(encoding="utf-8"))


def probe_options(key: str) -> list[str]:
    """The probe's named options: no ``Any``, no Custom, no free-width token."""

    values = [option["value"] for option in probe()[key]["options"]]
    return [value for value in values if value and value not in ("frange", "modal")]


def descriptive_group() -> dict[str, Any]:
    document = descriptive.document()
    return next(group for group in document["groups"] if group["id"] == "descriptive")


def controls() -> dict[str, dict[str, Any]]:
    return {control["id"]: control for control in descriptive_group()["controls"]}


# ---------------------------------------------------------------------------
# The shape
# ---------------------------------------------------------------------------


def test_the_document_serves_five_tabs_and_descriptive_keeps_its_twenty_five() -> None:
    document = descriptive.document()

    assert document["schema_version"] == "finviz-2"
    assert [group["id"] for group in document["groups"]] == [
        "descriptive",
        "fundamental",
        "technical",
        "news",
        "etf",
    ]
    assert {group["id"]: len(group["controls"]) for group in document["groups"]} == {
        "descriptive": 25,
        "fundamental": 37,
        "technical": 20,
        "news": 2,
        "etf": 1,
    }
    group = document["groups"][0]
    assert group["label"] == "Descriptive"
    assert tuple(control["id"] for control in group["controls"]) == CONTROL_IDS
    assert len(group["controls"]) == 25
    assert len(set(CONTROL_IDS)) == 25


def test_every_control_states_its_key_its_shape_and_how_its_values_compile() -> None:
    vendored_keys = set(probe())  # every Descriptive key Finviz has, sh_dilution included

    for control_id, control in controls().items():
        assert control["label"].strip(), f"{control_id} has no label"
        assert control["control"] in {"buckets", "select", "text", "number", "order", "signal"}
        assert control["form"] in {"single", "multi"}
        assert control["status"] in {"available", "unavailable", "out_of_scope"}
        assert isinstance(control["available"], bool)
        assert (control["key"] is None) or control["key"] in vendored_keys, (
            f"{control_id} names a key Finviz's Descriptive group does not have"
        )
        if control["available"]:
            assert control["reason"] is None
            if control["control"] not in {"order", "signal"}:
                assert control["op"], f"{control_id} is available and names no operator"
            assert (
                control["options"]
                or control["options_source"]
                or control["control"] in {"text", "number"}
            ), f"{control_id} is available and offers nothing to send"
        else:
            assert control["reason"] and len(control["reason"]) > 20, (
                f"{control_id} is unavailable without a usable reason"
            )


def test_every_option_count_matches_the_probe() -> None:
    published = controls()

    for control_id, expected in OPTION_COUNTS.items():
        options = published[control_id]["options"]
        assert len(options) == expected, f"{control_id}: {len(options)} options, probe says {expected}"

    for control_id, key in PROBE_KEY.items():
        assert len(published[control_id]["options"]) == len(probe_options(key)), (
            f"{control_id} does not publish exactly the probe's named options"
        )
        assert [option["finviz"] for option in published[control_id]["options"]] == (
            probe_options(key)
        ), f"{control_id} publishes the probe's options in another order"


def test_the_live_populations_are_referenced_not_copied() -> None:
    """Sector labels and venue counts are live lists: two copies drift."""

    document = descriptive.document()
    published = controls()

    assert document["options_sources"] == {
        "/market/sectors": descriptive.SECTORS_SOURCE,
        "/market/exchanges": descriptive.EXCHANGES_SOURCE,
    }
    sector = published["sec"]
    assert sector["control"] == "select"
    assert sector["form"] == "multi"
    assert sector["options"] == [], "the sector taxonomy is the provider's, not a copy"
    assert sector["options_source"] == "/market/sectors"
    assert "sector in" in sector["predicate_template"]

    for control_id in ("industry", "geo"):
        control = published[control_id]
        assert control["control"] == "text"
        assert control["op"] == "contains"
        assert control["options"] == []
        assert control["hint"], f"{control_id} is free text and must say so"


def test_every_unavailable_control_states_why_and_has_no_predicate() -> None:
    published = controls()

    for control_id in UNAVAILABLE:
        control = published[control_id]
        assert control["available"] is False, control_id
        assert control["reason"].strip(), control_id
        assert control["options"], f"{control_id} is greyed out, not emptied"
        for option in control["options"]:
            assert option["predicate"] is None, (
                f"{control_id}/{option['id']} is unavailable and still claims a predicate"
            )

    assert published["sh_trades"]["status"] == "out_of_scope", (
        "Trades has no print-count source: it is named, not faked"
    )
    assert "trade count" in published["sh_trades"]["reason"]
    for control_id in UNAVAILABLE:
        if control_id != "sh_trades":
            assert published[control_id]["status"] == "unavailable"


def test_every_bucket_control_resolves_every_option() -> None:
    published = controls()

    for control_id, key in BUCKET_CONTROLS.items():
        control = published[control_id]
        assert control["control"] == "buckets", control_id
        family = buckets.for_key(key)
        assert len(control["options"]) == len(family), (
            f"{control_id}: {len(control['options'])} options for {len(family)} buckets"
        )
        for option, item in zip(control["options"], family, strict=True):
            assert option["id"] == item.id
            assert option["label"] == item.label
            assert option["finviz"] == item.finviz
            expected = item.describe if item.available else None
            assert option["predicate"] == expected, (
                f"{option['id']} publishes a predicate the bucket does not run"
            )
            assert option["available"] is item.available


def test_a_bucket_option_is_the_token_a_request_sends() -> None:
    published = controls()

    assert [option["id"] for option in published["idx"]["options"]] == [
        "idx:sp500",
        "idx:ndx",
        "idx:dji",
        "idx:rut",
    ]
    assert published["price"]["options"][4]["id"] == "price:u5"
    assert published["price"]["options"][4]["predicate"] == "price < 5"
    assert published["earn"]["options"][0]["id"] == "earn:today"


def test_the_earnings_sessions_are_published_but_disabled() -> None:
    earn = controls()["earn"]
    by_id = {option["id"]: option for option in earn["options"]}

    assert len(by_id) == 15
    for token in (
        "earn:todaybefore",
        "earn:todayafter",
        "earn:tomorrowbefore",
        "earn:tomorrowafter",
        "earn:yesterdaybefore",
        "earn:yesterdayafter",
    ):
        assert by_id[token]["available"] is False
        assert by_id[token]["reason"], token
        assert by_id[token]["predicate"] is None
    assert sum(1 for option in earn["options"] if option["available"]) == 9
    assert earn["available"] is True, "the control runs; six of its options do not"


def test_the_exchange_control_keeps_txse_disabled() -> None:
    exch = controls()["exch"]
    by_id = {option["id"]: option for option in exch["options"]}

    assert set(by_id) == {"amex", "cboe", "nasd", "nyse", "txse"}
    assert sum(1 for option in exch["options"] if option["available"]) == 4
    assert by_id["txse"]["available"] is False
    assert "TXSE" in by_id["txse"]["reason"]
    assert by_id["nyse"]["predicate"] == "exchange == 'XNYS'"


def test_the_order_control_is_the_catalog_not_a_literal() -> None:
    order = controls()["order"]

    assert order["control"] == "order"
    assert order["available"] is True
    assert [option["id"] for option in order["options"]] == list(ORDER_BY)
    assert "pe_ttm" in {option["id"] for option in order["options"]}
    assert "-pe_ttm" in order["hint"], "ascending is a leading minus"
    for name in ("market_cap", "turnover", "gainers"):
        assert name in order["hint"], f"the legacy alias {name} is still an order"


def test_the_signal_control_publishes_the_vocabulary_and_its_ten() -> None:
    signal = controls()["signal"]

    assert signal["control"] == "signal"
    assert len(signal["options"]) == 27
    available = [option for option in signal["options"] if option["available"]]
    assert len(available) == 10
    assert [option["id"] for option in available] == [
        "top-gainers",
        "top-losers",
        "most-active",
        "most-volatile",
        "most-increased-volume",
        "unusual-volume",
        "overbought",
        "oversold",
        "new-high",
        "new-low",
    ]
    for option in signal["options"]:
        if option["available"]:
            assert option["predicate"], f"{option['id']} names no predicate"
            assert option["order"] in {"asc", "desc"}
        else:
            assert option["predicate"] is None
            assert option["reason"]


# ---------------------------------------------------------------------------
# The runtime half
# ---------------------------------------------------------------------------


def test_the_membership_block_is_the_loaded_snapshot() -> None:
    snapshot = index_membership.load()
    membership = descriptive.document()["membership"]

    assert membership["available"] is snapshot.available
    assert membership["as_of"] == snapshot.as_of
    assert membership["counts"] == snapshot.counts
    assert membership["counts"]["sp500"] > 480
    assert membership["counts"]["dji"] == 30


def test_the_document_publishes_no_internal_membership_column() -> None:
    """``index_members`` is internal: a cell, never a column and never a key."""

    document = descriptive.document()
    controls = document["groups"][0]["controls"]
    keys = {control["key"] for control in controls}
    ids = {option["id"] for control in controls for option in control["options"]}
    tokens = {option["finviz"] for control in controls for option in control["options"]}

    assert "index_members" not in keys | ids | tokens
    # The four index predicates *name* the cell they read ("'sp500' in
    # index_members") — that is the audit sentence, not a projectable column.
    idx = next(control for control in controls if control["id"] == "idx")
    for option in idx["options"]:
        assert option["predicate"] == f"'{option['finviz']}' in index_members"

    catalog = json.loads(
        (ROOT / "app" / "engine" / "data" / "finviz_columns_116.json").read_text(encoding="utf-8")
    )
    assert len(catalog) == 116, "the catalog is frozen"
    assert "index_members" not in {column["key"] for column in catalog}


def test_the_document_is_deterministic() -> None:
    first = descriptive.document()
    second = descriptive.document()

    assert first == second
    assert json.dumps(first) == json.dumps(second)


def test_the_vendored_options_are_current() -> None:
    """The JSON is what the script generates from the probe — no hand edits.

    Same bargain as the schema artifact: regenerate with
    ``python scripts/descriptive-options.py``, and a probe change that nobody
    re-vendored fails here with a sentence.
    """

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "descriptive_options_script", ROOT / "scripts" / "descriptive-options.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.encoded(module.build()) == descriptive.DATA_PATH.read_text(encoding="utf-8")


def test_the_vendored_vocabularies_ship_with_the_package() -> None:
    """A JSON under ``app/screen/data`` loads from a checkout and 500s from a wheel.

    An editable install hides this: the file is read from the source tree. So the
    packaging entry is *pinned* here rather than hoped for — the plan names it as
    a review item, and a review item with a test is a rule.
    """

    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]
    assert "data/*.json" in package_data["app.screen"], (
        "app.screen's vendored JSON is not declared as package data"
    )
    for path in (descriptive.DATA_PATH, index_membership.DATA_PATH):
        assert path.is_file(), path
        assert path.parent == ROOT / "app" / "screen" / "data"


def test_an_absent_snapshot_disables_idx_with_its_own_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(index_membership, "load", lambda path=None: index_membership.Snapshot())
    monkeypatch.setattr(index_membership, "available", lambda: False)

    document = descriptive.document()
    published = {control["id"]: control for control in document["groups"][0]["controls"]}

    assert document["membership"]["available"] is False
    assert document["membership"]["as_of"] is None
    assert document["membership"]["counts"] == {}
    assert published["idx"]["available"] is False
    assert "index-membership snapshot" in published["idx"]["reason"]
    assert len(published["idx"]["options"]) == 4, "a disabled control keeps its options"
    for option in published["idx"]["options"]:
        assert option["available"] is False
        assert option["predicate"] is None
