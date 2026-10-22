"""Redaction for anything that reaches a log line (02 §3.11, Fase 2 gate 7).

The rule is "never log a credential", and the only reliable way to keep it is to
make the unsafe version impossible to write by accident:

* a provider key is registered in :data:`app.providers.registry.REGISTRY` as an
  **env-var name**; :func:`secret_values` reads those names and nothing else, so
  there is no list of secrets to keep in sync;
* a URL is reduced to ``scheme://host/path`` — query strings are where ``token=``
  and ``apiKey=`` live, and a fragment is never needed for diagnosis;
* :func:`redact` is the function a log site calls; it replaces every present
  secret value **and** the value of any credential-shaped query parameter, so a
  key we never saw in the environment (a caller-supplied one) is still scrubbed.

This is not a general-purpose scrubber and must not become one: it covers the
shapes this service can emit, and a log site that formats a raw URL is still a
bug — the tests assert the rendered text, not this function's intent.
"""

from __future__ import annotations

import os
import re
from typing import Iterable

from app.providers.registry import REGISTRY

REDACTED = "«redacted»"

#: Query parameter names that carry a credential in some provider's API.
_SENSITIVE_PARAMS = (
    "token",
    "apikey",
    "api_key",
    "key",
    "access_token",
    "auth",
    "secret",
    "password",
)

_QUERY_VALUE = re.compile(
    r"((?:" + "|".join(_SENSITIVE_PARAMS) + r")=)[^&\s\"']+", re.IGNORECASE
)

#: ``scheme://authority/path`` — everything before the query or fragment.
_URL = re.compile(
    r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*)://(?P<authority>[^/?#]*)(?P<path>[^?#]*)"
)


def secret_env_names() -> tuple[str, ...]:
    """Every credential env var the registry declares (one source, no drift)."""

    return tuple(sorted(spec.auth for spec in REGISTRY if spec.auth))


def secret_values() -> tuple[str, ...]:
    """Present values of those variables (never their names, never absent ones)."""

    values: list[str] = []
    for name in secret_env_names():
        value = (os.environ.get(name) or "").strip()
        if value:
            values.append(value)
    return tuple(values)


def redact(text: object, *, secrets: Iterable[str] | None = None) -> str:
    """Replace credential values and credential-bearing query parameters."""

    out = str(text)
    for value in secrets if secrets is not None else secret_values():
        if value and len(value) >= 4:
            out = out.replace(value, REDACTED)
    return _QUERY_VALUE.sub(lambda match: match.group(1) + REDACTED, out)


def safe_url(url: object) -> str:
    """``scheme://host/path`` with no query, no fragment and no userinfo.

    A provider URL is never a diagnostic: the provider name and the rung are, and
    both are logged instead. This helper exists so that if a URL must be logged
    it loses the only part that can carry a credential.

    Parsed with a regex rather than ``urllib.parse``: ``urllib`` is a banned
    transport import in ``app/`` (05 §3.4 rule 1), and the rule exists so that no
    module drifts into "just fetch it here".
    """

    match = _URL.match(str(url))
    if match is None:  # pragma: no cover - malformed URL
        return REDACTED
    authority = match.group("authority").rsplit("@", 1)[-1]
    return f"{match.group('scheme')}://{authority}{match.group('path')}"


__all__ = ["REDACTED", "redact", "safe_url", "secret_env_names", "secret_values"]
