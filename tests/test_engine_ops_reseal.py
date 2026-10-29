"""Contract for ``ops reseal`` and for a broken snapshot being audible.

A snapshot whose dataset is intact but whose digest is stale fails closed —
correct, but silent: `SecFundamentalsDepthSource` catches the error, logs at
INFO, returns nothing, and the store keeps serving seed values. The operator
sees a healthy service and three-day-old numbers.

Two rules follow, and these tests pin them:

* a stale digest can be repaired without rebuilding anything, by recomputing
  the digest with the store's own `_checksum` — and an implausible dataset is
  refused rather than blessed;
* the depth rungs say so at WARNING, naming the tool to run.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.engine.ingest import EngineDataset
from app.engine.security import SecurityMaster
from app.engine.ops import main as ops_main
from app.engine.ops import reseal_snapshot
from app.engine.store import EngineStore

FIXTURES = Path(__file__).parent / "fixtures"


def _dataset() -> EngineDataset:
    master = SecurityMaster.from_company_tickers(
        json.loads((FIXTURES / "engine_sec_company_tickers_sample.json").read_text()),
        as_of="2026-10-26",
    )
    return EngineDataset(
        fetched_at="2026-10-26T12:00:00+00:00",
        security_master=master,
        facts={"AAPL": {}},
        errors={},
    )


def _sealed(tmp_path: Path) -> Path:
    path = tmp_path / "snapshot.json"
    EngineStore(path).save(_dataset())
    EngineStore(path).load()  # sanity: the fixture is loadable
    return path


def _stale(path: Path) -> str:
    """Rewrite the digest to a wrong value, leaving the dataset untouched."""

    raw = json.loads(path.read_text())
    original = raw["checksum"]
    raw["checksum"] = "sha256:" + "0" * 64
    path.write_text(json.dumps(raw))
    return original


def test_a_stale_digest_is_repaired_and_loads_again(tmp_path: Path) -> None:
    path = _sealed(tmp_path)
    original = _stale(path)

    with pytest.raises(Exception):
        EngineStore(path).load()  # the failure the operator actually sees

    document = reseal_snapshot(path)

    assert document["checksum_before"] == "sha256:" + "0" * 64
    assert document["checksum_after"] == original
    assert document["verified"] is True
    assert EngineStore(path).load().fetched_at == "2026-10-26T12:00:00+00:00"


def test_a_healthy_snapshot_is_reported_unchanged(tmp_path: Path) -> None:
    path = _sealed(tmp_path)
    document = reseal_snapshot(path)
    assert document["checksum_before"] == document["checksum_after"]
    assert document["verified"] is True


def test_a_missing_dataset_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"schema_version": 1, "checksum": "sha256:x"}))
    with pytest.raises(ValueError, match="dataset"):
        reseal_snapshot(path)


def test_empty_securities_are_refused(tmp_path: Path) -> None:
    path = _sealed(tmp_path)
    raw = json.loads(path.read_text())
    raw["dataset"]["securities"] = []
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="securities"):
        reseal_snapshot(path)


def test_empty_facts_are_refused(tmp_path: Path) -> None:
    path = _sealed(tmp_path)
    raw = json.loads(path.read_text())
    raw["dataset"]["facts"] = {}
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="facts"):
        reseal_snapshot(path)


def test_the_cli_prints_the_document(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _sealed(tmp_path)
    _stale(path)

    code = ops_main(["reseal", "--snapshot", str(path)])

    assert code == 0
    document = json.loads(capsys.readouterr().out)
    assert document["verified"] is True
    assert document["path"] == str(path)


def test_the_cli_exits_2_on_a_bad_snapshot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text("{}")

    code = ops_main(["reseal", "--snapshot", str(path)])

    assert code == 2
    error = json.loads(capsys.readouterr().err)
    assert error["error"] == "invalid_request"
    assert "dataset" in error["errorMessage"]


def test_a_broken_snapshot_is_a_warning_naming_the_tool(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """INFO is how this stayed invisible for days; it has to be WARNING."""

    from app.market.sec_fundamentals_depth import SecFundamentalsDepthSource

    path = _sealed(tmp_path)
    _stale(path)

    with caplog.at_level(logging.WARNING):
        source = SecFundamentalsDepthSource(snapshot_path=str(path))
        source._resolve_ciks()  # first touch loads and verifies the snapshot

    messages = [record.getMessage() for record in caplog.records]
    assert any("reseal" in message for message in messages), messages
    assert source.build_error or source.cik_error, "the failure is recorded"
