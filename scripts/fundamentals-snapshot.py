#!/usr/bin/env python3
"""Build fundamentals snapshots for a **configurable** symbol list (P11A).

Twenty symbols, not the 5,600-symbol universe: a fundamentals snapshot costs one
provider walk per symbol, so the CLI is for the display-sized list the user
actually asked about. The default is twenty US mega caps; ``--symbols``,
``--symbols-file`` and ``--limit`` decide otherwise.

What it does, in order:

1. brings the database to ``head`` through Alembic (the live dev database is at
   ``0002``; the fundamentals table arrives in ``0003``) — never a second schema
   definition, and never a silent ``create_all``;
2. builds one snapshot per symbol through ``Runtime.ladder()`` (the same ladder
   the worker uses: ``sec_edgar`` has no adapter, so ``yahoo`` answers), 24 h
   TTL, idempotent upsert, nulls for cells the provider does not serve;
3. prints the measured field coverage of the rows that are now fresh — the
   number to quote, instead of "22 columns exist".

Exit codes: ``0`` every requested symbol has a snapshot; ``1`` at least one
``empty``/``failed``; ``2`` nothing could be built (or the command is unusable).

Examples
--------
    .venv/bin/python scripts/fundamentals-snapshot.py
    .venv/bin/python scripts/fundamentals-snapshot.py --symbols AAPL,MSFT,NVDA
    .venv/bin/python scripts/fundamentals-snapshot.py --symbols-file watchlist.txt --limit 10 --json
    .venv/bin/python scripts/fundamentals-snapshot.py --report-only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # runnable as a file, not only as a module
    sys.path.insert(0, str(ROOT))

from app.fundamentals.snapshot import (  # noqa: E402
    FUNDAMENTAL_FIELDS,
    TTL_S,
    BuildReport,
    FundamentalsSchemaError,
    FundamentalsSnapshotBuilder,
    SqliteFundamentalsStore,
    apply_migrations,
    canonical_symbol,
    migration_hint,
)

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


def render_human(
    *,
    db_path: Path,
    revision: str | None,
    report: BuildReport | None,
    coverage: dict[str, Any],
) -> str:
    lines: list[str] = []
    lines.append(
        f"fundamentals snapshot (P11A)  db={db_path}  head={revision or '?'}  ttl={TTL_S}s"
    )
    lines.append(
        f"symbols={len(report.results)}"
        if report is not None
        else f"symbols={coverage['symbols']} (stored)"
    )
    lines.append("")
    if report is not None:
        header = f"{'symbol':<10} {'status':<9} {'filled':>7} {'rung':<8} {'req':>4}  reason"
        lines.append(header)
        for item in report.results:
            lines.append(
                f"{item.symbol:<10} {item.status:<9} "
                f"{item.filled:>3}/{len(FUNDAMENTAL_FIELDS):<3} "
                f"{(item.rung or '-'):<8} {item.requests:>4}  {item.reason or ''}"
            )
        lines.append("")
        lines.append(
            "build: "
            f"requested={report.requested} built={report.built} written={report.written} "
            f"cached={report.cached} empty={report.empty} failed={report.failed} "
            f"requests={report.requests}"
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
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    from app.config import Settings
    from app.runtime import Runtime

    settings = Settings(db_path=Path(args.db)) if args.db else Settings()
    db_path = Path(settings.db_path).resolve()

    before = current_revision(db_path)
    revision = before
    try:
        revision = apply_migrations(db_path)
    except Exception as exc:  # noqa: BLE001 - a migration failure is the answer
        print(f"migration failed for {db_path}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    symbols = load_symbols(args) if not args.report_only else []
    store = SqliteFundamentalsStore(db_path)
    report: BuildReport | None = None
    try:
        if not args.report_only:
            builder = FundamentalsSnapshotBuilder(
                ladder=Runtime(settings).ladder(), store=store, ttl_s=TTL_S
            )
            report = await builder.build_many(
                symbols, concurrency=args.concurrency, skip_fresh=not args.refresh
            )
        coverage = await store.coverage()
    except FundamentalsSchemaError as exc:
        print(f"{exc}\nhint: {migration_hint(db_path)}", file=sys.stderr)
        return 2
    finally:
        await store.aclose()

    if args.json:
        document: dict[str, Any] = {
            "db": str(db_path),
            "revision": {"before": before, "after": revision},
            "ttl_s": TTL_S,
            "symbols": symbols or None,
            "coverage": coverage,
        }
        if report is not None:
            document["build"] = report.as_dict()
        print(json.dumps(document, indent=2, sort_keys=True, allow_nan=False))
    else:
        print(
            render_human(
                db_path=db_path,
                revision=revision,
                report=report,
                coverage=coverage,
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
            "configurable symbol list through the runtime provider ladder."
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
        "--limit", type=int, default=0, help="keep at most N symbols (0 = all)"
    )
    parser.add_argument(
        "--db", help="SQLite path (default: MEE_DB_PATH or ./ztockz.db)"
    )
    parser.add_argument(
        "--concurrency", type=int, default=4, help="in-flight walks (default 4)"
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
    return asyncio.run(run(args))


if __name__ == "__main__":  # pragma: no cover - entrypoint
    raise SystemExit(main())
