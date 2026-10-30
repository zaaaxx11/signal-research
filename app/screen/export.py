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
  impossible to request by accident. Its manifest's ``deep_rows`` is that batch
  size (20), so a symbol the engine could not enrich shows up as
  ``row_count < deep_rows`` instead of a quietly shorter promise.

Each scope writes every requested format plus one manifest that hashes the real
bytes on disk (``files[*].sha256``) and declares ``coverage.ratio == 1.0`` only
because ``rows_expected`` is known and equals what the writers counted.

The manifest also records **which question** the bundle answers
(PLAN-finviz-parity.md §4 "Source Graph + Provenance", §6 #7): ``selection_id``,
``filter_hash``, ``view``, ``source_graph_version``, ``criteria_snapshot`` and
``as_of``. Three of them are read rather than claimed — the stored
``filters_hash`` and the snapshot's ``as_of`` come from its row, and the graph
version from the document this checkout pins. The rest are optional
:class:`ExportMetadata`; what the caller does not know is written as ``None``
rather than guessed, and ``metadata`` itself is optional so every pre-existing
``export_bundle(...)`` call keeps working unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from app.contracts import CANONICAL_COLUMNS_V1
from app.export.manifest import (
    MANIFEST_VERSION,
    audit_block,
    file_sha256,
    write_manifest,
)
from app.export.writer import FORMATS, write_table
from app.screen import rank as ranking
from app.screen import universe as universes
from app.screen.repository import SqliteScreenRepository
from app.screen.symbols import provider_symbol
from app.screen.scanner import FULL_SCAN_ROWS, fetch_all, fetch_scan
from app.screen.views import VIEW_NAMES
from app.sources.graph import GRAPH_VERSION
from app.screen.depth_overlay import SCREEN_ALIASES, overlay
from app.screen.depth_source import current_depth

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

#: The audit fields a caller may supply. ``as_of`` is **not** one of them: the
#: manifest dates the table from the snapshot row, because a caller who could
#: set the date could date a bundle to a day the data never existed. The stored
#: ``filters_hash`` and :data:`GRAPH_VERSION` fill their fields when the caller
#: says nothing; the rest stay ``None``.
METADATA_FIELDS: tuple[str, ...] = (
    "selection_id",
    "filter_hash",
    "view",
    "source_graph_version",
    "criteria_snapshot",
)


class InvalidExportMetadata(ValueError):
    """The caller's audit metadata cannot be recorded truthfully (a 422)."""

    status_code = 422


def _json_copy(value: Any, *, name: str) -> Any:
    """A deep copy of ``value`` that survives ``json.dumps`` unchanged.

    The manifest is JSON on disk, so a criteria document holding a ``set``, a
    ``NaN`` or a non-string key would either fail at write time (after the
    expensive scan and deep pass were already paid for) or be silently
    rewritten by ``json.dumps``. Both are refused here, before any I/O.
    """

    def check_keys(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            for key, item in node.items():
                if not isinstance(key, str):
                    raise InvalidExportMetadata(
                        f"{path} has a non-string key {key!r}; a JSON object's keys "
                        "are strings, so this document would be altered on write"
                    )
                check_keys(item, f"{path}.{key}")
        elif isinstance(node, (list, tuple)):
            for index, item in enumerate(node):
                check_keys(item, f"{path}[{index}]")

    check_keys(value, name)
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise InvalidExportMetadata(f"{name} is not JSON-serialisable: {exc}") from exc


@dataclass(frozen=True)
class ExportMetadata:
    """What a bundle records about the *question*, when the caller knows it.

    Every field is optional because ``export_bundle`` predates this contract:
    an old caller passes nothing and gets ``None`` in the manifest, which says
    "not recorded" instead of inventing a selection. ``criteria_snapshot`` is
    stored as a JSON copy, so the manifest keeps what the criteria *were* even
    if the caller mutates its request object afterwards.
    """

    selection_id: str | None = None
    filter_hash: str | None = None
    view: str | None = None
    source_graph_version: str | None = None
    criteria_snapshot: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for name in ("selection_id", "filter_hash", "source_graph_version"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise InvalidExportMetadata(
                    f"{name} must be a non-empty string when given, got {value!r}"
                )
        if self.view is not None and self.view not in VIEW_NAMES:
            raise InvalidExportMetadata(
                f"unknown view {self.view!r}; expected one of {', '.join(VIEW_NAMES)}"
            )
        if self.criteria_snapshot is not None:
            if not isinstance(self.criteria_snapshot, Mapping):
                raise InvalidExportMetadata(
                    "criteria_snapshot must be a mapping written into the manifest as "
                    f"JSON, got {type(self.criteria_snapshot).__name__}"
                )
            object.__setattr__(
                self,
                "criteria_snapshot",
                _json_copy(dict(self.criteria_snapshot), name="criteria_snapshot"),
            )

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> ExportMetadata:
        """Build from a plain mapping; an unknown key is refused, not ignored."""

        unknown = sorted(set(values) - set(METADATA_FIELDS))
        if unknown:
            raise InvalidExportMetadata(
                f"unknown export metadata field(s) {unknown}; expected any of "
                f"{', '.join(METADATA_FIELDS)}"
            )
        return cls(**{name: values.get(name) for name in METADATA_FIELDS})


def _coerce_metadata(
    metadata: ExportMetadata | Mapping[str, Any] | None,
) -> ExportMetadata:
    """Accept the typed object, a plain mapping, or nothing at all."""

    if metadata is None:
        return ExportMetadata()
    if isinstance(metadata, ExportMetadata):
        return metadata
    if isinstance(metadata, Mapping):
        return ExportMetadata.from_mapping(metadata)
    raise InvalidExportMetadata(
        "metadata must be ExportMetadata, a mapping of "
        f"{', '.join(METADATA_FIELDS)}, or None; got {type(metadata).__name__}"
    )


def _audit(
    metadata: ExportMetadata, *, screen: Mapping[str, Any], as_of: str
) -> tuple[dict[str, Any], dict[str, str]]:
    """The manifest's audit block, and where each value in it came from.

    The snapshot answers ``as_of`` and ``filter_hash`` (the recipe it was built
    with) and the checkout answers ``source_graph_version`` (the graph document
    the 92 canonical columns are pinned to); those are read, not claimed. The
    remaining fields are the caller's, and an unanswered one is ``None``. The
    provenance map is written next to the values so an auditor can tell a
    recorded field from a derived one without reading this module.
    """

    stored_hash = screen.get("filters_hash")
    filter_hash = metadata.filter_hash or (
        stored_hash.strip()
        if isinstance(stored_hash, str) and stored_hash.strip()
        else None
    )
    graph_version = metadata.source_graph_version or GRAPH_VERSION
    provenance = {
        "selection_id": "request" if metadata.selection_id else "not-recorded",
        "filter_hash": (
            "request"
            if metadata.filter_hash
            else ("screens.filters_hash" if filter_hash else "not-recorded")
        ),
        "view": "request" if metadata.view else "not-recorded",
        "source_graph_version": (
            "request" if metadata.source_graph_version else "reference/source_graph.json"
        ),
        "criteria_snapshot": (
            "request" if metadata.criteria_snapshot is not None else "not-recorded"
        ),
        "as_of": "screens.as_of",
    }
    block = audit_block(
        {
            "selection_id": metadata.selection_id,
            "filter_hash": filter_hash,
            "view": metadata.view,
            "source_graph_version": graph_version,
            "criteria_snapshot": metadata.criteria_snapshot,
            "as_of": as_of,
        }
    )
    return block, provenance


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
    #: What the manifests recorded about the selection; ``None`` fields mean the
    #: caller did not supply them (never that a value was guessed).
    metadata: ExportMetadata = field(default_factory=ExportMetadata)

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
    audit: Mapping[str, Any],
    audit_provenance: Mapping[str, str],
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
        "manifest_version": MANIFEST_VERSION,
        "job_id": prefix,
        "scope": scope,
        "screen_id": screen["id"],
        "region": screen["region"],
        "sort": screen["sort"],
        # One selection, two tables: both scopes record the same audit block,
        # so an ``all`` file and its ``top20`` sibling can be proven to answer
        # the same question instead of being paired by directory name.
        **audit,
        "audit_provenance": dict(audit_provenance),
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
    metadata: ExportMetadata | Mapping[str, Any] | None = None,
) -> Bundle:
    """Write both scopes for ``screen_id`` and return the bundle description.

    ``metadata`` is optional on purpose: every call written before the audit
    contract existed keeps working and produces the same tables, with the
    fields it says nothing about recorded as ``None``. It is validated before
    the scan is paid for, so a bad criteria document costs no provider request.
    """

    requested = _validate_formats(formats)
    resolved_metadata = _coerce_metadata(metadata)
    screen = await repository.get(screen_id)
    if screen is None:
        raise ScreenNotFound(f"screen {screen_id} does not exist")

    region = str(screen["region"])
    sort = str(screen["sort"])
    source = str(screen.get("source") or "tradingview")
    as_of = str(screen["as_of"])
    audit, audit_provenance = _audit(resolved_metadata, screen=screen, as_of=as_of)

    # ``all`` is the universe as it is *now*, not the twenty rows on screen: the
    # display table is a cache of the top of the ranking, not the universe.
    # One large request is enough for the observed live universe; the paging
    # fallback only runs if the provider has more rows than the window.
    page = await fetch_scan(region, limit=FULL_SCAN_ROWS)
    if page.received < page.total_count:
        page = await fetch_all(region)
    universe = universes.build_universe(page.rows, region=region)
    depth = current_depth()
    if depth.symbols:
        universe = overlay(universe, depth.symbols, default_ticker_key="symbol", aliases=SCREEN_ALIASES)
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
        audit=audit,
        audit_provenance=audit_provenance,
    )
    top_scope = _write_scope(
        "top20",
        deep,
        columns=CANONICAL_COLUMNS_V1,
        formats=requested,
        directory=root,
        prefix=prefix,
        # The size of the batch the deep pass was *asked* to cover, not the
        # number of rows that came back: a symbol the engine could not enrich
        # then shows up as ``row_count < deep_rows`` instead of disappearing
        # from the manifest. For a full display this is DEEP_ROWS == 20, the
        # number PLAN-screen.md §4 promises.
        deep_rows=len(symbols),
        screen=screen,
        audit=audit,
        audit_provenance=audit_provenance,
    )

    return Bundle(
        screen_id=screen_id,
        region=region,
        sort=sort,
        as_of=as_of,
        directory=root,
        scopes={"all": all_scope, "top20": top_scope},
        metadata=resolved_metadata,
    )


__all__ = [
    "DATA_FORMATS",
    "DEEP_ROWS",
    "EXPORT_FORMATS",
    "LIGHT_COLUMNS",
    "METADATA_FIELDS",
    "SCOPES",
    "Bundle",
    "DeepEngine",
    "ExportMetadata",
    "InvalidExportMetadata",
    "Scope",
    "ScreenNotFound",
    "UnknownScreenFormat",
    "export_bundle",
]
