"""RED contract tests for the R7 operations surface (metrics, retention, backup).

These tests describe the behaviour promised by
``briefs/market-engine/R7-CONTRACT.md`` §1–§3 and are written before
``app/engine/ops.py`` is implemented: every helper below currently raises
``NotImplementedError`` and ``GET /engine/metrics`` is not mounted yet, so the
failures are behavioural rather than import errors.

Every test is deterministic without the wall clock: mtimes are written with
``os.utime``, ages are computed from an injected ``now``, and backup names come
from an injected clock. No network, no sleeps, no ``time.time()`` assertions.
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from app.engine.ingest import EngineDataset
from app.engine.license import LicensePolicy
from app.engine.model import Cell, Provenance, SecurityRecord, Unit
from app.engine.ops import (
    CacheEntry,
    apply_retention,
    backup_snapshot,
    cache_stats,
    dataset_metrics,
    main,
    plan_retention,
    restore_snapshot,
    scan_cache,
)
from app.engine.security import SecurityMaster
from app.engine.service import EngineService

#: The clock every age/backup assertion is computed against.
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)

#: Fixed mtimes for the six cache files, oldest first.
_FUND_MTIME = datetime(2026, 9, 20, tzinfo=UTC)
_FACTS_MTIME = datetime(2026, 9, 25, tzinfo=UTC)
_NPORT_MTIME = datetime(2026, 10, 1, tzinfo=UTC)
_INSIDER_MTIME = datetime(2026, 10, 10, tzinfo=UTC)
_SUBMISSIONS_MTIME = datetime(2026, 10, 20, tzinfo=UTC)
_OTHER_MTIME = datetime(2026, 10, 22, tzinfo=UTC)

#: filename → (bytes, mtime) for one complete cache directory.
_CACHE_FILES: dict[str, tuple[bytes, datetime]] = {
    "company_tickers_mf.json": (b'{"fund":"map"}', _FUND_MTIME),
    "CIK0000000001-companyfacts.json": (b'{"facts":"aapl"}', _FACTS_MTIME),
    "CIK0000000002-nport-0001.xml": (b"<nport>holdings</nport>", _NPORT_MTIME),
    "CIK0000000003-form4-0001.xml": (b"<form4/>", _INSIDER_MTIME),
    "CIK0000000004-submissions.json": (b'{"submissions":"msft"}', _SUBMISSIONS_MTIME),
    "unknown.txt": (b"operator notes", _OTHER_MTIME),
}

#: filename → the kind ``cache_stats`` must report for it.
_CACHE_KINDS: dict[str, str] = {
    "company_tickers_mf.json": "fund_series",
    "CIK0000000001-companyfacts.json": "facts",
    "CIK0000000002-nport-0001.xml": "nport",
    "CIK0000000003-form4-0001.xml": "insider",
    "CIK0000000004-submissions.json": "submissions",
    "unknown.txt": "other",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write(path: Path, data: bytes, *, mtime: datetime) -> Path:
    """Write ``data`` to ``path`` and pin its mtime, so no test reads the clock."""

    path.write_bytes(data)
    stamp = mtime.timestamp()
    os.utime(path, (stamp, stamp))
    return path


def _populated_cache(tmp_path: Path) -> Path:
    """A cache directory holding one file of every kind, with fixed mtimes."""

    cache_dir = tmp_path / "engine-cache"
    cache_dir.mkdir()
    for name, (data, mtime) in _CACHE_FILES.items():
        _write(cache_dir / name, data, mtime=mtime)
    return cache_dir


def _iso(value: object) -> datetime:
    """Parse one stats timestamp; the contract allows ``Z`` or ``+00:00``."""

    assert isinstance(value, str), f"expected an ISO-8601 string, got {value!r}"
    parsed = datetime.fromisoformat(value)
    assert parsed.utcoffset() == timedelta(0), f"{value!r} is not UTC"
    return parsed


def _kind_count(value: object) -> int:
    """The file count behind one ``by_kind`` value.

    The contract fixes the ``by_kind`` keys but not whether a value is a plain
    count or a ``{files, bytes}`` block, so the count invariant is asserted for
    both shapes instead of pinning an undefined one.
    """

    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, dict):
        files = value.get("files")
        assert isinstance(files, int) and not isinstance(files, bool), (
            f"a by_kind block must carry an integer 'files', got {value!r}"
        )
        return files
    raise AssertionError(f"unsupported by_kind value: {value!r}")


def _master(*symbols: str) -> SecurityMaster:
    """An identity master with one record per symbol and one provenance."""

    master = SecurityMaster()
    provenance = Provenance(source="sec", as_of="2026-10-26")
    for symbol in symbols:
        master.add(
            SecurityRecord(
                symbol=symbol,
                name=f"{symbol} Incorporated",
                cik=None,
                exchange="XNAS",
                mic="XNAS",
                asset_class="equity",
                currency="USD",
                country="US",
                sector=None,
                industry=None,
            ),
            provenance,
        )
    return master


def _cell(value: float) -> Cell:
    """One observed fact cell; two metrics per symbol make the count non-trivial."""

    return Cell(
        value=value,
        unit=Unit.USD,
        provenance=Provenance(source="sec", as_of="2025-09-27"),
    )


def _dataset(fetched_at: str = "2026-10-26T12:00:00+00:00") -> EngineDataset:
    """Three identities, facts for two symbols, one recorded failure."""

    return EngineDataset(
        fetched_at=fetched_at,
        security_master=_master("AAPL", "MSFT", "NVDA"),
        facts={
            "AAPL": {"sales": _cell(1.0), "assets": _cell(2.0)},
            "MSFT": {"sales": _cell(3.0)},
        },
        errors={"NVDA": "companyfacts could not be read"},
    )


def _entry(name: str, size: int, *, mtime: datetime, root: Path) -> CacheEntry:
    """One retention candidate, with an explicit size and age."""

    return CacheEntry(path=root / name, size=size, mtime=mtime.timestamp())


@asynccontextmanager
async def _api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[Any, Any]]:
    """Yield ``(application, client)`` over the ASGI app, with no sockets."""

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))

    from httpx2 import ASGITransport, AsyncClient

    from app.main import create_app

    application = create_app()
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://mee.test") as client:
        yield application, client


# ---------------------------------------------------------------------------
# 1. scan_cache / cache_stats
# ---------------------------------------------------------------------------


def test_scan_cache_lists_direct_files_with_their_size_and_mtime(
    tmp_path: Path,
) -> None:
    """Every direct file is one entry; a subdirectory is not a cache entry."""

    cache_dir = _populated_cache(tmp_path)
    nested = cache_dir / "nested"
    nested.mkdir()
    _write(nested / "CIK0000000099-companyfacts.json", b"{}", mtime=_FACTS_MTIME)

    entries = scan_cache(cache_dir)

    assert isinstance(entries, tuple), "scan_cache returns a tuple, per the contract"
    assert {entry.path.name for entry in entries} == set(_CACHE_FILES)
    assert all(isinstance(entry, CacheEntry) for entry in entries)
    for entry in entries:
        data, mtime = _CACHE_FILES[entry.path.name]
        assert entry.size == len(data)
        assert entry.mtime == pytest.approx(mtime.timestamp(), abs=1e-6)
        assert entry.path.parent == cache_dir


def test_scan_cache_answers_an_empty_tuple_for_a_missing_directory(
    tmp_path: Path,
) -> None:
    """A missing cache directory is "nothing cached", never an exception."""

    assert scan_cache(tmp_path / "absent") == ()


def test_cache_stats_totals_kinds_and_iso_extremes(tmp_path: Path) -> None:
    """Totals, per-kind counts and the oldest/newest mtimes are all stated."""

    cache_dir = _populated_cache(tmp_path)

    stats = cache_stats(cache_dir)

    assert stats["files"] == len(_CACHE_FILES)
    assert stats["bytes"] == sum(len(data) for data, _ in _CACHE_FILES.values())
    assert _iso(stats["oldest_mtime"]) == _FUND_MTIME
    assert _iso(stats["newest_mtime"]) == _OTHER_MTIME

    by_kind = stats["by_kind"]
    assert isinstance(by_kind, dict)
    assert set(by_kind) == set(_CACHE_KINDS.values())
    assert {kind: _kind_count(value) for kind, value in by_kind.items()} == {
        kind: 1 for kind in _CACHE_KINDS.values()
    }


def test_cache_stats_is_a_zero_block_for_a_missing_directory(tmp_path: Path) -> None:
    """No directory means zeros and null timestamps, not an error."""

    stats = cache_stats(tmp_path / "absent")

    assert stats["files"] == 0
    assert stats["bytes"] == 0
    assert stats["oldest_mtime"] is None
    assert stats["newest_mtime"] is None
    by_kind = stats["by_kind"]
    assert isinstance(by_kind, dict)
    assert sum(_kind_count(value) for value in by_kind.values()) == 0


def test_cache_stats_exposes_no_path_and_no_filename(tmp_path: Path) -> None:
    """The stats document is safe to serve: no directory, no basename, no keys."""

    cache_dir = _populated_cache(tmp_path)

    document = json.dumps(cache_stats(cache_dir), sort_keys=True)

    assert str(tmp_path) not in document
    assert str(cache_dir) not in document
    for name in _CACHE_FILES:
        assert name not in document
        assert Path(name).stem not in document
    assert '"path"' not in document


# ---------------------------------------------------------------------------
# 2. dataset_metrics
# ---------------------------------------------------------------------------


def test_dataset_metrics_without_a_dataset_is_explicitly_null() -> None:
    """An empty engine reports ``dataset: None`` plus the licence mode."""

    service = EngineService(policy=LicensePolicy(public_mode=True))

    assert dataset_metrics(service, now=NOW) == {
        "dataset": None,
        "public_mode": True,
    }


def test_dataset_metrics_counts_symbols_and_fact_symbols_with_an_exact_age() -> None:
    """``symbols`` counts identities, ``facts_symbols`` counts fact keys."""

    service = EngineService(
        dataset=_dataset(), policy=LicensePolicy(public_mode=True)
    )
    now = NOW + timedelta(minutes=5, seconds=30)

    document = dataset_metrics(service, now=now)

    assert document["public_mode"] is True
    block = document["dataset"]
    assert isinstance(block, dict)
    assert set(block) == {
        "symbols",
        "facts_symbols",
        "fetched_at",
        "age_seconds",
        "errors",
    }
    assert block["symbols"] == 3
    assert block["facts_symbols"] == 2, "two symbols carry facts; AAPL has two cells"
    assert block["fetched_at"] == "2026-10-26T12:00:00+00:00"
    assert block["age_seconds"] == 330
    assert block["errors"] == 1


def test_dataset_metrics_clamps_a_future_fetched_at_to_zero() -> None:
    """A clock skew must not publish a negative age."""

    service = EngineService(dataset=_dataset("2026-10-26T12:10:00+00:00"))

    block = dataset_metrics(service, now=NOW)["dataset"]

    assert isinstance(block, dict)
    assert block["age_seconds"] == 0
    assert block["fetched_at"] == "2026-10-26T12:10:00+00:00"


def test_dataset_metrics_reports_a_null_age_for_an_unparseable_fetched_at() -> None:
    """An unreadable timestamp is an honest ``None``, never a guessed age."""

    service = EngineService(dataset=_dataset("not-a-timestamp"))

    block = dataset_metrics(service, now=NOW)["dataset"]

    assert isinstance(block, dict)
    assert block["age_seconds"] is None
    assert block["fetched_at"] == "not-a-timestamp"


# ---------------------------------------------------------------------------
# 3. plan_retention / apply_retention
# ---------------------------------------------------------------------------


def test_plan_retention_deletes_a_file_older_than_max_age_days(tmp_path: Path) -> None:
    """The age rule runs first and reports a count, not a path list."""

    entries = (
        _entry(
            "CIK0000000001-companyfacts.json",
            120,
            mtime=NOW - timedelta(days=40),
            root=tmp_path,
        ),
        _entry(
            "CIK0000000002-nport-0001.xml",
            80,
            mtime=NOW - timedelta(days=1),
            root=tmp_path,
        ),
    )

    report = plan_retention(entries, max_age_days=30, now=NOW)

    assert set(report) == {
        "deleted",
        "freed_bytes",
        "kept_files",
        "kept_bytes",
        "protected_files",
        "dry_run",
    }
    assert report["deleted"] == 1
    assert report["freed_bytes"] == 120
    assert report["kept_files"] == 1
    assert report["kept_bytes"] == 80
    assert report["protected_files"] == 0
    assert report["dry_run"] is True
    assert str(tmp_path) not in json.dumps(report, default=str)


def test_plan_retention_deletes_oldest_first_until_max_bytes_fits(
    tmp_path: Path,
) -> None:
    """The size rule walks oldest → newest and stops as soon as it fits."""

    entries = (
        _entry(
            "CIK0000000001-companyfacts.json",
            400,
            mtime=NOW - timedelta(days=30),
            root=tmp_path,
        ),
        _entry(
            "CIK0000000002-nport-0001.xml",
            200,
            mtime=NOW - timedelta(days=20),
            root=tmp_path,
        ),
        _entry(
            "CIK0000000003-form4-0001.xml",
            100,
            mtime=NOW - timedelta(days=10),
            root=tmp_path,
        ),
    )

    report = plan_retention(entries, max_bytes=250, now=NOW)

    assert report["deleted"] == 2, "400 alone leaves 300 > 250, so 200 must go too"
    assert report["freed_bytes"] == 600
    assert report["kept_files"] == 1
    kept_bytes = report["kept_bytes"]
    assert isinstance(kept_bytes, int)
    assert kept_bytes == 100
    assert kept_bytes <= 250


def test_plan_retention_never_deletes_the_fund_map_or_unknown_files(
    tmp_path: Path,
) -> None:
    """The IVV map and every unknown file are protected, even when over budget."""

    entries = (
        _entry(
            "company_tickers_mf.json",
            500,
            mtime=NOW - timedelta(days=90),
            root=tmp_path,
        ),
        _entry(
            "unknown.txt",
            400,
            mtime=NOW - timedelta(days=80),
            root=tmp_path,
        ),
        _entry(
            "CIK0000000001-companyfacts.json",
            300,
            mtime=NOW - timedelta(days=1),
            root=tmp_path,
        ),
    )

    report = plan_retention(entries, max_bytes=0, max_age_days=0.0, now=NOW)

    assert report["deleted"] == 1, "only the CIK-prefixed eligible file may go"
    assert report["freed_bytes"] == 300
    assert report["protected_files"] == 2
    assert report["kept_files"] == 2
    assert report["kept_bytes"] == 900


def test_apply_retention_dry_run_reports_the_plan_and_deletes_nothing(
    tmp_path: Path,
) -> None:
    """A dry run is a plan: every file survives, including the doomed one."""

    cache_dir = tmp_path / "engine-cache"
    cache_dir.mkdir()
    fund_map = _write(
        cache_dir / "company_tickers_mf.json",
        b"m" * 40,
        mtime=NOW - timedelta(days=90),
    )
    facts = _write(
        cache_dir / "CIK0000000001-companyfacts.json",
        b"f" * 300,
        mtime=NOW - timedelta(days=40),
    )
    nport = _write(
        cache_dir / "CIK0000000002-nport-0001.xml",
        b"n" * 100,
        mtime=NOW - timedelta(days=1),
    )

    report = apply_retention(cache_dir, max_bytes=100, dry_run=True)

    assert report["dry_run"] is True
    assert report["deleted"] == 1
    assert report["freed_bytes"] == 300
    assert facts.exists(), "a dry run must not unlink anything"
    assert fund_map.exists()
    assert nport.exists()


def test_apply_retention_deletes_eligible_files_and_leaves_protected_ones(
    tmp_path: Path,
) -> None:
    """The real run unlinks the plan and keeps the fund map untouched."""

    cache_dir = tmp_path / "engine-cache"
    cache_dir.mkdir()
    fund_map = _write(
        cache_dir / "company_tickers_mf.json",
        b"m" * 40,
        mtime=NOW - timedelta(days=90),
    )
    facts = _write(
        cache_dir / "CIK0000000001-companyfacts.json",
        b"f" * 300,
        mtime=NOW - timedelta(days=40),
    )
    nport = _write(
        cache_dir / "CIK0000000002-nport-0001.xml",
        b"n" * 100,
        mtime=NOW - timedelta(days=1),
    )

    report = apply_retention(cache_dir, max_bytes=100)

    assert report["dry_run"] is False
    assert report["deleted"] == 1
    assert report["freed_bytes"] == 300
    assert not facts.exists(), "the oldest eligible file is over budget"
    assert nport.exists(), "the newest eligible file already fits"
    assert fund_map.exists(), "the fund map is never a retention candidate"


# ---------------------------------------------------------------------------
# 4. backup_snapshot / restore_snapshot
# ---------------------------------------------------------------------------


def test_backup_snapshot_names_by_the_injected_clock_and_copies_the_bytes(
    tmp_path: Path,
) -> None:
    """The name is derived from ``now`` and the copy is byte-identical."""

    snapshot = tmp_path / "snapshot.json"
    source = b'{"schema_version":1,"dataset":{"symbols":3}}'
    snapshot.write_bytes(source)
    dest = tmp_path / "offbox"
    dest.mkdir()
    now = datetime(2026, 10, 26, 14, 5, 9, tzinfo=UTC)

    report = backup_snapshot(snapshot, dest, keep=3, now=now)

    name = "engine-snapshot-20261026T140509Z.json"
    assert Path(str(report["backup"])).name == name
    backup = dest / name
    assert backup.is_file()
    assert backup.read_bytes() == source
    assert report["bytes"] == len(source)
    assert report["sha256"] == hashlib.sha256(source).hexdigest()
    assert report["kept"] == 1
    assert report["pruned"] == 0


def test_backup_snapshot_writes_a_sha256_sidecar_that_matches_the_source(
    tmp_path: Path,
) -> None:
    """The sidecar is ``<hex>  <name>`` over the bytes that were copied."""

    snapshot = tmp_path / "snapshot.json"
    source = b'{"schema_version":1,"dataset":{"symbols":0}}'
    snapshot.write_bytes(source)
    dest = tmp_path / "offbox"
    dest.mkdir()
    now = datetime(2026, 10, 26, 14, 5, 9, tzinfo=UTC)

    backup_snapshot(snapshot, dest, now=now)

    name = "engine-snapshot-20261026T140509Z.json"
    sidecar = dest / f"{name}.sha256"
    assert sidecar.is_file(), "a backup without its digest cannot be verified later"
    assert sidecar.read_text(encoding="utf-8").split() == [
        hashlib.sha256(source).hexdigest(),
        name,
    ]


def test_backup_snapshot_rotation_keeps_the_newest_n(tmp_path: Path) -> None:
    """``keep=2`` prunes the oldest snapshot and its report says so."""

    snapshot = tmp_path / "snapshot.json"
    snapshot.write_bytes(b'{"schema_version":1}')
    dest = tmp_path / "offbox"
    dest.mkdir()
    first = datetime(2026, 10, 26, 14, 0, 0, tzinfo=UTC)

    backup_snapshot(snapshot, dest, keep=2, now=first)
    backup_snapshot(snapshot, dest, keep=2, now=first + timedelta(minutes=1))
    report = backup_snapshot(
        snapshot, dest, keep=2, now=first + timedelta(minutes=2)
    )

    names = sorted(path.name for path in dest.glob("engine-snapshot-*.json"))
    assert names == [
        "engine-snapshot-20261026T140100Z.json",
        "engine-snapshot-20261026T140200Z.json",
    ]
    assert report["kept"] == 2
    assert report["pruned"] == 1


def test_restore_snapshot_verifies_the_sidecar_and_writes_the_target(
    tmp_path: Path,
) -> None:
    """The restored file is a byte-identical copy after a digest check."""

    snapshot = tmp_path / "snapshot.json"
    source = b'{"schema_version":1,"dataset":{"symbols":1}}'
    snapshot.write_bytes(source)
    dest = tmp_path / "offbox"
    dest.mkdir()
    backup_snapshot(snapshot, dest, now=NOW)
    backup = dest / "engine-snapshot-20261026T120000Z.json"
    target = tmp_path / "restored" / "snapshot.json"
    target.parent.mkdir()

    report = restore_snapshot(backup, target)

    assert isinstance(report, dict)
    assert target.read_bytes() == source


def test_restore_snapshot_rejects_a_tampered_sidecar_and_writes_nothing(
    tmp_path: Path,
) -> None:
    """A digest mismatch is ``ValueError``; an existing target is untouched."""

    snapshot = tmp_path / "snapshot.json"
    snapshot.write_bytes(b'{"schema_version":1,"dataset":{"symbols":2}}')
    dest = tmp_path / "offbox"
    dest.mkdir()
    backup_snapshot(snapshot, dest, now=NOW)
    backup = dest / "engine-snapshot-20261026T120000Z.json"
    sidecar = dest / "engine-snapshot-20261026T120000Z.json.sha256"
    sidecar.write_text(f"{'0' * 64}  {backup.name}\n", encoding="utf-8")

    missing_target = tmp_path / "restored.json"
    with pytest.raises(ValueError):
        restore_snapshot(backup, missing_target)
    assert not missing_target.exists(), "a failed restore must not create a file"

    existing_target = tmp_path / "live.json"
    existing_target.write_bytes(b"original")
    with pytest.raises(ValueError):
        restore_snapshot(backup, existing_target)
    assert existing_target.read_bytes() == b"original"


def test_restore_snapshot_requires_the_sidecar(tmp_path: Path) -> None:
    """No digest means no restore, even when the snapshot body is readable."""

    snapshot = tmp_path / "snapshot.json"
    snapshot.write_bytes(b'{"schema_version":1,"dataset":{"symbols":4}}')
    dest = tmp_path / "offbox"
    dest.mkdir()
    backup_snapshot(snapshot, dest, now=NOW)
    backup = dest / "engine-snapshot-20261026T120000Z.json"
    (dest / "engine-snapshot-20261026T120000Z.json.sha256").unlink()

    target = tmp_path / "restored.json"
    with pytest.raises(ValueError):
        restore_snapshot(backup, target)
    assert not target.exists()


# ---------------------------------------------------------------------------
# 5. GET /engine/metrics
# ---------------------------------------------------------------------------


async def test_engine_metrics_without_a_dataset_is_a_zero_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No dataset and no cache directory still answers 200 with zeros."""

    monkeypatch.delenv("MEE_ENGINE_CACHE_DIR", raising=False)
    monkeypatch.delenv("MEE_ENGINE_PUBLIC_MODE", raising=False)

    async with _api(tmp_path, monkeypatch) as (_application, client):
        response = await client.get("/engine/metrics")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"dataset", "cache", "public_mode", "startup_error", "process"}
    assert body["dataset"] is None
    assert body["public_mode"] is False
    assert body["startup_error"] is None

    cache = body["cache"]
    assert cache["files"] == 0
    assert cache["bytes"] == 0
    assert cache["oldest_mtime"] is None
    assert cache["newest_mtime"] is None
    assert sum(_kind_count(value) for value in cache["by_kind"].values()) == 0

    process = body["process"]
    assert set(process) == {"uptime_seconds"}
    uptime = process["uptime_seconds"]
    assert isinstance(uptime, int) and not isinstance(uptime, bool)
    assert uptime >= 0
    assert str(tmp_path) not in response.text


async def test_engine_metrics_describes_the_dataset_and_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loaded dataset and the cache block are reported without paths."""

    cache_dir = tmp_path / "engine-cache"
    cache_dir.mkdir()
    _write(
        cache_dir / "CIK0000000001-companyfacts.json",
        b'{"facts":"aapl"}',
        mtime=_FACTS_MTIME,
    )
    _write(
        cache_dir / "company_tickers_mf.json",
        b'{"fund":"map"}',
        mtime=_FUND_MTIME,
    )
    monkeypatch.setenv("MEE_ENGINE_CACHE_DIR", str(cache_dir))
    monkeypatch.delenv("MEE_ENGINE_PUBLIC_MODE", raising=False)
    service = EngineService(
        dataset=_dataset(), policy=LicensePolicy(public_mode=True)
    )

    async with _api(tmp_path, monkeypatch) as (application, client):
        application.state.engine = service
        response = await client.get("/engine/metrics")

    assert response.status_code == 200, response.text
    body = response.json()
    block = body["dataset"]
    assert isinstance(block, dict)
    assert block["symbols"] == 3
    assert block["facts_symbols"] == 2
    assert block["fetched_at"] == "2026-10-26T12:00:00+00:00"
    assert block["errors"] == 1
    assert isinstance(block["age_seconds"], int)
    assert block["age_seconds"] >= 0

    assert body["cache"]["files"] == 2
    assert body["cache"]["bytes"] == 14 + 16
    assert _iso(body["cache"]["oldest_mtime"]) == _FUND_MTIME
    assert body["public_mode"] is True
    assert str(tmp_path) not in response.text
    assert "CIK0000000001" not in response.text


async def test_engine_metrics_propagates_the_startup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed boot is visible in the metrics document, and still a 200."""

    monkeypatch.setenv("MEE_ENGINE_CACHE_DIR", str(tmp_path / "missing-cache"))

    async with _api(tmp_path, monkeypatch) as (application, client):
        application.state.engine_startup_error = "RuntimeError: EDGAR unavailable"
        response = await client.get("/engine/metrics")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["startup_error"] == "RuntimeError: EDGAR unavailable"
    assert body["dataset"] is None


# ---------------------------------------------------------------------------
# 6. CLI
# ---------------------------------------------------------------------------


def test_cli_retention_without_limits_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Neither limit is a usage error: exit 2 and one JSON error document."""

    cache_dir = tmp_path / "engine-cache"
    cache_dir.mkdir()

    exit_code = main(["retention", "--cache-dir", str(cache_dir)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out.strip() == ""
    payload = json.loads(captured.err)
    assert isinstance(payload, dict)
    assert "error" in payload and "errorMessage" in payload
    assert payload["error"]
    assert payload["errorMessage"]


def test_cli_cache_prints_one_json_document_and_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``cache`` is the operator's read-only view of the cache directory."""

    cache_dir = tmp_path / "engine-cache"
    cache_dir.mkdir()
    _write(
        cache_dir / "company_tickers_mf.json",
        b'{"fund":"map"}',
        mtime=_FUND_MTIME,
    )
    _write(
        cache_dir / "CIK0000000001-companyfacts.json",
        b'{"facts":"aapl"}',
        mtime=_FACTS_MTIME,
    )

    exit_code = main(["cache", "--cache-dir", str(cache_dir)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert isinstance(payload, dict)
    assert payload["files"] == 2
    assert payload["bytes"] == 14 + 16
