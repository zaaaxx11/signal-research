"""Realtime watchlist quotes: one ``QuoteTick`` per symbol, provenance included.

The engine is a **poller**, not a subscriber: every ``poll`` asks each injected
source callable in priority order and keeps the first fresh answer. A source is
a ``QuoteSource(name, fetch)`` pair — or a bare callable, named from its
``__name__`` — so a test can drive a whole trading day from a list of lambdas
and a fake clock. Nothing here opens a socket or imports an adapter.

**The rules that make a tick trustworthy**

* ``last`` is a finite **positive** price. A source that reports ``0`` has not
  reported a price; it is rejected and the next source is tried. ``prev_close``
  and ``volume`` are ``None`` when the provider did not publish them — never
  ``0`` as a stand-in.
* ``ts_exchange`` is when the exchange printed the quote; ``ts_received`` is
  when this engine saw it. Staleness is measured on the **data** clock
  (``ts_received − ts_exchange`` at issue time), because a quote cannot become
  fresh again by being fetched again.
* A stale primary answer does not win: the engine tries the next source and
  only serves a stale tick, explicitly flagged, when no source has anything
  fresher. ``snapshot()``/``latest()`` re-evaluate the age at read time, so a
  tick cached five seconds ago is never handed out as fresh.
* Repeated identical payloads are deduped: no new history entry, no reset of
  velocity. A payload with an older ``ts_exchange`` never overwrites a newer
  cached tick.
* ``direction`` is the sign of the move against ``prev_close``
  (``up``/``down``/``flat``); with no ``prev_close`` it is ``unknown``, not
  ``flat``. ``velocity`` is the price change per second over the recent tick
  window, signed the same way, and ``None`` until two distinct ticks exist.

**Batches.** ``poll`` asks one symbol at a time; ``poll_many`` is the async twin
the HTTP layer uses. A source that declares ``fetch_many`` is asked **once** for
the symbols still unresolved (Yahoo answers twenty per request), the rest are
asked per symbol. Every answer is judged by the same rules as a single poll, so
a batch cannot bypass the stale/failover/dedupe contract. State is only mutated
between awaits, so two concurrent ``poll_many`` calls cannot corrupt a tick.
A source that declares ``accept_stale`` ends the search for a symbol with its
stale answer, still flagged — that is for a primary whose backups are per-symbol
fallbacks that cannot be fresher, where otherwise a closed market would cost one
fallback request per symbol.
"""

from __future__ import annotations

import inspect
import math
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

#: A quote older than this is stale (PLAN P2 gate: "stale > 5 s ditandai").
STALE_AFTER_S = 5.0

#: Velocity is measured against the oldest tick inside this window.
VELOCITY_WINDOW_S = 10.0

#: Per-symbol tick history kept for velocity and audit.
MAX_TICKS = 240

#: Recent source attempts kept for the audit trail.
MAX_ATTEMPTS = 200


class Direction(str, Enum):
    """Day direction of the quote against the previous close."""

    UP = "up"
    DOWN = "down"
    FLAT = "flat"
    UNKNOWN = "unknown"


class SourceOutcome(str, Enum):
    """What happened when one source was asked for one symbol."""

    OK = "ok"
    EMPTY = "empty"
    ERROR = "error"
    STALE = "stale"


def _aware(moment: datetime, name: str) -> datetime:
    """An observation without a timezone is not an observation."""

    if not isinstance(moment, datetime):
        raise TypeError(f"{name} must be a datetime, got {type(moment).__name__}")
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware, got {moment!r}")
    return moment


def _finite(value: object, name: str) -> float:
    """A float that is not NaN and not infinite, or ``ValueError``."""

    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got {value!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return number


def _positive(value: object, name: str) -> float:
    """A price is a finite number **greater than zero**; zero is not a quote."""

    number = _finite(value, name)
    if number <= 0:
        raise ValueError(f"{name} must be > 0, got {value!r}")
    return number


def _optional_positive(value: float | None, name: str) -> float | None:
    return None if value is None else _positive(value, name)


def _optional_non_negative(value: float | None, name: str) -> float | None:
    if value is None:
        return None
    number = _finite(value, name)
    if number < 0:
        raise ValueError(f"{name} must be >= 0, got {value!r}")
    return number


@dataclass(frozen=True, slots=True)
class SourceQuote:
    """One source's answer for one symbol, before the engine judges it.

    A source either returns a ``SourceQuote`` or ``None`` (nothing there). It
    never returns zeros to mean "unknown": omitted fields stay ``None``.
    ``realtime`` is the provider's own claim (Nasdaq publishes ``isRealTime``);
    ``None`` means the provider did not say, never "yes".
    """

    last: float
    ts_exchange: datetime
    prev_close: float | None = None
    volume: float | None = None
    realtime: bool | None = None

    def __post_init__(self) -> None:
        _positive(self.last, "last")
        _optional_positive(self.prev_close, "prev_close")
        _optional_non_negative(self.volume, "volume")
        _aware(self.ts_exchange, "ts_exchange")
        if self.realtime is not None and not isinstance(self.realtime, bool):
            raise TypeError(f"realtime must be a bool or None, got {self.realtime!r}")


@dataclass(frozen=True, slots=True)
class QuoteTick:
    """A judged quote.

    ``change``, ``change_pct`` and ``direction`` are derived in
    ``__post_init__`` so a tick can never carry a change that disagrees with
    its own prices. ``stale`` is stamped by the engine at issue time; use
    ``is_stale`` for a read-time opinion.
    """

    symbol: str
    last: float
    prev_close: float | None
    volume: float | None
    ts_exchange: datetime
    ts_received: datetime
    source: str
    velocity: float | None = None
    stale: bool = False
    change: float | None = field(init=False, default=None)
    change_pct: float | None = field(init=False, default=None)
    direction: Direction = field(init=False, default=Direction.UNKNOWN)

    def __post_init__(self) -> None:
        symbol = self.symbol.strip() if isinstance(self.symbol, str) else ""
        if not symbol:
            raise ValueError("symbol must be a non-empty string")
        object.__setattr__(self, "symbol", symbol)
        last = _positive(self.last, "last")
        previous = _optional_positive(self.prev_close, "prev_close")
        volume = _optional_non_negative(self.volume, "volume")
        object.__setattr__(self, "volume", volume)
        _aware(self.ts_exchange, "ts_exchange")
        _aware(self.ts_received, "ts_received")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source must be a non-empty string")
        object.__setattr__(self, "source", self.source.strip())
        velocity = None if self.velocity is None else _finite(self.velocity, "velocity")
        object.__setattr__(self, "velocity", velocity)

        if previous is None:
            change: float | None = None
            change_pct: float | None = None
            direction = Direction.UNKNOWN
        else:
            change = last - previous
            change_pct = (last / previous - 1.0) * 100.0
            direction = (
                Direction.UP
                if change > 0
                else Direction.DOWN
                if change < 0
                else Direction.FLAT
            )
        object.__setattr__(self, "change", change)
        object.__setattr__(self, "change_pct", change_pct)
        object.__setattr__(self, "direction", direction)

    def age_s(self, now: datetime) -> float:
        """Seconds between the exchange print and ``now`` (negative clock skew → 0)."""

        _aware(now, "now")
        return max(0.0, (now - self.ts_exchange).total_seconds())

    def is_stale(self, now: datetime, stale_after_s: float = STALE_AFTER_S) -> bool:
        """Age strictly greater than the threshold; exactly 5.0 s is still fresh."""

        return self.age_s(now) > stale_after_s


@dataclass(frozen=True, slots=True)
class QuoteSource:
    """A named, injectable quote callable.

    ``fetch(symbol)`` returns a ``SourceQuote`` or ``None``; it may raise, and
    the engine will move to the next source and record the attempt.

    ``fetch_many(symbols)`` is the batch twin: it returns a mapping from every
    requested symbol to its answer (``None`` for "this source does not know
    it"), which is how a twenty-symbol HTTP request becomes twenty ticks. A
    source may implement only one of the two; the engine records a clear ERROR
    attempt when a sync poll meets a batch-only source, instead of pretending
    the source had nothing.

    ``accept_stale`` says a stale answer from **this** source ends the search
    for that symbol: the tick is served with ``stale=True`` rather than asking a
    later source. It is for a primary whose backup is a *per-symbol fallback*
    (Nasdaq is one request per symbol, US-only and delayed 15 minutes), where
    "maybe the backup is fresher" costs seconds and can never be true. A source
    that leaves it ``False`` keeps the original rule: a fresher backup wins.
    """

    name: str
    fetch: Callable[[str], SourceQuote | None | Awaitable[SourceQuote | None]] | None = None
    fetch_many: (
        Callable[
            [Sequence[str]],
            Mapping[str, SourceQuote | None] | Awaitable[Mapping[str, SourceQuote | None]],
        ]
        | None
    ) = None
    accept_stale: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("source name must be a non-empty string")
        object.__setattr__(self, "name", self.name.strip())
        if self.fetch is not None and not callable(self.fetch):
            raise TypeError("source fetch must be callable")
        if self.fetch_many is not None and not callable(self.fetch_many):
            raise TypeError("source fetch_many must be callable")
        if self.fetch is None and self.fetch_many is None:
            raise ValueError("a QuoteSource needs fetch or fetch_many")
        if not isinstance(self.accept_stale, bool):
            raise TypeError(f"accept_stale must be a bool, got {self.accept_stale!r}")


@dataclass(frozen=True, slots=True)
class Attempt:
    """One line of the failover audit trail."""

    symbol: str
    source: str
    outcome: SourceOutcome
    detail: str | None = None


@dataclass
class _SymbolState:
    ticks: deque[QuoteTick]
    latest: QuoteTick | None = None


class QuoteEngine:
    """Poll a watchlist through ordered sources and keep the freshest tick."""

    def __init__(
        self,
        sources: Sequence[QuoteSource | Callable[[str], SourceQuote | None]],
        watchlist: Iterable[str] = (),
        *,
        stale_after_s: float = STALE_AFTER_S,
        velocity_window_s: float = VELOCITY_WINDOW_S,
        max_ticks: int = MAX_TICKS,
        max_attempts: int = MAX_ATTEMPTS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not sources:
            raise ValueError("at least one QuoteSource is required")
        resolved: list[QuoteSource] = []
        for index, source in enumerate(sources):
            if isinstance(source, QuoteSource):
                resolved.append(source)
                continue
            if callable(source):
                name = getattr(source, "__name__", "") or f"source_{index}"
                if name == "<lambda>":
                    name = f"source_{index}"
                resolved.append(QuoteSource(name, source))
                continue
            raise TypeError(
                "sources must be QuoteSource instances or callables, got "
                f"{type(source).__name__}"
            )
        names = [source.name for source in resolved]
        if len(set(names)) != len(names):
            raise ValueError(f"source names must be unique, got {names}")
        if not stale_after_s > 0:
            raise ValueError(f"stale_after_s must be > 0, got {stale_after_s!r}")
        if not velocity_window_s > 0:
            raise ValueError(f"velocity_window_s must be > 0, got {velocity_window_s!r}")
        if not isinstance(max_ticks, int) or max_ticks < 2:
            raise ValueError(f"max_ticks must be an int >= 2, got {max_ticks!r}")
        if not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError(f"max_attempts must be an int >= 1, got {max_attempts!r}")
        self._sources: tuple[QuoteSource, ...] = tuple(resolved)
        self._stale_after_s = float(stale_after_s)
        self._velocity_window_s = float(velocity_window_s)
        self._max_ticks = max_ticks
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self._states: dict[str, _SymbolState] = {}
        self._attempts: deque[Attempt] = deque(maxlen=max_attempts)
        self._watchlist: tuple[str, ...] = ()
        self.watch(watchlist)

    # -- watchlist ---------------------------------------------------------

    @staticmethod
    def _symbol(symbol: str) -> str:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string, got {symbol!r}")
        return symbol.strip().upper()

    def watch(self, symbols: Iterable[str]) -> tuple[str, ...]:
        """Replace the watchlist, preserving first-seen order and dropping repeats."""

        ordered: list[str] = []
        for symbol in symbols:
            normalized = self._symbol(symbol)
            if normalized not in ordered:
                ordered.append(normalized)
        self._watchlist = tuple(ordered)
        return self._watchlist

    def add(self, symbol: str) -> tuple[str, ...]:
        """Append one symbol to the watchlist (idempotent)."""

        return self.watch([*self._watchlist, symbol])

    @property
    def watchlist(self) -> tuple[str, ...]:
        return self._watchlist

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(source.name for source in self._sources)

    @property
    def stale_after_s(self) -> float:
        return self._stale_after_s

    @property
    def attempts(self) -> tuple[Attempt, ...]:
        """Recent source attempts, oldest first (bounded audit trail)."""

        return tuple(self._attempts)

    # -- polling -----------------------------------------------------------

    def clock_now(self) -> datetime:
        moment = self._clock()
        return _aware(moment, "clock")

    def poll(self, symbol: str) -> QuoteTick | None:
        """Ask every source in order; return the freshest acceptable tick.

        ``None`` means no source produced anything valid *and* no cached tick
        exists — which is different from a tick with a null field.
        """

        key = self._symbol(symbol)
        now = self.clock_now()
        state = self._state(key)
        stale_candidates: list[QuoteTick] = []

        for source in self._sources:
            if source.fetch is None:
                # A batch-only source has no single-symbol answer; saying
                # "empty" here would be a lie about the data.
                self._record(
                    key,
                    source.name,
                    SourceOutcome.ERROR,
                    "batch-only source; use poll_many()",
                )
                continue
            try:
                raw = source.fetch(key)
            except Exception as exc:  # a broken feed is a failover, not a crash
                self._record(key, source.name, SourceOutcome.ERROR, f"{type(exc).__name__}: {exc}")
                continue
            if raw is None:
                self._record(key, source.name, SourceOutcome.EMPTY)
                continue
            if not isinstance(raw, SourceQuote):
                got = (
                    "an awaitable (async source; use poll_many())"
                    if inspect.isawaitable(raw)
                    else type(raw).__name__
                )
                self._record(
                    key,
                    source.name,
                    SourceOutcome.ERROR,
                    f"invalid quote: a source must return SourceQuote or None, got {got}",
                )
                continue
            try:
                tick = self._tick(key, raw, source.name, now, state)
            except (TypeError, ValueError) as exc:
                self._record(key, source.name, SourceOutcome.ERROR, f"invalid quote: {exc}")
                continue
            if tick.stale:
                self._record(
                    key,
                    source.name,
                    SourceOutcome.STALE,
                    f"age={tick.age_s(now):.3f}s",
                )
                if source.accept_stale:
                    # This source's stale answer is the answer; a later fallback
                    # cannot be fresher, and asking costs a request per symbol.
                    return self._accept(key, state, tick, now)
                stale_candidates.append(tick)
                continue
            self._record(key, source.name, SourceOutcome.OK)
            return self._accept(key, state, tick, now)

        if stale_candidates:
            freshest = max(stale_candidates, key=lambda candidate: candidate.ts_exchange)
            return self._accept(key, state, freshest, now)

        if state.latest is not None:
            # Serve memory, but never as fresh: the age is re-read on this clock.
            return replace(
                state.latest,
                stale=state.latest.is_stale(now, self._stale_after_s),
            )
        return None

    async def poll_many(self, symbols: Iterable[str]) -> dict[str, QuoteTick | None]:
        """Poll many symbols through the sources, batching where a source can.

        Sources with ``fetch_many`` are asked **once** for the symbols still
        unresolved; a source without it is asked per symbol. Judging is the same
        as :meth:`poll`: a fresh answer wins, identical prints are deduped and a
        symbol nobody answered is ``None`` — never a zero quote. A stale answer
        is parked and a later source may beat it, unless the source declared
        ``accept_stale`` (a primary whose backups cannot be fresher): then the
        stale tick is served, flagged, and the search for that symbol ends.

        The order of the returned mapping is the caller's order, deduplicated;
        a symbol that appears twice is polled once.
        """

        requested: list[str] = []
        for symbol in symbols:
            key = self._symbol(symbol)
            if key not in requested:
                requested.append(key)
        if not requested:
            return {}

        results: dict[str, QuoteTick | None] = dict.fromkeys(requested)
        pending: list[str] = list(requested)
        stale_candidates: dict[str, QuoteTick] = {}

        for source in self._sources:
            if not pending:
                break
            if source.fetch_many is not None:
                answers = await self._batch(source, pending)
                if answers is None:
                    # The whole source failed; every pending symbol was recorded.
                    continue
                # The data clock restarts when the answer *arrives*: a two-second
                # HTTP round-trip must not be served as a two-second-fresher quote.
                now = self.clock_now()
                for key in list(pending):
                    if key in answers:
                        self._judge(
                            key,
                            answers[key],
                            source.name,
                            now,
                            results,
                            pending,
                            stale_candidates,
                            accept_stale=source.accept_stale,
                        )
                continue
            # A per-symbol source is judged as each answer lands, so the first
            # Nasdaq fallback is not re-stamped with the last symbol's arrival
            # time (which would make a fresh quote look stale).
            for key in list(pending):
                try:
                    raw = source.fetch(key) if source.fetch is not None else None
                    if inspect.isawaitable(raw):
                        raw = await raw
                except Exception as exc:
                    self._record(
                        key, source.name, SourceOutcome.ERROR, f"{type(exc).__name__}: {exc}"
                    )
                    continue
                self._judge(
                    key,
                    raw,
                    source.name,
                    self.clock_now(),
                    results,
                    pending,
                    stale_candidates,
                    accept_stale=source.accept_stale,
                )

        now = self.clock_now()
        for key in pending:
            candidate = stale_candidates.get(key)
            if candidate is not None:
                results[key] = self._accept(key, self._state(key), candidate, now)
                continue
            state = self._states.get(key)
            if state is not None and state.latest is not None:
                # Serve memory, re-aged on this clock: never as fresh.
                results[key] = replace(
                    state.latest,
                    stale=state.latest.is_stale(now, self._stale_after_s),
                )
        return results

    def poll_all(self) -> dict[str, QuoteTick | None]:
        """Poll every watchlist symbol, in order; one failure does not stop the rest."""

        return {symbol: self.poll(symbol) for symbol in self._watchlist}

    def latest(self, symbol: str) -> QuoteTick | None:
        """The cached tick with staleness recomputed at read time."""

        key = self._symbol(symbol)
        state = self._states.get(key)
        if state is None or state.latest is None:
            return None
        return replace(
            state.latest,
            stale=state.latest.is_stale(self.clock_now(), self._stale_after_s),
        )

    def snapshot(self) -> dict[str, QuoteTick | None]:
        """Watchlist order, every symbol present, missing ones explicitly ``None``."""

        return {symbol: self.latest(symbol) for symbol in self._watchlist}

    def history(self, symbol: str) -> tuple[QuoteTick, ...]:
        """Accepted (deduped) ticks for one symbol, oldest first."""

        state = self._states.get(self._symbol(symbol))
        return tuple(state.ticks) if state is not None else ()

    # -- internals ---------------------------------------------------------

    def _state(self, key: str) -> _SymbolState:
        return self._states.setdefault(key, _SymbolState(deque(maxlen=self._max_ticks)))

    async def _batch(
        self, source: QuoteSource, symbols: Sequence[str]
    ) -> dict[str, Any] | None:
        """One source's answers for ``symbols``; ``None`` when the source failed.

        The mapping carries **every** requested key: a symbol the provider
        skipped is explicitly ``None`` (EMPTY), never "not asked".
        """

        fetch_many = source.fetch_many
        if fetch_many is None:  # pragma: no cover - callers check first
            return None
        try:
            raw = fetch_many(tuple(symbols))
            if inspect.isawaitable(raw):
                raw = await raw
            if not isinstance(raw, Mapping):
                raise TypeError(
                    f"fetch_many must return a mapping, got {type(raw).__name__}"
                )
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            for key in symbols:
                self._record(key, source.name, SourceOutcome.ERROR, detail)
            return None
        # Keys are normalized: a source that echoes ``aapl`` still answers the
        # engine's ``AAPL``, and a symbol it skipped is explicitly None.
        normalized = {str(name).strip().upper(): answer for name, answer in raw.items()}
        return {key: normalized.get(key) for key in symbols}

    def _judge(
        self,
        key: str,
        raw: Any,
        source_name: str,
        now: datetime,
        results: dict[str, QuoteTick | None],
        pending: list[str],
        stale_candidates: dict[str, QuoteTick],
        *,
        accept_stale: bool = False,
    ) -> None:
        """Try one answer: accept it, park it as stale, or record why not."""

        if raw is None:
            self._record(key, source_name, SourceOutcome.EMPTY)
            return
        state = self._state(key)
        try:
            tick = self._tick(key, raw, source_name, now, state)
        except (TypeError, ValueError) as exc:
            self._record(key, source_name, SourceOutcome.ERROR, f"invalid quote: {exc}")
            return
        if tick.stale:
            self._record(
                key, source_name, SourceOutcome.STALE, f"age={tick.age_s(now):.3f}s"
            )
            if accept_stale:
                results[key] = self._accept(key, state, tick, now)
                if key in pending:
                    pending.remove(key)
                return
            earlier = stale_candidates.get(key)
            if earlier is None or tick.ts_exchange > earlier.ts_exchange:
                stale_candidates[key] = tick
            return
        self._record(key, source_name, SourceOutcome.OK)
        results[key] = self._accept(key, state, tick, now)
        if key in pending:
            pending.remove(key)

    def _tick(
        self,
        symbol: str,
        raw: SourceQuote,
        source_name: str,
        now: datetime,
        state: _SymbolState,
    ) -> QuoteTick:
        if not isinstance(raw, SourceQuote):
            raise TypeError(
                "a source must return SourceQuote or None, got "
                f"{type(raw).__name__}"
            )
        return QuoteTick(
            symbol=symbol,
            last=raw.last,
            prev_close=raw.prev_close,
            volume=raw.volume,
            ts_exchange=raw.ts_exchange,
            ts_received=now,
            source=source_name,
            velocity=self._velocity(state, raw.last, now),
            stale=(now - raw.ts_exchange).total_seconds() > self._stale_after_s,
        )

    def _velocity(self, state: _SymbolState, last: float, now: datetime) -> float | None:
        """Price change per second against the oldest tick inside the window."""

        cutoff = now - timedelta(seconds=self._velocity_window_s)
        anchor = next(
            (tick for tick in state.ticks if tick.ts_received >= cutoff),
            None,
        )
        if anchor is None:
            return None
        elapsed = (now - anchor.ts_received).total_seconds()
        if elapsed <= 0:
            return None
        return (last - anchor.last) / elapsed

    def _accept(
        self, symbol: str, state: _SymbolState, tick: QuoteTick, now: datetime
    ) -> QuoteTick:
        latest = state.latest
        if latest is not None:
            if tick.ts_exchange < latest.ts_exchange:
                # Out-of-order payload: stale news may not overwrite fresh news.
                return replace(latest, stale=latest.is_stale(now, self._stale_after_s))
            if (
                tick.ts_exchange == latest.ts_exchange
                and tick.last == latest.last
                and tick.volume == latest.volume
            ):
                # Dedupe: same print, same price. No history entry, no velocity reset.
                return replace(latest, stale=latest.is_stale(now, self._stale_after_s))
        state.ticks.append(tick)
        state.latest = tick
        return tick

    def _record(
        self,
        symbol: str,
        source: str,
        outcome: SourceOutcome,
        detail: str | None = None,
    ) -> None:
        self._attempts.append(
            Attempt(symbol=symbol, source=source, outcome=outcome, detail=detail)
        )


__all__ = [
    "MAX_ATTEMPTS",
    "MAX_TICKS",
    "STALE_AFTER_S",
    "VELOCITY_WINDOW_S",
    "Attempt",
    "Direction",
    "QuoteEngine",
    "QuoteSource",
    "QuoteTick",
    "SourceOutcome",
    "SourceQuote",
]
