"""Egress ladder for IP-limited providers (zcrapers pattern, trimmed).

The provider ladder in :mod:`app.providers.ladder` answers *which capability
can serve this fact*. This module answers the layer underneath it: *which
network egress may send the request*. They are deliberately separate because
the same Yahoo rung can be fine on one exit and ``429/401`` on another, and
mixing the two decisions in one class makes a retry look like a fallback
provider.

The three rungs are the zcrapers production order:

``direct`` → ``free`` (up to ``free_attempts`` ranked proxies) → ``paid``

A failed proxy enters a cooldown instead of being retried immediately; a
success clears it. The ranked list is the same ``pool_ranked.json`` shape
zcrapers prepares (``{"proxies": [{"proxy", "score", "ok"}, ...]}``), with a
plain text list accepted as the smaller form. No credential is read here.

This is a pool, not a scheduler: it never sleeps, never starts a request and
never invents a proxy. The caller decides how many attempts its latency budget
can afford.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

FREE = "free"
PAID = "paid"
TIERS: tuple[str, ...] = (FREE, PAID)


def _normalize(raw: Any) -> str | None:
    """``1.2.3.4:8080`` → ``http://1.2.3.4:8080``; blank/comment → ``None``."""

    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.startswith("#"):
        return None
    if "://" not in text:
        text = f"http://{text}"
    return text


def _looks_like_path(value: str) -> bool:
    return bool(value) and "://" not in value and Path(value).expanduser().exists()


class ProxyPool:
    """Ranked free proxies, optional paid proxies, cooldown and rotation."""

    def __init__(
        self,
        *,
        free_urls: Iterable[str] = (),
        paid_urls: Iterable[str] = (),
        free_scores: Mapping[str, float] | None = None,
        cooldown_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        free_attempts: int = 3,
    ) -> None:
        self.free_urls: list[str] = []
        self.paid_urls: list[str] = []
        self._scores: dict[str, float] = dict(free_scores or {})
        self.cooldown_s = max(0.0, float(cooldown_s))
        self.free_attempts = max(1, int(free_attempts))
        self._clock = clock
        self._cooldown_until: dict[str, float] = {}
        self._cursor: dict[str, int] = {FREE: 0, PAID: 0}
        self._add(self.free_urls, free_urls)
        self._add(self.paid_urls, paid_urls)

    @staticmethod
    def _add(target: list[str], entries: Iterable[str]) -> None:
        for raw in entries:
            normalized = _normalize(raw)
            if normalized and normalized not in target:
                target.append(normalized)

    @classmethod
    def from_file(
        cls,
        path: str | Path | None,
        *,
        cooldown_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        free_attempts: int = 3,
    ) -> "ProxyPool":
        """Load ranked JSON or a one-proxy-per-line text file.

        A missing/unreadable file is an empty pool, not an exception: the
        caller can then honestly report that the proxy rung is unavailable.
        """

        pool = cls(cooldown_s=cooldown_s, clock=clock, free_attempts=free_attempts)
        if not path:
            return pool
        target = Path(path).expanduser()
        try:
            text = target.read_text(encoding="utf-8")
        except OSError:
            return pool
        if target.suffix.lower() == ".json":
            pool._load_ranked(text)
        else:
            pool._add(pool.free_urls, text.splitlines())
        return pool

    def _load_ranked(self, text: str) -> None:
        try:
            document = json.loads(text)
        except ValueError:
            return
        items = document.get("proxies", []) if isinstance(document, dict) else document
        if not isinstance(items, list):
            return
        ranked: list[tuple[float, int, str]] = []
        for position, item in enumerate(items):
            if isinstance(item, Mapping):
                if item.get("ok") is False:
                    continue
                proxy = _normalize(item.get("proxy"))
                try:
                    score = float(item.get("score") or 0.0)
                except (TypeError, ValueError):
                    score = 0.0
            else:
                proxy = _normalize(item)
                score = 0.0
            if proxy:
                ranked.append((score, position, proxy))
                self._scores[proxy] = score
        ranked.sort(key=lambda entry: (-entry[0], entry[1]))
        for _score, _position, proxy in ranked:
            if proxy not in self.free_urls:
                self.free_urls.append(proxy)

    def _tier_urls(self, tier: str) -> list[str]:
        if tier == PAID:
            return self.paid_urls
        if tier == FREE:
            return self.free_urls
        raise ValueError(f"unknown proxy tier {tier!r}; expected {FREE!r} or {PAID!r}")

    def _live(self, tier: str) -> list[str]:
        now = self._clock()
        return [url for url in self._tier_urls(tier) if self._cooldown_until.get(url, 0.0) <= now]

    def _ordered_live(self, tier: str) -> list[str]:
        live = self._live(tier)
        return sorted(live, key=lambda url: -float(self._scores.get(url, 0.0)))

    def available(self, tier: str) -> bool:
        return bool(self._live(tier))

    def acquire(self, tier: str) -> str | None:
        """Next live proxy (highest score first, round-robin within a score)."""

        live = self._ordered_live(tier)
        if not live:
            return None
        index = self._cursor.get(tier, 0) % len(live)
        self._cursor[tier] = index + 1
        return live[index]

    def report_failure(self, proxy: str | None, tier: str) -> None:
        if proxy:
            self._cooldown_until[proxy] = self._clock() + self.cooldown_s

    def report_success(self, proxy: str | None, tier: str) -> None:
        if proxy:
            self._cooldown_until.pop(proxy, None)

    def snapshot(self) -> dict[str, Any]:
        return {
            "free_urls": len(self.free_urls),
            "free_live": len(self._live(FREE)),
            "paid_urls": len(self.paid_urls),
            "paid_live": len(self._live(PAID)),
            "scored": len(self._scores),
            "cooldown_s": self.cooldown_s,
            "free_attempts": self.free_attempts,
        }


__all__ = ["FREE", "PAID", "TIERS", "ProxyPool"]
