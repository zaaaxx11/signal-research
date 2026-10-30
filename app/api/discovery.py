"""``GET /market/breadth`` and ``GET /symbols/search`` — the two market-wide reads.

Why these routes exist
----------------------
The interface needs two numbers the engine does not publish. The old UI computed
"advancing vs declining" in JavaScript over the <= 20 rows it happened to be
showing, which is a statement about a page, not about a market. Breadth is a
property of the *universe*, so it is counted where the universe lives.

Both routes are read-only and additive: no existing response changes, and a
client that never calls them sees the API it had before.

Breadth: one scan, one pass, honest denominators
------------------------------------------------
The screener already knows how to build the universe a screen filters over
(``app.screen.universe.build_universe``): ``type=stock``, no cap floor, no
subtype gate, OTC out, one row per issuer. That is the row set breadth counts,
so the panel and the screener can never disagree about what "the market" is.

One scan builds it (~1-2 s, network) and one pass counts everything: the three
buckets and every sector bucket are filled in the same loop. Nineteen sector
averages must not cost nineteen scans of 5,600 rows.

Three decisions the document states instead of hiding:

* **A flat row is ``unchanged``.** ``change_pct == 0.0`` (or ``-0.0``, or the
  integer/string spelling of zero) is not a decline; the comparison is written
  as explicit ``> 0`` / ``< 0`` branches so a truthiness shortcut cannot flip it.
* **A row with no ``change_pct`` is still in the partition.** ``advancing +
  declining + unchanged == universe_total`` is the number a progress bar
  needs, so a missing change is counted in ``unchanged`` — and reported
  separately as ``unchanged_missing``, because "we did not measure it" is not
  "it did not move".
* **A sector average excludes missing changes.** The mean is over the rows of
  that sector that carry a numeric change; those rows still contribute to
  ``count``. A sector with no measurable row answers ``avg_change_pct: null``,
  never ``0.0`` — zero would be a fabricated "flat". ``method`` in the response
  says this in words, next to the denominators.

``new_highs``/``new_lows`` are only measurable on rows carrying both a price and
the 52-week extreme. ``new_highs_denominator``/``new_lows_denominator`` publish
that row count, so a caller can tell "no new highs today" from "this deployment
never measured new highs".

Sector buckets partition the rows: a row the provider did not label lands in one
bucket whose ``name`` is ``null`` (a blank label is unknown, not a label), and
``sector_missing`` reports how large that tail is.

Search: the identity snapshot, not the depth store
--------------------------------------------------
``GET /symbols/search`` reads the 10k-row identity master — the authoritative
list of listed securities — because the depth store is an enrichment cache over
a subset, and a symbol missing from it is not a symbol that does not exist. The
projection is built once per master and reused (an index, not a scan), and
ranking is a single ordered pass:

1. exact symbol (case-insensitive),
2. symbol prefix,
3. company-name prefix,
4. company-name contains.

Within a tier the order is ``(symbol length, symbol)``: the tightest symbol
match first, then alphabetical, so the same query answers the same page twice.
``limit`` is bounded at 20 — a lookup must not become a universe dump — and a
query that is missing or only whitespace is a 422 that names ``q`` rather than
a silently empty page.

An outage costs one scan, not one per request
---------------------------------------------
A *warm* failure already served the last snapshot, marked stale, and deferred
the next attempt by a TTL. A *cold* one had nothing to serve, so every request
re-raised after buying its own 5,700-row scan: five panels opening during a bot
wall cost five scans of an endpoint that had just refused us. The cache now
remembers the failure for :data:`UNIVERSE_FAILURE_TTL_S` (five seconds) and
answers ``503 scanner_unavailable`` from memory inside that window — the same
"one request per page, no retry loop against a bot wall" rule the scanner
itself states, one layer up. The window is short because a failure is the
*absence* of a measurement: it must expire on its own, so a recovered scanner
is noticed on the next request after it rather than being cached into a
permanent outage.

Errors speak one language
-------------------------
Both routes take their query parameters as text and validate them here, so a
typo is a ``422`` in the house ``{error, errorMessage}`` envelope with a code
that names the parameter (``invalid_region``, ``invalid_asset_class``,
``asset_class_mismatch``, ``invalid_limit``, ``invalid_query``). A ``Literal``
annotation would have made FastAPI answer ``{"detail": …}`` before the route
ran, which is the one language this API otherwise never speaks.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter, Request

from app.auth import ApiError
from app.engine.security import SecurityMaster
from app.screen.scanner import SCAN_COLUMNS, FULL_SCAN_ROWS, ScanError, fetch_scan
from app.screen.universe import (
    US_EXCHANGES,
    asset_class_for,
    build_universe,
    mic_for,
)

router = APIRouter(tags=["discovery"])

#: The closed vocabularies, as data rather than as ``Literal`` annotations.
#: A ``Literal`` in a signature makes FastAPI refuse a typo with a
#: framework-shaped ``{"detail": …}`` before the route runs, while every other
#: failure on this router is the house ``{error, errorMessage}``; so the
#: parameter is taken as text and validated here, where the refusal can be the
#: house envelope (F12). Blank and wrongly-cased values are refused, not
#: coerced: the vocabulary is closed and must stay closed.
BREADTH_REGIONS: tuple[str, ...] = ("us", "global", "us_etf")
ASSET_CLASSES: tuple[str, ...] = ("stock", "etf")
SEARCH_REGIONS: tuple[str, ...] = ("us", "global")

#: Default page for a lookup: enough to choose from, small enough to read.
SEARCH_LIMIT_DEFAULT = 10
#: A lookup must never become a universe dump, however large a ``limit`` a
#: caller sends.
SEARCH_LIMIT_MAX = 20

#: How long one scan may serve breadth. The scanner is a network call and the
#: number is intraday, so a minute is the point where "fresh" stops being worth
#: a scan per request without going stale inside a session.
BREADTH_TTL_S = 60.0

#: How long one *failed* scan answers for every caller.
#:
#: A success is a measurement worth a minute; a failure is the *absence* of one,
#: and the only thing to protect is the scanner. ``app.screen.scanner`` states
#: the rule — one request per page, because "a retry loop against a bot wall is
#: how a scraper earns a ban" — and this is that rule one layer up: during an
#: outage the route must cost one attempt per window instead of one per request
#: (F10: twenty cold requests used to buy twenty 5,700-row scans).
#:
#: Five seconds because it is far longer than a burst of panel requests (twenty
#: requests take tens of milliseconds, so a burst collapses to one attempt) and
#: far shorter than the 60 s snapshot TTL (so a scanner that recovers is noticed
#: on the next request after the window, not up to a minute later). It caps an
#: outage at 0.2 attempts per second, twelve a minute — the same order as the
#: scanner's own budget — while still expiring on its own, which is what keeps
#: one transient failure from being cached into a permanent 503.
UNIVERSE_FAILURE_TTL_S = 5.0

#: Columns the breadth scan asks for: the light row the universe filter needs,
#: plus the two 52-week extremes. Requested explicitly rather than inherited
#: from ``PARITY_COLUMNS`` (70+ columns, ~4x the payload) or from
#: ``SCAN_COLUMNS`` alone (whose list could drop an extreme without anyone
#: noticing). ``build_universe`` copies a parity field when its *provider*
#: column was requested, so naming the two here is what makes ``high_52w`` and
#: ``low_52w`` land on the row.
BREADTH_COLUMNS: tuple[str, ...] = tuple(
    dict.fromkeys((*SCAN_COLUMNS, "price_52_week_high", "price_52_week_low"))
)

#: The document's ``method`` block: the rules a reader would otherwise have to
#: reverse-engineer from the numbers. Kept short on purpose — one sentence per
#: decision, in the response, where a client can show it.
_METHOD: dict[str, str] = {
    "unchanged": (
        "change_pct == 0.0 or absent; a missing change is counted here, never as "
        "a decline, and reported separately as unchanged_missing"
    ),
    "sector_avg_change_pct": (
        "arithmetic mean over the rows of the sector carrying a numeric "
        "change_pct; a row without one is excluded from the mean and stays in count"
    ),
    "new_highs": (
        "price >= high_52w, counted over rows carrying both; "
        "new_highs_denominator is that row count"
    ),
    "new_lows": (
        "price <= low_52w, counted over rows carrying both; "
        "new_lows_denominator is that row count"
    ),
    "sectors": (
        "one bucket per provider sector label plus a null bucket for rows the "
        "provider did not label, so sector counts sum to universe_total"
    ),
}


def _number(value: Any) -> float | None:
    """A cell as a finite float, or ``None``.

    ``None`` is "not measured" and must never become ``0.0``: a missing change
    is not a flat session, and ``NaN``/``inf`` compare false in every direction,
    so they are not evidence either.
    """

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            return None
    if not math.isfinite(number):
        return None
    return number


def _sector_label(value: Any) -> str | None:
    """A sector cell as a label, or ``None`` when the provider did not state one.

    Trimmed and blank-as-unknown, which is the same rule
    :mod:`app.screen.universe` applies to its one text field: an empty string is
    not a sector a filter (or a bar chart) could match by accident.
    """

    if not isinstance(value, str):
        return None
    label = value.strip()
    return label or None


@dataclass(slots=True)
class _SectorTally:
    """Accumulator for one sector bucket; filled inside the single pass."""

    name: str | None
    count: int = 0
    changes: int = 0
    total_change: float = 0.0
    advancing: int = 0
    declining: int = 0

    def document(self) -> dict[str, Any]:
        """The bucket as the API states it; ``null`` mean when nothing measured."""

        average = self.total_change / self.changes if self.changes else None
        return {
            "name": self.name,
            "avg_change_pct": average,
            "count": self.count,
            "advancing": self.advancing,
            "declining": self.declining,
        }


def breadth_of(
    rows: Iterable[Mapping[str, Any]],
    *,
    as_of: str,
    source: str,
    region: str,
    asset_class: str,
    stale: bool = False,
) -> dict[str, Any]:
    """Count ``rows`` in one pass and return the breadth document.

    Every counter — the three buckets, the two 52-week extremes and each sector
    bucket — is filled in this one loop. A second loop over the same rows to
    build sector averages would double the cost of the only expensive thing
    this route does.
    """

    advancing = 0
    declining = 0
    unchanged = 0
    unchanged_reported = 0
    unchanged_missing = 0
    new_highs = 0
    new_lows = 0
    highs_basis = 0
    lows_basis = 0
    labelled = 0
    total = 0
    tallies: dict[str | None, _SectorTally] = {}

    for row in rows:
        total += 1

        change = _number(row.get("change_pct"))
        if change is None:
            unchanged += 1
            unchanged_missing += 1
        elif change > 0.0:
            advancing += 1
        elif change < 0.0:
            declining += 1
        else:
            # Exactly zero, including -0.0 and "0.00": flat, never a decline.
            unchanged += 1
            unchanged_reported += 1

        name = _sector_label(row.get("sector"))
        if name is not None:
            labelled += 1
        tally = tallies.get(name)
        if tally is None:
            tally = tallies[name] = _SectorTally(name=name)
        tally.count += 1
        if change is not None:
            tally.changes += 1
            tally.total_change += change
            if change > 0.0:
                tally.advancing += 1
            elif change < 0.0:
                tally.declining += 1

        price = _number(row.get("price"))
        if price is not None:
            high = _number(row.get("high_52w"))
            if high is not None:
                highs_basis += 1
                if price >= high:
                    new_highs += 1
            low = _number(row.get("low_52w"))
            if low is not None:
                lows_basis += 1
                if price <= low:
                    new_lows += 1

    sectors = [tally.document() for tally in tallies.values()]
    # Largest sector first, alphabetical on a tie, and the unlabelled bucket
    # last: a UI can render the tail as "unspecified" without re-sorting.
    sectors.sort(
        key=lambda bucket: (bucket["name"] is None, -bucket["count"], bucket["name"] or "")
    )

    return {
        "as_of": as_of,
        "region": region,
        "asset_class": asset_class,
        "source": source,
        "stale": stale,
        "universe_total": total,
        "advancing": advancing,
        "declining": declining,
        "unchanged": unchanged,
        "unchanged_reported": unchanged_reported,
        "unchanged_missing": unchanged_missing,
        "new_highs": new_highs,
        "new_lows": new_lows,
        "new_highs_denominator": highs_basis,
        "new_lows_denominator": lows_basis,
        "sector_missing": total - labelled,
        "method": dict(_METHOD),
        "sectors": sectors,
    }


@dataclass(frozen=True, slots=True)
class UniverseSnapshot:
    """One measurement of a screened universe, ready to be counted.

    ``rows`` is a tuple so a reader cannot grow or shrink the measurement it was
    handed; the rows themselves are never written (breadth only reads cells).
    """

    rows: tuple[Mapping[str, Any], ...]
    as_of: str
    source: str


async def scan_universe(region: str, asset_class: str) -> UniverseSnapshot:
    """Build the breadth universe from one scanner page.

    The filters are the screener's own *criteria* universe — no cap floor, no
    ``subtype=common`` gate, OTC out, one row per issuer — so the breadth bar
    and the "N matches" of a screen describe the same population. The rows are
    copied out of the scan so the caller's page and this snapshot can never be
    the same objects.
    """

    page = await fetch_scan(
        region,
        limit=FULL_SCAN_ROWS,
        columns=BREADTH_COLUMNS,
        min_market_cap=None,
        subtype=None,
        asset_class=asset_class,
    )
    rows = build_universe(
        page.rows,
        region=region,
        min_market_cap=None,
        columns=BREADTH_COLUMNS,
        allow_any_subtype=True,
        asset_class=asset_class,
    )
    return UniverseSnapshot(
        rows=tuple(dict(row) for row in rows),
        as_of=datetime.now(UTC).isoformat(timespec="seconds"),
        source=f"scan:{region}:{asset_class}",
    )


class UniverseUnavailable(ScanError):
    """The scan failed moments ago, so it was refused instead of retried.

    Raised by :meth:`UniverseCache.snapshot` instead of calling the loader again
    inside :data:`UNIVERSE_FAILURE_TTL_S` of a cold failure. It is a
    ``ScanError`` so a caller that already handles "the scanner could not
    answer" needs no new branch, and it carries the *original* message, so the
    client reads the same reason (a bot wall, a throttle) it would have read
    from the live attempt.
    """


#: The scan a cache is asked to run: ``(region, asset_class)`` → snapshot.
_Loader = Callable[[str, str], Awaitable[UniverseSnapshot]]


@dataclass(frozen=True, slots=True)
class _Measured:
    """One snapshot, and the loader that built it."""

    loader: _Loader
    built_at: float
    snapshot: UniverseSnapshot
    stale: bool


@dataclass(frozen=True, slots=True)
class _Refused:
    """One failed attempt, and the loader that made it."""

    loader: _Loader
    failed_at: float
    message: str


class UniverseCache:
    """In-process cache: one scan serves many breadth reads.

    The scan is a network call of a second or two; breadth must answer in tens
    of milliseconds, so the rows are built once and reused until the TTL
    expires. A failed refresh with a warm entry serves the last snapshot, marks
    it stale for the next TTL window (so a bot wall does not turn every request
    into another scan) and says so (``stale``); a failed refresh with nothing to
    serve raises, because an empty market is a lie. Only the snapshot is cached
    — the measurement runs on every request, so a cached read is never a cached
    *number*.

    A cold entry is built under a per-key lock: two panels opening at once must
    not send two scans to a bot-walled endpoint, and the lock is what makes the
    second caller wait for the first instead of racing it. The failure is
    recorded *before* the lock is released, so the waiters behind it read the
    refusal instead of each retrying the dead scanner (F10).

    Two rules this class states rather than hides:

    * **The loader is an argument, not state.** ``snapshot`` takes the loader it
      is to call, so a deployment that swaps ``app.state.universe_loader`` is
      obeyed on the next request. An entry — or a refusal — is only reused by
      the loader that produced it, so a new source never inherits the rows, or
      the failure, of the one it replaced. (F13: the loader used to be captured
      when the cache was created, which made the documented per-request lookup
      dead code and a swap silently ineffective.)
    * **A failed attempt is cached, briefly, and only for a cold entry.** The
      window is :data:`UNIVERSE_FAILURE_TTL_S` and it expires on its own: a
      transient failure must not become a permanent 503.
    """

    def __init__(
        self,
        *,
        ttl_s: float = BREADTH_TTL_S,
        failure_ttl_s: float = UNIVERSE_FAILURE_TTL_S,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._ttl_s = ttl_s
        self._failure_ttl_s = failure_ttl_s
        self._clock = clock if clock is not None else time.monotonic
        #: ``_Measured`` per ``(region, asset_class)``, tagged with its loader.
        self._entries: dict[tuple[str, str], _Measured] = {}
        #: ``_Refused`` per key: the cold failure the window still covers.
        self._refusals: dict[tuple[str, str], _Refused] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _fresh(self, key: tuple[str, str], loader: _Loader, now: float) -> _Measured | None:
        """The entry for ``key`` if *this* loader built it and it is fresh."""

        entry = self._entries.get(key)
        if entry is None or entry.loader is not loader:
            return None
        if now - entry.built_at >= self._ttl_s:
            return None
        return entry

    def _refusal(
        self, key: tuple[str, str], loader: _Loader, now: float
    ) -> _Refused | None:
        """The recent failure for ``key`` by *this* loader, if still in the window."""

        refusal = self._refusals.get(key)
        if refusal is None or refusal.loader is not loader:
            return None
        if now - refusal.failed_at >= self._failure_ttl_s:
            return None
        return refusal

    def _raise_if_refused(self, key: tuple[str, str], loader: _Loader, now: float) -> None:
        """Refuse from memory when this scanner failed less than a window ago."""

        refusal = self._refusal(key, loader, now)
        if refusal is not None:
            raise UniverseUnavailable(refusal.message)

    async def snapshot(
        self, region: str, asset_class: str, loader: _Loader
    ) -> tuple[UniverseSnapshot, bool]:
        """Return ``(snapshot, stale)``, scanning only when no recent answer exists.

        ``stale`` is true only on the *warm* path — an entry that could not be
        refreshed is still served, marked. A *cold* failure has nothing to serve
        and is refused: once per window, not once per request.
        """

        key = (region, asset_class)
        now = self._clock()
        entry = self._fresh(key, loader, now)
        if entry is not None:
            return entry.snapshot, entry.stale
        self._raise_if_refused(key, loader, now)
        async with self._locks.setdefault(key, asyncio.Lock()):
            # Re-read under the lock: a sibling request may have just answered,
            # with rows or with a failure. Both are recorded before the lock is
            # released, so the waiters behind it never touch the loader.
            now = self._clock()
            entry = self._fresh(key, loader, now)
            if entry is not None:
                return entry.snapshot, entry.stale
            self._raise_if_refused(key, loader, now)
            try:
                snapshot = await loader(region, asset_class)
            except ScanError as exc:
                self._refusals[key] = _Refused(
                    loader=loader, failed_at=now, message=str(exc)
                )
                warm = self._entries.get(key)
                if warm is None or warm.loader is not loader:
                    raise
                # Keep the last measurement, keep saying it is stale, and defer
                # the next attempt by a TTL: a warm outage must serve data, not
                # a refusal. (The refusal record only matters while cold.)
                self._entries[key] = _Measured(loader, now, warm.snapshot, True)
                return warm.snapshot, True
            self._refusals.pop(key, None)
            self._entries[key] = _Measured(loader, now, snapshot, False)
            return snapshot, False


def _exchange_of(record: Any) -> str | None:
    """The venue in the screener's vocabulary: its MIC, else the provider code.

    ``/screens`` rows carry a MIC (``XNAS``), so search answers the same
    vocabulary and a client can join the two without a translation table. A
    label with no known MIC is returned as printed rather than guessed.
    """

    mic = str(getattr(record, "mic", None) or "").strip().upper()
    if mic:
        return mic
    label = getattr(record, "exchange", None)
    return mic_for(label) or None


class IdentityIndex:
    """A searchable projection of the identity master, built once per master.

    ``records()`` on a 10k master copies ten thousand frozen dataclasses, which
    is fine once and wasteful per keystroke, so the projection is a tuple of
    small tuples with the comparison keys pre-folded. Nothing here writes to the
    master: it is a read-only view, and a search cannot make the identity
    snapshot disagree with itself.
    """

    __slots__ = ("_rows",)

    def __init__(self, rows: Iterable[Sequence[Any]]) -> None:
        #: ``(symbol, name, exchange, symbol_key, name_key)`` per security.
        self._rows: tuple[tuple[Any, ...], ...] = tuple(
            (symbol, name, exchange, symbol_key, name_key)
            for symbol, name, exchange, symbol_key, name_key in rows
        )

    @classmethod
    def from_master(cls, master: SecurityMaster) -> IdentityIndex:
        """Project ``master``'s records; never a second network or disk read."""

        return cls(
            (
                record.symbol,
                record.name,
                _exchange_of(record),
                record.symbol.casefold(),
                " ".join(record.name.split()).casefold(),
            )
            for record in master.records()
        )

    def search(self, needle: str, *, limit: int, region: str) -> list[dict[str, Any]]:
        """Rank the master against ``needle`` and answer at most ``limit`` hits.

        ``needle`` is expected already trimmed and case-folded by the route, so
        one query cannot be ranked differently from another spelling of itself.
        """

        us_only = region == "us"
        matches: list[tuple[int, int, str, int]] = []
        for position, (symbol, _name, exchange, symbol_key, name_key) in enumerate(self._rows):
            if us_only and exchange not in US_EXCHANGES:
                continue
            if symbol_key == needle:
                tier = 0
            elif symbol_key.startswith(needle):
                tier = 1
            elif name_key.startswith(needle):
                tier = 2
            elif needle in name_key:
                tier = 3
            else:
                continue
            matches.append((tier, len(symbol), symbol, position))

        matches.sort()
        return [
            {
                "symbol": self._rows[position][0],
                "name": self._rows[position][1],
                "exchange": self._rows[position][2],
            }
            for _tier, _length, _symbol, position in matches[:limit]
        ]


def _universe_loader(request: Request) -> _Loader:
    """The scan this process builds breadth from.

    Tests (and an operator with a pre-built row set) attach
    ``app.state.universe_loader``; the default is the live scanner. This is read
    on *every* request and handed to the cache with each call, so a swap takes
    effect on the next request — including for an entry the previous loader
    built, which the cache refuses to reuse (F13: the loader used to be captured
    once, when the cache was created, so the swap was silently ignored).
    """

    loader = getattr(request.app.state, "universe_loader", None)
    return cast(_Loader, loader) if callable(loader) else scan_universe


def _cache_for(request: Request) -> UniverseCache:
    """The process's universe cache, created on first use.

    The cache holds no loader — the route passes one to every call — so this
    function never has to be told that the source changed.
    """

    cache = getattr(request.app.state, "breadth_cache", None)
    if isinstance(cache, UniverseCache):
        return cache
    cache = UniverseCache()
    request.app.state.breadth_cache = cache
    return cache


def _universe_family(region: str, asset_class: str | None) -> str:
    """The instrument family ``region`` implies, or a ``422`` in the house envelope.

    Validated here rather than by a ``Literal`` annotation so a typo is
    ``{error, errorMessage}`` like every other refusal. The rules themselves are
    the screener's (``asset_class_for``), so the two cannot disagree about what
    ``us_etf`` means.
    """

    if region not in BREADTH_REGIONS:
        raise ApiError(
            status_code=422,
            error="invalid_region",
            message=(
                f"region must be one of {', '.join(BREADTH_REGIONS)}; got {region!r}"
            ),
        )
    if asset_class is not None and asset_class not in ASSET_CLASSES:
        raise ApiError(
            status_code=422,
            error="invalid_asset_class",
            message=(
                "asset_class must be one of "
                f"{', '.join(ASSET_CLASSES)}; got {asset_class!r}"
            ),
        )
    try:
        return asset_class_for(region, asset_class)
    except ValueError as exc:
        raise ApiError(
            status_code=422, error="asset_class_mismatch", message=str(exc)
        ) from exc


def _search_region(region: str) -> str:
    """The validated search scope, or ``422 invalid_region``."""

    if region not in SEARCH_REGIONS:
        raise ApiError(
            status_code=422,
            error="invalid_region",
            message=(
                f"region must be one of {', '.join(SEARCH_REGIONS)}; got {region!r}"
            ),
        )
    return region


def _search_limit(raw: str) -> int:
    """The validated page size, clamped at :data:`SEARCH_LIMIT_MAX`.

    Blank, non-numeric and anything below one are the same refusal (``422
    invalid_limit``): a page of zero rows is not a lookup. A value above the
    maximum is *clamped*, not refused, because the caller asked for "more" and
    the bound is the endpoint's, not theirs.
    """

    text = raw.strip() if isinstance(raw, str) else ""
    try:
        value = int(text)
    except ValueError as exc:
        raise ApiError(
            status_code=422,
            error="invalid_limit",
            message=(
                f"limit must be an integer 1..{SEARCH_LIMIT_MAX}, got {raw!r}"
            ),
        ) from exc
    if value < 1:
        raise ApiError(
            status_code=422,
            error="invalid_limit",
            message=f"limit must be at least 1, got {value}",
        )
    return min(value, SEARCH_LIMIT_MAX)


def _identity_index_of(request: Request) -> IdentityIndex:
    """The index for the app's current master, rebuilt only when it changes.

    The cache is keyed on object identity *and* size: a lifespan swap replaces
    the master (new object) and a load grows it (new size), so neither can leave
    a stale index answering for a list that no longer exists.
    """

    service = getattr(request.app.state, "engine", None)
    master = getattr(service, "security_master", None)
    if master is None:
        raise ApiError(
            status_code=503,
            error="identity_unavailable",
            message=(
                "no identity master is loaded, so there is nothing to search; "
                "ingest a dataset (or point MEE_ENGINE_SNAPSHOT at one) first"
            ),
        )
    size = len(master)
    cached = getattr(request.app.state, "identity_index", None)
    if cached is not None and cached[0] is master and cached[1] == size:
        return cached[2]
    index = IdentityIndex.from_master(master)
    request.app.state.identity_index = (master, size, index)
    return index


@router.get("/market/breadth")
async def breadth(
    request: Request,
    region: str = "us",
    asset_class: str | None = None,
) -> dict[str, Any]:
    """Market-wide advance/decline, 52-week extremes and sector performance.

    The parameters are text, validated by :func:`_universe_family` before
    anything is scanned: a refused query is the house ``{error, errorMessage}``
    envelope and costs no scan, so ``region=global&asset_class=etf`` is refused
    rather than answered with a universe the product does not have.

    A cold scanner failure answers ``503 scanner_unavailable`` — raised here for
    a live attempt, and raised from the cache's failure window inside
    :data:`UNIVERSE_FAILURE_TTL_S` of one, which is what stops an outage from
    becoming a scan per request.
    """

    family = _universe_family(region, asset_class)
    cache = _cache_for(request)
    try:
        snapshot, stale = await cache.snapshot(region, family, _universe_loader(request))
    except UniverseUnavailable as exc:
        raise ApiError(
            status_code=503,
            error="scanner_unavailable",
            message=(
                "the scanner could not answer the breadth request and no earlier "
                "scan is cached; the same failure was seen less than "
                f"{UNIVERSE_FAILURE_TTL_S:g} s ago, so this request was refused "
                f"without a new scan: {exc}"
            ),
        ) from exc
    except ScanError as exc:
        raise ApiError(
            status_code=503,
            error="scanner_unavailable",
            message=(
                "the scanner could not answer the breadth request and no earlier "
                f"scan is cached: {exc}"
            ),
        ) from exc

    return breadth_of(
        snapshot.rows,
        as_of=snapshot.as_of,
        source=snapshot.source,
        region=region,
        asset_class=family,
        stale=stale,
    )


@router.get("/symbols/search")
async def search(
    request: Request,
    q: str = "",
    limit: str = str(SEARCH_LIMIT_DEFAULT),
    region: str = "us",
) -> list[dict[str, Any]]:
    """Ranked symbol lookup over the identity snapshot.

    ``region=us`` is the scope the screener trades — the US venues behind
    ``/screens`` rows, OTC and unstated venues out; ``region=global`` searches
    every security the master holds. A blank query and a page of zero rows are
    refusals, not empty answers: both would otherwise render as "no such
    symbol", which is a different statement from "you did not ask" or "I am not
    allowed to show a page that small".

    ``limit`` is text parsed by :func:`_search_limit` (a framework 422 would
    bypass the house envelope), and the three parameters are refused in the
    order they are read: the query, then the page size, then the scope. Every
    refusal is ``422`` in ``{error, errorMessage}``.
    """

    needle = " ".join(q.split()).casefold()
    if not needle:
        raise ApiError(
            status_code=422,
            error="invalid_query",
            message=(
                "q must contain at least one non-space character; pass "
                "?q=<symbol or company prefix>"
            ),
        )
    page = _search_limit(limit)
    scope = _search_region(region)

    index = _identity_index_of(request)
    return index.search(needle, limit=page, region=scope)


__all__ = [
    "ASSET_CLASSES",
    "BREADTH_COLUMNS",
    "BREADTH_REGIONS",
    "IdentityIndex",
    "SEARCH_REGIONS",
    "UNIVERSE_FAILURE_TTL_S",
    "UniverseCache",
    "UniverseSnapshot",
    "UniverseUnavailable",
    "breadth",
    "breadth_of",
    "router",
    "scan_universe",
    "search",
]
