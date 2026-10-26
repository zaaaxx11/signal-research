"""Operational surface: metrics, cache retention, snapshot backup.

The engine is one process and one JSON snapshot. That is simple until the disk
fills or the box is lost: this module answers "how big is the cache", "what may
be deleted without breaking the fund map", and "can I restore a known-good
snapshot". It deliberately has no network and no vendor.

Contract: ``briefs/market-engine/R7-CONTRACT.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from collections.abc import Sequence
from typing import Any

from app.engine.service import EngineService


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """One cache file, with the two facts retention needs."""

    path: Path
    size: int
    mtime: float


def scan_cache(cache_dir: Path) -> tuple[CacheEntry, ...]:
    """List the cache files directly. Implemented in R7."""

    raise NotImplementedError("scan_cache is implemented in R7")


def cache_stats(cache_dir: Path) -> dict[str, object]:
    """Summarise the cache without exposing paths. Implemented in R7."""

    raise NotImplementedError("cache_stats is implemented in R7")


def dataset_metrics(
    service: EngineService, *, now: datetime | None = None
) -> dict[str, object]:
    """Describe the loaded dataset and its age. Implemented in R7."""

    raise NotImplementedError("dataset_metrics is implemented in R7")


def plan_retention(
    entries: Sequence[CacheEntry],
    *,
    max_bytes: int | None = None,
    max_age_days: float | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Choose cache files to delete. Implemented in R7."""

    raise NotImplementedError("plan_retention is implemented in R7")


def apply_retention(
    cache_dir: Path,
    *,
    max_bytes: int | None = None,
    max_age_days: float | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    """Apply the retention plan. Implemented in R7."""

    raise NotImplementedError("apply_retention is implemented in R7")


def backup_snapshot(
    snapshot: Path,
    dest_dir: Path,
    *,
    keep: int = 3,
    now: datetime | None = None,
) -> dict[str, object]:
    """Copy and rotate a snapshot. Implemented in R7."""

    raise NotImplementedError("backup_snapshot is implemented in R7")


def restore_snapshot(backup: Path, target: Path) -> dict[str, object]:
    """Restore a verified snapshot. Implemented in R7."""

    raise NotImplementedError("restore_snapshot is implemented in R7")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Implemented in R7."""

    raise NotImplementedError("main is implemented in R7")


__all__ = [
    "CacheEntry",
    "apply_retention",
    "backup_snapshot",
    "cache_stats",
    "dataset_metrics",
    "main",
    "plan_retention",
    "restore_snapshot",
    "scan_cache",
]
