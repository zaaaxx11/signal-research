"""Fase 4 gate 5 — quality reconciles, and a critical failure blocks the export.

Two identities decide whether a quality report is worth reading:

* ``filled + empty == row_count`` — nothing appears or vanishes;
* ``verified + rejected + disputed == filled`` — every filled cell has a fate.

A ``filled`` cell is a cell that held a value when the verifier saw it. A
rejected cell moves to ``rejected`` and is empty in the export; it is not
silently dropped from the denominator, because that is how a broken column
starts reporting a 100% pass rate.

The verdict is the product's only honest summary: ``PASS`` / ``DEGRADED`` /
``REJECT``. A critical rule (V7 unit, V9 parity, V11 currency, V15 band, V16
future ``as_of``) or a failed reconciliation is ``REJECT``.
"""

from __future__ import annotations

import pytest

from app.contracts import CANONICAL_COLUMNS_V1
from tests._phase4 import (
    clean_row,
    column_blocks,
    quality_module,
    reconcile,
    verdict,
)


def _rows() -> list[dict[str, object]]:
    return [
        clean_row(instrument_id="XNAS:A", symbol="A"),
        clean_row(
            instrument_id="XNAS:B",
            symbol="B",
            last_price=None,
            prev_close=None,
            change_pct=None,
            bid=None,
            ask=None,
        ),
        clean_row(
            instrument_id="XNAS:C",
            symbol="C",
            # A rejected cell in a *post-verification* row: the value is gone,
            # but the rejection keeps its place in ``filled``.
            change_pct=None,
        ),
    ]


def _rejections() -> list[dict[str, object]]:
    return [
        {
            "instrument_id": "XNAS:C",
            "column": "change_pct",
            "rule": "V1",
            "reason": "change_pct_mismatch",
        }
    ]


def _blocks() -> dict[str, dict[str, object]]:
    return column_blocks(_rows(), rejections=_rejections())


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_the_column_list_is_injected_because_verify_may_not_import_contracts() -> None:
    blocks = column_blocks(_rows(), columns=["last_price", "change_pct"])
    assert list(blocks) == ["last_price", "change_pct"], (
        "verify/quality must build the report over the columns it is handed; "
        "importing app.contracts would break the verify-pure gate"
    )


def test_one_block_per_canonical_column_in_contract_order() -> None:
    blocks = _blocks()
    assert list(blocks) == list(CANONICAL_COLUMNS_V1), (
        "the report must cover the 92-column contract, in the frozen order"
    )
    assert len(blocks) == 92


def test_each_block_carries_the_documented_counters() -> None:
    block = _blocks()["last_price"]
    assert set(block) >= {
        "filled",
        "verified",
        "rejected",
        "disputed",
        "empty",
        "sources",
        "min_as_of",
        "max_as_of",
    }
    assert all(isinstance(block[key], int) for key in ("filled", "verified", "rejected", "disputed", "empty"))
    assert isinstance(block["sources"], dict)


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def test_filled_cells_are_counted_even_after_a_rejection() -> None:
    block = _blocks()["change_pct"]
    assert (block["filled"], block["verified"], block["rejected"], block["disputed"], block["empty"]) == (2, 1, 1, 0, 1), (
        "a rejected cell keeps its place in filled and moves out of verified"
    )


def test_filled_plus_empty_equals_the_row_count_for_every_column() -> None:
    rows = _rows()
    for column, block in _blocks().items():
        assert block["filled"] + block["empty"] == len(rows), column


def test_verified_plus_rejected_plus_disputed_equals_filled_for_every_column() -> None:
    for column, block in _blocks().items():
        assert block["verified"] + block["rejected"] + block["disputed"] == block["filled"], column


def test_a_disputed_cell_is_counted_separately_from_verified() -> None:
    disputed = [{"instrument_id": "XNAS:A", "column": "last_price"}]
    block = column_blocks(_rows(), rejections=_rejections(), disputed=disputed)["last_price"]
    assert (block["filled"], block["verified"], block["rejected"], block["disputed"], block["empty"]) == (2, 1, 0, 1, 1)


def test_sources_and_timestamps_describe_the_filled_cells() -> None:
    row = clean_row(
        instrument_id="XNAS:PROV",
        _provenance={
            "last_price": {
                "source": "yahoo",
                "as_of": "2026-10-22T14:59:30+00:00",
                "status": "ok",
                "trust": 1,
                "verified": False,
            }
        },
    )
    block = column_blocks([row])["last_price"]
    assert block["sources"].get("yahoo") == 1, block["sources"]
    assert block["min_as_of"] == block["max_as_of"] == "2026-10-22T14:59:30+00:00"


def test_reconcile_accepts_a_consistent_report() -> None:
    assert reconcile(_blocks(), row_count=3) is None


def test_reconcile_rejects_a_filled_count_that_does_not_add_up() -> None:
    blocks = _blocks()
    blocks["last_price"]["filled"] = 4  # 4 + 1 != 3 rows
    with pytest.raises(quality_module().QualityError) as caught:
        reconcile(blocks, row_count=3)
    assert "last_price" in str(caught.value)


def test_reconcile_rejects_a_verified_count_that_does_not_add_up() -> None:
    blocks = _blocks()
    blocks["change_pct"]["verified"] = 5  # 5 + 1 + 0 != 3
    with pytest.raises(quality_module().QualityError) as caught:
        reconcile(blocks, row_count=3)
    assert "change_pct" in str(caught.value)


def test_reconcile_rejects_a_report_that_forgets_a_column() -> None:
    blocks = _blocks()
    blocks.pop("isin")
    with pytest.raises(quality_module().QualityError) as caught:
        reconcile(blocks, row_count=3)
    assert "isin" in str(caught.value)


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def test_verdict_passes_a_clean_report() -> None:
    assert verdict(column_blocks(_rows())) == "PASS"


def test_verdict_degrades_on_a_major_rejection() -> None:
    assert verdict(_blocks(), rejections=_rejections()) == "DEGRADED"


def test_verdict_rejects_on_a_critical_rejection() -> None:
    critical = [{"instrument_id": "XNAS:C", "column": "opt_iv_atm", "rule": "V9", "reason": "parity_violation"}]
    assert verdict(_blocks(), rejections=critical) == "REJECT"
    for rule in ("V7", "V11", "V15", "V16"):
        rejection = [{"instrument_id": "XNAS:C", "column": "quote_as_of", "rule": rule, "reason": "x"}]
        assert verdict(_blocks(), rejections=rejection) == "REJECT", rule
    assert verdict(_blocks(), rejections=[{"instrument_id": "XNAS:C", "column": "opt_iv_atm", "rule": "V7", "reason": "iv_unit"}]) == "REJECT"


def test_verdict_degrades_on_a_disputed_cell() -> None:
    disputed = [{"instrument_id": "XNAS:A", "column": "last_price"}]
    blocks = column_blocks(_rows(), disputed=disputed)
    assert verdict(blocks) == "DEGRADED", (
        "two providers disagreeing is not a pass, even when the trusted value is exported"
    )


def test_verdict_rejects_when_the_report_did_not_reconcile() -> None:
    assert verdict(_blocks(), reconciled=False) == "REJECT", (
        "a job whose quality report does not reconcile must not be Success"
    )
    assert verdict(_blocks(), rejections=_rejections(), reconciled=False) == "REJECT"


def test_verdict_refuses_an_unknown_rule() -> None:
    with pytest.raises(ValueError):
        verdict(_blocks(), rejections=[{"instrument_id": "XNAS:A", "column": "last_price", "rule": "V99", "reason": "x"}])


def test_the_verdict_enum_is_closed() -> None:
    assert tuple(quality_module().VERDICTS) == ("PASS", "DEGRADED", "REJECT")
