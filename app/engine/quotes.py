"""Bridge the realtime quote engine to the row assembler.

:class:`~app.market.quote.QuoteTick` and
:class:`~app.engine.rows.build_row` describe the same observation with two
different vocabularies. The important difference is ``change``: the quote engine
carries the absolute move (``last - prev_close``) while the catalog's ``change``
column is a **percentage**, so copying the field name directly would publish a
dollar amount as percent points. This adapter names that conversion explicitly
and leaves every missing field as ``None`` for the row assembler to omit.

No network, no clock and no state: a tick in, a mapping out.

:data:`QUOTE_FIELDS` and :func:`snapshot_from_ticks` are the other half of the
bridge: which catalog keys a quote actually observes, and how one polled batch
becomes the freshness sidecar a screen document carries. Both are pure, so a
route can report *what it has* without consulting a clock of its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.market.quote import QuoteTick

__all__ = [
    "MAX_QUOTE_REFRESH_SYMBOLS",
    "QUOTE_FIELDS",
    "QuoteSnapshot",
    "quote_fields",
    "snapshot_from_ticks",
]

#: The catalog keys whose value a quote rung observes. A query "uses quote
#: fields" — and so depends on freshness — when any filter field or the sort
#: field is in this set. ``market_cap`` is deliberately absent: the row derives
#: it from price × shares, so it is a derived value, not an observed print.
QUOTE_FIELDS: frozenset[str] = frozenset(
    {"price", "change", "volume", "prev_close"}
)

#: A quote-based filter refreshes its candidate set *before* it decides
#: membership, which is only safe while that set is small: the loaded SEC
#: identity master can hold 10k+ symbols, and one screen request must not turn
#: into a bulk quote crawl. Above this many symbols the route keeps the
#: pre-refresh snapshot and says ``quote_filter_capped`` instead of claiming the
#: filter saw fresh prices. A persisted quote universe (R3) is the real fix.
MAX_QUOTE_REFRESH_SYMBOLS = 100


def quote_fields(tick: QuoteTick) -> dict[str, object]:
    """Project one judged tick into the row assembler's quote vocabulary.

    ``price`` is ``last``; ``change`` is ``change_pct`` (the catalog unit is
    ``pct``); ``prev_close`` and ``volume`` pass through. ``market_cap`` is not
    invented here: the quote engine does not observe it, and the row's derived
    market capitalisation can fill that gap from price × shares.

    A missing ``prev_close`` therefore yields a missing ``change`` as well,
    rather than a fabricated flat day. ``as_of`` is the **exchange** timestamp,
    not the receive time: a cached tick must not look fresher because it was
    polled again.
    """

    if not isinstance(tick, QuoteTick):
        raise TypeError(f"quote_fields needs a QuoteTick, got {type(tick).__name__}")

    return {
        "symbol": tick.symbol,
        "price": tick.last,
        "prev_close": tick.prev_close,
        "change": tick.change_pct,
        "volume": tick.volume,
        "as_of": tick.ts_exchange.isoformat(),
    }


#: The only scopes a refresh may claim. ``none`` means no fetch was attempted,
#: ``page`` means the refresh targeted the symbols of the returned page, and
#: ``candidates`` means it targeted the whole loaded dataset **before** a
#: quote-based filter or sort ran.
REFRESH_SCOPES: frozenset[str] = frozenset({"none", "page", "candidates"})


@dataclass(frozen=True, slots=True)
class QuoteSnapshot:
    """Sidecar metadata for the quotes attached to one screen document.

    The row already carries the price in a :class:`~app.engine.model.Cell` with
    its own provenance. This object answers the questions the row cannot: *was
    a fetch attempted*, *over which set*, *how fresh is the newest print*, and
    *did a filter run against a snapshot instead of a refreshed value*. It
    deliberately carries no provider name: a source label is audit metadata,
    not a presentation field.

    ``stale`` is the quote engine's own verdict (``any`` returned tick was
    stamped stale), not a second clock computed here. ``as_of`` is the newest
    exchange timestamp among the returned ticks, so a partial batch cannot look
    newer than its oldest member by accident — it reports the best print it
    actually has.
    """

    refreshed: bool = False
    scope: str = "none"
    requested: int = 0
    returned: int = 0
    as_of: str | None = None
    stale: bool | None = None
    reason: str | None = None
    filter_used_snapshot: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.refreshed, bool):
            raise TypeError(f"refreshed must be a bool, got {self.refreshed!r}")
        scope = self.scope.strip().lower() if isinstance(self.scope, str) else ""
        if scope not in REFRESH_SCOPES:
            raise ValueError(
                f"scope must be one of {sorted(REFRESH_SCOPES)}, got {self.scope!r}"
            )
        object.__setattr__(self, "scope", scope)
        for name in ("requested", "returned"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer, got {value!r}")
        if self.returned > self.requested:
            raise ValueError("returned cannot exceed requested")
        if self.refreshed and scope == "none":
            raise ValueError("a refreshed snapshot must name its scope")
        if self.stale is not None and not isinstance(self.stale, bool):
            raise TypeError(f"stale must be a bool or None, got {self.stale!r}")
        if self.filter_used_snapshot and not isinstance(self.filter_used_snapshot, bool):
            raise TypeError("filter_used_snapshot must be a bool")
        if self.as_of is not None:
            text = self.as_of.strip() if isinstance(self.as_of, str) else ""
            if not text:
                raise ValueError("as_of must be non-blank text or None")
            object.__setattr__(self, "as_of", text)
        if self.reason is not None:
            text = self.reason.strip() if isinstance(self.reason, str) else ""
            if not text:
                raise ValueError("reason must be non-blank text or None")
            object.__setattr__(self, "reason", text)

    def document(self) -> dict[str, Any]:
        """Project the snapshot into the JSON shape of the screen document."""

        return {
            "refreshed": self.refreshed,
            "scope": self.scope,
            "requested": self.requested,
            "returned": self.returned,
            "as_of": self.as_of,
            "stale": self.stale,
            "reason": self.reason,
            "filter_used_snapshot": self.filter_used_snapshot,
        }


def snapshot_from_ticks(
    ticks: Mapping[str, QuoteTick | None],
    *,
    scope: str,
    requested: int,
    reason: str | None = None,
    filter_used_snapshot: bool = False,
) -> QuoteSnapshot:
    """Build the sidecar for one attempted refresh from the batch it returned.

    ``ticks`` is the engine's own answer, including the ``None`` values that
    mean "nobody had this symbol". Those are not prices, so ``returned`` counts
    only real ticks and ``refreshed`` is true only when at least one exists:
    a batch of four ``None`` values must not read as a refreshed page.

    ``as_of`` is the newest ``ts_exchange`` in the batch — never ``ts_received``
    and never the wall clock — so a cached print cannot be made to look fresh by
    polling it again. ``stale`` is the engine's verdict over the same set, and
    stays ``None`` when there is no tick to judge.

    ``requested`` is passed in rather than derived from ``ticks`` because a
    batch that raised before answering still has a known target set: the caller
    reports what it asked for, not what came back.
    """

    returned_ticks = tuple(
        tick for tick in ticks.values() if isinstance(tick, QuoteTick)
    )
    newest = max((tick.ts_exchange for tick in returned_ticks), default=None)
    return QuoteSnapshot(
        refreshed=bool(returned_ticks),
        scope=scope,
        requested=requested,
        returned=len(returned_ticks),
        as_of=None if newest is None else newest.isoformat(),
        stale=(
            None
            if not returned_ticks
            else any(tick.stale for tick in returned_ticks)
        ),
        reason=reason,
        filter_used_snapshot=filter_used_snapshot,
    )
