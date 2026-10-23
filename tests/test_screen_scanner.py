"""Screen S0 RED — the TradingView scanner: one request, honest verdicts, mapping.

Gate (PLAN-screen.md §2, §6):

* one page is **one** HTTP request (bulk discovery, never a request per symbol);
* the response is positional: ``data[i].d`` is an array aligned with the
  ``columns`` the client asked for, so the parser must map **by column name** —
  a parser indexing ``d`` by hardcoded positions silently swaps volume with
  market cap when the column order changes;
* ``totalCount`` is the provider's row count, carried on the page, not guessed
  from ``len(data)``;
* an HTML page served with HTTP 200 is a **blocked** verdict, not a parse
  error and not a retry: TradingView's bot wall answers 200 + HTML, and a
  retry loop against it is how a scraper gets banned.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.contracts import VerdictKind
from tests import _screen as S


# ---------------------------------------------------------------------------
# Declared columns
# ---------------------------------------------------------------------------


def test_scanner_declares_every_field_the_contract_maps() -> None:
    declared = tuple(S.scanner().SCAN_COLUMNS)
    missing = [field for field in S.SCANNER_FIELDS if field not in declared]
    assert not missing, (
        "the scan request must ask for every field the screen row needs; missing "
        f"{missing}; declared: {list(declared)}"
    )
    for structural in ("type", "subtype", "currency"):
        assert structural in declared, (
            f"{structural!r} is required by the universe filter (PLAN-screen.md §2) "
            "and must be part of the scan columns"
        )


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def test_parse_maps_every_declared_field_by_name() -> None:
    body = S.scan_body("us")
    page = S.scanner().parse_scan(body, tuple(body["columns"]))

    assert page.total_count == S.US_RAW, (
        "totalCount is the provider's row count and must be carried verbatim; "
        f"got {page.total_count!r}, body says {body['totalCount']!r}"
    )
    assert len(page.rows) == S.US_RAW

    row = next((item for item in page.rows if item["symbol"] == "AAPL"), None)
    assert row is not None, "the parse produced no AAPL row"
    expected = S.fixture_row("us", "AAPL")
    for field in S.SCANNER_FIELDS:
        assert row[field] == expected[field], (
            f"{field}: parsed {row[field]!r}, fixture says {expected[field]!r}"
        )
    assert row["name"] == "AAPL"
    assert row["description"] == "Apple Inc."
    assert row["exchange"] == "NASDAQ"


def test_parse_maps_by_column_name_not_by_position() -> None:
    body, columns = S.shuffled_body("us")
    page = S.scanner().parse_scan(body, columns)

    row = next((item for item in page.rows if item["symbol"] == "AAPL"), None)
    assert row is not None
    expected = S.fixture_row("us", "AAPL")
    for field in ("close", "change", "volume", "Value.Traded", "market_cap_basic", "RSI"):
        assert row[field] == expected[field], (
            f"{field} was read from the wrong position: a body whose columns are "
            f"{list(columns)} produced {row[field]!r}, expected {expected[field]!r}"
        )


def test_parse_of_an_empty_body_reports_zero_without_inventing_rows() -> None:
    page = S.scanner().parse_scan({"totalCount": 0, "data": []}, tuple(S.scan_columns()))
    assert page.total_count == 0
    assert page.rows == []


# ---------------------------------------------------------------------------
# Fetch — one request per page
# ---------------------------------------------------------------------------


async def test_fetch_scans_the_us_endpoint_exactly_once(monkeypatch: Any) -> None:
    web = S.scanner_web()
    S.install_web(monkeypatch, web)

    page = await S.scanner().fetch_scan("us")

    assert page.total_count == S.US_RAW
    assert len(page.rows) == S.US_RAW
    assert S.provider_requests(web) == 1, (
        "one scan page is one request; got "
        f"{[record.url for record in web.requests]}"
    )
    record = web.requests[0]
    assert record.method == "POST", "the scanner body is a POST, not a query string"
    assert "scanner.tradingview.com" in record.host, record.host
    assert "/america/scan" in record.url, (
        f"the US tab scans the america endpoint, got {record.url}"
    )


async def test_fetch_scans_the_global_endpoint_exactly_once(monkeypatch: Any) -> None:
    web = S.scanner_web()
    S.install_web(monkeypatch, web)

    page = await S.scanner().fetch_scan("global")

    assert page.total_count == S.GLOBAL_RAW
    assert S.provider_requests(web) == 1, [record.url for record in web.requests]
    assert "/global/scan" in web.requests[0].url, web.requests[0].url


async def test_html_served_with_200_is_blocked_and_never_retried(monkeypatch: Any) -> None:
    web = S.MockWeb()
    web.html("tradingview", status=200)
    S.install_web(monkeypatch, web)

    with pytest.raises(S.scanner().ScanBlocked) as info:
        await S.scanner().fetch_scan("us")

    assert info.value.verdict is VerdictKind.BLOCKED, (
        "an HTML body is the bot wall: the verdict must be blocked, got "
        f"{info.value.verdict!r}"
    )
    assert S.provider_requests(web) == 1, (
        "a blocked response must not be retried; the scanner sent "
        f"{S.provider_requests(web)} requests"
    )
