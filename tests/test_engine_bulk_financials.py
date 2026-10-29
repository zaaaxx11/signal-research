"""Contract for SEC bulk financial statement ingestion (``app.engine.bulk_financials``).

The slice answers one question: **how does a 10k-symbol universe get XBRL facts
without ten thousand requests?** EDGAR publishes one ZIP per quarter holding
every filer's statements as TSV; this module downloads it once, caches it, and
streams ``sub.txt``/``num.txt`` into an index keyed by CIK.

Every rule here is about not lying with a number and not exhausting the host:

* **Streaming, never resident.** ``num.txt`` is ~875 MB uncompressed. The
  parsers are generators over ``zipfile`` + ``csv``; a fact is materialised one
  row at a time and only kept when its CIK was asked for. A test that needed
  the whole file in RAM would be a test of the wrong thing.
* **Header-driven columns.** The 2026q3 layout is not the layout of the 2019
  readme (``segments`` replaced ``dimh``/``iprx``); columns are resolved by
  name from the header row, so a reordered file still parses.
* **Absent, never zero.** A blank or unparseable ``value`` is a skipped row,
  not a ``0.0`` fact. A missing ``uom`` is kept as the empty string and left
  for the mapper to reject.
* **One download per quarter.** A warm cache answers with zero requests; the
  User-Agent carries contact information because EDGAR fair-access asks for it.

The fixture is a ZIP built in-test from a handful of ``sub.txt``/``num.txt``
rows: two issuers, a restatement, a dimensional row, a wrong-unit row and a
comma-formatted value. The suite-wide socket guard in ``tests/conftest.py``
proves no test ever reaches sec.gov.
"""

from __future__ import annotations

import json
import zipfile
from datetime import date
from pathlib import Path

import httpx2
import pytest

from app.engine import bulk_financials as bf
from app.engine.bulk_financials import (
    Fact,
    FactIndex,
    Submission,
    _fact_sort_key,
    build_quarters,
)
from app.providers import http as provider_http

# ---------------------------------------------------------------------------
# Fixture ZIP — the real 2026q3 column layout, a few rows of it
# ---------------------------------------------------------------------------

SUB_HEADER = [
    "adsh", "cik", "name", "sic", "countryba", "stprba", "cityba", "zipba",
    "bas1", "bas2", "baph", "countryma", "stprma", "cityma", "zipma", "mas1",
    "mas2", "countryinc", "stprinc", "ein", "former", "changed", "afs", "wksi",
    "fye", "form", "period", "fy", "fp", "filed", "accepted", "prevrpt",
    "detail", "instance", "nciks", "aciks",
]

NUM_HEADER = [
    "adsh", "tag", "version", "ddate", "qtrs", "uom", "segments", "coreg",
    "value", "footnote",
]

AAPL_ADSH_Q = "0000320193-26-000020"
AAPL_ADSH_K = "0000320193-25-000079"
MSFT_ADSH = "0000789019-26-000045"
AAPL_CIK = "0000320193"
MSFT_CIK = "0000789019"


def sub_row(
    adsh: str,
    cik: int,
    name: str,
    form: str,
    period: str,
    fy: int,
    fp: str,
    filed: str,
    sic: str = "3571",
) -> list[str]:
    """One ``sub.txt`` row in the real 36-column layout."""

    row = [""] * len(SUB_HEADER)
    row[0] = adsh
    row[1] = str(cik)
    row[2] = name
    row[3] = sic
    row[25] = form
    row[26] = period
    row[27] = str(fy)
    row[28] = fp
    row[29] = filed
    return row


def num_row(
    adsh: str,
    tag: str,
    ddate: str,
    qtrs: object,
    uom: str,
    value: object,
    *,
    version: str = "us-gaap/2026",
    segments: str = "",
    coreg: str = "",
    footnote: str = "",
) -> list[str]:
    """One ``num.txt`` row in the real 10-column layout."""

    return [
        adsh,
        tag,
        version,
        ddate,
        "" if qtrs is None else str(qtrs),
        uom,
        segments,
        coreg,
        "" if value is None else str(value),
        footnote,
    ]


def fixture_sub_rows() -> list[list[str]]:
    return [
        sub_row(AAPL_ADSH_Q, 320193, "Apple Inc.", "10-Q", "20260630", 2026, "Q3", "20260731"),
        sub_row(AAPL_ADSH_K, 320193, "Apple Inc.", "10-K", "20250927", 2025, "FY", "20251031"),
        sub_row(MSFT_ADSH, 789019, "Microsoft Corp", "10-Q", "20260630", 2026, "Q4", "20260729"),
        # A row with no accession is not a filing.
        [""] + [""] * (len(SUB_HEADER) - 1),
        # A truncated row is not a filing either.
        sub_row("0000000000-26-000999", 1, "Broken", "8-K", "20260101", 2026, "Q1", "20260102")[:9],
    ]


def fixture_num_rows() -> list[list[str]]:
    revenue_tag = "RevenueFromContractWithCustomerExcludingAssessedTax"
    return [
        # --- Apple: the annual duration the mapper must pick ----------------
        num_row(AAPL_ADSH_K, revenue_tag, "20250927", 4, "USD", 416_161_000_000),
        # A newer nine-month YTD and a newer quarter must not beat the annual.
        num_row(AAPL_ADSH_Q, revenue_tag, "20260627", 3, "USD", 300_000_000_000),
        num_row(AAPL_ADSH_Q, revenue_tag, "20260627", 1, "USD", 94_000_000_000),
        # A dimensional (segment) row is not the consolidated fact.
        num_row(AAPL_ADSH_Q, revenue_tag, "20260627", 4, "USD", 50_000_000, segments="SEG-1"),
        # --- Apple: restatement — same tag/date, later filing wins ----------
        num_row(AAPL_ADSH_K, "NetIncomeLoss", "20250927", 4, "USD", 112_010_000_000),
        num_row(AAPL_ADSH_Q, "NetIncomeLoss", "20250927", 4, "USD", 111_000_000_000),
        num_row(AAPL_ADSH_K, "EarningsPerShareDiluted", "20250927", 4, "USD/shares", 7.46),
        num_row(AAPL_ADSH_K, "GrossProfit", "20250927", 4, "USD", 195_200_000_000),
        num_row(AAPL_ADSH_K, "OperatingIncomeLoss", "20250927", 4, "USD", 122_432_000_000),
        # --- Apple: instants -------------------------------------------------
        num_row(AAPL_ADSH_K, "Assets", "20250927", 0, "USD", 364_980_000_000),
        num_row(AAPL_ADSH_K, "StockholdersEquity", "20250927", 0, "USD", 73_000_000_000),
        num_row(AAPL_ADSH_K, "AssetsCurrent", "20250927", 0, "USD", 152_987_000_000),
        num_row(AAPL_ADSH_K, "LiabilitiesCurrent", "20250927", 0, "USD", 176_000_000_000),
        num_row(AAPL_ADSH_K, "InventoryNet", "20250927", 0, "USD", 7_000_000_000),
        num_row(AAPL_ADSH_K, "CashAndCashEquivalentsAtCarryingValue", "20250927", 0, "USD", 30_000_000_000),
        num_row(AAPL_ADSH_K, "LongTermDebtNoncurrent", "20250927", 0, "USD", 85_750_000_000),
        num_row(AAPL_ADSH_K, "CommonStockSharesOutstanding", "20250927", 0, "shares", 14_800_000_000),
        # The same share count quoted in USD is not a share count.
        num_row(AAPL_ADSH_Q, "CommonStockSharesOutstanding", "20260627", 0, "USD", 999),
        num_row(AAPL_ADSH_K, "CommonStockDividendsPerShareDeclared", "20250927", 4, "USD/shares", 1.01),
        # A foreign-currency amount is not a USD amount.
        num_row(AAPL_ADSH_K, "Revenues", "20250630", 4, "EUR", 123),
        # Blank, unparseable and undurated rows.
        num_row(AAPL_ADSH_K, "Assets", "20250927", 0, "USD", None),
        num_row(AAPL_ADSH_K, "Assets", "20250927", 0, "USD", "not-a-number"),
        num_row(AAPL_ADSH_K, "Assets", "20250927", None, "USD", 123),
        # --- Microsoft: comma-formatted values -------------------------------
        num_row(MSFT_ADSH, "Revenues", "20250630", 4, "USD", "1,234.5"),
        num_row(MSFT_ADSH, "WeightedAverageNumberOfDilutedSharesOutstanding", "20250630", 4, "shares", "7,430"),
    ]


def tsv(header: list[str], rows: list[list[str]]) -> str:
    lines = ["\t".join(header)]
    lines.extend("\t".join(row) for row in rows)
    return "\n".join(lines) + "\n"


def write_fixture_zip(
    path: Path,
    *,
    sub_rows: list[list[str]] | None = None,
    num_rows: list[list[str]] | None = None,
    num_header: list[str] | None = None,
) -> Path:
    """A real ZIP holding ``sub.txt``/``num.txt`` — the only I/O these tests do."""

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("sub.txt", tsv(SUB_HEADER, sub_rows if sub_rows is not None else fixture_sub_rows()))
        archive.writestr(
            "num.txt",
            tsv(num_header or NUM_HEADER, num_rows if num_rows is not None else fixture_num_rows()),
        )
    return path


@pytest.fixture
def zip_path(tmp_path: Path) -> Path:
    return write_fixture_zip(tmp_path / "2026q3.zip")


@pytest.fixture
def index(zip_path: Path) -> FactIndex:
    return FactIndex.build(zip_path)


# ---------------------------------------------------------------------------
# Quarters and URLs
# ---------------------------------------------------------------------------


def test_parse_quarter_reads_the_edgar_spelling() -> None:
    assert bf.parse_quarter("2026q3") == (2026, 3)
    assert bf.parse_quarter(" 2026Q3 ") == (2026, 3)


@pytest.mark.parametrize("text", ["", "2026", "2026q", "2026q0", "2026q5", "26q3", "2026-q3", "2026q3.zip", None])
def test_parse_quarter_rejects_anything_else(text: object) -> None:
    with pytest.raises(ValueError):
        bf.parse_quarter(text)  # type: ignore[arg-type]


def test_quarter_label_round_trips() -> None:
    assert bf.quarter_label(2026, 3) == "2026q3"
    assert bf.parse_quarter(bf.quarter_label(2026, 1)) == (2026, 1)


@pytest.mark.parametrize("quarter", [0, 5, -1])
def test_quarter_label_rejects_a_quarter_that_does_not_exist(quarter: int) -> None:
    with pytest.raises(ValueError):
        bf.quarter_label(2026, quarter)


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        (date(2026, 10, 28), "2026q4"),
        (date(2026, 1, 1), "2026q1"),
        (date(2026, 3, 31), "2026q1"),
        (date(2026, 4, 1), "2026q2"),
        (date(2026, 12, 31), "2026q4"),
    ],
)
def test_current_quarter_is_the_quarter_containing_the_date(when: date, expected: str) -> None:
    assert bf.current_quarter(when) == expected


def test_dataset_url_points_at_the_edgar_quarter_file() -> None:
    assert bf.dataset_url("2026q3") == (
        "https://www.sec.gov/files/dera/data/financial-statement-data-sets/2026q3.zip"
    )


def test_dataset_url_rejects_a_bad_quarter() -> None:
    with pytest.raises(ValueError):
        bf.dataset_url("2026q9")


# ---------------------------------------------------------------------------
# Cache locations
# ---------------------------------------------------------------------------


def test_dataset_cache_path_lives_under_the_sec_bulk_subdirectory(tmp_path: Path) -> None:
    path = bf.dataset_cache_path(tmp_path, "2026q3")
    assert path == tmp_path / "sec-bulk" / "2026q3.zip"
    assert bf.dataset_cache_path(str(tmp_path), "2026q3") == path


def test_dataset_cache_path_follows_the_engine_cache_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEE_ENGINE_CACHE_DIR", str(tmp_path))
    assert bf.dataset_cache_path(None, "2026q3") == tmp_path / "sec-bulk" / "2026q3.zip"


def test_snapshot_path_is_the_quarterly_facts_document(tmp_path: Path) -> None:
    assert bf.snapshot_path(tmp_path, "2026q3") == tmp_path / "sec-bulk" / "2026q3-facts.json"


# ---------------------------------------------------------------------------
# CIK normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("320193", "0000320193"),
        (320193, "0000320193"),
        ("0000320193", "0000320193"),
        ("  320193  ", "0000320193"),
        ("CIK0000320193", "0000320193"),
        ("cik0000320193", "0000320193"),
        ("0", "0000000000"),
    ],
)
def test_normalize_cik_zero_pads_to_ten_digits(raw: object, expected: str) -> None:
    assert bf.normalize_cik(raw) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize("raw", ["", "   ", "abc", "32-0193", "12345678901", None, 3.5, True])
def test_normalize_cik_rejects_what_is_not_a_cik(raw: object) -> None:
    with pytest.raises(ValueError):
        bf.normalize_cik(raw)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# iter_submissions — streaming sub.txt
# ---------------------------------------------------------------------------


def test_iter_submissions_streams_the_filings(zip_path: Path) -> None:
    submissions = list(bf.iter_submissions(zip_path))

    assert [item.adsh for item in submissions] == [AAPL_ADSH_Q, AAPL_ADSH_K, MSFT_ADSH]
    assert all(isinstance(item, Submission) for item in submissions)
    apple = submissions[0]
    assert apple.cik == AAPL_CIK, "the CIK is normalised to ten digits"
    assert apple.name == "Apple Inc."
    assert apple.form == "10-Q"
    assert apple.period == "2026-06-30", "period is an ISO date, not YYYYMMDD"
    assert apple.filed == "2026-07-31"
    assert apple.fy == 2026
    assert apple.fp == "Q3"
    assert apple.sic == "3571"


def test_iter_submissions_skips_rows_without_an_accession_or_with_a_short_row(zip_path: Path) -> None:
    submissions = list(bf.iter_submissions(zip_path))
    assert len(submissions) == 3, "the blank row and the truncated row are not filings"


def test_iter_submissions_of_a_zip_without_sub_txt_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "empty.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("num.txt", tsv(NUM_HEADER, fixture_num_rows()))
    assert list(bf.iter_submissions(path)) == []


# ---------------------------------------------------------------------------
# iter_facts — streaming num.txt
# ---------------------------------------------------------------------------


def test_iter_facts_streams_the_rows(zip_path: Path) -> None:
    facts = list(bf.iter_facts(zip_path))

    assert facts, "the fixture produced no fact"
    assert all(isinstance(item, Fact) for item in facts)
    revenue = [f for f in facts if f.tag == "RevenueFromContractWithCustomerExcludingAssessedTax"]
    annual = [f for f in revenue if f.qtrs == 4 and not f.segments]
    assert len(annual) == 1
    assert annual[0].value == pytest.approx(416_161_000_000)
    assert annual[0].ddate == "2025-09-27", "ddate is an ISO date, not YYYYMMDD"
    assert annual[0].uom == "USD"
    assert annual[0].adsh == AAPL_ADSH_K
    assert annual[0].version == "us-gaap/2026"


def test_iter_facts_parses_a_comma_formatted_value(zip_path: Path) -> None:
    facts = [f for f in bf.iter_facts(zip_path) if f.adsh == MSFT_ADSH and f.tag == "Revenues"]
    assert len(facts) == 1
    assert facts[0].value == pytest.approx(1234.5)


def test_iter_facts_skips_a_row_whose_value_is_blank_or_not_a_number(zip_path: Path) -> None:
    assets = [f for f in bf.iter_facts(zip_path) if f.tag == "Assets"]
    # The blank row and the unparseable row are gone; the blank-qtrs row stays
    # (its value is fine — only its duration is missing), which is the rule
    # test_iter_facts_keeps_a_blank_qtrs_as_none pins down on its own.
    assert sorted(f.value for f in assets) == [
        pytest.approx(123.0),
        pytest.approx(364_980_000_000),
    ]


def test_iter_facts_keeps_a_row_with_no_unit_as_an_empty_unit(zip_path: Path) -> None:
    path = write_fixture_zip(
        zip_path.parent / "nounit.zip",
        num_rows=[num_row(AAPL_ADSH_K, "SomeTag", "20250927", 0, "", 5)],
    )
    facts = list(bf.iter_facts(path))
    assert len(facts) == 1
    assert facts[0].uom == "", "the raw unit is kept; rejecting it is the mapper's job"


def test_iter_facts_keeps_a_blank_qtrs_as_none(zip_path: Path) -> None:
    undurated = [f for f in bf.iter_facts(zip_path) if f.tag == "Assets" and f.qtrs is None]
    assert len(undurated) == 1
    assert undurated[0].value == pytest.approx(123)


def test_iter_facts_reads_columns_by_name_not_position(tmp_path: Path) -> None:
    """The 2026q3 layout is not the 2019 readme's; a reordered header still parses."""

    header = ["value", "uom", "qtrs", "ddate", "tag", "version", "adsh", "footnote", "segments", "coreg"]
    row = ["42", "USD", "0", "20250927", "Assets", "us-gaap/2026", AAPL_ADSH_K, "", "", ""]
    path = write_fixture_zip(tmp_path / "reordered.zip", num_header=header, num_rows=[row])
    facts = list(bf.iter_facts(path))
    assert len(facts) == 1
    assert facts[0].tag == "Assets"
    assert facts[0].value == pytest.approx(42)
    assert facts[0].ddate == "2025-09-27"


def test_iter_facts_of_a_zip_without_num_txt_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "empty.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("sub.txt", tsv(SUB_HEADER, fixture_sub_rows()))
    assert list(bf.iter_facts(path)) == []


# ---------------------------------------------------------------------------
# FactIndex — the CIK index
# ---------------------------------------------------------------------------


def test_build_indexes_facts_by_cik(index: FactIndex) -> None:
    assert index.ciks() == (AAPL_CIK, MSFT_CIK)
    apple = index.facts_for(AAPL_CIK)
    assert apple, "Apple has facts"
    assert {f.cik for f in apple} == {AAPL_CIK}
    microsoft = index.facts_for(MSFT_CIK)
    assert {f.tag for f in microsoft} == {
        "Revenues",
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    }


def test_build_joins_the_filing_date_from_sub_txt(index: FactIndex) -> None:
    """``filed`` lives in sub.txt; a fact without it cannot win a restatement."""

    restated = [
        f for f in index.facts_for(AAPL_CIK) if f.tag == "NetIncomeLoss" and f.value == pytest.approx(111_000_000_000)
    ]
    assert len(restated) == 1
    assert restated[0].filed == "2026-07-31"
    assert restated[0].adsh == AAPL_ADSH_Q


def test_facts_for_accepts_an_unpadded_cik_and_an_unknown_one(index: FactIndex) -> None:
    assert index.facts_for("320193") == index.facts_for(AAPL_CIK)
    assert index.facts_for("0000000001") == ()
    assert index.facts_for("not-a-cik") == ()


def test_submissions_for_returns_the_filings_of_one_issuer(index: FactIndex) -> None:
    submissions = index.submissions_for(AAPL_CIK)
    assert [s.adsh for s in submissions] == [AAPL_ADSH_Q, AAPL_ADSH_K]
    assert index.submissions_for(MSFT_CIK)[0].name == "Microsoft Corp"
    assert index.submissions_for("0000000001") == ()


def test_build_with_a_cik_filter_retains_only_what_was_asked_for(zip_path: Path) -> None:
    """The memory bound: a 4217-symbol universe must not index 30k filers."""

    index = FactIndex.build(zip_path, ciks=["320193"])
    assert index.ciks() == (AAPL_CIK,)
    assert index.facts_for(AAPL_CIK)
    assert index.facts_for(MSFT_CIK) == ()
    assert MSFT_CIK not in index.by_cik


def test_build_is_deterministic(zip_path: Path) -> None:
    first = FactIndex.build(zip_path)
    second = FactIndex.build(zip_path)
    assert first == second
    assert first.facts_for(AAPL_CIK) == second.facts_for(AAPL_CIK)


def test_facts_are_sorted_by_tag_then_date(index: FactIndex) -> None:
    keys = [(f.tag, f.ddate, f.qtrs if f.qtrs is not None else -1, f.uom, f.adsh, f.value) for f in index.facts_for(AAPL_CIK)]
    assert keys == sorted(keys)


def test_build_of_a_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises((FileNotFoundError, OSError)):
        FactIndex.build(tmp_path / "nope.zip")


# ---------------------------------------------------------------------------
# Snapshot round-trip — the monthly document
# ---------------------------------------------------------------------------


def test_snapshot_document_round_trips(index: FactIndex) -> None:
    document = index.to_document()
    assert set(document) == {"ciks"}
    assert set(document["ciks"]) == {AAPL_CIK, MSFT_CIK}

    restored = FactIndex.from_document(document)
    assert restored == index
    assert restored.facts_for(AAPL_CIK) == index.facts_for(AAPL_CIK)
    assert restored.submissions_for(AAPL_CIK) == index.submissions_for(AAPL_CIK)


def test_save_and_load_snapshot_round_trips_through_disk(index: FactIndex, tmp_path: Path) -> None:
    path = tmp_path / "snap" / "2026q3-facts.json"
    bf.save_snapshot(index, path)

    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["ciks"], "the document is plain JSON"

    loaded = bf.load_snapshot(path)
    assert loaded == index


def test_from_document_of_junk_is_an_empty_index() -> None:
    assert FactIndex.from_document({}) == FactIndex()
    assert FactIndex.from_document({"ciks": "nope"}) == FactIndex()


# ---------------------------------------------------------------------------
# download_dataset — one request per quarter, cached
# ---------------------------------------------------------------------------


def recording_client(content: bytes, *, status: int = 200, log: list[httpx2.Request] | None = None):
    def handle(request: httpx2.Request) -> httpx2.Response:
        if log is not None:
            log.append(request)
        return httpx2.Response(status, content=content)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handle))


@pytest.fixture
def zip_bytes(zip_path: Path) -> bytes:
    return zip_path.read_bytes()


async def test_download_writes_the_cache_entry_and_sends_a_contact_user_agent(
    tmp_path: Path, zip_bytes: bytes
) -> None:
    log: list[httpx2.Request] = []
    client = recording_client(zip_bytes, log=log)

    path = await bf.download_dataset("2026q3", cache_dir=tmp_path, client=client)

    assert path == tmp_path / "sec-bulk" / "2026q3.zip"
    assert path.read_bytes() == zip_bytes
    assert zipfile.is_zipfile(path)
    assert len(log) == 1
    assert str(log[0].url) == bf.dataset_url("2026q3")
    assert "contact" in log[0].headers["user-agent"].lower()
    assert log[0].headers["user-agent"] == bf.SEC_USER_AGENT
    # No temporary file is left behind.
    assert [p.name for p in path.parent.iterdir()] == ["2026q3.zip"]


async def test_a_warm_cache_answers_with_zero_requests(tmp_path: Path, zip_bytes: bytes) -> None:
    log: list[httpx2.Request] = []
    client = recording_client(zip_bytes, log=log)
    cache_dir = tmp_path / "cache"

    first = await bf.download_dataset("2026q3", cache_dir=cache_dir, client=client)
    second = await bf.download_dataset("2026q3", cache_dir=cache_dir, client=client)

    assert first == second
    assert len(log) == 1, "the second call read the cache and made no request"


async def test_a_downloaded_zip_is_usable(tmp_path: Path, zip_bytes: bytes) -> None:
    client = recording_client(zip_bytes)
    path = await bf.download_dataset("2026q3", cache_dir=tmp_path, client=client)
    index = FactIndex.build(path)
    assert index.ciks() == (AAPL_CIK, MSFT_CIK)


@pytest.mark.parametrize("status", [404, 429, 500, 503])
async def test_a_failed_status_raises_and_leaves_no_cache_entry(
    tmp_path: Path, zip_bytes: bytes, status: int
) -> None:
    client = recording_client(zip_bytes, status=status)
    with pytest.raises(bf.BulkDownloadError):
        await bf.download_dataset("2026q3", cache_dir=tmp_path, client=client)
    assert list((tmp_path / "sec-bulk").glob("*")) == [], "a failed download caches nothing"


async def test_a_body_that_is_not_a_zip_raises_and_leaves_no_cache_entry(tmp_path: Path) -> None:
    client = recording_client(b"<html>Access denied</html>")
    with pytest.raises(bf.BulkDownloadError):
        await bf.download_dataset("2026q3", cache_dir=tmp_path, client=client)
    assert list((tmp_path / "sec-bulk").glob("*")) == []


async def test_a_zip_without_the_statement_files_raises(tmp_path: Path) -> None:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.htm", "not the dataset")
    client = recording_client(buffer.getvalue())
    with pytest.raises(bf.BulkDownloadError):
        await bf.download_dataset("2026q3", cache_dir=tmp_path, client=client)
    assert list((tmp_path / "sec-bulk").glob("*")) == []


async def test_download_rejects_a_bad_quarter_before_any_request(tmp_path: Path, zip_bytes: bytes) -> None:
    log: list[httpx2.Request] = []
    client = recording_client(zip_bytes, log=log)
    with pytest.raises(ValueError):
        await bf.download_dataset("2026q9", cache_dir=tmp_path, client=client)
    assert log == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_downloads_and_reports_the_cache_entry(
    tmp_path: Path, zip_bytes: bytes, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    log: list[httpx2.Request] = []
    monkeypatch.setattr(provider_http, "build_client", lambda **kwargs: recording_client(zip_bytes, log=log))

    code = bf.main(["--quarter", "2026q3", "--cache-dir", str(tmp_path)])

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["quarter"] == "2026q3"
    assert report["cached"] is False
    assert report["bytes"] == len(zip_bytes)
    assert Path(report["path"]) == tmp_path / "sec-bulk" / "2026q3.zip"
    assert len(log) == 1


def test_main_of_a_warm_quarter_makes_no_request(
    tmp_path: Path, zip_bytes: bytes, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    log: list[httpx2.Request] = []
    monkeypatch.setattr(provider_http, "build_client", lambda **kwargs: recording_client(zip_bytes, log=log))

    assert bf.main(["--quarter", "2026q3", "--cache-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    assert bf.main(["--quarter", "2026q3", "--cache-dir", str(tmp_path)]) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["cached"] is True
    assert len(log) == 1


def test_main_rejects_a_bad_quarter(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        bf.main(["--quarter", "not-a-quarter", "--cache-dir", str(tmp_path)])


# ---------------------------------------------------------------------------
# The suite never leaves the host
# ---------------------------------------------------------------------------


def test_the_fixture_zip_is_the_only_io(zip_path: Path) -> None:
    """conftest.py blocks sockets; this test documents that the parsers are pure file I/O."""

    with zipfile.ZipFile(zip_path) as archive:
        assert set(archive.namelist()) == {"sub.txt", "num.txt"}
    assert FactIndex.build(zip_path).ciks() == (AAPL_CIK, MSFT_CIK)


# ---------------------------------------------------------------------------
# Merging quarters
# ---------------------------------------------------------------------------
#
# A mid-year filer's annual row lives in the quarter that carried its 10-K
# while its year-to-date pair lives in the quarter that carried its 10-Q, so
# the TTM arithmetic reads one fact list built over both datasets.


def _quarter_zip(
    tmp_path: Path,
    name: str,
    *,
    sub_rows: list[list[str]],
    num_rows: list[list[str]],
) -> Path:
    return write_fixture_zip(tmp_path / name, sub_rows=sub_rows, num_rows=num_rows)


def test_merge_unions_facts_from_two_quarters(tmp_path: Path) -> None:
    """The 10-K quarter and the 10-Q quarter answer as one issuer."""

    annual = _quarter_zip(
        tmp_path,
        "2025q4.zip",
        sub_rows=[sub_row(AAPL_ADSH_K, 320193, "Apple Inc.", "10-K", "20250927", 2025, "FY", "20251031")],
        num_rows=[num_row(AAPL_ADSH_K, "Revenues", "20250927", 4, "USD", 400_000_000_000)],
    )
    ytd = _quarter_zip(
        tmp_path,
        "2026q3.zip",
        sub_rows=[sub_row(AAPL_ADSH_Q, 320193, "Apple Inc.", "10-Q", "20260630", 2026, "Q3", "20260731")],
        num_rows=[
            num_row(AAPL_ADSH_Q, "Revenues", "20260627", 3, "USD", 300_000_000_000),
            num_row(AAPL_ADSH_Q, "Revenues", "20250628", 3, "USD", 280_000_000_000),
        ],
    )

    merged = FactIndex.build(annual).merge(FactIndex.build(ytd))
    facts = merged.facts_for(AAPL_CIK)
    assert len(facts) == 3, "both quarters' rows land in one fact list"
    assert {fact.qtrs for fact in facts} == {3, 4}
    assert merged.issuer_name(AAPL_CIK) == "Apple Inc."


def test_merge_is_indistinguishable_from_one_build_over_both_zips(tmp_path: Path) -> None:
    """Merging then sorting equals building over a single concatenated dataset."""

    first = _quarter_zip(
        tmp_path,
        "a.zip",
        sub_rows=[sub_row(AAPL_ADSH_K, 320193, "Apple Inc.", "10-K", "20250927", 2025, "FY", "20251031")],
        num_rows=[num_row(AAPL_ADSH_K, "Revenues", "20250927", 4, "USD", 400_000_000_000)],
    )
    second = _quarter_zip(
        tmp_path,
        "b.zip",
        sub_rows=[sub_row(AAPL_ADSH_Q, 320193, "Apple Inc.", "10-Q", "20260630", 2026, "Q3", "20260731")],
        num_rows=[num_row(AAPL_ADSH_Q, "Revenues", "20260627", 3, "USD", 300_000_000_000)],
    )

    merged = FactIndex.build(first).merge(FactIndex.build(second))
    assert merged.facts_for(AAPL_CIK) == tuple(
        sorted(merged.facts_for(AAPL_CIK), key=_fact_sort_key)
    ), "the merged list is in the canonical build order"


def test_merge_lets_the_later_quarter_win_an_accession_collision(tmp_path: Path) -> None:
    """One accession refiled in a later quarter keeps the later filing date."""

    early = _quarter_zip(
        tmp_path,
        "early.zip",
        sub_rows=[sub_row(AAPL_ADSH_K, 320193, "Apple Inc.", "10-K", "20250927", 2025, "FY", "20251031")],
        num_rows=[num_row(AAPL_ADSH_K, "Revenues", "20250927", 4, "USD", 400_000_000_000)],
    )
    late = _quarter_zip(
        tmp_path,
        "late.zip",
        sub_rows=[sub_row(AAPL_ADSH_K, 320193, "Apple Inc.", "10-K/A", "20250927", 2025, "FY", "20251201")],
        num_rows=[num_row(AAPL_ADSH_K, "Revenues", "20250927", 4, "USD", 401_000_000_000)],
    )

    merged = FactIndex.build(early).merge(FactIndex.build(late))
    assert merged.submissions[AAPL_ADSH_K].filed == "2025-12-01"
    assert merged.facts_for(AAPL_CIK)[-1].value == 401_000_000_000


def test_build_quarters_over_two_zips(tmp_path: Path) -> None:
    """The convenience entry point builds and merges in one call."""

    annual = _quarter_zip(
        tmp_path,
        "2025q4.zip",
        sub_rows=[sub_row(AAPL_ADSH_K, 320193, "Apple Inc.", "10-K", "20250927", 2025, "FY", "20251031")],
        num_rows=[num_row(AAPL_ADSH_K, "Revenues", "20250927", 4, "USD", 400_000_000_000)],
    )
    ytd = _quarter_zip(
        tmp_path,
        "2026q3.zip",
        sub_rows=[sub_row(AAPL_ADSH_Q, 320193, "Apple Inc.", "10-Q", "20260630", 2026, "Q3", "20260731")],
        num_rows=[num_row(AAPL_ADSH_Q, "Revenues", "20260627", 3, "USD", 300_000_000_000)],
    )

    index = build_quarters([annual, ytd])
    assert index.ciks() == (AAPL_CIK,)
    assert len(index.facts_for(AAPL_CIK)) == 2


def test_build_quarters_with_no_paths_is_an_empty_index() -> None:
    """A caller with nothing cached yet still gets a usable object."""

    index = build_quarters([])
    assert index.ciks() == ()
    assert index.facts_for(AAPL_CIK) == ()


# ---------------------------------------------------------------------------
# Concept filter
# ---------------------------------------------------------------------------
#
# The six-quarter universe index is ~3.9 GB when it carries every filed
# concept and gets the worker cgroup OOM-killed; the mapper reads ~30.


def test_build_keeps_only_the_requested_tags(tmp_path: Path) -> None:
    zip_path = write_fixture_zip(tmp_path / "q.zip")
    full = FactIndex.build(zip_path)
    filtered = FactIndex.build(zip_path, tags={"Revenues"})

    assert filtered.facts_for(AAPL_CIK), "the kept concept survives"
    assert all(fact.tag == "Revenues" for fact in filtered.facts_for(AAPL_CIK))
    assert len(filtered.facts_for(AAPL_CIK)) < len(full.facts_for(AAPL_CIK))


def test_build_without_a_tag_filter_keeps_everything(tmp_path: Path) -> None:
    zip_path = write_fixture_zip(tmp_path / "q.zip")
    assert FactIndex.build(zip_path, tags=None).facts_for(AAPL_CIK) == (
        FactIndex.build(zip_path).facts_for(AAPL_CIK)
    )


def test_build_quarters_forwards_the_tag_filter(tmp_path: Path) -> None:
    zip_path = write_fixture_zip(tmp_path / "q.zip")
    index = build_quarters([zip_path], tags={"NetIncomeLoss"})
    assert index.facts_for(AAPL_CIK)
    assert all(fact.tag == "NetIncomeLoss" for fact in index.facts_for(AAPL_CIK))


def test_a_filtered_index_answers_the_same_mapped_values(tmp_path: Path) -> None:
    """The filter is a memory optimisation, never a different answer."""

    from app.market.sec_fundamentals import TAG_MAP, fundamentals_for_cik

    zip_path = write_fixture_zip(tmp_path / "q.zip")
    needed = {tag for group in TAG_MAP.values() for tag in group}
    full = fundamentals_for_cik(FactIndex.build(zip_path), AAPL_CIK, price=250.0)
    filtered = fundamentals_for_cik(
        FactIndex.build(zip_path, tags=needed), AAPL_CIK, price=250.0
    )
    assert filtered == full
