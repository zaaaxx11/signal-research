"""Contract tests for the alert sinks (``app.ops.sinks``).

A sink is the only part of alerting that can fail for a reason the engine does
not control — a full disk, a revoked bot token, an SMTP host that hangs up. The
contract these tests pin is therefore mostly about failure: a sink that raises
must be a returned error and never a lost event for its neighbours.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ops.alerting import AlertEvent, AlertState, Severity
from app.ops.sinks import (
    FileSink,
    MultiSink,
    NullSink,
    RecordingSink,
    Sink,
    SinkFailure,
    build_sink,
)


def event(key: str = "depth_store", detail: str = "12h old") -> AlertEvent:
    return AlertEvent(
        key=key,
        severity=Severity.CRITICAL,
        state=AlertState.OPEN,
        title="the depth store is stale",
        detail=detail,
        count=1,
        first_seen=100.0,
        last_seen=200.0,
        fingerprint="stale",
    )


class Exploding:
    """A sink that always raises — the broken platform adapter of the future."""

    def __init__(self, name: str = "telegram", message: str = "401 unauthorized") -> None:
        self.name = name
        self.message = message
        self.calls: list[AlertEvent] = []

    def send(self, incoming: AlertEvent) -> None:
        self.calls.append(incoming)
        raise RuntimeError(self.message)


# ---------------------------------------------------------------------------
# FileSink
# ---------------------------------------------------------------------------


def test_file_sink_is_created_lazily(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "alerts.jsonl"
    sink = FileSink(target)
    assert not target.exists(), "constructing a sink must not create a file"

    sink.send(event())
    assert target.exists()


def test_file_sink_writes_one_json_object_per_line(tmp_path: Path) -> None:
    target = tmp_path / "alerts.jsonl"
    sink = FileSink(target)
    sink.send(event("depth_store"))
    sink.send(event("disk", detail="2.0 GiB free"))

    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    documents = [json.loads(line) for line in lines]
    assert [document["key"] for document in documents] == ["depth_store", "disk"]
    assert documents[0]["state"] == "open"
    assert documents[0]["severity"] == "critical"
    assert documents[0]["count"] == 1
    assert documents[0]["detail"] == "12h old"
    assert target.read_text(encoding="utf-8").endswith("\n")


def test_a_second_run_appends_instead_of_truncating(tmp_path: Path) -> None:
    target = tmp_path / "alerts.jsonl"
    FileSink(target).send(event("first"))
    FileSink(target).send(event("second"))

    keys = [json.loads(line)["key"] for line in target.read_text(encoding="utf-8").splitlines()]
    assert keys == ["first", "second"]


def test_file_sink_terminates_a_torn_last_line(tmp_path: Path) -> None:
    """A crash mid-write must not merge two events into one unparseable line."""

    target = tmp_path / "alerts.jsonl"
    target.write_text('{"key": "half", "state": "op', encoding="utf-8")

    FileSink(target).send(event("whole"))

    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    # The fragment is preserved verbatim (history is never rewritten), but it
    # is on its own line, so every line this sink wrote still parses.
    assert lines[0] == '{"key": "half", "state": "op'
    assert json.loads(lines[1])["key"] == "whole"


def test_file_sink_refuses_a_directory(tmp_path: Path) -> None:
    directory = tmp_path / "alerts.jsonl"
    directory.mkdir()
    with pytest.raises(OSError):
        FileSink(directory).send(event())


# ---------------------------------------------------------------------------
# the quiet sinks
# ---------------------------------------------------------------------------


def test_null_sink_discards_without_complaint() -> None:
    assert NullSink().send(event()) is None


def test_recording_sink_keeps_every_event() -> None:
    sink = RecordingSink()
    first, second = event("a"), event("b")
    sink.send(first)
    sink.send(second)
    assert sink.events == [first, second]
    assert sink.name == "recording"


# ---------------------------------------------------------------------------
# MultiSink
# ---------------------------------------------------------------------------


def test_multi_sink_delivers_to_every_sink() -> None:
    first, second = RecordingSink("one"), RecordingSink("two")
    failures = MultiSink([first, second]).send(event())

    assert failures == []
    assert len(first.events) == len(second.events) == 1


def test_multi_sink_isolates_one_raising_sink(tmp_path: Path) -> None:
    """Adversarial (d): the broken adapter loses its own event, nobody else's."""

    broken = Exploding()
    file_sink = FileSink(tmp_path / "alerts.jsonl")
    recording = RecordingSink()

    failures = MultiSink([broken, file_sink, recording]).send(event())

    assert len(failures) == 1, failures
    failure = failures[0]
    assert isinstance(failure, SinkFailure)
    assert failure.sink == "telegram"
    assert "RuntimeError" in failure.error and "401 unauthorized" in failure.error
    assert len(recording.events) == 1, "the sink after the broken one must still deliver"
    assert len((tmp_path / "alerts.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_multi_sink_reports_every_failure_not_just_the_first() -> None:
    failures = MultiSink(
        [Exploding("telegram"), Exploding("smtp", "connection refused"), RecordingSink()]
    ).send(event())

    assert [failure.sink for failure in failures] == ["telegram", "smtp"]
    assert "connection refused" in failures[1].error


def test_multi_sink_with_no_sinks_is_not_a_crash() -> None:
    assert MultiSink([]).send(event()) == []


def test_multi_sink_lets_policy_errors_through() -> None:
    """Only ``Exception`` is a delivery failure; an exit request is not one."""

    class Exiting:
        name = "exiting"

        def send(self, incoming: AlertEvent) -> None:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        MultiSink([Exiting()]).send(event())


# ---------------------------------------------------------------------------
# specs
# ---------------------------------------------------------------------------


def test_every_sink_satisfies_the_protocol() -> None:
    assert isinstance(FileSink("x.jsonl"), Sink)
    assert isinstance(NullSink(), Sink)
    assert isinstance(RecordingSink(), Sink)
    assert isinstance(MultiSink([]), Sink)


def test_build_sink_parses_the_scheme(tmp_path: Path) -> None:
    target = tmp_path / "alerts.jsonl"
    built = build_sink(f"file:{target}")
    assert isinstance(built, FileSink)
    assert built.path == target

    assert isinstance(build_sink("null"), NullSink)
    assert isinstance(build_sink("recording"), RecordingSink)
    assert isinstance(build_sink("  FILE:  " + str(target) + " "), FileSink)


@pytest.mark.parametrize("spec", ["", "   ", "file:", "telegram:12345", "smtp://mail"])
def test_build_sink_refuses_what_it_cannot_deliver(spec: str) -> None:
    with pytest.raises(ValueError):
        build_sink(spec)
