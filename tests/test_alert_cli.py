"""Contract tests for ``python -m app.ops.alerts`` (``app.ops.alerts``).

The CLI is the only place where the three fact sources meet: the ``/health``
document (built in process by the route's own builder, never over HTTP), the
depth ledger beside the store, and the depth store's own coverage. These tests
pin the translation of each into an observation, and then the process
behaviour the operator asked for: one line per event, state written only when
the run was real, and exit 0 because a fired alert is not a crashed job.

Every path is under ``tmp_path``. Nothing here reads the production data
directory, and nothing reaches the network (the suite-wide socket guard would
fail the test if it tried — which is also the proof that ``/health`` is read in
process).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.market.depth_fields import CATALOG_FIELDS
from app.market.health_ledger import HealthLedger
from app.ops import alerts
from app.ops.alerting import AlertEvent, AlertState, Severity

#: Real catalog columns, taken from the registry so a renamed field cannot make
#: a coverage test pass by counting nothing.
CATALOG = sorted(CATALOG_FIELDS)


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """Settings rooted in ``tmp_path`` — the production data dir is never used."""

    values: dict[str, Any] = {
        "depth": False,
        "db_path": tmp_path / "ztockz.db",
        "depth_store_path": tmp_path / "depth.json",
        "health_snapshot_path": None,
        "health_data_dir": tmp_path,
        "alert_state_path": tmp_path / "alerts.state.json",
        "alert_sink_spec": f"file:{tmp_path / 'alerts.jsonl'}",
    }
    values.update(overrides)
    return Settings(**values)


def write_depth_store(path: Path, fields: list[str], symbols: tuple[str, ...] = ("AAPL",)) -> None:
    payload = {
        "as_of": "2026-10-30T00:00:00+00:00",
        "count": len(symbols),
        "symbols": {symbol: {name: 1.0 for name in fields} for symbol in symbols},
        "missing": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def report(status: str, checks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """A ``/health`` document shaped exactly as ``app.api.health`` builds it."""

    return {
        "status": status,
        "version": "0.0.0-test",
        "checked_at": "2026-10-30T00:00:00+00:00",
        "elapsed_ms": 0.5,
        "startup_error": None,
        "checks": checks,
    }


DEGRADED = report(
    "degraded",
    {
        "depth_store": {
            "ok": False,
            "status": "stale",
            "error": "the store is 90000s old; the budget is 93600s",
        },
        "disk": {"ok": True, "status": "ok", "error": None},
    },
)

FAILING = report(
    "failing",
    {
        "depth_store": {"ok": False, "status": "unparseable", "error": "not valid JSON"},
        "disk": {"ok": True, "status": "ok", "error": None},
    },
)


# ---------------------------------------------------------------------------
# the /health document -> observations
# ---------------------------------------------------------------------------


def test_health_observations_name_the_verdict_and_each_check() -> None:
    observations = alerts.health_observations(FAILING)
    by_key = {entry.key: entry for entry in observations}

    assert set(by_key) == {"health.deployment", "health.check.depth_store", "health.check.disk"}
    verdict = by_key["health.deployment"]
    assert verdict.ok is False
    assert verdict.severity is Severity.CRITICAL
    assert verdict.fingerprint == "failing"
    assert "unparseable" in verdict.detail

    check = by_key["health.check.depth_store"]
    assert check.ok is False
    assert check.severity is Severity.CRITICAL, "an unparseable store is untrusted data"
    assert check.title == "health check depth_store"
    assert "not valid JSON" in check.detail

    assert by_key["health.check.disk"].ok is True


def test_a_degraded_deployment_is_a_warning_not_a_critical() -> None:
    by_key = {entry.key: entry for entry in alerts.health_observations(DEGRADED)}
    assert by_key["health.deployment"].ok is False
    assert by_key["health.deployment"].severity is Severity.WARNING
    assert by_key["health.check.depth_store"].severity is Severity.WARNING
    assert "stale" in by_key["health.check.depth_store"].fingerprint


def test_a_healthy_deployment_produces_no_bad_observation() -> None:
    healthy = report("ok", {"depth_store": {"ok": True, "status": "ok", "error": None}})
    observations = alerts.health_observations(healthy)
    assert all(entry.ok for entry in observations)


def test_an_escalation_changes_the_fingerprint_so_it_is_said_again() -> None:
    """degraded -> failing is a different problem, not the same one louder."""

    degraded = alerts.health_observations(DEGRADED)[0]
    failing = alerts.health_observations(FAILING)[0]
    assert degraded.fingerprint != failing.fingerprint


# ---------------------------------------------------------------------------
# the ledger -> observations
# ---------------------------------------------------------------------------


def test_a_sick_depth_source_becomes_a_warning(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, depth=True)
    ledger = HealthLedger(tmp_path / "depth.health.json")
    ledger.record_error("sec_bulk", error="HTTP 503 from sec.gov")
    ledger.record_error("sec_bulk", error="HTTP 503 from sec.gov")
    ledger.record_ok("cnbc", duration_s=1.2, fields=42)
    assert ledger.persist()

    by_key = {entry.key: entry for entry in alerts.ledger_observations(settings)}

    sick = by_key["depth.source.sec_bulk"]
    assert sick.ok is False
    assert sick.severity is Severity.WARNING
    assert "2 consecutive" in sick.detail
    assert "HTTP 503" in sick.detail
    assert by_key["depth.source.cnbc"].ok is True


def test_one_failed_cycle_is_below_the_threshold(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, depth=True)
    ledger = HealthLedger(tmp_path / "depth.health.json")
    ledger.record_error("sec_bulk", error="timeout")
    assert ledger.persist()

    observations = alerts.ledger_observations(settings)
    assert [entry.key for entry in observations] == ["depth.source.sec_bulk"]
    assert observations[0].ok is True


def test_the_threshold_follows_the_setting(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, depth=True, alert_ledger_error_threshold=3)
    ledger = HealthLedger(tmp_path / "depth.health.json")
    ledger.record_error("sec_bulk", error="timeout")
    ledger.record_error("sec_bulk", error="timeout")
    assert ledger.persist()

    assert alerts.ledger_observations(settings)[0].ok is True


def test_a_disabled_depth_subsystem_is_not_a_sick_one(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, depth=False)
    ledger = HealthLedger(tmp_path / "depth.health.json")
    for _ in range(3):
        ledger.record_error("sec_bulk", error="timeout")
    assert ledger.persist()

    assert alerts.ledger_observations(settings) == []


# ---------------------------------------------------------------------------
# coverage -> observation
# ---------------------------------------------------------------------------


def test_coverage_measures_a_drop_against_the_high_water_mark(tmp_path: Path) -> None:
    store_path = tmp_path / "depth.json"
    write_depth_store(store_path, CATALOG[:10])
    settings = make_settings(tmp_path, depth=True)

    first, high_water = alerts.coverage_observation(settings, baseline=None)
    assert first is not None and first.ok is True
    assert high_water == 10
    assert "no baseline" in first.detail

    steady, high_water = alerts.coverage_observation(settings, baseline=high_water)
    assert steady is not None and steady.ok is True
    assert high_water == 10

    # Five fields gone is exactly the threshold: the fifth is the alarm.
    write_depth_store(store_path, CATALOG[:5])
    dropped, high_water = alerts.coverage_observation(settings, baseline=high_water)
    assert dropped is not None
    assert dropped.ok is False
    assert "5" in dropped.detail
    assert high_water == 10, "the high-water mark never falls back with the data"


def test_a_smaller_drop_is_noise(tmp_path: Path) -> None:
    store_path = tmp_path / "depth.json"
    write_depth_store(store_path, CATALOG[:10])
    settings = make_settings(tmp_path, depth=True)

    write_depth_store(store_path, CATALOG[:6])
    dropped, _ = alerts.coverage_observation(settings, baseline=10)

    assert dropped is not None and dropped.ok is True


def test_a_new_low_is_a_new_fingerprint(tmp_path: Path) -> None:
    store_path = tmp_path / "depth.json"
    settings = make_settings(tmp_path, depth=True)
    write_depth_store(store_path, CATALOG[:4])
    first, _ = alerts.coverage_observation(settings, baseline=10)
    write_depth_store(store_path, CATALOG[:1])
    second, _ = alerts.coverage_observation(settings, baseline=10)

    assert first is not None and second is not None
    assert first.ok is False and second.ok is False
    assert first.fingerprint != second.fingerprint


def test_an_unreadable_store_is_left_to_the_health_check(tmp_path: Path) -> None:
    """One fact, one observation: /health already reports the unreadable store."""

    settings = make_settings(tmp_path, depth=True)
    (tmp_path / "depth.json").write_text("{not json", encoding="utf-8")

    observation, high_water = alerts.coverage_observation(settings, baseline=10)

    assert observation is None
    assert high_water == 10


def test_build_observations_combines_all_three_sources(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, depth=True)
    write_depth_store(tmp_path / "depth.json", CATALOG[:3])
    ledger = HealthLedger(tmp_path / "depth.health.json")
    ledger.record_error("sec_bulk", error="timeout")
    ledger.record_error("sec_bulk", error="timeout")
    assert ledger.persist()

    observations, high_water = alerts.build_observations(settings, baseline=None)
    keys = {entry.key for entry in observations}

    assert "health.deployment" in keys
    assert "depth.source.sec_bulk" in keys
    assert "depth.coverage" in keys
    assert high_water == 3


# ---------------------------------------------------------------------------
# the process
# ---------------------------------------------------------------------------


def test_the_cli_prints_one_line_per_event_and_writes_the_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(alerts, "build_health_report", lambda settings, **kwargs: DEGRADED)
    settings = make_settings(tmp_path)

    code = alerts.main(["--once"], settings=settings)

    captured = capsys.readouterr()
    assert code == 0
    lines = captured.out.splitlines()
    assert len(lines) == 2, captured.out
    assert "health.deployment" in lines[0]
    assert "degraded" in lines[0].lower()
    assert "health.check.depth_store" in lines[1]
    assert "stale" in lines[1]

    state = json.loads(settings.alert_state_path.read_text(encoding="utf-8"))
    assert state["schema"] == 1
    assert set(state["alerts"]) == {"health.deployment", "health.check.depth_store"}
    assert state["alerts"]["health.deployment"]["state"] == "open"

    written = [
        json.loads(line)
        for line in Path(settings.alert_sink_spec.removeprefix("file:")).read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert [entry["key"] for entry in written] == [
        "health.deployment",
        "health.check.depth_store",
    ]
    assert written[0]["state"] == "open"


def test_a_second_run_reports_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(alerts, "build_health_report", lambda settings, **kwargs: DEGRADED)
    settings = make_settings(tmp_path)

    assert alerts.main(["--once"], settings=settings) == 0
    capsys.readouterr()
    assert alerts.main(["--once"], settings=settings) == 0

    assert capsys.readouterr().out == ""


def test_a_dry_run_prints_but_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(alerts, "build_health_report", lambda settings, **kwargs: DEGRADED)
    settings = make_settings(tmp_path)

    code = alerts.main(["--once", "--dry-run"], settings=settings)

    assert code == 0
    assert len(capsys.readouterr().out.splitlines()) == 2
    assert not settings.alert_state_path.exists()
    assert not Path(settings.alert_sink_spec.removeprefix("file:")).exists()


def test_a_fix_is_reported_as_a_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy = report("ok", {"depth_store": {"ok": True, "status": "ok", "error": None}})
    monkeypatch.setattr(alerts, "build_health_report", lambda settings, **kwargs: DEGRADED)
    settings = make_settings(tmp_path)
    alerts.main(["--once"], settings=settings)
    alerts.main(["--once"], settings=settings)  # the same bad news, said once
    capsys.readouterr()

    monkeypatch.setattr(alerts, "build_health_report", lambda settings, **kwargs: healthy)
    assert alerts.main(["--once"], settings=settings) == 0

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert all("RESOLVED" in line for line in lines)
    assert all("count=2" in line for line in lines)


def test_a_corrupt_state_file_is_reported_and_recovered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(alerts, "build_health_report", lambda settings, **kwargs: FAILING)
    settings = make_settings(tmp_path)
    settings.alert_state_path.write_text("}{ garbage", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="mee.ops.alerting"):
        code = alerts.main(["--once"], settings=settings)

    assert code == 0, "a corrupt memory must not stop the deployment being watched"
    assert len(capsys.readouterr().out.splitlines()) == 2
    assert any("corrupt" in entry.getMessage() for entry in caplog.records)
    assert json.loads(settings.alert_state_path.read_text(encoding="utf-8"))["alerts"]


def test_an_unwritable_sink_is_a_real_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(alerts, "build_health_report", lambda settings, **kwargs: FAILING)
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    settings = make_settings(tmp_path, alert_sink_spec=f"file:{blocker / 'alerts.jsonl'}")

    code = alerts.main(["--once"], settings=settings)

    assert code == 1
    assert capsys.readouterr().err.strip(), "the operator must be told why nothing was delivered"
    assert not settings.alert_state_path.exists(), (
        "state must not advance past an event nobody received: the next run has to say it again"
    )


def test_an_unknown_sink_scheme_is_a_real_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = make_settings(tmp_path, alert_sink_spec="telegram:12345")

    assert alerts.main(["--once"], settings=settings) == 1
    assert "telegram" in capsys.readouterr().err
    assert not settings.alert_state_path.exists()


def test_without_once_the_cli_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A loop belongs to the scheduler; an unsupervised daemon does not."""

    assert alerts.main([], settings=make_settings(tmp_path)) == 2


def test_the_cli_reads_health_in_process(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No monkeypatching: the real builder, a real tmp deployment, no HTTP."""

    settings = make_settings(tmp_path)

    code = alerts.main(["--once"], settings=settings)

    out = capsys.readouterr().out
    assert code == 0
    assert "health.deployment" in out
    assert "degraded" in out.lower(), out


def test_the_cli_writes_only_the_state_and_the_sink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Adversarial (e) for the whole process, with the real ``/health`` reader.

    ``chdir`` puts a stray relative write inside the sandbox; the real builder
    is left in place on purpose, because "the health probe is read-only" is a
    claim this test can check rather than inherit.
    """

    sandbox = tmp_path / "run"
    sandbox.mkdir()
    monkeypatch.chdir(sandbox)
    settings = make_settings(
        tmp_path,
        alert_state_path=sandbox / "state.json",
        alert_sink_spec=f"file:{sandbox / 'alerts.jsonl'}",
    )

    assert alerts.main(["--once"], settings=settings) == 0

    written = sorted(entry.name for entry in sandbox.iterdir())
    assert written == ["alerts.jsonl", "state.json"]
    capsys.readouterr()


def test_the_coverage_high_water_mark_survives_between_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The baseline lives in the state file, or a slow slide is never noticed."""

    healthy = report("ok", {"depth_store": {"ok": True, "status": "ok", "error": None}})
    monkeypatch.setattr(alerts, "build_health_report", lambda settings, **kwargs: healthy)
    store_path = tmp_path / "depth.json"
    write_depth_store(store_path, CATALOG[:10])
    settings = make_settings(tmp_path, depth=True)

    assert alerts.main(["--once"], settings=settings) == 0
    capsys.readouterr()
    state = json.loads(settings.alert_state_path.read_text(encoding="utf-8"))
    assert state["meta"]["fields_filled"] == 10

    write_depth_store(store_path, CATALOG[:5])
    assert alerts.main(["--once"], settings=settings) == 0

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert "depth.coverage" in lines[0]
    assert "drop 5" in lines[0]
    assert json.loads(settings.alert_state_path.read_text(encoding="utf-8"))["meta"][
        "fields_filled"
    ] == 10, "the high-water mark must not fall back with the data"


def test_the_summary_line_is_one_line_carrying_the_facts() -> None:
    event = AlertEvent(
        key="depth.source.sec_bulk",
        severity=Severity.WARNING,
        state=AlertState.OPEN,
        title="depth source sec_bulk",
        detail="2 consecutive failed cycle(s): timeout",
        count=1,
        first_seen=0.0,
        last_seen=0.0,
        fingerprint="sec_bulk|timeout",
    )

    line = alerts.format_event(event)

    assert "\n" not in line
    for token in ("OPEN", "warning", "depth.source.sec_bulk", "timeout", "count=1"):
        assert token in line


def test_the_cli_declares_itself_an_out_of_process_observer(
    monkeypatch, tmp_path: Path
) -> None:
    """The CLI must never claim the service's collector is missing.

    It reads the health builder from another process, so it has to say so;
    otherwise a healthy deployment pages on every run.
    """

    from app.ops import alerts

    seen: dict[str, object] = {}

    def _builder(settings, **kwargs):
        seen.update(kwargs)
        return {"status": "ok", "checks": {}, "elapsed_ms": 1.0}

    monkeypatch.setattr(alerts, "build_health_report", _builder)

    exit_code = alerts.main(
        [
            "--once",
            "--sink",
            f"file:{tmp_path / 'out.jsonl'}",
            "--state",
            str(tmp_path / "state.json"),
        ]
    )

    assert exit_code == 0
    assert seen.get("observe_runtime") is False, seen
