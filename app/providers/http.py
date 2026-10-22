"""The single HTTP seam. Only this module may import ``httpx2`` (05 §3.4).

Every adapter receives a client from here; migrating the client (R1) is then a
one-file change instead of a twelve-file change. The AST test enforces it.

Adapters must call ``http.build_client(...)`` as a **module attribute**, never
``from app.providers.http import build_client``: the Phase-2 suite installs a
``MockTransport`` by monkeypatching that attribute, and a from-import would keep
a reference to the real factory (and therefore to a socket).

The client is opened per fetch (``async with``) and closed at the end of it.
That is deliberate: a provider response is a few kilobytes and SQLite-style
connection reuse buys nothing here, while a leaked client keeps a pool alive
across event loops — which is exactly what the test suite does.
"""

from __future__ import annotations

import httpx2

#: Provider traffic is JSON over TLS with no interactive step; 20 s read is the
#: documented ceiling in 02 §3.4 and short enough to leave room for the ladder.
DEFAULT_TIMEOUT_S = 20.0
DEFAULT_CONNECT_TIMEOUT_S = 5.0
DEFAULT_MAX_CONNECTIONS = 8


def build_client(
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    connect_s: float = DEFAULT_CONNECT_TIMEOUT_S,
    max_connections: int = DEFAULT_MAX_CONNECTIONS,
    http2: bool = True,
    headers: dict[str, str] | None = None,
) -> httpx2.AsyncClient:
    """Build a provider-scoped AsyncClient (timeouts, limits, no redirects).

    ``follow_redirects=False`` is a security property, not an optimisation: an
    allowlisted host must not be able to redirect the walk to an arbitrary one
    (02 §3.11 SSRF). A caller that wants a hop must follow it explicitly, and
    the ladder simply treats a 3xx as a failed rung.
    """

    return httpx2.AsyncClient(
        timeout=httpx2.Timeout(connect=connect_s, read=timeout_s, write=timeout_s, pool=connect_s),
        follow_redirects=False,
        limits=httpx2.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max(1, max_connections // 2),
        ),
        http2=http2,
        headers=headers or {},
        trust_env=False,
    )


__all__ = ["DEFAULT_TIMEOUT_S", "build_client"]
