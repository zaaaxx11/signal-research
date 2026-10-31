"""``GET /screens``, ``POST /screens/select`` and ``POST /screens/{id}/export``.

Browse is a display, so the route has one job: serve the snapshot document. It
never paginates (no ``cursor`` parameter exists) and it never lets ``limit``
widen the page — ``limit`` is accepted so a client that already sends it is not
broken by a 422, and ignored past :data:`~app.screen.snapshot.DISPLAY_ROWS`.

**Two sources, one display.** ``source`` is a closed two-value vocabulary:

* ``multi`` (default) — the twenty candidates of the snapshot, each cell the
  median of TradingView plus the live verifiers (see
  :mod:`app.screen.multi_source`). When every verifier fails, the service answers
  the raw snapshot with ``source="tradingview"`` and ``degraded=true``; it never
  labels an unverified page ``multi``.
* ``tradingview`` — the raw snapshot document, byte-for-byte what this route
  served before P10. It exists so the verified view can be audited against its
  own selection, and because an operator debugging a verifier needs the input.

An unknown ``source`` is a 422 before any scan: the vocabulary is closed, and a
silently-ignored value would serve a page the caller did not ask for.

Select is the query half: ``POST /screens/select`` takes the two-mode JSON
selection (a criteria query or an explicit ticker list) and answers the page
**plus the post-filter total**, because a display that only says "20 rows" is
how a five-row filter gets read as a five-stock market. It runs one bulk scan
per call for the same reason the export's ``all`` scope does: ``screen_rows``
holds the 20 displayed rows, and a total computed over those 20 would be a
number nobody can reproduce. The scan a criteria query runs is the **parity**
one: it asks for the metric columns the filters address and drops the browse
floor (``min_market_cap``) and the ``subtype=common`` gate, so "P/E below 10"
means the whole stock market rather than the 100M+ common shares the display
happens to show. ``symbols`` mode keeps the light scan — a ticker lookup needs
no metric. Every selection then joins the fundamentals cells the scan cannot answer
(``pe_forward``, ``ps``, ``payout_ratio``) and derives the two ratios over them
(``price_to_cash``, ``ev_to_sales``) from the runtime's
fundamentals snapshot store, once per call and by symbol — see
:mod:`app.screen.fundamentals`. The same call then joins the three short and
ownership cells (``short_float``, ``insider_own``, ``inst_own``) from the
runtime's facts store, once per call and by symbol — see :mod:`app.screen.facts`.
``GET /screens/select/schema`` serves the request
schema — the same document that ships at
``schemas/stock_selection_request_v1.json``.

Validation lives in :mod:`app.screen.selection` (one
:class:`~app.screen.selection.SelectionError`, one 422 code per failure kind), so
this module only maps a validated request onto a scan and the executor.

**The answer is cacheable, and the validator names its inputs.** ``POST
/screens/select`` returns a **weak** ``ETag`` computed **before the answer is
built**: the depth store's file identity, the snapshot's id and stamp, the scan
page's generation, the two enrichment stores' row count and newest
``updated_at``, the brand-asset directory, and the canonical request. A matching
``If-None-Match`` is answered ``304`` without building anything, because a
revalidation whose work is the whole answer has saved the bytes and nothing
else. (Phase 7 measured the old shape: 744 ms p50 against a 150 ms budget — the
tag *was* the answer's digest, so it could only be computed after
``build_universe``, both joins and ``select_rows`` had run.)

The digest was the one revision that could not silently omit an input, and it
was also the one that could not be computed early. Every input is named instead
(:func:`_data_revision` lists them) and the trade is explicit: a store that
cannot describe its own revision answers a **unique** token, so the route pays a
``200`` rather than certify a body it never read. ``as_of`` stays out — it is
the read moment, and a tag that moved with the clock would never match; that is
why the tag is weak.

**One scan serves a burst, but not a second longer.** The scan is the expensive
half of a selection (measured: 2.0 s live for the 5,715-row US universe, 12.5 s
when this box is loaded, and the panel's own build is a further ~0.4 s), and a
view switch changes only which columns are projected, not which universe is
read. So the universe comes through a short-TTL, single-flight :class:`ScanCache`
and every panel inside the window reuses it. The cache holds the *scan*, never
the answer: the depth and fundamentals/facts reads happen per request, so a
store that changes still moves the tag. It expires hard at
:data:`SCAN_CACHE_TTL_S` — no stale-while-revalidate, no stale-on-error — and
the ``X-Scan-Cache`` header says whether a given answer was a ``miss``, a
``hit`` or ``shared``.

The cache also hands out a **generation** with every page (:meth:`ScanCache.page`
returns it), and the tag carries the generation of the page it was built from.
That is what makes the fast path safe: a tag can only ever name the page the
answer used, so a concurrent scan cannot mint a ``304`` for rows a client never
saw. Once the window closes no generation is available, the route must build,
and the next tag carries the next number — a revalidation after the TTL is a
``200``, which is right, because it has to re-scan to know.

**One panel, one budget.** A selection page is at most :data:`PANEL_BUDGET_BYTES`
(60,000 B) whatever way it is asked for — ``columns`` or ``view``. The two used
to differ by 3× (24 KB against 274 KB for the same 100 rows), because the budget
was enforced only for a projection. A body that has to shrink says so: it
carries a ``budget`` object naming the bytes it shipped, whether it was
truncated, which fields went and how many rows were cut from the tail.

**Two counts, two names.** ``GET /screens`` reports ``matched`` (the rows its own
question matched) beside ``universe_total`` (the universe it ran over, which on
that route is the same set because browse applies no filter). The shipped client
used to print this route's ``total`` and the selection route's ``total`` under one
label, "Universe", although the selection route answers over the whole market
rather than the display's 100M-floor subset.

Export is the two-scope bundle and it runs as a **response background task**,
the same way ``POST /markets/enrich`` runs a job: the 202 body leaves first, the
work is attached to the response instead of a bare ``create_task`` (which can be
destroyed mid-write when the caller's loop goes away). Preflight happens before
a job row exists — an unknown screen is a 404, an unknown format a 422, and
neither leaves a Pending job behind to poll forever.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from app.api.cache import cache_headers, not_modified
from app.api.markets import runtime_of
from app.auth import ApiError, owner_fingerprint
from app.branding import logo_for
from app.export.manifest import file_sha256
from app.runtime import Runtime
from app.screen.export import (
    DATA_FORMATS,
    EXPORT_FORMATS,
    ScreenNotFound,
    UnknownScreenFormat,
    export_bundle,
)
from app.screen.facts import enrich_rows as enrich_facts
from app.screen.depth_overlay import SCREEN_ALIASES, overlay
from app.screen.depth_source import current_depth, resolve_store_path
from app.screen.fundamentals import enrich_rows
from app.screen.schema import SCREEN_RESPONSE_SCHEMA
from app.screen.selection import (
    ETAG_NAMESPACE,
    SELECTION_REQUEST_SCHEMA,
    SelectionError,
    SelectionRequest,
    parse_selection,
    request_digest,
    select_rows,
)
from app.screen.snapshot import DISPLAY_ROWS, SOURCE, load_screen
from app.screen.scanner import DEFAULT_FLOOR, FULL_SCAN_ROWS, ScanError, ScanPage, fetch_scan
from app.screen.universe import PARITY_COLUMNS, asset_class_for, build_universe
from app.screen.wide_fill import WideFiller

logger = logging.getLogger("mee.api.screens")

router = APIRouter(prefix="/screens", tags=["screens"])

#: ``region`` is a closed vocabulary (PLAN-screen.md §2): ``idx`` is not a
#: smaller US, and a silently-ignored region would serve the wrong tab.
#: ``us_etf`` is the US fund tab (``type=fund``/``subtype=etf``).
Region = Literal["us", "global", "us_etf"]
#: The three transparent sorts (PLAN §3).
Sort = Literal["market_cap", "turnover", "gainers"]
#: How the displayed cells were produced (P10): ``multi`` is the verified
#: consensus, ``tradingview`` the raw snapshot it verified. Closed on purpose.
ScreenSource = Literal["multi", "tradingview"]

#: The job service name a client sees in ``GET /requests/{id}``.
SCREEN_EXPORT_SERVICE = "screen_export"


#: The panel budget: a 100-row page of nine to twelve numeric columns is ~25 KB,
#: and the plan promises 60,000 B for one panel response. It is enforced on the
#: finished body — ``columns`` and ``view`` alike — because the same 100 rows used
#: to cost 24 KB one way and 274 KB the other, with nothing in either response
#: saying which one the caller got.
PANEL_BUDGET_BYTES = 60_000

#: The directory :func:`app.branding.logos.logo_for` resolves vendored marks
#: against, spelled with the same expression that module uses so the two cannot
#: drift silently (``tests/test_screen_select_revalidation.py`` pins the
#: equality). It is a revision input because a row's ``logo`` is a **URL into
#: this directory**: adding an asset changes the cell a row carries.
BRAND_DIR = Path(__file__).resolve().parents[2] / "ui" / "logos"

#: The two enrichment tables whose cells land in a selection row. Named here
#: because the revision reads one aggregate from each — see
#: :func:`_store_identity` for why a row count and a newest ``updated_at`` are
#: the cheapest complete signal these stores offer.
FUNDAMENTALS_TABLE = "fundamentals_snapshots"
FACTS_TABLE = "screen_facts"


def _encoded(document: Mapping[str, Any]) -> bytes:
    """The exact bytes a document ships as — the same encoder the response uses.

    ``separators`` matches Starlette's ``JSONResponse``: a byte count measured
    with a different encoder is a byte count that can be wrong by 10%.
    """

    return json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _etag(*, revision: str, request: SelectionRequest) -> str:
    """A quoted validator over ``(data revision, canonical request)``.

    The revision is hashed **full width**. ``app.screen.selection.selection_etag``
    folds its own revision through ``sha1(...)[:8]``, which is a 32-bit target:
    whoever can influence the inputs (a store row, a store's stamp) can mine two
    of them onto one tag. Sixty-four hex characters cannot be mined, and the tag
    is a per-request header, not a per-row cell, so the length costs nothing.

    The ``W/`` prefix is added by :func:`select`: the body carries ``as_of`` (the
    read moment), so this is a weak validator by definition — same answer, not
    necessarily the same bytes.
    """

    digest = hashlib.sha256(revision.encode("utf-8")).hexdigest()
    return f'"{ETAG_NAMESPACE}-{digest}-{request_digest(request)}"'


def _answer_digest(document: Mapping[str, Any]) -> str:
    """The answer, minus the read clock, as one sha256.

    Only ``fill_wide`` still uses it: those cells are live page fetches that no
    store revision can name, so that one mode keeps the old shape (build, digest
    the answer, compare afterwards). Every other selection is judged from
    :func:`_data_revision` *before* it builds — which is the whole point of that
    function, and why this one is no longer on the ``304`` path.

    ``as_of`` is the only field that is not data: it dates the read, so two calls
    over one store state differ there and nowhere else.
    """

    payload: dict[str, Any] = {
        key: value for key, value in document.items() if key != "as_of"
    }
    payload["rows"] = [
        {key: value for key, value in row.items() if key != "as_of"}
        for row in document.get("rows") or []
    ]
    return hashlib.sha256(_encoded(payload)).hexdigest()


def _unknown_revision(what: str) -> str:
    """A revision that matches nothing: an input we cannot describe is a miss.

    A *shared* placeholder (``"?"``) would be F3 again — two different store
    states under one token, and a ``304`` for a body nobody read. A unique token
    costs exactly one ``200`` and cannot certify anything.
    """

    return f"unreadable:{what}:{time.monotonic_ns()}"


def _depth_identity() -> str:
    """The depth store's **file version**, or why there is not one.

    ``path``, ``mtime_ns`` and ``size`` — never the collector's ``as_of``. The
    store is rewritten atomically every cycle, so a new cycle moves ``mtime_ns``
    even when the stamp it carries does not move, and a same-size rewrite is
    invisible to a revision built from ``size`` alone. Reading the parsed
    snapshot here instead would cost an 11.7 MB parse on the revalidation path
    this function exists to keep cheap (``load_depth`` caches it, but a cold
    process would pay it once for a ``304`` that needs no rows at all); hashing
    the file to be thorough costs 1.7 ms to read plus 6.4 ms to digest, against
    2.5 µs for the ``stat`` — and buys nothing the writer's atomic rename does
    not already guarantee.
    """

    try:
        path = resolve_store_path()
    except Exception as exc:  # noqa: BLE001 - an unreadable configuration is a miss
        logger.warning("selection revision: the depth store path is unusable: %s", exc)
        return _unknown_revision("depth")
    if path is None:
        return "off"
    try:
        stat = Path(path).stat()
    except OSError:
        # No collector on this box is the normal case, not a fault.
        return "absent"
    except (ValueError, RuntimeError):  # pragma: no cover - exotic path
        return _unknown_revision("depth")
    return f"{path}|{stat.st_mtime_ns}|{stat.st_size}"


def _brand_identity() -> str:
    """The vendored-asset directory's version: which marks exist, not their bytes.

    A row carries ``/ui/logos/AAPL.png``, so *adding* an asset changes the answer
    and *rewriting* one does not. A directory's ``mtime_ns`` moves on exactly
    the first of those.
    """

    try:
        stat = BRAND_DIR.stat()
    except OSError:
        return "absent"
    except (ValueError, RuntimeError):  # pragma: no cover - exotic path
        return _unknown_revision("brand")
    return f"{BRAND_DIR}|{stat.st_mtime_ns}|{stat.st_size}"


async def _store_identity(store: Any, table: str) -> str:
    """``<rows>:<newest updated_at>`` for one enrichment table, or why not.

    A selection's rows carry cells from two stores, and neither publishes a
    revision counter — so the revision is read from the table the join reads:
    ``COUNT(*)`` catches an insert or a purge, ``MAX(updated_at)`` catches a
    rewrite (both ``upsert`` implementations stamp ``updated_at`` on every
    write, and both ``purge_expired`` delete). Measured on the service database
    (3,902 and 3,831 rows): the two aggregates cost **6.3 ms and 3.3 ms**;
    materialising ``rows()`` to hash them costs **228 ms and 74 ms** — 302 ms,
    twice the whole 150 ms budget, to describe data the aggregate describes.

    Three answers that are **not** a revision, in order of how much they cost:

    * ``none`` — no store was wired, so it contributes nothing (and the body
      says so with nulls); a stable token, because the absence is stable.
    * ``absent`` — no such table, same argument.
    * a unique ``unreadable:`` token — the store exists but cannot describe
      itself (no ``engine``: a test double, or a future non-SQL store). Every
      such request pays a ``200``; none of them can be a stale ``304``.

    ``table`` is interpolated rather than bound, because SQLite does not bind
    identifiers; the caller passes one of the two module constants above.
    """

    if store is None:
        return "none"
    try:
        engine = getattr(store, "engine", None)
    except Exception as exc:  # noqa: BLE001 - a broken store is a miss, never a 500
        logger.warning("selection revision: %s exposes no usable engine: %s", table, exc)
        return _unknown_revision(table)
    if engine is None:
        return _unknown_revision(f"{table}-no-engine")
    assert table in {FUNDAMENTALS_TABLE, FACTS_TABLE}, f"unexpected table {table!r}"
    try:
        from sqlalchemy import text

        async with engine.begin() as connection:
            result = await connection.execute(
                text(f"SELECT COUNT(*) AS n, MAX(updated_at) AS m FROM {table}")
            )
            row = result.mappings().first() or {}
    except Exception as exc:  # noqa: BLE001 - see the three answers above
        if "no such table" in str(exc).lower():
            logger.debug("selection revision: %s has no table (%s)", table, exc)
            return "absent"
        logger.warning("selection revision: %s cannot be read: %s", table, exc)
        return _unknown_revision(table)
    return f"{int(row.get('n') or 0)}:{row.get('m') or '-'}"


def _data_revision(
    *,
    snapshot_id: str | None,
    snapshot_as_of: str | None,
    depth: str,
    scan: int | str,
    fundamentals: str,
    facts: str,
    brand: str,
    answer: str | None = None,
) -> str:
    """Every input that can change the body, in one auditable line.

    * ``v2`` — **a deliberate bump of the revision, not of the tag format.**
      The tag is still ``select-<64 hex sha256>-<32 hex request>`` and still
      served weak (``W/``); a client that stores and replays it needs no change.
      What moved is the *composition*: the answer digest left, five input
      identities arrived. Every warm client's tag therefore changes exactly
      once, on its next request, which is a ``200`` — the safe direction. The
      old string could not be kept because it contained the answer, and the
      answer is the one thing this route may no longer build before deciding.
    * ``snapshot=`` — the stored snapshot a ticker list was resolved in: its id
      and its own ``as_of``. A criteria query has none (it scans live), hence
      ``-``. The snapshot's *rows* are not read: symbols mode resolves tickers
      against the live scan, and the snapshot only names the universe and is
      echoed, so its identity is its stamp.
    * ``depth=`` — the depth store's file identity (:func:`_depth_identity`),
      which is strictly stronger than the cycle stamp it replaces: a version
      implies a stamp, a stamp does not imply a version.
    * ``scan=`` — the **generation** of the scan page this answer is built from
      (:meth:`ScanCache.page` hands it out with the page). A generation names one
      *page*, not one row set: a re-scan gets a new number even when it returns
      the same rows, because telling those two apart means encoding and hashing
      11.4 MB of parsed page — measured at 193–200 ms per scan, the same order as
      the ``build_universe`` this route stopped paying. The direction of the
      error is the safe one: the cost of a false move is a body the client
      already had, never a stale ``304``.
    * ``fund=`` / ``facts=`` — the two joins (:func:`_store_identity`).
    * ``logo=`` — the vendored brand set (:func:`_brand_identity`).
    * ``answer=`` — present **only for ``fill_wide``**, whose cells are live
      page fetches that no store revision can name. That mode keeps the old
      behaviour exactly: build, digest the answer, compare afterwards.

    The canonical request is hashed beside this string by :func:`_etag`, so two
    questions that happen to answer the same rows still get different entries.
    """

    line = (
        "v2"
        f";snapshot={snapshot_id or '-'}@{snapshot_as_of or '-'}"
        f";depth={depth}"
        f";scan={scan}"
        f";fund={fundamentals}"
        f";facts={facts}"
        f";logo={brand}"
    )
    if answer is not None:
        line += f";answer={answer}"
    return line


def _budget_report(
    *, limit: int, document: dict[str, Any], dropped: Sequence[str], rows_dropped: int
) -> dict[str, Any]:
    """The ``budget`` object: what the panel cost and what it went without."""

    report: dict[str, Any] = {
        "limit_bytes": limit,
        "bytes": 0,
        "truncated": bool(dropped) or rows_dropped > 0,
        "dropped_fields": list(dropped),
        "rows_dropped": rows_dropped,
    }
    # ``bytes`` counts itself, so settle the number in a couple of passes: its
    # own width changes the length it is describing.
    for _ in range(4):
        document["budget"] = report
        size = len(_encoded(document))
        if report["bytes"] == size:
            break
        report["bytes"] = size
    document["budget"] = report
    return report


def _fit_panel(
    document: dict[str, Any], *, protect: Sequence[str] = ()
) -> dict[str, Any]:
    """Fit the answer inside :data:`PANEL_BUDGET_BYTES`, and say what was cut.

    Three levers, in the order they cost the caller least:

    1. the **implicit brand mark** goes first (~640 B of base64 per row, 64 KB
       per 100 rows), unless the caller named ``logo`` in ``columns`` — a
       projection that asked for it gets it, budget or not;
    2. then **rows from the tail**. Never the first row: a truncated panel has
       to be a prefix of the answer it truncated, or a client cannot tell which
       page it is holding;
    3. only if a *single* row cannot fit, **cells from the tail of each row**,
       never ``symbol`` and never a column the caller named.

    The report is the point of the whole exercise. ``view=all`` at 100 rows was
    273,923 B and the response said nothing about it; now ``budget.truncated``,
    ``budget.rows_dropped`` and ``budget.dropped_fields`` do.
    """

    limit = PANEL_BUDGET_BYTES
    rows = [dict(row) for row in document.get("rows") or []]
    dropped: list[str] = []

    def build(
        rows_now: Sequence[dict[str, Any]],
        *,
        rows_dropped: int = 0,
        extra: Sequence[str] = (),
    ) -> tuple[dict[str, Any], int]:
        candidate = {**document, "rows": list(rows_now)}
        if rows_dropped:
            candidate["returned"] = len(rows_now)
        report = _budget_report(
            limit=limit,
            document=candidate,
            # One entry per field, in the order it went: `dropped` is what the
            # budget took for every row, `extra` the cells the last-resort cut
            # removed from the surviving ones.
            dropped=list(dict.fromkeys([*dropped, *extra])),
            rows_dropped=rows_dropped,
        )
        return candidate, int(report["bytes"])

    candidate, size = build(rows)
    if size <= limit:
        return candidate

    if "logo" not in protect and any("logo" in row for row in rows):
        rows = [{key: value for key, value in row.items() if key != "logo"} for row in rows]
        dropped.append("logo")
        candidate, size = build(rows)
        if size <= limit:
            return candidate

    # Rows, from the tail. Estimate first, then walk down until it fits — the
    # estimate is one serialisation instead of a hundred.
    overhead = len(_encoded({**document, "rows": [], "returned": 0}))
    per_row = max(1, (size - overhead) // max(1, len(rows)))
    keep = max(1, min(len(rows), (limit - overhead) // per_row))
    keep = min(keep, len(rows))
    candidate, size = build(rows[:keep], rows_dropped=len(rows) - keep)
    while keep > 1 and size > limit:
        keep -= 1
        candidate, size = build(rows[:keep], rows_dropped=len(rows) - keep)
    if size <= limit or not rows:
        return candidate

    # One row still does not fit: cut cells from its tail, keeping the identity
    # the panel is sorted by and every column the caller asked for by name.
    protected = {"symbol", *protect}
    names = [name for name in rows[0] if name not in protected]
    cut: list[str] = []
    while names and size > limit:
        cut.append(names.pop())
        trimmed = [{key: row[key] for key in row if key not in cut} for row in rows[:keep]]
        candidate, size = build(trimmed, rows_dropped=len(rows) - keep, extra=cut)
    return candidate


def _integral(value: Any, *, where: str, code: str) -> Any:
    """A JSON float that spells a whole number is that integer (F11).

    ``json.loads("4.0")`` is a ``float``, and JSON Schema's ``integer`` is "a
    number with no fractional part", so the published contract always accepted
    ``4.0``; the validator refusing it was the defect. ``1e9`` is the same thing
    at a size the panel clamps to 100. A *fraction* of a row is refused with the
    sentence that names it, because there is no page of 4.5 rows.
    """

    if isinstance(value, bool) or not isinstance(value, float):
        return value
    if not math.isfinite(value) or not value.is_integer():
        raise ApiError(
            status_code=422,
            error=code,
            message=(
                f"{where} must be a whole number, got {value!r}: a JSON float that "
                "names an integer is accepted (`4.0` is 4) and clamped to the "
                "panel; a fraction of a row is refused"
            ),
        )
    return int(value)


def _integral_knobs(block: Mapping[str, Any], *, prefix: str) -> dict[str, Any]:
    patched = dict(block)
    for name in ("limit", "offset"):
        if name in patched:
            patched[name] = _integral(
                patched[name], where=f"'{prefix}{name}'", code=f"invalid_{name}"
            )
    return patched


def _integral_numbers(payload: Any) -> Any:
    """Normalise the two integer knobs before the validator sees them (F11)."""

    if not isinstance(payload, Mapping):
        return payload
    patched: dict[str, Any] = dict(payload)
    block = patched.get("criteria")
    if isinstance(block, Mapping):
        patched["criteria"] = _integral_knobs(block, prefix="criteria.")
    return _integral_knobs(patched, prefix="")


# ---------------------------------------------------------------------------
# One scan, many panels — the scan cache
# ---------------------------------------------------------------------------

#: How long one scan may answer more than one request, in seconds.
#:
#: A number chosen against a measurement, not a feeling. A live parity scan of
#: the US universe is **2.0 s** on this box (5,715 rows; 2,051 ms for the first
#: cold panel, of which ~1.6 s is the provider), and a view switch is one click:
#: without a cache every tab paid that again to show the same universe with
#: different columns, so a switch was a 2 s empty panel. Five seconds is ~2.5
#: scans' worth, which covers a burst of switches and a double-click, and it
#: bounds the damage: a cached page is at most 5 s old — 1/60 of the 300 s the
#: browse snapshot already lives for (:data:`app.screen.snapshot.SNAPSHOT_TTL_S`).
#: Longer is not free: at 60 s every "Check again" inside the window would
#: revalidate against a page that cannot have changed, and `as_of` would be a
#: minute ahead of the rows it dates.
SCAN_CACHE_TTL_S = 5.0


def _scan_key(
    *,
    region: str,
    asset_class: str,
    source: str,
    columns: Sequence[str] | None,
    limit: int,
    min_market_cap: Any,
    subtype: str | None,
    offset: int = 0,
) -> tuple[str, ...]:
    """The identity of one scan: the universe it addresses, then the page it asks for.

    ``(region, asset_class, source)`` is the universe — the first two are what
    :func:`app.screen.universe.build_universe` receives, the third is the feed a
    ``symbols`` selection names — and the rest is the page shape. Both halves are
    in the key because both change the answer: ``parse_scan`` reads ``d``
    **positionally** through the requested column list, so a page fetched for one
    column list would put a number under another column's name; a different cap
    floor, subtype or offset is a different question to the provider. A key that
    named only the universe would serve one panel's cells to another.
    """

    if min_market_cap is DEFAULT_FLOOR:
        floor = "default"
    elif min_market_cap is None:
        floor = "-"
    else:
        floor = f"{float(min_market_cap):.10g}"
    return (
        f"region={region}",
        f"asset_class={asset_class}",
        f"source={source}",
        f"columns={'-' if columns is None else ','.join(columns)}",
        f"limit={int(limit)}",
        f"min_market_cap={floor}",
        f"subtype={subtype or '-'}",
        f"offset={int(offset)}",
    )


class ScanCache:
    """A short-TTL, single-flight cache in front of :func:`fetch_scan`.

    Three promises, in the order they matter to the panel:

    1. **never older than the TTL.** An entry answers only while
       ``clock() - stored_at < ttl_s``. At ``ttl_s == 0`` nothing is ever served
       (the single-flight below still applies), which is how a caller that must
       see every scan — a test pinning what the validator covers — asks for it.
       There is no stale-while-revalidate and no stale-on-error: an expired entry
       is not served even once, because "the cache outlived its TTL by one
       request" is the bug this class exists to avoid.
    2. **one scan per burst.** Concurrent misses on one key share one in-flight
       task, so ten panels that arrive before the scan returns cost one scan, not
       ten. Each waiter is shielded from the others' cancellation, and the task
       is owned by the cache rather than by whichever request happened to be
       first.
    3. **a failure is never an answer.** Nothing is stored unless the fetch
       returned. A scanner error reaches every waiter as that error and the next
       request tries again — a cached page must not become an outage that reads
       as a fresh 200.

    The TTL is read from :data:`SCAN_CACHE_TTL_S` at construction, so a test can
    pin it by patching the constant **before** the app's first scan.
    """

    def __init__(
        self,
        *,
        ttl_s: float = SCAN_CACHE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_s < 0:
            raise ValueError(f"ttl_s must be zero or positive, got {ttl_s!r}")
        self._ttl = float(ttl_s)
        self._clock = clock
        self._entries: dict[tuple[str, ...], tuple[float, ScanPage]] = {}
        self._inflight: dict[tuple[str, ...], asyncio.Task[tuple[ScanPage, int]]] = {}
        #: Every stored page gets the next number, so a generation names exactly
        #: one page for the life of the process. The tag carries it; see
        #: :meth:`page` and :func:`_data_revision`.
        self._generation = 0
        self._stamps: dict[tuple[str, ...], int] = {}
        # Guards the dictionaries only; a scan is never awaited under it, so
        # a slow provider cannot serialise unrelated keys.
        self._lock = asyncio.Lock()
        self._counts = {"scan": 0, "hit": 0, "shared": 0}

    # -- reading -----------------------------------------------------------

    def stats(self) -> dict[str, int]:
        """Scans actually run, plus how the other requests were answered."""

        return dict(self._counts)

    def _fresh(self, key: tuple[str, ...]) -> ScanPage | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, page = entry
        if self._clock() - stored_at >= self._ttl:
            return None
        return page

    def stamp(self, key: tuple[str, ...]) -> int | None:
        """The generation of the page this key would answer *from*, or ``None``.

        Read-only by contract — no scan, no counter, no entry touched — because
        it runs on the revalidation path, whose whole point is that it does no
        work. ``None`` means "a request for this key has to scan", which is
        exactly when a ``304`` cannot be decided from the inputs alone: after the
        TTL, whether the answer still holds is a question only the provider can
        answer, so the route must ask it and pay the ``200``.
        """

        if self._fresh(key) is None:
            return None
        return self._stamps.get(key)

    async def page(
        self, key: tuple[str, ...], fetch: Callable[[], Any]
    ) -> tuple[ScanPage, str, int]:
        """The page for ``key``, how this request got it, and its generation.

        The second element is ``"hit"`` (a cached page), ``"miss"`` (this request
        ran the scan) or ``"shared"`` (it waited on a scan another request had
        already started). It is the ``X-Scan-Cache`` response header, so the
        behaviour is observable without a counter inside the process.

        The third is the **generation of the page returned**, handed out with it
        rather than looked up afterwards: a tag built from a lookup could name a
        page another request stored in the meantime, and a validator that names
        a page it did not use is a ``304`` for rows the client never saw.
        """

        page = self._fresh(key)
        if page is not None:
            self._counts["hit"] += 1
            return page, "hit", self._stamps[key]

        async with self._lock:
            page = self._fresh(key)
            if page is not None:
                self._counts["hit"] += 1
                return page, "hit", self._stamps[key]
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.ensure_future(self._scan(key, fetch))
                self._inflight[key] = task
                # Without this, a task nobody has finished awaiting when it fails
                # logs "exception was never retrieved" at collection time.
                task.add_done_callback(_quiet)
                role = "miss"
            else:
                self._counts["shared"] += 1
                role = "shared"

        # Shield: one waiter's cancellation must not cancel the scan the others
        # are waiting on. A failure propagates to every waiter and is stored
        # nowhere.
        page, generation = await asyncio.shield(task)
        return page, role, generation

    async def _scan(
        self, key: tuple[str, ...], fetch: Callable[[], Any]
    ) -> tuple[ScanPage, int]:
        self._counts["scan"] += 1
        try:
            page = await fetch()
            self._prune()
            # The age is measured from the moment the answer arrived, not from
            # the moment it was asked for: a 12.5 s scan must not spend its own
            # duration of the window.
            self._entries[key] = (self._clock(), page)
            # After the fetch, never before: a scan that raises must not move the
            # number another caller's validator is built on.
            self._generation += 1
            self._stamps[key] = self._generation
            return page, self._generation
        finally:
            self._inflight.pop(key, None)

    def _prune(self) -> None:
        """Drop expired entries on write; the key space is small but unbounded."""

        now = self._clock()
        for key in [k for k, (at, _) in self._entries.items() if now - at >= self._ttl]:
            self._entries.pop(key, None)
            self._stamps.pop(key, None)


def _quiet(task: asyncio.Task[Any]) -> None:
    """Mark a done task's exception as retrieved, so an abandoned scan is silent."""

    if not task.cancelled():
        task.exception()


def _scan_cache(request: Request) -> ScanCache:
    """The app's scan cache, created on first use.

    Per app, not per process: a test builds a fresh app per case and gets a fresh
    cache, and two deployments in one process cannot answer each other. The
    getattr/setattr pair has no ``await`` between them, so on one event loop it
    is atomic — two concurrent first requests cannot create two caches.
    """

    cache = getattr(request.app.state, "scan_cache", None)
    if not isinstance(cache, ScanCache):
        cache = ScanCache(ttl_s=SCAN_CACHE_TTL_S)
        request.app.state.scan_cache = cache
    return cache


@dataclass(frozen=True, slots=True)
class _ScanPlan:
    """What one selection asks the provider for, and the key that names it.

    Both halves are computed before the scan runs, so the cache can be *asked*
    whether it holds a fresh page (:meth:`ScanCache.stamp`) without running one.
    """

    key: tuple[str, ...]
    columns: Sequence[str] | None
    min_market_cap: Any
    subtype: str | None


def _scan_plan(
    *, selection: SelectionRequest, region: str, family: str, source: str
) -> _ScanPlan:
    """The one scan a selection needs, described before it is run.

    A criteria query filters on metrics the light row never carried (P/E, RSI,
    beta, the SMAs...), so it asks for the parity columns and drops both provider
    filters that would hide rows: no 100M floor and no ``subtype=common`` gate,
    because a selection answers over the stock market, not over the display's
    subset. ``type=stock`` (or ``type=fund``/``subtype=etf`` for the ETF
    universe) and the OTC exclusion stay on.

    A ticker lookup needs no metric unless the caller asked for a view or a
    column: then the cells must come from the parity scan, because the light row
    carries none of them.
    """

    if selection.is_criteria:
        columns: Sequence[str] | None = PARITY_COLUMNS
        floor: Any = None
        subtype: str | None = None
    else:
        columns = PARITY_COLUMNS if (selection.view or selection.columns) else None
        floor = DEFAULT_FLOOR
        subtype = "common"
    return _ScanPlan(
        key=_scan_key(
            region=region,
            asset_class=family,
            source=source,
            columns=columns,
            limit=FULL_SCAN_ROWS,
            min_market_cap=floor,
            subtype=subtype,
        ),
        columns=columns,
        min_market_cap=floor,
        subtype=subtype,
    )


async def _selection_scan(
    request: Request,
    plan: _ScanPlan,
    *,
    region: str,
    asset_class: str,
    source: str,
) -> tuple[ScanPage, str, int]:
    """One selection's universe, through the cache, and how it was answered."""

    cache = _scan_cache(request)
    return await cache.page(
        plan.key,
        lambda: fetch_scan(
            region,
            limit=FULL_SCAN_ROWS,
            columns=plan.columns,
            min_market_cap=plan.min_market_cap,
            subtype=plan.subtype,
            asset_class=asset_class,
        ),
    )


def _revalidated(etag: str, *, scan: str) -> Response:
    """The ``304``: the same headers a ``200`` carries, and no body at all."""

    headers = cache_headers(etag)
    # Weak on purpose: `as_of` is the read moment, the one field the revision
    # leaves out, so "same answer" is the promise, not "same bytes". A client
    # that sends either form revalidates — `cache.matches` strips the prefix.
    headers["ETag"] = f"W/{etag}"
    headers["X-Scan-Cache"] = scan
    return Response(status_code=304, headers=headers)


def _browse_document(document: dict[str, Any]) -> dict[str, Any]:
    """Rename the browse count to ``matched`` and name the universe beside it.

    ``total`` on this route and ``total`` on ``/screens/select`` were different
    numbers — 3,896 (with the display's 100M floor and ``subtype=common`` gate)
    against 5,600 (the whole stock market) — and the shipped client printed both
    under one label, "Universe". The browse number is the count of rows that
    matched *this route's* question, so it is called ``matched``; ``universe_total``
    is the universe the question ran over, which on this route is the same set
    because browse applies no filter. The two names now mean the same thing on
    both routes, and the client can label them correctly.
    """

    result = dict(document)
    # Tolerant on purpose: the document builder and this rename may land in
    # either order, and clobbering a `matched` that is already there with the
    # count of a missing `total` is how a 23-row screen reads as an empty market.
    matched = result.pop("total", result.get("matched", 0))
    matched = int(matched or 0)
    result["matched"] = matched
    result["universe_total"] = int(result.get("universe_total") or matched)
    return result


def _with_logos(document: dict[str, Any], *, columns: Sequence[str] = ()) -> dict[str, Any]:
    """Attach a guaranteed brand mark to every row without mutating a snapshot.

    ``logo`` belongs to the instrument identity, not to the source that answered.
    The resolver is filesystem-only, so this adds no provider call and no
    latency; a symbol without a vendored asset gets an offline monogram.

    A **projected** row is the exception: ``columns`` names the exact cells the
    caller wants, and a monogram is ~640 bytes of base64. 100 implicit copies
    would add 64 KB to a panel that asked for twelve numeric columns, so the mark
    is attached only when the projection asks for ``"logo"`` by name (or when
    there is no projection at all, where the light row has always carried it).

    A ``view`` asks for the mark implicitly (its column list contains ``logo``),
    so the panel budget — not this function — is what takes it away, and only
    when the finished body would exceed :data:`PANEL_BUDGET_BYTES`: dropping 64 KB
    of decoration is the cheapest way to keep all 100 rows.
    """

    if columns and "logo" not in columns:
        return document
    result = dict(document)
    result["rows"] = [
        {**dict(row), "logo": logo_for(row.get("symbol"), row.get("name"))}
        for row in document.get("rows", [])
    ]
    return result


class ExportPayload(BaseModel):
    """``POST /screens/{id}/export`` body; an unknown knob is a 422."""

    model_config = ConfigDict(extra="forbid")

    formats: list[str] = Field(default_factory=lambda: list(DATA_FORMATS))


@router.get("")
async def browse(
    request: Request,
    region: Region = "us",
    sort: Sort = "market_cap",
    source: ScreenSource = "multi",
    limit: int = DISPLAY_ROWS,
) -> dict[str, Any]:
    """The top 20 of the ``(region, sort)`` snapshot, verified by default.

    ``source=multi`` is the product default (P10): one row per stock, the median
    of the selection snapshot and the live verifiers. ``source=tradingview`` is
    the raw snapshot the consensus was built from — the audit view, and the
    escape hatch if a deployment turns verification off.
    """

    # ``limit`` is accepted for compatibility and deliberately ignored: the
    # display is the top 20 of a snapshot, and a knob that could widen it to 23
    # would turn a display into an unpaginated dump. Requesting limit=100 is not
    # an error — it just cannot buy more rows than the contract sells.
    del limit

    runtime = runtime_of(request)
    try:
        if source == "multi":
            return _browse_document(
                _with_logos(await runtime.multi_source_screen.load(region, sort))
            )
        return _browse_document(
            _with_logos(
                await load_screen(region, sort, repository=runtime.screen_repository)
            )
        )
    except ScanError as exc:
        raise ApiError(
            status_code=503,
            error="scanner_unavailable",
            message=f"the scanner could not answer and no snapshot exists yet: {exc}",
        ) from exc


@router.get("/schema")
async def schema() -> dict[str, object]:
    """The JSON Schema for a browse response, versioned with the route."""

    return SCREEN_RESPONSE_SCHEMA


@router.get("/select/schema")
async def selection_schema() -> dict[str, object]:
    """The JSON Schema for a ``POST /screens/select`` body, plus the view catalogue.

    Served from the module that validates with it, so the published contract and
    the validator cannot drift: this exact document is written to
    ``schemas/stock_selection_request_v1.json`` and compared to it, byte for
    byte, by the schema tests.

    ``views`` is a top-level annotation on that document, beside
    ``x-filter-keys``: the canonical view catalogue (name, label, job and ordered
    column keys) from :mod:`app.screen.views`, so a client builds its tab strip
    and its column lists from this answer instead of spelling them itself. That
    is the one thing that cannot drift — Phase 0's F1 was a client hardcoding a
    column set the server had already renamed. It is not a request property: the
    document's ``properties`` does not name it, so a body that carries it is
    still refused. (F3 was this key living only in the route's return value: the
    served document and the artifact then differed by one key, and "the artifact"
    and "the answer" were two documents with one name.)
    """

    return SELECTION_REQUEST_SCHEMA


@router.post("/select")
async def select(request: Request) -> Any:
    """Answer a two-mode selection: a criteria query or an explicit ticker list.

    The body is validated by :func:`app.screen.selection.parse_selection`, not by
    a pydantic model, because the failure messages are part of the product here:
    an unknown operator or an unavailable Finviz key has to name itself and say
    *why* it cannot run, and FastAPI's ``detail`` list cannot carry that sentence.
    Every rejection therefore lands in the same ``{error, errorMessage}``
    envelope as the rest of the API.

    One bulk scan per call (never the 20 stored display rows) is what makes
    ``total`` a post-filter number over the whole universe. A ticker list is
    resolved against the snapshot named by ``snapshot_id``: unknown snapshot is a
    404 before any scan is spent, and a snapshot built from another source is a
    422 instead of being relabelled with the requested one. The snapshot names
    *which* universe the tickers are looked up in; the rows are read now and the
    response dates them with its own ``as_of``, never with the snapshot's.

    The answer carries a **weak** ``ETag`` computed from the answer's **inputs**,
    before the answer exists: the depth store's file version, the snapshot's id
    and stamp, the scan page's generation, the two enrichment stores' revisions,
    the brand-asset directory and the canonical request (see
    :func:`_data_revision`). A repeat POST that presents it gets ``304`` with no
    body and **no build** — the comparison happens first, so what a revalidation
    saves is the 235–318 ms universe, the 295–315 ms fundamentals join, the
    104–110 ms facts join, the 93 ms selection and the body. It is weak because
    the body carries ``as_of``, the read moment, which the revision leaves out; a
    strong tag would have to move with the clock and would never match.

    **One scan serves a burst.** The universe comes through :class:`ScanCache`,
    keyed by what the provider was asked (see :func:`_scan_key`) and good for
    :data:`SCAN_CACHE_TTL_S` seconds, so five view switches inside the window pay
    one scan instead of five. The tag stays honest because the cache holds the
    *scan* and not the answer: the depth store, the fundamentals join and the
    facts join are read per request, so a store that changes still moves the tag
    and a replay of the old tag is still a ``200`` with the new cell. Outside the
    window — and after any failed scan — a revalidation pays one scan, because
    the scan is an input.

    ``fill_wide`` is the one exception, and it keeps the old shape: its cells are
    live page fetches that no store revision can name, so that mode builds first
    and folds the answer's digest into the revision afterwards.
    """

    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ApiError(
            status_code=422,
            error="invalid_json",
            message=f"the selection body is not valid JSON: {exc}",
        ) from exc

    # F11: `4.0` is 4 and `1e9` is clamped, exactly as the schema has always
    # said; only a fraction of a row is refused, with the sentence that names it.
    payload = _integral_numbers(payload)

    try:
        selection = parse_selection(payload)
    except SelectionError as exc:
        raise ApiError(
            status_code=exc.status_code, error=exc.code, message=str(exc)
        ) from exc

    runtime = runtime_of(request)
    snapshot_id: str | None = None
    snapshot_as_of: str | None = None
    if selection.is_criteria:
        assert selection.region is not None  # parse_selection requires it
        region = selection.region
        source = SOURCE
        family = selection.asset_class or asset_class_for(region)
    else:
        snapshot = await runtime.screen_repository.get(selection.snapshot_id or "")
        if snapshot is None:
            raise ApiError(
                status_code=404,
                error="snapshot_not_found",
                message=f"snapshot {selection.snapshot_id} does not exist",
            )
        if snapshot.get("source") != selection.source:
            raise ApiError(
                status_code=422,
                error="source_mismatch",
                message=(
                    f"snapshot {selection.snapshot_id} was built from source "
                    f"{snapshot.get('source')!r}, not {selection.source!r}"
                ),
            )
        region = str(snapshot["region"])
        source = str(selection.source)
        snapshot_id = selection.snapshot_id
        snapshot_as_of = str(snapshot.get("as_of") or "") or None
        try:
            family = asset_class_for(region, selection.asset_class)
        except ValueError as exc:
            # A symbols selection may not relabel the snapshot's own tab: an
            # ETF snapshot is the ETF universe, and a caller asking for stocks
            # against it is contradicted by the snapshot it named.
            raise ApiError(
                status_code=422, error="asset_class_mismatch", message=str(exc)
            ) from exc

    # ---- the revision, before any of the work it describes ----------------
    #
    # Everything below is a stat or one indexed aggregate: ~13 ms in total,
    # against the 744 ms the build used to cost before the tag could be
    # compared. `depth` is read below, after the fast path has had its chance,
    # so a `304` never parses the 11.7 MB store.
    cache = _scan_cache(request)
    plan = _scan_plan(selection=selection, region=region, family=family, source=source)
    depth_revision = _depth_identity()
    brand_revision = _brand_identity()
    fundamentals_revision = await _store_identity(
        getattr(runtime, "fundamentals_store", None), FUNDAMENTALS_TABLE
    )
    facts_revision = await _store_identity(
        getattr(runtime, "facts_store", None), FACTS_TABLE
    )

    def revision(scan: int | str, answer: str | None = None) -> str:
        return _data_revision(
            snapshot_id=snapshot_id,
            snapshot_as_of=snapshot_as_of,
            depth=depth_revision,
            scan=scan,
            fundamentals=fundamentals_revision,
            facts=facts_revision,
            brand=brand_revision,
            answer=answer,
        )

    # A revalidation of an answer whose scan is still warm is decided here, and
    # only here: no scan, no universe, no joins, no body.
    if not selection.fill_wide:
        stamp = cache.stamp(plan.key)
        if stamp is not None:
            etag = _etag(revision=revision(stamp), request=selection)
            if not_modified(request, etag):
                return _revalidated(etag, scan="hit")

    # Depth is the broadest enrichment; it is read once per selection, before
    # the scan, and only by the requests that are going to build.
    depth = current_depth()

    try:
        page, scan, generation = await _selection_scan(
            request, plan, region=region, asset_class=family, source=source
        )
    except ScanError as exc:
        raise ApiError(
            status_code=503,
            error="scanner_unavailable",
            message=f"the scanner could not answer the selection: {exc}",
        ) from exc

    # The universe, not the display table: the total must count every match.
    if selection.is_criteria:
        universe = build_universe(
            page.rows,
            region=region,
            min_market_cap=None if family == "etf" else 0,
            columns=PARITY_COLUMNS,
            allow_any_subtype=not selection.common_only,
            asset_class=family,
        )
    else:
        universe = build_universe(
            page.rows,
            region=region,
            columns=PARITY_COLUMNS if (selection.view or selection.columns) else None,
            asset_class=family,
        )

    # P11G/P11I: the scan answers null for forward P/E, P/S and payout ratio;
    # those three cells, plus the ratios derived from the snapshot inputs, are
    # owned by the fundamentals snapshot store. The join reads that
    # table **once** for the whole universe (no per-symbol query), copies only
    # fresh rows, and never invents a value: a symbol the store does not hold
    # keeps ``None``, which matches no filter. A selection must not become a 500
    # because the optional table was never migrated, so the join degrades to "no
    # enrichment" and logs why; ``getattr`` is that same bargain for a runtime
    # built by an older composition root.
    # Depth is the broadest enrichment: it carries the columns no scan metric
    # exists for. It is merged first so that a store which *owns* a cell
    # (forward P/E, short float, insider own) still wins over it below.
    if depth.symbols:
        universe = overlay(universe, depth.symbols, default_ticker_key="symbol", aliases=SCREEN_ALIASES)

    universe = await enrich_rows(universe, getattr(runtime, "fundamentals_store", None))

    # P11J: the three short/ownership cells (Short Float, Insider Own, Inst Own)
    # have no scan metric at all; the facts store owns them. Same contract as
    # the fundamentals join — one batch read per selection, fresh rows only,
    # ``None`` where the store has no answer (unknown matches no filter), and a
    # missing table is no enrichment rather than a failed selection.
    universe = await enrich_facts(universe, getattr(runtime, "facts_store", None))

    moment = datetime.now(UTC).isoformat(timespec="microseconds")
    answer = select_rows(
        universe,
        selection,
        region=region,
        as_of=moment,
        source=source,
        snapshot_id=snapshot_id,
    )
    document = answer.as_document()
    if selection.fill_wide:
        # Display-only enrichment. It runs after the selection has been
        # answered, so a slow or blocked stock page can never change which
        # rows matched; cells it cannot read stay null.
        filler = getattr(request.app.state, "wide_filler", None)
        if filler is None:
            # Reuse the process's registered Finviz adapter so the optional
            # page fetches inherit the same egress ladder, cooldown and proxy
            # pool as the fundamentals path. A runtime without adapters (a
            # fake composition root) still gets a plain adapter.
            adapters = getattr(runtime, "adapters", None)
            adapter = None
            if callable(adapters):
                built: Any = adapters()
                if hasattr(built, "get"):
                    adapter = built.get("finviz")
            filler = WideFiller(adapter=adapter)
            request.app.state.wide_filler = filler
        document["rows"] = await filler.fill(document.get("rows", []))

    # The validator, from the same inputs the fast path used — plus the page
    # generation this request actually built from, which is what lets the next
    # caller's `cache.stamp` reproduce this exact tag.
    document = _fit_panel(
        _with_logos(document, columns=selection.columns), protect=selection.columns
    )
    etag = _etag(
        revision=revision(
            generation,
            # `fill_wide` cells are live page fetches: no store revision names
            # them, so that mode keeps the old answer-digest behaviour.
            _answer_digest(document) if selection.fill_wide else None,
        ),
        request=selection,
    )
    headers = cache_headers(etag)
    # Weak on purpose: `as_of` is the read moment, the one field the revision
    # leaves out, so "same answer" is the promise, not "same bytes". A client
    # that sends either form revalidates — `cache.matches` strips the prefix.
    headers["ETag"] = f"W/{etag}"
    # Whether this answer's universe came from a scan or from the scan cache
    # (five views inside one window = one "miss" and four "hits"). It is the only
    # place the caching is visible to a client, and it is how the panel can tell
    # "refreshed" from "replayed" without a second request.
    headers["X-Scan-Cache"] = scan
    if not_modified(request, etag):
        return _revalidated(etag, scan=scan)
    return Response(content=_encoded(document), media_type="application/json", headers=headers)


@router.post("/{screen_id}/export", status_code=202)
async def export(
    screen_id: str, payload: ExportPayload, request: Request
) -> Any:
    """Publish ``all`` + ``top20`` artifacts as a job; 202 + ``job_id``."""

    runtime = runtime_of(request)
    formats = tuple(payload.formats)
    unknown = [fmt for fmt in formats if fmt not in EXPORT_FORMATS]
    if not formats or unknown:
        raise ApiError(
            status_code=422,
            error="unknown_format",
            message=(
                f"unknown export format(s) {unknown or ['<none>']}; expected any of "
                f"{', '.join(EXPORT_FORMATS)}"
            ),
        )

    screen = await runtime.screen_repository.get(screen_id)
    if screen is None:
        raise ApiError(
            status_code=404,
            error="screen_not_found",
            message=f"screen {screen_id} does not exist",
        )

    job = await runtime.repository.create(
        params={
            "screen_id": screen_id,
            "region": screen["region"],
            "sort": screen["sort"],
            "formats": list(formats),
        },
        api_key=owner_fingerprint(request),
        service=SCREEN_EXPORT_SERVICE,
        rows_target=int(screen.get("total_count") or 0),
    )
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job["id"],
            "screen_id": screen_id,
            "status": "Pending",
            "formats": list(formats),
            "results_location": f"/requests/{job['id']}",
            "expires_at": job["expires_at"],
        },
        background=BackgroundTask(run_screen_export, runtime, job["id"], screen_id, formats),
    )


async def run_screen_export(
    runtime: Runtime, job_id: str, screen_id: str, formats: tuple[str, ...]
) -> None:
    """Write the bundle, register one artifact per scope+format, finish the job.

    The job record is the only thing a client polls, so every outcome has to
    land in it: a Success with eight artifacts, or a Failure with the sentence
    that explains why. Escaping as a 500 after the 202 was sent is not an
    outcome a client can act on.
    """

    jobs = runtime.repository
    try:
        await jobs.claim_job(job_id, worker_id="screen-export")
        bundle = await export_bundle(
            screen_id,
            repository=runtime.screen_repository,
            directory=runtime.export_dir / f"screen-{job_id}",
            formats=formats,
            deep_engine=runtime.screen_deep_engine,
            job_id=job_id,
        )
        for scope in bundle.scopes.values():
            for fmt, path in scope.files.items():
                await jobs.record_artifact(
                    job_id,
                    kind=f"{scope.scope}_{fmt}",
                    path=str(path),
                    sha256=file_sha256(path),
                    row_count=scope.row_count,
                    bytes_=Path(path).stat().st_size,
                )
        await jobs.complete(
            job_id,
            result_count=bundle.scopes["all"].row_count,
            result_mode="bundle",
            usage={
                "scopes": {
                    name: {
                        "row_count": scope.row_count,
                        "deep_rows": scope.deep_rows,
                        "manifest": scope.manifest_path.name,
                    }
                    for name, scope in bundle.scopes.items()
                },
                "as_of_achieved": bundle.as_of,
            },
        )
    except (ScreenNotFound, UnknownScreenFormat) as exc:
        # Raised before the plan is refused; a client error, not a retry.
        await jobs.fail(job_id, error=f"{type(exc).__name__}: {exc}", retryable=False)
    except Exception as exc:  # noqa: BLE001 - the job row is the error channel
        await jobs.fail(job_id, error=f"{type(exc).__name__}: {exc}", retryable=False)


__all__ = [
    "SCREEN_EXPORT_SERVICE",
    "browse",
    "export",
    "router",
    "run_screen_export",
    "select",
    "selection_schema",
]
