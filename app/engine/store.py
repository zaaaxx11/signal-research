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

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.engine.facts import _shared
from app.engine.ingest import EngineDataset
from app.engine.model import Cell, CellStatus, Provenance, SecurityRecord, Unit
from app.engine.security import SecurityMaster

#: Bumped only when the on-disk shape changes incompatibly.
SCHEMA_VERSION = 1

#: The canonical hash the envelope carries; the prefix names the algorithm so a
#: future digest can be introduced without guessing at a bare hex string.
_CHECKSUM_PREFIX = "sha256:"

#: ``SecurityRecord`` text fields that are allowed to be ``None`` on disk.
_OPTIONAL_SECURITY_TEXT: tuple[str, ...] = (
    "cik",
    "exchange",
    "mic",
    "country",
    "sector",
    "industry",
)

#: The keys a dataset body must carry; a missing one is a shape failure, not a
#: reason to serve defaults.
_DATASET_KEYS: tuple[str, ...] = ("fetched_at", "securities", "facts", "errors")


class SnapshotError(RuntimeError):
    """A snapshot file cannot be trusted as this build's dataset.

    ``code`` is stable so the startup path can report it without parsing the
    message: ``snapshot_unreadable``, ``snapshot_invalid``,
    ``snapshot_version`` or ``snapshot_checksum``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _invalid(message: str) -> SnapshotError:
    """A shape failure; returned rather than raised so callers can ``raise``."""

    return SnapshotError("snapshot_invalid", message)


def _canonical_bytes(document: object) -> bytes:
    """The exact bytes the contract hashes for one document.

    Key order and whitespace must not change the digest, or two byte-identical
    datasets written by different Python builds would disagree. ``sort_keys``
    and the compact separators are the normalisation; non-ASCII is kept literal
    so a security name survives a round trip without escaping.
    """

    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _checksum(document: object) -> str:
    """``sha256:<hex>`` over the canonical form of ``document``."""

    return _CHECKSUM_PREFIX + hashlib.sha256(_canonical_bytes(document)).hexdigest()


def _security_document(record: SecurityRecord) -> dict[str, object]:
    """One security row exactly as the persistence contract spells it."""

    return {
        "symbol": record.symbol,
        "name": record.name,
        "cik": record.cik,
        "exchange": record.exchange,
        "mic": record.mic,
        "asset_class": record.asset_class,
        "currency": record.currency,
        "country": record.country,
        "sector": record.sector,
        "industry": record.industry,
        "active": record.active,
    }


def _cell_document(cell: Cell) -> dict[str, object]:
    """One fact cell: value, unit and the whole provenance.

    Provenance is stored in full because a number without its source, as-of
    date and status cannot be told apart from a guess after a restart.
    """

    provenance = cell.provenance
    if provenance is None:  # defensive: Cell rejects a provenance-less value
        raise ValueError("a stored cell must carry provenance")
    return {
        "value": cell.value,
        "unit": cell.unit.value,
        "provenance": {
            "source": provenance.source,
            "as_of": provenance.as_of,
            "status": provenance.status.value,
            "url": provenance.url,
            "inputs": list(provenance.inputs),
        },
    }


def _dataset_document(dataset: EngineDataset) -> dict[str, object]:
    """The contract's ``dataset`` object for ``dataset``.

    Securities are written in symbol order and facts keep their mapping order;
    the checksum is order-independent, but a stable file makes a diff readable.
    """

    return {
        "fetched_at": dataset.fetched_at,
        "securities": [
            _security_document(record) for record in dataset.security_master.records()
        ],
        "facts": {
            symbol: {
                metric: _cell_document(cell) for metric, cell in cells.items()
            }
            for symbol, cells in dataset.facts.items()
        },
        "errors": dict(dataset.errors),
    }


def _require_mapping(value: object, *, where: str) -> Mapping[str, Any]:
    """``value`` as an object, or ``SnapshotError('snapshot_invalid')``."""

    if not isinstance(value, Mapping):
        raise _invalid(f"{where} must be a JSON object, got {type(value).__name__}")
    return value


def _require_sequence(value: object, *, where: str) -> Sequence[Any]:
    """``value`` as a JSON array (never a bare string), or invalid."""

    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _invalid(f"{where} must be a JSON array, got {type(value).__name__}")
    return value


def _require_text(value: object, *, where: str) -> str:
    """A non-blank string, or ``SnapshotError('snapshot_invalid')``."""

    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"{where} must be a non-blank string")
    return value


def _optional_text(value: object, *, where: str) -> str | None:
    """A string, ``None``, or invalid; used for the nullable identity fields."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise _invalid(f"{where} must be a string or null")
    return value


def _security_record(payload: object, *, index: int) -> SecurityRecord:
    """Rebuild one :class:`SecurityRecord` from its on-disk row."""

    where = f"dataset.securities[{index}]"
    entry = _require_mapping(payload, where=where)
    active = entry.get("active", True)
    if not isinstance(active, bool):
        raise _invalid(f"{where}.active must be a boolean")
    optional = {
        field: _optional_text(entry.get(field), where=f"{where}.{field}")
        for field in _OPTIONAL_SECURITY_TEXT
    }
    return SecurityRecord(
        symbol=_require_text(entry.get("symbol"), where=f"{where}.symbol"),
        name=_require_text(entry.get("name"), where=f"{where}.name"),
        cik=optional["cik"],
        exchange=optional["exchange"],
        mic=optional["mic"],
        asset_class=_require_text(
            entry.get("asset_class"), where=f"{where}.asset_class"
        ),
        currency=_require_text(entry.get("currency"), where=f"{where}.currency"),
        country=optional["country"],
        sector=optional["sector"],
        industry=optional["industry"],
        active=active,
    )


def _provenance(payload: object, *, where: str) -> Provenance:
    """Rebuild a :class:`Provenance`, including the enum status and inputs."""

    entry = _require_mapping(payload, where=where)
    status_text = _require_text(entry.get("status"), where=f"{where}.status")
    try:
        status = CellStatus(status_text)
    except ValueError as exc:
        raise _invalid(f"{where}.status is not a cell status: {status_text!r}") from exc
    raw_inputs = entry.get("inputs", ())
    inputs = _require_sequence(raw_inputs, where=f"{where}.inputs")
    if any(not isinstance(item, str) for item in inputs):
        raise _invalid(f"{where}.inputs must contain only strings")
    return Provenance(
        source=_require_text(entry.get("source"), where=f"{where}.source"),
        as_of=_require_text(entry.get("as_of"), where=f"{where}.as_of"),
        status=status,
        url=_optional_text(entry.get("url"), where=f"{where}.url"),
        # ``inputs`` is the audit trail (a tag and an accession per source row),
        # and a snapshot repeats the same accession across thousands of cells.
        # Pooling each element shares one object per distinct value instead of
        # one copy per row; the value and the order are unchanged.
        inputs=tuple(_shared(item) for item in inputs),
    )


def _cell(payload: object, *, where: str) -> Cell:
    """Rebuild one fact :class:`Cell`; the value may legitimately be null."""

    entry = _require_mapping(payload, where=where)
    if "value" not in entry:
        raise _invalid(f"{where} has no value")
    unit_text = _require_text(entry.get("unit"), where=f"{where}.unit")
    try:
        unit = Unit(unit_text)
    except ValueError as exc:
        raise _invalid(f"{where}.unit is not an engine unit: {unit_text!r}") from exc
    return Cell(
        value=entry["value"],
        unit=unit,
        provenance=_provenance(entry.get("provenance"), where=f"{where}.provenance"),
    )


def _dataset_body(document: object) -> Mapping[str, Any]:
    """The dataset object of a verified envelope, or ``snapshot_invalid``.

    Shape is checked here, after the checksum: a matching hash proves the bytes
    are the ones that were written, not that they describe a dataset. Missing
    or extra keys are refused because a silent default would change what a
    restored row claims.
    """

    body = _require_mapping(document, where="dataset")
    missing = [key for key in _DATASET_KEYS if key not in body]
    if missing:
        raise _invalid(f"dataset is missing {', '.join(missing)}")
    return body


def _rebuild_dataset(body: Mapping[str, Any]) -> EngineDataset:
    """Build one :class:`EngineDataset` from an already shape-checked body.

    The master is loaded under ``Provenance(source="snapshot", as_of=fetched_at)``
    because the snapshot is evidence of the last verified load, not a fresh SEC
    observation. Only one provenance is appended per symbol, so a reload cannot
    invent a second observation.
    """

    fetched_at = _require_text(body.get("fetched_at"), where="dataset.fetched_at")

    records = tuple(
        _security_record(entry, index=index)
        for index, entry in enumerate(
            _require_sequence(body.get("securities"), where="dataset.securities")
        )
    )
    master = SecurityMaster()
    master.load(
        records, Provenance(source="snapshot", as_of=fetched_at, status=CellStatus.OK)
    )

    raw_facts = _require_mapping(body.get("facts"), where="dataset.facts")
    facts: dict[str, Mapping[str, Cell]] = {}
    for symbol, cells in raw_facts.items():
        key = _require_text(symbol, where="dataset.facts key")
        cells_map = _require_mapping(cells, where=f"dataset.facts[{key!r}]")
        facts[key] = {
            _require_text(metric, where=f"dataset.facts[{key!r}] metric"): _cell(
                cell, where=f"dataset.facts[{key!r}][{metric!r}]"
            )
            for metric, cell in cells_map.items()
        }

    raw_errors = _require_mapping(body.get("errors"), where="dataset.errors")
    errors: dict[str, str] = {}
    for symbol, message in raw_errors.items():
        if not isinstance(symbol, str) or not isinstance(message, str):
            raise _invalid(
                "dataset.errors must map a symbol string to a message string"
            )
        errors[symbol] = message

    return EngineDataset(
        fetched_at=fetched_at,
        security_master=master,
        facts=facts,
        errors=errors,
    )


class EngineStore:
    """Read and write one dataset snapshot at ``path``."""

    SCHEMA_VERSION = SCHEMA_VERSION

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()

    def exists(self) -> bool:
        """Whether the snapshot file is present and readable.

        This deliberately does not parse or verify: startup uses it to decide
        *which* source owns the process, and then calls :meth:`load`, which
        reports why a file is unusable. A corrupt file must still count as
        present so startup fails closed instead of quietly ingesting.
        """

        try:
            with self.path.open("rb"):
                return True
        except OSError:
            return False

    def save(self, dataset: EngineDataset) -> Path:
        """Write ``dataset`` atomically and return the snapshot path.

        A temporary file in the destination directory is flushed, fsynced and
        then moved over the target with :func:`os.replace`. Reading a
        half-written snapshot is therefore impossible: a reader sees either the
        previous file or the complete new one. The temporary is removed on any
        failure so a crash cannot leave litter beside the snapshot.
        """

        if not isinstance(dataset, EngineDataset):
            raise TypeError(
                "EngineStore.save needs an EngineDataset, got "
                f"{type(dataset).__name__}"
            )
        body = _dataset_document(dataset)
        document = {
            "schema_version": SCHEMA_VERSION,
            "checksum": _checksum(body),
            "dataset": body,
        }
        text = json.dumps(document, ensure_ascii=False, indent=2)
        self._write_atomically(text)
        return self.path

    def _write_atomically(self, text: str) -> None:
        """Publish ``text`` at :attr:`path` with no intermediate state visible."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        )
        try:
            with temporary:
                temporary.write(text)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary.name, self.path)
        except BaseException:
            Path(temporary.name).unlink(missing_ok=True)
            raise

    def load(self) -> EngineDataset:
        """Read and verify the snapshot, or raise :class:`SnapshotError`.

        The checks run in a fixed order so the reported code is the first thing
        that is actually wrong: readable? decodable? an envelope with this
        build's version? the bytes the checksum covers? a well-shaped dataset?
        Collapsing them would report ``snapshot_invalid`` for a foreign version
        and hide the migration that is needed.
        """

        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SnapshotError(
                "snapshot_unreadable",
                f"cannot read snapshot {self.path}: {type(exc).__name__}: {exc}",
            ) from exc

        try:
            document = json.loads(text)
        except ValueError as exc:
            raise SnapshotError(
                "snapshot_invalid",
                f"snapshot {self.path} is not valid JSON "
                f"(a truncated write has no recovery): {exc}",
            ) from exc

        envelope = _require_mapping(document, where=f"snapshot {self.path}")
        version = envelope.get("schema_version")
        if version != SCHEMA_VERSION:
            found = "missing" if version is None else repr(version)
            raise SnapshotError(
                "snapshot_version",
                f"snapshot {self.path} has schema_version {found}; "
                f"this build reads {SCHEMA_VERSION}",
            )

        stored = envelope.get("checksum")
        if not isinstance(stored, str) or not stored:
            # A missing digest is "cannot be verified", not "wrong shape": the
            # operator's action is the same as a mismatch — regenerate the file.
            raise SnapshotError(
                "snapshot_checksum",
                f"snapshot {self.path} carries no checksum; it cannot be verified",
            )
        body = envelope.get("dataset")
        actual = _checksum(body)
        if stored != actual:
            raise SnapshotError(
                "snapshot_checksum",
                f"snapshot {self.path} checksum {stored} does not match "
                f"the dataset bytes ({actual}); the file is corrupt or edited",
            )

        verified = _dataset_body(body)
        try:
            return _rebuild_dataset(verified)
        except SnapshotError:
            raise
        except (TypeError, ValueError) as exc:
            raise _invalid(
                f"snapshot {self.path} does not describe a dataset: "
                f"{type(exc).__name__}: {exc}"
            ) from exc


__all__ = ["SCHEMA_VERSION", "EngineStore", "SnapshotError"]
