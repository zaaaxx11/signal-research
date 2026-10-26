"""Contract tests for the persisted dataset snapshot (R1 §3).

Everything here is filesystem-only: a snapshot is written into ``tmp_path`` and
read back, or a hand-built document is put on disk to prove which failure code a
corrupt file produces. No network, no clock, no EDGAR.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from app.engine.ingest import EngineDataset
from app.engine.model import Cell, CellStatus, Provenance, SecurityRecord, Unit
from app.engine.security import SecurityMaster
from app.engine.store import SCHEMA_VERSION, EngineStore, SnapshotError

FIXTURES = Path(__file__).with_name("fixtures")
FETCHED_AT = "2026-10-26T12:00:00+00:00"
CHECKSUM_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
SECURITY_KEYS = {
    "symbol",
    "name",
    "cik",
    "exchange",
    "mic",
    "asset_class",
    "currency",
    "country",
    "sector",
    "industry",
    "active",
}


def _master() -> SecurityMaster:
    """The fixture security master, observed at a fixed date."""

    payload = json.loads(
        (FIXTURES / "engine_sec_company_tickers_sample.json").read_text()
    )
    return SecurityMaster.from_company_tickers(payload, as_of="2026-09-27")


def _dataset() -> EngineDataset:
    """One dataset with a fixed timestamp, one fact and one recorded failure."""

    return EngineDataset(
        fetched_at=FETCHED_AT,
        security_master=_master(),
        facts={
            "AAPL": {
                "sales": Cell(
                    value=466_823_000_000.0,
                    unit=Unit.USD,
                    provenance=Provenance(
                        source="sec",
                        as_of="2025-09-27",
                        status=CellStatus.OK,
                        url="https://data.sec.gov/x",
                        inputs=("revenue", "prior"),
                    ),
                )
            }
        },
        errors={"MSFT": "timed out"},
    )


def _canonical_bytes(document: object) -> bytes:
    """The exact bytes the contract hashes for one document."""

    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _checksum(document: object) -> str:
    """``sha256:<hex>`` over the canonical form of ``document``."""

    return "sha256:" + hashlib.sha256(_canonical_bytes(document)).hexdigest()


def _security_document(record: SecurityRecord) -> dict[str, object]:
    """One on-disk security row for ``record``."""

    return {
        "symbol": record.symbol,
        "name": record.name,
        "cik": record.cik,
        "exchange": record.exchange,
        "mic": record.mic,
        "asset_class": record.asset_class,
        "currency": record.currency,
        "country": record.country,
        "sector": record.sector,
        "industry": record.industry,
        "active": record.active,
    }


def _cell_document(cell: Cell) -> dict[str, object]:
    """One on-disk fact cell: value, unit and the whole provenance."""

    provenance = cell.provenance
    assert provenance is not None, "the fixture cell always carries provenance"
    return {
        "value": cell.value,
        "unit": cell.unit.value,
        "provenance": {
            "source": provenance.source,
            "as_of": provenance.as_of,
            "status": provenance.status.value,
            "url": provenance.url,
            "inputs": list(provenance.inputs),
        },
    }


def _dataset_document(dataset: EngineDataset) -> dict[str, object]:
    """The contract's ``dataset`` object, built without ``EngineStore.save``."""

    return {
        "fetched_at": dataset.fetched_at,
        "securities": [
            _security_document(record) for record in dataset.security_master.records()
        ],
        "facts": {
            symbol: {
                metric: _cell_document(cell) for metric, cell in cells.items()
            }
            for symbol, cells in dataset.facts.items()
        },
        "errors": dict(dataset.errors),
    }


def _snapshot_document(
    dataset: EngineDataset,
    *,
    schema_version: object = SCHEMA_VERSION,
    checksum: str | None = None,
) -> dict[str, object]:
    """A complete snapshot envelope for ``dataset``, assembled by hand."""

    body = _dataset_document(dataset)
    return {
        "schema_version": schema_version,
        "checksum": _checksum(body) if checksum is None else checksum,
        "dataset": body,
    }


def _write_document(path: Path, document: object) -> Path:
    """Write ``document`` as plain JSON, with no store involved."""

    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def _failure(path: Path) -> SnapshotError:
    """Load ``path`` expecting a :class:`SnapshotError`, and return it."""

    store = EngineStore(path)
    with pytest.raises(SnapshotError) as excinfo:
        store.load()
    return excinfo.value


def test_snapshot_round_trip_restores_the_dataset(tmp_path: Path) -> None:
    """A saved dataset loads back with identity, facts, provenance and errors."""

    dataset = _dataset()
    path = tmp_path / "snapshot.json"
    store = EngineStore(path)

    written = store.save(dataset)

    assert Path(written) == path
    assert store.exists()
    loaded = EngineStore(path).load()
    assert loaded.fetched_at == dataset.fetched_at
    assert loaded.security_master.symbols() == dataset.security_master.symbols()
    assert loaded.security_master.symbols() == ("AAPL", "BRK-B", "MSFT", "NVDA")
    assert loaded.security_master.require("AAPL").name == "Apple Inc."
    assert loaded.security_master.require("AAPL").cik == "0000320193"

    cell = loaded.facts["AAPL"]["sales"]
    assert cell.value == 466_823_000_000.0
    assert cell.unit is Unit.USD
    provenance = cell.provenance
    assert provenance is not None
    assert provenance.source == "sec"
    assert provenance.as_of == "2025-09-27"
    assert provenance.status is CellStatus.OK
    assert provenance.url == "https://data.sec.gov/x"
    assert provenance.inputs == ("revenue", "prior")
    assert dict(loaded.errors) == {"MSFT": "timed out"}

    history = loaded.security_master.provenance("AAPL")
    assert any(
        entry.source == "snapshot" and entry.as_of == dataset.fetched_at
        for entry in history
    ), "a restored master is loaded under the snapshot provenance"


def test_snapshot_on_disk_is_versioned_canonical_json(tmp_path: Path) -> None:
    """The file is the contract's envelope and its checksum is recomputable."""

    dataset = _dataset()
    path = tmp_path / "snapshot.json"
    EngineStore(path).save(dataset)

    document = json.loads(path.read_text(encoding="utf-8"))

    assert set(document) == {"schema_version", "checksum", "dataset"}
    assert document["schema_version"] == 1
    assert document["schema_version"] == SCHEMA_VERSION
    stored_checksum = document["checksum"]
    assert isinstance(stored_checksum, str)
    assert CHECKSUM_PATTERN.fullmatch(stored_checksum) is not None
    body = document["dataset"]
    assert set(body) == {"fetched_at", "securities", "facts", "errors"}
    assert body["fetched_at"] == FETCHED_AT
    assert stored_checksum == _checksum(body)

    securities = body["securities"]
    assert isinstance(securities, list) and len(securities) == 4
    assert SECURITY_KEYS <= set(securities[0])
    fact = body["facts"]["AAPL"]["sales"]
    assert {"value", "unit", "provenance"} <= set(fact)
    assert {"source", "as_of", "status", "url", "inputs"} <= set(fact["provenance"])


def test_save_leaves_only_a_complete_target_file(tmp_path: Path) -> None:
    """A successful save publishes the target and leaves no temp file behind."""

    path = tmp_path / "snapshot.json"
    EngineStore(path).save(_dataset())

    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert text.startswith("{")
    assert [entry.name for entry in tmp_path.iterdir()] == [path.name]
    json.loads(text)  # a half-written document would not parse


def test_load_rejects_a_truncated_file(tmp_path: Path) -> None:
    """A truncated snapshot is invalid JSON, not a partially rebuilt dataset."""

    path = tmp_path / "snapshot.json"
    path.write_bytes(b'{"schema_version":1')

    assert _failure(path).code == "snapshot_invalid"


def test_load_rejects_a_foreign_schema_version(tmp_path: Path) -> None:
    """The version is checked before the checksum, so 99 never reaches the hash."""

    path = tmp_path / "snapshot.json"
    _write_document(
        path,
        _snapshot_document(
            _dataset(), schema_version=99, checksum="sha256:" + "0" * 64
        ),
    )

    assert _failure(path).code == "snapshot_version"


def test_load_rejects_a_checksum_mismatch(tmp_path: Path) -> None:
    """A well-shaped document whose hash does not match is refused."""

    path = tmp_path / "snapshot.json"
    _write_document(
        path, _snapshot_document(_dataset(), checksum="sha256:" + "0" * 64)
    )

    assert _failure(path).code == "snapshot_checksum"


def test_load_rejects_a_malformed_dataset_body(tmp_path: Path) -> None:
    """A matching checksum does not excuse a dataset with the wrong shape."""

    document = _snapshot_document(_dataset())
    body = document["dataset"]
    assert isinstance(body, dict)
    del body["securities"]
    document["checksum"] = _checksum(body)
    path = tmp_path / "snapshot.json"
    _write_document(path, document)

    assert _failure(path).code == "snapshot_invalid"


def test_load_reports_a_missing_snapshot_as_unreadable(tmp_path: Path) -> None:
    """A path that is not there cannot be loaded."""

    assert _failure(tmp_path / "absent.json").code == "snapshot_unreadable"


def test_exists_tracks_file_presence_only(tmp_path: Path) -> None:
    """``exists()`` is a readability check before and after a save."""

    path = tmp_path / "snapshot.json"
    store = EngineStore(path)

    assert store.exists() is False
    store.save(_dataset())
    assert store.exists() is True

    path.write_text("not a snapshot", encoding="utf-8")
    assert store.exists() is True
