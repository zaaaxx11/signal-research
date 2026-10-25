"""0004_field_sources — per-cell provenance on ``fundamentals_snapshots`` (P11F-A).

``0003`` stored **one** ``source`` per row. That answers "who served this symbol"
and cannot answer "who served this number": a merged row (the point of adding a
second source) holds cells from several places, and a reader comparing two
vendors' P/E has no way to tell which one they are looking at.

This revision adds one nullable ``TEXT`` column, ``field_sources``, holding a
JSON object of the 22-cell vocabulary mapped to the source that supplied each
cell (``null`` where the cell is empty or its origin is unknown):

* **Additive and nullable, deliberately.** Every pre-0004 row keeps
  ``field_sources IS NULL``; its own ``source`` still says who served all of its
  filled cells, and the snapshot reader applies that fallback. Backfilling with
  the row label here would be indistinguishable from a measured per-cell claim —
  the column exists precisely to tell those two apart.
* **No default, no index.** The column is read with its row (``SELECT *``), never
  filtered on: an index would cost the scan's bulk inserts and buy nothing.
* **Downgrade drops the column only.** The table, its rows and the
  ``ix_fundamentals_expiry`` index are untouched, so a rollback to ``0003``
  loses provenance and keeps the data — the safe direction for a live store.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

#: The table ``0003_fundamentals`` created; this revision only widens it.
TABLE = "fundamentals_snapshots"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(
            "field_sources",
            sa.Text(),
            nullable=True,
            comment="JSON: 22 canonical field -> source, null for an empty/unattributed cell",
        ),
    )


def downgrade() -> None:
    op.drop_column(TABLE, "field_sources")
