"""US universe resolution for the bulk fundamentals snapshot (P11C).

The bulk mode answers a different question from P11A's symbol list: *"the US
stock universe, one fundamentals row each"*. Its symbols therefore come from the
screen engine's own scan rather than from a second list that could disagree
about which instruments are stocks:

* ``fetch_all(region, columns=PARITY_COLUMNS)`` walks every scanner page — the
  scanner returns 5,000 rows per page, so the whole US market is two or three
  requests, not one per symbol;
* :func:`app.screen.universe.build_universe` applies the tab's own gates: OTC and
  pink sheets out, the USD 100M market-cap floor (a row with no cap cannot prove
  it fits), ``type=stock`` + ``subtype=common`` only (a fund, a preferred share
  or a warrant is a different instrument), US venue only, and one row per issuer
  so a cross-listing cannot enter twice;
* the **symbol** dedupe below is a third, narrower key. The fundamentals store is
  keyed on ``symbol``, so two rows the issuer dedupe kept — an ADR pair, two
  companies that share a ticker on different venues — must not race for one
  row. First occurrence wins; the scan is ordered by market cap descending, so
  the winner is the larger issuer.

Cost
----
A fundamentals snapshot is one ladder walk per symbol, and the production rung
(Yahoo) answers the 22 cells with a request or two per symbol. The universe scan
is cheap (a handful of pages); the walks are the expensive part. A full US
universe is therefore thousands of requests over tens of minutes, which is why
the CLI refuses an unbounded walk unless ``--limit``, ``--time-budget`` or
``--all`` says otherwise, and why :class:`UniversePlan` reports how many walks it
is about to authorise before the first one starts.

Why Yahoo per symbol instead of the scan's own bulk columns
------------------------------------------------------------
The parity scan already carries a dozen fundamentals-ish columns (P/E, margins,
market cap), but it does **not** carry the 22-cell vocabulary (statements,
balance-sheet ratios, revision history) and probes showed several bulk cells
null for every row. A per-symbol walk costs more and is the only source that
fills the vocabulary the snapshots and the filters are built on; the scan is
used for *which* symbols, never for *what* their fundamentals are.

Nothing in this module builds a provider, opens a socket or writes a row: the
fetcher is injected (``fetch_all`` by default) and the plan is a frozen value.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Sequence

from app.fundamentals.snapshot import canonical_symbol
from app.screen.scanner import DEFAULT_FLOOR, ScanPage, fetch_all
from app.screen.universe import PARITY_COLUMNS, build_universe

logger = logging.getLogger("mee.fundamentals.universe")

#: The regions the bulk mode knows. ``us`` is the one tab whose fundamentals the
#: snapshot store is scoped to; ``us_etf`` is a different instrument family and
#: ``global`` would mix venues whose provider coverage differs.
UNIVERSE_REGIONS: tuple[str, ...] = ("us",)
DEFAULT_REGION = "us"

#: How many symbols the JSON/summary document quotes. The plan carries a count,
#: never the whole list: a 3,000-symbol dump is not a report.
SAMPLE_SYMBOLS = 20

#: An injected scanner page source. ``fetch_all`` is the production one; tests
#: pass a scripted page so the suite can never leave the process.
UniverseFetcher = Callable[..., Awaitable[ScanPage]]


class UniverseError(RuntimeError):
    """The scanner did not answer a usable universe (never silently empty)."""


@dataclass(frozen=True, slots=True)
class UniversePlan:
    """The symbol list the bulk mode will build, and the scan it came from."""

    region: str
    symbols: tuple[str, ...]
    #: Rows the scanner sent (all pages).
    rows: int
    #: The provider's own promised row count.
    total_count: int
    #: Rows that survived the universe gates and the issuer dedupe.
    issuers: int
    #: Symbols collapsed by the canonical-key dedupe (``AAPL`` twice).
    duplicates: int
    #: The ``--limit`` that produced ``symbols``; ``None`` is "all".
    limit: int | None
    #: The provider columns the scan was asked for.
    columns: tuple[str, ...]

    @property
    def dropped(self) -> int:
        """Rows the universe did not keep: gate rejections + cross-listings."""

        return max(0, self.rows - self.issuers)

    @property
    def truncated(self) -> bool:
        """True when ``--limit`` kept fewer symbols than the universe held."""

        if self.limit is None:
            return False
        return len(self.symbols) < self.issuers

    @property
    def walks(self) -> int:
        """Provider walks this plan authorises: one per symbol."""

        return len(self.symbols)

    def as_dict(self) -> dict[str, Any]:
        return {
            "region": self.region,
            "rows": self.rows,
            "total_count": self.total_count,
            "issuers": self.issuers,
            "dropped": self.dropped,
            "duplicates": self.duplicates,
            "limit": self.limit,
            "symbols": len(self.symbols),
            "truncated": self.truncated,
            "sample": list(self.symbols[:SAMPLE_SYMBOLS]),
            "columns": list(self.columns),
        }


async def fetch_us_symbols(
    *,
    limit: int | None = None,
    region: str = DEFAULT_REGION,
    fetch: UniverseFetcher | None = None,
    columns: Sequence[str] = PARITY_COLUMNS,
    min_market_cap: Any = DEFAULT_FLOOR,
) -> UniversePlan:
    """Scan ``region``, apply the universe gates, dedupe and limit the symbols.

    ``limit`` is applied **after** the gates and the dedupe: the scanner's own
    order is by market cap, so ``limit=100`` means "the 100 largest US common
    stocks a fundamentals screen would consider", not "the first 100 rows", which
    would include pink sheets and funds the gate then removes. ``0``/``None``
    keeps every symbol.

    ``fetch`` is the injected page source (``fetch_all`` by default); a page that
    promises rows but sends none is an error rather than an empty universe,
    because a truncated scanner body and a market with no stocks must not look
    alike.
    """

    if region not in UNIVERSE_REGIONS:
        raise ValueError(
            f"unknown universe region {region!r}; expected one of "
            f"{', '.join(UNIVERSE_REGIONS)}"
        )
    if limit is not None and int(limit) < 0:
        raise ValueError(f"limit must be a non-negative integer, got {limit!r}")
    wanted_limit = int(limit) if limit else None
    requested_columns = tuple(columns)

    scan = fetch or fetch_all
    page = await scan(
        region, columns=requested_columns, min_market_cap=min_market_cap
    )
    rows = list(page.rows)
    if not rows and page.total_count:
        raise UniverseError(
            f"the scanner promised {int(page.total_count):,} rows for {region} but "
            "sent none; refusing to build an empty universe"
        )

    universe = build_universe(
        rows,
        region=region,
        columns=requested_columns,
        min_market_cap=min_market_cap,
    )

    symbols: list[str] = []
    seen: set[str] = set()
    duplicates = 0
    for row in universe:
        token = str(row.get("symbol") or "").strip()
        if not token:
            continue
        key = canonical_symbol(token)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        symbols.append(key)
    if wanted_limit is not None:
        symbols = symbols[:wanted_limit]

    plan = UniversePlan(
        region=region,
        symbols=tuple(symbols),
        rows=len(rows),
        total_count=int(page.total_count),
        issuers=len(universe),
        duplicates=duplicates,
        limit=wanted_limit,
        columns=requested_columns,
    )
    logger.info(
        "fundamentals universe region=%s rows=%d issuers=%d duplicates=%d "
        "symbols=%d limit=%s",
        plan.region,
        plan.rows,
        plan.issuers,
        plan.duplicates,
        len(plan.symbols),
        plan.limit if plan.limit is not None else "all",
    )
    return plan


__all__ = [
    "DEFAULT_REGION",
    "SAMPLE_SYMBOLS",
    "UNIVERSE_REGIONS",
    "UniverseError",
    "UniverseFetcher",
    "UniversePlan",
    "fetch_us_symbols",
]
