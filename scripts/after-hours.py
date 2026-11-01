#!/usr/bin/env python3
"""Capture one after-hours snapshot from Yahoo's bulk quote path.

``ah_close`` and ``ah_change`` read two cells per row
(``post_market_close`` / ``post_market_change``) that
:mod:`app.screen.after_hours` attaches from a stamped file. This script builds
that file with the **same** bulk quote source the scanner uses
(:class:`app.market.yahoo_batch.YahooBatchQuoteSource`, 400 symbols per request,
keyless), so the numbers are the provider's own ``postMarketPrice`` /
``postMarketChangePercent`` — never a change computed from two of our rows.

    python scripts/after-hours.py                  # the local screen_facts symbols
    python scripts/after-hours.py --symbols AAPL,MSFT,NVDA
    python scripts/after-hours.py --symbols-file tickers.txt
    python scripts/after-hours.py --check          # validate the file, no fetch

The session is recorded from the clock at capture time. Run it after 16:00 New
York: a regular-session capture publishes no post-market price, and the filter
then matches nothing (the document says so) instead of showing yesterday's
numbers as today's.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.market.yahoo_batch import MAX_BATCH, YahooBatchQuoteSource  # noqa: E402
from app.screen import after_hours  # noqa: E402

#: Where the product's symbol list comes from when the caller names none: the
#: depth store's own universe (one row per collected symbol).
DEFAULT_DB = ROOT / "ztockz.db"


def symbols_from_db(path: Path, *, limit: int | None = None) -> list[str]:
    """The distinct tickers the screen facts table holds, alphabetical."""

    if not path.exists():
        return []
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "select distinct symbol from screen_facts where symbol is not null order by symbol"
        ).fetchall()
    except sqlite3.DatabaseError:
        return []
    finally:
        connection.close()
    symbols = [str(row[0]).strip().upper() for row in rows if str(row[0]).strip()]
    return symbols[:limit] if limit else symbols


def _rows(answers: dict[str, dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Yahoo catalog rows → the two cells, only where the provider printed both."""

    rows: dict[str, dict[str, float]] = {}
    for symbol, row in answers.items():
        price = row.get("post_market_price")
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        entry: dict[str, float] = {"close": float(price)}
        change = row.get("post_market_change_pct")
        if isinstance(change, (int, float)):
            entry["change"] = float(change)
        else:
            absolute = row.get("post_market_change")
            close = row.get("price")
            if isinstance(absolute, (int, float)) and isinstance(close, (int, float)) and close:
                entry["change"] = float(absolute) / float(close) * 100.0
        rows[str(symbol).strip().upper()] = entry
    return rows


async def capture(symbols: Iterable[str], *, timeout_s: float) -> dict[str, Any]:
    source = YahooBatchQuoteSource(timeout_s=timeout_s)
    wanted = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
    answers = await source.fetch_many(wanted) if wanted else {}
    rows = _rows(answers)
    moment = datetime.now(UTC)
    return {
        "as_of": moment.astimezone(after_hours.NEW_YORK).date().isoformat(),
        "captured_at": moment.isoformat(timespec="seconds"),
        "session": after_hours.session_at(moment),
        "source": {
            "name": "yahoo",
            "endpoint": "/v7/finance/quote",
            "fields": ["postMarketPrice", "postMarketChangePercent"],
            "symbols": len(wanted),
            "requests": (len(wanted) + MAX_BATCH - 1) // MAX_BATCH if wanted else 0,
            "answered": len(answers),
            "priced": len(rows),
            "errors": list(getattr(source, "last_errors", ()) or ()),
        },
        "rows": rows,
    }


def check(target: Path) -> int:
    snapshot = after_hours.load(target)
    coverage = {
        "as_of": snapshot.as_of,
        "session": snapshot.session,
        "priced": snapshot.priced,
        "available": snapshot.available,
        "reason": snapshot.reason(),
    }
    print(json.dumps(coverage, sort_keys=True))
    return 0 if snapshot.available else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", help="comma-separated tickers")
    parser.add_argument("--symbols-file", help="one ticker per line")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="read symbols from this screen_facts table")
    parser.add_argument("--limit", type=int, default=None, help="cap the symbol count")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--check", action="store_true", help="validate the file, no fetch")
    parser.add_argument(
        "--out",
        default=str(after_hours.DATA_PATH),
        help="where to write the snapshot (default: the shipped data file)",
    )
    arguments = parser.parse_args(argv)
    target = Path(arguments.out)
    if arguments.check:
        return check(target)

    if arguments.symbols:
        symbols = [token.strip() for token in arguments.symbols.split(",") if token.strip()]
    elif arguments.symbols_file:
        symbols = Path(arguments.symbols_file).read_text(encoding="utf-8").split()
    else:
        symbols = symbols_from_db(Path(arguments.db), limit=arguments.limit)
    if not symbols:
        print("FAIL no symbols: pass --symbols, --symbols-file or a --db that holds rows", file=sys.stderr)
        return 1

    document = asyncio.run(capture(symbols, timeout_s=arguments.timeout))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"wrote {target} as_of={document['as_of']} session={document['session']} "
        f"symbols={document['source']['symbols']} priced={document['source']['priced']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
