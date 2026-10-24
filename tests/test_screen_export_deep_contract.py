"""Screen P9 — the export bundle's deep half is a 20 x 92 table, pinned.

Gate (PLAN-screen.md §4 "Export", §8 #5/#6; NEXT-ACTIONS.md §3 P9):

* ``top20`` is exactly the 92 canonical columns — ``CANONICAL_COLUMNS_V1``, in
  order — and exactly ``DEEP_ROWS == 20`` rows, in every format the bundle
  writes. The header is the contract, not a suggestion: ``tests/spec/
  columns_v1.json`` is the oracle, so a drifted constant cannot satisfy this
  file by definition.
* The deep engine is called **once**, with the twenty displayed symbols in rank
  order (provider-mapped for non-US venues), and with nothing else. The
  full-universe deep export PLAN-screen.md §6 refuses is unreachable from here.
* A cell the engine did not fill stays empty (``""`` in CSV, ``null`` in
  JSON/JSONL/Parquet, a blank cell in XLSX). It never becomes ``0``, ``N/A``,
  ``"None"`` or a 93rd column — a zero that means "missing" survives every
  downstream average as a lie, and a 93rd column breaks every consumer that
  trusts the header.
* The ``top20`` manifest declares ``deep_rows == 20``; the ``all`` scope is
  untouched: still the 13 light columns over the full universe, ``deep_rows=0``.

The engine stand-in here is deliberately not :class:`tests._screen.FakeDeepEngine`:
it fills *every* canonical column with a value unique per (row, column), so a
column shifted by one or silently dropped is visible cell by cell, and it
records the exact batch it was handed.

No network: the scanner is the mocked recording fixture of the Screen suite and
the deep engine is a local object.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.contracts import CANONICAL_COLUMNS_V1
from app.export.manifest import read_manifest, verify_manifest
from app.screen.export import DEEP_ROWS, LIGHT_COLUMNS, export_bundle
from app.screen.symbols import provider_symbol
from tests import _screen as S

#: The frozen column contract, as a tuple for direct comparison with a header.
COLUMNS: tuple[str, ...] = CANONICAL_COLUMNS_V1

#: Keys a real pipeline may carry that the export contract has no room for.
EXTRA_KEYS: dict[str, Any] = {
    "rank": 999,
    "beta_1_year": 1.42,
    "price_52_week_high": 123.45,
    "provenance": "fake-engine",
}


def _spec() -> dict[str, Any]:
    """The mechanically extracted oracle of 02 §3.8.1 (not this module's copy)."""

    path = Path(__file__).resolve().parent / "spec" / "columns_v1.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _spec_columns() -> list[str]:
    return list(_spec()["columns"])


# ---------------------------------------------------------------------------
# A deep engine that is a table, not a mock: one row per symbol, 92 columns
# ---------------------------------------------------------------------------


@dataclass
class DeepStub:
    """Record every batch, answer with one canonical row per requested symbol.

    ``full`` fills all 92 columns with a value unique to (symbol, column) — the
    oracle that catches a shifted or dropped column. ``omit`` removes a key from
    the mapping entirely, which is how a provider that never saw the field looks
    (distinct from a field it saw and could not fill: ``None``).
    """

    full: bool = True
    omit: tuple[str, ...] = ()
    drop: frozenset[str] = field(default_factory=frozenset)
    extras: Mapping[str, Any] = field(default_factory=lambda: dict(EXTRA_KEYS))
    calls: list[list[str]] = field(default_factory=list)
    answered: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(self, symbols: Sequence[str]) -> list[dict[str, Any]]:
        batch = [str(symbol) for symbol in symbols]
        self.calls.append(batch)
        rows = [
            self._row(symbol, index)
            for index, symbol in enumerate(batch)
            if symbol not in self.drop
        ]
        self.answered = rows
        return rows

    def _row(self, symbol: str, index: int) -> dict[str, Any]:
        row: dict[str, Any] = {column: None for column in COLUMNS}
        row.update(
            {
                "instrument_id": f"XNAS:{symbol}",
                "symbol": symbol,
                "name": f"{symbol} deep holdings",
                "asset_class": "equity",
                "instrument_type": "common",
                "exchange": "XNAS",
                "mic": "XNAS",
                "currency": "USD",
                "country": "US",
                "instrument_status": "active",
                "last_price": 100.0 + index,
                "prev_close": 99.0 + index,
                "change_abs": 1.0,
                "change_pct": 1.0101,
                "day_volume": 1_000_000 + index,
                "turnover_value": 101_000_000.0,
                "quote_as_of": "2026-10-22T20:00:00+00:00",
                "market_cap": 1_000_000_000_000.0,
                "market_cap_usd": 1_000_000_000_000.0,
                # A real zero is data; only a missing cell is null.
                "ret_1d": 0.0,
                "opt_dte": 0,
                "data_quality": "partial",
                "as_of_achieved": "2026-10-22T21:00:00+00:00",
            }
        )
        if self.full:
            for column in COLUMNS:
                if row[column] is None:
                    row[column] = f"{column}={symbol}"
        for column in self.omit:
            row.pop(column, None)
        row.update(self.extras)
        return row


@dataclass
class IdentityOnlyStub:
    """Answer with ``{"symbol": ...}`` only: a row of nulls that must stay 92 wide."""

    calls: list[list[str]] = field(default_factory=list)

    async def __call__(self, symbols: Sequence[str]) -> list[dict[str, Any]]:
        self.calls.append([str(symbol) for symbol in symbols])
        return [{"symbol": str(symbol)} for symbol in symbols]


# ---------------------------------------------------------------------------
# Artifact readers — the table as a consumer sees it, not as it was written
# ---------------------------------------------------------------------------


def _csv_table(path: Path) -> list[list[str]]:
    return list(csv.reader(io.StringIO(Path(path).read_text(encoding="utf-8"))))


def _cells(header: Sequence[str], row: Sequence[str]) -> dict[str, str]:
    return dict(zip(header, row, strict=True))


def _jsonl_records(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _xlsx_table(path: Path) -> tuple[list[str], list[tuple[Any, ...]]]:
    from openpyxl import load_workbook

    book = load_workbook(Path(path))
    try:
        sheet = book["data"]
        rows = list(sheet.iter_rows(values_only=True))
    finally:
        book.close()
    header = [str(value) for value in rows[0]]
    return header, rows[1:]


def _parquet_table(path: Path) -> Any:
    import pyarrow.parquet as pq

    return pq.read_table(str(path))


def _parquet_column(path: Path, column: str) -> list[Any]:
    table = _parquet_table(path)
    return table.column(column).to_pylist()


# ---------------------------------------------------------------------------
# Bundle helper
# ---------------------------------------------------------------------------


async def _export(
    harness: S.Harness,
    *,
    region: str = "us",
    engine: Any = None,
    job_id: str = "job-screen-p9",
) -> tuple[dict[str, Any], Any, Any]:
    page = await harness.create_screen(region=region)
    deep = engine if engine is not None else DeepStub()
    bundle = await export_bundle(
        page["screen_id"],
        repository=harness.screen_repository,
        directory=harness.db_path.parent / job_id,
        formats=S.DATA_FORMATS,
        deep_engine=deep,
        job_id=job_id,
    )
    return page, deep, bundle


def _expected_deep_symbols(page: Mapping[str, Any]) -> list[str]:
    """The display symbols in rank order, mapped to what the provider speaks."""

    return [
        provider_symbol(row.get("symbol"), row.get("exchange")) for row in page["rows"]
    ]


# ---------------------------------------------------------------------------
# Header: exactly the 92 canonical columns, in order, in every format
# ---------------------------------------------------------------------------


async def test_top20_header_is_the_92_canonical_columns_in_every_format(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        _page, _engine, bundle = await _export(harness)
        scope = bundle.scopes["top20"]
        csv_table = _csv_table(scope.files["csv"])
        records = _jsonl_records(scope.files["jsonl"])
        xlsx_header, _xlsx_rows = _xlsx_table(scope.files["xlsx"])
        parquet = _parquet_table(scope.files["parquet"])
        manifest = read_manifest(scope.manifest_path)

    spec = _spec_columns()
    assert list(COLUMNS) == spec, (
        "precondition: the export contract must be the frozen 92-column spec, "
        f"module has {len(COLUMNS)} names"
    )
    assert tuple(scope.columns) == COLUMNS, (
        f"scope=top20 declares {len(scope.columns)} columns, expected 92"
    )
    assert csv_table[0] == list(COLUMNS), (
        f"CSV header has {len(csv_table[0])} columns, expected 92 in canonical order"
    )
    assert list(records[0]) == list(COLUMNS), (
        f"JSONL record has {len(records[0])} keys, expected 92 in canonical order"
    )
    assert xlsx_header == list(COLUMNS), (
        f"XLSX header has {len(xlsx_header)} columns, expected 92 in canonical order"
    )
    assert parquet.column_names == list(COLUMNS), (
        f"Parquet schema has {len(parquet.column_names)} columns, expected 92"
    )
    assert manifest["columns"] == list(COLUMNS), (
        "the manifest must describe the table it wrote, column for column"
    )
    assert "beta_1_year" not in xlsx_header and "beta_1_year" not in records[0], (
        "a key outside the canonical contract is not a 93rd column"
    )
    forbidden = set(_spec()["forbidden_columns"])
    for name, header in (
        ("csv", csv_table[0]),
        ("jsonl", list(records[0])),
        ("xlsx", xlsx_header),
        ("parquet", parquet.column_names),
    ):
        assert not forbidden & set(header), (
            f"{name} header carries no column outside the canonical contract; "
            f"found {sorted(forbidden & set(header))}"
        )


# ---------------------------------------------------------------------------
# 20 rows, 92 cells each
# ---------------------------------------------------------------------------


async def test_top20_holds_exactly_twenty_rows_of_ninety_two_cells(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        _page, _engine, bundle = await _export(harness)
        scope = bundle.scopes["top20"]
        csv_table = _csv_table(scope.files["csv"])
        records = _jsonl_records(scope.files["jsonl"])
        _xlsx_header, xlsx_rows = _xlsx_table(scope.files["xlsx"])
        parquet = _parquet_table(scope.files["parquet"])
        manifest = read_manifest(scope.manifest_path)

    assert scope.row_count == DEEP_ROWS == S.DEEP_ROWS == 20, (
        f"scope=top20 must hold the twenty displayed rows, got {scope.row_count}"
    )
    assert len(csv_table) - 1 == 20, f"CSV holds {len(csv_table) - 1} data rows"
    assert len(records) == 20, f"JSONL holds {len(records)} records"
    assert len(xlsx_rows) == 20, f"XLSX holds {len(xlsx_rows)} data rows"
    assert parquet.num_rows == 20, f"Parquet holds {parquet.num_rows} rows"
    assert parquet.num_columns == 92, f"Parquet has {parquet.num_columns} columns"

    widths = [len(row) for row in csv_table[1:]]
    assert widths == [92] * 20, (
        "every row of the deep table is 92 cells wide, whatever the engine left "
        f"empty; got widths {sorted(set(widths))}"
    )
    assert all(len(record) == 92 for record in records), [
        len(record) for record in records
    ]

    assert manifest["row_count"] == 20, manifest["row_count"]
    assert {entry["row_count"] for entry in manifest["files"]} == {20}, (
        f"every top20 artifact must hold 20 rows, entries say "
        f"{[entry['row_count'] for entry in manifest['files']]}"
    )


# ---------------------------------------------------------------------------
# The deep engine: one call, twenty symbols, rank order
# ---------------------------------------------------------------------------


async def test_deep_engine_is_called_once_with_twenty_symbols_in_rank_order(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page, engine, _bundle = await _export(harness)
        stored = S.stored_rows(harness.db_path, page["screen_id"])
        after = S.provider_requests(harness.web)

    expected = _expected_deep_symbols(page)
    assert len(expected) == 20, f"precondition: the display has 20 rows, got {len(expected)}"
    assert len(set(expected)) == 20, f"precondition: 20 distinct symbols, got {expected}"
    assert [row["symbol"] for row in stored] == [
        row["symbol"] for row in page["rows"]
    ], "precondition: the display and the stored table agree on rank order"

    assert len(engine.calls) == 1, (
        "enrichment is paid once per bundle; the deep engine was called "
        f"{len(engine.calls)} times: {engine.calls}"
    )
    assert engine.calls[0] == expected, (
        "the deep pass must receive the twenty display symbols in rank order; got "
        f"{engine.calls[0]}"
    )
    assert after == 2, (
        "one scan for the display and one on-demand scan for the all scope: the "
        f"deep pass adds no scan (provider requests: {after})"
    )


async def test_top20_rows_are_written_in_rank_order_and_keep_their_values(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page, engine, bundle = await _export(harness)
        scope = bundle.scopes["top20"]
        csv_table = _csv_table(scope.files["csv"])
        records = _jsonl_records(scope.files["jsonl"])

    expected_rows = [
        {column: row.get(column) for column in COLUMNS} for row in engine.answered
    ]
    assert len(expected_rows) == 20
    assert records == expected_rows, (
        "each cell of the deep table must be the cell the engine answered: a "
        "shifted or dropped column shows up here"
    )
    assert [record["symbol"] for record in records] == [
        row["symbol"] for row in page["rows"]
    ], "the written rows follow rank order, not engine-internal order"

    header = csv_table[0]
    symbols = [_cells(header, row)["symbol"] for row in csv_table[1:]]
    assert symbols == [record["symbol"] for record in records], (
        "the CSV and the JSONL of one scope must describe the same rows in the "
        f"same order; csv={symbols} jsonl={[r['symbol'] for r in records]}"
    )
    assert "beta_1_year" not in header and "rank" not in header, (
        "non-canonical engine keys must be dropped before the table is written"
    )


async def test_global_rows_are_provider_mapped_before_the_deep_call(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page, engine, bundle = await _export(harness, region="global", job_id="job-p9-global")
        scope = bundle.scopes["top20"]
        csv_table = _csv_table(scope.files["csv"])

    expected = _expected_deep_symbols(page)
    assert len(expected) == 20
    assert any("." in symbol for symbol in expected), (
        "precondition: the global top 20 must contain a suffixed provider ticker"
    )
    assert engine.calls == [expected], (
        "a global display symbol must reach the engine as the provider's ticker, "
        f"in rank order; got {engine.calls}"
    )
    assert scope.row_count == 20 and len(scope.columns) == 92
    assert len(csv_table) - 1 == 20
    assert all(len(row) == 92 for row in csv_table[1:])


# ---------------------------------------------------------------------------
# Missing cells: empty, never zero, never a sentinel
# ---------------------------------------------------------------------------


async def test_a_missing_cell_stays_null_and_never_becomes_zero(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # ``rsi_14`` and ``opt_iv_atm`` are absent from the engine's mapping; the
    # rest of the row is explicitly null. ``ret_1d``/``opt_dte`` are real zeros
    # and must stay zeros, so "null became 0" cannot pass by accident.
    stub = DeepStub(full=False, omit=("rsi_14", "opt_iv_atm"))
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        _page, _engine, bundle = await _export(harness, engine=stub)
        scope = bundle.scopes["top20"]
        csv_table = _csv_table(scope.files["csv"])
        records = _jsonl_records(scope.files["jsonl"])
        parquet_rsi = _parquet_column(scope.files["parquet"], "rsi_14")
        parquet_ret = _parquet_column(scope.files["parquet"], "ret_1d")
        parquet_dte = _parquet_column(scope.files["parquet"], "opt_dte")
        xlsx_header, xlsx_rows = _xlsx_table(scope.files["xlsx"])

    header = csv_table[0]
    index = {name: position for position, name in enumerate(header)}
    first = _cells(header, csv_table[1])

    assert len(csv_table[1]) == 92
    for column in ("rsi_14", "opt_iv_atm", "ma_20"):
        assert first[column] == "", f"CSV {column}: {first[column]!r}, expected an empty cell"
        assert first[column] != "0", (
            f"CSV {column}: a missing cell became 0 — the one value that lies "
            "quietly in every downstream average"
        )
    assert first["ret_1d"] == "0.0", "a real zero must survive as a zero"
    assert first["opt_dte"] == "0"
    assert not {"N/A", "None", "nan", "NaN"} & set(csv_table[1]), (
        f"a missing cell became a sentinel: {sorted(set(csv_table[1]))[:5]}"
    )

    for record in records:
        assert len(record) == 92
        for column in ("rsi_14", "opt_iv_atm", "ma_20"):
            assert record[column] is None, f"JSONL {column}: {record[column]!r}"
        assert record["ret_1d"] == 0.0
        assert record["opt_dte"] == 0

    assert parquet_rsi == [None] * 20, parquet_rsi
    assert parquet_ret == [0.0] * 20, parquet_ret
    assert parquet_dte == [0] * 20, parquet_dte

    assert len(xlsx_header) == 92
    for row in xlsx_rows:
        assert len(row) == 92
        assert row[index["rsi_14"]] is None, row[index["rsi_14"]]
        assert row[index["ma_20"]] is None
        assert row[index["ret_1d"]] == 0
        assert row[index["opt_dte"]] == 0


async def test_a_row_of_nulls_is_still_ninety_two_cells(
    tmp_path: Path, monkeypatch: Any
) -> None:
    stub = IdentityOnlyStub()
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page, engine, bundle = await _export(harness, engine=stub)
        scope = bundle.scopes["top20"]
        csv_table = _csv_table(scope.files["csv"])
        records = _jsonl_records(scope.files["jsonl"])
        parquet = _parquet_table(scope.files["parquet"])

    assert engine.calls == [_expected_deep_symbols(page)]
    assert len(csv_table) - 1 == 20
    header = csv_table[0]
    symbols = [row["symbol"] for row in page["rows"]]
    for position, row in enumerate(csv_table[1:]):
        assert len(row) == 92, f"row {position} is {len(row)} cells wide, expected 92"
        assert row[header.index("symbol")] == symbols[position]
        cells = _cells(header, row)
        assert all(cell == "" for column, cell in cells.items() if column != "symbol"), (
            "a null cell is empty in CSV, never 0 or a sentinel"
        )

    assert [record["symbol"] for record in records] == symbols
    assert all(
        value is None
        for record in records
        for column, value in record.items()
        if column != "symbol"
    ), "a null cell is null in JSONL, never 0"
    assert parquet.num_rows == 20 and parquet.num_columns == 92
    assert parquet.column("rsi_14").to_pylist() == [None] * 20


# ---------------------------------------------------------------------------
# Manifest and the untouched all-light scope
# ---------------------------------------------------------------------------


async def test_top20_manifest_declares_twenty_deep_rows(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        _page, _engine, bundle = await _export(harness)
        scope = bundle.scopes["top20"]
        manifest = read_manifest(scope.manifest_path)
        check = verify_manifest(scope.manifest_path)

    assert scope.deep_rows == DEEP_ROWS == 20, (
        f"scope=top20 must declare twenty deep rows, got {scope.deep_rows}"
    )
    assert manifest["deep_rows"] == 20, manifest["deep_rows"]
    assert manifest["scope"] == "top20"
    assert manifest["row_count"] == 20
    assert manifest["coverage"]["ratio"] == 1.0, manifest["coverage"]
    assert manifest["coverage"]["by_format"] == {fmt: 20 for fmt in S.DATA_FORMATS}, (
        manifest["coverage"]["by_format"]
    )
    assert check.ok is True, check.problems


async def test_the_all_scope_stays_the_thirteen_light_columns(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page, engine, bundle = await _export(harness)
        scope = bundle.scopes["all"]
        manifest = read_manifest(scope.manifest_path)
        csv_table = _csv_table(scope.files["csv"])
        records = _jsonl_records(scope.files["jsonl"])

    assert tuple(scope.columns) == LIGHT_COLUMNS == S.LIGHT_COLUMNS, (
        f"scope=all is the light table, got {len(scope.columns)} columns"
    )
    assert len(LIGHT_COLUMNS) == 13
    assert scope.row_count == S.US_TOTAL, (
        f"scope=all must hold the whole universe ({S.US_TOTAL}), got {scope.row_count}"
    )
    assert scope.deep_rows == 0 and manifest["deep_rows"] == 0, (
        "the all scope runs no deep pass"
    )
    assert manifest["columns"] == list(LIGHT_COLUMNS)
    assert manifest["row_count"] == S.US_TOTAL
    assert csv_table[0] == list(LIGHT_COLUMNS)
    assert len(csv_table) - 1 == S.US_TOTAL
    assert {len(row) for row in csv_table[1:]} == {13}, "every light row is 13 cells"
    assert set(records[0]) == set(LIGHT_COLUMNS)
    assert len(records) == S.US_TOTAL

    deep_only = {"instrument_id", "mic", "rsi_14", "opt_iv_atm", "pe_ttm", "figi"}
    assert not deep_only & set(scope.columns), (
        "no canonical-only column may leak into the light table: "
        f"{sorted(deep_only & set(scope.columns))}"
    )
    assert engine.calls == [_expected_deep_symbols(page)], (
        "exporting all must not widen the deep batch beyond the display rows"
    )
    assert scope.row_count - len(engine.calls[0]) == S.US_TOTAL - 20 > 0, (
        "the all table is longer than the deep batch: it is the universe, not the display"
    )


async def test_a_symbol_the_engine_omits_is_a_short_table_not_a_padded_row(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Nothing is invented to reach 20 rows: a dropped symbol is visible as a gap."""

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page = await harness.create_screen()
        omitted = provider_symbol(page["rows"][-1]["symbol"], page["rows"][-1]["exchange"])
        stub = DeepStub(drop=frozenset({omitted}))
        bundle = await export_bundle(
            page["screen_id"],
            repository=harness.screen_repository,
            directory=harness.db_path.parent / "job-p9-short",
            formats=S.DATA_FORMATS,
            deep_engine=stub,
            job_id="job-p9-short",
        )
        scope = bundle.scopes["top20"]
        manifest = read_manifest(scope.manifest_path)
        records = _jsonl_records(scope.files["jsonl"])
        csv_table = _csv_table(scope.files["csv"])

    assert len(stub.calls) == 1 and len(stub.calls[0]) == 20, stub.calls
    assert scope.row_count == len(records) == len(csv_table) - 1 == 19
    assert manifest["row_count"] == 19
    assert scope.deep_rows == DEEP_ROWS == 20, (
        "deep_rows counts the batch the deep pass was asked to cover; a dropped "
        "symbol must show as row_count < deep_rows, not vanish from the manifest"
    )
    assert omitted not in {record["symbol"] for record in records}, (
        "no row may be invented for a symbol the engine did not answer"
    )
    assert all(len(row) == 92 for row in csv_table[1:])
