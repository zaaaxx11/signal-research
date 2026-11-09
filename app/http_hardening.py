"""Response headers and a request cap for a public, keyless API.

Why this exists
---------------
The API is deliberately open: :mod:`app.auth` fingerprints the ``X-API-Key``
header for *ownership* and Phase 0 serves anonymous callers (the module's own
docstring states it). A public read API still owes the browser two things it
cannot get from the routes:

* a **policy** — what this origin may load and be framed by, so a browser does
  not have to guess. Without it a cross-site script injected anywhere on the
  origin can reach anything, and the document can be framed for clickjacking;
* a **request cap** — a public endpoint that accepts arbitrarily large bodies is
  a free way to spend this box's memory.

Decisions, stated rather than hidden
------------------------------------
* ``script-src 'self'`` — the built document contains **no inline script** (the
  Vite output was checked) and the bundle contains no ``eval``/``new Function``,
  so no nonce or hash is needed. This is the strictest useful policy; the
  browser gate is what proves it (a CSP that breaks the app is silent in tests).
* ``style-src 'self' 'unsafe-inline'`` — the grid sets per-row ``style``
  attributes (widths, heights), which CSP treats as inline style. A nonce cannot
  cover a style attribute, so this one exception is deliberate.
* ``img-src`` allows ``https:`` besides ``self`` and ``data:`` — the UI renders
  upstream-hosted images (a crypto logo, a brand mark) and naming that host here
  would put a data source in a public header, which the operator's rule
  forbids. The alternative — proxying every image — is a product change.
* ``HSTS`` is sent only when the request actually arrived over HTTPS (the edge
  terminates TLS and forwards ``x-forwarded-proto``); announcing it on a plain
  HTTP origin would pin a scheme that origin cannot serve.
* The cap reads ``Content-Length``. A chunked body has none, so it is not capped
  here; this API's POSTs are small JSON documents and a streamed upload is not a
  path it offers. Stated so nobody mistakes this for a hard guarantee.
"""

from __future__ import annotations

from typing import Any, Callable

#: The default request cap: 1 MiB. The largest legitimate body is a filter
#: document (tens of kilobytes); a megabyte is headroom, not a suggestion.
MAX_BODY_BYTES = 1_048_576

#: The content security policy. See the module docstring for every exception.
CSP = "; ".join(
    (
        "default-src 'self'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: https:",
        "font-src 'self'",
        "connect-src 'self'",
    )
)

#: Headers every response carries, whatever the route.
STATIC_HEADERS: tuple[tuple[str, str], ...] = (
    ("content-security-policy", CSP),
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("referrer-policy", "strict-origin-when-cross-origin"),
    ("permissions-policy", "geolocation=(), microphone=(), camera=(), payment=()"),
)

#: Sent only over HTTPS, with a year of max-age. No ``preload``: preloading is a
#: commitment about every subdomain, which is the operator's call, not ours.
HSTS = "max-age=31536000; includeSubDomains"


def _content_length(scope: dict[str, Any]) -> int | None:
    """The declared body size, or ``None`` when the client did not say."""

    for name, value in scope.get("headers") or ():
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _too_large_body(max_bytes: int) -> bytes:
    """The house envelope, not FastAPI's ``{"detail": ...}``."""

    limit = max_bytes // 1024
    return (
        '{"error":"payload_too_large","errorMessage":"request body over '
        f'{limit} KiB"}}'
    ).encode("utf-8")


class HardeningMiddleware:
    """Pure ASGI: no body buffering, so a large upload is refused unmoved.

    ``BaseHTTPMiddleware`` would read the body to hand it on, which is exactly
    what a size cap must not do.
    """

    def __init__(self, app: Callable, *, max_body_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        declared = _content_length(scope)
        if declared is not None and declared > self.max_body_bytes:
            body = _too_large_body(self.max_body_bytes)
            await send(
                {
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        *((name.encode(), value.encode()) for name, value in STATIC_HEADERS),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        secure = any(
            name == b"x-forwarded-proto" and value == b"https"
            for name, value in scope.get("headers") or ()
        )
        extra = (("strict-transport-security", HSTS),) if secure else ()

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {name.lower() for name, _ in headers}
                for name, value in (*STATIC_HEADERS, *extra):
                    if name.encode() not in present:
                        headers.append((name.encode(), value.encode()))
            await send(message)

        await self.app(scope, receive, send_with_headers)


def install(application: Any, *, max_body_bytes: int = MAX_BODY_BYTES) -> None:
    """Put the middleware in front of everything, mounts included."""

    application.add_middleware(HardeningMiddleware, max_body_bytes=max_body_bytes)


__all__ = [
    "CSP",
    "HSTS",
    "HardeningMiddleware",
    "MAX_BODY_BYTES",
    "STATIC_HEADERS",
    "install",
]
