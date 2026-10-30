"""The daily canary: a real run of the data path, compared with yesterday.

Every other check in this deployment reads a file a *previous* cycle wrote, so a
fetch that has quietly rotted — an upstream changed its JSON, a source answers
zero rows, a parser yields ``None`` — leaves plausible files behind for a day.
This module is the small end-to-end run that notices before a customer does: it
loads the snapshot, parses the depth store, ages it, asks CNBC for five real
prices, asks Yahoo for three, reads the per-source ledger, and only then
decides. One failing check never stops the others: a missing snapshot must not
hide a dead store.

Two rules shape the rest of the file.

* **The canary is out-of-process.** It is a systemd oneshot beside the service,
  not a thread inside it, so it cannot ask the service's in-memory collector
  anything. It reads the same files the service wrote and calls the same public
  fetchers; it never claims a runtime object is missing, for the same reason
  ``build_health_report(..., observe_runtime=False)`` exists.
* **Absence is not a value.** An empty store has no median (a ``fail``, not
  ``0``), a first run has no previous report (``first_run``, not ``ok``), and a
  fetcher that answers nothing has not proved egress. Each of those is the shape
  a naive implementation reports as healthy, so each has a test.

**Budgets.** ``canary_timeout_s`` bounds one upstream call and
``canary_budget_s`` bounds the network time of the whole run. A synchronous
fetcher cannot be cancelled from the outside, so the call runs in a daemon
thread and the join carries the deadline: the canary returns on time even when
the fetch never does, and the process can exit without waiting for it.

**Deploy.** ``deploy/ztockz-canary.timer`` runs this once a day at 04:15 UTC.
Its unit differs from the retention/backup pair on purpose: it orders after
``network-online.target`` (its whole job is an outbound call, so the network
stack being up is not enough) and states ``Restart=no`` explicitly, because a
failing check is the finding — a restart loop would re-hit both upstreams every
few seconds and bury the one line an operator needs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, TypeVar

from app.config import Settings, get_settings
from app.engine.store import EngineStore, SnapshotError
from app.market.bulk import CnbcBatchSource
from app.market.depth_fields import CATALOG_FIELDS
from app.market.health_ledger import HealthLedger, health_path_for
from app.market.yahoo_batch import YahooBatchQuoteSource
from app.ops.alerts import format_event
from app.ops.alerting import (
    AlertEngine,
    AlertStateStore,
    Observation,
    Severity,
)
from app.ops.sinks import Sink, build_sink

#: Bumped only when the report/state shape changes incompatibly. A reader that
#: does not know a version treats it as ``first_run`` — re-measuring is
#: recoverable, inventing a baseline is not.
SCHEMA_VERSION = 1

#: One stable key per check. The check name, not the detail, is the identity:
#: "store" failing twice is one open alert, and a new check failing is a new one.
KEY_CHECK_PREFIX = "canary.check."
KEY_DRIFT = "canary.drift"

#: The checks in report order. The network probes come after the local reads so
#: a filesystem failure is on the page before the budget is spent on Yahoo.
CHECK_NAMES: tuple[str, ...] = (
    "snapshot",
    "store",
    "freshness",
    "quote",
    "depth_source",
    "ledger",
)

#: The quote probe: five liquid names, four prices required. One symbol missing
#: is an upstream that drops a row; three is an upstream that is not answering.
QUOTE_SAMPLE = 5
QUOTE_MIN_PRICES = 4

#: The egress probe. Yahoo is a different wire path from CNBC, so "rows came
#: back" proves the whole egress story that one host cannot: a proxy or firewall
#: may allow CNBC and block Yahoo.
DEPTH_SOURCE_SAMPLE = 3

#: Two consecutive failed cycles is a rung that stopped answering, not a flaky
#: chunk. It mirrors ``alert_ledger_error_threshold``'s default but is fixed
#: here: the canary reports the deployment's rule, it does not retune it.
LEDGER_ERROR_STREAK = 2

#: An mtime this far in the future is clock skew, not freshness. Filesystem
#: granularity is milliseconds; anything beyond a few minutes means the writer's
#: clock and the reader's disagree, which makes every age comparison a guess.
FUTURE_TOLERANCE_S = 300.0

#: Fallback probe symbols. Kept here as well as in Settings so an empty or
#: malformed configuration cannot shrink the sample to zero and pass by asking
#: nothing.
DEFAULT_QUOTE_SYMBOLS: tuple[str, ...] = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL")

T = TypeVar("T")

#: The one rename the atomic writers use. A module-level alias, not a local
#: ``os.replace`` call, so a test can make the rename fail and prove the
#: previous document survives — the failure mode the temp-file dance exists for.
_atomic_replace = os.replace


# ---------------------------------------------------------------------------
# value objects
# ---------------------------------------------------------------------------


class Status(StrEnum):
    """How one check, or the whole run, came out.

    ``StrEnum`` so a report loaded from JSON compares equal to the enum and a
    log line can print the word without a lookup table.
    """

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class DriftStatus(StrEnum):
    """Whether today can be compared with yesterday — and if not, why.

    ``FIRST_RUN`` is deliberately not ``OK``: no previous measurement is not
    evidence that today's value is good, and a report that says "ok" would make
    the first day of every deployment look like a healthy trend.
    """

    FIRST_RUN = "first_run"
    OK = "ok"
    WARN = "warn"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One check's verdict, its own wall-clock cost, and the numbers behind it.

    ``reason`` is a short machine code (``stale``, ``below_floor``, ``timeout``)
    that survives rewording the human sentence; it is what the alert
    fingerprint is built from, so an improvement in prose does not re-page.
    """

    name: str
    status: Status
    detail: str
    seconds: float = 0.0
    reason: str = ""
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        if not name:
            raise ValueError("a check name must be non-empty")
        if not isinstance(self.status, Status):
            object.__setattr__(self, "status", Status(str(self.status).lower()))
        seconds = float(self.seconds)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("seconds must be a finite, non-negative number")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "seconds", seconds)
        object.__setattr__(self, "detail", str(self.detail or "").strip())
        object.__setattr__(self, "reason", str(self.reason or "").strip())
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))

    @property
    def ok(self) -> bool:
        return self.status is Status.PASS

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "detail": self.detail,
            "seconds": round(self.seconds, 6),
            "reason": self.reason,
            # MappingProxyType is not JSON-serialisable; the wire form is a copy.
            "metrics": dict(self.metrics),
        }


@dataclass(frozen=True, slots=True)
class Drift:
    """Today against yesterday, or the honest statement that there is no yesterday."""

    status: DriftStatus
    detail: str
    previous_median_fields: float | None = None
    median_fields: float | None = None
    delta: float | None = None
    new_failures: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, DriftStatus):
            object.__setattr__(self, "status", DriftStatus(str(self.status).lower()))
        object.__setattr__(self, "detail", str(self.detail or "").strip())
        object.__setattr__(
            self, "new_failures", tuple(str(name) for name in self.new_failures)
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "detail": self.detail,
            "previous_median_fields": self.previous_median_fields,
            "median_fields": self.median_fields,
            "delta": self.delta,
            "new_failures": list(self.new_failures),
        }


@dataclass(frozen=True, slots=True)
class CanaryReport:
    """Everything one run measured, in the order it was measured."""

    started_at: datetime
    seconds: float
    symbols: int
    checks: tuple[CheckResult, ...]
    drift: Drift

    def __post_init__(self) -> None:
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("started_at must be timezone-aware")
        seconds = float(self.seconds)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("seconds must be a finite, non-negative number")
        object.__setattr__(self, "seconds", seconds)
        object.__setattr__(self, "symbols", int(self.symbols))
        object.__setattr__(self, "checks", tuple(self.checks))

    def check(self, name: str) -> CheckResult | None:
        """The named check, or ``None`` — never a synthesized pass."""
        for result in self.checks:
            if result.name == name:
                return result
        return None

    @property
    def finished_at(self) -> datetime:
        return self.started_at + timedelta(seconds=self.seconds)

    @property
    def failing(self) -> tuple[str, ...]:
        return tuple(result.name for result in self.checks if result.status is Status.FAIL)

    @property
    def warning(self) -> tuple[str, ...]:
        return tuple(result.name for result in self.checks if result.status is Status.WARN)

    @property
    def median_fields(self) -> float | None:
        store = self.check("store")
        value = store.metrics.get("median_fields") if store is not None else None
        return _as_measurement(value)

    @property
    def verdict(self) -> Status:
        """The run's headline: any ``fail`` wins, then any ``warn`` (and drift)."""
        statuses = {result.status for result in self.checks}
        if Status.FAIL in statuses:
            return Status.FAIL
        if Status.WARN in statuses or self.drift.status is DriftStatus.WARN:
            return Status.WARN
        return Status.PASS

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "seconds": round(self.seconds, 6),
            "symbols": self.symbols,
            "verdict": self.verdict.value,
            "checks": {result.name: result.as_dict() for result in self.checks},
            "drift": self.drift.as_dict(),
        }

    def as_state(self) -> dict[str, Any]:
        """The small projection tomorrow compares itself with — not the report."""
        return {
            "schema": SCHEMA_VERSION,
            "written_at": self.finished_at.isoformat(),
            "verdict": self.verdict.value,
            "median_fields": self.median_fields,
            "checks": {result.name: result.status.value for result in self.checks},
        }


# ---------------------------------------------------------------------------
# reading values without inventing them
# ---------------------------------------------------------------------------


def _as_measurement(value: Any) -> float | None:
    """A finite, non-negative number, or ``None`` — never ``0`` for "missing"."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _usable_value(value: Any) -> bool:
    """Whether a stored depth cell is a value a reader could use.

    ``None`` and ``""`` are absences the store writes on purpose; a list, a
    dict, ``NaN`` or a blank string is a parser producing garbage. Counting a
    garbage cell as coverage would let a broken merge look full, which is the
    lie this check exists to catch.
    """

    if value is None:
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, str):
        return bool(value.strip())
    return False


def _filled_fields(fields: Any) -> int:
    """How many *catalog* columns in one row hold a usable value."""

    if not isinstance(fields, Mapping):
        return 0
    return sum(
        1
        for name, value in fields.items()
        if str(name) in CATALOG_FIELDS and _usable_value(value)
    )


def _price_of(value: Any) -> float | None:
    """A positive price out of a ``SourceQuote``, a Yahoo row, or a bare number."""

    if value is None:
        return None
    if isinstance(value, Mapping):
        value = value.get("price", value.get("last"))
    else:
        value = getattr(value, "last", value)
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _quote_symbols(settings: Settings) -> tuple[str, ...]:
    """Exactly five probe symbols, padded from the defaults when configured short.

    A short configuration must not silently shrink the sample: "4 of 5" with a
    sample of two is a check that passes by asking nothing.
    """

    raw = str(settings.canary_quote_symbols or "").replace(";", ",")
    tokens = [token.strip().upper() for token in raw.split(",") if token.strip()]
    ordered = list(dict.fromkeys(tokens))
    for fallback in DEFAULT_QUOTE_SYMBOLS:
        if len(ordered) >= QUOTE_SAMPLE:
            break
        if fallback not in ordered:
            ordered.append(fallback)
    return tuple(ordered[:QUOTE_SAMPLE])


# ---------------------------------------------------------------------------
# the bounded call
# ---------------------------------------------------------------------------


class _BudgetExhausted(RuntimeError):
    """The run's total budget is gone before this check could start."""


def _remaining(settings: Settings, deadline: float) -> float:
    """The slice this call may take: the per-call cap, or what the run has left."""

    left = deadline - time.monotonic()
    if left <= 0:
        raise _BudgetExhausted("the canary's total budget is exhausted")
    return min(float(settings.canary_timeout_s), left)


def _bounded(fn: Callable[[], T], timeout_s: float) -> T:
    """Run ``fn`` and stop waiting after ``timeout_s``; raise ``TimeoutError``.

    A daemon thread, not a pool: a provider that ignores cancellation must not
    be able to keep the process alive after the canary has already reported it
    dead. The thread may finish later and its result is discarded.
    """

    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise _BudgetExhausted("the canary's total budget is exhausted")
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            box["error"] = exc

    thread = threading.Thread(target=_target, name="canary-fetch", daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        raise TimeoutError(f"no answer within {timeout_s:.2f}s")
    error = box.get("error")
    if error is not None:
        raise error
    return box.get("value")  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# the checks — each one answers, none of them raises on purpose
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Outcome:
    """One check's raw answer before it is timed and named."""

    status: Status
    detail: str
    reason: str
    metrics: Mapping[str, Any] = field(default_factory=dict)


def _check_snapshot(settings: Settings) -> _Outcome:
    """``EngineStore.load`` verifies the digest; then the universe must be real."""

    raw = settings.health_snapshot_path
    if raw is None:
        return _Outcome(
            Status.FAIL,
            "no snapshot is configured (MEE_ENGINE_SNAPSHOT); the engine has nothing to serve",
            "not_configured",
        )
    path = Path(raw).expanduser()
    try:
        dataset = EngineStore(path).load()
    except SnapshotError as exc:
        return _Outcome(Status.FAIL, f"{exc.code}: {exc}", exc.code)
    except OSError as exc:  # defensive: EngineStore already wraps its reads
        return _Outcome(Status.FAIL, f"{type(exc).__name__}: {exc}", "snapshot_unreadable")

    count = len(dataset.security_master)
    floor = max(1, int(settings.canary_min_securities))
    metrics = {"securities": count, "floor": floor, "path": str(path)}
    if count < floor:
        return _Outcome(
            Status.FAIL,
            f"{path.name} verified but holds {count} securities; the floor is {floor}",
            "below_floor",
            metrics,
        )
    return _Outcome(
        Status.PASS,
        f"{path.name} verified: {count} securities, digest ok",
        "ok",
        metrics,
    )


def _check_store(settings: Settings, symbols: int) -> _Outcome:
    """The depth store parses, holds enough symbols, and covers enough columns."""

    path = Path(settings.depth_store_path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _Outcome(
            Status.FAIL, f"{path} does not exist; the collector has written nothing", "missing"
        )
    except (OSError, UnicodeDecodeError) as exc:
        return _Outcome(Status.FAIL, f"{path} cannot be read: {exc}", "unreadable")
    try:
        payload = json.loads(text)
    except ValueError as exc:
        return _Outcome(Status.FAIL, f"{path} is not valid JSON: {exc}", "invalid")
    if not isinstance(payload, Mapping):
        return _Outcome(Status.FAIL, f"{path} is not a JSON object", "invalid")
    rows = payload.get("symbols")
    if not isinstance(rows, Mapping):
        return _Outcome(Status.FAIL, f"{path} has no 'symbols' object", "invalid")

    entries = len(rows)
    floor = max(1, int(settings.canary_min_median_fields))
    metrics: dict[str, Any] = {
        "symbols": entries,
        "median_fields": None,
        "floor": floor,
        "required_symbols": symbols,
        "path": str(path),
    }
    if entries == 0:
        # Zero rows has no median. Reporting 0 would compare equal to a store
        # whose rows are empty, and both are failures; reporting None says why.
        return _Outcome(
            Status.FAIL,
            f"{path.name} holds no symbols; a median over zero rows is not a measurement",
            "empty",
            metrics,
        )
    if entries < symbols:
        return _Outcome(
            Status.FAIL,
            f"{path.name} holds {entries} symbols; the probe needs at least {symbols}",
            "too_few_symbols",
            metrics,
        )

    median = float(statistics.median([_filled_fields(fields) for fields in rows.values()]))
    metrics["median_fields"] = median
    if median < floor:
        return _Outcome(
            Status.FAIL,
            f"{path.name} median coverage {median:g} catalog fields; the floor is {floor}",
            "below_floor",
            metrics,
        )
    return _Outcome(
        Status.PASS,
        f"{path.name}: {entries} symbols, median {median:g} catalog fields (floor {floor})",
        "ok",
        metrics,
    )


def _check_freshness(settings: Settings, now_epoch: float) -> _Outcome:
    """The store's mtime against twice the depth cadence. Late is a warning."""

    path = Path(settings.depth_store_path)
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return _Outcome(
            Status.WARN, f"{path.name} is missing, so it has no age to measure", "missing"
        )
    except OSError as exc:
        return _Outcome(Status.WARN, f"{path} cannot be stat'ed: {exc}", "unreadable")

    budget = 2.0 * float(settings.depth_refresh_s)
    age = float(now_epoch) - float(mtime)
    metrics = {"age_s": age, "budget_s": budget, "mtime": mtime, "path": str(path)}
    if age < -FUTURE_TOLERANCE_S:
        return _Outcome(
            Status.WARN,
            f"{path.name} is dated {-age:.0f}s in the future; the writer's clock disagrees",
            "future",
            metrics,
        )
    if age > budget:
        return _Outcome(
            Status.WARN,
            f"{path.name} is {age:.0f}s old; the budget is {budget:.0f}s "
            f"(2x the {float(settings.depth_refresh_s):.0f}s cadence)",
            "stale",
            metrics,
        )
    return _Outcome(
        Status.PASS, f"{path.name} is {age:.0f}s old; the budget is {budget:.0f}s", "ok", metrics
    )


def _check_quote(
    settings: Settings,
    fetcher: Callable[[Sequence[str], float], Mapping[str, Any]],
    deadline: float,
) -> _Outcome:
    """Five real CNBC prices; four non-null is the pass line."""

    sample = _quote_symbols(settings)
    if len(sample) < QUOTE_SAMPLE:  # defensive: _quote_symbols pads to five
        return _Outcome(
            Status.FAIL,
            f"the quote probe resolved {len(sample)} symbol(s); {QUOTE_SAMPLE} are required",
            "no_symbols",
        )
    metrics: dict[str, Any] = {
        "sample": list(sample),
        "priced": 0,
        "required": QUOTE_MIN_PRICES,
    }
    try:
        slice_s = _remaining(settings, deadline)
        answers = _bounded(lambda: fetcher(sample, slice_s), slice_s)
    except _BudgetExhausted:
        return _Outcome(
            Status.FAIL,
            "the run's total budget was exhausted before the quote probe",
            "budget",
            metrics,
        )
    except TimeoutError as exc:
        return _Outcome(
            Status.FAIL, f"the CNBC batch did not answer: {exc}", "timeout", metrics
        )
    except Exception as exc:  # noqa: BLE001 - a provider failure is this check's verdict
        return _Outcome(
            Status.FAIL,
            f"the CNBC batch failed: {type(exc).__name__}: {exc}",
            "fetch_error",
            metrics,
        )
    if not isinstance(answers, Mapping):
        return _Outcome(
            Status.FAIL,
            f"the CNBC fetcher returned a {type(answers).__name__}, not a mapping",
            "fetch_error",
            metrics,
        )

    priced = sum(1 for symbol in sample if _price_of(answers.get(symbol)) is not None)
    metrics["priced"] = priced
    if priced < QUOTE_MIN_PRICES:
        return _Outcome(
            Status.FAIL,
            f"{priced} of {len(sample)} CNBC prices parsed; {QUOTE_MIN_PRICES} are required",
            "too_few_prices",
            metrics,
        )
    return _Outcome(Status.PASS, f"{priced} of {len(sample)} CNBC prices parsed", "ok", metrics)


def _check_depth_source(
    settings: Settings,
    fetcher: Callable[[Sequence[str], float], Mapping[str, Any]],
    deadline: float,
) -> _Outcome:
    """One real Yahoo batch call: the second rung's egress, proven on its own wire."""

    sample = _quote_symbols(settings)[:DEPTH_SOURCE_SAMPLE]
    if len(sample) < DEPTH_SOURCE_SAMPLE:  # defensive, as above
        return _Outcome(
            Status.FAIL,
            f"the egress probe resolved {len(sample)} symbol(s); "
            f"{DEPTH_SOURCE_SAMPLE} are required",
            "no_symbols",
        )
    metrics: dict[str, Any] = {"sample": list(sample), "priced": 0}
    try:
        slice_s = _remaining(settings, deadline)
        rows = _bounded(lambda: fetcher(sample, slice_s), slice_s)
    except _BudgetExhausted:
        return _Outcome(
            Status.FAIL,
            "the run's total budget was exhausted before the egress probe",
            "budget",
            metrics,
        )
    except TimeoutError as exc:
        return _Outcome(
            Status.FAIL, f"the Yahoo batch did not answer: {exc}", "timeout", metrics
        )
    except Exception as exc:  # noqa: BLE001 - a provider failure is this check's verdict
        return _Outcome(
            Status.FAIL,
            f"the Yahoo batch failed: {type(exc).__name__}: {exc}",
            "fetch_error",
            metrics,
        )
    if not isinstance(rows, Mapping):
        return _Outcome(
            Status.FAIL,
            f"the Yahoo fetcher returned a {type(rows).__name__}, not a mapping",
            "fetch_error",
            metrics,
        )

    priced = sum(1 for symbol in sample if _price_of(rows.get(symbol)) is not None)
    metrics["priced"] = priced
    if priced == 0:
        return _Outcome(
            Status.FAIL,
            f"Yahoo answered no priced row for {', '.join(sample)}; "
            "egress or the batch protocol is broken",
            "no_rows",
            metrics,
        )
    if priced < len(sample):
        return _Outcome(
            Status.WARN,
            f"{priced} of {len(sample)} Yahoo rows priced; egress works but the source is thin",
            "partial",
            metrics,
        )
    return _Outcome(Status.PASS, f"{priced} Yahoo rows priced — egress works", "ok", metrics)


def _check_ledger(settings: Settings) -> _Outcome:
    """Every source has succeeded at least once and none is on a failing streak."""

    if not bool(settings.depth):
        return _Outcome(
            Status.WARN,
            "depth is disabled; there is no per-source ledger to read",
            "disabled",
        )
    path = health_path_for(settings.depth_store_path)
    if path is None:  # defensive: health_path_for only returns None for None
        return _Outcome(Status.FAIL, "no ledger path can be derived from the store path", "missing")
    if not path.exists():
        return _Outcome(
            Status.FAIL, f"{path} does not exist; no source has recorded a cycle", "missing"
        )
    ledger = HealthLedger(path)
    names = ledger.names()
    metrics: dict[str, Any] = {
        "sources": len(names),
        "sick": 0,
        "never_succeeded": 0,
        "path": str(path),
    }
    if not names:
        return _Outcome(Status.FAIL, f"{path} holds no source entries", "empty", metrics)

    sick: list[str] = []
    never: list[str] = []
    for name in names:
        record = ledger.get(name)
        if record is None:  # pragma: no cover - names() and get() share a store
            continue
        if int(record.consecutive_errors) >= LEDGER_ERROR_STREAK:
            sick.append(name)
        elif int(record.ok_count) == 0:
            # "Not yet" is "no": the canary's question is whether every rung
            # works today, and a rung that has never answered has not proved it.
            never.append(name)
    metrics["sick"] = len(sick)
    metrics["never_succeeded"] = len(never)
    if sick:
        return _Outcome(
            Status.FAIL,
            f"{', '.join(sick)} failed {LEDGER_ERROR_STREAK}+ cycles in a row",
            "consecutive_errors",
            metrics,
        )
    if never:
        return _Outcome(
            Status.FAIL,
            f"{', '.join(never)} never recorded a successful cycle",
            "never_succeeded",
            metrics,
        )
    return _Outcome(
        Status.PASS,
        f"{len(names)} source(s) have succeeded and none is on a failing streak",
        "ok",
        metrics,
    )


# ---------------------------------------------------------------------------
# drift
# ---------------------------------------------------------------------------


def _load_previous(path: Path) -> Mapping[str, Any] | None:
    """Yesterday's projection, or ``None`` when there is nothing trustworthy."""

    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None
    try:
        document = json.loads(text)
    except ValueError:
        return None
    if not isinstance(document, Mapping):
        return None
    # A file from a future build is not a baseline this one can read. Starting
    # over costs one day of drift; misreading a shape costs a wrong delta.
    version = document.get("schema")
    if version is not None and (isinstance(version, bool) or version != SCHEMA_VERSION):
        return None
    return document


def _drift(
    previous: Mapping[str, Any] | None,
    results: Sequence[CheckResult],
    path: Path,
    threshold: int,
) -> Drift:
    """Compare today with yesterday without ever inventing yesterday's number."""

    store = next((result for result in results if result.name == "store"), None)
    current = _as_measurement(store.metrics.get("median_fields")) if store is not None else None

    if previous is None:
        return Drift(
            DriftStatus.FIRST_RUN,
            f"no previous report at {path}; nothing to compare",
            median_fields=current,
        )

    previous_median = _as_measurement(previous.get("median_fields"))
    raw_checks = previous.get("checks")
    previous_checks: Mapping[str, Any] = raw_checks if isinstance(raw_checks, Mapping) else {}
    new_failures = tuple(
        result.name
        for result in results
        if result.status is Status.FAIL and str(previous_checks.get(result.name, "")) != "fail"
    )
    delta = (
        previous_median - current
        if previous_median is not None and current is not None
        else None
    )

    problems: list[str] = []
    if delta is not None and delta > threshold:
        problems.append(
            f"median catalog fields fell from {previous_median:g} to {current:g} "
            f"(delta {delta:g} > threshold {threshold})"
        )
    if new_failures:
        problems.append("new failing check(s): " + ", ".join(new_failures))

    if problems:
        return Drift(
            DriftStatus.WARN,
            "; ".join(problems),
            previous_median,
            current,
            delta,
            new_failures,
        )

    if delta is not None:
        detail = f"median fields {current:g} (previous {previous_median:g}); no new failure"
    else:
        detail = "no median on one side to compare; no new failing check"
    return Drift(DriftStatus.OK, detail, previous_median, current, delta, new_failures)


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


def _as_clock(now: datetime | None) -> tuple[datetime, float]:
    """The run's wall clock and its epoch, with a naive value read as UTC.

    The CLI never passes ``now``; a library caller or a test may. A naive stamp
    has no other defensible reading on a UTC deployment, and refusing one would
    make the age check harder to exercise, not safer.
    """

    moment = datetime.now(UTC) if now is None else now
    if not isinstance(moment, datetime):
        raise TypeError(f"now must be a datetime, got {type(moment).__name__}")
    if moment.tzinfo is None or moment.utcoffset() is None:
        moment = moment.replace(tzinfo=UTC)
    return moment, moment.timestamp()


def _cnbc_fetcher(symbols: Sequence[str], timeout_s: float) -> Mapping[str, Any]:
    """The real CNBC batch. Synchronous interface over the source's async one."""

    async def _go() -> Mapping[str, Any]:
        return await asyncio.wait_for(
            CnbcBatchSource(timeout_s=timeout_s).fetch_many(symbols), timeout_s
        )

    return asyncio.run(_go())


def _yahoo_fetcher(symbols: Sequence[str], timeout_s: float) -> Mapping[str, Any]:
    """The real Yahoo v7 batch, on the direct exit (a proxy would hide a break)."""

    async def _go() -> Mapping[str, Any]:
        return await asyncio.wait_for(
            YahooBatchQuoteSource(timeout_s=timeout_s).fetch_many(symbols), timeout_s
        )

    return asyncio.run(_go())


def _data_root(settings: Any) -> Path:
    """The data root the rest of the deployment writes to.

    An explicit ``health_data_dir`` wins (it is the one setting that already
    names the volume); otherwise ``MEE_DATA_DIR`` decides, exactly as it does
    for the depth store and the backups.
    """

    configured = getattr(settings, "health_data_dir", None)
    if configured:
        return Path(configured)
    # The deployment rarely sets MEE_DATA_DIR: engine.env pins each store by
    # absolute path instead. The depth store's own directory is therefore the
    # best evidence of where this box keeps its data, and it is where the
    # canary's state belongs beside it.
    store = getattr(settings, "health_depth_store_path", None) or getattr(
        settings, "depth_store_path", None
    )
    if store:
        parent = Path(store).parent
        if parent.is_absolute() and str(parent) not in ("", "."):
            return parent
    from app.engine.paths import data_dir

    return data_dir()


def resolve_data_path(configured: Path | str, data_root: Path) -> Path:
    """Put a relative default under the data root instead of the process cwd.

    Every path setting in this project defaults to ``data/<name>`` so a config
    file reads well, but the deployment sets ``MEE_DATA_DIR`` and runs from the
    code directory: taken literally, ``data/canary.report.json`` would land in
    the repository while the rest of the state lives on the data volume.
    An absolute path is obeyed as given; a relative one keeps its subdirectories
    and only the leading ``data`` segment is replaced by the real root.
    """

    path = Path(configured)
    if path.is_absolute():
        return path
    parts = path.parts
    if parts and parts[0] == "data":
        parts = parts[1:]
    return data_root / Path(*parts) if parts else data_root / path.name


def run_canary(
    settings: Settings,
    *,
    symbols: int = 20,
    now: datetime | None = None,
    quote_fetcher: Callable[[Sequence[str], float], Mapping[str, Any]] | None = None,
    depth_fetcher: Callable[[Sequence[str], float], Mapping[str, Any]] | None = None,
    state_path: str | Path | None = None,
    persist: bool = True,
) -> CanaryReport:
    """Run every check once and return the report.

    A check that raises is reported as a failing check with reason
    ``exception`` — one broken measurement must not cost the other five, which
    is the whole reason the checks are independent.

    ``persist`` writes today's projection to ``state_path`` so tomorrow has
    something to compare with; the CLI turns it off and writes the state itself
    *after* delivery, so a run whose alerts never arrived does not also move the
    baseline it will be judged against.
    """

    sample = int(symbols)
    if sample < 1:
        raise ValueError(f"symbols must be at least 1, got {sample!r}")
    started, now_epoch = _as_clock(now)
    wall_started = time.perf_counter()
    deadline = time.monotonic() + float(settings.canary_budget_s)
    quote = quote_fetcher or _cnbc_fetcher
    egress = depth_fetcher or _yahoo_fetcher

    checks: tuple[tuple[str, Callable[[], _Outcome]], ...] = (
        ("snapshot", lambda: _check_snapshot(settings)),
        ("store", lambda: _check_store(settings, sample)),
        ("freshness", lambda: _check_freshness(settings, now_epoch)),
        ("quote", lambda: _check_quote(settings, quote, deadline)),
        ("depth_source", lambda: _check_depth_source(settings, egress, deadline)),
        ("ledger", lambda: _check_ledger(settings)),
    )

    results: list[CheckResult] = []
    for name, check in checks:
        check_started = time.perf_counter()
        try:
            outcome = check()
        except Exception as exc:  # noqa: BLE001 - the failure *is* this check's result
            outcome = _Outcome(
                Status.FAIL, f"{type(exc).__name__}: {exc}", "exception"
            )
        results.append(
            CheckResult(
                name=name,
                status=outcome.status,
                detail=outcome.detail,
                seconds=time.perf_counter() - check_started,
                reason=outcome.reason,
                metrics=outcome.metrics,
            )
        )

    previous_path = (
        Path(state_path)
        if state_path is not None
        else resolve_data_path(settings.canary_state_path, _data_root(settings))
    )
    drift = _drift(
        _load_previous(previous_path),
        results,
        previous_path,
        max(1, int(settings.canary_field_drop_threshold)),
    )
    report = CanaryReport(
        started_at=started,
        seconds=time.perf_counter() - wall_started,
        symbols=sample,
        checks=tuple(results),
        drift=drift,
    )
    if persist:
        write_state(report, previous_path)
    return report


# ---------------------------------------------------------------------------
# the report in the alert engine's vocabulary
# ---------------------------------------------------------------------------


def canary_observations(report: CanaryReport) -> list[Observation]:
    """One observation per check, plus drift when there is something to say.

    ``warn`` is an observation that is *not ok* on purpose: a stale store is the
    deployment being late, and the engine's job is to say it once, not to hide
    it because it is not yet fatal. A first run emits no drift observation at
    all — no opinion is not the same as a good one.
    """

    observations: list[Observation] = []
    for result in report.checks:
        observations.append(
            Observation(
                key=f"{KEY_CHECK_PREFIX}{result.name}",
                ok=result.status is Status.PASS,
                severity=Severity.CRITICAL
                if result.status is Status.FAIL
                else Severity.WARNING,
                title=f"canary {result.name}",
                detail=result.detail,
                # The status *and* the reason: a store sliding from "stale" to
                # "unparseable" is a new problem, but the same stale store is
                # one open alert, never one per run.
                fingerprint=f"{result.status.value}|{result.reason}",
            )
        )

    if report.drift.status is DriftStatus.WARN:
        delta = "none" if report.drift.delta is None else f"{report.drift.delta:g}"
        observations.append(
            Observation(
                key=KEY_DRIFT,
                ok=False,
                severity=Severity.WARNING,
                title="canary drift",
                detail=report.drift.detail,
                # The size of the fall is the identity: a deepening drop is new
                # news, a re-run of the same drop is not.
                fingerprint=f"drift|{delta}|{','.join(report.drift.new_failures)}",
            )
        )
    elif report.drift.status is DriftStatus.OK:
        observations.append(
            Observation(
                key=KEY_DRIFT,
                ok=True,
                severity=Severity.WARNING,
                title="canary drift",
                detail=report.drift.detail,
                fingerprint="drift|ok",
            )
        )
    return observations


# ---------------------------------------------------------------------------
# writing (atomic, and nowhere else)
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


def _write_atomic(path: str | Path, document: Mapping[str, Any]) -> Path:
    """Temp file + ``fsync`` + ``os.replace``: old or new, never half of either."""

    target = Path(path)
    encoded = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        _atomic_replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return target


def write_report(report: CanaryReport, path: str | Path) -> Path:
    """Write the full report atomically."""

    return _write_atomic(path, report.as_dict())


def write_state(report: CanaryReport, path: str | Path) -> Path:
    """Write the drift baseline atomically."""

    return _write_atomic(path, report.as_state())


# ---------------------------------------------------------------------------
# the CLI
# ---------------------------------------------------------------------------


def format_check(result: CheckResult) -> str:
    """One line per check: name, verdict, its own cost, the finding."""

    return (
        f"{result.name:<12} {result.status.value.upper():<5} "
        f"{result.seconds:8.3f}s  {result.detail}"
    )


def format_drift(drift: Drift) -> str:
    """The drift line is not a check; its status word is ``first_run``/``ok``/``warn``."""

    return f"{'drift':<12} {drift.status.value.upper():<5} {'':>9} {drift.detail}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.canary",
        description=(
            "Run the data path end to end once and compare it with yesterday. "
            "A loop belongs to the scheduler: run this from a systemd timer or cron."
        ),
    )
    parser.add_argument(
        "--symbols",
        type=int,
        default=None,
        metavar="N",
        help="symbols the store must hold (default: MEE_CANARY_SYMBOLS, 20)",
    )
    parser.add_argument(
        "--state",
        default=None,
        metavar="PATH",
        help="yesterday's measurement (default: MEE_CANARY_STATE_PATH)",
    )
    parser.add_argument(
        "--report",
        default=None,
        metavar="PATH",
        help="where the report is written (default: MEE_CANARY_REPORT_PATH)",
    )
    parser.add_argument(
        "--alert-state",
        default=None,
        metavar="PATH",
        help=(
            "the alert engine's memory, distinct from the drift baseline "
            "(default: MEE_CANARY_ALERT_STATE_PATH)"
        ),
    )
    parser.add_argument(
        "--sink",
        default=None,
        metavar="SPEC",
        help="where events go: file:PATH, null or recording (default: MEE_CANARY_SINK_SPEC)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the checks, deliver nothing and write nothing",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the report as one JSON document instead of lines",
    )
    return parser


def _run(args: argparse.Namespace, settings: Settings) -> int:
    """One pass; returns the exit code, never raises for a delivery problem."""

    # Built first: a bad spec must fail before anything is written, the same
    # ordering ``app.ops.alerts`` uses.
    sink: Sink = build_sink(args.sink or settings.canary_sink_spec)
    report_path = (
        Path(args.report) if args.report else resolve_data_path(settings.canary_report_path, _data_root(settings))
    )
    state_path = (
        Path(args.state) if args.state else resolve_data_path(settings.canary_state_path, _data_root(settings))
    )
    alert_state_path = (
        Path(args.alert_state)
        if args.alert_state
        else resolve_data_path(settings.canary_alert_state_path, _data_root(settings))
    )
    symbols = args.symbols if args.symbols is not None else int(settings.canary_symbols)

    report = run_canary(settings, symbols=symbols, state_path=state_path, persist=False)

    if args.json:
        print(json.dumps(report.as_dict(), sort_keys=True))
    else:
        # One line per check, then the drift line: the drift is a comparison,
        # not a check, but it is the difference between "fine" and "fine today".
        for result in report.checks:
            print(format_check(result))
        print(format_drift(report.drift))

    if args.dry_run:
        # The exit code still reports the verdict: a dry run that always exits
        # 0 would hide exactly the failure the operator is rehearsing.
        return 1 if report.verdict is Status.FAIL else 0

    write_report(report, report_path)

    store = AlertStateStore(alert_state_path)
    engine = AlertEngine(store)
    events = engine.evaluate(
        canary_observations(report),
        now=report.finished_at.timestamp(),
        persist=False,
    )
    # A green canary is silent. Resolutions of yesterday's alerts are computed
    # but not delivered, and the memory below still advances: otherwise a
    # recurrence tomorrow would dedupe against an alert nobody closed.
    deliverable = [event for event in events if report.verdict is not Status.PASS]

    failures = 0
    for event in deliverable:
        print(format_event(event), file=sys.stderr if args.json else sys.stdout)
        try:
            sink.send(event)
        except Exception as exc:  # noqa: BLE001 - one event must not lose the rest
            failures += 1
            print(
                f"canary: {sink.name} could not deliver {event.key}: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
    if failures:
        # Do not advance either memory past an event nobody received: the next
        # run must say it again, and must compare against the same baseline.
        return 1

    store.save(engine.state)
    write_state(report, state_path)
    return 1 if report.verdict is Status.FAIL else 0


def main(argv: Sequence[str] | None = None, *, settings: Settings | None = None) -> int:
    """Run once. ``settings`` is injectable so a test never reads the env."""

    parser = _build_parser()
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else list(argv))
    except SystemExit as exc:  # --help and usage errors both land here
        return int(exc.code) if isinstance(exc.code, int) else 2

    try:
        return _run(args, settings if settings is not None else get_settings())
    except (OSError, ValueError) as exc:
        # The class is kept: EACCES on the report and ENOSPC on the state are
        # different operator stories with the same exit code.
        print(f"canary: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - exercised through main([...])
    raise SystemExit(main())


__all__ = [
    "CHECK_NAMES",
    "DEFAULT_QUOTE_SYMBOLS",
    "KEY_CHECK_PREFIX",
    "KEY_DRIFT",
    "SCHEMA_VERSION",
    "CanaryReport",
    "CheckResult",
    "Drift",
    "DriftStatus",
    "Status",
    "canary_observations",
    "format_check",
    "format_drift",
    "main",
    "run_canary",
    "write_report",
    "write_state",
]
