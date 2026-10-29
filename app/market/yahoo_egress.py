"""Yahoo egress ladder: direct first, then a ranked free proxy.

Yahoo's crumb endpoint throttles an IP, not a cookie: once ``/v1/test/getcrumb``
answers ``429 Too Many Requests`` a fresh cookie jar does not help, because the
refusal is addressed to the network exit. That is what made the 4,217-symbol
run fall from 109/116 to 75/116 — every crumb-gated call (v7 batch quote and
quoteSummary) went dark while the crumb-free chart endpoint kept working.

The fix is the layer under the provider: try the direct exit, and when its
handshake is refused, walk the ranked free-proxy list until one answers with a
real token. The proxy is then handed to the sources as their ``proxy`` argument,
so the whole pass rides one working exit instead of re-probing per request.

This module only *finds* an exit. It never retries a request, never sleeps on a
schedule and never invents a proxy; a caller with no working exit gets ``None``
and decides what an unauthenticated pass is worth.
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterable

from app.providers import http as provider_http

#: Cookie warm-up, then the token exchange: the host that sets the cookie is
#: not the host that answers the crumb.
COOKIE_URL = "https://fc.yahoo.com"
CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

#: A crumb is a short URL-safe token; an HTML challenge page or the literal
#: ``Too Many Requests`` body is not one.
_MIN_CRUMB = 6
_MAX_CRUMB = 24


def _looks_like_crumb(text: str) -> bool:
    if not (_MIN_CRUMB <= len(text) <= _MAX_CRUMB):
        return False
    return all(ch.isalnum() or ch in "-_." for ch in text)


class YahooEgress:
    """Pick the network exit whose crumb handshake actually succeeds.

    ``proxies`` is an ordered list of proxy URLs (the ranked pool's output).
    ``max_attempts`` bounds how many exits are tried in one search, because a
    free pool has hundreds of dead entries and a full walk would cost minutes.
    """

    def __init__(
        self,
        *,
        proxies: Iterable[str] = (),
        max_attempts: int = 8,
        timeout_s: float = 20.0,
        user_agent: str = BROWSER_USER_AGENT,
    ) -> None:
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {max_attempts!r}")
        if not timeout_s > 0:
            raise ValueError(f"timeout_s must be > 0, got {timeout_s!r}")
        self.proxies = [str(p).strip() for p in proxies if str(p).strip()]
        self.max_attempts = int(max_attempts)
        self.timeout_s = float(timeout_s)
        self.user_agent = str(user_agent)
        #: Exit → refusal reason for the most recent search.
        self.last_errors: dict[str, str] = {}

    def headers(self) -> dict[str, str]:
        return {
            "user-agent": self.user_agent,
            "accept": "application/json,text/plain,*/*",
            "accept-language": "en-US,en;q=0.9",
        }

    async def probe(self, proxy: str | None) -> str | None:
        """The crumb this exit answers, or ``None`` when it refuses.

        A proxy that cannot connect, times out, or answers a challenge is a
        refusal like any other — the caller only cares whether a usable token
        came back.
        """

        label = proxy or "direct"
        try:
            async with provider_http.build_client(
                timeout_s=self.timeout_s,
                headers=self.headers(),
                proxy=proxy,
            ) as client:
                # fc.yahoo.com answers 404 by design; the cookie it sets is the
                # point, so a non-200 here is not a failure.
                await client.get(COOKIE_URL)
                response = await client.get(CRUMB_URL)
        except Exception as exc:  # noqa: BLE001 - a dead exit is not an error
            self.last_errors[label] = f"{type(exc).__name__}: {exc}"
            return None

        status = int(getattr(response, "status_code", 0) or 0)
        token = str(getattr(response, "text", "") or "").strip()
        if status != 200 or not _looks_like_crumb(token):
            self.last_errors[label] = f"HTTP {status}: {token[:40]!r}"
            return None
        self.last_errors.pop(label, None)
        return token

    async def find_proxy(self) -> str | None:
        """The first working exit: ``None`` for direct, a proxy URL otherwise.

        Direct is tried once. Only when it is refused does the pool get walked,
        and only up to ``max_attempts`` exits — enough to find a live one in a
        pool that measured 3 working out of 12.
        """

        self.last_errors = {}
        if await self.probe(None) is not None:
            return None

        candidates = self.proxies[: self.max_attempts]
        if not candidates:
            return None
        results = await asyncio.gather(*(self.probe(p) for p in candidates))
        for proxy, token in zip(candidates, results):
            if token is not None:
                return proxy
        return None


__all__ = ["BROWSER_USER_AGENT", "COOKIE_URL", "CRUMB_URL", "YahooEgress"]
