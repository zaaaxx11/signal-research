"""Contract tests for the alert decision engine (``app.ops.alerting``).

The engine has one job: turn "what is true now" plus "what I already said" into
at most one event per key per change of state. These tests pin every transition
the operator specified, then the three ways a naive implementation gets it
wrong — a detail that changes every poll, a key that vanishes from the input, a
clock that steps backwards — and finally the containment rule: the only file the
engine touches is its own state document.

Nothing here freezes the global clock. ``evaluate`` takes ``now`` and the
constructor takes ``clock``, so determinism is the caller's, not the process's.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import pytest

from app.ops.alerting import (
    AlertEngine,
    AlertEvent,
    AlertRecord,
    AlertState,
    AlertStateStore,
    Observation,
    Severity,
)

DAY = 86_400.0


def observation(
    key: str = "depth_store",
    *,
    ok: bool = False,
    severity: Severity = Severity.CRITICAL,
    title: str = "the depth store is stale",
    detail: str = "12h old",
    fingerprint: str = "stale",
) -> Observation:
    """One observation with the 2 a.m. defaults: a broken thing, said loudly."""

    return Observation(
        key=key,
        ok=ok,
        severity=severity,
        title=title,
        detail=detail,
        fingerprint=fingerprint,
    )


def engine_at(tmp_path: Path, **kwargs: object) -> AlertEngine:
    """An engine over a per-test state file (never the production data dir)."""

    store = AlertStateStore(tmp_path / "alerts.state.json")
    return AlertEngine(store, **kwargs)  # type: ignore[arg-type]


def _tree(root: Path) -> dict[str, tuple[int, int]]:
    """Every file under ``root`` with the two numbers that prove it changed."""

    return {
        path.relative_to(root).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# ---------------------------------------------------------------------------
# the four transitions
# ---------------------------------------------------------------------------


def test_ok_to_bad_opens_with_count_one(tmp_path: Path) -> None:
    engine = engine_at(tmp_path)
    assert engine.evaluate([observation(ok=True)], now=100.0) == []

    events = engine.evaluate([observation(detail="the store is 12h old")], now=200.0)

    assert len(events) == 1
    event = events[0]
    assert event.key == "depth_store"
    assert event.state is AlertState.OPEN
    assert event.count == 1
    assert event.severity is Severity.CRITICAL
    assert event.detail == "the store is 12h old"
    assert event.fingerprint == "stale"
    assert event.first_seen == 200.0
    assert event.last_seen == 200.0


def test_bad_to_bad_with_the_same_fingerprint_emits_nothing(tmp_path: Path) -> None:
    engine = engine_at(tmp_path)
    assert len(engine.evaluate([observation()], now=100.0)) == 1

    assert engine.evaluate([observation()], now=200.0) == []
    assert engine.evaluate([observation()], now=300.0) == []

    # Silence, not amnesia: the episode's length is the count a resolve reports.
    resolved = engine.evaluate([observation(ok=True)], now=400.0)
    assert [event.state for event in resolved] == [AlertState.RESOLVED]
    assert resolved[0].count == 3


def test_bad_to_bad_with_a_changed_fingerprint_opens_again(tmp_path: Path) -> None:
    """The problem mutated (stale -> unreadable): that is new information."""

    engine = engine_at(tmp_path)
    engine.evaluate([observation(fingerprint="missing")], now=100.0)

    events = engine.evaluate(
        [observation(fingerprint="unparseable", detail="not valid JSON")], now=200.0
    )

    assert len(events) == 1
    assert events[0].state is AlertState.OPEN
    assert events[0].count == 1, "the old episode's count must not leak into the new one"
    assert events[0].fingerprint == "unparseable"
    assert events[0].first_seen == 200.0


def test_bad_to_ok_resolves_with_the_final_count(tmp_path: Path) -> None:
    engine = engine_at(tmp_path)
    opened = engine.evaluate([observation(title="store is stale")], now=100.0)[0]
    engine.evaluate([observation()], now=200.0)

    events = engine.evaluate([observation(ok=True, detail="fresh again")], now=300.0)

    assert len(events) == 1
    event = events[0]
    assert event.state is AlertState.RESOLVED
    assert event.count == 2
    assert event.first_seen == opened.first_seen
    assert event.last_seen == 300.0
    assert event.title == "store is stale"
    assert event.severity is Severity.CRITICAL, "a resolve closes the episode as it was announced"
    assert "fresh again" in event.detail

    # A second ok is not a second resolution.
    assert engine.evaluate([observation(ok=True)], now=400.0) == []


# ---------------------------------------------------------------------------
# memory across a restart
# ---------------------------------------------------------------------------


def test_a_restart_does_not_re_emit_an_open_alert(tmp_path: Path) -> None:
    path = tmp_path / "alerts.state.json"
    first = AlertEngine(AlertStateStore(path))
    assert len(first.evaluate([observation()], now=100.0)) == 1

    restarted = AlertEngine(AlertStateStore(path))

    assert restarted.evaluate([observation()], now=200.0) == []
    assert restarted.state["depth_store"].state is AlertState.OPEN
    assert restarted.state["depth_store"].count == 2


def test_a_resolved_key_that_fails_again_does_emit(tmp_path: Path) -> None:
    path = tmp_path / "alerts.state.json"
    engine = AlertEngine(AlertStateStore(path))
    engine.evaluate([observation()], now=100.0)
    engine.evaluate([observation(ok=True)], now=200.0)

    events = AlertEngine(AlertStateStore(path)).evaluate([observation()], now=300.0)

    assert [event.state for event in events] == [AlertState.OPEN]
    assert events[0].count == 1
    assert events[0].first_seen == 300.0


# ---------------------------------------------------------------------------
# flapping
# ---------------------------------------------------------------------------


def test_four_cycles_inside_the_window_emit_one_flapping_notice(tmp_path: Path) -> None:
    engine = engine_at(tmp_path, flap_window_s=100.0, flap_limit=3)
    emitted: list[AlertEvent] = []
    moment = 0.0
    for cycle in range(4):
        emitted.extend(engine.evaluate([observation(detail=f"failure {cycle}")], now=moment))
        emitted.extend(engine.evaluate([observation(ok=True)], now=moment + 1.0))
        moment += 2.0

    # Three honest round trips, then one notice instead of the fourth.
    assert [event.state for event in emitted] == [
        AlertState.OPEN,
        AlertState.RESOLVED,
        AlertState.OPEN,
        AlertState.RESOLVED,
        AlertState.OPEN,
        AlertState.RESOLVED,
        AlertState.OPEN,
    ]
    flapping = emitted[-1]
    assert flapping.count == 1
    assert "flapping" in flapping.detail.lower()
    assert "4" in flapping.detail

    # Inside the suppression window the key may oscillate as much as it likes.
    assert engine.evaluate([observation(ok=True)], now=8.0) == []
    assert engine.evaluate([observation()], now=9.0) == []
    assert engine.evaluate([observation(ok=True)], now=10.0) == []


def test_the_key_speaks_again_once_the_window_passes(tmp_path: Path) -> None:
    engine = engine_at(tmp_path, flap_window_s=100.0, flap_limit=3)
    moment = 0.0
    for cycle in range(4):
        engine.evaluate([observation()], now=moment)
        engine.evaluate([observation(ok=True)], now=moment + 1.0)
        moment += 2.0
    flapping_open = engine.state["depth_store"]
    assert flapping_open.suppressed_until == pytest.approx(6.0 + 100.0)

    # The window passes while the deployment is healthy: the notice is closed.
    events = engine.evaluate([observation(ok=True)], now=107.0)
    assert [event.state for event in events] == [AlertState.RESOLVED]

    # And the next failure is an ordinary open, not an immediate re-flap: the
    # notice started a fresh history instead of arming itself for ever.
    events = engine.evaluate([observation()], now=200.0)
    assert [event.state for event in events] == [AlertState.OPEN]
    assert "flapping" not in events[0].detail.lower()


def test_a_single_cycle_never_flaps(tmp_path: Path) -> None:
    engine = engine_at(tmp_path, flap_window_s=100.0, flap_limit=3)
    events = engine.evaluate([observation()], now=1.0)
    assert "flapping" not in events[0].detail.lower()


def test_a_restart_inside_the_quiet_window_stays_quiet(tmp_path: Path) -> None:
    """The suppression is part of the memory, like everything else."""

    path = tmp_path / "alerts.state.json"
    engine = AlertEngine(AlertStateStore(path), flap_window_s=100.0, flap_limit=3)
    moment = 0.0
    for _ in range(4):
        engine.evaluate([observation()], now=moment)
        engine.evaluate([observation(ok=True)], now=moment + 1.0)
        moment += 2.0

    restarted = AlertEngine(AlertStateStore(path), flap_window_s=100.0, flap_limit=3)
    assert restarted.evaluate([observation()], now=7.0) == []
    assert restarted.state["depth_store"].suppressed_until == pytest.approx(106.0)


# ---------------------------------------------------------------------------
# adversarial (a): which field is the change signal
# ---------------------------------------------------------------------------


def test_detail_is_presentation_and_the_fingerprint_is_the_signal(tmp_path: Path) -> None:
    """Every non-fingerprint field may churn without waking the operator."""

    engine = engine_at(tmp_path)
    engine.evaluate([observation(detail="age 100s", fingerprint="stale")], now=100.0)

    churned = observation(
        detail="age 200s, next poll at 03:00, backlog 12",
        severity=Severity.WARNING,
        title="renamed by a refactor",
        fingerprint="stale",
    )
    assert engine.evaluate([churned], now=200.0) == []

    # The identical text, one changed fingerprint: the condition is a different
    # one (the store is now unreadable, it was merely old), so it is news.
    changed = dataclasses.replace(churned, fingerprint="stale:unreadable")
    events = engine.evaluate([changed], now=300.0)

    assert len(events) == 1
    assert events[0].state is AlertState.OPEN
    assert events[0].detail == churned.detail


# ---------------------------------------------------------------------------
# adversarial (b): a key that is no longer observed
# ---------------------------------------------------------------------------


def test_a_key_that_disappears_from_the_input_does_not_resolve(tmp_path: Path) -> None:
    """Absence is not evidence of recovery, so silence must not close a page.

    An input that stops mentioning a key cannot distinguish "the check no longer
    applies" from "the collector that would have said so is dead". Closing the
    alert on that guess is exactly the failure this deployment already has: a
    stale thing that reports healthy. The record therefore stays open, keeps its
    fingerprint, and a later failure is a dedupe rather than a second page.
    ``AlertStateStore.prune`` is the only thing that may forget it.
    """

    engine = engine_at(tmp_path)
    engine.evaluate([observation(key="depth_store"), observation(key="disk", ok=True)], now=100.0)

    assert engine.evaluate([observation(key="disk", ok=True)], now=200.0) == []
    record = engine.state["depth_store"]
    assert record.state is AlertState.OPEN
    assert record.count == 1

    assert engine.evaluate([observation(key="depth_store")], now=300.0) == []
    assert engine.state["depth_store"].count == 2


# ---------------------------------------------------------------------------
# adversarial (c): clocks
# ---------------------------------------------------------------------------


def test_a_backwards_clock_does_not_corrupt_counts(tmp_path: Path) -> None:
    engine = engine_at(tmp_path)
    engine.evaluate([observation()], now=1_000.0)

    assert engine.evaluate([observation()], now=900.0) == []

    record = engine.state["depth_store"]
    assert record.count == 2
    assert record.first_seen == 1_000.0
    assert record.last_seen == 1_000.0, "last_seen must never move backwards"

    resolved = engine.evaluate([observation(ok=True)], now=950.0)
    assert resolved[0].count == 2
    assert resolved[0].last_seen >= resolved[0].first_seen


def test_a_backwards_clock_does_not_age_out_flap_evidence(tmp_path: Path) -> None:
    """A clock step must not launder a flapping key back into a pager."""

    engine = engine_at(tmp_path, flap_window_s=100.0, flap_limit=3)
    engine.evaluate([observation()], now=1_000.0)
    engine.evaluate([observation(ok=True)], now=1_001.0)
    engine.evaluate([observation()], now=990.0)
    engine.evaluate([observation(ok=True)], now=1_002.0)
    engine.evaluate([observation()], now=1_003.0)
    engine.evaluate([observation(ok=True)], now=1_004.0)

    events = engine.evaluate([observation()], now=1_005.0)

    assert [event.state for event in events] == [AlertState.OPEN]
    assert "flapping" in events[0].detail.lower()


def test_two_observations_with_the_same_timestamp_are_counted(tmp_path: Path) -> None:
    engine = engine_at(tmp_path)
    engine.evaluate([observation()], now=100.0)
    assert engine.evaluate([observation()], now=100.0) == []
    assert engine.evaluate([observation()], now=100.0) == []
    assert engine.state["depth_store"].count == 3

    engine.evaluate([observation(ok=True)], now=100.0)
    events = engine.evaluate([observation()], now=100.0)
    assert [event.state for event in events] == [AlertState.OPEN]
    assert events[0].count == 1


# ---------------------------------------------------------------------------
# the state document
# ---------------------------------------------------------------------------


def test_the_state_round_trips_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "alerts.state.json"
    engine = AlertEngine(AlertStateStore(path))
    engine.evaluate([observation()], now=100.0)

    assert path.exists()
    assert sorted(entry.name for entry in path.parent.iterdir()) == ["alerts.state.json"]

    reloaded = AlertStateStore(path).load()
    assert set(reloaded) == {"depth_store"}
    assert reloaded["depth_store"].state is AlertState.OPEN
    assert reloaded["depth_store"].fingerprint == "stale"
    assert reloaded["depth_store"].count == 1


def test_a_corrupt_state_file_is_a_warning_and_an_empty_state(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "alerts.state.json"
    path.write_text("{ this is not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="mee.ops.alerting"):
        records = AlertStateStore(path).load()

    assert records == {}
    assert any("corrupt" in entry.getMessage() for entry in caplog.records), caplog.text

    # And the run continues: an empty memory re-alerts, which is recoverable.
    engine = AlertEngine(AlertStateStore(path))
    assert len(engine.evaluate([observation()], now=100.0)) == 1
    assert AlertStateStore(path).load()["depth_store"].state is AlertState.OPEN


@pytest.mark.parametrize(
    "document",
    [
        "[]",
        '{"schema": 999, "alerts": {"depth_store": {"state": "open"}}}',
        '{"alerts": {"depth_store": {"state": "wharrgarbl"}}}',
        '{"alerts": "not a mapping"}',
    ],
)
def test_an_unusable_state_document_degrades_to_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, document: str
) -> None:
    path = tmp_path / "alerts.state.json"
    path.write_text(document, encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="mee.ops.alerting"):
        assert AlertStateStore(path).load() == {}
    assert caplog.records, f"{document!r} was accepted without a word"


def test_prune_forgets_only_old_resolved_alerts(tmp_path: Path) -> None:
    store = AlertStateStore(tmp_path / "alerts.state.json")
    store.save(
        {
            "old_open": AlertRecord(state=AlertState.OPEN, count=1, last_seen=0.0),
            "old_resolved": AlertRecord(state=AlertState.RESOLVED, count=2, last_seen=0.0),
            "new_resolved": AlertRecord(
                state=AlertState.RESOLVED, count=1, last_seen=DAY * 9.0
            ),
        }
    )

    removed = store.prune(now=DAY * 10.0, max_age_days=7)

    assert removed == ["old_resolved"]
    assert set(store.load()) == {"old_open", "new_resolved"}


def test_prune_rejects_a_nonsense_retention(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        AlertStateStore(tmp_path / "alerts.state.json").prune(now=0.0, max_age_days=0)


# ---------------------------------------------------------------------------
# purity and containment
# ---------------------------------------------------------------------------


def test_the_clock_is_injected_never_read_from_the_process(tmp_path: Path) -> None:
    engine = engine_at(tmp_path, clock=lambda: 1_234.5)
    events = engine.evaluate([observation()])
    assert events[0].first_seen == 1_234.5
    assert events[0].last_seen == 1_234.5


def test_the_engine_writes_only_its_state_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adversarial (e): nothing outside the state document, ever.

    ``chdir`` makes a stray relative write land in the sandbox where the tree
    snapshot can see it, which a check on absolute paths alone would miss.
    """

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    monkeypatch.chdir(sandbox)
    before = _tree(sandbox)

    engine = AlertEngine(AlertStateStore(sandbox / "state" / "alerts.state.json"))
    engine.evaluate([observation(), observation(key="disk")], now=100.0)
    engine.evaluate([observation(key="disk", ok=True)], now=200.0)

    after = _tree(sandbox)
    assert set(after) - set(before) == {"state/alerts.state.json"}
    assert not [name for name in after if name.endswith(".tmp")]

    # A memory-only pass (the --dry-run path) writes nothing at all.
    before_dry = _tree(sandbox)
    engine.evaluate([observation(ok=True)], now=300.0, persist=False)
    assert _tree(sandbox) == before_dry


def test_an_engine_without_a_path_still_decides(tmp_path: Path) -> None:
    engine = AlertEngine(AlertStateStore(None))
    assert len(engine.evaluate([observation()], now=100.0)) == 1
    assert engine.state["depth_store"].state is AlertState.OPEN


# ---------------------------------------------------------------------------
# value objects
# ---------------------------------------------------------------------------


def test_an_observation_is_frozen_and_normalised() -> None:
    entry = Observation(
        key="  depth_store  ", ok=False, severity="critical"  # type: ignore[arg-type]
    )
    assert entry.key == "depth_store"
    assert entry.severity is Severity.CRITICAL
    assert entry.fingerprint == ""

    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.ok = True  # type: ignore[misc]

    with pytest.raises(ValueError):
        Observation(key="   ", ok=False)


def test_an_observation_with_an_unknown_severity_is_refused() -> None:
    with pytest.raises(ValueError):
        Observation(key="k", ok=False, severity="emergency")  # type: ignore[arg-type]


def test_an_event_serialises_to_a_json_safe_document() -> None:
    event = AlertEvent(
        key="depth_store",
        severity=Severity.CRITICAL,
        state=AlertState.OPEN,
        title="stale",
        detail="12h old",
        count=1,
        first_seen=100.0,
        last_seen=200.0,
        fingerprint="stale",
    )
    document = event.as_dict()

    assert document["key"] == "depth_store"
    assert document["severity"] == "critical"
    assert document["state"] == "open"
    assert document["count"] == 1
    assert document["first_seen"] == "1970-01-01T00:01:40+00:00"
    assert document["last_seen"] == "1970-01-01T00:03:20+00:00"


def test_a_duplicate_key_in_one_batch_uses_the_last_observation(tmp_path: Path) -> None:
    engine = engine_at(tmp_path)
    assert engine.evaluate([observation(detail="first"), observation(ok=True)], now=100.0) == []
    assert engine.state == {}

    events = engine.evaluate([observation(ok=True), observation()], now=200.0)
    assert [event.state for event in events] == [AlertState.OPEN]
