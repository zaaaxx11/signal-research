"""Seed ``instruments`` / ``instrument_aliases`` from the reference master.

Phase 1 delivers a reference master with two venues (XNAS + XIDX) and the
schema already knows where instruments live (02 §4.4/§4.5, created by
``0001_init``). This module is the bridge: it copies the master into the two
tables so later phases can join observations to identity instead of re-reading
a JSON file.

Rules:

* **idempotent.** Every row is ``ON CONFLICT ... DO UPDATE`` keyed by
  ``instrument_id`` (and by ``(alias_norm, alias_type, instrument_id)`` for
  aliases). Running it twice is a no-op apart from ``last_seen``; there is no
  "truncate then insert" that could delete a row a job is pointing at.
* **never destructive.** ``first_seen`` is kept from the first seed; a row that
  disappears from the master is *not* deleted here (a delisting is a status
  change, not a missing row).
* **explicit.** Nothing calls this at import time. ``POST /markets/resolve``
  and ``/markets/estimate`` call it once per process/database, and
  ``python -m app.resolve.seed`` runs it on demand.

The alias confidence follows 02 §3.3: a ticker alias is exact (1.0), a name
alias is 0.95 — the floor at which an alias may write a row at all.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import text

from app.config import get_settings
from app.db import build_engine, ensure_schema_async
from app.resolve.aliases import normalize
from app.resolve.reference import Instrument, Reference, load_reference

_ALIAS_TYPE_SYMBOL = "ticker"
_ALIAS_TYPE_NAME = "name"
_ALIAS_SYMBOL_CONFIDENCE = 1.0
_ALIAS_NAME_CONFIDENCE = 0.95
_ALIAS_SOURCE = "reference_master"

_UPSERT_INSTRUMENT = text(
    """
    INSERT INTO instruments (
        instrument_id, symbol, mic, exchange, currency, name, asset_class,
        instrument_type, isin, country, status, first_seen, last_seen
    ) VALUES (
        :instrument_id, :symbol, :mic, :exchange, :currency, :name, :asset_class,
        :instrument_type, :isin, :country, :status, :now, :now
    )
    ON CONFLICT(instrument_id) DO UPDATE SET
        symbol = excluded.symbol,
        mic = excluded.mic,
        exchange = excluded.exchange,
        currency = excluded.currency,
        name = excluded.name,
        asset_class = excluded.asset_class,
        instrument_type = excluded.instrument_type,
        isin = excluded.isin,
        country = excluded.country,
        status = excluded.status,
        last_seen = excluded.last_seen
    """
)

_UPSERT_ALIAS = text(
    """
    INSERT INTO instrument_aliases (
        alias_norm, alias_type, instrument_id, source, confidence, created_at
    ) VALUES (
        :alias_norm, :alias_type, :instrument_id, :source, :confidence, :now
    )
    ON CONFLICT(alias_norm, alias_type, instrument_id) DO UPDATE SET
        confidence = excluded.confidence,
        source = excluded.source
    """
)


async def seed_reference(db_path: str | Path, reference: Reference) -> int:
    """Upsert every instrument and alias of ``reference``; return the row count."""

    await ensure_schema_async(db_path)
    engine = build_engine(db_path)
    now = _iso(datetime.now(UTC))
    try:
        async with engine.begin() as connection:
            instruments = [_instrument_params(row, now) for row in reference.instruments]
            aliases = [
                params
                for row in reference.instruments
                for params in _alias_params(row, now)
            ]
            if instruments:
                await connection.execute(_UPSERT_INSTRUMENT, instruments)
            if aliases:
                await connection.execute(_UPSERT_ALIAS, aliases)
    finally:
        await engine.dispose()
    return len(reference.instruments)


def _iso(moment: datetime) -> str:
    """ISO-8601 UTC text, the same shape the job/quote tables use (02 §4)."""

    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _instrument_params(instrument: Instrument, now: str) -> dict[str, Any]:
    return {
        "instrument_id": instrument.instrument_id,
        "symbol": instrument.symbol,
        "mic": instrument.mic,
        "exchange": instrument.exchange,
        "currency": instrument.currency,
        "name": instrument.name,
        "asset_class": instrument.asset_class,
        "instrument_type": instrument.instrument_type,
        "isin": instrument.isin,
        "country": instrument.country,
        "status": instrument.status,
        "now": now,
    }


def _alias_params(instrument: Instrument, now: str) -> Iterable[dict[str, Any]]:
    """One row per (normalised alias, alias_type), deduplicated per instrument."""

    rows: dict[tuple[str, str], float] = {}
    symbol = normalize(instrument.symbol)
    if symbol:
        rows[(symbol, _ALIAS_TYPE_SYMBOL)] = _ALIAS_SYMBOL_CONFIDENCE
    for value in (instrument.name, *instrument.aliases):
        key = normalize(value)
        if key:
            rows.setdefault((key, _ALIAS_TYPE_NAME), _ALIAS_NAME_CONFIDENCE)

    for (alias_norm, alias_type), confidence in sorted(rows.items()):
        yield {
            "alias_norm": alias_norm,
            "alias_type": alias_type,
            "instrument_id": instrument.instrument_id,
            "source": _ALIAS_SOURCE,
            "confidence": confidence,
            "now": now,
        }


def main() -> int:
    """``python -m app.resolve.seed`` — seed the database named by settings."""

    settings = get_settings()
    reference = load_reference(settings.reference_path)
    seeded = asyncio.run(seed_reference(settings.db_path, reference))
    print(
        f"seeded {seeded} instruments from {reference.path} into {Path(settings.db_path).resolve()}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())


__all__ = ["main", "seed_reference"]
