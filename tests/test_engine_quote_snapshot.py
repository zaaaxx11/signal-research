from __future__ import annotations

from typing import Any

import pytest

from app.engine.model import Cell, CellStatus, Provenance, Unit
from app.engine.quotes import QuoteSnapshot
from app.engine.screener import ScreenResult
from app.engine.service import EngineService


def test_quote_snapshot_document_is_exact_and_source_free() -> None:
    snapshot = QuoteSnapshot(
        refreshed=True,
        scope="page",
        requested=2,
        returned=2,
        as_of="2026-10-26T14:30:00+00:00",
        stale=False,
        reason=None,
        filter_used_snapshot=False,
    )

    assert snapshot.document() == {
        "refreshed": True,
        "scope": "page",
        "requested": 2,
        "returned": 2,
        "as_of": "2026-10-26T14:30:00+00:00",
        "stale": False,
        "reason": None,
        "filter_used_snapshot": False,
    }
    assert "source" not in snapshot.document()


def test_quote_snapshot_rejects_a_refresh_without_a_scope() -> None:
    with pytest.raises(ValueError, match="scope"):
        QuoteSnapshot(refreshed=True, scope="none")


def test_quote_snapshot_rejects_more_returned_than_requested() -> None:
    with pytest.raises(ValueError, match="returned"):
        QuoteSnapshot(refreshed=True, scope="page", requested=1, returned=2)


def test_screen_document_carries_the_sidecar_when_given_one() -> None:
    cell: Any = Cell(
        value=1.0,
        unit=Unit.USD,
        provenance=Provenance(source="sec", as_of="2026-10-26"),
    )
    result = ScreenResult(
        rows=({"ticker": cell},),
        total=1,
        returned=1,
        limit=20,
        offset=0,
        applied_filters=(),
    )
    snapshot = QuoteSnapshot(refreshed=False, reason="refresh_disabled")

    document = EngineService.screen_document(result, quote_snapshot=snapshot)

    assert document["quote_snapshot"]["refreshed"] is False
    assert document["quote_snapshot"]["reason"] == "refresh_disabled"


def test_screen_document_omits_the_sidecar_when_not_given_one() -> None:
    result = ScreenResult(
        rows=(),
        total=0,
        returned=0,
        limit=20,
        offset=0,
        applied_filters=(),
    )

    assert "quote_snapshot" not in EngineService.screen_document(result)
