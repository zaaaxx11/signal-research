"""``GET /logos/{symbol}``: one brand mark per URL, fetched once and kept.

A screen row's ``logo`` used to be a **data URI** — a ~640-byte monogram, base64
in the JSON. A 100-row panel that asked its view for all columns therefore
carried 64 KB of decoration, and the panel budget paid for it by dropping the
field, which is how the grid ended up showing a bare letter. A logo belongs on
its own URL: the projection drops the field entirely, every row keeps an
``<img src="/logos/AAPL">``, and the bytes are fetched by the browser once,
cached for thirty days.

The ladder
----------

1. **the store** — ``<logo_dir>/{SYMBOL}.{png,svg,jpg,jpeg,webp}``. This is the
   hand-curated set and the upstream cache, one directory, so an operator drops a
   file in and it wins. ``scripts/logos-backfill.py`` fills this rung in bulk;
2. **the vendored set** — the 35 files under ``ui/logos`` the cockpit has always
   shipped. They stay authoritative for what they cover;
3. **symbol-addressed providers** — ``assets.parqet.com/logos/symbol/{SYMBOL}``
   then ``images.financialmodelingprep.com/symbol/{SYMBOL}.png``. Both are
   keyless, both take the ticker and need no website (79% and most of the
   remainder on a 200-symbol pilot), so a symbol the depth store has never heard
   of can still get a mark;
4. **one domain-addressed provider** — a favicon service addressed by the
   symbol's website **domain**, not by its URL. The host is a constant in this
   module: the data contributes one validated hostname, and nothing else;
5. **the monogram** — the same generator ``_with_logos`` uses, decoded from its
   ``data:`` URI. Every valid symbol answers 200, so a row is never a hole.

A mark is fetched once, validated, and written to the store; while the file
exists the network is never touched again. A success and a refusal are both
remembered, so an outage cannot turn one panel into a hundred probes. The
ladder is bounded by a per-request deadline: an unreachable provider costs one
timeout, not one per rung.

Why the domain rung is ``t2.gstatic.com`` and not Clearbit
----------------------------------------------------------

``https://logo.clearbit.com/{domain}?size=64`` is the obvious shape, and it no
longer resolves: ``logo.clearbit.com`` has no A record at all (checked over
DNS-over-HTTPS as well as locally, 2026-11-01), so shipping it as a rung would
ship one that can only fail. ``faviconV2`` on ``t2.gstatic.com`` has the same
contract — one fixed host, the domain as a parameter, a PNG or a 404 — and it
answers (measured 1.2 KB in ~40 ms). The template is a module constant rather
than a setting on purpose: a configurable base URL is an SSRF knob, and this
route has no legitimate use for one.

Security
--------

* ``symbol`` is validated by :func:`normalize_symbol` **before** any path is
  built: a traversal spelling, a space, a NUL or a 200-character string is a 404
  and reaches neither the filesystem nor the fetcher. Case is folded, so
  ``aapl`` and ``AAPL`` are one cache entry.
* ``website`` comes from the depth store, which is provider data — hostile by
  assumption. :func:`safe_domain` accepts a bare hostname and nothing else:
  loopback, link-local, RFC1918, IPv6 literals, ``.local``/``.internal``,
  userinfo, ports and non-HTTP schemes are refused, and the domain is then pasted
  into a **fixed** host, so a redirect or a crafted host cannot retarget the
  request.
* A cached file is re-validated by magic bytes on every read. A 0-byte file, an
  HTML error page or a truncated PNG is deleted and the ladder continues; nothing
  invalid is ever served as ``image/*``.
* Writes are atomic (temp file in the same directory, then ``os.replace``), and
  the store has a per-file ceiling and a total budget that evicts the oldest
  marks. A logo cache that can fill a disk is an outage with a delay fuse.

Caching
-------

The two answers have opposite cache lives, because they are not the same kind of
thing. A **real mark** is bytes with a strong ``ETag`` (a digest of those bytes);
it is served ``public, max-age=86400, must-revalidate``, so the browser reuses it
for a day and then revalidates — a mark replaced in the store is picked up on the
next request past that window, and the digest guarantees the 304/200 decision is
about the bytes, not the file's mtime. A client that cannot wait can bust the URL
with any query string; the route ignores it.

A **monogram** is a placeholder for a mark that does not exist yet. It used to
carry the same thirty-day ``immutable`` policy, which is the bug this contract
closes: a monogram cached before the logo existed (or before the backfill ran)
stayed in the browser for a month, and the real logo only appeared after a hard
refresh. It is now ``no-store``. ``ETag`` stays a digest of the bytes that were
sent so a same-page repeat can still be answered with 304, but nothing is ever
written to a cache.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.api.cache import matches
from app.auth import ApiError
from app.branding import LOGO_DOMAINS, logo_for
from app.config import Settings, get_settings
from app.engine.paths import data_dir

logger = logging.getLogger("mee.api.logos")

router = APIRouter(prefix="/logos", tags=["logos"])

#: The one host this route may talk to. A constant, not a setting: an operator
#: who can point the logo cache at an arbitrary URL can use it as an SSRF relay.
UPSTREAM_HOST = "t2.gstatic.com"
#: ``{domain}`` is the only interpolation, and ``safe_domain`` guarantees it is
#: ``[a-z0-9.-]``, so the query string cannot be widened by a crafted website.
UPSTREAM_TEMPLATE = (
    f"https://{UPSTREAM_HOST}/faviconV2?client=SOCIAL&type=FAVICON"
    "&fallback_opts=TYPE,SIZE,URL&url=https%3A%2F%2F{domain}&size=64"
)

#: The symbol-addressed rungs. Both hosts are constants for the same SSRF
#: reason as ``UPSTREAM_HOST``; only the ticker rides in the path, and
#: :func:`normalize_symbol` has already refused anything that is not one.
#: They need no website, which is what makes a symbol the depth store has never
#: seen resolve at all.
PARQET_TEMPLATE = "https://assets.parqet.com/logos/symbol/{symbol}?format=png"
FMP_TEMPLATE = "https://images.financialmodelingprep.com/symbol/{symbol}.png"

#: The ladder's provider order, symbol-addressed first. A provider that can only
#: fail (``logo.clearbit.com``, no A record) is not in it: a dead rung costs a
#: timeout on every uncached symbol and buys nothing.
PROVIDER_ORDER: tuple[str, ...] = ("parqet", "fmp", "google")

#: The RFC 3986 unreserved set: a ticker never needs anything else escaped, and
#: ``urllib.parse`` is a **legacy transport** this tree may not import
#: (``tests/test_import_direction.py``). Encoding a path segment is not a
#: transport, so the eight lines live here rather than pulling a banned module
#: in for one call.
_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)


def _quote(text: str) -> str:
    """``text`` percent-encoded as one URL path segment (UTF-8 bytes)."""

    return "".join(
        chr(byte) if chr(byte) in _UNRESERVED else f"%{byte:02X}"
        for byte in text.encode("utf-8")
    )


#: How many seconds one uncached symbol may spend across the whole ladder. A
#: provider that is up answers in ~50-600 ms; a provider that is down must cost
#: one timeout, not one per rung. The single-fetch timeout still comes from
#: ``logo_upstream_timeout_s``.
LADDER_BUDGET_S = 12.0

#: One day for a real mark, then revalidate. Long enough that a page turn costs
#: nothing, short enough that a logo replaced in the store is picked up without
#: a hard refresh. ``must-revalidate`` is what makes the ``ETag`` load-bearing:
#: the browser may not reuse a stale mark once the window closes, and every
#: revalidation is answered from a digest of the bytes.
MAX_AGE_S = 86_400
CACHE_CONTROL = f"public, max-age={MAX_AGE_S}, must-revalidate"

#: A monogram is a placeholder for a mark that does not exist yet, so it is not
#: cacheable at all. Caching one — especially as ``immutable`` — is how a
#: browser kept showing a letter for thirty days after the logo landed.
MONOGRAM_CACHE_CONTROL = "no-store"

#: How long a failed fetch is remembered in-process. Long enough that a dead
#: upstream costs one probe per symbol per ten minutes, short enough that a
#: recovered one is picked up inside a session.
RETRY_AFTER_S = 600.0

#: Per-file ceiling for an upstream answer. A 64 px favicon is 1–5 KB; anything
#: past this is not the image that was asked for.
DEFAULT_MAX_BYTES = 262_144
#: Total store budget (~4,200 marks at 3 KB is 12 MB, so this is generous).
DEFAULT_STORE_MAX_BYTES = 33_554_432

#: The extensions the ladder understands, in the order it looks for them.
EXTENSIONS: tuple[str, ...] = (".png", ".svg", ".jpg", ".jpeg", ".webp")

#: The vendored set the cockpit has always shipped (``app.branding.logos``
#: resolves the same directory for the JSON field; a test pins the equality).
VENDORED_DIR = Path(__file__).resolve().parents[2] / "ui" / "logos"

_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}
_EXT_FOR: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}

_DATA_PREFIX = "data:image/svg+xml;base64,"

#: A ticker as every provider spells it: ``AAPL``, ``BRK.B``, ``BTC-USD``,
#: ``000660.KS``, ``^GSPC``, and the preferred-share spelling with a class
#: suffix (``JPM/PC``, 34 of a 100-row US panel). Bounded in length so a
#: 200-character string is refused before it becomes a filename, and the suffix
#: is one slash and at most three letters, so a path cannot be smuggled through.
_SYMBOL_RE = re.compile(r"([A-Z0-9^][A-Z0-9.\-^]{0,19})(?:/([A-Z]{1,3}))?")

#: Longest accepted symbol: 20 characters plus ``/`` plus three class letters.
_MAX_SYMBOL = 24

_HOST_RE = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
_IPV4_RE = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")

_ALLOWED_SCHEMES = frozenset({"http", "https"})
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

#: Bounded so a caller inventing symbols cannot grow the lock table without
#: limit. Pruning only happens when no lock is held, so it cannot split a
#: single-flight group.
_MAX_LOCKS = 4_096


class InvalidSymbol(ValueError):
    """``symbol`` is not a ticker; the route answers 404 and touches nothing."""


def normalize_symbol(symbol: object) -> str:
    """The canonical cache key for ``symbol``: upper-case, bounded, path-free.

    The single gate between a URL segment and a filename. ``fullmatch`` is used
    rather than ``match`` with ``$``, because ``$`` also matches before a
    trailing newline — which is exactly how ``"AAPL\\n"`` becomes a filename
    that is not the one that was validated.

    A preferred-share ticker is spelled ``JPM/PC`` by the provider and ``JPM-PC``
    by the depth store, so the class suffix is folded onto a dash: one key, one
    file, and a slash can never survive into a path. Anything else with a
    separator in it is refused here, before a path or a URL exists.
    """

    raw = "" if symbol is None else str(symbol)
    if not raw or len(raw) > _MAX_SYMBOL or any(char.isspace() for char in raw):
        raise InvalidSymbol(f"{symbol!r} is not a ticker")
    key = raw.upper()
    matched = _SYMBOL_RE.fullmatch(key)
    if matched is None:
        raise InvalidSymbol(f"{symbol!r} is not a ticker")
    base, share_class = matched.groups()
    return f"{base}-{share_class}" if share_class else base


def safe_domain(website: object) -> str | None:
    """The bare hostname of ``website``, or ``None`` when it is not safe to ask.

    ``website`` is provider data. Only an ``http(s)`` document whose authority is
    a plain DNS name survives: no userinfo, no port, no IPv4/IPv6 literal, no
    single-label host, and nothing in the private, loopback, link-local or
    special-use namespaces. What comes back is pasted into a **fixed** URL, so
    this is an allow-list of shapes, not a block-list of the day's attack.
    """

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
        # ``@`` is userinfo and ``:`` is a port or an IPv6 literal; neither is
        # part of a domain this route will address a fixed host with.
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


def symbol_variants(symbol: str) -> tuple[str, ...]:
    """The spellings the symbol providers use for one issuer, best first.

    ``BRK.B`` is ``BRK-B`` at both symbol providers, while the depth store
    spells the class with a dash and a caller may use a dot. The canonical
    spelling is always first, so the common case costs one request; a class
    suffix is the only thing that grows the list.
    """

    variants = [symbol]
    if "." in symbol:
        variants.append(symbol.replace(".", "-"))
    if "-" in symbol:
        base, _, suffix = symbol.partition("-")
        if base and 1 <= len(suffix) <= 3 and suffix.isalpha():
            variants.append(f"{base}.{suffix}")
    return tuple(dict.fromkeys(variants))


def provider_urls(symbol: str, domain: str | None) -> list[tuple[str, str]]:
    """``(provider, url)`` pairs for ``symbol``, in ladder order.

    A domain-addressed provider contributes nothing when there is no domain —
    that is not a failure, it is a rung with no question to ask. The symbol
    rungs always contribute, which is why a symbol with no website can still
    resolve to a real mark.
    """

    urls: list[tuple[str, str]] = []
    for provider in PROVIDER_ORDER:
        if provider == "parqet":
            urls.extend(
                ("parqet", PARQET_TEMPLATE.format(symbol=_quote(variant)))
                for variant in symbol_variants(symbol)
            )
        elif provider == "fmp":
            urls.extend(
                ("fmp", FMP_TEMPLATE.format(symbol=_quote(variant)))
                for variant in symbol_variants(symbol)
            )
        elif provider == "google" and domain:
            urls.append(("google", UPSTREAM_TEMPLATE.format(domain=domain)))
    return urls


def sniff_image(body: bytes | bytearray | None) -> str | None:
    """The media type of ``body`` by magic bytes, or ``None`` when it is not one.

    Extension is never consulted: a cached ``.png`` that holds an HTML error
    page must not be served as ``image/png``. SVG is the one text format, so it
    is recognised by its opening tag rather than by a binary signature.
    """

    if not body:
        return None
    data = bytes(body[:4096]) if len(body) > 4096 else bytes(body)
    head = bytes(body[:16])
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    text = data.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if text.startswith(b"<svg"):
        return "image/svg+xml"
    if text.startswith(b"<?xml") and b"<svg" in text:
        return "image/svg+xml"
    return None


@dataclass(frozen=True, slots=True)
class LogoImage:
    """One answer of the ladder: the bytes, their type, and which rung answered."""

    body: bytes
    media_type: str
    source: str

    @property
    def etag(self) -> str:
        """A strong validator: the digest of the exact bytes being sent."""

        return '"' + hashlib.sha256(self.body).hexdigest()[:32] + '"'


#: A fetch is ``(url, timeout_s) -> body``; ``None`` means "no image". Injected
#: by every test, so the suite never opens a socket and can count the calls.
Fetcher = Callable[[str, float], Awaitable[bytes | None]]


async def fetch_upstream(url: str, timeout_s: float) -> bytes | None:
    """The one outbound call this module makes, through the project's seam.

    ``build_client`` is called as a module attribute, which is what makes the
    Phase-2 ``MockTransport`` patch reach here too. Redirects are off in the
    seam, so a 3xx is a failed rung rather than a hop to a host nobody vetted.
    """

    from app.providers import http as http_seam

    try:
        client = http_seam.build_client(
            timeout_s=max(1.0, float(timeout_s)),
            connect_s=min(5.0, max(1.0, float(timeout_s))),
            max_connections=4,
            http2=False,
            headers={"accept": "image/png,image/webp,image/*;q=0.8"},
        )
    except Exception:  # pragma: no cover - a broken client factory is fatal elsewhere
        logger.warning("logo upstream client could not be built", exc_info=True)
        return None

    try:
        async with client:
            response = await client.get(url)
            if response.status_code != 200:
                logger.debug("logo upstream %s answered %s", url, response.status_code)
                return None
            return response.content
    except Exception as exc:  # noqa: BLE001 - a missing mark is never a 500
        logger.debug("logo upstream %s failed: %s", url, exc)
        return None


def resolve_logo_dir(settings: Any | None = None) -> Path:
    """The on-disk store: ``logo_dir``, else ``<data root>/logos``.

    ``None`` is not "no store" — it is "the store next to the other data", which
    is what makes ``MEE_DATA_DIR`` move the logos with the depth store and the
    snapshots instead of leaving them behind in the working directory.
    """

    configured = getattr(settings, "logo_dir", None)
    if isinstance(configured, Path):
        return configured.expanduser()
    if isinstance(configured, str) and configured.strip():
        return Path(configured.strip()).expanduser()
    # This deployment rarely sets MEE_DATA_DIR: engine.env pins each store by
    # absolute path instead, so ``data_dir()`` would resolve to the working
    # directory and drop a logo cache into the checkout. The depth store's own
    # directory is the best evidence of where this box keeps its data.
    store = getattr(settings, "health_depth_store_path", None) or getattr(
        settings, "depth_store_path", None
    )
    if store:
        parent = Path(store).parent
        if parent.is_absolute() and str(parent) not in ("", "."):
            return parent / "logos"
    return data_dir() / "logos"


def _atomic_write(path: Path, body: bytes) -> None:
    """Write ``body`` to ``path`` through a temp file in the same directory.

    ``os.replace`` is atomic within one filesystem, so a reader either sees the
    old file or the new one — never a half-written PNG, which the next request
    would have to treat as corrupt.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp"
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


class LogoStore:
    """The ladder over one directory, with the fetch, the lock and the budget.

    One instance per application (``app.state.logos``), so the single-flight
    locks and the negative cache are properties of the process. The store is
    stateless between requests otherwise: the bytes live on disk, which is what
    makes a second request free even across a restart.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        fetcher: Fetcher | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.directory = resolve_logo_dir(self.settings)
        self.upstream = bool(getattr(self.settings, "logo_upstream", True))
        self.timeout_s = float(getattr(self.settings, "logo_upstream_timeout_s", 5.0))
        self.max_bytes = int(getattr(self.settings, "logo_max_bytes", DEFAULT_MAX_BYTES))
        self.store_max_bytes = int(
            getattr(self.settings, "logo_store_max_bytes", DEFAULT_STORE_MAX_BYTES)
        )
        self.fetcher: Fetcher = fetcher or fetch_upstream
        self._clock = clock or time.time
        self._locks: dict[str, asyncio.Lock] = {}
        self._retry_after: dict[str, float] = {}
        self._counts = {"store": 0, "vendored": 0, "upstream": 0, "monogram": 0, "refused": 0}

    # -- the ladder ---------------------------------------------------------

    async def resolve(self, symbol: str, *, name: str | None = None) -> LogoImage:
        """A mark for ``symbol``, or a monogram. Never raises, never 404s."""

        try:
            stored = self._stored(symbol)
            if stored is not None:
                return stored
            vendored = self._vendored(symbol)
            if vendored is not None:
                return vendored
            if self.upstream:
                website = self.website_for(symbol)
                domain = safe_domain(website) if website else None
                if website and domain is None:
                    self._counts["refused"] += 1
                    logger.info(
                        "logo: refusing non-public website %r for %s", website, symbol
                    )
                # The symbol providers do not need a domain; the domain rung is
                # contributed by ``provider_urls`` only when there is one.
                fetched = await self._fetch_once(symbol, domain)
                if fetched is not None:
                    return fetched
        except Exception:  # noqa: BLE001 - the mark is decoration; a 500 is not
            logger.warning("logo resolution failed for %r", symbol, exc_info=True)
        return self._monogram(symbol, name)

    def website_for(self, symbol: str) -> str | None:
        """The symbol's website from the depth store, then the folded map.

        The depth store is the catalogue this deployment already reads (4,076 of
        4,217 symbols carry a site). Its 11.6 MB parse is cached per file
        version by ``app.screen.depth_source``, so a logo request pays a
        ``stat``, not a parse, once the screener has run.
        """

        row = self._row(symbol)
        if row is not None:
            website = row.get("website")
            if isinstance(website, str) and website.strip():
                return website.strip()
        return LOGO_DOMAINS.get(symbol)

    def _row(self, symbol: str) -> Mapping[str, Any] | None:
        try:
            from app.screen.depth_source import current_depth

            snapshot = current_depth(self.settings)
        except Exception:  # noqa: BLE001 - no depth store is a normal deployment
            logger.debug("logo: depth store unavailable for %s", symbol, exc_info=True)
            return None
        # The store spells class shares with a dash (``BRK-B``); a caller may
        # spell them with a dot. Both are the same issuer.
        for key in (
            symbol,
            symbol.replace(".", "-"),
            symbol.replace("-", "."),
            symbol.replace("-", "/"),
        ):
            row = snapshot.symbols.get(key)
            if isinstance(row, Mapping):
                return row
        return None

    def _stored(self, symbol: str) -> LogoImage | None:
        for extension in EXTENSIONS:
            path = self._path(symbol, extension)
            if path is None or not path.is_file():
                continue
            image = self._read(path, "store", prune=True)
            if image is not None:
                return image
        return None

    def _vendored(self, symbol: str) -> LogoImage | None:
        for extension in EXTENSIONS:
            path = VENDORED_DIR / f"{symbol}{extension}"
            if not path.is_file():
                continue
            image = self._read(path, "vendored", prune=False)
            if image is not None:
                return image
        return None

    def _read(self, path: Path, source: str, *, prune: bool) -> LogoImage | None:
        """Read and validate one file; a corrupt one is removed, never served."""

        try:
            body = path.read_bytes()
        except OSError as exc:
            logger.debug("logo: %s is unreadable: %s", path, exc)
            return None
        media = sniff_image(body)
        if media is None:
            logger.warning(
                "logo: discarding %s (%d bytes, not an image)", path, len(body)
            )
            if prune:
                try:
                    path.unlink()
                except OSError:  # already gone, or a read-only store
                    pass
            return None
        self._counts[source] = self._counts.get(source, 0) + 1
        return LogoImage(body=body, media_type=media, source=source)

    def _monogram(self, symbol: str, name: str | None = None) -> LogoImage:
        """The offline mark, decoded from the one generator the JSON field uses."""

        mark = logo_for(symbol, name)
        if not mark.startswith(_DATA_PREFIX):
            # ``logo_for`` prefers a vendored file; this rung is only reached
            # when there is none, so a hit here is a race with a build. Ask for
            # the guaranteed branch rather than fail a row over it.
            mark = logo_for(None, name)
        body = base64.b64decode(mark[len(_DATA_PREFIX) :])
        self._counts["monogram"] = self._counts.get("monogram", 0) + 1
        return LogoImage(body=body, media_type="image/svg+xml", source="monogram")

    # -- the fetch ----------------------------------------------------------

    async def _fetch_once(self, symbol: str, domain: str | None) -> LogoImage | None:
        """Fetch ``symbol`` at most once, even under fifty concurrent requests.

        The lock is per symbol: the first caller fetches, the other forty-nine
        wait and then find the file the first one wrote. The disk is re-checked
        inside the lock because "the file exists" is the cache, and a writer
        outside this process (an operator, or the batch backfill) counts.

        Inside the lock the provider ladder is walked in order — symbol-addressed
        first, because it needs no data beyond the ticker, then the domain rung
        when there is a safe domain. The first body that sniffs as an image and
        fits the per-file ceiling wins and is written; ``LADDER_BUDGET_S`` bounds
        the whole walk, so an unreachable provider costs one timeout instead of
        one per rung.
        """

        if self._retry_after.get(symbol, 0.0) > self._clock():
            return None

        lock = self._lock_for(symbol)
        async with lock:
            cached = self._stored(symbol)
            if cached is not None:
                return cached
            if self._retry_after.get(symbol, 0.0) > self._clock():
                return None

            ladder = provider_urls(symbol, domain)
            if not ladder:
                self._retry_after[symbol] = self._clock() + RETRY_AFTER_S
                return None
            self._counts["upstream"] = self._counts.get("upstream", 0) + 1

            deadline = self._clock() + LADDER_BUDGET_S
            for provider, url in ladder:
                if self._clock() > deadline:
                    logger.info(
                        "logo: ladder budget spent for %s before %s", symbol, provider
                    )
                    break
                try:
                    body = await self.fetcher(url, self.timeout_s)
                except Exception:  # noqa: BLE001 - an outage is a monogram, not a 500
                    logger.debug("logo: fetch for %s raised", symbol, exc_info=True)
                    body = None

                media = sniff_image(body) if body else None
                if body is None or media is None:
                    logger.debug("logo: %s had no mark for %s", provider, symbol)
                    continue
                if len(body) > self.max_bytes:
                    logger.warning(
                        "logo: refusing %d bytes from %s for %s (ceiling %d)",
                        len(body),
                        provider,
                        symbol,
                        self.max_bytes,
                    )
                    continue

                self._retry_after.pop(symbol, None)
                self._write(symbol, body, media)
                logger.debug("logo: %s answered for %s", provider, symbol)
                return LogoImage(body=body, media_type=media, source="upstream")

            self._retry_after[symbol] = self._clock() + RETRY_AFTER_S
            return None

    def _lock_for(self, symbol: str) -> asyncio.Lock:
        lock = self._locks.get(symbol)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[symbol] = lock
            if len(self._locks) > _MAX_LOCKS and not any(
                entry.locked() for entry in self._locks.values()
            ):
                self._locks = {symbol: lock}
        return lock

    # -- the store ----------------------------------------------------------

    def _path(self, symbol: str, extension: str) -> Path | None:
        """``<store>/<SYMBOL><ext>``, or ``None`` if it would leave the store.

        ``normalize_symbol`` already refuses separators, so this is the second
        lock on the same door — the one that still holds if a future caller
        reaches ``_path`` with something the gate never saw.
        """

        name = f"{symbol}{extension}"
        if Path(name).name != name:
            return None
        candidate = self.directory / name
        try:
            if candidate.resolve().parent != self.directory.resolve():
                return None
        except OSError:  # pragma: no cover - exotic path
            return None
        return candidate

    def _write(self, symbol: str, body: bytes, media: str) -> None:
        path = self._path(symbol, _EXT_FOR[media])
        if path is None:  # pragma: no cover - unreachable through the route
            return
        if len(body) > self.store_max_bytes:
            # One mark may not exceed the whole budget: caching it would evict
            # every other mark to store something the store cannot hold.
            logger.info(
                "logo: %s is %d bytes, over the %d-byte store budget; served, not cached",
                symbol,
                len(body),
                self.store_max_bytes,
            )
            return
        self._make_room(len(body))
        try:
            _atomic_write(path, body)
        except OSError as exc:  # a full or read-only disk costs a monogram, not a 500
            logger.warning("logo: could not store %s: %s", path, exc)

    def _files(self) -> list[tuple[Path, int, float]]:
        """Every file in the store as ``(path, size, mtime)``; never raises."""

        found: list[tuple[Path, int, float]] = []
        try:
            with os.scandir(self.directory) as entries:
                for entry in entries:
                    try:
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if entry.name.startswith("."):
                        # A temp file from a write in flight is not a mark and
                        # must never be evicted out from under its writer.
                        continue
                    found.append((Path(entry.path), int(stat.st_size), float(stat.st_mtime)))
        except OSError:
            return []
        return found

    def _make_room(self, incoming: int) -> None:
        """Evict the oldest marks until ``incoming`` fits inside the budget."""

        budget = self.store_max_bytes
        if incoming > budget:
            return  # the write is refused by the caller's per-file ceiling anyway
        files = self._files()
        total = sum(size for _path, size, _mtime in files)
        if total + incoming <= budget:
            return
        for path, size, _mtime in sorted(files, key=lambda item: item[2]):
            if total + incoming <= budget:
                break
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
            logger.info("logo: evicted %s to stay under %d bytes", path.name, budget)

    def stats(self) -> dict[str, Any]:
        """What the store holds, in bytes, and what the ladder has answered."""

        files = self._files()
        return {
            "directory": str(self.directory),
            "count": len(files),
            "bytes": sum(size for _path, size, _mtime in files),
            "max_bytes": self.store_max_bytes,
            "per_file_max_bytes": self.max_bytes,
            "upstream_enabled": self.upstream,
            "served": dict(self._counts),
        }


def logo_store_of(request: Request) -> LogoStore:
    """The store ``create_app`` attached, or one built from this app's settings.

    Built lazily and then remembered on ``app.state``: the first request pays the
    directory resolution, every later one shares the single-flight locks. A test
    (or an operator) may replace ``app.state.logos`` without a code path.
    """

    store: LogoStore | None = getattr(request.app.state, "logos", None)
    if store is None:
        runtime = getattr(request.app.state, "runtime", None)
        store = LogoStore(getattr(runtime, "settings", None))
        request.app.state.logos = store
    return store


@router.get(
    "/{symbol:path}",
    response_class=Response,
    responses={
        200: {
            "content": {"image/png": {}, "image/jpeg": {}, "image/svg+xml": {}},
            "description": "The brand mark, or an offline monogram when none exists.",
        },
        304: {"description": "The client already holds these bytes."},
        404: {"description": "``symbol`` is not a ticker."},
    },
)
async def logo(symbol: str, request: Request) -> Response:
    """One image per symbol: cacheable when it is a mark, never when it is not.

    The 404 is reserved for a string that cannot be a ticker: that request named
    no instrument, and answering it with a mark would put a letter in the Company
    column for a symbol that does not exist. Every *real* symbol — including one
    with no asset anywhere and no website — is a 200. The converter is ``path``
    so the preferred-share spelling (``JPM/PC``) reaches this function as one
    value; :func:`normalize_symbol` is what decides that it is a ticker and folds
    the class onto the same dash spelling the depth store uses.
    """

    try:
        key = normalize_symbol(symbol)
    except InvalidSymbol as exc:
        raise ApiError(404, "unknown_symbol", str(exc)) from exc

    image = await logo_store_of(request).resolve(key)
    # A monogram is a placeholder, not an asset: ``no-store`` so a mark that
    # appears later is picked up on the next page load instead of after a hard
    # refresh. A real mark keeps the long window but revalidates on its digest.
    cache_control = (
        MONOGRAM_CACHE_CONTROL if image.source == "monogram" else CACHE_CONTROL
    )
    headers = {
        "Cache-Control": cache_control,
        "ETag": image.etag,
        # Not a contract a client parses; the cheapest way to see which rung
        # answered without opening the filesystem.
        "X-Logo-Source": image.source,
    }
    if matches(image.etag, request.headers.get("if-none-match")):
        return Response(status_code=304, headers=headers)
    return Response(content=image.body, media_type=image.media_type, headers=headers)


__all__ = [
    "CACHE_CONTROL",
    "EXTENSIONS",
    "InvalidSymbol",
    "LogoImage",
    "LogoStore",
    "MAX_AGE_S",
    "MONOGRAM_CACHE_CONTROL",
    "UPSTREAM_HOST",
    "UPSTREAM_TEMPLATE",
    "VENDORED_DIR",
    "fetch_upstream",
    "logo_store_of",
    "normalize_symbol",
    "resolve_logo_dir",
    "router",
    "safe_domain",
    "sniff_image",
]
