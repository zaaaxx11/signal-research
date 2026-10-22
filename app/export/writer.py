"""Streaming export writer: csv / json / xlsx / parquet (02 §3.8.2).

Rules that the tests pin down:

* header is ``CANONICAL_COLUMNS_V1`` byte-for-byte, in order, 92 names;
* atomic write (tmp + rename), streaming (never ``list(rows)``);
* empty cell is "" in CSV and ``null`` in JSON — never "N/A" or 0;
* formula injection is neutralised for CSV/XLSX;
* provenance never becomes column 93.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, Iterator

FORMATS = ("csv", "json", "xlsx", "parquet")


@dataclass(frozen=True)
class ExportResult:
    path: Path
    fmt: str
    row_count: int
    bytes: int
    sha256: str


def write_export(
    rows: Iterable[Mapping[str, Any]],
    fmt: str,
    path: str | Path,
    *,
    columns: Sequence[str],
) -> ExportResult:
    """Write one artifact from a row iterable."""

    raise NotImplementedError(f"export writer is not implemented (Phase 0 RED): fmt={fmt!r}")


def write_export_iter(
    rows: Iterable[Mapping[str, Any]],
    fmt: str,
    path: str | Path,
    *,
    columns: Sequence[str],
) -> Iterator[bytes]:
    """Streaming variant; the worker must call this, never materialise rows."""

    raise NotImplementedError("streaming export is not implemented (Phase 5 RED)")
