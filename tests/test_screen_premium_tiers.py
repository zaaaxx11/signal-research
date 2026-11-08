"""Tier is a plan, not a source: premium features are built and gated, not refused.

The owner's rule, tested here end to end:

* every control and every option states a **tier** (``free``/``premium``) read
  from ``app/screen/data/entitlements.json``;
* an anonymous caller is ``free``; a premium feature asked on a free plan is a
  **403 ``premium_required``**, never an empty 200 and never a silent downgrade;
* the plan is presented by the request (``X-Plan``) or by an API key
  (``X-Api-Key``, matched by its sha256 in the config), and the body may only
  *narrow* it;
* flipping a feature from premium to free is a **config edit** — the last test
  writes a new table, points the module at it and proves the same request that
  was 403 now answers 200, with no code change.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.screen import after_hours, descriptive, entitlements
from tests import _screen as S

GATED = {
    "ah_close",
    "ah_change",
    "news_keywords",
    "etf_heldby",
    "sh_trades",
}


def select_body(filters: list[dict[str, Any]], **criteria: Any) -> dict[str, Any]:
    block: dict[str, Any] = {"limit": 5}
    block.update(criteria)
    return {"mode": "criteria", "region": "us", "criteria": {**block, "filters": filters}}


def priced_universe(count: int = 6) -> Any:
    """Rows for symbols the after-hours snapshot really priced.

    The enrichment under test is the real one (``after_hours.enrich_rows``), so
    the symbols have to be ones the vendored capture carries; the row price is a
    placeholder the scan would have supplied.
    """

    symbols = sorted(after_hours.load().rows)[:count]
    assert symbols, "the after-hours snapshot must carry prints for this suite"

    def build(_rows: Any, **_: Any) -> list[dict[str, Any]]:
        return [
            {"symbol": symbol, "name": symbol, "price": 100.0, "market_cap_usd": 1e9}
            for symbol in symbols
        ]

    return build, symbols


def by_key(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        control["key"]: control
        for group in document["groups"]
        for control in group["controls"]
        if control["key"] is not None
    }


def write_config(tmp_path: Path, *, features: dict[str, str], keys: dict[str, str] | None = None) -> Path:
    path = tmp_path / "entitlements.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "default": "free",
                "features": features,
                "api_keys": keys or {},
            }
        ),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# The document states a tier for everything
# ---------------------------------------------------------------------------


def test_every_control_and_option_states_one_of_the_two_tiers() -> None:
    document = descriptive.document()
    seen = {"controls": 0, "premium": 0, "options": 0, "premium_options": 0}
    for group in document["groups"]:
        for control in group["controls"]:
            assert control["tier"] in entitlements.TIERS, control["id"]
            seen["controls"] += 1
            seen["premium"] += control["tier"] == "premium"
            for option in control["options"]:
                assert option["tier"] == control["tier"], (control["id"], option["id"])
                seen["options"] += 1
                seen["premium_options"] += option["tier"] == "premium"
    # 86, not 85: `fa_fcfmargin` added the FCF Margin control to the
    # fundamental group. The five premium controls the source panel charges for
    # are unchanged.
    assert seen["controls"] == 86, seen
    assert seen["premium"] == 5, "the five controls the source panel charges for"
    assert seen["premium_options"] > 0


def test_the_five_gated_features_publish_the_configured_tier() -> None:
    document = descriptive.document()
    configured = document["entitlements"]["features"]
    assert set(configured) == GATED
    keys = by_key(document)
    for feature, tier in configured.items():
        assert keys[feature]["tier"] == tier, feature
    assert document["entitlements"]["plans"] == ["free", "premium"]
    assert document["entitlements"]["anonymous_plan"] == "free"


def test_a_premium_feature_the_code_answers_is_published_available() -> None:
    keys = by_key(descriptive.document())
    for feature in ("ah_close", "ah_change", "news_keywords", "etf_heldby"):
        control = keys[feature]
        assert control["tier"] == "premium", feature
        if control["available"]:
            assert control["reason"] is None, feature
            assert control["disposition"] in descriptive.ANSWERABLE_DISPOSITIONS, feature


def test_trade_counts_stay_refused_for_the_missing_feed_and_say_exactly_that() -> None:
    control = by_key(descriptive.document())["sh_trades"]
    assert control["tier"] == "premium", "the tier is still the panel's paid tier"
    assert control["available"] is False
    assert "no trade-count feed" in control["reason"]
    assert "Finra publishes short-sale volume, not trade counts" in control["reason"]
    assert control["disposition"] == "new_source"


# ---------------------------------------------------------------------------
# The gate: 403 without the plan, 200 with it
# ---------------------------------------------------------------------------


async def test_a_premium_filter_without_a_plan_is_403(tmp_path: Path, monkeypatch: Any) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        build, _symbols = priced_universe()
        from app.api import screens as screens_api

        monkeypatch.setattr(screens_api, "build_universe", build)
        response = await harness.client.post(
            "/screens/select",
            json=select_body([{"key": "ah_close", "op": "eq", "value": "ah_close:o1"}]),
        )

    assert response.status_code == 403, response.text
    body = response.json()
    assert body["error"] == "premium_required", body
    assert "ah_close" in body["errorMessage"]
    assert "premium" in body["errorMessage"]
    assert body["errorMessage"].count("empty page") == 1, (
        "the message must say why a 403 is not an empty 200"
    )


async def test_the_same_filter_with_the_plan_header_is_200(tmp_path: Path, monkeypatch: Any) -> None:
    build, symbols = priced_universe()
    expected = sum(1 for symbol in symbols if (after_hours.load().rows[symbol].close or 0) > 1.0)

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        from app.api import screens as screens_api

        monkeypatch.setattr(screens_api, "build_universe", build)
        response = await harness.client.post(
            "/screens/select",
            json=select_body([{"key": "ah_close", "op": "eq", "value": "ah_close:o1"}]),
            headers={"X-Plan": "premium"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == expected
    assert body["total"] > 0
    assert body["filters"] == [
        {"key": "ah_close", "field": None, "op": "eq", "value": "ah_close:o1"}
    ]


async def test_an_api_key_carrying_the_plan_is_200(tmp_path: Path, monkeypatch: Any) -> None:
    config = write_config(
        tmp_path,
        features={"etf_heldby": "premium"},
        keys={"sha256:" + hashlib.sha256(b"premium-key").hexdigest(): "premium"},
    )
    monkeypatch.setenv(entitlements.CONFIG_ENV, str(config))
    entitlements.clear()
    try:
        async with S.screen_client(tmp_path, monkeypatch) as harness:
            denied = await harness.client.post(
                "/screens/select",
                json=select_body([{"key": "etf_heldby", "op": "eq", "value": "heldby:spy"}]),
            )
            allowed = await harness.client.post(
                "/screens/select",
                json=select_body([{"key": "etf_heldby", "op": "eq", "value": "heldby:spy"}]),
                headers={"X-Api-Key": "premium-key"},
            )
    finally:
        entitlements.clear()

    assert denied.status_code == 403, denied.text
    assert denied.json()["error"] == "premium_required"
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["total"] >= 20, (
        "the fixture scanner's universe is a sample; every one of its rows that "
        "SPY holds must come back"
    )


async def test_a_recognised_key_wins_over_the_declaration(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A key that is in the config decides; the header cannot upgrade it."""

    config = write_config(
        tmp_path,
        features={"ah_close": "premium"},
        keys={"sha256:" + hashlib.sha256(b"free-key").hexdigest(): "free"},
    )
    monkeypatch.setenv(entitlements.CONFIG_ENV, str(config))
    entitlements.clear()
    try:
        async with S.screen_client(tmp_path, monkeypatch) as harness:
            response = await harness.client.post(
                "/screens/select",
                json=select_body([{"key": "ah_close", "op": "eq", "value": "ah_close:o1"}]),
                headers={"X-Api-Key": "free-key", "X-Plan": "premium"},
            )
    finally:
        entitlements.clear()

    assert response.status_code == 403, response.text


async def test_the_body_cannot_grant_itself_premium(tmp_path: Path, monkeypatch: Any) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select",
            json=select_body(
                [{"key": "sh_price", "op": "lt", "value": 50}], entitlement="premium"
            ),
        )

    assert response.status_code == 403, response.text
    body = response.json()
    assert body["error"] == "premium_required"
    assert "declares entitlement" in body["errorMessage"]


async def test_a_premium_caller_may_narrow_to_free(tmp_path: Path, monkeypatch: Any) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select",
            json=select_body(
                [{"key": "ah_close", "op": "eq", "value": "ah_close:o1"}],
                entitlement="free",
            ),
            headers={"X-Plan": "premium"},
        )

    assert response.status_code == 403, (
        response.text,
        "the narrower ask is honoured",
    )
    assert response.json()["error"] == "premium_required"


async def test_an_unknown_entitlement_is_422(tmp_path: Path, monkeypatch: Any) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select",
            json=select_body([{"key": "sh_price", "op": "lt", "value": 50}], entitlement="platinum"),
        )

    assert response.status_code == 422, response.text
    assert response.json()["error"] == "unknown_entitlement"


async def test_a_free_filter_is_unaffected_by_the_plan(tmp_path: Path, monkeypatch: Any) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        anonymous = await harness.client.post(
            "/screens/select", json=select_body([{"key": "sh_price", "op": "lt", "value": 1e9}])
        )
        premium = await harness.client.post(
            "/screens/select",
            json=select_body([{"key": "sh_price", "op": "lt", "value": 1e9}]),
            headers={"X-Plan": "premium"},
        )

    assert anonymous.status_code == 200 and premium.status_code == 200
    assert anonymous.json()["total"] == premium.json()["total"]


# ---------------------------------------------------------------------------
# The flip: a config edit, not a code change
# ---------------------------------------------------------------------------


async def test_flipping_a_feature_to_free_needs_only_the_config_file(
    tmp_path: Path, monkeypatch: Any
) -> None:
    build, symbols = priced_universe()
    body = select_body([{"key": "ah_close", "op": "eq", "value": "ah_close:o1"}])

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        from app.api import screens as screens_api

        monkeypatch.setattr(screens_api, "build_universe", build)
        before = await harness.client.post("/screens/select", json=body)
        before_document = await harness.client.get("/screens/filters")

        config = write_config(tmp_path, features={"ah_close": "free"})
        monkeypatch.setenv(entitlements.CONFIG_ENV, str(config))
        entitlements.clear()
        try:
            after = await harness.client.post("/screens/select", json=body)
            after_document = await harness.client.get("/screens/filters")
        finally:
            entitlements.clear()

    assert before.status_code == 403
    assert after.status_code == 200, after.text
    assert after.json()["total"] > 0

    def tier_of(document: dict[str, Any]) -> str:
        return next(
            control["tier"]
            for control in document["groups"][2]["controls"]
            if control["id"] == "ah_close"
        )

    assert tier_of(before_document.json()) == "premium"
    assert tier_of(after_document.json()) == "free", (
        "the document reads the table per build: one edit moves the panel"
    )
    assert before_document.headers["etag"] != after_document.headers["etag"]


def test_the_config_refuses_a_tier_it_does_not_have() -> None:
    with pytest.raises(entitlements.ConfigError):
        entitlements.parse({"default": "free", "features": {"ah_close": "platinum"}})
    with pytest.raises(entitlements.ConfigError):
        entitlements.parse({"default": "free", "features": {"not_a_control": "premium"}})


def test_an_unlisted_feature_takes_the_configured_default() -> None:
    config = entitlements.parse({"default": "free", "features": {}})
    assert config.tier("ah_close") == "free"
    config = entitlements.parse({"default": "premium", "features": {"ah_close": "free"}})
    assert config.tier("ah_change") == "premium"
    assert config.tier("ah_close") == "free"
