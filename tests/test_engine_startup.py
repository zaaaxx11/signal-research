from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.engine.ingest import EngineDataset
from app.engine.security import SecurityMaster
from app.main import _engine_lifespan

FIXTURES = Path(__file__).with_name("fixtures")


def _dataset() -> EngineDataset:
    master = SecurityMaster.from_company_tickers(
        json.loads((FIXTURES / "engine_sec_company_tickers_sample.json").read_text()),
        as_of="2026-10-26",
    )
    return EngineDataset(
        fetched_at="2026-10-26T12:00:00+00:00",
        security_master=master,
        facts={},
        errors={},
    )


def _app() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace())


async def test_lifespan_without_opt_in_does_not_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MEE_ENGINE_SYMBOLS", raising=False)
    application = _app()

    async with _engine_lifespan(application):
        pass

    assert not hasattr(application.state, "engine")
    assert not hasattr(application.state, "engine_startup_error")


async def test_lifespan_loads_configured_symbols_and_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[tuple[str, ...], Path | None]] = []

    async def fake_load(
        symbols: tuple[str, ...], *, cache_dir: Path | None = None
    ) -> EngineDataset:
        calls.append((symbols, cache_dir))
        return _dataset()

    monkeypatch.setenv("MEE_ENGINE_SYMBOLS", "aapl, msft")
    monkeypatch.setenv("MEE_ENGINE_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr("app.main.load_sec_dataset", fake_load)
    application = _app()

    async with _engine_lifespan(application):
        pass

    assert calls == [(("AAPL", "MSFT"), tmp_path)]
    assert application.state.engine is not None
    assert application.state.engine.health()["security_master"] is True


async def test_lifespan_records_a_load_failure_without_failing_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_load(
        symbols: tuple[str, ...], *, cache_dir: Path | None = None
    ) -> EngineDataset:
        raise RuntimeError("EDGAR unavailable")

    monkeypatch.setenv("MEE_ENGINE_SYMBOLS", "AAPL")
    monkeypatch.setattr("app.main.load_sec_dataset", failing_load)
    application = _app()

    async with _engine_lifespan(application):
        pass

    assert application.state.engine_startup_error == "RuntimeError: EDGAR unavailable"
    assert not hasattr(application.state, "engine")
