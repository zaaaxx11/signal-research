#!/usr/bin/env python3
"""Vendor the four index-membership lists into one stamped snapshot.

``idx`` needs a membership list no provider column carries; the product reads one
vendored file (``app/screen/data/index_membership.json``) instead of querying a
vendor at request time. This script writes that file:

    python scripts/index-membership.py            # fetch, validate, write
    python scripts/index-membership.py --check    # validate the file, no fetch
    python scripts/index-membership.py --from sp500=sp500.csv --from ndx=ndx.csv

Where each list comes from
--------------------------
* ``sp500`` — Wikipedia's ``List of S&P 500 companies`` (the table whose header
  carries ``Symbol``);
* ``ndx`` — Wikipedia's ``List of NASDAQ-100 companies`` (header ``Ticker``);
* ``dji`` — Wikipedia's ``List of Dow Jones Industrial Average companies``;
* ``rut`` — the depth store's own ``index_membership`` labels, which
  ``app.market.official`` derives from the iShares IWM holdings file. No public
  HTML table of ~1,950 Russell 2000 names exists, so the collector's own output
  is the readable source; the snapshot records the path and the labels it used,
  and the file says how many names it kept.

Every list is checked against ``app.screen.index_membership.PLAUSIBLE`` before a
byte is written: a parse that lost the table must fail the build, not ship a
12-name S&P 500 that silently answers "not a member" for 491 companies.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.request
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.screen import index_membership  # noqa: E402  (after the path fix)

USER_AGENT = "ztockz-index-membership/1.0 (+https://ztockz.local; contact=ops@ztockz.local)"

#: index → (url, header cells that identify its constituents table)
SOURCES: Mapping[str, tuple[str, tuple[str, ...]]] = {
    "sp500": (
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        ("Symbol",),
    ),
    "ndx": (
        "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies",
        ("Ticker", "Symbol"),
    ),
    "dji": (
        "https://en.wikipedia.org/wiki/List_of_Dow_Jones_Industrial_Average_companies",
        ("Symbol", "Ticker"),
    ),
}

#: The depth store that carries the Russell 2000 labels (``app.market.official``).
RUT_DEPTH = os.environ.get("ZTOCKZ_DEPTH", "/home/ubuntu/ztockz-data/depth.json")

#: The labels the depth store writes for the Russell 2000. ``official.py`` uses
#: the iShares fund's own name; two spellings exist in older stores.
RUT_LABELS: frozenset[str] = frozenset({"russell 2000", "rut"})

#: A US listing ticker: 1–6 letters, optionally a class suffix (``BRK.B``).
SYMBOL = re.compile(r"^[A-Z]{1,6}(?:[.\-][A-Z]{1,2})?$")

#: Wikipedia prints footnotes inside cells (``BRK.B[5]``).
FOOTNOTE = re.compile(r"\[[^\]]*\]")


# ---------------------------------------------------------------------------
# Reading an HTML table
# ---------------------------------------------------------------------------


class _Tables(HTMLParser):
    """Every ``<table>`` on a page as a list of rows of trimmed cell text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


def _fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - https, fixed hosts
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, "replace")


def _clean_symbol(value: object) -> str | None:
    """A cell → the ticker it names, or ``None`` when it names none."""

    if not isinstance(value, str):
        return None
    text = FOOTNOTE.sub("", value).strip().upper().replace(" ", "")
    text = text.rstrip(".")
    if not text or not SYMBOL.fullmatch(text):
        return None
    return text


def _constituents(html: str, headers: Sequence[str]) -> list[str]:
    """The first table whose header carries ``headers``, as tickers."""

    reader = _Tables()
    reader.feed(html)
    wanted = {header.casefold() for header in headers}
    for table in reader.tables:
        if len(table) < 5:
            continue
        header = [cell.casefold() for cell in table[0]]
        column = next((index for index, cell in enumerate(header) if cell in wanted), None)
        if column is None:
            continue
        tickers: list[str] = []
        seen: set[str] = set()
        for row in table[1:]:
            if column >= len(row):
                continue
            symbol = _clean_symbol(row[column])
            if symbol is None or symbol in seen:
                continue
            seen.add(symbol)
            tickers.append(symbol)
        if tickers:
            return tickers
    return []


# ---------------------------------------------------------------------------
# Reading a CSV fallback / the depth store
# ---------------------------------------------------------------------------


def _from_csv(payload: str) -> list[str]:
    """A CSV with a ``symbol``/``ticker`` column (or one column) → tickers."""

    rows = list(csv.reader(io.StringIO(payload)))
    if not rows:
        return []
    header = [cell.strip().casefold() for cell in rows[0]]
    column = next(
        (index for index, cell in enumerate(header) if cell in ("symbol", "ticker", "tickers")),
        0,
    )
    start = 1 if any(cell in ("symbol", "ticker", "tickers") for cell in header) else 0
    tickers: list[str] = []
    seen: set[str] = set()
    for row in rows[start:]:
        if column >= len(row):
            continue
        symbol = _clean_symbol(row[column])
        if symbol is None or symbol in seen:
            continue
        seen.add(symbol)
        tickers.append(symbol)
    return tickers


def _from_depth(path: Path) -> list[str]:
    """The Russell 2000 labels the depth store carries, as tickers."""

    document = json.loads(path.read_text(encoding="utf-8"))
    symbols = document.get("symbols") if isinstance(document, Mapping) else None
    if not isinstance(symbols, Mapping):
        return []
    tickers: list[str] = []
    seen: set[str] = set()
    for symbol, row in symbols.items():
        if not isinstance(row, Mapping):
            continue
        labels = row.get("index_membership")
        if not isinstance(labels, str):
            continue
        named = {label.strip().casefold() for label in labels.split(",")}
        if not named & RUT_LABELS:
            continue
        cleaned = _clean_symbol(symbol)
        if cleaned is None or cleaned in seen:
            continue
        seen.add(cleaned)
        tickers.append(cleaned)
    return tickers


# ---------------------------------------------------------------------------
# Building and validating the document
# ---------------------------------------------------------------------------


def _validate_counts(indices: Mapping[str, Sequence[str]], *, force: bool) -> list[str]:
    problems: list[str] = []
    for name, (low, high) in index_membership.PLAUSIBLE.items():
        count = len(indices.get(name, ()))
        if not low <= count <= high:
            problems.append(f"{name}: {count} names, expected {low}..{high}")
    if problems and not force:
        return problems
    return []


def build(
    *, overrides: Mapping[str, Path], rut_depth: Path, as_of: str | None
) -> dict[str, Any]:
    indices: dict[str, list[str]] = {}
    source: dict[str, dict[str, Any]] = {}
    for name in ("sp500", "ndx", "dji"):
        if name in overrides:
            path = overrides[name]
            tickers = _from_csv(path.read_text(encoding="utf-8"))
            source[name] = {"path": str(path), "label": index_membership.LABELS[name]}
        else:
            url, headers = SOURCES[name]
            tickers = _constituents(_fetch(url), headers)
            source[name] = {"url": url, "label": index_membership.LABELS[name]}
        indices[name] = tickers
    rut = _from_depth(rut_depth) if rut_depth.exists() else []
    indices["rut"] = rut
    source["rut"] = {
        "path": str(rut_depth),
        "label": index_membership.LABELS["rut"],
        "derived_from": (
            "index_membership labels written by app.market.official "
            "(iShares IWM holdings)"
        ),
    }
    document = {
        "as_of": as_of or date.today().isoformat(),
        "source": source,
        "indices": indices,
    }
    return document, []


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Vendor the four index-membership lists into one stamped snapshot."
    )
    parser.add_argument(
        "--from",
        dest="from_",
        action="append",
        default=[],
        metavar="INDEX=PATH",
        help="read one index from a CSV instead of fetching it (repeatable)",
    )
    parser.add_argument("--rut-depth", default=RUT_DEPTH, help="depth store for the Russell 2000")
    parser.add_argument("--out", default=str(index_membership.DATA_PATH), help="snapshot to write")
    parser.add_argument("--as-of", default=None, help="stamp (default: today, UTC)")
    parser.add_argument("--force", action="store_true", help="write even if a count is implausible")
    parser.add_argument("--check", action="store_true", help="validate the written file, no fetch")
    arguments = parser.parse_args(list(argv) if argv is not None else None)

    target = Path(arguments.out)
    if arguments.check:
        snapshot = index_membership.load(target)
        problems = _validate_counts(snapshot.indices, force=False)
        if not snapshot.available or problems:
            for problem in problems or ["snapshot unavailable"]:
                print(f"FAIL {problem}", file=sys.stderr)
            return 1
        print(f"OK {snapshot.as_of} " + json.dumps(snapshot.counts, sort_keys=True))
        return 0

    overrides: dict[str, Path] = {}
    for item in arguments.from_:
        name, _, raw = item.partition("=")
        if not raw or name not in SOURCES:
            parser.error(f"--from expects INDEX=PATH for one of {', '.join(SOURCES)}")
        overrides[name] = Path(raw)

    document = build(
        overrides=overrides,
        rut_depth=Path(arguments.rut_depth),
        as_of=arguments.as_of or datetime.now(UTC).date().isoformat(),
    )
    indices = document["indices"]
    problems = _validate_counts(indices, force=arguments.force)
    if problems:
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        print("refusing to write an implausible snapshot; use --force to override", file=sys.stderr)
        return 1
    for name, entry in document["source"].items():
        entry["count"] = len(indices[name])
        entry["retrieved_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    counts = json.dumps({name: len(values) for name, values in indices.items()}, sort_keys=True)
    print(f"wrote {target} as_of={document['as_of']} {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
