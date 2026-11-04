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

Group-by
--------
``group_by`` asks :func:`~app.api.sectors.tally_sectors` to read a different
column: ``"sector"`` (the default, the provider's own twenty-one-name taxonomy),
``"industry"`` (the parity scan's ``industry`` cell), ``"country"``, or
``"capitalization"`` (which buckets ``market_cap_usd`` into five stable brackets).
An unknown value is refused with a ``422``.

Sort
----
``sort`` names the key and ``direction`` picks ``"asc"`` or ``"desc"``; the
default (``avg_change desc``) is unchanged. Valid sort keys: ``name``, ``count``,
``avg_change``, ``market_cap``. A group whose sort value is ``None`` sorts last
(ascending) or first (descending), so an unmeasured number is never confused
with a measured one.

Multi-timeframe performance
---------------------------
Every group also carries ``perf_1w``, ``perf_1m`` and ``perf_3m`` — the
cap-weighted mean of ``ret_1w`` / ``ret_1m`` / ``ret_3m`` from the parity scan.
A group whose members carry no window value publishes ``null``.

Counts that add up
------------------
``count`` is every row in the group, and ``adv + dec + unchanged == count`` by
construction: ``adv`` is a positive change, ``dec`` a negative one, and
``unchanged`` is everything else — exactly zero **or** no metric at all. A name
with no change did not "advance", and calling it unchanged is the honest reading
of "we have no move for it"; the map says the same thing by leaving it out of the
tiles while keeping it in ``count``.

Revalidation
------------
``W/"grp-<sha256(v1;region=…;family=…;scan=<generation>;shape=2)">``, decided from
:func:`app.api.sectors.stamp_for` before any build, with ``Cache-Control:
no-cache`` on both answers and ``HEAD`` as its own route. The body is small (a
couple of kilobytes) but the work behind it is not — one 5,576-row pass — so the
``304`` is worth more here than its size suggests.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import APIRouter, Query, Request
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
from app.auth import ApiError

router = APIRouter(tags=["discovery"])

#: The document's shape and meaning, folded into the tag (see ``MAP_SHAPE``).
#: Bumped to ``"2"`` because ``group_by``, ``sort``, ``direction`` and perf
#: columns change the document's contract.
GROUPS_SHAPE = "2"

#: Values ``group_by`` accepts.  ``sector`` (the default) reads the provider's
#: ``sector`` cell; ``industry`` reads the parity scan's ``industry``;
#: ``country`` reads the scan's ``country``; ``capitalization`` buckets
#: ``market_cap_usd`` into five brackets.
GROUP_BY_VALUES: frozenset[str] = frozenset({"sector", "industry", "country", "capitalization"})
GROUP_BY_DEFAULT = "sector"

#: Sort keys the endpoint accepts.
SORT_KEYS: frozenset[str] = frozenset({"name", "count", "avg_change", "market_cap"})
SORT_DEFAULT = "avg_change"


def _sort_key(sort: str, direction: str):
    """A key function for a group dict, with ``None``-last semantics."""
    reverse = direction == "desc"

    def key(group: dict[str, Any]):
        value = group.get(_SORT_FIELD_MAP.get(sort, sort))
        if value is None:
            # None sorts last ascending, first descending — unknown is not
            # the smallest number.
            return (0 if reverse else 1, 0)
        return (1 if reverse else 0, -(value if isinstance(value, (int, float)) else 0) if reverse else value)

    return key


_SORT_FIELD_MAP: dict[str, str] = {
    "name": "label",
    "count": "count",
    "avg_change": "avg_change",
    "market_cap": "market_cap",
}


def _round4(value: float | None) -> float | None:
    """``None`` passes through; a float rounds to four decimal places."""
    return None if value is None else round(value, 4)


def build_groups_document(
    rows: Iterable[Mapping[str, Any]],
    *,
    region: str,
    as_of: str,
    group_by: str = GROUP_BY_DEFAULT,
    sort: str = SORT_DEFAULT,
    direction: str = "desc",
) -> dict[str, Any]:
    """The groups table, from one pass over the universe. Pure."""

    tallies = tally_sectors(rows, group_by=group_by)
    groups: list[dict[str, Any]] = []
    for tally in tallies.values():
        groups.append(
            {
                "id": tally.id,
                "label": tally.label,
                "count": tally.count,
                "avg_change": _round4(tally.average),
                "adv": tally.adv,
                "dec": tally.dec,
                "unchanged": tally.count - tally.adv - tally.dec,
                "market_cap": int(round(tally.cap_total)),
                "perf_1w": _round4(tally.perf_1w),
                "perf_1m": _round4(tally.perf_1m),
                "perf_3m": _round4(tally.perf_3m),
            }
        )

    # Default sort: avg_change descending, label ascending.
    groups.sort(
        key=lambda group: (
            -(group["avg_change"] if group["avg_change"] is not None else float("-inf")),
            group["label"],
        )
    )

    # Apply requested sort if different from default.
    if sort != SORT_DEFAULT or direction != "desc":
        sort_atom = _SORT_FIELD_MAP.get(sort, sort)
        if direction == "desc":
            groups.sort(
                key=lambda g: (
                    -((g[sort_atom] if g.get(sort_atom) is not None else -1e308) if isinstance(g.get(sort_atom), (int, float)) else 0),
                    g["label"],
                )
            )
        else:
            groups.sort(
                key=lambda g: (
                    (g[sort_atom] if g.get(sort_atom) is not None else 1e308) if isinstance(g.get(sort_atom), (int, float)) else 0,
                    g["label"],
                )
            )

    return {
        "as_of": as_of,
        "region": region,
        "group_by": group_by,
        "universe_total": sum(group["count"] for group in groups),
        "groups": groups,
    }


def groups_revision(
    *,
    region: str,
    family: str,
    generation: int,
    shape: str = GROUPS_SHAPE,
    group_by: str = GROUP_BY_DEFAULT,
) -> str:
    """The answer's identity, from the inputs that decide it."""

    return (
        "v1"
        f";region={region}"
        f";family={family}"
        f";scan={int(generation)}"
        f";shape={shape}"
        f";group_by={group_by}"
    )


def _build(
    rows: Iterable[Mapping[str, Any]],
    *,
    region: str,
    family: str,
    generation: int,
    group_by: str = GROUP_BY_DEFAULT,
    sort: str = SORT_DEFAULT,
    direction: str = "desc",
) -> CachedDocument:
    """Build, encode and name one document (the memo miss path)."""

    document = build_groups_document(
        rows,
        region=region,
        as_of=moment(),
        group_by=group_by,
        sort=sort,
        direction=direction,
    )
    return CachedDocument(
        body=encode(document),
        etag=etag(
            "grp",
            groups_revision(
                region=region, family=family, generation=generation, group_by=group_by
            ),
        ),
        generation=int(generation),
    )


async def _groups_answer(
    request: Request,
    *,
    region: str,
    head: bool,
    group_by: str = GROUP_BY_DEFAULT,
    sort: str = SORT_DEFAULT,
    direction: str = "desc",
) -> Response:
    """The document, its ``304``, or its ``HEAD`` — cheapest path first."""

    family = _universe_family(region, None)
    memo: BodyMemo = memo_for(request, "groups", limit=32)

    stamp = stamp_for(request, region=region, family=family)
    if stamp is not None:
        cached = memo.get((region, family, group_by, stamp))
        if cached is not None:
            return serve(request, cached, scan="hit", head=head)

    page = await universe_for(request, region=region, family=family)
    key = (region, family, group_by, page.generation)
    cached = memo.get(key)
    if cached is None:
        cached = memo.put(
            key,
            _build(
                page.rows,
                region=region,
                family=family,
                generation=page.generation,
                group_by=group_by,
                sort=sort,
                direction=direction,
            ),
        )
    return serve(request, cached, scan=page.scan, head=head)


@router.get("/market/groups")
async def market_groups(
    request: Request,
    region: str = "us",
    group_by: str = Query(GROUP_BY_DEFAULT),
    sort: str = Query(SORT_DEFAULT),
    direction: str = Query("desc"),
) -> Response:
    """Every group with its counts, its weighted move and its size (GET).

    ``group_by``: ``"sector"`` (default), ``"industry"``, ``"country"``, or
    ``"capitalization"``.  Unknown is a ``422``.

    ``sort``: ``"avg_change"`` (default), ``"name"``, ``"count"``, or
    ``"market_cap"``.  Unknown is a ``422``.

    ``direction``: ``"asc"`` or ``"desc"`` (default).  Unknown is a ``422``.
    """

    if group_by not in GROUP_BY_VALUES:
        raise ApiError(
            status_code=422,
            error="invalid_group_by",
            message=(
                f"group_by must be one of {', '.join(sorted(GROUP_BY_VALUES))};"
                f" got {group_by!r}"
            ),
        )

    if sort not in SORT_KEYS:
        raise ApiError(
            status_code=422,
            error="invalid_sort",
            message=(
                f"sort must be one of {', '.join(sorted(SORT_KEYS))};"
                f" got {sort!r}"
            ),
        )

    if direction not in ("asc", "desc"):
        raise ApiError(
            status_code=422,
            error="invalid_direction",
            message=f"direction must be 'asc' or 'desc'; got {direction!r}",
        )

    return await _groups_answer(
        request,
        region=region,
        head=False,
        group_by=group_by,
        sort=sort,
        direction=direction,
    )


@router.head("/market/groups", include_in_schema=False)
async def market_groups_head(
    request: Request,
    region: str = "us",
    group_by: str = Query(GROUP_BY_DEFAULT),
    sort: str = Query(SORT_DEFAULT),
    direction: str = Query("desc"),
) -> Response:
    """The same headers as GET, no body."""

    return await _groups_answer(
        request,
        region=region,
        head=True,
        group_by=group_by,
        sort=sort,
        direction=direction,
    )


__all__ = [
    "GROUPS_SHAPE",
    "GROUP_BY_DEFAULT",
    "GROUP_BY_VALUES",
    "SORT_DEFAULT",
    "SORT_KEYS",
    "build_groups_document",
    "groups_revision",
    "market_groups",
    "market_groups_head",
    "router",
]