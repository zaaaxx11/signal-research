"""Company profile: the business summary and key officers Yahoo publishes.

The ticker page's Company block is one ``quoteSummary`` read of the
``assetProfile`` module. That module carries the long business summary
(``longBusinessSummary``) and the key people (``companyOfficers``); the
``price`` module rides the same request only for the display name
(``longName``/``shortName``), because ``assetProfile`` has no name of its own.

**One transport.** The cookie/crumb handshake is not re-implemented here:
:class:`YahooCompanySource` inherits :class:`app.market.yahoo_depth.YahooDepthSource`,
which already owns the ``fc.yahoo.com`` cookie warm-up, the
``/v1/test/getcrumb`` exchange, the 401/403 retry and the one HTTP seam
(``app.providers.http``). This module adds the narrower URL and the parser, not
a second way to talk to Yahoo.

**The cache is the product.** A company profile changes once a year, so the
answer is served from ``<data root>/company/{symbol}.json`` for seven days and
a cache hit does **not** consult the network. The file carries the normalized
payload plus a ``fetched_at`` stamp, so its age is a fact on disk rather than a
memory of the last process. When Yahoo refuses, an expired copy is still served
— labelled ``yahoo-cache-stale`` in ``source`` — because an out-of-date
paragraph is honest and a fabricated one is not. With nothing on disk at all
the route answers ``404 no_company_profile``.

**Never a paragraph from nothing.** A payload without a non-blank
``longBusinessSummary`` is not a profile: the parser returns ``None``, nothing
is written to the cache, and the caller falls back or refuses. An officer
without a name or a title is skipped rather than completed; the list preserves
Yahoo's order and stops at ten.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.market.depth import canonical_symbol
from app.market.yahoo_depth import (
    QUOTE_SUMMARY_PATH,
    YahooDepthSource,
    quote_summary_result,
)
from app.providers import http as provider_http
from app.providers.adapters.yahoo import HOST as YAHOO_HOST

logger = logging.getLogger("mee.market.company")

#: The one module the block is read from, plus the name source in the same
#: request. Asking for the fourteen depth modules here would pay for thirteen
#: modules nobody reads.
COMPANY_MODULES: tuple[str, ...] = ("assetProfile", "price")

#: A company profile is a historical document: seven days is the product's
#: refresh window, not a provider limit.
COMPANY_TTL_S = 7 * 24 * 60 * 60

#: The block shows the key people, not the whole register.
MAX_OFFICERS = 10

#: Where the cache lives. The explicit override exists for tests and for a
#: deployment that keeps company profiles on a different volume; otherwise the
#: directory sits beside the depth store the rest of this box's data uses.
COMPANY_DIR_ENV = "MEE_COMPANY_CACHE_DIR"

#: A symbol that can safely become a filename: uppercase provider spelling
#: (``BRK-B``, ``2330.TW``, ``^GSPC``), never a path separator.
_SYMBOL_RE = re.compile(r"^[A-Z0-9^][A-Z0-9.^=-]{0,23}$")

#: A stale crumb is auth plumbing, not a verdict about the company.
_AUTH_STATUS = frozenset({401, 403})


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _text(value: Any) -> str:
    """A stripped string, or ``""`` for anything that is not text."""

    return value.strip() if isinstance(value, str) else ""


def _collapse(value: Any) -> str:
    """Plain text: every run of whitespace (newlines included) becomes one space."""

    return " ".join(_text(value).split())


def _safe_symbol(raw: Any) -> str:
    """The provider spelling when it is filename-safe, else ``""``."""

    key = canonical_symbol(raw)
    return key if _SYMBOL_RE.match(key) else ""


# ---------------------------------------------------------------------------
# The block's shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Officer:
    """One key person, exactly as the source names them."""

    name: str
    title: str

    def document(self) -> dict[str, str]:
        return {"name": self.name, "title": self.title}


@dataclass(frozen=True, slots=True)
class CompanyProfile:
    """A parsed ``assetProfile``: the paragraph and the people."""

    symbol: str
    name: str
    description: str
    officers: tuple[Officer, ...]

    def payload(self) -> dict[str, Any]:
        """The part of the block worth caching; provenance is added at serve time."""

        return {
            "name": self.name,
            "description": self.description,
            "officers": [officer.document() for officer in self.officers],
        }

    def document(self, *, source: str, as_of: datetime) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "description": self.description,
            "officers": [officer.document() for officer in self.officers],
            "source": source,
            "as_of": as_of.isoformat(),
        }


def _officers(raw: Any) -> tuple[Officer, ...]:
    """Up to :data:`MAX_OFFICERS` named people, in the source's order."""

    if not isinstance(raw, list):
        return ()
    found: list[Officer] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        name = _collapse(entry.get("name"))
        title = _collapse(entry.get("title"))
        if not name or not title:
            # A blank name is not a person and a blank title is not a role;
            # completing either one would be inventing the block.
            continue
        found.append(Officer(name=name, title=title))
        if len(found) == MAX_OFFICERS:
            break
    return tuple(found)


def parse_company_profile(symbol: Any, modules: Any) -> CompanyProfile | None:
    """``quoteSummary.result[0]`` → a profile, or ``None`` when there is none.

    ``None`` is the single honest answer for every way the block is absent: no
    envelope, no ``assetProfile`` block, or no business summary text. The route
    turns it into ``404 no_company_profile``; the cache is never written from
    it.
    """

    key = _safe_symbol(symbol)
    if not key or not isinstance(modules, Mapping):
        return None
    profile = modules.get("assetProfile")
    if not isinstance(profile, Mapping):
        return None
    description = _collapse(profile.get("longBusinessSummary"))
    if not description:
        return None

    price = modules.get("price")
    quote_type = modules.get("quoteType")
    name = _text(price.get("longName")) if isinstance(price, Mapping) else ""
    if not name and isinstance(price, Mapping):
        name = _text(price.get("shortName"))
    if not name and isinstance(quote_type, Mapping):
        name = _text(quote_type.get("longName"))
    if not name:
        name = key

    return CompanyProfile(
        symbol=key,
        name=name,
        description=description,
        officers=_officers(profile.get("companyOfficers")),
    )


# ---------------------------------------------------------------------------
# The disk cache
# ---------------------------------------------------------------------------


def company_dir() -> Path:
    """The profile directory: an explicit override, else beside the depth store.

    The deployment pins ``MEE_DEPTH_STORE_PATH`` (``/home/ubuntu/ztockz-data/
    depth.json``) rather than ``MEE_DATA_DIR``, so the depth store's directory
    is the evidence of where this box keeps its data — the same rule
    ``app/api/logos.py`` and the canary already follow. With neither set, the
    project's portable ``data_dir()`` applies.
    """

    configured = os.environ.get(COMPANY_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    store = os.environ.get("MEE_DEPTH_STORE_PATH", "").strip()
    if store:
        parent = Path(store).expanduser().parent
        if str(parent) not in ("", "."):
            return parent / "company"
    from app.engine.paths import data_dir

    return data_dir() / "company"


def cache_path(symbol: Any, directory: Path | None = None) -> Path | None:
    """``<company dir>/<SYMBOL>.json``, or ``None`` for an unsafe symbol."""

    key = _safe_symbol(symbol)
    if not key:
        return None
    return (directory or company_dir()) / f"{key}.json"


@dataclass(frozen=True, slots=True)
class CachedCompany:
    """A profile read back from disk, with the clock its file records."""

    profile: CompanyProfile
    fetched_at: datetime


def _stamp(value: Any) -> datetime | None:
    """A timezone-aware ISO stamp, or ``None`` — a naive one cannot be aged."""

    text = _text(value)
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        return None
    return moment.astimezone(UTC)


def read_cache(path: Path) -> CachedCompany | None:
    """A valid cached profile, or ``None`` for absent/corrupt/undated files."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, Mapping):
        return None
    fetched_at = _stamp(raw.get("fetched_at"))
    payload = raw.get("payload")
    symbol = _safe_symbol(raw.get("symbol"))
    if fetched_at is None or not isinstance(payload, Mapping) or not symbol:
        return None
    description = _collapse(payload.get("description"))
    if not description:
        return None
    return CachedCompany(
        profile=CompanyProfile(
            symbol=symbol,
            name=_text(payload.get("name")) or symbol,
            description=description,
            officers=_officers(payload.get("officers")),
        ),
        fetched_at=fetched_at,
    )


def write_cache(path: Path, profile: CompanyProfile, fetched_at: datetime) -> None:
    """Write the payload and its stamp atomically; a half file is never served."""

    body = json.dumps(
        {
            "symbol": profile.symbol,
            "fetched_at": fetched_at.isoformat(),
            "source": "yahoo",
            "payload": profile.payload(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - cleanup of a failed write
            pass


def _fresh(fetched_at: datetime, now: datetime, ttl_s: float) -> bool:
    """Within the window, on the document's own clock."""

    return (now - fetched_at).total_seconds() <= ttl_s


# ---------------------------------------------------------------------------
# The Yahoo read — on the depth source's transport
# ---------------------------------------------------------------------------


class YahooCompanySource(YahooDepthSource):
    """One ``quoteSummary`` request for ``assetProfile`` (+ ``price``).

    Everything transport-shaped is inherited: the Windows UA, the cookie jar,
    the crumb exchange and the one 401/403 renewal. This class only narrows the
    request and parses the result, so a company block cannot drift onto a
    second Yahoo path.
    """

    name = "yahoo_company"

    def __init__(
        self,
        *,
        timeout_s: float = 20.0,
        proxy: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(
            timeout_s=timeout_s,
            include_timeseries=False,
            include_options=False,
            proxy=proxy,
            clock=clock,
        )

    @staticmethod
    def profile_url(symbol: str, crumb: str = "") -> str:
        url = (
            f"{YAHOO_HOST}{QUOTE_SUMMARY_PATH.format(symbol=symbol)}"
            f"?modules={','.join(COMPANY_MODULES)}"
            "&corsDomain=finance.yahoo.com&formatted=false"
        )
        return f"{url}&crumb={crumb}" if crumb else url

    async def fetch(self, symbol: Any) -> CompanyProfile | None:
        """The company block for one symbol, or ``None`` when Yahoo will not say."""

        key = _safe_symbol(symbol)
        if not key:
            return None
        async with provider_http.build_client(
            timeout_s=self.timeout_s, headers=self.headers(), proxy=self.proxy
        ) as client:
            # One handshake per read: the crumb is cheap next to a body request,
            # and a fresh client has an empty cookie jar anyway.
            self._crumb = await self._login(client)
            status, payload = await self._get_json(
                client, self.profile_url(key, self._crumb)
            )
            if status in _AUTH_STATUS:
                crumb = await self._renew_crumb(client, self._crumb)
                if crumb:
                    status, payload = await self._get_json(
                        client, self.profile_url(key, crumb)
                    )
            if status != 200:
                logger.info("yahoo company profile for %s answered %s", key, status)
                return None
            modules = quote_summary_result(payload)
            return parse_company_profile(key, modules)


Fetcher = Callable[[str], Awaitable[CompanyProfile | None]]


def default_fetcher() -> Fetcher:
    """The production read: one Yahoo ``assetProfile`` request."""

    return YahooCompanySource().fetch


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


async def collect_company_profile(
    symbol: Any,
    *,
    fetcher: Fetcher | None = None,
    now: datetime | None = None,
    ttl_s: float = COMPANY_TTL_S,
    directory: Path | None = None,
) -> dict[str, Any] | None:
    """The company block, from a fresh cache, a live read, or an old cache.

    Order of answers:

    1. a cache file inside ``ttl_s`` — served, no network, ``source="yahoo-cache"``;
    2. a live payload with a business summary — cached, ``source="yahoo"``;
    3. an expired cache file when the live read refused — served as
       ``source="yahoo-cache-stale"``;
    4. ``None``, which the route turns into ``404 no_company_profile``.
    """

    key = _safe_symbol(symbol)
    if not key:
        return None
    path = cache_path(key, directory)
    if path is None:  # pragma: no cover - cache_path applies the same rule
        return None
    moment = now or _utcnow()
    cached = read_cache(path)
    if cached is not None and _fresh(cached.fetched_at, moment, ttl_s):
        return cached.profile.document(source="yahoo-cache", as_of=cached.fetched_at)

    profile: CompanyProfile | None = None
    try:
        profile = await (fetcher or default_fetcher())(key)
    except Exception as exc:  # noqa: BLE001 - a refusal is answered by the cache
        logger.warning("company profile read for %s failed: %s", key, exc)
        profile = None

    if profile is not None:
        # The document's own contract, enforced on this side of the seam too:
        # plain text, no blank paragraph, no officer without a name and a title.
        description = _collapse(profile.description)
        if description:
            profile = CompanyProfile(
                symbol=key,
                name=_text(profile.name) or key,
                description=description,
                officers=_officers([o.document() for o in profile.officers]),
            )
        else:
            profile = None

    if profile is not None:
        try:
            write_cache(path, profile, moment)
        except OSError as exc:
            logger.warning("company profile cache write for %s failed: %s", key, exc)
        return profile.document(source="yahoo", as_of=moment)

    if cached is not None:
        return cached.profile.document(source="yahoo-cache-stale", as_of=cached.fetched_at)
    return None


__all__ = [
    "COMPANY_DIR_ENV",
    "COMPANY_MODULES",
    "COMPANY_TTL_S",
    "MAX_OFFICERS",
    "CachedCompany",
    "CompanyProfile",
    "Officer",
    "YahooCompanySource",
    "cache_path",
    "collect_company_profile",
    "company_dir",
    "default_fetcher",
    "parse_company_profile",
    "read_cache",
    "write_cache",
]
