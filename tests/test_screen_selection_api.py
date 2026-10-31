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
* a ``limit`` above the panel (100 rows) is clamped and the envelope says so;
* the schema route serves the document that ships in ``schemas/``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
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
    "columns",
    # The panel budget report (F9): bytes shipped, whether the body was
    # truncated, which fields went and how many rows were cut from the tail.
    "budget",
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


def criteria(*filters: dict[str, Any], sort: str = "market_cap", limit: int = 20, offset: int = 0) -> dict[str, Any]:
    return {
        "mode": "criteria",
        "region": "us",
        "criteria": {"filters": list(filters), "sort": sort, "limit": limit, "offset": offset},
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


async def test_fill_wide_is_opt_in_and_does_not_change_the_selection(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A wide page may add display cells, but never rows or their order."""

    module = S.screens_api()
    calls: list[list[dict[str, Any]]] = []

    class FakeFiller:
        def __init__(self, adapter: Any = None) -> None:
            self.adapter = adapter

        async def fill(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            calls.append(rows)
            return [{**row, "employees": 42} for row in rows]

    monkeypatch.setattr(module, "WideFiller", FakeFiller)
    body = criteria(
        {"key": "sec", "op": "eq", "value": "Technology"}, limit=5
    )
    body["fill_wide"] = True
    body["view"] = "all"

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post("/screens/select", json=body)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 7, "enrichment must not change the match count"
    assert [row["symbol"] for row in payload["rows"]] == US_TECHNOLOGY[:5]
    assert all(row["employees"] == 42 for row in payload["rows"])
    assert len(calls) == 1 and len(calls[0]) == 5


async def test_without_fill_wide_no_stock_page_is_requested(
    tmp_path: Path, monkeypatch: Any
) -> None:
    module = S.screens_api()
    calls: list[Any] = []

    class FakeFiller:
        def __init__(self, adapter: Any = None) -> None:
            self.adapter = adapter

        async def fill(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            calls.append(rows)
            return rows

    monkeypatch.setattr(module, "WideFiller", FakeFiller)

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post("/screens/select", json=criteria(limit=5))

    assert response.status_code == 200, response.text
    assert calls == [], "the default selection must not spend a page request"


async def test_a_limit_above_the_panel_serves_the_whole_fixture_universe(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post("/screens/select", json=criteria(limit=100))

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["limit"] == 100, (
        f"the panel is 100 rows and the envelope must say so, got {body['limit']}"
    )
    assert body["returned"] == US_CRITERIA_UNIVERSE and len(body["rows"]) == US_CRITERIA_UNIVERSE, (
        "the fixture's criteria universe (27) is smaller than the panel, so one "
        "request returns all of it"
    )
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


async def test_offset_walks_the_same_universe_without_changing_total(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        first = await harness.client.post(
            "/screens/select", json=criteria(limit=5, offset=0)
        )
        second = await harness.client.post(
            "/screens/select", json=criteria(limit=5, offset=5)
        )

    left, right = first.json(), second.json()
    assert first.status_code == second.status_code == 200
    assert left["total"] == right["total"]
    assert left["returned"] == right["returned"] == 5
    assert not set(row["symbol"] for row in left["rows"]) & set(
        row["symbol"] for row in right["rows"]
    ), "a page offset must advance, not repeat the first page"


async def test_a_negative_offset_is_a_422(tmp_path: Path, monkeypatch: Any) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post("/screens/select", json=criteria(offset=-1))

    error = assert_envelope(response, status=422)
    assert "offset" in error["errorMessage"]


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
    assert len(mapped) == 47, (
        "the published schema must name every key that filters a parity row, the "
        "three derived columns, the five fundamentals columns (three joined, two "
        "computed) and the three facts columns included"
    )
    chosen = [
        key for key, entry in served["x-filter-keys"].items() if "choice" in entry
    ]
    assert chosen == ["ta_perf", "ta_perf2", "ta_volatility"], (
        "the three option-driven keys publish their vocabulary, not one column"
    )
    assert "fa_pe" in mapped and "ta_beta" in mapped and "ta_sma50" in mapped
    assert "industry" in mapped and "fa_div" in mapped and "ta_gap" in mapped
    assert "an_recom" in mapped and "ta_changeopen" in mapped and "ta_highlow52w" in mapped
    assert "fa_fpe" in mapped and "fa_ps" in mapped and "fa_payoutratio" in mapped
    assert "fa_pc" in mapped and "fa_evsales" in mapped, (
        "the two ratios computed from the fundamentals snapshot are published too"
    )


# ---------------------------------------------------------------------------
# The scan cache — one scan serves a burst, and not one request longer
# ---------------------------------------------------------------------------


#: Five panels a user can click through in a second. The scan does not depend on
#: the view (the view only projects the parity columns), so all five are one
#: question to the provider and five different answers to the client.
BURST_VIEWS = ("today", "value", "quality", "growth", "momentum")


def view_body(view: str, **criteria: Any) -> dict[str, Any]:
    """A criteria selection rendered as ``view`` — the panel a tab click asks for."""

    return {
        "mode": "criteria",
        "region": "us",
        "view": view,
        "criteria": {"filters": [], "sort": "market_cap", "limit": 20, **criteria},
    }


def fundamentals_row(symbol: str, **cells: Any) -> Any:
    """One fresh stored fundamentals snapshot, full width, only ``cells`` filled."""

    from app.fundamentals.snapshot import FUNDAMENTAL_FIELDS, FundamentalsSnapshot

    now = datetime.now(UTC)
    fields: dict[str, Any] = {name: None for name in FUNDAMENTAL_FIELDS}
    fields.update(cells)
    return FundamentalsSnapshot(
        symbol=symbol,
        instrument_id=f"test:{symbol}",
        fields=fields,
        source="yahoo",
        as_of=now,
        fetched_at=now,
        expires_at=now + timedelta(hours=24),
    )


async def test_five_view_switches_inside_the_window_cost_one_scan(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The panel used to pay 12.5 s per tab click; a burst must pay it once.

    The five bodies are five different questions (five ETags, five column sets)
    and one scan: the universe is keyed by what the *provider* was asked, not by
    what the panel renders.
    """

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        responses = [
            await harness.client.post("/screens/select", json=view_body(view))
            for view in BURST_VIEWS
        ]
        requests = S.provider_requests(harness.web)

    assert [response.status_code for response in responses] == [200] * 5, [
        response.text for response in responses
    ]
    assert requests == 1, (
        f"five view switches inside one TTL must cost one scan, not {requests}"
    )
    roles = [response.headers.get("x-scan-cache") for response in responses]
    assert roles == ["miss", "hit", "hit", "hit", "hit"], (
        f"the first panel scans and the rest are served from it, got {roles}"
    )
    panels = [response.json()["rows"] for response in responses]
    assert all(len(rows) == 20 for rows in panels), "every view answers the same 20 rows"
    assert set(panels[0][0]) != set(panels[1][0]), (
        "the panels must actually differ, or five cached copies of one view would "
        "pass this test for the wrong reason"
    )
    tags = {response.headers["etag"] for response in responses}
    assert len(tags) == 5, "five different questions cannot share a validator"


async def test_the_first_request_after_the_ttl_scans_again(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A cache that outlives its TTL by even one request is a bug."""

    from app.api import screens as screens_api

    monkeypatch.setattr(screens_api, "SCAN_CACHE_TTL_S", 0.4)
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        first = await harness.client.post("/screens/select", json=view_body("today"))
        second = await harness.client.post("/screens/select", json=view_body("today"))
        inside = S.provider_requests(harness.web)

        await asyncio.sleep(0.45)
        third = await harness.client.post("/screens/select", json=view_body("today"))
        after = S.provider_requests(harness.web)

    assert inside == 1, "the second request is inside the window and must not re-scan"
    assert second.headers.get("x-scan-cache") == "hit"
    assert after == 2, (
        "the first request after the TTL must re-scan — an entry that answered one "
        f"request past its expiry is exactly the bug; provider calls: {after}"
    )
    assert third.headers.get("x-scan-cache") == "miss"
    assert third.status_code == 200 and third.json()["rows"]


async def test_concurrent_cold_requests_share_one_scan(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Single-flight: N cold requests cost one scan, not N."""

    from app.api import screens as screens_api

    calls = {"n": 0}
    real = screens_api.fetch_scan

    async def slow(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return await real(*args, **kwargs)

    monkeypatch.setattr(screens_api, "fetch_scan", slow)
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        responses = await asyncio.gather(
            *(
                harness.client.post("/screens/select", json=view_body(view))
                for view in BURST_VIEWS
            )
        )

    assert [response.status_code for response in responses] == [200] * 5
    assert calls["n"] == 1, (
        f"five requests that arrive before the scan returns are one scan, got {calls['n']}"
    )
    roles = sorted(response.headers["x-scan-cache"] for response in responses)
    assert roles == ["miss", "shared", "shared", "shared", "shared"], roles


async def test_a_store_change_still_moves_the_tag_behind_a_warm_cache(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The scan is cached; the stores are not. A changed store moves the tag.

    The 304 in the middle is what a warm cache makes cheap. The replay after the
    store change must be a ``200`` with the new cell, because the tag names the
    answer — and the answer changed.
    """

    body = {
        "mode": "criteria",
        "region": "us",
        "columns": ["symbol", "pe_forward"],
        "criteria": {"filters": [], "sort": "market_cap", "limit": 3},
    }
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        first = await harness.client.post("/screens/select", json=body)
        assert first.status_code == 200, first.text
        tag = first.headers["etag"]

        warm = await harness.client.post(
            "/screens/select", json=body, headers={"if-none-match": tag}
        )
        assert warm.status_code == 304, warm.text

        symbol = first.json()["rows"][0]["symbol"]
        await harness.runtime.fundamentals_store.upsert(
            fundamentals_row(symbol, pe_forward=41.0)
        )
        fresh = await harness.client.post("/screens/select", json=body)
        replay = await harness.client.post(
            "/screens/select", json=body, headers={"if-none-match": tag}
        )
        scans = S.provider_requests(harness.web)

    assert scans == 1, (
        "the scan did not change; the three later requests must be served by the "
        f"cache, so the store is what moved the tag (scans: {scans})"
    )
    assert fresh.headers["etag"] != tag, (
        "a store cell changed the body; a tag that stood still would 304 the new answer"
    )
    assert replay.status_code == 200, "a stale validator is answered with the document"
    assert replay.json()["rows"][0]["pe_forward"] == 41.0


async def test_a_filter_change_is_not_answered_from_another_filters_rows(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The cache is in front of the *scan*, not in front of the answer."""

    technology = criteria({"key": "sec", "op": "eq", "value": "Technology"}, limit=100)
    healthcare = criteria({"key": "sec", "op": "eq", "value": "Healthcare"}, limit=100)

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        left = await harness.client.post("/screens/select", json=technology)
        right = await harness.client.post("/screens/select", json=healthcare)
        scans = S.provider_requests(harness.web)

    assert left.status_code == right.status_code == 200
    assert scans == 1, "both filters read one universe — that is the only thing shared"
    left_rows = {row["symbol"] for row in left.json()["rows"]}
    right_rows = {row["symbol"] for row in right.json()["rows"]}
    assert left_rows == set(US_TECHNOLOGY), (
        f"the Technology panel must be the Technology rows, got {sorted(left_rows)}"
    )
    assert right_rows and not (left_rows & right_rows), (
        "one filter's rows must never answer another filter"
    )
    assert left.headers["etag"] != right.headers["etag"]


async def test_two_asset_classes_never_share_a_scan(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The key names the universe: same region, same feed, different family."""

    stock = {"mode": "criteria", "region": "us", "criteria": {"filters": [], "limit": 5}}
    fund = {
        "mode": "criteria",
        "region": "us",
        "asset_class": "etf",
        "criteria": {"filters": [], "limit": 5},
    }
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        first = await harness.client.post("/screens/select", json=stock)
        second = await harness.client.post("/screens/select", json=fund)
        scans = S.provider_requests(harness.web)

    assert first.status_code == second.status_code == 200, (first.text, second.text)
    assert scans == 2, (
        "the stock and ETF universes are different provider questions; one entry "
        f"cannot answer both (scans: {scans})"
    )


async def test_a_scanner_failure_is_never_cached(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A failure is not an answer, and it is not a cached one either."""

    from app.api import screens as screens_api
    from app.screen.scanner import ScanError

    calls = {"n": 0}
    real = screens_api.fetch_scan

    async def flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ScanError("the scanner is down")
        return await real(*args, **kwargs)

    monkeypatch.setattr(screens_api, "fetch_scan", flaky)
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        failed = await harness.client.post("/screens/select", json=view_body("today"))
        recovered = await harness.client.post("/screens/select", json=view_body("today"))

    assert failed.status_code == 503, failed.text
    assert failed.json()["error"] == "scanner_unavailable"
    assert calls["n"] == 2, (
        "a failed scan must not be remembered: the next request has to try again"
    )
    assert recovered.status_code == 200 and recovered.json()["rows"]
    assert recovered.headers["x-scan-cache"] == "miss"


async def test_an_expired_entry_is_never_served_as_a_stale_200(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """No stale-on-error: past the TTL a failed scan is a 503, not old rows."""

    from app.api import screens as screens_api
    from app.screen.scanner import ScanError

    monkeypatch.setattr(screens_api, "SCAN_CACHE_TTL_S", 0.4)
    real = screens_api.fetch_scan
    state = {"broken": False}

    async def breaker(*args: Any, **kwargs: Any) -> Any:
        if state["broken"]:
            raise ScanError("the scanner went away")
        return await real(*args, **kwargs)

    monkeypatch.setattr(screens_api, "fetch_scan", breaker)
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        warm = await harness.client.post("/screens/select", json=view_body("today"))
        assert warm.status_code == 200, warm.text

        state["broken"] = True
        await asyncio.sleep(0.45)
        cold = await harness.client.post("/screens/select", json=view_body("today"))

        state["broken"] = False
        back = await harness.client.post("/screens/select", json=view_body("today"))

    assert cold.status_code == 503, (
        "an expired page is not a fallback: the caller must be told the scan failed"
    )
    assert set(cold.json()) == {"error", "errorMessage"}
    assert "scanner" in cold.json()["errorMessage"]
    assert back.status_code == 200 and back.json()["rows"]


async def test_the_key_names_the_scan_and_not_the_question(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A key is what the provider was asked, field by field."""

    from app.api import screens as screens_api

    base = dict(
        region="us",
        asset_class="stock",
        source="tradingview",
        columns=("close", "market_cap"),
        limit=100,
        min_market_cap=None,
        subtype=None,
        offset=0,
    )
    assert screens_api._scan_key(**base) == screens_api._scan_key(**base)
    for field, other in (
        ("region", "global"),
        ("asset_class", "etf"),
        ("source", "multi"),
        ("columns", ("close",)),
        ("limit", 50),
        ("min_market_cap", 100.0),
        ("subtype", "common"),
        ("offset", 5),
    ):
        assert screens_api._scan_key(**{**base, field: other}) != screens_api._scan_key(**base), (
            f"{field} is part of the scan's identity; two different scans cannot share a key"
        )
