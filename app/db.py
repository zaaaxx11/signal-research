"""Async engine/session factory plus SQLite PRAGMAs (02 §4, 05 R5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


async def init_db(db_path: str | Path) -> Any:
    """Create the engine, apply WAL/busy_timeout/foreign_keys, run migrations."""

    raise NotImplementedError(
        f"database bootstrap is not implemented (Phase 0 RED): init_db({db_path!r})"
    )
