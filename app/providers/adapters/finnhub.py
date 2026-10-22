"""Finnhub ``/quote`` — the T1 rung (02 §3.4 L4).

T1 means "free but needs a key and has a quota". Two consequences this adapter
implements and the ladder relies on:

* the key is read from ``$FINNHUB_API_KEY`` **inside the adapter**, never from the
  spec, the database or a log line (02 §3.11). Finnhub takes it as the ``token``
  query parameter, so the URL is credential-bearing: it is never logged and it
  never reaches an attempt record — the ladder keeps bytes, not URLs;
* a missing key is an ``authwall`` here and a ``skipped`` rung in the ladder:
  ``is_available()`` runs first, so this branch is only reached if the
  environment changed mid-walk.

Finnhub answers ``{"c": 0, "pc": 0, "t": 0}`` for a symbol it does not know.
All-zero is ``empty`` (a fact: no quote), not a price of zero — the exact
misread that makes a watchlist look like a portfolio of bankruptcies.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from app.contracts import Capability
from app.providers.adapters._support import (
    HttpAdapter,
    as_float,
    envelope_verdict,
    json_of,
    observation,
    price_unit,
)
from app.providers.base import InstrumentRef, Verdict

HOST = "https://finnhub.io"
QUOTE_PATH = "/api/v1/quote"

#: Environment variable holding the credential — the same name the registry's
#: ``ProviderSpec.auth`` carries.
KEY_ENV = "FINNHUB_API_KEY"

#: Fields a stock quote carries; used by the ladder to skip a useless rung.
QUOTE_FIELDS = ("c", "pc", "o", "h", "l", "t")


class FinnhubAdapter(HttpAdapter):
    """Keyed, realtime, trust 2 (vendor); quota-limited."""

    name = "finnhub"
    trust = 2
    capabilities = frozenset({Capability.QUOTE, Capability.FUNDAMENTALS})

    def api_key(self) -> str | None:
        value = os.environ.get(KEY_ENV)
        return value.strip() if value and value.strip() else None

    def url(self, instrument: InstrumentRef) -> str:
        key = self.api_key()
        symbol = instrument.symbol.upper()
        if not key:
            # No credential: build a keyless URL and let the verdict answer
            # ``authwall`` instead of raising from inside URL construction.
            return f"{HOST}{QUOTE_PATH}?symbol={symbol}"
        return f"{HOST}{QUOTE_PATH}?symbol={symbol}&token={key}"

    def request_headers(self, instrument: InstrumentRef) -> dict[str, str]:
        headers = super().request_headers(instrument)
        key = self.api_key()
        if key:
            # Also sent as a header: the query parameter is what Finnhub reads,
            # the header keeps the credential out of any URL echoed by a proxy.
            headers["x-finnhub-token"] = key
        return headers

    # -- verdict -----------------------------------------------------------

    def verdict(self, raw: Mapping[str, Any], instrument: InstrumentRef) -> Verdict:
        envelope = envelope_verdict(raw)
        if envelope is not None:
            return envelope
        if not self.api_key():
            return Verdict(False, "authwall", f"{KEY_ENV} is not set")
        payload = json_of(raw)
        if not isinstance(payload, dict):
            return Verdict(False, "schema_drift", "body is not a JSON object")
        missing = [name for name in ("c", "t") if name not in payload]
        if missing:
            return Verdict(False, "schema_drift", f"finnhub payload missing {missing}")
        price = as_float(payload.get("c"))
        prev = as_float(payload.get("pc"))
        stamp = as_float(payload.get("t"))
        if price is None or not stamp:
            return Verdict(False, "empty", "finnhub returned no quote for the symbol")
        if price == 0.0 and (prev in (None, 0.0)):
            return Verdict(False, "empty", "finnhub returned an all-zero quote")
        return Verdict(True, "ok")

    # -- parse -------------------------------------------------------------

    def parse(
        self, raw: Mapping[str, Any], instrument: InstrumentRef
    ) -> Sequence[Mapping[str, Any]]:
        payload = json_of(raw)
        if not isinstance(payload, dict):
            return []
        stamp_seconds = as_float(payload.get("t"))
        if not stamp_seconds:
            return []
        stamp = datetime.fromtimestamp(int(stamp_seconds), UTC)
        unit = price_unit(instrument.currency)
        out: list[dict[str, Any]] = []
        for field_name, key in (
            ("last_price", "c"),
            ("prev_close", "pc"),
            ("day_open", "o"),
            ("day_high", "h"),
            ("day_low", "l"),
        ):
            value = as_float(payload.get(key))
            if value is None:
                continue
            out.append(
                observation(
                    instrument,
                    field_name,
                    value_num=value,
                    unit=unit,
                    source=self.name,
                    trust=self.trust,
                    as_of=stamp,
                )
            )
        out.append(
            observation(
                instrument,
                "quote_as_of",
                value_text=stamp.isoformat(timespec="seconds"),
                unit="text",
                source=self.name,
                trust=self.trust,
                as_of=stamp,
            )
        )
        return out


__all__ = ["HOST", "KEY_ENV", "QUOTE_FIELDS", "FinnhubAdapter"]
