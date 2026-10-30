"""SEC bulk fundamentals as a depth source: the keyless fast path's backbone.

A keyless run has no per-symbol fundamentals provider. It does have EDGAR's
**financial statement data sets**: one ZIP per quarter holding every filer's
tagged numbers, cached on disk. This module is the depth adapter over that
cache — :func:`app.engine.bulk_financials.build_quarters` streams the ZIPs into
a :class:`~app.engine.bulk_financials.FactIndex`,
:func:`app.market.sec_fundamentals.fundamentals_for_symbol` maps one issuer's
facts onto the catalog's seventeen fundamentals columns, and
:class:`SecFundamentalsDepthSource` publishes the result as
:class:`~app.market.depth.DepthObservation` rows.

Four properties are the whole design:

* **Construction is free.** Four quarters of ``num.txt`` is ~3.5 GB of TSV to
  stream, so the index is built on the **first** ``fetch_many`` — inside a
  worker thread, so the collector's event loop (and its chunk deadlines) stay
  alive — and then reused for every later chunk. Building it here would make
  ``build_depth_collector`` block for minutes.
* **A missing cache is silence, not an exception.** No cached quarter, no
  snapshot, no CIK map: the source contributes no rows and the cycle keeps
  every other source. Nothing here downloads; the cache is somebody else's
  contract (the engine's snapshot job).
* **Price is the caller's.** ``market_cap`` and ``pe`` need a quote, so the
  source takes a price reference — a mapping or a (sync or async) callable —
  and leaves both fields absent when it cannot answer. A value that is not a
  positive finite number is ignored rather than allowed to raise inside a
  chunk.
* **A share count is filed first, referenced second.** A multi-class filer
  (META, GOOGL, BRK.B) tags ``CommonStockSharesOutstanding`` only on a
  ``ClassOfStock`` axis, so the filing carries no consolidated count and the
  three price-dependent columns cannot resolve. The source takes the same
  shape of reference for shares — the Yahoo v7 batch ``sharesOutstanding`` —
  and the mapper uses it only when the filing has no count at all.

The module also carries the three keyless reference maps the collector wires:
:func:`keyless_price_reference` (CNBC batch quotes for ``market_cap``/``pe``),
:func:`shares_reference` (the Yahoo v7 batch ``sharesOutstanding`` a
multi-class filer never files as a consolidated count) and
:func:`average_volume_reference` (the Yahoo v7 batch 3-month average volume
that FINRA's ``short_ratio`` divides by). They live here, next to the source
that consumes them, because the collector's wiring function is the only place
allowed to name them.

Contract: ``briefs/market-engine`` keyless plan, phase 3.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import os
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from itertools import batched
from pathlib import Path
from typing import Any

from app.engine.bulk_financials import (
    FactIndex,
    build_quarters,
    current_quarter,
    dataset_cache_path,
    parse_quarter,
    quarter_label,
)
from app.market.depth import DepthObservation, canonical_symbol
from app.market.sec_fundamentals import fundamentals_for_symbol

logger = logging.getLogger("mee.depth.sec_bulk")

#: How many cached quarters the default discovery keeps. A mid-year filer's
#: annual row lives in the quarter that carried its 10-K while its
#: year-to-date pair lives in the quarter that carried its 10-Q, so one
#: quarter is never enough for the TTM arithmetic; four covers a 10-K plus
#: three 10-Qs.
DEFAULT_QUARTER_COUNT = 4

#: How far back the discovery walks when the newest quarters are not cached
#: yet (a fresh deployment, or the first days of a new quarter).
DEFAULT_QUARTER_LOOKBACK = 8

#: A quote — or a share count — is a positive finite number, or it is not one.
PriceReference = Mapping[str, Any] | Callable[[], Any] | None

#: The v7 columns that answer "how many shares trade in a day on average".
#: FINRA's ratio is documented against the 3-month figure.
_AVERAGE_VOLUME_KEYS = ("averageDailyVolume3Month", "averageDailyVolume")

#: The v7 column that answers "how many shares are outstanding". It is the
#: fallback for class-share filers, whose XBRL count is dimensional only.
_SHARES_OUTSTANDING_KEYS = ("sharesOutstanding",)

#: Asked explicitly so the column arrives even if Yahoo honours ``fields``.
_VOLUME_FIELDS = "symbol,regularMarketTime,averageDailyVolume3Month"
_SHARES_FIELDS = "symbol,regularMarketTime,sharesOutstanding"

#: A crumb is a short URL-safe token; anything else is a challenge page.
_CRUMB_RE = re.compile(r"^[A-Za-z0-9._~-]{1,32}$")

#: Characters that are legal in a provider symbol but must be escaped in a
#: query string (the same two :mod:`app.market.sources` escapes).
_QUERY_ESCAPES = {"^": "%5E", "=": "%3D"}

#: One v7 batch request answers at most this many symbols (verified live).
_YAHOO_BATCH_SIZE = 400


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _recent_quarters(count: int, lookback: int) -> tuple[str, ...]:
    """The ``lookback`` newest dataset labels, current quarter first."""
    year, number = parse_quarter(current_quarter())
    index = year * 4 + (number - 1)
    labels: list[str] = []
    for step in range(max(int(lookback), int(count))):
        y, q = divmod(index - step, 4)
        labels.append(quarter_label(y, q + 1))
    return tuple(labels)


def _quote(value: Any) -> float | None:
    """A usable price, or ``None``. A string, a bool or a non-positive number
    is not a quote and must not reach the mapper (it would raise)."""
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0 else None


def _price_map(raw: Any) -> dict[str, float]:
    """``{symbol: positive number}``, canonical keys, usable values only.

    One validator serves both reference maps: a quote and a share count are
    both positive finite numbers, and a string, a bool, a zero or a negative
    is neither. Filtering here keeps a bad reference from raising inside a
    chunk (the mapper would refuse it) and from pricing an issuer at zero.
    """
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        symbol = canonical_symbol(key)
        number = _quote(value)
        if symbol and number is not None:
            out.setdefault(symbol, number)
    return out


def _cik_map(raw: Mapping[str, Any]) -> dict[str, str]:
    """``{canonical symbol: CIK}``; a row without either half is dropped."""
    out: dict[str, str] = {}
    for key, value in raw.items():
        symbol = canonical_symbol(key)
        cik = str(value or "").strip()
        if symbol and cik:
            out.setdefault(symbol, cik)
    return out


def _reference_state(reference: PriceReference, ttl_s: float | None) -> dict[str, Any]:
    """One lazily resolved reference map's state: its source, ttl and cache.

    The two references (``price``, ``shares``) differ only in which column they
    fill, so they share one resolver and one state shape.
    """

    return {
        "reference": reference,
        "ttl_s": float(ttl_s) if ttl_s else 0.0,
        "values": {},
        "at": None,
    }


class SecFundamentalsDepthSource:
    """The seventeen fundamentals columns, read from cached SEC bulk quarters.

    ``snapshot_path`` (or an injected ``cik_by_symbol``) owns symbol → CIK;
    ``cache_dir`` owns the quarterly ZIPs; ``price`` owns the quote the two
    price-dependent fields need and ``shares`` the count a multi-class filer
    never files as a total. Every one of them is resolved lazily and every one
    of them may be absent — the source then contributes no rows.
    """

    name = "sec_bulk"

    def __init__(
        self,
        *,
        snapshot_path: str | Path | None = None,
        cache_dir: str | Path | None = None,
        quarters: Sequence[str] | None = None,
        cik_by_symbol: Mapping[str, Any] | None = None,
        price: PriceReference = None,
        price_ttl_s: float | None = None,
        shares: PriceReference = None,
        shares_ttl_s: float | None = None,
        quarter_count: int = DEFAULT_QUARTER_COUNT,
        quarter_lookback: int = DEFAULT_QUARTER_LOOKBACK,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        raw = (
            snapshot_path
            or os.environ.get("MEE_ENGINE_SNAPSHOT")
            or "data/engine-snapshot.json"
        )
        self.snapshot_path = Path(raw).expanduser()
        self.cache_dir = Path(cache_dir).expanduser() if cache_dir is not None else None
        self.quarters = tuple(str(item) for item in quarters) if quarters else None
        self.quarter_count = max(1, int(quarter_count))
        self.quarter_lookback = max(1, int(quarter_lookback))
        self._clock = clock or _utcnow
        #: The lazily resolved reference maps, one state box per kind. ``price``
        #: multiplies the share count; ``shares`` stands in for a count the
        #: filing never carried as a total (META, GOOGL, BRK.B).
        self._references: dict[str, dict[str, Any]] = {
            "price": _reference_state(price, price_ttl_s),
            "shares": _reference_state(shares, shares_ttl_s),
        }
        self._injected_ciks = _cik_map(cik_by_symbol) if cik_by_symbol else None
        self._index: FactIndex | None = None
        self._build_attempted = False
        self.build_error: str | None = None
        #: Quarters the configuration named that had no cached ZIP. A subset of
        #: the requested window is a smaller answer, not an error — so it is
        #: reported here instead of failing the cycle.
        self.missing_quarters: tuple[str, ...] = ()
        self._ciks: dict[str, str] | None = None
        self.cik_error: str | None = None

    # -- observable state ---------------------------------------------------

    @property
    def fact_index(self) -> FactIndex | None:
        """The merged index, or ``None`` until the first fetch builds it."""
        return self._index

    # -- the CIK map --------------------------------------------------------

    def _resolve_ciks(self) -> dict[str, str]:
        """Symbol → CIK: the injected map, else the engine snapshot's master."""
        if self._ciks is not None:
            return self._ciks
        self._ciks = {}
        if self._injected_ciks is not None:
            self._ciks = dict(self._injected_ciks)
            return self._ciks
        if not self.snapshot_path.exists():
            self.cik_error = f"snapshot not found: {self.snapshot_path}"
            return self._ciks
        try:
            from app.engine.store import EngineStore

            dataset = EngineStore(self.snapshot_path).load()
            master = getattr(dataset, "security_master", None)
            records = master.records() if master is not None else ()
            self._ciks = _cik_map(
                {
                    record.symbol: record.cik
                    for record in records
                    if getattr(record, "cik", None)
                }
            )
        except Exception as exc:  # noqa: BLE001 - identity enrichment is optional
            self.cik_error = f"{type(exc).__name__}: {exc}"
            # An optional rung must not crash the cycle, but it must not vanish
            # either: a snapshot whose digest no longer matches fails closed on
            # every read, so this source silently returns nothing while the
            # store keeps serving its seed. That looked like a healthy
            # deployment with three-day-old numbers. WARNING, and name the fix.
            logger.warning(
                "sec bulk depth snapshot failed: %s (if the dataset is intact "
                "but the digest is stale, run: python -m app.engine.ops reseal "
                "--snapshot %s)",
                self.cik_error,
                self.snapshot_path,
            )
        return self._ciks

    # -- the quarterly cache ------------------------------------------------

    def _cached_quarters(self) -> tuple[str, ...]:
        """The configured (or discovered) quarters that actually have a ZIP.

        Records the requested-but-absent ones in :attr:`missing_quarters`,
        because a hole in the middle is the dangerous case: the index builds
        from the quarters it found, serves a shorter window, and looks healthy.
        A growth CAGR from a gapped window is subtly wrong rather than missing.
        """
        candidates = self.quarters or _recent_quarters(
            self.quarter_count, self.quarter_lookback
        )
        found: list[str] = []
        missing: list[str] = []
        for quarter in candidates:
            try:
                path = dataset_cache_path(self.cache_dir, quarter)
            except ValueError:  # a malformed label is not a cached quarter
                continue
            if path.is_file():
                found.append(quarter)
            else:
                missing.append(str(quarter))
        self.missing_quarters = tuple(missing)
        if missing and found:
            logger.warning(
                "sec bulk depth is missing cached quarter(s) %s; the index is "
                "built from %s and any window spanning the hole is unreliable",
                ", ".join(missing),
                ", ".join(found),
            )
        return tuple(found)

    async def _ensure_index(self) -> FactIndex | None:
        """Build the index once, on a worker thread, or explain why not."""
        if self._build_attempted:
            return self._index
        self._build_attempted = True
        ciks = self._resolve_ciks()
        if not ciks:
            self.build_error = self.cik_error or "no symbol → CIK map is available"
            return None
        quarters = self._cached_quarters()
        if not quarters:
            root = self.cache_dir if self.cache_dir is not None else "the engine cache"
            self.build_error = f"no cached SEC bulk quarter under {root}"
            logger.info(
                "sec bulk depth found no cached quarter under %s (walked %s)",
                root,
                self.quarters or _recent_quarters(self.quarter_count, self.quarter_lookback),
            )
            return None
        paths = [dataset_cache_path(self.cache_dir, quarter) for quarter in quarters]
        wanted = sorted(set(ciks.values()))
        try:
            # The stream is minutes of CPU/IO; a worker thread keeps the
            # collector's loop (and its chunk deadlines) alive meanwhile.
            self._index = await asyncio.to_thread(
                build_quarters, paths, ciks=wanted
            )
        except Exception as exc:  # noqa: BLE001 - a broken cache is no rows
            self.build_error = f"{type(exc).__name__}: {exc}"
            logger.info("sec bulk depth index failed: %s", self.build_error)
            self._index = None
        return self._index

    # -- the reference maps -------------------------------------------------

    async def _resolve(self, kind: str) -> dict[str, float]:
        """One reference map, re-resolved at most once per its ttl.

        ``kind`` is ``"price"`` or ``"shares"``. A source that fails is logged
        and **not** memoised, so the next chunk tries again — one dead batch
        must not silence a whole cycle's quotes.
        """
        state = self._references[kind]
        now = time.monotonic()
        if state["at"] is not None and (
            state["ttl_s"] <= 0 or now - state["at"] < state["ttl_s"]
        ):
            return state["values"]
        reference = state["reference"]
        if reference is None:
            state["values"], state["at"] = {}, now
            return state["values"]
        try:
            raw = reference() if callable(reference) else reference
            if inspect.isawaitable(raw):
                raw = await raw
        except Exception as exc:  # noqa: BLE001 - a reference is optional
            logger.info("sec bulk depth %s reference failed: %s", kind, exc)
            return {}  # not memoised: the next chunk tries again
        state["values"], state["at"] = _price_map(raw), now
        return state["values"]

    async def _resolve_prices(self) -> dict[str, float]:
        """The quote map the ``market_cap``/``pe`` columns stand on."""
        return await self._resolve("price")

    async def _resolve_shares(self) -> dict[str, float]:
        """The reference share count multi-class filers do not file as a total."""
        return await self._resolve("shares")

    # -- the protocol -------------------------------------------------------

    async def fetch_many(
        self, symbols: Sequence[str]
    ) -> dict[str, DepthObservation]:
        wanted = [canonical_symbol(symbol) for symbol in symbols]
        wanted = [symbol for symbol in wanted if symbol]
        if not wanted:
            return {}
        index = await self._ensure_index()
        if index is None:
            return {}
        ciks = self._resolve_ciks()
        if not ciks:
            return {}
        prices = await self._resolve_prices()
        share_counts = await self._resolve_shares()
        as_of = self._clock()
        out: dict[str, DepthObservation] = {}
        for symbol in wanted:
            try:
                fields = fundamentals_for_symbol(
                    index,
                    symbol,
                    ciks,
                    price=prices.get(symbol),
                    # The filing's own count wins inside the mapper; this is
                    # the fallback for the class-share filers that file none.
                    shares=share_counts.get(symbol),
                )
            except (TypeError, ValueError) as exc:  # defensive: never sink a chunk
                logger.info("sec bulk depth failed for %s: %s", symbol, exc)
                continue
            present = {
                name: value for name, value in fields.items() if value is not None
            }
            if present:
                out[symbol] = DepthObservation(
                    symbol=symbol,
                    fields=present,
                    as_of=as_of,
                    source=self.name,
                )
        return out


# ---------------------------------------------------------------------------
# The keyless reference maps the collector wires
# ---------------------------------------------------------------------------


def _memoized(fetch: Callable[[], Any], ttl_s: float) -> Callable[[], Any]:
    """Call the **synchronous** ``fetch`` at most once per ``ttl_s`` window.

    The collector asks a source chunk by chunk; a reference map that covers
    the whole universe must not be re-fetched once per chunk. Only ever wrap
    a plain function here — memoising a coroutine would hand the same spent
    awaitable to the second caller.
    """
    state: dict[str, Any] = {"at": None, "value": None}

    def cached() -> Any:
        now = time.monotonic()
        if state["at"] is not None and (ttl_s <= 0 or now - state["at"] < ttl_s):
            return state["value"]
        value = fetch()
        state["at"], state["value"] = now, value
        return value

    return cached


def keyless_price_reference(symbols: Sequence[str]) -> Callable[[], Any]:
    """CNBC-batch prices for ``market_cap``/``pe``; no key, no per-symbol call.

    The registry is built on the first call (never at construction). How
    often the map is refreshed is the *source's* ``price_ttl_s``, so one
    depth cycle pays one batch for the whole cycle instead of one per chunk.
    """
    wanted = tuple(symbols)
    registry: dict[str, Any] = {}

    async def reference() -> dict[str, float]:
        from app.market.price import CnbcPriceSource, NasdaqPriceSource, PriceRegistry

        if "registry" not in registry:
            registry["registry"] = PriceRegistry(
                primary=CnbcPriceSource(), fallback=NasdaqPriceSource()
            )
        quotes = await registry["registry"].quotes(wanted)
        return {
            symbol: quote.last for symbol, quote in quotes.items() if quote is not None
        }

    return reference


def _run_sync(coroutine: Any) -> dict[str, float]:
    """Run ``coroutine`` to completion on a private loop in a worker thread.

    The FINRA source asks for its reference map **synchronously**, from inside
    the collector's own event loop, where ``asyncio.run`` would refuse. A
    worker thread owns a fresh loop, so the batch still goes through the one
    HTTP seam (and the mock transport in tests) without touching a socket.
    """
    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = asyncio.run(coroutine)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            box["error"] = exc

    thread = threading.Thread(target=runner, name="sec-depth-yahoo-volume")
    try:
        thread.start()
    except BaseException:
        coroutine.close()  # a never-awaited coroutine is a warning, and warnings are errors
        raise
    thread.join()
    if "error" in box:
        raise box["error"]
    value = box.get("value")
    return dict(value) if isinstance(value, Mapping) else {}


async def _yahoo_crumb(client: Any) -> str:
    """Warm the cookie jar, then exchange it for a crumb (``""`` on refusal)."""
    from app.market.sources import YAHOO_COOKIE_URL, YAHOO_CRUMB_PATH, YAHOO_HOST

    try:
        await client.get(YAHOO_COOKIE_URL)
        response = await client.get(f"{YAHOO_HOST}{YAHOO_CRUMB_PATH}")
        token = str(getattr(response, "text", "") or "").strip()
    except Exception as exc:  # noqa: BLE001 - warm-up must not become the error
        logger.info("yahoo average volume handshake failed: %s", exc)
        return ""
    return token if _CRUMB_RE.match(token) else ""


def _result_rows(payload: Any) -> list[Mapping[str, Any]]:
    envelope = payload.get("quoteResponse") if isinstance(payload, Mapping) else None
    rows = envelope.get("result") if isinstance(envelope, Mapping) else None
    if not isinstance(rows, (list, tuple)):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _positive_number(row: Mapping[str, Any], keys: Sequence[str]) -> float | None:
    """The first usable positive finite number under ``keys``, or ``None``.

    Both reference columns are counts of the same kind — a volume and an
    outstanding-share total — so a string, a bool, a zero or a negative is not
    an answer for either.
    """

    for key in keys:
        value = row.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number > 0:
            return number
    return None


def _average_volume(row: Mapping[str, Any]) -> float | None:
    return _positive_number(row, _AVERAGE_VOLUME_KEYS)


def _shares_outstanding(row: Mapping[str, Any]) -> float | None:
    return _positive_number(row, _SHARES_OUTSTANDING_KEYS)


async def _fetch_yahoo_numbers(
    symbols: Sequence[str],
    *,
    fields: str,
    pick: Callable[[Mapping[str, Any]], float | None],
    timeout_s: float,
) -> dict[str, float]:
    """The v7 batch column ``pick`` reads, per symbol; ``{}`` when unavailable.

    One cookie/crumb handshake, then one request per
    :data:`_YAHOO_BATCH_SIZE` symbols. A chunk that fails is skipped: the
    column it would have fed is absent, never zero.
    """
    from app.market.sources import (
        BROWSER_USER_AGENT,
        YAHOO_HOST,
        YAHOO_QUOTE_PATH,
        clean_symbols,
    )
    from app.providers import http as provider_http

    wanted = clean_symbols(symbols)
    if not wanted:
        return {}
    headers = {
        "user-agent": BROWSER_USER_AGENT,
        "accept": "application/json,text/plain,*/*",
        "accept-language": "en-US,en;q=0.9",
    }
    out: dict[str, float] = {}
    try:
        async with provider_http.build_client(
            timeout_s=timeout_s, headers=headers
        ) as client:
            crumb = await _yahoo_crumb(client)
            for chunk in batched(wanted, _YAHOO_BATCH_SIZE):
                joined = ",".join(
                    "".join(_QUERY_ESCAPES.get(character, character) for character in symbol)
                    for symbol in chunk
                )
                url = (
                    f"{YAHOO_HOST}{YAHOO_QUOTE_PATH}"
                    f"?symbols={joined}&fields={fields}"
                )
                if crumb:
                    url = f"{url}&crumb={crumb}"
                try:
                    response = await client.get(url)
                    if int(getattr(response, "status_code", 0) or 0) != 200:
                        continue
                    payload = response.json()
                except Exception as exc:  # noqa: BLE001 - one chunk is optional
                    logger.info("yahoo batch column chunk failed: %s", exc)
                    continue
                for row in _result_rows(payload):
                    symbol = str(row.get("symbol") or "").strip().upper()
                    number = pick(row)
                    if symbol and number is not None:
                        out.setdefault(symbol, number)
    except Exception as exc:  # noqa: BLE001 - the reference is optional
        logger.info("yahoo batch column reference failed: %s", exc)
        return {}
    return out


async def fetch_yahoo_average_volume(
    symbols: Sequence[str], *, timeout_s: float = 10.0
) -> dict[str, float]:
    """The v7 batch 3-month average volume per symbol; ``{}`` when unavailable."""
    return await _fetch_yahoo_numbers(
        symbols,
        fields=_VOLUME_FIELDS,
        pick=_average_volume,
        timeout_s=timeout_s,
    )


async def fetch_yahoo_shares_outstanding(
    symbols: Sequence[str], *, timeout_s: float = 10.0
) -> dict[str, float]:
    """The v7 batch ``sharesOutstanding`` per symbol; ``{}`` when unavailable.

    A symbol Yahoo leaves out of the response stays absent: the caller's
    mapper then keeps the filed count it already had, or reports no market cap
    at all. This is a fallback, never a replacement.
    """
    return await _fetch_yahoo_numbers(
        symbols,
        fields=_SHARES_FIELDS,
        pick=_shares_outstanding,
        timeout_s=timeout_s,
    )


def yahoo_average_volume(
    symbols: Sequence[str], *, timeout_s: float = 10.0
) -> dict[str, float]:
    """The synchronous face of :func:`fetch_yahoo_average_volume`."""
    try:
        return _run_sync(fetch_yahoo_average_volume(symbols, timeout_s=timeout_s))
    except Exception as exc:  # noqa: BLE001 - a missing divisor is an absent ratio
        logger.info("yahoo average volume reference failed: %s", exc)
        return {}


def yahoo_shares_outstanding(
    symbols: Sequence[str], *, timeout_s: float = 10.0
) -> dict[str, float]:
    """The synchronous face of :func:`fetch_yahoo_shares_outstanding`."""
    try:
        return _run_sync(
            fetch_yahoo_shares_outstanding(symbols, timeout_s=timeout_s)
        )
    except Exception as exc:  # noqa: BLE001 - a missing count is an absent field
        logger.info("yahoo shares reference failed: %s", exc)
        return {}


def shares_reference(
    symbols: Sequence[str], *, ttl_s: float = 0.0
) -> Callable[[], dict[str, float]]:
    """The share-count fallback, as the source's callable reference."""
    wanted = tuple(symbols)
    return _memoized(lambda: yahoo_shares_outstanding(wanted), ttl_s)


def average_volume_reference(
    symbols: Sequence[str], *, ttl_s: float = 0.0
) -> Callable[[], dict[str, float]]:
    """The FINRA ``short_ratio`` divisor, as the source's callable reference."""
    wanted = tuple(symbols)
    return _memoized(lambda: yahoo_average_volume(wanted), ttl_s)


__all__ = [
    "DEFAULT_QUARTER_COUNT",
    "DEFAULT_QUARTER_LOOKBACK",
    "SecFundamentalsDepthSource",
    "average_volume_reference",
    "fetch_yahoo_average_volume",
    "fetch_yahoo_shares_outstanding",
    "keyless_price_reference",
    "shares_reference",
    "yahoo_average_volume",
    "yahoo_shares_outstanding",
]
