"""Client identity: a fingerprint, never a key (02 §3.11).

The raw API key never reaches the database, a log line or an error message.
What is stored is ``sha256:<hex>`` of the key — the same shape as zcrapers'
``public_key_id``: enough to scope ownership, useless if the row leaks.

Phase 0 has no key provisioning, so a request without ``X-API-Key`` maps to the
fingerprint of the empty string: one shared anonymous namespace. That is a
deliberate, documented limitation (see 09-phase0-green.md), not an accident: the
alternative — rejecting anonymous calls — would make the Phase-0 smoke path
require a credential store that does not exist yet.
"""

from __future__ import annotations

import hashlib

from fastapi import Request

API_KEY_HEADER = "x-api-key"
ANONYMOUS = ""


class ApiError(Exception):
    """An HTTP error rendered with the zcrapers envelope ``{error, errorMessage}``."""

    def __init__(self, status_code: int, error: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.message = message


def fingerprint(raw_key: str | None) -> str:
    """``sha256:<hex>`` of the presented key; the key itself is not retained."""

    return "sha256:" + hashlib.sha256((raw_key or ANONYMOUS).encode("utf-8")).hexdigest()


def owner_fingerprint(request: Request) -> str:
    """Fingerprint of the caller of ``request``."""

    return fingerprint(request.headers.get(API_KEY_HEADER))


__all__ = ["ANONYMOUS", "API_KEY_HEADER", "ApiError", "fingerprint", "owner_fingerprint"]
