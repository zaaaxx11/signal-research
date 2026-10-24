"""``GET /news/{symbol}`` (P12): eight headlines as one consolidated row.

The route is a thin projection of :class:`app.news.service.NewsService` — all
merging, ordering, caching and error accounting lives in the service so the
document a client reads is the same one a test can build without HTTP.

Three answers, and each one means exactly one thing:

* **200** — a document. ``count`` may be ``0``: two healthy wires with nothing to
  say about a ticker is "no news", which is an answer. ``provider_errors`` names
  any wire that failed while the others answered.
* **422** — ``symbol`` is not a ticker (:data:`app.market.sources.SYMBOL_RE`), or
  ``limit`` is outside ``1..MAX_POOL_ITEMS`` (that one is FastAPI's own 422, and
  it names the parameter).
* **503** — **every** provider failed. A 200 with an empty list here would tell a
  reader "no news exists" when the truth is "nobody could be asked", so the
  outage is an error the caller can retry.

The service is attached to ``app.state.news`` by :func:`app.main.create_app`:
one cache per application, injectable by a test (or an operator) without a code
path, and never a module-level global that two apps in one process would share.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from app.auth import ApiError
from app.news.service import (
    DEFAULT_ITEMS,
    MAX_POOL_ITEMS,
    AllNewsProvidersFailed,
    InvalidNewsSymbol,
    NewsService,
    build_news_service,
    normalize_symbol,
)

router = APIRouter(prefix="/news", tags=["news"])


def news_service_of(request: Request) -> NewsService:
    """The service built by ``create_app``, or a default one on first use.

    The fallback exists so a hand-built ``FastAPI`` in a probe still answers; in
    production the service is always the one ``create_app`` attached, which is
    what makes the fifteen-minute cache a property of the process.
    """

    service: NewsService | None = getattr(request.app.state, "news", None)
    if service is None:
        service = build_news_service()
        request.app.state.news = service
    return service


@router.get("/{symbol}")
async def news(
    symbol: str,
    request: Request,
    limit: int | None = Query(
        default=None,
        ge=1,
        le=MAX_POOL_ITEMS,
        description=(
            "Headlines to return. Omitted → the service's configured panel size "
            f"({DEFAULT_ITEMS} unless MEE_NEWS_LIMIT says otherwise). The service "
            "caches the merged pool, so a second read at another limit is still "
            f"zero provider requests. Range 1..{MAX_POOL_ITEMS}."
        ),
    ),
) -> dict[str, Any]:
    """One consolidated news document for ``symbol``."""

    try:
        ticker = normalize_symbol(symbol)
    except InvalidNewsSymbol as exc:
        raise ApiError(
            status_code=422,
            error="invalid_symbol",
            message=(
                f"{exc}; a ticker is 1-32 characters from "
                "A-Z, 0-9 and . ^ = _ -"
            ),
        ) from exc

    try:
        return await news_service_of(request).news(ticker, limit=limit)
    except AllNewsProvidersFailed as exc:
        raise ApiError(
            status_code=503,
            error="news_unavailable",
            message=str(exc),
        ) from exc


__all__ = ["news", "news_service_of", "router"]
