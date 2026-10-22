"""Nasdaq ``/api/quote`` — the T0 delayed-15 rung (02 §3.4 L3, US only).

``api.nasdaq.com`` serves a JSON envelope whose numbers are **strings with
currency symbols** (``"$231.50"``) and whose timestamp is a human sentence
(``"Oct 22, 2026 1:58 PM ET"``). Both are parsed strictly:

* an unparseable price is not a price (``schema_drift``, never ``0.0``);
* an unparseable timestamp is ``schema_drift`` too, because an observation
  without an ``as_of`` may not exist (02 §4.6).

The endpoint is US-only; the registry's ``supports`` predicate keeps it out of
the walk for an XIDX symbol instead of letting it answer "N/A" for a Jakarta
ticker.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

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

HOST = "https://api.nasdaq.com"
QUOTE_PATH = "/api/quote/{symbol}/info?assetclass=stocks"

#: Nasdaq renders Eastern time; the ET wall clock is what the string means.
_EASTERN = ZoneInfo("America/New_York")
_STAMP_FORMATS = ("%b %d, %Y %I:%M %p ET", "%b %d, %Y %I:%M %p")


class NasdaqAdapter(HttpAdapter):
    """Free, delayed 15 minutes, US listings only; trust 3."""

    name = "nasdaq"
    trust = 3
    capabilities = frozenset({Capability.QUOTE})

    def url(self, instrument: InstrumentRef) -> str:
        return f"{HOST}{QUOTE_PATH.format(symbol=instrument.symbol.upper())}"

    # -- verdict -----------------------------------------------------------

    def _primary(self, raw: Mapping[str, Any]) -> Any:
        payload = json_of(raw)
        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        primary = data.get("primaryData")
        return primary if isinstance(primary, dict) else None

    def verdict(self, raw: Mapping[str, Any], instrument: InstrumentRef) -> Verdict:
        envelope = envelope_verdict(raw)
        if envelope is not None:
            return envelope
        payload = json_of(raw)
        if not isinstance(payload, dict):
            return Verdict(False, "schema_drift", "body is not a JSON object")
        data = payload.get("data")
        if data is None:
            return Verdict(False, "empty", "data is null")
        if not isinstance(data, dict):
            return Verdict(False, "schema_drift", "data is not an object")
        primary = data.get("primaryData")
        if not isinstance(primary, dict):
            return Verdict(False, "schema_drift", "data.primaryData missing")
        if as_float(primary.get("lastSalePrice")) is None:
            return Verdict(False, "schema_drift", "primaryData.lastSalePrice missing")
        if _stamp(primary) is None:
            return Verdict(False, "schema_drift", "primaryData.lastTradeTimestamp unparseable")
        return Verdict(True, "ok")

    # -- parse -------------------------------------------------------------

    def parse(
        self, raw: Mapping[str, Any], instrument: InstrumentRef
    ) -> Sequence[Mapping[str, Any]]:
        primary = self._primary(raw)
        if primary is None:
            return []
        stamp = _stamp(primary)
        if stamp is None:
            return []
        unit = price_unit(instrument.currency)
        out: list[dict[str, Any]] = []
        price = as_float(primary.get("lastSalePrice"))
        if price is not None:
            out.append(
                observation(
                    instrument,
                    "last_price",
                    value_num=price,
                    unit=unit,
                    source=self.name,
                    trust=self.trust,
                    as_of=stamp,
                )
            )
        net_change = as_float(primary.get("netChange"))
        if net_change is not None:
            out.append(
                observation(
                    instrument,
                    "change_abs",
                    value_num=net_change,
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


def _stamp(primary: Mapping[str, Any]) -> datetime | None:
    """Parse ``"Oct 22, 2026 1:58 PM ET"`` into an aware UTC instant."""

    text = str(primary.get("lastTradeTimestamp") or "").strip()
    if not text:
        return None
    for pattern in _STAMP_FORMATS:
        try:
            naive = datetime.strptime(text, pattern)
        except ValueError:
            continue
        return naive.replace(tzinfo=_EASTERN).astimezone(UTC)
    return None


__all__ = ["HOST", "NasdaqAdapter"]
