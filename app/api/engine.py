"""Read-only ``/engine`` routes.

The engine is not yet the screen's data path. These routes expose the frozen
catalog and the in-memory identity master without inventing prices or
fundamentals. They are intentionally read-only and perform no I/O, so they are
safe to mount while the ingestion/source adapters are still being built.

Unknown keys use the same ``{error, errorMessage}`` envelope as the rest of the
API. A security lookup against an empty master is a 404, not an empty 200:
"nobody loaded a master" must not look like "the market has no AAPL".
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.auth import ApiError
from app.engine.service import EngineService

router = APIRouter(prefix="/engine", tags=["engine"])


def engine_service_of(request: Request) -> EngineService:
    service = getattr(request.app.state, "engine", None)
    if isinstance(service, EngineService):
        return service
    return EngineService()


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    return engine_service_of(request).health()


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
