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

Egress
------
The adapter walks ``direct → r.jina.ai (free) → free proxies → paid proxies``
(:class:`app.providers.adapters.finviz.FinvizAdapter`), because Cloudflare
refuses a datacenter exit while the same page answers from another wire.
``--proxy-pool`` supplies the ranked pool (a zcrapers ``pool_ranked.json``, a
one-proxy-per-line text file, or comma-separated URLs); without it the run is
direct + the reader only. The winning exit is printed per symbol so a blocked
run can be told from a written one.

Retrying what is missing
------------------------
``--retry-missing`` reads the facts store first and drops every symbol that
already has a **fresh** row, so a retry spends its requests only on the symbols
that never wrote one (blocked, empty, not served). A fresh row is evidence and
is not re-scraped; a blocked symbol still writes nothing — the command never
fills a missing value with a default.

Examples
--------
    .venv/bin/python scripts/screen-facts.py --top 10
    .venv/bin/python scripts/screen-facts.py --symbols AAPL,MSFT,NVDA
    .venv/bin/python scripts/screen-facts.py --top 10 --json
    .venv/bin/python scripts/screen-facts.py --report-only --top 10
    .venv/bin/python scripts/screen-facts.py --retry-missing --symbols AAPL,MSFT \\
        --proxy-pool ~/zcrapers/data/pool_ranked.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
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
from app.providers.proxy_pool import ProxyPool  # noqa: E402
from app.screen import facts as F  # noqa: E402
from app.screen.scanner import fetch_scan  # noqa: E402
from app.screen.universe import build_universe  # noqa: E402

#: The cells this command fills, as the adapter's parse vocabulary.
WANTED: frozenset[str] = frozenset(F.FACTS_FIELDS)

#: A scrape is a polite scrape: one page every ``SLEEP_S`` seconds.
SLEEP_S = 0.4

#: The proxy rungs a single symbol may spend, matching the adapter default.
PROXY_ATTEMPTS = 3

#: Verdict kinds that mean "the site refused the exit", not "the symbol has no
#: page": they are the blocked count in every report (Cloudflare 403 is an
#: ``authwall`` in the shared taxonomy, a 429 a ``throttled``).
REFUSAL_KINDS: frozenset[str] = frozenset({"blocked", "authwall", "throttled"})


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
    parser.add_argument(
        "--proxy-pool",
        default=None,
        help=(
            "zcrapers pool_ranked.json / text proxy list / comma-separated URLs; "
            "default: MEE_PROXY_POOL (empty = direct + reader only)"
        ),
    )
    parser.add_argument(
        "--proxy-attempts",
        type=int,
        default=None,
        help=f"free-proxy attempts per symbol (default: MEE_PROXY_ATTEMPTS or {PROXY_ATTEMPTS})",
    )
    parser.add_argument(
        "--direct-cooldown",
        type=float,
        default=None,
        help="seconds the direct rung stays cooling after a refusal (default: MEE_PROXY_DIRECT_COOLDOWN_S)",
    )
    parser.add_argument(
        "--retry-missing",
        action="store_true",
        help=(
            "read the facts store first and scrape only the symbols without a "
            "fresh row (blocked/empty/unserved); fresh rows are never re-fetched"
        ),
    )
    return parser.parse_args(argv)


def load_pool(
    raw: Any, *, attempts: int, cooldown_s: float
) -> ProxyPool | None:
    """The egress pool from a ranked file, a text list or inline URLs.

    ``None`` means "no proxy rung": the run is direct + ``r.jina.ai``, which is
    still a real ladder. A path that does not exist is a caller error — silently
    running proxy-less would hide a typo in a 3,000-symbol fill.
    """

    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    candidate = Path(text).expanduser()
    if candidate.exists():
        return ProxyPool.from_file(
            candidate, cooldown_s=cooldown_s, free_attempts=attempts
        )
    urls = [part for part in re.split(r"[,\s]+", text) if part]
    if not urls:
        return None
    if not any("." in part or ":" in part for part in urls):
        raise SystemExit(f"--proxy-pool is neither a readable file nor proxy URLs: {text}")
    return ProxyPool(free_urls=urls, cooldown_s=cooldown_s, free_attempts=attempts)


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
    adapter: FinvizAdapter | None = None,
    target: int | None = None,
) -> list[dict[str, Any]]:
    """Scrape symbols and upsert one row each; stops after ``target`` settled rows.

    ``target`` is how ``--top`` keeps its promise: the screens list share-class
    aliases that Finviz does not serve, so the walk continues past a 404 until
    the store holds the requested number of fresh rows. Every failure stays in
    the result, by name, with the exit that answered (``egress``) so a blocked
    run is distinguishable from a written one.

    A non-``ok`` verdict is never parsed: a challenge page and a markdown reader
    body write nothing, and the row (if any) keeps its previous value.
    """

    scraper = adapter or FinvizAdapter()
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
            raw = await scraper.fetch(Capability.FUNDAMENTALS, instrument, set(WANTED))
        except Exception as exc:  # noqa: BLE001 - one symbol must not sink the run
            results.append(
                {
                    "symbol": symbol,
                    "status": "failed",
                    "kind": "error",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        egress = raw.get("egress") if isinstance(raw, dict) else None
        verdict = scraper.verdict(raw, instrument)
        if not verdict.ok:
            results.append(
                {
                    "symbol": symbol,
                    "status": "failed",
                    "kind": verdict.kind,
                    "detail": f"{verdict.kind}: {verdict.detail}",
                    "egress": egress,
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
                    "kind": "empty",
                    "detail": "the page carries none of the three facts labels",
                    "egress": egress,
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
                "egress": egress,
                "fields": row.cells,
                "field_count": row.field_count,
                "expires_at": F.iso(row.expiry),
                "elapsed_ms": round((F.utc_now() - started).total_seconds() * 1000),
            }
        )
    return results


async def missing_symbols(
    store: F.SqliteFactsStore, symbols: Sequence[str]
) -> list[str]:
    """``symbols`` without a fresh row — the ones a retry may spend a request on.

    A blocked or empty scrape wrote no row, so it is "missing" here by
    construction; a fresh row is evidence and is left alone. One read of the
    table, then a set difference in memory.
    """

    now = F.utc_now()
    fresh = {row.symbol for row in await store.rows() if row.is_fresh(now)}
    out: list[str] = []
    for symbol in symbols:
        key = F.canonical_symbol(symbol)
        if key not in fresh and key not in out:
            out.append(key)
    return out


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
            exit_note = (
                f" via {entry['egress']}"
                if entry.get("egress") not in (None, "", "direct")
                else ""
            )
            lines.append(
                f"{entry['symbol']:<8} {cell(cells.get('short_float')):>12} "
                f"{cell(cells.get('insider_own')):>12} {cell(cells.get('inst_own')):>10}  "
                f"{entry['status']}" + (f" ({entry['detail']})" if entry.get("detail") else "")
                + exit_note
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
    requested_symbols = list(symbols)

    settings = get_settings()
    attempts = (
        args.proxy_attempts
        if getattr(args, "proxy_attempts", None) is not None
        else int(getattr(settings, "proxy_attempts", PROXY_ATTEMPTS))
    )
    cooldown_s = (
        args.direct_cooldown
        if getattr(args, "direct_cooldown", None) is not None
        else float(getattr(settings, "proxy_direct_cooldown_s", 600.0))
    )
    #: ``--proxy-pool`` wins; otherwise the service setting is the operator's
    #: configured pool, exactly like ``/providers`` builds its adapters.
    pool_source = (
        args.proxy_pool
        if getattr(args, "proxy_pool", None) is not None
        else getattr(settings, "proxy_pool", None)
    )
    pool = load_pool(pool_source, attempts=max(1, attempts), cooldown_s=cooldown_s)
    adapter = FinvizAdapter(
        proxy_pool=pool, proxy_attempts=max(1, attempts), direct_cooldown_s=cooldown_s
    )

    store = F.SqliteFactsStore(db_path)
    retry: dict[str, Any] | None = None
    try:
        if args.retry_missing:
            symbols = await missing_symbols(store, symbols)
            retry = {
                "requested": len(requested_symbols),
                "missing": len(symbols),
                "fresh": len(requested_symbols) - len(symbols),
            }
            if not symbols:
                print(
                    f"nothing to retry: all {len(requested_symbols)} requested "
                    "symbol(s) already have a fresh row",
                    file=sys.stderr,
                )
        results: list[dict[str, Any]] = []
        if not args.report_only and symbols:
            # A retry spends one request per *missing* symbol, never a ``--top``
            # budget: the caller already chose the set.
            target = None if (args.symbols or args.retry_missing) else max(args.top, 1)
            results = await fill(store, symbols, args, adapter=adapter, target=target)
        attempted = [entry["symbol"] for entry in results] or symbols or requested_symbols
        document = await report(store, attempted, revision=revision)
    finally:
        await store.aclose()

    document["requested"] = (
        len(results) if results else (retry["missing"] if retry else len(symbols))
    )
    document["candidates"] = len(requested_symbols)
    document["results"] = results
    if retry is not None:
        document["retry"] = retry
    outcomes = {
        name: sum(1 for entry in results if entry["status"] == name)
        for name in ("written", "kept", "empty", "failed")
    }
    blocked = sum(1 for entry in results if entry.get("kind") in REFUSAL_KINDS)
    document["outcomes"] = outcomes
    document["blocked"] = blocked
    document["egress"] = {
        "pool": None if pool is None else pool.snapshot(),
        "rungs": ["direct", "jina", "free", "paid"],
    }
    failures = [entry for entry in results if entry["status"] in {"failed", "empty"}]
    filled = [entry for entry in results if entry["status"] in {"written", "kept"}]
    if args.report_only:
        header = f"screen_facts report · {len(requested_symbols)} symbol(s)"
    elif retry is not None:
        header = (
            f"screen_facts retry · {len(filled)} written / {len(results)} attempted · "
            f"{blocked} blocked · {retry['missing']} missing of {retry['requested']}"
        )
    else:
        header = (
            f"screen_facts fill · {len(filled)} filled / {len(results)} attempted · "
            f"{len(failures)} failed"
        )
    if args.json:
        print(json.dumps({"header": header, **document}, indent=2, ensure_ascii=False, default=str))
    else:
        print(render_text(results, document, header=header))

    if args.report_only:
        return 0
    if not results:
        # ``--retry-missing`` with every requested row already fresh: nothing to
        # do is a success, not a failed fill.
        return 0
    return 0 if not failures and filled else (1 if not filled else 0)


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
