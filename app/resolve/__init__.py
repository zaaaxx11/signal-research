"""Resolver, reference master and estimation (02 §3.3, Phase 1).

The package is pure data + pure functions: it reads the reference master, turns
a ``MarketSpec`` into a provable instrument list (or an honest question), and
projects a deterministic plan. It owns **no** I/O beyond reading the master
file, so the API layer decides when to touch the database or issue a quote.
"""

from app.resolve.estimator import (
    build_estimate,
    build_eta,
    build_plan,
    build_quote_document,
    select_fields,
)
from app.resolve.reference import Reference, ReferenceError, load_reference
from app.resolve.universe import Resolution, resolve_market

__all__ = [
    "Reference",
    "ReferenceError",
    "Resolution",
    "build_estimate",
    "build_eta",
    "build_plan",
    "build_quote_document",
    "load_reference",
    "resolve_market",
    "select_fields",
]
