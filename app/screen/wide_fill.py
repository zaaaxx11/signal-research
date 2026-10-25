"""Optional display-only enrichment for the 71-column cockpit table.

The bulk scan carries the parity metrics that can be filtered.  A stock page
also publishes cells such as ``Employees``, ``IPO`` and ``Insider Trans`` that
are useful on screen but are not part of the 22-cell fundamentals contract and
would add one request per symbol if the scan asked for them.  This module reads
those cells only when the caller explicitly asks for a wide page, keeps them in
a process-lifetime cache, and never turns a failed page into a fabricated zero.

The provider name is deliberately absent from the returned row: it is an
identity/display concern, not a column.  A page that cannot be read simply leaves
the fields it did not answer as ``None``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable, Mapping

from app.providers.adapters.finviz import WIDE_FIELD_LABELS, FinvizAdapter, parse_wide_metrics
from app.providers.base import Capability, InstrumentRef

logger = logging.getLogger("mee.screen.wide_fill")

#: Fields the wide table displays but the bulk scan does not own.
WIDE_FILL_FIELDS: tuple[str, ...] = tuple(WIDE_FIELD_LABELS)


def _body_text(raw: Mapping[str, Any]) -> str:
    body = raw.get("body", b"")
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")
    return str(body or "")


class WideFiller:
    """Fill display-only cells for one page, with a per-process value cache.

    ``adapter`` is injectable so tests can use a deterministic fake.  The
    default is the same Finviz adapter the fundamentals ladder uses, which means
    the egress order is unchanged: direct → Jina free → free proxy → paid.
    """

    def __init__(
        self,
        *,
        adapter: Any | None = None,
        concurrency: int = 6,
    ) -> None:
        self.adapter = adapter or FinvizAdapter()
        self.concurrency = max(1, int(concurrency))
        self._cache: dict[str, dict[str, Any]] = {}

    async def _one(self, symbol: str) -> dict[str, Any]:
        cached = self._cache.get(symbol)
        if cached is not None:
            return cached
        instrument = InstrumentRef.from_symbol(symbol)
        try:
            raw = await self.adapter.fetch(
                Capability.FUNDAMENTALS, instrument, set()
            )
            if int(raw.get("status", 0)) != 200:
                return {}
            values = parse_wide_metrics(_body_text(raw))
        except Exception as exc:  # noqa: BLE001 - display enrichment is optional
            logger.info("wide fill failed for %s: %s", symbol, exc)
            return {}
        if values:
            self._cache[symbol] = values
        return values

    async def fill(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return copied rows with missing wide cells filled when available."""

        result = [dict(row) for row in rows]
        semaphore = asyncio.Semaphore(self.concurrency)

        async def work(row: dict[str, Any]) -> None:
            symbol = str(row.get("symbol") or "").strip().upper()
            if not symbol:
                return
            async with semaphore:
                values = await self._one(symbol)
            for field, value in values.items():
                if field in WIDE_FILL_FIELDS and row.get(field) is None:
                    row[field] = value

        await asyncio.gather(*(work(row) for row in result))
        return result


__all__ = ["WIDE_FILL_FIELDS", "WideFiller"]
