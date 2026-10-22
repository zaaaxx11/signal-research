"""Shared adapter plumbing: one request shape, one verdict order (02 §3.4).

Adapters may import **only** this module, ``app.providers.base``, and the HTTP
seam as a module attribute (``from app.providers import http as provider_http``).
That last detail is the seam: ``http.build_client`` is looked up at call time so
the Phase-2 suite can install a ``MockTransport`` by patching the attribute.

A verdict is computed from the response **content** in a fixed order, because
order is the contract:

1. ``401/403`` → ``authwall``; ``404`` → ``not_found``; ``429`` → ``throttled``;
   ``5xx`` → ``5xx``; a 3xx (we never follow redirects) → ``blocked``;
2. an HTML body on a 200 → ``blocked`` (a consent page is not a quote);
3. a body that is not JSON where JSON was promised → ``schema_drift``;
4. provider-specific path checks → ``empty`` / ``schema_drift``.

Step 3 and 4 are the ones status codes cannot express, and the reason
``schema_drift`` exists at all.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from app.contracts import Capability
from app.providers import http as provider_http
from app.providers.base import InstrumentRef, ProviderError, Verdict

#: A browser-ish UA: several vendors serve a challenge page to library clients.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

#: Capabilities a single adapter can serve. Adapters that cannot serve the asked
#: capability raise rather than returning a body the ladder would misparse.
RawResponse = dict[str, Any]

_HTML_PREFIXES = (b"<", b"<!doctype", b"\xef\xbb\xbf<")


def utc_now() -> datetime:
    return datetime.now(UTC)


def text_of(raw: Mapping[str, Any]) -> str:
    """Decode a body leniently; adapters must never crash on a mojibake header."""

    body = raw.get("body") or b""
    if isinstance(body, str):  # pragma: no cover - adapters always store bytes
        return body
    return bytes(body).decode("utf-8", errors="replace")


def looks_like_html(raw: Mapping[str, Any]) -> bool:
    """Content-type says HTML, or the bytes do. Both are checked on purpose."""

    headers = {str(key).lower(): str(value).lower() for key, value in (raw.get("headers") or {}).items()}
    if "html" in headers.get("content-type", ""):
        return True
    body = raw.get("body") or b""
    if isinstance(body, str):  # pragma: no cover
        body = body.encode("utf-8", errors="ignore")
    return bytes(body).lstrip()[:16].lower().startswith(_HTML_PREFIXES)


def json_of(raw: Mapping[str, Any]) -> Any | None:
    """Parsed JSON, or ``None`` when the body is not JSON at all."""

    try:
        return json.loads(text_of(raw))
    except (ValueError, TypeError):
        return None


def envelope_verdict(raw: Mapping[str, Any]) -> Verdict | None:
    """The status/HTML verdict, or ``None`` when the body deserves a parse."""

    status = int(raw.get("status") or 0)
    if status in (401, 403):
        return Verdict(ok=False, kind="authwall", detail=f"status {status}")
    if status == 404:
        return Verdict(ok=False, kind="not_found", detail="status 404")
    if status == 429:
        return Verdict(ok=False, kind="throttled", detail="status 429")
    if 300 <= status < 400:
        return Verdict(ok=False, kind="blocked", detail=f"redirect {status} (not followed)")
    if 500 <= status < 600:
        return Verdict(ok=False, kind="5xx", detail=f"status {status}")
    if status != 200:
        return Verdict(ok=False, kind="5xx" if status >= 500 else "schema_drift", detail=f"status {status}")
    if looks_like_html(raw):
        return Verdict(ok=False, kind="blocked", detail="200 with an HTML body")
    return None


def observation(
    instrument: InstrumentRef,
    field_name: str,
    *,
    value_num: float | None = None,
    value_text: str | None = None,
    unit: str,
    source: str,
    trust: int,
    as_of: datetime,
    fetched_at: datetime | None = None,
    verdict: str = "ok",
) -> dict[str, Any]:
    """One observation document (``app.contracts.Observation`` shape)."""

    stamp = as_of.astimezone(UTC).isoformat(timespec="seconds")
    return {
        "instrument_id": instrument.instrument_id,
        "field": field_name,
        "value_num": value_num,
        "value_text": value_text,
        "unit": unit,
        "source": source,
        "source_id": None,
        "as_of": stamp,
        "fetched_at": (fetched_at or utc_now()).astimezone(UTC).isoformat(timespec="seconds"),
        "confidence": 1.0,
        "verdict": verdict,
        "trust": trust,
        "raw_sha256": None,
        "job_id": None,
    }


def price_unit(currency: str | None) -> str:
    """``price:<ISO-4217>``; a missing currency is USD, the venue default here."""

    code = (currency or "USD").strip().upper()
    return f"price:{code[:3] or 'USD'}"


def as_float(value: Any) -> float | None:
    """Provider numbers arrive as ``"231.50"``, ``"$231.50"``, ``"-1,234.5"`` or None."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("$", "").replace("%", "")
    if not text or text.upper() in {"N/D", "NA", "NULL", "NONE", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


class HttpAdapter:
    """Base class: build URL → one request → raw response. Verdict/parse follow."""

    name: str = ""
    #: Trust class for every observation this adapter emits (02 §4.6).
    trust: int = 3
    #: Which capabilities this adapter actually implements.
    capabilities: frozenset[Capability] = frozenset({Capability.QUOTE})
    accept: str = "application/json"

    def __init__(self, *, timeout_s: float = 20.0) -> None:
        self.timeout_s = timeout_s

    # -- request -----------------------------------------------------------

    def url(self, instrument: InstrumentRef) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    def request_headers(self, instrument: InstrumentRef) -> dict[str, str]:
        """Headers this adapter sends. Anonymous rungs must not add Auth here."""

        return {"user-agent": USER_AGENT, "accept": self.accept}

    async def fetch(
        self, capability: Capability, instrument: InstrumentRef, fields: set[str]
    ) -> RawResponse:
        """One request. Bytes are read fully so the meter can count them."""

        if capability not in self.capabilities:
            raise ProviderError(
                Verdict(
                    ok=False,
                    kind="schema_drift",
                    detail=f"{self.name} does not implement {capability.value}",
                )
            )
        url = self.url(instrument)
        headers = self.request_headers(instrument)
        async with provider_http.build_client(
            timeout_s=self.timeout_s, headers=headers
        ) as client:
            response = await client.get(url)
            body = response.content
        return {
            "status": int(response.status_code),
            "headers": {str(key).lower(): str(value) for key, value in response.headers.items()},
            "body": body,
            "url": url,
            "capability": capability.value,
        }

    # -- interpretation ----------------------------------------------------

    def verdict(self, raw: Mapping[str, Any], instrument: InstrumentRef) -> Verdict:
        """Content verdict; subclasses add the provider-specific paths."""

        return envelope_verdict(raw) or Verdict(ok=True, kind="ok")

    def parse(
        self, raw: Mapping[str, Any], instrument: InstrumentRef
    ) -> Sequence[Mapping[str, Any]]:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- helpers for subclasses -------------------------------------------

    @staticmethod
    def _require(mapping: Any, path: str) -> Any:
        """Walk a dotted path, returning ``_MISSING`` when any hop is absent."""

        current = mapping
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                return _MISSING
            current = current[part]
        return current


class _Missing:
    """Sentinel: a ``None`` value is data, an absent path is drift."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<missing>"


_MISSING = _Missing()


__all__ = [
    "HttpAdapter",
    "RawResponse",
    "USER_AGENT",
    "_MISSING",
    "as_float",
    "envelope_verdict",
    "json_of",
    "looks_like_html",
    "observation",
    "price_unit",
    "text_of",
    "utc_now",
]
