"""RED contract for R5D: ETF holdings from SEC Form N-PORT.

Four surfaces are pinned here, and every assertion is a sentence from
``briefs/market-engine/R5D-CONTRACT.md`` §1–§4:

1. **The parser** reads SPY's real N-PORT-P body
   (``tests/fixtures/nport_spy.xml``, 445 KB, 504 ``invstOrSec`` rows) field by
   field, keeps document order, and copies ``pctVal`` as percent points. The
   synthetic documents below isolate the namespace rule, the blank-number rule,
   the nameless-row rule and the malformed-document answer.
2. **The collector** walks ``filings.recent`` for the newest ``NPORT-P``, builds
   the archive URL from the unpadded CIK, the dashless accession and the
   *basename* of ``primaryDocument`` (stripping any ``xslFormNPORT-P_X01/``
   prefix), fetches the body once, caches it under the contract name, and
   serves a warm cache with zero requests. A missing filing and an unreadable
   body are two different named errors.
3. **The route** answers ``GET /engine/etf/{symbol}/holdings`` with the document
   shape, the house error envelopes, and the one empty-but-successful case (a
   symbol with no CIK). Validation precedes lookup everywhere.

Everything is offline: the only transport is ``httpx2.MockTransport`` keyed by
exact URL, every date is a literal, and the suite-wide socket guard in
``tests/conftest.py`` fails the test if anything tries to leave. The collector
seam is patched in **both** places it can live — the engine module and the name
the API module imports — because the route is written after this file and must
not be able to bypass the double by choosing the other import style.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast
import xml.etree.ElementTree as ET

import httpx2
import pytest

from app.engine.ingest import DEFAULT_USER_AGENT, EngineDataset
from app.engine.model import Provenance, SecurityRecord
from app.engine.nport import (
    EtfHolding,
    HoldingsNotFound,
    HoldingsUnavailable,
    collect_holdings,
    parse_nport,
)
from app.engine.security import SecurityMaster
from app.engine.service import EngineService

ROOT = Path(__file__).resolve().parents[1]

#: The real SPY N-PORT-P body, copied byte for byte from EDGAR.
FIXTURE = ROOT / "tests" / "fixtures" / "nport_spy.xml"

#: Ten-digit CIK of the SPDR S&P 500 ETF Trust, as EDGAR spells it.
SPY_CIK = "0000884394"

#: The newest NPORT-P accession in this slice, and the dates it reports.
ACCESSION = "000141036826089410"
FILED = "2026-08-28"
REPORT = "2026-06-30"

#: An older NPORT-P and a 10-K; neither may be fetched.
OLD_ACCESSION = "000141036826080001"
OLD_URL = "https://www.sec.gov/Archives/edgar/data/884394/000141036826080001/old_doc.xml"

#: The XML body URL: unpadded CIK, dashless accession, basename only.
ARCHIVE_URL = (
    "https://www.sec.gov/Archives/edgar/data/884394/000141036826089410/primary_doc.xml"
)

#: The submissions index the collector reads.
SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{SPY_CIK}.json"

#: The namespace the real fixture's root carries.
NPORT_NS = "http://www.sec.gov/edgar/nport"

#: The nine fields one holding carries onto the wire.
HOLDING_FIELDS: tuple[str, ...] = (
    "name",
    "title",
    "cusip",
    "balance",
    "value_usd",
    "pct_val",
    "asset_category",
    "country",
    "currency",
)

#: The seven keys the route's document always carries.
ROUTE_FIELDS: tuple[str, ...] = (
    "symbol",
    "as_of",
    "filed",
    "accession",
    "count",
    "holdings",
    "provider_errors",
)


# ---------------------------------------------------------------------------
# Fixture helpers — an independent reading of the real document
# ---------------------------------------------------------------------------


def local_name(tag: object) -> str:
    """``{ns}invstOrSec`` → ``invstOrSec``; no namespace survives."""

    return str(tag).rsplit("}", 1)[-1]


def fixture_rows() -> list[dict[str, str]]:
    """Every ``invstOrSec`` row of the real fixture, in document order.

    Read with ``ElementTree`` directly rather than with the code under test, so
    the order assertion has an oracle the parser cannot influence.
    """

    root = ET.parse(FIXTURE).getroot()
    rows: list[dict[str, str]] = []
    for element in root.iter():
        if local_name(element.tag) != "invstOrSec":
            continue
        rows.append(
            {
                local_name(child.tag): (child.text or "").strip()
                for child in element
            }
        )
    return rows


def fixture_text() -> str:
    """The real N-PORT body as text."""

    return FIXTURE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Inline N-PORT documents
# ---------------------------------------------------------------------------


def holding_xml(
    *,
    name: str | None = "Aflac Inc",
    title: str | None = "Aflac Inc",
    cusip: str | None = "001055102",
    balance: str | None = "5551377.00000000",
    value_usd: str | None = "650898953.25000000",
    pct_val: str | None = "0.083321585405",
    asset_category: str | None = "EC",
    country: str | None = "US",
    currency: str | None = "USD",
) -> str:
    """One ``invstOrSec`` row.

    ``None`` omits the element entirely; ``""`` keeps the element and leaves it
    blank. Both are unknown on the wire and must read as ``None``, never zero.
    """

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


def nport_xml(*rows: str, namespace: str | None = NPORT_NS) -> str:
    """A minimal N-PORT body around ``rows``.

    ``namespace=None`` drops the ``xmlns`` declaration, and any other URI
    renames the default namespace: the parser must match by local tag name in
    all three cases.
    """

    declaration = f' xmlns="{namespace}"' if namespace is not None else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<edgarSubmission{declaration}>"
        "<formData><invstOrSecs>"
        + "".join(rows)
        + "</invstOrSecs></formData></edgarSubmission>"
    )


# ---------------------------------------------------------------------------
# §1 Parser — the real filing
# ---------------------------------------------------------------------------


def test_parse_nport_reads_the_live_spy_filing_exactly() -> None:
    """The fixture's 504 rows are read, and Aflac carries its own nine fields."""

    holdings = parse_nport(fixture_text())

    assert len(holdings) == 504, "one row per invstOrSec in the real body"
    first = holdings[0]
    assert isinstance(first, EtfHolding)
    assert first.name == "Aflac Inc"
    assert first.title == "Aflac Inc"
    assert first.cusip == "001055102"
    assert first.balance == 5551377.0
    assert first.value_usd == 650898953.25
    assert first.pct_val == 0.083321585405
    assert first.asset_category == "EC"
    assert first.country == "US"
    assert first.currency == "USD"

    assert first.document() == {
        "name": "Aflac Inc",
        "title": "Aflac Inc",
        "cusip": "001055102",
        "balance": 5551377.0,
        "value_usd": 650898953.25,
        "pct_val": 0.083321585405,
        "asset_category": "EC",
        "country": "US",
        "currency": "USD",
    }, "the document projection is exactly the nine contract fields"

    assert tuple(sorted(first.document())) == tuple(sorted(HOLDING_FIELDS))


def test_parse_nport_preserves_document_order() -> None:
    """The tuple's order is the document's order, row for row."""

    holdings = parse_nport(fixture_text())

    assert [holding.name for holding in holdings] == [
        row["name"] for row in fixture_rows()
    ]


def test_parse_nport_reads_title_from_the_title_element() -> None:
    """SPY's second row is ``AES Corp/The`` whose title is the shorter ``AES Corp``."""

    holdings = parse_nport(fixture_text())

    assert holdings[1].name == "AES Corp/The"
    assert holdings[1].title == "AES Corp"


def test_pct_val_is_percent_points_not_a_fraction() -> None:
    """The 504 ``pctVal`` values sum to ≈99.98, because SEC reports percent points."""

    holdings = parse_nport(fixture_text())

    pcts = [holding.pct_val for holding in holdings]
    assert all(pct is not None for pct in pcts), "every real row reports a pctVal"
    total = sum(pct for pct in pcts if pct is not None)

    assert total == pytest.approx(99.98, abs=0.1), (
        "a fraction reading of the same text would sum to ≈1, not ≈100"
    )


# ---------------------------------------------------------------------------
# §1 Namespace tolerance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "namespace",
    (NPORT_NS, "urn:example:not-nport", None),
    ids=["nport", "foreign", "none"],
)
def test_parse_nport_matches_local_tag_names(namespace: str | None) -> None:
    """A default namespace, a foreign one or none at all parse the same row."""

    holdings = parse_nport(nport_xml(holding_xml(), namespace=namespace))

    assert len(holdings) == 1
    assert holdings[0].name == "Aflac Inc"
    assert holdings[0].cusip == "001055102"


# ---------------------------------------------------------------------------
# §1 Values: blank means unknown, zero is a number
# ---------------------------------------------------------------------------


def test_blank_numeric_fields_are_none_not_zero() -> None:
    """``<balance/>``, ``<valUSD>   </valUSD>`` and ``<pctVal></pctVal>`` are None."""

    document = nport_xml(
        holding_xml(balance="", value_usd="   ", pct_val="")
    )

    (row,) = parse_nport(document)

    assert row.balance is None
    assert row.value_usd is None
    assert row.pct_val is None


@pytest.mark.parametrize("raw", ("twelve", "N/A", "1,234"), ids=["word", "na", "commas"])
def test_a_non_numeric_amount_is_none(raw: str) -> None:
    """Text that cannot be a float is unknown, not an exception and not zero."""

    (row,) = parse_nport(nport_xml(holding_xml(balance=raw)))

    assert row.balance is None


def test_a_reported_zero_stays_zero() -> None:
    """``0`` is a number the fund reported; only blank or bad text becomes None."""

    (row,) = parse_nport(
        nport_xml(holding_xml(balance="0", value_usd="0", pct_val="0"))
    )

    assert row.balance == 0.0
    assert row.value_usd == 0.0
    assert row.pct_val == 0.0


def test_text_fields_are_stripped_and_blank_becomes_none() -> None:
    """Surrounding whitespace is removed; an all-blank text field is unknown."""

    (row,) = parse_nport(
        nport_xml(
            holding_xml(
                name="  Aflac Inc  ",
                cusip="  ",
                currency="",
                country=None,
            )
        )
    )

    assert row.name == "Aflac Inc"
    assert row.cusip is None
    assert row.currency is None
    assert row.country is None


# ---------------------------------------------------------------------------
# §1 Row shape: a nameless row is not a holding
# ---------------------------------------------------------------------------


def test_a_row_without_a_name_is_skipped() -> None:
    """An absent and a blank ``name`` are dropped; the named row still returns."""

    document = nport_xml(
        holding_xml(name=None),
        holding_xml(name="   "),
        holding_xml(name="AES Corp/The", title="AES Corp"),
    )

    holdings = parse_nport(document)

    assert [holding.name for holding in holdings] == ["AES Corp/The"]


def test_title_is_the_title_element_not_a_copy_of_name() -> None:
    """A differing title is kept as filed; an absent title is None, not the name."""

    document = nport_xml(
        holding_xml(name="AES Corp/The", title="AES Corp"),
        holding_xml(name="Aflac Inc", title=None),
    )

    expensive, unnamed = parse_nport(document)

    assert expensive.name == "AES Corp/The"
    assert expensive.title == "AES Corp"
    assert unnamed.name == "Aflac Inc"
    assert unnamed.title is None, "a missing title must not be filled from name"


# ---------------------------------------------------------------------------
# §1 Malformed documents
# ---------------------------------------------------------------------------

MALFORMED_DOCUMENTS: tuple[str, ...] = (
    "",
    "not xml at all",
    "<edgarSubmission><formData><invstOrSecs>",
    "<edgarSubmission><invstOrSec></edgarSubmission>",
    '<?xml version="1.0"?><companyFacts><facts/></companyFacts>',
)


@pytest.mark.parametrize(
    "document",
    MALFORMED_DOCUMENTS,
    ids=["empty", "text", "unclosed", "mismatched", "foreign-root"],
)
def test_parse_nport_returns_empty_for_malformed_xml(document: str) -> None:
    """Bad XML has one answer — ``()`` — not a library-specific exception."""

    assert parse_nport(document) == ()


def test_parse_nport_returns_empty_when_there_are_no_rows() -> None:
    """A well-formed N-PORT envelope with no ``invstOrSec`` holds nothing."""

    assert parse_nport(nport_xml()) == ()


# ---------------------------------------------------------------------------
# §2 Collector — offline transport and submissions fixture
# ---------------------------------------------------------------------------


class Web:
    """A recording ``httpx2.MockTransport`` keyed by exact URL.

    An unrouted request is answered ``404`` and still recorded, so a test can
    never pass because the collector asked the wrong host or forgot the xsl
    prefix strip: the recorded URL is the assertion.
    """

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self._routes: dict[str, Callable[[httpx2.Request], httpx2.Response]] = {}
        self._transport = httpx2.MockTransport(self._handle)

    def json(self, url: str, payload: Any) -> None:
        """Answer ``url`` with a 200 JSON body."""

        self._routes[url] = lambda _request: httpx2.Response(200, json=payload)

    def text(self, url: str, body: str) -> None:
        """Answer ``url`` with a 200 body of ``body``."""

        self._routes[url] = lambda _request: httpx2.Response(200, text=body)

    def status(self, url: str, status: int) -> None:
        """Answer ``url`` with ``status`` and a short JSON error."""

        self._routes[url] = lambda _request: httpx2.Response(
            status, json={"error": status}
        )

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        """Record the request, then answer from the route table."""

        self.requests.append(request)
        responder = self._routes.get(str(request.url))
        if responder is None:
            return httpx2.Response(
                404, json={"error": "unrouted", "url": str(request.url)}
            )
        return responder(request)

    def client(self) -> httpx2.AsyncClient:
        """An async client over this transport."""

        return httpx2.AsyncClient(transport=self._transport, follow_redirects=False)

    def urls(self) -> list[str]:
        """Every requested URL, in request order."""

        return [str(request.url) for request in self.requests]

    def headers_for(self, url: str) -> list[httpx2.Headers]:
        """The headers of every request made to ``url``, in request order."""

        return [
            request.headers
            for request in self.requests
            if str(request.url) == url
        ]


def archive_url(accession: str, document: str = "primary_doc.xml") -> str:
    """The archive URL for one body: unpadded CIK, dashless accession, basename."""

    return (
        "https://www.sec.gov/Archives/edgar/data/884394/"
        f"{accession.replace('-', '')}/{document}"
    )


def submissions_payload(
    filings: Sequence[tuple[str, str, str, str, str]],
) -> dict[str, Any]:
    """A submissions document whose ``filings.recent`` rows are the tuples.

    Each tuple is ``(accession, filing_date, form, primary_document,
    report_date)``. EDGAR spells ``primaryDocument`` with an
    ``xslFormNPORT-P_X01/`` prefix for N-PORT, which is exactly what the
    collector must strip before building the URL.
    """

    columns: dict[str, list[str]] = {
        "accessionNumber": [],
        "filingDate": [],
        "form": [],
        "primaryDocument": [],
        "reportDate": [],
    }
    for accession, filed, form, document, report_date in filings:
        columns["accessionNumber"].append(accession)
        columns["filingDate"].append(filed)
        columns["form"].append(form)
        columns["primaryDocument"].append(document)
        columns["reportDate"].append(report_date)
    return {
        "cik": SPY_CIK,
        "name": "SPDR S&P 500 ETF Trust",
        "tickers": ["SPY"],
        "exchanges": ["NYSE Arca"],
        "filings": {"recent": columns, "files": []},
    }


def newest_filing_payload() -> dict[str, Any]:
    """The submissions index with an older NPORT-P, a 10-K and the newest NPORT-P."""

    return submissions_payload(
        (
            (OLD_ACCESSION, "2026-05-01", "NPORT-P", "old_doc.xml", "2026-03-31"),
            ("000141036826070001", "2026-07-01", "10-K", "spy10k.htm", "2026-03-31"),
            (
                ACCESSION,
                FILED,
                "NPORT-P",
                "xslFormNPORT-P_X01/primary_doc.xml",
                REPORT,
            ),
        )
    )


def nport_cache_path(cache_dir: Path, accession: str = ACCESSION) -> Path:
    """``CIK##########-nport-<accession_nodash>.xml`` under ``cache_dir``."""

    return cache_dir / f"CIK{SPY_CIK}-nport-{accession.replace('-', '')}.xml"


def document_holdings(document: dict[str, object]) -> list[dict[str, object]]:
    """The ``holdings`` list of a collect document, narrowed for the type checker."""

    rows = document.get("holdings")
    assert isinstance(rows, list), "holdings is a list of holding documents"
    assert all(isinstance(row, dict) for row in rows), "each holding is an object"
    return cast(list[dict[str, object]], rows)


# ---------------------------------------------------------------------------
# §2 Collector — the newest filing, one body, one cache file
# ---------------------------------------------------------------------------


async def test_collect_holdings_reads_the_newest_nport_and_ignores_the_rest(
    tmp_path: Path,
) -> None:
    """The newest NPORT-P is fetched; the older one and the 10-K never are."""

    web = Web()
    web.json(SUBMISSIONS_URL, newest_filing_payload())
    web.text(ARCHIVE_URL, fixture_text())

    document = await collect_holdings(
        "SPY", SPY_CIK, client=web.client(), cache_dir=tmp_path, limit=50
    )

    assert document["symbol"] == "SPY"
    assert document["as_of"] == REPORT, "as_of is the filing's reportDate"
    assert document["filed"] == FILED
    assert document["accession"] == ACCESSION
    assert document["provider_errors"] == []
    assert document["count"] == 50
    holdings = document_holdings(document)
    assert len(holdings) == 50
    assert holdings[0]["name"] == "NVIDIA Corp"

    assert web.urls() == [SUBMISSIONS_URL, ARCHIVE_URL], (
        "the xsl prefix is stripped from the URL, and neither the older "
        "NPORT-P nor the 10-K is fetched"
    )
    assert OLD_URL not in web.urls()

    body_headers = web.headers_for(ARCHIVE_URL)
    assert len(body_headers) == 1, "the body is fetched exactly once"
    assert body_headers[0]["user-agent"] == DEFAULT_USER_AGENT, (
        "EDGAR needs the contactable User-Agent from ingest.DEFAULT_USER_AGENT"
    )

    cache = nport_cache_path(tmp_path)
    assert cache.is_file(), "the fetched body is cached under the contract name"
    assert cache.read_text(encoding="utf-8") == fixture_text(), (
        "the cache holds the body byte for byte"
    )
    assert cache.stat().st_size == FIXTURE.stat().st_size


async def test_collect_holdings_sorts_by_pct_val_then_value_descending(
    tmp_path: Path,
) -> None:
    """The page is the largest positions first, and ``limit`` truncates it."""

    web = Web()
    web.json(SUBMISSIONS_URL, newest_filing_payload())
    web.text(ARCHIVE_URL, fixture_text())

    document = await collect_holdings(
        "SPY", SPY_CIK, client=web.client(), cache_dir=tmp_path, limit=5
    )

    assert document["count"] == 5
    holdings = document_holdings(document)
    assert [holding["name"] for holding in holdings] == [
        "NVIDIA Corp",
        "Apple Inc",
        "Microsoft Corp",
        "Amazon.com Inc",
        "Alphabet Inc",
    ]
    pcts = cast(list[float], [holding["pct_val"] for holding in holdings])
    assert all(isinstance(pct, float) for pct in pcts)
    assert pcts == sorted(pcts, reverse=True)


async def test_a_missing_pct_val_sorts_last_even_with_a_larger_value(
    tmp_path: Path,
) -> None:
    """``(pct_val, value_usd)`` descending puts unknown percentages at the end."""

    accession = "000141036826089999"
    body = nport_xml(
        holding_xml(name="Low", pct_val="1.0", value_usd="100"),
        holding_xml(name="Absent", pct_val=None, value_usd="900"),
        holding_xml(name="High", pct_val="2.0", value_usd="10"),
    )
    web = Web()
    web.json(
        SUBMISSIONS_URL,
        submissions_payload(
            ((accession, FILED, "NPORT-P", "body.xml", REPORT),)
        ),
    )
    web.text(archive_url(accession, "body.xml"), body)

    document = await collect_holdings(
        "SPY", SPY_CIK, client=web.client(), cache_dir=tmp_path, limit=50
    )

    assert [holding["name"] for holding in document_holdings(document)] == [
        "High",
        "Low",
        "Absent",
    ]


async def test_a_warm_cache_serves_the_document_without_a_request(
    tmp_path: Path,
) -> None:
    """A second call over the same cache dir makes zero requests."""

    warm = Web()
    warm.json(SUBMISSIONS_URL, newest_filing_payload())
    warm.text(ARCHIVE_URL, fixture_text())
    first = await collect_holdings(
        "SPY", SPY_CIK, client=warm.client(), cache_dir=tmp_path, limit=50
    )

    forbidden = Web()
    second = await collect_holdings(
        "SPY", SPY_CIK, client=forbidden.client(), cache_dir=tmp_path, limit=50
    )

    assert forbidden.urls() == [], (
        "a cached submissions body and a cached N-PORT body cost no request"
    )
    assert second["holdings"] == first["holdings"]
    assert (second["as_of"], second["filed"], second["accession"]) == (
        REPORT,
        FILED,
        ACCESSION,
    )
    assert second["count"] == first["count"]


# ---------------------------------------------------------------------------
# §2 Collector — the two named failures
# ---------------------------------------------------------------------------


async def test_no_nport_filing_raises_holdings_not_found(
    tmp_path: Path,
) -> None:
    """A registrant whose recent filings hold no NPORT-P has no portfolio to serve."""

    web = Web()
    web.json(
        SUBMISSIONS_URL,
        submissions_payload(
            (("000141036826070001", "2026-07-01", "10-K", "spy10k.htm", REPORT),)
        ),
    )

    with pytest.raises(HoldingsNotFound) as caught:
        await collect_holdings(
            "SPY", SPY_CIK, client=web.client(), cache_dir=tmp_path, limit=50
        )

    assert str(caught.value).strip(), "the error states why there is no portfolio"
    assert web.urls() == [SUBMISSIONS_URL], "there is no body URL to ask for"


async def test_a_failed_body_raises_holdings_unavailable(
    tmp_path: Path,
) -> None:
    """A 500 on the body is one domain error, never an empty holdings list."""

    web = Web()
    web.json(SUBMISSIONS_URL, newest_filing_payload())
    web.status(ARCHIVE_URL, 500)

    with pytest.raises(HoldingsUnavailable) as caught:
        await collect_holdings(
            "SPY", SPY_CIK, client=web.client(), cache_dir=tmp_path, limit=50
        )

    assert "500" in str(caught.value), "the failure names the HTTP status it saw"
    assert not nport_cache_path(tmp_path).exists(), "a failed body is not cached"


async def test_a_body_that_is_not_nport_raises_holdings_unavailable(
    tmp_path: Path,
) -> None:
    """A 200 error page parses to no rows, and no rows is a parse failure."""

    web = Web()
    web.json(SUBMISSIONS_URL, newest_filing_payload())
    web.text(ARCHIVE_URL, "<html><body>request blocked</body></html>")

    with pytest.raises(HoldingsUnavailable):
        await collect_holdings(
            "SPY", SPY_CIK, client=web.client(), cache_dir=tmp_path, limit=50
        )


# ---------------------------------------------------------------------------
# §3 Route — the collector double
# ---------------------------------------------------------------------------


class FakeCollector:
    """A collector double that records the route's call and answers one document."""

    def __init__(
        self,
        *,
        document: dict[str, object] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.document = holdings_document() if document is None else document
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def __call__(
        self,
        symbol: str,
        cik: str,
        *,
        client: Any = None,
        cache_dir: Path | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        """Record one call, then return the canned document or raise."""

        self.calls.append(
            {
                "symbol": symbol,
                "cik": cik,
                "limit": limit,
                "cache_dir": cache_dir,
                "client": client,
            }
        )
        if self.error is not None:
            raise self.error
        return self.document


def holding_document(**overrides: Any) -> dict[str, object]:
    """One holding's JSON shape, built from a real ``EtfHolding``."""

    fields: dict[str, Any] = {
        "name": "NVIDIA Corp",
        "title": "NVIDIA Corp",
        "cusip": "67066G104",
        "balance": 315000000.0,
        "value_usd": 58722060041.15,
        "pct_val": 7.517011844111,
        "asset_category": "EC",
        "country": "US",
        "currency": "USD",
    }
    fields.update(overrides)
    return EtfHolding(**fields).document()


def holdings_document(
    *,
    symbol: str = "SPY",
    holdings: Sequence[dict[str, object]] | None = None,
    **overrides: Any,
) -> dict[str, object]:
    """The collector's document, by default two holdings in pct-descending order."""

    rows = (
        [
            holding_document(),
            holding_document(
                name="Apple Inc",
                title="Apple Inc",
                cusip="037833100",
                balance=205000000.0,
                value_usd=51496255070.56,
                pct_val=6.592036434374,
            ),
        ]
        if holdings is None
        else list(holdings)
    )
    document: dict[str, object] = {
        "symbol": symbol,
        "as_of": REPORT,
        "filed": FILED,
        "accession": ACCESSION,
        "count": len(rows),
        "holdings": rows,
        "provider_errors": [],
    }
    document.update(overrides)
    return document


#: A one-symbol ticker index: the SPY trust the route resolves.
TICKER_PAYLOAD: dict[str, Any] = {
    "0": {"cik_str": 884394, "ticker": "SPY", "title": "SPDR S&P 500 ETF Trust"}
}


def engine_master(*, extra: Sequence[SecurityRecord] = ()) -> SecurityMaster:
    """The identity master the route reads: SPY plus any extras."""

    master = SecurityMaster.from_company_tickers(TICKER_PAYLOAD, as_of="2026-10-26")
    for record in extra:
        master.add(record, Provenance(source="test", as_of="2026-10-26"))
    return master


def engine_service(master: SecurityMaster) -> EngineService:
    """A service whose dataset carries ``master`` and no facts."""

    dataset = EngineDataset(
        fetched_at="2026-10-26T12:00:00+00:00",
        security_master=master,
        facts={},
        errors={},
    )
    return EngineService.from_dataset(dataset)


@asynccontextmanager
async def route_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service: EngineService | None = None,
) -> AsyncIterator[httpx2.AsyncClient]:
    """An ASGI client over a fresh app whose engine service is ``service``."""

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.delenv("MEE_ENGINE_SNAPSHOT", raising=False)
    monkeypatch.delenv("MEE_ENGINE_SYMBOLS", raising=False)

    from app.main import create_app

    application = create_app()
    if service is not None:
        application.state.engine = service
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=application), base_url="http://mee.test"
    ) as client:
        yield client


def install_collector(
    monkeypatch: pytest.MonkeyPatch, fake: FakeCollector
) -> None:
    """Patch both collector seams the route could hold.

    The route may import ``collect_holdings`` into ``app.api.engine`` at module
    import time or call it through ``app.engine.nport``; patching both means the
    double is reached either way, and the shared call log still proves the route
    asked exactly once.
    """

    monkeypatch.setattr("app.engine.nport.collect_holdings", fake)
    monkeypatch.setattr("app.api.engine.collect_holdings", fake, raising=False)


def ten_digit_cik(value: object) -> str:
    """The ten-digit spelling of a CIK, whichever form the route passed."""

    digits = "".join(character for character in str(value) if character.isdigit())
    return digits.zfill(10)


# ---------------------------------------------------------------------------
# §3 Route — 200 documents and the limit
# ---------------------------------------------------------------------------


async def test_holdings_route_returns_the_filing_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """200 carries symbol, as_of, filed, accession, count, holdings and errors."""

    fake = FakeCollector()
    install_collector(monkeypatch, fake)

    async with route_client(
        tmp_path, monkeypatch, engine_service(engine_master())
    ) as client:
        response = await client.get("/engine/etf/SPY/holdings?limit=50")

    assert response.status_code == 200, response.text
    document = response.json()

    assert tuple(sorted(document)) == tuple(sorted(ROUTE_FIELDS)), (
        "the document carries exactly the seven contract keys"
    )
    assert document["symbol"] == "SPY"
    assert document["as_of"] == REPORT
    assert document["filed"] == FILED
    assert document["accession"] == ACCESSION
    assert document["count"] == 2
    assert document["provider_errors"] == []

    holdings = document["holdings"]
    assert [holding["name"] for holding in holdings] == [
        "NVIDIA Corp",
        "Apple Inc",
    ], "the collector's pct-descending order is what the route serves"
    assert [holding["pct_val"] for holding in holdings] == sorted(
        [holding["pct_val"] for holding in holdings], reverse=True
    )
    assert tuple(sorted(holdings[0])) == tuple(sorted(HOLDING_FIELDS))

    assert len(fake.calls) == 1, "the route collects once per request"
    call = fake.calls[0]
    assert call["symbol"] == "SPY"
    assert ten_digit_cik(call["cik"]) == SPY_CIK, (
        "the route resolves the CIK from the loaded identity master"
    )
    assert int(call["limit"]) == 50  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"), (("1", 1), ("500", 500)), ids=["one", "five_hundred"]
)
async def test_holdings_route_accepts_the_inclusive_limit_bounds(
    raw: str, expected: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``limit`` is an integer 1..500, and both bounds are accepted."""

    fake = FakeCollector()
    install_collector(monkeypatch, fake)

    async with route_client(
        tmp_path, monkeypatch, engine_service(engine_master())
    ) as client:
        response = await client.get(f"/engine/etf/SPY/holdings?limit={raw}")

    assert response.status_code == 200, response.text
    assert len(fake.calls) == 1
    assert int(fake.calls[0]["limit"]) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize("limit", ("0", "501", "x"), ids=["zero", "too_many", "text"])
@pytest.mark.parametrize("symbol", ("SPY", "ZZZZ"), ids=["known", "unknown"])
async def test_holdings_route_rejects_an_invalid_limit_before_any_lookup(
    limit: str, symbol: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad page size is ``422 invalid_limit`` even for a symbol not in the master."""

    fake = FakeCollector()
    install_collector(monkeypatch, fake)

    async with route_client(
        tmp_path, monkeypatch, engine_service(engine_master())
    ) as client:
        response = await client.get(f"/engine/etf/{symbol}/holdings?limit={limit}")

    assert response.status_code == 422, response.text
    assert response.json().get("error") == "invalid_limit"
    assert fake.calls == [], "a rejected query never reaches the collector"


# ---------------------------------------------------------------------------
# §3 Route — refusals and the empty-but-successful case
# ---------------------------------------------------------------------------


async def test_holdings_route_unknown_symbol_is_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symbol outside the loaded master is ``security_not_found``."""

    fake = FakeCollector()
    install_collector(monkeypatch, fake)

    async with route_client(
        tmp_path, monkeypatch, engine_service(engine_master())
    ) as client:
        response = await client.get("/engine/etf/ZZZZ/holdings?limit=50")

    assert response.status_code == 404, response.text
    assert response.json().get("error") == "security_not_found"
    assert fake.calls == []


async def test_holdings_route_without_a_dataset_is_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing ingested is ``screen_unavailable``, not an empty portfolio."""

    fake = FakeCollector()
    install_collector(monkeypatch, fake)

    async with route_client(tmp_path, monkeypatch, None) as client:
        response = await client.get("/engine/etf/SPY/holdings?limit=50")

    assert response.status_code == 503, response.text
    assert response.json().get("error") == "screen_unavailable"
    assert fake.calls == []


async def test_holdings_route_symbol_without_a_cik_is_an_empty_200(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A listed symbol with no CIK has no filing to ask for, and says so."""

    record = SecurityRecord(
        symbol="PRIV",
        name="Private Holdings Inc.",
        cik=None,
        exchange=None,
        mic=None,
        asset_class="equity",
        currency="USD",
        country="US",
        sector=None,
        industry=None,
    )
    fake = FakeCollector()
    install_collector(monkeypatch, fake)

    async with route_client(
        tmp_path, monkeypatch, engine_service(engine_master(extra=(record,)))
    ) as client:
        response = await client.get("/engine/etf/PRIV/holdings?limit=50")

    assert response.status_code == 200, response.text
    document = response.json()
    assert tuple(sorted(document)) == tuple(sorted(ROUTE_FIELDS))
    assert document["symbol"] == "PRIV"
    assert document["as_of"] is None
    assert document["filed"] is None
    assert document["accession"] is None
    assert document["count"] == 0
    assert document["holdings"] == []
    assert document["provider_errors"] == ["no CIK"]
    assert fake.calls == [], "there is no CIK to collect against"


async def test_holdings_route_without_a_filing_is_404_holdings_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The collector's ``HoldingsNotFound`` becomes ``404 holdings_not_found``."""

    fake = FakeCollector(error=HoldingsNotFound("CIK0000884394 has no NPORT-P filing"))
    install_collector(monkeypatch, fake)

    async with route_client(
        tmp_path, monkeypatch, engine_service(engine_master())
    ) as client:
        response = await client.get("/engine/etf/SPY/holdings?limit=50")

    assert response.status_code == 404, response.text
    assert response.json().get("error") == "holdings_not_found"
    assert len(fake.calls) == 1


async def test_holdings_route_failure_is_503_holdings_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable body is ``503 holdings_unavailable``, never an empty 200."""

    fake = FakeCollector(
        error=HoldingsUnavailable("HTTPStatusError: 500 on primary_doc.xml")
    )
    install_collector(monkeypatch, fake)

    async with route_client(
        tmp_path, monkeypatch, engine_service(engine_master())
    ) as client:
        response = await client.get("/engine/etf/SPY/holdings?limit=50")

    assert response.status_code == 503, response.text
    assert response.json().get("error") == "holdings_unavailable"
    assert len(fake.calls) == 1
