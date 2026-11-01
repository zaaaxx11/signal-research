"""Held by: a fund's own published holdings, and what happens when it is missing.

QQQ's file did not answer (Invesco serves an HTML bot wall instead of the CSV),
so the honest test is the one that pins *both* halves: SPY/DIA/IWM select their
members, and QQQ is refused with the measured reason — never filled from an
index list dressed as a holding.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.screen import buckets, descriptive, etf_holdings
from app.screen.selection import SelectionError, parse_selection
from tests import _screen as S


def snapshot_document(
    *,
    spy: list[str] | None = None,
    qqq_error: str = "HTTP 200 answered an HTML page, not the CSV the download promises (a bot wall)",
) -> dict[str, Any]:
    return {
        "as_of": "2026-11-01",
        "captured_at": "2026-11-01T20:00:00+00:00",
        "funds": {
            "SPY": {
                "label": "SPDR S&P 500 ETF Trust",
                "url": "https://www.ssga.com/spy.xlsx",
                "status": 200,
                "retrieved_at": "2026-11-01T20:00:00+00:00",
                "members": spy if spy is not None else ["AAPL", "MSFT", "BRK-B"],
            },
            "QQQ": {
                "label": "Invesco QQQ Trust",
                "url": "https://www.invesco.com/qqq.csv",
                "status": 200,
                "retrieved_at": "2026-11-01T20:00:00+00:00",
                "members": [],
                "error": qqq_error,
            },
        },
    }


def use_snapshot(tmp_path: Path, monkeypatch: Any, document: dict[str, Any]) -> None:
    path = tmp_path / "etf_holdings.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setenv(etf_holdings.PATH_ENV, str(path))
    etf_holdings.clear()


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    etf_holdings.clear()
    yield
    etf_holdings.clear()


def body(value: str) -> dict[str, Any]:
    return {
        "mode": "criteria",
        "region": "us",
        "criteria": {"filters": [{"key": "etf_heldby", "op": "eq", "value": value}], "limit": 10},
    }


# ---------------------------------------------------------------------------
# The vendored snapshot
# ---------------------------------------------------------------------------


def test_the_shipped_snapshot_stamps_every_fund_and_its_source() -> None:
    snapshot = etf_holdings.load()
    assert snapshot.as_of, "a holdings list without a stamp is a claim"
    counts = snapshot.counts()
    assert counts["SPY"] >= 450, counts
    assert counts["DIA"] == 30, counts
    assert counts["IWM"] >= 1500, counts
    assert counts["QQQ"] == 0, "the Invesco download did not answer; it is not invented"
    for symbol, fund in snapshot.funds.items():
        assert fund.url, symbol
        assert fund.status is not None, symbol
        assert fund.retrieved_at, symbol


def test_the_document_offers_every_fund_with_its_own_availability() -> None:
    control = next(
        control
        for group in descriptive.document()["groups"]
        if group["id"] == "etf"
        for control in group["controls"]
    )
    assert control["id"] == "etf_heldby" and control["tier"] == "premium"
    assert [option["id"] for option in control["options"]] == [
        "heldby:spy",
        "heldby:qqq",
        "heldby:dia",
        "heldby:iwm",
    ]
    by_id = {option["id"]: option for option in control["options"]}
    assert by_id["heldby:spy"]["available"] is True
    assert by_id["heldby:qqq"]["available"] is False
    assert "Invesco" in by_id["heldby:qqq"]["reason"] or "QQQ" in by_id["heldby:qqq"]["reason"]
    assert "bot wall" in by_id["heldby:qqq"]["reason"]
    assert "503 names" in by_id["heldby:spy"]["predicate"]


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------


def test_held_by_selects_the_funds_members(tmp_path: Path, monkeypatch: Any) -> None:
    use_snapshot(tmp_path, monkeypatch, snapshot_document())
    found = buckets.bucket("heldby:spy")
    context = buckets.Context.at("2026-11-01")

    assert found.key == "etf_heldby"
    assert found.predicate({"symbol": "AAPL"}, context) is True
    assert found.predicate({"symbol": "MSFT"}, context) is True
    assert found.predicate({"symbol": "BRK.B"}, context) is True, "BRK-B folds to BRK.B"
    assert found.predicate({"symbol": "TSLA"}, context) is False
    assert found.predicate({}, context) is False


def test_an_unknown_fund_is_refused_by_name(tmp_path: Path, monkeypatch: Any) -> None:
    use_snapshot(tmp_path, monkeypatch, snapshot_document())
    with pytest.raises(buckets.UnknownBucket) as caught:
        buckets.bucket("heldby:vti")
    assert caught.value.code == "unknown_bucket"
    assert "SPY" in str(caught.value)


def test_a_fund_without_a_file_is_unavailable_with_the_measured_reason(
    tmp_path: Path, monkeypatch: Any
) -> None:
    use_snapshot(tmp_path, monkeypatch, snapshot_document())
    with pytest.raises(buckets.UnavailableBucket) as caught:
        buckets.bucket("heldby:qqq")
    assert caught.value.code == "unavailable_bucket"
    assert "bot wall" in str(caught.value)


def test_the_token_reaches_the_parser(tmp_path: Path, monkeypatch: Any) -> None:
    use_snapshot(tmp_path, monkeypatch, snapshot_document())
    request = parse_selection(body("spy"), plan="premium")
    assert request.filters[0].value == "heldby:spy"
    with pytest.raises(SelectionError) as caught:
        parse_selection(body("vti"), plan="premium")
    assert caught.value.code == "unknown_bucket"


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------


async def test_held_by_is_premium_then_answers(tmp_path: Path, monkeypatch: Any) -> None:
    use_snapshot(tmp_path, monkeypatch, snapshot_document())
    rows = [
        {"symbol": "AAPL", "name": "Apple", "price": 200.0, "market_cap_usd": 3e12},
        {"symbol": "TSLA", "name": "Tesla", "price": 400.0, "market_cap_usd": 1e12},
    ]

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        from app.api import screens as screens_api

        monkeypatch.setattr(screens_api, "build_universe", lambda _rows, **_: list(rows))
        denied = await harness.client.post("/screens/select", json=body("heldby:spy"))
        allowed = await harness.client.post(
            "/screens/select", json=body("heldby:spy"), headers={"X-Plan": "premium"}
        )
        missing = await harness.client.post(
            "/screens/select", json=body("heldby:qqq"), headers={"X-Plan": "premium"}
        )

    assert denied.status_code == 403
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["total"] == 1
    assert allowed.json()["rows"][0]["symbol"] == "AAPL"
    assert missing.status_code == 422, missing.text
    assert missing.json()["error"] == "unavailable_bucket"
    assert "bot wall" in missing.json()["errorMessage"]
