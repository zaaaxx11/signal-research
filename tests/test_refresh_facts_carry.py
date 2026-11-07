"""Behaviour lock for ``refresh-facts --carry-enrichment-from``.

The per-issuer ``submissions`` pass that supplies ``country``/``sector``/
``industry`` can be skipped or fail **without a hard error**, so a finished facts
crawl can quietly blank fields a previous snapshot already paid for. This suite
pins the small, honest repair:

* only blanks are filled, from the **same** symbol in an older snapshot;
* a value the crawl *did* state is never overwritten;
* the fact body is proven identical before and after the carry;
* with no flag, nothing is carried and the report keeps its original shape.

Everything here is ``tmp_path`` and a stubbed ``load_sec_dataset`` — no network
(the suite-wide guard in ``tests/conftest.py`` would fail any test that dials).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from app.engine.ingest import EngineDataset
from app.engine.model import Cell, Provenance, SecurityRecord, Unit
from app.engine.security import SecurityMaster
from app.engine.store import EngineStore


def _load_refresh_facts():
    """Import ``scripts/refresh-facts.py`` (a hyphenated name, not a module)."""

    path = Path(__file__).resolve().parents[1] / "scripts" / "refresh-facts.py"
    spec = importlib.util.spec_from_file_location("refresh_facts_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["refresh_facts_script"] = module
    spec.loader.exec_module(module)
    return module


refresh_facts = _load_refresh_facts()

FETCHED_AT = "2026-11-07T12:56:16+00:00"
PROV = Provenance(
    source="sec", as_of="2026-11-07", url="https://data.sec.gov/submissions"
)


def _record(symbol: str, **overrides: object) -> SecurityRecord:
    fields: dict[str, object] = {
        "symbol": symbol,
        "name": f"{symbol} Inc.",
        "cik": "0000000001",
        "exchange": "NASDAQ",
        "mic": "XNAS",
        "asset_class": "equity",
        "currency": "USD",
        "country": None,
        "sector": None,
        "industry": None,
    }
    fields.update(overrides)
    return SecurityRecord(**fields)  # type: ignore[arg-type]


def _cell() -> Cell:
    return Cell(
        value=1.0, unit=Unit.USD, provenance=Provenance(source="sec", as_of="2025-01-01")
    )


def _save(path: Path, records: list[SecurityRecord]) -> Path:
    """Write a small snapshot whose facts cover exactly ``records``' symbols."""

    master = SecurityMaster()
    for record in records:
        master.add(record, PROV)
    facts = {record.symbol: {"revenue": _cell()} for record in records}
    EngineStore(path).save(
        EngineDataset(
            fetched_at=FETCHED_AT,
            security_master=master,
            facts=facts,
            errors={},
        )
    )
    return path


def test_blank_fields_are_filled_from_prior_snapshot(tmp_path: Path) -> None:
    """(a) A blank country/sector/industry is carried from the older snapshot."""

    prior = _save(
        tmp_path / "prior.json",
        [_record("SYM", country="US", sector="Technology", industry="Software")],
    )
    fresh = _save(tmp_path / "fresh.json", [_record("SYM")])
    out = tmp_path / "carried.json"

    report = refresh_facts.carry_snapshot_enrichment(prior, fresh, out)

    assert report["symbols_carried"] == 1
    assert report["sample"] == ["SYM"]
    assert report["facts_identical"] is True
    assert report["verified"] is True
    record = EngineStore(out).load().security_master.get("SYM")
    assert record is not None
    assert record.country == "US"
    assert record.sector == "Technology"
    assert record.industry == "Software"


def test_existing_values_are_never_overwritten(tmp_path: Path) -> None:
    """(b) A field the crawl stated wins; only the blanks are carried."""

    prior = _save(
        tmp_path / "prior.json",
        [_record("SYM", country="US", sector="OldSector", industry="OldIndustry")],
    )
    fresh = _save(
        tmp_path / "fresh.json",
        # The fresh crawl knows the sector and disagrees with the old snapshot.
        [_record("SYM", sector="Technology")],
    )
    out = tmp_path / "carried.json"

    report = refresh_facts.carry_snapshot_enrichment(prior, fresh, out)

    record = EngineStore(out).load().security_master.get("SYM")
    assert record is not None
    assert record.sector == "Technology"  # stated by the crawl: kept, not clobbered
    assert record.country == "US"  # blank: carried
    assert record.industry == "OldIndustry"  # blank: carried
    assert report["symbols_carried"] == 1


def test_facts_are_identical_before_and_after(tmp_path: Path) -> None:
    """(c) Carrying enrichment leaves the fact body byte-for-byte untouched."""

    prior = _save(
        tmp_path / "prior.json",
        [_record("AAA", country="US"), _record("BBB", country="CA", industry="Banks")],
    )
    fresh = _save(tmp_path / "fresh.json", [_record("AAA"), _record("BBB")])
    before = dict(EngineStore(fresh).load().facts)
    out = tmp_path / "carried.json"

    report = refresh_facts.carry_snapshot_enrichment(prior, fresh, out)

    after = dict(EngineStore(out).load().facts)
    assert report["facts_identical"] is True
    assert after == before


def test_carry_enrichment_only_touches_the_three_fields(tmp_path: Path) -> None:
    """The helper fills exactly country/sector/industry; name/cik are untouched."""

    prior = _save(
        tmp_path / "prior.json",
        [_record("SYM", country="US", sector="Tech", industry="Software")],
    )
    fresh = _save(tmp_path / "fresh.json", [_record("SYM", cik="0000000009")])
    out = tmp_path / "carried.json"

    refresh_facts.carry_snapshot_enrichment(prior, fresh, out)

    record = EngineStore(out).load().security_master.get("SYM")
    assert record is not None
    assert record.cik == "0000000009"
    assert (record.country, record.sector, record.industry) == (
        "US",
        "Tech",
        "Software",
    )


async def _fake_load_sec_dataset(symbols, *, cache_dir):  # noqa: ANN001, ARG001
    """Stand in for the EDGAR pass: identity with blank enrichment, one fact each."""

    master = SecurityMaster()
    for symbol in symbols:
        master.add(_record(symbol), PROV)
    facts = {symbol: {"revenue": _cell()} for symbol in symbols}
    return EngineDataset(
        fetched_at=FETCHED_AT, security_master=master, facts=facts, errors={}
    )


def _run_crawl(monkeypatch, tmp_path, *, carry_from=None):
    monkeypatch.setattr(refresh_facts, "load_sec_dataset", _fake_load_sec_dataset)
    output = tmp_path / ("out-carry.json" if carry_from else "out-plain.json")
    code = refresh_facts.crawl(
        symbols=["AAA"],
        output=output,
        cache_dir=tmp_path,
        min_free_gb=0.0,
        chunk_size=1,
        carry_from=carry_from,
    )
    return code, output


def test_without_flag_there_is_no_carry(tmp_path: Path, monkeypatch, capsys) -> None:
    """(d) No flag → none of the carry keys appear and nothing is enriched."""

    args = refresh_facts.parse_args(["--source", str(tmp_path / "whatever.json")])
    assert args.carry_enrichment_from is None

    code, output = _run_crawl(monkeypatch, tmp_path)
    document = json.loads(capsys.readouterr().out)
    assert code == 0
    assert "enrichment_carried" not in document
    assert "enrichment_sample" not in document
    record = EngineStore(output).load().security_master.get("AAA")
    assert record is not None
    assert record.country is None  # nothing was carried


def test_crawl_reports_carry_when_flag_is_given(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """With the flag, the crawl carries, reports the count/sample and verifies."""

    prior = _save(tmp_path / "prior.json", [_record("AAA", country="US")])
    code, output = _run_crawl(monkeypatch, tmp_path, carry_from=prior)
    document = json.loads(capsys.readouterr().out)

    assert code == 0
    assert document["enrichment_carried"] == 1
    assert document["enrichment_sample"] == ["AAA"]
    assert document["verified"] is True
    record = EngineStore(output).load().security_master.get("AAA")
    assert record is not None
    assert record.country == "US"


def test_missing_carry_snapshot_writes_nothing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A carry source that cannot be read is a failure, never a silent skip."""

    monkeypatch.setattr(refresh_facts, "load_sec_dataset", _fake_load_sec_dataset)
    output = tmp_path / "never.json"
    code = refresh_facts.crawl(
        symbols=["AAA"],
        output=output,
        cache_dir=tmp_path,
        min_free_gb=0.0,
        chunk_size=1,
        carry_from=tmp_path / "does-not-exist.json",
    )

    document = json.loads(capsys.readouterr().out)
    assert code == 2
    assert document["verified"] is False
    assert not output.exists()
