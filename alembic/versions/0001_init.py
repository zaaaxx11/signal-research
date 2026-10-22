"""0001_init — the nine MEE tables (02 §4.1–§4.9).

Rules honoured here (05 Langkah 3):

* additive + nullable: every ``NOT NULL`` column that is not part of the
  identity either has a ``server_default`` or is only written on insert;
* the partial unique index ``ux_instruments_isin`` is expressed as SQLite
  needs it (``WHERE isin IS NOT NULL``);
* ``downgrade`` drops in reverse dependency order, so a rollback on a copy of a
  live database is possible without fighting foreign keys.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("params", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'Pending'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.Text(), nullable=True),
        sa.Column("started_at", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.Column(
            "cancel_requested", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rows_target", sa.Integer(), nullable=True),
        sa.Column("rows_unresolved", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("result_mode", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("usage", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_jobs_claim", "jobs", ["status", "locked_at"])
    op.create_index("ix_jobs_owner", "jobs", ["api_key", "created_at"])

    op.create_table(
        "job_batches",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Text(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("batch_index", sa.Integer(), nullable=False),
        sa.Column("universe_slice", sa.Text(), nullable=False),
        sa.Column("symbols_expected", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'Pending'")),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.UniqueConstraint("job_id", "batch_index", name="ux_job_batches_job_index"),
    )

    op.create_table(
        "quotes",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("plan", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'Open'")),
        sa.Column("job_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("used_at", sa.Text(), nullable=True),
    )
    op.create_index("ix_quotes_owner", "quotes", ["api_key", "expires_at"])

    op.create_table(
        "instruments",
        sa.Column("instrument_id", sa.Text(), primary_key=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("mic", sa.Text(), nullable=False),
        sa.Column("exchange", sa.Text(), nullable=True),
        sa.Column("currency", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("asset_class", sa.Text(), nullable=False),
        sa.Column("instrument_type", sa.Text(), nullable=True),
        sa.Column("isin", sa.Text(), nullable=True),
        sa.Column("figi", sa.Text(), nullable=True),
        sa.Column("cusip", sa.Text(), nullable=True),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("first_seen", sa.Text(), nullable=False),
        sa.Column("last_seen", sa.Text(), nullable=False),
        sa.UniqueConstraint("mic", "symbol", name="ux_instruments_mic_symbol"),
    )
    op.create_index(
        "ux_instruments_isin",
        "instruments",
        ["isin"],
        unique=True,
        sqlite_where=sa.text("isin IS NOT NULL"),
    )

    op.create_table(
        "instrument_aliases",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("alias_norm", sa.Text(), nullable=False),
        sa.Column("alias_type", sa.Text(), nullable=False),
        sa.Column(
            "instrument_id",
            sa.Text(),
            sa.ForeignKey("instruments.instrument_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "alias_norm", "alias_type", "instrument_id", name="ux_aliases_norm_type_instrument"
        ),
    )

    op.create_table(
        "observations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "job_id", sa.Text(), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("instrument_id", sa.Text(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("value_num", sa.Float(), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=True),
        sa.Column("as_of", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column("verdict", sa.Text(), nullable=False, server_default=sa.text("'ok'")),
        sa.Column("trust", sa.Integer(), nullable=False),
        sa.Column("raw_sha256", sa.Text(), nullable=True),
    )
    op.create_index("ix_obs_lookup", "observations", ["instrument_id", "field", "as_of"])
    op.create_index("ix_obs_job", "observations", ["job_id"])

    op.create_table(
        "verification_rejections",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "job_id",
            sa.Text(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("instrument_id", sa.Text(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("rule", sa.Text(), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_rejections_job", "verification_rejections", ["job_id", "rule"])

    op.create_table(
        "job_artifacts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Text(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("job_id", "kind", name="ux_job_artifacts_job_kind"),
    )

    op.create_table(
        "provider_health",
        sa.Column("provider", sa.Text(), primary_key=True),
        sa.Column("capability", sa.Text(), primary_key=True),
        sa.Column("state", sa.Text(), nullable=False, server_default=sa.text("'closed'")),
        sa.Column("failures", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("opened_at", sa.Text(), nullable=True),
        sa.Column("cooldown_until", sa.Text(), nullable=True),
        sa.Column("last_verdict", sa.Text(), nullable=True),
        sa.Column("last_ok_at", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    for table in (
        "provider_health",
        "job_artifacts",
        "verification_rejections",
        "observations",
        "instrument_aliases",
        "instruments",
        "quotes",
        "job_batches",
        "jobs",
    ):
        op.drop_table(table)
