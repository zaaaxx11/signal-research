"""Response validators for the read-only data routes.

A watchlist client polls. Most polls return the same bytes. An ETag lets the
server answer ``304 Not Modified`` with an empty body, so the saving is the
payload, not the round trip.

The validator is derived from the **store revision**, not from a hash of the
response: the revision is bumped when a cell actually changes, so computing the
tag costs nothing and stays correct while a cycle is writing. A hash of the body
would also be correct, but it would re-serialise 4,217 symbols on every poll to
decide that nothing changed.
"""

from __future__ import annotations

import hashlib
from typing import Any

__all__ = ["cache_headers", "etag_for", "matches", "not_modified"]


def _digest(*parts: str) -> str:
    """A short, stable digest over the parts that name this entity."""

    digest = hashlib.sha1()
    for part in parts:
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()[:16]


def etag_for(namespace: str, revision: int, *parts: str) -> str:
    """A quoted strong ETag for one namespace, revision and query.

    ``namespace`` keeps two routes from ever producing the same tag for
    different documents; ``revision`` is the store's change counter; ``parts``
    are the query coordinates (the symbol list, in request order, because that
    is what the caller asked for).
    """

    return f'"{namespace}-r{int(revision)}-{_digest(*parts)}"'


def matches(etag: str, header: str | None) -> bool:
    """Whether an ``If-None-Match`` header names ``etag``.

    Handles the forms clients actually send: a single tag, a comma-separated
    list, ``*`` (any current representation) and the weak ``W/`` prefix. Weak
    comparison is correct here: the route is read-only and there is no
    semantically different but byte-equal variant to protect against.
    """

    if not header:
        return False
    for candidate in str(header).split(","):
        value = candidate.strip()
        if not value:
            continue
        if value == "*":
            return True
        if value.startswith("W/"):
            value = value[2:].strip()
        if value == etag:
            return True
    return False


def cache_headers(etag: str, *, max_age: int = 0) -> dict[str, str]:
    """The headers a 200 and a 304 both carry.

    ``must-revalidate`` is deliberate: a client may cache the body, but it must
    ask before reusing it. Combined with the ETag the ask is cheap — a 304 with
    no body — while a stale hit is impossible.
    """

    return {
        "ETag": etag,
        "Cache-Control": f"private, max-age={int(max_age)}, must-revalidate",
    }


def not_modified(request: Any, etag: str) -> bool:
    """Whether the request asked for exactly this entity and may get a 304."""

    return matches(etag, request.headers.get("if-none-match"))
