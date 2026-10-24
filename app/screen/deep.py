"""Default production deep engine for the twenty displayed rows.

The screen's cheap half ranks the universe from one bulk scan; this is the
expensive half, and it exists behind one callable so the export bundle never
constructs a provider itself::

    deep_engine(symbols) -> rows with the 92 canonical columns

**Four capabilities, one row.** P9 wires the depth PLAN-screen.md §6 promised:
``QUOTE`` (P1) + ``EOD_BARS`` (P2/P3) + ``FUNDAMENTALS`` (P4) + ``OPTIONS``
(P5), one :class:`app.providers.ladder.Ladder` walk each, per symbol, through the
same ladder the job worker uses. The observations of all four are merged into
one canonical row; a cell no rung served stays ``None`` — nothing is derived,
defaulted or estimated, and ``data_quality`` is computed by
:func:`app.enrich.quality.finalize_row` from what actually arrived.

**The quote owns the row's existence.** A symbol whose ``QUOTE`` walk serves
nothing is not a row and is dropped, because a 92-column table with no price is
not a table. The three deeper capabilities fail in isolation: a blocked options
chain leaves the price, technicals and fundamentals cells standing (that is what
makes ``data_quality`` say ``partial`` instead of throwing the row away). The
quote rung is also the row's ``primary_source``; its ``quote_as_of`` is kept as
the row's ``quote_as_of`` cell and copied to ``as_of_achieved``.

**Budget.** Twenty symbols × four capabilities = **80 ladder walks**
(PLAN-screen.md §6: "deep top20: 20 simbol × 4 capability = ~80 request"). The
figure counts ``Ladder.fetch`` calls, which is the ceiling for a cold cache: the
ladder's L0 cache can answer a walk with zero transport requests, and a
provider rung that is unavailable is skipped with zero requests, so the actual
socket count is ``sum(result.requests)`` and may be lower. It is never higher
than one body request per non-options capability and two for options (expiry
list, then the dated chain) on Yahoo. A symbol whose quote serves nothing is
dropped after **one** walk — the three deeper walks are not spent on a row that
cannot be exported.

That budget is why ``DEEP_ROWS`` is a constant and not a parameter of the HTTP
surface: a full-universe deep export would cost ~81k × 4.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable, Iterable, Mapping, Sequence

from app.contracts import CANONICAL_COLUMNS_V1, Capability
from app.enrich.quality import finalize_row
from app.providers.base import InstrumentRef
from app.providers.cache import session_date

_COLUMNS: frozenset[str] = frozenset(CANONICAL_COLUMNS_V1)
#: ``quote_as_of`` dates the *data*: it feeds ``as_of_achieved`` **and** keeps
#: its own cell. The export table has a ``quote_as_of`` column, and the deep
#: pass has no later conflation phase to fill it from the observation stream.
_AS_OF_FIELD = "quote_as_of"


def _provider_ticker(symbol: str) -> str:
    """Yahoo writes US class shares with a hyphen (BRK.B -> BRK-B).

    Foreign suffixes (``2330.TW``) are kept exactly as the screen printed them;
    a single-letter suffix on a US style ticker is a share class, not an
    exchange suffix.
    """

    text = str(symbol).strip()
    if text.count(".") == 1:
        head, suffix = text.split(".", 1)
        if len(suffix) == 1 and suffix.isalpha():
            return f"{head}-{suffix}"
    return text

#: The four capabilities a top20 deep row is built from (PLAN-screen.md §6,
#: PLAN-market-enrichment-engine.md P2–P5). ``QUOTE`` is first because it decides
#: whether the row exists at all; the order is the walk order and the merge
#: precedence.
DEFAULT_CAPABILITIES: tuple[Capability, ...] = (
    Capability.QUOTE,
    Capability.EOD_BARS,
    Capability.FUNDAMENTALS,
    Capability.OPTIONS,
)


def _coerce_capabilities(capabilities: Iterable[Capability | str]) -> tuple[Capability, ...]:
    """Deduplicate, coerce, hoist ``QUOTE`` first, and refuse a rowless set.

    ``QUOTE`` is mandatory rather than silently prepended: a caller that asked
    for EOD/fundamentals/options only would get an empty table, and an empty
    table that looks like a successful export is exactly the failure mode this
    project refuses.
    """

    ordered: list[Capability] = []
    for raw in capabilities:
        capability = raw if isinstance(raw, Capability) else Capability(str(raw))
        if capability not in ordered:
            ordered.append(capability)
    if Capability.QUOTE not in ordered:
        raise ValueError(
            "ScreenDeepEngine requires Capability.QUOTE: a row without a quote is "
            "not a row, so the capability set cannot exclude it"
        )
    # The quote is the row's existence and the walk's first question, so it is
    # always walked first whatever order the caller declared. The result is
    # visible through :attr:`ScreenDeepEngine.capabilities`.
    ordered.remove(Capability.QUOTE)
    ordered.insert(0, Capability.QUOTE)
    return tuple(ordered)


class ScreenDeepEngine:
    """Four-rung enrichment for a batch of symbols (the top20 deep half)."""

    def __init__(
        self,
        ladder_factory: Callable[..., Any],
        *,
        as_of: datetime | None = None,
        capabilities: Sequence[Capability | str] = DEFAULT_CAPABILITIES,
    ) -> None:
        self._ladder_factory = ladder_factory
        self._as_of = as_of
        self._capabilities = _coerce_capabilities(capabilities)

    @property
    def capabilities(self) -> tuple[Capability, ...]:
        """The capabilities this engine walks, in order (quote first by default)."""

        return self._capabilities

    async def __call__(self, symbols: Sequence[str]) -> list[dict[str, Any]]:
        return await self.enrich(symbols)

    async def enrich(self, symbols: Sequence[str]) -> list[dict[str, Any]]:
        """One canonical row per symbol whose quote was served, in input order."""

        moment = self._as_of or datetime.now(UTC)
        ladder = (
            self._ladder_factory(as_of=self._as_of)
            if self._as_of is not None
            else self._ladder_factory()
        )
        fields = set(_COLUMNS)

        rows: list[dict[str, Any]] = []
        for symbol in symbols:
            display_symbol = str(symbol)
            instrument = InstrumentRef.from_symbol(_provider_ticker(display_symbol))
            served: dict[Capability, list[Mapping[str, Any]]] = {}
            rungs: dict[Capability, str | None] = {}

            for capability in self._capabilities:
                result = await ladder.fetch(capability, instrument, fields)
                if not result.verdict.ok or not result.observations:
                    if capability is Capability.QUOTE:
                        # No quote -> no row. Stop here instead of spending the
                        # three deeper walks on a symbol that cannot be
                        # exported; the row is dropped below either way.
                        break
                    # An "ok" whose parser produced nothing served nothing: the
                    # cell block stays absent while the row lives on.
                    continue
                served[capability] = list(result.observations)
                rungs[capability] = result.rung

            if not served.get(Capability.QUOTE):
                # No observation is an empty answer, not a row of nulls: a
                # twenty-row table padded with blanks is worse than nineteen
                # rows and an honest count. A failed EOD/fundamentals/options
                # walk, by contrast, never reaches this branch.
                continue

            # ``self._capabilities`` is quote-first (``_coerce_capabilities``), so
            # a cell quoted by two capabilities keeps the quote's value in
            # ``_row``'s first-wins merge.
            merged = [
                observation
                for capability in self._capabilities
                for observation in served.get(capability, ())
            ]
            row = self._row(
                instrument,
                merged,
                rung=rungs.get(Capability.QUOTE),
                moment=moment,
            )
            # Keep the screen's symbol spelling (BRK.B) while the provider saw
            # its own spelling (BRK-B); the export joins on the screen row.
            row["symbol"] = display_symbol
            rows.append(row)
        return rows

    @staticmethod
    def _row(
        instrument: InstrumentRef,
        observations: Iterable[Mapping[str, Any]],
        *,
        rung: str | None,
        moment: datetime,
    ) -> dict[str, Any]:
        """Merge four capabilities' observations into one 92-column row.

        First non-empty value wins, and the caller feeds quote observations
        first, so a deeper rung cannot overwrite a quoted cell. The row is full
        width — every canonical column exists — and a cell no rung served is
        ``None``: missing stays missing, never ``0`` and never a guess.
        """

        cells: dict[str, Any] = {}
        as_of: str | None = None
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
                if as_of is None:
                    as_of = str(value)
                cells.setdefault(field, str(value))
                continue
            if field in _COLUMNS and field not in cells:
                cells[field] = value

        row: dict[str, Any] = {column: None for column in CANONICAL_COLUMNS_V1}
        row.update(
            {
                "instrument_id": instrument.instrument_id,
                "symbol": instrument.symbol,
                "mic": instrument.mic,
                "asset_class": instrument.asset_class,
                "currency": instrument.currency,
                "session_date": session_date(moment),
                "primary_source": rung,
            }
        )
        row.update(cells)
        if as_of is not None:
            row["as_of_achieved"] = as_of
        return finalize_row(row)


def build_screen_deep_engine(
    ladder_factory: Callable[..., Any],
    *,
    capabilities: Sequence[Capability | str] = DEFAULT_CAPABILITIES,
) -> ScreenDeepEngine:
    """The runtime's default deep engine (one construction path, like ``ladder``).

    ``capabilities`` defaults to all four rungs PLAN-screen.md §6 budgets for, so
    the composition root's engine is the production one unless a caller says
    otherwise.
    """

    return ScreenDeepEngine(ladder_factory, capabilities=capabilities)


__all__ = [
    "DEFAULT_CAPABILITIES",
    "ScreenDeepEngine",
    "build_screen_deep_engine",
]
