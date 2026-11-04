"""``GET /market/calendar`` — the earnings calendar, from the depth store.

Why this route reads the store and not a scan
---------------------------------------------
Every earnings date the product owns is already collected: the depth collector
writes ``earnings_date`` into ``data/depth.json`` for 3,730 of its 4,217 symbols,
on its own daily cycle. A calendar therefore needs **no provider scan at all** —
it is a filter and a group-by over one file — which is why this route answers
``X-Scan-Cache: depth`` and never ``hit``/``miss``: there is no scan cache
involved, and saying so is cheaper than leaving a client to guess.

That also decides the validator: the store's **file version** (``path``,
``mtime_ns``, ``size``) and the window are the whole answer's input. The tag is
built from them before the file is parsed, so a warm ``If-None-Match`` is a
``304`` after one ``stat`` — no 11.7 MB parse, no pass over 4,217 symbols. The
collector rewrites the file atomically, so a new cycle always moves ``mtime_ns``,
and a window that crosses midnight moves ``from``/``to`` and therefore the tag.

Two date spellings, one calendar
--------------------------------
The store carries two, because two providers wrote it: ISO (``2026-11-23``,
3,263 symbols) and the page's own ``Sep 28 BMO`` / ``Sep 29 AMC`` (467). Both are
read here; anything else is not a date and contributes nothing. A year-less date
is the **next occurrence on or after today** — "Jan 5" asked in late December is
next year's, and a date the provider already passed this year rolls forward a
year and then falls outside the window, which is the right outcome for a
forward-looking calendar.

The window is exact, and the counts do not lie
----------------------------------------------
``from`` is today and ``to`` is ``today + days``, both inclusive, both echoed in
the document so a client can label the axis without recomputing it. ``days`` is
clamped to :data:`DAYS_MIN`…:data:`DAYS_MAX`: ``days=0`` is one day (today) and
``days=999`` is sixty, because a caller asking for either is asking for the
nearest useful window, not for an error. Dates outside the window are dropped —
including every past date in the store, and any null cell (a null is not a date,
so it can never land on today).

At most :data:`BUCKET_LIMIT` symbols are published per bucket, ordered cap
descending then symbol; ``count`` carries the **true** total, so the live 5
November cluster — 506 names — still says 506 while shipping its fifty largest.
A region with no date in the window answers ``buckets: []`` — a statement about
the store, not a 404.

Region
------
The store is one collector's: 4,188 of its rows name the United States and 29
name another country. ``us``/``us_etf`` publish the US rows plus a row whose
country is missing (the symbol list *is* the US collector's; one absent cell is
not evidence of a foreign listing), and ``global`` publishes the rest. That is a
partition, so the two tabs describe one population between them.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.api.discovery import _universe_family
from app.api.sectors import (
    CachedDocument,
    encode,
    etag,
    identity,
    memo_for,
    moment,
    number,
    serve,
    text,
)
from app.auth import ApiError
from app.screen.depth_source import current_depth, resolve_store_path

logger = logging.getLogger("mee.calendar")

router = APIRouter(tags=["discovery"])

#: The calendar kinds the route answers. Earnings and dividends come from the
#: depth store; economic is pending a source.
_CALENDAR_KINDS: frozenset[str] = frozenset({"earnings", "dividends", "economic"})

#: The default window, in days, and the clamp a caller's ``days`` is held to.
#: Three weeks is one earnings season's visible edge; sixty is two months, past
#: which "the next earnings" stops being a calendar and becomes a forecast.
DAYS_DEFAULT = 21
DAYS_MIN = 1
DAYS_MAX = 60

#: How many symbols one bucket publishes. ``count`` keeps the true total.
BUCKET_LIMIT = 50

#: The document's shape and meaning, folded into the tag.
CALENDAR_SHAPE = "1"

#: The country spellings the US tab owns. Same set the universe builder uses, so
#: "us" means one thing across the API.
_US_COUNTRIES: frozenset[str] = frozenset(
    {"united states", "united states of america", "usa", "us"}
)

#: ``2026-11-23``.
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

#: ``Sep 28``, ``Sep 28 BMO``, ``September 28 AMC``. The provider prints the
#: session marker after the day, not before it.
_MONTH_DAY = re.compile(r"^([A-Za-z]{3,9})\.? (\d{1,2})(?: (?:BMO|AMC))?$")

#: Month names as a table rather than ``strptime("%b")``: ``%b`` reads the process
#: locale, and a container with ``LANG`` set would parse "mai" and refuse "may".
_MONTHS: dict[str, int] = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _today() -> date:
    """The calendar's first day. A function so a test can move the clock."""

    return datetime.now(UTC).date()


def _days(raw: str) -> int:
    """``days`` as an integer clamped to the window, or ``422 invalid_days``.

    Clamped rather than refused: a caller asking for 0 days or 999 is asking for
    the nearest useful window, and answering with the nearest edge is more useful
    than a 422 about a parameter whose intent is clear.
    """

    token = str(raw).strip()
    try:
        value = int(token, 10)
    except (TypeError, ValueError):
        raise ApiError(
            status_code=422,
            error="invalid_days",
            message=f"days must be a whole number of days; got {raw!r}",
        ) from None
    return max(DAYS_MIN, min(DAYS_MAX, value))


def parse_earnings_date(raw: Any, *, today: date) -> date | None:
    """One ``earnings_date`` cell as a date, or ``None`` when it is not one.

    ``None`` for a null, a blank, a number, a sentence, an impossible day (31
    February) or a month name the table does not carry. A null in particular can
    never become today: there is no parse that turns absence into a date.
    """

    value = text(raw)
    if value is None:
        return None
    match = _ISO_DATE.match(value)
    if match is not None:
        year, month, day = (int(part) for part in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    match = _MONTH_DAY.match(value)
    if match is None:
        return None
    month = _MONTHS.get(match.group(1)[:3].casefold())
    if month is None:
        return None
    day = int(match.group(2))
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if candidate >= today:
            return candidate
    return None


def _in_region(fields: Mapping[str, Any], region: str) -> bool:
    """Whether a depth row belongs to ``region``'s tab."""

    country = text(fields.get("country"))
    is_us = country is None or country.casefold() in _US_COUNTRIES
    return is_us if region in ("us", "us_etf") else not is_us


def build_calendar_document(
    symbols: Mapping[str, Mapping[str, Any]],
    *,
    region: str,
    days: int,
    today: date,
    as_of: str,
    kind: str = "earnings",
) -> dict[str, Any]:
    """The calendar document, from one pass over the depth rows. Pure.

    Buckets are ordered by date (the only order a calendar has) and only dates in
    ``[today, today + days]`` are published. Inside a bucket, names sort by market
    cap descending then symbol, so the fifty that survive truncation are the
    fifty a reader is most likely to be looking for.

    ``kind`` selects the date field: ``earnings_date`` for earnings,
    ``dividend_ex_date`` for dividends.
    """

    field_map = {"earnings": "earnings_date", "dividends": "dividend_ex_date"}
    date_field = field_map.get(kind, "earnings_date")

    end = today + timedelta(days=days)
    buckets: dict[date, list[tuple[float, str, str | None]]] = {}
    for symbol, fields in symbols.items():
        if not isinstance(fields, Mapping) or not _in_region(fields, region):
            continue
        when = parse_earnings_date(fields.get(date_field), today=today)
        if when is None or when < today or when > end:
            continue
        cap = number(fields.get("market_cap"))
        buckets.setdefault(when, []).append(
            (cap if cap is not None else float("-inf"), str(symbol), text(fields.get("company")))
        )

    published: list[dict[str, Any]] = []
    for when in sorted(buckets):
        members = sorted(buckets[when], key=lambda member: (-member[0], member[1]))
        published.append(
            {
                "date": when.isoformat(),
                "count": len(members),
                "symbols": [
                    {"s": symbol, "n": name}
                    for _, symbol, name in members[:BUCKET_LIMIT]
                ],
            }
        )
    return {
        "as_of": as_of,
        "region": region,
        "from": today.isoformat(),
        "to": end.isoformat(),
        "buckets": published,
    }


def calendar_revision(
    *, region: str, days: int, start: date, depth: str, kind: str = "earnings", shape: str = CALENDAR_SHAPE
) -> str:
    """The answer's identity: the window and the store version, nothing else."""

    return (
        "v1"
        f";region={region}"
        f";days={int(days)}"
        f";from={start.isoformat()}"
        f";depth={depth}"
        f";kind={kind}"
        f";shape={shape}"
    )


def _depth_identity(request: Request) -> str:
    """The depth store's file version, or why there is not one.

    The path comes from the **application's** settings, not from a fresh
    :func:`app.config.get_settings` call: building a ``Settings`` object reads and
    validates every ``MEE_*`` variable and costs 5.2 ms, which is more than the
    whole ``304`` this identity exists to make cheap.
    """

    runtime = getattr(request.app.state, "runtime", None)
    settings = getattr(runtime, "settings", None)
    try:
        path = resolve_store_path(settings)
    except Exception as exc:  # noqa: BLE001 - an unusable configuration is a version
        # Never silent: a store path that cannot be resolved answers "unknown",
        # which makes every request a 200, and without this line the reason would
        # be invisible for as long as the misconfiguration lasted.
        logger.warning("calendar revision: the depth store path is unusable: %s", exc)
        return "unknown"
    return identity(None if path is None else str(path))


async def _calendar_answer(
    request: Request, *, region: str, days: str, kind: str, head: bool
) -> Response:
    """The document, its ``304``, or its ``HEAD`` — the ``304`` before any parse."""

    _universe_family(region, None)  # validates the region, 422 in the envelope
    window = _days(days)
    start = _today()
    store = _depth_identity(request)
    memo = memo_for(request, "calendar", limit=16)
    key = (kind, region, window, start.isoformat(), store)
    cached = memo.get(key)
    if cached is None:
        # The store's own stamp is the honest `as_of` for earnings dates: they do
        # not change when this request happened to be served.
        runtime = getattr(request.app.state, "runtime", None)
        snapshot = current_depth(getattr(runtime, "settings", None))
        document = build_calendar_document(
            snapshot.symbols,
            region=region,
            days=window,
            today=start,
            as_of=snapshot.as_of or moment(),
            kind=kind,
        )
        cached = memo.put(
            key,
            CachedDocument(
                body=encode(document),
                etag=etag(
                    "cal",
                    calendar_revision(
                        region=region, days=window, start=start, depth=store, kind=kind
                    ),
                ),
                generation=0,
            ),
        )
    return serve(request, cached, scan="depth", head=head)


def _unavailable_document(*, region: str, days: str, reason: str) -> dict[str, Any]:
    """The honest answer for a calendar whose source is not integrated yet.

    ``available: false`` is a statement about the data, not a failure: the
    window is still named, so a client can label the axis it would have drawn.
    """

    start = _today()
    window = _days(days)
    return {
        "available": False,
        "reason": reason,
        "region": region,
        "from": start.isoformat(),
        "to": (start + timedelta(days=window)).isoformat(),
        "buckets": [],
    }


def _kind(raw: str) -> str:
    """The calendar kind, or ``422 invalid_kind``."""

    kind = str(raw).strip().casefold()
    if kind not in _CALENDAR_KINDS:
        raise ApiError(
            status_code=422,
            error="invalid_kind",
            message=f"kind must be earnings, dividends, or economic; got {kind!r}",
        )
    return kind


@router.get("/market/calendar")
async def market_calendar(
    request: Request,
    region: str = "us",
    days: str = str(DAYS_DEFAULT),
    kind: str = "earnings",
) -> Response:
    """Calendar dates inside the window, grouped by day (GET).

    ``kind`` selects the calendar: ``earnings`` reads ``earnings_date`` from the
    depth store, ``dividends`` reads ``dividend_ex_date``, and ``economic``
    answers ``available: false`` until a source is integrated.
    """

    resolved = _kind(kind)
    if resolved == "economic":
        document = _unavailable_document(
            region=region, days=days, reason="no economic calendar source"
        )
        return Response(
            content=encode(document),
            media_type="application/json",
        )

    return await _calendar_answer(
        request, region=region, days=days, kind=resolved, head=False
    )


@router.head("/market/calendar", include_in_schema=False)
async def market_calendar_head(
    request: Request,
    region: str = "us",
    days: str = str(DAYS_DEFAULT),
    kind: str = "earnings",
) -> Response:
    """The same headers as GET, no body."""

    resolved = _kind(kind)
    if resolved == "economic":
        document = _unavailable_document(
            region=region, days=days, reason="no economic calendar source"
        )
        body = encode(document)
        return Response(
            status_code=200,
            headers={"Content-Length": str(len(body))},
        )

    return await _calendar_answer(
        request, region=region, days=days, kind=resolved, head=True
    )


__all__ = [
    "BUCKET_LIMIT",
    "CALENDAR_SHAPE",
    "DAYS_DEFAULT",
    "DAYS_MAX",
    "DAYS_MIN",
    "build_calendar_document",
    "calendar_revision",
    "market_calendar",
    "market_calendar_head",
    "parse_earnings_date",
    "router",
]
