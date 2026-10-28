"""Keyless price registry: CNBC batch primary, Nasdaq per-symbol fallback.

Neither provider needs a key. CNBC's undocumented ``restQuote`` backend
answers up to 500 symbols in one request and publishes the previous close;
Nasdaq's ``/api/quote`` endpoint answers one symbol per request for US
listings. The registry therefore runs the cheap batch source first and spends
per-symbol requests only on the symbols it could not answer.

Three rules, inherited from :mod:`app.market.sources` and :mod:`app.market.bulk`:

* a symbol nobody answered is ``None`` — never a zero-priced quote;
* one source failing (HTTP error, broken body, timeout) costs that source its
  own pass, never the whole run;
* ``change_pct`` is derived from ``last``/``prev_close`` when the caller does
  not supply it, so a quote can never carry a change that disagrees with its
  own prices.

Timeouts are per source. The registry bounds each source call with
``asyncio.wait_for`` under that source's budget, and the per-symbol fallback
additionally bounds every single request inside its own pass — one hanging
symbol must not sink its neighbours, and a transport that ignores its own
read timeout must not hang the registry either.
"""

from __future__ import annotations

import asyncio
import inspect
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.market.bulk import CNBC_MAX_BATCH, CNBC_USER_AGENT, CnbcBatchSource
from app.market.quote import SourceQuote
from app.market.sources import (
    BROWSER_USER_AGENT,
    DEFAULT_TIMEOUT_S,
    NasdaqQuoteSource,
    clean_symbols,
)

#: Whole-batch budget for the primary source (one CNBC request per 500 symbols).
DEFAULT_PRIMARY_TIMEOUT_S = 15.0

#: Whole-pass budget for the per-symbol fallback. Every request inside the
#: pass carries the fallback's own timeout; this is the backstop for a
#: transport that ignores it.
DEFAULT_FALLBACK_TIMEOUT_S = 60.0


# ---------------------------------------------------------------------------
# Field validation (a price is a finite positive number, or it is not a price)
# ---------------------------------------------------------------------------


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got {value!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return number


def _positive(value: Any, name: str) -> float:
    number = _finite(value, name)
    if number <= 0:
        raise ValueError(f"{name} must be > 0, got {value!r}")
    return number


def _optional_positive(value: float | None, name: str) -> float | None:
    return None if value is None else _positive(value, name)


def _optional_non_negative(value: float | None, name: str) -> float | None:
    if value is None:
        return None
    number = _finite(value, name)
    if number < 0:
        raise ValueError(f"{name} must be >= 0, got {value!r}")
    return number


def _aware(moment: Any, name: str) -> datetime:
    if not isinstance(moment, datetime):
        raise TypeError(f"{name} must be a datetime, got {type(moment).__name__}")
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware, got {moment!r}")
    return moment


# ---------------------------------------------------------------------------
# The quote
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PriceQuote:
    """One judged price for one symbol, with its provenance.

    ``change_pct`` is the day move in percent. Pass ``None`` and it is
    derived from ``last``/``prev_close``; pass a number and it is kept as
    given (a caller that measured the move against a different close owns
    that number). ``prev_close`` and ``volume`` stay ``None`` when the
    provider did not publish them — never ``0`` as a stand-in.
    """

    symbol: str
    last: float
    prev_close: float | None
    change_pct: float | None
    volume: float | None
    as_of: datetime
    source: str

    def __post_init__(self) -> None:
        symbol = str(self.symbol or "").strip().upper()
        if not symbol:
            raise ValueError("symbol must be a non-empty string")
        object.__setattr__(self, "symbol", symbol)
        last = _positive(self.last, "last")
        previous = _optional_positive(self.prev_close, "prev_close")
        object.__setattr__(
            self, "volume", _optional_non_negative(self.volume, "volume")
        )
        change = self.change_pct
        if change is None and previous is not None:
            change = (last / previous - 1.0) * 100.0
        if change is not None:
            change = _finite(change, "change_pct")
        object.__setattr__(self, "change_pct", change)
        _aware(self.as_of, "as_of")
        source = str(self.source or "").strip()
        if not source:
            raise ValueError("source must be a non-empty string")
        object.__setattr__(self, "source", source)


def _to_price_quote(
    symbol: str, quote: SourceQuote | None, source: str
) -> PriceQuote | None:
    """One source answer → one registry quote (``None`` stays ``None``)."""

    if quote is None:
        return None
    return PriceQuote(
        symbol=symbol,
        last=quote.last,
        prev_close=quote.prev_close,
        change_pct=None,
        volume=quote.volume,
        as_of=quote.ts_exchange,
        source=source,
    )


# ---------------------------------------------------------------------------
# Primary: CNBC, batched
# ---------------------------------------------------------------------------


class CnbcPriceSource:
    """Batch prices from CNBC, normalized to :class:`PriceQuote`.

    A thin wrapper over :class:`~app.market.bulk.CnbcBatchSource`: the batch
    source owns the endpoint, the chunking and the parsing; this class owns
    the normalization to the registry's quote type. Pass ``batch_source`` to
    drive it without a socket (the construction arguments are then unused).
    """

    name = "cnbc"

    def __init__(
        self,
        *,
        batch_source: Any = None,
        batch_size: int = CNBC_MAX_BATCH,
        timeout_s: float = 10.0,
        user_agent: str = CNBC_USER_AGENT,
    ) -> None:
        self.batch_source = (
            batch_source
            if batch_source is not None
            else CnbcBatchSource(
                batch_size=batch_size, timeout_s=timeout_s, user_agent=user_agent
            )
        )

    async def fetch_many(
        self, symbols: Sequence[str]
    ) -> dict[str, PriceQuote | None]:
        answered = self.batch_source.fetch_many(symbols)
        if inspect.isawaitable(answered):
            answered = await answered
        return {
            str(symbol).strip().upper(): _to_price_quote(symbol, quote, self.name)
            for symbol, quote in answered.items()
        }


# ---------------------------------------------------------------------------
# Fallback: Nasdaq, one symbol per request
# ---------------------------------------------------------------------------


class NasdaqPriceSource:
    """Per-symbol prices from Nasdaq, normalized to :class:`PriceQuote`.

    Wraps :class:`~app.market.sources.NasdaqQuoteSource`. :meth:`fetch` keeps
    the underlying contract — a non-200 raises :class:`QuoteSourceError` —
    while :meth:`fetch_many` is the registry's interface and isolates every
    symbol: one symbol's 429, broken body or timeout answers ``None`` for
    that symbol, never for its neighbours, and never as a zero price. The
    per-symbol failures are recorded in :attr:`last_errors`, so "no data"
    stays distinguishable from "the source errored".
    """

    name = "nasdaq"

    def __init__(
        self,
        *,
        quote_source: Any = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        user_agent: str = BROWSER_USER_AGENT,
    ) -> None:
        self.quote_source = (
            quote_source
            if quote_source is not None
            else NasdaqQuoteSource(timeout_s=timeout_s, user_agent=user_agent)
        )
        self.timeout_s = float(timeout_s)
        self.last_errors: dict[str, str] = {}

    async def fetch(self, symbol: str) -> PriceQuote | None:
        answered = self.quote_source.fetch(symbol)
        if inspect.isawaitable(answered):
            answered = await answered
        return _to_price_quote(symbol, answered, self.name)

    async def fetch_many(
        self, symbols: Sequence[str]
    ) -> dict[str, PriceQuote | None]:
        answers: dict[str, PriceQuote | None] = {}
        self.last_errors = {}
        for symbol in symbols:
            key = str(symbol).strip().upper()
            try:
                answered = self.fetch(key)
                if inspect.isawaitable(answered):
                    answered = await asyncio.wait_for(answered, self.timeout_s)
                answers[key] = answered if isinstance(answered, PriceQuote) else None
            except Exception as exc:  # noqa: BLE001 - one symbol is not the pass
                self.last_errors[key] = f"{type(exc).__name__}: {exc}"
                answers[key] = None
        return answers


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


class PriceRegistry:
    """CNBC-first price lookup with a per-symbol Nasdaq fallback.

    The primary is asked once for the whole list. Every symbol it could not
    answer — absent from the body, unpriced, or lost to a source failure —
    is asked of the fallback, and only those. Each source call is bounded by
    its own timeout and isolated: a source that raises or runs out of clock
    loses its own pass, the symbols it owed stay ``None``, and the other
    source's answers survive. Nothing here reads a key; both providers are
    keyless.
    """

    def __init__(
        self,
        *,
        primary: Any,
        fallback: Any,
        primary_timeout_s: float = DEFAULT_PRIMARY_TIMEOUT_S,
        fallback_timeout_s: float = DEFAULT_FALLBACK_TIMEOUT_S,
    ) -> None:
        for label, source in (("primary", primary), ("fallback", fallback)):
            name = str(getattr(source, "name", "") or "").strip()
            if not name:
                raise ValueError(f"{label} source needs a non-empty name")
            if not callable(getattr(source, "fetch_many", None)):
                raise TypeError(
                    f"{label} source {name!r} must implement fetch_many"
                )
        if not primary_timeout_s > 0:
            raise ValueError(
                f"primary_timeout_s must be > 0, got {primary_timeout_s!r}"
            )
        if not fallback_timeout_s > 0:
            raise ValueError(
                f"fallback_timeout_s must be > 0, got {fallback_timeout_s!r}"
            )
        self.primary = primary
        self.fallback = fallback
        self.primary_timeout_s = float(primary_timeout_s)
        self.fallback_timeout_s = float(fallback_timeout_s)
        #: Source name → why it owed nothing, for the most recent ``quotes``
        #: call. A source that answered everything leaves no entry.
        self.last_errors: dict[str, str] = {}

    async def quotes(
        self, symbols: Sequence[str]
    ) -> dict[str, PriceQuote | None]:
        """One answer per requested symbol, in the caller's order.

        A symbol nobody answered is present with ``None`` — the caller can
        tell "missing" from "priced at zero" without a second lookup.
        """

        wanted = clean_symbols(symbols)
        if not wanted:
            return {}
        self.last_errors = {}
        found: dict[str, PriceQuote | None] = {}
        primary = await self._ask(self.primary, wanted, self.primary_timeout_s)
        for symbol in wanted:
            found[symbol] = primary.get(symbol)
        missing = [symbol for symbol in wanted if found[symbol] is None]
        if missing:
            fallback = await self._ask(
                self.fallback, missing, self.fallback_timeout_s
            )
            for symbol in missing:
                quote = fallback.get(symbol)
                if quote is not None:
                    found[symbol] = quote
        return found

    async def _ask(
        self, source: Any, symbols: Sequence[str], timeout_s: float
    ) -> dict[str, PriceQuote | None]:
        """One source's answers for ``symbols``; ``{}`` when it could not answer.

        A timeout, a raised error and a non-mapping answer are the same thing
        here: this source owes nothing for this pass. The failure is recorded
        in :attr:`last_errors` and every symbol is treated as unanswered —
        which routes it to the fallback, never to a zero price.
        """

        try:
            answered = source.fetch_many(symbols)
            if inspect.isawaitable(answered):
                answered = await asyncio.wait_for(answered, timeout_s)
            if not isinstance(answered, Mapping):
                raise TypeError(
                    f"{source.name} fetch_many returned "
                    f"{type(answered).__name__}, not a mapping"
                )
        except TimeoutError:
            self.last_errors[source.name] = f"timeout after {timeout_s:.3f}s"
            return {}
        except Exception as exc:  # noqa: BLE001 - one source is not the batch
            self.last_errors[source.name] = f"{type(exc).__name__}: {exc}"
            return {}
        return {
            str(symbol).strip().upper(): (
                quote if isinstance(quote, PriceQuote) else None
            )
            for symbol, quote in answered.items()
        }


__all__ = [
    "DEFAULT_FALLBACK_TIMEOUT_S",
    "DEFAULT_PRIMARY_TIMEOUT_S",
    "CnbcPriceSource",
    "NasdaqPriceSource",
    "PriceQuote",
    "PriceRegistry",
]
