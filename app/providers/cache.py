"""Observation cache: TTL per class + a hard session boundary (02 §3.6).

The cache exists for one measurable reason: a **second identical job must make
zero provider requests**. Everything else here is what keeps that from becoming
a lie.

Key (02 §3.6, extended with the two fields the tests pin):

    sha256(provider | capability | instrument_id | canonical_params |
           as_of_bucket | session_date | schema_version)

* ``canonical_params`` — sorted field names, so the same cell set is one key;
* ``as_of_bucket`` — per class (quote: 1 minute, eod/fundamentals: 1 day);
* ``session_date`` — the trading day the request is *for*. A quote cached in
  yesterday's session is never served today, even when the TTL from
  ``fetched_at`` is still fresh: a stale price is worse than an empty cell;
* ``schema_version`` — a provider that changes shape invalidates its own rows.

TTL is measured from ``fetched_at`` (when the bytes arrived), never from
``as_of`` (what the data describes): a value fetched late may still be served
for its own timestamp, but fetching it does not extend its freshness.

This layer is intentionally in-process. The contract the tests pin is
"0 requests on an identical second run"; whether the rows also land in
``observations`` is a Phase-3 decision about retention, not a Fase-2 gate. The
interface is small enough that a SQLite-backed implementation can replace the
dict without touching the ladder.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from app.contracts import Capability
from app.providers.base import Verdict

#: TTL per capability, in seconds (02 §3.6 table). ``None`` = never expires
#: within the session, which the ``session_date`` key already bounds.
TTL_S: dict[Capability, float] = {
    Capability.QUOTE: 15.0,
    Capability.OPTIONS: 60.0,
    Capability.FX: 3600.0,
    Capability.EOD_BARS: 86400.0,
    Capability.FUNDAMENTALS: 86400.0,
    Capability.CORPORATE_ACTIONS: 86400.0,
    Capability.CONSTITUENTS: 7 * 86400.0,
    Capability.REFERENCE: 30 * 86400.0,
}

#: Bucket width per capability (seconds). A quote bucket is one minute so two
#: requests inside the same minute share a key; an EOD bucket is one day.
BUCKET_S: dict[Capability, float] = {
    Capability.QUOTE: 60.0,
    Capability.OPTIONS: 60.0,
    Capability.FX: 60.0,
    Capability.EOD_BARS: 86400.0,
    Capability.FUNDAMENTALS: 86400.0,
    Capability.CORPORATE_ACTIONS: 86400.0,
    Capability.CONSTITUENTS: 86400.0,
    Capability.REFERENCE: 86400.0,
}


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("cache keys require an aware ``as_of``")
    return moment.astimezone(UTC)


def session_date(moment: datetime) -> str:
    """The trading day a request is for (UTC date; the NYSE/XIDX close is later)."""

    return _utc(moment).date().isoformat()


def as_of_bucket(capability: Capability, moment: datetime) -> str:
    """Floor ``as_of`` to the class bucket so sub-minute jitter shares a key."""

    width = BUCKET_S.get(capability, 60.0)
    seconds = _utc(moment).timestamp()
    floored = seconds - (seconds % width)
    return datetime.fromtimestamp(floored, UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class CacheEntry:
    """One cached provider answer: the observations *and* the verdict that made them."""

    key: str
    provider: str
    capability: Capability
    instrument_id: str
    observations: tuple[Mapping[str, Any], ...]
    verdict: Verdict
    fetched_at: datetime
    expires_at: datetime
    hits: int = 0


@dataclass
class ObservationCache:
    """In-process cache with TTL + session-date keys. Never serves a stale quote."""

    ttl_s: Mapping[Capability, float] = field(default_factory=lambda: dict(TTL_S))
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    max_entries: int = 20_000
    _entries: dict[str, CacheEntry] = field(default_factory=dict, repr=False)

    # -- key ---------------------------------------------------------------

    def key(
        self,
        *,
        provider: str,
        capability: Capability,
        instrument_id: str,
        fields: Sequence[str],
        as_of: datetime,
        schema_version: str,
    ) -> str:
        material = {
            "provider": provider,
            "capability": capability.value,
            "instrument_id": instrument_id,
            "params": sorted(str(name) for name in fields),
            "as_of_bucket": as_of_bucket(capability, as_of),
            "session_date": session_date(as_of),
            "schema_version": schema_version,
        }
        blob = json.dumps(material, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    # -- read / write ------------------------------------------------------

    def get(
        self,
        *,
        provider: str,
        capability: Capability,
        instrument_id: str,
        fields: Sequence[str],
        as_of: datetime,
        schema_version: str,
    ) -> CacheEntry | None:
        """A live entry, or ``None``. Expired entries are dropped on read."""

        key = self.key(
            provider=provider,
            capability=capability,
            instrument_id=instrument_id,
            fields=fields,
            as_of=as_of,
            schema_version=schema_version,
        )
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self.clock() >= entry.expires_at:
            self._entries.pop(key, None)
            return None
        return entry

    def put(
        self,
        *,
        provider: str,
        capability: Capability,
        instrument_id: str,
        fields: Sequence[str],
        as_of: datetime,
        schema_version: str,
        observations: Sequence[Mapping[str, Any]],
        verdict: Verdict,
        fetched_at: datetime | None = None,
    ) -> CacheEntry:
        """Store a served answer. The TTL starts at ``fetched_at`` (02 §3.6)."""

        stamp = _utc(fetched_at or self.clock())
        ttl = self.ttl_s.get(capability, 60.0)
        entry = CacheEntry(
            key=self.key(
                provider=provider,
                capability=capability,
                instrument_id=instrument_id,
                fields=fields,
                as_of=as_of,
                schema_version=schema_version,
            ),
            provider=provider,
            capability=capability,
            instrument_id=instrument_id,
            observations=tuple(dict(item) for item in observations),
            verdict=verdict,
            fetched_at=stamp,
            expires_at=stamp + timedelta(seconds=ttl),
        )
        if len(self._entries) >= self.max_entries:
            # Bounded memory, oldest-first: a cache that grows without a ceiling
            # is a leak with a friendly name.
            oldest = sorted(self._entries.items(), key=lambda item: item[1].fetched_at)
            for key, _entry in oldest[: max(1, self.max_entries // 10)]:
                self._entries.pop(key, None)
        self._entries[entry.key] = entry
        return entry

    # -- maintenance -------------------------------------------------------

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


__all__ = [
    "BUCKET_S",
    "TTL_S",
    "CacheEntry",
    "ObservationCache",
    "as_of_bucket",
    "session_date",
]
