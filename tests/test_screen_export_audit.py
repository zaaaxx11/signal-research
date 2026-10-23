"""Screen P4 — the export manifest records the selection that produced it.

Gate (PLAN-finviz-parity.md §4 "Export bundle (all light + top20 deep)" and
§6 #7 "all row_count == total; top20 == 20; manifest verify"):

* both manifests carry ``selection_id``, ``filter_hash``, ``view``,
  ``source_graph_version``, ``criteria_snapshot`` and ``as_of``;
* a bundle exported with no metadata records ``None`` — never an empty criteria
  document, never an invented selection id — while the fields the snapshot
  itself can answer (``filter_hash``, ``as_of``) and the field this checkout
  pins (``source_graph_version``) are read from those sources;
* adding the audit block did not change the tables: still four formats per
  scope, ``all`` == the universe, ``top20`` == 20, every ``sha256`` equal to
  the bytes on disk, every ``coverage.ratio`` == 1.0;
* the manifest declares its own version, and a version this reader does not
  know is refused instead of being verified with the wrong rules.

``tests/fixtures/export_audit.json`` is the spec: the field lists and the
criteria document are read from it rather than rebuilt from the module under
test, so a constant that drifts from the contract cannot satisfy the test by
definition.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from app.export.manifest import (
    AUDIT_FIELDS,
    MANIFEST_VERSION,
    file_sha256,
    read_manifest,
    verify_manifest,
)
from app.screen.export import (
    DATA_FORMATS,
    METADATA_FIELDS,
    ExportMetadata,
    InvalidExportMetadata,
    export_bundle,
)
from app.screen.filters import parse_filters
from app.sources.graph import GRAPH_VERSION
from tests import _screen as S

FIXTURE_PATH = S.FIXTURES / "export_audit.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _criteria() -> dict[str, Any]:
    """A fresh copy of the fixture's criteria document (never shared state)."""

    return _spec()["metadata"]["criteria_snapshot"]


async def _bundle_for(
    harness: S.Harness,
    screen_id: str,
    *,
    directory: Path,
    metadata: Any = None,
    formats: tuple[str, ...] = DATA_FORMATS,
    job_id: str = "job-screen-p4",
) -> tuple[S.FakeDeepEngine, Any]:
    """Export one existing screen, optionally with audit metadata."""

    extra: dict[str, Any] = {} if metadata is None else {"metadata": metadata}
    engine = S.FakeDeepEngine()
    bundle = await export_bundle(
        screen_id,
        repository=harness.screen_repository,
        directory=directory,
        formats=formats,
        deep_engine=engine,
        job_id=job_id,
        **extra,
    )
    return engine, bundle


async def _export(
    harness: S.Harness,
    *,
    metadata: Any = None,
    job_id: str = "job-screen-p4",
) -> tuple[dict[str, Any], S.FakeDeepEngine, Any]:
    page = await harness.create_screen()
    engine, bundle = await _bundle_for(
        harness,
        page["screen_id"],
        directory=harness.db_path.parent / job_id,
        metadata=metadata,
        job_id=job_id,
    )
    return page, engine, bundle


def _manifests(bundle: Any) -> dict[str, dict[str, Any]]:
    return {
        scope.scope: read_manifest(scope.manifest_path) for scope in bundle.scopes.values()
    }


def _audit(manifest: dict[str, Any]) -> dict[str, Any]:
    return {name: manifest.get(name) for name in AUDIT_FIELDS}


def _lines(path: Path) -> list[str]:
    return [line for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# The fixture is the spec
# ---------------------------------------------------------------------------


def test_the_audit_contract_matches_the_frozen_spec() -> None:
    spec = _spec()

    assert list(AUDIT_FIELDS) == spec["audit_fields"], (
        "the manifest must record exactly the six audit fields of the contract; "
        f"module has {list(AUDIT_FIELDS)}, spec has {spec['audit_fields']}"
    )
    assert list(METADATA_FIELDS) == spec["metadata_fields"], (
        "as_of is not caller-supplied: the snapshot dates the table. Metadata "
        f"fields are {list(METADATA_FIELDS)}, spec has {spec['metadata_fields']}"
    )


def test_the_fixture_criteria_are_a_document_the_filter_engine_can_compile() -> None:
    criteria = _criteria()

    compiled = parse_filters(criteria["filters"])

    assert len(compiled) == len(criteria["filters"])
    assert [item.as_dict()["field"] for item in compiled] == [
        item["field"] for item in criteria["filters"]
    ], "the fixture must be a real criteria document, not an arbitrary blob"


# ---------------------------------------------------------------------------
# Metadata present: it appears, in both scopes
# ---------------------------------------------------------------------------


async def test_both_scope_manifests_record_the_metadata_they_were_given(
    tmp_path: Path, monkeypatch: Any
) -> None:
    spec = _spec()
    metadata = spec["metadata"]
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page, _engine, bundle = await _export(harness, metadata=metadata)
        manifests = _manifests(bundle)

    assert set(manifests) == set(S.SCOPES), (
        f"one bundle carries both scopes, got {sorted(manifests)}"
    )
    for scope, manifest in manifests.items():
        for name in ("selection_id", "filter_hash", "view", "source_graph_version"):
            assert manifest[name] == metadata[name], (
                f"scope={scope}: manifest {name} is {manifest[name]!r}, "
                f"caller recorded {metadata[name]!r}"
            )
        assert manifest["criteria_snapshot"] == metadata["criteria_snapshot"], (
            f"scope={scope}: the criteria the bundle answered are not the ones recorded"
        )
        assert manifest["as_of"] == page["as_of"], (
            f"scope={scope}: as_of must be the snapshot's timestamp, got {manifest['as_of']!r}"
        )
        assert manifest["audit_provenance"] == {
            "selection_id": "request",
            "filter_hash": "request",
            "view": "request",
            "source_graph_version": "request",
            "criteria_snapshot": "request",
            "as_of": "screens.as_of",
        }, f"scope={scope}: provenance must say the caller supplied these"

    assert _audit(manifests["all"]) == _audit(manifests["top20"]), (
        "the two tables answer one selection, so their audit blocks must agree"
    )
    assert bundle.metadata.selection_id == metadata["selection_id"]
    assert bundle.metadata == ExportMetadata.from_mapping(metadata)


async def test_a_plain_mapping_is_accepted_and_records_the_same_block(
    tmp_path: Path, monkeypatch: Any
) -> None:
    spec = _spec()
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        _page, _engine, bundle = await _export(harness, metadata=dict(spec["metadata"]))
        manifests = _manifests(bundle)

    for scope, manifest in manifests.items():
        assert manifest["selection_id"] == spec["metadata"]["selection_id"], scope
        assert manifest["filter_hash"] == spec["metadata"]["filter_hash"], scope
        assert manifest["view"] == spec["metadata"]["view"], scope
        assert (
            manifest["source_graph_version"] == spec["metadata"]["source_graph_version"]
        ), scope
        assert manifest["criteria_snapshot"] == spec["metadata"]["criteria_snapshot"], scope


async def test_the_recorded_criteria_are_a_copy_not_a_live_reference(
    tmp_path: Path, monkeypatch: Any
) -> None:
    criteria = _criteria()
    metadata = {"selection_id": "sel_p4_copy", "criteria_snapshot": criteria}
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        _page, _engine, bundle = await _export(harness, metadata=metadata)
        manifest = read_manifest(bundle.scopes["top20"].manifest_path)
        # Mutate the caller's request after the export: the manifest is a
        # snapshot of what the question *was*, so neither the file nor the
        # in-memory copy may move with it.
        criteria["sort"] = "gainers"
        criteria["filters"].append({"field": "price", "op": "gt", "value": 1})
        assert bundle.metadata.criteria_snapshot == _criteria(), (
            "ExportMetadata must deep-copy the criteria, not hold the caller's dict"
        )

    assert manifest["criteria_snapshot"] == _criteria()
    assert manifest["criteria_snapshot"]["sort"] == "market_cap"


# ---------------------------------------------------------------------------
# Metadata absent: null, not a guess
# ---------------------------------------------------------------------------


async def test_a_bundle_without_metadata_records_none_instead_of_a_guess(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page, _engine, bundle = await _export(harness)
        stored = S.stored_screen(harness.db_path, page["screen_id"])
        manifests = _manifests(bundle)

    for scope, manifest in manifests.items():
        assert set(AUDIT_FIELDS) <= set(manifest), (
            f"scope={scope}: every audit field must be present even when unknown, "
            f"got {sorted(manifest)}"
        )
        for name in ("selection_id", "view", "criteria_snapshot"):
            assert manifest[name] is None, (
                f"scope={scope}: {name} was invented as {manifest[name]!r}; a bundle "
                "that was told nothing must record nothing"
            )
        assert manifest["filter_hash"] == stored["filters_hash"], (
            f"scope={scope}: filter_hash must be the stored recipe "
            f"{stored['filters_hash']!r}, got {manifest['filter_hash']!r}"
        )
        assert manifest["as_of"] == stored["as_of"] == page["as_of"], (
            f"scope={scope}: as_of must be the snapshot's own timestamp"
        )
        assert manifest["source_graph_version"] == GRAPH_VERSION, (
            f"scope={scope}: source_graph_version must be the version of the graph "
            f"this checkout pins ({GRAPH_VERSION!r}), got {manifest['source_graph_version']!r}"
        )
        assert manifest["audit_provenance"] == {
            "selection_id": "not-recorded",
            "filter_hash": "screens.filters_hash",
            "view": "not-recorded",
            "source_graph_version": "reference/source_graph.json",
            "criteria_snapshot": "not-recorded",
            "as_of": "screens.as_of",
        }, f"scope={scope}: provenance must distinguish derived from not-recorded"


def test_metadata_is_an_optional_keyword_that_defaults_to_none() -> None:
    signature = inspect.signature(export_bundle)
    parameters = signature.parameters

    assert list(parameters) == [
        "screen_id",
        "repository",
        "directory",
        "formats",
        "deep_engine",
        "job_id",
        "metadata",
    ], "the pre-audit parameters must keep their names and order"
    assert parameters["metadata"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["metadata"].default is None, (
        "metadata must be optional: every call written before the audit contract "
        "has to keep working"
    )
    assert parameters["screen_id"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD


# ---------------------------------------------------------------------------
# Refused metadata costs nothing
# ---------------------------------------------------------------------------


async def test_refused_metadata_is_refused_before_a_scan_is_paid_for(
    tmp_path: Path, monkeypatch: Any
) -> None:
    spec = _spec()
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page = await harness.create_screen()
        before = S.provider_requests(harness.web)
        for key in spec["forged"]:
            with pytest.raises(InvalidExportMetadata) as excinfo:
                await export_bundle(
                    page["screen_id"],
                    repository=harness.screen_repository,
                    directory=tmp_path / "refused",
                    formats=("csv",),
                    deep_engine=S.FakeDeepEngine(),
                    metadata={key: spec["forged"][key]},
                )
            assert key in str(excinfo.value), (
                f"the refusal must name the offending field {key!r}: {excinfo.value}"
            )
        after = S.provider_requests(harness.web)

    assert after == before, (
        "metadata is validated before the universe is fetched; a refused document "
        f"must cost no provider request (went {before} -> {after})"
    )
    assert not (tmp_path / "refused").exists(), "nothing is written for a refused metadata"


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"view": "screener"}, "unknown view"),
        ({"selection_id": "   "}, "non-empty string"),
        ({"filter_hash": 42}, "non-empty string"),
        ({"criteria_snapshot": ["market_cap"]}, "must be a mapping"),
        ({"criteria_snapshot": {"filters": {"gte"}}}, "not JSON-serialisable"),
        ({"criteria_snapshot": {"value": float("nan")}}, "not JSON-serialisable"),
        ({"criteria_snapshot": {1: "us"}}, "non-string key"),
    ],
)
def test_metadata_values_are_validated(metadata: dict[str, Any], expected: str) -> None:
    with pytest.raises(InvalidExportMetadata) as excinfo:
        ExportMetadata.from_mapping(metadata)

    assert expected in str(excinfo.value), str(excinfo.value)


@pytest.mark.parametrize("metadata", [42, "selection=all", object()])
async def test_metadata_of_the_wrong_type_is_refused(
    tmp_path: Path, monkeypatch: Any, metadata: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page = await harness.create_screen()
        before = S.provider_requests(harness.web)
        with pytest.raises(InvalidExportMetadata) as excinfo:
            await export_bundle(
                page["screen_id"],
                repository=harness.screen_repository,
                directory=tmp_path / "refused",
                formats=("csv",),
                deep_engine=S.FakeDeepEngine(),
                metadata=metadata,
            )
        after = S.provider_requests(harness.web)

    assert "metadata must be" in str(excinfo.value), str(excinfo.value)
    assert after == before, "a metadata object of the wrong type costs no provider request"


# ---------------------------------------------------------------------------
# The tables did not change; the audit block did
# ---------------------------------------------------------------------------


async def test_audit_metadata_does_not_change_the_exported_table(
    tmp_path: Path, monkeypatch: Any
) -> None:
    spec = _spec()
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page = await harness.create_screen()
        _plain_engine, plain = await _bundle_for(
            harness, page["screen_id"], directory=tmp_path / "plain", job_id="job-plain"
        )
        _audited_engine, audited = await _bundle_for(
            harness,
            page["screen_id"],
            directory=tmp_path / "audited",
            metadata=spec["metadata"],
            job_id="job-audited",
        )

    for fmt in ("csv", "jsonl"):
        for scope in S.SCOPES:
            assert file_sha256(plain.scopes[scope].files[fmt]) == file_sha256(
                audited.scopes[scope].files[fmt]
            ), (
                f"scope={scope} {fmt}: the audit block must not be injected into the "
                "data files, so the bytes must be identical"
            )

    assert _audit(read_manifest(audited.scopes["all"].manifest_path)) != {
        name: None for name in AUDIT_FIELDS
    }, "precondition: the audited bundle must differ from the plain one in its manifest"


# ---------------------------------------------------------------------------
# Hash, counts and coverage still hold
# ---------------------------------------------------------------------------


async def test_every_artifact_is_hashed_from_the_bytes_on_disk(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        _page, _engine, bundle = await _export(harness, metadata=_spec()["metadata"])

        for scope in bundle.scopes.values():
            manifest = read_manifest(scope.manifest_path)
            entries = {entry["name"]: entry for entry in manifest["files"]}
            assert set(entries) == {Path(path).name for path in scope.files.values()}, (
                f"scope={scope.scope}: the manifest must describe exactly the files "
                f"written, got {sorted(entries)}"
            )
            for fmt, path in scope.files.items():
                entry = entries[Path(path).name]
                assert entry["sha256"] == file_sha256(path), (
                    f"scope={scope.scope} {fmt}: sha256 does not match the bytes on disk"
                )
                assert entry["bytes"] == Path(path).stat().st_size
                assert entry["row_count"] == manifest["row_count"]

            check = verify_manifest(scope.manifest_path)
            assert check.ok is True, f"scope={scope.scope}: {check.problems}"
            assert check.files_checked == len(DATA_FORMATS)


async def test_all_counts_the_universe_and_top20_counts_the_display(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        _page, _engine, bundle = await _export(harness)
        manifests = _manifests(bundle)
        line_counts = {
            name: len(_lines(scope.files["jsonl"])) for name, scope in bundle.scopes.items()
        }

    assert manifests["all"]["row_count"] == S.US_TOTAL, (
        f"scope=all must hold the whole universe ({S.US_TOTAL}), got "
        f"{manifests['all']['row_count']}"
    )
    assert manifests["top20"]["row_count"] == S.DEEP_ROWS == 20, (
        f"scope=top20 must hold the displayed rows ({S.DEEP_ROWS}), got "
        f"{manifests['top20']['row_count']}"
    )
    assert line_counts == {"all": S.US_TOTAL, "top20": S.DEEP_ROWS}, line_counts
    assert manifests["all"]["deep_rows"] == 0
    assert manifests["top20"]["deep_rows"] == S.DEEP_ROWS

    for scope, manifest in manifests.items():
        coverage = manifest["coverage"]
        assert coverage["rows_expected"] == coverage["rows_written"] == manifest["row_count"], (
            f"scope={scope}: coverage must be a comparison, got {coverage}"
        )
        assert coverage["ratio"] == 1.0, f"scope={scope}: coverage is not complete"
        assert set(coverage["by_format"]) == set(DATA_FORMATS)
        assert set(coverage["by_format"].values()) == {manifest["row_count"]}
        assert manifest["files_count"] == len(manifest["files"]) == len(DATA_FORMATS)


# ---------------------------------------------------------------------------
# The manifest version
# ---------------------------------------------------------------------------


async def test_the_manifest_declares_its_version_and_refuses_an_unknown_one(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        _page, _engine, bundle = await _export(harness)
        for scope, manifest in _manifests(bundle).items():
            assert manifest["manifest_version"] == MANIFEST_VERSION, (
                f"scope={scope}: a manifest must say which shape it is"
            )

        path = bundle.scopes["all"].manifest_path
        assert verify_manifest(path).ok is True

        document = read_manifest(path)
        document["manifest_version"] = "v999"
        path.write_text(json.dumps(document), encoding="utf-8")
        unknown = verify_manifest(path)

        # A manifest written before the field existed is still read as v1 ...
        document.pop("manifest_version")
        path.write_text(json.dumps(document), encoding="utf-8")
        legacy = verify_manifest(path)

    assert unknown.ok is False, "a version this reader does not know must not be vouched for"
    assert any("manifest_version" in problem for problem in unknown.problems), unknown.problems
    assert legacy.ok is True, legacy.problems


async def test_a_manifest_that_cannot_date_its_table_is_refused(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        _page, _engine, bundle = await _export(harness)
        path = bundle.scopes["top20"].manifest_path
        document = read_manifest(path)
        document["as_of"] = None
        path.write_text(json.dumps(document), encoding="utf-8")

        check = verify_manifest(path)

    assert check.ok is False, "as_of is what makes the bundle auditable"
    assert any("as_of" in problem for problem in check.problems), check.problems


# ---------------------------------------------------------------------------
# The HTTP export path keeps working (and invents nothing it was not told)
# ---------------------------------------------------------------------------


async def test_the_http_export_publishes_audited_manifests(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        page = await harness.create_screen()
        S.install_deep_engine(harness.runtime, S.FakeDeepEngine())

        created = await harness.client.post(
            f"/screens/{page['screen_id']}/export",
            json={"formats": list(DATA_FORMATS)},
        )
        assert created.status_code in (200, 202), created.text
        job_id = created.json()["job_id"]

        final = await S.wait_for_terminal(harness.client, job_id)
        assert final["status"] == "Success", final
        assert S.artifact_kinds() <= set(final["files"]), sorted(final["files"])

        directory = Path(harness.runtime.export_dir) / f"screen-{job_id}"
        manifests = {
            name: read_manifest(directory / f"{job_id}-{name}.manifest.json")
            for name in S.SCOPES
        }

    for scope, manifest in manifests.items():
        assert manifest["manifest_version"] == MANIFEST_VERSION, scope
        assert set(AUDIT_FIELDS) <= set(manifest), scope
        # The route passes no metadata today; whatever it does or does not
        # record, "not-recorded" must never sit next to an invented value.
        for name, source in manifest["audit_provenance"].items():
            if source == "not-recorded":
                assert manifest[name] is None, (
                    f"scope={scope}: provenance says {name} was not recorded but the "
                    f"manifest carries {manifest[name]!r}"
                )
        assert manifest["filter_hash"], f"scope={scope}: the stored recipe is recorded"
        assert manifest["as_of"] == manifests["all"]["as_of"], scope
        assert verify_manifest(
            directory / f"{job_id}-{scope}.manifest.json"
        ).ok is True, scope
