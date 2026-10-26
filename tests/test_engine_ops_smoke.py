"""RED contract for the R7 deployment smoke check.

`ops smoke` is the answer to "did this deploy come up with the dataset and the
public flag I intended?" — not a second health endpoint. It reads the two
documents the engine already serves and turns them into named checks:

* health 200 and metrics 200;
* the dataset exists and carries symbols;
* neither document reports a startup error;
* `public_mode` matches the expected value when one is given;
* dataset age is within the allowed window when one is given.

An HTTP failure is a failed check, never an exception: the command's job is to
report the deployment, and a traceback is not a report.
"""

from __future__ import annotations

from typing import Any
import json

import httpx2
import pytest

from app.engine import ops

HEALTH: dict[str, Any] = {
    "engine": "v2",
    "catalog_version": 1,
    "catalog_hash": "x",
    "metrics": 116,
    "public_mode": False,
    "security_master": True,
    "rows": True,
    "dataset": {
        "fetched_at": "2026-10-26T16:41:02+00:00",
        "symbols": 10429,
        "errors": 0,
    },
    "startup_error": None,
}

METRICS: dict[str, Any] = {
    "dataset": {
        "symbols": 10429,
        "facts_symbols": 38,
        "fetched_at": "2026-10-26T16:41:02+00:00",
        "age_seconds": 3600,
        "errors": 0,
    },
    "cache": {"files": 1, "bytes": 10, "by_kind": {}},
    "public_mode": False,
    "startup_error": None,
    "process": {"uptime_seconds": 60},
}


def client_for(
    *, health: tuple[int, Any] = (200, HEALTH), metrics: tuple[int, Any] = (200, METRICS)
) -> httpx2.AsyncClient:
    """A client whose two routes answer the given status/payload."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/engine/health":
            status, payload = health
        elif request.url.path == "/engine/metrics":
            status, payload = metrics
        else:  # pragma: no cover - the smoke check must only ask these two
            return httpx2.Response(404, json={"detail": "Not Found"})
        return httpx2.Response(status, json=payload)

    return httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler), base_url="http://mee.test"
    )


def check(report: dict[str, Any], name: str) -> dict[str, Any]:
    """The named check from a smoke report."""

    return next(item for item in report["checks"] if item["name"] == name)


async def test_a_healthy_private_deployment_passes_every_check() -> None:
    async with client_for() as client:
        report = await ops.smoke_check("http://mee.test", client=client)

    assert report["ok"] is True
    assert report["base_url"] == "http://mee.test"
    assert all(item["ok"] for item in report["checks"])


async def test_http_failure_is_a_failed_check_not_an_exception() -> None:
    async with client_for(health=(503, {"error": "down"})) as client:
        report = await ops.smoke_check("http://mee.test", client=client)

    assert report["ok"] is False
    assert check(report, "health_status")["ok"] is False


async def test_a_startup_error_fails_the_report() -> None:
    broken = dict(METRICS, startup_error="SnapshotError: bad checksum")
    async with client_for(metrics=(200, broken)) as client:
        report = await ops.smoke_check("http://mee.test", client=client)

    assert report["ok"] is False
    assert check(report, "startup_error")["ok"] is False


async def test_public_mode_must_match_when_expected() -> None:
    public = dict(METRICS, public_mode=True)
    async with client_for(metrics=(200, public)) as client:
        report = await ops.smoke_check(
            "http://mee.test", client=client, expect_public=True
        )

    assert report["ok"] is True
    assert check(report, "public_mode")["ok"] is True

    async with client_for() as client:
        mismatch = await ops.smoke_check(
            "http://mee.test", client=client, expect_public=True
        )

    assert mismatch["ok"] is False
    assert check(mismatch, "public_mode")["ok"] is False


async def test_dataset_age_must_be_within_the_window() -> None:
    async with client_for() as client:
        fresh = await ops.smoke_check(
            "http://mee.test", client=client, max_age_seconds=7200
        )
        stale = await ops.smoke_check(
            "http://mee.test", client=client, max_age_seconds=60
        )

    assert fresh["ok"] is True
    assert stale["ok"] is False
    assert check(stale, "dataset_age")["ok"] is False


async def test_an_empty_dataset_fails_the_report() -> None:
    empty = dict(HEALTH, dataset={"symbols": 0, "errors": 0})
    async with client_for(health=(200, empty)) as client:
        report = await ops.smoke_check("http://mee.test", client=client)

    assert report["ok"] is False
    assert check(report, "dataset_symbols")["ok"] is False


def test_cli_exit_code_follows_the_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def good(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "base_url": "http://mee.test", "checks": []}

    async def bad(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "base_url": "http://mee.test", "checks": []}

    monkeypatch.setattr(ops, "smoke_check", good)
    assert ops.main(["smoke", "--base-url", "http://mee.test"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    monkeypatch.setattr(ops, "smoke_check", bad)
    assert ops.main(["smoke", "--base-url", "http://mee.test"]) == 2
