"""``GET /market/map`` — the whole market as one treemap payload.

Why this route exists
---------------------
A treemap of the US market is 5,576 tiles, and the browser cannot compute it: the
universe lives behind a provider scan, and a page that asked ``/screens/select``
for every row would receive a 100-row display and a truncated picture. So the
geometry's inputs are published once, compactly, and revalidated cheaply: one
document, one scan generation, one pass.

The shape, and why it is this small
-----------------------------------
``{as_of, region, metric, universe_total, mapped, sectors: [{id, label, count,
value, adv, dec, symbols: [{s, c, v}]}]}``. One tile is three short keys — symbol,
cap in **USD billions** (one decimal), the metric in percent (two decimals) —
because the body is the largest response in the app and every byte of a key name
is paid 5,576 times. Measured on the live universe: **~173 KB raw** (173,035
bytes, against a 250 KB budget) and **~33 KB brotli** at quality 11 (33,425
bytes, against 40 KB). Compression is a transport concern and is not applied
here; the numbers are what the document compresses to.

``mapped`` is published beside ``universe_total`` on purpose
------------------------------------------------------------
A tile needs a symbol, a cap **and** a metric. Three names in the live 5,576 have
no ``change_pct`` (the provider sent none for them that cycle), so the treemap can
draw 5,573 of them; the other three are still in the universe, still counted in
their sector's ``count``, and simply not placed. Publishing ``mapped`` makes that
difference visible instead of letting a client discover it by summing arrays and
wondering why the total is three short.

The budget is quoted for the US tab, and the other tabs scale with their own
universe: ``us_etf`` publishes 5,776 funds (176 KB — one ``miscellaneous`` bucket,
because the provider classifies funds as Miscellaneous and leaves a single one
unclassified), and ``global`` publishes 40,774 foreign listings, which is 1.28 MB
of JSON. That is the shape of the data, not a regression: a body is linear in its
tiles, the ``ETag`` keeps every repeat free, and a client that wants the world
asks for it by name.

A sector's ``value`` is cap-weighted, and it is the same number ``/market/groups``
publishes: both read :func:`app.api.sectors.tally_sectors`, so a sector cannot
move 1.2 % on the treemap's legend and 1.4 % in the table beside it.

Revalidation
------------
The tag is derived from the answer's **inputs**, before the answer exists::

    W/"map-<sha256(v1;region=…;family=…;metric=…;scan=<generation>;shape=1)>"

A generation names exactly one scan page for the life of the process, every
number in the body is a function of that page and of code whose meaning is
versioned by ``shape``, and ``region``/``family``/``metric`` name a different
document. So a warm ``If-None-Match`` is decided by
:func:`app.api.sectors.stamp_for` alone — no scan, no universe build, no pass, no
body — and a matching one is a ``304`` in well under the 5 ms the goal asks for.
``Cache-Control: no-cache`` rides on both answers. ``HEAD`` is its own route
(a second method on one ``api_route`` collides on the operation id) and answers
the same validator and the length the ``GET`` would carry, with no bytes.

The cost of the first request
-----------------------------
One scan (shared with ``/screens/select`` through the same scan key), one
universe build, one pass over 5,576 rows, one encode. The result is memoised per
``(region, family, metric, generation)`` as **encoded bytes**, so the second
request is a dict lookup and a ``Response``: it does not re-serialise 169 KB to
answer ``304``, and it does not re-build the document to answer ``200``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.api.discovery import _universe_family
from app.api.sectors import (
    BodyMemo,
    CachedDocument,
    capital_billions,
    encode,
    etag,
    memo_for,
    moment,
    serve,
    stamp_for,
    tally_sectors,
    universe_for,
)
from app.auth import ApiError

router = APIRouter(tags=["discovery"])

#: The one metric this route can publish today. The percent cell is the scan
#: row's ``change_pct``; a second metric (volume, turnover) would need its own
#: reader in :func:`app.api.sectors.tally_sectors`, and until it exists a
#: ``metric=volume`` is refused rather than silently answered with percent.
METRIC_DEFAULT = "change"
METRICS: frozenset[str] = frozenset({"change"})

#: Timeframe → the row cell the change is read from and the metric the document
#: names. ``1d`` is the selector's ``change_pct``; ``1w``/``1m`` are parity-scan
#: cells (``Perf.W`` → ``ret_1w``, ``Perf.1M`` → ``ret_1m``) copied verbatim onto
#: the same universe row, so one scan serves every timeframe and no historical
#: store is required. An unknown token is a ``422 invalid_timeframe``.
TIMEFRAMES: Mapping[str, tuple[str, str]] = {
    "1d": ("change_pct", "change_1d"),
    "1w": ("ret_1w", "change_1w"),
    "1m": ("ret_1m", "change_1m"),
}
TIMEFRAME_DEFAULT = "1d"

#: The document's shape and meaning, folded into the tag. Bump it when a key
#: changes or a number changes meaning: clients replay tags across deploys, and a
#: tag that survives a shape change is a ``304`` for a body they cannot parse.
MAP_SHAPE = "1"


def build_map_document(
    rows: Iterable[Mapping[str, Any]], *, region: str, metric_name: str, as_of: str
) -> dict[str, Any]:
    """The treemap document, from one pass over the universe. Pure.

    The rows already have ``change_pct`` set to the selected window metric
    (``change_pct`` native, ``ret_1w``, ``ret_1m``); ``tally_sectors`` reads it
    directly. ``metric_name`` is what the document calls it on the wire
    (``change_1d``, ``change_1w``, ``change_1m``). Sectors are ordered
    ``count`` descending then ``id`` — biggest legend first, stable across
    calls, so two responses diff cleanly. Symbols inside a sector are ordered
    cap descending then symbol, which is the order a treemap draws them in and
    the order a client would have to sort them into anyway.
    """

    tallies = tally_sectors(rows)
    sectors: list[dict[str, Any]] = []
    mapped = 0
    for tally in sorted(tallies.values(), key=lambda item: (-item.count, item.id)):
        members = sorted(tally.members, key=lambda member: (-member[0], member[2]))
        symbols = [
            {
                "s": symbol,
                "c": capital_billions(cap),
                # `or 0.0` folds the one ugly float in JSON: round(-0.001, 2)
                # is -0.0, which serialises as "-0.0" and reads as a bug.
                "v": round(change, 2) or 0.0,
            }
            for cap, change, symbol in members
        ]
        mapped += len(symbols)
        average = tally.average
        sectors.append(
            {
                "id": tally.id,
                "label": tally.label,
                "count": tally.count,
                "value": None if average is None else round(average, 4),
                "adv": tally.adv,
                "dec": tally.dec,
                "symbols": symbols,
            }
        )
    return {
        "as_of": as_of,
        "region": region,
        "metric": metric_name,
        "universe_total": sum(tally.count for tally in tallies.values()),
        "mapped": mapped,
        "sectors": sectors,
    }


def map_revision(
    *, region: str, family: str, metric: str, generation: int, shape: str = MAP_SHAPE
) -> str:
    """The answer's identity, from the inputs that decide it."""

    return (
        "v1"
        f";region={region}"
        f";family={family}"
        f";metric={metric}"
        f";scan={int(generation)}"
        f";shape={shape}"
    )


def _build(
    rows: Iterable[Mapping[str, Any]],
    *,
    region: str,
    family: str,
    metric_name: str,
    generation: int,
) -> CachedDocument:
    """Build, encode and name one document (the memo miss path)."""

    document = build_map_document(
        rows, region=region, metric_name=metric_name, as_of=moment()
    )
    return CachedDocument(
        body=encode(document),
        etag=etag(
            "map",
            map_revision(
                region=region, family=family, metric=metric_name, generation=generation
            ),
        ),
        generation=int(generation),
    )


async def _map_answer(
    request: Request, *, region: str, timeframe: str, head: bool
) -> Response:
    """The document, its ``304``, or its ``HEAD`` — cheapest path first."""

    family = _universe_family(region, None)
    pair = TIMEFRAMES.get(timeframe)
    if pair is None:
        raise ApiError(
            status_code=422,
            error="invalid_timeframe",
            message=(
                f"timeframe must be one of {', '.join(sorted(TIMEFRAMES))};"
                f" got {timeframe!r}"
            ),
        )
    metric_field, metric_name = pair

    memo: BodyMemo = memo_for(request, "map")

    # A revalidation is decided from the generation alone: `stamp_for` is
    # read-only (no scan, no counter), and the memo holds the document that
    # generation produced. A matching tag costs a dict lookup and a comparison.
    stamp = stamp_for(request, region=region, family=family)
    if stamp is not None:
        cached = memo.get((region, family, metric_name, stamp))
        if cached is not None:
            return serve(request, cached, scan="hit", head=head)

    page = await universe_for(request, region=region, family=family)
    key = (region, family, metric_name, page.generation)
    cached = memo.get(key)
    if cached is None:
        rows = _rows_for_timeframe(page.rows, metric_field)
        cached = memo.put(
            key,
            _build(
                rows,
                region=region,
                family=family,
                metric_name=metric_name,
                generation=page.generation,
            ),
        )
    return serve(request, cached, scan=page.scan, head=head)


def _rows_for_timeframe(
    rows: Iterable[Mapping[str, Any]], metric_field: str
) -> list[dict[str, Any]]:
    """Rows with the chosen window cell copied onto ``change_pct``.

    ``tally_sectors`` reads ``change_pct``; the parity scan carries the window
    metric (``ret_1w``, ``ret_1m``) under its own name, so a windowed map is the
    same universe with one column renamed. ``None`` stays ``None`` — unknown is
    not flat — and the shallow copy keeps the universe's other cells untouched.
    """

    if metric_field == "change_pct":
        return list(rows)  # type: ignore[return-value]
    output: list[dict[str, Any]] = []
    for row in rows:
        copied: dict[str, Any] = dict(row)
        copied["change_pct"] = row.get(metric_field)
        output.append(copied)
    return output


@router.get("/market/map")
async def market_map(
    request: Request, region: str = "us", timeframe: str = TIMEFRAME_DEFAULT
) -> Response:
    """Every tile of the market, in one payload (GET)."""

    return await _map_answer(request, region=region, timeframe=timeframe, head=False)


@router.head("/market/map", include_in_schema=False)
async def market_map_head(
    request: Request, region: str = "us", timeframe: str = TIMEFRAME_DEFAULT
) -> Response:
    """The same headers as GET, no body: a monitor's question, not the document."""

    return await _map_answer(request, region=region, timeframe=timeframe, head=True)


__all__ = [
    "MAP_SHAPE",
    "METRICS",
    "METRIC_DEFAULT",
    "TIMEFRAMES",
    "TIMEFRAME_DEFAULT",
    "build_map_document",
    "map_revision",
    "market_map",
    "market_map_head",
    "router",
]
