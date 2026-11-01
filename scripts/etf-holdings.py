#!/usr/bin/env python3
"""Vendor the ETF held-by lists into one stamped snapshot.

``etf_heldby`` needs a fund→constituent table, and the funds publish one daily.
This script fetches each fund's own file through the parsers
:mod:`app.market.official` already uses for index membership, checks the count
against :data:`app.screen.etf_holdings.PLAUSIBLE`, and writes
``app/screen/data/etf_holdings.json``:

    python scripts/etf-holdings.py            # fetch, validate, write
    python scripts/etf-holdings.py --check    # validate the file, no fetch
    python scripts/etf-holdings.py --from SPY=spy.xlsx --from DIA=dia.xlsx

A fund that does not answer keeps its entry with the measured HTTP status and
**no members**: ``heldby:<fund>`` is then a 422 that repeats the reason. What
this script must never do is fill a fund from a proxy (the Nasdaq-100 list for
QQQ, say): that would be an index dressed as a holding.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.market import official  # noqa: E402  (after the path fix)
from app.screen import etf_holdings  # noqa: E402

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

SSGA = "https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{fund}.xlsx"

#: fund → where its holdings come from, and how they are parsed. ``ssga`` and
#: ``ishares`` are the two readers in :mod:`app.market.official`; ``csv`` is the
#: generic "a header row names a Ticker column" reader below.
SOURCES: Mapping[str, dict[str, str]] = {
    "SPY": {
        "label": "SPDR S&P 500 ETF Trust",
        "url": SSGA.format(fund="spy"),
        "format": "xlsx",
        "reader": "ssga",
    },
    "QQQ": {
        "label": "Invesco QQQ Trust",
        "url": (
            "https://www.invesco.com/us/financial-products/etfs/holdings/main/"
            "holdings/0?audienceType=Investor&action=download&ticker=QQQ"
        ),
        "format": "csv",
        "reader": "csv",
    },
    "DIA": {
        "label": "SPDR Dow Jones Industrial Average ETF Trust",
        "url": SSGA.format(fund="dia"),
        "format": "xlsx",
        "reader": "ssga",
    },
    "IWM": {
        "label": "iShares Russell 2000 ETF",
        "url": official.IWM_HOLDINGS_URL,
        "format": "csv",
        "reader": "ishares",
    },
}


def fetch(url: str, *, timeout_s: float = 45.0) -> tuple[int, str, bytes]:
    """``(status, content_type, body)`` — an HTTP error is a status, not a throw."""

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return (
                int(response.status),
                str(response.headers.get("Content-Type", "")),
                response.read(),
            )
    except urllib.error.HTTPError as exc:
        return int(exc.code), str(exc.headers.get("Content-Type", "") if exc.headers else ""), b""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, "", str(exc).encode("utf-8")


def csv_tickers(text: str) -> set[str]:
    """Tickers from a CSV whose header names a ticker column (any fund's shape).

    The header is located by name rather than by position: a fund's download may
    carry a title block, a "as of" line and a blank row before the table (the
    iShares file does), and a fixed row index would read the wrong table.
    """

    rows = list(csv.reader(io.StringIO(text)))
    for index, row in enumerate(rows):
        cells = [str(cell).strip().casefold() for cell in row]
        for column, name in enumerate(cells):
            if name in {"ticker", "symbol", "holding ticker", "holding symbol"}:
                found: set[str] = set()
                for data in rows[index + 1 :]:
                    if column >= len(data):
                        continue
                    token = str(data[column]).strip().upper()
                    if token and token not in {"-", "N/A", "CASH"}:
                        found.add(token)
                return found
    return set()


def parse_body(reader: str, body: bytes, fund: str) -> set[str]:
    if reader == "ssga":
        return official.parse_ssga_holdings(body, fund)
    if reader == "ishares":
        return official.parse_ishares_holdings(body, fund)
    return csv_tickers(body.decode("utf-8", "replace"))


def collect(overrides: Mapping[str, Path]) -> dict[str, Any]:
    """One entry per fund: provenance, members, and what went wrong."""

    now = datetime.now(UTC).isoformat(timespec="seconds")
    funds: dict[str, Any] = {}
    for fund in etf_holdings.ETFS:
        spec = SOURCES[fund]
        entry: dict[str, Any] = {
            "label": spec["label"],
            "url": spec["url"],
            "format": spec["format"],
            "retrieved_at": now,
            "members": [],
        }
        if fund in overrides:
            body = overrides[fund].read_bytes()
            status, content_type = 200, "text/csv"
            entry["url"] = str(overrides[fund])
        else:
            status, content_type, body = fetch(spec["url"])
        entry["status"] = status
        if status != 200:
            entry["error"] = (
                f"HTTP {status} ({content_type or 'no content type'})"
                if status
                else body.decode("utf-8", "replace")[:160]
            )
            print(f"FAIL {fund}: {entry['error']} ({spec['url']})", file=sys.stderr)
            funds[fund] = entry
            continue
        try:
            members = parse_body(spec["reader"], body, fund)
        except Exception as exc:  # noqa: BLE001 - a parser failure is a data failure
            entry["error"] = f"{type(exc).__name__}: {exc}"
            print(f"FAIL {fund}: {entry['error']}", file=sys.stderr)
            funds[fund] = entry
            continue
        low, high = etf_holdings.PLAUSIBLE[fund]
        if not low <= len(members) <= high:
            served = (
                "an HTML page, not the CSV the download promises (a bot wall)"
                if "html" in content_type.casefold()
                else f"{content_type or 'no content type'}"
            )
            entry["error"] = (
                f"HTTP {status} answered {served}, {len(body)} bytes, and no "
                f"Ticker table could be read from it ({len(members)} ticker(s), "
                f"plausible range {low}..{high}): a truncated table is not a "
                "holdings list"
            )
            print(f"FAIL {fund}: {entry['error']}", file=sys.stderr)
            entry["members"] = []
            funds[fund] = entry
            continue
        entry["members"] = sorted(members)
        entry["count"] = len(members)
        print(f"OK {fund}: {len(members)} tickers from {spec['url']}")
        funds[fund] = entry
    return {
        "as_of": datetime.now(UTC).date().isoformat(),
        "captured_at": now,
        "source": {
            "note": (
                "each fund's own daily holdings file, parsed by "
                "app.market.official; a fund that did not answer carries its HTTP "
                "status and no members"
            )
        },
        "funds": funds,
    }


def check(target: Path) -> int:
    snapshot = etf_holdings.load(target)
    if snapshot.as_of is None:
        print("FAIL no fund in the snapshot", file=sys.stderr)
        return 1
    for fund, count in snapshot.counts().items():
        state = "OK" if count else "UNAVAILABLE"
        print(f"{state} {fund}: {count}")
    return 0 if snapshot.available else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate the file, no fetch")
    parser.add_argument(
        "--from",
        dest="from_",
        action="append",
        default=[],
        metavar="FUND=PATH",
        help="read one fund from a local file instead of fetching it",
    )
    parser.add_argument(
        "--out",
        default=str(etf_holdings.DATA_PATH),
        help="where to write the snapshot (default: the shipped data file)",
    )
    arguments = parser.parse_args(argv)
    target = Path(arguments.out)
    if arguments.check:
        return check(target)

    overrides: dict[str, Path] = {}
    for item in arguments.from_:
        fund, _, raw = item.partition("=")
        if fund not in etf_holdings.ETFS or not raw:
            parser.error(f"--from expects FUND=PATH for one of {', '.join(etf_holdings.ETFS)}")
        overrides[fund] = Path(raw)

    document = collect(overrides)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    counts = {fund: len(entry["members"]) for fund, entry in document["funds"].items()}
    print(f"wrote {target} as_of={document['as_of']} {json.dumps(counts, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
