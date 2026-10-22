"""Alembic environment for MEE.

The database URL is resolved in this order, so the same script works for the
service, for a test copy and for the Fase-0 gate:

1. ``alembic -x db=/tmp/copy.db upgrade head`` (explicit wins);
2. ``$MEE_DB_PATH`` (the process setting);
3. ``sqlalchemy.url`` in ``alembic.ini`` (repo-local default).

``render_as_batch=True`` is mandatory for SQLite: without it an ``ALTER TABLE``
that the next phase needs would be a no-op error instead of a rebuild.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import Base  # noqa: E402  (path setup must happen first)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    # 1. An explicit programmatic target (app.db.ensure_schema) always wins.
    attribute = config.attributes.get("db_url")
    if attribute:
        return str(attribute)
    # 2. The CLI form: alembic -x db=/tmp/copy.db upgrade head.
    override = context.get_x_argument(as_dictionary=True).get("db")
    if override:
        return f"sqlite:///{Path(override).resolve()}"
    # 3. The process setting.
    env_path = os.environ.get("MEE_DB_PATH")
    if env_path:
        return f"sqlite:///{Path(env_path).resolve()}"
    # 4. alembic.ini fallback.
    return config.get_main_option("sqlalchemy.url") or "sqlite:///mee.db"


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
