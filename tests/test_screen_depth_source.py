"""Screen depth reader: one parse per file version, and never a raise.

``/screens`` may overlay the depth collector's 4,217-symbol store onto a
selection universe. That store is ~9 MB of JSON, rewritten atomically every ~13
minutes, so the reader the wiring calls has four contracts:

* **Read once per file version.** A parsed snapshot is cached against
  ``(path, mtime_ns, size)``; an unchanged file is never parsed twice and a
  rewritten file is read again on the next call. Parsing 9 MB per row, or per
  request, is the failure this pins.
* **At most one snapshot per process.** One store path at a time: a second path
  replaces the first, so a caller that invents paths cannot grow the process by
  a parsed copy apiece.
* **A failure is an empty picture, never an exception.** ``path=None``, a
  missing file, an unreadable path, invalid JSON, a payload that is not a JSON
  object and a symbol whose value is not an object all answer
  ``as_of=None, symbols={}``. A depth panel is an enrichment; it may not turn a
  selection into a 500.
* **A broken file is cached like a good one.** Re-parsing 9 MB of invalid JSON
  on every scan would turn one bad write into a latency incident; the empty
  answer is cached against the same fingerprint and invalidated by the next
  rewrite.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.config import Settings
from app.screen import depth_source

#: The stamp the collector writes (``app.market.depth`` persist step).
AS_OF = "2026-10-29T23:50:00.128054+00:00"
REWRITTEN_AS_OF = "2026-10-30T00:03:00.000000+00:00"


@pytest.fixture(autouse=True)
def _fresh_cache() -> Any:
    """Every test starts and ends with no parsed snapshot held by the process."""

    depth_source.reset_cache()
    yield
    depth_source.reset_cache()


def store(
    path: Path, *, as_of: str = AS_OF, symbols: dict[str, Any] | None = None
) -> Path:
    """Write a depth store shaped exactly like the collector's public JSON."""

    payload = {
        "as_of": as_of,
        "count": len(symbols or {}),
        "missing": [],
        "symbols": symbols or {},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def parse_spy(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Count how many times the module actually parses JSON."""

    real = json.loads
    calls: list[Any] = []

    def spy(raw: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(raw)
        return real(raw, *args, **kwargs)

    monkeypatch.setattr(depth_source.json, "loads", spy)
    return calls


def setting(settings: Settings, name: str) -> Any:
    """Read one setting, or fail RED when the field does not exist yet."""

    if name not in type(settings).model_fields:  # pragma: no cover - RED path
        raise NotImplementedError(f"Settings has no {name} field yet (RED)")
    return getattr(settings, name)


# ---------------------------------------------------------------------------
# Empty answers: every failure mode is the same empty snapshot
# ---------------------------------------------------------------------------


def test_missing_file_is_empty(tmp_path: Path) -> None:
    snapshot = depth_source.load_depth(tmp_path / "absent.json")

    assert snapshot.symbols == {}
    assert snapshot.as_of is None
    assert isinstance(snapshot.loaded_at, float)


def test_none_path_is_empty() -> None:
    snapshot = depth_source.load_depth(None)

    assert snapshot.symbols == {}
    assert snapshot.as_of is None


def test_directory_path_is_empty_and_does_not_raise(tmp_path: Path) -> None:
    """A path that cannot be read as a file is an empty picture, not a crash."""

    snapshot = depth_source.load_depth(tmp_path)

    assert snapshot.symbols == {}
    assert snapshot.as_of is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_unreadable_file_is_empty(tmp_path: Path) -> None:
    path = store(tmp_path / "depth.json", symbols={"AAPL": {"pe": 31.5}})
    path.chmod(0o000)
    try:
        snapshot = depth_source.load_depth(path)
    finally:
        path.chmod(0o644)

    assert snapshot.symbols == {}
    assert snapshot.as_of is None


def test_invalid_json_is_empty_and_does_not_raise(tmp_path: Path) -> None:
    path = tmp_path / "depth.json"
    path.write_text('{"as_of": "2026-10-29T23:50:00+00:00", "symbols": {', encoding="utf-8")

    snapshot = depth_source.load_depth(path)

    assert snapshot.symbols == {}
    assert snapshot.as_of is None


@pytest.mark.parametrize("payload", ["[]", '"AAPL"', "3", "null"])
def test_payload_that_is_not_an_object_is_empty(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "depth.json"
    path.write_text(payload, encoding="utf-8")

    snapshot = depth_source.load_depth(path)

    assert snapshot.symbols == {}
    assert snapshot.as_of is None


# ---------------------------------------------------------------------------
# A good file: symbols and as_of, nothing invented
# ---------------------------------------------------------------------------


def test_valid_store_parses_symbols_and_as_of(tmp_path: Path) -> None:
    path = store(
        tmp_path / "depth.json",
        symbols={
            "AAPL": {"pe": 31.5, "sector": "Technology"},
            "BRK-B": {"pe": 14.0},
        },
    )

    snapshot = depth_source.load_depth(path)

    assert snapshot.as_of == AS_OF
    assert dict(snapshot.symbols) == {
        "AAPL": {"pe": 31.5, "sector": "Technology"},
        "BRK-B": {"pe": 14.0},
    }
    assert snapshot.symbols["AAPL"]["pe"] == 31.5
    assert snapshot.loaded_at > 0


def test_symbol_values_that_are_not_objects_are_dropped(tmp_path: Path) -> None:
    path = store(
        tmp_path / "depth.json",
        symbols={
            "AAPL": {"pe": 31.5},
            "MSFT": {"pe": 35.0},
            "SCALAR": 3.0,
            "NULL": None,
            "LIST": [1, 2],
        },
    )

    snapshot = depth_source.load_depth(path)

    assert set(snapshot.symbols) == {"AAPL", "MSFT"}
    assert snapshot.symbols["MSFT"] == {"pe": 35.0}


def test_as_of_that_is_not_a_string_is_none(tmp_path: Path) -> None:
    path = store(tmp_path / "depth.json", as_of=17, symbols={"AAPL": {"pe": 31.5}})  # type: ignore[arg-type]

    snapshot = depth_source.load_depth(path)

    assert snapshot.as_of is None
    assert snapshot.symbols["AAPL"] == {"pe": 31.5}


def test_clock_dates_the_read(tmp_path: Path) -> None:
    path = store(tmp_path / "depth.json", symbols={"AAPL": {"pe": 31.5}})

    snapshot = depth_source.load_depth(path, clock=lambda: 1234.5)

    assert snapshot.loaded_at == 1234.5


# ---------------------------------------------------------------------------
# Caching: one parse per file version
# ---------------------------------------------------------------------------


def test_unchanged_file_is_parsed_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = store(tmp_path / "depth.json", symbols={"AAPL": {"pe": 31.5}})
    calls = parse_spy(monkeypatch)

    first = depth_source.load_depth(path)
    second = depth_source.load_depth(path)
    third = depth_source.load_depth(path)

    assert len(calls) == 1, "an unchanged store must not be parsed again"
    assert third.symbols == first.symbols
    assert third.as_of == second.as_of


def test_rewritten_file_is_read_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = store(tmp_path / "depth.json", symbols={"AAPL": {"pe": 31.5}})
    calls = parse_spy(monkeypatch)
    depth_source.load_depth(path)

    store(
        path,
        as_of=REWRITTEN_AS_OF,
        symbols={"AAPL": {"pe": 32.0}, "MSFT": {"pe": 35.0}},
    )
    # A same-tick rewrite must still be visible: the fingerprint is (mtime_ns,
    # size), not the second-resolution mtime some filesystems expose.
    current = path.stat()
    os.utime(path, ns=(current.st_atime_ns, current.st_mtime_ns + 1_000_000))

    snapshot = depth_source.load_depth(path)

    assert len(calls) == 2, "a rewritten store must be read again"
    assert snapshot.as_of == REWRITTEN_AS_OF
    assert snapshot.symbols["AAPL"]["pe"] == 32.0
    assert "MSFT" in snapshot.symbols


def test_malformed_file_is_not_reparsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "depth.json"
    path.write_text('{"symbols": {"AAPL": ', encoding="utf-8")
    calls = parse_spy(monkeypatch)

    first = depth_source.load_depth(path)
    second = depth_source.load_depth(path)
    third = depth_source.load_depth(path)

    assert first.symbols == {} and second.symbols == {}
    assert third.as_of is None
    assert len(calls) == 1, "a broken store must not be re-parsed on every call"


def test_malformed_file_is_reread_after_a_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "depth.json"
    path.write_text("{not json", encoding="utf-8")
    mono = parse_spy(monkeypatch)
    depth_source.load_depth(path)

    store(path, symbols={"AAPL": {"pe": 31.5}})
    current = path.stat()
    os.utime(path, ns=(current.st_atime_ns, current.st_mtime_ns + 1_000_000))

    snapshot = depth_source.load_depth(path)

    assert len(mono) == 2
    assert snapshot.symbols["AAPL"]["pe"] == 31.5


def test_ttl_serves_the_snapshot_without_touching_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = store(tmp_path / "depth.json", symbols={"AAPL": {"pe": 31.5}})
    calls = parse_spy(monkeypatch)
    now = [1_000.0]

    first = depth_source.load_depth(path, clock=lambda: now[0], ttl_s=60.0)
    store(path, as_of=REWRITTEN_AS_OF, symbols={"AAPL": {"pe": 99.0}})
    now[0] += 30.0
    second = depth_source.load_depth(path, clock=lambda: now[0], ttl_s=60.0)

    assert len(calls) == 1, "inside ttl_s the file must not even be stat-ed"
    assert second.symbols["AAPL"]["pe"] == first.symbols["AAPL"]["pe"]

    now[0] += 31.0  # past the window: the rewrite becomes visible
    third = depth_source.load_depth(path, clock=lambda: now[0], ttl_s=60.0)
    assert third.symbols["AAPL"]["pe"] == 99.0


def test_reset_cache_forces_the_next_call_to_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = store(tmp_path / "depth.json", symbols={"AAPL": {"pe": 31.5}})
    calls = parse_spy(monkeypatch)
    depth_source.load_depth(path)

    depth_source.reset_cache()
    depth_source.load_depth(path)

    assert len(calls) == 2


def test_a_second_path_replaces_the_cached_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At most one parsed snapshot per process: the last path wins."""

    first_path = store(tmp_path / "one.json", symbols={"AAPL": {"pe": 31.5}})
    second_path = store(tmp_path / "two.json", symbols={"MSFT": {"pe": 35.0}})
    calls = parse_spy(monkeypatch)

    depth_source.load_depth(first_path)
    depth_source.load_depth(second_path)

    assert len(calls) == 2
    assert set(depth_source.load_depth(second_path).symbols) == {"MSFT"}


# ---------------------------------------------------------------------------
# Path resolution and settings
# ---------------------------------------------------------------------------


def test_default_store_path_is_the_collectors_default() -> None:
    from app.market.depth import DEFAULT_STORE_PATH as COLLECTOR_DEFAULT

    assert depth_source.DEFAULT_STORE_PATH == COLLECTOR_DEFAULT


def test_resolve_store_path_prefers_the_screen_override(tmp_path: Path) -> None:
    settings = SimpleNamespace(
        screen_depth=True,
        screen_depth_store_path=str(tmp_path / "screen.json"),
        depth_store_path=tmp_path / "collector.json",
    )

    assert depth_source.resolve_store_path(settings) == tmp_path / "screen.json"


def test_resolve_store_path_falls_back_to_the_collectors_store(tmp_path: Path) -> None:
    settings = SimpleNamespace(
        screen_depth=True,
        screen_depth_store_path=None,
        depth_store_path=tmp_path / "collector.json",
    )

    assert depth_source.resolve_store_path(settings) == tmp_path / "collector.json"


def test_resolve_store_path_is_none_when_the_flag_is_off(tmp_path: Path) -> None:
    settings = SimpleNamespace(
        screen_depth=False,
        screen_depth_store_path=str(tmp_path / "screen.json"),
        depth_store_path=tmp_path / "collector.json",
    )

    assert depth_source.resolve_store_path(settings) is None
    assert depth_source.load_depth(None).symbols == {}


def test_screen_depth_settings_defaults() -> None:
    settings = Settings()

    assert setting(settings, "screen_depth") is True
    assert setting(settings, "screen_depth_store_path") is None


def test_screen_depth_settings_read_their_env_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEE_SCREEN_DEPTH", "0")
    monkeypatch.setenv("MEE_SCREEN_DEPTH_STORE", "/srv/depth.json")

    settings = Settings()

    assert setting(settings, "screen_depth") is False
    assert setting(settings, "screen_depth_store_path") == "/srv/depth.json"


def test_screen_depth_unset_falls_back_to_the_collectors_configured_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MEE_SCREEN_DEPTH_STORE", raising=False)
    monkeypatch.setenv("MEE_DEPTH_STORE_PATH", "/srv/collector.json")

    settings = Settings()

    assert setting(settings, "screen_depth_store_path") is None
    assert depth_source.resolve_store_path(settings) == Path("/srv/collector.json")
