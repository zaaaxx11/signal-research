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

logger = logging.getLogger("mee.depth")

DEPTH_REFRESH_S = 86_400.0
DEPTH_JITTER_S = 1_800.0
DEFAULT_STORE_PATH = Path("data/depth.json")

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
}


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
        object.__setattr__(
            self,
            "fields",
            {canonical_field(field): value for field, value in self.fields.items()},
        )


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
                if observation.as_of < current.as_of:
                    continue
                if (
                    observation.as_of == current.as_of
                    and priority > current.priority
                ):
                    continue
            bucket[name] = DepthField(
                value=value,
                as_of=observation.as_of,
                source=observation.source,
                priority=priority,
            )
            changed += 1
        return changed

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


class DepthCollector:
    """Collect every depth source and merge by field."""

    def __init__(
        self,
        *,
        symbols: Sequence[str],
        sources: Sequence[DepthSourceProtocol],
        store_path: str | Path | None = DEFAULT_STORE_PATH,
        refresh_s: float = DEPTH_REFRESH_S,
        jitter_s: float = DEPTH_JITTER_S,
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
        self.symbols = tuple(ordered)
        self.sources = tuple(sources)
        self.store_path = Path(store_path) if store_path is not None else None
        self.refresh_s = float(refresh_s)
        self.jitter_s = float(jitter_s)
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
        self.started = True
        self._stop.clear()
        self._tasks = [
            asyncio.create_task(self._loop(), name="depth-collector"),
        ]

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

    async def refresh(self) -> DepthCycleResult:
        started_at = self._clock()
        started = time.perf_counter()
        observations: dict[str, list[tuple[int, DepthObservation]]] = {}
        messages: list[str] = []
        errors = 0

        for priority, source in enumerate(self.sources):
            try:
                fetched = source.fetch_many(self.symbols)
                result = await fetched if inspect.isawaitable(fetched) else fetched
                if not isinstance(result, Mapping):
                    raise TypeError("fetch_many did not return a mapping")
            except Exception as exc:  # noqa: BLE001 - one source is not a cycle
                errors += 1
                message = f"{source.name}: {type(exc).__name__}: {exc}"
                messages.append(message)
                self.last_error = message
                logger.warning("depth source failed: %s", message)
                continue
            for symbol, observation in result.items():
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
        await self.persist()
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
    "EPS (Diluted)": "eps_ttm",
    "Revenue": "revenue_ttm",
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
        semaphore = asyncio.Semaphore(self.concurrency)
        out: dict[str, DepthObservation] = {}

        async def one(symbol: str) -> None:
            async with semaphore:
                try:
                    instrument = InstrumentRef.from_symbol(symbol)
                    raw = await adapter.fetch(
                        Capability.FUNDAMENTALS, instrument, set()
                    )
                    verdict = adapter.verdict(raw, instrument)
                    if not getattr(verdict, "ok", False):
                        return
                    observations = adapter.parse(raw, instrument)
                    fields: dict[str, Any] = {}
                    stamps: list[datetime] = []
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
                        raw_stamp = observation.get("as_of")
                        if raw_stamp:
                            try:
                                stamps.append(datetime.fromisoformat(str(raw_stamp)))
                            except ValueError:
                                pass
                    if fields:
                        out[canonical_symbol(symbol)] = DepthObservation(
                            symbol=symbol,
                            fields=fields,
                            as_of=max(stamps) if stamps else datetime.now(UTC),
                            source=self.name,
                        )
                except Exception as exc:  # noqa: BLE001 - one symbol is optional
                    logger.info("yahoo depth failed for %s: %s", symbol, exc)

        await asyncio.gather(*(one(symbol) for symbol in symbols))
        return out


def build_depth_collector(
    settings: Any,
    symbols: Sequence[str],
    *,
    clock: Callable[[], datetime] | None = None,
    random_source: random.Random | None = None,
) -> DepthCollector:
    """Build Finviz + StockAnalysis + Yahoo; no request at construction."""

    sources = [
        FinvizDepthSource(
            concurrency=int(getattr(settings, "depth_concurrency", 3)),
            delay_s=float(getattr(settings, "depth_delay_s", 0.1)),
        ),
        StockAnalysisDepthSource(
            concurrency=int(getattr(settings, "depth_concurrency", 3)),
            delay_s=float(getattr(settings, "depth_delay_s", 0.1)),
        ),
        YahooDepthSource(
            concurrency=int(getattr(settings, "depth_yahoo_concurrency", 2))
        ),
    ]
    return DepthCollector(
        symbols=symbols,
        sources=sources,
        store_path=getattr(settings, "depth_store_path", DEFAULT_STORE_PATH),
        refresh_s=float(getattr(settings, "depth_refresh_s", DEPTH_REFRESH_S)),
        jitter_s=float(getattr(settings, "depth_jitter_s", DEPTH_JITTER_S)),
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
    "build_depth_collector",
    "canonical_symbol",
    "parse_stockanalysis_statistics",
]
