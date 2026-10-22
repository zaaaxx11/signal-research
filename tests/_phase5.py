"""Shared helpers and fixtures for the Phase-5 RED suite.

Not a test module (leading underscore), so pytest never collects it. Every
contract this module pins is expanded in ``briefs/market-engine/18-phase5-red.md``.

Contracts pinned here
---------------------

* **One run writes four data files.** ``app.export.pipeline.export_job(rows,
  *, job_id, directory, formats=DEFAULT_FORMATS, columns=..., rows_per_shard=
  SHARD_ROWS, xlsx_sheet_rows=XLSX_SHEET_ROW_LIMIT, metadata=None, resume=True,
  on_shard=None, expected_rows=None) -> ExportReport`` writes ``<job>.csv``,
  ``<job>.xlsx``, ``<job>.jsonl``, ``<job>.parquet``, ``<job>.manifest.json`` and
  ``<job>.custody.jsonl`` into ``directory``, staging shards under one hidden
  ``.<job>.parts/`` directory (``shard-000.jsonl``...). ``ExportReport`` exposes
  ``files`` (fmt -> Path), ``manifest_path``, ``custody_path``, ``row_count``,
  ``content_sha256``, ``shards_written``, ``shards_skipped`` and ``resumed``.
* **Cells are the source cells.** ``None`` is ``""`` in CSV, ``null`` in JSONL
  and blank in XLSX; ``0`` and ``False`` stay values; text starting with
  ``= + - @ \\t \\r`` gets a ``'`` prefix in CSV/XLSX; a list/dict becomes compact
  JSON text in every format; dates become ISO-8601. Numeric XLSX cells are
  numbers, never strings, and no cell is a formula.
* **Parquet has an explicit schema.** One column per canonical name, in order;
  bool -> bool, int -> int64, int+float -> double, anything else (str, date,
  list, all-null, mixed) -> string. Schema metadata carries
  ``mee.columns_version = b"v1"``.
* **Content identity.** ``content_sha256`` is the SHA-256 of the canonical rows
  serialised as ``json.dumps([cell, ...], ensure_ascii=False,
  separators=(",", ":")) + "\\n"`` in canonical column order, with dates as ISO
  strings and containers kept as JSON. It does not depend on shard size or on
  which formats were requested.
* **Manifest is about the data files, not about itself.** ``files`` lists one
  entry per data file with ``name``, ``bytes``, ``sha256`` and ``row_count``;
  the manifest and the custody log never appear in it. ``row_count``,
  ``columns``, ``columns_version``, ``files_count``, ``options`` and
  ``coverage`` make it self-describing; ``coverage.rows_expected``,
  ``coverage.rows_written`` and ``coverage.by_format[*]`` must all equal the
  source row count, and ``coverage.ratio == 1.0``. ``verify_manifest(path)``
  recomputes every hash and returns ``ok``, ``problems``, ``files_checked``.
* **Retention never guesses.** ``DEFAULT_RETENTION_DAYS == 30``. A job is
  purged only when it is expired (``expires_at <= now``) **and** older than the
  policy's day floor; files are unlinked before any row is deleted; one failed
  unlink leaves every row in place (the DB mutation is one transaction).
  ``SweepReport`` exposes ``purged``, ``retained``, ``aborted``, ``dry_run``,
  ``files_deleted``, ``bytes_freed``, ``failed`` (``job_id``/``path``/``error``)
  and ``custody_path``; ``sweep`` appends ``retention_purged`` records to
  ``<directory>/retention.custody.jsonl``.

The column list is read from the frozen spec (``tests/spec/columns_v1.json``),
never from the implementation, so a broken constant cannot satisfy a test by
definition.
"""

from __future__ import annotations

import csv
import hashlib
import importlib
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from tests._support import COLUMNS

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "export_rows_v1.json"

#: Fixed clock for the retention fixtures (aware UTC, never naive).
NOW = datetime.now(UTC).replace(microsecond=0)

#: The five formats the export layer knows; four of them are data files.
DATA_FORMATS: tuple[str, ...] = ("csv", "xlsx", "jsonl", "parquet")

JOB_ID = "job-phase5"

#: Text prefixes a spreadsheet would execute (02 §3.8.2).
INJECTION_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")

DATE_COLUMNS: tuple[str, ...] = (
    "listing_date",
    "session_date",
    "first_bar_date",
    "last_bar_date",
    "opt_expiry",
)
DATETIME_COLUMNS: tuple[str, ...] = ("quote_as_of", "as_of_achieved")

DEFAULT_METADATA: dict[str, Any] = {
    "request_hash": "sha256:" + "1" * 64,
    "plan_hash": "sha256:" + "2" * 64,
    "as_of_requested": "2026-10-22T00:00:00+00:00",
    "providers": [
        {"name": "fake", "capability": "quote", "requests": 6, "verdicts": {"ok": 6}}
    ],
    "usage": {"requests_total": 6, "bytes_total": 1843112, "credits_total": 0, "cache_hits": 2},
    "quality": {"filled_ratio": 0.71, "verified_ratio": 0.93, "rejections_by_rule": {"V14": 3}},
}


# ---------------------------------------------------------------------------
# Module loading with RED-shaped failures
# ---------------------------------------------------------------------------


def _load(module_name: str) -> Any:
    """Import a Phase-5 module, turning a missing module into a RED failure."""

    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:  # pragma: no cover - RED path
        raise NotImplementedError(
            f"{module_name} is not implemented yet (Fase 5 RED): {exc}"
        ) from exc


def _require(module: Any, *names: str) -> Any:
    missing = [name for name in names if not hasattr(module, name)]
    if missing:  # pragma: no cover - RED path
        raise NotImplementedError(
            f"{module.__name__} is missing " + ", ".join(missing) + " (Fase 5 RED)"
        )
    if len(names) == 1:
        return getattr(module, names[0])
    return tuple(getattr(module, name) for name in names)


def writer_module() -> Any:
    module = _load("app.export.writer")
    _require(module, "write_export", "xlsx_sheet_names", "XLSX_SHEET_ROW_LIMIT")
    return module


def manifest_module() -> Any:
    module = _load("app.export.manifest")
    _require(module, "read_manifest", "verify_manifest", "read_custody")
    return module


def pipeline_module() -> Any:
    module = _load("app.export.pipeline")
    _require(
        module,
        "export_job",
        "plan_shards",
        "SHARD_ROWS",
        "DEFAULT_FORMATS",
        "ExportCoverageError",
    )
    return module


def cleanup_module() -> Any:
    module = _load("app.export.cleanup")
    _require(module, "sweep", "DEFAULT_RETENTION_DAYS")
    return module


# ---------------------------------------------------------------------------
# Fixture: six canonical rows that between them cover every cell shape
# ---------------------------------------------------------------------------


def raw_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def raw_rows() -> list[dict[str, Any]]:
    return [dict(row) for row in raw_fixture()["rows"]]


def _typed(value: Any, column: str) -> Any:
    if value is None:
        return None
    if column in DATETIME_COLUMNS and isinstance(value, str):
        return datetime.fromisoformat(value)
    if column in DATE_COLUMNS and isinstance(value, str):
        return date.fromisoformat(value)
    return value


def source_rows() -> list[dict[str, Any]]:
    """The fixture with real ``date``/``datetime`` objects, in canonical order.

    A key mismatch is an ``AssertionError`` here rather than a confusing diff
    deep inside a writer test: the fixture *is* the table.
    """

    rows: list[dict[str, Any]] = []
    for raw in raw_rows():
        assert list(raw) == COLUMNS, (
            "fixture row keys drifted from columns_v1.json: "
            f"{[c for c in COLUMNS if c not in raw][:5]} missing"
        )
        rows.append({column: _typed(raw[column], column) for column in COLUMNS})
    return rows


def bulk_rows(count: int, *, prefix: str = "BULK") -> list[dict[str, Any]]:
    """``count`` copies of the full fixture row with unique identities."""

    base = source_rows()[0]
    rows: list[dict[str, Any]] = []
    for index in range(count):
        row = dict(base)
        row["instrument_id"] = f"XNAS:{prefix}{index:04d}"
        row["symbol"] = f"{prefix}{index:04d}"
        row["name"] = f"{prefix} Instruments {index:04d}"
        rows.append({column: row[column] for column in COLUMNS})
    return rows


def resume_rows() -> list[dict[str, Any]]:
    """23 rows: five shards at ``rows_per_shard=5``, failure point at shard 3."""

    return bulk_rows(23, prefix="RES")


# ---------------------------------------------------------------------------
# Cell oracles: the contract, written out independently of the writer
# ---------------------------------------------------------------------------


def _container_text(value: Sequence[Any] | Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _prefixed(text: str) -> str:
    return "'" + text if text[:1] in INJECTION_PREFIXES else text


def csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, dict)):
        return _prefixed(_container_text(value))
    return _prefixed(str(value))


def json_cell(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def xlsx_cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, dict)):
        return _prefixed(_container_text(value))
    return _prefixed(str(value))


def parquet_cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, dict)):
        return _container_text(value)
    return str(value)


def csv_row(row: Mapping[str, Any]) -> list[str]:
    return [csv_cell(row.get(column)) for column in COLUMNS]


def json_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {column: json_cell(row.get(column)) for column in COLUMNS}


def xlsx_row(row: Mapping[str, Any]) -> list[Any]:
    return [xlsx_cell(row.get(column)) for column in COLUMNS]


def parquet_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {column: parquet_cell(row.get(column)) for column in COLUMNS}


def parquet_type(values: Iterable[Any]) -> str:
    """The declared Parquet type for a column, from its source values."""

    kinds: set[str] = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            kinds.add("bool")
        elif isinstance(value, int):
            kinds.add("int")
        elif isinstance(value, float):
            kinds.add("float")
        else:
            kinds.add("string")
    if not kinds:
        return "string"
    if kinds == {"bool"}:
        return "bool"
    if kinds <= {"int"}:
        return "int64"
    if kinds <= {"int", "float"}:
        return "double"
    return "string"


def content_sha256(rows: Sequence[Mapping[str, Any]]) -> str:
    """SHA-256 of the canonical table: one JSON array of cells per row."""

    digest = hashlib.sha256()
    for row in rows:
        line = json.dumps(
            [json_cell(row.get(column)) for column in COLUMNS],
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Readers (independent of the writer under test)
# ---------------------------------------------------------------------------


def read_csv_table(path: str | Path) -> list[list[str]]:
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def read_xlsx_sheet(path: str | Path, title: str) -> list[list[Any]]:
    from openpyxl import load_workbook

    workbook = load_workbook(filename=str(path), data_only=False)
    try:
        sheet = workbook[title]
        return [list(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()


def read_parquet(path: str | Path) -> Any:
    import pyarrow.parquet as pq

    return pq.read_table(str(path))


# ---------------------------------------------------------------------------
# Export entry points
# ---------------------------------------------------------------------------


def export_job(
    rows: Iterable[Mapping[str, Any]],
    *,
    directory: str | Path,
    job_id: str = JOB_ID,
    formats: Sequence[str] | None = None,
    rows_per_shard: int | None = None,
    xlsx_sheet_rows: int | None = None,
    metadata: Mapping[str, Any] | None = None,
    resume: bool = True,
    on_shard: Callable[[int, int], None] | None = None,
    expected_rows: int | None = None,
) -> Any:
    """Call ``pipeline.export_job`` with the suite's frozen defaults."""

    module = pipeline_module()
    kwargs: dict[str, Any] = {
        "job_id": job_id,
        "directory": Path(directory),
        "columns": COLUMNS,
        "resume": resume,
    }
    if formats is not None:
        kwargs["formats"] = tuple(formats)
    if rows_per_shard is not None:
        kwargs["rows_per_shard"] = rows_per_shard
    if xlsx_sheet_rows is not None:
        kwargs["xlsx_sheet_rows"] = xlsx_sheet_rows
    if metadata is not None:
        kwargs["metadata"] = dict(metadata)
    if on_shard is not None:
        kwargs["on_shard"] = on_shard
    if expected_rows is not None:
        kwargs["expected_rows"] = expected_rows
    return module.export_job(rows, **kwargs)


def report_field(report: Any, name: str) -> Any:
    """Read one field from ``ExportReport``/``SweepReport`` or fail as RED."""

    if isinstance(report, Mapping):
        if name in report:
            return report[name]
    elif hasattr(report, name):
        return getattr(report, name)
    raise NotImplementedError(f"{type(report).__name__} must expose {name!r} (Fase 5 RED)")


def plan_shards(row_count: int, rows_per_shard: int) -> tuple[tuple[int, int, int], ...]:
    """Normalise ``plan_shards`` to ``(index, start, stop)`` triples."""

    raw = pipeline_module().plan_shards(row_count, rows_per_shard)
    triples: list[tuple[int, int, int]] = []
    for entry in raw:
        if isinstance(entry, Mapping):
            triples.append((int(entry["index"]), int(entry["start"]), int(entry["stop"])))
        elif hasattr(entry, "index"):
            triples.append((int(entry.index), int(entry.start), int(entry.stop)))
        else:
            index, start, stop = entry
            triples.append((int(index), int(start), int(stop)))
    return tuple(triples)


def coverage_error() -> type[BaseException]:
    return pipeline_module().ExportCoverageError


def read_manifest(path: str | Path) -> dict[str, Any]:
    return dict(manifest_module().read_manifest(Path(path)))


def verify_manifest(path: str | Path) -> Any:
    return manifest_module().verify_manifest(Path(path))


def check_field(check: Any, name: str) -> Any:
    if isinstance(check, Mapping):
        if name in check:
            return check[name]
    elif hasattr(check, name):
        return getattr(check, name)
    raise NotImplementedError(f"manifest check must expose {name!r} (Fase 5 RED)")


def read_custody(path: str | Path) -> list[dict[str, Any]]:
    return [dict(record) for record in manifest_module().read_custody(Path(path))]


async def purge_jobs(repository: Any, job_ids: Iterable[str]) -> int:
    """Call the Phase-5 repository purge, or fail as RED when it is missing."""

    method = getattr(repository, "purge_jobs", None)
    if method is None:  # pragma: no cover - RED path
        raise NotImplementedError(
            "SqliteJobRepository.purge_jobs(job_ids) is missing (Fase 5 RED)"
        )
    return int(await method(tuple(str(job_id) for job_id in job_ids)))


def write_export(
    rows: Iterable[Mapping[str, Any]], fmt: str, path: str | Path
) -> Any:
    return writer_module().write_export(rows, fmt, Path(path), columns=COLUMNS)


def xlsx_sheet_names(total_rows: int, *, rows_per_sheet: int | None = None) -> tuple[str, ...]:
    function = writer_module().xlsx_sheet_names
    if rows_per_sheet is None:
        return tuple(function(total_rows))
    return tuple(function(total_rows, rows_per_sheet=rows_per_sheet))


# ---------------------------------------------------------------------------
# Files and directories
# ---------------------------------------------------------------------------


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def data_file_names(job_id: str = JOB_ID) -> dict[str, str]:
    return {
        "csv": f"{job_id}.csv",
        "xlsx": f"{job_id}.xlsx",
        "jsonl": f"{job_id}.jsonl",
        "parquet": f"{job_id}.parquet",
    }


def parts_dir(directory: str | Path, job_id: str = JOB_ID) -> Path:
    """The one hidden staging directory for a job (name is not pinned)."""

    candidates = [
        path
        for path in Path(directory).iterdir()
        if path.is_dir() and job_id in path.name
    ]
    assert len(candidates) == 1, (
        f"expected exactly one staging directory for {job_id!r} under {directory}; "
        f"got {[p.name for p in candidates]}"
    )
    return candidates[0]


def stage_files(directory: str | Path, job_id: str = JOB_ID) -> list[Path]:
    return sorted(parts_dir(directory, job_id).glob("shard-*"))


def temp_files(directory: str | Path) -> list[Path]:
    return sorted(Path(directory).rglob("*.tmp-*"))


# ---------------------------------------------------------------------------
# Retention fixtures
# ---------------------------------------------------------------------------


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class SeededJob:
    job_id: str
    files: tuple[Path, ...]
    digests: tuple[str, ...]


async def seed_job(
    db_path: str | Path,
    directory: str | Path,
    *,
    job_id: str,
    created_days_ago: int = 0,
    expires_in_days: int = 7,
    formats: Sequence[str] = ("csv",),
) -> SeededJob:
    """A completed-looking job with artifact rows and real files on disk."""

    from app.jobs.repository import SqliteJobRepository
    from tests._support import enrich_payload

    exports = Path(directory)
    exports.mkdir(parents=True, exist_ok=True)
    repository = SqliteJobRepository(Path(db_path))
    try:
        job = await repository.create(
            params=enrich_payload(format=["csv"]),
            api_key="client-phase5",
            rows_target=3,
            retention_days=7,
            expires_at=NOW + timedelta(days=expires_in_days),
            job_id=job_id,
        )
        paths: list[Path] = []
        digests: list[str] = []
        for fmt in formats:
            path = exports / f"{job_id}.{fmt}"
            path.write_bytes(f"{job_id}:{fmt}:payload\n".encode("utf-8"))
            digest = sha256_file(path)
            await repository.record_artifact(
                job_id,
                kind=fmt,
                path=path,
                sha256=digest,
                row_count=3,
                bytes_=path.stat().st_size,
            )
            paths.append(path)
            digests.append(digest)
    finally:
        await repository.close()

    backdate(db_path, job_id, created_days_ago=created_days_ago)
    return SeededJob(job_id=job_id, files=tuple(paths), digests=tuple(digests))


def backdate(db_path: str | Path, job_id: str, *, created_days_ago: int) -> None:
    """Move ``created_at`` backwards without touching ``expires_at``."""

    stamp = _iso(NOW - timedelta(days=created_days_ago))
    with closing(sqlite3.connect(Path(db_path))) as connection:
        connection.execute(
            "UPDATE jobs SET created_at = ?, updated_at = ? WHERE id = ?",
            (stamp, stamp, job_id),
        )
        connection.commit()


def table_rows(db_path: str | Path, table: str, job_id: str) -> list[dict[str, Any]]:
    path = Path(db_path)
    if not path.exists():  # pragma: no cover - a seeded job always creates it
        return []
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in connection.execute(
                f"SELECT * FROM {table} WHERE job_id = ? ORDER BY 1", (job_id,)
            )
        ]


def job_row(db_path: str | Path, job_id: str) -> dict[str, Any] | None:
    path = Path(db_path)
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row is not None else None


class RecordingUnlink:
    """An ``unlink`` seam that logs the call and can fail once on one path."""

    def __init__(self, *, fail_on: str | Path | None = None) -> None:
        self.fail_on = Path(fail_on) if fail_on is not None else None
        self.events: list[tuple[str, str]] = []
        self.deleted: list[Path] = []

    def __call__(self, path: str | Path) -> None:
        target = Path(path)
        self.events.append(("unlink", str(target)))
        if self.fail_on is not None and target == self.fail_on:
            raise OSError(f"injected unlink failure for {target.name}")
        os.unlink(target)
        self.deleted.append(target)


class PurgeSpy:
    """Delegates to the repository while recording the ``purge_jobs`` call."""

    def __init__(self, repository: Any, events: list[tuple[str, Any]]) -> None:
        if not hasattr(repository, "purge_jobs"):  # pragma: no cover - RED path
            raise NotImplementedError(
                "SqliteJobRepository.purge_jobs(job_ids) is missing (Fase 5 RED)"
            )
        object.__setattr__(self, "_repository", repository)
        object.__setattr__(self, "events", events)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._repository, name)

    async def purge_jobs(self, job_ids: Iterable[str]) -> int:
        ids = tuple(str(job_id) for job_id in job_ids)
        self.events.append(("purge", ids))
        return await self._repository.purge_jobs(ids)


def sweep(
    repository: Any,
    *,
    directory: str | Path,
    now: datetime | None = None,
    retention_days: int | None = None,
    dry_run: bool = False,
    unlink: Callable[[Any], None] | None = None,
) -> Any:
    module = cleanup_module()
    kwargs: dict[str, Any] = {
        "directory": Path(directory),
        "now": now or NOW,
        "dry_run": dry_run,
    }
    if retention_days is not None:
        kwargs["retention_days"] = retention_days
    if unlink is not None:
        kwargs["unlink"] = unlink
    return module.sweep(repository, **kwargs)


__all__ = [
    "COLUMNS",
    "DATA_FORMATS",
    "DEFAULT_METADATA",
    "FIXTURE_PATH",
    "INJECTION_PREFIXES",
    "JOB_ID",
    "NOW",
    "PurgeSpy",
    "RecordingUnlink",
    "SeededJob",
    "backdate",
    "bulk_rows",
    "check_field",
    "cleanup_module",
    "content_sha256",
    "coverage_error",
    "csv_cell",
    "csv_row",
    "data_file_names",
    "export_job",
    "job_row",
    "json_cell",
    "json_row",
    "manifest_module",
    "parquet_cell",
    "parquet_row",
    "parquet_type",
    "parts_dir",
    "pipeline_module",
    "plan_shards",
    "purge_jobs",
    "raw_rows",
    "read_csv_table",
    "read_custody",
    "read_jsonl",
    "read_manifest",
    "read_parquet",
    "read_xlsx_sheet",
    "report_field",
    "resume_rows",
    "seed_job",
    "sha256_file",
    "source_rows",
    "stage_files",
    "sweep",
    "table_rows",
    "temp_files",
    "verify_manifest",
    "write_export",
    "writer_module",
    "xlsx_cell",
    "xlsx_row",
    "xlsx_sheet_names",
]
