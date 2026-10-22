"""SQLAlchemy ORM models for the nine tables (02 §4).

The schema is *data*: it is declared once here and once in
``alembic/versions/0001_init.py``. Migrations are additive and nullable so an
``alembic upgrade head`` on a copy of a live database never drops a row.

Timestamps are ISO-8601 UTC strings in ``TEXT`` columns
(``YYYY-MM-DDTHH:MM:SS.ffffff+00:00``), which keeps lexical order equal to
chronological order and matches the DDL in 02 §4 exactly. JSON payloads
(``params``, ``usage``, ``plan``) are TEXT.
"""

from __future__ import annotations

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    Float,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all MEE tables."""


class Job(Base):
    """02 §4.1 — one enrichment job. ``status`` is the queue."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)
    service: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'Pending'"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    worker_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    locked_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows_unresolved: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    result_mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_jobs_claim", "status", "locked_at"),
        Index("ix_jobs_owner", "api_key", "created_at"),
    )


class JobBatch(Base):
    """02 §4.2 — per-batch checkpoint. ``Skipped`` is not an error."""

    __tablename__ = "job_batches"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        Text, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    batch_index: Mapped[int] = mapped_column(Integer, nullable=False)
    universe_slice: Mapped[str] = mapped_column(Text, nullable=False)
    symbols_expected: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'Pending'"))
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("job_id", "batch_index", name="ux_job_batches_job_index"),
    )


class Quote(Base):
    """02 §4.3 — single-use quote. ``expires_at`` is NOT NULL on purpose."""

    __tablename__ = "quotes"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)
    plan: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'Open'"))
    job_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    used_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_quotes_owner", "api_key", "expires_at"),)


class Instrument(Base):
    """02 §4.4 — ``instrument_id`` (``<MIC>:<symbol>``) is the dedupe key."""

    __tablename__ = "instruments"

    instrument_id: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    mic: Mapped[str] = mapped_column(Text, nullable=False)
    exchange: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_class: Mapped[str] = mapped_column(Text, nullable=False)
    instrument_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    isin: Mapped[str | None] = mapped_column(Text, nullable=True)
    figi: Mapped[str | None] = mapped_column(Text, nullable=True)
    cusip: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    first_seen: Mapped[str] = mapped_column(Text, nullable=False)
    last_seen: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("mic", "symbol", name="ux_instruments_mic_symbol"),
        # Partial index: many instruments have no ISIN, but a present one is unique.
        Index(
            "ux_instruments_isin",
            "isin",
            unique=True,
            sqlite_where=text("isin IS NOT NULL"),
        ),
    )


class InstrumentAlias(Base):
    """02 §4.5 — aliases *propose* identities; they never become one."""

    __tablename__ = "instrument_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alias_norm: Mapped[str] = mapped_column(Text, nullable=False)
    alias_type: Mapped[str] = mapped_column(Text, nullable=False)
    instrument_id: Mapped[str] = mapped_column(
        Text, ForeignKey("instruments.instrument_id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "alias_norm", "alias_type", "instrument_id", name="ux_aliases_norm_type_instrument"
        ),
    )


class ObservationRow(Base):
    """02 §4.6 — append-only; the source of truth for provenance.

    Named ``ObservationRow`` so it never collides with the ``Observation``
    contract in ``app.contracts``: one is a table, the other is a payload.
    """

    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    instrument_id: Mapped[str] = mapped_column(Text, nullable=False)
    field: Mapped[str] = mapped_column(Text, nullable=False)
    value_num: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    as_of: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("1.0"))
    verdict: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ok'"))
    trust: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_obs_lookup", "instrument_id", "field", "as_of"),
        Index("ix_obs_job", "job_id"),
    )


class VerificationRejection(Base):
    """02 §4.7 — without this, "empty" and "not provided" are indistinguishable."""

    __tablename__ = "verification_rejections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        Text, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    instrument_id: Mapped[str] = mapped_column(Text, nullable=False)
    field: Mapped[str] = mapped_column(Text, nullable=False)
    rule: Mapped[str] = mapped_column(Text, nullable=False)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ix_rejections_job", "job_id", "rule"),)


class JobArtifact(Base):
    """02 §4.8 — ``UNIQUE(job_id, kind)`` makes a retry replace, not stack."""

    __tablename__ = "job_artifacts"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        Text, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (UniqueConstraint("job_id", "kind", name="ux_job_artifacts_job_kind"),)


class ProviderHealth(Base):
    """02 §4.9 — breaker state in the DB, so a second worker honours it too."""

    __tablename__ = "provider_health"

    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    capability: Mapped[str] = mapped_column(Text, primary_key=True)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'closed'"))
    failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    opened_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    cooldown_until: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verdict: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_ok_at: Mapped[str | None] = mapped_column(Text, nullable=True)


#: The nine tables, in dependency order (parents before children).
TABLES: tuple[str, ...] = (
    "jobs",
    "job_batches",
    "quotes",
    "instruments",
    "instrument_aliases",
    "observations",
    "verification_rejections",
    "job_artifacts",
    "provider_health",
)

__all__ = [
    "Base",
    "Instrument",
    "InstrumentAlias",
    "Job",
    "JobArtifact",
    "JobBatch",
    "ObservationRow",
    "ProviderHealth",
    "Quote",
    "TABLES",
    "VerificationRejection",
]
