"""Export bundle: ``all`` light + ``top20`` deep, one manifest per scope.

The expensive half of this engine is enrichment, so the bundle is deliberately
asymmetric (PLAN-screen.md §4, §8 #5-#6):

* **``all``** is the whole post-filter universe in 13 light columns, and it is
  fetched **on demand at export time** — one extra scan. It is *not* built from
  ``screen_rows``: that table holds the 20 displayed rows, and a 20-row table
  sold as "all 23 issuers" is the exact lie this layer exists to prevent.
* **``top20``** is the 92 canonical columns for the 20 displayed rows only. The
  deep engine is called **once**, with the display symbols in rank order — the
  contract that keeps a full-universe deep export (81k × 4 provider calls)
  impossible to request by accident.

Each scope writes every requested format plus one manifest that hashes the real
bytes on disk (``files[*].sha256``) and declares ``coverage.ratio == 1.0`` only
because ``rows_expected`` is known and equals what the writers counted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from app.contracts import CANONICAL_COLUMNS_V1
from app.export.manifest import file_sha256, write_manifest
from app.export.writer import FORMATS, write_table
from app.screen import rank as ranking
from app.screen import universe as universes
from app.screen.repository import SqliteScreenRepository
from app.screen.symbols import provider_symbol
from app.screen.scanner import FULL_SCAN_ROWS, fetch_all, fetch_scan

#: Rows the deep engine may enrich. Never the whole universe.
DEEP_ROWS = 20

#: The light table of the ``all`` scope (PLAN-screen.md §3, minimum columns).
LIGHT_COLUMNS: tuple[str, ...] = (
    "rank",
    "symbol",
    "name",
    "exchange",
    "country",
    "sector",
    "price",
    "change_pct",
    "volume",
    "turnover",
    "market_cap_usd",
    "as_of",
    "source",
)

#: The scopes one bundle always carries, in the order a reader wants them.
SCOPES: tuple[str, ...] = ("all", "top20")

#: Formats a scope can be written in. ``json`` (the array document) is accepted
#: because the writer has it; the four data formats are the default bundle.
DATA_FORMATS: tuple[str, ...] = ("csv", "xlsx", "jsonl", "parquet")
EXPORT_FORMATS: tuple[str, ...] = FORMATS

#: ``deep_engine(symbols) -> rows``: injected by the composition root.
DeepEngine = Callable[[Sequence[str]], Awaitable[Iterable[Mapping[str, Any]]]]


class ScreenNotFound(LookupError):
    """No such snapshot (the API answers 404 before spending a scan)."""

    status_code = 404


class UnknownScreenFormat(ValueError):
    """The caller asked for a format this bundle cannot write (422)."""

    status_code = 422


@dataclass(frozen=True)
class Scope:
    """One table of the bundle: its columns, its files and its manifest."""

    scope: str
    row_count: int
    deep_rows: int
    columns: tuple[str, ...]
    files: dict[str, Path]
    manifest_path: Path

    def artifact_name(self, fmt: str) -> str:
        return Path(self.files[fmt]).name


@dataclass(frozen=True)
class Bundle:
    """A screen's two-scope export, as written to ``directory``."""

    screen_id: str
    region: str
    sort: str
    as_of: str
    directory: Path
    scopes: dict[str, Scope] = field(default_factory=dict)

    def files(self) -> dict[str, Path]:
        """Every artifact keyed by kind (``all_csv``, ``top20_csv``, ...)."""

        return {
            f"{scan.scope}_{fmt}": path
            for scan in self.scopes.values()
            for fmt, path in scan.files.items()
        }


def _light_row(row: Mapping[str, Any], *, as_of: str, source: str) -> dict[str, Any]:
    return {
        "rank": row.get("rank"),
        "symbol": row.get("symbol"),
        "name": row.get("name"),
        "exchange": row.get("exchange"),
        "country": row.get("country"),
        "sector": row.get("sector"),
        "price": row.get("price"),
        "change_pct": row.get("change_pct"),
        "volume": row.get("volume"),
        "turnover": row.get("turnover"),
        "market_cap_usd": row.get("market_cap_usd"),
        "as_of": as_of,
        "source": source,
    }


def _canonical_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Exactly the 92 canonical columns, whatever extra keys the engine sent."""

    return {column: row.get(column) for column in CANONICAL_COLUMNS_V1}


def _validate_formats(formats: Sequence[str]) -> tuple[str, ...]:
    requested = tuple(formats)
    if not requested:
        raise UnknownScreenFormat("a bundle needs at least one format")
    unknown = [fmt for fmt in requested if fmt not in EXPORT_FORMATS]
    if unknown:
        raise UnknownScreenFormat(
            f"unknown export format(s) {unknown}; expected any of {', '.join(EXPORT_FORMATS)}"
        )
    if len(set(requested)) != len(requested):
        raise UnknownScreenFormat(f"duplicate export formats requested: {list(requested)}")
    return requested


def _write_scope(
    scope: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    columns: Sequence[str],
    formats: Sequence[str],
    directory: Path,
    prefix: str,
    deep_rows: int,
    screen: Mapping[str, Any],
) -> Scope:
    """Write one scope's files and its manifest, then return what was written."""

    files: dict[str, Path] = {}
    counts: dict[str, int] = {}
    for fmt in formats:
        target = directory / f"{prefix}-{scope}.{fmt}"
        result = write_table(rows, fmt, target, columns=columns)
        files[fmt] = result.path
        counts[fmt] = result.row_count

    row_count = len(rows)
    entries = [
        {
            "name": path.name,
            "bytes": path.stat().st_size,
            "sha256": file_sha256(path),
            "row_count": counts[fmt],
        }
        for fmt, path in files.items()
    ]
    manifest = {
        "job_id": prefix,
        "scope": scope,
        "screen_id": screen["id"],
        "region": screen["region"],
        "sort": screen["sort"],
        "as_of": screen["as_of"],
        "created_at": datetime.now(UTC).isoformat(timespec="microseconds"),
        "columns": list(columns),
        "row_count": row_count,
        "deep_rows": deep_rows,
        "files_count": len(entries),
        "files": entries,
        "coverage": {
            "rows_expected": row_count,
            "rows_written": row_count,
            "ratio": 1.0 if all(count == row_count for count in counts.values()) else 0.0,
            "by_format": dict(counts),
        },
    }
    manifest_path = write_manifest(directory / f"{prefix}-{scope}.manifest.json", manifest)
    return Scope(
        scope=scope,
        row_count=row_count,
        deep_rows=deep_rows,
        columns=tuple(columns),
        files=files,
        manifest_path=manifest_path,
    )


async def export_bundle(
    screen_id: str,
    *,
    repository: SqliteScreenRepository,
    directory: str | Path,
    formats: Sequence[str] = DATA_FORMATS,
    deep_engine: DeepEngine,
    job_id: str | None = None,
) -> Bundle:
    """Write both scopes for ``screen_id`` and return the bundle description."""

    requested = _validate_formats(formats)
    screen = await repository.get(screen_id)
    if screen is None:
        raise ScreenNotFound(f"screen {screen_id} does not exist")

    region = str(screen["region"])
    sort = str(screen["sort"])
    source = str(screen.get("source") or "tradingview")
    as_of = str(screen["as_of"])

    # ``all`` is the universe as it is *now*, not the twenty rows on screen: the
    # display table is a cache of the top of the ranking, not the universe.
    # One large request is enough for the observed live universe; the paging
    # fallback only runs if the provider has more rows than the window.
    page = await fetch_scan(region, limit=FULL_SCAN_ROWS)
    if page.received < page.total_count:
        page = await fetch_all(region)
    universe = universes.build_universe(page.rows, region=region)
    ranked = ranking.rank_rows(universe, sort)
    light = [_light_row(row, as_of=as_of, source=source) for row in ranked]

    displayed = await repository.rows(screen_id)
    source_rows = displayed or ranked
    symbols = [
        provider_symbol(row.get("symbol"), row.get("exchange"))
        for row in source_rows[:DEEP_ROWS]
    ]
    deep = [_canonical_row(row) for row in await deep_engine(symbols)]

    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    prefix = job_id or screen_id

    all_scope = _write_scope(
        "all",
        light,
        columns=LIGHT_COLUMNS,
        formats=requested,
        directory=root,
        prefix=prefix,
        deep_rows=0,
        screen=screen,
    )
    top_scope = _write_scope(
        "top20",
        deep,
        columns=CANONICAL_COLUMNS_V1,
        formats=requested,
        directory=root,
        prefix=prefix,
        deep_rows=len(deep),
        screen=screen,
    )

    return Bundle(
        screen_id=screen_id,
        region=region,
        sort=sort,
        as_of=as_of,
        directory=root,
        scopes={"all": all_scope, "top20": top_scope},
    )


__all__ = [
    "DATA_FORMATS",
    "DEEP_ROWS",
    "EXPORT_FORMATS",
    "LIGHT_COLUMNS",
    "SCOPES",
    "Bundle",
    "DeepEngine",
    "Scope",
    "ScreenNotFound",
    "UnknownScreenFormat",
    "export_bundle",
]
