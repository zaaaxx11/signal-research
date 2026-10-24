"""0003_fundamentals — ``fundamentals_snapshots`` (P11A).

One row per symbol, and the shape is the contract:

* ``fields`` is the JSON object of the 22 canonical fundamentals/statement
  cells. It is stored full width (missing cells ``null``) so a reader can tell
  "the provider has no P/E here" from "this key is not in the vocabulary".
* ``source`` names the rung that served the row, ``as_of`` dates the **data**
  (the provider's own stamp) and ``expires_at`` is ``fetched_at + 24 h`` — two
  clocks, because old data and an old fetch are not the same fact.
* ``symbol`` is the primary key: the write is an upsert, and two builds of one
  symbol must leave one row, not a history. An *older* build is refused by the
  statement's ``WHERE`` clause, so the window can only move forward.

Policy honoured as in ``0001_init``/``0002_screen``: additive; every ``NOT
NULL`` column that is not the identity either carries a server default or is
written on insert; ``downgrade`` drops the index before the table so a rollback
works on a copy of a live database.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fundamentals_snapshots",
        sa.Column("symbol", sa.Text(), primary_key=True),
        sa.Column(
            "instrument_id", sa.Text(), nullable=False, server_default=sa.text("''")
        ),
        sa.Column("fields", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("as_of", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column(
            "field_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("complete", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    #: The sweeper's read ("which rows are past their window") and the coverage
    #: report's fresh/expired split both scan this column.
    op.create_index(
        "ix_fundamentals_expiry", "fundamentals_snapshots", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_fundamentals_expiry", table_name="fundamentals_snapshots")
    op.drop_table("fundamentals_snapshots")
