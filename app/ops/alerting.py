"""Alert decisions with no delivery channel.

The deployment already *knows* when it is lying: ``/health`` says a store is
stale, the ledger says which rung stopped answering, the depth store says how
much of the catalog it still fills. What it cannot do is tell a human. This
module is the missing half of that sentence and nothing else — it turns facts
into :class:`AlertEvent` objects and remembers what it already said. Where an
event goes is ``app.ops.sinks``' problem, deliberately: an engine that imports a
Telegram client is an engine that has to change when the operator changes chat
apps, and this one never will.

**The transition table** (``ok`` = the observation reports health)

===================================  ==========================================
ok -> bad                            ``open``, ``count`` 1
bad -> bad, same fingerprint         nothing (dedupe); ``count`` grows silently
bad -> bad, changed fingerprint      ``open`` again, ``count`` reset to 1
bad -> ok                            ``resolved``, ``count`` = the final count
> ``flap_limit`` opens in the window  one ``open`` whose detail says flapping,
                                     then silence for that key for one window
key missing from the input           nothing — absence is not recovery
===================================  ==========================================

**What is the change signal.** The ``fingerprint``, and only the fingerprint.
``detail`` is presentation: it is the sentence a human reads, and it naturally
carries numbers that move every poll ("age 8100s" is a different string at 8101
seconds). Alerting on it would page once a cycle for a condition that has not
changed. The fingerprint is the caller's assertion about *which problem this
is*; when it changes — stale becomes unreadable, a different source fails, a
capacity limit moves — the problem mutated and the operator gets a new event
even if the words are identical. Severity and title are presentation too, on
purpose: an escalation the caller wants announced is an escalation it must put
in the fingerprint.

**Silence is not recovery.** A key that stops appearing in the observations is
left open. An input that omits a key cannot distinguish "the check no longer
applies" from "the collector that would have reported it is dead", and closing a
page on that guess is the exact bug this deployment already had — a stale thing
that reports healthy. :meth:`AlertStateStore.prune` is the only thing allowed to
forget an alert, and it forgets only resolved ones.

**Time.** ``evaluate`` never reads a clock: ``now`` is a parameter and the
constructor takes a ``clock`` for the caller that has none. Timestamps never
move backwards within an episode (``last_seen`` is monotonic), and a clock step
backwards does not age flap evidence out of the window — the alternative lets a
time-sync daemon turn a flapping key back into a pager.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger("mee.ops.alerting")

#: Bumped only when the on-disk shape changes incompatibly. A file from a future
#: build is read as empty with a WARNING: re-alerting is recoverable, silently
#: misreading a shape we do not understand is not.
SCHEMA_VERSION = 1

#: Seconds in a day, for a retention expressed in days.
_DAY_S = 86_400.0


class Severity(StrEnum):
    """How loud an event is. Presentation only — it never decides a transition."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertState(StrEnum):
    """Whether the deployment is currently believed to be broken."""

    OPEN = "open"
    RESOLVED = "resolved"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _as_number(value: Any, name: str) -> float:
    """A finite float, or ``ValueError`` — a bool is not a timestamp."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} is not a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} is not finite")
    return number


def _as_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("count is not an integer")
    if value < 0:
        raise ValueError("count is negative")
    return int(value)


def _iso(stamp: float) -> str:
    """Epoch seconds as the ISO-8601 UTC stamp the JSON line carries."""

    return datetime.fromtimestamp(float(stamp), tz=UTC).isoformat()


# ---------------------------------------------------------------------------
# value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Observation:
    """One fact about one key, as the caller measured it.

    ``fingerprint`` is the identity of the problem, not of the measurement: two
    observations of the same broken store share it, and an observation of a
    *different* breakage of the same key does not. An empty fingerprint is a
    legitimate answer — it means "this key has no sub-identity", so the key
    alone is the identity.
    """

    key: str
    ok: bool
    severity: Severity = Severity.WARNING
    title: str = ""
    detail: str = ""
    fingerprint: str = ""

    def __post_init__(self) -> None:
        key = _text(self.key)
        if not key:
            raise ValueError("an observation key must be non-empty")
        object.__setattr__(self, "key", key)
        if not isinstance(self.severity, Severity):
            # A caller may pass the string from a config file or a JSON body;
            # an unknown word is refused rather than coerced to a default,
            # because "critical" spelled "emergency" must not page as a warning.
            object.__setattr__(self, "severity", Severity(_text(self.severity).lower()))
        object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(self, "title", _text(self.title))
        object.__setattr__(self, "detail", _text(self.detail))
        object.__setattr__(self, "fingerprint", _text(self.fingerprint))


@dataclass(frozen=True, slots=True)
class AlertEvent:
    """One thing worth telling a human, and everything needed to say it twice.

    ``first_seen``/``last_seen`` are epoch seconds; :meth:`as_dict` is the
    wire form (ISO-8601 UTC), which is what a sink appends.
    """

    key: str
    severity: Severity
    state: AlertState
    title: str
    detail: str
    count: int
    first_seen: float
    last_seen: float
    fingerprint: str = ""

    def as_dict(self) -> dict[str, object]:
        """The JSON-safe document a sink writes. No path, no secret, no clock."""

        return {
            "key": self.key,
            "severity": self.severity.value,
            "state": self.state.value,
            "title": self.title,
            "detail": self.detail,
            "count": self.count,
            "first_seen": _iso(self.first_seen),
            "last_seen": _iso(self.last_seen),
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class AlertRecord:
    """What must survive a restart for a key: the last thing said about it.

    ``opens`` holds the timestamps of the opens emitted inside the flap window;
    ``suppressed_until`` is the quiet period a flapping notice bought. Both are
    part of the memory: a restart in the middle of a flap must not reset the
    evidence and page again.
    """

    state: AlertState
    fingerprint: str = ""
    severity: Severity = Severity.WARNING
    title: str = ""
    detail: str = ""
    count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    opens: tuple[float, ...] = ()
    suppressed_until: float | None = None

    def to_document(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "fingerprint": self.fingerprint,
            "severity": self.severity.value,
            "title": self.title,
            "detail": self.detail,
            "count": self.count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "opens": list(self.opens),
            "suppressed_until": self.suppressed_until,
        }

    @classmethod
    def from_document(cls, raw: Mapping[str, Any]) -> "AlertRecord":
        """Read one stored record, or ``ValueError`` naming what is wrong.

        Strict on purpose: the caller (the store) drops the entry with a
        WARNING. A half-understood record is worse than none — it could claim an
        alert is resolved when it is not.
        """

        if not isinstance(raw, Mapping):
            raise ValueError("the record is not an object")
        state = AlertState(_text(raw.get("state")).lower())
        severity = Severity(_text(raw.get("severity") or Severity.WARNING.value).lower())
        raw_opens = raw.get("opens", [])
        if raw_opens is None:
            raw_opens = []
        if not isinstance(raw_opens, (list, tuple)):
            raise ValueError("opens is not a list")
        opens = tuple(sorted(_as_number(stamp, "opens[]") for stamp in raw_opens))
        raw_suppressed = raw.get("suppressed_until")
        suppressed = (
            None if raw_suppressed is None else _as_number(raw_suppressed, "suppressed_until")
        )
        return cls(
            state=state,
            fingerprint=_text(raw.get("fingerprint")),
            severity=severity,
            title=_text(raw.get("title")),
            detail=_text(raw.get("detail")),
            count=_as_count(raw.get("count", 0)),
            first_seen=_as_number(raw.get("first_seen", 0.0), "first_seen"),
            last_seen=_as_number(raw.get("last_seen", 0.0), "last_seen"),
            opens=opens,
            suppressed_until=suppressed,
        )


# ---------------------------------------------------------------------------
# the memory
# ---------------------------------------------------------------------------


def _fsync_directory(directory: Path) -> None:
    """Make the rename durable; a filesystem that refuses is not an error."""

    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class AlertStateStore:
    """One JSON document: what was last said about each key, plus a little meta.

    Reading is tolerant and writing is honest, the opposite of the depth health
    ledger (which must never break a cycle). A corrupt memory degrades to "say
    everything again" with a WARNING, because a duplicate page is recoverable
    and a silently forgotten incident is not. A write that cannot complete is
    an ``OSError`` the caller must report: pretending the state was saved would
    make the next run repeat every alert, and pretending it did not matter would
    make this run's alerts forgettable.

    ``meta`` is a small sidecar for measurements that are not alerts — the depth
    coverage high-water mark, today. It lives here so an operator has one file
    to back up, one file to delete to force a re-announcement.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._meta: dict[str, Any] = {}
        self._loaded = False

    # -- reading -----------------------------------------------------------

    def _read(self) -> Mapping[str, Any] | None:
        """Parse the document tolerantly; ``None`` means "nothing usable"."""

        if self.path is None:
            return None
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            # A first run is not a problem and must stay quiet.
            return None
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("alert state unreadable (%s): %s; starting empty", self.path, exc)
            return None
        if not text.strip():
            logger.warning("alert state is empty (%s); starting empty", self.path)
            return None
        try:
            document = json.loads(text)
        except ValueError as exc:
            logger.warning(
                "alert state is corrupt (%s): %s; starting empty", self.path, exc
            )
            return None
        if not isinstance(document, Mapping):
            logger.warning(
                "alert state is a JSON %s, not an object (%s); starting empty",
                type(document).__name__,
                self.path,
            )
            return None
        return document

    def load(self) -> dict[str, AlertRecord]:
        """Every usable record; anything unusable is a WARNING and a skipped key."""

        document = self._read()
        self._loaded = True
        self._meta = {}
        if document is None:
            return {}

        version = document.get("schema", SCHEMA_VERSION)
        if isinstance(version, bool) or version != SCHEMA_VERSION:
            logger.warning(
                "alert state schema %r is not %d (%s); starting empty",
                version,
                SCHEMA_VERSION,
                self.path,
            )
            return {}

        raw_meta = document.get("meta")
        if isinstance(raw_meta, Mapping):
            self._meta = dict(raw_meta)

        raw_alerts = document.get("alerts")
        if raw_alerts is None:
            # A file written by hand, or by a build before the envelope existed,
            # is a bare key -> record mapping. Reading it beats re-paging.
            raw_alerts = {name: value for name, value in document.items() if name != "meta"}
        if not isinstance(raw_alerts, Mapping):
            logger.warning(
                "alert state has no usable 'alerts' object (%s); starting empty", self.path
            )
            return {}

        records: dict[str, AlertRecord] = {}
        for raw_key, raw_record in raw_alerts.items():
            key = _text(raw_key)
            if not key:
                continue
            try:
                records[key] = AlertRecord.from_document(raw_record)
            except (TypeError, ValueError) as exc:
                logger.warning("alert state entry %r is unusable (%s); dropping it", key, exc)
        return records

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    @property
    def meta(self) -> dict[str, Any]:
        """The sidecar values, read once from the document on first use."""

        self._ensure_loaded()
        return dict(self._meta)

    def set_meta(self, **values: Any) -> None:
        """Record scalars that are not alerts; JSON-safe values only."""

        self._ensure_loaded()
        self._meta.update(values)

    # -- writing -----------------------------------------------------------

    def save(self, mapping: Mapping[str, AlertRecord]) -> bool:
        """Write the whole memory atomically; ``False`` only when there is nowhere to write.

        Temp file + ``fsync`` + ``os.replace``: a crash leaves the previous
        document or the new one, never half of either. Nothing here swallows an
        ``OSError`` — the CLI turns it into exit 1, because a monitor that cannot
        remember what it said is a monitor that pages every cycle.
        """

        if self.path is None:
            return False
        self._ensure_loaded()
        alerts: dict[str, object] = {}
        for key, record in sorted(mapping.items()):
            if not isinstance(record, AlertRecord):
                raise TypeError(f"alert state for {key!r} is a {type(record).__name__}")
            alerts[key] = record.to_document()
        document = {"schema": SCHEMA_VERSION, "alerts": alerts, "meta": self._meta}
        encoded = json.dumps(
            document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

        target = self.path
        temporary = target.with_name(f".{target.name}.tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            _fsync_directory(target.parent)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return True

    def prune(self, now: float, max_age_days: float) -> list[str]:
        """Forget resolved alerts older than the retention; return their keys.

        Only ``resolved`` records may go. An open alert is the record of a
        promise to a human, and age is not evidence that it was kept.
        """

        retention = float(max_age_days)
        if not math.isfinite(retention) or retention <= 0:
            raise ValueError("max_age_days must be a positive number")
        cutoff = float(now) - retention * _DAY_S
        kept: dict[str, AlertRecord] = {}
        removed: list[str] = []
        for key, record in self.load().items():
            if record.state is AlertState.RESOLVED and record.last_seen < cutoff:
                removed.append(key)
            else:
                kept[key] = record
        if removed:
            self.save(kept)
        return sorted(removed)


# ---------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------


def _flapping_detail(detail: str, opens: int, window: float) -> str:
    """The one sentence a flapping key gets until the window passes."""

    note = (
        f"flapping: {opens} open events within {window:g}s; "
        "no further alerts for this key until the window passes"
    )
    return f"{detail} — {note}" if detail else note


class AlertEngine:
    """Turn observations into at most one event per key per change of state.

    The engine is pure with respect to time (``now`` is injected, the clock is
    only a fallback) and has exactly one side effect: it saves the memory to its
    store after a pass, unless the caller asked for a memory-only pass (the
    ``--dry-run`` path).
    """

    def __init__(
        self,
        store: AlertStateStore,
        *,
        flap_window_s: float = 3_600.0,
        flap_limit: int = 3,
        clock: Callable[[], float] | None = None,
    ) -> None:
        window = float(flap_window_s)
        if not math.isfinite(window) or window <= 0:
            raise ValueError("flap_window_s must be positive")
        limit = int(flap_limit)
        if limit < 1:
            raise ValueError("flap_limit must be at least 1")
        self.store = store
        self.flap_window_s = window
        self.flap_limit = limit
        self._clock = clock or time.time
        self._records: dict[str, AlertRecord] = dict(store.load())

    @property
    def state(self) -> dict[str, AlertRecord]:
        """A copy of the memory, for a caller that persists it itself."""

        return dict(self._records)

    # -- one pass ----------------------------------------------------------

    def evaluate(
        self,
        observations: Iterable[Observation],
        now: float | None = None,
        *,
        persist: bool = True,
    ) -> list[AlertEvent]:
        """Apply one batch; return the events worth delivering, in input order.

        A key that appears twice in one batch is evaluated once, with the last
        observation winning: two truths about one key in one pass would otherwise
        produce a transition nobody observed.
        """

        moment = float(self._clock() if now is None else now)
        if not math.isfinite(moment):
            raise ValueError("now must be a finite timestamp")

        batch: dict[str, Observation] = {}
        for entry in observations:
            if not isinstance(entry, Observation):
                raise TypeError(f"expected an Observation, got {type(entry).__name__}")
            batch[entry.key] = entry

        events: list[AlertEvent] = []
        for key, entry in batch.items():
            record = self._records.get(key)
            if (
                record is not None
                and record.suppressed_until is not None
                and moment < record.suppressed_until
            ):
                # The flapping notice already spoke for this window; the key may
                # oscillate as much as it likes, the operator hears nothing. The
                # count does not grow either: this episode is not being narrated.
                continue
            if entry.ok:
                event, updated = self._resolve(key, entry, record, moment)
            elif (
                record is None
                or record.state is AlertState.RESOLVED
                or record.fingerprint != entry.fingerprint
            ):
                event, updated = self._open(key, entry, record, moment)
            else:
                # Same key, same problem, still broken: the operator already
                # knows. The count still grows — it is what a resolve reports.
                event, updated = None, self._repeat(record, moment)
            if updated is not None:
                self._records[key] = updated
            if event is not None:
                events.append(event)

        if persist:
            self.store.save(self._records)
        return events

    # -- the four transitions ---------------------------------------------

    def _window(self, opens: Iterable[float], moment: float) -> tuple[float, ...]:
        """The opens still inside the flap window, oldest first.

        ``moment - stamp <= window`` keeps a stamp in the future (a clock that
        stepped backwards) inside the window on purpose: losing that evidence
        would let a time-sync daemon turn a flapping key back into a pager.
        """

        return tuple(sorted(stamp for stamp in opens if moment - stamp <= self.flap_window_s))

    def _open(
        self,
        key: str,
        entry: Observation,
        record: AlertRecord | None,
        moment: float,
    ) -> tuple[AlertEvent, AlertRecord]:
        recent = self._window(record.opens, moment) if record is not None else ()
        # Every emitted open counts, including one caused by a changed
        # fingerprint: the rule is about how often this key woke the operator,
        # not about why it did.
        opens = len(recent) + 1
        detail = entry.detail
        suppressed_until: float | None = None
        if opens > self.flap_limit:
            detail = _flapping_detail(detail, opens, self.flap_window_s)
            # The notice buys a whole quiet window and starts a fresh history:
            # keeping the old stamps would re-arm the detector and page once per
            # window for ever, which is the noise the rule exists to prevent.
            history: tuple[float, ...] = ()
            suppressed_until = moment + self.flap_window_s
        else:
            history = tuple(sorted((*recent, moment)))
        event = AlertEvent(
            key=key,
            severity=entry.severity,
            state=AlertState.OPEN,
            title=entry.title,
            detail=detail,
            count=1,
            first_seen=moment,
            last_seen=moment,
            fingerprint=entry.fingerprint,
        )
        updated = AlertRecord(
            state=AlertState.OPEN,
            fingerprint=entry.fingerprint,
            severity=entry.severity,
            title=entry.title,
            detail=detail,
            count=1,
            first_seen=moment,
            last_seen=moment,
            opens=history,
            suppressed_until=suppressed_until,
        )
        return event, updated

    def _repeat(self, record: AlertRecord, moment: float) -> AlertRecord:
        """The same bad news again: no event, a longer episode, a newer witness."""

        return replace(
            record,
            count=record.count + 1,
            last_seen=max(record.last_seen, moment),
            opens=self._window(record.opens, moment),
        )

    def _resolve(
        self,
        key: str,
        entry: Observation,
        record: AlertRecord | None,
        moment: float,
    ) -> tuple[AlertEvent | None, AlertRecord | None]:
        if record is None or record.state is not AlertState.OPEN:
            # Nothing was open: recovery is not news, and an ok -> ok key never
            # touches the memory (so a quiet deployment writes nothing new).
            return None, None
        last_seen = max(record.last_seen, moment)
        detail = entry.detail or "the condition cleared"
        detail = f"{detail} (resolved after {record.count} observation(s))"
        event = AlertEvent(
            key=key,
            # The episode is closed as it was announced: a resolved critical is
            # still the critical the operator was paged about.
            severity=record.severity,
            state=AlertState.RESOLVED,
            title=record.title or entry.title,
            detail=detail,
            count=record.count,
            first_seen=record.first_seen,
            last_seen=last_seen,
            fingerprint=record.fingerprint,
        )
        return event, replace(record, state=AlertState.RESOLVED, last_seen=last_seen)


__all__ = [
    "SCHEMA_VERSION",
    "AlertEngine",
    "AlertEvent",
    "AlertRecord",
    "AlertState",
    "AlertStateStore",
    "Observation",
    "Severity",
]
