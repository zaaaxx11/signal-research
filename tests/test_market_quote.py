"""P2 realtime quotes: ``QuoteTick`` derivation, staleness, failover, dedupe, velocity.

Every test drives a fake clock and injected source callables, so a "five second
old quote" is a fact about the code and not about how busy the test machine was.
The gates from PLAN §5/P2 that are asserted here:

* a quote is ``last``/``prev_close``/``change``/``change_pct``/``volume`` plus
  both timestamps and the winning source;
* a missing ``prev_close`` is ``None`` — never ``change = 0``;
* a quote older than 5 s is flagged, and a cached quote is re-aged on read so
  stale data is never served as fresh;
* a broken or stale primary fails over to the next source;
* identical payloads are deduped and cannot reset velocity;
* ``velocity`` is signed (positive when price rises, negative when it falls).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

import pytest

from app.market.quote import (
    STALE_AFTER_S,
    Attempt,
    Direction,
    QuoteEngine,
    QuoteSource,
    QuoteTick,
    SourceOutcome,
    SourceQuote,
)

T0 = datetime(2026, 10, 23, 14, 30, 0, tzinfo=UTC)


class FakeClock:
    """A clock the test moves by hand; aware UTC, like production."""

    def __init__(self, moment: datetime = T0) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, seconds: float) -> datetime:
        self.moment += timedelta(seconds=seconds)
        return self.moment


def _source(name: str, payload: Any) -> QuoteSource:
    """A source returning a fixed payload; an exception payload is raised."""

    def fetch(symbol: str) -> SourceQuote | None:
        if isinstance(payload, BaseException):
            raise payload
        if callable(payload):
            return payload(symbol)
        return payload

    return QuoteSource(name, fetch)


def _quote(
    last: float = 101.0,
    prev_close: float | None = 100.0,
    volume: float | None = 1_500.0,
    *,
    at: datetime = T0,
) -> SourceQuote:
    return SourceQuote(last=last, prev_close=prev_close, volume=volume, ts_exchange=at)


def _engine(
    sources: list[QuoteSource],
    symbols: tuple[str, ...] = ("AAPL",),
    clock: Callable[[], datetime] | None = None,
    **kwargs: Any,
) -> QuoteEngine:
    return QuoteEngine(sources, symbols, clock=clock or FakeClock(), **kwargs)


# ---------------------------------------------------------------------------
# Tick shape and derived fields
# ---------------------------------------------------------------------------


def test_tick_carries_prices_provenance_and_derived_change() -> None:
    clock = FakeClock()
    engine = _engine([_source("nasdaq", _quote(last=101.5, volume=2_400))], clock=clock)

    tick = engine.poll("AAPL")

    assert tick is not None
    assert tick.symbol == "AAPL"
    assert tick.last == 101.5
    assert tick.prev_close == 100.0
    assert tick.change == pytest.approx(1.5)
    assert tick.change_pct == pytest.approx(1.5)
    assert tick.volume == 2_400.0
    assert tick.ts_exchange == T0
    assert tick.ts_received == T0
    assert tick.source == "nasdaq"
    assert tick.direction is Direction.UP
    assert tick.velocity is None  # one tick is not a velocity
    assert tick.stale is False


def test_missing_prev_close_yields_none_not_zero_change() -> None:
    engine = _engine([_source("primary", _quote(last=50.0, prev_close=None, volume=None))])

    tick = engine.poll("AAPL")

    assert tick is not None
    assert tick.last == 50.0
    assert tick.prev_close is None
    assert tick.change is None  # a 0.0 here would claim "unchanged" with no evidence
    assert tick.change_pct is None
    assert tick.direction is Direction.UNKNOWN
    assert tick.volume is None


def test_direction_follows_the_sign_against_prev_close() -> None:
    down = _engine([_source("p", _quote(last=99.0))]).poll("AAPL")
    flat = _engine([_source("p", _quote(last=100.0))]).poll("AAPL")

    assert down is not None and down.direction is Direction.DOWN
    assert down.change == pytest.approx(-1.0)
    assert flat is not None and flat.direction is Direction.FLAT
    assert flat.change == 0.0


def test_zero_volume_is_measured_but_missing_volume_is_none() -> None:
    zero = _engine([_source("p", _quote(volume=0.0))]).poll("AAPL")
    missing = _engine([_source("p", _quote(volume=None))]).poll("AAPL")

    assert zero is not None and zero.volume == 0.0
    assert missing is not None and missing.volume is None


def test_tick_is_frozen_after_issue() -> None:
    tick = _engine([_source("p", _quote())]).poll("AAPL")
    assert tick is not None

    with pytest.raises(FrozenInstanceError):
        tick.last = 999.0  # type: ignore[misc]


def test_tick_exposes_every_documented_field_when_dumped() -> None:
    tick = _engine([_source("p", _quote())]).poll("AAPL")
    assert tick is not None

    payload = asdict(tick)

    assert set(payload) == {
        "symbol",
        "last",
        "prev_close",
        "change",
        "change_pct",
        "volume",
        "ts_exchange",
        "ts_received",
        "source",
        "direction",
        "velocity",
        "stale",
    }
    assert payload["direction"] == "up"


def test_tick_cannot_be_built_with_a_zero_or_negative_price() -> None:
    with pytest.raises(ValueError, match="last"):
        QuoteTick(
            symbol="AAPL",
            last=0.0,
            prev_close=100.0,
            volume=None,
            ts_exchange=T0,
            ts_received=T0,
            source="p",
        )
    with pytest.raises(ValueError, match="prev_close"):
        SourceQuote(last=10.0, prev_close=0.0, ts_exchange=T0)
    with pytest.raises(ValueError, match="ts_exchange"):
        SourceQuote(last=10.0, ts_exchange=datetime(2026, 10, 23, 14, 30))


# ---------------------------------------------------------------------------
# Staleness: measured on the data clock, re-read at read time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("age", "stale"), [(0.0, False), (5.0, False), (5.001, True)])
def test_staleness_threshold_is_strictly_greater_than_five_seconds(
    age: float, stale: bool
) -> None:
    clock = FakeClock(T0 + timedelta(seconds=age))
    engine = _engine([_source("p", _quote(at=T0))], clock=clock)

    tick = engine.poll("AAPL")

    assert tick is not None
    assert tick.stale is stale


def test_read_time_recomputes_staleness_and_history_keeps_the_issue_stamp() -> None:
    clock = FakeClock()
    engine = _engine([_source("p", _quote(at=T0))], clock=clock)
    issued = engine.poll("AAPL")
    assert issued is not None and issued.stale is False

    clock.advance(6.0)

    latest = engine.latest("AAPL")
    snapshot = engine.snapshot()
    history = engine.history("AAPL")

    assert latest is not None and latest.stale is True
    assert snapshot["AAPL"] is not None and snapshot["AAPL"].stale is True
    # The ledger keeps what was true at issue time; it is not rewritten.
    assert history[0].stale is False


def test_cached_tick_is_served_stale_when_every_source_fails() -> None:
    clock = FakeClock()
    fail = {"now": False}

    def flaky(symbol: str) -> SourceQuote:
        if fail["now"]:
            raise RuntimeError("feed down")
        return _quote(at=clock())

    engine = _engine([_source("p", flaky)], clock=clock)
    first = engine.poll("AAPL")
    assert first is not None and first.stale is False

    clock.advance(6.0)
    fail["now"] = True
    second = engine.poll("AAPL")

    assert second is not None
    assert second.last == first.last
    assert second.ts_exchange == first.ts_exchange
    assert second.ts_received == first.ts_received
    assert second.stale is True


def test_stale_threshold_is_configurable_per_engine() -> None:
    clock = FakeClock(T0 + timedelta(seconds=2))
    engine = _engine([_source("p", _quote(at=T0))], clock=clock, stale_after_s=1.0)

    tick = engine.poll("AAPL")

    assert tick is not None and tick.stale is True
    assert engine.stale_after_s == 1.0
    assert STALE_AFTER_S == 5.0


def test_a_future_exchange_stamp_is_clock_skew_not_staleness() -> None:
    engine = _engine([_source("p", _quote(at=T0 + timedelta(seconds=3)))])

    tick = engine.poll("AAPL")

    assert tick is not None
    assert tick.age_s(T0) == 0.0
    assert tick.stale is False


# ---------------------------------------------------------------------------
# Failover: ordered sources, no stale served as fresh, one symbol never blocks
# ---------------------------------------------------------------------------


def test_primary_error_fails_over_to_the_backup() -> None:
    engine = _engine(
        [
            _source("primary", RuntimeError("primary exploded")),
            _source("backup", _quote(last=102.0)),
        ]
    )

    tick = engine.poll("AAPL")

    assert tick is not None
    assert tick.source == "backup"
    assert tick.last == 102.0
    attempts = engine.attempts
    assert [(a.source, a.outcome) for a in attempts] == [
        ("primary", SourceOutcome.ERROR),
        ("backup", SourceOutcome.OK),
    ]
    assert "primary exploded" in (attempts[0].detail or "")


def test_empty_primary_fails_over_to_the_backup() -> None:
    engine = _engine([_source("primary", None), _source("backup", _quote())])

    tick = engine.poll("AAPL")

    assert tick is not None and tick.source == "backup"
    assert engine.attempts[0].outcome is SourceOutcome.EMPTY


def test_invalid_payload_is_rejected_and_the_next_source_is_tried() -> None:
    engine = _engine([_source("broken", {"last": 1.0}), _source("backup", _quote())])

    tick = engine.poll("AAPL")

    assert tick is not None and tick.source == "backup"
    assert engine.attempts[0].outcome is SourceOutcome.ERROR
    assert "invalid quote" in (engine.attempts[0].detail or "")


def test_zero_price_payload_fails_over_instead_of_being_stored() -> None:
    broken = lambda symbol: SourceQuote(last=0.0, ts_exchange=T0)  # noqa: E731
    engine = _engine([_source("broken", broken), _source("backup", _quote())])

    tick = engine.poll("AAPL")

    assert tick is not None and tick.source == "backup" and tick.last == 101.0
    assert engine.attempts[0].outcome is SourceOutcome.ERROR


def test_a_stale_primary_loses_to_a_fresh_backup() -> None:
    clock = FakeClock(T0 + timedelta(seconds=60))
    engine = _engine(
        [
            _source("delayed", _quote(at=T0)),
            _source("realtime", _quote(at=T0 + timedelta(seconds=60))),
        ],
        clock=clock,
    )

    tick = engine.poll("AAPL")

    assert tick is not None
    assert tick.source == "realtime"
    assert tick.stale is False
    assert [(a.source, a.outcome) for a in engine.attempts] == [
        ("delayed", SourceOutcome.STALE),
        ("realtime", SourceOutcome.OK),
    ]


def test_when_every_source_is_stale_the_freshest_is_served_but_flagged() -> None:
    clock = FakeClock(T0)
    engine = _engine(
        [
            _source("older", _quote(last=90.0, at=T0 - timedelta(seconds=30))),
            _source("newer", _quote(last=95.0, at=T0 - timedelta(seconds=10))),
        ],
        clock=clock,
    )

    tick = engine.poll("AAPL")

    assert tick is not None
    assert tick.source == "newer"
    assert tick.last == 95.0
    assert tick.stale is True


def test_no_source_and_no_cache_is_none_not_a_zero_tick() -> None:
    engine = _engine([_source("a", RuntimeError("x")), _source("b", None)])

    assert engine.poll("AAPL") is None
    assert engine.snapshot() == {"AAPL": None}
    assert [a.outcome for a in engine.attempts] == [SourceOutcome.ERROR, SourceOutcome.EMPTY]


def test_failover_is_scoped_to_one_symbol_and_the_rest_of_the_watchlist_polls() -> None:
    def primary(symbol: str) -> SourceQuote | None:
        return _quote(last=101.0) if symbol == "AAA" else None

    def backup(symbol: str) -> SourceQuote | None:
        return _quote(last=202.0) if symbol == "BBB" else None

    engine = _engine([_source("primary", primary), _source("backup", backup)], ("AAA", "BBB"))

    ticks = engine.poll_all()

    assert list(ticks) == ["AAA", "BBB"]
    assert ticks["AAA"] is not None and ticks["AAA"].source == "primary"
    assert ticks["BBB"] is not None and ticks["BBB"].source == "backup"


# ---------------------------------------------------------------------------
# Dedupe and ordering
# ---------------------------------------------------------------------------


def test_identical_payload_is_deduped_without_a_history_entry() -> None:
    clock = FakeClock()
    engine = _engine([_source("p", _quote(at=T0))], clock=clock)
    first = engine.poll("AAPL")
    assert first is not None

    clock.advance(2.0)
    second = engine.poll("AAPL")

    assert second is not None
    assert second.ts_exchange == first.ts_exchange
    assert second.ts_received == first.ts_received  # arrival of that print, not of the re-poll
    assert len(engine.history("AAPL")) == 1
    assert second.velocity is None


def test_deduping_does_not_hide_aging() -> None:
    clock = FakeClock()
    engine = _engine([_source("p", _quote(at=T0))], clock=clock)
    assert engine.poll("AAPL") is not None

    clock.advance(6.0)
    deduped = engine.poll("AAPL")

    assert deduped is not None
    assert len(engine.history("AAPL")) == 1
    assert deduped.stale is True


def test_a_revised_price_on_the_same_timestamp_is_accepted() -> None:
    prints = iter([100.0, 101.0])
    engine = _engine([_source("p", lambda symbol: _quote(last=next(prints), at=T0))])

    first = engine.poll("AAPL")
    second = engine.poll("AAPL")

    assert first is not None and second is not None
    assert second.last == 101.0
    assert len(engine.history("AAPL")) == 2


def test_an_older_timestamp_never_overwrites_a_newer_tick() -> None:
    clock = FakeClock()
    old = {"on": False}

    def feed(symbol: str) -> SourceQuote:
        return _quote(last=95.0, at=T0 - timedelta(seconds=5)) if old["on"] else _quote(at=T0)

    engine = _engine([_source("p", feed)], clock=clock)
    fresh = engine.poll("AAPL")
    assert fresh is not None and fresh.stale is False

    clock.advance(1.0)
    old["on"] = True
    result = engine.poll("AAPL")

    assert result is not None
    assert result.ts_exchange == T0
    assert result.last == 101.0
    assert result.stale is False  # still one second old
    assert len(engine.history("AAPL")) == 1


# ---------------------------------------------------------------------------
# Velocity: signed price change per second over the recent tick window
# ---------------------------------------------------------------------------


def test_velocity_is_positive_when_price_rises() -> None:
    clock = FakeClock()
    prices = iter([100.0, 101.0, 103.0])
    engine = _engine(
        [_source("p", lambda symbol: _quote(last=next(prices), at=clock()))], clock=clock
    )

    first = engine.poll("AAPL")
    clock.advance(1.0)
    second = engine.poll("AAPL")
    clock.advance(1.0)
    third = engine.poll("AAPL")

    assert first is not None and first.velocity is None
    assert second is not None and second.velocity == pytest.approx(1.0)
    assert third is not None and third.velocity == pytest.approx(1.5)
    assert third.velocity is not None and third.velocity > 0


def test_velocity_is_negative_when_price_falls() -> None:
    clock = FakeClock()
    prices = iter([100.0, 99.0, 97.0])
    engine = _engine(
        [_source("p", lambda symbol: _quote(last=next(prices), at=clock()))], clock=clock
    )

    assert engine.poll("AAPL") is not None
    clock.advance(1.0)
    assert engine.poll("AAPL") is not None
    clock.advance(1.0)
    third = engine.poll("AAPL")

    assert third is not None
    assert third.velocity == pytest.approx(-1.5)
    assert third.velocity is not None and third.velocity < 0


def test_velocity_of_an_unchanged_price_is_a_measured_zero() -> None:
    clock = FakeClock()
    engine = _engine([_source("p", lambda symbol: _quote(last=101.0, at=clock()))], clock=clock)

    assert engine.poll("AAPL") is not None
    clock.advance(1.0)
    second = engine.poll("AAPL")

    assert second is not None
    assert second.velocity == 0.0


def test_velocity_needs_two_distinct_arrival_times() -> None:
    prints = iter([100.0, 101.0])
    engine = _engine(
        [_source("p", lambda symbol: _quote(last=next(prints), at=T0))], clock=FakeClock()
    )

    assert engine.poll("AAPL") is not None
    same_instant = engine.poll("AAPL")

    assert same_instant is not None
    assert same_instant.velocity is None


def test_velocity_does_not_reach_past_the_window() -> None:
    clock = FakeClock()
    prices = iter([100.0, 140.0])
    engine = _engine(
        [_source("p", lambda symbol: _quote(last=next(prices), at=clock()))],
        clock=clock,
        velocity_window_s=5.0,
    )

    assert engine.poll("AAPL") is not None
    clock.advance(30.0)
    later = engine.poll("AAPL")

    assert later is not None
    assert later.velocity is None  # the only anchor is outside the 5 s window


# ---------------------------------------------------------------------------
# Watchlist and engine contract
# ---------------------------------------------------------------------------


def test_watchlist_normalizes_dedupes_and_keeps_order() -> None:
    engine = _engine([_source("p", _quote())], ("aapl", "MSFT", "AAPL", " msft "))

    assert engine.watchlist == ("AAPL", "MSFT")
    assert engine.add("nvda") == ("AAPL", "MSFT", "NVDA")
    assert engine.add("NVDA") == ("AAPL", "MSFT", "NVDA")


def test_sources_receive_the_normalized_symbol() -> None:
    seen: list[str] = []

    def fetch(symbol: str) -> SourceQuote:
        seen.append(symbol)
        return _quote()

    engine = _engine([_source("p", fetch)], ())
    engine.poll(" aapl ")

    assert seen == ["AAPL"]


def test_poll_all_covers_the_whole_watchlist_in_order() -> None:
    def fetch(symbol: str) -> SourceQuote | None:
        return None if symbol == "MISSING" else _quote()

    engine = _engine([_source("p", fetch)], ("AAA", "MISSING", "BBB"))

    ticks = engine.poll_all()

    assert list(ticks) == ["AAA", "MISSING", "BBB"]
    assert ticks["AAA"] is not None and ticks["BBB"] is not None
    assert ticks["MISSING"] is None  # explicit absence, not a zero quote


def test_snapshot_reports_every_watchlist_symbol() -> None:
    engine = _engine([_source("p", _quote())], ("AAA", "BBB"))
    engine.poll("AAA")

    snapshot = engine.snapshot()

    assert list(snapshot) == ["AAA", "BBB"]
    assert snapshot["AAA"] is not None
    assert snapshot["BBB"] is None


def test_engine_requires_sources_with_unique_names() -> None:
    with pytest.raises(ValueError, match="at least one"):
        QuoteEngine([])
    with pytest.raises(ValueError, match="unique"):
        QuoteEngine([_source("p", _quote()), _source("p", _quote())])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"stale_after_s": 0.0},
        {"stale_after_s": -1.0},
        {"velocity_window_s": 0.0},
        {"max_ticks": 1},
        {"max_attempts": 0},
    ],
)
def test_bad_engine_thresholds_raise(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        QuoteEngine([_source("p", _quote())], ("AAPL",), **kwargs)


def test_bare_callables_are_accepted_and_named() -> None:
    def nasdaq(symbol: str) -> SourceQuote:
        return _quote(last=101.0)

    engine = QuoteEngine([nasdaq], ("AAPL",), clock=FakeClock())
    tick = engine.poll("AAPL")

    assert engine.sources == ("nasdaq",)
    assert tick is not None and tick.source == "nasdaq"

    with pytest.raises(TypeError, match="QuoteSource"):
        QuoteEngine([42])  # type: ignore[list-item]


def test_blank_symbols_and_names_are_rejected() -> None:
    engine = _engine([_source("p", _quote())], ())
    with pytest.raises(ValueError):
        engine.poll("  ")
    with pytest.raises(ValueError):
        engine.watch([""])
    with pytest.raises(ValueError):
        QuoteSource("  ", lambda symbol: None)
    with pytest.raises(TypeError):
        QuoteSource("p", "not callable")  # type: ignore[arg-type]


def test_a_naive_clock_is_rejected_at_poll_time() -> None:
    engine = _engine(
        [_source("p", _quote())], clock=lambda: datetime(2026, 10, 23, 14, 30)
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        engine.poll("AAPL")


def test_attempts_are_bounded_by_the_engine() -> None:
    engine = _engine([_source("p", RuntimeError("x"))], ("AAPL",), max_attempts=3)

    for _ in range(5):
        assert engine.poll("AAPL") is None

    assert len(engine.attempts) == 3
    assert all(isinstance(attempt, Attempt) for attempt in engine.attempts)
