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


__all__ = ["COLLECT_LIMIT", "DEFAULT_WINDOW", "insider_flow", "parse_days", "router"]
