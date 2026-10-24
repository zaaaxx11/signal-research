"""P11D — the bulk **fallback** source: one TradingView scan, 3,888 snapshots.

P11C walks the ladder once per symbol because Yahoo is the only rung that serves
the whole 22-cell vocabulary. That cost is also its ceiling: after 1,266 rows the
live run was throttled (HTTP 429), so the universe could not be filled by
per-symbol walks. This module is the other source, and it is cheaper by three
orders of magnitude:

* **one request, not one per symbol.** ``fetch_all(region, columns=...)`` walks
  the scanner's pages (5,000 rows a page; the whole US market is two requests,
  5,720 rows live) and returns every row with the fundamental columns the probe
  proved to be filled;
* **:func:`app.screen.universe.build_universe`** applies the tab's own gates
  (OTC/pink sheets out, USD 100M floor, ``type=stock`` + ``subtype=common``, US
  venue, one row per issuer), so the symbols are the same ones the screen engine
  trusts — not a second list;
* :func:`snapshot_from_scan_row` maps each raw row onto the **same** 22-cell
  snapshot vocabulary, so the store has one shape whichever rung wrote a row.

The 16 cells this source fills, and the 6 it does not
-----------------------------------------------------
Probed live against ``scanner.tradingview.com/america/scan`` (5,720 US rows, one
request): ``total_revenue`` 97.6%, ``ebitda`` 79.7%, ``net_income`` 97.6%,
``free_cash_flow`` 81.0%, ``total_debt`` 95.7%,
``earnings_per_share_diluted_ttm`` 90.9%, ``market_cap_basic`` 98.2%,
``total_shares_outstanding`` 98.6%, ``float_shares_outstanding`` 97.5%,
``price_earnings_ttm`` 52.8%, ``price_earnings_growth_ttm`` 41.1%,
``price_book_fq`` 86.8%, ``enterprise_value_ebitda_ttm`` 48.8%,
``dividends_yield`` 40.7%, ``return_on_equity`` 83.8%, ``debt_to_equity`` 88.5%.

That is :data:`SCAN_FIELD_MAP`, sixteen provider columns, one canonical cell
each. ``price_earnings_forward``, ``price_sales_ttm``,
``dividend_payout_ratio`` and every ``cash_*`` column were null for **every**
row when probed, so no mapping asks for them: the six cells they would have
filled (``pe_forward``, ``ps``, ``payout_ratio``, ``cash_and_equivalents``,
``eps_forward`` and ``market_cap_usd``) stay ``null``, and
:data:`UNSERVED_FIELDS` names them so a report can say so instead of implying
22-cell coverage.

**One scale, stated once.** ``dividends_yield`` and ``return_on_equity`` already
arrive as percents (``0.31`` is a 0.31% yield; AAPL's ROE is 148.75, identical to
the Yahoo rung's), but ``debt_to_equity`` arrives as a *ratio* (``0.784``) where
the Yahoo rung publishes a percent (``78.445``). The scan multiplies that one
cell by 100 so a field means the same thing whichever rung wrote the row — the
same quantity in the unit the store already uses, not a new number. A cell the
provider did not send (or sent as a non-finite / non-numeric value) stays
``null``: a missing P/E is never a P/E of zero, and nothing here is derived from
another cell.

Never overwrite a fresh answer
------------------------------
The store holds **one row per symbol**, so a fallback source must not silently
downgrade a better row. A Yahoo row fills 22 cells; a scan row fills 16. The
rule is therefore: a symbol that already has a **fresh** snapshot is kept —
``skip_fresh`` (the default) keeps *any* fresh row, and even ``--refresh`` will
not replace a fresh row whose source is in :data:`PROTECTED_SOURCES`
(``yahoo``). An *expired* Yahoo row is not an answer any more, so the scan fills
the gap. Every kept symbol is reported with the source that won, which is what
makes "1,266 Yahoo rows survived the fill" a measurement rather than a hope.
Rows for symbols **outside** today's universe are left alone rather than
deleted: they are still answers for their own symbol and expire on their own
24 h clock, while deleting them would turn "the gate moved" into data loss.

Nothing here builds a provider, opens a socket or constructs a ladder: the page
source is injected (``fetch_all`` by default) and the store is passed in. The
only clock is the one the caller injects, so a test is arithmetic, not a sleep.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from app.fundamentals.snapshot import (
    FUNDAMENTAL_FIELDS,
    TTL_S,
    FundamentalsSnapshot,
    SqliteFundamentalsStore,
    canonical_symbol,
    coverage_report,
    instrument_for,
    iso,
    utc_now,
)
from app.fundamentals.universe import DEFAULT_REGION, UNIVERSE_REGIONS
from app.fundamentals.universe import UniverseError
from app.screen.scanner import DEFAULT_FLOOR, ScanPage, fetch_all
from app.screen.universe import build_universe, mic_for

logger = logging.getLogger("mee.fundamentals.scan")

#: The source label a scan-built row carries. The provider is TradingView's
#: scanner, and a reader must be able to tell a 16-cell row from a 22-cell one
#: without opening the JSON.
SCAN_SOURCE = "tradingview"

#: Provider column → canonical cell, for the sixteen columns the live probe found
#: filled. One column, one cell: a mapping that filled two canonical cells from
#: one provider cell would let the two disagree the moment one of them is
#: rewritten by another source.
SCAN_FIELD_MAP: Mapping[str, str] = {
    # -- income statement / cash flow, in dollars
    "total_revenue": "revenue_ttm",
    "ebitda": "ebitda_ttm",
    "net_income": "net_income_ttm",
    "free_cash_flow": "fcf_ttm",
    # -- balance sheet and share structure
    "total_debt": "total_debt",
    "market_cap_basic": "market_cap",
    "total_shares_outstanding": "shares_outstanding",
    "float_shares_outstanding": "float_shares",
    # -- per-share and multiples (ratios, copied verbatim from the provider)
    "earnings_per_share_diluted_ttm": "eps_ttm",
    "price_earnings_ttm": "pe_ttm",
    "price_earnings_growth_ttm": "peg",
    "price_book_fq": "pb",
    "enterprise_value_ebitda_ttm": "ev_ebitda",
    # -- percents, already on the store's 0-100 scale
    "dividends_yield": "dividend_yield",
    "return_on_equity": "roe",
    # -- a ratio at the provider, a percent in the store: see SCAN_SCALE
    "debt_to_equity": "debt_to_equity",
}

#: The one cell whose provider unit is not the store's unit. ``debt_to_equity``
#: is a ratio at TradingView (``0.784`` = 78.4%) and a percent at the Yahoo rung
#: (``78.445``), so the scan states the conversion instead of storing a number
#: that means something different from the row next to it.
SCAN_SCALE: Mapping[str, float] = {"debt_to_equity": 100.0}

#: The gate columns :func:`app.screen.universe.build_universe` needs (type,
#: subtype, venue, currency, issuer name). ``market_cap_basic`` arrives through
#: :data:`SCAN_FIELD_MAP`. ``symbol`` is not requested: it comes from the ticker.
#:
#: ``Value.Traded`` is here for the representative rule, not for a snapshot cell:
#: an issuer with several share classes (``BRK.A``/``BRK.B``) keeps the most
#: traded listing, and without the turnover column every candidate ties at zero,
#: so the winner would be whichever row the provider happened to send first —
#: which flips between two runs and leaves the store holding the sibling class.
GATE_COLUMNS: tuple[str, ...] = (
    "name",
    "description",
    "exchange",
    "country",
    "type",
    "subtype",
    "currency",
    "Value.Traded",
)

#: Every provider column one scan asks for: the gates first (so a row can be
#: gated without a second request), then the sixteen fundamental cells.
SCAN_COLUMNS: tuple[str, ...] = tuple(
    dict.fromkeys(tuple(GATE_COLUMNS) + tuple(SCAN_FIELD_MAP))
)

#: The cells this source cannot serve, derived from the map rather than retyped,
#: so adding a column cannot leave the report claiming the wrong holes.
UNSERVED_FIELDS: tuple[str, ...] = tuple(
    name for name in FUNDAMENTAL_FIELDS if name not in set(SCAN_FIELD_MAP.values())
)

if len(SCAN_FIELD_MAP) != 16 or len(set(SCAN_FIELD_MAP.values())) != 16:  # pragma: no cover
    raise RuntimeError(
        "the scan mapping is 16 provider columns to 16 distinct cells, got "
        f"{len(SCAN_FIELD_MAP)} columns / {len(set(SCAN_FIELD_MAP.values()))} cells"
    )
_unknown_targets = sorted(set(SCAN_FIELD_MAP.values()) - set(FUNDAMENTAL_FIELDS))
if _unknown_targets:  # pragma: no cover - contract guard
    raise RuntimeError(f"the scan maps to cells outside the vocabulary: {_unknown_targets}")
del _unknown_targets

#: A source a scan row may not replace while the row is still fresh. Yahoo fills
#: all 22 cells; a 16-cell scan row for the same symbol is a strict downgrade,
#: so the scan defers to it even under ``--refresh``. A *stale* row is not
#: protected: an expired answer is not an answer.
PROTECTED_SOURCES: frozenset[str] = frozenset({"yahoo"})

#: Build outcomes. ``written`` is a new row; ``kept`` is an existing row that
#: answered (whatever wrote it); ``empty`` is a row the scan served no cell for;
#: ``missing`` is a universe symbol with no raw row behind it.
WRITTEN = "written"
KEPT = "kept"
EMPTY = "empty"
MISSING = "missing"
SCAN_STATUSES: frozenset[str] = frozenset({WRITTEN, KEPT, EMPTY, MISSING})

#: How many symbols a JSON/summary document quotes. A 3,888-symbol dump is not a
#: report; the store is what holds the rows.
SAMPLE_SYMBOLS = 20

#: An injected scanner page source. ``fetch_all`` is production; tests pass a
#: scripted page so the suite can never leave the process.
ScanFetcher = Callable[..., Awaitable[ScanPage]]


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------


def _as_utc(moment: datetime) -> datetime:
    """Aware UTC: the store's ``iso`` refuses a naive stamp."""

    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def scan_cell(value: Any) -> float | None:
    """One provider cell as a store value: a finite float, or ``None``.

    ``None``, ``NaN``/``inf``, a bool and a string that is not a number are all
    *absent* — the same rule :mod:`app.fundamentals.snapshot` applies to an
    observation. ``0`` is a number and survives: a company with no debt reports
    ``0``, which is different from a provider that sent nothing.
    """

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except (TypeError, ValueError):
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def mapped_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    """The 22-cell, full-width document one raw scan row yields.

    Every vocabulary cell is present; the sixteen the scan serves carry the
    provider's number (scaled by :data:`SCAN_SCALE` where the unit differs) and
    the six in :data:`UNSERVED_FIELDS` are ``None``.
    """

    cells: dict[str, Any] = {}
    for column, name in SCAN_FIELD_MAP.items():
        value = scan_cell(row.get(column))
        if value is None:
            continue
        scale = SCAN_SCALE.get(name)
        cells[name] = value * scale if scale else value
    return {name: cells.get(name) for name in FUNDAMENTAL_FIELDS}


def snapshot_from_scan_row(
    row: Mapping[str, Any],
    *,
    symbol: str | None = None,
    fetched_at: datetime,
    ttl_s: int = TTL_S,
) -> FundamentalsSnapshot:
    """One raw scanner row → one ``source='tradingview'`` snapshot.

    ``as_of`` is the fetch clock: the scanner publishes no per-cell data stamp
    for these columns, and the scan is a live read of the current quarter, so the
    moment it was read is the only clock this row can honestly claim (inventing
    a fiscal-period date from the values would be worse than saying "read now").
    """

    if ttl_s <= 0:
        raise ValueError("ttl_s must be positive")
    key = canonical_symbol(symbol or str(row.get("symbol") or ""))
    moment = _as_utc(fetched_at)
    return FundamentalsSnapshot(
        symbol=key,
        instrument_id=instrument_for(key).instrument_id,
        fields=mapped_fields(row),
        source=SCAN_SOURCE,
        as_of=moment,
        fetched_at=moment,
        expires_at=moment + timedelta(seconds=int(ttl_s)),
    )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScanResult:
    """What one universe symbol's scan row did."""

    symbol: str
    status: str
    filled: int = 0
    #: The source of the row that now answers for this symbol (``tradingview``
    #: for a write, the kept row's own source otherwise).
    source: str | None = None
    #: When ``status`` is ``kept``: the source that won. ``None`` otherwise.
    kept_source: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in SCAN_STATUSES:  # pragma: no cover - programmer error
            raise ValueError(f"unknown scan status {self.status!r}")

    @property
    def ok(self) -> bool:
        return self.status in {WRITTEN, KEPT}

    @property
    def missing(self) -> int:
        return len(FUNDAMENTAL_FIELDS) - self.filled

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "status": self.status,
            "filled": self.filled,
            "missing": self.missing,
            "source": self.source,
            "kept_source": self.kept_source,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ScanReport:
    """One scan-built fill: the scan that was read, what it wrote, what it kept.

    ``requested`` is the number of symbols the universe handed to the fill, so
    the arithmetic closes: ``requested == written + kept + empty + missing``. A
    kept symbol is not a failure — it is a symbol that already had a fresh
    answer — and :attr:`kept_sources` names who answered each one.
    """

    region: str
    rows: int
    total_count: int
    issuers: int
    duplicates: int
    columns: tuple[str, ...]
    results: tuple[ScanResult, ...]
    #: The ``--limit`` that produced ``results``; ``None`` is "all".
    limit: int | None = None
    #: The rows this run wrote (kept symbols have their own stored snapshot).
    snapshots: tuple[FundamentalsSnapshot, ...] = ()
    missing_fields: tuple[str, ...] = UNSERVED_FIELDS
    fetched_at: datetime | None = None
    elapsed_s: float | None = None

    def _count(self, status: str) -> int:
        return sum(1 for item in self.results if item.status == status)

    @property
    def requested(self) -> int:
        return len(self.results)

    @property
    def written(self) -> int:
        return self._count(WRITTEN)

    @property
    def kept(self) -> int:
        return self._count(KEPT)

    @property
    def empty(self) -> int:
        return self._count(EMPTY)

    @property
    def missing(self) -> int:
        return self._count(MISSING)

    @property
    def kept_sources(self) -> dict[str, int]:
        """Which sources answered the kept symbols (``{'yahoo': 1266}``)."""

        counts: dict[str, int] = {}
        for item in self.results:
            if item.status != KEPT:
                continue
            label = item.kept_source or "unknown"
            counts[label] = counts.get(label, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def filled_cells(self) -> int:
        return sum(item.filled for item in self.results)

    @property
    def truncated(self) -> bool:
        """True when a ``--limit`` kept fewer symbols than the universe held."""

        return self.requested < self.issuers

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(item.symbol for item in self.results)

    def coverage(self) -> dict[str, Any]:
        """Measured fill of the rows **this run wrote**, per field.

        Uses the store's own :func:`app.fundamentals.snapshot.coverage_report`, so
        the scan's numbers and the store's are the same measurement. Kept rows
        are excluded on purpose: they belong to the source that wrote them, and
        mixing a 22-cell Yahoo row into a 16-cell claim would overstate this
        source's coverage.
        """

        return coverage_report(
            self.snapshots,
            now=self.fetched_at or utc_now(),
            ttl_s=TTL_S,
        )

    def as_dict(self) -> dict[str, Any]:
        """A summary: counts, per-source histogram, coverage, holes — no dump."""

        document: dict[str, Any] = {
            "source": SCAN_SOURCE,
            "region": self.region,
            "rows": self.rows,
            "total_count": self.total_count,
            "issuers": self.issuers,
            "duplicates": self.duplicates,
            "truncated": self.truncated,
            "limit": self.limit,
            "columns": list(self.columns),
            "requested": self.requested,
            "written": self.written,
            "kept": self.kept,
            "kept_sources": self.kept_sources,
            "empty": self.empty,
            "missing": self.missing,
            "filled_cells": self.filled_cells,
            "missing_fields": list(self.missing_fields),
            "coverage": self.coverage(),
            "sample": list(self.symbols[:SAMPLE_SYMBOLS]),
            "anomalies": [
                item.as_dict()
                for item in self.results
                if item.status in {EMPTY, MISSING}
            ],
        }
        if self.fetched_at is not None:
            document["fetched_at"] = iso(self.fetched_at)
        if self.elapsed_s is not None:
            document["elapsed_s"] = self.elapsed_s
        return document


# ---------------------------------------------------------------------------
# The raw-row index
# ---------------------------------------------------------------------------


def _index_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    """Raw rows by canonical symbol, in scan order (first occurrence first)."""

    index: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        token = str(row.get("symbol") or "").strip()
        if not token:
            continue
        index.setdefault(canonical_symbol(token), []).append(row)
    return index


def _row_for(
    candidates: Sequence[Mapping[str, Any]], exchange: Any
) -> Mapping[str, Any] | None:
    """The raw row behind a universe symbol, preferring the universe's venue.

    One ticker can be listed on more than one venue; the universe keeps the
    representative (US listing, most traded) and its ``exchange`` is a MIC. The
    scan row for that same MIC is the one the universe actually gated, so the
    cells copied are the ones the row was chosen for — falling back to the first
    occurrence when the provider's code does not map to a MIC.
    """

    wanted = str(exchange or "").strip().upper()
    for row in candidates:
        if wanted and mic_for(row.get("exchange")) == wanted:
            return row
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# The fill
# ---------------------------------------------------------------------------


async def build_scan_snapshots(
    store: SqliteFundamentalsStore,
    *,
    region: str = DEFAULT_REGION,
    fetch: ScanFetcher | None = None,
    limit: int | None = None,
    skip_fresh: bool = True,
    protect: frozenset[str] = PROTECTED_SOURCES,
    clock: Callable[[], datetime] | None = None,
    min_market_cap: Any = DEFAULT_FLOOR,
    columns: Sequence[str] = SCAN_COLUMNS,
    ttl_s: int = TTL_S,
) -> ScanReport:
    """Read one scan, gate it into the universe, and fill every missing symbol.

    ``limit`` is applied **after** the gate and the symbol dedupe, exactly like
    ``fetch_us_symbols``: ``limit=100`` means the 100 largest US common stocks a
    screen would consider, not the first 100 raw rows (which include pink sheets
    and funds the gate then removes).

    ``skip_fresh`` keeps any symbol whose stored row is inside its 24 h window,
    with no scan lookup beyond the one page already paid for. ``protect`` is the
    narrower rule that outlives ``--refresh``: a fresh row from a protected
    source (:data:`PROTECTED_SOURCES` — Yahoo, 22 cells) is kept even when the
    caller asked to rebuild, because replacing it with a 16-cell scan row is a
    downgrade of the row that already answers.

    A row that maps to **no** cell is reported ``empty`` and not written: a row
    of nulls would claim an answer nobody gave, which is the same rule the
    ladder-fed builder applies to an all-rungs-failed walk.
    """

    if region not in UNIVERSE_REGIONS:
        raise ValueError(
            f"unknown universe region {region!r}; expected one of "
            f"{', '.join(UNIVERSE_REGIONS)}"
        )
    if limit is not None and int(limit) < 0:
        raise ValueError(f"limit must be a non-negative integer, got {limit!r}")
    if ttl_s <= 0:
        raise ValueError("ttl_s must be positive")
    wanted_limit = int(limit) if limit else None
    requested_columns = tuple(columns)
    moment = _as_utc((clock or utc_now)())
    started = time.monotonic()

    scan = fetch or fetch_all
    page = await scan(
        region, columns=requested_columns, min_market_cap=min_market_cap
    )
    raw_rows = list(page.rows)
    if not raw_rows and page.total_count:
        raise UniverseError(
            f"the scanner promised {int(page.total_count):,} rows for {region} but "
            "sent none; refusing to build an empty universe"
        )

    universe = build_universe(
        raw_rows,
        region=region,
        columns=requested_columns,
        min_market_cap=min_market_cap,
    )
    index = _index_rows(raw_rows)
    existing = {snapshot.symbol: snapshot for snapshot in await store.rows()}

    results: list[ScanResult] = []
    written_rows: list[FundamentalsSnapshot] = []
    seen: set[str] = set()
    duplicates = 0

    for candidate in universe:
        token = str(candidate.get("symbol") or "").strip()
        if not token:
            continue
        key = canonical_symbol(token)
        if key in seen:
            duplicates += 1
            continue
        if wanted_limit is not None and len(seen) >= wanted_limit:
            break
        seen.add(key)

        row = _row_for(index.get(key, ()), candidate.get("exchange"))
        if row is None:  # pragma: no cover - the universe is built from these rows
            results.append(
                ScanResult(
                    symbol=key,
                    status=MISSING,
                    reason="the universe kept a symbol with no raw scan row",
                )
            )
            continue

        stored = existing.get(key)
        if stored is not None and stored.is_fresh(moment):
            protected = (stored.source or "").lower() in protect
            if skip_fresh or protected:
                results.append(
                    ScanResult(
                        symbol=key,
                        status=KEPT,
                        filled=stored.field_count,
                        source=stored.source,
                        kept_source=stored.source,
                        reason=(
                            f"fresh {stored.source or 'unknown'} row from "
                            f"{iso(stored.fetched_at)} is inside its {ttl_s}s window"
                            if protected
                            else "a fresh row already answers for this symbol"
                        ),
                    )
                )
                continue

        snapshot = snapshot_from_scan_row(row, symbol=key, fetched_at=moment, ttl_s=ttl_s)
        if snapshot.field_count == 0:
            results.append(
                ScanResult(
                    symbol=key,
                    status=EMPTY,
                    reason="the scan row served none of the 16 fundamental cells",
                )
            )
            continue

        written = await store.upsert(snapshot)
        if written:
            written_rows.append(snapshot)
            results.append(
                ScanResult(
                    symbol=key,
                    status=WRITTEN,
                    filled=snapshot.field_count,
                    source=SCAN_SOURCE,
                )
            )
        else:
            results.append(
                ScanResult(
                    symbol=key,
                    status=KEPT,
                    filled=snapshot.field_count,
                    source=snapshot.source,
                    kept_source=SCAN_SOURCE,
                    reason="stored row is newer than this scan; it was not overwritten",
                )
            )

    report = ScanReport(
        region=region,
        rows=len(raw_rows),
        total_count=int(page.total_count),
        issuers=len(universe),
        duplicates=duplicates,
        columns=requested_columns,
        results=tuple(results),
        limit=wanted_limit,
        snapshots=tuple(written_rows),
        fetched_at=moment,
        elapsed_s=max(0.0, time.monotonic() - started),
    )
    logger.info(
        "fundamentals scan region=%s rows=%d issuers=%d requested=%d written=%d "
        "kept=%d empty=%d missing=%d",
        report.region,
        report.rows,
        report.issuers,
        report.requested,
        report.written,
        report.kept,
        report.empty,
        report.missing,
    )
    return report


__all__ = [
    "EMPTY",
    "GATE_COLUMNS",
    "KEPT",
    "MISSING",
    "PROTECTED_SOURCES",
    "SAMPLE_SYMBOLS",
    "SCAN_COLUMNS",
    "SCAN_FIELD_MAP",
    "SCAN_SCALE",
    "SCAN_SOURCE",
    "SCAN_STATUSES",
    "UNSERVED_FIELDS",
    "WRITTEN",
    "ScanFetcher",
    "ScanReport",
    "ScanResult",
    "build_scan_snapshots",
    "mapped_fields",
    "scan_cell",
    "snapshot_from_scan_row",
]
