"""``GET /market/groups`` — the sector table beside the treemap.

Why this route exists
---------------------
The map answers "where is the market", the way a treemap does: every name, once,
sized and coloured. A reader also wants the row above it — how many names in each
sector, how far the sector moved, how many advanced and declined, how big it is —
and a client cannot compute that from the map without walking 5,573 tiles. So the
same one pass publishes the table too.

One population, one pass, one definition of "the sector moved"
--------------------------------------------------------------
The population is the selector's, built by
:func:`app.api.sectors.universe_for` with the selector's own arguments and scan
key; the pass is :func:`app.api.sectors.tally_sectors`, the **same function** the
map reads. That is not a convenience: a sector's ``avg_change`` here and its
``value`` there are one number computed once, so the legend and the table cannot
disagree about how far Electronic Technology moved.

``avg_change`` is **cap-weighted** — ``sum(cap × change) / sum(cap)`` over the
members that carry both — and it is attached to ``market_cap``, which is the sum
of every member's cap in USD (an integer, not billions: this document is a
kilobyte, so there is no reason to lose the dollars). The alternative, a plain
mean of the percents, would let a USD 40 M microcap outvote a USD 3 T issuer in a
number a reader compares across sectors of very different size.

Counts that add up
------------------
``count`` is every row in the sector, and ``adv + dec + unchanged == count`` by
construction: ``adv`` is a positive change, ``dec`` a negative one, and
``unchanged`` is everything else — exactly zero **or** no metric at all. A name
with no change did not "advance", and calling it unchanged is the honest reading
of "we have no move for it"; the map says the same thing by leaving it out of the
tiles while keeping it in ``count``.

Order
-----
``avg_change`` descending, then ``label`` ascending, with a sector that has no
weighable member sorted last (``null`` is not a small number). Two calls diff
cleanly and the table reads top-down as a leaderboard.

Revalidation
------------
``W/"grp-<sha256(v1;region=…;family=…;scan=<generation>;shape=1)>"``, decided from
:func:`app.api.sectors.stamp_for` before any build, with ``Cache-Control:
no-cache`` on both answers and ``HEAD`` as its own route. The body is small (a
couple of kilobytes) but the work behind it is not — one 5,576-row pass — so the
``304`` is worth more here than its size suggests.
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
    encode,
    etag,
    memo_for,
    moment,
    serve,
    stamp_for,
    tally_sectors,
    universe_for,
)

router = APIRouter(tags=["discovery"])

#: The document's shape and meaning, folded into the tag (see ``MAP_SHAPE``).
GROUPS_SHAPE = "1"


def build_groups_document(
    rows: Iterable[Mapping[str, Any]], *, region: str, as_of: str
) -> dict[str, Any]:
    """The sector table, from one pass over the universe. Pure."""

    tallies = tally_sectors(rows)
    groups: list[dict[str, Any]] = []
    for tally in tallies.values():
        average = tally.average
        groups.append(
            {
                "id": tally.id,
                "label": tally.label,
                "count": tally.count,
                "avg_change": None if average is None else round(average, 4),
                "adv": tally.adv,
                "dec": tally.dec,
                "unchanged": tally.count - tally.adv - tally.dec,
                "market_cap": int(round(tally.cap_total)),
            }
        )
    # `-(-inf)` sorts a sector with nothing to weigh to the end: an unknown move
    # is not the smallest move. Ties break on the label, so the order is total.
    groups.sort(
        key=lambda group: (
            -(group["avg_change"] if group["avg_change"] is not None else float("-inf")),
            group["label"],
        )
    )
    return {
        "as_of": as_of,
        "region": region,
        "universe_total": sum(group["count"] for group in groups),
        "groups": groups,
    }


def groups_revision(
    *, region: str, family: str, generation: int, shape: str = GROUPS_SHAPE
) -> str:
    """The answer's identity, from the inputs that decide it."""

    return (
        "v1"
        f";region={region}"
        f";family={family}"
        f";scan={int(generation)}"
        f";shape={shape}"
    )


def _build(
    rows: Iterable[Mapping[str, Any]],
    *,
    region: str,
    family: str,
    generation: int,
) -> CachedDocument:
    """Build, encode and name one document (the memo miss path)."""

    document = build_groups_document(rows, region=region, as_of=moment())
    return CachedDocument(
        body=encode(document),
        etag=etag(
            "grp",
            groups_revision(region=region, family=family, generation=generation),
        ),
        generation=int(generation),
    )


async def _groups_answer(request: Request, *, region: str, head: bool) -> Response:
    """The document, its ``304``, or its ``HEAD`` — cheapest path first."""

    family = _universe_family(region, None)
    memo: BodyMemo = memo_for(request, "groups", limit=32)

    stamp = stamp_for(request, region=region, family=family)
    if stamp is not None:
        cached = memo.get((region, family, stamp))
        if cached is not None:
            return serve(request, cached, scan="hit", head=head)

    page = await universe_for(request, region=region, family=family)
    key = (region, family, page.generation)
    cached = memo.get(key)
    if cached is None:
        cached = memo.put(
            key,
            _build(
                page.rows,
                region=region,
                family=family,
                generation=page.generation,
            ),
        )
    return serve(request, cached, scan=page.scan, head=head)


@router.get("/market/groups")
async def market_groups(request: Request, region: str = "us") -> Response:
    """Every sector with its counts, its weighted move and its size (GET)."""

    return await _groups_answer(request, region=region, head=False)


@router.head("/market/groups", include_in_schema=False)
async def market_groups_head(request: Request, region: str = "us") -> Response:
    """The same headers as GET, no body."""

    return await _groups_answer(request, region=region, head=True)


__all__ = [
    "GROUPS_SHAPE",
    "build_groups_document",
    "groups_revision",
    "market_groups",
    "market_groups_head",
    "router",
]
