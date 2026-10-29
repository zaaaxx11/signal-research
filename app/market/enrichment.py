"""Per-symbol enrichment: the two sources the keyless fast path cannot reach.

The keyless fast path (SEC bulk fundamentals, CNBC quote batch, Yahoo chart
OHLC, FINRA short interest, the official index files) answers 4 217 symbols
without one credential and without one page per symbol. What it structurally
cannot answer is the half of the catalog that only a *per-symbol* request
carries: the fourteen ``quoteSummary`` modules, the four fiscal years of
statement history, and the three news cells. This stage is that crawl, and it
runs after the fast path has already published, so a slow or throttled Yahoo
never delays the keyless row.

Two sources, two different hosts, two different failure modes:

* :class:`app.market.yahoo_depth.YahooDepthSource` (``yahoo_full``) — one
  cookie/crumb per batch, one ``quoteSummary`` plus one
  ``fundamentals-timeseries`` per symbol, paced at ``request_delay_s`` because
  the crumb endpoint throttles under aggressive use. ``include_options`` stays
  ``False``: the expiry list costs a third request per symbol for one cell, and
  at 4 217 symbols that is 4 217 requests for ``optionable``.
* :class:`app.market.news.RssNewsSource` (``news``) — Yahoo's headline feed
  first, Nasdaq only when Yahoo had nothing dated, i.e. ``fallback=True``: one
  request per symbol instead of two, the same three published cells.

Both are asked for the same symbol list, concurrently, and their
:class:`~app.market.depth.DepthObservation` field maps are merged per symbol.
**Yahoo wins a key collision.** The two vocabularies overlap on identity cells
(``company``, ``sector``, ``shares_float``), and where they overlap the deeper
source is the one that paid two body requests for the answer; a headline feed
that happens to print a company name must not outvote it. Everything either
source uniquely owns — the news cells, the timeseries growth rates — survives
the merge untouched.

Isolation is the other half of the contract. A source is a *contributor*, never
a gate: a throttled crumb endpoint, a 500 from a feed, or a source that answers
with something that is not a mapping costs that source's fields and nothing
else. Each failure is recorded per source in :attr:`EnrichmentStage.last_errors`
(``{source name: "ExceptionType: message"}``, replaced on every run, empty when
the run was clean) so a caller can tell "Yahoo was throttled" from "the news
feed was down" without reading a log.

The stage also hands back the one map a *later* source needs.
:class:`app.market.finra.FinraShortSource` computes ``short_float`` as
``short_interest / shares_float * 100`` and refuses to invent a divisor, so the
float shares are read out of the Yahoo observations and published as
:attr:`EnrichmentStage.shares_float` (``{symbol: float}``) for
``set_shares_float()``. Only a finite number counts: ``None``, ``"n/a"``,
``True`` and ``NaN`` are absent facts, not zero-share companies, and FINRA's own
``> 0`` guard stays the single place a non-positive float is rejected.
"""

from __future__ import annotations

import asyncio
import json
import inspect
import logging
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.market.depth import DepthObservation, canonical_symbol
from app.market.news import RssNewsSource
from app.market.yahoo_depth import YahooDepthSource

logger = logging.getLogger("mee.depth.enrichment")

#: Yahoo's per-symbol fan-out. The 100-symbol probe measured 100/100 at
#: concurrency 4 with zero 429s; a wider fan-out buys seconds and risks the
#: crumb endpoint, which is the one request this source cannot retry cheaply.
DEFAULT_CONCURRENCY = 4

#: The RSS hosts answered 20/20 with 268 items in 0.71 s; plain feeds tolerate a
#: wider fan-out than an authenticated JSON API does.
DEFAULT_NEWS_CONCURRENCY = 8

#: One paced request per symbol on the quoteSummary/timeseries pair.
YAHOO_REQUEST_DELAY_S = 0.25

#: The cell FINRA divides by. ``DepthObservation`` already canonicalises the
#: ``float_shares`` spelling, so the alias only matters for a hand-built map.
SHARES_FLOAT_FIELD = "shares_float"
_SHARES_FLOAT_ALIASES: tuple[str, ...] = (SHARES_FLOAT_FIELD, "float_shares")


def _unique_symbols(symbols: Sequence[str]) -> list[str]:
    """Canonical, de-duplicated, order-preserving; empty strings dropped."""

    ordered: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        symbol = canonical_symbol(raw)
        if symbol and symbol not in seen:
            seen.add(symbol)
            ordered.append(symbol)
    return ordered


def _as_float(value: Any) -> float | None:
    """A finite number, or ``None`` for anything a share count cannot be.

    ``bool`` is rejected on purpose: ``True`` is an ``int`` in Python and would
    otherwise publish a one-share float. A numeric *string* is accepted because
    a provider that prints ``"24300000000"`` has still answered the question;
    ``"n/a"``, ``""`` and ``"12.5M"`` have not.
    """

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def shares_float_map(
    observations: Mapping[str, DepthObservation],
) -> dict[str, float]:
    """``{symbol: shares_float}`` for the observations that carry a real number.

    A missing cell, a ``None``, a non-numeric string or a non-finite value
    leaves the symbol out of the map entirely: FINRA then publishes no
    ``short_float`` for it, which is the honest answer.
    """

    out: dict[str, float] = {}
    for symbol, observation in observations.items():
        fields = observation.fields
        for name in _SHARES_FLOAT_ALIASES:
            if name not in fields:
                continue
            number = _as_float(fields[name])
            if number is not None:
                out[symbol] = number
            break
    return out


class EnrichmentStage:
    """Run the per-symbol sources and merge their fields, Yahoo first.

    ``concurrency`` is Yahoo's per-symbol fan-out; the news feeds get their own,
    wider default because they are unauthenticated plain RSS. Both sources are
    injectable, which is how the offline suite drives this class without a
    socket: any object with a ``name`` and a ``fetch_many`` (sync or async)
    answering a mapping of ``DepthObservation`` satisfies the contract.

    Construction performs no request, so a stage can be built at import time
    and run later, exactly like the depth sources it wraps.
    """

    def __init__(
        self,
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        news_concurrency: int = DEFAULT_NEWS_CONCURRENCY,
        yahoo_source: YahooDepthSource | None = None,
        news_source: RssNewsSource | None = None,
        batch_size: int | None = None,
        state_path: Any = None,
    ) -> None:
        #: Rotate through the universe instead of crawling all of it in one
        #: pass. Measured: a single 4,217-symbol Yahoo pass stalls the egress
        #: (0 bytes for 45 s) whether it rides direct or a residential proxy,
        #: because Yahoo does not sustain thousands of quoteSummary requests
        #: from one session. A batch turns that marathon into a daily rotation:
        #: each cycle enriches the next slice, and the depth store keeps the
        #: rest. ``None`` keeps the crawl-everything behaviour for small runs.
        self.batch_size = None if batch_size is None else max(1, int(batch_size))
        self.state_path = Path(state_path).expanduser() if state_path else None
        self.concurrency = max(1, int(concurrency))
        self.news_concurrency = max(1, int(news_concurrency))
        self.yahoo_source: Any = (
            yahoo_source
            if yahoo_source is not None
            else YahooDepthSource(
                concurrency=self.concurrency,
                include_timeseries=True,
                include_options=False,
                request_delay_s=YAHOO_REQUEST_DELAY_S,
            )
        )
        self.news_source: Any = (
            news_source
            if news_source is not None
            else RssNewsSource(
                concurrency=self.news_concurrency,
                fallback=True,
            )
        )
        #: ``{source name: "ExceptionType: message"}`` for the last run only.
        self.last_errors: dict[str, str] = {}
        self._shares_float: dict[str, float] = {}

    @property
    def shares_float(self) -> dict[str, float]:
        """The float-share map the last run extracted, for FINRA's divisor."""

        return dict(self._shares_float)

    async def run(self, symbols: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Enrich ``symbols`` and return ``{symbol: merged fields}``.

        A symbol neither source answered is absent from the result rather than
        present with an empty map: the caller merges into a row that already
        exists, and an empty map would only be noise there.
        """

        ordered = _unique_symbols(symbols)
        self.last_errors = {}
        self._shares_float = {}
        if not ordered:
            return {}

        if self.batch_size is not None and len(ordered) > self.batch_size:
            ordered = self._take_batch(ordered)

        # Both sources are asked for the whole list and awaited together. The
        # two hosts are independent, so the crawl costs the slower of the two
        # rather than their sum; isolation is enforced inside ``_collect``, so
        # neither can cancel or sink the other.
        yahoo_observations, news_observations = await asyncio.gather(
            self._collect(self.yahoo_source, ordered),
            self._collect(self.news_source, ordered),
        )

        merged: dict[str, dict[str, Any]] = {}
        for symbol in ordered:
            fields: dict[str, Any] = {}
            # News first, Yahoo second: on a shared key the later update wins,
            # and Yahoo is the source that paid two body requests for it.
            news = news_observations.get(symbol)
            if news is not None:
                fields.update(news.fields)
            yahoo = yahoo_observations.get(symbol)
            if yahoo is not None:
                fields.update(yahoo.fields)
            if fields:
                merged[symbol] = fields

        self._shares_float = shares_float_map(yahoo_observations)
        return merged

    def _take_batch(self, ordered: list[str]) -> list[str]:
        """The next ``batch_size`` symbols, wrapping, and advance the cursor.

        The cursor is persisted so a restart continues where the last cycle
        stopped instead of re-enriching the head of the universe forever. A
        missing or corrupt state file starts at zero rather than raising: the
        rotation is an optimisation, not a correctness requirement.
        """

        size = len(ordered)
        start = self._load_cursor() % size
        batch = ordered[start : start + self.batch_size]
        if len(batch) < self.batch_size:
            batch += ordered[: self.batch_size - len(batch)]
        self._save_cursor((start + self.batch_size) % size)
        #: Reported so a caller can tell which slice this cycle covered.
        self.last_batch = (start, len(batch))
        return batch

    def _load_cursor(self) -> int:
        if self.state_path is None:
            return getattr(self, "_cursor", 0)
        try:
            document = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return 0
        value = document.get("cursor") if isinstance(document, dict) else None
        return value if isinstance(value, int) and value >= 0 else 0

    def _save_cursor(self, value: int) -> None:
        self._cursor = value
        if self.state_path is None:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps({"cursor": value}))
        except OSError:
            # An unwritable state file costs a restart of the rotation, not
            # the pass itself.
            return

    async def _collect(
        self, source: Any, symbols: Sequence[str]
    ) -> dict[str, DepthObservation]:
        """One source's observations, or ``{}`` plus a recorded error.

        Every failure mode that costs fields is caught here and named in
        :attr:`last_errors`: a raised exception, and a source that answers with
        something other than a mapping of observations. A value that is not a
        ``DepthObservation`` is skipped rather than trusted — the same filter
        the depth collector applies.
        """

        name = str(getattr(source, "name", "") or "source")
        try:
            fetched = source.fetch_many(symbols)
            result = await fetched if inspect.isawaitable(fetched) else fetched
        except Exception as exc:  # noqa: BLE001 - one source is not the stage
            self._record(name, exc)
            return {}
        if not isinstance(result, Mapping):
            self._record(name, TypeError("fetch_many did not return a mapping"))
            return {}
        return {
            observation.symbol: observation
            for observation in result.values()
            if isinstance(observation, DepthObservation)
        }

    def _record(self, name: str, exc: BaseException) -> None:
        message = f"{type(exc).__name__}: {exc}"
        self.last_errors[name] = message
        logger.warning("enrichment source %s failed: %s", name, message)


__all__ = [
    "DEFAULT_CONCURRENCY",
    "DEFAULT_NEWS_CONCURRENCY",
    "SHARES_FLOAT_FIELD",
    "YAHOO_REQUEST_DELAY_S",
    "EnrichmentStage",
    "shares_float_map",
]
