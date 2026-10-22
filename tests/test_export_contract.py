"""Export header contract (02 §3.8.1/§3.8.2, Fase-0 gate #2).

This is the test named by the architecture doc. It compares the written header
against the frozen JSON spec, not against ``CANONICAL_COLUMNS_V1`` itself, so a
wrong constant cannot satisfy the test by definition.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from app.export.writer import write_export
from tests._support import COLUMNS, fake_rows


def test_header_is_exactly_canonical_v1(tmp_path: Path) -> None:
    rows = fake_rows()
    target = tmp_path / "export.csv"

    result = write_export(rows, "csv", target, columns=COLUMNS)

    assert target.exists()
    table = list(csv.reader(io.StringIO(target.read_text(encoding="utf-8"))))
    header = table[0]
    assert len(header) == 92, f"header has {len(header)} columns, expected 92"
    assert header == COLUMNS
    assert len(table) == 1 + len(rows)
    assert all(len(row) == 92 for row in table[1:])
    assert result.row_count == len(rows)
    assert result.fmt == "csv"
