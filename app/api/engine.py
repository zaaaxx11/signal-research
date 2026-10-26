"""Read-only ``/engine`` routes.

These routes expose the frozen catalog and the in-memory identity master without
inventing prices or fundamentals. ``POST /engine/screen`` reads a row provider
that was already loaded by ingestion; the route performs no network I/O. A
deployment with no provider answers ``503 screen_unavailable`` rather than an
empty screen.

Unknown keys use the same ``{error, errorMessage}`` envelope as the rest of the
API. A security lookup against an empty master is a 404, not an empty 200:
"nobody loaded a master" must not look like "the market has no AAPL".
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request

from app.auth import ApiError
from app.api.market import quote_engine_of
from app.engine.quotes import quote_fields
from app.engine.screener import ScreenError
from app.engine.service import EngineService, EngineUnavailable

router = APIRouter(prefix="/engine", tags=["engine"])


def engine_service_of(request: Request) -> EngineService:
    service = getattr(request.app.state, "engine", None)
    if isinstance(service, EngineService):
        return service
    return EngineService()


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    document = engine_service_of(request).health()
    startup_error = getattr(request.app.state, "engine_startup_error", None)
    if startup_error is not None:
        document["startup_error"] = startup_error
    quote_error = getattr(request.app.state, "engine_quote_error", None)
    if quote_error is not None:
        document["quote_error"] = quote_error
    return document


@router.get("/catalog")
async def catalog(request: Request) -> dict[str, Any]:
    return engine_service_of(request).catalog()


@router.get("/catalog/{key}")
async def metric(key: str, request: Request) -> dict[str, Any]:
    document = engine_service_of(request).metric(key)
    if document is None:
        raise ApiError(
            404,
            "metric_not_found",
            f"no metric named {key!r}; GET /engine/catalog lists the 116 fields",
        )
    return document


@router.post("/screen")
async def screen(request: Request) -> dict[str, Any]:
    """Apply a local screen query to the configured row provider.

    The route is read-only and does no fetching. A deployment with no dataset
    gets an explicit 503; a malformed filter is a 422 even in that state.
    """

    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ApiError(
            422,
            "invalid_json",
            f"the screen body is not valid JSON: {exc}",
        ) from exc
    if not isinstance(payload, dict):
        raise ApiError(422, "invalid_body", "the screen body must be a JSON object")

    refresh_quotes = bool(payload.pop("refresh_quotes", True))
    service = engine_service_of(request)
    try:
        result = service.screen(payload)
    except ScreenError as exc:
        raise ApiError(422, exc.code, str(exc)) from exc
    except EngineUnavailable as exc:
        raise ApiError(503, exc.code, str(exc)) from exc

    if refresh_quotes and service.dataset is not None:
        symbols = [
            row["ticker"].value
            for row in result.rows
            if "ticker" in row and isinstance(row["ticker"].value, str)
        ]
        if symbols:
            try:
                engine = quote_engine_of(request)
                ticks = await engine.poll_many(symbols)
            except Exception:  # noqa: BLE001 - a quote outage must not kill the screen
                ticks = {}
            quotes = {
                symbol: quote_fields(tick)
                for symbol, tick in ticks.items()
                if tick is not None
            }
            if quotes:
                service = service.with_quotes(quotes)
                request.app.state.engine = service
                try:
                    result = service.screen(payload)
                except ScreenError as exc:  # pragma: no cover - same validated query
                    raise ApiError(422, exc.code, str(exc)) from exc

    return EngineService.screen_document(result)


@router.get("/security/{symbol}")
async def security(symbol: str, request: Request) -> dict[str, Any]:
    document = engine_service_of(request).security(symbol)
    if document is None:
        raise ApiError(
            404,
            "security_not_found",
            f"security {symbol!r} is not in the loaded identity master",
        )
    return document


__all__ = ["engine_service_of", "router"]
