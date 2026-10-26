"""RED contract for R5A: SEC Form 4 insider transactions.

Three surfaces are pinned here, and every assertion is a sentence from
``briefs/market-engine/R5A-CONTRACT.md`` §1–§3:

1. **The parser** reads only ``nonDerivativeTransaction`` rows, echoes the
   filing's identity into every row, and reports a missing number as ``None``
   rather than a zero. The live Apple filing
   (``tests/fixtures/form4_aapl.xml``, the 2026-10-20 Form 4) supplies the one
   real row; the synthetic documents below isolate the role matrix, the
   incomplete-row rule and the malformed-document answer.
2. **The collector** walks ``filings.recent`` for ``form == "4"``, builds the
   archive URL from the accession and the *basename* of ``primaryDocument``,
   reports a failed body without inventing a row, and serves a warm cache
   without a request.
3. **The route** answers ``GET /engine/insider/{symbol}`` with the document
   shape, the house error envelopes, and the one empty-but-successful case
   (a symbol with no CIK).

Everything is offline: the only transport is ``httpx2.MockTransport`` keyed by
exact URL, every date is a literal, and the suite-wide socket guard in
``tests/conftest.py`` would fail the test if anything tried to leave. The
collector seam is patched in **both** places it can live — the engine module and
the name the API module imports — because the route is written after this file
and must not be able to bypass the double by choosing the other import style.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from app.engine.ingest import EngineDataset
from app.engine.insider import InsiderTransaction, collect_insider, parse_form4
from app.engine.model import Provenance, SecurityRecord
from app.engine.security import SecurityMaster
from app.engine.service import EngineService

ROOT = Path(__file__).resolve().parents[1]

#: The real Apple Form 4 body, copied byte for byte from EDGAR.
FIXTURE = ROOT / "tests" / "fixtures" / "form4_aapl.xml"

#: The accession/filing/URL triple this slice echoes into every parsed row.
ACCESSION = "0001140361-26-040540"
FILED = "2026-10-22"
URL = "https://www.sec.gov/Archives/edgar/data/320193/000114036126040540/form4.xml"

#: Ten-digit CIK of Apple, as the identity master and EDGAR spell it.
AAPL_CIK = "0000320193"

#: The fourteen fields one transaction carries onto the wire.
INSIDER_FIELDS: tuple[str, ...] = (
    "accession",
    "filed",
    "period_of_report",
    "owner_name",
    "owner_cik",
    "role",
    "security",
    "transaction_date",
    "code",
    "shares",
    "price",
    "acquired_disposed",
    "shares_after",
    "url",
)

#: The submissions document the collector reads, as EDGAR serves it.
SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{AAPL_CIK}.json"

#: A second Form 4 whose body answers 500; its failure is reported, not hidden.
FAILING_ACCESSION = "0001140361-26-040500"
FAILING_URL = (
    "https://www.sec.gov/Archives/edgar/data/320193/000114036126040500/form4b.xml"
)


# ---------------------------------------------------------------------------
# Inline Form 4 documents
# ---------------------------------------------------------------------------


def transaction_xml(
    *,
    security: str | None = "Common Stock",
    transaction_date: str | None = "2026-10-20",
    code: str | None = "P",
    shares: str | None = "100",
    price: str | None = "10.50",
    acquired_disposed: str | None = "A",
    shares_after: str | None = "1100",
) -> str:
    """One ``nonDerivativeTransaction``; ``None`` omits the element.

    An empty string is different from ``None`` on purpose: ``""`` keeps the
    element and leaves its ``<value>`` blank, which is the EDGAR shape the
    parser must translate to ``None`` instead of zero.
    """

    parts: list[str] = ["<nonDerivativeTransaction>"]
    if security is not None:
        parts.append(f"<securityTitle><value>{security}</value></securityTitle>")
    if transaction_date is not None:
        parts.append(
            f"<transactionDate><value>{transaction_date}</value></transactionDate>"
        )
    if code is not None:
        parts.append(
            "<transactionCoding>"
            f"<transactionCode>{code}</transactionCode>"
            "</transactionCoding>"
        )

    amounts: list[str] = []
    if shares is not None:
        amounts.append(
            f"<transactionShares><value>{shares}</value></transactionShares>"
        )
    if price is not None:
        amounts.append(
            "<transactionPricePerShare>"
            f"<value>{price}</value>"
            "</transactionPricePerShare>"
        )
    if acquired_disposed is not None:
        amounts.append(
            "<transactionAcquiredDisposedCode>"
            f"<value>{acquired_disposed}</value>"
            "</transactionAcquiredDisposedCode>"
        )
    parts.append("<transactionAmounts>" + "".join(amounts) + "</transactionAmounts>")

    if shares_after is not None:
        parts.append(
            "<postTransactionAmounts>"
            "<sharesOwnedFollowingTransaction>"
            f"<value>{shares_after}</value>"
            "</sharesOwnedFollowingTransaction>"
            "</postTransactionAmounts>"
        )
    parts.append("</nonDerivativeTransaction>")
    return "".join(parts)


def derivative_transaction_xml() -> str:
    """A derivative row whose numbers must never surface as a share trade."""

    return (
        "<derivativeTransaction>"
        "<securityTitle><value>Employee Stock Option (Right to Buy)</value></securityTitle>"
        "<transactionDate><value>2026-10-20</value></transactionDate>"
        "<transactionCoding><transactionCode>M</transactionCode></transactionCoding>"
        "<transactionAmounts>"
        "<transactionShares><value>999</value></transactionShares>"
        "<transactionPricePerShare><value>1</value></transactionPricePerShare>"
        "<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>"
        "</transactionAmounts>"
        "</derivativeTransaction>"
    )


def relationship_xml(
    *,
    director: bool = False,
    officer: bool = False,
    ten_percent: bool = False,
    other: bool = False,
    officer_title: str | None = None,
) -> str:
    """A ``reportingOwnerRelationship`` with the given flags set to ``true``."""

    flags = (
        ("isDirector", director),
        ("isOfficer", officer),
        ("isTenPercentOwner", ten_percent),
        ("isOther", other),
    )
    parts = [
        f"<{name}>{'true' if value else 'false'}</{name}>" for name, value in flags
    ]
    if officer_title is not None:
        parts.append(f"<officerTitle>{officer_title}</officerTitle>")
    return "<reportingOwnerRelationship>" + "".join(parts) + "</reportingOwnerRelationship>"


#: The default relationship used by documents that are not about roles.
DIRECTOR_RELATIONSHIP = relationship_xml(director=True)


def form4_xml(
    *,
    transactions: str,
    relationship: str = DIRECTOR_RELATIONSHIP,
    derivative: str = "",
    period: str = "2026-10-20",
    owner_cik: str | None = "0001780525",
    owner_name: str = "Newstead Jennifer",
) -> str:
    """A complete ``ownershipDocument`` around the supplied rows."""

    owner_id = (
        f"<rptOwnerCik>{owner_cik}</rptOwnerCik>" if owner_cik is not None else ""
    )
    derivative_table = (
        f"<derivativeTable>{derivative}</derivativeTable>" if derivative else ""
    )
    return (
        '<?xml version="1.0"?>'
        "<ownershipDocument>"
        "<documentType>4</documentType>"
        f"<periodOfReport>{period}</periodOfReport>"
        "<issuer>"
        "<issuerCik>0000320193</issuerCik>"
        "<issuerName>Apple Inc.</issuerName>"
        "<issuerTradingSymbol>AAPL</issuerTradingSymbol>"
        "</issuer>"
        "<reportingOwner>"
        "<reportingOwnerId>"
        f"{owner_id}"
        f"<rptOwnerName>{owner_name}</rptOwnerName>"
        "</reportingOwnerId>"
        f"{relationship}"
        "</reportingOwner>"
        f"<nonDerivativeTable>{transactions}</nonDerivativeTable>"
        f"{derivative_table}"
        "</ownershipDocument>"
    )


# ---------------------------------------------------------------------------
# §1 Parser — the real filing
# ---------------------------------------------------------------------------


def test_parse_form4_reads_the_live_apple_filing_exactly() -> None:
    """The fixture's one sale is read with its own code, owner and amounts."""

    rows = parse_form4(
        FIXTURE.read_text(encoding="utf-8"), accession=ACCESSION, filed=FILED, url=URL
    )

    assert len(rows) == 1, "the filing carries exactly one non-derivative row"
    row = rows[0]
    assert isinstance(row, InsiderTransaction)
    assert (row.accession, row.filed, row.url) == (ACCESSION, FILED, URL)
    assert row.period_of_report == "2026-10-20"
    assert row.owner_name == "Newstead Jennifer"
    assert row.owner_cik == "0001780525"
    assert row.role == "Officer: SVP, GC and Government Affairs"
    assert row.security == "Common Stock"
    assert row.transaction_date == "2026-10-20"
    assert row.code == "S"
    assert row.shares == 2399.0
    assert row.price == 339.62
    assert row.acquired_disposed == "D"
    assert row.shares_after == 34795.0


def test_parse_form4_ignores_derivative_transactions() -> None:
    """An option exercise is a different row shape, not a share purchase."""

    document = form4_xml(
        transactions=transaction_xml(code="P", shares="100"),
        derivative=derivative_transaction_xml(),
    )

    rows = parse_form4(document, accession=ACCESSION, filed=FILED, url=URL)

    assert len(rows) == 1
    assert rows[0].code == "P"
    assert rows[0].security == "Common Stock"


def test_parse_form4_keeps_document_order_for_several_rows() -> None:
    """Two non-derivative rows stay in the order the document lists them."""

    document = form4_xml(
        transactions=transaction_xml(code="P") + transaction_xml(code="S")
    )

    rows = parse_form4(document, accession=ACCESSION, filed=FILED, url=URL)

    assert [row.code for row in rows] == ["P", "S"]


# ---------------------------------------------------------------------------
# §1 Role matrix
# ---------------------------------------------------------------------------

ROLE_CASES: tuple[tuple[dict[str, Any], str | None], ...] = (
    ({"director": True}, "Director"),
    (
        {"officer": True, "officer_title": "Chief Financial Officer"},
        "Officer: Chief Financial Officer",
    ),
    ({"ten_percent": True}, "10% Owner"),
    ({"other": True}, "Other"),
    (
        {
            "director": True,
            "officer": True,
            "ten_percent": True,
            "other": True,
            "officer_title": "Chief Executive Officer",
        },
        "Director; Officer: Chief Executive Officer; 10% Owner; Other",
    ),
    ({}, None),
)


@pytest.mark.parametrize(
    ("flags", "expected"),
    ROLE_CASES,
    ids=["director", "officer", "ten_percent", "other", "all", "none"],
)
def test_parse_form4_builds_the_role_from_the_relationship_flags(
    flags: dict[str, Any], expected: str | None
) -> None:
    """Each flag contributes its contract label, joined in contract order."""

    document = form4_xml(
        transactions=transaction_xml(), relationship=relationship_xml(**flags)
    )

    rows = parse_form4(document, accession=ACCESSION, filed=FILED, url=URL)

    assert len(rows) == 1
    assert rows[0].role == expected


# ---------------------------------------------------------------------------
# §1 Missing values and incomplete rows
# ---------------------------------------------------------------------------

MISSING_VALUE_CASES: tuple[tuple[str, dict[str, Any], str], ...] = (
    ("blank shares", {"shares": ""}, "shares"),
    ("blank price", {"price": ""}, "price"),
    ("absent price", {"price": None}, "price"),
    ("blank shares_after", {"shares_after": ""}, "shares_after"),
    ("absent shares_after", {"shares_after": None}, "shares_after"),
)


@pytest.mark.parametrize(
    ("label", "amounts", "attribute"),
    MISSING_VALUE_CASES,
    ids=[case[0] for case in MISSING_VALUE_CASES],
)
def test_a_missing_amount_is_none_never_zero(
    label: str, amounts: dict[str, Any], attribute: str
) -> None:
    """Blank or absent ``<value>`` is unknown, and unknown is ``None``."""

    document = form4_xml(transactions=transaction_xml(**amounts))

    rows = parse_form4(document, accession=ACCESSION, filed=FILED, url=URL)

    assert len(rows) == 1
    assert getattr(rows[0], attribute) is None, (
        f"{label} must read as None, not as a zero-filled amount"
    )


def test_an_absent_owner_cik_is_none() -> None:
    """``owner_cik`` is optional; a missing element does not fabricate one."""

    document = form4_xml(transactions=transaction_xml(), owner_cik=None)

    rows = parse_form4(document, accession=ACCESSION, filed=FILED, url=URL)

    assert len(rows) == 1
    assert rows[0].owner_cik is None


INCOMPLETE_ROW_CASES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("no security", {"security": None}),
    ("no date", {"transaction_date": None}),
    ("no code", {"code": None}),
)


@pytest.mark.parametrize(
    ("label", "omissions"),
    INCOMPLETE_ROW_CASES,
    ids=[case[0] for case in INCOMPLETE_ROW_CASES],
)
def test_a_row_missing_security_date_or_code_is_skipped(
    label: str, omissions: dict[str, Any]
) -> None:
    """An incomplete row is dropped; the complete row beside it still returns."""

    incomplete: dict[str, Any] = {"code": "S"}
    incomplete.update(omissions)
    document = form4_xml(
        transactions=transaction_xml(code="P") + transaction_xml(**incomplete)
    )

    rows = parse_form4(document, accession=ACCESSION, filed=FILED, url=URL)

    assert len(rows) == 1, f"the row with {label} must be skipped, not zero-filled"
    assert rows[0].code == "P"


MALFORMED_DOCUMENTS: tuple[str, ...] = (
    "",
    "not xml at all",
    "<ownershipDocument><nonDerivativeTable>",
    "<ownershipDocument><nonDerivativeTransaction></ownershipDocument>",
)


@pytest.mark.parametrize(
    "document", MALFORMED_DOCUMENTS, ids=["empty", "text", "unclosed", "mismatched"]
)
def test_parse_form4_returns_empty_for_malformed_xml(document: str) -> None:
    """Bad XML has one answer — ``()`` — not a library-specific exception."""

    assert (
        parse_form4(document, accession=ACCESSION, filed=FILED, url=URL) == ()
    )


def test_parse_form4_returns_empty_for_a_foreign_root() -> None:
    """A 200 body that is not an ownership document is not a Form 4."""

    document = '<?xml version="1.0"?><companyFacts><facts/></companyFacts>'

    assert (
        parse_form4(document, accession=ACCESSION, filed=FILED, url=URL) == ()
    )


# ---------------------------------------------------------------------------
# §1 Numeric text
# ---------------------------------------------------------------------------

INVALID_NUMBERS: tuple[str, ...] = ("twelve", "N/A", "1,234")


@pytest.mark.parametrize("raw", INVALID_NUMBERS, ids=["word", "na", "commas"])
def test_a_non_numeric_amount_is_none(raw: str) -> None:
    """Text that cannot be a float is unknown, not an exception and not zero."""

    document = form4_xml(transactions=transaction_xml(shares=raw))

    rows = parse_form4(document, accession=ACCESSION, filed=FILED, url=URL)

    assert len(rows) == 1
    assert rows[0].shares is None


def test_a_real_zero_stays_zero() -> None:
    """``0`` is a reported number; only blank or bad text becomes ``None``."""

    document = form4_xml(
        transactions=transaction_xml(shares="0", price="0", shares_after="0")
    )

    rows = parse_form4(document, accession=ACCESSION, filed=FILED, url=URL)

    assert len(rows) == 1
    assert rows[0].shares is not None and rows[0].shares == 0.0
    assert rows[0].price is not None and rows[0].price == 0.0
    assert rows[0].shares_after is not None and rows[0].shares_after == 0.0


# ---------------------------------------------------------------------------
# §2 Collector — offline transport and submissions fixtures
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


def form4_url(accession: str, *, document: str = "form4.xml") -> str:
    """The archive URL for one body: no dashes, no ``xslF345X06/`` prefix."""

    return (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        f"{accession.replace('-', '')}/{document}"
    )


def submissions_payload(
    filings: Sequence[tuple[str, str, str, str]],
) -> dict[str, Any]:
    """A submissions document whose ``filings.recent`` rows are the tuples.

    Each tuple is ``(accession, filing_date, form, primary_document)``. EDGAR
    spells ``primaryDocument`` with an ``xslF345X06/`` prefix for Form 4, which
    is exactly what the collector must strip before building the URL.
    """

    columns: dict[str, list[str]] = {
        "accessionNumber": [],
        "filingDate": [],
        "form": [],
        "primaryDocument": [],
    }
    for accession, filed, form, document in filings:
        columns["accessionNumber"].append(accession)
        columns["filingDate"].append(filed)
        columns["form"].append(form)
        columns["primaryDocument"].append(document)
    return {
        "cik": AAPL_CIK,
        "name": "Apple Inc.",
        "tickers": ["AAPL"],
        "exchanges": ["Nasdaq"],
        "filings": {"recent": columns, "files": []},
    }


def aapl_fixture_body() -> str:
    """The real Form 4 XML as text."""

    return FIXTURE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# §2 Collector — walking filings.recent
# ---------------------------------------------------------------------------


async def test_collect_insider_reads_the_newest_form4_and_reports_a_failure(
    tmp_path: Path,
) -> None:
    """Two Form 4 rows, one 500: one item, one message, no fabricated row."""

    web = Web()
    web.json(
        SUBMISSIONS_URL,
        submissions_payload(
            (
                (ACCESSION, FILED, "4", "xslF345X06/form4.xml"),
                (FAILING_ACCESSION, "2026-10-21", "4", "xslF345X06/form4b.xml"),
                ("0001140361-26-030001", "2026-09-01", "3", "xslF345X06/form3.xml"),
            )
        ),
    )
    web.text(URL, aapl_fixture_body())
    web.status(FAILING_URL, 500)

    items, errors = await collect_insider(
        "AAPL", AAPL_CIK, client=web.client(), cache_dir=tmp_path, limit=5
    )

    assert [item.accession for item in items] == [ACCESSION]
    assert items[0].owner_name == "Newstead Jennifer"
    assert items[0].filed == FILED
    assert len(errors) == 1 and errors[0].strip(), "the failed body is reported"
    assert web.urls() == [SUBMISSIONS_URL, URL, FAILING_URL], (
        "the form 3 is never fetched, and both form 4 URLs strip the xsl prefix"
    )
    cache = tmp_path / f"CIK{AAPL_CIK}-form4-{ACCESSION.replace('-', '')}.xml"
    assert cache.is_file(), "the fetched body is cached under the contract name"
    assert "Newstead Jennifer" in cache.read_text(encoding="utf-8")


async def test_a_warm_cache_serves_the_body_without_a_request(tmp_path: Path) -> None:
    """A second call over the same cache dir makes zero form-4 requests."""

    warm = Web()
    warm.json(
        SUBMISSIONS_URL,
        submissions_payload(
            (
                (ACCESSION, FILED, "4", "xslF345X06/form4.xml"),
                (FAILING_ACCESSION, "2026-10-21", "4", "xslF345X06/form4b.xml"),
            )
        ),
    )
    warm.text(URL, aapl_fixture_body())
    warm.status(FAILING_URL, 500)
    first_items, _ = await collect_insider(
        "AAPL", AAPL_CIK, client=warm.client(), cache_dir=tmp_path, limit=5
    )

    forbidden = Web()
    second_items, second_errors = await collect_insider(
        "AAPL", AAPL_CIK, client=forbidden.client(), cache_dir=tmp_path, limit=1
    )

    assert forbidden.urls() == [], "a cached submissions body and Form 4 cost no request"
    assert second_items == first_items
    assert second_errors == ()


async def test_collect_insider_sorts_by_filed_then_transaction_date_descending(
    tmp_path: Path,
) -> None:
    """Newest filing first; within a filing, the newest transaction first."""

    filings = (
        (("0001140361-26-000001", "2026-10-20", "4", "form4.xml"), "2026-10-20"),
        (("0001140361-26-000002", "2026-10-22", "4", "form4.xml"), "2026-10-19"),
        (("0001140361-26-000003", "2026-10-22", "4", "form4.xml"), "2026-10-21"),
    )
    web = Web()
    web.json(
        SUBMISSIONS_URL,
        submissions_payload(tuple(row[0] for row in filings)),
    )
    for (accession, _filed, _form, document), transaction_date in filings:
        web.text(
            form4_url(accession, document=document),
            form4_xml(transactions=transaction_xml(transaction_date=transaction_date)),
        )

    items, errors = await collect_insider(
        "AAPL", AAPL_CIK, client=web.client(), cache_dir=tmp_path, limit=5
    )

    assert errors == ()
    assert [item.accession for item in items] == [
        "0001140361-26-000003",
        "0001140361-26-000002",
        "0001140361-26-000001",
    ]


async def test_collect_insider_reports_every_failed_body_and_returns_no_items(
    tmp_path: Path,
) -> None:
    """An outage is a list of messages, never a synthesized transaction."""

    other = "0001140361-26-040501"
    other_url = form4_url(other)
    web = Web()
    web.json(
        SUBMISSIONS_URL,
        submissions_payload(
            (
                (ACCESSION, FILED, "4", "xslF345X06/form4.xml"),
                (other, "2026-10-21", "4", "xslF345X06/form4b.xml"),
            )
        ),
    )
    web.status(URL, 500)
    web.status(other_url, 503)

    items, errors = await collect_insider(
        "AAPL", AAPL_CIK, client=web.client(), cache_dir=tmp_path, limit=5
    )

    assert items == ()
    assert len(errors) == 2


# ---------------------------------------------------------------------------
# §3 Route — the collector double
# ---------------------------------------------------------------------------


class FakeCollector:
    """A collector double that records the route's call and answers canned rows."""

    def __init__(
        self,
        *,
        items: Sequence[InsiderTransaction] = (),
        errors: Sequence[str] = (),
    ) -> None:
        self.items = tuple(items)
        self.errors = tuple(errors)
        self.calls: list[dict[str, object]] = []

    async def __call__(
        self,
        symbol: str,
        cik: str,
        *,
        client: Any = None,
        cache_dir: Path | None = None,
        limit: int = 5,
    ) -> tuple[tuple[InsiderTransaction, ...], tuple[str, ...]]:
        """Record one call and return the canned rows and errors."""

        self.calls.append(
            {
                "symbol": symbol,
                "cik": cik,
                "limit": limit,
                "cache_dir": cache_dir,
                "client": client,
            }
        )
        return self.items, self.errors


def insider_item(**overrides: Any) -> InsiderTransaction:
    """One transaction literal built from the real filing's values."""

    fields: dict[str, Any] = {
        "accession": ACCESSION,
        "filed": FILED,
        "period_of_report": "2026-10-20",
        "owner_name": "Newstead Jennifer",
        "owner_cik": "0001780525",
        "role": "Officer: SVP, GC and Government Affairs",
        "security": "Common Stock",
        "transaction_date": "2026-10-20",
        "code": "S",
        "shares": 2399.0,
        "price": 339.62,
        "acquired_disposed": "D",
        "shares_after": 34795.0,
        "url": URL,
    }
    fields.update(overrides)
    return InsiderTransaction(**fields)


TICKERS_FIXTURE = ROOT / "tests" / "fixtures" / "engine_sec_company_tickers_sample.json"


def engine_master(*, extra: Sequence[SecurityRecord] = ()) -> SecurityMaster:
    """The identity master the route reads, four SEC symbols plus extras."""

    master = SecurityMaster.from_company_tickers(
        json.loads(TICKERS_FIXTURE.read_text(encoding="utf-8")), as_of="2026-10-26"
    )
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

    The route may import ``collect_insider`` into ``app.api.engine`` at module
    import time or call it through ``app.engine.insider``; patching both means
    the double is reached either way, and the shared call log still proves the
    route asked exactly once.
    """

    monkeypatch.setattr("app.engine.insider.collect_insider", fake)
    monkeypatch.setattr("app.api.engine.collect_insider", fake, raising=False)


def ten_digit_cik(value: object) -> str:
    """The ten-digit spelling of a CIK, whichever form the route passed."""

    digits = "".join(character for character in str(value) if character.isdigit())
    return digits.zfill(10)


# ---------------------------------------------------------------------------
# §3 Route — 200 documents
# ---------------------------------------------------------------------------


async def test_insider_route_returns_the_filing_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """200 carries symbol, as_of, count, items and the empty error list."""

    fake = FakeCollector(items=(insider_item(),))
    install_collector(monkeypatch, fake)

    async with route_client(
        tmp_path, monkeypatch, engine_service(engine_master())
    ) as client:
        response = await client.get("/engine/insider/AAPL?limit=5")

    assert response.status_code == 200, response.text
    document = response.json()
    assert document["symbol"] == "AAPL"
    assert document["as_of"] == FILED
    assert document["count"] == 1
    assert document["provider_errors"] == []
    assert len(document["items"]) == 1

    item = document["items"][0]
    assert tuple(sorted(item)) == tuple(sorted(INSIDER_FIELDS)), (
        "every item carries the fourteen InsiderTransaction fields"
    )
    assert item["accession"] == ACCESSION
    assert item["owner_name"] == "Newstead Jennifer"
    assert item["shares"] == 2399.0

    assert len(fake.calls) == 1, "the route collects once per request"
    call = fake.calls[0]
    assert call["symbol"] == "AAPL"
    assert ten_digit_cik(call["cik"]) == AAPL_CIK, (
        "the route resolves the CIK from the loaded identity master"
    )
    assert int(call["limit"]) == 5  # type: ignore[arg-type]


async def test_insider_route_as_of_is_the_newest_filed_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``as_of`` is the max ``filed`` in the returned page, not the fetch time."""

    fake = FakeCollector(
        items=(
            insider_item(accession=ACCESSION, filed="2026-10-22"),
            insider_item(accession="0001140361-26-040600", filed="2026-10-24"),
        )
    )
    install_collector(monkeypatch, fake)

    async with route_client(
        tmp_path, monkeypatch, engine_service(engine_master())
    ) as client:
        response = await client.get("/engine/insider/AAPL")

    assert response.status_code == 200, response.text
    document = response.json()
    assert document["count"] == 2
    assert document["as_of"] == "2026-10-24"


async def test_insider_route_keeps_200_and_lists_a_partial_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One good filing beside one failed one is a 200 with a message."""

    message = f"{FAILING_ACCESSION}: HTTP 500"
    fake = FakeCollector(items=(insider_item(),), errors=(message,))
    install_collector(monkeypatch, fake)

    async with route_client(
        tmp_path, monkeypatch, engine_service(engine_master())
    ) as client:
        response = await client.get("/engine/insider/AAPL")

    assert response.status_code == 200, response.text
    assert response.json()["provider_errors"] == [message]
    assert response.json()["count"] == 1


# ---------------------------------------------------------------------------
# §3 Route — refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("limit", ["0", "21", "x"], ids=["zero", "too_many", "text"])
async def test_insider_route_rejects_an_invalid_limit_with_422(
    limit: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``limit`` is an integer 1..20; anything else is ``invalid_limit``."""

    fake = FakeCollector(items=(insider_item(),))
    install_collector(monkeypatch, fake)

    async with route_client(
        tmp_path, monkeypatch, engine_service(engine_master())
    ) as client:
        response = await client.get(f"/engine/insider/AAPL?limit={limit}")

    assert response.status_code == 422, response.text
    assert response.json().get("error") == "invalid_limit"
    assert fake.calls == [], "a rejected query never reaches the network seam"


async def test_insider_route_unknown_symbol_is_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symbol outside the loaded master is ``security_not_found``."""

    fake = FakeCollector(items=(insider_item(),))
    install_collector(monkeypatch, fake)

    async with route_client(
        tmp_path, monkeypatch, engine_service(engine_master())
    ) as client:
        response = await client.get("/engine/insider/ZZZZ?limit=5")

    assert response.status_code == 404, response.text
    assert response.json().get("error") == "security_not_found"
    assert fake.calls == []


async def test_insider_route_without_a_dataset_is_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing ingested is ``screen_unavailable``, not an empty document."""

    fake = FakeCollector(items=(insider_item(),))
    install_collector(monkeypatch, fake)

    async with route_client(tmp_path, monkeypatch, None) as client:
        response = await client.get("/engine/insider/AAPL?limit=5")

    assert response.status_code == 503, response.text
    assert response.json().get("error") == "screen_unavailable"
    assert fake.calls == []


async def test_insider_route_symbol_without_a_cik_is_an_empty_200(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A listed symbol with no CIK has no filings, and says so plainly."""

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
    fake = FakeCollector(items=(insider_item(),))
    install_collector(monkeypatch, fake)

    async with route_client(
        tmp_path, monkeypatch, engine_service(engine_master(extra=(record,)))
    ) as client:
        response = await client.get("/engine/insider/PRIV?limit=5")

    assert response.status_code == 200, response.text
    document = response.json()
    assert document["symbol"] == "PRIV"
    assert document["as_of"] is None
    assert document["count"] == 0
    assert document["items"] == []
    assert document["provider_errors"] == ["no CIK"]
    assert fake.calls == [], "there is no CIK to collect against"


async def test_insider_route_all_filings_failed_is_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An outage with nothing cached is ``insider_unavailable``, not an empty 200."""

    fake = FakeCollector(
        items=(), errors=(f"{ACCESSION}: HTTP 500", "0001140361-26-040500: HTTP 503")
    )
    install_collector(monkeypatch, fake)

    async with route_client(
        tmp_path, monkeypatch, engine_service(engine_master())
    ) as client:
        response = await client.get("/engine/insider/AAPL?limit=5")

    assert response.status_code == 503, response.text
    assert response.json().get("error") == "insider_unavailable"
