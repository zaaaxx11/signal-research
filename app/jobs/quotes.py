"""Single-use quote store (02 §4.3).

A quote is a frozen plan with an expiry and a ``used_at`` CAS column. It is not
a reservation: consuming it twice must be impossible, and a foreign owner must
get the same 409 as a replay so quote ids cannot be probed.
"""

from __future__ import annotations

from pathlib import Path

from app.jobs.ports import QuoteRepository


async def open_quote_repository(db_path: str | Path) -> QuoteRepository:
    """Open the quote store for ``db_path``."""

    raise NotImplementedError(
        "single-use quote store is not implemented (Phase 0 RED): "
        f"open_quote_repository({db_path!r})"
    )
