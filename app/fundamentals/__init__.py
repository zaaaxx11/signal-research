"""Fundamentals: one 24-hour snapshot per symbol, fed by the provider ladder.

The package is deliberately small. P11A ships the *foundation* the fundamentals
filters will read, not the forty-six-filter catalogue:

``snapshot``
    22 canonical cells per symbol (``app.contracts``' ``Fundamentals`` +
    ``Statements`` groups), the rung that served them, the data clock
    (``as_of``), the fetch clock, a 24 h ``expires_at``, an idempotent upsert, a
    nullable-cell rule (absence is ``null``, never ``0``) and a measured
    coverage report.

The builder takes an **injected** ladder (``Runtime.ladder()`` in production, a
scripted double in tests); it never constructs a provider, so the module stays
network-free unless a caller hands it one. A failed build writes nothing and
does not erase the last good row.
"""

from __future__ import annotations

from app.fundamentals.snapshot import (
    CACHED,
    COMPLETE,
    EMPTY,
    EMPTY_VERDICTS,
    FAILED,
    FUNDAMENTAL_FIELDS,
    PARTIAL,
    STATUSES,
    TABLE,
    TTL_S,
    BuildReport,
    BuildResult,
    FundamentalsLadder,
    FundamentalsSchemaError,
    FundamentalsSnapshot,
    FundamentalsSnapshotBuilder,
    SqliteFundamentalsStore,
    apply_migrations,
    canonical_symbol,
    coverage_report,
    instrument_for,
    iso,
    migration_hint,
    provider_symbol,
    snapshot_from_observations,
    utc_now,
)

__all__ = [
    "CACHED",
    "COMPLETE",
    "EMPTY",
    "EMPTY_VERDICTS",
    "FAILED",
    "FUNDAMENTAL_FIELDS",
    "PARTIAL",
    "STATUSES",
    "TABLE",
    "TTL_S",
    "BuildReport",
    "BuildResult",
    "FundamentalsLadder",
    "FundamentalsSchemaError",
    "FundamentalsSnapshot",
    "FundamentalsSnapshotBuilder",
    "SqliteFundamentalsStore",
    "apply_migrations",
    "canonical_symbol",
    "coverage_report",
    "instrument_for",
    "iso",
    "migration_hint",
    "provider_symbol",
    "snapshot_from_observations",
    "utc_now",
]
