from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.engine.ingest import EngineDataset
from app.engine.security import SecurityMaster
from app.main import _engine_lifespan
from app.market.quote import QuoteTick

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


async def test_lifespan_attaches_a_quote_snapshot_when_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_load(
        symbols: tuple[str, ...], *, cache_dir: Path | None = None
    ) -> EngineDataset:
        return _dataset()

    class FakeQuoteEngine:
        async def poll_many(self, symbols: tuple[str, ...]):
            assert symbols == ("AAPL",)
            return {
                "AAPL": QuoteTick(
                    symbol="AAPL",
                    last=110.0,
                    prev_close=100.0,
                    volume=1_000.0,
                    ts_exchange=datetime(2026, 10, 26, 14, 30, tzinfo=UTC),
                    ts_received=datetime(2026, 10, 26, 14, 30, 1, tzinfo=UTC),
                    source="yahoo",
                )
            }

    monkeypatch.setenv("MEE_ENGINE_SYMBOLS", "AAPL")
    monkeypatch.setenv("MEE_ENGINE_QUOTES", "1")
    monkeypatch.setattr("app.main.load_sec_dataset", fake_load)
    monkeypatch.setattr(
        "app.main.build_quote_engine", lambda settings: FakeQuoteEngine()
    )
    application = _app()
    application.state.runtime = SimpleNamespace(settings=object())

    async with _engine_lifespan(application):
        pass

    result = application.state.engine.screen(
        {
            "filters": [{"field": "price", "op": "gt", "value": 0}],
            "limit": 20,
            "offset": 0,
        }
    )
    assert result.total == 1
    assert result.rows[0]["price"].value == 110.0
    assert result.rows[0]["change"].value == pytest.approx(10.0)


async def test_lifespan_keeps_the_dataset_when_quotes_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_load(
        symbols: tuple[str, ...], *, cache_dir: Path | None = None
    ) -> EngineDataset:
        return _dataset()

    class BrokenQuoteEngine:
        async def poll_many(self, symbols: tuple[str, ...]):
            raise RuntimeError("quote provider unavailable")

    monkeypatch.setenv("MEE_ENGINE_SYMBOLS", "AAPL")
    monkeypatch.setenv("MEE_ENGINE_QUOTES", "1")
    monkeypatch.setattr("app.main.load_sec_dataset", fake_load)
    monkeypatch.setattr(
        "app.main.build_quote_engine", lambda settings: BrokenQuoteEngine()
    )
    application = _app()
    application.state.runtime = SimpleNamespace(settings=object())

    async with _engine_lifespan(application):
        pass

    assert application.state.engine_quote_error == (
        "RuntimeError: quote provider unavailable"
    )
    assert application.state.engine is not None
    assert application.state.engine.health()["rows"] is True
