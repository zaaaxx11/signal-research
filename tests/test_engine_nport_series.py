"""RED contract for R5E: series-disambiguated N-PORT holdings.

GAPS 5.6: a trust CIK can file one N-PORT-P per series, so picking the newest
filing for the CIK can serve another ETF's portfolio. Two official SEC
documents fix that, and every assertion here is a sentence from
``briefs/market-engine/R5E-CONTRACT.md`` §1–§3:

1. **The fund map** (``company_tickers_mf.json``) bridges ticker → series id.
   Columns are read **by name** (the payload below shuffles them), the symbol
   is upper cased, the last duplicate row wins, and a row missing its symbol,
   CIK or series id is skipped. An absent or unreadable file is ``{}`` — a
   missing map means "fall back", never a crash and never a wrong series.
2. **The EFTS lookup** locates the newest filing whose XML carries one series
   id, filtered by padded CIK and ``root_forms == NPORT-P``, parsing ``_id`` at
   the first colon. No qualifying hit is ``None``; a transport or JSON failure
   is one domain error.
3. **The collector** takes the series path when the symbol is in the map: EFTS
   first, then the archive body, and **no submissions request at all**. The
   EFTS ``period_ending``/``file_date``/accession win over the XML's own dates,
   the XML ``seriesId`` must match, a known series with no filings is
   ``HoldingsNotFound``, and a warm body cache costs no body request.

The fallback test pins today's submissions behaviour for a symbol that is not
in the map (SPY is a UIT), so R5E cannot regress it.

Everything is offline: ``httpx2.MockTransport`` is the only transport, every
date is a literal, and the suite-wide socket guard in ``tests/conftest.py``
fails the test if anything tries to leave. The EFTS route matches on host and
path rather than the exact query string, so these tests pin the *parameters*
the contract names without depending on their serialization order.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import json
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest

from app.engine.fund_series import (
    FundSeries,
    fund_series_path,
    load_fund_series,
    parse_fund_series,
)
from app.engine.ingest import DEFAULT_USER_AGENT
from app.engine.nport import (
    HoldingsNotFound,
    HoldingsUnavailable,
    SeriesFiling,
    collect_holdings,
    find_series_filing,
    nport_cache_path,
)

#: iShares Trust — one CIK, hundreds of series. IVV is ``S000004310``.
IVV_CIK = "0001100663"
IVV_SERIES = "S000004310"
IVV_CLASS = "C000012040"

#: IVV's newest N-PORT-P, verified live against EDGAR (contract header).
ACCESSION = "0002071691-26-019760"
FILED = "2026-08-25"
REPORT = "2026-06-30"

#: The archive body URL: unpadded CIK, dashless accession, basename document.
IVV_BODY_URL = (
    "https://www.sec.gov/Archives/edgar/data/1100663/"
    "000207169126019760/primary_doc.xml"
)

#: Another NPORT-P under the same trust — what the submissions path would pick.
OTHER_ACCESSION = "0002071691-26-000001"
OTHER_BODY_URL = (
    "https://www.sec.gov/Archives/edgar/data/1100663/"
    "000207169126000001/primary_doc.xml"
)

#: The submissions index the series path must never read.
SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{IVV_CIK}.json"

#: The SPDR S&P 500 ETF Trust, absent from the fund map because it is a UIT.
SPY_CIK = "0000884394"
SPY_SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{SPY_CIK}.json"
SPY_ACCESSION = "000141036826089410"
SPY_FILED = "2026-08-28"
SPY_REPORT = "2026-06-30"
SPY_BODY_URL = (
    "https://www.sec.gov/Archives/edgar/data/884394/"
    "000141036826089410/primary_doc.xml"
)

#: The one EFTS endpoint R5E may ask.
EFTS_HOST = "efts.sec.gov"
EFTS_PATH = "/LATEST/search-index"

#: The form EFTS is asked for.
NPORT_FORM = "NPORT-P"

#: The seven keys the holdings document always carries (contract §2).
DOCUMENT_FIELDS: tuple[str, ...] = (
    "symbol",
    "as_of",
    "filed",
    "accession",
    "count",
    "holdings",
    "provider_errors",
)


# ---------------------------------------------------------------------------
# Fixture helpers — recording transport
# ---------------------------------------------------------------------------


class Web:
    """A recording ``httpx2.MockTransport`` with predicate routing.

    An unrouted request is answered ``404`` and still recorded, so a test can
    never pass because the collector asked the wrong host: the recorded URL is
    the assertion. Routes are consulted in registration order.
    """

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self._routes: list[
            tuple[
                Callable[[httpx2.Request], bool],
                Callable[[httpx2.Request], httpx2.Response],
            ]
        ] = []
        self._transport = httpx2.MockTransport(self._handle)

    def route_json(
        self, matches: Callable[[httpx2.Request], bool], payload: Any
    ) -> None:
        """Answer every request ``matches`` accepts with a 200 JSON body."""

        self._routes.append(
            (matches, lambda _request: httpx2.Response(200, json=payload))
        )

    def route_text(
        self, matches: Callable[[httpx2.Request], bool], body: str
    ) -> None:
        """Answer every request ``matches`` accepts with the raw text ``body``."""

        self._routes.append(
            (matches, lambda _request: httpx2.Response(200, text=body))
        )

    def route_status(
        self, matches: Callable[[httpx2.Request], bool], status: int
    ) -> None:
        """Answer every request ``matches`` accepts with ``status``."""

        self._routes.append(
            (
                matches,
                lambda _request: httpx2.Response(status, json={"error": status}),
            )
        )

    def json(self, url: str, payload: Any) -> None:
        """Answer the exact ``url`` with a 200 JSON body."""

        self.route_json(lambda request: str(request.url) == url, payload)

    def text(self, url: str, body: str) -> None:
        """Answer the exact ``url`` with the raw text ``body``."""

        self.route_text(lambda request: str(request.url) == url, body)

    def status(self, url: str, status: int) -> None:
        """Answer the exact ``url`` with ``status`` and a short JSON error."""

        self.route_status(lambda request: str(request.url) == url, status)

    def efts_json(self, payload: Any) -> None:
        """Answer any EFTS search request with ``payload``, whatever the query."""

        self.route_json(self._is_efts, payload)

    def efts_text(self, body: str) -> None:
        """Answer any EFTS search request with the raw ``body``."""

        self.route_text(self._is_efts, body)

    def efts_status(self, status: int) -> None:
        """Answer any EFTS search request with ``status``."""

        self.route_status(self._is_efts, status)

    @staticmethod
    def _is_efts(request: httpx2.Request) -> bool:
        """True for the one EFTS endpoint, independent of the query order."""

        return request.url.host == EFTS_HOST and request.url.path == EFTS_PATH

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        """Record the request, then answer from the route table."""

        self.requests.append(request)
        for matches, responder in self._routes:
            if matches(request):
                return responder(request)
        return httpx2.Response(
            404, json={"error": "unrouted", "url": str(request.url)}
        )

    def client(self) -> httpx2.AsyncClient:
        """An async client over this transport."""

        return httpx2.AsyncClient(transport=self._transport, follow_redirects=False)

    def urls(self) -> list[str]:
        """Every requested URL, in request order."""

        return [str(request.url) for request in self.requests]

    def efts_requests(self) -> list[httpx2.Request]:
        """Every request made to the EFTS search endpoint, in request order."""

        return [request for request in self.requests if self._is_efts(request)]

    def headers_for(self, url: str) -> list[httpx2.Headers]:
        """The headers of every request made to ``url``, in request order."""

        return [
            request.headers
            for request in self.requests
            if str(request.url) == url
        ]


def assert_series_query(request: httpx2.Request, series_id: str) -> None:
    """The request is the contract's EFTS URL, and only its four parameters.

    ``q`` is the series id, ``forms`` the one portfolio form, and ``from`` /
    ``size`` the single page of 100 the contract calls enough. ``multi_items``
    is asserted rather than ``dict(params)`` so a duplicated parameter cannot
    hide behind the last value.
    """

    url = request.url
    assert request.method == "GET"
    assert url.scheme == "https"
    assert url.host == EFTS_HOST
    assert url.path == EFTS_PATH
    assert sorted(url.params.multi_items()) == sorted(
        (
            ("q", series_id),
            ("forms", NPORT_FORM),
            ("from", "0"),
            ("size", "100"),
        )
    )


# ---------------------------------------------------------------------------
# Fixture helpers — SEC documents
# ---------------------------------------------------------------------------


def fund_series_payload(
    rows: Sequence[Mapping[str, object]],
    *,
    fields: Sequence[str] = ("cik", "seriesId", "classId", "symbol"),
) -> dict[str, object]:
    """A ``company_tickers_mf.json`` document in the given column order.

    ``row.get(field)`` is deliberate: a row that lacks a key serialises as
    ``null``, exactly like the SEC file's optional ``classId``.
    """

    return {
        "fields": list(fields),
        "data": [[row.get(field) for field in fields] for row in rows],
    }


def write_fund_series(
    cache_dir: Path,
    rows: Sequence[Mapping[str, object]],
    *,
    fields: Sequence[str] = ("cik", "seriesId", "classId", "symbol"),
) -> Path:
    """Write the map under its contract name and return the path."""

    path = fund_series_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(fund_series_payload(rows, fields=fields)), encoding="utf-8"
    )
    return path


def holding_xml(
    *,
    name: str = "NVIDIA Corp",
    title: str | None = "NVIDIA Corp",
    cusip: str | None = "67066G104",
    balance: str | None = "1000000.00000000",
    value_usd: str | None = "1000.00000000",
    pct_val: str | None = "7.500000000000",
    asset_category: str | None = "EC",
    country: str | None = "US",
    currency: str | None = "USD",
) -> str:
    """One ``invstOrSec`` row; ``None`` omits the element entirely."""

    values: tuple[tuple[str, str | None], ...] = (
        ("name", name),
        ("title", title),
        ("cusip", cusip),
        ("balance", balance),
        ("curCd", currency),
        ("valUSD", value_usd),
        ("pctVal", pct_val),
        ("assetCat", asset_category),
        ("invCountry", country),
    )
    parts: list[str] = ["<invstOrSec>"]
    for tag, value in values:
        if value is not None:
            parts.append(f"<{tag}>{value}</{tag}>")
    parts.append("</invstOrSec>")
    return "".join(parts)


def nport_body(
    *rows: str,
    series_id: str | None = IVV_SERIES,
    class_id: str | None = IVV_CLASS,
    rep_pd_date: str | None = REPORT,
) -> str:
    """A minimal N-PORT-P body whose ``genInfo`` names the filed series.

    The real document keeps ``seriesId`` and ``classId`` in ``genInfo`` and
    ``repPdDate`` beside them; the parser under test must find those by local
    tag name, namespace or not.
    """

    series = "" if series_id is None else f"<seriesId>{series_id}</seriesId>"
    klass = "" if class_id is None else f"<classId>{class_id}</classId>"
    period = "" if rep_pd_date is None else f"<repPdDate>{rep_pd_date}</repPdDate>"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">'
        "<headerData><submissionType>NPORT-P</submissionType></headerData>"
        "<formData><genInfo><regName>iShares Trust</regName>"
        "<seriesName>iShares Core S&amp;P 500 ETF</seriesName>"
        f"{series}{klass}{period}</genInfo>"
        "<invstOrSecs>" + "".join(rows) + "</invstOrSecs>"
        "</formData></edgarSubmission>"
    )


def ivv_body(*, series_id: str = IVV_SERIES, rep_pd_date: str = REPORT) -> str:
    """IVV's two-row portfolio, largest position first."""

    return nport_body(
        holding_xml(
            name="NVIDIA Corp",
            pct_val="7.500000000000",
            value_usd="1000.00000000",
        ),
        holding_xml(
            name="Apple Inc",
            cusip="037833100",
            pct_val="6.250000000000",
            value_usd="900.00000000",
        ),
        series_id=series_id,
        rep_pd_date=rep_pd_date,
    )


def efts_hit(
    accession: str,
    *,
    filed: str,
    report_date: str | None,
    cik: str = IVV_CIK,
    root_forms: Sequence[str] = (NPORT_FORM,),
    document: str = "primary_doc.xml",
) -> dict[str, object]:
    """One EFTS hit: ``_id`` is ``<accession>:<document>`` as EDGAR spells it."""

    return {
        "_id": f"{accession}:{document}",
        "_source": {
            "ciks": [cik],
            "file_date": filed,
            "period_ending": report_date,
            "root_forms": list(root_forms),
            "form": root_forms[0] if root_forms else "",
        },
    }


def efts_payload(*hits: Mapping[str, object]) -> dict[str, object]:
    """An EFTS search response around ``hits``."""

    return {
        "hits": {
            "total": {"value": len(hits)},
            "hits": list(hits),
        }
    }


def submissions_payload(
    rows: Sequence[tuple[str, str, str, str, str]],
    *,
    cik: str = SPY_CIK,
    name: str = "SPDR S&P 500 ETF Trust",
    tickers: Sequence[str] = ("SPY",),
) -> dict[str, object]:
    """A submissions document whose ``filings.recent`` rows are the tuples.

    Each tuple is ``(accession, filing_date, form, primary_document,
    report_date)``.
    """

    columns: dict[str, list[str]] = {
        "accessionNumber": [],
        "filingDate": [],
        "form": [],
        "primaryDocument": [],
        "reportDate": [],
    }
    for accession, filed, form, document, report_date in rows:
        columns["accessionNumber"].append(accession)
        columns["filingDate"].append(filed)
        columns["form"].append(form)
        columns["primaryDocument"].append(document)
        columns["reportDate"].append(report_date)
    return {
        "cik": cik,
        "name": name,
        "tickers": list(tickers),
        "filings": {"recent": columns, "files": []},
    }


def spy_submissions() -> dict[str, object]:
    """SPY's submissions index with one NPORT-P row."""

    return submissions_payload(
        (
            (
                SPY_ACCESSION,
                SPY_FILED,
                NPORT_FORM,
                "xslFormNPORT-P_X01/primary_doc.xml",
                SPY_REPORT,
            ),
        )
    )


def spy_body() -> str:
    """SPY's two-row portfolio; a UIT body carries no ``seriesId``."""

    return nport_body(
        holding_xml(name="NVIDIA Corp", pct_val="7.500000000000"),
        holding_xml(name="Apple Inc", cusip="037833100", pct_val="6.250000000000"),
        series_id=None,
        class_id=None,
        rep_pd_date=SPY_REPORT,
    )


def document_holdings(document: dict[str, object]) -> list[dict[str, object]]:
    """The ``holdings`` list of a collect document, narrowed for the checker."""

    rows = document.get("holdings")
    assert isinstance(rows, list), "holdings is a list of holding documents"
    assert all(isinstance(row, dict) for row in rows), "each holding is an object"
    return cast(list[dict[str, object]], rows)


# ---------------------------------------------------------------------------
# §1 parse_fund_series — fields by name, symbols, duplicates, bad rows
# ---------------------------------------------------------------------------


def test_parse_fund_series_reads_fields_by_name() -> None:
    """Reordering ``fields`` changes nothing: the columns are read by name."""

    payload = fund_series_payload(
        (
            {
                "symbol": "ivv",
                "classId": IVV_CLASS,
                "seriesId": IVV_SERIES,
                "cik": IVV_CIK,
            },
            {
                "symbol": "spy",
                "classId": None,
                "seriesId": "S000004311",
                "cik": SPY_CIK,
            },
        ),
        fields=("symbol", "classId", "seriesId", "cik"),
    )

    mapping = parse_fund_series(payload)

    assert mapping == {
        "IVV": FundSeries(
            cik=IVV_CIK,
            series_id=IVV_SERIES,
            class_id=IVV_CLASS,
            symbol="IVV",
        ),
        "SPY": FundSeries(
            cik=SPY_CIK,
            series_id="S000004311",
            class_id=None,
            symbol="SPY",
        ),
    }


def test_parse_fund_series_upper_cases_the_symbol() -> None:
    """The key and the stored symbol are both upper case, whatever was filed."""

    mapping = parse_fund_series(
        fund_series_payload(
            (
                {"symbol": "Spy", "classId": None, "seriesId": "S1", "cik": SPY_CIK},
                {"symbol": "ivv", "classId": None, "seriesId": "S2", "cik": IVV_CIK},
            )
        )
    )

    assert set(mapping) == {"SPY", "IVV"}
    assert mapping["SPY"].symbol == "SPY"
    assert mapping["IVV"].symbol == "IVV"


def test_parse_fund_series_last_duplicate_row_wins() -> None:
    """Two rows for one symbol collapse to the later row, deterministically."""

    mapping = parse_fund_series(
        fund_series_payload(
            (
                {
                    "symbol": "ivv",
                    "classId": None,
                    "seriesId": "S000000001",
                    "cik": "0000000001",
                },
                {
                    "symbol": "IVV",
                    "classId": IVV_CLASS,
                    "seriesId": IVV_SERIES,
                    "cik": IVV_CIK,
                },
            )
        )
    )

    assert len(mapping) == 1
    assert mapping["IVV"].series_id == IVV_SERIES
    assert mapping["IVV"].cik == IVV_CIK


def test_parse_fund_series_skips_rows_missing_symbol_cik_or_series() -> None:
    """A row without its symbol, CIK or series id cannot name a series."""

    payload: dict[str, object] = {
        "fields": ["cik", "seriesId", "classId", "symbol"],
        "data": [
            ["0000000001", "S000000001", None, ""],
            ["0000000001", "S000000002", None, "   "],
            [None, "S000000003", None, "AAA"],
            ["   ", "S000000004", None, "BBB"],
            ["0000000001", None, None, "CCC"],
            ["0000000001", "S000000005", None],
            ["0000000001", "S000000006", None, "GOOD"],
        ],
    }

    mapping = parse_fund_series(payload)

    assert set(mapping) == {"GOOD"}, "only the complete row names a series"
    assert mapping["GOOD"].series_id == "S000000006"


def test_parse_fund_series_class_id_is_optional() -> None:
    """A null or absent ``classId`` keeps the row with ``class_id=None``."""

    null_class = fund_series_payload(
        (
            {
                "cik": IVV_CIK,
                "seriesId": IVV_SERIES,
                "classId": None,
                "symbol": "IVV",
            },
        )
    )
    absent_class: dict[str, object] = {
        "fields": ["cik", "seriesId", "symbol"],
        "data": [[SPY_CIK, "S000004311", "SPY"]],
    }

    assert parse_fund_series(null_class)["IVV"].class_id is None
    assert parse_fund_series(absent_class)["SPY"].class_id is None, (
        "a missing classId column is not an error"
    )


# ---------------------------------------------------------------------------
# §1 load_fund_series — absent or unreadable is the empty map
# ---------------------------------------------------------------------------


def test_load_fund_series_missing_file_is_empty(tmp_path: Path) -> None:
    """A missing map is a normal state: ``{}``, so the caller falls back."""

    assert load_fund_series(fund_series_path(tmp_path)) == {}


def test_load_fund_series_corrupt_json_is_empty(tmp_path: Path) -> None:
    """An unreadable cache is ``{}`` too: no crash and no invented series."""

    path = fund_series_path(tmp_path)
    path.write_text('{"fields": ["cik"], "data": [', encoding="utf-8")

    assert load_fund_series(path) == {}


def test_load_fund_series_reads_the_cached_map(tmp_path: Path) -> None:
    """A valid cached file parses to the symbol-keyed mapping."""

    path = write_fund_series(
        tmp_path,
        (
            {
                "cik": IVV_CIK,
                "seriesId": IVV_SERIES,
                "classId": IVV_CLASS,
                "symbol": "IVV",
            },
        ),
    )

    assert load_fund_series(path) == {
        "IVV": FundSeries(
            cik=IVV_CIK,
            series_id=IVV_SERIES,
            class_id=IVV_CLASS,
            symbol="IVV",
        )
    }


# ---------------------------------------------------------------------------
# §2 find_series_filing — one EFTS page, CIK and form filtered, newest wins
# ---------------------------------------------------------------------------


async def test_find_series_filing_selects_the_newest_matching_hit() -> None:
    """Wrong-CIK and wrong-form hits are dropped; the newest correct one wins.

    A same-day lower accession is present so the ``(file_date, accession)``
    ordering is actually exercised, and the wrong-CIK hit is *newer* than every
    correct one: a collector that forgot the CIK filter would return it.
    """

    correct_old = efts_hit(
        "0002071691-26-009999", filed="2026-05-12", report_date="2026-03-31"
    )
    correct_same_day = efts_hit(
        "0002071691-26-019700", filed=FILED, report_date=REPORT
    )
    newest_correct = efts_hit(ACCESSION, filed=FILED, report_date=REPORT)
    wrong_cik = efts_hit(
        "000141036826089410",
        filed="2026-09-01",
        report_date="2026-06-30",
        cik=SPY_CIK,
    )
    wrong_form = efts_hit(
        "0002071691-26-099999",
        filed="2026-09-02",
        report_date="2026-06-30",
        root_forms=("NPORT-EX",),
    )
    web = Web()
    web.efts_json(
        efts_payload(
            wrong_cik,
            wrong_form,
            newest_correct,
            correct_same_day,
            correct_old,
        )
    )

    filing = await find_series_filing(IVV_SERIES, IVV_CIK, client=web.client())

    assert filing == SeriesFiling(
        filed=FILED,
        report_date=REPORT,
        accession=ACCESSION,
        document="primary_doc.xml",
    )
    assert len(web.requests) == 1, "one page of 100 is the whole lookup"
    assert_series_query(web.requests[0], IVV_SERIES)


async def test_find_series_filing_splits_the_id_at_the_first_colon() -> None:
    """``_id`` is ``<accession>:<document>``; only the first colon is a break."""

    web = Web()
    web.efts_json(
        efts_payload(
            efts_hit(
                ACCESSION,
                filed=FILED,
                report_date=REPORT,
                document="primary_doc.xml:amended",
            )
        )
    )

    filing = await find_series_filing(IVV_SERIES, IVV_CIK, client=web.client())

    assert filing is not None
    assert filing.accession == ACCESSION
    assert filing.document == "primary_doc.xml:amended", (
        "a colon inside the document name must survive the split"
    )


async def test_find_series_filing_returns_none_without_hits() -> None:
    """No qualifying hit is ``None``; the caller turns that into NotFound."""

    web = Web()
    web.efts_json(efts_payload())

    assert await find_series_filing(IVV_SERIES, IVV_CIK, client=web.client()) is None
    assert len(web.efts_requests()) == 1


async def test_find_series_filing_http_500_is_holdings_unavailable() -> None:
    """An EFTS failure is one domain error, never "no filing"."""

    web = Web()
    web.efts_status(500)

    with pytest.raises(HoldingsUnavailable) as caught:
        await find_series_filing(IVV_SERIES, IVV_CIK, client=web.client())

    assert str(caught.value).strip(), "the error says what failed"


async def test_find_series_filing_bad_json_is_holdings_unavailable() -> None:
    """A 200 that is not JSON is a body failure, not an empty result set."""

    web = Web()
    web.efts_text("<html>rate limited</html>")

    with pytest.raises(HoldingsUnavailable):
        await find_series_filing(IVV_SERIES, IVV_CIK, client=web.client())


# ---------------------------------------------------------------------------
# §2 collect_holdings — the series path
# ---------------------------------------------------------------------------


async def test_collect_holdings_uses_the_series_filing_and_never_submissions(
    tmp_path: Path,
) -> None:
    """IVV resolves through the map: EFTS, then the body, and no submissions.

    The submissions route is present and deliberately divergent (another
    accession, another report date) so a collector that still takes today's
    path fails on the recorded URLs *and* on the dates, instead of only
    tripping over an unrouted 404.
    """

    write_fund_series(
        tmp_path,
        (
            {
                "cik": IVV_CIK,
                "seriesId": IVV_SERIES,
                "classId": IVV_CLASS,
                "symbol": "IVV",
            },
        ),
    )
    body = ivv_body(rep_pd_date="2026-05-31")
    web = Web()
    web.efts_json(
        efts_payload(
            efts_hit(
                "0002071691-26-099999",
                filed="2026-09-02",
                report_date="2026-09-01",
                cik=SPY_CIK,
            ),
            efts_hit(ACCESSION, filed=FILED, report_date=REPORT),
        )
    )
    web.text(IVV_BODY_URL, body)
    web.json(
        SUBMISSIONS_URL,
        submissions_payload(
            (
                (
                    OTHER_ACCESSION,
                    "2026-04-15",
                    NPORT_FORM,
                    "xslFormNPORT-P_X01/primary_doc.xml",
                    "2026-03-31",
                ),
            ),
            cik=IVV_CIK,
        ),
    )
    web.text(OTHER_BODY_URL, body)

    document = await collect_holdings(
        "IVV", IVV_CIK, client=web.client(), cache_dir=tmp_path, limit=50
    )

    urls = web.urls()
    assert SUBMISSIONS_URL not in urls, "the series path never reads the index"
    assert not (tmp_path / f"CIK{IVV_CIK}-submissions.json").exists(), (
        "no submissions body may be fetched, let alone cached"
    )
    assert len(urls) == 2, "EFTS then the archive body, nothing else"
    assert urls[0].startswith(f"https://{EFTS_HOST}{EFTS_PATH}?")
    assert urls[1] == IVV_BODY_URL
    assert_series_query(web.requests[0], IVV_SERIES)

    assert document["symbol"] == "IVV"
    assert document["as_of"] == REPORT, "as_of is EFTS period_ending"
    assert document["filed"] == FILED, "filed is EFTS file_date"
    assert document["accession"] == ACCESSION, "accession comes from EFTS"
    assert document["provider_errors"] == []
    assert sorted(document) == sorted(DOCUMENT_FIELDS)
    holdings = document_holdings(document)
    assert document["count"] == 2
    assert [holding["name"] for holding in holdings] == ["NVIDIA Corp", "Apple Inc"]

    body_headers = web.headers_for(IVV_BODY_URL)
    assert len(body_headers) == 1, "the body is fetched exactly once"
    assert body_headers[0]["user-agent"] == DEFAULT_USER_AGENT

    cache = nport_cache_path(tmp_path, IVV_CIK, ACCESSION)
    assert cache.is_file(), "the fetched body is cached under the contract name"
    assert cache.read_text(encoding="utf-8") == body


async def test_collect_holdings_rejects_an_xml_for_another_series(
    tmp_path: Path,
) -> None:
    """A body whose ``seriesId`` disagrees with the map is a named failure.

    EFTS is a full-text index, not a promise: the downloaded XML is the only
    proof of which series the portfolio belongs to, so a mismatch must stop
    rather than publish another ETF's holdings.
    """

    write_fund_series(
        tmp_path,
        (
            {
                "cik": IVV_CIK,
                "seriesId": IVV_SERIES,
                "classId": IVV_CLASS,
                "symbol": "IVV",
            },
        ),
    )
    web = Web()
    web.efts_json(
        efts_payload(efts_hit(ACCESSION, filed=FILED, report_date=REPORT))
    )
    web.text(IVV_BODY_URL, ivv_body(series_id="S000004999"))

    with pytest.raises(HoldingsUnavailable) as caught:
        await collect_holdings(
            "IVV", IVV_CIK, client=web.client(), cache_dir=tmp_path, limit=50
        )

    message = str(caught.value)
    assert IVV_SERIES in message, "the error names the expected series"
    assert "S000004999" in message, "the error names the series it received"
    assert SUBMISSIONS_URL not in web.urls(), "the fallback must not rescue it"


async def test_collect_holdings_series_without_filings_is_not_found(
    tmp_path: Path,
) -> None:
    """A series in the map but absent from EFTS has no portfolio to serve."""

    write_fund_series(
        tmp_path,
        (
            {
                "cik": IVV_CIK,
                "seriesId": IVV_SERIES,
                "classId": IVV_CLASS,
                "symbol": "IVV",
            },
        ),
    )
    web = Web()
    web.efts_json(efts_payload())
    web.json(
        SUBMISSIONS_URL,
        submissions_payload(
            (
                (
                    OTHER_ACCESSION,
                    "2026-04-15",
                    NPORT_FORM,
                    "primary_doc.xml",
                    "2026-03-31",
                ),
            ),
            cik=IVV_CIK,
        ),
    )
    web.text(OTHER_BODY_URL, ivv_body())

    with pytest.raises(HoldingsNotFound):
        await collect_holdings(
            "IVV", IVV_CIK, client=web.client(), cache_dir=tmp_path, limit=50
        )

    assert len(web.efts_requests()) == 1
    assert len(web.urls()) == 1, "there is no body URL to ask for"
    assert SUBMISSIONS_URL not in web.urls(), (
        "a known series with no filing is NotFound, not a submissions lookup"
    )


# ---------------------------------------------------------------------------
# §2 collect_holdings — the fallback path is today's, unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "write_map",
    (
        pytest.param(False, id="no-map-file"),
        pytest.param(True, id="symbol-absent"),
    ),
)
async def test_collect_holdings_falls_back_to_submissions(
    tmp_path: Path,
    write_map: bool,
) -> None:
    """No map, or a map without SPY: the submissions path, and no EFTS call."""

    if write_map:
        write_fund_series(
            tmp_path,
            (
                {
                    "cik": IVV_CIK,
                    "seriesId": IVV_SERIES,
                    "classId": IVV_CLASS,
                    "symbol": "IVV",
                },
            ),
        )
    else:
        assert not fund_series_path(tmp_path).exists()

    web = Web()
    web.json(SPY_SUBMISSIONS_URL, spy_submissions())
    web.text(SPY_BODY_URL, spy_body())
    web.efts_json(efts_payload())

    document = await collect_holdings(
        "SPY", SPY_CIK, client=web.client(), cache_dir=tmp_path, limit=50
    )

    assert document["symbol"] == "SPY"
    assert document["as_of"] == SPY_REPORT
    assert document["filed"] == SPY_FILED
    assert document["accession"] == SPY_ACCESSION
    assert document["count"] == 2
    assert [row["name"] for row in document_holdings(document)] == [
        "NVIDIA Corp",
        "Apple Inc",
    ]
    assert web.efts_requests() == [], (
        "a symbol outside the map must not be sent to full-text search"
    )
    assert web.urls() == [SPY_SUBMISSIONS_URL, SPY_BODY_URL]


# ---------------------------------------------------------------------------
# §2 collect_holdings — warm body cache
# ---------------------------------------------------------------------------


async def test_collect_holdings_serves_a_warm_xml_cache(
    tmp_path: Path,
) -> None:
    """A cached body is used as-is: EFTS locates the filing, nobody fetches XML.

    The XML URL is routed with a one-row document, so a collector that fetched
    it anyway would also change the holdings; the request list is asserted
    first because that is the behaviour the contract names.
    """

    write_fund_series(
        tmp_path,
        (
            {
                "cik": IVV_CIK,
                "seriesId": IVV_SERIES,
                "classId": IVV_CLASS,
                "symbol": "IVV",
            },
        ),
    )
    cached = nport_cache_path(tmp_path, IVV_CIK, ACCESSION)
    cached.write_text(ivv_body(rep_pd_date="2026-05-31"), encoding="utf-8")

    web = Web()
    web.efts_json(
        efts_payload(efts_hit(ACCESSION, filed=FILED, report_date=REPORT))
    )
    web.text(IVV_BODY_URL, nport_body(holding_xml(name="Fetched Over Network")))
    web.json(
        SUBMISSIONS_URL,
        submissions_payload(
            (
                (
                    OTHER_ACCESSION,
                    "2026-04-15",
                    NPORT_FORM,
                    "primary_doc.xml",
                    "2026-03-31",
                ),
            ),
            cik=IVV_CIK,
        ),
    )
    web.text(OTHER_BODY_URL, nport_body(holding_xml(name="Fetched Over Network")))

    document = await collect_holdings(
        "IVV", IVV_CIK, client=web.client(), cache_dir=tmp_path, limit=50
    )

    urls = web.urls()
    assert [url for url in urls if url == IVV_BODY_URL] == [], (
        "a warm body cache costs no body request"
    )
    assert len(urls) == 1, "EFTS is the only request; it is the index lookup"
    assert urls[0].startswith(f"https://{EFTS_HOST}{EFTS_PATH}?")
    assert SUBMISSIONS_URL not in urls

    assert document["as_of"] == REPORT, "EFTS period_ending wins over repPdDate"
    assert document["filed"] == FILED
    assert document["accession"] == ACCESSION
    assert document["count"] == 2
    assert [row["name"] for row in document_holdings(document)] == [
        "NVIDIA Corp",
        "Apple Inc",
    ]


async def test_efts_error_payload_is_unavailable_not_an_empty_result() -> None:
    """An EFTS backend error is an outage, not 'this fund never filed'.

    EDGAR's search can answer HTTP 200 with ``{"errorType": "ConnectionError",
    ...}`` when its OpenSearch backend is unhealthy.  Treating that as zero
    hits would publish ``holdings_not_found`` for a fund that actually files
    every quarter, so it must raise ``HoldingsUnavailable`` instead.
    """

    web = Web()
    web.efts_json(
        {
            "errorType": "ConnectionError",
            "errorMessage": "getaddrinfo EBUSY efts-backend",
        }
    )

    with pytest.raises(HoldingsUnavailable):
        await find_series_filing(IVV_SERIES, IVV_CIK, client=web.client())
