"""Fase 6 — the export wiring: every declared format reaches a real artifact.

What these tests refuse to accept:

* ``IMPLEMENTED_FORMATS`` narrower than ``FORMATS``: the writer can produce
  xlsx/jsonl/parquet but the API still answers ``501`` — a capability nobody can
  download is not a capability;
* a ``Success`` whose ``result_count`` is not the number of rows inside the
  files it points at (the artifact is read with the format's own library, never
  with the writer);
* a bundle without ``<job>.manifest.json``, or a manifest whose hashes do not
  verify (02 §3.8.2: the manifest is mandatory for every successful job);
* an unknown format that is accepted instead of rejected with ``422``.

The job-level test runs the production entrypoint ``process_job`` on the fake
provider, so "the runner writes four formats" is a property of the runner, not
of a test-only helper.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests import _wiring as W


async def _inline_job(client: Any, formats: list[str]) -> dict[str, Any]:
    """Submit one inline job and return its view (200, never 202)."""

    response = await client.post(
        "/markets/enrich", json=W.submit_payload(format=list(formats))
    )
    assert response.status_code == 200, (
        f"POST /markets/enrich format={formats} -> {response.status_code}: {response.text}"
    )
    view = response.json()
    assert view["status"] == "Success", view.get("error") or view
    return view


# ---------------------------------------------------------------------------
# The advertised surface
# ---------------------------------------------------------------------------


def test_implemented_formats_covers_every_writer_format() -> None:
    """The API may only advertise formats the runner actually materialises."""

    declared = W.formats()
    assert W.FOUR_FORMATS == ("csv", "xlsx", "jsonl", "parquet")
    for fmt in W.FOUR_FORMATS:
        assert fmt in declared, f"{fmt!r} is a writer format but not in FORMATS={declared}"
    assert W.implemented_formats() == declared, (
        "the writer produces every format, so the runner (and therefore the API) "
        f"must advertise all of them; IMPLEMENTED_FORMATS={W.implemented_formats()!r} "
        f"vs FORMATS={declared!r} (19 §4.1: the tuple is raised by the wiring step)"
    )
    assert "xml" not in declared


async def test_unknown_format_is_rejected_with_422(tmp_path: Path, monkeypatch: Any) -> None:
    """``xml`` is not a format: pydantic refuses it before a job exists."""

    async with W.api(tmp_path, monkeypatch) as (client, exports):
        response = await client.post(
            "/markets/enrich", json=W.submit_payload(format=["xml"])
        )
        assert response.status_code == 422, (
            "an unknown format is a validation error (422), not a 501 and not a "
            f"job: {response.status_code} {response.text}"
        )
        assert list(exports.glob("*")) == [], "a rejected request wrote an artifact"


# ---------------------------------------------------------------------------
# POST /markets/enrich — three formats that used to be 501
# ---------------------------------------------------------------------------


async def test_enrich_writes_xlsx_jsonl_parquet_and_a_manifest(
    tmp_path: Path, monkeypatch: Any
) -> None:
    formats = ["xlsx", "jsonl", "parquet"]

    async with W.api(tmp_path, monkeypatch) as (client, exports):
        view = await _inline_job(client, formats)
        job_id = view["id"]
        assert view["result_count"] == 3, view
        assert set(view["files"]) == set(formats), (
            "GET /requests/{id}.files must expose one download per written format: "
            f"{sorted(view['files'])}"
        )

        for fmt in formats:
            path = exports / f"{job_id}.{fmt}"
            assert path.exists(), f"{fmt} was promised by the API but {path} does not exist"
            assert path.stat().st_size > 0, f"{path} is empty"
            assert W.artifact_row_count(fmt, path) == view["result_count"], (
                f"{path.name} does not hold result_count rows"
            )
            assert W.artifact_header(fmt, path) == W.column_oracle_header(), (
                f"{path.name} does not carry the frozen v1 header"
            )

        manifest_path = exports / f"{job_id}.manifest.json"
        assert manifest_path.exists(), (
            "02 §3.8.2: every successful job ships a manifest; the runner wrote the "
            "data files without one"
        )
        check = W.verify_manifest(manifest_path)
        assert W.check_field(check, "ok") is True, W.check_field(check, "problems")
        assert W.check_field(check, "files_checked") == len(formats)

        manifest = W.read_manifest(manifest_path)
        assert manifest["row_count"] == view["result_count"] == 3
        assert manifest["coverage"]["ratio"] == 1.0
        assert manifest["coverage"]["by_format"] == {fmt: 3 for fmt in formats}
        assert {entry["name"] for entry in manifest["files"]} == {
            f"{job_id}.{fmt}" for fmt in formats
        }

        # The download route serves the artifact the manifest describes.
        for fmt in formats:
            response = await client.get(f"/requests/{job_id}/export/{fmt}")
            assert response.status_code == 200, (
                f"GET export/{fmt} -> {response.status_code}: {response.text}"
            )


async def test_export_route_serves_each_wired_format_with_its_media_type(
    tmp_path: Path, monkeypatch: Any
) -> None:
    formats = list(W.FOUR_FORMATS)

    async with W.api(tmp_path, monkeypatch) as (client, exports):
        view = await _inline_job(client, formats)
        job_id = view["id"]

        for fmt in formats:
            response = await client.get(f"/requests/{job_id}/export/{fmt}")
            assert response.status_code == 200, (
                f"GET export/{fmt} -> {response.status_code}: {response.text}"
            )
            content_type = response.headers.get("content-type", "")
            assert content_type.split(";")[0].strip().lower() == W.MEDIA_TYPES[fmt], (
                f"{fmt}: expected media type {W.MEDIA_TYPES[fmt]!r}, got {content_type!r}"
            )
            assert response.content, f"{fmt}: the download is empty"
            served = exports / f"{job_id}.{fmt}"
            assert response.content == served.read_bytes(), (
                f"{fmt}: the response is not the artifact on disk (a re-render?)"
            )


# ---------------------------------------------------------------------------
# The runner: four formats, one manifest, real row counts
# ---------------------------------------------------------------------------


async def test_runner_writes_four_formats_with_a_manifest_and_row_count(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from app.jobs.repository import SqliteJobRepository

    exports = tmp_path / "exports"
    repository = SqliteJobRepository(tmp_path / "mee.db")
    try:
        job = await repository.create(
            params=W.submit_payload(format=list(W.FOUR_FORMATS)),
            api_key="wiring-client",
            rows_target=3,
            retention_days=7,
        )
        record = await W.runner_module().process_job(
            job["id"], repository=repository, export_dir=exports
        )
        artifacts = await repository.artifacts(job["id"])
    finally:
        await repository.close()

    assert record["status"] == "Success", record.get("error")
    assert record["result_count"] == 3, record

    job_id = job["id"]
    for fmt in W.FOUR_FORMATS:
        path = exports / f"{job_id}.{fmt}"
        assert path.exists(), f"the runner did not write {fmt} ({path})"
        assert path.stat().st_size > 0, f"{path} is empty"
        assert W.artifact_row_count(fmt, path) == record["result_count"]

    manifest_path = exports / f"{job_id}.manifest.json"
    assert manifest_path.exists(), (
        "the runner wrote four data files but no manifest: the bundle is unsigned "
        "and the download route has nothing to verify against"
    )
    manifest = W.read_manifest(manifest_path)
    assert manifest["job_id"] == job_id
    assert manifest["row_count"] == record["result_count"]
    assert manifest["files_count"] == len(W.FOUR_FORMATS)
    assert manifest["coverage"]["rows_written"] == record["result_count"]
    assert manifest["coverage"]["rows_expected"] == record["result_count"]
    assert manifest["coverage"]["by_format"] == {
        fmt: record["result_count"] for fmt in W.FOUR_FORMATS
    }
    assert manifest["quality"].get("provenance") != "default-placeholder", (
        "the runner must pass the job's real quality block, not the frozen "
        "placeholder (19 §4.4)"
    )

    check = W.verify_manifest(manifest_path)
    assert W.check_field(check, "ok") is True, W.check_field(check, "problems")

    stored = {artifact["kind"]: artifact for artifact in artifacts}
    assert set(stored) == set(W.FOUR_FORMATS), (
        f"job_artifacts must record one row per format, got {sorted(stored)}"
    )
    for fmt, artifact in stored.items():
        assert artifact["row_count"] == record["result_count"], (fmt, artifact)
        assert artifact["bytes"] == (exports / f"{job_id}.{fmt}").stat().st_size
        assert artifact["sha256"]

    # The manifest is about the data files, never about itself or the custody log.
    names = {entry["name"] for entry in manifest["files"]}
    assert f"{job_id}.manifest.json" not in names
    assert not any(name.endswith(".custody.jsonl") for name in names)


async def test_jsonl_artifact_is_one_json_object_per_row(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A line-shaped file must be readable line by line, not only as a blob."""

    async with W.api(tmp_path, monkeypatch) as (client, exports):
        view = await _inline_job(client, ["jsonl"])
        path = exports / f"{view['id']}.jsonl"
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == view["result_count"]
        first = json.loads(lines[0])
        assert list(first) == W.column_oracle_header()
