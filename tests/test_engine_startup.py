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


# -- R1 Task 4: persisted snapshot and startup precedence --------------------

import hashlib  # noqa: E402

from app.engine.model import Cell, SecurityRecord  # noqa: E402
from app.engine.store import EngineStore  # noqa: E402

SNAPSHOT_FETCHED_AT = "2026-10-26T12:00:00+00:00"


def _snapshot_security_row(record: SecurityRecord) -> dict[str, object]:
    """One on-disk security row for ``record``, per the persistence contract."""

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


def _snapshot_cell(cell: Cell) -> dict[str, object]:
    """One on-disk fact cell, including the whole provenance."""

    provenance = cell.provenance
    assert provenance is not None, "a fixture cell always carries provenance"
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


def _write_snapshot(path: Path, dataset: EngineDataset) -> Path:
    """Hand-write a valid snapshot so the arrange phase needs no ``EngineStore``."""

    body = {
        "fetched_at": dataset.fetched_at,
        "securities": [
            _snapshot_security_row(record)
            for record in dataset.security_master.records()
        ],
        "facts": {
            symbol: {
                metric: _snapshot_cell(cell) for metric, cell in cells.items()
            }
            for symbol, cells in dataset.facts.items()
        },
        "errors": dict(dataset.errors),
    }
    checksum = "sha256:" + hashlib.sha256(
        json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    path.write_text(
        json.dumps(
            {"schema_version": 1, "checksum": checksum, "dataset": body},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _configure_engine_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    snapshot: Path | None,
    symbols: str | None,
) -> None:
    """Set exactly the engine environment this test needs and clear the rest."""

    for name in (
        "MEE_ENGINE_SNAPSHOT",
        "MEE_ENGINE_SYMBOLS",
        "MEE_ENGINE_CACHE_DIR",
        "MEE_ENGINE_QUOTES",
        "MEE_ENGINE_PUBLIC_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    if snapshot is not None:
        monkeypatch.setenv("MEE_ENGINE_SNAPSHOT", str(snapshot))
    if symbols is not None:
        monkeypatch.setenv("MEE_ENGINE_SYMBOLS", symbols)


async def test_lifespan_prefers_a_snapshot_over_ingestion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An existing snapshot is loaded and EDGAR is never called."""

    path = _write_snapshot(tmp_path / "snapshot.json", _dataset())

    async def forbidden_load(
        symbols: tuple[str, ...], *, cache_dir: Path | None = None
    ) -> EngineDataset:
        raise AssertionError("ingestion must not run when a snapshot exists")

    _configure_engine_env(monkeypatch, snapshot=path, symbols="AAPL")
    monkeypatch.setattr("app.main.load_sec_dataset", forbidden_load)
    application = _app()

    async with _engine_lifespan(application):
        pass

    engine = getattr(application.state, "engine", None)
    assert engine is not None, "a readable snapshot must build the engine"
    assert engine.dataset is not None
    assert engine.dataset.security_master.symbols() == ("AAPL", "BRK-B", "MSFT", "NVDA")
    assert engine.dataset.fetched_at == SNAPSHOT_FETCHED_AT
    assert application.state.engine_snapshot == str(path)
    assert getattr(application.state, "engine_startup_error", None) is None


async def test_lifespan_fails_closed_on_a_corrupt_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A corrupt snapshot is a visible error, never a silent EDGAR fallback."""

    path = tmp_path / "snapshot.json"
    path.write_bytes(b"{not json at all")

    async def forbidden_load(
        symbols: tuple[str, ...], *, cache_dir: Path | None = None
    ) -> EngineDataset:
        raise AssertionError("ingestion must not run when a snapshot exists")

    _configure_engine_env(monkeypatch, snapshot=path, symbols="AAPL")
    monkeypatch.setattr("app.main.load_sec_dataset", forbidden_load)
    application = _app()

    async with _engine_lifespan(application):
        pass

    error = getattr(application.state, "engine_startup_error", None)
    assert isinstance(error, str), "a corrupt snapshot must be reported on state"
    assert error.startswith("SnapshotError: ")
    assert not getattr(getattr(application.state, "engine", None), "dataset", None)


async def test_lifespan_saves_an_ingested_dataset_to_the_snapshot_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ingestion with a snapshot path configured leaves a loadable snapshot."""

    calls: list[tuple[str, ...]] = []

    async def fake_load(
        symbols: tuple[str, ...], *, cache_dir: Path | None = None
    ) -> EngineDataset:
        calls.append(symbols)
        return _dataset()

    path = tmp_path / "snapshot.json"
    _configure_engine_env(monkeypatch, snapshot=path, symbols="AAPL")
    monkeypatch.setattr("app.main.load_sec_dataset", fake_load)
    application = _app()

    async with _engine_lifespan(application):
        pass

    assert calls == [("AAPL",)]
    assert path.exists(), "a configured snapshot path must be written after ingest"
    loaded = EngineStore(path).load()
    assert loaded.security_master.symbols() == ("AAPL", "BRK-B", "MSFT", "NVDA")
    assert getattr(application.state, "engine_snapshot_error", None) is None

    block = application.state.engine.health().get("dataset")
    assert block is not None, "health() must describe the dataset backing the rows"
    assert block.get("symbols") == 4
    assert block.get("errors") == 0

