"""Shared helpers for the Phase-1 RED suite (resolver, estimate, quote-over-HTTP).

Not a test module (leading underscore), so pytest never collects it.

Two things here are deliberate and documented in ``briefs/market-engine/10-phase1-red.md``:

* **Reference data is injected, not assumed.** ``reference_client`` points
  ``MEE_REFERENCE_PATH`` at ``tests/fixtures/reference_venue.json`` before the
  app is built. The fixture is therefore both the seed the resolver must read
  and the oracle the tests assert against: a resolver that answers from a
  hardcoded list drifts from the file and fails.
* **The database is read with ``sqlite3`` and the schema of 02 §4.** ``jobs.params``
  and ``quotes.plan`` are the only places where "the frozen plan won over the
  caller's input" can be observed without inventing an API field, so the tests
  read them directly. ``age_quote`` rewrites ``quotes.expires_at`` (a NOT NULL
  column in the frozen schema) instead of sleeping through the TTL.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_PATH = ROOT / "tests" / "fixtures" / "reference_venue.json"

#: ``as_of`` used by every request in the suite: fixed, so plans are comparable.
AS_OF = "2026-10-22T00:00:00Z"


def reference() -> dict[str, Any]:
    return json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))


REFERENCE: dict[str, Any] = reference()
INSTRUMENTS: list[dict[str, Any]] = list(REFERENCE["instruments"])
BY_ID: dict[str, dict[str, Any]] = {row["instrument_id"]: row for row in INSTRUMENTS}


def instrument_ids() -> set[str]:
    return set(BY_ID)


def ids_where(**criteria: Any) -> set[str]:
    """Instrument ids whose fixture row matches every ``criteria`` pair exactly."""

    return {
        row["instrument_id"]
        for row in INSTRUMENTS
        if all(row.get(key) == value for key, value in criteria.items())
    }


def symbol_of(instrument_id: str) -> str:
    return instrument_id.split(":", 1)[1]


# ---------------------------------------------------------------------------
# Request bodies (they bind ``app.contracts.MarketRequest``: one vocabulary)
# ---------------------------------------------------------------------------


def resolve_payload(text: str, **market: Any) -> dict[str, Any]:
    """A valid ``POST /markets/resolve`` body for a free-text query."""

    spec: dict[str, Any] = {"kind": "text", "text": text}
    spec.update(market)
    return {"market": spec, "as_of": AS_OF}


def estimate_payload(market: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    """A valid ``POST /markets/estimate`` body; ``target_rows`` is explicit."""

    body: dict[str, Any] = {
        "market": market if market is not None else {"kind": "text", "text": "banking in Indonesia"},
        "fields": None,
        "enrichment": ["quote"],
        "as_of": AS_OF,
        "max_staleness_s": 900,
        "allow_delayed": True,
        "budget": {"requests": 500, "credits": 0, "seconds": 300},
        "retention_days": 7,
        "target_rows": 200,
        "mode": "balanced",
    }
    body.update(overrides)
    return body


def enrich_with_quote_payload(quote_id: str, **overrides: Any) -> dict[str, Any]:
    """``POST /markets/enrich`` that presents a quote and a **different** universe.

    The point of the mismatch is gate 4 (02 §5 Fase 1): the frozen plan wins.
    """

    body: dict[str, Any] = {
        "market": {"kind": "watchlist", "symbols": ["NVDA"]},
        "fields": None,
        "enrichment": ["quote"],
        "as_of": "2026-01-05T00:00:00Z",
        "max_staleness_s": 900,
        "allow_delayed": True,
        "budget": {"requests": 500, "credits": 0, "seconds": 300},
        "format": ["csv"],
        "retention_days": 7,
        "quote_id": quote_id,
        "async": True,
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# ASGI client wired to the reference fixture
# ---------------------------------------------------------------------------


@asynccontextmanager
async def reference_client(
    tmp_path: Path,
    monkeypatch: Any,
    *,
    api_key: str | None = None,
    reference_path: Path | None = None,
) -> AsyncIterator[Any]:
    """Yield an ``httpx2`` client whose app reads ``reference_venue.json``."""

    monkeypatch.setenv("MEE_DB_PATH", str(tmp_path / "mee.db"))
    monkeypatch.setenv("MEE_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("MEE_REFERENCE_PATH", str(reference_path or REFERENCE_PATH))

    from httpx2 import ASGITransport, AsyncClient

    from app.main import create_app

    application = create_app()
    transport = ASGITransport(app=application)
    headers = {"x-api-key": api_key} if api_key is not None else {}
    async with AsyncClient(
        transport=transport, base_url="http://mee.test", headers=headers
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Frozen-schema reads (02 §4.1 jobs, §4.3 quotes)
# ---------------------------------------------------------------------------


def connect_db(tmp_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(tmp_path / "mee.db", timeout=10.0)
    connection.row_factory = sqlite3.Row
    return connection


def _rows(tmp_path: Path, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    # ``closing`` is not optional: the suite runs with ``filterwarnings = error``,
    # so a connection left to the GC becomes a PytestUnraisableExceptionWarning
    # that fails whichever test happens to be running.
    with closing(connect_db(tmp_path)) as connection:
        return [dict(row) for row in connection.execute(sql, params).fetchall()]


def job_rows(tmp_path: Path) -> list[dict[str, Any]]:
    return _rows(tmp_path, "SELECT * FROM jobs ORDER BY created_at")


def job_params(tmp_path: Path, job_id: str) -> dict[str, Any]:
    rows = _rows(tmp_path, "SELECT params FROM jobs WHERE id = ?", (job_id,))
    assert rows, f"no jobs row for {job_id}"
    return json.loads(rows[0]["params"])


def count_jobs(tmp_path: Path) -> int:
    return int(_rows(tmp_path, "SELECT COUNT(*) AS n FROM jobs")[0]["n"])


def quote_row(tmp_path: Path, quote_id: str) -> dict[str, Any]:
    rows = _rows(tmp_path, "SELECT * FROM quotes WHERE id = ?", (quote_id,))
    assert rows, f"no quotes row for {quote_id}"
    return rows[0]


def quote_plan(tmp_path: Path, quote_id: str) -> dict[str, Any]:
    plan = quote_row(tmp_path, quote_id)["plan"]
    return json.loads(plan) if isinstance(plan, str) else plan


def age_quote(tmp_path: Path, quote_id: str, *, seconds: int = 120) -> datetime:
    """Move a quote's ``expires_at`` into the past (TTL test without a sleep)."""

    expired_at = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    stamp = expired_at.isoformat(timespec="microseconds")
    last: Exception | None = None
    for _attempt in range(8):
        try:
            with closing(connect_db(tmp_path)) as connection:
                connection.execute(
                    "UPDATE quotes SET expires_at = ? WHERE id = ?", (stamp, quote_id)
                )
                connection.commit()
            return expired_at
        except sqlite3.OperationalError as exc:  # single-writer SQLite under WAL
            last = exc
    raise AssertionError(f"could not age quote {quote_id}: {last}")


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def canonical_json(value: Any) -> str:
    """Canonical bytes for a JSON document: key order and separators fixed."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def plan_symbols(plan: dict[str, Any]) -> list[str]:
    """Flatten ``plan.batches[*].universe_slice.symbols`` in batch order."""

    symbols: list[str] = []
    for batch in plan["batches"]:
        symbols.extend(batch["universe_slice"]["symbols"] or [])
    return symbols


def walk_strings(value: Any) -> Iterator[str]:
    """Every string (key or value) inside a JSON document, for tolerant asserts."""

    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_strings(item)


__all__ = [
    "AS_OF",
    "BY_ID",
    "INSTRUMENTS",
    "REFERENCE",
    "REFERENCE_PATH",
    "age_quote",
    "canonical_json",
    "connect_db",
    "count_jobs",
    "enrich_with_quote_payload",
    "estimate_payload",
    "ids_where",
    "instrument_ids",
    "job_params",
    "job_rows",
    "parse_dt",
    "plan_symbols",
    "quote_plan",
    "quote_row",
    "reference",
    "reference_client",
    "resolve_payload",
    "symbol_of",
    "walk_strings",
]
