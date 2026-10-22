"""ASGI entrypoint.

Phase-0 skeleton: the app is wired (routers are included) but every business
endpoint raises ``NotImplementedError``. That is intentional — the RED suite
(``tests/test_api.py``) defines the responses that must exist.
"""

from __future__ import annotations

from fastapi import FastAPI

from app import __version__
from app.api import health, markets, providers, requests


def create_app() -> FastAPI:
    """Build the ASGI application."""

    application = FastAPI(
        title="Market Enrichment Engine",
        version=__version__,
        docs_url=None,
        redoc_url=None,
    )
    application.include_router(health.router)
    application.include_router(markets.router)
    application.include_router(requests.router)
    application.include_router(providers.router)
    return application


app = create_app()
