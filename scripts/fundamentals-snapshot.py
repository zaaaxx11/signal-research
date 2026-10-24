#!/usr/bin/env python3
"""Build fundamentals snapshots for a symbol list **or** the US universe.

Two modes, one store:

* **symbols** (P11A, the default) — a configurable list: ``--symbols``,
  ``--symbols-file``, or the twenty US mega caps. A fundamentals snapshot costs
  one provider walk per symbol, so this is the display-sized path.
* **universe** (P11C) — ``--universe us`` takes the symbol list from the screen
  engine's own TradingView scan (``fetch_all`` with the parity columns, then
  ``build_universe``), dedupes it on the storage key, and builds one snapshot
  each. ``--limit`` (the 100 largest symbols, say) and ``--time-budget`` are how
  a pilot stays a pilot: a full US universe is thousands of Yahoo walks and the
  command refuses to start one without an explicit bound.

Why per-symbol Yahoo walks instead of the scan's own bulk columns: the parity
scan carries price multiples and technicals, not the 22-cell
fundamentals/statements vocabulary, and several bulk cells a screen could use
were probed null for every row. The scan decides *which* symbols; the Yahoo rung
is the only source that actually fills the snapshot's cells. The bill for that
detail is one walk per symbol — hence the plan line, the limit, the budget and
``skip_fresh`` (a 24 h row answers with zero requests, so a re-run resumes).

What it does, in order:

1. brings the database to ``head`` through Alembic (the live dev database is at
   ``0002``; the fundamentals table arrives in ``0003``) — never a second schema
   definition, and never a silent ``create_all``;
2. resolves the symbols (scan or list), then builds one snapshot per symbol
   through ``Runtime.ladder()`` (the same ladder the worker uses: ``sec_edgar``
   has no adapter, so ``yahoo`` answers), 24 h TTL, idempotent upsert, nulls for
   cells the provider does not serve;
3. reports ``requested/built/written/cached/failed/requests``, the elapsed time,
   the ETA for a deferred tail, and the measured field coverage of the rows that
   are now fresh — the numbers to quote, instead of "22 columns exist".

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
from typing import Any, Callable, Sequence

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
from app.fundamentals.universe import (  # noqa: E402
    UNIVERSE_REGIONS,
    UniverseError,
    UniversePlan,
    fetch_us_symbols,
)
from app.screen.scanner import ScanError  # noqa: E402

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

    Three rules, each a real accident that would otherwise reach the provider:

    * ``--universe`` and ``--symbols`` are two answers to "which symbols"; taking
      both would silently build one and ignore the other;
    * more than eight in-flight walks is not a pilot and is what gets the rung
      throttled;
    * an unbounded walk is thousands of requests. ``--limit``, ``--time-budget``
      or an explicit ``--all`` must say the operator meant it.
    """

    if not getattr(args, "universe", None) or args.report_only:
        return None
    if args.symbols or args.symbols_file:
        return (
            "--universe replaces --symbols/--symbols-file; pass one symbol source, "
            "not two"
        )
    if args.concurrency < 1 or args.concurrency > MAX_UNIVERSE_CONCURRENCY:
        return (
            f"--concurrency must be between 1 and {MAX_UNIVERSE_CONCURRENCY} for "
            f"--universe (got {args.concurrency})"
        )
    if not args.limit and not args.time_budget and not args.all:
        return (
            "refusing to walk the whole US universe without a bound: pass --limit N "
            "for a pilot (for example --limit 100), --time-budget SECONDS for a "
            "bounded batch, or --all to accept one provider walk per symbol"
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
    if coverage["oldest_as_of"]:
        lines.append(
            f"as_of: {coverage['oldest_as_of']} .. {coverage['newest_as_of']} "
            "(data clock, not the fetch clock)"
        )
    lines.append("")
    lines.append(f"{'field':<22} {'filled':>10}  coverage")
    for row in coverage["fields"]:
        lines.append(
            f"{row['field']:<22} {row['filled']:>4}/{row['total']:<5}  "
            f"{_percent(row['filled'], row['total'])}"
        )
    lines.append("")
    lines.append(
        "note: 22 canonical cells per symbol is the vocabulary; the filled column is "
        "what the provider actually served."
    )
    if plan is not None:
        lines.append(
            "note: one provider walk per symbol — the scan chose the symbols, the "
            "per-symbol rung filled the 22 cells."
        )
    return "\n".join(lines)


async def run(
    args: argparse.Namespace,
    *,
    ladder: Any | None = None,
    universe_fetch: Any | None = None,
) -> int:
    """Execute one invocation; ``ladder``/``universe_fetch`` are the test seams."""

    from app.config import Settings

    settings = Settings(db_path=Path(args.db)) if args.db else Settings()
    db_path = Path(settings.db_path).resolve()

    before = current_revision(db_path)
    revision = before
    try:
        revision = apply_migrations(db_path)
    except Exception as exc:  # noqa: BLE001 - a migration failure is the answer
        print(f"migration failed for {db_path}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    plan: UniversePlan | None = None
    scan_elapsed: float | None = None
    symbols: list[str] = []
    if not args.report_only:
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
            # A summary, not 3,000 symbols: the plan names the sample and the
            # count; the store is what holds the rest.
            "symbols": None if plan is not None else (symbols or None),
            "universe": None,
            "coverage": coverage,
        }
        if plan is not None:
            universe_document = plan.as_dict()
            if scan_elapsed is not None:
                universe_document["scan_elapsed_s"] = scan_elapsed
            document["universe"] = universe_document
        if report is not None:
            document["build"] = report.as_dict()
            document["elapsed_s"] = elapsed_s
            document["eta_s"] = eta_s
        print(json.dumps(document, indent=2, sort_keys=True, allow_nan=False))
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
            "through the runtime provider ladder."
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
