"""A row provider over one ingested :class:`~app.engine.ingest.EngineDataset`.

The ingestion slice answers “what documents did we read”; the row assembler
answers “what does one instrument look like”; this adapter is the one-line
composition between them. It is deliberately synchronous and read-only: a
screen reads a dataset that was already loaded, and a request never triggers a
network fetch.

Rows follow the order of :meth:`SecurityMaster.records` (symbol order), so two
screens over the same dataset are comparable and pagination is deterministic.
A symbol whose facts fetch failed still gets an identity row; the failure stays
in :attr:`errors` and is never turned into a fabricated metric.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.engine.ingest import EngineDataset
from app.engine.model import Cell
from app.engine.rows import build_row


@dataclass(frozen=True, slots=True)
class DatasetRows:
    """The screen row provider backed by one ingested dataset.

    ``quotes`` is optional because identity + SEC facts are useful without a
    live quote. A quote is keyed by symbol and is handed to
    :func:`~app.engine.rows.build_row` unchanged; a quote that names another
    symbol raises the row assembler's ``unknown_symbol`` error rather than
    attaching the wrong price to an identity.
    """

    dataset: EngineDataset
    quotes: Mapping[str, Mapping[str, object]] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not isinstance(self.dataset, EngineDataset):
            raise TypeError(
                "DatasetRows needs an EngineDataset, got "
                f"{type(self.dataset).__name__}"
            )
        if not isinstance(self.quotes, Mapping):
            raise TypeError("quotes must be a mapping of symbol to quote fields")
        frozen: dict[str, Mapping[str, object]] = {}
        for symbol, quote in self.quotes.items():
            if not isinstance(symbol, str) or not isinstance(quote, Mapping):
                raise TypeError("quotes must map symbol strings to mappings")
            frozen[symbol.upper()] = MappingProxyType(dict(quote))
        object.__setattr__(self, "quotes", MappingProxyType(frozen))

    @property
    def errors(self) -> Mapping[str, str]:
        """The per-symbol ingestion failures, unchanged."""

        return self.dataset.errors

    def rows(self) -> tuple[dict[str, Cell], ...]:
        """Build one row per known identity; missing facts stay missing."""

        rows: list[dict[str, Cell]] = []
        for security in self.dataset.security_master.records():
            facts = self.dataset.facts.get(security.symbol, {})
            quote = self.quotes.get(security.symbol)
            rows.append(
                build_row(
                    security,
                    facts,
                    quote,
                    as_of=self.dataset.fetched_at,
                )
            )
        return tuple(rows)

    def row(self, symbol: str) -> dict[str, Cell] | None:
        """Build the row for ``symbol`` alone, or ``None`` when it is unknown.

        A single-instrument endpoint must not assemble the universe to read one
        company: :meth:`rows` is the screen's contract (every identity, symbol
        order), while this is the same build for one looked-up record. ``None``
        is the honest answer for a symbol the master does not hold — "no such
        instrument" is not an identity row with nothing on it — and the lookup
        keeps the master's own tolerance (case-insensitive, ``BRK.B`` is
        ``BRK-B``).
        """

        security = self.dataset.security_master.get(symbol)
        if security is None:
            return None
        return build_row(
            security,
            self.dataset.facts.get(security.symbol, {}),
            self.quotes.get(security.symbol),
            as_of=self.dataset.fetched_at,
        )


__all__ = ["DatasetRows"]
