"""Portable data locations.

The engine must run on a laptop, a VPS, or inside a container without editing
commands. Every location therefore resolves in one order:

1. the explicit environment variable (``MEE_ENGINE_CACHE_DIR``, …);
2. ``MEE_DATA_DIR`` / a subdirectory;
3. ``./data`` relative to the process working directory.

No absolute host path is baked in. The container sets ``MEE_DATA_DIR=/data``;
a systemd unit or a shell session can set the same variables, so the code does
not know which one it is running under.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DATA_DIRNAME = "data"
CACHE_DIRNAME = "cache"
BACKUP_DIRNAME = "backups"
SNAPSHOT_FILENAME = "engine-snapshot.json"


def _env_path(name: str) -> Path | None:
    """A non-blank environment path, expanded, or ``None``."""

    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else None


def data_dir() -> Path:
    """The data root: ``MEE_DATA_DIR`` or ``./data``."""

    return _env_path("MEE_DATA_DIR") or (Path.cwd() / DEFAULT_DATA_DIRNAME)


def cache_dir() -> Path:
    """The document cache: ``MEE_ENGINE_CACHE_DIR`` or ``<data>/cache``."""

    return _env_path("MEE_ENGINE_CACHE_DIR") or (data_dir() / CACHE_DIRNAME)


def snapshot_path() -> Path:
    """The dataset snapshot: ``MEE_ENGINE_SNAPSHOT`` or ``<data>/engine-snapshot.json``."""

    return _env_path("MEE_ENGINE_SNAPSHOT") or (data_dir() / SNAPSHOT_FILENAME)


def backup_dir() -> Path:
    """The backup directory: ``MEE_BACKUP_DIR`` or ``<data>/backups``."""

    return _env_path("MEE_BACKUP_DIR") or (data_dir() / BACKUP_DIRNAME)


__all__ = [
    "BACKUP_DIRNAME",
    "CACHE_DIRNAME",
    "DEFAULT_DATA_DIRNAME",
    "SNAPSHOT_FILENAME",
    "backup_dir",
    "cache_dir",
    "data_dir",
    "snapshot_path",
]
