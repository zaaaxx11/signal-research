"""``GET /market/instruments`` — the manifest's instrument table, one row each.

The manifest (``coinid.json``, see :mod:`app.market.coinids`) answers "fetch
exactly these". This route answers the other half: **show exactly these**, with
every signal this deployment can honestly compute and a ``null`` (plus a
reason) for everything it cannot.

One scan, the selector's readers
--------------------------------
There is no second computation path here. The prices and metrics are read from
the same population ``POST /screens/select`` builds: one
:func:`app.screen.scanner.fetch_scan` page through the app's
:class:`~app.api.screens.ScanCache` (the same key the selector uses, so a panel
that opens both pays one provider scan), one
:func:`app.screen.universe.build_universe` with the selector's own arguments
(``min_market_cap=0`` for stocks, ``allow_any_subtype=True``,
``PARITY_COLUMNS``), and one :func:`app.screen.depth_overlay.overlay` with the
same aliases. The cells the scan answered are copied verbatim; nothing here
recomputes a price or a beta.

What the rows promise
---------------------
A manifest entry is a **promise to show** an instrument, not a promise to
invent it. Therefore:

* a stock row found in the scanned universe carries its price, change, volume,
  ``beta_1y``, market cap, dividend yield, recommendation, trailing and forward
  P/E, RSI(14), SMA 50/200 and the YTD/1-week/1-month returns;
* a symbol the scan does not carry is still a row, with every signal ``null``,
  ``priced: false`` and ``reason: "not in the scanned universe"``;
* a row the scan carries but without a last price is ``null`` with
  ``"the scan row carries no price"`` — the provider's silence is not a price;
* an option row is its **declared contract** (``contract.expiry`` /
  ``strike`` / ``right``, copied from the manifest) and nothing else: there is
  no option-chain source in this deployment, so its market cells are ``null``
  and the document names that reason once, in ``unavailable[]``.

``unavailable[]`` is block-level on purpose: greeks, option expiry and
days-to-maturity have *no source at all* (not a null for one symbol), and IBKR
is a broker capability this deployment has not configured. ``signals`` lists
the row cells that are real numbers when a row is priced, so a client can build
its columns from the document instead of hard-coding them. A signal the
provider did not send for one symbol is ``null`` as well, without an
``unavailable[]`` entry: that block names what no source can produce, not a
cell this row's answer left empty.

``GET /market/instruments/meta``
--------------------------------
The manifest's own status: where it lives, its version and stamp, how many
entries it holds, which **stock** entries the scanned master does not carry
(``not_in_master``) and which it carries without a price (``no_price``). Labels
are the entry's ``id`` when it has one, else its symbol. Option entries are
skipped in both lists by construction — an option is not a stock-master row;
whether the chain exists is the block-level reason, not a per-symbol finding.

A missing scanner does not hide the manifest: ``meta`` answers ``200`` with
``master.scan: "unavailable"`` and ``null`` findings, because the manifest's
state is still known. The table itself answers ``503``: it cannot price
anything without the scan, and a table of unexplained nulls is not an answer
this route is willing to give.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from fastapi import APIRouter, Request

from app.api.screens import _scan_cache, _scan_key
from app.auth import ApiError
from app.market.coinids import (
    ASSET_OPTION,
    Instrument,
    Manifest,
    ManifestError,
    load_manifest,
)
from app.screen.depth_overlay import SCREEN_ALIASES, _spellings, overlay
from app.screen.depth_source import current_depth
from app.screen.scanner import FULL_SCAN_ROWS, ScanError, fetch_scan
from app.screen.snapshot import SOURCE
from app.screen.universe import PARITY_COLUMNS, build_universe

router = APIRouter(prefix="/market", tags=["market"])

#: The manifest's stock entries are US stocks, so they are looked up in the
#: selector's US stock population — the one page that carries every signal this
#: route publishes (``beta_1_year`` and the P/E pair included).
REGION = "us"
FAMILY = "stock"

#: The row cells that are a real number when the row is priced. Spelled here in
#: the plan's order; the route copies each one from the universe row.
SIGNALS: tuple[str, ...] = (
    "price",
    "change_pct",
    "volume",
    "beta_1y",
    "market_cap_usd",
    "dividend_yield",
    "recommendation",
    "pe_ttm",
    "pe_forward",
    "rsi_14",
    "sma_50",
    "sma_200",
    "ret_ytd",
    "ret_1w",
    "ret_1m",
)

#: The option cells no source can fill yet. They are row keys (always ``null``)
#: and named once in ``unavailable[]``.
OPTION_SIGNALS: tuple[str, ...] = ("greeks", "expiry", "days_to_maturity")

REASON_NO_CHAIN = "no option-chain source yet"
REASON_NO_BROKER = "broker integration not configured"
REASON_NOT_IN_MASTER = "not in the scanned universe"
REASON_NO_PRICE = "the scan row carries no price"

#: The block-level statement of what this deployment cannot compute. One entry
#: per signal, with the reason — never a value invented to fill a cell.
UNAVAILABLE: tuple[dict[str, str], ...] = (
    {"signal": "greeks", "reason": REASON_NO_CHAIN},
    {"signal": "expiry", "reason": REASON_NO_CHAIN},
    {"signal": "days_to_maturity", "reason": REASON_NO_CHAIN},
    {"signal": "ibkr", "reason": REASON_NO_BROKER},
)


# ---------------------------------------------------------------------------
# The scan, through the selector's own cache and readers
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Master:
    """The scanned universe, or the reason it could not be read.

    ``scan`` is the same observable the selector emits: ``hit``/``miss`` from
    the scan cache, ``skipped`` when the manifest has no row to price, and
    ``unavailable`` when the provider failed (``reason`` then says why).
    """

    rows: list[dict[str, Any]]
    scan: str
    universe_total: int | None
    reason: str | None = None


def _scan_key_for() -> tuple[str, ...]:
    """The selector's scan key — the one ``/screens/select`` and signals use."""

    return _scan_key(
        region=REGION,
        asset_class=FAMILY,
        source=SOURCE,
        columns=PARITY_COLUMNS,
        limit=FULL_SCAN_ROWS,
        min_market_cap=None,
        subtype=None,
    )


def _universe_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The selector's population, built with the selector's arguments.

    ``min_market_cap=0`` admits every common listing with a reported cap,
    ``allow_any_subtype`` addresses the whole stock market rather than the
    browse display's common shares, and ``PARITY_COLUMNS`` is what gives the
    row ``beta_1y`` and the parity metrics. Copied from
    ``app.api.screens.select`` rather than approximated: a manifest priced
    against a different population is a price a screen cannot reproduce.
    """

    return build_universe(
        rows,
        region=REGION,
        min_market_cap=0,
        columns=PARITY_COLUMNS,
        allow_any_subtype=True,
        asset_class=FAMILY,
    )


def _with_depth(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``rows`` with the depth store's cells, exactly as a screen sees them.

    Same function, aliases and ticker normalisation the selector uses. An
    absent or empty store is not an error: every row keeps the cells it had.
    """

    snapshot = current_depth()
    symbols = getattr(snapshot, "symbols", None)
    if not symbols:
        return rows
    return overlay(rows, symbols, default_ticker_key="symbol", aliases=SCREEN_ALIASES)


async def _master(request: Request, *, needed: bool) -> _Master:
    """The scanned universe, or an unavailable marker. Never raises ``ScanError``."""

    if not needed:
        # Nothing to price: an empty (or all-option) manifest must not cost a
        # 5,000-row provider scan to answer an empty table.
        return _Master(rows=[], scan="skipped", universe_total=None)

    cache = _scan_cache(request)
    try:
        page, scan, _generation = await cache.page(
            _scan_key_for(),
            lambda: fetch_scan(
                REGION,
                limit=FULL_SCAN_ROWS,
                columns=PARITY_COLUMNS,
                min_market_cap=None,
                subtype=None,
                asset_class=FAMILY,
            ),
        )
    except ScanError as exc:
        return _Master(rows=[], scan="unavailable", universe_total=None, reason=str(exc))

    universe = _with_depth(_universe_rows(page.rows))
    return _Master(rows=universe, scan=scan, universe_total=len(universe))


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


def _index(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """The universe keyed by normalised symbol; the first row wins a tie."""

    index: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if symbol and symbol not in index:
            index[symbol] = row
    return index


def _lookup(index: Mapping[str, Mapping[str, Any]], symbol: str) -> Mapping[str, Any] | None:
    """One symbol's row, trying the provider's ``BRK.B``/``BRK-B`` spellings.

    The spelling rule is :func:`app.screen.depth_overlay._spellings`' own — the
    same one the depth overlay uses to join a ticker — so a manifest entry
    spelled the other way is not silently "missing".
    """

    for spelling in _spellings(symbol):
        row = index.get(spelling)
        if row is not None:
            return row
    return None


def _blank(instrument: Instrument, *, reason: str) -> dict[str, Any]:
    """A row that is present because it was asked for, and empty because it is unknown."""

    row: dict[str, Any] = {
        "id": instrument.id,
        "symbol": instrument.symbol,
        "asset": instrument.asset,
        "contract": instrument.contract,
        "priced": False,
        "reason": reason,
    }
    row.update({name: None for name in SIGNALS})
    row.update({name: None for name in OPTION_SIGNALS})
    return row


def _row(instrument: Instrument, master_row: Mapping[str, Any] | None) -> dict[str, Any]:
    """One manifest entry → one table row, from the scan row when it has one."""

    if instrument.asset == ASSET_OPTION:
        return _blank(instrument, reason=REASON_NO_CHAIN)
    if master_row is None:
        return _blank(instrument, reason=REASON_NOT_IN_MASTER)

    values = {name: master_row.get(name) for name in SIGNALS}
    if values["price"] is None:
        # The scan carried the symbol but not a last price. Every other cell is
        # readable, yet a row without a price is not priced — and saying so is
        # the whole contract of this route.
        return _blank(instrument, reason=REASON_NO_PRICE)

    row: dict[str, Any] = {
        "id": instrument.id,
        "symbol": instrument.symbol,
        "asset": instrument.asset,
        "contract": None,
        "priced": True,
        "reason": None,
    }
    row.update(values)
    row.update({name: None for name in OPTION_SIGNALS})
    return row


# ---------------------------------------------------------------------------
# The manifest block, the selection and the document
# ---------------------------------------------------------------------------


def _manifest_block(manifest: Manifest) -> dict[str, Any]:
    return {
        "path": str(manifest.path) if manifest.path is not None else None,
        "present": manifest.present,
        "version": manifest.version,
        "updated_at": manifest.updated_at,
        "count": len(manifest),
        "legacy": manifest.legacy,
    }


def _load() -> Manifest:
    """Load the manifest, mapping its one error to the house 422 envelope."""

    try:
        return load_manifest()
    except ManifestError as exc:
        raise ApiError(status_code=422, error=exc.code, message=str(exc)) from exc


def _selection(manifest: Manifest, raw: str | None) -> tuple[Instrument, ...]:
    """The rows a caller asked for by ``id``, in manifest order.

    A blank ``ids`` is no narrowing. An id the manifest does not hold is a 422
    ``unknown_instrument`` that names it — a silently empty answer would look
    like a manifest that lost the entry. Duplicates are collapsed because a row
    is an instrument, not a repetition.
    """

    if raw is None:
        return manifest.instruments
    wanted = [part.strip() for part in raw.split(",")]
    wanted = [part for part in wanted if part]
    if not wanted:
        return manifest.instruments

    labelled = manifest.by_id
    unknown = [name for name in dict.fromkeys(wanted) if name not in labelled]
    if unknown:
        known = ", ".join(sorted(labelled)) or "none"
        raise ApiError(
            status_code=422,
            error="unknown_instrument",
            message=(
                f"the manifest at {manifest.path} has no instrument with id "
                f"{', '.join(repr(name) for name in unknown)}; known ids: {known}"
            ),
        )
    chosen = frozenset(wanted)
    return tuple(row for row in manifest.instruments if row.id in chosen)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/instruments")
async def instruments(request: Request, ids: str | None = None) -> dict[str, Any]:
    """One row per manifest entry, in manifest order, with reasons for nulls."""

    manifest = _load()
    selected = _selection(manifest, ids)
    master = await _master(request, needed=any(row.asset != ASSET_OPTION for row in selected))
    if master.reason is not None:
        raise ApiError(
            status_code=503,
            error="scanner_unavailable",
            message=f"the scanner could not price the manifest: {master.reason}",
        )

    index = _index(master.rows)
    return {
        "as_of": datetime.now(UTC).isoformat(timespec="seconds"),
        "manifest": _manifest_block(manifest),
        "region": REGION,
        "scan": master.scan,
        "universe_total": master.universe_total,
        "signals": list(SIGNALS),
        "unavailable": [dict(item) for item in UNAVAILABLE],
        "rows": [
            _row(instrument, _lookup(index, instrument.symbol)) for instrument in selected
        ],
    }


@router.get("/instruments/meta")
async def instruments_meta(request: Request) -> dict[str, Any]:
    """The manifest's own state, plus which stock entries the master cannot price."""

    manifest = _load()
    stocks = [row for row in manifest.instruments if row.asset != ASSET_OPTION]
    master = await _master(request, needed=bool(stocks))

    if master.reason is not None:
        # The manifest's identity is still an answer; the two findings are not
        # known, so they are null rather than an empty list that would claim
        # "everything is fine".
        missing: list[str] | None = None
        unpriced: list[str] | None = None
    else:
        index = _index(master.rows)
        missing = []
        unpriced = []
        for instrument in stocks:
            row = _lookup(index, instrument.symbol)
            if row is None:
                missing.append(instrument.label)
            elif row.get("price") is None:
                unpriced.append(instrument.label)

    return {
        **_manifest_block(manifest),
        "master": {
            "source": "scan",
            "universe_total": master.universe_total,
            "scan": master.scan,
            "reason": master.reason,
        },
        "not_in_master": missing,
        "no_price": unpriced,
    }


# ---------------------------------------------------------------------------
# The published contract
# ---------------------------------------------------------------------------

_NULLABLE_NUMBER: dict[str, Any] = {
    "type": ["number", "null"],
    "description": "The scan's own cell, or null when the provider sent none.",
}

#: The JSON Schema for a ``GET /market/instruments`` document. Kept beside the
#: route that builds it and shipped verbatim at
#: ``schemas/instruments_response_v1.json`` (the tests compare the two), so a
#: generated client and the server cannot describe two different tables.
#: ``additionalProperties: false`` and a full ``required`` list are the point:
#: a row has every signal key on every row — a null is a stated absence, not a
#: missing key a client would read as zero.
INSTRUMENTS_RESPONSE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://ztockz.local/schemas/instruments-response-v1.json",
    "title": "InstrumentsResponseV1",
    "description": (
        "One row per coinids manifest entry, in manifest order. The computable "
        "signals are copied from the selector's own scan row (plus the depth "
        "overlay); option greeks, observed expiry and days-to-maturity have no "
        "source yet and are null, named once in unavailable[]. A row the scan "
        "cannot price is still present with priced=false and a reason."
    ),
    "type": "object",
    "additionalProperties": False,
    "required": [
        "as_of",
        "manifest",
        "region",
        "scan",
        "universe_total",
        "signals",
        "unavailable",
        "rows",
    ],
    "properties": {
        "as_of": {
            "type": "string",
            "description": "The read moment, ISO-8601 with seconds in UTC.",
        },
        "manifest": {"$ref": "#/$defs/manifest"},
        "region": {
            "enum": ["us"],
            "description": "The scanned population the stock entries were priced against.",
        },
        "scan": {
            "enum": ["hit", "miss", "skipped"],
            "description": (
                "How the universe was answered: the scan cache's hit/miss, or "
                "skipped when no entry needed pricing. A failed scan is a 503 "
                "error envelope, never this document."
            ),
        },
        "universe_total": {
            "type": ["integer", "null"],
            "description": "Rows in the scanned universe; null when the scan was skipped.",
        },
        "signals": {
            "type": "array",
            "items": {"type": "string"},
            "description": "The row cells that are real numbers when a row is priced.",
        },
        "unavailable": {
            "type": "array",
            "items": {"$ref": "#/$defs/unavailable"},
            "description": "Every signal this deployment cannot compute, with its reason.",
        },
        "rows": {"type": "array", "items": {"$ref": "#/$defs/row"}},
    },
    "$defs": {
        "manifest": {
            "type": "object",
            "additionalProperties": False,
            "required": ["path", "present", "version", "updated_at", "count", "legacy"],
            "properties": {
                "path": {"type": ["string", "null"]},
                "present": {
                    "type": "boolean",
                    "description": "False when the file is absent: an empty manifest, not an error.",
                },
                "version": {"type": "integer"},
                "updated_at": {"type": ["string", "null"]},
                "count": {"type": "integer"},
                "legacy": {
                    "type": "boolean",
                    "description": "True when the owner's flat {id: symbol} form was read.",
                },
            },
        },
        "unavailable": {
            "type": "object",
            "additionalProperties": False,
            "required": ["signal", "reason"],
            "properties": {
                "signal": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
        "contract": {
            "type": "object",
            "additionalProperties": False,
            "required": ["expiry", "strike", "right"],
            "properties": {
                "expiry": {"type": "string", "description": "ISO date declared by the manifest."},
                "strike": {"type": "number"},
                "right": {"enum": ["C", "P"]},
            },
        },
        "row": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "id",
                "symbol",
                "asset",
                "contract",
                "priced",
                "reason",
                *SIGNALS,
                *OPTION_SIGNALS,
            ],
            "properties": {
                "id": {
                    "type": ["string", "null"],
                    "description": "The owner's label; null when the entry has none.",
                },
                "symbol": {"type": "string"},
                "asset": {"enum": ["stock", "option"]},
                "contract": {
                    "anyOf": [{"type": "null"}, {"$ref": "#/$defs/contract"}],
                    "description": (
                        "The manifest's declared contract terms for an option row; "
                        "null for a stock. This is configuration, not an observation."
                    ),
                },
                "priced": {
                    "type": "boolean",
                    "description": (
                        "True iff the scan answered a last price for this row. An "
                        "individual signal cell can still be null when the provider "
                        "sent no value; an unpriced row carries no other cells."
                    ),
                },
                "reason": {
                    "type": ["string", "null"],
                    "description": "Why the row is unpriced; null when priced.",
                },
                **{name: dict(_NULLABLE_NUMBER) for name in SIGNALS},
                "greeks": {"type": "null"},
                "expiry": {"type": "null"},
                "days_to_maturity": {"type": "null"},
            },
        },
    },
}


__all__ = [
    "INSTRUMENTS_RESPONSE_SCHEMA",
    "OPTION_SIGNALS",
    "REASON_NO_CHAIN",
    "REASON_NO_PRICE",
    "REASON_NOT_IN_MASTER",
    "SIGNALS",
    "UNAVAILABLE",
    "router",
]
