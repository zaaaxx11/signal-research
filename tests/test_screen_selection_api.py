"""``POST /screens/select`` and ``GET /screens/select/schema`` over the ASGI app.

The route's job is small and easy to get subtly wrong, so each test pins one of
those ways:

* a **criteria** selection answers ``total`` (every post-filter match) next to
  ``returned`` (the page), and it must read the universe rather than the 20 rows
  the browse display stored — a filter that matches the 23rd issuer has to find
  it;
* **symbols** resolves an explicit ticker list inside the snapshot it names, in
  request order, and names the tickers it could not find;
* every rejection is the same ``{error, errorMessage}`` envelope as the rest of
  the API, with a code a client can switch on — an unknown field, an unknown
  operator and a Finviz key with no light column are 422s, never an ignored
  filter;
* a ``limit`` above the display window still serves 20 rows and says so;
* the schema route serves the document that ships in ``schemas/``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests import _screen as S

FIXTURE = S.FIXTURES / "selection_request.json"

#: Every key a successful selection body carries — one envelope for both modes.
ENVELOPE_KEYS = {
    "mode",
    "region",
    "source",
    "as_of",
    "snapshot_id",
    "sort",
    "limit",
    "universe_total",
    "total",
    "returned",
    "filters",
    "symbols",
    "missing",
    "rows",
}

#: The US tab after filter+dedupe for the **browse** contract (100M floor,
#: ``subtype=common``): hand-counted from the scanner fixture.
US_UNIVERSE_TOTAL = 23

#: The universe a **criteria** selection runs over: the same scan without the
#: 100M floor and without the ``subtype=common`` gate, so the sub-floor common
#: row (TINY), the REIT (O), the preferred (PFD) and the warrant (WAR) are part
#: of the market the filters address. Funds (SPY, VNQ) and OTC (XYZQ) stay out.
US_CRITERIA_UNIVERSE = 27

US_TECHNOLOGY = ["AAPL", "NVDA", "MSFT", "ASML", "ORCL", "AMD", "CRM"]


def payload() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def criteria(*filters: dict[str, Any], sort: str = "market_cap", limit: int = 20) -> dict[str, Any]:
    return {
        "mode": "criteria",
        "region": "us",
        "criteria": {"filters": list(filters), "sort": sort, "limit": limit},
    }


def symbols(snapshot_id: str, tickers: list[str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "mode": "symbols",
        "symbols": tickers,
        "source": "tradingview",
        "snapshot_id": snapshot_id,
    }
    body.update(overrides)
    return body


def assert_envelope(response: Any, *, status: int) -> dict[str, Any]:
    """A failure is ``{error, errorMessage}`` and nothing else (02 §3.2)."""

    assert response.status_code == status, f"{response.status_code}: {response.text}"
    body = response.json()
    assert set(body) == {"error", "errorMessage"}, (
        f"the error envelope is exactly error/errorMessage, got {sorted(body)}"
    )
    assert isinstance(body["error"], str) and body["error"], body
    assert isinstance(body["errorMessage"], str) and body["errorMessage"], body
    return body


# ---------------------------------------------------------------------------
# Criteria
# ---------------------------------------------------------------------------


async def test_select_answers_the_page_and_the_post_filter_total(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post("/screens/select", json=payload())

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == ENVELOPE_KEYS, f"the envelope changed shape: {sorted(body)}"
    assert body["mode"] == "criteria"
    assert body["region"] == "us"
    assert body["source"] == "tradingview"
    assert body["snapshot_id"] is None, "a criteria query is not pinned to a stored snapshot"
    assert body["sort"] == "market_cap"
    assert body["limit"] == 20
    assert body["universe_total"] == US_CRITERIA_UNIVERSE, (
        "a criteria selection answers over the whole market, not the browse subset"
    )
    assert body["total"] == 7, "total counts every match, not the rows on the page"
    assert body["returned"] == 7
    assert body["missing"] == [] and body["symbols"] == []
    assert [row["symbol"] for row in body["rows"]] == US_TECHNOLOGY
    assert body["filters"][0]["key"] == "cap"
    assert body["filters"][0]["field"] == "market_cap_usd"
    S.parse_dt(body["as_of"])
    for row in body["rows"]:
        assert row["as_of"] == body["as_of"], "each row dates itself with the selection's as_of"


async def test_a_match_outside_the_displayed_twenty_is_found(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """CRM is the 23rd US issuer by market cap: the browse page cannot see it."""

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        browse = await harness.create_screen()
        response = await harness.client.post(
            "/screens/select",
            json=criteria({"field": "symbol", "op": "eq", "value": "CRM"}),
        )

    assert "CRM" not in [row["symbol"] for row in browse["rows"]], (
        "the fixture no longer puts CRM outside the top 20; pick another symbol"
    )
    body = response.json()
    assert response.status_code == 200, response.text
    assert body["total"] == 1, (
        "the selection must run over the universe, not the 20 stored display rows "
        f"(total={body['total']})"
    )
    assert [row["symbol"] for row in body["rows"]] == ["CRM"]
    assert body["universe_total"] == US_CRITERIA_UNIVERSE


async def test_a_limit_above_twenty_still_serves_twenty_rows(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post("/screens/select", json=criteria(limit=100))

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["limit"] == 20, (
        f"the display window is 20 rows and the envelope must say so, got {body['limit']}"
    )
    assert body["returned"] == 20 and len(body["rows"]) == 20
    assert body["total"] == US_CRITERIA_UNIVERSE, (
        "clamping the page must not clamp the total: the whole criteria universe "
        "matches an empty filter"
    )


async def test_the_same_selection_twice_answers_the_same_page(
    tmp_path: Path, monkeypatch: Any
) -> None:
    body = criteria({"key": "sec", "op": "eq", "value": "Technology"}, limit=5)
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        first = await harness.client.post("/screens/select", json=body)
        second = await harness.client.post("/screens/select", json=body)

    assert first.status_code == second.status_code == 200
    left, right = first.json(), second.json()

    def cells(document: dict[str, Any]) -> list[dict[str, Any]]:
        """The rows without the read timestamp — the only thing a clock may change."""

        return [
            {key: value for key, value in row.items() if key != "as_of"}
            for row in document["rows"]
        ]

    assert cells(left) == cells(right), "the page must not depend on when it was read"
    assert [row["symbol"] for row in left["rows"]] == [row["symbol"] for row in right["rows"]]
    assert (left["total"], left["returned"], left["universe_total"]) == (
        right["total"],
        right["returned"],
        right["universe_total"],
    )


# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------


async def test_symbols_resolve_inside_the_named_snapshot(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        screen = await harness.create_screen()
        response = await harness.client.post(
            "/screens/select",
            json=symbols(screen["screen_id"], ["NVDA", "AAPL", "NOTREAL"]),
        )
        requests = S.provider_requests(harness.web)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["mode"] == "symbols"
    assert body["region"] == "us", "the region comes from the snapshot, not from a knob"
    assert body["snapshot_id"] == screen["screen_id"]
    assert body["source"] == "tradingview"
    assert body["sort"] is None, "a ticker list is not a ranking"
    assert [row["symbol"] for row in body["rows"]] == ["NVDA", "AAPL"], (
        f"request order must be preserved, got {[row['symbol'] for row in body['rows']]}"
    )
    assert body["missing"] == ["NOTREAL"], "an unknown ticker is named, never invented"
    assert body["total"] == 2 and body["returned"] == 2
    rows = body["rows"]
    assert all(row["as_of"] == body["as_of"] for row in rows), (
        "the rows are read now and dated by the response's as_of"
    )
    assert S.parse_dt(body["as_of"]) >= S.parse_dt(screen["as_of"]), (
        "a ticker list is resolved against the snapshot's universe, not replayed "
        "from its stored (older) prices"
    )
    assert requests == 2, "one scan for the browse snapshot, one for the selection"


async def test_an_unknown_snapshot_is_a_404_before_any_scan(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select", json=symbols("scr_us_market_cap_missing", ["AAPL"])
        )
        requests = S.provider_requests(harness.web)

    body = assert_envelope(response, status=404)
    assert body["error"] == "snapshot_not_found"
    assert "scr_us_market_cap_missing" in body["errorMessage"]
    assert requests == 0, "a snapshot that does not exist must not buy a scan"


async def test_a_snapshot_built_from_another_source_is_refused(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        foreign = await harness.runtime.screen_repository.save(
            region="us",
            sort="market_cap",
            as_of=datetime.now(UTC),
            total_count=0,
            rows=[],
            source="another-feed",
        )
        response = await harness.client.post(
            "/screens/select", json=symbols(foreign["id"], ["AAPL"])
        )
        requests = S.provider_requests(harness.web)

    body = assert_envelope(response, status=422)
    assert body["error"] == "source_mismatch"
    assert "another-feed" in body["errorMessage"], (
        "the refusal must say which source the snapshot really came from"
    )
    assert requests == 0


# ---------------------------------------------------------------------------
# Rejections — a filter is never ignored
# ---------------------------------------------------------------------------


async def test_an_unknown_field_is_a_422_in_the_api_envelope(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select",
            json=criteria({"field": "pe_ratio", "op": "gte", "value": 5}),
        )
        requests = S.provider_requests(harness.web)

    body = assert_envelope(response, status=422)
    assert body["error"] == "invalid_filter"
    assert "unknown filter field" in body["errorMessage"]
    assert "pe_ratio" in body["errorMessage"]
    assert requests == 0, "an invalid filter must be refused before the scan"


async def test_an_unknown_operator_is_a_422(tmp_path: Path, monkeypatch: Any) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select",
            json=criteria({"field": "price", "op": "approximately", "value": 5}),
        )

    body = assert_envelope(response, status=422)
    assert body["error"] == "invalid_filter"
    assert "unknown filter operator" in body["errorMessage"]


async def test_an_unmapped_finviz_key_is_refused_by_name_not_ignored(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select", json=criteria({"key": "fa_divgrowth", "op": "lte", "value": 10})
        )
        requests = S.provider_requests(harness.web)

    body = assert_envelope(response, status=422)
    assert body["error"] == "unavailable_filter_key"
    message = body["errorMessage"]
    assert "fa_divgrowth" in message and "Dividend Growth" in message
    assert "cannot be applied" in message
    assert "refused instead of ignored" in message
    assert requests == 0, "the refusal must not spend a scan on a filter it cannot apply"


async def test_a_payload_mixing_the_two_modes_is_a_422(
    tmp_path: Path, monkeypatch: Any
) -> None:
    body = payload()
    body["symbols"] = ["AAPL"]
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post("/screens/select", json=body)

    error = assert_envelope(response, status=422)
    assert error["error"] == "invalid_selection"
    assert "mode 'criteria'" in error["errorMessage"]


async def test_a_limit_of_zero_is_a_422(tmp_path: Path, monkeypatch: Any) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post("/screens/select", json=criteria(limit=0))

    error = assert_envelope(response, status=422)
    assert error["error"] == "invalid_selection"
    assert "limit" in error["errorMessage"]


async def test_a_symbol_list_without_a_snapshot_is_a_422(
    tmp_path: Path, monkeypatch: Any
) -> None:
    body = symbols("scr_ignored", ["AAPL"])
    del body["snapshot_id"]
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post("/screens/select", json=body)

    error = assert_envelope(response, status=422)
    assert "snapshot_id" in error["errorMessage"]


async def test_a_body_that_is_not_json_is_a_422_in_the_same_envelope(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select",
            content=b"{not json",
            headers={"content-type": "application/json"},
        )

    error = assert_envelope(response, status=422)
    assert error["error"] == "invalid_json"


# ---------------------------------------------------------------------------
# The schema route
# ---------------------------------------------------------------------------


async def test_the_schema_route_serves_the_repo_artifact(
    tmp_path: Path, monkeypatch: Any
) -> None:
    artifact = Path(__file__).resolve().parents[1] / "schemas" / "stock_selection_request_v1.json"
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.get("/screens/select/schema")

    assert response.status_code == 200, response.text
    assert response.json() == json.loads(artifact.read_text(encoding="utf-8")), (
        "the served schema and schemas/stock_selection_request_v1.json must be one document"
    )
    served = response.json()
    assert served["x-filter-key-count"] == 82
    assert len(served["x-filter-keys"]) == 82
    mapped = [key for key, entry in served["x-filter-keys"].items() if entry["field"]]
    assert len(mapped) == 42, (
        "the published schema must name every key that filters a parity row, the "
        "three derived columns and the three joined fundamentals included"
    )
    assert "fa_pe" in mapped and "ta_beta" in mapped and "ta_sma50" in mapped
    assert "industry" in mapped and "fa_div" in mapped and "ta_gap" in mapped
    assert "an_recom" in mapped and "ta_changeopen" in mapped and "ta_highlow52w" in mapped
    assert "fa_fpe" in mapped and "fa_ps" in mapped and "fa_payoutratio" in mapped
