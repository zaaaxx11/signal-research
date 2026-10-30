"""Contract tests for the daily canary (``app.ops.canary``).

The canary exists because every other check reads a file a previous cycle
wrote. These tests exercise the *run*, not the files: a missing snapshot must
fail one check and leave the other five answering; an empty store has no median
and is a failure; a first run has no yesterday and says so; a green run writes a
report and tells nobody; a red one sends exactly one event per failing check.

No test reaches the network. ``_cnbc_fetcher``/``_yahoo_fetcher`` are the two
module-level adapters and every test replaces them (or passes ``quote_fetcher``/
``depth_fetcher``), so the suite-wide socket guard is never even approached.
Every path is under ``tmp_path``; nothing reads ``data/``.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.config import Settings
from app.engine.ingest import EngineDataset
from app.engine.model import Provenance, SecurityRecord
from app.engine.security import SecurityMaster
from app.engine.store import EngineStore
from app.market.depth_fields import CATALOG_FIELDS
from app.market.health_ledger import HealthLedger, health_path_for
from app.ops import canary
from app.ops.alerting import Severity
from app.ops.canary import CanaryReport, Drift, DriftStatus, Status

#: Real catalog columns, from the registry: a renamed field must not make a
#: coverage test pass by counting nothing.
CATALOG = sorted(CATALOG_FIELDS)

#: One more than the time a fast check should ever need on tmpfs.
SLOW_CHECK_S = 2.0


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """Settings rooted in ``tmp_path``; the production data dir is never used."""

    values: dict[str, Any] = {
        "depth": True,
        "db_path": tmp_path / "ztockz.db",
        "depth_store_path": tmp_path / "depth.json",
        "health_snapshot_path": tmp_path / "snapshot.json",
        "health_data_dir": tmp_path,
        "alert_state_path": tmp_path / "alerts.state.json",
        "alert_sink_spec": f"file:{tmp_path / 'alerts.jsonl'}",
        "canary_report_path": tmp_path / "canary.report.json",
        "canary_state_path": tmp_path / "canary.state.json",
        "canary_alert_state_path": tmp_path / "canary.alerts.state.json",
        "canary_sink_spec": f"file:{tmp_path / 'canary.jsonl'}",
        "canary_symbols": 3,
        "canary_min_securities": 2,
        "canary_min_median_fields": 3,
        "canary_field_drop_threshold": 2,
        "canary_timeout_s": 1.0,
        "canary_budget_s": 10.0,
    }
    values.update(overrides)
    return Settings(**values)


def write_snapshot(path: Path, *, count: int = 3) -> None:
    """A real, digest-verified snapshot with ``count`` securities."""

    master = SecurityMaster()
    records = [
        SecurityRecord(
            symbol=f"SYM{index}",
            name=f"Symbol {index}",
            cik=f"{index + 1:010d}",
            exchange="NASDAQ",
            mic="XNAS",
            asset_class="stock",
            currency="USD",
            country="US",
            sector=None,
            industry=None,
        )
        for index in range(count)
    ]
    master.load(records, Provenance(source="test", as_of="2026-10-30T00:00:00+00:00"))
    dataset = EngineDataset(
        fetched_at="2026-10-30T00:00:00+00:00",
        security_master=master,
        facts={},
        errors={},
    )
    EngineStore(path).save(dataset)


def store_rows(count: int = 3, fields: int = 5) -> dict[str, dict[str, Any]]:
    """``count`` rows, each filling the first ``fields`` real catalog columns."""

    return {
        f"SYM{index}": {name: 1.0 + index for name in CATALOG[:fields]}
        for index in range(count)
    }


def write_store(
    path: Path, rows: dict[str, Any], *, mtime: float | None = None
) -> None:
    payload = {
        "as_of": "2026-10-30T00:00:00+00:00",
        "count": len(rows),
        "symbols": rows,
        "missing": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def write_ledger(
    path: Path,
    *,
    ok: tuple[str, ...] = ("sec_bulk", "cnbc"),
    failed: tuple[str, ...] = (),
) -> None:
    ledger = HealthLedger(path)
    for name in ok:
        ledger.record_ok(name, duration_s=1.0, fields=10)
    for name in failed:
        ledger.record_error(name, error="HTTP 503")
    assert ledger.persist()


def healthy(tmp_path: Path, **overrides: Any) -> Settings:
    """A deployment where every local fact is good and every fetch answers."""

    settings = make_settings(tmp_path, **overrides)
    write_snapshot(Path(settings.health_snapshot_path), count=3)
    write_store(Path(settings.depth_store_path), store_rows(3, 5))
    write_ledger(health_path_for(settings.depth_store_path))
    return settings


def good_quotes(symbols: Any, timeout_s: float) -> dict[str, Any]:
    """A quote fetcher that answers every symbol with a price."""

    return {str(symbol): SimpleNamespace(last=100.0) for symbol in symbols}


def good_depth(symbols: Any, timeout_s: float) -> dict[str, Any]:
    """A Yahoo-shaped fetcher: canonical symbol -> catalog row."""

    return {str(symbol): {"price": 100.0} for symbol in symbols}


def run(settings: Settings, **kwargs: Any) -> CanaryReport:
    """One canary run with fake egress, so no test touches a socket."""

    kwargs.setdefault("symbols", settings.canary_symbols)
    kwargs.setdefault("quote_fetcher", good_quotes)
    kwargs.setdefault("depth_fetcher", good_depth)
    return canary.run_canary(settings, **kwargs)


def by_name(report: CanaryReport) -> dict[str, Any]:
    return {result.name: result for result in report.checks}


def sink_lines(settings: Settings) -> list[dict[str, Any]]:
    path = Path(settings.canary_sink_spec.removeprefix("file:"))
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def previous_document(
    *,
    median_fields: float | None,
    checks: dict[str, str] | None = None,
) -> dict[str, Any]:
    """A hand-written ``canary.state.json`` — the exact shape ``as_state`` writes."""

    return {
        "schema": 1,
        "written_at": "2026-10-29T04:15:00+00:00",
        "verdict": "pass",
        "median_fields": median_fields,
        "checks": checks
        or {name: "pass" for name in canary.CHECK_NAMES},
    }


# ---------------------------------------------------------------------------
# snapshot: digest verified, security count above the floor
# ---------------------------------------------------------------------------


def test_a_verified_snapshot_passes(tmp_path: Path) -> None:
    result = by_name(run(healthy(tmp_path)))["snapshot"]

    assert result.status is Status.PASS
    assert result.metrics["securities"] == 3
    assert "verified" in result.detail


def test_a_missing_snapshot_fails_only_the_snapshot_check(tmp_path: Path) -> None:
    """The whole point of independent checks: one hole must not hide five facts."""

    settings = healthy(tmp_path)
    Path(settings.health_snapshot_path).unlink()

    report = run(settings)

    assert report.check("snapshot").status is Status.FAIL
    assert "snapshot_unreadable" in report.check("snapshot").detail
    assert report.failing == ("snapshot",)
    for name in ("store", "freshness", "quote", "depth_source", "ledger"):
        assert report.check(name).status is Status.PASS, name


def test_a_snapshot_whose_digest_no_longer_verifies_fails(tmp_path: Path) -> None:
    settings = healthy(tmp_path)
    path = Path(settings.health_snapshot_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["dataset"]["fetched_at"] = "2026-10-29T00:00:00+00:00"
    path.write_text(json.dumps(document), encoding="utf-8")

    result = by_name(run(settings))["snapshot"]

    assert result.status is Status.FAIL
    assert "snapshot_checksum" in result.detail
    assert result.reason == "snapshot_checksum"


def test_a_snapshot_below_the_security_floor_fails(tmp_path: Path) -> None:
    settings = healthy(tmp_path, canary_min_securities=10)
    result = by_name(run(settings))["snapshot"]

    assert result.status is Status.FAIL
    assert result.reason == "below_floor"
    assert result.metrics["securities"] == 3


def test_a_deployment_with_no_snapshot_configured_fails_rather_than_skips(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path, health_snapshot_path=None)
    write_store(Path(settings.depth_store_path), store_rows(3, 5))
    ledger_path = health_path_for(settings.depth_store_path)
    assert ledger_path is not None
    write_ledger(ledger_path)

    result = by_name(run(settings))["snapshot"]

    assert result.status is Status.FAIL
    assert result.reason == "not_configured"


# ---------------------------------------------------------------------------
# store: parses, enough symbols, median coverage above the floor
# ---------------------------------------------------------------------------


def test_a_store_at_the_symbol_floor_passes(tmp_path: Path) -> None:
    result = by_name(run(healthy(tmp_path)))["store"]

    assert result.status is Status.PASS
    assert result.metrics["symbols"] == 3
    assert result.metrics["median_fields"] == 5


def test_a_store_below_the_symbol_floor_fails(tmp_path: Path) -> None:
    settings = healthy(tmp_path)
    write_store(Path(settings.depth_store_path), store_rows(2, 5))

    result = by_name(run(settings))["store"]

    assert result.status is Status.FAIL
    assert result.reason == "too_few_symbols"


def test_a_store_whose_median_coverage_is_below_the_floor_fails(tmp_path: Path) -> None:
    settings = healthy(tmp_path)
    write_store(Path(settings.depth_store_path), store_rows(3, 2))

    result = by_name(run(settings))["store"]

    assert result.status is Status.FAIL
    assert result.reason == "below_floor"
    assert result.metrics["median_fields"] == 2


def test_an_empty_store_is_a_failure_and_has_no_median(tmp_path: Path) -> None:
    """Zero symbols has no coverage to average; ``0`` would read as a real number."""

    settings = healthy(tmp_path)
    write_store(Path(settings.depth_store_path), {})

    report = run(settings)
    result = report.check("store")

    assert result.status is Status.FAIL
    assert result.reason == "empty"
    assert result.metrics["median_fields"] is None
    assert report.median_fields is None


def test_a_store_that_parses_into_garbage_fails(tmp_path: Path) -> None:
    """Adversarial: valid JSON, valid envelope, no usable catalog values."""

    settings = healthy(tmp_path)
    write_store(
        Path(settings.depth_store_path),
        {
            "AAPL": {CATALOG[0]: None, "not_a_catalog_field": 1.0, "junk": ["x"]},
            "MSFT": {CATALOG[0]: "", CATALOG[1]: None},
            "NVDA": "not even a row",
        },
    )

    result = by_name(run(settings))["store"]

    assert result.status is Status.FAIL
    assert result.metrics["median_fields"] == 0


def test_a_store_that_is_not_json_fails(tmp_path: Path) -> None:
    settings = healthy(tmp_path)
    Path(settings.depth_store_path).write_text("{not json", encoding="utf-8")

    result = by_name(run(settings))["store"]

    assert result.status is Status.FAIL
    assert result.reason == "invalid"


def test_the_median_is_of_per_symbol_catalog_counts_not_of_the_union(
    tmp_path: Path,
) -> None:
    settings = healthy(tmp_path)
    write_store(
        Path(settings.depth_store_path),
        {
            "A": {name: 1.0 for name in CATALOG[:4]},
            "B": {name: 1.0 for name in CATALOG[:4]},
            "C": {name: 1.0 for name in CATALOG[:5]},
            "D": {name: 1.0 for name in CATALOG[:6]},
        },
    )

    result = by_name(run(settings))["store"]

    assert result.metrics["median_fields"] == 4.5


def test_a_store_with_zero_requested_symbols_is_refused(tmp_path: Path) -> None:
    """Adversarial: an empty sample must not trivially satisfy the symbol floor."""

    with pytest.raises(ValueError):
        canary.run_canary(
            healthy(tmp_path),
            symbols=0,
            quote_fetcher=good_quotes,
            depth_fetcher=good_depth,
        )


# ---------------------------------------------------------------------------
# freshness: an age compared with twice the depth cadence
# ---------------------------------------------------------------------------


def test_a_fresh_store_passes(tmp_path: Path) -> None:
    result = by_name(run(healthy(tmp_path)))["freshness"]

    assert result.status is Status.PASS
    assert result.metrics["age_s"] < result.metrics["budget_s"]


def test_a_stale_store_is_a_warning_not_a_failure(tmp_path: Path) -> None:
    settings = healthy(tmp_path)
    now = datetime.now(UTC)
    stale_at = (now - timedelta(days=3)).timestamp()
    write_store(Path(settings.depth_store_path), store_rows(3, 5), mtime=stale_at)

    report = run(settings, now=now)

    assert report.check("freshness").status is Status.WARN
    assert report.check("freshness").reason == "stale"
    assert report.check("store").status is Status.PASS
    assert report.verdict is Status.WARN


def test_freshness_is_measured_against_the_injected_clock(tmp_path: Path) -> None:
    """A clock ten days ahead must age the store; wall-clock code would pass it."""

    settings = healthy(tmp_path)
    result = canary.run_canary(
        settings,
        symbols=settings.canary_symbols,
        now=datetime.now(UTC) + timedelta(days=10),
        quote_fetcher=good_quotes,
        depth_fetcher=good_depth,
    )

    assert result.check("freshness").status is Status.WARN
    assert result.check("freshness").metrics["age_s"] > 9 * 86_400


def test_a_store_mtime_in_the_future_is_a_warning_not_freshness(tmp_path: Path) -> None:
    """Adversarial: a backwards clock makes the age negative, which is not fresh."""

    settings = healthy(tmp_path)
    now = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    write_store(
        Path(settings.depth_store_path),
        store_rows(3, 5),
        mtime=(now + timedelta(days=2)).timestamp(),
    )

    result = by_name(run(settings, now=now))["freshness"]

    assert result.status is Status.WARN
    assert result.reason == "future"


def test_a_missing_store_leaves_freshness_a_warning_not_a_crash(tmp_path: Path) -> None:
    settings = healthy(tmp_path)
    Path(settings.depth_store_path).unlink()

    report = run(settings)

    assert report.check("store").status is Status.FAIL
    assert report.check("freshness").status is Status.WARN
    assert report.check("freshness").reason == "missing"


# ---------------------------------------------------------------------------
# quote: five real symbols, four prices
# ---------------------------------------------------------------------------


def test_four_of_five_prices_pass(tmp_path: Path) -> None:
    def sparse(symbols: Any, timeout_s: float) -> dict[str, Any]:
        answers: dict[str, Any] = {str(symbol): SimpleNamespace(last=1.0) for symbol in symbols}
        answers[str(list(symbols)[0])] = None
        return answers

    result = by_name(run(healthy(tmp_path), quote_fetcher=sparse))["quote"]

    assert result.status is Status.PASS
    assert result.metrics["priced"] == 4


def test_three_of_five_prices_fail(tmp_path: Path) -> None:
    def sparse(symbols: Any, timeout_s: float) -> dict[str, Any]:
        answers: dict[str, Any] = {str(symbol): SimpleNamespace(last=1.0) for symbol in symbols}
        for symbol in list(symbols)[:2]:
            answers[str(symbol)] = None
        return answers

    result = by_name(run(healthy(tmp_path), quote_fetcher=sparse))["quote"]

    assert result.status is Status.FAIL
    assert result.reason == "too_few_prices"
    assert result.metrics["priced"] == 3


def test_an_empty_quote_answer_fails_rather_than_passing_on_nothing(
    tmp_path: Path,
) -> None:
    """Adversarial: no symbols answered is the exact shape a vacuous truth takes."""

    result = by_name(run(healthy(tmp_path), quote_fetcher=lambda symbols, timeout_s: {}))[
        "quote"
    ]

    assert result.status is Status.FAIL
    assert result.metrics["priced"] == 0


def test_an_empty_symbol_configuration_is_padded_to_five_real_probes(
    tmp_path: Path,
) -> None:
    seen: dict[str, tuple[str, ...]] = {}

    def record(symbols: Any, timeout_s: float) -> dict[str, Any]:
        seen["symbols"] = tuple(str(symbol) for symbol in symbols)
        return good_quotes(symbols, timeout_s)

    run(healthy(tmp_path, canary_quote_symbols=" , "), quote_fetcher=record)

    assert len(seen["symbols"]) == canary.QUOTE_SAMPLE
    assert set(seen["symbols"]) == set(canary.DEFAULT_QUOTE_SYMBOLS)


# ---------------------------------------------------------------------------
# depth_source: one real Yahoo batch call proves egress
# ---------------------------------------------------------------------------


def test_three_yahoo_rows_pass(tmp_path: Path) -> None:
    result = by_name(run(healthy(tmp_path)))["depth_source"]

    assert result.status is Status.PASS
    assert result.metrics["priced"] == 3


def test_a_partial_yahoo_answer_is_a_warning(tmp_path: Path) -> None:
    def partial(symbols: Any, timeout_s: float) -> dict[str, Any]:
        rows = good_depth(symbols, timeout_s)
        return {list(rows)[0]: rows[list(rows)[0]]}

    result = by_name(run(healthy(tmp_path), depth_fetcher=partial))["depth_source"]

    assert result.status is Status.WARN
    assert result.reason == "partial"


def test_no_yahoo_rows_is_a_failure(tmp_path: Path) -> None:
    result = by_name(
        run(healthy(tmp_path), depth_fetcher=lambda symbols, timeout_s: {})
    )["depth_source"]

    assert result.status is Status.FAIL
    assert result.reason == "no_rows"


# ---------------------------------------------------------------------------
# ledger: a source that never succeeded, and a streak that is a rung
# ---------------------------------------------------------------------------


def test_a_healthy_ledger_passes(tmp_path: Path) -> None:
    result = by_name(run(healthy(tmp_path)))["ledger"]

    assert result.status is Status.PASS
    assert result.metrics["sources"] == 2


def test_a_source_that_never_succeeded_is_a_failure(tmp_path: Path) -> None:
    settings = healthy(tmp_path)
    write_ledger(health_path_for(settings.depth_store_path), failed=("new_rung",))

    result = by_name(run(settings))["ledger"]

    assert result.status is Status.FAIL
    assert result.reason == "never_succeeded"
    assert result.metrics["never_succeeded"] == 1


def test_two_consecutive_errors_are_a_failure(tmp_path: Path) -> None:
    settings = healthy(tmp_path)
    path = health_path_for(settings.depth_store_path)
    ledger = HealthLedger(path)
    ledger.record_ok("sec_bulk", duration_s=1.0, fields=10)
    ledger.record_error("sec_bulk", error="HTTP 503")
    ledger.record_error("sec_bulk", error="HTTP 503")
    assert ledger.persist()

    result = by_name(run(settings))["ledger"]

    assert result.status is Status.FAIL
    assert result.reason == "consecutive_errors"
    assert result.metrics["sick"] == 1


def test_a_missing_ledger_is_a_failure(tmp_path: Path) -> None:
    settings = healthy(tmp_path)
    health_path_for(settings.depth_store_path).unlink()

    result = by_name(run(settings))["ledger"]

    assert result.status is Status.FAIL
    assert result.reason == "missing"


def test_an_empty_ledger_is_a_failure(tmp_path: Path) -> None:
    settings = healthy(tmp_path)
    health_path_for(settings.depth_store_path).write_text("{}", encoding="utf-8")

    result = by_name(run(settings))["ledger"]

    assert result.status is Status.FAIL
    assert result.reason == "empty"


# ---------------------------------------------------------------------------
# drift: yesterday, or the honest absence of it
# ---------------------------------------------------------------------------


def test_the_first_run_reports_first_run_not_ok(tmp_path: Path) -> None:
    report = run(healthy(tmp_path))

    assert report.drift.status is DriftStatus.FIRST_RUN
    assert "no previous" in report.drift.detail
    assert report.drift.delta is None
    assert report.verdict is Status.PASS


def test_a_run_persists_its_measurement_so_tomorrow_can_compare(
    tmp_path: Path,
) -> None:
    """The public API alone closes the loop: run twice, the second has a yesterday."""

    settings = healthy(tmp_path)

    first = run(settings)
    assert first.drift.status is DriftStatus.FIRST_RUN
    assert Path(settings.canary_state_path).exists()

    second = run(settings)
    assert second.drift.status is DriftStatus.OK
    assert second.drift.previous_median_fields == first.median_fields


def test_a_median_drop_beyond_the_threshold_warns_with_the_exact_delta(
    tmp_path: Path,
) -> None:
    settings = healthy(tmp_path)
    state = tmp_path / "previous.json"
    state.write_text(
        json.dumps(previous_document(median_fields=9.0)), encoding="utf-8"
    )

    report = run(settings, state_path=state)

    assert report.drift.status is DriftStatus.WARN
    assert report.drift.delta == 4
    assert "delta 4" in report.drift.detail
    assert "9" in report.drift.detail and "5" in report.drift.detail
    assert report.verdict is Status.WARN


def test_a_drop_exactly_at_the_threshold_is_not_a_warning(tmp_path: Path) -> None:
    settings = healthy(tmp_path)
    state = tmp_path / "previous.json"
    state.write_text(
        json.dumps(previous_document(median_fields=7.0)), encoding="utf-8"
    )

    report = run(settings, state_path=state)

    assert report.drift.status is DriftStatus.OK
    assert report.drift.delta == 2
    assert report.verdict is Status.PASS


def test_a_new_failing_check_warns_even_when_coverage_is_steady(
    tmp_path: Path,
) -> None:
    settings = healthy(tmp_path)
    state = tmp_path / "previous.json"
    state.write_text(
        json.dumps(previous_document(median_fields=5.0)), encoding="utf-8"
    )
    path = health_path_for(settings.depth_store_path)
    ledger = HealthLedger(path)
    ledger.record_ok("sec_bulk", duration_s=1.0, fields=10)
    ledger.record_error("sec_bulk", error="HTTP 503")
    ledger.record_error("sec_bulk", error="HTTP 503")
    assert ledger.persist()

    report = run(settings, state_path=state)

    assert report.drift.status is DriftStatus.WARN
    assert report.drift.delta == 0
    assert report.drift.new_failures == ("ledger",)
    assert "ledger" in report.drift.detail


def test_an_unchanged_failure_is_not_a_new_failure(tmp_path: Path) -> None:
    settings = healthy(tmp_path)
    state = tmp_path / "previous.json"
    state.write_text(
        json.dumps(
            previous_document(
                median_fields=5.0,
                checks={**{name: "pass" for name in canary.CHECK_NAMES}, "ledger": "fail"},
            )
        ),
        encoding="utf-8",
    )
    path = health_path_for(settings.depth_store_path)
    ledger = HealthLedger(path)
    ledger.record_ok("sec_bulk", duration_s=1.0, fields=10)
    ledger.record_error("sec_bulk", error="HTTP 503")
    ledger.record_error("sec_bulk", error="HTTP 503")
    assert ledger.persist()

    report = run(settings, state_path=state)

    assert report.drift.new_failures == ()
    assert report.drift.status is DriftStatus.OK
    assert report.verdict is Status.FAIL


def test_a_corrupt_previous_report_is_first_run(tmp_path: Path) -> None:
    settings = healthy(tmp_path)
    state = tmp_path / "previous.json"
    state.write_text("}{ not json", encoding="utf-8")

    report = run(settings, state_path=state)

    assert report.drift.status is DriftStatus.FIRST_RUN
    assert report.drift.previous_median_fields is None


def test_a_previous_report_from_a_future_schema_is_first_run(tmp_path: Path) -> None:
    settings = healthy(tmp_path)
    state = tmp_path / "previous.json"
    document = previous_document(median_fields=9.0)
    document["schema"] = 99
    state.write_text(json.dumps(document), encoding="utf-8")

    report = run(settings, state_path=state)

    assert report.drift.status is DriftStatus.FIRST_RUN


def test_a_previous_report_without_a_median_never_invents_one(tmp_path: Path) -> None:
    settings = healthy(tmp_path)
    state = tmp_path / "previous.json"
    state.write_text(
        json.dumps(previous_document(median_fields=None)), encoding="utf-8"
    )

    report = run(settings, state_path=state)

    assert report.drift.delta is None
    assert report.drift.previous_median_fields is None
    assert report.drift.status is DriftStatus.OK


# ---------------------------------------------------------------------------
# observations: the report in the alert engine's vocabulary
# ---------------------------------------------------------------------------


def test_a_passing_check_is_an_ok_observation_with_a_stable_key(tmp_path: Path) -> None:
    observations = canary.canary_observations(run(healthy(tmp_path)))
    snapshot = {entry.key: entry for entry in observations}["canary.check.snapshot"]

    assert snapshot.ok is True
    assert snapshot.title


def test_a_failing_check_is_critical_and_names_its_reason(tmp_path: Path) -> None:
    settings = healthy(tmp_path)
    write_store(Path(settings.depth_store_path), store_rows(3, 2))

    observations = canary.canary_observations(run(settings))
    store = {entry.key: entry for entry in observations}["canary.check.store"]

    assert store.ok is False
    assert store.severity is Severity.CRITICAL
    assert store.fingerprint.startswith("fail|below_floor")


def test_a_warning_check_is_a_warning_not_a_critical(tmp_path: Path) -> None:
    settings = healthy(tmp_path)
    write_store(
        Path(settings.depth_store_path),
        store_rows(3, 5),
        mtime=time.time() - 10 * 86_400,
    )

    observations = canary.canary_observations(run(settings))
    freshness = {entry.key: entry for entry in observations}["canary.check.freshness"]

    assert freshness.ok is False
    assert freshness.severity is Severity.WARNING


def test_drift_warning_becomes_its_own_observation(tmp_path: Path) -> None:
    settings = healthy(tmp_path)
    state = tmp_path / "previous.json"
    state.write_text(
        json.dumps(previous_document(median_fields=9.0)), encoding="utf-8"
    )

    observations = canary.canary_observations(run(settings, state_path=state))
    drift = {entry.key: entry for entry in observations}["canary.drift"]

    assert drift.ok is False
    assert drift.severity is Severity.WARNING
    assert "delta 4" in drift.detail


def test_a_first_run_emits_no_drift_observation(tmp_path: Path) -> None:
    keys = {entry.key for entry in canary.canary_observations(run(healthy(tmp_path)))}

    assert "canary.drift" not in keys
    assert keys == {f"canary.check.{name}" for name in canary.CHECK_NAMES}


def test_a_check_that_raises_is_a_failure_and_the_rest_still_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(settings: Settings) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(canary, "_check_ledger", explode)

    report = run(healthy(tmp_path))

    assert report.check("ledger").status is Status.FAIL
    assert report.check("ledger").reason == "exception"
    assert "boom" in report.check("ledger").detail
    assert report.check("store").status is Status.PASS


# ---------------------------------------------------------------------------
# the process
# ---------------------------------------------------------------------------


def wire_egress(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the two network adapters; the CLI has no fetcher arguments."""

    monkeypatch.setattr(canary, "_cnbc_fetcher", good_quotes)
    monkeypatch.setattr(canary, "_yahoo_fetcher", good_depth)


def test_a_green_run_writes_the_report_and_sends_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = healthy(tmp_path)
    wire_egress(monkeypatch)

    assert canary.main([], settings=settings) == 0

    document = json.loads(Path(settings.canary_report_path).read_text(encoding="utf-8"))
    assert document["verdict"] == "pass"
    assert set(document["checks"]) == set(canary.CHECK_NAMES)
    assert Path(settings.canary_state_path).exists()
    assert sink_lines(settings) == [], "a green canary must stay silent"

    printed = capsys.readouterr().out.splitlines()
    for name in canary.CHECK_NAMES:
        assert any(line.startswith(name) for line in printed), name


def test_a_red_run_sends_one_event_per_failing_check_with_a_stable_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = healthy(tmp_path)
    write_store(Path(settings.depth_store_path), store_rows(3, 2))
    path = health_path_for(settings.depth_store_path)
    ledger = HealthLedger(path)
    ledger.record_ok("sec_bulk", duration_s=1.0, fields=10)
    ledger.record_error("sec_bulk", error="HTTP 503")
    ledger.record_error("sec_bulk", error="HTTP 503")
    assert ledger.persist()
    wire_egress(monkeypatch)

    assert canary.main([], settings=settings) == 1

    events = sink_lines(settings)
    assert [event["key"] for event in events] == [
        "canary.check.store",
        "canary.check.ledger",
    ]
    assert all(event["state"] == "open" for event in events)

    # The same bad news again is not a second page.
    assert canary.main([], settings=settings) == 1
    assert len(sink_lines(settings)) == 2


def test_a_warn_run_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = healthy(tmp_path)
    write_store(
        Path(settings.depth_store_path),
        store_rows(3, 5),
        mtime=time.time() - 10 * 86_400,
    )
    wire_egress(monkeypatch)

    assert canary.main([], settings=settings) == 0
    document = json.loads(Path(settings.canary_report_path).read_text(encoding="utf-8"))
    assert document["verdict"] == "warn"


def test_a_dry_run_writes_nothing_and_still_returns_the_verdict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = healthy(tmp_path)
    write_store(Path(settings.depth_store_path), store_rows(3, 2))
    wire_egress(monkeypatch)

    assert canary.main(["--dry-run"], settings=settings) == 1

    assert capsys.readouterr().out.strip(), "a dry run still prints the checks"
    assert not Path(settings.canary_report_path).exists()
    assert not Path(settings.canary_state_path).exists()
    assert not Path(settings.canary_alert_state_path).exists()
    assert sink_lines(settings) == []


def test_the_json_flag_prints_one_machine_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = healthy(tmp_path)
    wire_egress(monkeypatch)

    assert canary.main(["--json"], settings=settings) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["verdict"] == "pass"
    assert document["symbols"] == settings.canary_symbols
    assert document["drift"]["status"] == "first_run"


def test_the_symbol_count_flag_reaches_the_store_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = healthy(tmp_path)
    wire_egress(monkeypatch)

    assert canary.main(["--symbols", "4"], settings=settings) == 1
    document = json.loads(Path(settings.canary_report_path).read_text(encoding="utf-8"))
    assert document["checks"]["store"]["reason"] == "too_few_symbols"


def test_an_unknown_sink_scheme_is_a_real_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = healthy(tmp_path, canary_sink_spec="telegram:12345")
    wire_egress(monkeypatch)

    assert canary.main([], settings=settings) == 1
    assert "telegram" in capsys.readouterr().err
    assert not Path(settings.canary_report_path).exists(), (
        "a bad spec must fail before anything is written"
    )


def test_a_crash_mid_write_leaves_the_previous_report_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = healthy(tmp_path)
    wire_egress(monkeypatch)
    report_path = Path(settings.canary_report_path)
    report_path.write_text('{"previous":true}', encoding="utf-8")

    def fail_replace(source: Any, target: Any) -> None:
        raise OSError("disk vanished")

    monkeypatch.setattr(canary, "_atomic_replace", fail_replace)

    assert canary.main([], settings=settings) == 1
    assert json.loads(report_path.read_text(encoding="utf-8")) == {"previous": True}
    assert not list(tmp_path.glob(f".{report_path.name}*")), "the temp file is cleaned"


def test_the_canary_writes_only_its_report_state_and_sink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = healthy(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    wire_egress(monkeypatch)

    assert canary.main([], settings=settings) == 0

    assert list(work.iterdir()) == [], "nothing is written relative to the cwd"
    written = {
        path.relative_to(tmp_path).as_posix()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert written == {
        "snapshot.json",
        "depth.json",
        "depth.health.json",
        "canary.report.json",
        "canary.state.json",
        "canary.alerts.state.json",
    }


def test_the_state_file_is_a_projection_not_a_copy_of_the_report(
    tmp_path: Path,
) -> None:
    settings = healthy(tmp_path)
    report = run(settings)
    path = canary.write_state(report, tmp_path / "state.json")
    document = json.loads(path.read_text(encoding="utf-8"))

    assert set(document) == {"schema", "written_at", "verdict", "median_fields", "checks"}
    assert document["checks"]["store"] == "pass"
    assert "detail" not in document


def test_the_report_is_valid_json_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    path = canary.write_report(run(healthy(tmp_path)), tmp_path / "report.json")

    json.loads(path.read_text(encoding="utf-8"))
    assert list(tmp_path.glob(".report.json*")) == []


# ---------------------------------------------------------------------------
# budget: a hanging upstream cannot hang the canary
# ---------------------------------------------------------------------------


def test_a_slow_fetcher_is_cut_off_by_the_per_call_timeout(tmp_path: Path) -> None:
    settings = healthy(tmp_path, canary_timeout_s=0.05, canary_budget_s=1.0)

    def hang(symbols: Any, timeout_s: float) -> dict[str, Any]:
        time.sleep(5.0)
        return good_quotes(symbols, timeout_s)

    started = time.perf_counter()
    report = canary.run_canary(
        settings,
        symbols=settings.canary_symbols,
        quote_fetcher=hang,
        depth_fetcher=hang,
    )
    elapsed = time.perf_counter() - started

    assert elapsed < SLOW_CHECK_S, elapsed
    assert report.check("quote").status is Status.FAIL
    assert report.check("quote").reason == "timeout"
    assert report.check("depth_source").reason == "timeout"
    assert report.check("snapshot").status is Status.PASS


def test_the_budget_is_total_not_a_per_call_allowance(tmp_path: Path) -> None:
    # A per-call cap of 10 s would allow 20 s of waiting; the run's own budget
    # is 0.2 s, so the second probe cannot be given a fresh allowance.
    settings = healthy(tmp_path, canary_timeout_s=10.0, canary_budget_s=0.2)

    def hang(symbols: Any, timeout_s: float) -> dict[str, Any]:
        time.sleep(5.0)
        return good_quotes(symbols, timeout_s)

    started = time.perf_counter()
    report = canary.run_canary(
        settings,
        symbols=settings.canary_symbols,
        quote_fetcher=hang,
        depth_fetcher=hang,
    )
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0, elapsed
    assert report.check("quote").reason == "timeout"
    # Whichever probe the deadline lands on, the run must end inside its budget
    # rather than serve each call a fresh ten seconds.
    assert report.check("depth_source").reason in {"budget", "timeout"}


# ---------------------------------------------------------------------------
# value objects
# ---------------------------------------------------------------------------


def test_the_report_is_frozen(tmp_path: Path) -> None:
    report = run(healthy(tmp_path))

    with pytest.raises(dataclasses.FrozenInstanceError):
        report.seconds = 99.0  # type: ignore[misc]


def test_a_naive_started_at_is_refused() -> None:
    with pytest.raises(ValueError):
        CanaryReport(
            started_at=datetime(2026, 10, 30),
            seconds=0.0,
            symbols=1,
            checks=(),
            drift=Drift(DriftStatus.FIRST_RUN, "no previous"),
        )


def test_a_relative_default_lands_on_the_data_volume_not_the_repo(
    tmp_path: Path, monkeypatch
) -> None:
    """Every path setting defaults to data/<name> for readability.

    Taken literally from a process whose cwd is the code directory, that writes
    into the repository while the rest of the state lives on the data volume.
    The canary resolves relative defaults against the data root instead.
    """

    from app.ops import canary

    monkeypatch.setenv("MEE_DATA_DIR", str(tmp_path))
    resolved = canary.resolve_data_path(Path("data/canary.report.json"), tmp_path)

    assert resolved == tmp_path / "canary.report.json"
    assert canary.resolve_data_path(Path("/var/tmp/abs.json"), tmp_path) == Path(
        "/var/tmp/abs.json"
    )
    assert (
        canary.resolve_data_path(Path("data/sub/x.json"), tmp_path)
        == tmp_path / "sub" / "x.json"
    )
