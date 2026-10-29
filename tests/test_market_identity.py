"""Contract for the identity join (``app.market.identity``).

The universe file and the engine snapshot already carry the identity columns
the screener publishes; the fast path simply never joined them. This module is
that join, and the rules are the same ones the rest of the engine keeps:

* **Absent, never invented.** A symbol the universe does not carry contributes
  nothing — not a guessed sector, not a default country.
* **Two vocabularies, honestly labelled.** ``sector`` comes from the universe
  file's TradingView taxonomy (``Electronic Technology``); ``industry`` comes
  from the SEC snapshot's SIC description (``Semiconductors``). They are not
  the same taxonomy and are never merged into one column.
* **The row key is the ticker.** ``ticker`` is the canonical symbol the row is
  keyed by, not a second lookup.
* **No network, no disk writes.** Both documents are read once at construction.

No test reaches the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.market.identity import IdentityIndex

UNIVERSE = Path("/home/ubuntu/briefs/market-engine/research/probes-1000/universe.json")
SNAPSHOT = Path("/home/ubuntu/ztockz-data/engine-snapshot.json")


def _universe(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "universe.json"
    path.write_text(json.dumps({"rows": rows}))
    return path


def _snapshot(tmp_path: Path, securities: list[dict]) -> Path:
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"dataset": {"securities": securities}}))
    return path


NVDA_UNIVERSE = {
    "name": "NVDA",
    "description": "NVIDIA Corporation",
    "exchange": "NASDAQ",
    "country": "United States",
    "sector": "Electronic Technology",
    "type": "stock",
    "subtype": "common",
}

NVDA_SNAPSHOT = {
    "symbol": "NVDA",
    "name": "NVIDIA CORP",
    "cik": "0001045810",
    "exchange": "Nasdaq",
    "industry": "Semiconductors",
    "country": "United States",
}


def test_the_row_key_is_the_ticker(tmp_path: Path) -> None:
    index = IdentityIndex(
        universe_path=_universe(tmp_path, [NVDA_UNIVERSE]),
        snapshot_path=_snapshot(tmp_path, [NVDA_SNAPSHOT]),
    )
    fields = index.fields_for("NVDA")
    assert fields["ticker"] == "NVDA"


def test_sector_comes_from_the_universe_taxonomy(tmp_path: Path) -> None:
    index = IdentityIndex(
        universe_path=_universe(tmp_path, [NVDA_UNIVERSE]),
        snapshot_path=_snapshot(tmp_path, [NVDA_SNAPSHOT]),
    )
    assert index.fields_for("NVDA")["sector"] == "Electronic Technology"


def test_industry_comes_from_the_sec_sic_description(tmp_path: Path) -> None:
    index = IdentityIndex(
        universe_path=_universe(tmp_path, [NVDA_UNIVERSE]),
        snapshot_path=_snapshot(tmp_path, [NVDA_SNAPSHOT]),
    )
    assert index.fields_for("NVDA")["industry"] == "Semiconductors"


def test_the_two_taxonomies_stay_separate_columns(tmp_path: Path) -> None:
    """TradingView sector and SEC SIC industry are never merged."""

    index = IdentityIndex(
        universe_path=_universe(tmp_path, [NVDA_UNIVERSE]),
        snapshot_path=_snapshot(tmp_path, [NVDA_SNAPSHOT]),
    )
    fields = index.fields_for("NVDA")
    assert fields["sector"] != fields["industry"]
    assert set(fields) >= {"sector", "industry"}


def test_company_country_and_security_type_join(tmp_path: Path) -> None:
    index = IdentityIndex(
        universe_path=_universe(tmp_path, [NVDA_UNIVERSE]),
        snapshot_path=_snapshot(tmp_path, [NVDA_SNAPSHOT]),
    )
    fields = index.fields_for("NVDA")
    assert fields["company"] == "NVIDIA Corporation"
    assert fields["country"] == "United States"
    assert fields["security_type"] == "stock"


def test_a_symbol_the_universe_does_not_carry_is_absent(tmp_path: Path) -> None:
    index = IdentityIndex(
        universe_path=_universe(tmp_path, [NVDA_UNIVERSE]),
        snapshot_path=_snapshot(tmp_path, [NVDA_SNAPSHOT]),
    )
    fields = index.fields_for("ZZZZ")
    assert fields["ticker"] is None
    assert fields["sector"] is None
    assert fields["industry"] is None, "never guessed from a neighbour"


def test_the_snapshot_supplies_industry_when_the_universe_has_none(
    tmp_path: Path,
) -> None:
    row = {k: v for k, v in NVDA_UNIVERSE.items() if k != "sector"}
    index = IdentityIndex(
        universe_path=_universe(tmp_path, [row]),
        snapshot_path=_snapshot(tmp_path, [NVDA_SNAPSHOT]),
    )
    fields = index.fields_for("NVDA")
    assert fields["sector"] is None
    assert fields["industry"] == "Semiconductors", "the SIC description survives"


def test_symbol_canonicalization(tmp_path: Path) -> None:
    """BRK.B and BRK-B are one row, keyed the way the rest of the engine keys them."""

    row = dict(NVDA_UNIVERSE, name="BRK.B")
    index = IdentityIndex(
        universe_path=_universe(tmp_path, [row]),
        snapshot_path=_snapshot(tmp_path, []),
    )
    assert index.fields_for("BRK-B")["ticker"] == "BRK-B"
    assert index.fields_for("brk.b")["sector"] == "Electronic Technology"


def test_a_missing_document_is_an_empty_index_not_an_error(tmp_path: Path) -> None:
    index = IdentityIndex(
        universe_path=tmp_path / "nope.json",
        snapshot_path=tmp_path / "nope2.json",
    )
    assert index.fields_for("NVDA")["sector"] is None


def test_the_real_documents_load(tmp_path: Path) -> None:
    """The production documents parse and answer for a known symbol."""

    if not UNIVERSE.exists() or not SNAPSHOT.exists():
        pytest.skip("production documents not on this box")
    index = IdentityIndex(universe_path=UNIVERSE, snapshot_path=SNAPSHOT)
    fields = index.fields_for("NVDA")
    assert fields["ticker"] == "NVDA"
    assert fields["sector"], "the universe carries a sector"
    assert fields["industry"], "the snapshot carries a SIC description"
    assert fields["country"] == "United States"
