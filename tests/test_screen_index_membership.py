"""Index membership: a vendored snapshot, four labels, two ticker spellings.

Why a file and not a scan column
--------------------------------
No provider column carries index membership: ``idx`` was one of the 32 refused
Finviz keys because a market-cap threshold is not a membership test. The depth
store does carry a comma string per symbol (``official.py`` derives it from
SSGA, Nasdaq and IWM holdings), but it only covers the symbols that store
holds — a membership filter must not answer "not in the S&P 500" for a company
nobody collected. So the four lists are **vendored** as one stamped snapshot,
written by ``scripts/index-membership.py``, and the runtime only reads it.

The contract:

* the snapshot is stamped (``as_of`` ISO, not in the future) and its source is
  named — an undated list is not evidence of today's membership;
* the counts are plausible for the four indices, so a truncated fetch fails the
  build instead of shipping a 12-name S&P 500;
* tickers resolve in both spellings the product writes (``BRK.B`` from Finviz,
  ``BRK-B`` from several providers);
* a missing snapshot makes ``idx`` **unavailable with a reason**, never an empty
  page: "unknown membership" and "member of nothing" are different answers, and
  only one of them is a fact.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest

from app.screen import buckets, index_membership
from app.screen.buckets import Context, UnavailableBucket


def test_the_vendored_snapshot_is_plausible_and_stamped() -> None:
    snapshot = index_membership.load()
    assert snapshot.available, "the vendored snapshot must load from a checkout"
    counts = snapshot.counts
    assert 480 <= counts["sp500"] <= 520, counts
    assert 95 <= counts["ndx"] <= 110, counts
    assert counts["dji"] == 30, counts
    assert 1700 <= counts["rut"] <= 2200, counts
    assert snapshot.as_of, "an undated membership list is not evidence"
    stamped = date.fromisoformat(snapshot.as_of)
    assert stamped <= date.today(), "a future stamp is not a snapshot"
    assert snapshot.source, "the snapshot must name where each list came from"
    for index in index_membership.INDICES:
        assert index in snapshot.source, f"{index} needs a source line"
        assert snapshot.source[index].get("url") or snapshot.source[index].get("path")


def test_both_ticker_spellings_resolve() -> None:
    assert "sp500" in index_membership.indices_for("BRK.B")
    assert index_membership.indices_for("BRK.B") == index_membership.indices_for("BRK-B")
    assert index_membership.indices_for("brk.b") == ("sp500",)


def test_indices_for_is_panel_order() -> None:
    everything = index_membership.indices_for("AAPL")
    assert everything, "AAPL is in at least one of the four"
    assert list(everything) == [name for name in index_membership.INDICES if name in everything]


def test_enrich_rows_copies_membership_and_never_invents_a_list() -> None:
    rows = [{"symbol": "AAPL"}, {"symbol": "NOTATICKER"}]
    enriched = index_membership.enrich_rows(rows)
    assert enriched[0]["index_members"], "a member gets its labels"
    assert isinstance(enriched[0]["index_members"], list)
    assert enriched[1]["index_members"] == [], "present snapshot: in none of the four"
    assert rows[0] == {"symbol": "AAPL"}, "the input rows are not mutated"


def test_a_missing_snapshot_makes_idx_unavailable_not_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """No file → the control is disabled with a reason, and a filter is a 422."""

    monkeypatch.setattr(
        index_membership, "load", lambda path=None: index_membership.Snapshot()
    )
    enriched = index_membership.enrich_rows([{"symbol": "AAPL"}])
    assert enriched[0]["index_members"] is None, "unknown membership is not []"

    family = buckets.for_key("idx")
    assert len(family) == 4
    for item in family:
        assert item.available is False, item.id
        assert item.reason and "snapshot" in item.reason, item.id
    with pytest.raises(UnavailableBucket) as caught:
        buckets.bucket("idx:sp500")
    assert caught.value.code == "unavailable_bucket"
    assert "snapshot" in str(caught.value)


def test_the_four_index_buckets_are_membership_tests() -> None:
    family = {item.finviz: item for item in buckets.for_key("idx")}
    assert set(family) == {"sp500", "ndx", "dji", "rut"}
    assert [item.id for item in buckets.for_key("idx")] == [
        "idx:sp500",
        "idx:ndx",
        "idx:dji",
        "idx:rut",
    ]
    context = Context.at("2026-11-02T02:30:00+00:00")
    sp500 = buckets.bucket("idx:sp500")
    assert sp500.predicate({"index_members": ["sp500", "ndx"]}, context) is True
    assert sp500.predicate({"index_members": ["ndx"]}, context) is False
    assert sp500.predicate({"index_members": []}, context) is False
    assert sp500.predicate({"index_members": None}, context) is False
    assert sp500.predicate({}, context) is False
    assert sp500.describe.strip()


def test_the_snapshot_is_the_only_membership_reader() -> None:
    """A malformed file is a missing snapshot, not a half-loaded one."""

    snapshot = index_membership.parse({"as_of": "not-a-date", "indices": {"sp500": ["A"]}})
    assert snapshot.available is False
    assert snapshot.counts == {}
    snapshot = index_membership.parse({"indices": {"sp500": ["A"]}})
    assert snapshot.available is False
    snapshot = index_membership.parse({"as_of": "2026-11-01", "indices": {}})
    assert snapshot.available is False, "an empty snapshot is not a membership list"


def test_members_refuses_to_answer_from_an_absent_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        index_membership, "load", lambda path=None: index_membership.Snapshot()
    )
    with pytest.raises(index_membership.SnapshotMissing):
        index_membership.members("sp500")


def test_the_loader_reads_a_file_it_is_given(tmp_path: Any) -> None:
    path = tmp_path / "membership.json"
    path.write_text(
        '{"as_of": "2026-11-01", "source": {"sp500": {"url": "test"}},'
        ' "indices": {"sp500": ["aapl", "BRK.B"]}}',
        encoding="utf-8",
    )
    snapshot = index_membership.load(path)
    assert snapshot.available
    assert snapshot.counts == {"sp500": 2, "ndx": 0, "dji": 0, "rut": 0}
    assert snapshot.indices_for("BRK-B") == ("sp500",)
    assert snapshot.indices_for("AAPL") == ("sp500",)
    assert snapshot.indices_for("MSFT") == ()


def test_a_snapshot_from_the_future_is_refused(tmp_path: Any) -> None:
    path = tmp_path / "membership.json"
    path.write_text(
        '{"as_of": "2999-01-01", "indices": {"sp500": ["AAPL"]}}', encoding="utf-8"
    )
    assert index_membership.load(path).available is False
