"""``python -m app.ops.alerts`` — turn the deployment's facts into alerts.

One pass, three fact sources, no HTTP and no network:

* **The ``/health`` document**, built by ``app.api.health.build_health_report``
  — the route's own builder, the route's own budgets. Shelling out to
  ``localhost/health`` would alert on the *load balancer's* view (and fail when
  the process is the thing that is down); reading it in process means the
  scheduler can watch a deployment that is not answering at all. The verdict is
  one observation and each check is another, so the operator learns both "the
  deployment is degraded" and "because the depth store is stale".
* **The depth ledger** beside the store (``depth.health.json``), which records
  the consecutive-error streak per source and survives a restart. This is the
  only place "sec_bulk has failed twice running" is written down.
* **The depth store's coverage**, counted by ``app.api.market.depth_stats`` —
  the same function the route answers with, so a number here can never disagree
  with the number there. It is compared against a high-water mark kept in the
  alert state's ``meta``: a slow slide of one field a night must still fire on
  the fifth night, which a comparison against "yesterday" would never do.

The exit code follows the deployment's convention: **0 means the run happened**,
even when it fired alerts (a fired alert is not a crashed job, and cron would
otherwise mail a failure every day the deployment is sick). **1** is a real
failure — an unwritable sink or state file, an unusable spec — and **2** is a
usage error. A sink failure also stops the state from advancing: an event
nobody received must be said again on the next run, even at the cost of a
duplicate.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.api.health import build_health_report
from app.api.market import depth_stats
from app.config import Settings, get_settings
from app.market.depth import DepthStore
from app.market.health_ledger import HealthLedger, health_path_for
from app.ops.alerting import (
    AlertEngine,
    AlertEvent,
    AlertStateStore,
    Observation,
    Severity,
)
from app.ops.sinks import Sink, build_sink

#: One key for the deployment verdict, one per check, so a change of *which*
#: check failed is a change of fingerprint rather than a silent same-status.
KEY_DEPLOYMENT = "health.deployment"
KEY_CHECK_PREFIX = "health.check."
KEY_DEPTH_SOURCE_PREFIX = "depth.source."
KEY_COVERAGE = "depth.coverage"

DEPLOYMENT_OK = "ok"
DEPLOYMENT_FAILING = "failing"

#: The statuses ``app.api.health`` uses when data cannot be trusted (as opposed
#: to merely being behind). The distinction only picks a severity label — the
#: alert fires on ``ok`` either way — so a future status word cannot silence it.
UNTRUSTED_STATUSES = frozenset({"missing", "unparseable", "invalid", "corrupt"})

#: A stored error can be a provider's whole page; the fingerprint only needs to
#: distinguish one failure from another, and the state file must stay small.
_FINGERPRINT_CHARS = 120


# ---------------------------------------------------------------------------
# the /health document
# ---------------------------------------------------------------------------


def _deployment_detail(status: str, checks: Mapping[str, Any]) -> str:
    """``status=degraded; depth_store=stale, cursors=unreadable``."""

    broken: list[str] = []
    for name, raw in sorted(checks.items(), key=lambda item: str(item[0])):
        if not isinstance(raw, Mapping) or raw.get("ok"):
            continue
        broken.append(f"{name}={raw.get('status') or 'error'}")
    detail = f"status={status}"
    return f"{detail}; {', '.join(broken)}" if broken else detail


def health_observations(report: Mapping[str, Any]) -> list[Observation]:
    """The verdict as one observation, then one per check.

    ``degraded`` is *bad* on purpose. The whole point of this engine is that a
    deployment can serve stale data while every process says "up"; a monitor
    that only alerts on ``failing`` would reproduce that blind spot one layer
    up. It is a warning rather than a critical so the two are distinguishable.
    """

    status = str(report.get("status") or "unknown").strip() or "unknown"
    raw_checks = report.get("checks")
    checks: Mapping[str, Any] = raw_checks if isinstance(raw_checks, Mapping) else {}

    observations = [
        Observation(
            key=KEY_DEPLOYMENT,
            ok=status == DEPLOYMENT_OK,
            severity=Severity.CRITICAL
            if status == DEPLOYMENT_FAILING
            else Severity.WARNING,
            title="deployment health",
            detail=_deployment_detail(status, checks),
            # The verdict is the identity: degraded -> failing is a new problem
            # and must be said again, not deduped as "still bad".
            fingerprint=status,
        )
    ]

    for name, raw in sorted(checks.items(), key=lambda item: str(item[0])):
        document: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
        check_ok = bool(document.get("ok"))
        check_status = str(document.get("status") or "unknown").strip() or "unknown"
        error = document.get("error")
        error_text = "" if error is None else str(error).strip()
        observations.append(
            Observation(
                key=f"{KEY_CHECK_PREFIX}{name}",
                ok=check_ok,
                severity=Severity.CRITICAL
                if not check_ok and check_status in UNTRUSTED_STATUSES
                else Severity.WARNING,
                title=f"health check {name}",
                detail=f"status={check_status}"
                + (f": {error_text}" if error_text else ""),
                # Same status but a different error is a different failure:
                # "stale because the cycle is slow" and "stale because the file
                # vanished" are two different things to fix.
                fingerprint=f"{check_status}|{error_text[:_FINGERPRINT_CHARS]}",
            )
        )
    return observations


# ---------------------------------------------------------------------------
# the depth ledger
# ---------------------------------------------------------------------------


def ledger_observations(settings: Settings) -> list[Observation]:
    """One observation per depth source: is its consecutive-error streak long?

    Read from the ledger, not from the last log line: the ledger restores the
    streak across a restart, so a deploy cannot make a source that failed three
    nights running look healthy. ``last_ok``'s age is deliberately not a budget
    here — a daily rung and a five-minute rung cannot share one — which is why
    the count, not the clock, is the fact.
    """

    if not bool(settings.depth):
        # A subsystem that is switched off is not a sick one, and its ledger may
        # hold streaks from a deployment that no longer runs it.
        return []

    path = health_path_for(settings.depth_store_path)
    if path is None:
        return []

    limit = max(1, int(settings.alert_ledger_error_threshold))
    ledger = HealthLedger(path)
    observations: list[Observation] = []
    for name in ledger.names():
        record = ledger.get(name)
        if record is None:
            continue
        streak = int(record.consecutive_errors)
        error = (record.last_error or "").strip()
        detail = (
            f"{streak} consecutive failed cycle(s); "
            f"last success {record.last_ok or 'never'}"
        )
        if error:
            detail = f"{detail}: {error}"
        observations.append(
            Observation(
                key=f"{KEY_DEPTH_SOURCE_PREFIX}{name}",
                ok=streak < limit,
                severity=Severity.WARNING,
                title=f"depth source {name}",
                detail=detail,
                # The error text is the identity of the failure: a 403 becoming
                # a timeout is a different thing to fix, so it is new news even
                # though the source and the streak are unchanged.
                fingerprint=f"{name}|{error[:_FINGERPRINT_CHARS]}",
            )
        )
    return observations


# ---------------------------------------------------------------------------
# the depth store's coverage
# ---------------------------------------------------------------------------


def coverage_observation(
    settings: Settings, baseline: int | None
) -> tuple[Observation | None, int | None]:
    """How much of the catalog is filled, against the high-water mark.

    ``None`` as the observation means "no opinion": the store is disabled or
    unreadable, and ``/health`` already reports an unreadable store. Emitting a
    second observation for one fact would double every depth incident.

    The baseline returned is the high-water mark, never the latest value. A
    drop of five fields spread over five nights is a source that stopped
    answering; comparing against yesterday would see five one-field wobbles.
    """

    if not bool(settings.depth):
        return None, baseline

    path = Path(settings.depth_store_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None, baseline
    if not isinstance(payload, Mapping):
        return None, baseline

    store = DepthStore()
    store.seed(payload)
    document = depth_stats(store)
    filled = int(document.get("fields_filled") or 0)
    limit = max(1, int(settings.alert_field_drop_threshold))

    if baseline is None:
        return (
            Observation(
                key=KEY_COVERAGE,
                ok=True,
                severity=Severity.WARNING,
                title="depth coverage",
                detail=f"{filled} catalog fields filled; no baseline yet",
                fingerprint=str(filled),
            ),
            filled,
        )

    drop = int(baseline) - filled
    return (
        Observation(
            key=KEY_COVERAGE,
            ok=drop < limit,
            severity=Severity.WARNING,
            title="depth coverage",
            detail=(
                f"{filled} catalog fields filled; high-water {int(baseline)}; "
                f"drop {drop} (threshold {limit})"
            ),
            # A new low is a new problem: 97 filled and 91 filled are different
            # states of the store, so a further slide is said again.
            fingerprint=str(filled),
        ),
        max(int(baseline), filled),
    )


# ---------------------------------------------------------------------------
# the batch
# ---------------------------------------------------------------------------


def build_observations(
    settings: Settings, *, baseline: int | None = None
) -> tuple[list[Observation], int | None]:
    """Every fact this pass could observe, and the coverage high-water mark."""

    observations = health_observations(build_health_report(settings, observe_runtime=False))
    observations.extend(ledger_observations(settings))
    coverage, high_water = coverage_observation(settings, baseline)
    if coverage is not None:
        observations.append(coverage)
    return observations, high_water


# ---------------------------------------------------------------------------
# the CLI
# ---------------------------------------------------------------------------


def format_event(event: AlertEvent) -> str:
    """One line: when, which state, how loud, what — in that order, for a log scan."""

    stamp = datetime.fromtimestamp(event.last_seen, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"{stamp} {event.state.value.upper():<8} {event.severity.value:<8} "
        f"{event.key} {event.title}: {event.detail} (count={event.count})"
    )


def _baseline(raw: object) -> int | None:
    """A stored high-water mark, or ``None`` when there is not one to trust."""

    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return None
    return raw


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.alerts",
        description=(
            "Evaluate the deployment's health once and dispatch the events it implies. "
            "A loop belongs to the scheduler: run this from cron or a systemd timer."
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        required=True,
        help="evaluate once and exit (the only mode this tool implements)",
    )
    parser.add_argument(
        "--sink",
        default=None,
        metavar="SPEC",
        help="where events go: file:PATH, null or recording (default: MEE_ALERT_SINK_SPEC)",
    )
    parser.add_argument(
        "--state",
        default=None,
        metavar="PATH",
        help="the memory of what was already said (default: MEE_ALERT_STATE_PATH)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the events, deliver nothing and write nothing",
    )
    return parser


def _run(args: argparse.Namespace, settings: Settings) -> int:
    """One pass; returns the exit code, never raises for a delivery problem."""

    # Built first: a bad spec must fail before anything is written, so an
    # operator who typo'd the path does not also advance the memory.
    sink: Sink = build_sink(args.sink or settings.alert_sink_spec)
    state_path = Path(args.state) if args.state else Path(settings.alert_state_path)
    store = AlertStateStore(state_path)
    engine = AlertEngine(store)

    observations, high_water = build_observations(
        settings, baseline=_baseline(store.meta.get("fields_filled"))
    )
    events = engine.evaluate(observations, time.time(), persist=False)

    failures = 0
    for event in events:
        print(format_event(event))
        if args.dry_run:
            continue
        try:
            sink.send(event)
        except Exception as exc:  # noqa: BLE001 - one event must not lose the rest
            failures += 1
            print(
                f"alerts: {sink.name} could not deliver {event.key}: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

    if args.dry_run:
        return 0
    if failures:
        # Do not advance the memory past an event nobody received: the next run
        # says it again, which is the recoverable direction to be wrong in.
        return 1
    if high_water is not None:
        store.set_meta(fields_filled=high_water)
    store.save(engine.state)
    return 0


def main(argv: Sequence[str] | None = None, *, settings: Settings | None = None) -> int:
    """Run one pass. ``settings`` is injectable so a test never reads the env."""

    parser = _build_parser()
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else list(argv))
    except SystemExit as exc:  # --help and usage errors both land here
        return int(exc.code) if isinstance(exc.code, int) else 2

    try:
        return _run(args, settings if settings is not None else get_settings())
    except (OSError, ValueError) as exc:
        # The class is kept in the message: EACCES on the log file and ENOSPC on
        # the state file are different operator stories with the same exit code.
        print(f"alerts: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - exercised through main([...])
    raise SystemExit(main())


__all__ = [
    "KEY_CHECK_PREFIX",
    "KEY_COVERAGE",
    "KEY_DEPTH_SOURCE_PREFIX",
    "KEY_DEPLOYMENT",
    "build_observations",
    "coverage_observation",
    "format_event",
    "health_observations",
    "ledger_observations",
    "main",
]
