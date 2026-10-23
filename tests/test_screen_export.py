"""Screen S0 RED — the export bundle: ``all`` light + ``top20`` deep, in four formats.

Gate (PLAN-screen.md §4, §8 #5/#6, revised by the user):

* one bundle carries **two tables**: ``all`` (light columns, the full universe)
  and ``top20`` (the 92 canonical columns, exactly 20 rows);
* ``all`` is fetched **on demand at export time**, not recycled from the 20
  displayed ``screen_rows`` — a 23-issuer universe must export 23 rows;
* every scope has a manifest whose ``sha256`` matches the bytes on disk;
* four formats per scope (csv, xlsx, jsonl, parquet) and the artifacts are
  served by kind: ``all_csv``, ``top20_csv``, ...
* enrichment is the expensive half: the deep engine is called once, with the
  top 20 symbols, and never with the whole universe.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from tests import _screen as S


async def _bundle(harness: S.Harness) -> tuple[dict[str, Any], S.FakeDeepEngine, Any]:
    """Create a screen through the API, then export it with a recording engine."""

    page = await harness.create_screen()
    engine = S.FakeDeepEngine()
    bundle = await S.exporter().export_bundle(
        page["screen_id"],
        repository=harness.screen_repository,
        directory=harness.db_path.parent / "bundle",
        formats=S.DATA_FORMATS,
        deep_engine=engine,
        job_id="job-screen-s0",
    )
    return page, engine, bundle


def _manifest(scope: Any) -> dict[str, Any]:
    return json.loads(Path(scope.manifest_path).read_text(encoding="utf-8"))


def _lines(path: Path) -> list[str]:
    return [line for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Two scopes
# ---------------------------------------------------------------------------


async def test_bundle_has_an_all_scope_and_a_top20_scope(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        _page, _engine, bundle = await _bundle(harness)

    assert set(bundle.scopes) == set(S.SCOPES), (
        f"one bundle carries both scopes, got {sorted(bundle.scopes)}"
    )
    every = bundle.scopes["all"]
    top = bundle.scopes["top20"]

    assert every.row_count == S.US_TOTAL, (
        f"scope=all must hold the whole universe ({S.US_TOTAL}), got {every.row_count}"
    )
    assert every.deep_rows == 0
    assert tuple(every.columns) == S.LIGHT_COLUMNS, (
        "scope=all is light: the 13 columns of PLAN-screen.md §3, got "
        f"{len(every.columns)} columns"
    )

    assert top.row_count == S.DEEP_ROWS == 20
    assert top.deep_rows == S.DEEP_ROWS
    assert tuple(top.columns) == S.COLUMNS, (
        f"scope=top20 is the 92-column canonical table, got {len(top.columns)} columns"
    )


async def test_all_scope_is_fetched_on_demand_not_from_the_display_rows(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page = await harness.create_screen()
        stored = S.stored_rows(harness.db_path, page["screen_id"])
        before = S.provider_requests(harness.web)

        engine = S.FakeDeepEngine()
        bundle = await S.exporter().export_bundle(
            page["screen_id"],
            repository=harness.screen_repository,
            directory=tmp_path / "bundle",
            formats=("csv",),
            deep_engine=engine,
        )
        after = S.provider_requests(harness.web)

    assert len(stored) == S.DISPLAY_ROWS, "precondition: the display stored 20 rows"
    assert bundle.scopes["all"].row_count == S.US_TOTAL, (
        "export all is the full universe, so it must be longer than the 20-row "
        f"display; got {bundle.scopes['all'].row_count}"
    )
    assert after == before + 1, (
        "the full universe is fetched on demand exactly once at export time; "
        f"provider requests went {before} -> {after}"
    )


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


async def test_each_scope_manifest_hashes_the_artifacts_it_describes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        _page, _engine, bundle = await _bundle(harness)

    for scope in bundle.scopes.values():
        manifest = _manifest(scope)
        assert manifest["scope"] == scope.scope
        assert manifest["row_count"] == scope.row_count
        assert manifest["deep_rows"] == scope.deep_rows
        assert manifest["coverage"]["rows_written"] == scope.row_count
        assert manifest["coverage"]["ratio"] == 1.0, (
            f"scope={scope.scope} coverage is not complete: {manifest['coverage']}"
        )
        assert len(manifest["files"]) == len(S.DATA_FORMATS)

        hashes = {entry["name"]: entry["sha256"] for entry in manifest["files"]}
        assert set(hashes) == {Path(path).name for path in scope.files.values()}
        for fmt, path in scope.files.items():
            assert hashes[Path(path).name] == S.file_sha256(path), (
                f"scope={scope.scope} {fmt}: manifest sha256 does not match the file"
            )


async def test_all_scope_manifest_promises_the_total_it_wrote(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        _page, _engine, bundle = await _bundle(harness)

    manifest = _manifest(bundle.scopes["all"])
    assert manifest["row_count"] == S.US_TOTAL
    assert {entry["row_count"] for entry in manifest["files"]} == {S.US_TOTAL}, (
        "every all artifact must hold the same row count; entries say "
        f"{[entry['row_count'] for entry in manifest['files']]}"
    )


# ---------------------------------------------------------------------------
# Formats
# ---------------------------------------------------------------------------


async def test_four_formats_are_written_for_each_scope(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        _page, _engine, bundle = await _bundle(harness)

    for scope in bundle.scopes.values():
        assert set(scope.files) == set(S.DATA_FORMATS), (
            f"scope={scope.scope} formats: {sorted(scope.files)}"
        )
        for fmt, path in scope.files.items():
            path = Path(path)
            assert path.exists() and path.stat().st_size > 0, f"{fmt} artifact is empty"


async def test_all_and_top20_line_counts_match_the_manifest(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        _page, _engine, bundle = await _bundle(harness)

    assert len(_lines(bundle.scopes["all"].files["jsonl"])) == S.US_TOTAL
    assert len(_lines(bundle.scopes["top20"].files["jsonl"])) == S.DEEP_ROWS

    all_table = list(csv.reader(io.StringIO(bundle.scopes["all"].files["csv"].read_text())))
    assert all_table[0] == list(S.LIGHT_COLUMNS)
    assert len(all_table) - 1 == S.US_TOTAL

    top_table = list(csv.reader(io.StringIO(bundle.scopes["top20"].files["csv"].read_text())))
    assert top_table[0] == list(S.COLUMNS)
    assert len(top_table) - 1 == S.DEEP_ROWS
    assert all(len(row) == 92 for row in top_table[1:]), [len(row) for row in top_table[1:]]


# ---------------------------------------------------------------------------
# Deep only for the top 20
# ---------------------------------------------------------------------------


async def test_deep_engine_is_called_once_with_the_top_twenty_symbols(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page, engine, bundle = await _bundle(harness)

    expected = [row["symbol"] for row in page["rows"]]
    assert len(expected) == S.DEEP_ROWS
    assert engine.calls == [expected], (
        "the deep engine runs once, on the displayed top 20, in rank order; got "
        f"{engine.calls}"
    )
    assert bundle.scopes["top20"].row_count == len(expected)


async def test_the_light_scope_carries_no_deep_columns(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        _page, _engine, bundle = await _bundle(harness)

    light = set(bundle.scopes["all"].columns)
    assert "rsi_14" not in light and "beta_1_year" not in light
    assert len(bundle.scopes["all"].columns) < len(S.COLUMNS), (
        "92 columns for the whole universe is the thing the plan refuses"
    )


# ---------------------------------------------------------------------------
# HTTP: POST /screens/{id}/export and the artifacts it publishes
# ---------------------------------------------------------------------------


async def test_post_export_publishes_all_and_top20_artifacts(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page = await harness.create_screen()
        engine = S.FakeDeepEngine()
        S.install_deep_engine(harness.runtime, engine)

        created = await harness.client.post(
            f"/screens/{page['screen_id']}/export",
            json={"formats": list(S.DATA_FORMATS)},
        )
        assert created.status_code in (200, 202), created.text
        job_id = created.json()["job_id"]

        final = await S.wait_for_terminal(harness.client, job_id)
        assert final["status"] == "Success", final
        assert S.artifact_kinds() <= set(final["files"]), (
            "job artifacts are published per scope and format; got "
            f"{sorted(final['files'])}"
        )

        all_csv = await harness.client.get(f"/requests/{job_id}/export/all_csv")
        top_csv = await harness.client.get(f"/requests/{job_id}/export/top20_csv")
        requests_after = S.provider_requests(harness.web)

    assert all_csv.status_code == 200, all_csv.text
    assert all_csv.headers["content-type"].startswith("text/csv")
    all_table = list(csv.reader(io.StringIO(all_csv.text)))
    assert all_table[0] == list(S.LIGHT_COLUMNS)
    assert len(all_table) - 1 == S.US_TOTAL

    assert top_csv.status_code == 200, top_csv.text
    top_table = list(csv.reader(io.StringIO(top_csv.text)))
    assert top_table[0] == list(S.COLUMNS)
    assert len(top_table) - 1 == S.DEEP_ROWS

    assert engine.calls == [[row["symbol"] for row in page["rows"]]]
    assert requests_after == 2, (
        "one scan for the display and one on-demand scan for the full export; got "
        f"{requests_after} provider requests"
    )


async def test_export_of_an_unknown_screen_is_404(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/scr_does_not_exist/export", json={"formats": ["csv"]}
        )

    assert response.status_code == 404, response.text
    assert {"error", "errorMessage"} <= set(response.json())


async def test_export_rejects_an_unknown_format(tmp_path: Path, monkeypatch: Any) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page = await harness.create_screen()
        S.install_deep_engine(harness.runtime, S.FakeDeepEngine())

        response = await harness.client.post(
            f"/screens/{page['screen_id']}/export", json={"formats": ["pdf"]}
        )

    assert response.status_code == 422, response.text
