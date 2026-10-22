"""Environment guards (05 §3.4 rule 4, 05 R3, 05 Lampiran A)."""

from __future__ import annotations

import importlib.metadata
import sys

PINS = {
    "fastapi": "0.159.1",
    "pydantic": "2.14.1",
    "pydantic-settings": "2.16.1",
    "uvicorn": "0.54.0",
    "sqlalchemy": "2.1.6",
    "alembic": "1.20.1",
    "aiosqlite": "0.22.1",
    "httpx2": "2.16.0",
    "tenacity": "9.2.1",
    "pyarrow": "26.0.0",
    "xlsxwriter": "3.3.0",
    "structlog": "26.1.0",
    "prometheus-client": "0.26.0",
}


def test_python_is_314() -> None:
    assert sys.version_info[:2] == (3, 14), sys.version


def test_greenlet_is_importable() -> None:
    # sqlalchemy[asyncio] without greenlet fails at create_async_engine (05 R3).
    import greenlet

    assert greenlet.__version__


def test_exact_pins_are_installed() -> None:
    actual = {name: importlib.metadata.version(name) for name in PINS}
    assert actual == PINS
