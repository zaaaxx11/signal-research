"""0005_screen_facts — ``screen_facts``, the short/ownership cell store (P11J).

One row per symbol, and the shape is the contract:

* ``fields`` is the JSON object of the three-cell vocabulary
  (``short_float``, ``insider_own``, ``inst_own``), stored full width (missing
  cells ``null``) so a reader can tell "the page has no short float for this
  symbol" from "this key is not part of the vocabulary".
* ``source`` names the page that served the row (``finviz``), ``as_of`` dates
  the **data** and ``expires_at`` is ``fetched_at + 24 h`` — the same two
  clocks as ``fundamentals_snapshots``, because old data and an old fetch are
  not the same fact.
* ``symbol`` is the primary key: the write is an upsert, and two scrapes of one
  symbol must leave one row, not a history. An *older* build is refused by the
  statement's ``WHERE`` clause, so the window can only move forward.
* **Additive.** It creates one table and one index; nothing that existed before
  this revision is altered. A database upgraded from ``0004`` keeps every
  ``fundamentals_snapshots`` row untouched and simply gains an empty
  ``screen_facts`` table, which the selection join reads as "no facts yet" (its
  filters match nothing) rather than as an error.
* ``downgrade`` drops the index before the table so a rollback works on a copy
  of a live database, and leaves the fundamentals table alone.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

#: The one table this revision creates.
TABLE = "screen_facts"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("symbol", sa.Text(), primary_key=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("as_of", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("fields", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "field_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    #: The sweeper's read ("which rows are past their window") and coverage
    #: reports scan this column; the join itself reads the whole table once.
    op.create_index("ix_screen_facts_expiry", TABLE, ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_screen_facts_expiry", table_name=TABLE)
    op.drop_table(TABLE)
