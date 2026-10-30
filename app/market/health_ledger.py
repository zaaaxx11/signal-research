"""Per-source depth health ledger: which rung is sick, without reading a log.

``DepthCollector.refresh()`` already logs one line per source, but a log line is
a fact nobody queries at 03:00 and it describes only the cycle that produced it.
This module turns the same measurement into a small document beside the store —
``data/depth.health.json``, derived with the same ``.with_suffix`` rule the
enrichment cursors use, so there is no new configuration key:

```json
{"sec_bulk": {"last_ok": "2026-10-30T03:12:44+00:00", "last_error": null,
              "ok_count": 12, "error_count": 0, "consecutive_errors": 0,
              "last_duration_s": 270.2, "last_fields": 78323}}
```

**What the numbers mean**

* ``last_ok`` is the wall clock of the last *successful* cycle. A failure never
  moves it, so ``now - last_ok`` is how long the source has been sick — that age
  is the caller's comparison to make (against its own cadence), the same rule
  ``/health`` applies to the depth store. The ledger deliberately carries no
  freshness budget: a rung that runs every 24 hours and one that runs every 5
  minutes cannot share a threshold here.
* ``consecutive_errors`` is the streak, and it is the one number a restart must
  never rewrite. Loading a ledger restores the streak as it was written: a
  source that failed three nights running must not look healthy because the
  service was deployed in between. A success, and only a success, resets it.
* ``ok`` (in :meth:`HealthLedger.summary`) is "the last outcome was a success",
  not "this source is healthy right now" — the proof of liveness lives in
  ``last_ok``.

**What it must never do**

Reading is tolerant and writing is atomic. A truncated or missing file loads as
an empty ledger; a write that cannot complete (read-only volume, full disk,
killed between ``write`` and ``replace``) leaves the previous file readable and
returns ``False`` with a WARNING. Monitoring is never allowed to become the
reason a depth cycle fails.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

logger = logging.getLogger("mee.depth.health")

#: Enough of an exception's text to identify the failure. A provider that puts a
#: whole page into its message must not be able to grow the ledger without bound.
MAX_ERROR_CHARS = 500


def _utcnow() -> datetime:
    return datetime.now(UTC)


def health_path_for(store_path: str | Path | None) -> Path | None:
    """``data/depth.json`` -> ``data/depth.health.json``; ``None`` stays ``None``.

    The path is derived, never configured: a second key for ``depth_store_path``
    is a second thing to get wrong in a deployment, and the cursors already set
    the precedent.
    """

    if store_path is None:
        return None
    try:
        return Path(store_path).with_suffix(".health.json")
    except ValueError:
        # A last segment with no usable suffix ("data/depth.") is a valid path
        # for the store but not for `with_suffix`. Appending keeps the collector
        # buildable: monitoring must never be the reason depth cannot start.
        return Path(f"{store_path}.health.json")


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError, OverflowError):
        # `json.loads` accepts the bare tokens `Infinity`/`NaN`; a count that
        # cannot be an int is corruption, not a number.
        return default


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            return None
    return number if math.isfinite(number) else None


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_stamp(value: Any) -> str | None:
    """An ISO-8601 stamp, or ``None`` when the text is not a datetime.

    A garbage stamp must not reach a caller as a datetime: ``ok`` is derived
    from ``last_ok``, so "unparseable" and "never succeeded" are the same answer.
    """

    text = _as_text(value)
    if text is None:
        return None
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return stamp.isoformat()


@dataclass(slots=True)
class SourceHealth:
    """One source's lifetime counters and its last outcome.

    ``last_duration_s``/``last_fields`` are the *last success's* measurements:
    a failed cycle has no field count worth reporting, and keeping the previous
    one makes a partial rung visible instead of blank.
    """

    name: str
    last_ok: str | None = None
    last_error: str | None = None
    ok_count: int = 0
    error_count: int = 0
    consecutive_errors: int = 0
    last_duration_s: float | None = None
    last_fields: int | None = None

    @classmethod
    def from_mapping(cls, name: str, raw: Mapping[str, Any]) -> "SourceHealth":
        """Read one stored entry, coercing types and dropping impossible values."""

        return cls(
            name=name,
            last_ok=_as_stamp(raw.get("last_ok")),
            last_error=_as_text(raw.get("last_error")),
            ok_count=max(0, _as_int(raw.get("ok_count"))),
            error_count=max(0, _as_int(raw.get("error_count"))),
            consecutive_errors=max(0, _as_int(raw.get("consecutive_errors"))),
            last_duration_s=_as_float(raw.get("last_duration_s")),
            last_fields=(
                None
                if raw.get("last_fields") is None
                else max(0, _as_int(raw.get("last_fields")))
            ),
        )

    @property
    def empty(self) -> bool:
        """Nothing was ever recorded: the entry carries no evidence at all."""

        return (
            self.last_ok is None
            and self.last_error is None
            and self.ok_count == 0
            and self.error_count == 0
            and self.consecutive_errors == 0
            and self.last_duration_s is None
            and self.last_fields is None
        )

    def document(self) -> dict[str, Any]:
        """Everything the next process needs to continue the story."""

        return {
            "name": self.name,
            "last_ok": self.last_ok,
            "last_error": self.last_error,
            "ok_count": self.ok_count,
            "error_count": self.error_count,
            "consecutive_errors": self.consecutive_errors,
            "last_duration_s": self.last_duration_s,
            "last_fields": self.last_fields,
        }

    def summary(self) -> dict[str, Any]:
        """The public view: what a monitoring scrape needs, nothing else."""

        return {
            # "The last outcome was a success" — liveness is `last_ok`'s age.
            "ok": self.consecutive_errors == 0 and self.last_ok is not None,
            "last_ok": self.last_ok,
            "last_error": self.last_error,
            "consecutive_errors": self.consecutive_errors,
            "last_duration_s": self.last_duration_s,
        }


class HealthLedger:
    """Per-source health, loaded beside the store and written atomically."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else None
        self._clock = clock or _utcnow
        self._sources: dict[str, SourceHealth] = self._load()

    @classmethod
    def beside_store(
        cls,
        store_path: str | Path | None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> "HealthLedger":
        """The ledger that belongs to one store path."""

        return cls(health_path_for(store_path), clock=clock)

    # -- reading -----------------------------------------------------------

    def _load(self) -> dict[str, SourceHealth]:
        """Read the previous ledger; every failure is an empty ledger, not a raise."""

        if self.path is None:
            return {}
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeDecodeError) as exc:
            # A directory, a permission wall, a vanished mount, or binary
            # garbage where JSON should be: monitoring cannot be the thing that
            # stops a cycle, so the counters restart instead.
            logger.warning("depth health ledger unreadable (%s): %s", self.path, exc)
            return {}
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            logger.warning(
                "depth health ledger is corrupt (%s): %s; starting empty",
                self.path,
                exc,
            )
            return {}
        if not isinstance(payload, Mapping):
            logger.warning(
                "depth health ledger is not an object (%s); starting empty", self.path
            )
            return {}
        loaded: dict[str, SourceHealth] = {}
        for raw_name, raw_entry in payload.items():
            name = str(raw_name).strip()
            if not name or not isinstance(raw_entry, Mapping):
                continue
            record = SourceHealth.from_mapping(name, raw_entry)
            # An entry that decodes to nothing is not a source this ledger has
            # seen; keeping it would create a healthy-looking rung out of a
            # corrupt row.
            if record.empty:
                continue
            loaded[name] = record
        return loaded

    def __len__(self) -> int:
        return len(self._sources)

    def get(self, name: str) -> SourceHealth | None:
        return self._sources.get(str(name or "").strip())

    def names(self) -> list[str]:
        return sorted(self._sources)

    def retain(self, names: Iterable[str]) -> list[str]:
        """Forget sources that are no longer configured; return the dropped names.

        A disabled rung must not be reported as permanently sick, and its stale
        ``last_ok`` must not be mistaken for a live measurement.
        """

        keep = {str(name or "").strip() for name in names}
        dropped = sorted(name for name in self._sources if name not in keep)
        for name in dropped:
            del self._sources[name]
        return dropped

    # -- recording ---------------------------------------------------------

    def _entry(self, name: str) -> SourceHealth:
        key = str(name or "").strip()
        if not key:
            raise ValueError("source name must be non-empty")
        record = self._sources.get(key)
        if record is None:
            record = SourceHealth(name=key)
            self._sources[key] = record
        return record

    def record_ok(
        self,
        name: str,
        *,
        duration_s: float,
        fields: int,
        at: datetime | None = None,
    ) -> SourceHealth:
        """One successful cycle for one source: the streak ends here."""

        record = self._entry(name)
        record.ok_count += 1
        record.consecutive_errors = 0
        # The old error is history: a recovered source must not keep showing the
        # text that made an alert fire yesterday.
        record.last_error = None
        record.last_ok = (at or self._clock()).isoformat()
        record.last_duration_s = round(float(duration_s), 3)
        record.last_fields = int(fields)
        return record

    def record_error(self, name: str, *, error: str) -> SourceHealth:
        """One failing cycle for one source: the streak grows.

        ``last_ok`` is untouched on purpose — its age is the only evidence of how
        long this source has been failing.
        """

        record = self._entry(name)
        record.error_count += 1
        record.consecutive_errors += 1
        text = str(error or "unknown error").strip() or "unknown error"
        record.last_error = text[:MAX_ERROR_CHARS]
        return record

    # -- publishing --------------------------------------------------------

    def summary(self) -> dict[str, dict[str, Any]]:
        """Sorted so two scrapes of the same state diff to nothing."""

        return {name: self._sources[name].summary() for name in sorted(self._sources)}

    def document(self) -> dict[str, dict[str, Any]]:
        return {name: self._sources[name].document() for name in sorted(self._sources)}

    def persist(self) -> bool:
        """Write the ledger atomically; ``False`` means "not written", never "raise".

        tmp file + ``fsync`` + ``os.replace`` is the same bargain the depth store
        makes: a crash at any point leaves either the old document or the new
        one, never half of either.
        """

        if self.path is None:
            return False
        target = self.path
        encoded = json.dumps(
            self.document(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        temporary = target.with_name(f".{target.name}.tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            _fsync_directory(target.parent)
        except OSError as exc:
            logger.warning("depth health ledger write failed (%s): %s", target, exc)
            return False
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return True


def _fsync_directory(directory: Path) -> None:
    """Make the rename itself durable; a filesystem that refuses is not an error."""

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


__all__ = [
    "MAX_ERROR_CHARS",
    "HealthLedger",
    "SourceHealth",
    "health_path_for",
]
