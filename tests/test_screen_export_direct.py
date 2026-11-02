"""``POST /screens/export``: one synchronous file, capped at 200 rows.

The screener's export used to be a job — create a screen, enqueue a bundle, poll
``/requests/{id}`` and then fetch the artifact. The dialog described those three
hops while the first one answered a document with no ``screen_id``, so the only
thing a reader could do was fail. This route is the replacement: the body names
what to export, the answer **is** the file.

What this file pins, in the order it matters:

* the cap — 200 rows is accepted, 201 is a 422 that names the cap, and an
  omitted (or zero) limit means the full 200. The refusal happens **before any
  scan**, so a bad knob costs no provider call;
* the headers — ``Content-Disposition`` names a download, ``X-Row-Count`` states
  the rows in the body, and the CSV line count agrees with it;
* the three formats — CSV via the stdlib reader, JSON via ``json.loads``, XLSX
  via ``openpyxl`` (a dev dependency, so the test reads the real workbook);
* the two modes — criteria runs the selection pipeline (filters, columns,
  order), symbols resolves an explicit list in the caller's order and reports a
  ticker the universe does not carry instead of inventing a row.
"""

from __future__ import annotations

import csv
import io
import json

from openpyxl import load_workbook

from tests import _screen as S

#: The query every criteria export starts from: the whole US stock universe.
CRITERIA: dict[str, object] = {"filters": [], "sort": "market_cap"}


def _criteria(**extra: object) -> dict[str, object]:
    body: dict[str, object] = {"mode": "criteria", "criteria": dict(CRITERIA), "format": "csv"}
    body.update(extra)
    return body


async def _select_total(harness: object, criteria: dict[str, object] = CRITERIA) -> int:
    """The selection route's own match count — the number an export must agree with."""

    response = await harness.client.post(  # type: ignore[attr-defined]
        "/screens/select",
        json={"mode": "criteria", "region": "us", "criteria": {**criteria, "limit": 20}},
    )
    assert response.status_code == 200, response.text
    return int(response.json()["total"])


# ---------------------------------------------------------------------------
# The cap
# ---------------------------------------------------------------------------


async def test_limit_200_is_accepted_and_an_omitted_limit_exports_the_match(
    tmp_path: object, monkeypatch: object
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:  # type: ignore[arg-type]
        matched = await _select_total(harness)
        capped = await harness.client.post("/screens/export", json=_criteria(limit=200))
        default = await harness.client.post("/screens/export", json=_criteria())
        zero = await harness.client.post("/screens/export", json=_criteria(limit=0))

    assert capped.status_code == 200, capped.text
    assert default.status_code == 200, default.text
    assert zero.status_code == 200, zero.text
    capped_rows = int(capped.headers["x-row-count"])
    default_rows = int(default.headers["x-row-count"])
    assert capped_rows == min(matched, 200), (
        "the export must describe the selection's own population: the panel counted "
        f"{matched} rows, the file wrote {capped_rows}"
    )
    assert default_rows == min(matched, 200), (
        "an omitted limit means the full cap, so the whole match up to 200; got "
        f"{default_rows}"
    )
    assert int(zero.headers["x-row-count"]) == default_rows, "0 is how a body spells 'the cap'"
    assert capped_rows == default_rows and capped_rows > 0


async def test_limit_201_is_refused_by_name_before_any_scan(
    tmp_path: object, monkeypatch: object
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:  # type: ignore[arg-type]
        response = await harness.client.post("/screens/export", json=_criteria(limit=201))
        scans = S.provider_requests(harness.web)

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "export_limit_exceeded", body
    assert "200" in body["errorMessage"], (
        f"the refusal must name the cap it enforced; got {body['errorMessage']!r}"
    )
    assert scans == 0, "a limit over the cap must be refused before a provider scan"


async def test_a_limit_under_the_cap_cuts_the_file_to_that_many_rows(
    tmp_path: object, monkeypatch: object
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:  # type: ignore[arg-type]
        response = await harness.client.post("/screens/export", json=_criteria(limit=5))

    assert response.status_code == 200, response.text
    assert int(response.headers["x-row-count"]) == 5
    assert int(response.headers["x-total-match"]) > 5, (
        "the file is capped; the header still reports the full match so the client "
        "can say '5 of N'"
    )
    table = list(csv.reader(io.StringIO(response.text)))
    assert len(table) == 6, "one header plus the five rows the limit asked for"


# ---------------------------------------------------------------------------
# The file and its headers
# ---------------------------------------------------------------------------


async def test_csv_answer_carries_the_download_headers_and_a_matching_row_count(
    tmp_path: object, monkeypatch: object
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:  # type: ignore[arg-type]
        response = await harness.client.post("/screens/export", json=_criteria())

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;"), disposition
    assert 'filename="' in disposition and disposition.endswith('.csv"'), disposition

    table = list(csv.reader(io.StringIO(response.text)))
    assert len(table) - 1 == int(response.headers["x-row-count"])
    assert table[0][:2] == ["rank", "symbol"], table[0][:4]
    assert table[1][1] == "AAPL", "the fixture's top row is AAPL by market cap"


async def test_json_answer_is_the_file_itself(tmp_path: object, monkeypatch: object) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:  # type: ignore[arg-type]
        matched = await _select_total(harness)
        response = await harness.client.post("/screens/export", json=_criteria(format="json"))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    body = json.loads(response.text)
    assert body["mode"] == "criteria"
    assert body["row_count"] == int(response.headers["x-row-count"]) == min(matched, 200)
    assert body["total"] == matched, "the file reports the match, not just what it wrote"
    assert len(body["rows"]) == body["row_count"]
    assert body["rows"][0]["symbol"] == "AAPL"


async def test_xlsx_answer_is_a_readable_workbook(tmp_path: object, monkeypatch: object) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:  # type: ignore[arg-type]
        response = await harness.client.post("/screens/export", json=_criteria(format="xlsx"))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert response.headers["content-disposition"].endswith('.xlsx"')
    workbook = load_workbook(io.BytesIO(response.content), read_only=True)
    sheet = workbook.active
    assert sheet is not None
    assert sheet.max_row == int(response.headers["x-row-count"]) + 1
    header = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
    assert header[:2] == ["rank", "symbol"], header[:4]


async def test_columns_narrow_the_file_and_keep_their_order(
    tmp_path: object, monkeypatch: object
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:  # type: ignore[arg-type]
        response = await harness.client.post(
            "/screens/export", json=_criteria(columns=["market_cap", "price"])
        )

    assert response.status_code == 200, response.text
    table = list(csv.reader(io.StringIO(response.text)))
    assert table[0] == ["symbol", "market_cap", "price", "as_of"], table[0]
    assert table[1][0] == "AAPL"


# ---------------------------------------------------------------------------
# Criteria and symbols
# ---------------------------------------------------------------------------


async def test_a_criterion_filters_the_file_the_same_way_it_filters_the_screen(
    tmp_path: object, monkeypatch: object
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:  # type: ignore[arg-type]
        response = await harness.client.post(
            "/screens/export",
            json={
                "mode": "criteria",
                "criteria": {
                    "filters": [{"field": "symbol", "op": "eq", "value": "AAPL"}],
                    "sort": "market_cap",
                },
                "format": "json",
            },
        )

    assert response.status_code == 200, response.text
    body = json.loads(response.text)
    assert [row["symbol"] for row in body["rows"]] == ["AAPL"]
    assert body["total"] == 1, "one criterion, one matching row"


async def test_symbols_mode_exports_in_the_order_asked(
    tmp_path: object, monkeypatch: object
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:  # type: ignore[arg-type]
        response = await harness.client.post(
            "/screens/export",
            json={"mode": "symbols", "symbols": ["MSFT", "AAPL"], "format": "json"},
        )

    assert response.status_code == 200, response.text
    body = json.loads(response.text)
    assert [row["symbol"] for row in body["rows"]] == ["MSFT", "AAPL"]
    assert body["row_count"] == 2


async def test_a_ticker_the_universe_does_not_carry_is_named_not_invented(
    tmp_path: object, monkeypatch: object
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:  # type: ignore[arg-type]
        response = await harness.client.post(
            "/screens/export",
            json={"mode": "symbols", "symbols": ["AAPL", "NOPE"], "format": "json"},
        )

    assert response.status_code == 200, response.text
    body = json.loads(response.text)
    assert [row["symbol"] for row in body["rows"]] == ["AAPL"]
    assert body["missing"] == ["NOPE"]
    assert response.headers["x-row-count"] == "1"


async def test_the_cap_applies_to_the_ticker_list_too(
    tmp_path: object, monkeypatch: object
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:  # type: ignore[arg-type]
        response = await harness.client.post(
            "/screens/export",
            json={
                "mode": "symbols",
                "symbols": ["AAPL", "MSFT"],
                "format": "csv",
                "limit": 1,
            },
        )

    assert response.status_code == 200, response.text
    assert response.headers["x-row-count"] == "1"


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


async def test_an_unknown_format_is_refused_before_any_scan(
    tmp_path: object, monkeypatch: object
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:  # type: ignore[arg-type]
        response = await harness.client.post("/screens/export", json=_criteria(format="pdf"))
        scans = S.provider_requests(harness.web)

    assert response.status_code == 422, response.text
    assert response.json()["error"] == "unknown_format"
    assert scans == 0


async def test_an_unknown_mode_or_a_missing_one_is_refused(
    tmp_path: object, monkeypatch: object
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:  # type: ignore[arg-type]
        unknown = await harness.client.post(
            "/screens/export", json={"mode": "everything", "format": "csv"}
        )
        missing = await harness.client.post("/screens/export", json={"format": "csv"})

    for response in (unknown, missing):
        assert response.status_code == 422, response.text
        assert response.json()["error"] == "invalid_mode"


async def test_a_negative_limit_is_refused(tmp_path: object, monkeypatch: object) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:  # type: ignore[arg-type]
        response = await harness.client.post("/screens/export", json=_criteria(limit=-1))

    assert response.status_code == 422, response.text
    assert response.json()["error"] == "invalid_limit"
