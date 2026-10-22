"""Deterministic fake provider for Phase 0 — the only provider that exists.

It reads the same fixture the tests use (``tests/fixtures/fake_universe.json``)
and never touches the network, so the whole Phase-0 path can be exercised with
egress blocked. Every row is labelled ``primary_source = "fake"``: synthetic
prices must never be mistaken for real ones downstream.

Fixture resolution order (first hit wins):

1. ``$MEE_FAKE_FIXTURE`` — lets an operator point the fake at another universe;
2. ``<repo>/tests/fixtures/fake_universe.json`` — the Phase-0 default;
3. ``<cwd>/tests/fixtures/fake_universe.json`` — when the package is installed
   but the repo is the working directory.

A real adapter has the same three methods (``fetch`` → ``verdict`` →
``parse``); the fake differs only in that the response is a file.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.contracts import CANONICAL_COLUMNS_V1, Capability
from app.providers.base import ProviderSpec, Verdict

FAKE_NAME = "fake"
FIXTURE_ENV = "MEE_FAKE_FIXTURE"
ROOT = Path(__file__).resolve().parents[3]
_COLUMNS = frozenset(CANONICAL_COLUMNS_V1)


def fixture_path() -> Path:
    """Locate the fake universe file without importing anything from ``tests``."""

    override = os.environ.get(FIXTURE_ENV)
    if override:
        path = Path(override)
        if not path.exists():
            raise FileNotFoundError(f"{FIXTURE_ENV} points at a missing file: {path}")
        return path

    for candidate in (
        ROOT / "tests" / "fixtures" / "fake_universe.json",
        Path.cwd() / "tests" / "fixtures" / "fake_universe.json",
    ):
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "fake provider fixture not found; set "
        f"{FIXTURE_ENV} or run from the repository root"
    )


def _supports(capability: str, field: str) -> bool:
    """The fake declares what it can serve: quote cells of canonical columns."""

    return capability == Capability.QUOTE.value and field in _COLUMNS


def fake_spec() -> ProviderSpec:
    """Provider spec for the fake rung (T0, no key, one capability).

    Only ``QUOTE`` is declared. Declaring all eight capabilities because "it
    could" would make ``/providers`` claim coverage the fake does not have.
    """

    return ProviderSpec(
        name=FAKE_NAME,
        capabilities=frozenset({Capability.QUOTE}),
        cost_tier="T0",
        trust=1,
        delay="realtime",
        auth=None,
        rate_limit=(0, 0.0),
        supports=_supports,
        schema_version="fake/v1",
    )


class FakeProvider:
    """In-process provider: same interface as a real adapter, zero egress."""

    name = FAKE_NAME

    def __init__(self, fixture: str | Path | None = None) -> None:
        self._fixture = Path(fixture) if fixture is not None else None
        self._cache: dict[str, Any] | None = None

    # -- fixture -----------------------------------------------------------

    def load(self) -> dict[str, Any]:
        """Read and index the fixture once per instance."""

        if self._cache is None:
            path = self._fixture or fixture_path()
            payload = json.loads(path.read_text(encoding="utf-8"))
            index: dict[str, dict[str, Any]] = {}
            for row in payload.get("rows", []):
                for key in (row.get("symbol"), row.get("instrument_id")):
                    if key:
                        index[str(key).upper()] = row
            payload["_index"] = index
            self._cache = payload
        return self._cache

    # -- adapter interface -------------------------------------------------

    async def fetch(
        self, capability: Capability, symbols: Sequence[str], fields: set[str]
    ) -> Mapping[str, Any]:
        """Return the raw payload; no sockets, no sleeps, no randomness."""

        payload = self.load()
        wanted = {str(symbol).upper() for symbol in symbols}
        rows = {
            symbol: dict(row)
            for symbol, row in payload["_index"].items()
            if symbol in wanted
        }
        body = {
            "provider": FAKE_NAME,
            "capability": capability.value,
            "as_of": payload.get("as_of"),
            "fetched_at": payload.get("fetched_at"),
            "fields": sorted(fields),
            "rows": rows,
        }
        # ``bytes`` is what a meter would record; hashing the body keeps the
        # number honest without a fake HTTP layer.
        body["bytes"] = len(json.dumps(body, sort_keys=True).encode("utf-8"))
        body["raw_sha256"] = hashlib.sha256(
            json.dumps(body["rows"], sort_keys=True).encode("utf-8")
        ).hexdigest()
        return body

    def verdict(self, raw: Mapping[str, Any], symbol: str, capability: Capability | None = None) -> Verdict:
        """Content-based verdict: missing symbol is ``not_found``, empty row is ``empty``."""

        key = str(symbol).upper()
        rows = raw.get("rows") or {}
        row = rows.get(key)
        if row is None:
            return Verdict(ok=False, kind="not_found", detail=f"fake has no instrument {symbol!r}")
        if not row:
            return Verdict(ok=False, kind="empty", detail=f"fake returned no cells for {symbol!r}")
        return Verdict(ok=True, kind="ok")

    def parse(self, raw: Mapping[str, Any], symbol: str) -> list[dict[str, Any]]:
        """One canonical row per symbol, keyed by canonical column names."""

        key = str(symbol).upper()
        row = (raw.get("rows") or {}).get(key)
        if row is None:
            return []
        return [{column: row.get(column) for column in CANONICAL_COLUMNS_V1 if column in row}]

    def fetched_at(self) -> datetime:
        """Parsed ``fetched_at`` of the fixture (aware UTC)."""

        payload = self.load()
        raw = payload.get("fetched_at")
        return datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else datetime.now(UTC)


__all__ = ["FAKE_NAME", "FIXTURE_ENV", "FakeProvider", "fake_spec", "fixture_path"]
