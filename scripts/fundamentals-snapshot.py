#!/usr/bin/env python3
"""Build fundamentals snapshots for a symbol list **or** the US universe.

Three modes, one store:

* **symbols** (P11A, the default) — a configurable list: ``--symbols``,
  ``--symbols-file``, or the twenty US mega caps. A fundamentals snapshot costs
  one provider walk per symbol, so this is the display-sized path.
* **universe** (P11C) — ``--universe us`` takes the symbol list from the screen
  engine's own TradingView scan (``fetch_all`` with the parity columns, then
  ``build_universe``), dedupes it on the storage key, and builds one snapshot
  each. ``--limit`` (the 100 largest symbols, say) and ``--time-budget`` are how
  a pilot stays a pilot: a full US universe is thousands of Yahoo walks and the
  command refuses to start one without an explicit bound.
* **universe + ``--source scan``** (P11D) — the bulk *fallback*: the same
  universe, but the cells come from that one scan instead of a walk per symbol.
  Live, the Yahoo rung was throttled after 1,266 rows; the scanner answers the
  whole US market (3,888 issuers) in two requests and fills 16 of the 22 cells
  (``app.fundamentals.scan`` names the six it cannot). One page is not one walk
  per symbol, so this mode needs no ``--limit``/``--time-budget``/``--all``
  bound, but it refuses ``--symbols``: the symbols *are* the scan.

Why per-symbol Yahoo walks instead of the scan's own bulk columns: the parity
scan carries price multiples and technicals, not the 22-cell
fundamentals/statements vocabulary, and several bulk cells a screen could use
were probed null for every row. A *fundamentals* scan asking for its own columns
is a different request, and the one P11D makes: sixteen cells are filled for
most rows, the six the provider never serves stay null. The scan is a fallback,
never a replacement — a fresh 22-cell Yahoo row is kept, whatever the refresh
flag says, because one row per symbol means the better row must win. The bill
for the Yahoo detail is one walk per symbol — hence the plan line, the limit, the
budget and ``skip_fresh`` (a 24 h row answers with zero requests, so a re-run
resumes).

What it does, in order:

1. brings the database to ``head`` through Alembic (the live dev database is at
   ``0002``; the fundamentals table arrives in ``0003``) — never a second schema
   definition, and never a silent ``create_all``;
2. resolves the symbols (scan or list), then builds one snapshot per symbol
   through ``Runtime.ladder()`` (the same ladder the worker uses: ``sec_edgar``
   has no adapter, so ``yahoo`` answers), 24 h TTL, idempotent upsert, nulls for
   cells the provider does not serve — or, with ``--source scan``, maps one
   scanner row per symbol into the same snapshot shape (``source='tradingview'``,
   16 cells, the rest null);
3. reports ``requested/built/written/cached/failed/requests``, the elapsed time,
   the ETA for a deferred tail, and the measured field coverage of the rows that
   are now fresh — the numbers to quote, instead of "22 columns exist". Coverage
   is reported **per cell source** as well as per field (``0004_field_sources``):
   the ``sources`` histogram says which source won each row, the ``cells by
   source`` column says who supplied each number, and ``unattributed`` counts the
   filled cells with no record instead of crediting them to a guess. Scan mode
   reports per source (written vs kept, and which source kept each symbol), the
   coverage of the rows it wrote, and the fields the source never serves.

Exit codes: ``0`` every attempted symbol has a snapshot (a deferred tail from a
time budget is not a failure — the next run resumes it); ``1`` at least one
``empty``/``failed``; ``2`` nothing could be built (or the command is unusable,
or the scanner/universe could not be read).

Examples
--------
    .venv/bin/python scripts/fundamentals-snapshot.py
    .venv/bin/python scripts/fundamentals-snapshot.py --symbols AAPL,MSFT,NVDA
    .venv/bin/python scripts/fundamentals-snapshot.py --symbols-file watchlist.txt --limit 10 --json
    .venv/bin/python scripts/fundamentals-snapshot.py --report-only
    .venv/bin/python scripts/fundamentals-snapshot.py --universe us --limit 100 --progress-every 10
    .venv/bin/python scripts/fundamentals-snapshot.py --universe us --time-budget 900 --json
    .venv/bin/python scripts/fundamentals-snapshot.py --universe us --source scan
    .venv/bin/python scripts/fundamentals-snapshot.py --universe us --source scan --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # runnable as a file, not only as a module
    sys.path.insert(0, str(ROOT))

from app.fundamentals.snapshot import (  # noqa: E402
    CACHED,
    EMPTY,
    FAILED,
    FUNDAMENTAL_FIELDS,
    TTL_S,
    BuildReport,
    BuildResult,
    FundamentalsSchemaError,
    FundamentalsSnapshotBuilder,
    SqliteFundamentalsStore,
    apply_migrations,
    canonical_symbol,
    migration_hint,
)
from app.fundamentals.scan import (  # noqa: E402
    EMPTY as SCAN_EMPTY,
    MISSING as SCAN_MISSING,
    SCAN_SOURCE,
    ScanReport,
    build_scan_snapshots,
)
from app.fundamentals.universe import (  # noqa: E402
    UNIVERSE_REGIONS,
    UniverseError,
    UniversePlan,
    fetch_us_symbols,
)
from app.screen.scanner import ScanError  # noqa: E402

#: The two symbol sources. ``ladder`` (the default) is the per-symbol provider
#: walk — Yahoo in production, 22 cells, one request per symbol. ``scan`` is
#: P11D's bulk fallback: one TradingView scan, 16 cells per row, no walk per
#: symbol. ``yahoo`` is accepted as an explicit spelling of ``ladder``.
SOURCE_LADDER = "ladder"
SOURCE_SCAN = "scan"
SOURCE_ALIASES: dict[str, str] = {
    "ladder": SOURCE_LADDER,
    "yahoo": SOURCE_LADDER,
    "scan": SOURCE_SCAN,
}
SOURCE_CHOICES: tuple[str, ...] = ("ladder", "yahoo", "scan")
DEFAULT_SOURCE = SOURCE_LADDER


def normalize_source(value: str | None) -> str:
    """``--source`` as one of the two real sources (``yahoo`` → ``ladder``)."""

    token = str(value or DEFAULT_SOURCE).strip().lower()
    try:
        return SOURCE_ALIASES[token]
    except KeyError:  # pragma: no cover - argparse restricts the choices
        raise ValueError(
            f"unknown source {value!r}; expected one of {', '.join(SOURCE_CHOICES)}"
        ) from None


#: The default list: the twenty US mega caps a display would show. This is a
#: sample, not a universe — ``--symbols``/``--symbols-file`` replace it whole.
DEFAULT_SYMBOLS: tuple[str, ...] = (
    "AAPL",
    "MSFT",
    "NVDA",
    "GOOGL",
    "AMZN",
    "META",
    "AVGO",
    "TSLA",
    "BRK.B",
    "LLY",
    "JPM",
    "V",
    "UNH",
    "XOM",
    "MA",
    "WMT",
    "JNJ",
    "PG",
    "ORCL",
    "HD",
)

#: Eight in-flight walks is the ceiling for the bulk mode: every walk is a
#: provider request, and the Yahoo rung is rate-limited rather than free. The
#: default stays four (the screen engine's own batch size).
MAX_UNIVERSE_CONCURRENCY = 8

#: How often (in completed symbols) the bulk mode prints a progress line.
DEFAULT_PROGRESS_EVERY = 25

#: How many per-symbol rows the human report prints. A 3,000-symbol bulk run
#: wants a summary, not a scrollback; ``--json`` carries every result.
SYMBOL_TABLE_LIMIT = 40


def _symbols_from_text(text: str) -> list[str]:
    """Comma- or whitespace-separated tokens, upper-cased, blanks dropped."""

    tokens = text.replace("\n", ",").replace("\t", ",").split(",")
    cleaned = [token.strip().upper() for token in tokens if token.strip()]
    return [canonical_symbol(token) for token in cleaned]


def load_symbols(args: argparse.Namespace) -> list[str]:
    """Resolve the requested list: ``--symbols`` wins, then the file, then default."""

    if args.symbols:
        symbols = _symbols_from_text(args.symbols)
    elif args.symbols_file:
        path = Path(args.symbols_file)
        lines = [
            line.split("#", 1)[0]
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        symbols = _symbols_from_text(",".join(lines))
    else:
        symbols = [canonical_symbol(symbol) for symbol in DEFAULT_SYMBOLS]

    unique: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        if symbol not in seen:
            seen.add(symbol)
            unique.append(symbol)
    if args.limit and args.limit > 0:
        unique = unique[: args.limit]
    return unique


def universe_guard(args: argparse.Namespace) -> str | None:
    """The bulk mode's bounds, in one place; returns a refusal or ``None``.

    Four rules, each a real accident that would otherwise reach the provider:

    * ``--source scan`` has no per-symbol mode: its symbols *are* the region
      scan, so it needs ``--universe`` and cannot take a list;
    * ``--universe`` and ``--symbols`` are two answers to "which symbols"; taking
      both would silently build one and ignore the other;
    * more than eight in-flight walks is not a pilot and is what gets the rung
      throttled;
    * an unbounded *walk* is thousands of requests. ``--limit``,
      ``--time-budget`` or an explicit ``--all`` must say the operator meant it.
      Scan mode is exempt: one scanner page (two for the whole US market) is not
      one walk per symbol, so there is nothing to bound.
    """

    if args.report_only:
        return None
    source = normalize_source(getattr(args, "source", DEFAULT_SOURCE))
    if source == SOURCE_SCAN and not getattr(args, "universe", None):
        return (
            "--source scan builds the universe from the region scan; pass "
            "--universe us (scan mode has no per-symbol list)"
        )
    if not getattr(args, "universe", None):
        return None
    if args.symbols or args.symbols_file:
        return (
            "--universe replaces --symbols/--symbols-file; pass one symbol source, "
            "not two"
        )
    if source == SOURCE_SCAN:
        return None
    if args.concurrency < 1 or args.concurrency > MAX_UNIVERSE_CONCURRENCY:
        return (
            f"--concurrency must be between 1 and {MAX_UNIVERSE_CONCURRENCY} for "
            f"--universe (got {args.concurrency})"
        )
    if not args.limit and not args.time_budget and not args.all:
        return (
            "refusing to walk the whole US universe without a bound: pass --limit N "
            "for a pilot (for example --limit 100), --time-budget SECONDS for a "
            "bounded batch, or --all to accept one provider walk per symbol "
            "(or --source scan for the bulk fallback: one scanner request, 16 of "
            "the 22 cells)"
        )
    return None


def format_duration(seconds: float | None) -> str:
    """``0:42``/``1:02:03``; ``?`` when there is nothing to measure yet."""

    if seconds is None:
        return "?"
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def progress_line(
    *,
    completed: int,
    total: int,
    built: int,
    cached: int,
    failed: int,
    elapsed_s: float,
    eta_s: float | None,
    width: int = 12,
) -> str:
    """One progress line: how far, how it went, how long, and how much is left."""

    share = (100.0 * completed / total) if total else 100.0
    return (
        f"[{completed:>{width}}/{total}] {share:5.1f}% built={built} cached={cached} "
        f"failed={failed} elapsed={format_duration(elapsed_s)} "
        f"eta={format_duration(eta_s)}"
    )


def progress_reporter(*, every: int, started: float) -> Callable[..., None]:
    """A ``build_many(on_result=...)`` callback that prints to **stderr**.

    Progress is diagnostics: on stdout it would corrupt ``--json``. The counters
    are the batch's own statuses, so the line says ``cached`` when the 24 h store
    answered and never counts a deferred symbol as failed.
    """

    counters = {"built": 0, "cached": 0, "failed": 0}

    def report(result: BuildResult, completed: int, total: int) -> None:
        if result.status == CACHED:
            counters["cached"] += 1
        elif result.ok:
            counters["built"] += 1
        elif result.status in {FAILED, EMPTY}:
            counters["failed"] += 1
        if every <= 0:
            return
        if completed % every and completed != total:
            return
        elapsed = max(0.0, time.monotonic() - started)
        rate = (completed / elapsed) if elapsed > 0 else 0.0
        remaining = total - completed
        eta = (remaining / rate) if rate > 0 and remaining else 0.0
        print(
            progress_line(
                completed=completed,
                total=total,
                built=counters["built"],
                cached=counters["cached"],
                failed=counters["failed"],
                elapsed_s=elapsed,
                eta_s=eta,
            ),
            file=sys.stderr,
            flush=True,
        )

    return report


def current_revision(db_path: Path) -> str | None:
    """The Alembic revision on disk, read without opening a provider."""

    if not db_path.exists():
        return None
    with closing(sqlite3.connect(db_path)) as connection:
        try:
            rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        except sqlite3.DatabaseError:
            return None
    return str(rows[0][0]) if rows else None


def _percent(filled: int, total: int) -> str:
    return f"{(100.0 * filled / total):5.1f}%" if total else "    -"


def _provenance(coverage: dict[str, Any]) -> dict[str, Any]:
    """The coverage document's per-cell provenance block (``0004``), or empty.

    A caller that hands this module a coverage document from an older build has
    no block; the report then says ``-`` instead of inventing a source.
    """

    block = coverage.get("field_sources")
    return block if isinstance(block, dict) else {}


def _source_cells(row: Mapping[str, Any], provenance: dict[str, Any]) -> str:
    """``yahoo=8 tradingview=4`` for one field, plus its unattributed cells.

    Cells, not rows: one merged row can carry several sources, so this is the
    column that answers "who said this number" while the ``sources`` histogram
    above answers "which source won this row".
    """

    entry = (provenance.get("fields") or {}).get(row.get("field")) or {}
    parts = [
        f"{source}={count}"
        for source, count in (entry.get("sources") or {}).items()
    ]
    if entry.get("unattributed"):
        parts.append(f"unattributed={entry['unattributed']}")
    return " ".join(parts) or "-"


def _provenance_line(coverage: dict[str, Any]) -> str:
    """One line: how many cells are attributed, to whom, and how many are not."""

    block = _provenance(coverage)
    cells = block.get("cells") or {}
    unattributed = int(block.get("unattributed") or 0)
    attributed = int(block.get("attributed") or 0)
    return (
        f"provenance: attributed={attributed} unattributed={unattributed} "
        f"cells by source: {cells or '{}'}"
    )


def _table_rows(results: Sequence[BuildResult], limit: int) -> tuple[list[BuildResult], int]:
    """The rows the human report prints, and how many were elided.

    First N in scan order: the summary already carries the failure counts, and a
    bulk run's per-symbol table is a spot check — ``--json`` is the full record.
    """

    rows = list(results)
    if len(rows) <= limit:
        return rows, 0
    return rows[:limit], len(rows) - limit


def render_human(
    *,
    db_path: Path,
    revision: str | None,
    report: BuildReport | None,
    coverage: dict[str, Any],
    plan: UniversePlan | None = None,
    elapsed_s: float | None = None,
    eta_s: float | None = None,
    universe_mode: bool = False,
) -> str:
    lines: list[str] = []
    mode = "P11C bulk" if universe_mode or plan is not None else "P11A"
    lines.append(
        f"fundamentals snapshot ({mode})  db={db_path}  head={revision or '?'}  ttl={TTL_S}s"
    )
    if plan is not None:
        limit = plan.limit if plan.limit is not None else "all"
        lines.append(
            f"universe: {plan.region} rows={plan.rows} total={plan.total_count} "
            f"issuers={plan.issuers} dropped={plan.dropped} "
            f"duplicates={plan.duplicates} limit={limit} symbols={len(plan.symbols)}"
        )
        if plan.truncated:
            lines.append(
                f"  --limit kept the {len(plan.symbols)} largest of {plan.issuers} "
                "symbols (the gate ran before the limit, not after it)"
            )
        if report is not None:
            lines.append(
                f"  requested={report.requested} attempted={report.attempted} "
                f"deferred={len(report.deferred)}"
            )
    else:
        lines.append(
            f"symbols={len(report.results)}"
            if report is not None
            else f"symbols={coverage['symbols']} (stored)"
        )
    lines.append("")
    if report is not None:
        header = f"{'symbol':<10} {'status':<9} {'filled':>7} {'rung':<8} {'req':>4}  reason"
        lines.append(header)
        shown, elided = _table_rows(report.results, SYMBOL_TABLE_LIMIT)
        for item in shown:
            lines.append(
                f"{item.symbol:<10} {item.status:<9} "
                f"{item.filled:>3}/{len(FUNDAMENTAL_FIELDS):<3} "
                f"{(item.rung or '-'):<8} {item.requests:>4}  {item.reason or ''}"
            )
        if elided:
            lines.append(
                f"… {elided} more result(s) elided; --json prints every result"
            )
        lines.append("")
        lines.append(
            "build: "
            f"requested={report.requested} built={report.built} written={report.written} "
            f"cached={report.cached} empty={report.empty} failed={report.failed} "
            f"deferred={len(report.deferred)} requests={report.requests}"
        )
        lines.append(
            f"elapsed={format_duration(elapsed_s)} eta={format_duration(eta_s)}"
            + (
                f"  (eta covers {len(report.deferred)} deferred symbol(s) at this rate)"
                if report.deferred
                else ""
            )
        )
        if report.dropped:
            lines.append(f"dropped blank token(s): {list(report.dropped)!r}")
        lines.append("")

    lines.append(
        "coverage: "
        f"rows={coverage['symbols']} fresh={coverage['fresh']} expired={coverage['expired']} "
        f"complete={coverage['complete']} partial={coverage['partial']} "
        f"filled={coverage['filled_cells']}/{coverage['total_cells']} "
        f"fill_rate={coverage['fill_rate']:.4f}"
    )
    lines.append(f"sources: {coverage['sources']}")
    lines.append(_provenance_line(coverage))
    if coverage["oldest_as_of"]:
        lines.append(
            f"as_of: {coverage['oldest_as_of']} .. {coverage['newest_as_of']} "
            "(data clock, not the fetch clock)"
        )
    lines.append("")
    provenance = _provenance(coverage)
    lines.append(f"{'field':<22} {'filled':>10}  coverage  cells by source")
    for row in coverage["fields"]:
        lines.append(
            f"{row['field']:<22} {row['filled']:>4}/{row['total']:<5}  "
            f"{_percent(row['filled'], row['total'])}  {_source_cells(row, provenance)}"
        )
    lines.append("")
    lines.append(
        "note: 22 canonical cells per symbol is the vocabulary; the filled column is "
        "what the provider actually served, and 'cells by source' is who served each one."
    )
    if plan is not None:
        lines.append(
            "note: one provider walk per symbol — the scan chose the symbols, the "
            "per-symbol rung filled the 22 cells."
        )
    return "\n".join(lines)


def render_scan_human(
    *,
    db_path: Path,
    revision: str | None,
    scan: ScanReport,
    coverage: dict[str, Any],
    scan_elapsed_s: float | None = None,
) -> str:
    """The P11D fallback report: per-source, coverage, and the fields it cannot serve.

    Two coverage blocks, deliberately separate: the scan's own rows (what this
    source actually filled, 16 cells at best) and the store's whole table (every
    source, which is what a reader sees afterwards). Adding them together would
    hide that a 16-cell row and a 22-cell row are different answers.
    """

    lines: list[str] = []
    limit = scan.limit if scan.limit is not None else "all"
    lines.append(
        f"fundamentals snapshot (P11D scan)  db={db_path}  head={revision or '?'}  "
        f"ttl={TTL_S}s  source={SCAN_SOURCE}"
    )
    lines.append(
        f"scan: {scan.region} rows={scan.rows} total={scan.total_count} "
        f"issuers={scan.issuers} duplicates={scan.duplicates} "
        f"columns={len(scan.columns)} limit={limit}"
    )
    if scan.truncated:
        lines.append(
            f"  --limit kept the {scan.requested} largest of {scan.issuers} "
            "symbols (the gate ran before the limit, not after it)"
        )
    lines.append("")
    lines.append(
        f"build: requested={scan.requested} written={scan.written} "
        f"kept={scan.kept} empty={scan.empty} missing={scan.missing} "
        f"elapsed={format_duration(scan.elapsed_s)} "
        f"scan_elapsed={format_duration(scan_elapsed_s)}"
    )
    lines.append(
        f"per source: written({SCAN_SOURCE})={scan.written} "
        f"kept={scan.kept_sources}"
        + (
            "  (a fresh row is an answer: the scan did not replace it)"
            if scan.kept
            else ""
        )
    )
    if scan.empty or scan.missing:
        for item in scan.results:
            if item.status in {SCAN_EMPTY, SCAN_MISSING}:
                lines.append(f"  {item.symbol}: {item.status} — {item.reason or ''}")
    lines.append("")

    own = scan.coverage()
    lines.append(
        f"coverage (rows this scan wrote): rows={own['symbols']} "
        f"fresh={own['fresh']} complete={own['complete']} partial={own['partial']} "
        f"filled={own['filled_cells']}/{own['total_cells']} "
        f"fill_rate={own['fill_rate']:.4f}"
    )
    lines.append(
        f"  not served by {SCAN_SOURCE} ({len(scan.missing_fields)}): "
        f"{', '.join(scan.missing_fields)}"
    )
    lines.append("")
    lines.append(f"{'field':<22} {'filled':>10}  coverage  cells by source")
    provenance = _provenance(own)
    for row in own["fields"]:
        lines.append(
            f"{row['field']:<22} {row['filled']:>4}/{row['total']:<5}  "
            f"{_percent(row['filled'], row['total'])}  {_source_cells(row, provenance)}"
        )
    lines.append("")
    lines.append(
        "coverage (store): "
        f"rows={coverage['symbols']} fresh={coverage['fresh']} "
        f"expired={coverage['expired']} complete={coverage['complete']} "
        f"partial={coverage['partial']} "
        f"filled={coverage['filled_cells']}/{coverage['total_cells']} "
        f"fill_rate={coverage['fill_rate']:.4f}"
    )
    lines.append(f"sources: {coverage['sources']}")
    lines.append(_provenance_line(coverage))
    if coverage["oldest_as_of"]:
        lines.append(
            f"as_of: {coverage['oldest_as_of']} .. {coverage['newest_as_of']} "
            "(data clock, not the fetch clock)"
        )
    lines.append("")
    lines.append(
        f"note: 16 of the 22 canonical cells come from one scanner request per page "
        f"({len(scan.columns)} columns); the six listed above are never served by "
        f"{SCAN_SOURCE} and stay null."
    )
    lines.append(
        "note: a fresh row whose source is protected (yahoo, 22 cells) is kept even "
        "with --refresh; the scan only fills symbols with no fresh answer."
    )
    return "\n".join(lines)


async def run(
    args: argparse.Namespace,
    *,
    ladder: Any | None = None,
    universe_fetch: Any | None = None,
    scan_fetch: Any | None = None,
) -> int:
    """Execute one invocation; the three keyword seams are the test doubles."""

    from app.config import Settings

    overrides: dict[str, Any] = {}
    if args.db:
        overrides["db_path"] = Path(args.db)
    if args.proxy_pool:
        overrides["proxy_pool"] = Path(args.proxy_pool)
    settings = Settings(**overrides) if overrides else Settings()
    db_path = Path(settings.db_path).resolve()
    source = normalize_source(getattr(args, "source", DEFAULT_SOURCE))

    before = current_revision(db_path)
    revision = before
    try:
        revision = apply_migrations(db_path)
    except Exception as exc:  # noqa: BLE001 - a migration failure is the answer
        print(f"migration failed for {db_path}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    plan: UniversePlan | None = None
    scan_report: ScanReport | None = None
    scan_elapsed: float | None = None
    symbols: list[str] = []
    if not args.report_only and source != SOURCE_SCAN:
        if args.universe:
            scan_started = time.monotonic()
            try:
                plan = await fetch_us_symbols(
                    limit=args.limit or None, fetch=universe_fetch
                )
            except (UniverseError, ScanError) as exc:
                print(f"the {args.universe} universe could not be read: {exc}", file=sys.stderr)
                return 2
            scan_elapsed = time.monotonic() - scan_started
            symbols = list(plan.symbols)
            if not symbols:
                print(
                    f"the {plan.region} universe answered no symbols "
                    f"(rows={plan.rows}, issuers={plan.issuers}); nothing to build",
                    file=sys.stderr,
                )
                return 2
        else:
            symbols = load_symbols(args)

    store = SqliteFundamentalsStore(db_path)
    report: BuildReport | None = None
    try:
        if not args.report_only:
            if source == SOURCE_SCAN:
                scan_started = time.monotonic()
                try:
                    scan_report = await build_scan_snapshots(
                        store,
                        region=args.universe,
                        fetch=scan_fetch,
                        limit=args.limit or None,
                        skip_fresh=not args.refresh,
                    )
                except (UniverseError, ScanError) as exc:
                    print(
                        f"the {args.universe} universe could not be read: {exc}",
                        file=sys.stderr,
                    )
                    return 2
                scan_elapsed = time.monotonic() - scan_started
                if not scan_report.requested:
                    print(
                        f"the {scan_report.region} universe answered no symbols "
                        f"(rows={scan_report.rows}, issuers={scan_report.issuers}); "
                        "nothing to build",
                        file=sys.stderr,
                    )
                    return 2
            else:
                if ladder is None:
                    from app.runtime import Runtime

                    provider: Any = Runtime(settings).ladder()
                else:
                    provider = ladder
                builder = FundamentalsSnapshotBuilder(
                    ladder=provider, store=store, ttl_s=TTL_S
                )
                reporter = (
                    progress_reporter(every=args.progress_every, started=time.monotonic())
                    if args.progress_every
                    else None
                )
                report = await builder.build_many(
                    symbols,
                    concurrency=args.concurrency,
                    skip_fresh=not args.refresh,
                    time_budget_s=args.time_budget or None,
                    on_result=reporter,
                )
        coverage = await store.coverage()
    except FundamentalsSchemaError as exc:
        print(f"{exc}\nhint: {migration_hint(db_path)}", file=sys.stderr)
        return 2
    finally:
        await store.aclose()

    elapsed_s = report.elapsed_s if report is not None else None
    eta_s = report.eta_s if report is not None else None

    if args.json:
        document: dict[str, Any] = {
            "db": str(db_path),
            "revision": {"before": before, "after": revision},
            "ttl_s": TTL_S,
            "mode": "universe" if args.universe else "symbols",
            "source": source,
            # A summary, not 3,000 symbols: the plan names the sample and the
            # count; the store is what holds the rest.
            "symbols": None if plan is not None or scan_report is not None else (symbols or None),
            "universe": None,
            "scan": None,
            "coverage": coverage,
        }
        if plan is not None:
            universe_document = plan.as_dict()
            if scan_elapsed is not None:
                universe_document["scan_elapsed_s"] = scan_elapsed
            document["universe"] = universe_document
        if scan_report is not None:
            scan_document = scan_report.as_dict()
            if scan_elapsed is not None:
                scan_document["scan_elapsed_s"] = scan_elapsed
            document["scan"] = scan_document
            document["elapsed_s"] = scan_report.elapsed_s
        if report is not None:
            document["build"] = report.as_dict()
            document["elapsed_s"] = elapsed_s
            document["eta_s"] = eta_s
        print(json.dumps(document, indent=2, sort_keys=True, allow_nan=False))
    elif scan_report is not None:
        print(
            render_scan_human(
                db_path=db_path,
                revision=revision,
                scan=scan_report,
                coverage=coverage,
                scan_elapsed_s=scan_elapsed,
            )
        )
    else:
        print(
            render_human(
                db_path=db_path,
                revision=revision,
                report=report,
                coverage=coverage,
                plan=plan,
                elapsed_s=elapsed_s,
                eta_s=eta_s,
                universe_mode=bool(args.universe),
            )
        )

    if scan_report is not None:
        # Every symbol either has a row (written or kept) or is a hole: the
        # holes decide the exit code, exactly like the ladder path.
        if scan_report.empty or scan_report.missing:
            return 1 if (scan_report.written or scan_report.kept) else 2
        return 0 if scan_report.requested else 2
    if report is None:
        return 0 if coverage["fresh"] else 2
    if report.failed or report.empty:
        return 1 if report.built or report.cached else 2
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fundamentals-snapshot",
        description=(
            "Build 24 h fundamentals snapshots (22 canonical cells) for a "
            "configurable symbol list, or for the US universe via --universe us, "
            "through the runtime provider ladder (--source ladder, one walk per "
            "symbol) or from one TradingView scan (--source scan, 16 cells)."
        ),
    )
    parser.add_argument(
        "--symbols",
        help="comma-separated symbols (default: 20 US mega caps)",
    )
    parser.add_argument(
        "--symbols-file",
        help="file with one symbol per line ('#' comments allowed)",
    )
    parser.add_argument(
        "--universe",
        choices=UNIVERSE_REGIONS,
        help=(
            "bulk mode: take the symbol list from the TradingView region scan "
            "(P11C; 'us' is the stock universe)"
        ),
    )
    parser.add_argument(
        "--source",
        choices=SOURCE_CHOICES,
        default=DEFAULT_SOURCE,
        help=(
            "where the 22 cells come from: 'ladder'/'yahoo' walks the provider "
            "once per symbol (22 cells, rate-limited); 'scan' reads one "
            "TradingView scan for the whole universe (P11D fallback: 16 cells, "
            "no per-symbol walk; requires --universe)"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help=(
            "keep at most N symbols (0 = all); with --universe the limit applies "
            "after the universe gate"
        ),
    )
    parser.add_argument(
        "--db", help="SQLite path (default: MEE_DB_PATH or ./ztockz.db)"
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help=(
            "in-flight walks (default 4; --universe caps it at "
            f"{MAX_UNIVERSE_CONCURRENCY})"
        ),
    )
    parser.add_argument(
        "--time-budget",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help=(
            "stop starting walks after N seconds; the untouched tail is reported "
            "deferred and the next run resumes it (0 = no budget)"
        ),
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help=(
            "accept an unbounded --universe walk: one provider request per symbol, "
            "thousands of requests for the full US market"
        ),
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=DEFAULT_PROGRESS_EVERY,
        metavar="N",
        help=(
            "print a progress line (stderr) every N completed symbols "
            f"(default {DEFAULT_PROGRESS_EVERY}; 0 = quiet)"
        ),
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-walk every symbol even when its row is still fresh",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="no provider request: print the stored coverage only",
    )
    parser.add_argument(
        "--proxy-pool",
        help="zcrapers-shaped pool_ranked.json (or text proxy list) for the Yahoo egress ladder",
    )
    parser.add_argument("--json", action="store_true", help="print one JSON document")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.concurrency < 1:
        print("--concurrency must be at least 1", file=sys.stderr)
        return 2
    refusal = universe_guard(args)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return 2
    return asyncio.run(run(args))


if __name__ == "__main__":  # pragma: no cover - entrypoint
    raise SystemExit(main())
