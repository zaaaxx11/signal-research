"""``GET /market/exchanges`` — the venue vocabulary, with the rows each one holds.

Why this route exists
---------------------
The interface needs to offer a list of exchanges and say how many stocks each one
carries. It cannot count them itself without walking 5,576 rows, and it must not
ask the screener four times to learn four numbers. So the count is taken where
the universe lives, published once, and revalidated cheaply.

The population is the selector's, not breadth's
-----------------------------------------------
:func:`app.screen.universe.build_universe` is the same function for every route,
but its arguments are not: ``/market/breadth`` builds with ``min_market_cap=None``
(no floor at all) and answers **5,678** rows, while ``POST /screens/select``
builds the stock tab with ``min_market_cap=0`` (a row whose cap the provider never
sent cannot prove it fits) and answers **5,576**. The 102-row difference is 18
``XNAS``, **76** ``XNYS`` and 8 ``XASE`` — so a counter modelled on breadth would
publish ``XNYS 2,085`` where ``exch eq XNYS`` answers ``2,009``, a 3.8 % over-count
on the venue a reader is most likely to check.

This route therefore copies the selector's call, argument for argument (the same
``min_market_cap`` sentinel, the same ``allow_any_subtype``, the same
``PARITY_COLUMNS``), and it shares the selector's **scan key**: a criteria
selection and this route ask the provider the identical question, so a panel that
opens both pays one scan, and the two cannot describe different pages. The
equality is not a comment — ``tests/test_api_exchanges.py`` asserts
``count == select(exch eq code).total`` for every published venue, and
``universe_total`` against the unfiltered selection.

One tally per scan generation
-----------------------------
The count is one pass over the rows, so per request it is noise (1.4 ms over
5,576 rows, measured) and per generation it is free. The memo is keyed
``(region, family, common_only, generation)``, where ``generation`` is the
number :meth:`app.api.screens.ScanCache.page` hands out with the page it
returned: a re-scan always moves it, even when it returns the same rows. That
last part is the point — a memo keyed without the generation would keep serving
yesterday's counts after a refresh, and the only way to notice would be to
disbelieve the answer. A warm read is a dict lookup plus the JSON encode; it does
no scan, no universe build and no count, which is what keeps the route off the
5,576-row-per-request path ``/market/breadth`` walks.

Revalidation, before any work
-----------------------------
The body is ~500 B, but hashing the body can only happen *after* building it, so
the tag is derived from the answer's **inputs**, exactly as ``/screens/select``
does it::

    W/"exch-<sha256(region;family;common_only;scan=<gen>;total=<n>;vocab=<hash>)>"

Every input is there for a reason. ``region``/``family``/``common_only`` name a
different population; ``scan`` moves on every re-scan, so a changed universe
cannot answer ``304``; ``total`` is insurance against a build-flag change that
does not move the generation; and ``vocab`` hashes the ``(code, label, aliases)``
table because a label is code, not data — relabelling ``XASE`` changes the body
while no store moves, and a tag that ignored it would ``304`` a stale label.

An ``If-None-Match`` that matches is answered ``304`` with no body, no build and
no tally: on a warm generation the memo already holds the answer's identity, so
the comparison happens before any counting. ``Cache-Control: no-cache`` rides on
both answers — the body may be stored, but it must be revalidated, and that
revalidation is one conditional request rather than a fresh scan. (``/market/
breadth`` sends neither an ``ETag`` nor a ``Cache-Control``; this route does not
copy that defect.)

Honest refusals
---------------
The parameters are text, validated here in the house ``{error, errorMessage}``
envelope — a ``Literal`` annotation would make FastAPI answer ``{"detail": …}``
before the route ran. ``region``/``asset_class`` reuse the selector's own
:func:`app.api.discovery._universe_family` (the refusal *is* the contract, and two
parsers drift), so an unknown region is a ``422 invalid_region`` whose message
lists the accepted tabs and which costs no scan. A ``common_only`` token that is
neither a true nor a false spelling is a ``422 invalid_flag``, never a silent
``false``.

A region whose scan yields no venue is a ``200`` with ``exchanges: []``,
``code_total: 0`` and ``universe_total: 0`` — a statement about the scan, not a
500 and not a ``ZeroDivisionError``. ``share`` is ``count / universe_total`` and
is ``0.0`` when the denominator is zero, so the published shares always add to
``1.0`` whenever there is anything to divide.

One label map, one owner
------------------------
``MIC_BY_EXCHANGE`` already owns the sentence "the provider calls XNAS NASDAQ",
and that name is the label this route publishes, so the map is its **inversion**
rather than a second literal that could disagree with the code the rows carry.
The aliases are read from :data:`app.screen.filters.VALUE_ALIASES` — the same
table ``exch eq NYSE`` compiles through — so a spelling the filter accepts and a
spelling the endpoint advertises cannot drift apart. A code with no vendored name
answers ``label: null``; the code is never echoed as its own label, because a
label that repeats the code cannot be told from a real name that happens to be
one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.api.cache import not_modified
from app.api.discovery import _universe_family
from app.api.screens import _scan_cache, _scan_key
from app.auth import ApiError
from app.screen.filters import VALUE_ALIASES
from app.screen.scanner import FULL_SCAN_ROWS, ScanError, fetch_scan
from app.screen.snapshot import SOURCE
from app.screen.universe import MIC_BY_EXCHANGE, PARITY_COLUMNS, build_universe

router = APIRouter(tags=["discovery"])

#: What ``method`` states: the numbers are one pass over the population the
#: selector filters, not a provider-side count and not a stored aggregate.
METHOD = "scan"

#: The four venues, as a human reads them: ``MIC → name``. Derived from the one
#: table that already maps the provider's spelling to a MIC, so a venue added
#: there is labelled here without a second literal to keep in step.
EXCHANGE_LABELS: Mapping[str, str] = MappingProxyType(
    {mic: name for name, mic in MIC_BY_EXCHANGE.items()}
)

#: Venue → the spellings ``exch eq`` accepts for it, read from the filter's own
#: value table. Inverted once at import: the table is alias → code, and this is
#: the shape the document publishes. Nothing here is written twice.
_ALIASES_BY_CODE: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        code: tuple(
            sorted(
                alias
                for alias, target in VALUE_ALIASES["exchange"].items()
                if target == code
            )
        )
        for code in sorted(set(VALUE_ALIASES["exchange"].values()))
    }
)

#: ``(code, label, aliases)`` for every venue the product can name, sorted. This
#: is the vocabulary half of the ETag: it is code, not data, so editing a label
#: must move the tag even though no scan and no store did.
_VOCABULARY: tuple[tuple[str, str | None, tuple[str, ...]], ...] = tuple(
    (code, EXCHANGE_LABELS.get(code), _ALIASES_BY_CODE.get(code, ()))
    for code in sorted(set(EXCHANGE_LABELS) | set(_ALIASES_BY_CODE))
)

#: The digest that rides in the tag as ``vocab=``.
_VOCAB_HASH: str = hashlib.sha256(
    json.dumps(_VOCABULARY, separators=(",", ":")).encode("utf-8")
).hexdigest()

#: The boolean tokens ``common_only`` accepts. Text, like every other parameter
#: on this API, so a typo is a house-envelope ``422`` and not a silent default.
_TRUE_TOKENS: frozenset[str] = frozenset({"1", "true", "yes", "on"})
_FALSE_TOKENS: frozenset[str] = frozenset({"0", "false", "no", "off"})

#: How many tallies the memo keeps. Each entry is a handful of counts and one
#: timestamp; a few dozen generations of history is a few kilobytes, and the
#: bound is what stops an unusual query mix from growing without limit.
TALLY_MEMO_LIMIT = 32


@dataclass(frozen=True, slots=True)
class ExchangeTally:
    """One pass over one generation's universe: counts, total, and when.

    ``counts`` is a read-only mapping so a caller cannot edit the memoised
    measurement it was handed; ``as_of`` is **when the counts were computed**
    (the tally's build moment), not when the request arrived — a warm read must
    not date its numbers with its own clock.
    """

    counts: Mapping[str, int]
    universe_total: int
    generation: int
    as_of: str


def tally_exchanges(
    rows: Iterable[Mapping[str, Any]], *, generation: int, as_of: str
) -> ExchangeTally:
    """Count ``rows`` by venue in one pass.

    One loop, one dict: this is the only measurement the route performs, and it
    is memoised per generation rather than repeated per request. ``exchange`` is
    the MIC ``build_universe`` writes (``universe.py:446-451``); a row without a
    venue cannot reach here — the builder drops it at ``universe.py:422-423`` —
    so the counts partition ``universe_total`` by construction.
    """

    counts: dict[str, int] = {}
    total = 0
    for row in rows:
        total += 1
        code = str(row.get("exchange") or "").strip().upper()
        if not code:
            continue
        counts[code] = counts.get(code, 0) + 1
    return ExchangeTally(
        counts=MappingProxyType(counts),
        universe_total=total,
        generation=int(generation),
        as_of=as_of,
    )


def exchanges_document(
    counts: Mapping[str, int],
    *,
    universe_total: int,
    as_of: str,
    region: str,
    asset_class: str,
    common_only: bool,
) -> dict[str, Any]:
    """The answer, from a tally. Pure: the same tally always prints the same body.

    Ordering is ``count`` descending, then ``code`` ascending — the same rule
    breadth applies to its sector buckets, so the list is stable across calls
    and a client can diff two answers without re-sorting them.

    ``share`` is ``count / universe_total`` and ``0.0`` when the population is
    empty: a guard, not a fabricated ratio, and the reason an empty scan is a
    ``200`` rather than a ``ZeroDivisionError``.
    """

    entries = [
        {
            "code": code,
            "label": EXCHANGE_LABELS.get(code),
            "count": int(count),
            "share": (count / universe_total) if universe_total else 0.0,
            "aliases": list(_ALIASES_BY_CODE.get(code, ())),
        }
        for code, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {
        "as_of": as_of,
        "region": region,
        "asset_class": asset_class,
        "common_only": bool(common_only),
        "universe_total": int(universe_total),
        "code_total": len(entries),
        "exchanges": entries,
        "method": METHOD,
    }


def exchange_revision(
    *,
    region: str,
    family: str,
    common_only: bool,
    generation: int,
    universe_total: int,
    vocabulary: str = _VOCAB_HASH,
) -> str:
    """The answer's identity, from its inputs — never from the answer.

    Hashing the body would only be possible after building it, which is exactly
    what a revalidation must avoid; and ``as_of`` (the tally's build moment)
    would make two tallies of one unchanged universe different entities. So the
    revision names the six inputs that decide the numbers, and a change in any
    one of them is a new entity.
    """

    return (
        "v1"
        f";region={region}"
        f";family={family}"
        f";common_only={int(common_only)}"
        f";scan={int(generation)}"
        f";total={int(universe_total)}"
        f";vocab={vocabulary}"
    )


def _etag(revision: str) -> str:
    """The quoted validator; the ``W/`` is added where the header is written."""

    return f'"exch-{hashlib.sha256(revision.encode("utf-8")).hexdigest()}"'


class ExchangeTallyCache:
    """One tally per ``(region, family, common_only, scan generation)``.

    The generation is part of the key, not a detail beside it: a memo keyed
    without it would answer yesterday's counts after a data refresh and would
    look perfectly healthy while doing so. Entries are inserted in order and the
    oldest is evicted past :data:`TALLY_MEMO_LIMIT`, which bounds a query mix of
    four keys per generation to a few dozen small dicts.

    ``tally`` is get-or-compute and, deliberately, contains no ``await``: on one
    event loop the check and the store are therefore atomic, so two requests
    that race into it cannot both count the same generation.
    """

    __slots__ = ("_entries", "_limit")

    def __init__(self, *, limit: int = TALLY_MEMO_LIMIT) -> None:
        self._limit = max(1, int(limit))
        self._entries: dict[tuple[str, str, bool, int], ExchangeTally] = {}

    def get(self, key: tuple[str, str, bool, int]) -> ExchangeTally | None:
        """The tally for ``key``, or ``None`` when this generation is uncounted."""

        return self._entries.get(key)

    def tally(
        self,
        key: tuple[str, str, bool, int],
        rows: Iterable[Mapping[str, Any]],
        *,
        as_of: str,
    ) -> ExchangeTally:
        """The memoised tally for ``key``, counting ``rows`` only on a miss."""

        tally = self._entries.get(key)
        if tally is not None:
            return tally
        tally = tally_exchanges(rows, generation=key[3], as_of=as_of)
        self._entries[key] = tally
        while len(self._entries) > self._limit:
            # Insertion order is the eviction order: the oldest key first.
            self._entries.pop(next(iter(self._entries)))
        return tally


def _tallies(request: Request) -> ExchangeTallyCache:
    """The process's tally memo, created on first use (one per app, not global)."""

    cache = getattr(request.app.state, "exchange_tallies", None)
    if isinstance(cache, ExchangeTallyCache):
        return cache
    cache = ExchangeTallyCache()
    request.app.state.exchange_tallies = cache
    return cache


def _common_only_flag(raw: str) -> bool:
    """``common_only`` as a boolean, or ``422 invalid_flag``.

    The accepted spellings are stated in the message because the parameter is
    text: refusing a typo is cheap, and guessing that ``maybe`` means ``false``
    is how a client silently gets the wrong population.
    """

    token = raw.strip().casefold() if isinstance(raw, str) else ""
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    raise ApiError(
        status_code=422,
        error="invalid_flag",
        message=(
            "common_only must be one of "
            f"{', '.join(sorted(_TRUE_TOKENS))} (true) or "
            f"{', '.join(sorted(_FALSE_TOKENS))} (false); got {raw!r}"
        ),
    )


def _scan_key_for(region: str, family: str) -> tuple[str, ...]:
    """The scan key a **criteria selection** uses for the same tab.

    Shared verbatim with ``app.api.screens._scan_key`` (same columns, same
    ``FULL_SCAN_ROWS``, same ``min_market_cap=None`` at the provider and
    ``subtype=None``), so this route and ``/screens/select`` read one cache
    entry: a panel that opens both pays one provider scan, and the two cannot
    describe different pages. The cap floor that differs between breadth and the
    selector is applied later, by ``build_universe`` — not to the scan.
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


def _universe_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    region: str,
    family: str,
    common_only: bool,
) -> list[dict[str, Any]]:
    """The selector's population, built with the selector's arguments.

    Three arguments matter and all three are copied from
    ``app.api.screens.select``: the floor is ``0`` for stocks and ``None`` for
    the ETF tab (``screens.py:1199-1206``), and ``allow_any_subtype`` is the
    inverse of ``common_only``. Changing any one of them here would publish
    counts no screen can reproduce.
    """

    return build_universe(
        rows,
        region=region,
        min_market_cap=None if family == "etf" else 0,
        columns=PARITY_COLUMNS,
        allow_any_subtype=not common_only,
        asset_class=family,
    )


def _answer(
    request: Request,
    tally: ExchangeTally,
    *,
    region: str,
    family: str,
    common_only: bool,
    scan: str,
    body: bool = True,
) -> Response:
    """The 200, or the 304 — decided from the tally's identity, not its body.

    Both answers carry the same ``ETag`` and ``Cache-Control: no-cache``; the
    ``304`` has no body at all. ``X-Scan-Cache`` is the same observable
    ``/screens/select`` emits (``hit``/``miss``/``shared``), so a client can tell
    a replayed scan from a fresh one without a second request.
    """

    etag = _etag(
        exchange_revision(
            region=region,
            family=family,
            common_only=common_only,
            generation=tally.generation,
            universe_total=tally.universe_total,
        )
    )
    headers = {
        "ETag": f"W/{etag}",
        "Cache-Control": "no-cache",
        "X-Scan-Cache": scan,
    }
    if not_modified(request, etag):
        return Response(status_code=304, headers=headers)
    document = exchanges_document(
        tally.counts,
        universe_total=tally.universe_total,
        as_of=tally.as_of,
        region=region,
        asset_class=family,
        common_only=common_only,
    )
    if not body:
        # A HEAD answers the question a monitor asks — is this current? — with
        # the same headers and the length the GET would have carried, and not a
        # byte of the document.
        return Response(
            status_code=200,
            headers={**headers, "Content-Length": str(len(_encode(document)))},
        )
    return JSONResponse(content=document, headers=headers)


async def _exchanges_answer(
    request: Request,
    region: str,
    asset_class: str | None,
    common_only: str,
    *,
    head: bool,
) -> Response:
    """Every venue in the selection's universe, with its row count and share.

    The parameters are text, validated before anything is scanned, so a refused
    query costs nothing and speaks the house envelope. The scan is the one
    ``/screens/select`` uses (see :func:`_scan_key_for`); the universe is built
    with the selector's own arguments (see :func:`_universe_rows`); and the
    count is memoised per scan generation, so a warm read is a lookup and a
    revalidation is a comparison.
    """

    family = _universe_family(region, asset_class)
    narrowed = _common_only_flag(common_only)

    cache = _scan_cache(request)
    key = _scan_key_for(region, family)
    tallies = _tallies(request)

    # A revalidation is decided from the generation alone: `stamp` is read-only
    # (no scan, no counter) and the memo holds this generation's identity, so a
    # matching `If-None-Match` costs neither a universe nor a count.
    stamp = cache.stamp(key)
    if stamp is not None:
        tally = tallies.get((region, family, narrowed, stamp))
        if tally is not None:
            return _answer(
                request,
                tally,
                region=region,
                family=family,
                common_only=narrowed,
                scan="hit",
            )

    try:
        page, scan, generation = await cache.page(
            key,
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
            message=f"the scanner could not answer the exchanges request: {exc}",
        ) from exc

    memo_key = (region, family, narrowed, generation)
    tally = tallies.get(memo_key)
    if tally is None:
        tally = tallies.tally(
            memo_key,
            _universe_rows(page.rows, region=region, family=family, common_only=narrowed),
            as_of=datetime.now(UTC).isoformat(timespec="seconds"),
        )
    return _answer(
        request,
        tally,
        region=region,
        family=family,
        common_only=narrowed,
        scan=scan,
        body=not head,
    )


@router.get("/market/exchanges")
async def exchanges(
    request: Request,
    region: str = "us",
    asset_class: str | None = None,
    common_only: str = "0",
) -> Response:
    """The venue vocabulary with counts, revalidatable (GET)."""

    return await _exchanges_answer(
        request, region, asset_class, common_only, head=False
    )


@router.head("/market/exchanges", include_in_schema=False)
async def exchanges_head(
    request: Request,
    region: str = "us",
    asset_class: str | None = None,
    common_only: str = "0",
) -> Response:
    """The same headers as GET, no body: a monitor's question, not the document.

    Registered as its own route rather than as a second method on the GET one:
    FastAPI derives an operation id per route, and two methods on one route
    produce a duplicate id warning. It is ``include_in_schema=False`` because
    HEAD adds nothing to the contract a client reads.
    """

    return await _exchanges_answer(
        request, region, asset_class, common_only, head=True
    )


def _encode(document: Mapping[str, Any]) -> bytes:
    """The document as JSONResponse would encode it, for one Content-Length."""

    return json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


__all__ = [
    "EXCHANGE_LABELS",
    "ExchangeTally",
    "ExchangeTallyCache",
    "METHOD",
    "TALLY_MEMO_LIMIT",
    "exchanges",
    "exchanges_document",
    "exchange_revision",
    "router",
    "tally_exchanges",
]
