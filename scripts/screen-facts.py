#!/usr/bin/env python3
"""Fill ``screen_facts`` from the Finviz stock page — one scrape per symbol.

The three cells the selection filters need (``Short Float``, ``Insider Own``,
``Inst Own``) are printed on ``https://finviz.com/stock?t=SYMBOL`` and nowhere in
the bulk scan. This command is the write side of :mod:`app.screen.facts`:

1. brings the database to ``head`` through Alembic (``0005_screen_facts`` creates
   the table; never a second schema definition, never ``create_all``);
2. resolves the symbol list — ``--symbols``, or the top of the live US screen
   via the same TradingView scan the selection endpoint uses;
3. fetches **one** Finviz page per symbol, reads it with
   :func:`app.providers.adapters.finviz.parse_facts` (``-``/``N/A``/absent → no
   cell, never ``0``; percent points kept as printed), and upserts a full-width
   row with ``source='finviz'`` and a 24-hour window;
4. reports the per-symbol values and the measured per-field coverage of the
   rows that are now fresh.

The page publishes no data timestamp, so ``as_of`` is the fetch clock — the only
stamp this source can prove. A challenge page, a non-200 and a page with none of
the three labels are all reported as failures and write nothing; an older row
(if any) is left standing.

Examples
--------
    .venv/bin/python scripts/screen-facts.py --top 10
    .venv/bin/python scripts/screen-facts.py --symbols AAPL,MSFT,NVDA
    .venv/bin/python scripts/screen-facts.py --top 10 --json
    .venv/bin/python scripts/screen-facts.py --report-only --top 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # runnable from anywhere
    sys.path.insert(0, str(ROOT))

from alembic import command  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.contracts import Capability  # noqa: E402
from app.db import alembic_config  # noqa: E402
from app.providers.adapters.finviz import FinvizAdapter, parse_facts  # noqa: E402
from app.providers.base import InstrumentRef  # noqa: E402
from app.screen import facts as F  # noqa: E402
from app.screen.scanner import fetch_scan  # noqa: E402
from app.screen.universe import build_universe  # noqa: E402

#: The cells this command fills, as the adapter's parse vocabulary.
WANTED: frozenset[str] = frozenset(F.FACTS_FIELDS)

#: A scrape is a polite scrape: one page every ``SLEEP_S`` seconds.
SLEEP_S = 0.4


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=None, help="SQLite path (default: MEE_DB_PATH)")
    parser.add_argument(
        "--symbols",
        default=None,
        help="comma-separated tickers; overrides --top",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="scrape the top N of the live US screen (default: 10)",
    )
    parser.add_argument("--region", default="us", help="scan region for --top (default: us)")
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="read the stored rows and print the report; write nothing",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument(
        "--sleep",
        type=float,
        default=SLEEP_S,
        help=f"seconds between page fetches (default: {SLEEP_S})",
    )
    return parser.parse_args(argv)


def resolve_db(args: argparse.Namespace) -> Path:
    if args.db:
        return Path(args.db).resolve()
    return Path(get_settings().db_path).resolve()


def ensure_head(db_path: Path) -> str:
    """``alembic upgrade head`` on ``db_path``; returns the resulting revision."""

    config = alembic_config(db_path)
    command.upgrade(config, "head")
    from alembic.script import ScriptDirectory

    return str(ScriptDirectory.from_config(config).get_current_head() or "")


def symbols_from_symbols(args: argparse.Namespace) -> list[str]:
    raw = [part.strip() for part in str(args.symbols or "").split(",") if part.strip()]
    seen: dict[str, None] = {}
    for symbol in raw:
        seen.setdefault(F.canonical_symbol(symbol), None)
    return list(seen)


async def symbols_from_screen(region: str, top: int) -> list[str]:
    """Candidates from the live screen, in the scan's own order.

    A screen row is not always a Finviz *page*: TradingView lists share-class
    aliases (``GOOGM``/``GOOGN``) that Finviz answers with a 404. The caller asks
    for ``top`` filled rows, so it walks a wider window of candidates and stops
    when it has that many — the failures stay in the report by name.
    """

    window = min(max(top * 3, top + 5), 100)
    page = await fetch_scan(region, limit=window, min_market_cap=None, subtype=None)
    universe = build_universe(
        page.rows,
        region=region,
        min_market_cap=0 if region != "us_etf" else None,
        allow_any_subtype=True,
    )
    out: dict[str, None] = {}
    for row in universe:
        symbol = row.get("symbol")
        if isinstance(symbol, str) and symbol.strip():
            out.setdefault(F.canonical_symbol(symbol), None)
    return list(out)[:window]


def cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


async def fill(
    store: F.SqliteFactsStore,
    symbols: Sequence[str],
    args: argparse.Namespace,
    *,
    target: int | None = None,
) -> list[dict[str, Any]]:
    """Scrape symbols and upsert one row each; stops after ``target`` settled rows.

    ``target`` is how ``--top`` keeps its promise: the screens list share-class
    aliases that Finviz does not serve, so the walk continues past a 404 until
    the store holds the requested number of fresh rows. Every failure stays in
    the result, by name.
    """

    adapter = FinvizAdapter()
    results: list[dict[str, Any]] = []
    settled = 0
    for index, symbol in enumerate(symbols):
        if target is not None and settled >= target:
            break
        if index and args.sleep > 0:
            await asyncio.sleep(args.sleep)
        instrument = InstrumentRef.from_symbol(symbol)
        started = F.utc_now()
        try:
            raw = await adapter.fetch(Capability.FUNDAMENTALS, instrument, set(WANTED))
        except Exception as exc:  # noqa: BLE001 - one symbol must not sink the run
            results.append(
                {"symbol": symbol, "status": "failed", "detail": f"{type(exc).__name__}: {exc}"}
            )
            continue
        verdict = adapter.verdict(raw, instrument)
        if not verdict.ok:
            results.append(
                {
                    "symbol": symbol,
                    "status": "failed",
                    "detail": f"{verdict.kind}: {verdict.detail}",
                }
            )
            continue
        body = raw["body"].decode("utf-8", errors="replace") if isinstance(raw["body"], bytes) else str(raw["body"])
        values = parse_facts(body)
        if not values:
            results.append(
                {
                    "symbol": symbol,
                    "status": "empty",
                    "detail": "the page carries none of the three facts labels",
                }
            )
            continue
        now = F.utc_now()
        row = F.facts_from_values(
            symbol, values, source=F.SOURCE, fetched_at=now, as_of=now, ttl_s=store.ttl_s
        )
        changed = await store.upsert(row)
        settled += 1
        results.append(
            {
                "symbol": symbol,
                "status": "written" if changed else "kept",
                "fields": row.cells,
                "field_count": row.field_count,
                "expires_at": F.iso(row.expiry),
                "elapsed_ms": round((F.utc_now() - started).total_seconds() * 1000),
            }
        )
    return results


async def report(store: F.SqliteFactsStore, symbols: Sequence[str], *, revision: str) -> dict[str, Any]:
    """The measured state of the store for ``symbols`` (fresh rows decide fill)."""

    now = F.utc_now()
    wanted = set(symbols)
    rows = [row for row in await store.rows() if not wanted or row.symbol in wanted]
    fresh = [row for row in rows if row.is_fresh(now)]
    per_field = {
        name: sum(1 for row in fresh if row.cells.get(name) is not None) for name in F.FACTS_FIELDS
    }
    return {
        "db": str(store.db_path),
        "revision": revision,
        "as_of": F.iso(now),
        "ttl_s": store.ttl_s,
        "symbols": [row.symbol for row in fresh],
        "rows": {row.symbol: row.cells for row in fresh},
        "coverage": {
            name: {
                "filled": count,
                "total": len(fresh),
                "pct": round(100.0 * count / len(fresh), 1) if fresh else 0.0,
            }
            for name, count in per_field.items()
        },
        "stored": len(rows),
        "fresh": len(fresh),
        "expired": len(rows) - len(fresh),
    }


def render_text(results: Sequence[dict[str, Any]], document: dict[str, Any], *, header: str) -> str:
    lines = [header, ""]
    if results:
        lines.append(f"{'symbol':<8} {'short_float':>12} {'insider_own':>12} {'inst_own':>10}  status")
        for entry in results:
            cells = entry.get("fields") or {}
            lines.append(
                f"{entry['symbol']:<8} {cell(cells.get('short_float')):>12} "
                f"{cell(cells.get('insider_own')):>12} {cell(cells.get('inst_own')):>10}  "
                f"{entry['status']}" + (f" ({entry['detail']})" if entry.get("detail") else "")
            )
        lines.append("")
    lines.append(
        f"db={document['db']}  revision={document['revision']}  "
        f"rows={document['stored']} fresh={document['fresh']} expired={document['expired']}"
    )
    for name, coverage in document["coverage"].items():
        lines.append(
            f"  {name:<12} {coverage['filled']:>2}/{coverage['total']:<2}  {coverage['pct']:>5.1f}%"
        )
    if document["rows"]:
        lines.append("")
        lines.append("stored fresh rows:")
        for symbol, cells in document["rows"].items():
            lines.append(
                f"  {symbol:<8} short_float={cell(cells.get('short_float'))}  "
                f"insider_own={cell(cells.get('insider_own'))}  "
                f"inst_own={cell(cells.get('inst_own'))}"
            )
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    db_path = resolve_db(args)
    revision = ensure_head(db_path)
    if args.symbols:
        symbols = symbols_from_symbols(args)
    else:
        symbols = await symbols_from_screen(args.region, max(args.top, 1))
    if not symbols:
        print("no symbols to scrape: the screen answered an empty universe", file=sys.stderr)
        return 2

    store = F.SqliteFactsStore(db_path)
    try:
        results: list[dict[str, Any]] = []
        if not args.report_only:
            target = None if args.symbols else max(args.top, 1)
            results = await fill(store, symbols, args, target=target)
        attempted = [entry["symbol"] for entry in results] or symbols
        document = await report(store, attempted, revision=revision)
    finally:
        await store.aclose()

    document["requested"] = len(results) if results else len(symbols)
    document["candidates"] = len(symbols)
    document["results"] = results
    failures = [entry for entry in results if entry["status"] in {"failed", "empty"}]
    filled = [entry for entry in results if entry["status"] in {"written", "kept"}]
    header = (
        f"screen_facts fill · {len(filled)} filled / {len(results)} attempted · "
        f"{len(failures)} failed"
        if not args.report_only
        else f"screen_facts report · {len(symbols)} symbol(s)"
    )
    if args.json:
        print(json.dumps({"header": header, **document}, indent=2, ensure_ascii=False, default=str))
    else:
        print(render_text(results, document, header=header))

    if args.report_only:
        return 0
    return 0 if not failures and filled else (1 if not filled else 0)


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
