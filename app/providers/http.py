"""The single HTTP seam. Only this module may import ``httpx2`` (05 §3.4).

Every adapter receives a client from here; migrating the client (R1) is then a
one-file change instead of a twelve-file change. The AST test enforces it.
"""

from __future__ import annotations

import httpx2


def build_client(*, timeout_s: float = 20.0) -> httpx2.AsyncClient:
    """Build a provider-scoped AsyncClient (timeouts, limits, no redirects)."""

    raise NotImplementedError(
        f"httpx2 client factory is not implemented (Phase 0 RED): timeout_s={timeout_s}"
    )
