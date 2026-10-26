"""RED contract for portable paths and CLI defaults.

The engine must run from a laptop, a VPS or a container without editing a
command. The rule is one order: explicit env var → `MEE_DATA_DIR` → `./data`.
These tests pin that order and prove `ops` uses it when a flag is omitted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.engine import ops, paths


@pytest.fixture(autouse=True)
def no_location_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A clean location environment rooted at the test's cwd."""

    for name in (
        "MEE_DATA_DIR",
        "MEE_ENGINE_CACHE_DIR",
        "MEE_ENGINE_SNAPSHOT",
        "MEE_BACKUP_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def test_defaults_are_relative_to_the_working_directory() -> None:
    assert paths.data_dir() == Path.cwd() / "data"
    assert paths.cache_dir() == Path.cwd() / "data" / "cache"
    assert paths.snapshot_path() == Path.cwd() / "data" / "engine-snapshot.json"
    assert paths.backup_dir() == Path.cwd() / "data" / "backups"


def test_data_dir_moves_every_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "mnt" / "ztockz"
    monkeypatch.setenv("MEE_DATA_DIR", str(root))

    assert paths.data_dir() == root
    assert paths.cache_dir() == root / "cache"
    assert paths.snapshot_path() == root / "engine-snapshot.json"
    assert paths.backup_dir() == root / "backups"


def test_explicit_variables_win_over_the_data_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    cache = tmp_path / "elsewhere" / "cache"
    snapshot = tmp_path / "snap.json"
    backups = tmp_path / "backups"
    monkeypatch.setenv("MEE_DATA_DIR", str(root))
    monkeypatch.setenv("MEE_ENGINE_CACHE_DIR", str(cache))
    monkeypatch.setenv("MEE_ENGINE_SNAPSHOT", str(snapshot))
    monkeypatch.setenv("MEE_BACKUP_DIR", str(backups))

    assert paths.cache_dir() == cache
    assert paths.snapshot_path() == snapshot
    assert paths.backup_dir() == backups


def test_blank_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEE_DATA_DIR", "   ")
    assert paths.data_dir() == Path.cwd() / "data"


def test_cache_command_uses_the_env_directory_without_a_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = tmp_path / "env-cache"
    monkeypatch.setenv("MEE_ENGINE_CACHE_DIR", str(cache))
    seen: list[Path] = []

    def fake_cache_stats(path: Path) -> dict[str, object]:
        seen.append(path)
        return {"files": 0}

    monkeypatch.setattr(ops, "cache_stats", fake_cache_stats)
    assert ops.main(["cache"]) == 0
    assert seen == [cache]
    assert capsys.readouterr().out.strip() == '{"files": 0}'


def test_backup_command_uses_env_defaults_without_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot = tmp_path / "snap.json"
    dest = tmp_path / "backups"
    monkeypatch.setenv("MEE_ENGINE_SNAPSHOT", str(snapshot))
    monkeypatch.setenv("MEE_BACKUP_DIR", str(dest))
    seen: list[tuple[Path, Path, int]] = []

    def fake_backup(source: Path, target: Path, *, keep: int) -> dict[str, object]:
        seen.append((source, target, keep))
        return {"kept": 0}

    monkeypatch.setattr(ops, "backup_snapshot", fake_backup)
    assert ops.main(["backup", "--keep", "4"]) == 0
    assert seen == [(snapshot, dest, 4)]


def test_retention_uses_the_env_directory_without_a_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "env-cache"
    monkeypatch.setenv("MEE_ENGINE_CACHE_DIR", str(cache))
    seen: list[tuple[Path, int | None, float | None, bool]] = []

    def fake_apply(
        path: Path,
        *,
        max_bytes: int | None,
        max_age_days: float | None,
        dry_run: bool,
    ) -> dict[str, object]:
        seen.append((path, max_bytes, max_age_days, dry_run))
        return {"deleted": 0}

    monkeypatch.setattr(ops, "apply_retention", fake_apply)
    assert ops.main(["retention", "--max-age-days", "7", "--dry-run"]) == 0
    assert seen == [(cache, None, 7.0, True)]


def test_an_explicit_flag_still_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "env-cache"
    explicit = tmp_path / "explicit"
    monkeypatch.setenv("MEE_ENGINE_CACHE_DIR", str(cache))
    seen: list[Path] = []

    def fake_cache_stats(path: Path) -> dict[str, object]:
        seen.append(path)
        return {"files": 0}

    monkeypatch.setattr(ops, "cache_stats", fake_cache_stats)
    assert ops.main(["cache", "--cache-dir", str(explicit)]) == 0
    assert seen == [explicit]
