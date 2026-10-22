"""Phase-1 RED — resolver: intent anchor, universe expansion, honest "I don't know".

Gate (02 §5 Fase 1 #1-#3):

1. ``"banking in Indonesia"`` → ``venue=XIDX``, ``sector=banks``, ordered plan,
   every batch ≤ 100 symbols;
2. ``"AAPL, BBCA, NOTATICKER"`` → 2 resolved + ``unresolved=[{input:"NOTATICKER",
   reason:"not_found"}]`` and **no row** for NOTATICKER;
3. ``"top crypto"`` without a size → ``needs_narrowing``, ``plan=null``,
   ``quote_id=null``, HTTP 200 — ambiguity is an answer, not an error.

The reference master is ``tests/fixtures/reference_venue.json`` (2 venues,
39 instruments, 2 share-class ambiguity cases) and is injected through
``MEE_REFERENCE_PATH``; the tests use the same file as the oracle, so a
resolver that answers from a hardcoded list cannot pass.

Product decisions this file pins down (see ``10-phase1-red.md`` §3):

* a resolved text query returns a **materialised** universe: ``spec`` carries
  the inferred anchor and every instrument id comes from the fixture;
* an ambiguous query resolves **nothing** — the candidates are shown, never
  chosen;
* the plan is ordered by the documented stable key ``(venue, sector, mic,
  symbol)``, which degenerates to ``symbol`` ascending for a single-venue,
  single-sector universe.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests._phase1 import (
    BY_ID,
    INSTRUMENTS,
    REFERENCE,
    estimate_payload,
    ids_where,
    reference_client,
    resolve_payload,
    symbol_of,
    walk_strings,
)


def _body(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body.get("spec"), dict), f"resolve must echo a normalised spec, got {body!r}"
    for key in ("instruments", "unresolved", "narrowing"):
        assert key in body, f"resolve response is missing {key!r}: {sorted(body)}"
    return body


# ---------------------------------------------------------------------------
# Oracle guard — the fixture itself (green before and after Phase 1)
# ---------------------------------------------------------------------------


def test_reference_fixture_is_a_valid_two_venue_master() -> None:
    """The fixture is data the implementation reads, so it is asserted too."""

    ids = [row["instrument_id"] for row in INSTRUMENTS]
    assert len(ids) >= 30, f"fixture must seed at least 30 instruments, found {len(ids)}"
    assert len(ids) == len(set(ids)), "instrument_id must be unique"

    venue_mics = {venue["mic"] for venue in REFERENCE["venues"]}
    assert venue_mics == {"XNAS", "XIDX"}, venue_mics

    required = {"AAPL", "MSFT", "NVDA", "BBCA", "BBRI", "BMRI", "TLKM", "ASII"}
    assert required <= {row["symbol"] for row in INSTRUMENTS}, "fixture is missing a required symbol"

    venue_country = {venue["mic"]: venue["country"] for venue in REFERENCE["venues"]}
    for row in INSTRUMENTS:
        assert row["instrument_id"] == f"{row['mic']}:{row['symbol']}"
        assert row["mic"] in venue_mics
        assert row["status"] == "active"
        assert row["country"] == venue_country[row["mic"]], row["instrument_id"]
        assert row["aliases"], f"{row['instrument_id']} needs a name alias to be resolvable"

    assert len(ids_where(sector="banks", mic="XIDX")) >= 8, "the bank universe must be non-trivial"
    assert ids_where(sector="banks", mic="XNAS"), "a foreign-venue bank is needed to prove the veto"

    assert REFERENCE["ambiguous"], "at least one ambiguity case must be seeded"
    for case in REFERENCE["ambiguous"]:
        assert len(case["candidates"]) >= 2, case
        assert set(case["candidates"]) <= set(ids), case


# ---------------------------------------------------------------------------
# Gate 1 — "banking in Indonesia"
# ---------------------------------------------------------------------------


async def test_banking_in_indonesia_anchors_xidx_and_the_bank_sector(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with reference_client(tmp_path, monkeypatch) as client:
        body = _body(await client.post("/markets/resolve", json=resolve_payload("banking in Indonesia")))

    spec = body["spec"]
    assert spec["venue"] == "XIDX", f"Indonesia must anchor the venue, got {spec!r}"
    assert str(spec.get("sector", "")).lower() == "banks", spec

    expected = ids_where(sector="banks", mic="XIDX")
    got = {row["instrument_id"] for row in body["instruments"]}
    assert got == expected, f"resolved {sorted(got)} != fixture banks {sorted(expected)}"

    for row in body["instruments"]:
        assert row["mic"] == "XIDX", f"a US listing leaked into an Indonesian universe: {row!r}"
        assert row["symbol"] == symbol_of(row["instrument_id"])
        assert row["name"] == BY_ID[row["instrument_id"]]["name"], "the master name must be echoed"
        assert 0.0 < float(row["confidence"]) <= 1.0, row

    assert "XNAS:JPM" not in got, "JPMorgan is a bank but not an Indonesian listing"
    assert body["unresolved"] == []
    assert body["narrowing"]["required"] is False


async def test_banking_in_indonesia_plan_is_ordered_and_capped(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Gate 1's "ordered plan, batch ≤ 100".

    ``/markets/resolve`` has no plan in its contract (02 §3.2), so the plan is
    read from ``/markets/estimate`` for the same spec — the plan a quote freezes.
    """

    async with reference_client(tmp_path, monkeypatch) as client:
        resolved = _body(
            await client.post("/markets/resolve", json=resolve_payload("banking in Indonesia"))
        )
        response = await client.post("/markets/estimate", json=estimate_payload())
        assert response.status_code == 200, response.text
        plan = response.json()["plan"]

    assert plan is not None, "a spec that resolved must have a plan"
    batches = plan["batches"]
    assert [batch["batch_index"] for batch in batches] == list(range(len(batches)))

    symbols: list[str] = []
    for batch in batches:
        assert batch["symbols_expected"] <= 100, batch
        slice_symbols = batch["universe_slice"]["symbols"]
        assert slice_symbols, (
            "a quoteable plan must materialise symbols: QuotePlanV1.universe.symbols "
            "cannot be frozen from a filter-only slice"
        )
        assert len(slice_symbols) == batch["symbols_expected"], batch
        assert len(slice_symbols) <= 100, batch
        symbols.extend(slice_symbols)

    assert symbols == sorted(symbols), f"plan symbols are not in stable order: {symbols}"
    assert symbols == sorted(symbol_of(row["instrument_id"]) for row in resolved["instruments"])


# ---------------------------------------------------------------------------
# Gate 2 — mixed list: 2 resolved, 1 not_found, zero invented rows
# ---------------------------------------------------------------------------


async def test_watchlist_reports_two_resolved_and_one_not_found(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with reference_client(tmp_path, monkeypatch) as client:
        body = _body(await client.post("/markets/resolve", json=resolve_payload("AAPL, BBCA, NOTATICKER")))

    assert {row["instrument_id"] for row in body["instruments"]} == {"XNAS:AAPL", "XIDX:BBCA"}
    assert len(body["instruments"]) == 2, body["instruments"]

    unresolved = body["unresolved"]
    assert len(unresolved) == 1, f"expected exactly one unresolved input, got {unresolved!r}"
    assert unresolved[0]["input"] == "NOTATICKER"
    assert unresolved[0]["reason"] == "not_found"


async def test_no_invented_instrument_for_the_unknown_symbol(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """``test_no_invented_instrument`` (02 §5 Fase 1 gate 2): absent means absent."""

    async with reference_client(tmp_path, monkeypatch) as client:
        body = _body(await client.post("/markets/resolve", json=resolve_payload("AAPL, BBCA, NOTATICKER")))

    assert "NOTATICKER" not in " ".join(walk_strings(body["instruments"])), (
        "an unresolved input must not appear in instruments[] — no placeholder row, no null-id row"
    )
    for row in body["instruments"]:
        assert row["instrument_id"] in BY_ID, f"{row['instrument_id']} is not in the reference master"

    assert len(body["instruments"]) == len({row["instrument_id"] for row in body["instruments"]})


async def test_unresolvable_gibberish_is_reported_as_not_found(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with reference_client(tmp_path, monkeypatch) as client:
        body = _body(await client.post("/markets/resolve", json=resolve_payload("zzqx-wibble-9147")))

    assert body["instruments"] == []
    assert [entry["reason"] for entry in body["unresolved"]] == ["not_found"]


# ---------------------------------------------------------------------------
# Gate 3 — "top crypto" without a size is a question, not an error
# ---------------------------------------------------------------------------


async def test_top_crypto_without_a_size_is_a_narrowing_answer_not_an_error(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with reference_client(tmp_path, monkeypatch) as client:
        body = _body(await client.post("/markets/resolve", json=resolve_payload("top crypto")))

    assert body["instruments"] == [], "an unsized query must not pick a universe"
    assert body["narrowing"]["required"] is True
    assert len(body["narrowing"]["questions"]) >= 1, body["narrowing"]
    assert body.get("plan") is None
    assert body.get("quote_id") is None


# ---------------------------------------------------------------------------
# Ambiguity — candidates are shown, never chosen (03 §3.1 A3, §3.4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", REFERENCE["ambiguous"], ids=lambda case: case["query"])
async def test_ambiguous_query_returns_candidates_not_a_choice(
    case: dict[str, Any], tmp_path: Path, monkeypatch: Any
) -> None:
    async with reference_client(tmp_path, monkeypatch) as client:
        body = _body(await client.post("/markets/resolve", json=resolve_payload(case["query"])))

    assert body["instruments"] == [], (
        f"{case['query']!r} matches {case['candidates']}; returning a row would be a coin flip"
    )
    assert len(body["unresolved"]) == 1, body["unresolved"]
    assert body["unresolved"][0]["input"] == case["query"]
    assert body["unresolved"][0]["reason"] == "ambiguous"

    rendered = " ".join(walk_strings(body))
    for candidate in case["candidates"]:
        assert candidate in rendered, f"{candidate} must be offered as an option, not hidden"
    assert len(case["candidates"]) <= 3, "clarification shows at most 3 options (03 §3.4)"


# ---------------------------------------------------------------------------
# Contract edge — unknown knobs stay 422 on the new routes too
# ---------------------------------------------------------------------------


async def test_resolve_rejects_an_unknown_knob_with_422(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with reference_client(tmp_path, monkeypatch) as client:
        payload = resolve_payload("AAPL")
        payload["bogus_knob"] = True
        response = await client.post("/markets/resolve", json=payload)

    assert response.status_code == 422, response.text
    assert "bogus_knob" in response.text
    assert json.dumps(response.json())  # a JSON body, not an HTML traceback
