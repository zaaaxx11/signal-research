"""Contract tests for the R3 coverage document and its CLI (contract §2).

The report is a *measurement* of one dataset, so every assertion here is an
exact count over a dataset this file builds itself: the four identities of the
cached SEC index, three of them enriched with a venue, two with a country and
one with a SIC industry, plus fact cells over two symbols. Nothing is fetched
and no clock is read — the two documents in this file are the only evidence.

``dataset_coverage`` and ``main`` ship as stubs before R3 lands, so these tests
are RED with ``NotImplementedError`` until BUILD-E implements them. That is the
intended failure class; an ``ImportError`` or a collection error is not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.engine import coverage
from app.engine.ingest import EngineDataset
from app.engine.model import Cell, Provenance, SecurityRecord, Unit
from app.engine.security import SecurityMaster
from app.engine.store import EngineStore

FIXTURES = Path(__file__).with_name("fixtures")
FETCHED_AT = "2026-10-26T12:00:00+00:00"
TICKERS_AS_OF = "2026-10-25"
FACT_AS_OF = "2026-06-30"
FACT_URL = (
    "https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/"
    "us-gaap/Revenues.json"
)

#: The six identity fields, frozen by the contract.
IDENTITY_KEYS = frozenset({"cik", "exchange", "mic", "industry", "country", "sector"})
#: The fact block's four counts.
FACT_KEYS = frozenset({"symbols", "cells", "provenance_cells", "pct_of_universe"})
#: The three gap notes and their exact wording.
NOTE_KEYS = frozenset({"sector", "global", "delisted"})
NOTES = {
    "sector": "SEC publishes SIC industry, not a sector taxonomy",
    "global": "no global identity source in engine v2 (SEC is US-listed)",
    "delisted": "the SEC index carries current registrants only",
}


def _provenance() -> Provenance:
    """The identity document's stamp; the report counts fields, not this."""

    return Provenance(source="sec", as_of=TICKERS_AS_OF)


def _record(
    symbol: str,
    name: str,
    *,
    exchange: str | None = None,
    mic: str | None = None,
    industry: str | None = None,
    country: str | None = None,
) -> SecurityRecord:
    """An enrichment row carrying only the fields R3's documents can fill.

    ``cik`` is left ``None`` on purpose: the fixture index already asserted it,
    and the master's merge fills rather than overwrites.
    """

    return SecurityRecord(
        symbol=symbol,
        name=name,
        cik=None,
        exchange=exchange,
        mic=mic,
        asset_class="equity",
        currency="USD",
        country=country,
        sector=None,
        industry=industry,
    )


def _master() -> SecurityMaster:
    """The four-symbol index fixture, enriched the way R3's ingest enriches it.

    ``NVDA`` is deliberately left bare and ``BRK-B`` gets no industry/country,
    so a report that defaulted a missing field instead of counting it would be
    wrong by more than a rounding error.
    """

    payload = json.loads(
        (FIXTURES / "engine_sec_company_tickers_sample.json").read_text()
    )
    master = SecurityMaster.from_company_tickers(payload, as_of=TICKERS_AS_OF)
    for record in (
        _record(
            "AAPL",
            "Apple Inc.",
            exchange="Nasdaq",
            mic="XNAS",
            industry="Electronic Computers",
            country="United States",
        ),
        _record(
            "MSFT",
            "MICROSOFT CORP",
            exchange="Nasdaq",
            mic="XNAS",
            country="United States",
        ),
        _record("BRK-B", "BERKSHIRE HATHAWAY INC", exchange="NYSE", mic="XNYS"),
    ):
        master.add(record, _provenance())
    return master


def _cell(value: float, unit: Unit = Unit.USD) -> Cell:
    """An observed SEC fact cell; a non-null value requires its provenance."""

    return Cell(
        value=value,
        unit=unit,
        provenance=Provenance(source="sec", as_of=FACT_AS_OF, url=FACT_URL),
    )


def _dataset() -> EngineDataset:
    """Four identities, three fact cells over two symbols, one empty symbol.

    ``MSFT`` maps to an empty cell mapping because its document was fetched and
    held no usable fact: that symbol must not be counted as a fact symbol.
    """

    return EngineDataset(
        fetched_at=FETCHED_AT,
        security_master=_master(),
        facts={
            "AAPL": {
                "sales": _cell(466_823_000_000.0),
                "net_income": _cell(112_010_000_000.0),
            },
            "BRK-B": {"eps": _cell(6.5, Unit.UNKNOWN)},
            "MSFT": {},
        },
        errors={},
    )


# ---------------------------------------------------------------------------
# Document shape
# ---------------------------------------------------------------------------


def test_dataset_coverage_returns_the_exact_contract_document() -> None:
    """Every key the contract freezes is present, typed and spelled as written."""

    document = coverage.dataset_coverage(_dataset())

    assert set(document) == {
        "total",
        "active",
        "identity",
        "identity_pct",
        "facts",
        "notes",
    }

    identity = document["identity"]
    percentages = document["identity_pct"]
    facts = document["facts"]
    notes = document["notes"]
    assert isinstance(identity, dict)
    assert isinstance(percentages, dict)
    assert isinstance(facts, dict)
    assert isinstance(notes, dict)

    assert set(identity) == IDENTITY_KEYS
    assert set(percentages) == IDENTITY_KEYS
    assert set(facts) == FACT_KEYS
    assert set(notes) == NOTE_KEYS
    assert notes == NOTES

    assert isinstance(document["total"], int)
    assert isinstance(document["active"], int)
    for field in IDENTITY_KEYS:
        assert isinstance(identity[field], int)
        assert isinstance(percentages[field], float)
    for field in ("symbols", "cells", "provenance_cells"):
        assert isinstance(facts[field], int)
    assert isinstance(facts["pct_of_universe"], float)


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------


def test_dataset_coverage_counts_only_populated_identity_fields() -> None:
    """Counts are row counts: a bare venue-less identity contributes nothing."""

    dataset = _dataset()
    symbols_before = dataset.security_master.symbols()

    document = coverage.dataset_coverage(dataset)

    assert document["total"] == 4
    assert document["active"] == document["total"]
    assert document["identity"] == {
        "cik": 4,
        "exchange": 3,
        "mic": 3,
        "industry": 1,
        "country": 2,
        "sector": 0,
    }
    assert document["identity_pct"] == {
        "cik": 100.0,
        "exchange": 75.0,
        "mic": 75.0,
        "industry": 25.0,
        "country": 50.0,
        "sector": 0.0,
    }

    # Purity: two calls agree and the dataset is left exactly as it was.
    assert coverage.dataset_coverage(dataset) == document
    assert dataset.security_master.symbols() == symbols_before


def test_dataset_coverage_counts_fact_cells_and_the_symbols_that_hold_them() -> None:
    """``symbols`` is "at least one cell", not "has a facts entry"."""

    document = coverage.dataset_coverage(_dataset())

    assert document["facts"] == {
        "symbols": 2,
        "cells": 3,
        "provenance_cells": 3,
        "pct_of_universe": 50.0,
    }


def test_dataset_coverage_reports_zeroes_for_an_empty_dataset() -> None:
    """An empty universe is 0.0%, not a ``ZeroDivisionError``."""

    dataset = EngineDataset(
        fetched_at=FETCHED_AT,
        security_master=SecurityMaster(),
        facts={},
        errors={},
    )

    document = coverage.dataset_coverage(dataset)

    assert document["total"] == 0
    assert document["active"] == 0
    assert document["identity"] == {field: 0 for field in IDENTITY_KEYS}
    assert document["identity_pct"] == {field: 0.0 for field in IDENTITY_KEYS}
    assert document["facts"] == {
        "symbols": 0,
        "cells": 0,
        "provenance_cells": 0,
        "pct_of_universe": 0.0,
    }
    assert document["notes"] == NOTES


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_prints_the_coverage_document_for_a_saved_snapshot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI reads the snapshot through the store and prints the same report."""

    dataset = _dataset()
    snapshot = EngineStore(tmp_path / "snapshot.json")
    snapshot.save(dataset)

    code = coverage.main(["--snapshot", str(snapshot.path)])
    captured = capsys.readouterr()

    assert code == 0
    document = json.loads(captured.out)
    assert document["total"] == 4
    assert document["active"] == 4
    assert document["facts"]["symbols"] == 2
    assert document["notes"] == NOTES
    assert document == coverage.dataset_coverage(dataset)


def test_cli_reports_a_missing_snapshot_as_json_with_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing file is a typed error document, never a traceback."""

    missing = tmp_path / "absent.json"

    code = coverage.main(["--snapshot", str(missing)])
    captured = capsys.readouterr()

    assert code == 2
    payload = json.loads(captured.err)
    assert payload["error"] == "snapshot_unreadable"
    assert isinstance(payload["errorMessage"], str)
    assert payload["errorMessage"].strip()
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_cli_reports_a_corrupt_snapshot_as_json_with_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Truncated JSON keeps the store's own failure code, not a generic one."""

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_bytes(b"{not json")

    code = coverage.main(["--snapshot", str(corrupt)])
    captured = capsys.readouterr()

    assert code == 2
    payload = json.loads(captured.err)
    assert payload["error"] == "snapshot_invalid"
    assert isinstance(payload["errorMessage"], str)
    assert payload["errorMessage"].strip()
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
