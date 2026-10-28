"""Offline contract tests for :mod:`app.market.official`.

Everything here runs without a socket: the XLSX fixtures are generated in
memory, the NDX payload is a literal, and the transport is a fake client. The
suite-wide ``block_outbound_network`` fixture in ``conftest.py`` is the
backstop — if this module ever reaches for the network, these tests fail.
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from typing import Any

import openpyxl
import pytest

from app.market import official
from app.market.depth import DepthObservation, DepthStore
from app.market.official import (
    INDEX_FILES,
    INDEX_SPECS,
    IWM_HOLDINGS_URL,
    NDX_URL,
    NO_OPTIONS_STATUSES,
    OPTIONABLE_TTL_S,
    RUSSELL_2000_LABEL,
    SSGA_HOLDINGS_URL,
    OfficialDepthSource,
    clear_caches,
    parse_ishares_holdings,
    parse_nasdaq_ndx,
    parse_optionable_status,
    parse_ssga_holdings,
)

# ---------------------------------------------------------------------------
# Fixtures: real SSGA layout, including the preamble that shifts column rows
# ---------------------------------------------------------------------------

SPY_ROWS: list[list[Any]] = [
    ["Fund Name:", "State Street® SPDR® S&P 500® ETF Trust"],
    ["Ticker Symbol:", "SPY"],
    ["Holdings:", "As of 27-Oct-2026"],
    [],
    ["Name", "Ticker", "Identifier", "SEDOL", "Weight", "Sector", "Shares Held", "Local Currency"],
    ["NVIDIA CORP", "NVDA", "67066G104", "2379504", 7.924666, "-", 295426533.0, "USD"],
    ["APPLE INC", "AAPL", "037833100", "2046251", 7.611391, "-", 178161925.0, "USD"],
    ["MICROSOFT CORP", "MSFT", "594918104", "2588173", 5.927782, "-", 90642802.0, "USD"],
]

#: Column order permuted and the headers renamed: the parser must not assume
#: the SSGA column offsets it saw yesterday.
MDY_ROWS: list[list[Any]] = [
    ["Symbol", "Sector", "Weight (%)", "Name", "Shares Held"],
    ["KBH", "Consumer Discretionary", 0.51, "KB HOME", 4_000_000],
    ["CRL", "Health Care", 0.44, "CHARLES RIVER LABORATORIES INTL", 5_100_000],
]

DIA_ROWS: list[list[Any]] = [
    ["Fund Name:", "SPDR Dow Jones Industrial Average ETF Trust"],
    ["Ticker Symbol:", "DIA"],
    ["Holdings:", "As of 27-Oct-2026"],
    [],
    ["Name", "Ticker", "Identifier", "SEDOL", "Weight", "Sector", "Shares Held", "Local Currency"],
    ["APPLE INC", "AAPL", "037833100", "2046251", 4.5, "-", 1_000_000.0, "USD"],
    ["CASH_USD", "CASH_USD", "", "", 0.1, "-", 12_000.0, "USD"],
    ["US DOLLAR", "-", "", "", 0.0, "-", 12_000.0, "USD"],
    ["S&P 500 E-MINI FUTURE", "ESZ6", "", "", 0.2, "-", 40.0, "USD"],
    ["RUSSELL 2000 FUTURE", "RTYZ6", "", "", 0.1, "-", 10.0, "USD"],
    ["BERKSHIRE HATHAWAY CL B", "BRK.B", "084670702", "2073390", 3.2, "-", 2_000_000.0, "USD"],
    ["BERKSHIRE HATHAWAY CL B", "BRK.B", "084670702", "2073390", 3.2, "-", 2_000_000.0, "USD"],
    ["BROWN FORMAN CL B", "BF.B", "115637209", "2140742", 0.3, "-", 900_000.0, "USD"],
    ["", "", "", "", "", "", "", ""],
]

NDX_PAYLOAD: dict[str, Any] = {
    "data": {
        "totalrecords": 101,
        "date": "Oct 28, 2026 11:16 AM",
        "data": {
            "rows": [
                {"symbol": "AAPL", "companyName": "Apple Inc."},
                {"symbol": "MSFT", "companyName": "Microsoft Corporation"},
                {"symbol": "NVDA", "companyName": "NVIDIA Corporation"},
                {"symbol": "GOOGL", "companyName": "Alphabet Inc."},
                {"symbol": "", "companyName": "placeholder"},
                {"symbol": "-", "companyName": "cash"},
            ]
        },
    },
    "message": None,
    "status": {"rCode": 200, "bCode": 200},
}


def _xlsx(rows: list[list[Any]], *, sheet: str = "holdings") -> bytes:
    return _xlsx_multi([(sheet, rows)])


def _xlsx_multi(sheets: list[tuple[str, list[list[Any]]]]) -> bytes:
    workbook = openpyxl.Workbook()
    first = True
    for name, rows in sheets:
        worksheet = workbook.worksheets[0] if first else workbook.create_sheet()
        worksheet.title = name
        for row in rows:
            worksheet.append(list(row))
        first = False
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


SPY_BYTES = _xlsx(SPY_ROWS)
MDY_BYTES = _xlsx(MDY_ROWS)
DIA_BYTES = _xlsx(DIA_ROWS)

#: Real IWM layout: an eight-row fund preamble (name, as-of, inception, shares
#: outstanding, then Stock/Bond/Cash/Other), a blank line, then the header. The
#: file lists money-market, cash-collateral, currency and futures rows too, so
#: ``Asset Class`` — not the ticker shape — decides membership.
IWM_PREAMBLE = (
    "iShares Russell 2000 ETF\n"
    'Fund Holdings as of,"Oct 27, 2026"\n'
    'Inception Date,"May 22, 2000"\n'
    'Shares Outstanding,"269,550,000.00"\n'
    'Stock,"-"\n'
    'Bond,"-"\n'
    'Cash,"-"\n'
    'Other,"-"\n'
    "\n"
)

IWM_HEADER = (
    "Ticker,Name,Sector,Asset Class,Market Value,Weight (%)\n"
)

IWM_CSV = (
    IWM_PREAMBLE
    + IWM_HEADER
    + '"FROG","JFROG","Information Technology","Equity","277,900,790.18","0.37"\n'
    + '"TWST","TWIST, BIOSCIENCE","Health Care","Equity","268,309,343.95","0.35"\n'
    + '"GKOS","GLAUKOS","Health Care","Equity","264,666,180.24","0.35"\n'
    + '"MOG A","MOOG INC CLASS A","Industrials","Equity","256,639,775.76","0.34"\n'
    + '"BH A","BIGLARI HOLDINGS CLASS A","Industrials","Equity","1,000,000.00","0.01"\n'
    + '"XTSLA","BLK CSH FND TREASURY SL AGENCY","Money Market","Money Market",'
    '"1,200,000.00","0.00"\n'
    + '"MSFUT","CASH COLLATERAL MSFUT USD","Cash Collateral and Margins",'
    '"Cash Collateral and Margins","1,100,000.00","0.00"\n'
    + '"CAD","CAD CASH","Cash and/or Derivatives","Cash","-25,000.00","-0.01"\n'
    + '"USD","USD CASH","Cash and/or Derivatives","Cash","-27,876,332.61","-0.04"\n'
    + '"RTYZ6","RUSSELL 2000 EMINI CME DEC 26","Cash and/or Derivatives","Futures",'
    '"167,022,925.00","0.00"\n'
    + '"FROG","JFROG","Information Technology","Equity","277,900,790.18","0.37"\n'
    + '"","BLANK TICKER","-","Equity","0.00","0.00"\n'
    + '"","","","Equity","0.00","0.00"\n'
    + '"-","-","Cash and/or Derivatives","Equity","0.00","0.00"\n'
)

IWM_BYTES = IWM_CSV.encode("utf-8")

#: Every equity row above, canonicalised; the cash/collateral/currency/future
#: rows and the duplicate ``FROG`` line are not members.
IWM_MEMBERS = {"FROG", "TWST", "GKOS", "MOG-A", "BH-A"}


@pytest.fixture(autouse=True)
def _no_index_cache() -> Any:
    clear_caches()
    yield
    clear_caches()


# ---------------------------------------------------------------------------
# parse_ssga_holdings
# ---------------------------------------------------------------------------


def test_parse_spy_workbook_skips_preamble_and_header() -> None:
    assert parse_ssga_holdings(SPY_BYTES, "S&P 500") == {"AAPL", "MSFT", "NVDA"}


def test_parse_mdy_workbook_finds_permuted_header_by_name() -> None:
    assert parse_ssga_holdings(MDY_BYTES, "S&P 400") == {"KBH", "CRL"}


def test_parse_dia_workbook_drops_cash_futures_dashes_and_normalizes_class() -> None:
    found = parse_ssga_holdings(DIA_BYTES, "DJIA")
    assert found == {"AAPL", "BRK-B", "BF-B"}
    assert "CASH_USD" not in found
    assert "ESZ6" not in found
    assert "RTYZ6" not in found
    assert "-" not in found


def test_parse_dia_normalizes_dotted_class_to_dashed() -> None:
    # BRK.B and BF.B arrive dotted; the depth store keys them dashed.
    found = parse_ssga_holdings(DIA_BYTES, "DJIA")
    assert "BRK.B" not in found
    assert "BRK-B" in found


def test_parse_duplicate_rows_collapse() -> None:
    rows = [
        ["Name", "Ticker", "Weight"],
        ["APPLE INC", "AAPL", 7.6],
        ["APPLE INC", "AAPL", 7.6],
        ["MICROSOFT CORP", "MSFT", 5.9],
    ]
    assert parse_ssga_holdings(_xlsx(rows), "S&P 500") == {"AAPL", "MSFT"}


def test_parse_accepts_latin1_decoded_string() -> None:
    decoded = SPY_BYTES.decode("latin-1")
    assert parse_ssga_holdings(decoded, "S&P 500") == {"AAPL", "MSFT", "NVDA"}


def test_parse_accepts_delimited_text_payload() -> None:
    text = "Name,Ticker,Weight\nAPPLE INC,AAPL,7.6\nBRK,BRK.B,1.2\n"
    assert parse_ssga_holdings(text, "S&P 500") == {"AAPL", "BRK-B"}


def test_parse_empty_payload_is_empty_set() -> None:
    assert parse_ssga_holdings(b"", "S&P 500") == set()
    assert parse_ssga_holdings("", "S&P 500") == set()


def test_parse_corrupt_payload_is_empty_set_not_exception() -> None:
    assert parse_ssga_holdings(b"not a zip at all", "S&P 500") == set()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "no worksheets here")
    assert parse_ssga_holdings(buffer.getvalue(), "S&P 500") == set()


def test_parse_zip_without_ticker_header_is_empty_set() -> None:
    rows = [["Name", "Identifier", "Weight"], ["APPLE INC", "037833100", 7.6]]
    assert parse_ssga_holdings(_xlsx(rows), "S&P 500") == set()


def test_parse_rejects_empty_index_name() -> None:
    with pytest.raises(ValueError):
        parse_ssga_holdings(SPY_BYTES, "   ")


def test_parse_relaxed_header_ignores_two_cell_ticker_symbol_preamble() -> None:
    rows = [
        ["Ticker Symbol:", "SPY"],
        ["Name", "Ticker", "Sector"],
        ["APPLE INC", "AAPL", "Technology"],
    ]
    assert parse_ssga_holdings(_xlsx(rows), "S&P 500") == {"AAPL"}


def test_parse_skips_numeric_and_short_rows() -> None:
    rows = [
        ["Name", "Ticker", "Weight"],
        ["APPLE INC", "AAPL", 7.6],
        ["NUMBER", 12345, 0.1],
        ["SHORT ROW"],
        ["MICROSOFT CORP", "MSFT", 5.9],
    ]
    assert parse_ssga_holdings(_xlsx(rows), "S&P 500") == {"AAPL", "MSFT"}


def test_parse_reads_inline_string_worksheet() -> None:
    """A workbook written by another tool may use inline strings, not a table."""

    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        '<row r="1">'
        '<c r="A1" t="inlineStr"><is><t>Name</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>Ticker</t></is></c>'
        '<c r="C1" t="inlineStr"><is><t>Weight</t></is></c>'
        "</row>"
        '<row r="2">'
        '<c r="A2" t="inlineStr"><is><t>APPLE INC</t></is></c>'
        '<c r="B2" t="inlineStr"><is><t>AAPL</t></is></c>'
        '<c r="C2"><v>7.6</v></c>'
        "</row>"
        '<row r="3">'
        '<c r="A3" t="inlineStr"><is><t>BERKSHIRE HATHAWAY CL B</t></is></c>'
        '<c r="B3" t="inlineStr"><is><t>BRK.B</t></is></c>'
        '<c r="C3"><v>1.2</v></c>'
        "</row>"
        "</sheetData></worksheet>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="holdings" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    assert parse_ssga_holdings(buffer.getvalue(), "S&P 500") == {"AAPL", "BRK-B"}


def test_parse_does_not_invent_a_label_from_a_header_only_file() -> None:
    rows = [["Name", "Ticker", "Weight"]]
    assert parse_ssga_holdings(_xlsx(rows), "S&P 500") == set()


def test_parse_scans_past_a_cover_sheet() -> None:
    """A disclaimer sheet in front of the holdings must not blind the reader."""

    payload = _xlsx_multi(
        [
            ("disclaimer", [["Important"], ["Read the prospectus"]]),
            ("holdings", [["Name", "Ticker", "Weight"], ["APPLE INC", "AAPL", 7.6]]),
        ]
    )
    assert parse_ssga_holdings(payload, "S&P 500") == {"AAPL"}


# ---------------------------------------------------------------------------
# parse_ishares_holdings (Russell 2000 / IWM)
# ---------------------------------------------------------------------------


def test_parse_ishares_returns_only_equity_rows() -> None:
    found = parse_ishares_holdings(IWM_CSV)

    assert found == IWM_MEMBERS
    # The two-page preamble's Stock/Bond/Cash/Other labels are not rows.
    for junk in ("XTSLA", "MSFUT", "CAD", "USD", "RTYZ6", "-", "CASH"):
        assert junk not in found


def test_parse_ishares_accepts_bytes_and_str_identically() -> None:
    assert parse_ishares_holdings(IWM_BYTES) == parse_ishares_holdings(IWM_CSV) == IWM_MEMBERS


def test_parse_ishares_normalizes_space_and_dot_class_suffixes() -> None:
    assert "MOG-A" in parse_ishares_holdings(IWM_CSV)
    assert "BH-A" in parse_ishares_holdings(IWM_CSV)

    text = (
        "Ticker,Name,Sector,Asset Class\n"
        '"BRK.B","BERKSHIRE HATHAWAY CL B","Financials","Equity"\n'
        '"BF.B","BROWN FORMAN CL B","Consumer Staples","Equity"\n'
        '"MOG A","MOOG INC CLASS A","Industrials","Equity"\n'
    )
    assert parse_ishares_holdings(text) == {"BRK-B", "BF-B", "MOG-A"}


def test_parse_ishares_drops_every_non_equity_asset_class() -> None:
    text = (
        "Ticker,Name,Sector,Asset Class\n"
        + "".join(
            f'"T{index}","ROW {index}","-","{asset_class}"\n'
            for index, asset_class in enumerate(
                [
                    "Cash",
                    "Cash Collateral and Margins",
                    "Money Market",
                    "Futures",
                    "Bond",
                    "Other",
                    "Derivative",
                    "",
                ]
            )
        )
        + '"KEEP","KEPT ROW","-","Equity"\n'
    )
    assert parse_ishares_holdings(text) == {"KEEP"}


def test_parse_ishares_duplicate_rows_collapse() -> None:
    text = (
        "Ticker,Name,Sector,Asset Class\n"
        '"FROG","JFROG","Information Technology","Equity"\n'
        '"FROG","JFROG","Information Technology","Equity"\n'
        '"TWST","TWIST BIOSCIENCE","Health Care","Equity"\n'
    )
    assert parse_ishares_holdings(text) == {"FROG", "TWST"}


def test_parse_ishares_handles_quoted_commas_and_embedded_quotes() -> None:
    text = (
        "Ticker,Name,Sector,Asset Class\n"
        '"TWST","TWIST, BIOSCIENCE, INC.","Health Care","Equity"\n'
        '"ACME","ACME ""CLASS A"" HOLDINGS","Industrials","Equity"\n'
    )
    assert parse_ishares_holdings(text) == {"TWST", "ACME"}


def test_parse_ishares_header_is_found_by_name_at_any_preamble_depth() -> None:
    header = "Ticker,Name,Sector,Asset Class\n"
    body = '"FROG","JFROG","Information Technology","Equity"\n'

    for depth in (0, 1, 3, 8, 12):
        preamble = "".join(f'Label {index},"value {index}"\n' for index in range(depth))
        assert parse_ishares_holdings(preamble + header + body) == {"FROG"}, depth


def test_parse_ishares_follows_a_renamed_or_reordered_header() -> None:
    # Ticker last, and the asset column spelled "Asset Class" with a trailing
    # space; the parser locates both by name.
    text = (
        "Name,Sector,Asset Class ,Weight (%),Ticker\n"
        '"JFROG","Information Technology","Equity","0.37","FROG"\n'
        '"USD CASH","Cash and/or Derivatives","Cash","-0.04","USD"\n'
    )
    assert parse_ishares_holdings(text) == {"FROG"}


def test_parse_ishares_accepts_a_symbol_headed_file() -> None:
    text = (
        "Symbol,Name,Asset Class\n"
        '"AAPL","APPLE INC","Equity"\n'
    )
    assert parse_ishares_holdings(text) == {"AAPL"}


def test_parse_ishares_tolerates_crlf_and_bom() -> None:
    crlf = IWM_CSV.replace("\n", "\r\n")
    assert parse_ishares_holdings(crlf.encode("utf-8")) == IWM_MEMBERS
    assert parse_ishares_holdings(b"\xef\xbb\xbf" + crlf.encode("utf-8")) == IWM_MEMBERS


def test_parse_ishares_lowercase_tickers_are_upcased() -> None:
    text = (
        "Ticker,Name,Sector,Asset Class\n"
        '"frog","JFROG","Information Technology","Equity"\n'
        '"mog a","MOOG INC CLASS A","Industrials","Equity"\n'
    )
    assert parse_ishares_holdings(text) == {"FROG", "MOG-A"}


def test_parse_ishares_drops_blank_cusip_and_dash_tickers() -> None:
    text = (
        "Ticker,Name,Sector,Asset Class\n"
        '"","BLANK","-","Equity"\n'
        '"037833100","APPLE INC","-","Equity"\n'
        '"P5N994","PLACEHOLDER","-","Equity"\n'
        '"12345","NUMERIC","-","Equity"\n'
        '"CASH","CASH","-","Equity"\n'
        '"USD","USD CASH","-","Equity"\n'
        '"-","-","-","Equity"\n'
        '"BAD,"BROKEN QUOTE","-","Equity"\n'
        '"MSFT","MICROSOFT CORP","-","Equity"\n'
    )
    assert parse_ishares_holdings(text) == {"MSFT"}


def test_parse_ishares_row_shorter_than_the_ticker_column_is_skipped() -> None:
    text = (
        "Ticker,Name,Sector,Asset Class\n"
        "FROG\n"
        ",Name,Sector,Equity\n"
        '"TWST","TWIST BIOSCIENCE","Health Care","Equity"\n'
    )
    assert parse_ishares_holdings(text) == {"TWST"}


def test_parse_ishares_row_shorter_than_the_asset_column_is_skipped() -> None:
    text = (
        "Ticker,Name,Sector,Asset Class\n"
        '"FROG","JFROG"\n'
        '"TWST","TWIST BIOSCIENCE","Health Care","Equity"\n'
    )
    assert parse_ishares_holdings(text) == {"TWST"}


def test_parse_ishares_preamble_without_a_header_is_empty_set() -> None:
    assert parse_ishares_holdings(IWM_PREAMBLE) == set()
    assert parse_ishares_holdings(IWM_PREAMBLE + "FROG,JFROG\n") == set()


def test_parse_ishares_empty_and_invalid_payloads_are_empty_sets() -> None:
    assert parse_ishares_holdings(b"") == set()
    assert parse_ishares_holdings("") == set()
    assert parse_ishares_holdings("   \n\n") == set()
    assert parse_ishares_holdings(b"\x00\x01\x02\xff") == set()
    assert parse_ishares_holdings("<html><body>403 Forbidden</body></html>") == set()
    assert parse_ishares_holdings("<html>Ticker,Name,Sector,Asset Class</html>") == set()


def test_parse_ishares_header_only_file_is_empty_set() -> None:
    assert parse_ishares_holdings(IWM_PREAMBLE + IWM_HEADER) == set()


def test_parse_ishares_rejects_empty_index_name() -> None:
    with pytest.raises(ValueError):
        parse_ishares_holdings(IWM_CSV, "   ")


def test_parse_ishares_label_defaults_to_russell_2000() -> None:
    assert RUSSELL_2000_LABEL == "Russell 2000"
    assert parse_ishares_holdings(IWM_CSV, RUSSELL_2000_LABEL) == parse_ishares_holdings(IWM_CSV)


def test_index_specs_register_russell_2000_as_a_csv() -> None:
    spec = next(spec for spec in INDEX_SPECS if spec.name == RUSSELL_2000_LABEL)
    assert spec.kind == "csv"
    assert spec.url == IWM_HOLDINGS_URL
    assert IWM_HOLDINGS_URL == (
        "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/"
        "latest-holdings.csv"
    )


# ---------------------------------------------------------------------------
# parse_nasdaq_ndx
# ---------------------------------------------------------------------------


def test_parse_ndx_nested_payload() -> None:
    assert parse_nasdaq_ndx(NDX_PAYLOAD) == {"AAPL", "MSFT", "NVDA", "GOOGL"}


def test_parse_ndx_flat_rows_and_bare_strings() -> None:
    payload = {"data": {"rows": [{"Symbol": "AAPL"}, {"ticker": "MSFT"}, "NVDA"]}}
    assert parse_nasdaq_ndx(payload) == {"AAPL", "MSFT", "NVDA"}


def test_parse_ndx_empty_payloads() -> None:
    assert parse_nasdaq_ndx({}) == set()
    assert parse_nasdaq_ndx({"data": None}) == set()
    assert parse_nasdaq_ndx({"data": {"data": {"rows": []}}}) == set()
    assert parse_nasdaq_ndx({"status": {"rCode": 200}}) == set()


def test_parse_ndx_non_mapping_is_empty_set() -> None:
    assert parse_nasdaq_ndx(None) == set()  # type: ignore[arg-type]
    assert parse_nasdaq_ndx(["AAPL"]) == set()  # type: ignore[arg-type]


def test_parse_ndx_drops_cash_and_junk_rows() -> None:
    payload = {"data": {"rows": [{"symbol": "AAPL"}, {"symbol": "-"}, {"symbol": "CASH"}]}}
    assert parse_nasdaq_ndx(payload) == {"AAPL"}


# ---------------------------------------------------------------------------
# parse_optionable_status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, True),
        (403, False),
        (404, None),
        (301, None),
        (429, None),
        (500, None),
        (0, None),
    ],
)
def test_parse_optionable_status_matrix(status: int, expected: bool | None) -> None:
    assert parse_optionable_status(status) is expected


def test_parse_optionable_status_rejects_junk() -> None:
    assert parse_optionable_status("nope") is None  # type: ignore[arg-type]
    assert parse_optionable_status(None) is None  # type: ignore[arg-type]


def test_no_options_statuses_cover_403_and_404() -> None:
    assert NO_OPTIONS_STATUSES == frozenset({403, 404})


# ---------------------------------------------------------------------------
# Fake transport
# ---------------------------------------------------------------------------


class _Response:
    def __init__(self, status_code: int = 200, content: bytes = b"", payload: Any = None) -> None:
        self.status_code = status_code
        self.content = content
        self._payload = payload

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no JSON body in this fake response")
        return self._payload


class _Client:
    def __init__(self, routes: dict[str, Any], calls: list[tuple[str, str]]) -> None:
        self._routes = routes
        self._calls = calls
        self.closed = False

    def _resolve(self, method: str, url: str) -> _Response:
        self._calls.append((method, url))
        route = self._routes.get(url)
        if route is None:
            raise AssertionError(f"unexpected {method} {url}")
        if isinstance(route, Exception):
            raise route
        return route

    async def get(self, url: str) -> _Response:
        return self._resolve("GET", url)

    async def head(self, url: str) -> _Response:
        return self._resolve("HEAD", url)

    async def __aenter__(self) -> "_Client":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self.closed = True
        return False


class _Factory:
    """Records every request so a test can assert the exact official URLs."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str]] = []
        self.kwargs: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> _Client:
        self.kwargs.append(kwargs)
        return _Client(self.routes, self.calls)

    def count(self, method: str, url: str | None = None) -> int:
        return sum(
            1
            for seen_method, seen_url in self.calls
            if seen_method == method and (url is None or seen_url == url)
        )


class _Clock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _xlsx_route(rows: list[list[Any]]) -> _Response:
    return _Response(200, _xlsx(rows))


def _csv_route(text: str | bytes = IWM_BYTES) -> _Response:
    return _Response(200, text.encode("utf-8") if isinstance(text, str) else text)


def _index_routes(
    *, spy: Any = None, mdy: Any = None, russell: Any = None
) -> dict[str, Any]:
    return {
        SSGA_HOLDINGS_URL.format(fund="spy"): spy or _xlsx_route(SPY_ROWS),
        SSGA_HOLDINGS_URL.format(fund="mdy"): mdy or _xlsx_route(MDY_ROWS),
        SSGA_HOLDINGS_URL.format(fund="spsm"): _xlsx_route(
            [["Name", "Ticker", "Weight"], ["CROCS", "CROX", 0.4]]
        ),
        SSGA_HOLDINGS_URL.format(fund="dia"): _xlsx_route(DIA_ROWS),
        IWM_HOLDINGS_URL: russell or _csv_route(),
        NDX_URL: _Response(200, payload=NDX_PAYLOAD),
    }


CBOE = official.CBOE_OPTION_URL


def _cboe(statuses: dict[str, int]) -> dict[str, Any]:
    return {CBOE.format(symbol=symbol): _Response(status) for symbol, status in statuses.items()}


# ---------------------------------------------------------------------------
# OfficialDepthSource with injected loaders
# ---------------------------------------------------------------------------


async def test_source_emits_comma_sorted_membership_and_optionable() -> None:
    source = OfficialDepthSource(
        index_loader=lambda: {"S&P 500": {"AAPL"}, "NDX": {"AAPL"}, "DJIA": {"AAPL"}},
        optionable_checker=lambda symbol: True,
    )
    result = await source.fetch_many(["AAPL"])

    observation = result["AAPL"]
    assert observation.fields["index_membership"] == "DJIA, NDX, S&P 500"
    assert observation.fields["optionable"] == "Yes"
    assert observation.source == "official"
    assert observation.as_of.tzinfo is not None


async def test_source_emits_no_as_no() -> None:
    source = OfficialDepthSource(
        index_loader=lambda: {}, optionable_checker=lambda symbol: False
    )
    result = await source.fetch_many(["AAPL"])
    assert result["AAPL"].fields == {"optionable": "No"}


async def test_source_missing_optionable_is_absent_not_zero_filled() -> None:
    source = OfficialDepthSource(
        index_loader=lambda: {"S&P 500": {"AAPL"}},
        optionable_checker=lambda symbol: None,
    )
    result = await source.fetch_many(["AAPL"])

    assert result["AAPL"].fields == {"index_membership": "S&P 500"}
    assert "optionable" not in result["AAPL"].fields


async def test_source_symbol_with_neither_field_is_absent() -> None:
    source = OfficialDepthSource(
        index_loader=lambda: {"S&P 500": {"AAPL"}},
        optionable_checker=lambda symbol: None,
    )
    result = await source.fetch_many(["AAPL", "ZZZZ"])

    assert set(result) == {"AAPL"}


async def test_source_normalizes_dotted_symbols() -> None:
    source = OfficialDepthSource(
        index_loader=lambda: {"DJIA": {"BRK.B"}},
        optionable_checker=lambda symbol: True,
    )
    result = await source.fetch_many(["brk-b", "BRK.B"])

    assert set(result) == {"BRK-B"}
    assert result["BRK-B"].fields["index_membership"] == "DJIA"


async def test_source_deduplicates_symbols() -> None:
    seen: list[str] = []

    def checker(symbol: str) -> bool:
        seen.append(symbol)
        return True

    source = OfficialDepthSource(index_loader=lambda: {}, optionable_checker=checker)
    result = await source.fetch_many(["AAPL", "aapl", " AAPL "])

    assert seen == ["AAPL"]
    assert list(result) == ["AAPL"]


async def test_source_returns_empty_mapping_for_no_symbols() -> None:
    factory = _Factory(_index_routes())
    source = OfficialDepthSource(client_factory=factory)
    assert await source.fetch_many([]) == {}
    assert factory.calls == []


async def test_source_skips_blank_symbols() -> None:
    source = OfficialDepthSource(
        index_loader=lambda: {"S&P 500": {"AAPL"}},
        optionable_checker=lambda symbol: True,
    )
    result = await source.fetch_many(["", "   ", None, "AAPL"])  # type: ignore[list-item]

    assert set(result) == {"AAPL"}


async def test_source_one_optionable_failure_is_isolated() -> None:
    def checker(symbol: str) -> bool:
        if symbol == "BAD":
            raise RuntimeError("cboe timeout")
        return True

    source = OfficialDepthSource(
        index_loader=lambda: {"S&P 500": {"AAPL", "BAD", "MSFT"}},
        optionable_checker=checker,
    )
    result = await source.fetch_many(["AAPL", "BAD", "MSFT"])

    assert set(result) == {"AAPL", "BAD", "MSFT"}
    # The failed probe removes the field, not the symbol's other field.
    assert result["BAD"].fields == {"index_membership": "S&P 500"}
    assert result["AAPL"].fields["optionable"] == "Yes"
    assert result["MSFT"].fields["optionable"] == "Yes"


async def test_source_index_loader_failure_leaves_optionable_only() -> None:
    def loader() -> dict[str, set[str]]:
        raise RuntimeError("ssga down")

    source = OfficialDepthSource(index_loader=loader, optionable_checker=lambda symbol: True)
    result = await source.fetch_many(["AAPL"])

    assert result["AAPL"].fields == {"optionable": "Yes"}


async def test_source_index_loader_garbage_is_ignored() -> None:
    source = OfficialDepthSource(
        index_loader=lambda: None, optionable_checker=lambda symbol: True
    )
    result = await source.fetch_many(["AAPL"])
    assert result["AAPL"].fields == {"optionable": "Yes"}


async def test_source_awaits_async_loader_and_checker() -> None:
    async def loader() -> dict[str, set[str]]:
        await asyncio.sleep(0)
        return {"NDX": {"AAPL"}}

    async def checker(symbol: str) -> bool:
        await asyncio.sleep(0)
        return True

    source = OfficialDepthSource(index_loader=loader, optionable_checker=checker)
    result = await source.fetch_many(["AAPL"])

    assert result["AAPL"].fields == {"index_membership": "NDX", "optionable": "Yes"}


async def test_source_only_emits_the_two_owned_fields() -> None:
    source = OfficialDepthSource(
        index_loader=lambda: {"S&P 500": {"AAPL"}, "NDX": {"AAPL"}},
        optionable_checker=lambda symbol: True,
    )
    result = await source.fetch_many(["AAPL"])

    for observation in result.values():
        assert isinstance(observation, DepthObservation)
        assert set(observation.fields) <= {"index_membership", "optionable"}


async def test_source_observation_merges_into_depth_store_with_two_fields() -> None:
    source = OfficialDepthSource(
        index_loader=lambda: {"S&P 500": {"AAPL"}, "NDX": {"AAPL"}},
        optionable_checker=lambda symbol: True,
    )
    store = DepthStore()
    for observation in (await source.fetch_many(["AAPL"])).values():
        store.merge(observation, priority=0)

    document = store.document(["AAPL"])
    assert document["symbols"]["AAPL"] == {
        "index_membership": "NDX, S&P 500",
        "optionable": "Yes",
    }


async def test_source_concurrency_is_capped_at_four() -> None:
    in_flight = 0
    peak = 0

    async def checker(symbol: str) -> bool:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return True

    symbols = [f"S{index:02d}" for index in range(24)]
    source = OfficialDepthSource(
        index_loader=lambda: {}, optionable_checker=checker, concurrency=4
    )
    result = await source.fetch_many(symbols)

    assert peak <= 4
    assert len(result) == len(symbols)


async def test_source_uses_four_workers_by_default() -> None:
    assert OfficialDepthSource().concurrency == 4
    assert OfficialDepthSource().optionable_ttl_s == pytest.approx(OPTIONABLE_TTL_S)
    assert OPTIONABLE_TTL_S == pytest.approx(6 * 60 * 60)
    assert OfficialDepthSource().name == "official"


# ---------------------------------------------------------------------------
# Optionable cache
# ---------------------------------------------------------------------------


async def test_optionable_cache_serves_six_hours_then_refetches() -> None:
    clock = _Clock()
    calls: list[str] = []

    def checker(symbol: str) -> bool:
        calls.append(symbol)
        return True

    source = OfficialDepthSource(
        index_loader=lambda: {}, optionable_checker=checker, clock=clock
    )

    await source.fetch_many(["AAPL"])
    await source.fetch_many(["AAPL"])
    assert calls == ["AAPL"]

    clock.advance(OPTIONABLE_TTL_S - 1)
    await source.fetch_many(["AAPL"])
    assert calls == ["AAPL"]

    clock.advance(2)
    await source.fetch_many(["AAPL"])
    assert calls == ["AAPL", "AAPL"]


async def test_unknown_optionable_is_not_cached() -> None:
    clock = _Clock()
    calls: list[str] = []

    def checker(symbol: str) -> bool | None:
        calls.append(symbol)
        return None

    source = OfficialDepthSource(
        index_loader=lambda: {}, optionable_checker=checker, clock=clock
    )

    await source.fetch_many(["AAPL"])
    await source.fetch_many(["AAPL"])
    assert calls == ["AAPL", "AAPL"]


async def test_reset_clears_optionable_cache() -> None:
    calls: list[str] = []

    def checker(symbol: str) -> bool:
        calls.append(symbol)
        return True

    source = OfficialDepthSource(index_loader=lambda: {}, optionable_checker=checker)
    await source.fetch_many(["AAPL"])
    source.reset()
    await source.fetch_many(["AAPL"])

    assert calls == ["AAPL", "AAPL"]


# ---------------------------------------------------------------------------
# Production transport path (fake client, real URLs)
# ---------------------------------------------------------------------------


async def test_source_fetches_official_urls_and_labels_membership() -> None:
    routes = _index_routes()
    routes.update(_cboe({"AAPL": 200, "MMM": 403, "KBH": 404}))
    factory = _Factory(routes)
    source = OfficialDepthSource(client_factory=factory)

    result = await source.fetch_many(["AAPL", "MMM", "KBH"])

    assert result["AAPL"].fields == {"index_membership": "DJIA, NDX, S&P 500", "optionable": "Yes"}
    assert result["MMM"].fields == {"optionable": "No"}
    assert result["KBH"].fields == {"index_membership": "S&P 400", "optionable": "No"}

    for _name, fund in INDEX_FILES:
        assert factory.count("GET", SSGA_HOLDINGS_URL.format(fund=fund)) == 1
    assert factory.count("GET", NDX_URL) == 1
    assert factory.count("GET", IWM_HOLDINGS_URL) == 1
    assert factory.count("GET") == 6
    assert factory.count("HEAD") == 3
    assert all("timeout_s" in kwargs for kwargs in factory.kwargs)


async def test_source_labels_russell_2000_membership_from_the_ishares_csv() -> None:
    routes = _index_routes()
    routes.update(_cboe({"FROG": 200}))
    factory = _Factory(routes)
    source = OfficialDepthSource(client_factory=factory)

    result = await source.fetch_many(["FROG"])

    assert result["FROG"].fields == {"index_membership": "Russell 2000", "optionable": "Yes"}
    assert factory.count("GET", IWM_HOLDINGS_URL) == 1


async def test_source_matches_a_space_spelled_class_to_the_canonical_symbol() -> None:
    routes = _index_routes()
    routes.update(_cboe({"MOG.A": 200}))
    factory = _Factory(routes)
    source = OfficialDepthSource(client_factory=factory)

    result = await source.fetch_many(["mog a"])

    assert set(result) == {"MOG-A"}
    assert result["MOG-A"].fields["index_membership"] == "Russell 2000"


async def test_source_does_not_label_russell_2000_for_a_non_equity_row() -> None:
    routes = _index_routes()
    routes.update(_cboe({"XTSLA": 403, "USD": 403, "RTYZ6": 403}))
    factory = _Factory(routes)
    source = OfficialDepthSource(client_factory=factory)

    result = await source.fetch_many(["XTSLA", "USD", "RTYZ6"])

    for symbol in ("XTSLA", "USD", "RTYZ6"):
        assert result[symbol].fields == {"optionable": "No"}


@pytest.mark.parametrize("status", [403, 404, 500, 502])
async def test_source_does_not_label_russell_2000_when_ishares_fails(status: int) -> None:
    routes = _index_routes(russell=_Response(status))
    routes.update(_cboe({"FROG": 200}))
    factory = _Factory(routes)
    source = OfficialDepthSource(client_factory=factory)

    result = await source.fetch_many(["FROG"])

    assert result["FROG"].fields == {"optionable": "Yes"}
    assert "index_membership" not in result["FROG"].fields


async def test_source_does_not_label_russell_2000_from_an_html_error_page() -> None:
    routes = _index_routes(
        russell=_Response(200, b"<html><body>Access denied</body></html>")
    )
    routes.update(_cboe({"FROG": 200}))
    factory = _Factory(routes)
    source = OfficialDepthSource(client_factory=factory)

    result = await source.fetch_many(["FROG"])

    assert result["FROG"].fields == {"optionable": "Yes"}


async def test_source_does_not_label_russell_2000_when_the_csv_is_empty() -> None:
    routes = _index_routes(russell=_Response(200, IWM_PREAMBLE.encode("utf-8")))
    routes.update(_cboe({"FROG": 200}))
    factory = _Factory(routes)
    source = OfficialDepthSource(client_factory=factory)

    result = await source.fetch_many(["FROG"])

    assert "index_membership" not in result["FROG"].fields


async def test_source_survives_a_network_exception_on_the_ishares_file() -> None:
    routes = _index_routes(russell=ConnectionResetError("ishares reset"))
    routes.update(_cboe({"TWST": 200}))
    factory = _Factory(routes)
    source = OfficialDepthSource(client_factory=factory)

    result = await source.fetch_many(["TWST"])

    assert result["TWST"].fields == {"optionable": "Yes"}


async def test_source_maps_dashed_class_to_dotted_cboe_url() -> None:
    routes = _index_routes()
    routes.update({CBOE.format(symbol="BRK.B"): _Response(200)})
    factory = _Factory(routes)
    source = OfficialDepthSource(client_factory=factory)

    result = await source.fetch_many(["BRK-B"])

    assert result["BRK-B"].fields["optionable"] == "Yes"
    assert factory.count("HEAD", CBOE.format(symbol="BRK.B")) == 1


async def test_source_leaves_301_and_500_as_unknown() -> None:
    routes = _index_routes()
    routes.update(_cboe({"AAPL": 301, "MSFT": 500}))
    factory = _Factory(routes)
    source = OfficialDepthSource(client_factory=factory)

    result = await source.fetch_many(["AAPL", "MSFT"])

    for symbol in ("AAPL", "MSFT"):
        assert "optionable" not in result[symbol].fields
        assert result[symbol].fields["index_membership"]


async def test_source_does_not_label_sp500_when_the_file_is_empty() -> None:
    empty_spy = _Response(200, _xlsx([["Name", "Ticker", "Weight"]]))
    routes = _index_routes(spy=empty_spy)
    routes.update(_cboe({"AAPL": 200}))
    factory = _Factory(routes)
    source = OfficialDepthSource(client_factory=factory)

    result = await source.fetch_many(["AAPL"])

    # AAPL is in DIA and NDX; the empty SPY workbook must not add the S&P label.
    assert result["AAPL"].fields["index_membership"] == "DJIA, NDX"
    assert result["AAPL"].fields["optionable"] == "Yes"
    assert "S&P 500" not in result["AAPL"].fields["index_membership"]


async def test_source_does_not_label_an_index_whose_file_500s() -> None:
    routes = _index_routes(spy=_Response(500))
    routes.update(_cboe({"AAPL": 200}))
    factory = _Factory(routes)
    source = OfficialDepthSource(client_factory=factory)

    result = await source.fetch_many(["AAPL"])

    assert "index_membership" in result["AAPL"].fields
    assert "S&P 500" not in result["AAPL"].fields["index_membership"]
    assert result["AAPL"].fields["index_membership"] == "DJIA, NDX"


async def test_source_survives_a_transport_that_cannot_be_built() -> None:
    def factory(**kwargs: Any) -> Any:
        raise RuntimeError("httpx2 unavailable")

    source = OfficialDepthSource(client_factory=factory)
    assert await source.fetch_many(["AAPL"]) == {}

    source = OfficialDepthSource(
        index_loader=lambda: {"S&P 500": {"AAPL"}}, client_factory=factory
    )
    result = await source.fetch_many(["AAPL"])
    assert result["AAPL"].fields == {"index_membership": "S&P 500"}


async def test_source_survives_a_network_exception_on_one_index() -> None:
    routes = _index_routes(spy=ConnectionResetError("ssga reset"))
    routes.update(_cboe({"KBH": 200}))
    factory = _Factory(routes)
    source = OfficialDepthSource(client_factory=factory)

    result = await source.fetch_many(["KBH"])

    assert result["KBH"].fields == {"index_membership": "S&P 400", "optionable": "Yes"}


async def test_source_survives_a_network_exception_on_one_symbol() -> None:
    routes = _index_routes()
    routes.update(
        {
            CBOE.format(symbol="AAPL"): TimeoutError("cboe slow"),
            CBOE.format(symbol="MMM"): _Response(200),
        }
    )
    factory = _Factory(routes)
    source = OfficialDepthSource(client_factory=factory)

    result = await source.fetch_many(["AAPL", "MMM"])

    # AAPL's Cboe probe raised, so it keeps its membership row and loses only
    # the optionable field; the batch still returns MMM.
    assert "optionable" not in result["AAPL"].fields
    assert result["AAPL"].fields["index_membership"] == "DJIA, NDX, S&P 500"
    assert result["MMM"].fields["optionable"] == "Yes"


# ---------------------------------------------------------------------------
# Index cache (opt-in, still offline)
# ---------------------------------------------------------------------------


async def test_index_cache_avoids_refetching_within_the_day() -> None:
    clock = _Clock()
    factory = _Factory(_index_routes())
    source = OfficialDepthSource(
        client_factory=factory,
        optionable_checker=lambda symbol: None,
        clock=clock,
        cache_indices=True,
    )

    await source.fetch_many(["AAPL"])
    assert factory.count("GET") == 6

    clock.advance(3_600)
    await source.fetch_many(["AAPL"])
    assert factory.count("GET") == 6

    clock.advance(official.INDEX_TTL_S)
    await source.fetch_many(["AAPL"])
    assert factory.count("GET") == 12


async def test_partial_index_load_is_retried_after_fifteen_minutes() -> None:
    clock = _Clock()
    factory = _Factory(_index_routes(spy=_Response(500)))
    source = OfficialDepthSource(
        client_factory=factory,
        optionable_checker=lambda symbol: None,
        clock=clock,
        cache_indices=True,
    )

    await source.fetch_many(["KBH"])
    assert factory.count("GET") == 6

    clock.advance(official.INDEX_RETRY_S - 1)
    await source.fetch_many(["KBH"])
    assert factory.count("GET") == 6

    clock.advance(2)
    await source.fetch_many(["KBH"])
    assert factory.count("GET") == 12


async def test_index_cache_is_off_for_injected_clients_by_default() -> None:
    factory = _Factory(_index_routes())
    source = OfficialDepthSource(
        client_factory=factory, optionable_checker=lambda symbol: None
    )

    await source.fetch_many(["AAPL"])
    await source.fetch_many(["AAPL"])

    assert factory.count("GET") == 12


async def test_clear_caches_is_idempotent() -> None:
    clear_caches()
    clear_caches()
    assert not official._INDEX_CACHE
