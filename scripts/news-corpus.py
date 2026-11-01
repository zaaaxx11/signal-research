#!/usr/bin/env python3
"""Build the news corpus the ``news_keywords`` filter searches.

The filter is a **search**, not a feed read: it has to answer over the whole
universe before a query pages, so the headlines live in one stamped file
(``app/screen/data/news_corpus.json``) instead of being fetched per request.

The corpus is the product's own merged one — :func:`app.news.service.build_news_service`
(Google News + GDELT, deduped by title and URL, the same service
``GET /news/{symbol}`` and ``GET /market/news`` read) — asked once per symbol:

    python scripts/news-corpus.py                       # the S&P 500 names
    python scripts/news-corpus.py --from-holdings SPY --limit 200
    python scripts/news-corpus.py --symbols AAPL,MSFT --limit-per-symbol 10
    python scripts/news-corpus.py --check               # validate the file

A symbol whose feeds all failed is not a row and not a failure of the run: the
titles that did arrive are the corpus, and the count is published so a reader can
see how much of the market was covered. Nothing here invents a headline.
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

from app.news.service import (  # noqa: E402  (after the path fix)
    AllNewsProvidersFailed,
    InvalidNewsSymbol,
    build_news_service,
)
from app.screen import etf_holdings, news_index  # noqa: E402

DEFAULT_DB = ROOT / "ztockz.db"

#: How many headlines one symbol contributes. Enough for a keyword to have
#: recall, small enough that 500 symbols stay a one-file snapshot.
DEFAULT_PER_SYMBOL = 8

#: Concurrent service reads. Each read is cached and paced by the service.
DEFAULT_CONCURRENCY = 8


def symbols_from_db(path: Path) -> list[str]:
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
    return [str(row[0]).strip().upper() for row in rows if str(row[0]).strip()]


async def collect(
    symbols: Iterable[str],
    *,
    per_symbol: int,
    concurrency: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Headlines for every symbol, and the provenance of the pass."""

    service = build_news_service()
    wanted = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
    gate = asyncio.Semaphore(max(1, concurrency))
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    failures: dict[str, str] = {}
    covered: set[str] = set()

    async def one(symbol: str) -> None:
        async with gate:
            try:
                document = await service.news(symbol, limit=per_symbol)
            except InvalidNewsSymbol as exc:
                failures[symbol] = f"invalid symbol: {exc}"
                return
            except AllNewsProvidersFailed as exc:
                failures[symbol] = f"every provider failed: {exc}"
                return
            except Exception as exc:  # noqa: BLE001 - a wire failure is data, not a crash
                failures[symbol] = f"{type(exc).__name__}: {exc}"
                return
        count = 0
        for item in document.get("items", ()):
            headline = str(item.get("headline") or "").strip()
            if not headline:
                continue
            key = (symbol, " ".join(headline.casefold().split()))
            if key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "symbol": symbol,
                    "title": headline,
                    "published": item.get("published_at"),
                    "source": item.get("source") or None,
                }
            )
            count += 1
        if count:
            covered.add(symbol)

    await asyncio.gather(*(one(symbol) for symbol in wanted))
    return items, {
        "providers": list(service.provider_names),
        "symbols": len(wanted),
        "symbols_with_news": len(covered),
        "items": len(items),
        "limit_per_symbol": per_symbol,
        "failures": len(failures),
        "failure_examples": dict(list(sorted(failures.items()))[:12]),
    }


def check(target: Path) -> int:
    corpus = news_index.load(target)
    built = news_index.index(target)
    print(
        json.dumps(
            {
                "as_of": corpus.as_of,
                "available": corpus.available,
                "headlines": built.headlines,
                "words": len(built.words),
                "symbols": len(built.symbols),
                "reason": news_index.reason(),
            },
            sort_keys=True,
        )
    )
    return 0 if corpus.available else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", help="comma-separated tickers")
    parser.add_argument("--symbols-file", help="one ticker per line")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="read symbols from this screen_facts table")
    parser.add_argument(
        "--from-holdings",
        default="SPY",
        choices=list(etf_holdings.ETFS),
        help="read symbols from this fund's holdings in the ETF snapshot (default SPY)",
    )
    parser.add_argument("--limit", type=int, default=None, help="cap the symbol count")
    parser.add_argument("--limit-per-symbol", type=int, default=DEFAULT_PER_SYMBOL)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--check", action="store_true", help="validate the file, no fetch")
    parser.add_argument(
        "--out",
        default=str(news_index.DATA_PATH),
        help="where to write the corpus (default: the shipped data file)",
    )
    arguments = parser.parse_args(argv)
    target = Path(arguments.out)
    if arguments.check:
        return check(target)

    if arguments.symbols:
        symbols = [token.strip() for token in arguments.symbols.split(",") if token.strip()]
    elif arguments.symbols_file:
        symbols = Path(arguments.symbols_file).read_text(encoding="utf-8").split()
    elif arguments.from_holdings:
        try:
            symbols = sorted(etf_holdings.members(arguments.from_holdings))
        except (etf_holdings.UnknownEtf, etf_holdings.UnavailableEtf) as exc:
            print(
                f"FAIL {arguments.from_holdings} holdings are not in the snapshot ({exc}); "
                "pass --symbols or --db",
                file=sys.stderr,
            )
            return 1
    else:
        symbols = symbols_from_db(Path(arguments.db))
    if arguments.limit:
        symbols = symbols[: arguments.limit]
    if not symbols:
        print("FAIL no symbols to ask", file=sys.stderr)
        return 1

    print(f"asking {len(symbols)} symbol(s), limit {arguments.limit_per_symbol}")
    items, source = asyncio.run(
        collect(
            symbols,
            per_symbol=arguments.limit_per_symbol,
            concurrency=arguments.concurrency,
        )
    )
    moment = datetime.now(UTC)
    document = {
        "as_of": moment.date().isoformat(),
        "captured_at": moment.isoformat(timespec="seconds"),
        "source": {"service": "app.news.service", **source},
        "items": items,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"wrote {target} as_of={document['as_of']} items={source['items']} "
        f"symbols_with_news={source['symbols_with_news']}/{source['symbols']} "
        f"failures={source['failures']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
