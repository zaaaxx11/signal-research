"""Default production deep engine for the twenty displayed rows.

The screen's cheap half ranks the universe from one bulk scan; this is the
expensive half, and it exists behind one callable so the export bundle never
constructs a provider itself::

    deep_engine(symbols) -> rows with the 92 canonical columns

S0 pins the *contract* (injection point, one call, twenty symbols). What is
wired here is the honest minimum behind it: one ``quote`` walk per symbol
through :class:`app.providers.ladder.Ladder` (the same one the job worker uses),
projected onto the canonical columns. A cell the quote rung did not serve stays
``None`` — nothing is derived, defaulted or estimated, and ``data_quality`` is
computed by :func:`app.enrich.quality.finalize_row` from what actually arrived.
The fundamentals/options/EOD rungs are S3 work behind this same callable; a
``top20`` table today is a quote-depth table, and the report says so.

The budget matters: twenty symbols × one capability is ~20 requests, versus the
~81k×4 a full-universe deep export would cost. That is why ``DEEP_ROWS`` is a
constant and not a parameter of the HTTP surface.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable, Iterable, Mapping, Sequence

from app.contracts import CANONICAL_COLUMNS_V1, Capability
from app.enrich.quality import finalize_row
from app.providers.base import InstrumentRef
from app.providers.cache import session_date

_COLUMNS: frozenset[str] = frozenset(CANONICAL_COLUMNS_V1)
#: ``quote_as_of`` dates the *data*; it feeds ``as_of_achieved`` rather than a
#: column of its own (a row without it fails the core-identity gate downstream).
_AS_OF_FIELD = "quote_as_of"


class ScreenDeepEngine:
    """Quote-depth enrichment for a batch of symbols (S0 default)."""

    def __init__(
        self,
        ladder_factory: Callable[..., Any],
        *,
        as_of: datetime | None = None,
        capabilities: Sequence[Capability] = (Capability.QUOTE,),
    ) -> None:
        self._ladder_factory = ladder_factory
        self._as_of = as_of
        self._capabilities = tuple(capabilities)

    async def __call__(self, symbols: Sequence[str]) -> list[dict[str, Any]]:
        return await self.enrich(symbols)

    async def enrich(self, symbols: Sequence[str]) -> list[dict[str, Any]]:
        """One canonical row per symbol the ladder could quote, in input order."""

        moment = self._as_of or datetime.now(UTC)
        ladder = (
            self._ladder_factory(as_of=self._as_of)
            if self._as_of is not None
            else self._ladder_factory()
        )

        rows: list[dict[str, Any]] = []
        for symbol in symbols:
            instrument = InstrumentRef.from_symbol(str(symbol))
            merged: list[Mapping[str, Any]] = []
            rung: str | None = None
            for capability in self._capabilities:
                result = await ladder.fetch(capability, instrument, set(_COLUMNS))
                if not result.verdict.ok or not result.observations:
                    continue
                merged.extend(result.observations)
                if rung is None:
                    rung = result.rung
            if not merged:
                # No observation is an empty answer, not a row of nulls: a
                # twenty-row table padded with blanks is worse than nineteen
                # rows and an honest count.
                continue
            rows.append(self._row(instrument, merged, rung=rung, moment=moment))
        return rows

    @staticmethod
    def _row(
        instrument: InstrumentRef,
        observations: Iterable[Mapping[str, Any]],
        *,
        rung: str | None,
        moment: datetime,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "instrument_id": instrument.instrument_id,
            "symbol": instrument.symbol,
            "mic": instrument.mic,
            "asset_class": instrument.asset_class,
            "currency": instrument.currency,
            "session_date": session_date(moment),
            "primary_source": rung,
        }
        for observation in observations:
            field = str(observation.get("field") or "")
            if not field:
                continue
            value = (
                observation.get("value_num")
                if observation.get("value_num") is not None
                else observation.get("value_text")
            )
            if value is None:
                continue
            if field == _AS_OF_FIELD:
                row["as_of_achieved"] = str(value)
            elif field in _COLUMNS:
                row[field] = value
        return finalize_row(row)


def build_screen_deep_engine(ladder_factory: Callable[..., Any]) -> ScreenDeepEngine:
    """The runtime's default deep engine (one construction path, like ``ladder``)."""

    return ScreenDeepEngine(ladder_factory)


__all__ = ["ScreenDeepEngine", "build_screen_deep_engine"]
