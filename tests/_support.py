"""Shared test helpers: frozen spec, fake fixture, ASGI client, polling."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "tests" / "spec" / "columns_v1.json"
FAKE_UNIVERSE_PATH = ROOT / "tests" / "fixtures" / "fake_universe.json"

SPEC: dict[str, Any] = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
COLUMNS: list[str] = SPEC["columns"]


def fake_universe() -> dict[str, Any]:
    """Load the deterministic fake-provider fixture."""

    return json.loads(FAKE_UNIVERSE_PATH.read_text(encoding="utf-8"))


def fake_rows() -> list[dict[str, Any]]:
    return list(fake_universe()["rows"])


def enrich_payload(**overrides: Any) -> dict[str, Any]:
    """A valid ``POST /markets/enrich`` body (02 §3.2)."""

    payload: dict[str, Any] = {
        "market": {"kind": "watchlist", "symbols": ["AAPL", "BBCA.JK", "BTC-USD"]},
        "fields": None,
        "enrichment": ["quote"],
        "as_of": "2026-10-22T00:00:00Z",
        "max_staleness_s": 900,
        "allow_delayed": True,
        "budget": {"requests": 5000, "credits": 2000, "seconds": 1800},
        "format": ["csv", "json"],
        "retention_days": 7,
        "quote_id": None,
        "async": True,
    }
    payload.update(overrides)
    return payload


def quote_plan(**overrides: Any) -> dict[str, Any]:
    """A minimal frozen ``QuotePlanV1`` document (02 §4.3)."""

    plan: dict[str, Any] = {
        "universe": {"mic": "XNAS", "symbols": ["AAPL", "BBCA.JK"]},
        "fields": ["instrument_id", "last_price", "quote_as_of"],
        "freshness": {
            "as_of": "2026-10-22T00:00:00Z",
            "max_staleness_s": 900,
            "allow_delayed": True,
        },
        "budget": {"requests": 100, "credits": 0, "seconds": 60},
        "retention_days": 7,
        "columns_version": "v1",
        "plan_hash": "sha256:" + "0" * 64,
    }
    plan.update(overrides)
    return plan


@asynccontextmanager
async def api_client(tmp_path: Path, monkeypatch: Any) -> AsyncIterator[Any]:
    """Yield an httpx2 AsyncClient wired to the ASGI app (no sockets)."""

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))

    from httpx2 import ASGITransport, AsyncClient

    from app.main import create_app

    application = create_app()
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://mee.test") as client:
        yield client


async def wait_for_terminal(client: Any, job_id: str, *, timeout_s: float = 5.0) -> dict[str, Any]:
    """Poll ``GET /requests/{id}`` until Success/Failure; fail on timeout."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    last: dict[str, Any] = {}
    while loop.time() < deadline:
        response = await client.get(f"/requests/{job_id}")
        assert response.status_code == 200, f"GET /requests/{job_id} -> {response.status_code}: {response.text}"
        last = response.json()
        if last.get("status") in {"Success", "Failure"}:
            return last
        await asyncio.sleep(0.02)
    raise AssertionError(f"job {job_id} did not reach a terminal status within {timeout_s}s; last={last!r}")
