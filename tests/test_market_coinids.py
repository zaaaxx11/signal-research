"""Contract for the coinids manifest loader (:mod:`app.market.coinids`).

The manifest is server-side configuration the owner edits by hand, so every
rule here is about refusing to guess:

* **the symbol is the identity, the ``id`` is a label** — two rows for ``AAPL``
  under two labels are one instrument duplicated, which is a 422, not two rows;
* **the legacy flat form is read, not upgraded on disk** — the owner's own
  ``{"id1": "AAPL"}`` document normalises into the versioned shape in memory;
* **option rows are contracts** — ``expiry``, ``strike`` and ``right`` are
  required together, because half a contract cannot be looked up;
* **a missing file is an empty manifest** — a fresh deployment has no manifest
  yet, and that is a state, not an error;
* **the path is ``MEE_COINIDS_PATH``** — the same env-prefix convention the
  rest of the process uses, with the documented production default.

The suite is hermetic: every manifest is a real file in ``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.market import coinids
from app.market.coinids import (
    DEFAULT_MANIFEST_PATH,
    ENV_MANIFEST_PATH,
    ManifestError,
    load_manifest,
    parse_manifest,
    resolve_manifest_path,
)


def _write(path: Path, document: Any) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Missing file, and where the file lives
# ---------------------------------------------------------------------------


def test_missing_file_is_empty(tmp_path: Path) -> None:
    """No manifest is an empty manifest: not a 404 and not a traceback."""

    target = tmp_path / "coinid.json"
    manifest = load_manifest(target)

    assert manifest.instruments == ()
    assert manifest.present is False
    assert manifest.path == target
    assert manifest.version == 1
    assert manifest.updated_at is None
    assert manifest.legacy is False


def test_manifest_path_defaults_to_the_documented_data_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_MANIFEST_PATH, raising=False)
    assert resolve_manifest_path() == DEFAULT_MANIFEST_PATH
    assert str(DEFAULT_MANIFEST_PATH) == "/home/ubuntu/ztockz-data/coinid.json"


def test_manifest_path_reads_the_environment_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    override = tmp_path / "elsewhere" / "coinid.json"
    monkeypatch.setenv(ENV_MANIFEST_PATH, str(override))

    assert resolve_manifest_path() == override
    manifest = load_manifest()
    assert manifest.path == override
    assert manifest.present is False


def test_blank_override_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_MANIFEST_PATH, "   ")
    assert resolve_manifest_path() == DEFAULT_MANIFEST_PATH


# ---------------------------------------------------------------------------
# The two forms
# ---------------------------------------------------------------------------


def test_coinids_reads_legacy_flat_form(tmp_path: Path) -> None:
    """``{"id1": "AAPL", "id2": "AMZN"}`` becomes two stock instruments, in order."""

    manifest = load_manifest(_write(tmp_path / "coinid.json", {"id2": "AMZN", "id1": "AAPL"}))

    assert manifest.present is True
    assert manifest.legacy is True
    assert manifest.version == 1
    assert [(row.id, row.symbol, row.asset) for row in manifest.instruments] == [
        ("id2", "AMZN", "stock"),
        ("id1", "AAPL", "stock"),
    ]
    assert all(row.expiry is None and row.strike is None and row.right is None for row in manifest.instruments)


def test_coinids_reads_versioned_form(tmp_path: Path) -> None:
    """The versioned document keeps its stamp, order and contract terms."""

    document = {
        "version": 1,
        "updated_at": "2026-11-02T00:00:00Z",
        "instruments": [
            {"id": "id1", "symbol": "AAPL", "asset": "stock"},
            {
                "id": "id3",
                "symbol": "aapl",
                "asset": "option",
                "expiry": "2026-01-16",
                "strike": 200,
                "right": "c",
            },
        ],
    }
    manifest = load_manifest(_write(tmp_path / "coinid.json", document))

    assert manifest.legacy is False
    assert manifest.version == 1
    assert manifest.updated_at == "2026-11-02T00:00:00Z"
    assert [row.symbol for row in manifest.instruments] == ["AAPL", "AAPL"]
    option = manifest.instruments[1]
    assert (option.asset, option.expiry, option.strike, option.right) == (
        "option",
        "2026-01-16",
        200.0,
        "C",
    )


def test_coinids_normalises_a_missing_asset_to_stock() -> None:
    manifest = parse_manifest({"instruments": [{"symbol": "msft"}]})

    assert manifest.instruments[0].asset == "stock"
    assert manifest.instruments[0].symbol == "MSFT"


def test_coinids_accepts_unknown_top_level_keys() -> None:
    """Forward-compatible: a field this build does not know is not a failure."""

    manifest = parse_manifest(
        {
            "version": 1,
            "instruments": [{"symbol": "AAPL"}],
            "notes": ["owner: keep AAPL first"],
        }
    )
    assert len(manifest.instruments) == 1


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_coinids_rejects_duplicate(tmp_path: Path) -> None:
    """Two rows for one instrument (symbol is identity) is a 422, not two rows."""

    document = {
        "version": 1,
        "instruments": [
            {"id": "id1", "symbol": "AAPL"},
            {"id": "id2", "symbol": "aapl"},
        ],
    }
    with pytest.raises(ManifestError) as caught:
        load_manifest(_write(tmp_path / "coinid.json", document))

    assert "duplicate" in str(caught.value).lower()
    assert caught.value.code == "invalid_manifest"


def test_coinids_rejects_duplicate_id() -> None:
    """An ``id`` is a human label and must name one row."""

    document = {
        "instruments": [
            {"id": "id1", "symbol": "AAPL"},
            {"id": "id1", "symbol": "MSFT"},
        ],
    }
    with pytest.raises(ManifestError) as caught:
        parse_manifest(document)

    assert "duplicate" in str(caught.value).lower()
    assert "id1" in str(caught.value)


def test_coinids_rejects_duplicate_option_contract() -> None:
    """Two identical contracts are one instrument; the strike is compared by value."""

    row = {
        "symbol": "AAPL",
        "asset": "option",
        "expiry": "2026-01-16",
        "strike": 200,
        "right": "C",
    }
    with pytest.raises(ManifestError):
        parse_manifest({"instruments": [row, {**row, "strike": 200.0}]})


def test_coinids_allows_two_strikes_of_one_option_series() -> None:
    """The identity is the whole contract: 200C and 210C are different rows."""

    base = {"symbol": "AAPL", "asset": "option", "expiry": "2026-01-16", "right": "C"}
    manifest = parse_manifest({"instruments": [{**base, "strike": 200}, {**base, "strike": 210}]})

    assert [row.strike for row in manifest.instruments] == [200.0, 210.0]


@pytest.mark.parametrize("missing", ["expiry", "strike", "right"])
def test_coinids_option_requires_contract_fields(missing: str) -> None:
    row: dict[str, Any] = {
        "symbol": "AAPL",
        "asset": "option",
        "expiry": "2026-01-16",
        "strike": 200,
        "right": "C",
    }
    row.pop(missing)

    with pytest.raises(ManifestError) as caught:
        parse_manifest({"instruments": [row]})

    assert missing in str(caught.value)


def test_coinids_rejects_contract_fields_on_a_stock_row() -> None:
    """A forgotten ``asset: "option"`` is named, not silently served as a stock."""

    row = {"symbol": "AAPL", "expiry": "2026-01-16", "strike": 200, "right": "C"}
    with pytest.raises(ManifestError) as caught:
        parse_manifest({"instruments": [row]})

    assert "asset" in str(caught.value)
    assert "option" in str(caught.value)


def test_coinids_rejects_unknown_asset() -> None:
    with pytest.raises(ManifestError) as caught:
        parse_manifest({"instruments": [{"symbol": "BTC-USD", "asset": "crypto"}]})

    assert "crypto" in str(caught.value)


@pytest.mark.parametrize(
    "document",
    [
        [],
        "AAPL",
        {"instruments": "AAPL"},
        {"instruments": [{"symbol": ""}]},
        {"instruments": [{"symbol": 42}]},
        {"instruments": [{"symbol": "AAPL", "id": 7}]},
        {"instruments": [{"symbol": "AAPL", "asset": "option", "expiry": "16-01-2026", "strike": 200, "right": "C"}]},
        {"instruments": [{"symbol": "AAPL", "asset": "option", "expiry": "2026-01-16", "strike": -1, "right": "C"}]},
        {"instruments": [{"symbol": "AAPL", "asset": "option", "expiry": "2026-01-16", "strike": 200, "right": "X"}]},
        {"version": "one", "instruments": []},
        {"version": 1},
        {"id1": 42},
    ],
)
def test_coinids_refuses_malformed_documents(document: Any) -> None:
    with pytest.raises(ManifestError):
        parse_manifest(document)


def test_invalid_json_is_reported_with_the_path(tmp_path: Path) -> None:
    target = tmp_path / "coinid.json"
    target.write_text("{not json", encoding="utf-8")

    with pytest.raises(ManifestError) as caught:
        load_manifest(target)

    assert "not valid JSON" in str(caught.value)
    assert str(target) in str(caught.value)


def test_manifest_error_is_a_valueerror() -> None:
    """The API maps one exception type; a ``ValueError`` keeps that contract."""

    assert issubclass(ManifestError, ValueError)
    assert coinids.ASSET_OPTION == "option"
