"""``POST /screens/select`` over the ASGI app: the ETag, the panel size, symbols.

Phase 0 of the interface plan (``.hermes/plans/2026-10-30_ui-interface.md`` §2)
puts four HTTP-level promises on this route:

* **a validator derived from the question and from the data that answered it** —
  a repeat POST with ``If-None-Match`` answers ``304`` with **no body at all**,
  and the client keeps the rows it already has. The tag covers every input that
  can change the answer: the snapshot, the depth store's cycle, the live scan
  and the fundamentals/facts joins. F3 was the opposite: the tag named only the
  depth clock, so the scan and two stores could change the body and the client
  still got a 304 with its old numbers;
* **a clock tick does not change the tag, a data change does** — if the
  validator moved with ``as_of``, no client would ever get a 304 and the panel
  would repaint at 1.8 s per poll; if it *failed* to move with the data, the
  client would keep those 1.8 s rows forever (this file used to test only the
  first half);
* **a different filter does change it**, because the answer changed;
* **100 rows in one call** with ``columns``: exactly the requested cells, under
  60,000 bytes, so a twelve-column panel is one small response instead of five
  83 KB page calls (measured: 14.03 s, two symbols repeated).

The panel budget is the route's promise (F9), and it applies to ``view`` as well
as to ``columns``: the same panel asked for the other way is still the same
panel. ``limit`` is a page size, not a JSON integer type (F11): ``4.0`` is 4,
``1e9`` is clamped to 100, and ``4.5`` is a 422 that says why.

It also pins the two contract repairs of §2.7: the browse field is ``matched``
(never ``total``) next to ``universe_total``, and symbols mode honours
``columns`` and ``limit``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tests import _screen as S
from tests._phase2 import MockWeb

#: The twelve cells of the plan's "value" view, in the order the grid asks.
VALUE_COLUMNS = [
    "market_cap",
    "enterprise_value",
    "pe",
    "forward_pe",
    "peg",
    "ps",
    "pb",
    "pc",
    "pfcf",
    "ev_ebitda",
    "ev_sales",
    "target_price",
]


def criteria(
    *filters: dict[str, Any], limit: int | float | None = None, **extra: Any
) -> dict[str, Any]:
    block: dict[str, Any] = {"filters": list(filters)}
    if limit is not None:
        block["limit"] = limit
    return {"mode": "criteria", "region": "us", "criteria": block, **extra}


def symbols(snapshot_id: str, tickers: list[str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "mode": "symbols",
        "symbols": tickers,
        "source": "tradingview",
        "snapshot_id": snapshot_id,
    }
    body.update(overrides)
    return body


def cloned_web(copies: int = 5, *, answer_every_column: bool = False) -> MockWeb:
    """A scanner that answers ``copies`` renamed copies of the US fixture.

    The recorded fixture holds 23 issuers, so a 100-row page cannot be measured
    against it. Each copy gets a distinct ticker *and* issuer name, because
    ``build_universe`` dedupes by issuer: the point is a real universe bigger
    than one page, not the same 23 rows wearing five hats.

    The response echoes the columns the *request* asked for and maps each cell by
    name — ``parse_scan`` reads ``d`` positionally through the requested column
    list, so a body in the fixture's own column order would answer the parity
    request with the wrong cells.

    ``answer_every_column`` fills a cell the fixture does not carry with a
    deterministic number. The recorded fixture holds nine numbers per row (close,
    change, volume, value traded, market cap, RSI, beta, 52-week high/low), so a
    twelve-column projection of *this* fixture is twelve columns of ``null`` —
    and a "panel under 60 KB" assertion made over nulls can never fail, which is
    how the budget test passed for the wrong reason.
    """

    web = MockWeb()

    def responder(request: Any) -> dict[str, Any]:
        requested = json.loads(request.content)["columns"]
        data: list[dict[str, Any]] = []
        for copy_index in range(copies):
            for position, row in enumerate(S.scan_rows("us")):
                clone = dict(row)
                clone["symbol"] = (
                    f"{row['symbol']}{copy_index}" if copy_index else row["symbol"]
                )
                clone["description"] = (
                    f"{row.get('description') or row['symbol']} copy {copy_index}"
                )
                clone["name"] = clone["description"]
                cells: list[Any] = []
                for column in requested:
                    value = clone.get(column)
                    if value is None and answer_every_column:
                        value = round(10.0 + copy_index + position / 100.0, 2)
                    cells.append(value)
                data.append({"s": f"{row['venue']}:{clone['symbol']}", "d": cells})
        return {"totalCount": len(data), "columns": requested, "data": data}

    web.json("tradingview", responder)
    return web


def populated_web(copies: int = 5) -> MockWeb:
    """``cloned_web`` with every requested cell answered by a number."""

    return cloned_web(copies, answer_every_column=True)


# ---------------------------------------------------------------------------
# ETag / 304
# ---------------------------------------------------------------------------


async def test_a_repeat_post_answers_304_with_no_body(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A validated repeat costs no body and no repaint.

    It does cost **one scan**, because the scan is one of the inputs the tag
    covers (F3): a validator that could answer ``304`` without looking at the
    prices would certify rows it never read. The saving is the 30–270 KB body
    and the client's re-render, not the provider call.
    """

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        body = criteria({"key": "sec", "op": "eq", "value": "Technology"}, limit=5)
        first = await harness.client.post("/screens/select", json=body)
        tag = first.headers.get("etag")
        second = await harness.client.post(
            "/screens/select", json=body, headers={"if-none-match": tag}
        )

    assert first.status_code == 200, first.text
    assert tag, "a 200 must publish a validator, or no client can ever revalidate"
    assert tag.startswith('W/"') and tag.endswith('"'), (
        f"a weak ETag is expected (the body carries `as_of`, the read moment), got {tag!r}"
    )
    assert "max-age=0" in first.headers.get("cache-control", "")

    assert second.status_code == 304, second.text
    assert second.content == b"", (
        f"a 304 body is empty by definition; got {len(second.content)} bytes. "
        "The client keeps its rows — the server never sends a second document"
    )
    assert second.headers.get("etag") == tag, "the 304 repeats the validator"


async def test_the_tag_survives_a_clock_tick(tmp_path: Path, monkeypatch: Any) -> None:
    from app.api import screens as screens_api

    real = datetime

    class Ticking(real):  # type: ignore[misc, valid-type]
        @classmethod
        def now(cls, tz: Any = None) -> Any:
            return real.now(tz) + timedelta(seconds=90)

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        body = criteria(limit=5)
        first = await harness.client.post("/screens/select", json=body)
        monkeypatch.setattr(screens_api, "datetime", Ticking)
        second = await harness.client.post(
            "/screens/select", json=body, headers={"if-none-match": first.headers["etag"]}
        )
        third = await harness.client.post("/screens/select", json=body)

    assert first.status_code == 200, first.text
    assert second.status_code == 304, (
        "a clock tick alone must not invalidate the answer; `as_of` is the read "
        f"moment, not the data revision (got {second.status_code})"
    )
    assert third.headers["etag"] == first.headers["etag"], (
        "the tag must be a function of the data, never of the clock — and it must "
        "not have moved with the tick either"
    )
    assert third.json()["as_of"] != first.json()["as_of"], (
        "the clock tick must be visible in `as_of`, or this test proves nothing: a "
        "constant body would pass it for the wrong reason"
    )


async def test_a_changed_filter_changes_the_tag(tmp_path: Path, monkeypatch: Any) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        first = await harness.client.post(
            "/screens/select", json=criteria({"key": "sec", "op": "eq", "value": "Technology"})
        )
        second = await harness.client.post(
            "/screens/select", json=criteria({"key": "sec", "op": "eq", "value": "Healthcare"})
        )
        replay = await harness.client.post(
            "/screens/select",
            json=criteria({"key": "sec", "op": "eq", "value": "Healthcare"}),
            headers={"if-none-match": first.headers["etag"]},
        )

    assert first.status_code == second.status_code == 200
    assert first.headers["etag"] != second.headers["etag"], (
        "two different questions cannot share a cache entry"
    )
    assert replay.status_code == 200 and replay.json()["rows"], (
        "a stale validator must be answered with the document, never a 304"
    )


async def test_two_spellings_of_one_question_share_a_tag(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Omitted defaults vs explicit defaults are the same question."""

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        bare = await harness.client.post(
            "/screens/select", json={"mode": "criteria", "region": "us", "criteria": {}}
        )
        explicit = await harness.client.post(
            "/screens/select",
            json={
                "mode": "criteria",
                "region": "us",
                "criteria": {
                    "filters": [],
                    "sort": "market_cap",
                    "limit": 100,
                    "offset": 0,
                    "common_only": False,
                },
            },
        )
        replay = await harness.client.post(
            "/screens/select",
            json={"mode": "criteria", "region": "us", "criteria": {"limit": 100}},
            headers={"if-none-match": bare.headers["etag"]},
        )

    assert bare.headers["etag"] == explicit.headers["etag"]
    assert replay.status_code == 304


# ---------------------------------------------------------------------------
# 100 rows, twelve columns, one small body
# ---------------------------------------------------------------------------


async def test_a_hundred_rows_of_twelve_columns_in_one_small_response(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The panel budget, measured on a panel that actually holds numbers.

    ``cloned_web`` answers the recorded fixture, whose twelve "value" columns are
    all ``null`` — a 100-row all-null panel is 24,094 B, so a "< 60,000 B"
    assertion over it passes no matter what the projection does. This one fills
    every requested cell, and asserts that it did, so the byte count is a
    measurement of a real panel.
    """

    async with S.screen_client(tmp_path, monkeypatch, web=populated_web()) as harness:
        response = await harness.client.post(
            "/screens/select",
            json=criteria(limit=100, columns=VALUE_COLUMNS),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["limit"] == 100
    assert body["returned"] == 100, (
        f"one call must fill the panel, got {body['returned']} rows "
        f"out of {body['total']} matches"
    )
    assert len({row["symbol"] for row in body["rows"]}) == 100, "a page holds no duplicates"
    assert set(body["rows"][0]) == {"symbol", *VALUE_COLUMNS, "as_of"}, (
        f"a projection is exactly its cells, got {sorted(body['rows'][0])}"
    )
    # Three of the twelve cells are owned by the fundamentals snapshot store,
    # which this harness leaves empty — their nulls are a data fact, not a
    # projection bug. The other nine must carry a number on every row, or the
    # byte count below is a measurement of `null`s.
    store_owned = {"enterprise_value", "pc", "ev_sales"}
    for name in VALUE_COLUMNS:
        filled = sum(1 for row in body["rows"] if row[name] is not None)
        expected = 0 if name in store_owned else 100
        assert filled == expected, (
            f"{name}: {filled} of 100 rows carry a number, expected {expected}. A "
            "panel of nulls is 24,094 B, so the budget assertion below would pass "
            "no matter what the projection does — the wrong-reason pass this test "
            "exists to prevent"
        )
    assert len(response.content) < 60_000, (
        f"12 columns x 100 rows is {len(response.content):,} B; the panel budget "
        "is 60,000 B (the full document is ~162 KB at this size)"
    )
    assert body["budget"]["bytes"] == len(response.content), (
        "the budget report must measure the bytes that shipped, not an estimate"
    )
    assert body["budget"]["truncated"] is False and body["budget"]["dropped_fields"] == []
    print(f"\nAPI: 12 columns x 100 rows = {len(response.content):,} B")


async def test_a_symbols_lookup_honours_columns_and_limit(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        screen = await harness.create_screen()
        response = await harness.client.post(
            "/screens/select",
            json=symbols(
                screen["screen_id"], ["NVDA", "AAPL", "MSFT"], columns=["roic", "pe_ttm"], limit=2
            ),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["columns"] == ["roic", "pe_ttm"], (
        "symbols mode used to ignore `columns` and echo []; the schema always "
        "said it applied to both modes"
    )
    assert body["limit"] == 2 and body["returned"] == 2, (
        "a 25-name watchlist must be able to ask for more than 20 rows"
    )
    assert set(body["rows"][0]) == {"symbol", "roic", "pe_ttm", "as_of"}
    assert [row["symbol"] for row in body["rows"]] == ["NVDA", "AAPL"], "request order"


async def test_a_logo_is_available_on_request_but_never_implicit(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The brand mark is part of the row contract; it is not part of a projection."""

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        asked = await harness.client.post(
            "/screens/select", json=criteria(limit=3, columns=["symbol", "logo"])
        )
        light = await harness.client.post("/screens/select", json=criteria(limit=3))
        projected = await harness.client.post(
            "/screens/select", json=criteria(limit=3, columns=["symbol", "pe_ttm"])
        )

    assert asked.status_code == 200, asked.text
    logos = [row["logo"] for row in asked.json()["rows"]]
    assert all(isinstance(logo, str) and logo for logo in logos), "logo is never null"
    assert all("logo" in row for row in light.json()["rows"]), (
        "the light row keeps the identity lane it has always carried"
    )
    assert all("logo" not in row for row in projected.json()["rows"]), (
        "a 100-row projection must not carry 64 KB of base64 monograms nobody asked for"
    )


# ---------------------------------------------------------------------------
# F3 — every input that can change the body is in the validator
# ---------------------------------------------------------------------------


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


async def test_a_fundamentals_change_moves_the_tag_and_a_stale_tag_is_a_200(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """F3: the fundamentals store changed the body while the tag stood still.

    The constructed collision in the adversarial report: two 200s whose
    ``forward_pe`` cell moved, one ETag, and a revalidating client that kept
    1.0 forever. The tag now covers the answer, so the store is an input.
    """

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        body = criteria(limit=3, columns=["symbol", "pe_forward"])
        first = await harness.client.post("/screens/select", json=body)
        assert first.status_code == 200, first.text
        tag = first.headers["etag"]
        symbol = first.json()["rows"][0]["symbol"]

        await harness.runtime.fundamentals_store.upsert(
            fundamentals_row(symbol, pe_forward=41.0)
        )
        second = await harness.client.post("/screens/select", json=body)
        replay = await harness.client.post(
            "/screens/select", json=body, headers={"if-none-match": tag}
        )

    assert second.status_code == replay.status_code == 200
    assert second.headers["etag"] != tag, (
        "the fundamentals join changed the body, so the tag must move: F3 was a "
        "304 for a body the tag never covered"
    )
    assert replay.headers["etag"] == second.headers["etag"]
    assert replay.json()["rows"][0]["pe_forward"] == 41.0, (
        "a stale `If-None-Match` must return the new document, never a 304"
    )


def moving_scan_web() -> tuple[MockWeb, dict[str, float]]:
    """A scanner whose ``AAPL`` close the test moves between two calls."""

    state: dict[str, float] = {"close": 348.22}
    web = MockWeb()

    def responder(request: Any) -> dict[str, Any]:
        requested = json.loads(request.content)["columns"]
        data: list[dict[str, Any]] = []
        for row in S.scan_rows("us"):
            clone = dict(row)
            if clone["symbol"] == "AAPL":
                clone["close"] = state["close"]
            data.append(
                {
                    "s": f"{row['venue']}:{clone['symbol']}",
                    "d": [clone.get(column) for column in requested],
                }
            )
        return {"totalCount": len(data), "columns": requested, "data": data}

    web.json("tradingview", responder)
    return web, state


async def test_a_scan_change_moves_the_tag(tmp_path: Path, monkeypatch: Any) -> None:
    """F3, the half the depth clock cannot see: the live scan re-runs per call."""

    web, state = moving_scan_web()
    async with S.screen_client(tmp_path, monkeypatch, web=web) as harness:
        body = criteria(limit=3, columns=["symbol", "price"])
        first = await harness.client.post("/screens/select", json=body)
        assert first.status_code == 200, first.text
        tag = first.headers["etag"]

        state["close"] = 999.5
        second = await harness.client.post("/screens/select", json=body)
        replay = await harness.client.post(
            "/screens/select", json=body, headers={"if-none-match": tag}
        )

    assert second.headers["etag"] != tag, (
        "the scan answered different prices; a tag that did not move would certify "
        "rows it never read"
    )
    assert replay.status_code == 200, "a stale validator is answered with the document"
    prices = [row["price"] for row in replay.json()["rows"]]
    assert 999.5 in prices, f"the new scan must be in the body, got {prices}"


def depth_store(as_of: str | None, symbols: dict[str, dict[str, Any]]) -> Any:
    from app.screen.depth_source import DepthSnapshot

    return DepthSnapshot(as_of=as_of, symbols=symbols, loaded_at=0.0)


async def test_a_half_written_depth_store_cannot_mint_a_304(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """F3(b): a store caught mid-write must move the tag, not freeze it.

    A truncated write parses as *fewer symbols* (or as nothing at all) while the
    collector's ``as_of`` still says the old cycle. The tag names the cycle **and**
    the row count, so a partial store can only ever produce a miss.
    """

    from app.api import screens as screens_api

    whole = depth_store(
        "2026-10-30T16:13:15Z", {"AAPL": {"pe": 7.7}, "NVDA": {"pe": 8.8}}
    )
    partial = depth_store("2026-10-30T16:13:15Z", {"AAPL": {"pe": 7.7}})
    empty = depth_store(None, {})

    monkeypatch.setattr(screens_api, "current_depth", lambda *a, **k: whole)
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        body = criteria(limit=20, columns=["symbol", "pe_ttm"])
        first = await harness.client.post("/screens/select", json=body)
        assert first.status_code == 200, first.text
        tag = first.headers["etag"]

        monkeypatch.setattr(screens_api, "current_depth", lambda *a, **k: partial)
        second = await harness.client.post(
            "/screens/select", json=body, headers={"if-none-match": tag}
        )
        monkeypatch.setattr(screens_api, "current_depth", lambda *a, **k: empty)
        third = await harness.client.post(
            "/screens/select", json=body, headers={"if-none-match": tag}
        )

    assert second.status_code == 200, (
        "a half-written depth store is not the store the client revalidated against"
    )
    assert second.headers["etag"] != tag
    assert third.status_code == 200 and third.headers["etag"] not in {tag, second.headers["etag"]}
    assert any(row["pe_ttm"] == 7.7 for row in first.json()["rows"]), (
        "the whole store's cell must be in the body, or this test proves nothing"
    )


def test_the_revision_is_hashed_full_width() -> None:
    """F3(a): no truncation, so two revisions cannot be mined onto one tag.

    ``selection_etag`` folds its revision through ``sha1(...)[:8]`` — eight hex
    characters is a 32-bit target, and the caller controls store contents. The
    route hashes the composite full width; this pins that.
    """

    from app.api import screens as screens_api
    from app.screen.selection import parse_selection

    request = parse_selection({"mode": "criteria", "region": "us", "criteria": {}})
    prefix = "revision:" + "0" * 512
    left = screens_api._etag(revision=f"{prefix}1", request=request)
    right = screens_api._etag(revision=f"{prefix}2", request=request)

    digest = left.strip('"').split("-")[1]
    assert len(digest) == 64, (
        f"the revision must be hashed full width (64 hex), got {len(digest)}: "
        "a truncated stamp is a collision an attacker can mine"
    )
    assert digest == hashlib.sha256(f"{prefix}1".encode("utf-8")).hexdigest()
    assert left != right


async def test_two_200s_with_one_weak_tag_differ_only_in_as_of(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The validator is weak because the body carries the read moment.

    Everything that decides the *answer* is hashed; ``as_of`` is when the answer
    was read, and a strong validator that changed with it would never match. So
    the tag says ``W/`` — same answer, not necessarily the same bytes — and this
    test pins the difference to exactly that one field.
    """

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        body = criteria(limit=5)
        first = await harness.client.post("/screens/select", json=body)
        second = await harness.client.post("/screens/select", json=body)

    assert first.status_code == second.status_code == 200
    assert first.headers["etag"] == second.headers["etag"], (
        "same request, same data: one validator"
    )
    assert first.headers["etag"].startswith('W/"'), "a weak validator, honestly labelled"

    left, right = first.json(), second.json()
    assert left["as_of"] != right["as_of"], "the read moment must actually have moved"
    for document in (left, right):
        document.pop("as_of")
        for row in document["rows"]:
            row.pop("as_of")
    assert left == right, (
        "two 200s under one ETag differ in the read moment and nothing else; "
        "anything else is a body the validator does not cover"
    )


# ---------------------------------------------------------------------------
# F9 — the panel budget is the route's promise, `view` or `columns`
# ---------------------------------------------------------------------------

VIEW_VALUE: dict[str, Any] = {"mode": "criteria", "region": "us"}


def view_body(name: str, *, limit: int) -> dict[str, Any]:
    return {**VIEW_VALUE, "criteria": {"limit": limit}, "view": name}


async def test_a_view_panel_respects_the_panel_budget(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """F9: 87,918 B (value) and 273,923 B (all) were the same panel, other dial."""

    async with S.screen_client(tmp_path, monkeypatch, web=populated_web()) as harness:
        value = await harness.client.post("/screens/select", json=view_body("value", limit=100))
        everything = await harness.client.post(
            "/screens/select", json=view_body("all", limit=100)
        )

    for name, response in (("value", value), ("all", everything)):
        assert response.status_code == 200, response.text
        assert len(response.content) <= 60_000, (
            f"view={name} shipped {len(response.content):,} B; the panel budget is "
            "60,000 B and `view` is the documented way to ask for the same panel"
        )
        assert response.json()["budget"]["bytes"] == len(response.content)
        assert response.json()["budget"]["truncated"] is True, (
            "a body that had to shrink must say so in the response"
        )

    value_body = value.json()
    assert value_body["returned"] == 100, (
        "the value panel fits once the implicit brand mark is gone: no row is lost"
    )
    assert "logo" in value_body["budget"]["dropped_fields"]
    assert value_body["budget"]["rows_dropped"] == 0

    all_body = everything.json()
    assert all_body["budget"]["rows_dropped"] > 0, (
        "116 columns cannot fit 100 rows in 60 KB: the page must shrink, not the bytes"
    )
    assert all_body["returned"] == len(all_body["rows"])
    assert all_body["returned"] + all_body["budget"]["rows_dropped"] == 100, (
        "the response must account for every row it did not return: `returned` plus "
        "`rows_dropped` is the 100 the caller asked for"
    )


async def test_a_small_view_keeps_its_logo_and_reports_no_truncation(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The budget bites only when the body is over it — a 5-row view is untouched."""

    async with S.screen_client(tmp_path, monkeypatch, web=populated_web()) as harness:
        response = await harness.client.post("/screens/select", json=view_body("value", limit=5))

    assert response.status_code == 200, response.text
    body = response.json()
    assert all("logo" in row for row in body["rows"]), (
        "a view row carries the mark; only a budget-overflowed panel loses it"
    )
    assert body["budget"]["truncated"] is False
    assert body["budget"]["dropped_fields"] == [] and body["budget"]["rows_dropped"] == 0


async def test_budget_truncation_never_drops_the_first_row(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """F9(c): a truncated panel is a *prefix* of the answer, never a surprise."""

    from app.api import screens as screens_api

    async with S.screen_client(tmp_path, monkeypatch, web=populated_web()) as harness:
        reference = await harness.client.post(
            "/screens/select", json=view_body("all", limit=5)
        )
        monkeypatch.setattr(screens_api, "PANEL_BUDGET_BYTES", 6_000)
        squeezed = await harness.client.post(
            "/screens/select", json=view_body("all", limit=100)
        )

    assert reference.status_code == squeezed.status_code == 200
    squeezed_body = squeezed.json()
    assert 0 < squeezed_body["returned"] < 100, (
        "a 6 KB budget cannot hold 100 rows of 116 columns"
    )
    # `as_of` is the read moment and `logo` is the decoration the budget dropped
    # (announced in `dropped_fields`); the row itself must be the answer's first.
    def cells(row: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in row.items() if k not in {"as_of", "logo"}}

    assert cells(squeezed_body["rows"][0]) == cells(reference.json()["rows"][0]), (
        "the first row of a truncated panel must be the first row of the answer: "
        "truncation cuts from the tail"
    )
    assert squeezed_body["rows"][0]["symbol"] == reference.json()["rows"][0]["symbol"]
    assert "logo" in squeezed_body["budget"]["dropped_fields"]


# ---------------------------------------------------------------------------
# F11 — `limit` is a page size, not a JSON integer type
# ---------------------------------------------------------------------------


async def test_a_json_float_limit_is_the_integer_it_spells(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """F11: ``4.0`` and ``1e9`` were 422s while the schema promised a clamp."""

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        four = await harness.client.post("/screens/select", json=criteria(limit=4.0))
        huge = await harness.client.post("/screens/select", json=criteria(limit=1e9))

    assert four.status_code == 200, four.text
    assert four.json()["limit"] == 4 and four.json()["returned"] <= 4
    assert huge.status_code == 200, huge.text
    assert huge.json()["limit"] == 100, (
        "a JSON float that names an integer above the panel clamps like the integer "
        "it spells"
    )


async def test_a_fractional_limit_is_refused_with_the_sentence_that_explains_it(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post("/screens/select", json=criteria(limit=4.5))

    assert response.status_code == 422, response.text
    body = response.json()
    assert "limit" in body["errorMessage"] and "4.5" in body["errorMessage"], (
        f"a refused limit must name the value it refused, got {body!r}"
    )


# ---------------------------------------------------------------------------
# The browse contract: `matched`, not `total`
# ---------------------------------------------------------------------------


async def test_browse_reports_matched_and_universe_total(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.get("/screens", params={"region": "us"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert "matched" in body, (
        "`total` on the browse route and `universe_total` on the selection route "
        "were both labelled 'Universe' by the client; the browse number is the "
        f"post-filter match count, got keys {sorted(body)}"
    )
    assert "total" not in body, "the ambiguous name is gone, not left beside its replacement"
    assert body["matched"] == S.US_TOTAL
    assert body["universe_total"] == S.US_TOTAL, (
        "browse runs no filter, so its universe is the rows it matched"
    )


async def test_the_browse_schema_declares_matched() -> None:
    from app.screen.schema import SCREEN_RESPONSE_SCHEMA

    required = set(SCREEN_RESPONSE_SCHEMA["required"])
    assert "matched" in required and "universe_total" in required
    assert "total" not in required
    assert "matches" not in SCREEN_RESPONSE_SCHEMA["properties"]
