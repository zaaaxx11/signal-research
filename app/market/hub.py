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

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper() if isinstance(self.symbol, str) else ""
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
    """Own the free/premium loops and the shared latest-value store."""

    def __init__(
        self,
        *,
        free_source: QuoteSourceProtocol,
        premium_source: QuoteSourceProtocol,
        symbols: Sequence[str],
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
            symbol = str(raw).strip().upper()
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

        self.free_source = free_source
        self.premium_source = premium_source
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

    async def refresh(self, tier: str) -> CycleResult:
        source = self.free_source if tier == "free" else self.premium_source
        started_at = self._clock()
        started = time.perf_counter()
        answers: Mapping[str, SourceQuote | None] = {}
        error: str | None = None
        try:
            answered = source.fetch_many(self.symbols)
            answers = await answered if inspect.isawaitable(answered) else answered
            answers = {
                str(symbol).strip().upper(): quote
                for symbol, quote in (answers or {}).items()
            }
        except Exception as exc:  # noqa: BLE001 - a loop must keep its schedule
            error = f"{type(exc).__name__}: {exc}"
            self.last_error[tier] = error
            logger.warning("quotehub %s refresh failed: %s", tier, error)
        received = self._clock()
        returned = 0
        errors = 1 if error else 0
        for symbol in self.symbols:
            quote = answers.get(symbol)
            if quote is None:
                continue
            try:
                value = QuoteValue(
                    symbol=symbol,
                    last=quote.last,
                    prev_close=quote.prev_close,
                    volume=quote.volume,
                    ts_exchange=quote.ts_exchange,
                    ts_received=received,
                    source=source.name,
                    tier=tier,
                )
            except (TypeError, ValueError) as exc:
                errors += 1
                self.last_error[tier] = f"{type(exc).__name__}: {exc}"
                continue
            if self.store.merge(value):
                returned += 1
        result = CycleResult(
            tier=tier,
            requested=len(self.symbols),
            returned=returned,
            errors=errors,
            started_at=started_at,
            seconds=time.perf_counter() - started,
            error=error,
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
        free_source=free,
        premium_source=premium,
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
