"""Where an alert goes — and the only module that knows.

The engine produces :class:`~app.ops.alerting.AlertEvent` objects. A *sink* is
anything with a ``name`` and a ``send(event)``: a file today, a Telegram bot, an
SMTP relay or a webhook tomorrow, each a class here and a scheme in a spec
string, none of them an edit to a transition rule.

Two rules shape the file:

* **Append, never rewrite.** An alert log is evidence. :class:`FileSink` opens in
  ``"a"``, writes one JSON object per line, and flushes and ``fsync``s before it
  returns — a page that was "sent" must survive the power cut that follows it.
  A torn last line (a kill mid-write) is terminated rather than merged, so the
  file stays parseable line by line.
* **One broken sink loses one delivery.** :class:`MultiSink` calls every sink and
  returns the failures instead of raising, so a revoked bot token cannot stop
  the file sink that the on-call rotation actually reads. ``KeyboardInterrupt``
  and ``SystemExit`` are not delivery failures and are allowed through.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.ops.alerting import AlertEvent

logger = logging.getLogger("mee.ops.sinks")


@runtime_checkable
class Sink(Protocol):
    """A delivery channel: a stable name and one method that may raise."""

    name: str

    def send(self, event: AlertEvent) -> object | None:
        """Deliver ``event``; raising is the only way to report a failure."""


@dataclass(frozen=True, slots=True)
class SinkFailure:
    """One sink that did not deliver, and why — never the event's contents."""

    sink: str
    error: str


class FileSink:
    """Append one JSON object per line; create the file on the first event.

    Constructing the sink touches nothing. A monitor that is installed but never
    fires must not leave an empty log file behind, because "the file exists" is
    how an operator decides the thing is running.
    """

    name = "file"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def send(self, event: AlertEvent) -> None:
        line = json.dumps(event.as_dict(), sort_keys=True, ensure_ascii=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A crash between `write` and `close` can leave a line without its
        # newline; appending after it would merge two events into one
        # unparseable line, so terminate the torn one first.
        torn = self._ends_mid_line()
        with self.path.open("a", encoding="utf-8") as handle:
            if torn:
                handle.write("\n")
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _ends_mid_line(self) -> bool:
        try:
            with self.path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    return False
                handle.seek(-1, os.SEEK_END)
                return handle.read(1) != b"\n"
        except FileNotFoundError:
            return False


class NullSink:
    """A real sink that delivers nothing — the ``--dry-run`` and test sink."""

    name = "null"

    def send(self, event: AlertEvent) -> None:
        return None


class RecordingSink:
    """Keep events in memory. Tests assert on this; the CLI never builds one."""

    def __init__(self, name: str = "recording") -> None:
        self.name = name
        self.events: list[AlertEvent] = []

    def send(self, event: AlertEvent) -> None:
        self.events.append(event)


class MultiSink:
    """Fan one event out to every sink, isolating each failure.

    ``send`` returns the failures rather than raising: a caller that loses the
    exception would lose the delivery for the *other* sinks too, which is the
    exact coupling this class exists to remove. The caller decides whether a
    partial delivery is an exit code (the CLI does) or a log line.
    """

    name = "multi"

    def __init__(self, sinks: Sequence[Sink]) -> None:
        self.sinks = tuple(sinks)

    def send(self, event: AlertEvent) -> list[SinkFailure]:
        failures: list[SinkFailure] = []
        for sink in self.sinks:
            try:
                sink.send(event)
            except Exception as exc:  # noqa: BLE001 - the failure *is* this sink's result
                label = str(getattr(sink, "name", "") or type(sink).__name__)
                failures.append(SinkFailure(label, f"{type(exc).__name__}: {exc}"))
                logger.warning("alert sink %s failed on %s: %s", label, event.key, exc)
        return failures


#: Scheme -> factory. Adding Telegram later is one entry here and one class
#: above; nothing else in the codebase learns a new name.
def build_sink(spec: str) -> Sink:
    """``file:PATH``, ``null`` or ``recording`` — or ``ValueError`` naming why.

    The spec is a scheme, not a config tree: an operator can write it in an env
    var, a systemd unit or a shell, and a platform that needs credentials takes
    them from its own environment inside its own adapter, never from here.
    """

    text = str(spec or "").strip()
    if not text:
        raise ValueError(
            "a sink spec is required, e.g. file:/var/log/ztockz-alerts.jsonl"
        )
    scheme, _, rest = text.partition(":")
    scheme = scheme.strip().lower()
    if scheme == "file":
        target = rest.strip()
        if not target:
            raise ValueError("a file sink needs a path: file:/var/log/alerts.jsonl")
        return FileSink(Path(target))
    if scheme == "null":
        return NullSink()
    if scheme == "recording":
        return RecordingSink()
    raise ValueError(
        f"unknown sink scheme {scheme!r} in {text!r}; known schemes: file, null, recording"
    )


__all__ = [
    "FileSink",
    "MultiSink",
    "NullSink",
    "RecordingSink",
    "Sink",
    "SinkFailure",
    "build_sink",
]
