#!/usr/bin/env python3
"""Measure the Phase 7 performance budgets against a running Ztockz.

The plan (``.hermes/plans/2026-10-30_ui-interface.md`` §9) states three budgets:

* **first paint ≤ 1.5 s on a cold cache** — the document, the bundle and the
  first paint, with nothing already in the browser's cache;
* **filter change ≤ 2 s p50** — ``POST /screens/select``, the request the UI
  sends when a chip or a bucket changes;
* **ETag hits ≤ 150 ms** — a revalidation that should answer ``304``.

Adjectives do not survive a release, so this script prints numbers and exits
non-zero when one of them is over budget. It is deliberately a *script*: no new
runtime dependency lands in ``app/``, nothing imports it, and an operator can
run it against a deployment without editing it.

How the three are measured, and what "cold" means here
-----------------------------------------------------
**HTTP timings** use ``http.client`` on a persistent connection: TTFB is the
interval from sending the request to the response headers arriving, total adds
the body. That is the same split a browser reports as ``responseStart`` /
``responseEnd``, without a browser in the way.

**First paint** is measured in **Chromium over CDP**, not inferred from bytes.
Three numbers are read from the page itself: ``first-contentful-paint`` and
``first-paint`` from the Paint Timing API, and ``domContentLoadedEventEnd`` /
``loadEventEnd`` / ``responseStart`` from Navigation Timing. None of them can be
produced by a curl that never rendered anything.

Cold has two meanings, and they are not interchangeable:

* *browser cache* — this probe is genuinely cold, three ways: a fresh
  ``--user-data-dir`` per run (empty disk cache), ``Network.setCacheDisabled``
  (the protocol-level bypass), and a unique ``?cache_bust=<nonce>`` on the
  document URL (so no stored response can answer the navigation even if the
  first two were ignored). Every run therefore refetches the document, the
  bundle and the stylesheet.
* *OS page cache* — **not** busted by default. The app reads its shell with
  ``stat`` + ``sendfile``; dropping the kernel's page cache needs root, and a
  probe that silently claims it did would be lying. The ``cold`` row of the
  report says which one it measured (``--drop-os-caches`` asks for the other,
  and fails loudly rather than pretending when it cannot).

A cold document is 801 bytes and a cold bundle ~40 KB gzipped; the page-cache
question is real but bounded by that, and the report states it either way.

The ETag budget is measured twice, and the two numbers are never averaged
----------------------------------------------------------------------
``GET /app`` (and ``/`` once the cutover is live) is the shell document: a miss
is a ``200``, a hit is a ``304`` in a couple of milliseconds, and that is the
request a returning visitor's browser makes before it can paint.

``POST /screens/select`` also carries a weak ETag, and the shipped client does
revalidate it (``web/src/data/screens.ts`` keys its cache by it). That route
**recomputes its answer before it compares the tag** — it must, because the tag
names the answer — so its ``304`` costs what its ``200`` costs minus the body.
It is measured, printed and judged separately, and by default it does not
decide the exit code: it is a property of that route's design, not of the
cutover, and the plan's 150 ms figure is a cache-hit figure. ``--strict``
promotes it to a gate for anyone who wants the harsher reading.

Usage
-----
    python scripts/ui_perf_probe.py --base http://127.0.0.1:8099
    python scripts/ui_perf_probe.py --base http://127.0.0.1:8099 --json /tmp/perf.json
    python scripts/ui_perf_probe.py --reset-scan-cache --strict

Exit codes: ``0`` every gated budget met · ``1`` a budget was missed · ``2``
the probe could not measure (no browser, endpoint down, non-200 answer).
"""

from __future__ import annotations

import argparse
import glob
import http.client
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

#: The plan's numbers (§9). Milliseconds.
FIRST_PAINT_BUDGET_MS = 1_500.0
FILTER_P50_BUDGET_MS = 2_000.0
ETAG_HIT_BUDGET_MS = 150.0

#: How many requests each distribution is built from. The filter number is a
#: p50, and a p50 of fewer than ten samples is a median of anecdotes.
FILTER_SAMPLES = 12
ETAG_SAMPLES = 5
PAINT_SAMPLES = 3

#: The selection bodies a filter change produces, cycled in order so
#: consecutive requests ask different questions — that is what makes the
#: request a *filter change* rather than a repeat. Every one of them matches a
#: full 100-row page on the live US universe, so the measurement includes the
#: row shaping a real panel pays for: a filter that matches nothing would time
#: the universe build and skip the half of the route that renders.
FILTER_BODIES: tuple[dict[str, Any], ...] = tuple(
    {
        "mode": "criteria",
        "region": "us",
        "criteria": {
            "filters": [flt],
            "sort": "market_cap",
            "limit": 100,
            "offset": 0,
            "common_only": False,
        },
    }
    for flt in (
        {"key": "pe", "op": "lt", "value": 20},
        {"key": "market_cap", "op": "gt", "value": 10_000_000_000},
        {"key": "sec", "op": "eq", "value": "Electronic Technology"},
        {"key": "sec", "op": "eq", "value": "Health Technology"},
        {"key": "price", "op": "gt", "value": 50},
    )
)


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------


def percentile(values: Sequence[float], fraction: float) -> float:
    """The ``fraction`` percentile by linear interpolation (``statistics``-free)."""

    if not values:
        return float("nan")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


@dataclass
class Check:
    """One measured distribution and its verdict against a budget."""

    name: str
    samples: list[float] = field(default_factory=list)
    budget_ms: float | None = None
    gated: bool = True
    note: str = ""

    @property
    def n(self) -> int:
        return len(self.samples)

    @property
    def p50(self) -> float:
        return percentile(self.samples, 0.50)

    @property
    def p90(self) -> float:
        return percentile(self.samples, 0.90)

    @property
    def passed(self) -> bool:
        if self.budget_ms is None:
            return True
        return self.p50 <= self.budget_ms

    @property
    def verdict(self) -> str:
        if self.budget_ms is None:
            return "measured"
        if self.passed:
            return "PASS"
        return "FAIL" if self.gated else "FAIL (advisory)"

    def document(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "n": self.n,
            "min_ms": round(min(self.samples), 3) if self.samples else None,
            "p50_ms": round(self.p50, 3) if self.samples else None,
            "p90_ms": round(self.p90, 3) if self.samples else None,
            "max_ms": round(max(self.samples), 3) if self.samples else None,
            "budget_ms": self.budget_ms,
            "gated": self.gated,
            "verdict": self.verdict,
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# HTTP timings
# ---------------------------------------------------------------------------


@dataclass
class TimedResponse:
    status: int
    ttfb_ms: float
    total_ms: float
    headers: dict[str, str]
    body: bytes


class Client:
    """A keep-alive HTTP client with a TTFB/total split per request.

    One connection is reused — a browser reuses one too, and a fresh TCP
    handshake per sample would be measured as if it were server time.
    """

    def __init__(self, base: str, timeout_s: float = 60.0) -> None:
        parts = urllib.parse.urlsplit(base)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError(f"base must be an http(s) URL, got {base!r}")
        self.base = base.rstrip("/")
        self._https = parts.scheme == "https"
        self._host = parts.hostname
        self._port = parts.port or (443 if self._https else 80)
        self._timeout = timeout_s
        self._connection: http.client.HTTPConnection | None = None

    def _connect(self) -> http.client.HTTPConnection:
        if self._connection is None:
            factory = (
                http.client.HTTPSConnection
                if self._https
                else http.client.HTTPConnection
            )
            self._connection = factory(self._host, self._port, timeout=self._timeout)
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> TimedResponse:
        outgoing = {"Accept": "*/*", "Host": self._host, "Connection": "keep-alive"}
        if body is not None:
            outgoing["Content-Type"] = "application/json"
            outgoing["Content-Length"] = str(len(body))
        outgoing.update(headers or {})

        for attempt in (1, 2):
            connection = self._connect()
            started = time.perf_counter()
            try:
                connection.request(method, path, body=body, headers=outgoing)
                response = connection.getresponse()
                ttfb = (time.perf_counter() - started) * 1000.0
                payload = response.read()
                total = (time.perf_counter() - started) * 1000.0
            except (http.client.HTTPException, OSError):
                # A dropped keep-alive connection is not a result; reconnect
                # once and let the second failure surface.
                self.close()
                if attempt == 2:
                    raise
                continue
            if response.will_close:
                self.close()
            return TimedResponse(
                status=response.status,
                ttfb_ms=ttfb,
                total_ms=total,
                headers={key.lower(): value for key, value in response.getheaders()},
                body=payload,
            )
        raise RuntimeError("unreachable")  # pragma: no cover - loop returns or raises


# ---------------------------------------------------------------------------
# Chromium over CDP
# ---------------------------------------------------------------------------


def find_chromium(explicit: str | None) -> Path | None:
    """The first usable Chrome/Chromium: flag, ``$CHROME``, PATH, Playwright."""

    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("CHROME"):
        candidates.append(os.environ["CHROME"])
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    # The Playwright cache is where a browser exists on a box that never
    # installed one system-wide; it is a plain Chrome build and speaks CDP.
    home = Path.home()
    for pattern in (
        "~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome",
        "~/.cache/ms-playwright/chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
    ):
        candidates.extend(sorted(glob.glob(str(home / pattern[2:] if pattern.startswith("~/") else pattern))))

    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


class Cdp:
    """A minimal CDP client: one browser websocket, one attached page target.

    ``websockets`` is optional on purpose — the HTTP half of this probe runs
    anywhere, and the browser half says so plainly when the dependency is not
    installed instead of silently reporting a number it never measured.
    """

    def __init__(self, websocket_url: str, timeout_s: float = 30.0) -> None:
        try:
            from websockets.sync.client import connect
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "the browser leg needs the `websockets` package "
                "(pip install websockets); the HTTP budgets still run with --no-browser"
            ) from exc
        # The sync client is a context manager in this version; entering it by
        # hand keeps one connection alive for the whole measurement instead of
        # one per command.
        self._context = connect(
            websocket_url, open_timeout=timeout_s, max_size=64 * 1024 * 1024
        )
        self._ws = self._context.__enter__()
        self._next_id = 0
        self._events: list[dict[str, Any]] = []

    def close(self) -> None:
        try:
            self._context.__exit__(None, None, None)
        except Exception:  # noqa: BLE001 - closing a dead socket is not an error
            pass

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        self._next_id += 1
        message: dict[str, Any] = {"id": self._next_id, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        self._ws.send(json.dumps(message))
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            raw = self._ws.recv(timeout=max(0.1, deadline - time.monotonic()))
            payload = json.loads(raw)
            if payload.get("id") == message["id"]:
                if "error" in payload:
                    raise RuntimeError(f"CDP {method} failed: {payload['error']}")
                return payload.get("result", {})
            if "method" in payload:
                self._events.append(payload)
        raise TimeoutError(f"CDP {method} did not answer within 60 s")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_devtools(port: int, process: subprocess.Popen[bytes], timeout_s: float = 25.0) -> str:
    """Poll ``/json/version`` until Chrome publishes its browser websocket."""

    url = f"http://127.0.0.1:{port}/json/version"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Chromium exited early with code {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:  # noqa: S310 - loopback
                document = json.loads(response.read().decode("utf-8"))
            endpoint = document.get("webSocketDebuggerUrl")
            if endpoint:
                return str(endpoint)
        except (OSError, ValueError):
            pass
        time.sleep(0.15)
    raise TimeoutError("Chromium did not publish a DevTools endpoint within 25 s")


#: Read after the document has finished loading. Navigation Timing is the
#: clock the browser itself measured; the Paint entries are what the compositor
#: actually put on screen, which is the only honest "first paint".
_NAVIGATION_EXPRESSION = """
(() => {
  const nav = performance.getEntriesByType('navigation')[0];
  if (!nav) return null;
  const paints = Object.fromEntries(
    performance.getEntriesByType('paint').map((entry) => [entry.name, entry.startTime])
  );
  const resources = performance.getEntriesByType('resource');
  return {
    pathname: location.pathname,
    title: document.title,
    readyState: document.readyState,
    responseStart: nav.responseStart,
    responseEnd: nav.responseEnd,
    domContentLoadedEventEnd: nav.domContentLoadedEventEnd,
    loadEventEnd: nav.loadEventEnd,
    duration: nav.duration,
    firstContentfulPaint: paints['first-contentful-paint'] ?? null,
    firstPaint: paints['first-paint'] ?? null,
    resources: resources.length,
    transferSize: resources.reduce((total, entry) => total + (entry.transferSize || 0),
                                   nav.transferSize || 0)
  };
})()
"""


@dataclass
class PaintSample:
    """One cold navigation, in the browser's own units (milliseconds)."""

    url: str
    first_contentful_paint: float
    first_paint: float | None
    dom_content_loaded: float
    load: float
    ttfb: float
    resources: int
    transfer_bytes: int


def measure_paint(
    browser: Path,
    base: str,
    path: str,
    *,
    samples: int = PAINT_SAMPLES,
    user_data_dir: Path,
    settle_s: float = 1.5,
    extra_args: Iterable[str] = (),
) -> list[PaintSample]:
    """Navigate ``path`` ``samples`` times in a cold browser and read the clocks.

    One browser process, one empty profile, a disabled cache and a unique URL
    per navigation: the document, the bundle and the stylesheet are fetched
    every time, so the number is a cold-cache first paint rather than a warm
    one wearing a cold label.

    ``settle_s`` is the pause between navigations, and it is not cosmetic. The
    shell fetches ``/market/breadth`` on mount, and that route does a
    universe-sized scan: on a single-worker uvicorn the work lands on the same
    event loop as the *next* navigation's document, which then reports a TTFB
    inflated by a request it did not make. Pausing lets the previous page's
    work finish so each sample measures one arrival, which is what the budget
    describes.
    """

    port = _free_port()
    command = [
        str(browser),
        "--headless=new",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-extensions",
        "--disable-sync",
        "--disable-dev-shm-usage",
        "--hide-scrollbars",
        "--mute-audio",
        "--no-sandbox",
        "--window-size=1440,900",
        *extra_args,
        "about:blank",
    ]
    process = subprocess.Popen(  # noqa: S603 - a browser we located, with our own flags
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    cdp: Cdp | None = None
    try:
        cdp = Cdp(_wait_for_devtools(port, process))
        target = cdp.call("Target.createTarget", {"url": "about:blank"})
        session = cdp.call(
            "Target.attachToTarget", {"targetId": target["targetId"], "flatten": True}
        )["sessionId"]
        cdp.call("Page.enable", session_id=session)
        cdp.call("Network.enable", session_id=session)
        cdp.call("Network.setCacheDisabled", {"cacheDisabled": True}, session_id=session)
        try:  # not every build exposes it; a missing bypass is not a failure
            cdp.call("Network.setBypassServiceWorker", {"bypass": True}, session_id=session)
        except RuntimeError:
            pass

        results: list[PaintSample] = []
        expected_path = urllib.parse.urlsplit(f"{base}{path}").path or "/"
        for index in range(samples):
            nonce = f"{int(time.time() * 1000):x}{random.randrange(16**6):06x}"
            separator = "&" if "?" in path else "?"
            url = f"{base}{path}{separator}cache_bust={nonce}"
            cdp.call("Page.navigate", {"url": url}, session_id=session)

            # Wait for *this* document, not for whichever one happened to be
            # complete. about:blank is a finished navigation too, and reading
            # its clocks is how a "cold paint" ends up describing a blank page.
            # The pathname is the identity (the shell rewrites its own query
            # string with history.replaceState as soon as the filters mount).
            #
            # ``readyState === complete`` is not enough for the paint entry:
            # under ``--headless=new`` the Paint Timing entry is recorded when
            # the first frame is produced, which can trail the load event by a
            # frame or two. The loop therefore keeps asking for the paint clock
            # until the browser has one, rather than calling a page that has
            # simply not been composited yet "unpainted".
            deadline = time.monotonic() + 30.0
            paint_deadline = time.monotonic() + 35.0
            metrics: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                evaluated = cdp.call(
                    "Runtime.evaluate",
                    {"expression": _NAVIGATION_EXPRESSION, "returnByValue": True},
                    session_id=session,
                )
                value = evaluated.get("result", {}).get("value")
                loaded = (
                    isinstance(value, dict)
                    and value.get("pathname") == expected_path
                    and value.get("readyState") == "complete"
                )
                if loaded and value.get("firstContentfulPaint") is not None:
                    metrics = value
                    break
                if loaded:
                    deadline = max(deadline, paint_deadline)
                time.sleep(0.02)
            if metrics is None:
                raise TimeoutError(f"{url} did not reach readyState=complete within 30 s")

            fcp = metrics.get("firstContentfulPaint")
            if fcp is None:
                # A page that paints nothing has no Paint entry. The budget is
                # about paint, so say so rather than borrowing another clock.
                raise RuntimeError(
                    f"{url} produced no first-contentful-paint entry; "
                    "the shell did not paint (check the bundle's asset URLs)"
                )
            results.append(
                PaintSample(
                    url=url,
                    first_contentful_paint=float(fcp),
                    first_paint=(
                        float(metrics["firstPaint"])
                        if metrics.get("firstPaint") is not None
                        else None
                    ),
                    dom_content_loaded=float(metrics["domContentLoadedEventEnd"]),
                    load=float(metrics["loadEventEnd"]),
                    ttfb=float(metrics["responseStart"]),
                    resources=int(metrics.get("resources") or 0),
                    transfer_bytes=int(metrics.get("transferSize") or 0),
                )
            )
            if index + 1 < samples:
                time.sleep(settle_s)
        return results
    finally:
        if cdp is not None:
            cdp.close()
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - wedged browser
            process.kill()
            process.wait(timeout=5)


# ---------------------------------------------------------------------------
# the three budgets
# ---------------------------------------------------------------------------


def measure_http_document(client: Client, path: str, samples: int) -> tuple[Check, Check]:
    ttfb = Check(name=f"ttfb {path}", budget_ms=None)
    total = Check(name=f"total {path}", budget_ms=None)
    for _ in range(samples):
        response = client.request("GET", path)
        if response.status != 200:
            raise RuntimeError(
                f"GET {path} answered {response.status}: {response.body[:120]!r}"
            )
        ttfb.samples.append(response.ttfb_ms)
        total.samples.append(response.total_ms)
    ttfb.note = "time to response headers"
    total.note = "time to last byte of the document"
    return ttfb, total


def measure_filter_change(client: Client, path: str, samples: int) -> tuple[Check, dict[str, int]]:
    """``POST /screens/select`` with a different filter every time."""

    check = Check(name="filter change", budget_ms=FILTER_P50_BUDGET_MS)
    cache: dict[str, int] = {}
    for index in range(samples):
        body = FILTER_BODIES[index % len(FILTER_BODIES)]
        response = client.request("POST", path, body=json.dumps(body).encode("utf-8"))
        if response.status != 200:
            raise RuntimeError(
                f"POST {path} answered {response.status} for {body['criteria']['filters']}: "
                f"{response.body[:300]!r}"
            )
        check.samples.append(response.total_ms)
        marker = response.headers.get("x-scan-cache", "unknown")
        cache[marker] = cache.get(marker, 0) + 1
    hits = cache.get("hit", 0)
    check.note = f"{hits}/{samples} answered from the scan cache ({cache})"
    return check, cache


def measure_etag(client: Client, path: str, *, method: str = "GET", body: bytes | None = None) -> tuple[Check, Check]:
    """A first request (a miss) and its revalidations (the hits), kept apart.

    They are separate checks because they are separate budgets: the first
    request is a ``200`` that has to render, and the revalidation is a ``304``
    that must not. Averaging them would hide a slow hit behind a miss.
    """

    miss = Check(name=f"etag miss {method} {path}", budget_ms=None, gated=False)
    hit = Check(name=f"etag hit {method} {path}", budget_ms=ETAG_HIT_BUDGET_MS)

    first = client.request(method, path, body=body)
    if first.status != 200:
        raise RuntimeError(f"{method} {path} answered {first.status}: {first.body[:200]!r}")
    etag = first.headers.get("etag")
    if not etag:
        raise RuntimeError(f"{method} {path} answered without an ETag; nothing to revalidate")
    miss.samples.append(first.total_ms)
    miss.note = f"first answer is a 200 ({len(first.body)} B) — never averaged with the hits"

    for _ in range(ETAG_SAMPLES):
        response = client.request(method, path, body=body, headers={"If-None-Match": etag})
        if response.status != 304:
            raise RuntimeError(
                f"{method} {path} revalidation answered {response.status}, expected 304 "
                f"(etag {etag!r})"
            )
        hit.samples.append(response.total_ms)
    hit.note = f"304, {len(first.body)} B not re-sent"
    return miss, hit


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def _row(check: Check) -> str:
    budget = f"{check.budget_ms:.0f} ms" if check.budget_ms is not None else "—"
    values = (
        f"{min(check.samples):9.1f} {check.p50:9.1f} {check.p90:9.1f} {max(check.samples):9.1f}"
        if check.samples
        else f"{'—':>9} {'—':>9} {'—':>9} {'—':>9}"
    )
    return f"{check.name:<32} {check.n:>4} {values}  {budget:>9}  {check.verdict}"


def print_table(title: str, checks: Sequence[Check]) -> None:
    print(f"\n{title}")
    print(
        f"{'check':<32} {'n':>4} {'min ms':>9} {'p50 ms':>9} {'p90 ms':>9} {'max ms':>9}"
        f"  {'budget':>9}  verdict"
    )
    print("-" * 104)
    for check in checks:
        print(_row(check))
        if check.note:
            print(f"{'':<32} {'':>4} {check.note}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure the Phase 7 UI performance budgets against a running app.",
    )
    parser.add_argument(
        "--base",
        default=os.environ.get("MEE_PROBE_BASE", "http://127.0.0.1:8099"),
        help="running app to probe (default: %(default)s)",
    )
    parser.add_argument(
        "--path",
        default="/app",
        help="shell document to measure (default: %(default)s)",
    )
    parser.add_argument(
        "--front-door",
        default="/",
        help="entry point measured beside the shell (default: %(default)s)",
    )
    parser.add_argument(
        "--select",
        default="/screens/select",
        help="selection endpoint used for the filter-change budget (default: %(default)s)",
    )
    parser.add_argument("--samples", type=int, default=FILTER_SAMPLES, help="filter-change samples")
    parser.add_argument("--paint-samples", type=int, default=PAINT_SAMPLES, help="cold navigations")
    parser.add_argument(
        "--settle",
        type=float,
        default=1.5,
        help="seconds between cold navigations, so the previous page's own fetches finish",
    )
    parser.add_argument("--browser", default=None, help="Chromium binary (default: discovered)")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="skip first paint (HTTP budgets only; the paint budget is reported as unmeasured)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="also gate the selection route's revalidation on the 150 ms budget",
    )
    parser.add_argument("--json", default=None, help="write the measurements to this path")
    parser.add_argument(
        "--drop-os-caches",
        action="store_true",
        help="drop the kernel page cache first (needs root); fails loudly if it cannot",
    )
    args = parser.parse_args(argv)

    if args.drop_os_caches:
        try:
            subprocess.run(["sync"], check=True)
            Path("/proc/sys/vm/drop_caches").write_text("3\n", encoding="ascii")
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"error: --drop-os-caches needs root: {exc}", file=sys.stderr)
            return 2

    client = Client(args.base)
    started = datetime.now(timezone.utc)
    checks: list[Check] = []
    notes: dict[str, Any] = {
        "base": args.base,
        "started_at": started.isoformat(),
        "cold_measurement": (
            "browser cache cold: fresh profile + Network.setCacheDisabled + unique "
            "?cache_bust= per navigation"
        ),
        "os_page_cache": "dropped" if args.drop_os_caches else "warm (not dropped; no root)",
    }

    try:
        # -- TTFB / total for the shell document ---------------------------
        ttfb, total = measure_http_document(client, args.path, samples=5)
        checks.extend([ttfb, total])

        # -- filter change -------------------------------------------------
        filters, cache_mix = measure_filter_change(client, args.select, samples=args.samples)
        checks.append(filters)
        notes["scan_cache"] = cache_mix

        # -- ETag: the document (gated) ------------------------------------
        document_miss, document_hit = measure_etag(client, args.path)
        checks.extend([document_miss, document_hit])

        # -- ETag: the selection (measured, gated only with --strict) ------
        body = json.dumps(FILTER_BODIES[0]).encode("utf-8")
        try:
            select_miss, select_hit = measure_etag(
                client, args.select, method="POST", body=body
            )
        except RuntimeError as exc:  # a 200-only route: report, do not invent
            print(f"warning: selection revalidation not measured: {exc}", file=sys.stderr)
            notes["selection_revalidation"] = f"not measured: {exc}"
        else:
            select_hit.gated = args.strict
            select_hit.note = (
                "304, but the route rebuilds the answer before comparing the tag "
                "(recompute path — see research/phase7-perf.md)"
            )
            checks.extend([select_miss, select_hit])

        # -- the front door, when it serves the shell ----------------------
        front = client.request("GET", args.front_door)
        front_is_shell = front.status == 200 and "text/html" in front.headers.get(
            "content-type", ""
        )
        notes["front_door"] = {
            "path": args.front_door,
            "status": front.status,
            "location": front.headers.get("location"),
            "serves_the_shell": front_is_shell,
        }
        if front_is_shell and args.front_door != args.path:
            door_ttfb, door_total = measure_http_document(client, args.front_door, samples=5)
            door_ttfb.name = f"ttfb {args.front_door} (front door)"
            door_total.name = f"total {args.front_door} (front door)"
            _, door_hit = measure_etag(client, args.front_door)
            door_hit.name = f"etag hit GET {args.front_door} (front door)"
            checks.extend([door_ttfb, door_total, door_hit])

        # -- cold first paint ---------------------------------------------
        paint: list[PaintSample] = []
        if args.no_browser:
            notes["first_paint"] = "not measured (--no-browser)"
        else:
            browser = find_chromium(args.browser)
            if browser is None:
                print(
                    "error: no Chromium found (--browser, $CHROME, PATH or the Playwright cache)",
                    file=sys.stderr,
                )
                return 2
            notes["browser"] = str(browser)
            # A fresh profile directory is half of what makes the paint cold;
            # it is removed by hand (not by TemporaryDirectory) because Chrome's
            # zygote may still be writing when the parent exits, and a cleanup
            # that races the browser would fail the run after a good measurement.
            profile = Path(tempfile.mkdtemp(prefix="ztockz-perf-"))
            try:
                paint = measure_paint(
                    browser,
                    args.base,
                    args.path,
                    samples=args.paint_samples,
                    user_data_dir=profile / "profile",
                    settle_s=args.settle,
                )
            finally:
                time.sleep(0.3)
                shutil.rmtree(profile, ignore_errors=True)
            checks.insert(
                0,
                Check(
                    name="first paint (FCP)",
                    samples=[sample.first_contentful_paint for sample in paint],
                    budget_ms=FIRST_PAINT_BUDGET_MS,
                    note=f"{len(paint)} cold navigations of {args.path}; browser cache bypassed",
                ),
            )
            checks.insert(
                1,
                Check(
                    name="dom content loaded",
                    samples=[sample.dom_content_loaded for sample in paint],
                    budget_ms=None,
                ),
            )
            checks.insert(
                2,
                Check(name="browser ttfb", samples=[sample.ttfb for sample in paint], budget_ms=None),
            )
            notes["paint_samples"] = [asdict(sample) for sample in paint]
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        # Could not measure: the endpoint is down, answered a status the probe
        # cannot interpret, or the browser never painted. That is exit 2, not a
        # budget miss — a probe that cannot see is not a probe that failed.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()

    print_table(f"Ztockz UI perf probe — {args.base}  ({started.isoformat(timespec='seconds')})", checks)
    print(
        f"\nbudgets from .hermes/plans/2026-10-30_ui-interface.md §9: "
        f"first paint ≤ {FIRST_PAINT_BUDGET_MS:.0f} ms · filter change p50 ≤ "
        f"{FILTER_P50_BUDGET_MS:.0f} ms · ETag hit ≤ {ETAG_HIT_BUDGET_MS:.0f} ms"
    )
    for key, value in notes.items():
        if key in {"paint_samples", "scan_cache"}:
            continue
        print(f"note: {key} = {value}")

    gated = [check for check in checks if check.gated and check.budget_ms is not None]
    missed = [check for check in gated if not check.passed]
    advisory = [
        check for check in checks if not check.gated and check.budget_ms is not None and not check.passed
    ]
    # A passing p50 must not hide a slow visit. Every gated check whose p50 is
    # inside the budget but whose tail is not says so, with the count.
    for check in gated:
        if check.passed and check.samples and check.budget_ms is not None:
            over = [value for value in check.samples if value > check.budget_ms]
            if over:
                print(
                    f"\nnote: {check.name} p50 is inside the budget, but "
                    f"{len(over)}/{check.n} samples exceeded {check.budget_ms:.0f} ms "
                    f"(max {max(over):.0f} ms)"
                )
    if missed:
        print("\nMISSED: " + ", ".join(f"{check.name} {check.p50:.0f} ms" for check in missed))
    if advisory:
        print(
            "advisory (not gated, reported anyway): "
            + ", ".join(f"{check.name} {check.p50:.0f} ms" for check in advisory)
        )
    if not missed:
        print("\nall gated budgets met")

    if args.json:
        payload = {
            "base": args.base,
            "started_at": started.isoformat(),
            "notes": notes,
            "checks": [check.document() for check in checks],
            "missed": [check.name for check in missed],
            "advisory_failures": [check.name for check in advisory],
        }
        Path(args.json).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.json}")

    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
