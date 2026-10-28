"""Depth collector: many sources, one field-level match per symbol.

Price and depth are separate stores. A price is a single number with an exchange
timestamp; a depth field is a value that may arrive from Finviz, StockAnalysis,
Yahoo or SEC at different times. This module therefore merges by **field**, not
by row: a newer `pe` can win while an older `employees` remains the only answer.

No source is a fallback. Every configured source is fetched on every cycle; a
missing field simply has no candidate. The public JSON carries values only —
`source`, priority and per-field timestamps stay internal.
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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol

from app.market.sectors import classify_sector

logger = logging.getLogger("mee.depth")

DEPTH_REFRESH_S = 86_400.0
DEPTH_JITTER_S = 1_800.0
DEFAULT_STORE_PATH = Path("data/depth.json")

#: After a chunk's deadline expires it is cancelled; this is how long the
#: collector still waits for that cancellation to unwind. A provider that
#: ignores ``CancelledError`` must not be able to hang the cycle *after* its
#: budget already failed it.
CHUNK_DRAIN_S = 5.0

#: These belong to the quote hub; keeping them out of the depth row avoids two
#: stores publishing different prices for the same symbol.
QUOTE_ONLY_FIELDS = frozenset({"price", "change_pct", "volume", "prev_close"})

#: Different providers print the same catalog key under a local name. Normalise
#: at the edge so the store never publishes `oper_margin` and
#: `operating_margin` as two separate facts.
FIELD_ALIASES: dict[str, str] = {
    "pe_ttm": "pe",
    "pe_forward": "forward_pe",
    "oper_margin": "operating_margin",
    "profit_margin": "net_margin",
    "income": "net_income",
    "net_income_ttm": "net_income",
    "market_cap_usd": "market_cap",
    "float_shares": "shares_float",
    "avg_volume": "average_volume",
    "week52_high": "high_52w",
    "week52_low": "low_52w",
    "rsi_14": "rsi",
    "atr_14": "atr",
    "rel_volume": "relative_volume",
    "index": "index_membership",
    "ipo": "ipo_date",
    "perf_week": "perf_1w",
    "perf_month": "perf_4w",
    "perf_quarter": "perf_13w",
    "perf_half_y": "perf_26w",
    "perf_year": "perf_52w",
    "eps_ttm": "eps",
    "revenue_ttm": "sales",
}

#: Raw parser carry-overs that duplicate a canonical field already produced
#: from the same page. They are dropped at the edge, never published beside
#: their split/renamed counterpart.
DROP_FIELDS = frozenset(
    {"eps_past_3_5y", "sales_past_3_5y", "option_short", "volatility"}
)


def canonical_field(raw: Any) -> str:
    name = str(raw or "").strip()
    return FIELD_ALIASES.get(name, name)


class DepthSourceProtocol(Protocol):
    name: str

    async def fetch_many(
        self, symbols: Sequence[str]
    ) -> Mapping[str, "DepthObservation"]: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


_CLASS_SUFFIX = re.compile(r"^([A-Z][A-Z0-9]{0,9})\.([A-Z])$")


def canonical_symbol(raw: Any) -> str:
    """Yahoo/Finviz use ``BRK-B``; CNBC/StockAnalysis use ``BRK.B``."""

    symbol = str(raw or "").strip().upper()
    match = _CLASS_SUFFIX.match(symbol)
    return f"{match.group(1)}-{match.group(2)}" if match else symbol


@dataclass(frozen=True, slots=True)
class DepthObservation:
    """One source's parsed values for one symbol."""

    symbol: str
    fields: Mapping[str, Any]
    as_of: datetime
    source: str

    def __post_init__(self) -> None:
        symbol = canonical_symbol(self.symbol)
        if not symbol:
            raise ValueError("symbol must be non-empty")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        if not isinstance(self.fields, Mapping):
            raise TypeError("fields must be a mapping")
        object.__setattr__(self, "symbol", symbol)
        normalized: dict[str, Any] = {}
        for field, value in self.fields.items():
            name = canonical_field(field)
            if name in DROP_FIELDS:
                continue
            # Yahoo reports this one cell in percent points while Finviz and
            # StockAnalysis report the catalog's ratio. Normalize at the source
            # boundary; a merge must never choose between 0.17 and 16.97.
            if self.source.strip().lower() == "yahoo" and name == "debt_to_equity":
                try:
                    value = float(value) / 100.0
                except (TypeError, ValueError):
                    continue
            normalized[name] = value
        object.__setattr__(self, "fields", normalized)


@dataclass(frozen=True, slots=True)
class DepthField:
    value: Any
    as_of: datetime
    source: str
    priority: int


@dataclass(frozen=True, slots=True)
class DepthCycleResult:
    requested: int
    returned: int
    errors: int
    started_at: datetime
    seconds: float
    error: str | None = None


class DepthStore:
    """Field-level latest-value store with source priority on timestamp ties."""

    def __init__(self) -> None:
        self._symbols: dict[str, dict[str, DepthField]] = {}

    def __len__(self) -> int:
        return len(self._symbols)

    def merge(self, observation: DepthObservation, *, priority: int) -> int:
        changed = 0
        bucket = self._symbols.setdefault(observation.symbol, {})
        for field, value in observation.fields.items():
            if value is None or value == "":
                continue
            name = str(field).strip()
            if not name:
                continue
            current = bucket.get(name)
            if current is not None:
                # Priority is the stable tie-breaker: a daily depth row must
                # not change its `market_cap` because the network happened to
                # finish Yahoo after Finviz. Within one source, a newer
                # observation still replaces an older one.
                if priority > current.priority:
                    continue
                if priority == current.priority and observation.as_of <= current.as_of:
                    continue
            bucket[name] = DepthField(
                value=value,
                as_of=observation.as_of,
                source=observation.source,
                priority=priority,
            )
            changed += 1
        return changed

    def seed(self, payload: Mapping[str, Any], *, source: str = "cache") -> int:
        """Load a previous document as low-priority fallback values.

        A restart must not make ``GET /market/depth`` answer 503 while the first
        full cycle is still running. Cached values get a very high priority
        number (lower numbers win in :meth:`merge`), so every live source
        replaces them as soon as it has a value.
        """
        symbols = payload.get("symbols")
        if not isinstance(symbols, Mapping):
            return 0
        raw_stamp = str(payload.get("as_of") or "").strip()
        try:
            stamp = datetime.fromisoformat(raw_stamp) if raw_stamp else datetime.now(UTC)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
        except ValueError:
            stamp = datetime.now(UTC)
        loaded = 0
        for raw_symbol, raw_fields in symbols.items():
            if not isinstance(raw_fields, Mapping):
                continue
            symbol = canonical_symbol(raw_symbol)
            if not symbol:
                continue
            bucket = self._symbols.setdefault(symbol, {})
            for raw_name, value in raw_fields.items():
                if value is None or value == "":
                    continue
                name = str(raw_name).strip()
                if not name:
                    continue
                bucket.setdefault(
                    name,
                    DepthField(value=value, as_of=stamp, source=source, priority=10_000_000),
                )
                loaded += 1
        return loaded

    def snapshot(
        self, symbols: Sequence[str] | None = None
    ) -> dict[str, dict[str, DepthField]]:
        if symbols is None:
            return {symbol: dict(fields) for symbol, fields in self._symbols.items()}
        wanted = [canonical_symbol(symbol) for symbol in symbols]
        return {
            symbol: dict(self._symbols[symbol])
            for symbol in wanted
            if symbol in self._symbols
        }

    def document(self, symbols: Sequence[str] | None = None) -> dict[str, Any]:
        selected = self.snapshot(symbols)
        order = (
            [canonical_symbol(symbol) for symbol in symbols]
            if symbols is not None
            else sorted(selected)
        )
        rows: dict[str, dict[str, Any]] = {}
        stamps: list[datetime] = []
        for symbol in order:
            fields = selected.get(symbol)
            if not fields:
                continue
            rows[symbol] = {
                name: field.value
                for name, field in sorted(fields.items())
            }
            stamps.extend(field.as_of for field in fields.values())
        return {
            "as_of": max(stamps).isoformat() if stamps else None,
            "count": len(rows),
            "symbols": rows,
            "missing": [symbol for symbol in order if symbol not in rows],
        }

    def write(self, path: str | Path) -> int:
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


def _consume_task_result(task: asyncio.Task[Any]) -> None:
    """Read a finished task's outcome so a discarded chunk cannot warn later."""

    if task.cancelled():
        return
    task.exception()


def _chunked(symbols: Sequence[str], size: int) -> list[tuple[str, ...]]:
    """Split a run into request-sized batches; order is preserved."""

    return [
        tuple(symbols[offset : offset + size])
        for offset in range(0, len(symbols), size)
    ]


class DepthCollector:
    """Collect every depth source and merge by field."""

    def __init__(
        self,
        *,
        symbols: Sequence[str],
        sources: Sequence[DepthSourceProtocol],
        store_path: str | Path | None = DEFAULT_STORE_PATH,
        seed_path: str | Path | None = None,
        refresh_s: float = DEPTH_REFRESH_S,
        jitter_s: float = DEPTH_JITTER_S,
        source_timeout_s: float = 180.0,
        source_chunk_size: int = 250,
        clock: Callable[[], datetime] | None = None,
        random_source: random.Random | None = None,
    ) -> None:
        ordered = []
        seen: set[str] = set()
        for raw in symbols:
            symbol = canonical_symbol(raw)
            if symbol and symbol not in seen:
                seen.add(symbol)
                ordered.append(symbol)
        if not ordered:
            raise ValueError("at least one symbol is required")
        if not sources:
            raise ValueError("at least one depth source is required")
        names = [str(getattr(source, "name", "") or "").strip() for source in sources]
        if any(not name for name in names):
            raise ValueError("every depth source needs a name")
        if len(set(names)) != len(names):
            raise ValueError(f"depth source names must be unique: {names}")
        for source in sources:
            if not callable(getattr(source, "fetch_many", None)):
                raise TypeError(f"{source.name} must implement fetch_many")
        if refresh_s <= 0:
            raise ValueError("refresh_s must be > 0")
        if jitter_s < 0:
            raise ValueError("jitter_s must be >= 0")
        # A zero budget would turn every chunk into an instant timeout and a
        # zero chunk would loop forever without asking anything.
        if float(source_timeout_s) <= 0:
            raise ValueError("source_timeout_s must be > 0")
        if int(source_chunk_size) <= 0:
            raise ValueError("source_chunk_size must be > 0")
        self.symbols = tuple(ordered)
        self.sources = tuple(sources)
        self.store_path = Path(store_path) if store_path is not None else None
        self.seed_path = Path(seed_path) if seed_path is not None else None
        self.refresh_s = float(refresh_s)
        self.jitter_s = float(jitter_s)
        self.source_timeout_s = float(source_timeout_s)
        self.source_chunk_size = int(source_chunk_size)
        self.store = DepthStore()
        self._clock = clock or _utcnow
        self._random = random_source or random.Random()
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[Any]] = []
        self._persist_lock = asyncio.Lock()
        self.last_cycle: DepthCycleResult | None = None
        self.last_error: str | None = None
        self.started = False

    @property
    def ready(self) -> bool:
        return bool(self.store)

    def next_delay(self) -> float:
        if self.jitter_s <= 0:
            return self.refresh_s
        return self.refresh_s + self._random.uniform(-self.jitter_s, self.jitter_s)

    async def start(self) -> None:
        if self.started:
            return
        self._seed()
        self.started = True
        self._stop.clear()
        self._tasks = [
            asyncio.create_task(self._loop(), name="depth-collector"),
        ]

    def _seed(self) -> int:
        """Load the previous snapshot before the first network cycle."""
        if self.seed_path is None or not self.seed_path.exists():
            return 0
        try:
            payload = json.loads(self.seed_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - stale cache is optional
            logger.warning("depth seed failed: %s", exc)
            return 0
        loaded = self.store.seed(payload)
        if loaded:
            logger.info("depth seeded %s cached fields from %s", loaded, self.seed_path)
        return loaded

    async def stop(self) -> None:
        self._stop.set()
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.started = False

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                result = await self.refresh()
                logger.info(
                    "depth cycle requested=%s returned=%s errors=%s seconds=%.3f",
                    result.requested,
                    result.returned,
                    result.errors,
                    result.seconds,
                )
            except Exception as exc:  # noqa: BLE001 - keep the daily schedule
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("depth cycle crashed")
            delay = self.next_delay()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except TimeoutError:
                continue

    async def _call_source(
        self, source: DepthSourceProtocol, chunk: Sequence[str]
    ) -> Mapping[str, "DepthObservation"]:
        fetched = source.fetch_many(chunk)
        result = await fetched if inspect.isawaitable(fetched) else fetched
        if not isinstance(result, Mapping):
            raise TypeError("fetch_many did not return a mapping")
        return result

    async def _fetch_chunk(
        self, source: DepthSourceProtocol, chunk: Sequence[str]
    ) -> Mapping[str, "DepthObservation"]:
        """Ask one source for one chunk, bounded by ``source_timeout_s``.

        The chunk runs in its own task behind ``asyncio.shield``, so the
        deadline cancels the *wait*, not the work: ``TimeoutError`` is raised
        even if a provider swallows ``CancelledError`` and returns late values.
        A late mapping is reaped and discarded, never merged. Cancelling the
        cycle itself still propagates and cancels the chunk.
        """

        task = asyncio.ensure_future(self._call_source(source, chunk))
        try:
            return await asyncio.wait_for(
                asyncio.shield(task), timeout=self.source_timeout_s
            )
        except TimeoutError:
            # Cancel and reap the chunk, but only for a bounded while: a
            # provider that ignores cancellation has already failed and must
            # not be allowed to hold the cycle open past its deadline.
            task.cancel()
            done, pending = await asyncio.wait({task}, timeout=CHUNK_DRAIN_S)
            for finished in done:
                _consume_task_result(finished)
            for straggler in pending:
                straggler.add_done_callback(_consume_task_result)
                logger.warning(
                    "depth chunk for %s ignored cancellation after %.1fs",
                    source.name,
                    CHUNK_DRAIN_S,
                )
            raise
        except asyncio.CancelledError:
            task.cancel()
            raise

    async def refresh(self) -> DepthCycleResult:
        started_at = self._clock()
        started = time.perf_counter()
        observations: dict[str, list[tuple[int, DepthObservation]]] = {}
        messages: list[str] = []
        errors = 0

        # Sources are visited in configured priority order and every source is
        # asked chunk by chunk, sequentially. Priority is the source index
        # alone: which chunk of a source finished first cannot promote a
        # lower-priority value above an earlier source's field.
        for priority, source in enumerate(self.sources):
            for chunk in _chunked(self.symbols, self.source_chunk_size):
                try:
                    fetched = await self._fetch_chunk(source, chunk)
                except TimeoutError:
                    errors += 1
                    message = (
                        f"{source.name}: timeout after "
                        f"{self.source_timeout_s:g}s ({len(chunk)} symbols)"
                    )
                    messages.append(message)
                    self.last_error = message
                    logger.warning("depth source chunk timed out: %s", message)
                    continue
                except Exception as exc:  # noqa: BLE001 - one chunk is not a cycle
                    errors += 1
                    message = f"{source.name}: {type(exc).__name__}: {exc}"
                    messages.append(message)
                    self.last_error = message
                    logger.warning("depth source chunk failed: %s", message)
                    continue
                for observation in fetched.values():
                    if isinstance(observation, DepthObservation):
                        observations.setdefault(observation.symbol, []).append(
                            (priority, observation)
                        )

        returned = 0
        for symbol in self.symbols:
            candidates = observations.get(symbol)
            if not candidates:
                continue
            matched = 0
            for priority, observation in sorted(candidates, key=lambda item: item[0]):
                matched += self.store.merge(observation, priority=priority)
            if matched:
                returned += 1
        # A partial run is still a result: a timed-out chunk must not discard
        # the fields the other chunks and sources already delivered, and a
        # failed write must not discard the in-memory merge either.
        try:
            await self.persist()
        except Exception as exc:  # noqa: BLE001 - the next cycle retries the write
            errors += 1
            message = f"persist: {type(exc).__name__}: {exc}"
            messages.append(message)
            self.last_error = message
            logger.warning("depth persist failed: %s", message)
        else:
            if not messages:
                self.last_error = None
        result = DepthCycleResult(
            requested=len(self.symbols),
            returned=returned,
            errors=errors,
            started_at=started_at,
            seconds=time.perf_counter() - started,
            error="; ".join(messages)[:500] if messages else None,
        )
        self.last_cycle = result
        return result

    async def persist(self) -> int:
        if self.store_path is None:
            return 0
        async with self._persist_lock:
            return self.store.write(self.store_path)

    def document(self, symbols: Sequence[str] | None = None) -> dict[str, Any]:
        return self.store.document(symbols)


# ---------------------------------------------------------------------------
# Finviz
# ---------------------------------------------------------------------------

_FIN_NUMBER = re.compile(r"^([+-]?\d[\d,]*(?:\.\d+)?)\s*([KMBT])?%?$")
_FIN_SUFFIX = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def _fin_number(text: Any) -> float | None:
    raw = str(text or "").strip().replace("$", "").replace("%", "")
    if not raw or raw.upper() in {"N/A", "NA", "--", "-"}:
        return None
    match = _FIN_NUMBER.match(raw)
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    return value * _FIN_SUFFIX.get(match.group(2) or "", 1.0)


def _first_number(text: Any) -> float | None:
    match = re.search(r"[+-]?\d[\d,]*(?:\.\d+)?", str(text or ""))
    return _fin_number(match.group(0)) if match else None


def _percent_pair(text: Any) -> tuple[float | None, float | None]:
    values = re.findall(r"([+-]?\d[\d,]*(?:\.\d+)?)%", str(text or ""))
    if len(values) < 2:
        return (None, None)
    return (float(values[0].replace(",", "")), float(values[1].replace(",", "")))


def parse_finviz_extras(body: str) -> dict[str, Any]:
    """Read the identity/dividend/sentiment cells the wide parser ignores.

    These labels are already on the same page as the metrics. Reading them adds
    no request; a missing or dashed cell stays absent.
    """

    from app.providers.adapters.finviz import snapshot_rows

    rows = snapshot_rows(body)
    out: dict[str, Any] = {}

    text_fields = {
        "earnings_date": ("Earnings",),
        "dividend_ex_date": ("Dividend Ex-Date", "Ex-Div Date"),
    }
    for field, labels in text_fields.items():
        for label in labels:
            value = str(rows.get(label) or "").strip()
            if value and value != "-":
                out[field] = value
                break

    numeric_fields = {
        "dividend": ("Dividend TTM",),
        "dividend_ttm": ("Dividend TTM",),
        "payout_ratio": ("Payout",),
        "recommendation": ("Recom",),
        "target_price": ("Target Price",),
        "short_ratio": ("Short Ratio",),
        "short_interest": ("Short Interest",),
        "eps_surprise": ("EPS/Sales Surpr.",),
    }
    for field, labels in numeric_fields.items():
        for label in labels:
            value = _first_number(rows.get(label))
            if value is not None:
                out[field] = value
                break

    # EPS/Sales Surpr. is two percentages in one cell; read the second value
    # explicitly rather than letting the first-number rule hide it.
    surprise = rows.get("EPS/Sales Surpr.")
    if surprise:
        eps, revenue = _percent_pair(surprise)
        if eps is not None:
            out["eps_surprise"] = eps
        if revenue is not None:
            out["revenue_surprise"] = revenue

    growth = rows.get("Dividend Gr. 3/5Y")
    if growth:
        three, five = _percent_pair(growth)
        if three is not None:
            out["dividend_growth_3y"] = three
        if five is not None:
            out["dividend_growth_5y"] = five

    for prefix, label in (
        ("eps_past", "EPS past 3/5Y"),
        ("sales_past", "Sales past 3/5Y"),
    ):
        cell = rows.get(label)
        if cell:
            three, five = _percent_pair(cell)
            if three is not None:
                out[f"{prefix}_3y"] = three
            if five is not None:
                out[f"{prefix}_5y"] = five

    volatility = rows.get("Volatility")
    if volatility:
        weekly, monthly = _percent_pair(volatility)
        if weekly is not None:
            out["volatility_1w"] = weekly
        if monthly is not None:
            out["volatility_4w"] = monthly

    option_short = str(rows.get("Option/Short") or "").strip()
    if option_short:
        parts = [part.strip() for part in option_short.split("/", 1)]
        if parts and parts[0]:
            out["optionable"] = parts[0]
        if len(parts) > 1 and parts[1]:
            out["shortable"] = parts[1]

    for field, label in (("high_52w", "52W High"), ("low_52w", "52W Low")):
        value = _first_number(rows.get(label))
        if value is not None:
            out[field] = value

    return {key: value for key, value in out.items() if value not in (None, "")}


class FinvizDepthSource:
    """One Finviz stock page per symbol; existing parser, no new vocabulary."""

    name = "finviz"

    def __init__(
        self,
        *,
        adapter: Any | None = None,
        concurrency: int = 3,
        delay_s: float = 0.1,
    ) -> None:
        self.concurrency = max(1, int(concurrency))
        self.delay_s = max(0.0, float(delay_s))
        self._adapter = adapter

    def _adapter_or_build(self) -> Any:
        if self._adapter is None:
            from app.providers.adapters.finviz import FinvizAdapter

            self._adapter = FinvizAdapter()
        return self._adapter

    async def fetch_many(
        self, symbols: Sequence[str]
    ) -> dict[str, DepthObservation]:
        from app.contracts import Capability
        from app.providers.adapters.finviz import parse_facts, parse_wide_metrics
        from app.providers.base import InstrumentRef

        adapter = self._adapter_or_build()
        semaphore = asyncio.Semaphore(self.concurrency)
        out: dict[str, DepthObservation] = {}

        async def one(symbol: str) -> None:
            async with semaphore:
                if self.delay_s:
                    await asyncio.sleep(self.delay_s)
                try:
                    instrument = InstrumentRef.from_symbol(symbol)
                    raw = await adapter.fetch(
                        Capability.FUNDAMENTALS, instrument, set()
                    )
                    if int(raw.get("status", 0)) != 200:
                        return
                    body = raw.get("body", b"")
                    text = (
                        body.decode("utf-8", errors="replace")
                        if isinstance(body, bytes)
                        else str(body or "")
                    )
                    fields = parse_wide_metrics(text)
                    fields.update(parse_facts(text))
                    fields.update(parse_finviz_extras(text))
                    fields = {
                        name: value
                        for name, value in fields.items()
                        if name not in QUOTE_ONLY_FIELDS and value is not None
                    }
                    if fields:
                        out[canonical_symbol(symbol)] = DepthObservation(
                            symbol=symbol,
                            fields=fields,
                            as_of=datetime.now(UTC),
                            source=self.name,
                        )
                except Exception as exc:  # noqa: BLE001 - one symbol is optional
                    logger.info("finviz depth failed for %s: %s", symbol, exc)

        await asyncio.gather(*(one(symbol) for symbol in symbols))
        return out


# ---------------------------------------------------------------------------
# StockAnalysis
# ---------------------------------------------------------------------------

_SA_NUMBER = re.compile(r"^([+-]?\d[\d,]*(?:\.\d+)?)\s*([KMBT])?%?$")
_SA_SUFFIX = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def _sa_number(text: Any) -> float | None:
    raw = str(text or "").strip().replace("$", "").replace("%", "")
    if not raw or raw.upper() in {"N/A", "NA", "--", "-"}:
        return None
    match = _SA_NUMBER.match(raw)
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    return value * _SA_SUFFIX.get(match.group(2) or "", 1.0)


_SA_LABELS: dict[str, str] = {
    "Market Cap": "market_cap",
    "Enterprise Value": "enterprise_value",
    "PE Ratio": "pe",
    "Forward PE": "forward_pe",
    "PS Ratio": "ps",
    "PB Ratio": "pb",
    "P/FCF Ratio": "pfcf",
    "EV/EBITDA": "ev_ebitda",
    "EV/Sales": "ev_sales",
    "Current Ratio": "current_ratio",
    "Quick Ratio": "quick_ratio",
    "Debt / Equity": "debt_to_equity",
    "Return on Equity (ROE)": "roe",
    "Return on Assets (ROA)": "roa",
    "Return on Invested Capital (ROIC)": "roic",
    "Gross Margin": "gross_margin",
    "Operating Margin": "operating_margin",
    "Profit Margin": "net_margin",
    "EPS (Diluted)": "eps",
    "Revenue": "sales",
    "Shares Outstanding": "shares_outstanding",
    "Shares Float": "shares_float",
    "Beta (5Y)": "beta",
    "52-Week High": "high_52w",
    "52-Week Low": "low_52w",
    "Dividend Yield": "dividend_yield",
    "Payout Ratio": "payout_ratio",
    "Short Ratio": "short_ratio",
    "Short % of Float": "short_float",
}


class _TableRows(HTMLParser):
    """Collect text cells of every ``tr``; enough for StockAnalysis tables."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            if self._row is not None:
                self._row.append(text)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def parse_stockanalysis_statistics(body: str) -> dict[str, Any]:
    """Read the statistics page's label/value table into canonical names."""

    parser = _TableRows()
    try:
        parser.feed(body or "")
        parser.close()
    except Exception:  # noqa: BLE001 - a broken page is no rows, not a crash
        return {}
    out: dict[str, Any] = {}
    for row in parser.rows:
        if len(row) < 2:
            continue
        label = row[0].strip()
        field = _SA_LABELS.get(label)
        if field is None or field in out:
            continue
        value = _sa_number(row[1])
        if value is not None:
            out[field] = value
    return out


class StockAnalysisDepthSource:
    """One StockAnalysis statistics page per symbol."""

    name = "stockanalysis"

    def __init__(
        self,
        *,
        concurrency: int = 3,
        delay_s: float = 0.1,
        timeout_s: float = 15.0,
        user_agent: str = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    ) -> None:
        self.concurrency = max(1, int(concurrency))
        self.delay_s = max(0.0, float(delay_s))
        self.timeout_s = float(timeout_s)
        self.user_agent = user_agent

    def url(self, symbol: str) -> str:
        return f"https://stockanalysis.com/stocks/{symbol.lower()}/statistics/"

    def headers(self) -> dict[str, str]:
        return {
            "user-agent": self.user_agent,
            "accept": "text/html,application/xhtml+xml",
            "accept-language": "en-US,en;q=0.9",
        }

    async def fetch_many(
        self, symbols: Sequence[str]
    ) -> dict[str, DepthObservation]:
        from app.providers import http as provider_http

        semaphore = asyncio.Semaphore(self.concurrency)
        out: dict[str, DepthObservation] = {}

        async def one(symbol: str) -> None:
            async with semaphore:
                if self.delay_s:
                    await asyncio.sleep(self.delay_s)
                try:
                    async with provider_http.build_client(
                        timeout_s=self.timeout_s, headers=self.headers()
                    ) as client:
                        response = await client.get(self.url(symbol))
                    if int(getattr(response, "status_code", 0)) != 200:
                        return
                    fields = parse_stockanalysis_statistics(response.text)
                    fields = {
                        name: value
                        for name, value in fields.items()
                        if name not in QUOTE_ONLY_FIELDS and value is not None
                    }
                    if fields:
                        out[canonical_symbol(symbol)] = DepthObservation(
                            symbol=symbol,
                            fields=fields,
                            as_of=datetime.now(UTC),
                            source=self.name,
                        )
                except Exception as exc:  # noqa: BLE001 - one symbol is optional
                    logger.info("stockanalysis depth failed for %s: %s", symbol, exc)

        await asyncio.gather(*(one(symbol) for symbol in symbols))
        return out


# ---------------------------------------------------------------------------
# Yahoo
# ---------------------------------------------------------------------------


class YahooDepthSource:
    """Yahoo quoteSummary, projected from the existing adapter's observations."""

    name = "yahoo"

    def __init__(self, *, adapter: Any | None = None, concurrency: int = 2) -> None:
        self.concurrency = max(1, int(concurrency))
        self._adapter = adapter

    def _adapter_or_build(self) -> Any:
        if self._adapter is None:
            from app.providers.adapters.yahoo import YahooAdapter

            self._adapter = YahooAdapter()
        return self._adapter

    async def fetch_many(
        self, symbols: Sequence[str]
    ) -> dict[str, DepthObservation]:
        from app.contracts import Capability
        from app.providers.base import InstrumentRef

        adapter = self._adapter_or_build()
        instruments = [InstrumentRef.from_symbol(symbol) for symbol in symbols]
        bulk = getattr(adapter, "fetch_fundamentals_many", None)
        raws: dict[str, Any] = {}
        if callable(bulk):
            result = bulk(instruments, concurrency=self.concurrency)
            result = await result if inspect.isawaitable(result) else result
            raws = dict(result) if isinstance(result, Mapping) else {}
        else:
            # Test doubles and older adapters keep the single-symbol path.
            semaphore = asyncio.Semaphore(self.concurrency)

            async def one(instrument: Any) -> None:
                async with semaphore:
                    raws[instrument.symbol] = await adapter.fetch(
                        Capability.FUNDAMENTALS, instrument, set()
                    )

            await asyncio.gather(*(one(instrument) for instrument in instruments))

        out: dict[str, DepthObservation] = {}
        for instrument in instruments:
            symbol = instrument.symbol
            raw = raws.get(symbol)
            if not isinstance(raw, Mapping):
                continue
            try:
                verdict = adapter.verdict(raw, instrument)
                if not getattr(verdict, "ok", False):
                    continue
                observations = adapter.parse(raw, instrument)
                fields: dict[str, Any] = {}
                for observation in observations:
                    value = observation.get("value_num")
                    if value is None:
                        value = observation.get("value_text")
                    if value is None:
                        continue
                    field = str(observation.get("field") or "")
                    if not field or field in QUOTE_ONLY_FIELDS:
                        continue
                    fields[field] = value
                if fields:
                    out[canonical_symbol(symbol)] = DepthObservation(
                        symbol=symbol,
                        fields=fields,
                        # Depth merge recency is *fetch* recency. Yahoo's
                        # observation clock is the last trade, which before the
                        # open can be hours old and must not lose a field to a
                        # source fetched later by accident.
                        as_of=datetime.now(UTC),
                        source=self.name,
                    )
            except Exception as exc:  # noqa: BLE001 - one symbol is optional
                logger.info("yahoo depth failed for %s: %s", symbol, exc)
        return out


# ---------------------------------------------------------------------------
# Yahoo chart (performance/technical)
# ---------------------------------------------------------------------------


class YahooChartDepthSource:
    """One 10-year daily chart per symbol; no crumb and no cookie."""

    name = "yahoo_chart"

    def __init__(
        self,
        *,
        concurrency: int = 2,
        timeout_s: float = 20.0,
        base_url: str = "https://query1.finance.yahoo.com",
    ) -> None:
        self.concurrency = max(1, int(concurrency))
        self.timeout_s = float(timeout_s)
        self.base_url = base_url.rstrip("/")

    @staticmethod
    def url(symbol: str, base_url: str) -> str:
        return (
            f"{base_url}/v8/finance/chart/{symbol}"
            "?interval=1d&range=10y&includePrePost=false&events=div%2Csplit"
        )

    async def fetch_many(
        self, symbols: Sequence[str]
    ) -> dict[str, DepthObservation]:
        from app.market.chart import parse_chart_metrics
        from app.providers import http as provider_http
        from app.providers.adapters.yahoo import BROWSER_USER_AGENT

        semaphore = asyncio.Semaphore(self.concurrency)
        out: dict[str, DepthObservation] = {}
        headers = {"user-agent": BROWSER_USER_AGENT, "accept": "application/json"}

        async with provider_http.build_client(
            timeout_s=self.timeout_s, headers=headers
        ) as client:

            async def one(raw_symbol: str) -> None:
                symbol = canonical_symbol(raw_symbol)
                async with semaphore:
                    try:
                        response = await client.get(
                            self.url(symbol, self.base_url)
                        )
                        if int(getattr(response, "status_code", 0)) != 200:
                            return
                        payload = response.json()
                        result = (payload.get("chart") or {}).get("result") or []
                        if not result:
                            return
                        fields = parse_chart_metrics(result[0])
                        fields = {
                            key: value
                            for key, value in fields.items()
                            if key not in QUOTE_ONLY_FIELDS and value is not None
                        }
                        if fields:
                            out[symbol] = DepthObservation(
                                symbol=symbol,
                                fields=fields,
                                as_of=datetime.now(UTC),
                                source=self.name,
                            )
                    except Exception as exc:  # noqa: BLE001 - one symbol optional
                        logger.info("yahoo chart failed for %s: %s", symbol, exc)

            await asyncio.gather(*(one(symbol) for symbol in symbols))
        return out


# ---------------------------------------------------------------------------
# SEC identity
# ---------------------------------------------------------------------------


class SecDepthSource:
    """SEC identity/industry from the engine snapshot; no request per symbol.

    SEC is the official identity spine: ticker, company, exchange, industry and
    country. It does not publish a GICS sector, so ``sector`` comes from the
    explicit SIC-description classifier in :mod:`app.market.sectors`. A snapshot
    that is absent simply contributes no rows; it is not a network fallback.
    """

    name = "sec"

    def __init__(
        self,
        *,
        snapshot_path: str | Path | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        raw = snapshot_path or os.environ.get("MEE_ENGINE_SNAPSHOT") or "data/engine-snapshot.json"
        self.snapshot_path = Path(raw).expanduser()
        self._clock = clock or _utcnow
        self._dataset: Any | None = None
        self._loaded = False
        self.load_error: str | None = None

    def _load(self) -> Any | None:
        if self._loaded:
            return self._dataset
        self._loaded = True
        if not self.snapshot_path.exists():
            self.load_error = f"snapshot not found: {self.snapshot_path}"
            return None
        try:
            from app.engine.store import EngineStore

            self._dataset = EngineStore(self.snapshot_path).load()
        except Exception as exc:  # noqa: BLE001 - identity enrichment is optional
            self.load_error = f"{type(exc).__name__}: {exc}"
            logger.info("SEC depth snapshot failed: %s", self.load_error)
        return self._dataset

    def _as_of(self, dataset: Any) -> datetime:
        raw = str(getattr(dataset, "fetched_at", "") or "").strip()
        if raw:
            try:
                stamp = datetime.fromisoformat(raw)
                if stamp.tzinfo is not None and stamp.utcoffset() is not None:
                    return stamp
            except ValueError:
                pass
        return self._clock()

    async def fetch_many(
        self, symbols: Sequence[str]
    ) -> dict[str, DepthObservation]:
        dataset = self._load()
        if dataset is None:
            return {}
        master = getattr(dataset, "security_master", None)
        if master is None:
            return {}
        as_of = self._as_of(dataset)
        out: dict[str, DepthObservation] = {}
        for raw_symbol in symbols:
            symbol = canonical_symbol(raw_symbol)
            record = master.get(symbol)
            if record is None:
                continue
            fields: dict[str, Any] = {
                "ticker": record.symbol,
                "company": record.name,
                "security_type": (
                    "ETF" if str(record.asset_class).lower() == "etf" else "Stock"
                ),
            }
            if record.exchange:
                fields["exchange"] = record.exchange
            if record.industry:
                fields["industry"] = record.industry
            if record.country:
                fields["country"] = record.country
            sector = record.sector or classify_sector(record.industry)
            if sector:
                fields["sector"] = sector
            out[symbol] = DepthObservation(
                symbol=symbol,
                fields=fields,
                as_of=as_of,
                source=self.name,
            )
        return out


def build_depth_collector(
    settings: Any,
    symbols: Sequence[str],
    *,
    clock: Callable[[], datetime] | None = None,
    random_source: random.Random | None = None,
) -> DepthCollector:
    """Build SEC + Finviz + StockAnalysis + Yahoo; no request at construction."""

    sources: list[DepthSourceProtocol] = [
        SecDepthSource(
            snapshot_path=getattr(settings, "depth_sec_snapshot", None),
        ),
    ]
    if getattr(settings, "depth_finviz", True):
        sources.append(
            FinvizDepthSource(
                concurrency=int(getattr(settings, "depth_concurrency", 3)),
                delay_s=float(getattr(settings, "depth_delay_s", 0.1)),
            )
        )
    from app.market.yahoo_depth import YahooDepthSource as YahooFullDepthSource

    sources.append(
        YahooFullDepthSource(
            concurrency=int(getattr(settings, "depth_yahoo_concurrency", 2)),
            include_options=not bool(getattr(settings, "depth_finviz", True)),
            request_delay_s=float(getattr(settings, "depth_yahoo_delay_s", 0.25)),
            symbol_timeout_s=float(
                getattr(settings, "depth_yahoo_symbol_timeout_s", 30.0)
            ),
        )
    )
    if getattr(settings, "depth_chart", False):
        sources.append(
            YahooChartDepthSource(
                concurrency=int(getattr(settings, "depth_yahoo_concurrency", 2))
            )
        )
    if getattr(settings, "depth_official", True):
        from app.market.official import OfficialDepthSource

        sources.append(
            OfficialDepthSource(
                concurrency=int(
                    getattr(settings, "depth_official_concurrency", 2)
                ),
                optionable=False,
            )
        )
    sources.append(
        StockAnalysisDepthSource(
            concurrency=int(getattr(settings, "depth_concurrency", 3)),
            delay_s=float(getattr(settings, "depth_delay_s", 0.1)),
        )
    )
    if getattr(settings, "depth_finra", True):
        from app.market.finra import FinraShortSource

        sources.append(FinraShortSource())
    if getattr(settings, "depth_news", False):
        from app.market.news import RssNewsSource

        sources.append(
            RssNewsSource(
                concurrency=int(getattr(settings, "depth_news_concurrency", 8)),
                fallback=True,
            )
        )
    return DepthCollector(
        symbols=symbols,
        sources=sources,
        store_path=getattr(settings, "depth_store_path", DEFAULT_STORE_PATH),
        seed_path=getattr(settings, "depth_store_path", DEFAULT_STORE_PATH),
        refresh_s=float(getattr(settings, "depth_refresh_s", DEPTH_REFRESH_S)),
        jitter_s=float(getattr(settings, "depth_jitter_s", DEPTH_JITTER_S)),
        source_timeout_s=float(getattr(settings, "depth_source_timeout_s", 180.0)),
        source_chunk_size=int(getattr(settings, "depth_source_chunk_size", 250)),
        clock=clock,
        random_source=random_source,
    )


__all__ = [
    "DEFAULT_STORE_PATH",
    "DEPTH_REFRESH_S",
    "DepthCollector",
    "DepthCycleResult",
    "DepthField",
    "DepthObservation",
    "DepthStore",
    "FinvizDepthSource",
    "StockAnalysisDepthSource",
    "YahooDepthSource",
    "YahooChartDepthSource",
    "build_depth_collector",
    "canonical_symbol",
    "parse_stockanalysis_statistics",
    "parse_finviz_extras",
]
