"""Bridge the realtime quote engine to the row assembler.

:class:`~app.market.quote.QuoteTick` and
:class:`~app.engine.rows.build_row` describe the same observation with two
different vocabularies. The important difference is ``change``: the quote engine
carries the absolute move (``last - prev_close``) while the catalog's ``change``
column is a **percentage**, so copying the field name directly would publish a
dollar amount as percent points. This adapter names that conversion explicitly
and leaves every missing field as ``None`` for the row assembler to omit.

No network, no clock and no state: a tick in, a mapping out.
"""

from __future__ import annotations

from app.market.quote import QuoteTick

__all__ = ["quote_fields"]


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
