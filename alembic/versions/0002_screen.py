"""0002_screen — ``screens`` + ``screen_rows`` (PLAN-screen.md §5).

Two tables, and the split is the product contract:

* ``screens`` is one snapshot per ``(region, sort)`` with the universe size in
  ``total_count`` and a five-minute window in ``expires_at``. ``filters_hash``
  names the exact filter recipe, so a snapshot is only reused for the same
  question.
* ``screen_rows`` holds **only the twenty displayed rows**. The export's ``all``
  scope re-scans on demand; storing 81k rows every five minutes would be a
  different product ("we keep the whole universe warm"), not a cache.

Policy honoured as in ``0001_init``: additive, every non-identity ``NOT NULL``
column carries a server default or is written on insert, and ``downgrade`` drops
in reverse dependency order so a rollback works on a copy of a live database.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "screens",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("sort", sa.Text(), nullable=False),
        sa.Column("filters_hash", sa.Text(), nullable=False),
        sa.Column("as_of", sa.Text(), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'tradingview'")),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
    )
    #: The browse read is "newest still-valid snapshot for (region, sort)".
    op.create_index("ix_screens_region_sort", "screens", ["region", "sort", "created_at"])
    op.create_index("ix_screens_expiry", "screens", ["expires_at"])

    op.create_table(
        "screen_rows",
        sa.Column(
            "screen_id",
            sa.Text(),
            sa.ForeignKey("screens.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("rank", sa.Integer(), primary_key=True),
        sa.Column("instrument_id", sa.Text(), nullable=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("exchange", sa.Text(), nullable=True),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("sector", sa.Text(), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("change_pct", sa.Float(), nullable=True),
        sa.Column("volume", sa.Float(), nullable=True),
        sa.Column("turnover", sa.Float(), nullable=True),
        sa.Column("turnover_usd", sa.Float(), nullable=True),
        sa.Column("market_cap_usd", sa.Float(), nullable=True),
        sa.Column(
            "is_primary", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )


def downgrade() -> None:
    op.drop_table("screen_rows")
    op.drop_index("ix_screens_expiry", table_name="screens")
    op.drop_index("ix_screens_region_sort", table_name="screens")
    op.drop_table("screens")
