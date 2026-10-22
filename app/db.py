"""Async engine/session factory plus SQLite PRAGMAs (02 §4, 05 R3/R5).

Two rules this module exists to enforce:

* **One schema, one source.** Tables are created by Alembic
  (``alembic/versions/0001_init.py``), never by ``Base.metadata.create_all`` in
  production paths — a *second* schema definition would drift silently.
* **PRAGMAs on every connection.** ``journal_mode=WAL`` (readers never block
  the writer), ``busy_timeout=5000`` (a second worker waits instead of failing)
  and ``foreign_keys=ON`` (SQLite defaults it **off**, so ``ON DELETE CASCADE``
  would be a comment without this).

``NullPool`` is deliberate: connections are cheap for SQLite and a pooled
connection bound to one event loop breaks the next loop (tests use one loop per
test). Engines stay reusable; connections do not.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = ROOT / "alembic.ini"
ALEMBIC_DIR = ROOT / "alembic"

#: Paths whose schema has already been ensured in this process.
_ENSURED: set[Path] = set()
_ENSURE_LOCK = threading.Lock()


def _sync_url(db_path: Path) -> str:
    return f"sqlite:///{db_path}"


def async_url(db_path: str | Path) -> str:
    return f"sqlite+aiosqlite:///{Path(db_path).resolve()}"


def build_engine(db_path: str | Path) -> AsyncEngine:
    """Build an async engine with WAL/busy_timeout/foreign_keys on every connect."""

    engine = create_async_engine(async_url(db_path), poolclass=NullPool, future=True)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            for pragma in (
                "journal_mode=WAL",
                "busy_timeout=5000",
                "foreign_keys=ON",
                "synchronous=NORMAL",
            ):
                cursor.execute(f"PRAGMA {pragma}")
                cursor.fetchall()
        finally:
            cursor.close()

    return engine


def alembic_config(db_path: str | Path) -> Config:
    """Alembic config pointed at ``db_path`` (also driveable with ``-x db=...``).

    The target URL is carried in ``config.attributes`` rather than only in
    ``sqlalchemy.url``: ``env.py`` must prefer an *explicit* target over
    ``$MEE_DB_PATH``, otherwise a long-lived process that migrates a second
    database would silently upgrade the one named by the environment.
    """

    resolved = Path(db_path).resolve()
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", _sync_url(resolved))
    config.attributes["db_url"] = _sync_url(resolved)
    return config


def _has_jobs_table(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    # ``closing`` matters: ``with sqlite3.connect(...)`` commits but does not
    # close, and the suite runs with ``filterwarnings = error`` — a connection
    # left to the GC becomes a ResourceWarning that fails an unrelated test.
    with closing(sqlite3.connect(db_path)) as connection:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'"
        ).fetchone()
    return row is not None


def ensure_schema(db_path: str | Path) -> None:
    """Apply ``alembic upgrade head`` once per database path (idempotent)."""

    path = Path(db_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _ENSURE_LOCK:
        if path in _ENSURED:
            return
        if not _has_jobs_table(path):
            command.upgrade(alembic_config(path), "head")
        _ENSURED.add(path)


async def ensure_schema_async(db_path: str | Path) -> None:
    """Run :func:`ensure_schema` off the event loop (Alembic is synchronous)."""

    await asyncio.to_thread(ensure_schema, db_path)


async def init_db(db_path: str | Path) -> AsyncEngine:
    """Create the engine, apply WAL/busy_timeout/foreign_keys, run migrations."""

    await ensure_schema_async(db_path)
    return build_engine(db_path)


__all__ = [
    "ALEMBIC_DIR",
    "ALEMBIC_INI",
    "async_url",
    "build_engine",
    "alembic_config",
    "ensure_schema",
    "ensure_schema_async",
    "init_db",
]
