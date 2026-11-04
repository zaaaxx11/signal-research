"""The population, the sector vocabulary, and the validator three pages share.

Three read-only market aggregates arrived together — a treemap (``/market/map``),
a sector table (``/market/groups``) and an earnings calendar
(``/market/calendar``) — and the first two answer questions about the **same**
population. If each owned its own copy of "build the universe the selector
builds", the two would drift the first time a floor, a subtype gate or a scan key
changed, and the drift would be invisible: two pages, two totals, both plausible.
This module is that population, that vocabulary and that validator, written once.

The population is the selector's, to the row
--------------------------------------------
:func:`app.screen.universe.build_universe` is the same function everywhere, but
its arguments are not: ``/market/breadth`` drops the cap floor entirely and
answers 5,678 rows, while ``POST /screens/select`` builds the stock tab with
``min_market_cap=0`` (a row whose cap the provider never sent cannot prove it
fits) and answers **5,576**. These routes copy the selector's call argument for
argument — the same floor sentinel, the same ``allow_any_subtype=True`` (the
panel's ``common_only`` default), the same ``PARITY_COLUMNS`` — **and its scan
key**, so a panel that opens the treemap and a screen pays one provider scan and
the two cannot describe different pages.

The sector label is the scan row's, and only the scan row's
----------------------------------------------------------
``build_universe`` copies the provider's own ``sector`` cell, which is Finviz's
twenty-one-name taxonomy ("Electronic Technology", "Health Technology",
"Non-Energy Minerals"). The depth store also carries a ``sector``, but it is a
**different** taxonomy: its thirteen SEC-SIC labels are GICS-shaped
("Technology", "Financials"), because :mod:`app.market.sectors` translates SIC
descriptions and says so. Using the store as a fallback for a null scan cell
would therefore publish one legend with two vocabularies — "Technology" beside
"Electronic Technology" — and split a sector a reader expects to add up. The
store is not consulted here; a row the scan did not classify is
:data:`UNCLASSIFIED_ID`, below. (The live universe has no such row: 5,576 of
5,576 carry a sector.)

A null sector is its own published bucket, never "Other"
--------------------------------------------------------
Two dishonest options were available and both were refused. Dropping the row
would make the sector counts stop adding up to the universe — the one equality
every consumer of these documents checks. Folding it into an "Other" bucket
would merge "the provider did not classify this" with "the provider classified
this as Miscellaneous", and the two are different facts. So a null label is
published under the reserved id ``_unclassified`` with the label
``Unclassified``: the id can never collide with a slug (slugs are lowercase
alphanumerics and hyphens, and this one starts with an underscore), the label
says what happened, and ``sum(sector.count) == universe_total`` still holds.
A real sector named "Other" keeps ``id: "other"`` beside it.

Caps are billions, and no name rounds to zero
---------------------------------------------
``c`` is market cap **in USD billions with one decimal**, which is what a
treemap tile needs and 5,576 rows can afford. One decimal has a trap: 991 names
in the live universe are under USD 50 M, and ``round(0.049, 1)`` is ``0.0`` —
a tile with no area, a name that silently leaves the picture while still being
counted. Any strictly positive cap therefore publishes at least
:data:`CAP_MINIMUM` (``0.1``), so the smallest listed company still owns a tile;
the value is a display quantisation, not a measurement, and every aggregate this
module computes (a sector's cap-weighted change, a group's market cap) uses the
**exact** cap, never the quantised one. A provider-reported cap of exactly zero
publishes ``0.0``: zero is what it said.

Revalidation from the inputs, never from the body
-------------------------------------------------
Hashing the body can only happen after building it, which is exactly what a
revalidation must avoid. Each route derives a revision from the **inputs** that
decide its numbers — the scan page's generation for the two scan-backed routes,
the depth store's file version for the calendar — and compares that. A matching
``If-None-Match`` is therefore a ``304`` with no scan, no universe, no pass and
no body; ``Cache-Control: no-cache`` rides on both answers, and ``HEAD`` is its
own route (a second method on one ``api_route`` collides on the operation id).

The memo is keyed by the same generation, so a re-scan moves the key even when
it returns the same rows: a memo keyed without it would keep serving yesterday's
numbers after a refresh and look perfectly healthy while doing so.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import Response

from app.api.cache import not_modified
from app.api.screens import _scan_cache, _scan_key
from app.auth import ApiError
from app.screen.scanner import FULL_SCAN_ROWS, ScanError, fetch_scan
from app.screen.snapshot import SOURCE
from app.screen.universe import PARITY_COLUMNS, build_universe

__all__ = [
    "CAP_MINIMUM",
    "CachedDocument",
    "BodyMemo",
    "GROUP_BY_COLUMNS",
    "MEMO_LIMIT",
    "SectorTally",
    "UNCLASSIFIED_ID",
    "UNCLASSIFIED_LABEL",
    "UniversePage",
    "capital_billions",
    "encode",
    "etag",
    "identity",
    "memo_for",
    "moment",
    "number",
    "serve",
    "sector_ids",
    "slug",
    "stamp_for",
    "tally_sectors",
    "text",
    "universe_for",
    "universe_key",
]

#: The reserved id for a row the provider did not classify. A leading underscore
#: is not something :func:`slug` can produce, so no real label can ever claim it.
UNCLASSIFIED_ID = "_unclassified"

#: Its published name. It states the fact ("nothing classified this") rather than
#: a sector a reader could go and look up.
UNCLASSIFIED_LABEL = "Unclassified"

#: The display floor for a strictly positive market cap, in USD billions. It is
#: what keeps a USD 40 M company from rounding to a zero-area tile; see the
#: module docstring.
CAP_MINIMUM = 0.1

#: How many documents a memo keeps. The map body is the largest in the app
#: (~173 KB over 5,576 rows), so its bound is a memory budget, not a hit-rate
#: one: a handful of generations of the widest document is a megabyte or two, and
#: anything beyond it is history nobody asked for.
MEMO_LIMIT = 8


# ---------------------------------------------------------------------------
# Reading cells
# ---------------------------------------------------------------------------


def number(value: Any) -> float | None:
    """A cell as a finite float, or ``None`` when it is not a number.

    ``None`` is unknown, never zero: a stock with no market cap does not have a
    cap of zero, and a stock with no change did not trade flat. ``NaN``/``inf``
    are unknown too — they compare false in every direction and would make a
    weighted average or a sign test pick a side at random.
    """

    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def text(value: Any) -> str | None:
    """A cell as trimmed text, or ``None`` when it is blank or not a string."""

    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def slug(label: str) -> str:
    """A label as a URL-safe id: ``"Electronic Technology"`` → ``"electronic-technology"``.

    Ids are derived, not stored, so a provider relabelling a sector changes the
    label and the id together and no mapping table can fall out of step. Anything
    that is not alphanumeric becomes a separator, runs collapse, and the result is
    lower case — ``"Non-Energy Minerals"`` → ``"non-energy-minerals"``.
    """

    cleaned = "".join(char if char.isalnum() else " " for char in label.casefold())
    return "-".join(cleaned.split())


def sector_ids(labels: Iterable[Any]) -> dict[str | None, str]:
    """Label → id for every sector in one population, ``None`` included.

    Assignment is over the **sorted** set of labels, so two calls over the same
    labels produce the same ids whatever order the rows arrived in, and two
    routes that see the same population cannot publish the same label under two
    ids. Two labels that slug alike (``"Health Technology"`` and
    ``"Health-Technology"``) are disambiguated with a numeric suffix in that
    sorted order rather than by merging them.
    """

    mapping: dict[str | None, str] = {None: UNCLASSIFIED_ID}
    taken: set[str] = {UNCLASSIFIED_ID}
    present = sorted({label for raw in labels if (label := text(raw)) is not None})
    for label in present:
        # A label of punctuation only ("---") has no slug; it still needs an id
        # that is not the reserved one, which is the bucket for *no* label.
        base = slug(label) or "sector"
        candidate = base
        suffix = 2
        while candidate in taken:
            candidate = f"{base}-{suffix}"
            suffix += 1
        mapping[label] = candidate
        taken.add(candidate)
    return mapping


def capital_billions(cap: float) -> float:
    """A market cap in USD billions, one decimal, with :data:`CAP_MINIMUM` as floor.

    The quantisation is for drawing, not for measuring: a strictly positive cap
    never rounds to ``0.0`` (991 names in the live universe sit under USD 50 M,
    and a zero-area tile is a name that quietly leaves the picture), while a cap
    the provider reported as exactly zero stays zero — that is an observation.
    """

    if cap <= 0:
        return 0.0
    return max(round(cap / 1e9, 1), CAP_MINIMUM)


@dataclass(slots=True)
class SectorTally:
    """One sector's members, counted in a single pass over the population.

    ``count`` is every row the sector holds; ``adv``/``dec`` are the rows whose
    metric is positive/negative; ``cap_sum`` and ``weighted`` are the exact sums
    (cap, cap × change) the cap-weighted average needs. ``members`` is
    ``(cap_usd, change_pct, symbol, name, price)`` for the rows that carry
    **both** a cap and a metric — the only rows a treemap can place or a
    weighted average can weigh. ``name`` may be ``None`` (an anonymous listing)
    and ``price`` ``None`` (halted/unquoted name): the treemap labels what it
    has and leaves the rest out of the wire, rather than inventing text.

    ``weighted_1w``/``weighted_1m``/``weighted_3m`` are the cap-weighted sums
    for the performance-window fields (``ret_1w`` / ``ret_1m`` / ``ret_3m``),
    accumulated over the same cap+metric rows as ``weighted`` so every average
    shares the same denominator.
    """

    id: str
    label: str
    count: int = 0
    adv: int = 0
    dec: int = 0
    cap_total: float = 0.0
    cap_sum: float = 0.0
    weighted: float = 0.0
    weighted_1w: float = 0.0
    weighted_1m: float = 0.0
    weighted_3m: float = 0.0
    cap_1w: float = 0.0
    cap_1m: float = 0.0
    cap_3m: float = 0.0
    members: list[tuple[float, float, str, str | None, float | None]] = field(
        default_factory=list
    )

    @property
    def average(self) -> float | None:
        """The cap-weighted mean change of the members present, or ``None``.

        ``None`` when nothing can be weighed (no member with a cap), never
        ``0.0``: a sector with no measurable move is not a sector that traded
        flat.
        """

        return (self.weighted / self.cap_sum) if self.cap_sum > 0 else None

    @property
    def perf_1w(self) -> float | None:
        return (self.weighted_1w / self.cap_1w) if self.cap_1w > 0 else None

    @property
    def perf_1m(self) -> float | None:
        return (self.weighted_1m / self.cap_1m) if self.cap_1m > 0 else None

    @property
    def perf_3m(self) -> float | None:
        return (self.weighted_3m / self.cap_3m) if self.cap_3m > 0 else None


#: Performance-window fields the tally accumulates: (row_field, weighted_attr,
#: cap_attr).
_PERF_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("ret_1w", "weighted_1w", "cap_1w"),
    ("ret_1m", "weighted_1m", "cap_1m"),
    ("ret_3m", "weighted_3m", "cap_3m"),
)


#: The survey columns a group-by can read. "capitalization" is computed from
#: ``market_cap_usd`` rather than read from a single cell.
GROUP_BY_COLUMNS: frozenset[str] = frozenset({"sector", "industry", "country"})

#: Capitalisation brackets, lower bound (inclusive) → label. A cap of exactly
#: ``10e9`` enters the "Large" bracket; a row with no cap enters the last one.
_CAP_BRACKETS: tuple[tuple[float, str], ...] = (
    (200e9, "Mega Cap ($200B+)"),
    (10e9, "Large Cap ($10B-200B)"),
    (2e9, "Mid Cap ($2B-10B)"),
    (300e6, "Small Cap ($300M-2B)"),
    (0, "Micro Cap (<$300M)"),
)

_GROUP_BY_DEFAULT = "sector"


def _group_by_column(group_by: str, row: Mapping[str, Any]) -> str | None:
    """The label for *one* row under ``group_by``.

    "capitalization" buckets ``market_cap_usd`` into :data:`_CAP_BRACKETS`
    and returns a stable label; every other column reads a cell verbatim.
    """
    if group_by in GROUP_BY_COLUMNS:
        return text(row.get(group_by))
    if group_by == "capitalization":
        cap = number(row.get("market_cap_usd"))
        if cap is None:
            return "Unknown Cap"
        for floor, label in _CAP_BRACKETS:
            if cap >= floor:
                return label
        return "Unknown Cap"
    return None


def _tile_name(row: Mapping[str, Any]) -> str | None:
    """The reader-facing name a tile's tooltip can quote, or ``None``.

    The universe row calls it ``name``; the provider spelled it either way. An
    absent name is not a blank string: ``None`` tells the map to leave the field
    off the tile rather than send an empty ``""`` that a client would render as
    a real (blank) name.
    """

    value = row.get("name")
    if isinstance(value, str):
        value = value.strip()
        if value:
            return value
    return None


def tally_sectors(
    rows: Iterable[Mapping[str, Any]], *, group_by: str = _GROUP_BY_DEFAULT
) -> dict[str, SectorTally]:
    """One pass over the universe → one tally per group, keyed by id.

    ``group_by`` names the column whose value defines the bucket: ``"sector"``
    (the default), ``"industry"``, ``"country"``, or ``"capitalization"``
    (which buckets ``market_cap_usd``).  The metric is the row's ``change_pct``
    — the cell a screen returns and the depth store does not carry.  A row
    without it stays in ``count`` (it is in the group and in the universe) but
    joins no average and no advance/decline count: unknown is not flat.

    When ``group_by`` is ``"capitalization"`` the brackets are stable labels
    (:data:`_CAP_BRACKETS`), so they survive slug conversion and two calls over
    the same rows produce identical ids.
    """

    materialised = list(rows)
    ids = sector_ids(
        _group_by_column(group_by, row) for row in materialised
    )
    tallies: dict[str, SectorTally] = {}
    for row in materialised:
        label = _group_by_column(group_by, row)
        sector = ids[label]
        tally = tallies.get(sector)
        if tally is None:
            # The reserved bucket publishes a name rather than `null`: a legend
            # should never read "undefined", and its id already says the label
            # is ours and not the provider's.
            tally = tallies[sector] = SectorTally(
                id=sector, label=label or UNCLASSIFIED_LABEL
            )
        tally.count += 1
        change = number(row.get("change_pct"))
        if change is not None:
            if change > 0:
                tally.adv += 1
            elif change < 0:
                tally.dec += 1
        cap = number(row.get("market_cap_usd"))
        if cap is None:
            continue
        # The sector's size is every row with a cap, whether or not it traded:
        # a market cap does not need a price change to be a fact. The weighted
        # average, below, needs both.
        tally.cap_total += cap
        # Multi-timeframe performance windows: accumulated independently of
        # ``change_pct`` — a row with no daily change can still carry a weekly
        # return.  Each cap is tracked per-window so the denominator matches.
        for field, w_attr, c_attr in _PERF_FIELDS:
            val = number(row.get(field))
            if val is not None:
                setattr(tally, w_attr, getattr(tally, w_attr) + cap * val)
                setattr(tally, c_attr, getattr(tally, c_attr) + cap)
        if change is None:
            continue
        tally.cap_sum += cap
        tally.weighted += cap * change
        tally.members.append(
            (
                cap,
                change,
                str(row.get("symbol") or ""),
                _tile_name(row),
                number(row.get("price")),
            )
        )
    return tallies



# ---------------------------------------------------------------------------
# The population both scan-backed routes read
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UniversePage:
    """One build of the selector's universe, and the scan it came from.

    ``generation`` is handed out by :meth:`app.api.screens.ScanCache.page` with
    the page it returned — a number that names exactly one scan for the life of
    the process. It is the memo key and the validator's main input, so a re-scan
    always moves both, even when it returns identical rows.
    """

    rows: list[dict[str, Any]]
    scan: str
    generation: int


def universe_key(region: str, family: str) -> tuple[str, ...]:
    """The scan key a **criteria selection** uses for the same tab.

    Shared verbatim with ``app.api.screens._scan_key`` (same columns, same
    ``FULL_SCAN_ROWS``, same ``min_market_cap=None`` at the provider, same
    ``subtype=None``), so these routes and ``/screens/select`` read one cache
    entry: a panel that opens a treemap and a screen pays one provider scan. The
    cap floor that differs between breadth and the selector is applied later, by
    ``build_universe`` — never to the scan.
    """

    return _scan_key(
        region=region,
        asset_class=family,
        source=SOURCE,
        columns=PARITY_COLUMNS,
        limit=FULL_SCAN_ROWS,
        min_market_cap=None,
        subtype=None,
    )


def stamp_for(request: Request, *, region: str, family: str) -> int | None:
    """The generation a request for this tab would be answered from, or ``None``.

    Read-only by contract: it runs on the ``304`` path, whose whole point is that
    it does no work. A ``None`` means a request has to scan, which is exactly
    when a revalidation cannot be decided from the inputs alone.
    """

    return _scan_cache(request).stamp(universe_key(region, family))


def _universe_rows(
    rows: Iterable[Mapping[str, Any]], *, region: str, family: str
) -> list[dict[str, Any]]:
    """The selector's population, built with the selector's arguments."""

    return build_universe(
        rows,
        region=region,
        min_market_cap=None if family == "etf" else 0,
        columns=PARITY_COLUMNS,
        allow_any_subtype=True,
        asset_class=family,
    )


async def universe_for(request: Request, *, region: str, family: str) -> UniversePage:
    """Scan (or replay the scan) and build the selector's universe once.

    A failed scan is a ``503 scanner_unavailable`` in the house envelope, exactly
    as ``/market/exchanges`` answers it: the numbers are a statement about the
    population, so without one there is nothing honest to publish.
    """

    cache = _scan_cache(request)
    try:
        page, scan, generation = await cache.page(
            universe_key(region, family),
            lambda: fetch_scan(
                region,
                limit=FULL_SCAN_ROWS,
                columns=PARITY_COLUMNS,
                min_market_cap=None,
                subtype=None,
                asset_class=family,
            ),
        )
    except ScanError as exc:
        raise ApiError(
            status_code=503,
            error="scanner_unavailable",
            message=f"the scanner could not answer this market view: {exc}",
        ) from exc
    return UniversePage(
        rows=_universe_rows(page.rows, region=region, family=family),
        scan=scan,
        generation=generation,
    )


# ---------------------------------------------------------------------------
# One built document, memoised per input
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CachedDocument:
    """A document already encoded, with the validator that names it.

    ``body`` is kept as bytes because that is what the response writes: a warm
    read is a dict lookup and a ``Response``, with no re-serialisation of 5,576
    rows on the way out. ``etag`` is the quoted strong validator; the ``W/`` is
    added where the header is written, because ``as_of`` dates the build and a
    strong tag would have to move with the clock.
    """

    body: bytes
    etag: str
    generation: int


class BodyMemo:
    """One encoded document per key, oldest evicted past ``limit``.

    Insertion order is the eviction order. ``put`` and ``get`` contain no
    ``await``, so on one event loop a check and a store are atomic: two requests
    that race into a cold key cannot both build the same document.
    """

    __slots__ = ("_entries", "_limit")

    def __init__(self, *, limit: int = MEMO_LIMIT) -> None:
        self._limit = max(1, int(limit))
        self._entries: dict[tuple[Any, ...], CachedDocument] = {}

    def get(self, key: tuple[Any, ...]) -> CachedDocument | None:
        """The document for ``key``, or ``None`` when this key is uncounted."""

        return self._entries.get(key)

    def put(self, key: tuple[Any, ...], document: CachedDocument) -> CachedDocument:
        """Memoise ``document`` under ``key`` and answer it."""

        self._entries[key] = document
        while len(self._entries) > self._limit:
            self._entries.pop(next(iter(self._entries)))
        return document


def memo_for(request: Request, name: str, *, limit: int = MEMO_LIMIT) -> BodyMemo:
    """The process's memo for one route, created on first use (per app, not global)."""

    attribute = f"{name}_memo"
    memo = getattr(request.app.state, attribute, None)
    if isinstance(memo, BodyMemo):
        return memo
    memo = BodyMemo(limit=limit)
    setattr(request.app.state, attribute, memo)
    return memo


# ---------------------------------------------------------------------------
# Encoding, validators, answers
# ---------------------------------------------------------------------------


def encode(document: Mapping[str, Any]) -> bytes:
    """The document as JSON, in the compact form the response writes.

    No spaces after separators and no ``\\u`` escapes: the map is the largest
    body in the app, and the difference between ``json.dumps`` defaults and this
    is 30 KB over 5,576 rows — 18 % of a payload whose budget is 250 KB.
    """

    return json.dumps(
        document, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def etag(namespace: str, revision: str) -> str:
    """The quoted validator for one namespace and one revision.

    Full-width sha256: the revision is a per-request header, so the length costs
    nothing, and a 32-bit fold would be mineable by anything that can influence
    an input.
    """

    digest = hashlib.sha256(revision.encode("utf-8")).hexdigest()
    return f'"{namespace}-{digest}"'


def serve(
    request: Request, document: CachedDocument, *, scan: str, head: bool
) -> Response:
    """The ``200`` or the ``304``, decided from the document's identity.

    Both answers carry the same ``ETag``, ``Cache-Control: no-cache`` and
    ``X-Scan-Cache``; the ``304`` has no body at all, and the ``HEAD`` has the
    length the ``GET`` would have carried and none of the bytes.
    """

    headers = {
        "ETag": f"W/{document.etag}",
        "Cache-Control": "no-cache",
        "X-Scan-Cache": scan,
    }
    if not_modified(request, document.etag):
        return Response(status_code=304, headers=headers)
    if head:
        return Response(
            status_code=200,
            headers={**headers, "Content-Length": str(len(document.body))},
        )
    return Response(
        content=document.body, media_type="application/json", headers=headers
    )


def moment() -> str:
    """The build moment, second resolution, UTC — the ``as_of`` every route dates."""

    return datetime.now(UTC).isoformat(timespec="seconds")


def identity(path: str | None) -> str:
    """``path|mtime_ns|size`` for a store file, or why there is no version.

    A stat, never a read: an 11.7 MB parse to prove a ``304`` is the cost this
    exists to avoid, and the store's one writer replaces the file atomically, so
    a rewrite always moves ``mtime_ns``.
    """

    if path is None:
        return "off"
    try:
        stat = Path(path).stat()
    except OSError:
        return "absent"
    except (ValueError, RuntimeError):  # pragma: no cover - exotic path
        return "unknown"
    return f"{path}|{stat.st_mtime_ns}|{stat.st_size}"
