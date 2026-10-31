"""The build artefact is part of the deployment contract (F1c, F1d, plan §3).

``web/dist`` is generated, gitignored, and mounted at import time. Three holes
came out of the Phase 1 adversarial review and each gets a test here:

* **Absent** — the container and CI never built ``dist``, so ``/app`` answered
  404 while ``/health`` said ``ok``. The artefact is now a health check: a
  deployment that *declares* a UI (``MEE_SERVE_UI=1``, which the image sets)
  answers **503** when ``index.html`` is missing, and the startup log names the
  rebuild command. A source checkout that never built is ``degraded`` (200) —
  still not ``ok`` — because an API-only process must stay startable.
* **Not mounted** — the mount is decided in ``create_app``, so ``git pull &&
  npm run build`` on a running box changes nothing until a restart. The check
  compares the live file with the verdict this process started with and reports
  ``not_mounted``.
* **Stale** — a ``dist`` older than its sources was green through vitest, the
  budget gate and the mount tests. ``build_is_stale`` compares mtimes; the
  last test in this file is the gate over the real tree.

Each case is built in ``tmp_path`` — a throwaway ``web/`` with its own ``src``,
``dist`` and mtimes — so nothing here depends on whether *this* checkout happens
to have been built, and nothing touches the repository's own sources.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from app.api import health as health_api
from app.api.health import WebUiState, build_is_stale, resolve_web_ui
from app.config import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DIST = WEB / "dist"
INDEX = DIST / "index.html"
SRC = WEB / "src"

#: What every message that reports an unbuilt artefact has to say: the reader
#: must be able to paste it, not go looking for the build instructions.
REBUILD = "cd web && npm run build"


# ---------------------------------------------------------------------------
# a throwaway web project
# ---------------------------------------------------------------------------


def _project(
    tmp_path: Path,
    *,
    built: bool = True,
    built_at: float | None = None,
    source_at: float | None = None,
) -> Path:
    """``web/`` with a ``package.json``, a source file and (optionally) a build.

    ``built_at`` / ``source_at`` are absolute mtimes so a test can order the two
    without sleeping: the staleness rule is a comparison, and a test that sleeps
    for a second to produce one is a test that flakes on a loaded box.
    """

    web = tmp_path / "web"
    src = web / "src"
    src.mkdir(parents=True, exist_ok=True)
    manifest = web / "package.json"
    if not manifest.exists():
        manifest.write_text('{"name": "ztockz-web"}', encoding="utf-8")
    source = src / "app.tsx"
    if not source.exists():
        source.write_text("export const app = 1;\n", encoding="utf-8")

    if built:
        dist = web / "dist"
        (dist / "assets").mkdir(parents=True, exist_ok=True)
        (dist / "index.html").write_text(
            '<!doctype html><html><body><div id="root"></div></body></html>\n',
            encoding="utf-8",
        )
        (dist / "assets" / "index.js").write_text("console.log(1);\n", encoding="utf-8")
        if built_at is not None:
            for path in dist.rglob("*"):
                if path.is_file():
                    os.utime(path, (built_at, built_at))

    # A build is by definition after the sources it read, so an explicit build
    # time implies an earlier source time. Without this the call ``built_at =
    # time.time()`` is evaluated *before* the source is written and the source
    # wins the comparison by microseconds — the same ordering trap the real gate
    # has to survive in CI.
    stamp = source_at if source_at is not None else (
        built_at - 60 if built_at is not None else None
    )
    if stamp is not None:
        for path in src.rglob("*"):
            if path.is_file():
                os.utime(path, (stamp, stamp))
    return web


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No test inherits a UI switch from the developer's shell."""

    monkeypatch.delenv("MEE_SERVE_UI", raising=False)
    monkeypatch.delenv("MEE_WEB_DIST", raising=False)
    monkeypatch.delenv("MEE_WEB_SRC", raising=False)
    yield


def _settings(tmp_path: Path) -> Settings:
    """Settings pointed entirely at ``tmp_path`` — never at the production box."""

    # No snapshot path: a configured-but-missing snapshot is *fatal* on its own
    # and would answer 503 for a reason that has nothing to do with the shell.
    # An unconfigured one is merely degraded, so the verdict under test is the
    # UI's.
    return Settings(
        db_path=tmp_path / "ztockz.db",
        health_depth_store_path=tmp_path / "data" / "depth.json",
        health_snapshot_path=None,
        health_data_dir=tmp_path / "data",
        health_screen_db_path=tmp_path / "ztockz.db",
        depth=False,
    )


def _app(monkeypatch: pytest.MonkeyPatch, web: Path, *, serve_ui: str | None) -> Any:
    """An app whose ``/app`` mount points at ``web`` (or at nothing)."""

    monkeypatch.setenv("MEE_WEB_DIST", str(web / "dist"))
    if serve_ui is not None:
        monkeypatch.setenv("MEE_SERVE_UI", serve_ui)
    return create_app(_settings(web.parent / "settings"))


def _ui(client: TestClient) -> dict[str, Any]:
    response = client.get("/health")
    body = response.json()
    assert "ui" in body, body
    return {"response": response, **body["ui"]}


# ---------------------------------------------------------------------------
# F1c — absent, and the deployment says it serves a UI
# ---------------------------------------------------------------------------


def test_missing_dist_fails_the_healthcheck_when_the_ui_is_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The image sets ``MEE_SERVE_UI=1``; a UI-less image must not read healthy.

    ``failing`` (503) rather than ``degraded``: ``degraded`` is the deployment's
    word for "serving, but behind", and it is already the normal colour of a
    stale snapshot or a missing screen database. A deployment whose only HTML
    surface 404s is not behind, it is not the product — and an orchestrator only
    refuses a rollout on a non-200.
    """

    web = _project(tmp_path, built=False)
    app = _app(monkeypatch, web, serve_ui="1")

    with TestClient(app) as client:
        ui = _ui(client)

    assert ui["response"].status_code == 503, ui["response"].text
    assert ui["response"].json()["status"] == "failing"
    assert ui["ok"] is False
    assert ui["status"] == "missing"
    assert ui["required"] is True
    assert ui["exists"] is False
    assert REBUILD in (ui["error"] or "")


def test_the_startup_log_names_the_rebuild_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator who never reads ``/health`` still has to be told.

    The log is the only channel that exists before anything polls the process,
    so the line has to carry the fix, not just the symptom.
    """

    web = _project(tmp_path, built=False)
    monkeypatch.setenv("MEE_WEB_DIST", str(web / "dist"))
    monkeypatch.setenv("MEE_SERVE_UI", "1")

    records: list[logging.LogRecord] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - trivial
            records.append(record)

    logger = logging.getLogger("mee.web")
    handler = _Sink(level=logging.DEBUG)
    logger.addHandler(handler)
    try:
        create_app(_settings(tmp_path / "settings"))
    finally:
        logger.removeHandler(handler)

    errors = [record for record in records if record.levelno >= logging.ERROR]
    assert errors, [record.getMessage() for record in records]
    message = "\n".join(record.getMessage() for record in errors)
    assert REBUILD in message, message
    assert "index.html" in message, message


def test_a_source_checkout_without_a_build_is_degraded_never_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auto mode: a developer's unbuilt tree keeps serving, and still says so.

    503 here would take a working API out of rotation for a missing artefact the
    operator may not be shipping at all. ``degraded`` is the honest middle: the
    document carries ``ui.ok = false`` and the rebuild command, so nothing that
    reads the report can mistake it for a healthy UI deployment.
    """

    web = _project(tmp_path, built=False)
    app = _app(monkeypatch, web, serve_ui=None)

    with TestClient(app) as client:
        ui = _ui(client)

    assert ui["response"].status_code == 200, ui["response"].text
    assert ui["response"].json()["status"] == "degraded"
    assert ui["ok"] is False
    assert ui["status"] == "absent"
    assert ui["required"] is False
    assert REBUILD in (ui["error"] or "")


def test_an_api_only_deployment_can_switch_the_ui_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``MEE_SERVE_UI=0`` is a claim too: no UI, and no complaint about it.

    Without this arm, every API-only box would carry a permanent warning and the
    operator would learn to ignore the check.
    """

    web = _project(tmp_path, built=False)
    app = _app(monkeypatch, web, serve_ui="0")

    with TestClient(app) as client:
        ui = _ui(client)
        # The mount is decided by the artefact, so an API-only process still
        # answers /app with a 404 — the check's job is to say that is intended.
        assert client.get("/app").status_code == 404

    assert ui["response"].status_code == 200
    assert ui["ok"] is True
    assert ui["status"] == "disabled"
    assert ui["required"] is False


# ---------------------------------------------------------------------------
# F1c — present now, absent when the process started
# ---------------------------------------------------------------------------


def test_a_process_that_started_before_the_build_is_reported_as_not_mounted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live 8099 bug: ``npm run build`` after boot changes nothing.

    The mount is registered in ``create_app``, so a process that started before
    ``dist`` existed answers 404 for every ``/app`` URL until it restarts. The
    file is on disk now, which is what makes this indistinguishable from a
    working deployment to every check that only looks at the filesystem.
    """

    web = _project(tmp_path, built=False)
    app = _app(monkeypatch, web, serve_ui="1")

    with TestClient(app) as client:
        assert client.get("/app").status_code == 404
        _project(tmp_path, built=True)  # the build lands after the process booted
        ui = _ui(client)
        assert client.get("/app").status_code == 404

    assert ui["response"].status_code == 503
    assert ui["ok"] is False
    assert ui["status"] == "not_mounted"
    assert ui["exists"] is True
    assert "restart" in (ui["error"] or "").lower()


# ---------------------------------------------------------------------------
# F1d — stale
# ---------------------------------------------------------------------------


def test_a_dist_older_than_its_sources_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The artefact the process serves predates the source it claims to be."""

    built_at = time.time() - 3_600
    web = _project(tmp_path, built=True, built_at=built_at, source_at=time.time())
    app = _app(monkeypatch, web, serve_ui=None)

    with TestClient(app) as client:
        ui = _ui(client)

    assert ui["ok"] is False
    assert ui["status"] == "stale"
    assert ui["newest_source"] == "app.tsx"
    assert REBUILD in (ui["error"] or "")


def test_a_fresh_build_is_not_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mtime ordering trap: a tree built after its sources is healthy.

    ``vite build`` writes ``dist`` after the sources it read, so the artefact is
    always newer on a machine that just built. A check that compared the wrong
    pair (directory mtimes, or ``src`` against the *oldest* asset) would fail a
    perfect build, and a gate that cries wolf gets switched off.
    """

    source_at = time.time() - 60
    web = _project(tmp_path, built=True, built_at=time.time(), source_at=source_at)
    app = _app(monkeypatch, web, serve_ui=None)

    with TestClient(app) as client:
        ui = _ui(client)

    assert ui["ok"] is True, ui
    assert ui["status"] == "ok"
    assert ui["stale_s"] is None


def test_touching_a_source_file_makes_the_comparison_fire(tmp_path: Path) -> None:
    """The checker itself, without a process: touch, stale; rebuild, clean.

    The health check compares the *startup* verdict, so this is where the rule
    is proven to bite on a change made after boot.
    """

    web = _project(tmp_path, built=True, built_at=time.time() - 60)
    assert build_is_stale(web) is None

    source = web / "src" / "app.tsx"
    stamp = time.time()
    os.utime(source, (stamp, stamp))
    stale = build_is_stale(web)
    assert stale is not None
    assert stale[0] == source
    assert stale[1] == pytest.approx(stamp, abs=1)

    # A rebuild rewrites the artefact; the comparison clears.
    index = web / "dist" / "index.html"
    rebuilt = time.time() + 1
    os.utime(index, (rebuilt, rebuilt))
    assert build_is_stale(web) is None


def test_a_checkout_granularity_tie_is_not_stale(tmp_path: Path) -> None:
    """``git checkout`` and ``npm run build`` can land in the same second.

    A comparison that treated ``equal`` as stale would fail a tree that was
    built from exactly the sources it has — the false positive this gate cannot
    afford, because it is the one that fires on a perfect CI run.
    """

    stamp = time.time()
    web = _project(tmp_path, built=True, built_at=stamp, source_at=stamp)

    assert build_is_stale(web) is None


def test_a_test_file_does_not_make_the_artefact_stale(tmp_path: Path) -> None:
    """Editing a test does not change the bundle, so it cannot invalidate it.

    Without this rule the gate fires on the most common edit in the repository —
    a test — and teaches everyone to ignore it.
    """

    web = _project(tmp_path, built=True, built_at=time.time())
    later = time.time() + 5
    for name in ("tokens.test.ts", "shell.spec.tsx"):
        path = web / "src" / name
        path.write_text("// test\n", encoding="utf-8")
        os.utime(path, (later, later))

    assert build_is_stale(web) is None


# ---------------------------------------------------------------------------
# adversarial (c) — the poll must stay cheap
# ---------------------------------------------------------------------------


def test_health_only_stats_the_artefact_and_never_reads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A load balancer polls every few seconds; /health may not read 340 KB.

    The check stats ``index.html`` once and trusts the startup verdict for
    everything else, so the per-poll cost is one ``stat`` — the same order as
    the five checks that were already there. The measurement is printed rather
    than asserted, because a wall-clock bound on a shared box is a flake.
    """

    web = _project(tmp_path, built=True, built_at=time.time())
    app = _app(monkeypatch, web, serve_ui="1")

    opened: list[str] = []
    original_open = Path.open

    def _record(self: Path, *args: Any, **kwargs: Any) -> Any:
        opened.append(str(self))
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _record)

    with TestClient(app) as client:
        for _ in range(5):
            assert client.get("/health").status_code == 200

    touched = [path for path in opened if str(web / "dist") in path]
    assert not touched, touched

    state = resolve_web_ui(dist_dir=web / "dist", mounted=True)
    started = time.perf_counter()
    for _ in range(2_000):
        health_api.web_ui_check(state)
    per_call_us = (time.perf_counter() - started) * 1e6 / 2_000
    print(f"\nweb_ui check: {per_call_us:.1f} µs/call (stat only, no read)")
    # Measured at ~4 µs on the development box against ~2 µs for the bare
    # ``stat`` this needs; 500 µs is two orders of magnitude of headroom for a
    # loaded runner while still failing a check that starts walking the tree.
    assert per_call_us < 500, f"{per_call_us:.0f} µs per poll is not a stat"


# ---------------------------------------------------------------------------
# the gate over the real tree
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not INDEX.is_file(),
    reason="web/dist is not built — run `cd web && npm run build`",
)
def test_the_repository_dist_is_not_older_than_its_sources() -> None:
    """The gate: a stale ``web/dist`` fails ``pytest`` with the fix in the message.

    This is the only check in the suite that compares the committed sources with
    the generated artefact. Without it a source edit plus an old ``dist`` is
    green everywhere — the mount tests read the old bytes and certify them.
    """

    stale = build_is_stale(WEB)

    assert stale is None, (
        f"web/dist is stale: {stale[0].relative_to(WEB) if stale else ''} is newer "
        f"than web/dist/index.html by "
        f"{time.time() - INDEX.stat().st_mtime:.0f}s of source changes.\n"
        f"Rebuild it: {REBUILD}"
    )


def test_the_real_tree_resolves_a_servable_artefact() -> None:
    """Whatever the environment, ``resolve_web_ui`` is coherent about it."""

    state = resolve_web_ui(mounted=INDEX.is_file())

    assert isinstance(state, WebUiState)
    assert state.enabled is True, "the repository ships a web project"
    assert state.index == INDEX
    assert state.dist_dir == DIST
    assert state.source_dir == SRC
    # The health document must never carry an absolute path to an anonymous
    # caller; the label is relative and the log line has the real one.
    document = health_api.web_ui_check(state).document
    assert str(ROOT) not in str(document.get("error") or "")
