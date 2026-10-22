"""Fase 3 gates 2 and 3 — fill-only-empty inside a phase, trust ordering across phases.

Two rules, deliberately tested apart:

* **Fill-only-empty** answers "the cell already has a value; do not burn a second
  provider or a second phase overwriting it". It is order-sensitive by design.
* **Trust ordering** answers "a vendor correction must win even when it arrives
  last". That is the ``test_trust_beats_arrival_order`` case, and it is the one
  that fails if conflation is implemented as ``dict.update``.

The observation fixtures use trust 1 official / 2 vendor / 3 aggregator / 4
scrape (02 §4.6).
"""

from __future__ import annotations

import pytest

from tests._phase3 import (
    PROVENANCE_FIELDS,
    PROVENANCE_STATUSES,
    blank,
    conflate_module,
    empty_row,
    expected_filled_cells,
    observation,
    provenance_module,
)

ROW_ID = "XNAS:AAPL"


def test_fill_only_empty_keeps_the_first_value_and_its_source() -> None:
    conflate = conflate_module()
    row = empty_row(instrument_id=ROW_ID, symbol="AAPL")

    first = observation("pe_ttm", 20.0, source="L1", trust=1, instrument_id=ROW_ID)
    second = observation("pe_ttm", 99.0, source="L2", trust=1, instrument_id=ROW_ID)

    assert conflate.write_if_empty(row, "pe_ttm", 20.0, first) is True
    assert conflate.write_if_empty(row, "pe_ttm", 99.0, second) is False

    assert row["pe_ttm"] == 20.0, "a filled cell must not be overwritten by a later phase"
    assert provenance_module().provenance(row)["pe_ttm"]["source"] == "L1"


def test_blank_values_never_fill_a_cell() -> None:
    conflate = conflate_module()
    row = empty_row(instrument_id=ROW_ID, symbol="AAPL")

    for value in (None, "", "   "):
        obs = observation("pe_ttm", value, source="L1", trust=1, instrument_id=ROW_ID)
        assert conflate.write_if_empty(row, "pe_ttm", value, obs) is False

    assert row["pe_ttm"] is None
    assert "pe_ttm" not in provenance_module().provenance(row), (
        "an empty observation must not create provenance: it observed nothing"
    )


def test_higher_trust_replaces_a_lower_trust_value() -> None:
    conflate = conflate_module()
    row = empty_row(instrument_id=ROW_ID, symbol="AAPL")

    aggregator = observation("pe_ttm", 99.0, source="aggregator", trust=3, instrument_id=ROW_ID)
    vendor = observation("pe_ttm", 20.0, source="vendor", trust=2, instrument_id=ROW_ID)

    assert conflate.write_if_empty(row, "pe_ttm", 99.0, aggregator) is True
    assert conflate.write_if_empty(row, "pe_ttm", 20.0, vendor) is True

    assert row["pe_ttm"] == 20.0
    entry = provenance_module().provenance(row)["pe_ttm"]
    assert entry["source"] == "vendor"
    assert entry["trust"] == 2


@pytest.mark.parametrize(
    "arrivals",
    [
        (("vendor", 2, 20.0), ("aggregator", 3, 99.0)),
        (("aggregator", 3, 99.0), ("vendor", 2, 20.0)),
    ],
    ids=["vendor-first", "aggregator-first"],
)
def test_trust_beats_arrival_order(arrivals) -> None:
    conflate = conflate_module()
    row = empty_row(instrument_id=ROW_ID, symbol="AAPL")

    for source, trust, value in arrivals:
        obs = observation("pe_ttm", value, source=source, trust=trust, instrument_id=ROW_ID)
        conflate.write_if_empty(row, "pe_ttm", value, obs)

    assert row["pe_ttm"] == 20.0, (
        "the vendor value must win in both arrival orders; anything else means the "
        "last writer won, not the most trustworthy one"
    )
    assert provenance_module().provenance(row)["pe_ttm"]["source"] == "vendor"


def test_equal_trust_does_not_overwrite() -> None:
    conflate = conflate_module()
    row = empty_row(instrument_id=ROW_ID, symbol="AAPL")

    first = observation("pe_ttm", 20.0, source="vendor_a", trust=2, instrument_id=ROW_ID)
    second = observation("pe_ttm", 99.0, source="vendor_b", trust=2, instrument_id=ROW_ID)

    assert conflate.write_if_empty(row, "pe_ttm", 20.0, first) is True
    assert conflate.write_if_empty(row, "pe_ttm", 99.0, second) is False
    assert row["pe_ttm"] == 20.0


def test_worse_trust_never_overwrites_better_trust() -> None:
    conflate = conflate_module()
    row = empty_row(instrument_id=ROW_ID, symbol="AAPL")

    official = observation("pe_ttm", 20.0, source="official", trust=1, instrument_id=ROW_ID)
    scrape = observation("pe_ttm", 99.0, source="scrape", trust=4, instrument_id=ROW_ID)

    assert conflate.write_if_empty(row, "pe_ttm", 20.0, official) is True
    assert conflate.write_if_empty(row, "pe_ttm", 99.0, scrape) is False
    assert row["pe_ttm"] == 20.0


def test_verified_value_replaces_unverified_at_equal_trust() -> None:
    conflate = conflate_module()
    row = empty_row(instrument_id=ROW_ID, symbol="AAPL")

    guess = observation("pe_ttm", 99.0, source="vendor", trust=2, instrument_id=ROW_ID)
    checked = observation(
        "pe_ttm", 20.0, source="vendor+verifier", trust=2, verified=True, instrument_id=ROW_ID
    )

    assert conflate.write_if_empty(row, "pe_ttm", 99.0, guess) is True
    assert conflate.write_if_empty(row, "pe_ttm", 20.0, checked) is True, (
        "02 §3.5: a verified observation beats an unverified one at equal trust"
    )
    assert row["pe_ttm"] == 20.0
    assert provenance_module().provenance(row)["pe_ttm"]["verified"] is True


def test_non_canonical_column_is_rejected() -> None:
    conflate = conflate_module()
    row = empty_row(instrument_id=ROW_ID, symbol="AAPL")
    obs = observation("pe_ttm", 20.0, instrument_id=ROW_ID)

    with pytest.raises(ValueError):
        conflate.write_if_empty(row, "not_a_column", 1.0, obs)
    assert "not_a_column" not in row, "a rejected write must not leave a 93rd key"


def test_merge_records_provenance_for_every_written_cell() -> None:
    conflate = conflate_module()
    provenance = provenance_module()
    row = empty_row(instrument_id=ROW_ID, symbol="AAPL", mic="XNAS")

    observations = [
        observation("pe_ttm", 99.0, source="aggregator", trust=3, instrument_id=ROW_ID),
        observation("pe_ttm", 20.0, source="vendor", trust=2, instrument_id=ROW_ID),
        observation("eps_ttm", 6.5, source="vendor", trust=2, instrument_id=ROW_ID),
        observation("eps_ttm", None, source="vendor", trust=2, instrument_id=ROW_ID),
    ]
    merged = conflate.merge(row, observations)
    assert merged is row, "merge must conflate into the row it was given"

    record = provenance.provenance(row)
    assert set(record) == {"pe_ttm", "eps_ttm"}
    assert row["pe_ttm"] == 20.0
    assert row["eps_ttm"] == 6.5
    assert set(record) <= expected_filled_cells(row)
    for column, entry in record.items():
        assert set(entry) >= PROVENANCE_FIELDS, column
        assert entry["status"] in PROVENANCE_STATUSES, column
        assert not blank(entry["source"]), column
