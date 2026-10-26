"""SEC ingestion: fetch the documents, cache the bodies, build one dataset.

The engine's identity and accounting facts both come from EDGAR, and EDGAR is
three documents, not one API:

* ``company_tickers.json`` — the registrant index (ticker → CIK → title);
* ``submissions/CIK##########.json`` — one registrant, its classes and venues;
* ``api/xbrl/companyfacts/CIK##########.json`` — every us-gaap fact the issuer
  has tagged, with its accession and filing date.

This module is the I/O boundary for those three. It decodes nothing that
:mod:`app.engine.sources.sec` and :mod:`app.engine.facts` already own: the
parsers stay pure, and the load composes them. What lives here is the part that
touches the network and the disk, which is why it is the part with a cache and
an injected client.

Rules this slice makes structural
---------------------------------
* **Injected transport.** Every fetch takes ``client=``. Passing one means the
  caller owns its lifetime and this module never closes it; ``client=None``
  means build one through ``app.providers.http.build_client`` **as a module
  attribute**, so the suite can install a ``MockTransport`` at that seam. No
  module here imports ``httpx2``.
* **A contactable User-Agent.** EDGAR's fair-access policy answers ``403`` to
  an anonymous client, so the default carries a contact address and an override
  without one is refused before the request. The header is sent on every
  request, not just handed to the client factory: an injected client may ignore
  build-time defaults.
* **No redirects.** ``build_client`` already sets ``follow_redirects=False``,
  and a 3xx is treated as a failed fetch. Following a ``Location`` would let an
  allowlisted host walk the request to an arbitrary one.
* **One document per issuer.** The disk cache is keyed by the ten-digit CIK and
  the document kind (``CIK0000320193-companyfacts.json``), because
  ``companyfacts`` is per issuer and Berkshire's two classes are one document.
  A cache hit does not call the client. A corrupt entry, or one whose body
  belongs to another CIK, is treated as a miss and replaced — a cache is not
  evidence of anything except a document that was once read.
* **Per-symbol errors, never per-symbol guesses.** A failed issuer fetch is
  recorded in :attr:`EngineDataset.errors` and produces no facts entry. The
  other symbols in the same load still resolve.
* **The index is not cached.** It is the authority for *current* identity; a
  day-old copy could resolve a delisted ticker to a live-looking record, and it
  is one request per load. Only the per-issuer documents are cached.

No database, no scheduler, no sleeps: a dataset is one pass over a universe.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from app.engine.facts import fact_ttm_summary
from app.engine.model import Cell
from app.engine.security import SecurityMaster
from app.engine.sources.sec import (
    TICKERS_URL,
    normalize_cik,
    normalize_symbol,
    submissions_url,
)
from app.providers import http as provider_http

#: EDGAR's fair-access policy requires an identifying User-Agent **with a
#: contact**. An anonymous or library default is answered ``403``.
DEFAULT_USER_AGENT = "ztockz-market-engine/1.0 (market-engine@ztockz.local)"

#: ``companyfacts`` is not exposed by ``sources.sec`` (that module owns the
#: identity documents); the path is EDGAR's published shape.
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

#: Cache filename fragment: the CIK is the key and the kind keeps the filename
#: honest about which per-issuer document it holds.
_KIND_COMPANYFACTS = "companyfacts"


class SecIngestError(RuntimeError):
    """A SEC document could not be read, or was not the document expected."""


@dataclass(frozen=True)
class EngineDataset:
    """One observation of a universe: identity, facts and the failures.

    ``fetched_at`` is when this load ran (ISO-8601, UTC); a value's own date is
    on its :class:`~app.engine.model.Provenance`. ``facts`` holds only symbols
    whose document was read and parsed; ``errors`` holds one message per symbol
    that could not be, so an empty facts mapping and a failure are never
    confused.

    The mappings are read-only copies taken at construction. ``frozen=True``
    alone would still let a caller mutate the dict it passed in and rewrite
    what a completed load reported.
    """

    fetched_at: str
    security_master: SecurityMaster
    facts: Mapping[str, Mapping[str, Cell]]
    errors: Mapping[str, str]

    def __post_init__(self) -> None:
        fetched_at = (
            self.fetched_at.strip() if isinstance(self.fetched_at, str) else ""
        )
        if not fetched_at:
            raise ValueError("fetched_at must be a non-blank ISO timestamp")
        if not isinstance(self.security_master, SecurityMaster):
            raise TypeError(
                "security_master must be a SecurityMaster, got "
                f"{type(self.security_master).__name__}"
            )
        object.__setattr__(self, "fetched_at", fetched_at)
        object.__setattr__(self, "facts", MappingProxyType(_freeze_facts(self.facts)))
        object.__setattr__(self, "errors", MappingProxyType(_freeze_errors(self.errors)))


def _freeze_facts(
    facts: Mapping[str, Mapping[str, Cell]],
) -> dict[str, Mapping[str, Cell]]:
    """Copy ``symbol → cells`` and freeze each inner mapping."""

    if isinstance(facts, (str, bytes)) or not isinstance(facts, Mapping):
        raise TypeError("facts must be a mapping of symbol to metric cells")
    frozen: dict[str, Mapping[str, Cell]] = {}
    for symbol, cells in facts.items():
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"facts keys must be symbols, got {symbol!r}")
        if isinstance(cells, (str, bytes)) or not isinstance(cells, Mapping):
            raise TypeError(
                f"facts[{symbol!r}] must be a mapping of metric to Cell, got "
                f"{type(cells).__name__}"
            )
        frozen[symbol] = MappingProxyType(dict(cells))
    return frozen


def _freeze_errors(errors: Mapping[str, str]) -> dict[str, str]:
    """Copy ``symbol → message``, rejecting anything that is not text."""

    if isinstance(errors, (str, bytes)) or not isinstance(errors, Mapping):
        raise TypeError("errors must be a mapping of symbol to message")
    frozen: dict[str, str] = {}
    for symbol, message in errors.items():
        if not isinstance(symbol, str) or not isinstance(message, str):
            raise TypeError("errors must map a symbol string to a message string")
        frozen[symbol] = message
    return frozen


# ---------------------------------------------------------------------------
# Request plumbing
# ---------------------------------------------------------------------------


def _request_user_agent(user_agent: str | None) -> str:
    """The User-Agent to send, validated before any request is made."""

    candidate = DEFAULT_USER_AGENT if user_agent is None else user_agent
    text = candidate.strip() if isinstance(candidate, str) else ""
    if not text:
        raise ValueError("a User-Agent is required: SEC answers 403 without one")
    if "@" not in text:
        raise ValueError(
            "a User-Agent must carry a contact address for EDGAR's fair-access "
            f"policy, got {text!r}"
        )
    return text


def _headers(user_agent: str | None) -> dict[str, str]:
    """Per-request headers; sent on the request so an injected client keeps them."""

    return {
        "user-agent": _request_user_agent(user_agent),
        "accept": "application/json",
    }


def _require_cik(value: Any) -> str:
    """The ten-digit CIK, or ``ValueError`` — before any I/O."""

    cik = normalize_cik(value)
    if cik is None:
        raise ValueError(f"not a SEC CIK: {value!r}")
    return cik


def companyfacts_url(cik: Any) -> str:
    """The padded ``companyfacts`` URL for one CIK. Raises on a non-CIK."""

    return COMPANYFACTS_URL.format(cik=_require_cik(cik))


async def _fetch_json(
    url: str, *, client: Any, user_agent: str | None
) -> tuple[Any, str]:
    """GET one document; return ``(decoded payload, raw body text)``.

    Every transport-level failure becomes :class:`SecIngestError`, so the engine
    never has to catch an HTTP library's exception dialect. The raw text is
    returned for the cache: the document on disk should be the body EDGAR sent,
    not a re-serialisation of our own object.
    """

    headers = _headers(user_agent)
    try:
        if client is None:
            async with provider_http.build_client(headers=headers) as owned:
                response = await owned.get(url, headers=headers)
        else:
            response = await client.get(url, headers=headers)
        response.raise_for_status()
        text = response.text
    except Exception as exc:  # noqa: BLE001 - one domain error for the boundary
        raise SecIngestError(
            f"GET {url} failed: {type(exc).__name__}: {exc}"
        ) from exc
    try:
        return json.loads(text), text
    except ValueError as exc:
        raise SecIngestError(f"GET {url} did not answer JSON: {exc}") from exc


async def fetch_company_tickers(
    *, client: Any = None, user_agent: str | None = None
) -> object:
    """Read ``company_tickers.json`` (the registrant index) and decode it."""

    payload, _ = await _fetch_json(
        TICKERS_URL, client=client, user_agent=user_agent
    )
    return payload


async def fetch_submissions(
    cik: Any, *, client: Any = None, user_agent: str | None = None
) -> object:
    """Read one registrant's submissions document (classes, venues, filings)."""

    payload, _ = await _fetch_json(
        submissions_url(cik), client=client, user_agent=user_agent
    )
    return payload


async def fetch_companyfacts(
    cik: Any, *, client: Any = None, user_agent: str | None = None
) -> object:
    """Read one registrant's ``companyfacts`` document and decode it."""

    payload, _ = await _fetch_json(
        companyfacts_url(cik), client=client, user_agent=user_agent
    )
    return payload


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _cache_path(cache_dir: Path, cik: str, kind: str) -> Path:
    """``<cache_dir>/CIK##########-<kind>.json`` — keyed by CIK, openly JSON."""

    return Path(cache_dir) / f"CIK{cik}-{kind}.json"


def _looks_like_companyfacts(payload: Any) -> bool:
    """True for the outer shape ``{"facts": {...}}`` this slice can parse."""

    return isinstance(payload, Mapping) and isinstance(payload.get("facts"), Mapping)


def _read_facts_cache(path: Path, *, cik: str) -> Any | None:
    """The cached document, or ``None`` when the entry is absent or unusable.

    A missing file, invalid JSON, a body without a ``facts`` mapping, or a body
    whose own ``cik`` disagrees with the filename is a miss: the load re-fetches
    and overwrites instead of serving a document it cannot vouch for.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if not _looks_like_companyfacts(payload):
        return None
    document_cik = normalize_cik(payload.get("cik"))  # type: ignore[union-attr]
    if document_cik is not None and document_cik != cik:
        return None
    return payload


def _write_facts_cache(path: Path, text: str) -> None:
    """Write the body atomically, so a crash cannot leave half a document."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        with temporary:
            temporary.write(text)
        os.replace(temporary.name, path)
    except BaseException:
        Path(temporary.name).unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Coordination
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    """The load timestamp, seconds precision, always UTC."""

    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _observed_on(as_of: str | None) -> str:
    """The observation date for the index provenance."""

    if as_of is None:
        return datetime.now(UTC).date().isoformat()
    if not isinstance(as_of, str):
        raise TypeError(f"as_of must be text, got {type(as_of).__name__}")
    text = as_of.strip()
    if not text:
        raise ValueError("as_of must not be blank")
    return text


def _error_key(value: Any) -> str:
    """A stable key for a universe entry that is not a symbol."""

    if isinstance(value, str) and value.strip():
        return value
    return repr(value)


async def _issuer_facts(
    cik: str, *, client: Any, cache_dir: Path | None
) -> object:
    """Read one issuer's ``companyfacts`` body, through the cache when asked."""

    path: Path | None = None
    if cache_dir is not None:
        path = _cache_path(cache_dir, cik, _KIND_COMPANYFACTS)
        cached = _read_facts_cache(path, cik=cik)
        if cached is not None:
            return cached

    payload, text = await _fetch_json(
        companyfacts_url(cik), client=client, user_agent=None
    )
    if not _looks_like_companyfacts(payload):
        raise SecIngestError(f"CIK{cik} did not answer a companyfacts document")
    if path is not None:
        _write_facts_cache(path, text)
    return payload


class _IssuerReader:
    """Reads each issuer's document once per load.

    Two classes of one issuer (Berkshire's ``BRK-A``/``BRK-B``) are one CIK and
    therefore one document. The memo also remembers a failure, so the second
    class is reported with the same error instead of spending a second request
    on a document that has just failed.
    """

    def __init__(self, *, client: Any, cache_dir: Path | None) -> None:
        self._client = client
        self._cache_dir = cache_dir
        self._documents: dict[str, object] = {}
        self._failures: dict[str, str] = {}

    async def read(self, cik: str) -> object:
        """The issuer's decoded document, from the memo, the cache or EDGAR."""

        if cik in self._documents:
            return self._documents[cik]
        if cik in self._failures:
            raise SecIngestError(self._failures[cik])
        try:
            payload = await _issuer_facts(
                cik, client=self._client, cache_dir=self._cache_dir
            )
        except Exception as exc:
            self._failures[cik] = str(exc) or type(exc).__name__
            raise
        self._documents[cik] = payload
        return payload


async def load_sec_dataset(
    symbols: Sequence[str],
    *,
    client: Any = None,
    cache_dir: Path | None = None,
    as_of: str | None = None,
) -> EngineDataset:
    """Load identity and facts for ``symbols`` in one pass, no database.

    Identity comes from the registrant index, loaded every time because it is
    the authority for current listings. Facts come from each issuer's cached
    ``companyfacts`` document, keyed by CIK so two classes of one issuer share
    one read. A symbol that the master cannot resolve, and a document that
    cannot be read or parsed, lands in ``errors`` with no ``facts`` entry; every
    other symbol still loads.
    """

    if isinstance(symbols, (str, bytes)):
        raise TypeError("symbols must be a sequence of symbols, not one string")

    fetched_at = _utc_now()
    observed = _observed_on(as_of)

    index_payload = await fetch_company_tickers(client=client)
    if not isinstance(index_payload, Mapping):
        raise SecIngestError("company_tickers.json was not a JSON object")
    master = SecurityMaster.from_company_tickers(index_payload, as_of=observed)
    if not master.symbols():
        raise SecIngestError("company_tickers.json contained no usable registrant")

    facts: dict[str, Mapping[str, Cell]] = {}
    errors: dict[str, str] = {}
    seen: set[str] = set()
    reader = _IssuerReader(client=client, cache_dir=cache_dir)

    for raw in symbols:
        symbol = normalize_symbol(raw)
        if symbol is None:
            errors[_error_key(raw)] = "not a security symbol"
            continue
        if symbol in seen:
            continue
        seen.add(symbol)

        record = master.get(symbol)
        if record is None:
            errors[symbol] = "not in the SEC company_tickers index"
            continue
        if record.cik is None:
            errors[symbol] = "the SEC index carries no CIK for this symbol"
            continue

        try:
            payload = await reader.read(record.cik)
            facts[symbol] = fact_ttm_summary(payload)
        except Exception as exc:  # noqa: BLE001 - one symbol, one error row
            errors[symbol] = str(exc) or type(exc).__name__

    return EngineDataset(
        fetched_at=fetched_at,
        security_master=master,
        facts=facts,
        errors=errors,
    )


__all__ = [
    "COMPANYFACTS_URL",
    "DEFAULT_USER_AGENT",
    "EngineDataset",
    "SecIngestError",
    "companyfacts_url",
    "fetch_companyfacts",
    "fetch_company_tickers",
    "fetch_submissions",
    "load_sec_dataset",
]
