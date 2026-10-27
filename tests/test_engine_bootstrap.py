"""RED contract for the portable bootstrap command.

A fresh host or container must be one command away from a runnable snapshot:

    python -m app.engine.bootstrap --symbols AAPL,MSFT

The tests pin the contract, not the ingestion internals: symbols are cleaned,
the loader receives the resolved cache dir, the snapshot is written atomically
through the same store the service uses, and failures are the house envelope.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json

import pytest

from app.engine import bootstrap
from app.engine.ingest import EngineDataset
from app.engine.security import SecurityMaster

PAYLOAD = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
}


def dataset() -> EngineDataset:
    """A minimal dataset the real store can serialise."""

    master = SecurityMaster.from_company_tickers(PAYLOAD, as_of="2026-01-01")
    return EngineDataset(
        fetched_at="2026-10-26T00:00:00+00:00",
        security_master=master,
        facts={},
        errors={"NOPE": "not in the SEC registrant index"},
    )


async def test_build_snapshot_cleans_symbols_and_writes_the_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[tuple[list[str], Path, str | None]] = []

    async def fake_load(
        symbols: list[str], *, cache_dir: Path, as_of: str | None, client: Any
    ) -> EngineDataset:
        seen.append((list(symbols), cache_dir, as_of))
        return dataset()

    monkeypatch.setattr(bootstrap, "load_sec_dataset", fake_load)
    output = tmp_path / "nested" / "engine-snapshot.json"
    report = await bootstrap.build_snapshot(
        [" aapl ", "", "MSFT"], output=output, cache_dir=tmp_path / "cache"
    )

    assert seen == [(["AAPL", "MSFT"], tmp_path / "cache", None)]
    assert output.is_file()
    assert report["output"] == str(output)
    assert report["symbols"] == 2
    assert report["facts"] == 0
    assert report["errors"] == 1
    assert report["error_symbols"] == ["NOPE"]


async def test_defaults_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot = tmp_path / "env" / "snap.json"
    cache = tmp_path / "env-cache"
    monkeypatch.setenv("MEE_ENGINE_SNAPSHOT", str(snapshot))
    monkeypatch.setenv("MEE_ENGINE_CACHE_DIR", str(cache))
    seen: list[Path] = []

    async def fake_load(symbols: list[str], *, cache_dir: Path, **kwargs: Any) -> EngineDataset:
        seen.append(cache_dir)
        return dataset()

    monkeypatch.setattr(bootstrap, "load_sec_dataset", fake_load)
    report = await bootstrap.build_snapshot(["AAPL"])

    assert seen == [cache]
    assert snapshot.is_file()
    assert report["output"] == str(snapshot)


async def test_empty_symbols_are_a_value_error() -> None:
    with pytest.raises(ValueError):
        await bootstrap.build_snapshot([" ", ""])


def test_cli_prints_the_report_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_build(symbols: list[str], **kwargs: Any) -> dict[str, object]:
        calls.append({"symbols": symbols, **kwargs})
        return {"output": "/data/snap.json", "symbols": 2}

    monkeypatch.setattr(bootstrap, "build_snapshot", fake_build)
    assert bootstrap.main(["--symbols", "aapl, msft", "--output", "/data/snap.json"]) == 0

    assert calls[0]["symbols"] == ["aapl", "msft"]
    assert json.loads(capsys.readouterr().out)["symbols"] == 2


def test_cli_failure_is_the_house_envelope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def broken(symbols: list[str], **kwargs: Any) -> dict[str, object]:
        raise ValueError("no symbols")

    monkeypatch.setattr(bootstrap, "build_snapshot", broken)
    assert bootstrap.main(["--symbols", "AAPL"]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["error"] == "bootstrap_failed"
    assert "ValueError" in error["errorMessage"]
