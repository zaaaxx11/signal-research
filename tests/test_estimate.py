"""Phase-1 RED — estimate: never exact, ordered bounds, deterministic, target-free.

Gate (02 §5 Fase 1 #5-#6):

* two estimates for the same spec produce a **byte-identical** plan (the quote
  freezes it, so drift is a lie the client cannot see);
* ``exact is False`` always; ``low ≤ mid ≤ high``;
* the estimate for ``target_rows=100`` and ``target_rows=5000`` is the **same**
  — an estimate that depends on the target is a quote that depends on the
  customer's optimism.

``plan.target_rows`` legitimately echoes the request, so the 100-vs-5000 case
compares the ``estimate`` document (the gate's subject), not the whole plan.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tests._phase1 import (
    canonical_json,
    estimate_payload,
    plan_symbols,
    quote_plan,
    reference_client,
)
from tests._support import COLUMNS

_PLAN_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


def _estimate(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    body = response.json()
    for key in ("estimate", "plan", "narrowing", "quote_id", "quote_expires_at"):
        assert key in body, f"estimate response is missing {key!r}: {sorted(body)}"
    return body


# ---------------------------------------------------------------------------
# Gate 6 — exact is always false, bounds are ordered
# ---------------------------------------------------------------------------


async def test_estimate_is_never_exact_and_bounds_are_ordered(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with reference_client(tmp_path, monkeypatch) as client:
        body = _estimate(await client.post("/markets/estimate", json=estimate_payload()))

    estimate = body["estimate"]
    assert estimate["exact"] is False, (
        "exact must be the JSON boolean false on every response — a projection is not a measurement"
    )
    for key in ("low", "mid", "high"):
        assert isinstance(estimate[key], (int, float)) and not isinstance(estimate[key], bool), estimate
    assert 1 <= estimate["low"] <= estimate["mid"] <= estimate["high"], estimate
    assert 0.0 < estimate["confidence"] <= 1.0, estimate
    assert isinstance(estimate["basis"], list) and estimate["basis"], (
        "an estimate without basis[] is a number nobody can audit"
    )

    assert body["plan"] is not None
    assert body["quote_id"], "a resolved spec must be quotable"
    assert body["quote_expires_at"], "a quote without an expiry can be replayed forever"


# ---------------------------------------------------------------------------
# Gate 5 — determinism, byte for byte
# ---------------------------------------------------------------------------


async def test_estimate_is_deterministic_byte_for_byte(
    tmp_path: Path, monkeypatch: Any
) -> None:
    payload = estimate_payload()
    async with reference_client(tmp_path, monkeypatch) as client:
        first = _estimate(await client.post("/markets/estimate", json=payload))
        second = _estimate(await client.post("/markets/estimate", json=payload))

    assert canonical_json(first["plan"]) == canonical_json(second["plan"]), (
        "same spec, different plan: a frozen quote could never be replayed"
    )
    assert first["estimate"] == second["estimate"]

    assert first["quote_id"] != second["quote_id"], "each estimate issues its own single-use quote"
    hash_first = quote_plan(tmp_path, first["quote_id"])["plan_hash"]
    hash_second = quote_plan(tmp_path, second["quote_id"])["plan_hash"]
    assert _PLAN_HASH.fullmatch(hash_first), hash_first
    assert hash_first == hash_second, (
        "plan_hash is the sha256 of the frozen plan; identical plans must share it"
    )


# ---------------------------------------------------------------------------
# Gate 6 — the estimate does not depend on target_rows
# ---------------------------------------------------------------------------


async def test_estimate_does_not_depend_on_target_rows(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with reference_client(tmp_path, monkeypatch) as client:
        small = _estimate(await client.post("/markets/estimate", json=estimate_payload(target_rows=100)))
        large = _estimate(await client.post("/markets/estimate", json=estimate_payload(target_rows=5000)))

    assert small["estimate"] == large["estimate"], (
        "the estimate changed with target_rows; a client could buy a bigger number by asking for one"
    )
    assert small["estimate"]["exact"] is False
    assert large["estimate"]["exact"] is False
    assert small["plan"] is not None and large["plan"] is not None


# ---------------------------------------------------------------------------
# Plan ↔ quote coherence
# ---------------------------------------------------------------------------


async def test_plan_batches_are_materialised_ordered_and_capped(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with reference_client(tmp_path, monkeypatch) as client:
        body = _estimate(await client.post("/markets/estimate", json=estimate_payload()))

    plan = body["plan"]
    assert plan["mode"] == "balanced"
    batches = plan["batches"]
    assert batches, "an empty plan cannot be quoted"
    assert [batch["batch_index"] for batch in batches] == list(range(len(batches)))

    for batch in batches:
        symbols = batch["universe_slice"]["symbols"]
        assert symbols, "a quote must freeze explicit symbols, not a filter it cannot replay"
        assert len(symbols) == batch["symbols_expected"], batch
        assert batch["symbols_expected"] <= 100, batch
        assert batch["max_rows"] >= batch["symbols_expected"], batch

    symbols = plan_symbols(plan)
    assert len(symbols) == len(set(symbols)), f"a plan may not list an instrument twice: {symbols}"
    assert symbols == sorted(symbols), f"plan order is not stable: {symbols}"

    frozen = quote_plan(tmp_path, body["quote_id"])
    assert frozen["universe"]["symbols"] == symbols, "the quote must freeze the plan it estimated"
    assert frozen["columns_version"] == "v1"
    assert frozen["retention_days"] == 7
    assert frozen["fields"], "a frozen plan without fields[] cannot describe an export"
    assert set(frozen["fields"]) <= set(COLUMNS), sorted(set(frozen["fields"]) - set(COLUMNS))


# ---------------------------------------------------------------------------
# Narrowing — no plan, no quote, still HTTP 200
# ---------------------------------------------------------------------------


async def test_top_crypto_estimate_returns_narrowing_without_plan_or_quote(
    tmp_path: Path, monkeypatch: Any
) -> None:
    payload = estimate_payload(
        market={"kind": "text", "text": "top crypto"}, target_rows=None
    )
    async with reference_client(tmp_path, monkeypatch) as client:
        body = _estimate(await client.post("/markets/estimate", json=payload))

    assert body["narrowing"]["required"] is True
    assert len(body["narrowing"]["questions"]) >= 1, body["narrowing"]
    assert body["plan"] is None, "there is no plan for a universe that was never chosen"
    assert body["quote_id"] is None, "a quote without a plan cannot be honoured"
    assert body["quote_expires_at"] is None
    assert body["estimate"] is None, "a row range over an unknown universe would be a fabricated number"


# ---------------------------------------------------------------------------
# Contract edge
# ---------------------------------------------------------------------------


async def test_estimate_rejects_an_unknown_knob_with_422(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with reference_client(tmp_path, monkeypatch) as client:
        payload = estimate_payload()
        payload["bogus_knob"] = 1
        response = await client.post("/markets/estimate", json=payload)

    assert response.status_code == 422, response.text
    assert "bogus_knob" in response.text
