"""Yahoo Finance ``/v8/finance/chart`` — the T0 QUOTE rung (01-providers §Yahoo).

The endpoint is unofficial and answers ``200`` with three different bodies:

* a complete ``chart.result[0].meta`` (a quote);
* ``{"chart": {"result": null, "error": null}}`` for "nothing here" (``empty``);
* a consent/challenge HTML page (``blocked``, see ``_support``).

It also changes shape without changing the status code — ``meta`` losing
``regularMarketPrice`` is the canonical ``schema_drift`` of this project, and it
must never be read as "a quote of zero".

One request per instrument (02 §3.4 rule 4: fewest requests). The ``meta`` block
carries the day's OHLC and the timestamp, so asking for bars as well would be a
second request for data already in hand.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from app.contracts import Capability
from app.providers.adapters._support import (
    _MISSING,
    HttpAdapter,
    as_float,
    envelope_verdict,
    json_of,
    observation,
    price_unit,
)
from app.providers.base import InstrumentRef, Verdict

HOST = "https://query1.finance.yahoo.com"
CHART_PATH = "/v8/finance/chart/{symbol}"

#: Fields this adapter can fill from the ``meta`` block. Declared so the ladder
#: can skip a rung that cannot serve the asked field set.
META_FIELDS: frozenset[str] = frozenset(
    {
        "last_price",
        "prev_close",
        "day_open",
        "day_high",
        "day_low",
        "day_volume",
        "quote_as_of",
        "currency",
    }
)


class YahooAdapter(HttpAdapter):
    """Unofficial but free; trust 3 (aggregator), delay "realtime-ish"."""

    name = "yahoo"
    trust = 3
    capabilities = frozenset({Capability.QUOTE, Capability.EOD_BARS})

    def url(self, instrument: InstrumentRef) -> str:
        symbol = instrument.symbol.replace(" ", "%20")
        return (
            f"{HOST}{CHART_PATH.format(symbol=symbol)}"
            "?interval=1m&range=1d&includePrePost=false&events=div%2Csplit"
        )

    # -- verdict -----------------------------------------------------------

    def _meta(self, raw: Mapping[str, Any]) -> Any:
        payload = json_of(raw)
        if not isinstance(payload, dict):
            return _MISSING
        chart = payload.get("chart")
        if not isinstance(chart, dict):
            return _MISSING
        result = chart.get("result")
        if isinstance(result, list) and result and isinstance(result[0], dict):
            meta = result[0].get("meta")
            return meta if isinstance(meta, dict) else _MISSING
        return _MISSING

    def verdict(self, raw: Mapping[str, Any], instrument: InstrumentRef) -> Verdict:
        envelope = envelope_verdict(raw)
        if envelope is not None:
            return envelope

        payload = json_of(raw)
        if not isinstance(payload, dict):
            return Verdict(False, "schema_drift", "body is not a JSON object")
        chart = payload.get("chart")
        if not isinstance(chart, dict):
            return Verdict(False, "schema_drift", "chart envelope missing")

        error = chart.get("error")
        if error:
            detail = str(error)
            if "not found" in detail.lower() or "no data" in detail.lower():
                return Verdict(False, "not_found", detail)
            return Verdict(False, "schema_drift", f"chart.error: {detail}")

        result = chart.get("result")
        if result is None or result == []:
            # Correct schema, no data: a symbol with no quote, not a broken one.
            return Verdict(False, "empty", "chart.result is null")
        if not isinstance(result, list) or not isinstance(result[0], dict):
            return Verdict(False, "schema_drift", "chart.result[0] is not an object")

        meta = result[0].get("meta")
        if not isinstance(meta, dict):
            return Verdict(False, "schema_drift", "chart.result[0].meta missing")
        if meta.get("regularMarketPrice") is None:
            # The promised path is absent while the envelope looks healthy.
            return Verdict(False, "schema_drift", "meta.regularMarketPrice missing")
        if meta.get("regularMarketTime") is None:
            return Verdict(False, "schema_drift", "meta.regularMarketTime missing")
        return Verdict(True, "ok")

    # -- parse -------------------------------------------------------------

    def parse(
        self, raw: Mapping[str, Any], instrument: InstrumentRef
    ) -> Sequence[Mapping[str, Any]]:
        meta = self._meta(raw)
        if meta is _MISSING:
            return []
        stamp = datetime.fromtimestamp(int(meta["regularMarketTime"]), UTC)
        currency = str(meta.get("currency") or instrument.currency)
        unit = price_unit(currency)
        out: list[dict[str, Any]] = []

        def cell(field_name: str, value: Any, *, kind: str = "num") -> None:
            if kind == "text":
                out.append(
                    observation(
                        instrument,
                        field_name,
                        value_text=str(value),
                        unit="text",
                        source=self.name,
                        trust=self.trust,
                        as_of=stamp,
                    )
                )
                return
            number = as_float(value)
            if number is None:
                return
            out.append(
                observation(
                    instrument,
                    field_name,
                    value_num=number,
                    unit=unit,
                    source=self.name,
                    trust=self.trust,
                    as_of=stamp,
                )
            )

        cell("last_price", meta.get("regularMarketPrice"))
        cell("prev_close", meta.get("chartPreviousClose", meta.get("previousClose")))
        cell("day_open", meta.get("regularMarketOpen"))
        cell("day_high", meta.get("regularMarketDayHigh"))
        cell("day_low", meta.get("regularMarketDayLow"))
        cell("day_volume", meta.get("regularMarketVolume"), kind="num")
        cell(
            "quote_as_of",
            stamp.isoformat(timespec="seconds"),
            kind="text",
        )
        # ``currency`` is an identity column; recording it as an observation keeps
        # the provenance of "USD" attached to the provider that said so.
        cell("currency", currency, kind="text")
        return out


__all__ = ["HOST", "META_FIELDS", "YahooAdapter"]
