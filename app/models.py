"""SQLAlchemy ORM models for the nine tables (02 §4).

RED: no table is declared yet. Migrations must be additive and nullable; the
API reads entities through ``AsyncSession``, never an ``AsyncConnection``
(05 R14).
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all MEE tables."""
