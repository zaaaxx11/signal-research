"""In-memory quote hub for the free and premium refresh loops.

The hub is deliberately **not** a request-time scraper. Two background loops
write the latest observation for every symbol into one store; API routes read
that store. A slow source therefore delays the next refresh, never a user
request.

The free and premium loops are the same mechanism with different cadence. The
premium cadence is drawn from a range on every cycle, so the process does not
publish a fixed polling fingerprint. The tier is internal metadata: it decides
which observation wins a merge and is never part of the public document.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import os
import random
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.market.quote import SourceQuote

logger = logging.getLogger("mee.quotehub")

#: Free symbols refresh once every fifteen minutes. This is the product's
#: free-tier cadence, not a provider limit.
FREE_REFRESH_S = 900.0

#: Premium symbols refresh every one to two minutes, randomized per cycle.
PREMIUM_REFRESH_MIN_S = 60.0
PREMIUM_REFRESH_MAX_S = 120.0

#: The merged JSON snapshot is small (about 1 MB for 4k symbols). It is written
#: after a refresh, not on every symbol update.
DEFAULT_STORE_PATH = Path("data/quotes.json")


class QuoteSourceProtocol(Protocol):
    """The only thing the hub needs from a source adapter."""

    name: str

    async def fetch_many(
        self, symbols: Sequence[str]
    ) -> Mapping[str, SourceQuote | None]: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _finite_positive(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got {value!r}") from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be finite and > 0, got {value!r}")
    return number


def _optional_non_negative(value: Any, name: str) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got {value!r}") from None
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be finite and >= 0, got {value!r}")
    return number


_CLASS_SUFFIX = re.compile(r"^([A-Z][A-Z0-9]{0,9})\.([A-Z])$")


def canonical_symbol(raw: Any) -> str:
    """Normalize a provider ticker to the form the hub stores.

    Class shares are the one place providers disagree: Yahoo writes ``BRK-B``,
    CNBC/Google write ``BRK.B``. A one-letter class suffix becomes a dash; a
    dotted exchange suffix (``2330.TW``) is left alone because it is not a
    class share.
    """

    symbol = str(raw or "").strip().upper()
    match = _CLASS_SUFFIX.match(symbol)
    return f"{match.group(1)}-{match.group(2)}" if match else symbol


@dataclass(frozen=True, slots=True)
class QuoteValue:
    """One judged quote in the hub store."""

    symbol: str
    last: float
    prev_close: float | None
    volume: float | None
    ts_exchange: datetime
    ts_received: datetime
    source: str
    tier: str
    sources: tuple[str, ...] = ()
    conflict: bool = False

    def __post_init__(self) -> None:
        symbol = canonical_symbol(self.symbol)
        if not symbol:
            raise ValueError("symbol must be a non-empty string")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "last", _finite_positive(self.last, "last"))
        object.__setattr__(
            self, "prev_close", _optional_non_negative(self.prev_close, "prev_close")
        )
        object.__setattr__(self, "volume", _optional_non_negative(self.volume, "volume"))
        if self.ts_exchange.tzinfo is None or self.ts_exchange.utcoffset() is None:
            raise ValueError("ts_exchange must be timezone-aware")
        if self.ts_received.tzinfo is None or self.ts_received.utcoffset() is None:
            raise ValueError("ts_received must be timezone-aware")
        source = str(self.source or "").strip()
        tier = str(self.tier or "").strip().lower()
        if not source:
            raise ValueError("source must be non-empty")
        if tier not in {"free", "premium"}:
            raise ValueError("tier must be free or premium")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "tier", tier)
        names = tuple(str(name).strip() for name in (self.sources or (source,)) if str(name).strip())
        if not names:
            names = (source,)
        object.__setattr__(self, "sources", names)
        if not isinstance(self.conflict, bool):
            raise TypeError("conflict must be a bool")

    @property
    def change(self) -> float | None:
        if self.prev_close is None:
            return None
        return self.last - self.prev_close

    @property
    def change_pct(self) -> float | None:
        if self.prev_close is None or self.prev_close == 0:
            return None
        return (self.last / self.prev_close - 1.0) * 100.0

    def document(self) -> dict[str, Any]:
        """Public cell: values and the exchange clock, never source/tier."""

        return {
            "symbol": self.symbol,
            "last": self.last,
            "prev_close": self.prev_close,
            "change": self.change,
            "change_pct": self.change_pct,
            "volume": self.volume,
            "ts_exchange": self.ts_exchange.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class CycleResult:
    """What one refresh actually did."""

    tier: str
    requested: int
    returned: int
    errors: int
    started_at: datetime
    seconds: float
    error: str | None = None


class QuoteStore:
    """Latest-value store with a guarded, tier-aware merge.

    Rules:

    * an older exchange timestamp never overwrites a newer one;
    * a free observation never overwrites a premium observation at the same
      timestamp;
    * a null/missing price is rejected before it reaches this method;
    * the public document deliberately omits ``source`` and ``tier``.
    """

    def __init__(self) -> None:
        self._values: dict[str, QuoteValue] = {}

    def __len__(self) -> int:
        return len(self._values)

    def get(self, symbol: str) -> QuoteValue | None:
        return self._values.get(str(symbol).strip().upper())

    def merge(self, value: QuoteValue) -> bool:
        current = self._values.get(value.symbol)
        if current is not None:
            if value.ts_exchange < current.ts_exchange:
                return False
            if (
                value.ts_exchange == current.ts_exchange
                and current.tier == "premium"
                and value.tier == "free"
            ):
                return False
        self._values[value.symbol] = value
        return True

    def snapshot(self, symbols: Iterable[str] | None = None) -> dict[str, QuoteValue]:
        if symbols is None:
            return dict(self._values)
        wanted = [str(symbol).strip().upper() for symbol in symbols]
        return {
            symbol: self._values[symbol]
            for symbol in wanted
            if symbol in self._values
        }

    def document(self, symbols: Iterable[str] | None = None) -> dict[str, Any]:
        selected = self.snapshot(symbols)
        order = (
            [str(symbol).strip().upper() for symbol in symbols]
            if symbols is not None
            else sorted(selected)
        )
        missing = [symbol for symbol in order if symbol not in selected]
        values = [selected[symbol] for symbol in order if symbol in selected]
        newest = max((value.ts_exchange for value in values), default=None)
        return {
            "as_of": newest.isoformat() if newest is not None else None,
            "count": len(values),
            "quotes": [value.document() for value in values],
            "missing": missing,
        }

    def write(self, path: str | Path) -> int:
        """Write a compact snapshot atomically; return bytes written."""

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = self.document()
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        temporary = target.with_name(f".{target.name}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return len(encoded)


class QuoteHub:
    """Own the free/premium loops and the shared latest-value store.

    Every source in a tier is asked on every cycle. A source is not a fallback:
    its observation joins the same match, and the newest timestamp wins (the
    earlier source in the sequence breaks a timestamp tie). That is what lets
    Yahoo, CNBC, Nasdaq and future adapters disagree without hiding an answer.
    """

    def __init__(
        self,
        *,
        symbols: Sequence[str],
        free_sources: Sequence[QuoteSourceProtocol] = (),
        premium_sources: Sequence[QuoteSourceProtocol] = (),
        free_source: QuoteSourceProtocol | None = None,
        premium_source: QuoteSourceProtocol | None = None,
        free_interval_s: float = FREE_REFRESH_S,
        premium_min_s: float = PREMIUM_REFRESH_MIN_S,
        premium_max_s: float = PREMIUM_REFRESH_MAX_S,
        store_path: str | Path | None = DEFAULT_STORE_PATH,
        history_dir: str | Path | None = None,
        clock: Callable[[], datetime] | None = None,
        random_source: random.Random | None = None,
    ) -> None:
        ordered = []
        seen: set[str] = set()
        for raw in symbols:
            symbol = canonical_symbol(raw)
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            ordered.append(symbol)
        if not ordered:
            raise ValueError("at least one symbol is required")
        if free_interval_s <= 0:
            raise ValueError("free_interval_s must be > 0")
        if premium_min_s <= 0 or premium_max_s < premium_min_s:
            raise ValueError("premium interval range is invalid")
        free = tuple(free_sources) or ((free_source,) if free_source else ())
        premium = tuple(premium_sources) or (
            (premium_source,) if premium_source else ()
        )
        if not free:
            raise ValueError("at least one free source is required")
        if not premium:
            raise ValueError("at least one premium source is required")
        for label, group in (("free", free), ("premium", premium)):
            names = [str(getattr(source, "name", "") or "").strip() for source in group]
            if any(not name for name in names):
                raise ValueError(f"{label} source needs a non-empty name")
            if len(set(names)) != len(names):
                raise ValueError(f"{label} source names must be unique: {names}")
            for source in group:
                if not callable(getattr(source, "fetch_many", None)):
                    raise TypeError(
                        f"{label} source {getattr(source, 'name', source)!r} "
                        "must implement fetch_many"
                    )

        self.free_sources = free
        self.premium_sources = premium
        self.symbols = tuple(ordered)
        self.free_interval_s = float(free_interval_s)
        self.premium_min_s = float(premium_min_s)
        self.premium_max_s = float(premium_max_s)
        self.store_path = Path(store_path) if store_path is not None else None
        self.history_dir = Path(history_dir) if history_dir is not None else None
        self.store = QuoteStore()
        self._clock = clock or _utcnow
        self._random = random_source or random.Random()
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[Any]] = []
        self._persist_lock = asyncio.Lock()
        self._history_hour: str | None = None
        self.last_cycle: dict[str, CycleResult] = {}
        self.last_error: dict[str, str] = {}
        self.started = False

    # -- lifecycle ---------------------------------------------------------

    @property
    def ready(self) -> bool:
        return bool(self.store)

    def next_delay(self, tier: str) -> float:
        """Free is fixed; premium is uniformly random in its range."""

        if tier == "free":
            return self.free_interval_s
        if tier == "premium":
            return self._random.uniform(self.premium_min_s, self.premium_max_s)
        raise ValueError(f"unknown tier {tier!r}")

    async def start(self) -> None:
        if self.started:
            return
        self.started = True
        self._stop.clear()
        self._tasks = [
            asyncio.create_task(self._loop("free"), name="quotehub-free"),
            asyncio.create_task(self._loop("premium"), name="quotehub-premium"),
        ]

    async def stop(self) -> None:
        self._stop.set()
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.started = False

    async def _loop(self, tier: str) -> None:
        while not self._stop.is_set():
            try:
                result = await self.refresh(tier)
                logger.info(
                    "quotehub %s cycle requested=%s returned=%s errors=%s seconds=%.3f",
                    tier,
                    result.requested,
                    result.returned,
                    result.errors,
                    result.seconds,
                )
            except Exception as exc:  # noqa: BLE001 - a bad cycle must not kill the loop
                self.last_error[tier] = f"{type(exc).__name__}: {exc}"
                logger.exception("quotehub %s cycle crashed", tier)
            delay = self.next_delay(tier)
            logger.info("quotehub %s next delay=%.1fs", tier, delay)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except TimeoutError:
                continue

    # -- refreshes ---------------------------------------------------------

    async def _collect_source(self, source: QuoteSourceProtocol) -> dict[str, SourceQuote]:
        """Fetch one source and normalize every symbol key to hub spelling."""

        answered = source.fetch_many(self.symbols)
        result = await answered if inspect.isawaitable(answered) else answered
        if not isinstance(result, Mapping):
            raise TypeError(f"{source.name} fetch_many did not return a mapping")
        return {
            canonical_symbol(symbol): quote
            for symbol, quote in result.items()
            if isinstance(quote, SourceQuote)
        }

    @staticmethod
    def _match(
        candidates: Sequence[tuple[str, int, SourceQuote]],
    ) -> tuple[tuple[str, int, SourceQuote], tuple[str, ...], bool]:
        """Pick the newest observation; source order breaks an exact tie.

        The result also carries every source that supplied the symbol and a
        conflict flag. Conflict is a diagnostic, not a reason to discard a
        quote: the newest print still wins.
        """

        ordered = sorted(
            candidates,
            key=lambda item: (item[2].ts_exchange, -item[1]),
            reverse=True,
        )
        winner = ordered[0]
        names = tuple(
            source
            for source, _priority, _quote in sorted(
                candidates, key=lambda item: item[1]
            )
        )
        prices = [item[2].last for item in candidates]
        median = sorted(prices)[len(prices) // 2]
        conflict = len(prices) > 1 and (
            max(prices) - min(prices) > max(0.01, abs(median) * 0.005)
        )
        return winner, names, conflict

    async def refresh(self, tier: str) -> CycleResult:
        sources = self.free_sources if tier == "free" else self.premium_sources
        started_at = self._clock()
        started = time.perf_counter()
        observations: dict[str, list[tuple[str, int, SourceQuote]]] = {}
        messages: list[str] = []
        errors = 0

        # Every source is asked, every cycle. There is no fallback branch:
        # a source that returns nothing simply contributes no candidate, and a
        # source that raises does not hide the others.
        for priority, source in enumerate(sources):
            try:
                answers = await self._collect_source(source)
            except Exception as exc:  # noqa: BLE001 - one source is not a cycle
                errors += 1
                message = f"{source.name}: {type(exc).__name__}: {exc}"
                messages.append(message)
                self.last_error[tier] = message
                logger.warning("quotehub %s source failed: %s", tier, message)
                continue
            for symbol in self.symbols:
                quote = answers.get(symbol)
                if quote is not None:
                    observations.setdefault(symbol, []).append(
                        (source.name, priority, quote)
                    )

        received = self._clock()
        returned = 0
        for symbol in self.symbols:
            candidates = observations.get(symbol)
            if not candidates:
                continue
            try:
                winner, names, conflict = self._match(candidates)
                value = QuoteValue(
                    symbol=symbol,
                    last=winner[2].last,
                    prev_close=winner[2].prev_close,
                    volume=winner[2].volume,
                    ts_exchange=winner[2].ts_exchange,
                    ts_received=received,
                    source=winner[0],
                    tier=tier,
                    sources=names,
                    conflict=conflict,
                )
            except (TypeError, ValueError) as exc:
                errors += 1
                message = f"{symbol}: {type(exc).__name__}: {exc}"
                messages.append(message)
                self.last_error[tier] = message
                continue
            if self.store.merge(value):
                returned += 1

        if not messages:
            self.last_error.pop(tier, None)
        result = CycleResult(
            tier=tier,
            requested=len(self.symbols),
            returned=returned,
            errors=errors,
            started_at=started_at,
            seconds=time.perf_counter() - started,
            error="; ".join(messages)[:500] if messages else None,
        )
        self.last_cycle[tier] = result
        await self.persist()
        return result

    async def persist(self) -> int:
        if self.store_path is None:
            return 0
        async with self._persist_lock:
            written = self.store.write(self.store_path)
            if self.history_dir is not None and len(self.store):
                now = self._clock()
                hour = now.strftime("%Y-%m-%dT%H")
                if hour != self._history_hour:
                    self.store.write(
                        self.history_dir / now.strftime("%Y-%m-%d") / f"{now:%H}.json"
                    )
                    self._history_hour = hour
            return written

    def document(self, symbols: Iterable[str] | None = None) -> dict[str, Any]:
        return self.store.document(symbols)


def build_quote_hub(
    settings: Any,
    symbols: Sequence[str],
    *,
    clock: Callable[[], datetime] | None = None,
    random_source: random.Random | None = None,
) -> QuoteHub:
    """Build the production hub from settings, with no network at construction."""

    from app.market.bulk import CnbcBatchSource
    from app.market.sources import YahooQuoteSource

    batch_size = int(getattr(settings, "quote_hub_batch_size", 400))
    premium_batch = int(getattr(settings, "quote_premium_batch_size", 500))
    timeout_s = float(getattr(settings, "quote_timeout_s", 10.0))
    free = YahooQuoteSource(
        batch_size=batch_size, timeout_s=timeout_s, bulk=True
    )
    premium = CnbcBatchSource(batch_size=premium_batch, timeout_s=timeout_s)
    return QuoteHub(
        # Both sources are collected in both tiers. The tier chooses cadence,
        # not a single provider; the newest observation wins the match.
        free_sources=(free, premium),
        premium_sources=(premium, free),
        symbols=symbols,
        free_interval_s=float(getattr(settings, "quote_free_refresh_s", FREE_REFRESH_S)),
        premium_min_s=float(
            getattr(settings, "quote_premium_refresh_min_s", PREMIUM_REFRESH_MIN_S)
        ),
        premium_max_s=float(
            getattr(settings, "quote_premium_refresh_max_s", PREMIUM_REFRESH_MAX_S)
        ),
        store_path=getattr(settings, "quote_store_path", DEFAULT_STORE_PATH),
        history_dir=getattr(settings, "quote_history_dir", None),
        clock=clock,
        random_source=random_source,
    )


__all__ = [
    "CycleResult",
    "DEFAULT_STORE_PATH",
    "FREE_REFRESH_S",
    "PREMIUM_REFRESH_MAX_S",
    "PREMIUM_REFRESH_MIN_S",
    "QuoteHub",
    "QuoteStore",
    "QuoteValue",
    "build_quote_hub",
]
