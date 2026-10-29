"""RED — keyless depth sources on the fast-path benchmark.

The fast path promises sub-5-minute coverage for 4,217 symbols without an API
key. FINRA's consolidated short interest file and the official index
membership files (SSGA / Nasdaq / iShares) are both keyless, and both answer
catalog keys the SEC / CNBC / Yahoo stages cannot:

* ``finra``    -> ``short_interest``, plus ``short_ratio`` when the caller
  supplies the 3-month average-volume reference map (the benchmark supplies
  it from the Yahoo v7 batch), plus ``short_float`` when it supplies the
  float map (the benchmark does not, so that key stays absent);
* ``official`` -> ``index_membership``. ``optionable`` stays off: the Cboe
  HEAD probe 429s at full-universe scale, so the source is built with
  ``optionable=False``.

This suite pins the merge contract the benchmark must implement, with fake
sources and no network:

* a depth observation only ever **fills a missing** catalog field — the fast
  path's own sources stay primary, so coverage can only grow;
* ``None`` values and the four quote-hub fields (``price``, ``change_pct``,
  ``volume``, ``prev_close``) are never written by a depth source;
* symbols are canonicalised (``BRK.B`` -> ``BRK-B``) before the merge, and
  field aliases (``index`` -> ``index_membership``) are normalised;
* the stage reports **which catalog keys each source added**, the wall time
  per source, and per-source errors — a failed source degrades to "adds
  nothing" instead of crashing the benchmark;
* the official source is constructed with ``optionable=False``.

Every test fetches its callables through :func:`_contract`, so a missing
implementation fails with ``AssertionError`` (RED), never ``ImportError``.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.market.depth import DepthObservation

REPO = Path(__file__).resolve().parents[1]
BENCH_PATH = REPO / "scripts" / "fast_path_bench.py"
CATALOG_PATH = REPO / "app" / "engine" / "data" / "finviz_columns_116.json"

#: Catalog keys the two keyless depth sources exist to add.
FINRA_KEYS = {"short_interest", "short_ratio", "short_float"}
OFFICIAL_KEYS = {"index_membership", "optionable"}

#: The quote hub owns these four; a depth source must never publish them.
QUOTE_ONLY = {"price", "change_pct", "volume", "prev_close"}


def _load_bench() -> Any:
    """Import ``scripts/fast_path_bench.py`` without making it a package."""

    spec = importlib.util.spec_from_file_location("fast_path_bench_under_test", BENCH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CLI = _load_bench()


def _contract(name: str) -> Any:
    """Fetch a merge-contract callable; RED until the bench implements it."""

    fn = getattr(CLI, name, None)
    assert fn is not None, f"scripts/fast_path_bench.py does not implement {name} yet"
    return fn


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


def observation(
    symbol: str, fields: dict[str, Any], source: str = "finra"
) -> DepthObservation:
    """A real ``DepthObservation`` — the merge contract's actual input type."""

    return DepthObservation(
        symbol=symbol,
        fields=fields,
        as_of=datetime.now(UTC),
        source=source,
    )


class FakeDepthSource:
    """Scripted depth source: canned observations, optional failure."""

    def __init__(
        self,
        name: str,
        observations: dict[str, DepthObservation] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self.observations = observations or {}
        self.error = error
        self.calls: list[list[str]] = []
        self.average_volume_calls: list[Any] = []

    async def fetch_many(self, symbols: Any) -> dict[str, DepthObservation]:
        self.calls.append(list(symbols))
        if self.error is not None:
            raise self.error
        return self.observations

    def set_average_volume(self, values: Any) -> None:
        self.average_volume_calls.append(values)


def _finra_fixture() -> dict[str, DepthObservation]:
    return {
        "AAPL": observation(
            "AAPL",
            {"short_interest": 120_000_000, "short_ratio": 1.8, "short_float": 0.8},
        ),
        "BRK.B": observation("BRK.B", {"short_interest": 3_000}, source="finra"),
    }


def _official_fixture() -> dict[str, DepthObservation]:
    return {
        "AAPL": observation(
            "AAPL", {"index_membership": "DJIA, NDX, S&P 500"}, source="official"
        ),
        "BRK.B": observation(
            "BRK.B", {"index_membership": "S&P 500"}, source="official"
        ),
    }


# ---------------------------------------------------------------------------
# merge_observation_fields
# ---------------------------------------------------------------------------


def test_merge_fills_missing_fields_only() -> None:
    merge = _contract("merge_observation_fields")
    row: dict[str, Any] = {"pe": 28.5}
    added = merge(row, {"short_interest": 120_000_000, "short_ratio": 1.8})
    assert added == {"short_interest", "short_ratio"}
    assert row["short_interest"] == 120_000_000
    assert row["short_ratio"] == 1.8
    assert row["pe"] == 28.5


def test_merge_never_overwrites_existing_value() -> None:
    merge = _contract("merge_observation_fields")
    row: dict[str, Any] = {"short_interest": 999}
    added = merge(row, {"short_interest": 120_000_000})
    assert added == set()
    assert row["short_interest"] == 999


def test_merge_skips_none_and_quote_only_fields() -> None:
    merge = _contract("merge_observation_fields")
    row: dict[str, Any] = {}
    added = merge(
        row,
        {
            "short_interest": None,
            "short_ratio": 1.8,
            "price": 189.5,
            "change_pct": -1.2,
            "volume": 55_000_000,
            "prev_close": 191.0,
        },
    )
    assert added == {"short_ratio"}
    assert row == {"short_ratio": 1.8}


# ---------------------------------------------------------------------------
# merge_depth_observations
# ---------------------------------------------------------------------------


def test_merge_observations_group_added_keys_by_source() -> None:
    merge_observations = _contract("merge_depth_observations")
    merged: dict[str, dict[str, Any]] = {"AAPL": {}, "BRK-B": {}}
    added = merge_observations(merged, _finra_fixture())
    assert added == {"finra": {"short_interest", "short_ratio", "short_float"}}
    added.update(merge_observations(merged, _official_fixture()))
    assert added["official"] == {"index_membership"}
    assert merged["AAPL"]["short_interest"] == 120_000_000
    assert merged["AAPL"]["index_membership"] == "DJIA, NDX, S&P 500"
    assert merged["BRK-B"]["short_interest"] == 3_000
    assert merged["BRK-B"]["index_membership"] == "S&P 500"


def test_merge_observations_canonicalizes_symbols() -> None:
    merge_observations = _contract("merge_depth_observations")
    merged: dict[str, dict[str, Any]] = {"BRK-B": {}}
    merge_observations(merged, _finra_fixture())
    assert merged["BRK-B"]["short_interest"] == 3_000
    assert "BRK.B" not in merged


def test_merge_observations_creates_row_for_unknown_symbol() -> None:
    merge_observations = _contract("merge_depth_observations")
    merged: dict[str, dict[str, Any]] = {}
    added = merge_observations(merged, _official_fixture())
    assert added == {"official": {"index_membership"}}
    assert merged["AAPL"]["index_membership"] == "DJIA, NDX, S&P 500"


def test_merge_observations_fills_dot_spelled_class_row() -> None:
    """The universe writes ``BRK.B``; the depth layer canonicalises to a dash.

    The class share must not end up split across two rows — the depth
    fields belong on the row the earlier stages already built.
    """
    merge_observations = _contract("merge_depth_observations")
    merged: dict[str, dict[str, Any]] = {"BRK.B": {"price": 500.0}}
    added = merge_observations(
        merged, {"BRK.B": observation("BRK.B", {"short_interest": 3_000})}
    )
    assert added == {"finra": {"short_interest"}}
    assert merged["BRK.B"]["short_interest"] == 3_000
    assert merged["BRK.B"]["price"] == 500.0
    assert "BRK-B" not in merged


def test_merge_observations_empty_adds_nothing() -> None:
    merge_observations = _contract("merge_depth_observations")
    merged: dict[str, dict[str, Any]] = {"AAPL": {"pe": 28.5}}
    added = merge_observations(merged, {})
    assert added == {}
    assert merged == {"AAPL": {"pe": 28.5}}


def test_field_aliases_are_normalized() -> None:
    merge_observations = _contract("merge_depth_observations")
    merged: dict[str, dict[str, Any]] = {"IWM": {}}
    added = merge_observations(
        merged,
        {"IWM": observation("IWM", {"index": "Russell 2000"}, source="official")},
    )
    assert added == {"official": {"index_membership"}}
    assert merged["IWM"]["index_membership"] == "Russell 2000"
    assert "index" not in merged["IWM"]


# ---------------------------------------------------------------------------
# depth_stage
# ---------------------------------------------------------------------------


async def test_depth_stage_merges_both_sources() -> None:
    stage = _contract("depth_stage")
    finra = FakeDepthSource("finra", _finra_fixture())
    official = FakeDepthSource("official", _official_fixture())
    merged: dict[str, dict[str, Any]] = {"AAPL": {"pe": 28.5}, "BRK-B": {"pe": 500.0}}

    added, timings, errors = await stage(
        merged,
        ["AAPL", "BRK.B"],
        finra_source=finra,
        official_source=official,
    )

    assert finra.calls == [["AAPL", "BRK.B"]]
    assert official.calls == [["AAPL", "BRK.B"]]
    assert added["finra"] == FINRA_KEYS
    assert added["official"] == OFFICIAL_KEYS - {"optionable"}
    assert errors == {}
    assert merged["AAPL"]["short_interest"] == 120_000_000
    assert merged["AAPL"]["index_membership"] == "DJIA, NDX, S&P 500"
    assert merged["BRK-B"]["short_interest"] == 3_000
    # The fast path's own fields survive the merge untouched.
    assert merged["AAPL"]["pe"] == 28.5
    assert set(timings) >= {"finra", "official"}
    assert all(isinstance(v, float) and v >= 0.0 for v in timings.values())


async def test_depth_stage_source_failure_degrades() -> None:
    stage = _contract("depth_stage")
    finra = FakeDepthSource("finra", error=RuntimeError("FINRA HTTP 500"))
    official = FakeDepthSource("official", _official_fixture())
    merged: dict[str, dict[str, Any]] = {"AAPL": {"pe": 28.5}}

    added, _timings, errors = await stage(
        merged,
        ["AAPL"],
        finra_source=finra,
        official_source=official,
    )

    assert "finra" not in added
    assert added["official"] == {"index_membership"}
    assert "finra" in errors
    assert "500" in errors["finra"]
    assert "short_interest" not in merged["AAPL"]
    assert merged["AAPL"]["index_membership"] == "DJIA, NDX, S&P 500"


async def test_depth_stage_wires_average_volume_reference() -> None:
    stage = _contract("depth_stage")
    finra = FakeDepthSource("finra", _finra_fixture())
    official = FakeDepthSource("official", _official_fixture())
    volumes = {"AAPL": 66_000_000, "BRK-B": 1_500}

    await stage(
        {},
        ["AAPL", "BRK.B"],
        finra_source=finra,
        official_source=official,
        average_volume=volumes,
    )

    assert finra.average_volume_calls == [volumes]
    assert official.average_volume_calls == []


async def test_depth_stage_without_reference_map_skips_wiring() -> None:
    stage = _contract("depth_stage")
    finra = FakeDepthSource("finra", _finra_fixture())
    await stage({}, ["AAPL"], finra_source=finra, official_source=FakeDepthSource("official"))
    assert finra.average_volume_calls == []


async def test_depth_stage_constructs_official_without_optionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage = _contract("depth_stage")
    built: dict[str, dict[str, Any]] = {}

    class RecordingFinra:
        def __init__(self, **kwargs: Any) -> None:
            built["finra"] = kwargs

        async def fetch_many(self, symbols: Any) -> dict[str, DepthObservation]:
            return {}

    class RecordingOfficial:
        def __init__(self, **kwargs: Any) -> None:
            built["official"] = kwargs

        async def fetch_many(self, symbols: Any) -> dict[str, DepthObservation]:
            return {}

    monkeypatch.setattr(CLI, "FinraShortSource", RecordingFinra, raising=False)
    monkeypatch.setattr(CLI, "OfficialDepthSource", RecordingOfficial, raising=False)

    await stage({}, ["AAPL"])

    assert built["finra"] == {}
    assert built["official"] == {"optionable": False}


# ---------------------------------------------------------------------------
# coverage
# ---------------------------------------------------------------------------


def test_coverage_of_counts_filled_keys() -> None:
    coverage_of = _contract("coverage_of")
    merged = {
        "AAPL": {"pe": 28.5, "short_interest": 120_000_000},
        "MSFT": {"pe": None, "index_membership": "DJIA"},
    }
    report = coverage_of(merged, ["pe", "short_interest", "index_membership", "beta"])
    assert report["coverage"] == "3/4"
    assert report["filled"] == 3
    assert report["total_keys"] == 4
    # per-symbol field counts are [1 (MSFT), 2 (AAPL)]; the median is the upper.
    assert report["median_fields"] == 2


async def test_depth_stage_grows_coverage() -> None:
    stage = _contract("depth_stage")
    coverage_of = _contract("coverage_of")
    keys = ["pe", "short_interest", "short_ratio", "short_float", "index_membership"]
    merged: dict[str, dict[str, Any]] = {"AAPL": {"pe": 28.5}, "MSFT": {"pe": 400.0}}

    before = coverage_of(merged, keys)
    added, _timings, errors = await stage(
        merged,
        ["AAPL", "MSFT"],
        finra_source=FakeDepthSource("finra", _finra_fixture()),
        official_source=FakeDepthSource("official", _official_fixture()),
    )
    after = coverage_of(merged, keys)

    assert errors == {}
    assert before["coverage"] == "1/5"
    assert after["coverage"] == "5/5"
    assert after["filled"] == before["filled"] + 4
    assert after["median_fields"] > before["median_fields"]
    assert added["finra"] == FINRA_KEYS
    assert added["official"] == {"index_membership"}


def test_added_keys_are_catalog_keys() -> None:
    catalog = json.loads(CATALOG_PATH.read_text())
    keys = {c["key"] for c in catalog}
    for key in FINRA_KEYS | OFFICIAL_KEYS:
        assert key in keys, f"{key} is not a finviz_columns_116 catalog key"
