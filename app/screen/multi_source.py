"""Multi-source consensus display (P10): one row per stock, no per-source columns.

``GET /screens`` used to answer with the raw TradingView snapshot. That is a
*selection*, not a verified price: one venue's scanner decided both the order
and every cell. This module turns the same twenty candidates into a **consensus
row** — the median of what the selection source and the live verifiers publish —
and states how much of the evidence agreed.

The rules that make a consensus row trustworthy
-----------------------------------------------

* **One row per stock, never one column group per source.** A caller asking
  "what is AAPL's price" must not have to decide which of ``yahoo.price``,
  ``nasdaq.price`` and ``tradingview.price`` is the answer. The row carries one
  ``price`` (the median) and a ``source_summary`` that says how many sources
  stood behind it and how far apart they were. Per-source cells would also make
  the table's shape depend on which verifiers happened to answer, which is a
  schema that changes with the weather.
* **Median, not mean.** One provider printing a stale or post-market price moves
  a mean; it cannot move a median of three. Ties (even counts) average the two
  middle values, which is the documented ``statistics.median`` behaviour.
* **Missing stays null.** A verifier that publishes no change (Nasdaq's info
  endpoint has no previous close) contributes nothing to the change consensus,
  and a field no source published stays ``None`` — never ``0``. ``source_count``
  records how many sources actually answered, so a one-source row cannot
  masquerade as a consensus.
* **The selection source is a source, not the answer.** TradingView's snapshot
  row is the ``tradingview`` observation. It costs zero extra requests (the scan
  that produced the candidates *is* TradingView), and it keeps a two-source
  region (Global, where Nasdaq is US-only) at a real median instead of a single
  guess.
* **Total failure is not a consensus.** If no verifier answered anything, the
  document is the raw TradingView snapshot with ``source="tradingview"``,
  ``degraded=true`` and the reason in ``verification_error``. Claiming
  ``source="multi"`` over a snapshot nobody verified would be the same lie as an
  empty CSV.

Cache
-----

A verified document is cached for :data:`CACHE_TTL_S` under
``(region, sort, screen_id)``. The screen id is part of the key on purpose: a
refreshed snapshot is a different twenty rows (new ids, new prices), so its
verification must not inherit the previous window's document. A read inside the
window sends **zero** verifier calls — the display is a five-minute picture, and
re-verifying it on every poll would turn a browse tab into a quote terminal.

A degraded document is deliberately **not** cached: a two-second network blip
must not pin a "tradingview only" page for five minutes, so the next request
retries the verification. Only the successful consensus is worth remembering.

Timestamps
----------

``as_of`` on the document and on every row dates the **snapshot** the candidates
came from; ``source_summary.verified_at`` dates the verification pass. Per-source
timestamps are not published, because that would be the per-source columns this
module exists to remove; a caller who needs provenance reads the two timestamps
and the agreement cell.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import math
import statistics
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.config import Settings, get_settings
from app.market.quote import SourceQuote
from app.market.sources import (
    SYMBOL_RE,
    InvalidSymbol,
    NasdaqQuoteSource,
    QuoteSourceError,
    YahooQuoteSource,
)
from app.screen.repository import SqliteScreenRepository, iso
from app.screen.snapshot import load_screen
from app.screen.universe import US_EXCHANGES

#: The document-level ``source`` a verified page publishes. It is deliberately
#: *not* stored in ``screens.source``: the stored snapshot is TradingView's, and
#: a symbols-mode selection resolves ticks against that snapshot and its source.
MULTI_SOURCE = "multi"
#: Where the candidates came from. Always published, on both the consensus page
#: and the degraded one, so "multi" never hides which universe was selected.
SELECTION_SOURCE = "tradingview"
#: The method string a client keys on when it wants to know *how* a row was built.
CONSENSUS_METHOD = "multi_source_consensus"
#: The sources a full consensus can draw on. Coverage is measured against this
#: tuple, so ``confidence`` cannot be inflated by adding verifiers later without
#: also widening the documented set.
VERIFICATION_SOURCES: tuple[str, ...] = ("tradingview", "yahoo", "nasdaq")

#: Same window as the snapshot (PLAN-screen.md §4: 300 s per screen).
CACHE_TTL_S = 300
#: How far a source's change may sit from the median and still count as agreeing.
#: Half a percentage point: live spreads on a liquid name are ~0.01 pp, so this
#: tolerates rounding while flagging a real disagreement.
AGREEMENT_TOLERANCE_PP = 0.5

#: TradingView exchange code (and MIC, where they differ) → Yahoo listing suffix,
#: for the venues whose suffix is not a guess. A listing Yahoo does not know
#: answers empty, which is dropped from the consensus; it is never turned into a
#: price. US venues need no suffix and are handled separately, and a venue absent
#: from this table is not asked at all rather than asked with an invented ticker.
#:
#: ``EURONEXT`` is deliberately absent: the code covers Paris, Amsterdam,
#: Brussels and Lisbon, and one suffix would send a Brussels ticker to Paris —
#: where a *different* company may hold it. A MIC-specific entry (``XPAR``) is
#: unambiguous; the venue group is not. Secondary German venues (``BX``,
#: ``MUN``, ``TRADEGATE``) are absent for the same reason: they print the XETR
#: line of a foreign issuer, which is not a price discovery venue.
YAHOO_SUFFIX_BY_EXCHANGE: Mapping[str, str] = {
    # -- TradingView's own codes (what ``build_universe`` keeps for non-US rows)
    "TSE": ".T",
    "JPX": ".T",
    "HKEX": ".HK",
    "SSE": ".SS",
    "SZSE": ".SZ",
    "TPEX": ".TWO",
    "TWSE": ".TW",
    "KRX": ".KS",
    "KOSDAQ": ".KQ",
    "LSE": ".L",
    "NSE": ".NS",
    "BSE": ".BO",
    "TSX": ".TO",
    "ASX": ".AX",
    "SIX": ".SW",
    "BMV": ".MX",
    "BVMF": ".SA",
    "TADAWUL": ".SR",
    # -- MICs, for a caller that normalised the venue before asking
    "XLON": ".L",
    "XETR": ".DE",
    "XPAR": ".PA",
    "XSWX": ".SW",
    "XAMS": ".AS",
    "XMAD": ".MC",
    "XTKS": ".T",
    "XHKG": ".HK",
    "XSHG": ".SS",
    "XSHE": ".SZ",
    "XNSE": ".NS",
    "XTSE": ".TO",
    "XASX": ".AX",
    "XKRX": ".KS",
    "XTAI": ".TW",
}

#: US venues in both spellings: the MIC that :func:`app.screen.universe.mic_for`
#: stores on a snapshot row, and the TradingView code the scanner prints. A bare
#: ticker is correct for either, so accepting both is robustness, not guessing.
US_VENUES: frozenset[str] = US_EXCHANGES | frozenset({"NASDAQ", "NYSE", "AMEX", "CBOE"})

#: Numeric cells an observation can carry. Used by ``answered`` and by the
#: consensus, so a new field cannot be added in one place and forgotten in the
#: other.
_OBSERVATION_FIELDS: tuple[str, ...] = ("price", "change_pct", "volume", "market_cap_usd")


# ---------------------------------------------------------------------------
# Observations and verifiers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Observation:
    """One source's answer for one symbol, already in screen units.

    ``change_pct`` is a percent (``-0.59`` is a 0.59% decline), ``volume`` is
    shares, ``market_cap_usd`` is dollars. Any cell a provider did not publish is
    ``None``: an observation with no numbers at all is an EMPTY answer, not a
    zero-valued quote.
    """

    source: str
    price: float | None = None
    change_pct: float | None = None
    volume: float | None = None
    market_cap_usd: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("an observation needs a non-empty source name")
        object.__setattr__(self, "source", self.source.strip())
        for name in _OBSERVATION_FIELDS:
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool):
                raise TypeError(f"{name} must be a number, got {value!r}")
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{name} must be a number, got {value!r}") from None
            if not math.isfinite(number):
                raise ValueError(f"{name} must be finite, got {value!r}")
            object.__setattr__(self, name, number)

    @property
    def answered(self) -> bool:
        """Did this source publish at least one number for the symbol?"""

        return any(getattr(self, name) is not None for name in _OBSERVATION_FIELDS)


class Verifier(Protocol):
    """One independent source asked for the twenty displayed symbols.

    ``observe`` receives the candidate rows (screen shape: ``symbol``,
    ``exchange``, ``price``, ...) and returns one answer per symbol it could
    address. A symbol missing from the mapping is "this source does not cover
    it"; ``None`` is "it covers it and has no data". Raising means the source
    could not answer at all, and the service records that as a failure — it never
    becomes a null consensus value dressed up as agreement.
    """

    name: str

    async def observe(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Observation | None]: ...


class CallableVerifier:
    """Adapt a plain (async) function into a :class:`Verifier`.

    This is the injection point for the optional sources: a Finviz or Google
    Finance leg is a function the deployment supplies, not a class this module
    has to know how to scrape. The name is validated like a built-in's, so two
    verifiers can never share a column of the summary.
    """

    def __init__(
        self,
        name: str,
        observe: Callable[
            [Sequence[Mapping[str, Any]]],
            Mapping[str, Observation | None] | Awaitable[Mapping[str, Observation | None]],
        ],
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("a verifier needs a non-empty name")
        if not callable(observe):
            raise TypeError("observe must be callable")
        self.name = name.strip()
        self._observe = observe

    async def observe(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Observation | None]:
        answer = self._observe(rows)
        if inspect.isawaitable(answer):
            answer = await answer
        if not isinstance(answer, Mapping):
            raise TypeError(
                f"verifier {self.name!r} must return a mapping, got {type(answer).__name__}"
            )
        return dict(answer)


def _number(value: Any) -> float | None:
    """A finite float, or ``None``. Never coerces a string or a bool into a price."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        return None
    return number if math.isfinite(number) else None


def _change_pct(quote: SourceQuote) -> float | None:
    """The provider's change in percent; ``None`` without a previous close."""

    if quote.prev_close is None or quote.prev_close <= 0:
        return None
    return (quote.last / quote.prev_close - 1.0) * 100.0


def yahoo_symbol(row: Mapping[str, Any]) -> str | None:
    """The ticker Yahoo needs for one row, or ``None`` when it cannot be known.

    A US listing is the bare ticker; a known foreign venue appends its Yahoo
    suffix. An unknown venue is not asked at all — guessing a suffix would send a
    request for a ticker that does not exist and, worse, could match a *different*
    company if it did.
    """

    symbol = str(row.get("symbol") or "").strip().upper()
    if not symbol or not SYMBOL_RE.match(symbol):
        return None
    exchange = str(row.get("exchange") or "").strip().upper()
    if exchange in US_VENUES:
        return symbol
    suffix = YAHOO_SUFFIX_BY_EXCHANGE.get(exchange)
    if suffix is None:
        return None
    candidate = f"{symbol}{suffix}"
    return candidate if SYMBOL_RE.match(candidate) else None


class YahooVerifier:
    """Yahoo batch quotes: the primary independent check (up to 20 per request)."""

    name = "yahoo"

    def __init__(
        self, *, source: YahooQuoteSource | None = None, timeout_s: float = 10.0
    ) -> None:
        self._source = source or YahooQuoteSource(timeout_s=timeout_s)

    async def observe(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Observation | None]:
        # Provider ticker → row symbol. Two rows that map to one provider ticker
        # (a US line and a same-named foreign line) each get the answer; the row
        # symbol is the key the service addresses.
        wanted: dict[str, str] = {}
        for row in rows:
            provider = yahoo_symbol(row)
            symbol = str(row.get("symbol") or "").strip()
            if provider and symbol and provider not in wanted:
                wanted[provider] = symbol
        if not wanted:
            return {}

        # ``fetch_many`` raises only when every chunk failed; a per-symbol EMPTY
        # answer stays ``None``. That distinction is exactly what the service
        # needs to tell "Yahoo is down" from "Yahoo does not know this ticker".
        answers = await self._source.fetch_many(tuple(wanted))
        observations: dict[str, Observation | None] = {}
        for provider, symbol in wanted.items():
            quote = answers.get(provider)
            observations[symbol] = (
                None
                if quote is None
                else Observation(
                    source=self.name,
                    price=quote.last,
                    change_pct=_change_pct(quote),
                    volume=quote.volume,
                )
            )
        return observations


class NasdaqVerifier:
    """Nasdaq's per-symbol info endpoint; US listings only.

    Nasdaq is a *price* check, not a change check: ``/api/quote/{symbol}/info``
    publishes ``lastSalePrice`` and ``volume`` but no previous close, so its
    observation leaves ``change_pct`` null and the change consensus runs over the
    sources that actually publish one. A symbol Nasdaq does not cover (a foreign
    venue, or "not exists") is an EMPTY answer; only a request-level failure
    counts toward an outage.
    """

    name = "nasdaq"

    def __init__(
        self,
        *,
        source: NasdaqQuoteSource | None = None,
        timeout_s: float = 10.0,
        concurrency: int = 4,
        exchanges: frozenset[str] = US_VENUES,
    ) -> None:
        if not isinstance(concurrency, int) or concurrency < 1:
            raise ValueError(f"concurrency must be an int >= 1, got {concurrency!r}")
        self._source = source or NasdaqQuoteSource(timeout_s=timeout_s)
        self._concurrency = concurrency
        self._exchanges = frozenset(exchanges)

    async def observe(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Observation | None]:
        targets: list[str] = []
        for row in rows:
            if str(row.get("exchange") or "").strip().upper() not in self._exchanges:
                continue
            symbol = str(row.get("symbol") or "").strip().upper()
            if symbol and SYMBOL_RE.match(symbol) and symbol not in targets:
                targets.append(symbol)
        if not targets:
            return {}

        semaphore = asyncio.Semaphore(self._concurrency)
        answers: dict[str, Observation | None] = {}
        failures: list[BaseException] = []

        async def one(symbol: str) -> None:
            async with semaphore:
                try:
                    quote = await self._source.fetch(symbol)
                except InvalidSymbol:
                    # Not a Nasdaq ticker: an answer of "not covered".
                    answers[symbol] = None
                    return
                except Exception as exc:  # noqa: BLE001 - per-symbol failover
                    failures.append(exc)
                    return
                answers[symbol] = (
                    None
                    if quote is None
                    else Observation(
                        source=self.name,
                        price=quote.last,
                        change_pct=_change_pct(quote),
                        volume=quote.volume,
                    )
                )

        await asyncio.gather(*(one(symbol) for symbol in targets))
        if failures and len(failures) == len(targets):
            first = failures[0]
            raise QuoteSourceError(
                f"nasdaq failed for {len(targets)} symbol(s): "
                f"{type(first).__name__}: {first}",
                status=getattr(first, "status", None),
            ) from first
        return {symbol: answers.get(symbol) for symbol in targets}


def build_multi_source_verifiers(settings: Settings | None = None) -> tuple[Verifier, ...]:
    """The default verification set: Yahoo (batch) + Nasdaq (US per-symbol).

    Optional legs (Finviz, Google Finance) are *not* wired here: they are slower,
    bot-walled and carry no extra authority over a price, so a deployment that
    wants them injects a :class:`CallableVerifier` at the composition root. The
    knob ``MEE_SCREEN_MULTI_SOURCE=false`` returns no verifier at all, which makes
    the service answer the honest degraded TradingView document instead of
    claiming a consensus it never checked.
    """

    settings = settings or get_settings()
    if not settings.screen_multi_source:
        return ()
    timeout_s = settings.screen_verify_timeout_s
    yahoo = YahooQuoteSource(batch_size=settings.quote_batch_size, timeout_s=timeout_s)
    nasdaq = NasdaqQuoteSource(timeout_s=timeout_s)
    return (
        YahooVerifier(source=yahoo),
        NasdaqVerifier(source=nasdaq, timeout_s=timeout_s),
    )


# ---------------------------------------------------------------------------
# Consensus
# ---------------------------------------------------------------------------


def selection_observation(row: Mapping[str, Any]) -> Observation:
    """The TradingView leg: the snapshot row the selection scan produced.

    This is not a second request. Re-scanning TradingView for twenty symbols
    would double the display budget to add a datapoint that is, by construction,
    the same endpoint's opinion — the candidate row *is* that opinion, and it is
    the one the ranking already used.
    """

    return Observation(
        source=SELECTION_SOURCE,
        price=_number(row.get("price")),
        change_pct=_number(row.get("change_pct")),
        volume=_number(row.get("volume")),
        market_cap_usd=_number(row.get("market_cap_usd")),
    )


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _short(text: str, limit: int = 300) -> str:
    """One provider's sentence, trimmed: an error cell is a diagnostic, not a dump."""

    collapsed = " ".join(str(text).split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def source_summary(
    observations: Sequence[Observation],
    *,
    verified_at: str,
    tolerance_pp: float = AGREEMENT_TOLERANCE_PP,
) -> dict[str, Any]:
    """How many sources stood behind a row, and how much they agreed.

    ``agreement`` is the share of **change** observations within ``tolerance_pp``
    of the median change: a source that publishes no change (Nasdaq) counts
    toward ``source_count`` but cannot agree or disagree about a number it never
    sent, so it is excluded from that ratio. ``spread_pp`` is the max−min change
    in percentage points (``None`` with fewer than two change observations — a
    spread of one value is not a measurement). ``confidence`` discounts agreement
    by coverage (``source_count / len(VERIFICATION_SOURCES)``), so two of three
    sources in perfect agreement is 0.67, not 1.0.
    """

    counted = [observation for observation in observations if observation.answered]
    changes = [
        observation.change_pct
        for observation in counted
        if observation.change_pct is not None
    ]
    spread_pp: float | None = None
    agreement: float | None = None
    confidence: float | None = None
    if changes:
        middle = statistics.median(changes)
        if len(changes) >= 2:
            spread_pp = round(max(changes) - min(changes), 4)
        agreeing = sum(1 for change in changes if abs(change - middle) <= tolerance_pp)
        agreement = round(agreeing / len(changes), 4)
        coverage = min(1.0, len(counted) / len(VERIFICATION_SOURCES))
        confidence = round(agreement * coverage, 4)
    return {
        "method": CONSENSUS_METHOD,
        "source_count": len(counted),
        "agreement": agreement,
        "spread_pp": spread_pp,
        "confidence": confidence,
        "verified_at": verified_at,
    }


def consensus_row(
    row: Mapping[str, Any],
    observations: Sequence[Observation],
    *,
    as_of: str,
    verified_at: str,
    tolerance_pp: float = AGREEMENT_TOLERANCE_PP,
) -> dict[str, Any]:
    """One stock row: one median per cell, plus the summary — never source columns.

    ``turnover``/``turnover_usd`` stay the snapshot's cells. Deriving a turnover
    from the consensus price and a five-minute-old volume would mix two clocks
    into one number, and PLAN-screen.md §8 #8 forbids inventing a USD turnover
    where FX is missing; the snapshot's own traded value is the auditable cell.
    """

    prices = [o.price for o in observations if o.price is not None]
    changes = [o.change_pct for o in observations if o.change_pct is not None]
    volumes = [o.volume for o in observations if o.volume is not None]
    caps = [o.market_cap_usd for o in observations if o.market_cap_usd is not None]
    return {
        "rank": int(row["rank"]),
        "symbol": row.get("symbol"),
        "name": row.get("name"),
        "exchange": row.get("exchange"),
        "country": row.get("country"),
        "sector": row.get("sector"),
        "price": _median(prices),
        "change_pct": _median(changes),
        "volume": _median(volumes),
        "turnover": row.get("turnover"),
        "turnover_usd": row.get("turnover_usd"),
        "market_cap_usd": _median(caps),
        "is_primary": bool(row.get("is_primary")),
        "as_of": as_of,
        "source_summary": source_summary(
            observations, verified_at=verified_at, tolerance_pp=tolerance_pp
        ),
    }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CacheKey:
    """A verified document is reusable only for this exact question."""

    region: str
    sort: str
    screen_id: str


@dataclass(frozen=True, slots=True)
class _Cached:
    expires_at: datetime
    document: dict[str, Any]


class MultiSourceScreen:
    """Serve one verified document per snapshot, cached for five minutes."""

    def __init__(
        self,
        *,
        repository: SqliteScreenRepository,
        verifiers: Sequence[Verifier] = (),
        cache_ttl_s: int = CACHE_TTL_S,
        tolerance_pp: float = AGREEMENT_TOLERANCE_PP,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if isinstance(cache_ttl_s, bool) or not isinstance(cache_ttl_s, int):
            raise TypeError(f"cache_ttl_s must be an int, got {cache_ttl_s!r}")
        if cache_ttl_s <= 0:
            raise ValueError(f"cache_ttl_s must be > 0, got {cache_ttl_s!r}")
        if tolerance_pp < 0:
            raise ValueError(f"tolerance_pp must be >= 0, got {tolerance_pp!r}")
        names: list[str] = []
        for verifier in verifiers:
            name = str(getattr(verifier, "name", "") or "").strip()
            if not name:
                raise ValueError("every verifier needs a non-empty name")
            if name in (MULTI_SOURCE, SELECTION_SOURCE):
                raise ValueError(
                    f"verifier name {name!r} is reserved; {SELECTION_SOURCE!r} is the "
                    f"selection leg and {MULTI_SOURCE!r} is the document source"
                )
            if name in names:
                raise ValueError(f"duplicate verifier name {name!r}")
            names.append(name)
        self._repository = repository
        self._verifiers: tuple[Verifier, ...] = tuple(verifiers)
        self._ttl_s = cache_ttl_s
        self._tolerance_pp = float(tolerance_pp)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cache: dict[CacheKey, _Cached] = {}
        self._locks: dict[CacheKey, asyncio.Lock] = {}

    @property
    def verifier_names(self) -> tuple[str, ...]:
        return tuple(str(verifier.name) for verifier in self._verifiers)

    # -- reads -------------------------------------------------------------

    async def load(
        self, region: str, sort: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        """The verified document for ``(region, sort)``; cache-first.

        ``load_screen`` still runs on every call: it is the cheap SQLite read
        that makes the 300 s snapshot window real (and refreshes the snapshot
        when it expired). Only the *verification* is cached, because that is the
        part that costs provider requests.
        """

        moment = now or self._clock()
        snapshot = await load_screen(
            region, sort, repository=self._repository, now=moment
        )
        key = CacheKey(
            region=region, sort=sort, screen_id=str(snapshot["screen_id"])
        )
        cached = self._fresh(key, moment)
        if cached is not None:
            return copy.deepcopy(cached)

        # One build per key: concurrent readers of the same expired window wait
        # for the first verification instead of stampeding the providers.
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._fresh(key, moment)
            if cached is not None:
                return copy.deepcopy(cached)
            document = await self._build(snapshot, moment)
            if document.get("source") == MULTI_SOURCE:
                self._cache[key] = _Cached(
                    expires_at=moment + timedelta(seconds=self._ttl_s),
                    document=document,
                )
            else:
                # A degraded answer is retried next time, not remembered.
                self._cache.pop(key, None)
            return copy.deepcopy(document)

    # -- cache -------------------------------------------------------------

    def _fresh(self, key: CacheKey, moment: datetime) -> dict[str, Any] | None:
        for cached_key, entry in list(self._cache.items()):
            if entry.expires_at <= moment:
                self._cache.pop(cached_key, None)
        # A lock is only useful while its key is cached or a build is running.
        # Pruning the empty ones keeps one lock per snapshot ever read from
        # accumulating in a long-lived process (288 a day at a five-minute TTL).
        for cached_key, lock in list(self._locks.items()):
            if cached_key not in self._cache and not lock.locked():
                self._locks.pop(cached_key, None)
        entry = self._cache.get(key)
        if entry is None or entry.expires_at <= moment:
            return None
        return entry.document

    # -- building ----------------------------------------------------------

    async def _build(
        self, snapshot: Mapping[str, Any], moment: datetime
    ) -> dict[str, Any]:
        rows = [dict(row) for row in snapshot.get("rows") or []]
        verified_at = iso(moment)
        observations, failures = await self._observe(rows)

        answered = any(
            observation is not None and observation.answered
            for answers in observations.values()
            for observation in answers.values()
        )
        if not answered:
            return self._degraded(snapshot, failures)

        as_of = str(snapshot["as_of"])
        consensus_rows = [
            self._row(row, observations, as_of=as_of, verified_at=verified_at)
            for row in rows
        ]
        return {
            **snapshot,
            "source": MULTI_SOURCE,
            "selection_source": SELECTION_SOURCE,
            "degraded": False,
            "verification_error": None,
            "returned": len(consensus_rows),
            "rows": consensus_rows,
        }

    def _row(
        self,
        row: Mapping[str, Any],
        observations: Mapping[str, Mapping[str, Observation | None]],
        *,
        as_of: str,
        verified_at: str,
    ) -> dict[str, Any]:
        symbol = str(row.get("symbol") or "").strip()
        legs = [selection_observation(row)]
        for answers in observations.values():
            observation = answers.get(symbol)
            if observation is not None and observation.answered:
                legs.append(observation)
        return consensus_row(
            row,
            legs,
            as_of=as_of,
            verified_at=verified_at,
            tolerance_pp=self._tolerance_pp,
        )

    async def _observe(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> tuple[dict[str, dict[str, Observation | None]], list[str]]:
        """Ask every verifier once; a broken source is a recorded failure."""

        if not rows:
            return {}, ["the snapshot has no rows to verify"]
        if not self._verifiers:
            return {}, [
                "no verifier is configured (MEE_SCREEN_MULTI_SOURCE is off?)"
            ]

        outcomes = await asyncio.gather(
            *(self._ask(verifier, rows) for verifier in self._verifiers),
            return_exceptions=True,
        )
        observations: dict[str, dict[str, Observation | None]] = {}
        failures: list[str] = []
        for verifier, outcome in zip(self._verifiers, outcomes, strict=True):
            name = str(verifier.name)
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            if isinstance(outcome, BaseException):
                failures.append(
                    f"{name}: {type(outcome).__name__}: {_short(str(outcome))}"
                )
                continue
            answers = dict(outcome)
            observations[name] = answers
            if not any(
                observation is not None and observation.answered
                for observation in answers.values()
            ):
                failures.append(f"{name}: answered nothing")
        return observations, failures

    @staticmethod
    async def _ask(
        verifier: Verifier, rows: Sequence[Mapping[str, Any]]
    ) -> dict[str, Observation | None]:
        answer = await verifier.observe(rows)
        if not isinstance(answer, Mapping):
            raise TypeError(
                f"verifier {verifier.name!r} must return a mapping, "
                f"got {type(answer).__name__}"
            )
        clean: dict[str, Observation | None] = {}
        for symbol, observation in answer.items():
            if observation is not None and not isinstance(observation, Observation):
                raise TypeError(
                    f"verifier {verifier.name!r} answered {symbol!r} with "
                    f"{type(observation).__name__}, expected Observation or None"
                )
            clean[str(symbol)] = observation
        return clean

    def _degraded(
        self, snapshot: Mapping[str, Any], failures: Sequence[str]
    ) -> dict[str, Any]:
        """The honest fallback: the TradingView snapshot, labelled as such."""

        document = dict(snapshot)
        # The fallback states the selection source it actually is; if a caller
        # ever seeds a snapshot from another provider, the document must not
        # relabel it as TradingView.
        source = str(snapshot.get("source") or SELECTION_SOURCE)
        document["source"] = source
        document["selection_source"] = source
        document["degraded"] = True
        document["verification_error"] = (
            "multi-source verification failed: "
            + ("; ".join(failures) if failures else "no observation was returned")
        )
        return document


__all__ = [
    "AGREEMENT_TOLERANCE_PP",
    "CACHE_TTL_S",
    "CONSENSUS_METHOD",
    "MULTI_SOURCE",
    "SELECTION_SOURCE",
    "US_VENUES",
    "VERIFICATION_SOURCES",
    "YAHOO_SUFFIX_BY_EXCHANGE",
    "CacheKey",
    "CallableVerifier",
    "MultiSourceScreen",
    "NasdaqVerifier",
    "Observation",
    "Verifier",
    "YahooVerifier",
    "build_multi_source_verifiers",
    "consensus_row",
    "selection_observation",
    "source_summary",
    "yahoo_symbol",
]
