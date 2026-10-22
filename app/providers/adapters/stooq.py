"""Stooq CSV quotes — the T0 rung after Yahoo (02 §3.4 L2).

``/q/l/`` answers a five-line CSV for one symbol and needs no key. It is
delayed/EOD by nature, which is why it sits behind Yahoo even though both are
``T0``: same price, better freshness first.

The CSV is parsed by header name, never by column index — Stooq has changed the
column order before, and a positional parser would silently read the volume as
the close price (the ``unit_anomaly`` class this project exists to avoid).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from app.contracts import Capability
from app.providers.adapters._support import (
    HttpAdapter,
    as_float,
    envelope_verdict,
    observation,
    price_unit,
    text_of,
)
from app.providers.base import InstrumentRef, Verdict

HOST = "https://stooq.com"
QUOTE_PATH = "/q/l/"

#: Stooq's ``.us`` suffix is the US listing; a venue suffix already present is kept.
_SUFFIX = {"XNAS": "us", "XNYS": "us", "XASE": "us", "XWAR": ""}

CODES = ("Symbol", "Date", "Time", "Open", "High", "Low", "Close", "Volume")


def _rows(text: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return []
    header = [cell.strip() for cell in lines[0].split(",")]
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        cells = [cell.strip() for cell in line.split(",")]
        if len(cells) != len(header):
            continue
        rows.append(dict(zip(header, cells, strict=False)))
    return rows


class StooqAdapter(HttpAdapter):
    """Free CSV endpoint; trust 3, ``delay="delayed"`` (see registry)."""

    name = "stooq"
    trust = 3
    capabilities = frozenset({Capability.QUOTE, Capability.EOD_BARS})
    accept = "text/csv,text/plain"

    def url(self, instrument: InstrumentRef) -> str:
        symbol = instrument.symbol.lower()
        suffix = _SUFFIX.get(instrument.mic, "")
        if suffix and "." not in symbol:
            symbol = f"{symbol}.{suffix}"
        return f"{HOST}{QUOTE_PATH}?s={symbol}&f=sd2t2ohlcv&h&e=csv"

    # -- verdict -----------------------------------------------------------

    def verdict(self, raw: Mapping[str, Any], instrument: InstrumentRef) -> Verdict:
        envelope = envelope_verdict(raw)
        if envelope is not None:
            return envelope
        rows = _rows(text_of(raw))
        if not rows:
            return Verdict(False, "empty", "stooq returned no data row")
        row = rows[0]
        if not {"Close", "Date"} <= set(row):
            missing = sorted({"Close", "Date"} - set(row))
            return Verdict(False, "schema_drift", f"stooq CSV missing {missing}")
        if as_float(row.get("Close")) is None:
            return Verdict(False, "empty", "stooq Close is N/D")
        return Verdict(True, "ok")

    # -- parse -------------------------------------------------------------

    def parse(
        self, raw: Mapping[str, Any], instrument: InstrumentRef
    ) -> Sequence[Mapping[str, Any]]:
        rows = _rows(text_of(raw))
        if not rows:
            return []
        row = rows[0]
        stamp = _stamp(row)
        if stamp is None:
            return []
        unit = price_unit(instrument.currency)
        out: list[dict[str, Any]] = []
        for field_name, column in (
            ("last_price", "Close"),
            ("day_open", "Open"),
            ("day_high", "High"),
            ("day_low", "Low"),
            ("day_volume", "Volume"),
        ):
            value = as_float(row.get(column))
            if value is None:
                continue
            out.append(
                observation(
                    instrument,
                    field_name,
                    value_num=value,
                    unit=unit if column != "Volume" else "shares",
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


def _stamp(row: Mapping[str, str]) -> datetime | None:
    """``Date`` + ``Time`` are separate columns in Stooq's ``sd2t2ohlcv`` mode."""

    date_text = (row.get("Date") or "").strip()
    time_text = (row.get("Time") or "").strip()
    for candidate in (f"{date_text} {time_text}", date_text):
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(candidate.strip(), pattern).replace(tzinfo=UTC)
            except ValueError:
                continue
    return None


__all__ = ["CODES", "HOST", "StooqAdapter"]
