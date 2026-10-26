"""Snapshot persistence for one ingested :class:`EngineDataset`.

Ingestion is a network pass; serving is not. This module is the seam between
them: one JSON document on disk that a restarted process can load without
touching EDGAR, plus the integrity checks that make "the file exists" different
from "the file is the snapshot this build wrote".

Contract (freeze before implementation)
---------------------------------------
* ``save(dataset)`` writes ``{"schema_version", "checksum", "dataset"}`` where
  ``checksum`` is ``sha256:<hex>`` over the canonical JSON of ``dataset``
  (``sort_keys=True``, ``separators=(",", ":")``, UTF-8). The write is atomic:
  a temporary file in the destination directory replaces the target with
  ``os.replace``, so a crash cannot leave half a snapshot.
* ``load()`` verifies schema version, checksum, and the shape of the dataset
  before constructing :class:`EngineDataset`. A missing, truncated, foreign or
  corrupt file raises :class:`SnapshotError` with a stable ``code``:
  ``snapshot_unreadable``, ``snapshot_invalid``, ``snapshot_version`` or
  ``snapshot_checksum``. It never returns a partially rebuilt dataset.
* ``exists()`` is a cheap readability check only; it is **not** a validity
  claim. Startup calls ``load`` and treats a failure as a visible error.
* Facts are stored per symbol and per metric with value, unit and provenance;
  restoring them must preserve provenance (source/as_of/status/url/inputs), not
  just the number. A restored master is loaded under the provenance
  ``source="snapshot"`` with the dataset's ``fetched_at`` — the snapshot is
  evidence of what the last verified load said, not a new SEC observation.
"""

from __future__ import annotations

from pathlib import Path

from app.engine.ingest import EngineDataset

#: Bumped only when the on-disk shape changes incompatibly.
SCHEMA_VERSION = 1


class SnapshotError(RuntimeError):
    """A snapshot file cannot be trusted as this build's dataset.

    ``code`` is stable so the startup path can report it without parsing the
    message: ``snapshot_unreadable``, ``snapshot_invalid``,
    ``snapshot_version`` or ``snapshot_checksum``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class EngineStore:
    """Read and write one dataset snapshot at ``path``."""

    SCHEMA_VERSION = SCHEMA_VERSION

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()

    def exists(self) -> bool:
        """Whether the snapshot file is present and readable."""

        raise NotImplementedError("EngineStore.exists is implemented in R2")

    def save(self, dataset: EngineDataset) -> Path:
        """Write ``dataset`` atomically and return the snapshot path."""

        raise NotImplementedError("EngineStore.save is implemented in R2")

    def load(self) -> EngineDataset:
        """Read and verify the snapshot, or raise :class:`SnapshotError`."""

        raise NotImplementedError("EngineStore.load is implemented in R2")


__all__ = ["SCHEMA_VERSION", "EngineStore", "SnapshotError"]
