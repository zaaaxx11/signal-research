#!/usr/bin/env python3
"""Backfill real company logos into the store ``GET /logos/{symbol}`` serves.

The route's ladder is ``store file -> ui/logos -> symbol provider -> domain
provider -> monogram``. This script fills the first rung: one raster file per
symbol, so a request never needs the network and never falls through to a
monogram. It is the batch half of the same ladder and deliberately writes the
same filenames (``{NORMALIZED_SYMBOL}.png``), through the same atomic
temp-file-then-``os.replace`` idiom, so a half-finished run is never read as a
half-written PNG.

Providers, in the order they are tried (all measured, none assumed):

1. ``parqet``  — ``assets.parqet.com/logos/symbol/{SYMBOL}?format=png``, keyless,
   symbol-addressed, 100x100 PNG. Works without a website.
2. ``fmp``     — ``images.financialmodelingprep.com/symbol/{SYMBOL}.png``,
   keyless, symbol-addressed, 100x100 or 250x250. The second symbol rung: it
   covers most of what parqet misses (measured on the pilot).
3. ``google``  — ``www.google.com/s2/favicons?domain={DOMAIN}&sz=128``. Needs the
   website from the depth store; many answers are 32 px upscales, so it is a
   last resort, not a first choice.
4. ``clearbit``— ``logo.clearbit.com/{DOMAIN}``. Implemented, **off by default**:
   ``logo.clearbit.com`` has no A record any more (checked over DNS-over-HTTPS,
   not just this host), so the rung can only fail. Naming it explicitly still
   works if it ever comes back.

Politeness (the run is ~10k symbols, so this matters):

* concurrency 4, per-request timeout 8 s, no retries;
* **resume**: a symbol whose file is already a raster >= ``--min-px`` is skipped
  without a single request, so re-running the same command continues instead of
  repeating;
* a per-provider circuit breaker: after ``--max-failures`` consecutive transport
  failures that provider is dropped for the rest of the run. A 404 is an answer,
  not a failure, and resets the counter — otherwise one popular-but-missing
  ticker would look like an outage.

A "real" hit is a raster image (PNG/JPEG/GIF/WebP), at least ``--min-px`` on both
sides, at most ``--max-bytes``, and not the provider's generic placeholder. Both
symbol providers answer 404 rather than a placeholder for unknown tickers (pinned
below by ``PLACEHOLDER_SHA256`` being empty); dimension and magic bytes are
checked for every answer, so an HTML error page or a 16x16 dot is never counted.

Examples
--------
    # 200 symbols stratified across market caps, into a scratch store
    python scripts/logos-backfill.py --sample 200 --dir /tmp/logo-pilot

    # continue the full catalogue into the live store
    python scripts/logos-backfill.py --dir /home/ubuntu/ztockz-data/logos

    # a named list, one provider, JSON summary
    python scripts/logos-backfill.py --symbols AAPL,BRK.B,0700.HK \
        --providers parqet --report /tmp/logos.json

    # measure what is already on disk (no network)
    python scripts/logos-backfill.py --measure-only --dir data/logos
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: The one spellings this script writes, in the route's own order. A hit is
#: stored under the first extension that already exists, else ``.png``.
EXTENSIONS: tuple[str, ...] = (".png", ".svg", ".jpg", ".jpeg", ".webp")

#: Browsers get a name; a provider that sees a bare ``Python-urllib`` is entitled
#: to rate-limit it harder, and this run is a guest.
USER_AGENT = "ztockz-logo-backfill/1.0 (+https://github.com/; internal dashboard)"

#: Responses above this are not the 100x100 mark that was asked for.
DEFAULT_MAX_BYTES = 262_144
DEFAULT_TIMEOUT_S = 8.0
DEFAULT_MIN_PX = 48
DEFAULT_CONCURRENCY = 4
DEFAULT_MAX_FAILURES = 8

#: Provider endpoints. ``{symbol}``/``{domain}`` are the only interpolations and
#: both are validated before they reach a URL (see ``candidates``).
PARQET_TEMPLATE = "https://assets.parqet.com/logos/symbol/{symbol}?format=png"
FMP_TEMPLATE = "https://images.financialmodelingprep.com/symbol/{symbol}.png"
GOOGLE_TEMPLATE = "https://www.google.com/s2/favicons?domain={domain}&sz=128"
CLEARBIT_TEMPLATE = "https://logo.clearbit.com/{domain}"

#: The providers this script knows. ``--providers`` names a prefix of this set.
PROVIDERS: tuple[str, ...] = ("parqet", "fmp", "google", "clearbit")
#: Clearbit's host is dead (NXDOMAIN globally), so it is opt-in only.
DEFAULT_PROVIDERS: tuple[str, ...] = ("parqet", "fmp", "google")

#: Answers that are the provider's way of saying "no logo". Both symbol
#: providers were probed with invented tickers and answered 404 (no body), so
#: this is empty on purpose — kept as the seam if a provider ever starts
#: answering 200 with a grey square.
PLACEHOLDER_SHA256: frozenset[str] = frozenset()

#: Mirrors ``app.api.logos._SYMBOL_RE``; kept local so this file runs on a bare
#: interpreter, and pinned to the route's function by a test.
_SYMBOL_RE = re.compile(r"([A-Z0-9^][A-Z0-9.\-^]{0,19})(?:/([A-Z]{1,3}))?")
_MAX_SYMBOL = 24

_HOST_RE = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
_IPV4_RE = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata",
        "metadata.google.internal",
        "instance-data",
        "instance-data.ec2.internal",
    }
)
_BLOCKED_SUFFIXES: tuple[str, ...] = (
    ".local",
    ".localhost",
    ".localdomain",
    ".internal",
    ".home.arpa",
    ".in-addr.arpa",
    ".ip6.arpa",
)
_ALLOWED_SCHEMES = frozenset({"http", "https"})


# ---------------------------------------------------------------------------
# The two gates the route owns; mirrored here so the batch half writes exactly
# the filenames the request half looks for.
# ---------------------------------------------------------------------------


def normalize_symbol(symbol: object) -> str:
    """The route's cache key: upper-case, bounded, path-free, ``/`` -> ``-``."""

    raw = "" if symbol is None else str(symbol)
    if not raw or len(raw) > _MAX_SYMBOL or any(char.isspace() for char in raw):
        raise ValueError(f"{symbol!r} is not a ticker")
    key = raw.upper()
    matched = _SYMBOL_RE.fullmatch(key)
    if matched is None:
        raise ValueError(f"{symbol!r} is not a ticker")
    base, share_class = matched.groups()
    return f"{base}-{share_class}" if share_class else base


def safe_domain(website: object) -> str | None:
    """The bare public hostname of ``website``, or ``None``."""

    if not isinstance(website, str):
        return None
    raw = website.strip()
    if not raw or len(raw) > 300:
        return None
    rest = raw
    if "://" in raw:
        scheme, _, rest = raw.partition("://")
        if scheme.strip().lower() not in _ALLOWED_SCHEMES:
            return None
    elif raw.startswith(("//", "mailto:", "javascript:", "data:", "file:")):
        return None
    authority = re.split(r"[/?#]", rest, maxsplit=1)[0].strip()
    if not authority or "@" in authority or ":" in authority:
        return None
    host = authority.lower().rstrip(".")
    if not host or len(host) > 253 or host.startswith("["):
        return None
    if host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_SUFFIXES):
        return None
    if _IPV4_RE.fullmatch(host):
        return None
    if not _HOST_RE.fullmatch(host):
        return None
    if not host.rsplit(".", 1)[1].isalpha():
        return None
    return host


# ---------------------------------------------------------------------------
# Image validation: magic bytes and real pixel dimensions, never the extension.
# ---------------------------------------------------------------------------


def png_size(body: bytes) -> tuple[int, int] | None:
    if len(body) >= 24 and body[:8] == b"\x89PNG\r\n\x1a\n" and body[12:16] == b"IHDR":
        return struct.unpack(">II", body[16:24])
    return None


def gif_size(body: bytes) -> tuple[int, int] | None:
    if len(body) >= 10 and body[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<HH", body[6:10])
    return None


def jpeg_size(body: bytes) -> tuple[int, int] | None:
    """Walk the segment chain to the first start-of-frame, per ITU T.81."""

    if len(body) < 4 or body[:3] != b"\xff\xd8\xff":
        return None
    index = 2
    length = len(body)
    while index + 9 < length:
        if body[index] != 0xFF:
            index += 1
            continue
        marker = body[index + 1]
        index += 2
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > length:
            return None
        segment = struct.unpack(">H", body[index : index + 2])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if index + 7 > length:
                return None
            height, width = struct.unpack(">HH", body[index + 3 : index + 7])
            return (width, height)
        if segment < 2:
            return None
        index += segment
    return None


def webp_size(body: bytes) -> tuple[int, int] | None:
    if len(body) < 30 or body[:4] != b"RIFF" or body[8:12] != b"WEBP":
        return None
    chunk = body[12:16]
    if chunk == b"VP8X":
        width = 1 + int.from_bytes(body[24:27], "little")
        height = 1 + int.from_bytes(body[27:30], "little")
        return (width, height)
    if chunk == b"VP8 " and body[23:26] == b"\x9d\x01\x2a":
        width = int.from_bytes(body[26:28], "little") & 0x3FFF
        height = int.from_bytes(body[28:30], "little") & 0x3FFF
        return (width, height)
    if chunk == b"VP8L" and len(body) >= 25 and body[20] == 0x2F:
        bits = int.from_bytes(body[21:25], "little")
        return (1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF))
    return None


def image_size(body: bytes) -> tuple[str, int, int] | None:
    """``(format, width, height)`` for a raster body, or ``None``.

    SVG is deliberately not a raster: the route can serve it, but the coverage
    metric this script reports is "a real company logo", and a generated vector
    monogram would be counted as a logo when it is the thing being replaced.
    """

    for name, parser in (
        ("png", png_size),
        ("jpeg", jpeg_size),
        ("gif", gif_size),
        ("webp", webp_size),
    ):
        size = parser(body)
        if size is not None:
            return (name, size[0], size[1])
    return None


# ---------------------------------------------------------------------------
# One fetch, one answer. HTTP status is data; only transport trouble is an error.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Fetch:
    status: int = 0
    content_type: str = ""
    body: bytes = b""
    error: str = ""
    transport_error: bool = False


def http_get(url: str, timeout_s: float, max_bytes: int) -> Fetch:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/png,image/jpeg,image/webp,image/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = response.read(max_bytes + 1)
            return Fetch(
                status=int(getattr(response, "status", 200)),
                content_type=str(response.headers.get("content-type", "")),
                body=body,
            )
    except urllib.error.HTTPError as exc:
        # 404/410 are "no logo here", not a sick provider.
        return Fetch(status=int(exc.code), error=f"HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001 - DNS, TLS, timeout, reset
        return Fetch(status=0, error=f"{type(exc).__name__}: {exc}"[:160], transport_error=True)


# ---------------------------------------------------------------------------
# Candidates: provider -> ordered URLs for one symbol.
# ---------------------------------------------------------------------------


def symbol_variants(symbol: str) -> tuple[str, ...]:
    """The spellings providers use for one issuer, best first.

    ``BRK.B`` is ``BRK-B`` at both symbol providers; ``BRK-B`` is spelled with a
    dot by some feeds. One extra request per variant at most, and the canonical
    spelling is always first.
    """

    variants = [symbol]
    if "." in symbol:
        variants.append(symbol.replace(".", "-"))
    if "-" in symbol:
        base, _, suffix = symbol.partition("-")
        if base and 1 <= len(suffix) <= 3 and suffix.isalpha():
            variants.append(f"{base}.{suffix}")
    return tuple(dict.fromkeys(variants))


def candidates(
    symbol: str, domain: str | None, providers: Sequence[str]
) -> Iterator[tuple[str, list[str]]]:
    """``(provider, urls)`` pairs for one symbol, in ladder order."""

    for provider in providers:
        if provider == "parqet":
            yield provider, [
                PARQET_TEMPLATE.format(symbol=urllib.parse.quote(variant, safe=""))
                for variant in symbol_variants(symbol)
            ]
        elif provider == "fmp":
            yield provider, [
                FMP_TEMPLATE.format(symbol=urllib.parse.quote(variant, safe=""))
                for variant in symbol_variants(symbol)
            ]
        elif provider == "google" and domain:
            yield provider, [GOOGLE_TEMPLATE.format(domain=urllib.parse.quote(domain, safe=""))]
        elif provider == "clearbit" and domain:
            yield provider, [CLEARBIT_TEMPLATE.format(domain=urllib.parse.quote(domain, safe=""))]
        else:
            # A domain provider without a domain has nothing to ask. It is not a
            # miss for that provider: it was never tried.
            continue


# ---------------------------------------------------------------------------
# The store on disk.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Existing:
    path: Path | None = None
    width: int = 0
    height: int = 0
    size: int = 0

    @property
    def real(self) -> bool:
        return self.path is not None


def existing_file(directory: Path, symbol: str, min_px: int) -> Existing:
    """The symbol's current file, and whether it already clears the bar."""

    for extension in EXTENSIONS:
        path = directory / f"{symbol}{extension}"
        if not path.is_file():
            continue
        try:
            body = path.read_bytes()
        except OSError:
            continue
        info = image_size(body)
        if info is not None and min(info[1], info[2]) >= min_px:
            return Existing(path=path, width=info[1], height=info[2], size=len(body))
        return Existing(path=None)
    return Existing(path=None)


def atomic_write(path: Path, body: bytes) -> None:
    """Temp file in the same directory, then ``os.replace`` — no half PNGs."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{hashlib.sha256(body[:64]).hexdigest()[:8]}.tmp"
    try:
        with open(temporary, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# The universe.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Target:
    symbol: str
    market_cap: float | None = None
    website: str | None = None


def _read_json(path: Path) -> Any:
    with open(path, "rb") as handle:
        return json.load(handle)


def _first_existing(candidates: Iterable[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def universe_from_json(path: Path) -> list[Target]:
    """A JSON universe: a list of rows/strings, or ``{"rows"|"symbols": ...}``."""

    data = _read_json(path)
    rows: Any
    if isinstance(data, Mapping):
        rows = data.get("rows") or data.get("symbols") or data.get("universe") or []
        if isinstance(rows, Mapping):
            rows = [{"symbol": key, **(value if isinstance(value, Mapping) else {})} for key, value in rows.items()]
    else:
        rows = data
    targets: list[Target] = []
    for row in rows or []:
        if isinstance(row, str):
            targets.append(Target(symbol=row))
        elif isinstance(row, Mapping):
            raw = row.get("symbol") or row.get("ticker") or row.get("Ticker")
            if not raw:
                continue
            targets.append(
                Target(
                    symbol=str(raw),
                    market_cap=_number(row.get("market_cap") or row.get("market_cap_basic") or row.get("market_cap_usd")),
                    website=row.get("website") if isinstance(row.get("website"), str) else None,
                )
            )
    return targets


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def universe_from_stores(depth_path: Path | None, snapshot_path: Path | None) -> list[Target]:
    """The catalogue the screen can show: depth rows plus the US listings it lacks.

    This mirrors the screen's own population rules (PLAN-screen §2): common
    equity, no OTC/pink sheet, and the depth store is the global catalogue. The
    engine snapshot contributes **US** listings only — depth already carries the
    foreign names the global tab draws, so adding every venue the snapshot knows
    would backfill ~2.5k symbols the grid never asks for and spend the store
    budget on them. A depth row carries the market cap used for stratification;
    a US security the depth collector has not reached yet still gets a
    symbol-only attempt, which is exactly the hole this script exists to remove.
    """

    merged: dict[str, Target] = {}
    if depth_path is not None and depth_path.is_file():
        data = _read_json(depth_path)
        rows = data.get("symbols") if isinstance(data, Mapping) else None
        if isinstance(rows, Mapping):
            for raw, row in rows.items():
                if not isinstance(row, Mapping):
                    continue
                if str(row.get("exchange") or "").upper() in _OTC_EXCHANGES:
                    continue
                merged[str(raw)] = Target(
                    symbol=str(raw),
                    market_cap=_number(row.get("market_cap")),
                    website=row.get("website") if isinstance(row.get("website"), str) else None,
                )
    if snapshot_path is not None and snapshot_path.is_file():
        data = _read_json(snapshot_path)
        dataset = data.get("dataset") if isinstance(data, Mapping) else None
        securities = dataset.get("securities") if isinstance(dataset, Mapping) else None
        for row in securities or []:
            if not isinstance(row, Mapping) or not row.get("active", True):
                continue
            if str(row.get("asset_class") or "equity") != "equity":
                continue
            if str(row.get("country") or "") != "United States":
                continue
            if str(row.get("exchange") or "").upper() in _OTC_EXCHANGES:
                continue
            raw = row.get("symbol")
            if not raw:
                continue
            merged.setdefault(str(raw), Target(symbol=str(raw)))
    return list(merged.values())


#: Venues the screen excludes whatever the cap (``app.screen.universe``).
_OTC_EXCHANGES = frozenset({"OTC", "PNK", "OTCQB", "OTCQX", "PINK", "GREY"})


def stratified_sample(targets: Sequence[Target], count: int, seed: int) -> list[Target]:
    """``count`` symbols spread across market-cap deciles.

    A sample drawn from the whole population would be dominated by nano-caps
    (most tickers are small) and would measure a different question than "does
    the grid's top row have a logo". Ten buckets of equal size, equal draws from
    each; rows with no cap form an eleventh bucket so they are represented
    rather than silently dropped.
    """

    capped = sorted(
        (target for target in targets if target.market_cap is not None and target.market_cap > 0),
        key=lambda target: target.market_cap or 0.0,
    )
    uncapped = [target for target in targets if target.market_cap is None]
    if count >= len(targets):
        return list(targets)
    generator = random.Random(seed)
    buckets: list[list[Target]] = []
    if capped:
        size = len(capped)
        for index in range(10):
            low = index * size // 10
            high = (index + 1) * size // 10
            if high > low:
                buckets.append(capped[low:high])
    if uncapped:
        buckets.append(uncapped)
    per_bucket = max(1, count // max(1, len(buckets)))
    picked: list[Target] = []
    for bucket in buckets:
        take = min(per_bucket, len(bucket))
        picked.extend(generator.sample(bucket, take))
    # Fill any remainder from what is left, without repeating a symbol.
    chosen = {target.symbol for target in picked}
    remainder = [target for target in targets if target.symbol not in chosen]
    generator.shuffle(remainder)
    picked.extend(remainder[: max(0, count - len(picked))])
    return picked[:count]


# ---------------------------------------------------------------------------
# The run.
# ---------------------------------------------------------------------------


@dataclass
class ProviderStats:
    tried: int = 0
    real: int = 0
    small: int = 0
    not_image: int = 0
    missing: int = 0
    error: int = 0
    oversized: int = 0
    circuit: int = 0


@dataclass
class Circuit:
    """Per-provider consecutive-transport-failure breaker, shared by threads."""

    limit: int
    failures: int = 0
    open: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, transport_error: bool) -> None:
        with self.lock:
            if transport_error:
                self.failures += 1
                if self.failures >= self.limit and not self.open:
                    self.open = True
            else:
                self.failures = 0

    def is_open(self) -> bool:
        with self.lock:
            return self.open


class Backfill:
    def __init__(
        self,
        *,
        directory: Path,
        providers: Sequence[str],
        timeout_s: float,
        max_bytes: int,
        min_px: int,
        max_failures: int,
        dry_run: bool = False,
    ) -> None:
        self.directory = directory
        self.providers = tuple(providers)
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.min_px = min_px
        self.dry_run = dry_run
        self.stats = {name: ProviderStats() for name in self.providers}
        self.circuits = {name: Circuit(max_failures) for name in self.providers}
        self.lock = threading.Lock()
        self.outcomes: dict[str, dict[str, Any]] = {}
        self.skipped = 0
        self.failed_symbols: list[str] = []

    def run_one(self, target: Target) -> dict[str, Any]:
        symbol = target.symbol
        current = existing_file(self.directory, symbol, self.min_px)
        if current.real:
            with self.lock:
                self.skipped += 1
                self.outcomes[symbol] = {
                    "provider": "cache",
                    "path": current.path.name if current.path else "",
                    "bytes": current.size,
                    "width": current.width,
                    "height": current.height,
                }
            return self.outcomes[symbol]

        domain = safe_domain(target.website) if target.website else None
        tried: list[str] = []
        for provider, urls in candidates(symbol, domain, self.providers):
            circuit = self.circuits[provider]
            if circuit.is_open():
                with self.lock:
                    self.stats[provider].circuit += 1
                continue
            with self.lock:
                self.stats[provider].tried += 1
            tried.append(provider)
            for url in urls:
                answer = http_get(url, self.timeout_s, self.max_bytes)
                circuit.record(answer.transport_error)
                outcome, detail = self._classify(answer, symbol, provider, url)
                if outcome == "real":
                    return detail
                # A 404 on the canonical spelling is a miss for that spelling; a
                # second spelling is still worth one request (BRK.B vs BRK-B).
        with self.lock:
            self.failed_symbols.append(symbol)
            self.outcomes[symbol] = {"provider": None, "tried": tried}
        return self.outcomes[symbol]

    def _classify(
        self, answer: Fetch, symbol: str, provider: str, url: str
    ) -> tuple[str, dict[str, Any]]:
        stats = self.stats[provider]
        if answer.transport_error:
            with self.lock:
                stats.error += 1
            return "error", {"provider": None, "error": answer.error}
        if answer.status != 200 or not answer.body:
            with self.lock:
                if answer.status in (404, 410, 403, 401, 400):
                    stats.missing += 1
                else:
                    stats.error += 1
            return "miss", {"provider": None, "status": answer.status}
        if len(answer.body) > self.max_bytes:
            with self.lock:
                stats.oversized += 1
            return "miss", {"provider": None, "oversized": True}
        digest = hashlib.sha256(answer.body).hexdigest()
        if digest in PLACEHOLDER_SHA256:
            with self.lock:
                stats.not_image += 1
            return "miss", {"provider": None, "placeholder": True}
        info = image_size(answer.body)
        if info is None:
            with self.lock:
                stats.not_image += 1
            return "miss", {"provider": None, "not_image": True}
        _format, width, height = info
        if min(width, height) < self.min_px:
            with self.lock:
                stats.small += 1
            return "miss", {"provider": None, "small": (width, height)}
        if not self.dry_run:
            path = self.directory / f"{symbol}.png"
            try:
                atomic_write(path, answer.body)
            except OSError as exc:
                with self.lock:
                    stats.error += 1
                return "error", {"provider": None, "write_error": str(exc)}
        with self.lock:
            stats.real += 1
            self.outcomes[symbol] = {
                "provider": provider,
                "path": f"{symbol}.png",
                "bytes": len(answer.body),
                "width": width,
                "height": height,
                "content_type": answer.content_type,
                "url": url,
            }
            return "real", self.outcomes[symbol]


def print_table(backfill: Backfill) -> None:
    header = f"{'provider':<10} {'tried':>6} {'real':>6} {'small':>6} {'notimg':>7} {'404':>6} {'error':>6} {'big':>5} {'breaker':>8}"
    print(header)
    print("-" * len(header))
    for name in backfill.providers:
        stats = backfill.stats[name]
        print(
            f"{name:<10} {stats.tried:>6} {stats.real:>6} {stats.small:>6} "
            f"{stats.not_image:>7} {stats.missing:>6} {stats.error:>6} "
            f"{stats.oversized:>5} {stats.circuit:>8}"
        )


def measure(directory: Path, targets: Sequence[Target], min_px: int) -> dict[str, Any]:
    real = 0
    missing: list[str] = []
    for target in targets:
        if existing_file(directory, target.symbol, min_px).real:
            real += 1
        else:
            missing.append(target.symbol)
    return {
        "directory": str(directory),
        "universe": len(targets),
        "real": real,
        "missing": len(targets) - real,
        "coverage_pct": round(100.0 * real / len(targets), 2) if targets else 0.0,
        "missing_sample": missing[:50],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=0, help="only the first N targets (0 = all)")
    parser.add_argument("--symbols", default="", help="comma-separated tickers, bypassing the universe")
    parser.add_argument("--sample", type=int, default=0, help="stratified sample of N targets across market caps")
    parser.add_argument("--seed", type=int, default=42, help="sampling seed (default 42)")
    parser.add_argument("--report", type=Path, default=None, help="write a JSON summary here")
    parser.add_argument(
        "--providers",
        default=",".join(DEFAULT_PROVIDERS),
        help=f"comma-separated ladder, from {','.join(PROVIDERS)} (default {','.join(DEFAULT_PROVIDERS)})",
    )
    parser.add_argument("--dir", type=Path, default=None, help="logo store (default: the route's store)")
    parser.add_argument("--universe", type=Path, default=None, help="JSON universe instead of the engine stores")
    parser.add_argument("--depth", type=Path, default=None, help="depth store path")
    parser.add_argument("--snapshot", type=Path, default=None, help="engine snapshot path")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--min-px", type=int, default=DEFAULT_MIN_PX)
    parser.add_argument("--max-failures", type=int, default=DEFAULT_MAX_FAILURES)
    parser.add_argument("--measure-only", action="store_true", help="count real files, no network")
    parser.add_argument("--dry-run", action="store_true", help="fetch and classify, write nothing")
    return parser


def resolve_store(args: argparse.Namespace) -> Path:
    if args.dir is not None:
        return args.dir.expanduser()
    try:
        from app.api.logos import resolve_logo_dir  # the route's own answer
        from app.config import Settings

        return resolve_logo_dir(Settings())
    except Exception:  # noqa: BLE001 - standalone use on a bare interpreter
        return ROOT / "data" / "logos"


def resolve_sources(args: argparse.Namespace) -> tuple[Path | None, Path | None]:
    depth = args.depth or (
        Path(os.environ["MEE_DEPTH_STORE_PATH"]) if os.environ.get("MEE_DEPTH_STORE_PATH") else None
    )
    snapshot = args.snapshot or (
        Path(os.environ["MEE_ENGINE_SNAPSHOT"]) if os.environ.get("MEE_ENGINE_SNAPSHOT") else None
    )
    if depth is None:
        depth = _first_existing([ROOT / "data" / "depth.json", ROOT.parent / "ztockz-data" / "depth.json"])
    if snapshot is None:
        snapshot = _first_existing(
            [ROOT / "data" / "engine-snapshot.json", ROOT.parent / "ztockz-data" / "engine-snapshot.json"]
        )
    return depth, snapshot


def load_targets(args: argparse.Namespace) -> tuple[list[Target], str]:
    if args.symbols.strip():
        targets = []
        for raw in args.symbols.split(","):
            raw = raw.strip()
            if raw:
                targets.append(Target(symbol=raw))
        return targets, "explicit --symbols"
    if args.universe is not None:
        return universe_from_json(args.universe), f"universe {args.universe}"
    depth, snapshot = resolve_sources(args)
    return universe_from_stores(depth, snapshot), f"depth={depth or '-'} snapshot={snapshot or '-'}"


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    providers = tuple(part.strip() for part in args.providers.split(",") if part.strip())
    unknown = [name for name in providers if name not in PROVIDERS]
    if unknown:
        print(f"unknown provider(s): {', '.join(unknown)}; choose from {', '.join(PROVIDERS)}", file=sys.stderr)
        return 2
    if not providers:
        print("no providers selected", file=sys.stderr)
        return 2
    if args.concurrency < 1:
        print("--concurrency must be >= 1", file=sys.stderr)
        return 2

    directory = resolve_store(args)
    targets, source = load_targets(args)

    # Normalise and dedupe on the route's own key. An invalid ticker is dropped
    # here rather than requested from a provider.
    clean: dict[str, Target] = {}
    for target in targets:
        try:
            key = normalize_symbol(target.symbol)
        except ValueError:
            continue
        if key not in clean:
            clean[key] = Target(symbol=key, market_cap=target.market_cap, website=target.website)
    targets = list(clean.values())

    if args.sample:
        targets = stratified_sample(targets, args.sample, args.seed)
    if args.limit:
        targets = targets[: args.limit]

    print(f"store:     {directory}")
    print(f"universe:  {len(targets)} symbols ({source})")
    print(f"providers: {','.join(providers)}   concurrency: {args.concurrency}   timeout: {args.timeout}s")

    if args.measure_only:
        summary = measure(directory, targets, args.min_px)
        print(json.dumps(summary, indent=2))
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return 0

    backfill = Backfill(
        directory=directory,
        providers=providers,
        timeout_s=args.timeout,
        max_bytes=args.max_bytes,
        min_px=args.min_px,
        max_failures=args.max_failures,
        dry_run=args.dry_run,
    )

    started = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(backfill.run_one, target): target for target in targets}
        for future in futures:
            future.result()
            done += 1
            if done % 100 == 0 or done == len(targets):
                elapsed = time.time() - started
                rate = done / elapsed if elapsed > 0 else 0.0
                print(
                    f"  {done}/{len(targets)}  {rate:.1f}/s  real={sum(stats.real for stats in backfill.stats.values())}",
                    file=sys.stderr,
                    flush=True,
                )

    elapsed = time.time() - started
    real = sum(1 for outcome in backfill.outcomes.values() if outcome.get("provider"))
    coverage = 100.0 * real / len(targets) if targets else 0.0
    print()
    print_table(backfill)
    print()
    print(f"symbols:   {len(targets)}")
    print(f"cached:    {backfill.skipped} already real (skipped, no request)")
    print(f"covered:   {real}  ({coverage:.2f}%)")
    print(f"unresolved:{len(backfill.failed_symbols)}")
    print(f"elapsed:   {elapsed:.1f}s  ({len(targets) / elapsed:.1f} symbols/s)" if elapsed else "")

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "directory": str(directory),
        "universe": len(targets),
        "providers": {
            name: {
                "tried": backfill.stats[name].tried,
                "real": backfill.stats[name].real,
                "small": backfill.stats[name].small,
                "not_image": backfill.stats[name].not_image,
                "missing": backfill.stats[name].missing,
                "error": backfill.stats[name].error,
                "oversized": backfill.stats[name].oversized,
                "circuit_skips": backfill.stats[name].circuit,
            }
            for name in providers
        },
        "skipped_existing": backfill.skipped,
        "covered": real,
        "coverage_pct": round(coverage, 2),
        "unresolved": len(backfill.failed_symbols),
        "unresolved_symbols": backfill.failed_symbols[:500],
        "elapsed_s": round(elapsed, 1),
        "symbols": backfill.outcomes,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"report:    {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
