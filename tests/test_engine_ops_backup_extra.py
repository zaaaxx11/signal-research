"""Contract for ``backup --extra``: extra files/dirs ride in a tar archive.

The snapshot keeps its own copy + sidecar because that is what
:func:`app.engine.ops.restore_snapshot` verifies and restores; the extras are
packed into ``engine-extras-<stamp>.tar.gz`` beside it and rotated with the
same ``--keep`` window. Every extra's sha256 is reported, and a path that does
not exist fails the whole command before any artifact is written — a backup
that silently drops a store is worse than no backup.

The tests drive the operator CLI (``main([...])``) because that is the surface
that owns the flag; no test below reaches the network or the wall clock.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.engine import ops
from app.engine.ops import main

#: A fixed operator clock: the backup name, and therefore the archive name,
#: must come from the injected clock and never from ``time.time()``.
NOW = datetime(2026, 10, 29, 3, 46, 0, tzinfo=UTC)

SNAPSHOT_BYTES = b'{"schema_version":1,"dataset":{"symbols":3}}'
DEPTH_BYTES = b'{"as_of":"2026-10-29T03:00:00+00:00","symbols":{"AAPL":{"pe":28.0}}}'


class _Clock:
    """A stand-in for ``datetime`` whose ``now`` the test advances."""

    current = NOW

    @classmethod
    def now(cls, tz: object = None) -> datetime:
        return cls.current


@pytest.fixture()
def inputs(tmp_path: Path) -> tuple[Path, Path]:
    """A snapshot and a depth store, byte-for-byte known."""

    snapshot = tmp_path / "engine-snapshot.json"
    snapshot.write_bytes(SNAPSHOT_BYTES)
    depth = tmp_path / "depth.json"
    depth.write_bytes(DEPTH_BYTES)
    return snapshot, depth


def _backup(
    capsys: pytest.CaptureFixture[str],
    snapshot: Path,
    dest: Path,
    *,
    extras: tuple[Path, ...] = (),
    keep: int = 7,
) -> tuple[int, dict, str]:
    """Run one CLI backup; return ``(exit_code, stdout_document, stderr)``."""

    argv = [
        "backup",
        "--snapshot",
        str(snapshot),
        "--dest",
        str(dest),
        "--keep",
        str(keep),
    ]
    for extra in extras:
        argv.extend(["--extra", str(extra)])
    code = main(argv)
    captured = capsys.readouterr()
    document = json.loads(captured.out) if captured.out.strip() else {}
    return code, document, captured.err


def test_an_extra_file_is_archived_and_its_sha256_is_reported(
    tmp_path: Path,
    inputs: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The tarball holds ``depth.json`` and the report carries its digest."""

    snapshot, depth = inputs
    dest = tmp_path / "backups"

    code, report, stderr = _backup(capsys, snapshot, dest, extras=(depth,))

    assert code == 0, stderr
    assert report["sha256"] == hashlib.sha256(SNAPSHOT_BYTES).hexdigest()
    items = report["extras"]
    assert isinstance(items, list) and len(items) == 1
    item = items[0]
    assert item["name"] == "depth.json"
    assert item["kind"] == "file"
    assert item["bytes"] == len(DEPTH_BYTES)
    assert item["sha256"] == hashlib.sha256(DEPTH_BYTES).hexdigest()

    archive = report["extras_archive"]
    path = Path(archive["path"])
    assert path.is_file()
    assert path.parent == dest
    assert archive["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert archive["bytes"] == path.stat().st_size

    with tarfile.open(path, "r:gz") as tar:
        names = tar.getnames()
        assert "depth.json" in names
        member = tar.extractfile("depth.json")
        assert member is not None
        assert member.read() == DEPTH_BYTES


def test_an_extra_directory_is_archived_with_its_tree(
    tmp_path: Path,
    inputs: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A directory extra is walked in sorted order and archived under its name."""

    snapshot, _ = inputs
    bundle = tmp_path / "bundle"
    (bundle / "sub").mkdir(parents=True)
    (bundle / "a.json").write_bytes(b'{"a":1}')
    (bundle / "sub" / "b.txt").write_bytes(b"bee")
    dest = tmp_path / "backups"

    code, report, stderr = _backup(capsys, snapshot, dest, extras=(bundle,))

    assert code == 0, stderr
    item = report["extras"][0]
    assert item["name"] == "bundle"
    assert item["kind"] == "dir"
    assert item["files"] == 2
    assert item["bytes"] == 7 + 3

    with tarfile.open(report["extras_archive"]["path"], "r:gz") as tar:
        assert sorted(tar.getnames()) == ["bundle/a.json", "bundle/sub/b.txt"]


def test_a_missing_extra_path_fails_the_command_before_any_artifact(
    tmp_path: Path,
    inputs: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A typo is a clear error naming the path — never a silent skip."""

    snapshot, _ = inputs
    missing = tmp_path / "no-such-depth.json"
    dest = tmp_path / "backups"
    dest.mkdir()

    code, report, stderr = _backup(capsys, snapshot, dest, extras=(missing,))

    assert code == 2
    assert report == {}
    payload = json.loads(stderr)
    assert payload["error"] == "invalid_request"
    assert str(missing) in payload["errorMessage"]
    assert "does not exist" in payload["errorMessage"]
    assert list(dest.iterdir()) == [], "a refused backup must write nothing"


def test_extra_is_repeatable_and_colliding_basenames_are_kept_apart(
    tmp_path: Path,
    inputs: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Two stores may share a basename; both must survive in the archive."""

    snapshot, _ = inputs
    first = tmp_path / "a" / "depth.json"
    second = tmp_path / "b" / "depth.json"
    for path, payload in ((first, b'{"a":1}'), (second, b'{"b":2}')):
        path.parent.mkdir(parents=True)
        path.write_bytes(payload)
    dest = tmp_path / "backups"

    code, report, stderr = _backup(
        capsys, snapshot, dest, extras=(first, second)
    )

    assert code == 0, stderr
    names = [item["name"] for item in report["extras"]]
    assert sorted(names) == ["depth-2.json", "depth.json"]
    digests = {item["name"]: item["sha256"] for item in report["extras"]}
    assert digests["depth.json"] == hashlib.sha256(b'{"a":1}').hexdigest()
    assert digests["depth-2.json"] == hashlib.sha256(b'{"b":2}').hexdigest()

    with tarfile.open(report["extras_archive"]["path"], "r:gz") as tar:
        assert sorted(tar.getnames()) == ["depth-2.json", "depth.json"]


def test_keep_rotates_extras_with_their_snapshots(
    tmp_path: Path,
    inputs: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--keep 2`` leaves two snapshots **and** two extras archives."""

    monkeypatch.setattr(ops, "datetime", _Clock)
    _Clock.current = NOW
    snapshot, depth = inputs
    dest = tmp_path / "backups"

    for _ in range(3):
        code, _, stderr = _backup(capsys, snapshot, dest, extras=(depth,), keep=2)
        assert code == 0, stderr
        _Clock.current = _Clock.current + timedelta(minutes=1)

    snapshots = sorted(path.name for path in dest.glob("engine-snapshot-*.json"))
    archives = sorted(path.name for path in dest.glob("engine-extras-*.tar.gz"))
    assert snapshots == [
        "engine-snapshot-20261029T034700Z.json",
        "engine-snapshot-20261029T034800Z.json",
    ]
    assert archives == [
        "engine-extras-20261029T034700Z.tar.gz",
        "engine-extras-20261029T034800Z.tar.gz",
    ]
