"""Contract tests for ``GET /health`` and ``GET /health/deep`` (plan §3).

The old route answered ``{"status": "ok", "version": ...}`` without looking at
anything, so a process serving a ten-hour-old depth store, a snapshot whose
digest no longer verified, or a full disk still reported healthy. These tests
pin the replacement contract:

* ``/health`` reads the deployment's state (depth store, snapshot digest, the
  two cursors, free space, the screen database) and answers ``ok`` /
  ``degraded`` (HTTP 200) / ``failing`` (HTTP 503);
* ``/health`` is read-only and network-free — the suite-wide socket guard in
  ``tests/conftest.py`` already fails any test that opens a socket, and one test
  patches ``socket.connect`` itself to prove the route never even tries;
* ``/health/deep`` probes three upstreams with per-probe timeouts and a route
  budget, and a dead upstream is a reported ``ok: false``, never a 500.

Every store lives in ``tmp_path``. Nothing here reads or writes
``/home/ubuntu/ztockz-data``, and no probe reaches the network.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import time
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterator

import pytest
from httpx2 import ASGITransport, AsyncClient

from app.api import health as health_api
from app.config import Settings
from app.engine.ingest import EngineDataset
from app.engine.model import Cell, CellStatus, Provenance, Unit
from app.engine.security import SecurityMaster
from app.engine.store import EngineStore
from app.main import create_app

FETCHED_AT = "2026-10-29T00:00:00+00:00"
#: The five checks the contract names, in report order.
CHECK_NAMES = ("depth_store", "snapshot", "cursors", "disk", "screen_db")
#: 24 h cadence + 2 h tolerance, from the plan.
CADENCE_S = 86_400.0
SLACK_S = 7_200.0


# ---------------------------------------------------------------------------
# fixtures written into tmp_path
# ---------------------------------------------------------------------------


def _dataset(symbols: tuple[str, ...] = ("AAPL", "MSFT")) -> EngineDataset:
    """A minimal dataset the real store can serialise and verify."""

    payload = {
        str(index): {"cik_str": 320193 + index, "ticker": symbol, "title": symbol}
        for index, symbol in enumerate(symbols)
    }
    master = SecurityMaster.from_company_tickers(payload, as_of="2026-10-01")
    return EngineDataset(
        fetched_at=FETCHED_AT,
        security_master=master,
        facts={
            symbols[0]: {
                "sales": Cell(
                    value=1_000.0,
                    unit=Unit.USD,
                    provenance=Provenance(
                        source="sec", as_of="2025-09-27", status=CellStatus.OK
                    ),
                )
            }
        },
        errors={},
    )


def _write_snapshot(path: Path, *, symbols: tuple[str, ...] = ("AAPL", "MSFT")) -> Path:
    """Write a snapshot whose digest verifies, exactly as the engine would."""

    path.parent.mkdir(parents=True, exist_ok=True)
    return EngineStore(path).save(_dataset(symbols))


def _write_depth(path: Path, *, symbols: tuple[str, ...] = ("AAPL", "MSFT", "NVDA")) -> Path:
    """Write a depth store in the shape ``DepthStore.write`` produces."""

    payload = {
        "as_of": "2026-10-30T00:00:00+00:00",
        "count": len(symbols),
        "missing": [],
        "symbols": {symbol: {"pe": 20.0, "market_cap": 1.0} for symbol in symbols},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_cursors(depth_path: Path, *, enrich: Any = 783, news: Any = 283) -> None:
    """Write both cursors where the collector writes them (same ``with_suffix``)."""

    depth_path.with_suffix(".enrich-cursor.json").write_text(
        json.dumps({"cursor": enrich}), encoding="utf-8"
    )
    depth_path.with_suffix(".news-cursor.json").write_text(
        json.dumps({"cursor": news}), encoding="utf-8"
    )


def _write_screen_db(path: Path, *, screens: int = 3, table: bool = True) -> Path:
    """Create the screen database with ``screens`` rows (or without the table)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("DROP TABLE IF EXISTS screens")
        if table:
            connection.execute("CREATE TABLE screens (id TEXT PRIMARY KEY)")
            connection.executemany(
                "INSERT INTO screens (id) VALUES (?)",
                [(f"screen-{index}",) for index in range(screens)],
            )
        else:
            connection.execute("CREATE TABLE unrelated (id TEXT PRIMARY KEY)")
        connection.commit()
    return path


def _set_age(path: Path, age_s: float) -> None:
    """Backdate ``path``'s mtime, which is the age every check reads."""

    stamp = time.time() - age_s
    os.utime(path, (stamp, stamp))


class _FakeStore:
    """The two attributes of ``DepthStore`` a health check may read."""

    def __init__(self, symbols: int) -> None:
        self.symbols = symbols
        self.revision = 7

    def __len__(self) -> int:
        return self.symbols


class _FakeCollector:
    """``DepthCollector`` enough for the probe: ready, store, last_cycle."""

    def __init__(self, symbols: int = 3) -> None:
        self.store = _FakeStore(symbols)
        self.last_cycle: Any = None
        self.last_error: Any = None

    @property
    def ready(self) -> bool:
        return bool(self.store)


@pytest.fixture(autouse=True)
def _clean_verification_cache() -> Iterator[None]:
    """A cached digest verdict must never leak between tests."""

    health_api._clear_verification_cache()
    yield
    health_api._clear_verification_cache()


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    """Settings pointed entirely at ``tmp_path`` — never at the production box."""

    values: dict[str, Any] = {
        "db_path": tmp_path / "ztockz.db",
        "health_depth_store_path": tmp_path / "data" / "depth.json",
        "health_snapshot_path": tmp_path / "data" / "engine-snapshot.json",
        "health_data_dir": tmp_path / "data",
        "health_screen_db_path": tmp_path / "ztockz.db",
        "depth": False,
    }
    values.update(overrides)
    return Settings(**values)


@asynccontextmanager
async def _client(settings: Settings) -> AsyncIterator[tuple[AsyncClient, Any]]:
    """A client over the ASGI app, plus the app so a test can inject state."""

    application = create_app(settings)
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://health.test") as client:
        yield client, application


def _healthy(tmp_path: Path) -> Settings:
    """Every check inside its budget."""

    depth = _write_depth(tmp_path / "data" / "depth.json")
    _write_snapshot(tmp_path / "data" / "engine-snapshot.json")
    _write_cursors(depth)
    _write_screen_db(tmp_path / "ztockz.db")
    return _settings(tmp_path)


# ---------------------------------------------------------------------------
# the document and the status rules
# ---------------------------------------------------------------------------


async def test_health_reports_every_check_on_a_healthy_deployment(tmp_path: Path) -> None:
    async with _client(_healthy(tmp_path)) as (client, _app):
        response = await client.get("/health")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert tuple(body["checks"]) == CHECK_NAMES
    assert all(check["ok"] is True for check in body["checks"].values()), body["checks"]

    depth = body["checks"]["depth_store"]
    assert depth["status"] == "ok"
    assert depth["exists"] is True
    assert isinstance(depth["age_s"], int) and depth["age_s"] >= 0
    assert depth["budget_s"] == CADENCE_S + SLACK_S
    assert depth["symbols"] == 3

    snapshot = body["checks"]["snapshot"]
    assert snapshot["status"] == "ok"
    assert snapshot["checksum"] == "valid"
    assert isinstance(snapshot["age_s"], int)
    assert snapshot["budget_s"] == 172_800.0

    cursors = body["checks"]["cursors"]
    assert (cursors["enrich"], cursors["news"]) == (783, 283)

    assert body["checks"]["disk"]["free_gb"] > 0
    assert body["checks"]["screen_db"]["screens"] == 3


async def test_health_never_publishes_a_filesystem_path(tmp_path: Path) -> None:
    async with _client(_healthy(tmp_path)) as (client, _app):
        response = await client.get("/health")

    assert str(tmp_path) not in response.text


async def test_health_depth_budget_follows_the_configured_cadence(tmp_path: Path) -> None:
    settings = _healthy(tmp_path)
    settings = settings.model_copy(update={"depth_refresh_s": 3_600.0})
    async with _client(settings) as (client, _app):
        body = (await client.get("/health")).json()

    assert body["checks"]["depth_store"]["budget_s"] == 3_600.0 + SLACK_S


async def test_health_is_degraded_but_200_when_the_depth_store_is_over_budget(
    tmp_path: Path,
) -> None:
    settings = _healthy(tmp_path)
    depth = tmp_path / "data" / "depth.json"
    _set_age(depth, CADENCE_S + SLACK_S + 60)

    async with _client(settings) as (client, _app):
        response = await client.get("/health")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "degraded"
    check = body["checks"]["depth_store"]
    assert check["ok"] is False
    assert check["status"] == "stale"
    assert check["age_s"] > check["budget_s"]


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "[]",
        "{}",
        '{"symbols": []}',
        '{"symbols": {"AAPL": 5}}',
        "not json at all",
    ],
    ids=["empty-file", "json-array", "no-symbols", "symbols-list", "symbols-scalars", "not-json"],
)
async def test_health_fails_when_the_depth_store_is_not_a_depth_document(
    tmp_path: Path, payload: str
) -> None:
    settings = _healthy(tmp_path)
    (tmp_path / "data" / "depth.json").write_text(payload, encoding="utf-8")

    async with _client(settings) as (client, _app):
        response = await client.get("/health")

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["status"] == "failing"
    check = body["checks"]["depth_store"]
    assert check["ok"] is False
    assert check["status"] == "unparseable"
    assert check["error"]
    assert str(tmp_path) not in response.text


async def test_health_fails_when_the_depth_store_is_missing(tmp_path: Path) -> None:
    settings = _healthy(tmp_path)
    settings = settings.model_copy(update={"depth": True})
    (tmp_path / "data" / "depth.json").unlink()

    async with _client(settings) as (client, _app):
        response = await client.get("/health")

    assert response.status_code == 503, response.text
    check = response.json()["checks"]["depth_store"]
    assert check["ok"] is False
    assert check["status"] == "missing"
    assert check["exists"] is False


async def test_health_is_degraded_when_the_depth_store_is_empty_but_valid(
    tmp_path: Path,
) -> None:
    settings = _healthy(tmp_path)
    (tmp_path / "data" / "depth.json").write_text('{"symbols": {}}', encoding="utf-8")

    async with _client(settings) as (client, _app):
        response = await client.get("/health")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "degraded"
    check = response.json()["checks"]["depth_store"]
    assert (check["ok"], check["status"], check["symbols"]) == (False, "empty", 0)


async def test_health_is_ok_when_depth_is_switched_off_and_no_store_exists(
    tmp_path: Path,
) -> None:
    settings = _healthy(tmp_path)
    (tmp_path / "data" / "depth.json").unlink()

    async with _client(settings) as (client, _app):
        response = await client.get("/health")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"
    check = response.json()["checks"]["depth_store"]
    assert (check["ok"], check["status"], check["exists"]) == (True, "disabled", False)


async def test_health_is_degraded_when_the_running_collector_has_no_values(
    tmp_path: Path,
) -> None:
    """The store on disk is fine, but ``/market/depth`` would answer 503."""

    settings = _healthy(tmp_path)
    settings = settings.model_copy(update={"depth": True})
    async with _client(settings) as (client, app):
        app.state.depth = _FakeCollector(symbols=0)
        response = await client.get("/health")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "degraded"
    check = response.json()["checks"]["depth_store"]
    assert check["ok"] is False
    assert check["collector"] == "empty"


async def test_health_is_degraded_when_depth_is_enabled_but_no_collector_runs(
    tmp_path: Path,
) -> None:
    settings = _healthy(tmp_path)
    settings = settings.model_copy(update={"depth": True})

    async with _client(settings) as (client, _app):
        response = await client.get("/health")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["depth_store"]["collector"] == "missing"


async def test_health_reports_a_running_collector(tmp_path: Path) -> None:
    settings = _healthy(tmp_path)
    settings = settings.model_copy(update={"depth": True})
    async with _client(settings) as (client, app):
        collector = _FakeCollector(symbols=3)
        app.state.depth = collector
        response = await client.get("/health")

    check = response.json()["checks"]["depth_store"]
    assert check["collector"] == "running"
    assert check["revision"] == 7
    assert check["last_cycle"] is None
    assert check["ok"] is True


async def test_health_reports_the_last_cycle_accounting(tmp_path: Path) -> None:
    """``requested=4217, returned=900`` must be readable without journalctl."""

    from datetime import UTC, datetime
    from types import SimpleNamespace

    settings = _healthy(tmp_path)
    settings = settings.model_copy(update={"depth": True})
    async with _client(settings) as (client, app):
        collector = _FakeCollector(symbols=3)
        collector.last_cycle = SimpleNamespace(
            requested=4_217,
            returned=900,
            errors=12,
            seconds=2_400.5,
            started_at=datetime(2026, 10, 30, 2, 0, tzinfo=UTC),
        )
        app.state.depth = collector
        response = await client.get("/health")

    cycle = response.json()["checks"]["depth_store"]["last_cycle"]
    assert cycle["requested"] == 4_217
    assert cycle["returned"] == 900
    assert cycle["errors"] == 12
    assert cycle["seconds"] == 2_400.5
    assert cycle["started_at"].startswith("2026-10-30T02:00")


async def test_health_reports_a_crashed_check_instead_of_a_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bug in one check must degrade that check, not take the probe down."""

    def _boom(*args: object, **kwargs: object) -> health_api.Check:
        raise RuntimeError("disk usage probe exploded")

    monkeypatch.setattr(health_api, "_check_disk", _boom)

    async with _client(_healthy(tmp_path)) as (client, _app):
        response = await client.get("/health")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "degraded"
    check = body["checks"]["disk"]
    assert (check["ok"], check["status"]) == (False, "error")
    assert "disk usage probe exploded" in check["error"]
    assert body["checks"]["screen_db"]["ok"] is True


# ---------------------------------------------------------------------------
# snapshot: digest is fatal, age is not
# ---------------------------------------------------------------------------


async def test_health_is_degraded_but_200_when_the_snapshot_is_only_stale(
    tmp_path: Path,
) -> None:
    settings = _healthy(tmp_path)
    _set_age(tmp_path / "data" / "engine-snapshot.json", 3 * 86_400.0)

    async with _client(settings) as (client, _app):
        response = await client.get("/health")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "degraded"
    check = body["checks"]["snapshot"]
    assert (check["ok"], check["status"], check["checksum"]) == (False, "stale", "valid")


@pytest.mark.parametrize(
    ("mutate", "error_prefix"),
    [
        (lambda payload: payload["dataset"].__setitem__("fetched_at", "2026-10-30T00:00:00+00:00"), "snapshot_checksum"),
        (lambda payload: payload.pop("checksum"), "snapshot_checksum"),
        (lambda payload: payload.__setitem__("schema_version", 99), "snapshot_version"),
    ],
    ids=["tampered-dataset", "missing-digest", "wrong-version"],
)
async def test_health_fails_when_the_snapshot_cannot_be_verified(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], None], error_prefix: str
) -> None:
    settings = _healthy(tmp_path)
    snapshot = tmp_path / "data" / "engine-snapshot.json"
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    mutate(payload)
    snapshot.write_text(json.dumps(payload), encoding="utf-8")

    async with _client(settings) as (client, _app):
        response = await client.get("/health")

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["status"] == "failing"
    check = body["checks"]["snapshot"]
    assert check["ok"] is False
    assert check["checksum"] == "invalid"
    assert check["error"].startswith(error_prefix)


async def test_health_fails_when_the_snapshot_is_truncated(tmp_path: Path) -> None:
    settings = _healthy(tmp_path)
    snapshot = tmp_path / "data" / "engine-snapshot.json"
    snapshot.write_text(snapshot.read_text(encoding="utf-8")[:200], encoding="utf-8")

    async with _client(settings) as (client, _app):
        response = await client.get("/health")

    assert response.status_code == 503, response.text
    check = response.json()["checks"]["snapshot"]
    assert (check["ok"], check["checksum"]) == (False, "invalid")
    assert check["error"].startswith("snapshot_invalid")


async def test_health_fails_when_a_configured_snapshot_is_missing(tmp_path: Path) -> None:
    settings = _healthy(tmp_path)
    (tmp_path / "data" / "engine-snapshot.json").unlink()

    async with _client(settings) as (client, _app):
        response = await client.get("/health")

    assert response.status_code == 503, response.text
    check = response.json()["checks"]["snapshot"]
    assert (check["ok"], check["status"], check["checksum"]) == (False, "missing", "missing")


async def test_health_is_degraded_when_no_snapshot_is_configured_and_no_dataset_is_loaded(
    tmp_path: Path,
) -> None:
    settings = _healthy(tmp_path)
    settings = settings.model_copy(update={"health_snapshot_path": None})

    async with _client(settings) as (client, _app):
        # No MEE_ENGINE_SNAPSHOT in the environment and no lifespan ingest.
        response = await client.get("/health")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "degraded"
    check = body["checks"]["snapshot"]
    assert (check["ok"], check["status"]) == (False, "not_configured")


async def test_health_is_ok_when_the_engine_serves_an_ingested_dataset(
    tmp_path: Path,
) -> None:
    settings = _healthy(tmp_path)
    settings = settings.model_copy(update={"health_snapshot_path": None})

    from app.engine.service import EngineService

    async with _client(settings) as (client, app):
        app.state.engine = EngineService.from_dataset(_dataset())
        response = await client.get("/health")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["snapshot"]["status"] == "not_applicable"


async def test_health_is_degraded_when_startup_recorded_a_load_failure(
    tmp_path: Path,
) -> None:
    """A verifying file is not enough: the process may have failed to load it."""

    settings = _healthy(tmp_path)
    async with _client(settings) as (client, app):
        app.state.engine_startup_error = "SnapshotError: snapshot_checksum"
        response = await client.get("/health")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "degraded"
    assert body["startup_error"] == "SnapshotError: snapshot_checksum"


async def test_health_verifies_the_snapshot_once_per_file_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 23 MB digest cannot be recomputed per poll; the memo is keyed by file."""

    calls: list[str] = []

    class _CountingStore(EngineStore):
        def load(self) -> EngineDataset:
            calls.append(str(self.path))
            return super().load()

    monkeypatch.setattr(health_api, "EngineStore", _CountingStore)
    settings = _healthy(tmp_path)

    async with _client(settings) as (client, _app):
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/health")).status_code == 200
        assert len(calls) == 1

        # A rewrite changes inode and mtime, so the memo must not answer for it.
        _write_snapshot(tmp_path / "data" / "engine-snapshot.json", symbols=("AAPL",))
        assert (await client.get("/health")).status_code == 200
        assert len(calls) == 2


async def test_health_records_an_invalid_digest_even_after_a_valid_one(
    tmp_path: Path,
) -> None:
    """The opposite of a stale cache: a corrupt rewrite must flip the answer."""

    settings = _healthy(tmp_path)
    async with _client(settings) as (client, _app):
        assert (await client.get("/health")).status_code == 200
        snapshot = tmp_path / "data" / "engine-snapshot.json"
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        payload["dataset"]["errors"] = {"AAPL": "edited by hand"}
        snapshot.write_text(json.dumps(payload), encoding="utf-8")
        assert (await client.get("/health")).status_code == 503


# ---------------------------------------------------------------------------
# cursors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cursor", "status"),
    [({'cursor': "783"}, 200), ({"cursor": True}, 200), (["783"], 200), ({"cursor": 783.5}, 200)],
    ids=["string", "bool", "array", "float"],
)
async def test_health_is_degraded_when_a_cursor_is_not_an_integer(
    tmp_path: Path, cursor: Any, status: int
) -> None:
    settings = _healthy(tmp_path)
    depth = tmp_path / "data" / "depth.json"
    depth.with_suffix(".enrich-cursor.json").write_text(json.dumps(cursor), encoding="utf-8")

    async with _client(settings) as (client, _app):
        response = await client.get("/health")

    assert response.status_code == status, response.text
    body = response.json()
    assert body["status"] == "degraded"
    check = body["checks"]["cursors"]
    assert check["ok"] is False
    assert check["enrich"] is None
    assert check["news"] == 283
    assert check["error"] and "enrich" in check["error"]


async def test_health_is_degraded_when_a_cursor_file_is_missing(tmp_path: Path) -> None:
    settings = _healthy(tmp_path)
    (tmp_path / "data" / "depth.news-cursor.json").unlink()

    async with _client(settings) as (client, _app):
        response = await client.get("/health")

    assert response.status_code == 200, response.text
    check = response.json()["checks"]["cursors"]
    assert (check["ok"], check["enrich"], check["news"]) == (False, 783, None)
    assert "news" in check["error"]


# ---------------------------------------------------------------------------
# disk
# ---------------------------------------------------------------------------


async def test_health_is_degraded_when_free_space_is_below_the_floor(
    tmp_path: Path,
) -> None:
    settings = _healthy(tmp_path)
    settings = settings.model_copy(update={"health_min_free_gb": 1e9})

    async with _client(settings) as (client, _app):
        response = await client.get("/health")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "degraded"
    check = body["checks"]["disk"]
    assert (check["ok"], check["status"]) == (False, "low")
    assert check["free_gb"] < check["min_free_gb"]
    assert "below" in check["error"]


async def test_health_measures_the_nearest_existing_directory_when_data_dir_is_absent(
    tmp_path: Path,
) -> None:
    """A missing data directory must not raise; the volume is what matters."""

    settings = _healthy(tmp_path)
    settings = settings.model_copy(update={"health_data_dir": tmp_path / "nope" / "deeper"})

    async with _client(settings) as (client, _app):
        response = await client.get("/health")

    assert response.status_code == 200, response.text
    check = response.json()["checks"]["disk"]
    assert check["ok"] is True
    assert check["free_gb"] > 0


async def test_health_is_degraded_when_disk_usage_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    def _boom(_path: object) -> object:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(shutil, "disk_usage", _boom)

    async with _client(_healthy(tmp_path)) as (client, _app):
        response = await client.get("/health")

    assert response.status_code == 200, response.text
    check = response.json()["checks"]["disk"]
    assert (check["ok"], check["status"], check["free_gb"]) == (False, "unknown", None)
    assert check["error"]


# ---------------------------------------------------------------------------
# screen db
# ---------------------------------------------------------------------------


async def test_health_is_degraded_when_the_screen_table_is_missing(tmp_path: Path) -> None:
    settings = _healthy(tmp_path)
    _write_screen_db(tmp_path / "ztockz.db", table=False)

    async with _client(settings) as (client, _app):
        response = await client.get("/health")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "degraded"
    check = body["checks"]["screen_db"]
    assert (check["ok"], check["status"], check["screens"]) == (False, "no_table", None)
    assert "screens" in check["error"]


async def test_health_is_degraded_when_the_screen_database_is_missing(
    tmp_path: Path,
) -> None:
    settings = _healthy(tmp_path)
    (tmp_path / "ztockz.db").unlink()

    async with _client(settings) as (client, _app):
        response = await client.get("/health")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "degraded"
    check = body["checks"]["screen_db"]
    assert (check["ok"], check["status"], check["screens"]) == (False, "missing", None)


async def test_health_does_not_create_the_screen_database(tmp_path: Path) -> None:
    """/health is read-only: the probe must not migrate or create anything."""

    settings = _healthy(tmp_path)
    (tmp_path / "ztockz.db").unlink()

    async with _client(settings) as (client, _app):
        assert (await client.get("/health")).status_code == 200

    assert not (tmp_path / "ztockz.db").exists()


async def test_health_is_degraded_when_the_screen_database_is_locked(tmp_path: Path) -> None:
    settings = _healthy(tmp_path)
    settings = settings.model_copy(update={"health_screen_db_timeout_s": 0.1})
    database = tmp_path / "ztockz.db"
    writer = sqlite3.connect(database)
    try:
        writer.execute("PRAGMA journal_mode=DELETE")
        writer.execute("BEGIN EXCLUSIVE")
        started = time.perf_counter()
        async with _client(settings) as (client, _app):
            response = await client.get("/health")
        elapsed = time.perf_counter() - started
    finally:
        writer.rollback()
        writer.close()

    assert response.status_code == 200, response.text
    check = response.json()["checks"]["screen_db"]
    assert (check["ok"], check["status"]) == (False, "unreadable")
    assert "locked" in check["error"]
    assert elapsed < 3.0, "a locked database must not hold the probe open"


# ---------------------------------------------------------------------------
# read-only, network-free, fast
# ---------------------------------------------------------------------------


async def test_health_never_opens_a_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _refuse(self: socket.socket, *args: object, **kwargs: object) -> object:
        raise AssertionError("/health attempted an outbound connection")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)

    async with _client(_healthy(tmp_path)) as (client, _app):
        response = await client.get("/health")

    assert response.status_code == 200, response.text


async def test_health_stays_under_fifty_milliseconds_when_warm(tmp_path: Path) -> None:
    async with _client(_healthy(tmp_path)) as (client, _app):
        await client.get("/health")  # warm the digest/parse memos
        samples = []
        for _ in range(3):
            started = time.perf_counter()
            response = await client.get("/health")
            samples.append((time.perf_counter() - started) * 1_000.0)
            assert response.status_code == 200

    assert sorted(samples)[1] < 50.0, f"warm /health took {samples} ms"


async def test_health_and_health_deep_are_both_reachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_probes(monkeypatch)
    async with _client(_healthy(tmp_path)) as (client, _app):
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/health/deep")).status_code == 200


# ---------------------------------------------------------------------------
# /health/deep
# ---------------------------------------------------------------------------


def _stub_probes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cnbc: Callable[..., Any] | None = None,
    crumb: Callable[..., Any] | None = None,
    sec: Callable[..., Any] | None = None,
) -> None:
    """Replace every outbound probe: the deep route is never allowed a socket."""

    async def ok_cnbc(timeout_s: float, *, symbol: str = "AAPL") -> str:
        return f"{symbol} 261.40"

    async def ok_crumb(timeout_s: float) -> str:
        return "crumb of 12 chars"

    async def ok_sec(timeout_s: float, *, cik: str = "0000320193") -> str:
        return f"CompanyConcept for {cik}"

    monkeypatch.setattr(health_api, "_probe_cnbc", cnbc or ok_cnbc)
    monkeypatch.setattr(health_api, "_probe_yahoo_crumb", crumb or ok_crumb)
    monkeypatch.setattr(health_api, "_probe_sec", sec or ok_sec)


async def test_health_deep_reports_three_bounded_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_probes(monkeypatch)
    async with _client(_healthy(tmp_path)) as (client, _app):
        response = await client.get("/health/deep")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["budget_s"] == 10.0
    assert tuple(body["probes"]) == ("cnbc", "yahoo_crumb", "sec")
    for probe in body["probes"].values():
        assert probe["ok"] is True
        assert probe["error"] is None
        assert isinstance(probe["ms"], int)


async def test_health_deep_reports_a_failed_probe_without_a_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing_cnbc(timeout_s: float, *, symbol: str = "AAPL") -> str:
        raise RuntimeError("cnbc answered 503")

    _stub_probes(monkeypatch, cnbc=failing_cnbc)
    async with _client(_healthy(tmp_path)) as (client, _app):
        response = await client.get("/health/deep")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "degraded"
    probe = body["probes"]["cnbc"]
    assert probe["ok"] is False
    assert "cnbc answered 503" in probe["error"]
    assert body["probes"]["sec"]["ok"] is True


async def test_health_deep_bounds_each_probe_with_its_own_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    async def slow_cnbc(timeout_s: float, *, symbol: str = "AAPL") -> str:
        await asyncio.sleep(5.0)
        return "never"

    _stub_probes(monkeypatch, cnbc=slow_cnbc)
    settings = _healthy(tmp_path)
    settings = settings.model_copy(
        update={"health_probe_timeout_s": 0.05, "health_deep_budget_s": 5.0}
    )

    started = time.perf_counter()
    async with _client(settings) as (client, _app):
        response = await client.get("/health/deep")
    elapsed = time.perf_counter() - started

    assert response.status_code == 200, response.text
    probe = response.json()["probes"]["cnbc"]
    assert probe["ok"] is False
    assert "Timeout" in probe["error"]
    assert elapsed < 2.0, f"a per-probe timeout took {elapsed:.2f}s"


async def test_health_deep_bounds_the_whole_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a probe that ignores its own deadline cannot hold the route open."""

    import asyncio

    async def hung_sec(timeout_s: float, *, cik: str = "0000320193") -> str:
        await asyncio.sleep(5.0)
        return "never"

    _stub_probes(monkeypatch, sec=hung_sec)
    settings = _healthy(tmp_path)
    settings = settings.model_copy(
        update={"health_probe_timeout_s": 5.0, "health_deep_budget_s": 0.2}
    )

    started = time.perf_counter()
    async with _client(settings) as (client, _app):
        response = await client.get("/health/deep")
    elapsed = time.perf_counter() - started

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "degraded"
    assert body["probes"]["sec"]["ok"] is False
    assert "deadline" in body["probes"]["sec"]["error"]
    assert elapsed < 2.0, f"the route budget did not bound the call ({elapsed:.2f}s)"


async def test_health_deep_survives_every_probe_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(timeout_s: float, **kwargs: Any) -> str:
        raise OSError("network unreachable")

    _stub_probes(monkeypatch, cnbc=boom, crumb=boom, sec=boom)
    async with _client(_healthy(tmp_path)) as (client, _app):
        response = await client.get("/health/deep")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "degraded"
    assert all(probe["ok"] is False for probe in body["probes"].values())
    assert all("network unreachable" in probe["error"] for probe in body["probes"].values())


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


def test_health_settings_declare_the_plan_budgets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEE_ENGINE_SNAPSHOT", raising=False)
    monkeypatch.delenv("MEE_HEALTH_SNAPSHOT_PATH", raising=False)

    settings = Settings()

    assert settings.health_depth_store_path is None
    assert settings.health_depth_budget_slack_s == 7_200.0
    assert settings.health_snapshot_path is None
    assert settings.health_snapshot_budget_s == 172_800.0
    assert settings.health_min_free_gb == 2.0
    assert settings.health_screen_db_timeout_s == 0.5
    assert settings.health_deep_budget_s == 10.0
    assert settings.health_probe_timeout_s == 3.0


def test_health_snapshot_path_defaults_to_the_engine_snapshot_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One variable configures the process's snapshot and the probe's."""

    monkeypatch.delenv("MEE_HEALTH_SNAPSHOT_PATH", raising=False)
    monkeypatch.setenv("MEE_ENGINE_SNAPSHOT", "/srv/ztockz/engine-snapshot.json")

    assert Settings().health_snapshot_path == Path("/srv/ztockz/engine-snapshot.json")


def test_health_settings_read_their_own_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEE_HEALTH_SNAPSHOT_PATH", "/srv/other/snapshot.json")

    assert Settings().health_snapshot_path == Path("/srv/other/snapshot.json")
