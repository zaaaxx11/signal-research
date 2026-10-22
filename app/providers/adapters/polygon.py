"""Polygon.io snapshot — the T2 (paid) rung (02 §3.4 L5).

Paid means two things the ladder enforces *before* the adapter is called: a
credit must be available (1 credit = 1 request) and the key must exist. Both
checks live in ``Ladder.fetch`` — an adapter that discovers "no credit" after
sending a request has already spent the money.

The snapshot endpoint returns everything a quote needs in one call
(``lastTrade``, ``prevDay``, ``day``), which is why the T2 rung is one request
rather than three.
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

HOST = "https://api.polygon.io"
SNAPSHOT_PATH = "/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}"

#: Environment variable holding the credential (mirrors ``ProviderSpec.auth``).
KEY_ENV = "POLYGON_API_KEY"

#: Nanoseconds per second: Polygon timestamps are ns since epoch.
_NS = 1_000_000_000


class PolygonAdapter(HttpAdapter):
    """Keyed, paid, trust 2 (vendor); US equities only."""

    name = "polygon"
    trust = 2
    capabilities = frozenset(
        {Capability.QUOTE, Capability.EOD_BARS, Capability.OPTIONS, Capability.FX}
    )

    def api_key(self) -> str | None:
        value = os.environ.get(KEY_ENV)
        return value.strip() if value and value.strip() else None

    def url(self, instrument: InstrumentRef) -> str:
        symbol = instrument.symbol.upper()
        key = self.api_key()
        base = f"{HOST}{SNAPSHOT_PATH.format(symbol=symbol)}"
        return f"{base}?apiKey={key}" if key else base

    # -- verdict -----------------------------------------------------------

    def _ticker(self, raw: Mapping[str, Any]) -> Any:
        payload = json_of(raw)
        if not isinstance(payload, dict):
            return None
        ticker = payload.get("ticker")
        return ticker if isinstance(ticker, dict) else None

    def verdict(self, raw: Mapping[str, Any], instrument: InstrumentRef) -> Verdict:
        envelope = envelope_verdict(raw)
        if envelope is not None:
            return envelope
        if not self.api_key():
            return Verdict(False, "authwall", f"{KEY_ENV} is not set")
        payload = json_of(raw)
        if not isinstance(payload, dict):
            return Verdict(False, "schema_drift", "body is not a JSON object")
        if str(payload.get("status") or "").upper() in {"NOT_FOUND", "ERROR"}:
            return Verdict(False, "not_found", f"polygon status {payload.get('status')!r}")
        ticker = payload.get("ticker")
        if ticker is None:
            return Verdict(False, "empty", "ticker is null")
        if not isinstance(ticker, dict):
            return Verdict(False, "schema_drift", "ticker is not an object")
        if _last_price(ticker) is None:
            return Verdict(False, "schema_drift", "no lastTrade/min/day close path")
        if _stamp(ticker) is None:
            return Verdict(False, "schema_drift", "no lastTrade.t/updated timestamp")
        return Verdict(True, "ok")

    # -- parse -------------------------------------------------------------

    def parse(
        self, raw: Mapping[str, Any], instrument: InstrumentRef
    ) -> Sequence[Mapping[str, Any]]:
        ticker = self._ticker(raw)
        if ticker is None:
            return []
        stamp = _stamp(ticker)
        price = _last_price(ticker)
        if stamp is None or price is None:
            return []
        unit = price_unit(instrument.currency)
        out: list[dict[str, Any]] = []
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
        for field_name, container, key in (
            ("prev_close", "prevDay", "c"),
            ("day_open", "day", "o"),
            ("day_high", "day", "h"),
            ("day_low", "day", "l"),
            ("day_volume", "day", "v"),
        ):
            node = ticker.get(container)
            if not isinstance(node, dict):
                continue
            value = as_float(node.get(key))
            if value is None:
                continue
            out.append(
                observation(
                    instrument,
                    field_name,
                    value_num=value,
                    unit=unit if key != "v" else "shares",
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


def _last_price(ticker: Mapping[str, Any]) -> float | None:
    """Price path, most specific first: last trade, then minute close, then day."""

    for container, key in (("lastTrade", "p"), ("min", "c"), ("day", "c")):
        node = ticker.get(container)
        if isinstance(node, dict):
            value = as_float(node.get(key))
            if value is not None:
                return value
    return None


def _stamp(ticker: Mapping[str, Any]) -> datetime | None:
    for container, key in (("lastTrade", "t"), (None, "updated")):
        node = ticker if container is None else ticker.get(container)
        if not isinstance(node, dict):
            continue
        raw = as_float(node.get(key))
        if not raw:
            continue
        return datetime.fromtimestamp(int(raw) / _NS, UTC)
    return None


__all__ = ["HOST", "KEY_ENV", "PolygonAdapter"]
