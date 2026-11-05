"""``GET /insider-flow/{symbol}`` — the honest buy-vs-sell flow for one issuer.

What this route answers
-----------------------
One number pair per issuer — how many open-market shares its insiders bought,
and how many they sold, inside a window the caller names — computed from those
insiders' own SEC Form 4 filings by
:mod:`app.market.insider_flow`. The module carries the rules (``P`` buys,
``S`` sells, grants/exercises/gifts are neither, no price means no value, an
empty window is named not zeroed); this file is the dependency wiring, the
parameter validation and the document shape.

``GET /market/insider`` — the global insider trading feed with owner-type and
transaction-type filters. Rows carry symbol, owner, owner_type, relationship,
transaction_type, shares, value and filing_date. Sidebar blocks deliver the
top insider and top-10%-owner trades of the recent week.

Why the collector is reached through the security master
-------------------------------------------------------
A Form 4 is fetched by CIK, so the symbol must become a CIK before anything
hits the network. The identity master already holds that mapping — it is the
same one ``GET /engine/insider`` reads — so this route looks the issuer up
instead of querying a ticker directory of its own. The order is therefore
linear and each step is a different answer:

1. an unusable ``days`` is refused first, so a typo never reaches the network
   and never looks like an empty filing history;
2. no master at all is ``503 screen_unavailable`` — "nothing was ingested" must
   not look like "this company has no insiders";
3. a symbol outside the master is ``404 security_not_found``;
4. a symbol the master knows without a CIK has no filings to ask for, and
   answers a truthful empty ``200`` naming the absence instead of a 503.

Only then does the collector run, and its per-document failures come back in
``provider_errors`` rather than being smoothed into a quiet period.

Why there is no cache policy
----------------------------
The engine's own filings route memoizes, because a filing's freshness is a
collector's cycle. A flow document is a pure recompute of rows this request
already paid for, so there is nothing to store: the answer is a statement about
the newest filings on the wire, computed once, and ``as_of`` is those filings'
newest date rather than a clock this server happens to have.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.auth import ApiError
from app.market.insider_flow import (
    COLLECT_LIMIT,
    DEFAULT_WINDOW,
    build_insider_flow,
    collect_rows,
    parse_days,
)

router = APIRouter(tags=["market"])


def _identity_of(request: Request, symbol: str) -> tuple[str, str | None]:
    """``(symbol, cik)`` for one issuer, or the refusal that explains the gap.

    Mirrors ``engine_service_of``'s lookup without importing that module: a
    route module may not import another route module, and the master it needs
    is the same object either way.
    """

    runtime = getattr(request.app.state, "engine", None)
    master = getattr(runtime, "security_master", None)
    if master is None:
        raise ApiError(
            503,
            "screen_unavailable",
            "the engine has no identity master loaded yet; ingest a dataset first",
        )
    record = master.get(symbol)
    if record is None:
        raise ApiError(
            404,
            "security_not_found",
            f"security {symbol!r} is not in the loaded identity master",
        )
    return str(record.symbol), getattr(record, "cik", None)


@router.get("/insider-flow/{symbol}")
async def insider_flow(symbol: str, request: Request, days: str | None = None) -> dict[str, Any]:
    """Open-market insider buys and sells for one issuer, over 30 or 90 days.

    ``days`` is taken as raw text and validated here rather than as an ``int``
    query parameter, the way ``/engine/insider`` takes ``limit``: FastAPI would
    answer a framework-shaped ``422`` for ``?days=x``, while the house contract
    is the ``{error, errorMessage}`` envelope with ``invalid_days``. Anything
    outside the supported windows is the same refusal, so a caller cannot ask
    for ``45`` and quietly get ``30``.
    """

    window = parse_days(days if days is not None else str(DEFAULT_WINDOW))
    symbol, cik = _identity_of(request, symbol)
    if cik is None:
        return {
            "symbol": symbol,
            "windowDays": window,
            "bought_shares": None,
            "sold_shares": None,
            "bought_value": None,
            "sold_value": None,
            "net_shares": None,
            "filings": 0,
            "filers": 0,
            "otherTransactions": 0,
            "unpricedFilings": 0,
            "as_of": None,
            "reason": f"the identity master has no CIK on file for {symbol!r}",
            "provider_errors": ["no CIK"],
        }

    items, errors = await collect_rows(symbol, cik, limit=COLLECT_LIMIT)
    document = build_insider_flow(
        items,
        symbol=symbol,
        window_days=window,
    )
    document["provider_errors"] = [str(error) for error in (errors or [])]
    return document


# ---------------------------------------------------------------------------
# GET /market/insider — global insider trading feed
# ---------------------------------------------------------------------------

_VALID_OWNER_TYPES = frozenset({"insiders", "managers", "funds"})
_VALID_TRANSACTION_TYPES = frozenset({"all", "buy", "sell"})


def _owner_type_from_role(role: str | None) -> str:
    """Derive owner_type from the role string the engine parser produced."""
    if role is None:
        return "insider"
    r = role.casefold()
    has_director = "director" in r
    has_officer = "officer" in r
    has_10pct = "10%" in r or "ten percent" in r
    if has_10pct and not has_director and not has_officer:
        return "fund"
    if has_officer and not has_director:
        return "manager"
    return "insider"


@router.get("/market/insider")
async def market_insider(
    request: Request,
    owner_type: str = "insiders",
    transaction_type: str = "all",
) -> dict[str, Any]:
    """Global insider trading feed with owner-type and transaction-type filters.

    ``owner_type`` — ``insiders`` (directors + officers), ``managers`` (officers
    only), ``funds`` (10% owners). Invalid values are refused with
    ``invalid_owner_type``.

    ``transaction_type`` — ``all``, ``buy``, ``sell``. Invalid values are
    refused with ``invalid_transaction_type``.

    Returns ``rows`` (the filtered main table), ``top_weekly`` (top insider
    trades of the recent week), ``top_10_owner_weekly`` (top 10%-owner trades
    of the recent week), and ``as_of``.
    """
    ot = owner_type.strip().casefold()
    if ot not in _VALID_OWNER_TYPES:
        raise ApiError(
            422,
            "invalid_owner_type",
            f"owner_type must be one of {sorted(_VALID_OWNER_TYPES)}, got {owner_type!r}",
        )
    tt = transaction_type.strip().casefold()
    if tt not in _VALID_TRANSACTION_TYPES:
        raise ApiError(
            422,
            "invalid_transaction_type",
            f"transaction_type must be one of {sorted(_VALID_TRANSACTION_TYPES)}, got {transaction_type!r}",
        )

    runtime = getattr(request.app.state, "engine", None)
    master = getattr(runtime, "security_master", None) if runtime is not None else None
    rows: list[dict[str, Any]] = []
    if master is not None:
        import os
        from pathlib import Path

        cache_dir_raw = os.environ.get("ENGINE_CACHE_DIR")
        cache_dir = Path(cache_dir_raw) if cache_dir_raw else None
        from app.engine.insider import collect_insider

        for symbol, record in list(master.items())[:60]:
            if record.cik is None:
                continue
            try:
                items, _errors = await collect_insider(
                    record.symbol,
                    record.cik,
                    cache_dir=cache_dir,
                    limit=5,
                )
            except Exception:
                continue
            for item in items:
                doc = item.document()
                code = str(doc.get("code", "")).strip().upper()
                ttype = "Buy" if code == "P" else "Sale" if code == "S" else None
                if ttype is None:
                    continue
                role = doc.get("role")
                row_owner_type = _owner_type_from_role(str(role) if role else None)
                if row_owner_type != ot:
                    continue
                if tt != "all" and ttype.casefold() != tt:
                    continue
                shares = doc.get("shares")
                price = doc.get("price")
                value = None
                if isinstance(shares, (int, float)) and isinstance(price, (int, float)):
                    value = float(shares) * float(price)
                rows.append({
                    "symbol": record.symbol,
                    "owner": str(doc.get("owner_name", "")),
                    "owner_type": row_owner_type,
                    "relationship": str(role) if role else None,
                    "transaction_type": ttype,
                    "shares": shares,
                    "value": value,
                    "filing_date": str(doc.get("filed", "")),
                })

    # Sidebar blocks: slice rows to top by value desc within last 7 days
    now_iso = _utcnow_iso()
    week_ago = _week_ago_iso()
    recent = [r for r in rows if isinstance(r.get("filing_date"), str) and r["filing_date"] >= week_ago]
    recent.sort(key=lambda r: r.get("value") or 0, reverse=True)
    top_weekly = recent[:10]
    top_10_owner = [r for r in recent if r.get("owner_type") == "fund"][:10]

    # Sort rows by filing_date desc for the main table
    rows.sort(key=lambda r: r.get("filing_date") or "", reverse=True)

    return {
        "rows": rows,
        "top_weekly": top_weekly,
        "top_10_owner_weekly": top_10_owner,
        "as_of": now_iso[:10],
    }


def _utcnow_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _week_ago_iso() -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")


__all__ = ["COLLECT_LIMIT", "DEFAULT_WINDOW", "insider_flow", "market_insider", "parse_days", "router"]
