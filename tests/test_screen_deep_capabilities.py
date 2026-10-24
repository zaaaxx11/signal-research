"""Screen deep P9 — four capabilities per top20 symbol.

Gate this file pins (PLAN-screen.md §4/§6 and §8 #6; PLAN-market-enrichment-
engine.md P2–P5):

* the default capability set is ``QUOTE + EOD_BARS + FUNDAMENTALS + OPTIONS``,
  in that order, on the engine, on its factory and on
  ``Runtime.screen_deep_engine``;
* one ladder walk per capability per symbol — twenty symbols cost eighty
  ``Ladder.fetch`` calls (PLAN-screen §6: "deep top20: 20 simbol × 4 capability
  = ~80 request"). The ladder's L0 cache may answer one of those calls without a
  transport request, so eighty is a ceiling on *walks* and an upper bound on
  requests, not a promise of eighty sockets;
* the quote is the row's existence: a symbol without a served quote produces no
  row at all;
* EOD, fundamentals and options fail in isolation: their cells stay ``None``
  while the quote row — and every other capability's cells — survives;
* one merged observation stream fills the row, so ``last_price``, ``ret_1m``,
  ``market_cap`` and ``opt_iv_atm`` land in the same 92-column row.

No adapter and no HTTP client is constructed here: the ladder is a fake and the
suite-wide egress guard (``tests/conftest.py``) fails the test if anything tries
to open a socket.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping, Sequence

import pytest

from app.contracts import CANONICAL_COLUMNS_V1, Capability
from app.providers.base import Verdict
from app.providers.ladder import LadderResult
from app.runtime import Runtime
from app.screen.deep import (
    DEFAULT_CAPABILITIES,
    ScreenDeepEngine,
    build_screen_deep_engine,
)

STAMP = datetime(2026, 10, 22, 20, 0, tzinfo=UTC)
STAMP_TEXT = STAMP.isoformat(timespec="seconds")
ALL_COLUMNS = frozenset(CANONICAL_COLUMNS_V1)
FOUR = (
    Capability.QUOTE,
    Capability.EOD_BARS,
    Capability.FUNDAMENTALS,
    Capability.OPTIONS,
)

#: One cell set per capability. The four groups are disjoint on purpose except
#: for the quote's timestamp, so a row that carries all four proves the merge
#: rather than one capability drowning the others.
CELLS: dict[Capability, tuple[tuple[str, float | None, str | None], ...]] = {
    Capability.QUOTE: (
        ("last_price", 101.5, None),
        ("prev_close", 100.0, None),
        ("day_volume", 1_000_000.0, None),
        ("quote_as_of", None, STAMP_TEXT),
    ),
    Capability.EOD_BARS: (
        ("bars_count", 1_250.0, None),
        ("ma_20", 99.5, None),
        ("rsi_14", 55.2, None),
        ("ret_1m", 4.2, None),
        ("last_bar_date", None, "2026-10-22"),
    ),
    Capability.FUNDAMENTALS: (
        ("market_cap", 3_200_000_000_000.0, None),
        ("pe_ttm", 31.4, None),
        ("roe", 42.7, None),
    ),
    Capability.OPTIONS: (
        ("opt_dte", 30.0, None),
        ("opt_strike_atm", 100.0, None),
        ("opt_iv_atm", 0.2813, None),
        ("opt_expiry", None, "2026-11-20"),
    ),
}


# ---------------------------------------------------------------------------
# Fakes: one ladder, four capabilities, zero sockets
# ---------------------------------------------------------------------------


def _observation(
    capability: Capability,
    instrument_id: str,
    field: str,
    value_num: float | None,
    value_text: str | None,
) -> dict[str, Any]:
    """The observation mapping shape ``Ladder.fetch`` returns."""

    return {
        "instrument_id": instrument_id,
        "field": field,
        "value_num": value_num,
        "value_text": value_text,
        "unit": "ratio",
        "source": f"fake-{capability.value}",
        "source_id": None,
        "as_of": STAMP_TEXT,
        "fetched_at": STAMP_TEXT,
        "confidence": 1.0,
        "verdict": "ok",
        "trust": 3,
        "raw_sha256": None,
        "job_id": None,
    }


@dataclass(frozen=True)
class Fetch:
    """One recorded ladder walk."""

    capability: Capability
    instrument_id: str
    symbol: str
    fields: frozenset[str]


class FakeLadder:
    """Records every walk and answers all four capabilities, unless told to fail.

    ``failing`` fails a capability for every symbol; ``failing_symbols`` fails a
    capability for one symbol only, which is how the isolation tests prove that
    a dead options chain does not take the other symbols down with it.
    """

    def __init__(
        self,
        *,
        failing: Iterable[Capability] = (),
        failing_symbols: Mapping[str, Iterable[Capability]] | None = None,
    ) -> None:
        self.calls: list[Fetch] = []
        self._failing = frozenset(failing)
        self._failing_symbols = {
            symbol.upper(): frozenset(capabilities)
            for symbol, capabilities in (failing_symbols or {}).items()
        }

    async def fetch(
        self, capability: Capability, instrument: Any, fields: set[str]
    ) -> LadderResult:
        self.calls.append(
            Fetch(
                capability=capability,
                instrument_id=instrument.instrument_id,
                symbol=instrument.symbol,
                fields=frozenset(fields),
            )
        )
        failed = capability in self._failing or capability in self._failing_symbols.get(
            instrument.symbol, frozenset()
        )
        if failed:
            return LadderResult(
                observations=[],
                rung=None,
                verdict=Verdict(False, "5xx", f"fake {capability.value} failure"),
                attempts=[],
            )
        return LadderResult(
            observations=[
                _observation(capability, instrument.instrument_id, field, number, text)
                for field, number, text in CELLS[capability]
            ],
            rung=f"fake-{capability.value}",
            verdict=Verdict(True, "ok"),
            attempts=[],
        )


class LadderFactory:
    """Counts constructions and keeps the ladder so the test can read its log."""

    def __init__(self, **ladder_kwargs: Any) -> None:
        self._ladder_kwargs = ladder_kwargs
        self.ladders: list[FakeLadder] = []
        self.kwargs: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> FakeLadder:
        self.kwargs.append(dict(kwargs))
        ladder = FakeLadder(**self._ladder_kwargs)
        self.ladders.append(ladder)
        return ladder

    @property
    def ladder(self) -> FakeLadder:
        assert len(self.ladders) == 1, (
            "the engine must build exactly one ladder per call; it built "
            f"{len(self.ladders)}"
        )
        return self.ladders[0]


def _symbols(count: int) -> list[str]:
    return [f"SYM{index:02d}" for index in range(1, count + 1)]


# ---------------------------------------------------------------------------
# The default: all four capabilities, everywhere
# ---------------------------------------------------------------------------


def test_default_capabilities_are_all_four_in_plan_order() -> None:
    assert DEFAULT_CAPABILITIES == FOUR

    engine = ScreenDeepEngine(LadderFactory())
    assert engine.capabilities == FOUR, (
        "the deep engine's default is quote + EOD + fundamentals + options; got "
        f"{engine.capabilities}"
    )

    built = build_screen_deep_engine(LadderFactory())
    assert built.capabilities == FOUR, (
        "the runtime factory must hand out the four-capability engine, not a "
        f"quote-only one; got {built.capabilities}"
    )


def test_runtime_screen_deep_engine_uses_the_four_capability_default(
    tmp_path: Any,
) -> None:
    from app.config import Settings

    runtime = Runtime(
        Settings(db_path=tmp_path / "mee.db", export_dir=tmp_path / "exports")
    )
    assert runtime.screen_deep_engine.capabilities == FOUR, (
        "Runtime.screen_deep_engine is what the export route injects; it must "
        "walk all four capabilities, got "
        f"{runtime.screen_deep_engine.capabilities}"
    )


def test_a_capability_set_without_quote_is_refused() -> None:
    with pytest.raises(ValueError, match="QUOTE"):
        ScreenDeepEngine(
            LadderFactory(),
            capabilities=(Capability.EOD_BARS, Capability.FUNDAMENTALS),
        )


def test_capabilities_are_deduplicated_coerced_and_quote_first() -> None:
    engine = ScreenDeepEngine(
        LadderFactory(),
        capabilities=(
            "eod_bars",
            Capability.QUOTE,
            Capability.EOD_BARS,
            "options",
        ),
    )
    assert engine.capabilities == (
        Capability.QUOTE,
        Capability.EOD_BARS,
        Capability.OPTIONS,
    ), (
        "the walk order is the caller's order with the quote hoisted first; no "
        f"duplicates, no free-form strings; got {engine.capabilities}"
    )


# ---------------------------------------------------------------------------
# One walk per capability per symbol
# ---------------------------------------------------------------------------


async def test_one_fake_ladder_records_four_calls_per_symbol() -> None:
    factory = LadderFactory()
    rows = await ScreenDeepEngine(factory)(["AAPL"])

    ladder = factory.ladder  # asserts exactly one ladder was built
    assert [call.capability for call in ladder.calls] == list(FOUR), (
        "one symbol is four walks, in plan order; got "
        f"{[call.capability.value for call in ladder.calls]}"
    )
    assert {call.symbol for call in ladder.calls} == {"AAPL"}
    assert {call.instrument_id for call in ladder.calls} == {"XNAS:AAPL"}
    assert len(rows) == 1


async def test_every_walk_is_offered_the_canonical_field_set() -> None:
    factory = LadderFactory()
    await ScreenDeepEngine(factory)(["AAPL"])

    for call in factory.ladder.calls:
        assert call.fields == ALL_COLUMNS, (
            f"{call.capability.value}: the walk must ask for the 92-column "
            f"vocabulary, got {len(call.fields)} fields"
        )


async def test_twenty_symbols_cost_four_walks_each() -> None:
    """20 × 4 = 80 ladder walks (PLAN-screen.md §6).

    The number recorded here is ``Ladder.fetch`` calls. A rung the ladder's L0
    cache can serve answers with zero requests, so the transport request count
    may be lower; the walk count is the engine's own contract and the honest
    ceiling for a cold cache.
    """

    factory = LadderFactory()
    symbols = _symbols(20)
    rows = await ScreenDeepEngine(factory)(symbols)

    ladder = factory.ladder
    assert len(rows) == 20
    assert len(ladder.calls) == 80, (
        f"20 symbols x 4 capabilities = 80 walks; the ladder saw {len(ladder.calls)}"
    )
    per_symbol = {
        symbol: sum(1 for call in ladder.calls if call.symbol == symbol)
        for symbol in symbols
    }
    assert set(per_symbol.values()) == {4}, (
        f"every symbol is walked once per capability; got {per_symbol}"
    )
    per_capability = {
        capability.value: sum(1 for call in ladder.calls if call.capability is capability)
        for capability in FOUR
    }
    assert per_capability == {capability.value: 20 for capability in FOUR}, (
        f"every capability runs for all 20 symbols; got {per_capability}"
    )


async def test_the_ladder_is_asked_for_the_data_time_when_one_is_pinned() -> None:
    factory = LadderFactory()
    engine = ScreenDeepEngine(factory, as_of=STAMP)
    await engine(["AAPL"])

    assert factory.kwargs == [{"as_of": STAMP}], (
        "a pinned as_of is what the ladder's cache key buckets, so it must reach "
        f"the factory; got {factory.kwargs}"
    )


# ---------------------------------------------------------------------------
# The merged row
# ---------------------------------------------------------------------------


async def test_one_row_carries_cells_from_all_four_capabilities() -> None:
    rows = await ScreenDeepEngine(LadderFactory())(["AAPL"])

    assert len(rows) == 1
    row = rows[0]
    assert tuple(row) == tuple(CANONICAL_COLUMNS_V1), (
        "the deep row must still be exactly the 92 canonical columns; got "
        f"{len(row)} keys"
    )

    assert row["last_price"] == pytest.approx(101.5)
    assert row["ret_1m"] == pytest.approx(4.2)
    assert row["market_cap"] == pytest.approx(3_200_000_000_000.0)
    assert row["opt_iv_atm"] == pytest.approx(0.2813), (
        "the options cell never reached the row: opt_iv_atm is "
        f"{row['opt_iv_atm']!r}"
    )

    # The other cells of the same capabilities, so the merge is not a lucky one.
    for column, expected in (
        ("prev_close", 100.0),
        ("ma_20", 99.5),
        ("rsi_14", 55.2),
        ("pe_ttm", 31.4),
        ("roe", 42.7),
        ("opt_dte", 30.0),
        ("opt_expiry", "2026-11-20"),
        ("last_bar_date", "2026-10-22"),
    ):
        assert row[column] == expected, f"{column}: {row[column]!r} != {expected!r}"

    assert row["quote_as_of"] == STAMP_TEXT
    assert row["as_of_achieved"] == STAMP_TEXT, (
        "the quote's own timestamp dates the row; got "
        f"{row['as_of_achieved']!r}"
    )
    assert row["primary_source"] == "fake-quote", (
        "the quote rung is the row's primary source, not whichever capability "
        f"answered last; got {row['primary_source']!r}"
    )
    assert row["data_quality"] == "partial", (
        "a quote plus deeper blocks is partial coverage, not complete; got "
        f"{row['data_quality']!r}"
    )
    assert row["filled_columns_count"] > 4


# ---------------------------------------------------------------------------
# Failures: quote is required, the other three are not
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "capability",
    [Capability.EOD_BARS, Capability.FUNDAMENTALS, Capability.OPTIONS],
)
async def test_a_dead_capability_keeps_the_quote_row(capability: Capability) -> None:
    rows = await ScreenDeepEngine(LadderFactory(failing=(capability,)))(["AAPL"])

    assert len(rows) == 1, (
        f"a failed {capability.value} walk must not discard the quote row; got "
        f"{rows}"
    )
    row = rows[0]
    assert row["last_price"] == pytest.approx(101.5)
    assert row["quote_as_of"] == STAMP_TEXT
    for column, _number, _text in CELLS[capability]:
        assert row[column] is None, (
            f"{capability.value} failed, so {column} must stay None (never 0 or "
            f"a guess); got {row[column]!r}"
        )
    # The other two deeper capabilities still landed.
    survivors = [item for item in FOUR if item is not Capability.QUOTE and item is not capability]
    for other in survivors:
        column = CELLS[other][0][0]
        assert row[column] is not None, (
            f"{capability.value} failed and took {other.value} down with it: "
            f"{column} is empty"
        )


async def test_options_failure_keeps_the_quote_row() -> None:
    rows = await ScreenDeepEngine(LadderFactory(failing=(Capability.OPTIONS,)))(["AAPL"])

    assert len(rows) == 1
    row = rows[0]
    assert row["last_price"] == pytest.approx(101.5)
    assert row["ret_1m"] == pytest.approx(4.2)
    assert row["market_cap"] == pytest.approx(3_200_000_000_000.0)
    assert row["opt_iv_atm"] is None
    assert row["opt_strike_atm"] is None


async def test_a_quote_failure_drops_only_that_symbol_and_stops_its_walk() -> None:
    factory = LadderFactory(failing_symbols={"MSFT": (Capability.QUOTE,)})
    rows = await ScreenDeepEngine(factory)(["AAPL", "MSFT"])

    assert [row["symbol"] for row in rows] == ["AAPL"], (
        "a row without a quote is not a row: MSFT must be absent, AAPL present; "
        f"got {[row['symbol'] for row in rows]}"
    )
    msft_symbols = [call for call in factory.ladder.calls if call.symbol == "MSFT"]
    assert [call.capability for call in msft_symbols] == [Capability.QUOTE], (
        "a failed quote is one walk, not four: the deeper walks are not spent on "
        f"a symbol that cannot be exported; got {[call.capability.value for call in msft_symbols]}"
    )
    aapl_symbols = [call for call in factory.ladder.calls if call.symbol == "AAPL"]
    assert len(aapl_symbols) == 4, "the surviving symbol still pays all four walks"


async def test_an_empty_observation_list_is_a_failure_not_a_row() -> None:
    """A rung that says ``ok`` with no cells served nothing (defensive)."""

    class EmptyLadder(FakeLadder):
        async def fetch(self, capability: Capability, instrument: Any, fields: set[str]) -> LadderResult:
            result = await super().fetch(capability, instrument, fields)
            if capability is Capability.QUOTE:
                result.observations = []
            return result

    class EmptyFactory(LadderFactory):
        def __call__(self, **kwargs: Any) -> FakeLadder:
            self.kwargs.append(dict(kwargs))
            ladder = EmptyLadder()
            self.ladders.append(ladder)
            return ladder

    rows = await ScreenDeepEngine(EmptyFactory())(["AAPL"])
    assert rows == [], "an empty quote answer is an empty result, not a row of nulls"


async def test_missing_cells_stay_none_and_are_not_zeroed() -> None:
    rows = await ScreenDeepEngine(LadderFactory(failing=(Capability.EOD_BARS,)))(["AAPL"])
    row = rows[0]

    for column in ("ret_1m", "ma_20", "rsi_14", "bars_count"):
        assert row[column] is None, (
            f"{column} was never served, so it must be None, not 0; got "
            f"{row[column]!r}"
        )


async def test_us_class_share_symbol_uses_yahoo_hyphen_and_keeps_screen_spelling() -> None:
    """BRK.B is Yahoo's BRK-B; the exported row keeps the screen's BRK.B."""

    factory = LadderFactory()
    rows = await ScreenDeepEngine(factory)(["BRK.B"])

    assert [call.symbol for call in factory.ladder.calls[:4]] == [
        "BRK-B",
        "BRK-B",
        "BRK-B",
        "BRK-B",
    ], "the provider must see BRK-B, not a non-existent BRK.B chart"
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BRK.B", "the screen/export join key must stay BRK.B"
