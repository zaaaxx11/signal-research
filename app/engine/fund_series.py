"""Ticker → SEC fund series mapping.

A fund trust can file one N-PORT-P per series; the trust's CIK alone cannot say
which series a filing belongs to. SEC publishes ``company_tickers_mf.json``
(``cik``, ``seriesId``, ``classId``, ``symbol``) — the official bridge from an
ETF ticker to the series id that appears inside the N-PORT XML.

The file is a column document (``fields`` names the columns, ``data`` holds the
rows), so every column is read **by name**: a positional read would silently
swap ``seriesId`` and ``classId`` the day SEC reorders the file, and a wrong
series id is a wrong portfolio. The symbol is upper cased because EDGAR's
spelling is the ticker, and the last row for a symbol wins so a duplicate is
resolved deterministically by file order.

A missing map is a normal state: callers fall back to the single-series path
rather than inventing a series. That is why ``load_fund_series`` answers ``{}``
for an absent, truncated or unreadable file — the map is an optimisation of
correctness, never a reason to crash a quote.

Contract: ``briefs/market-engine/R5E-CONTRACT.md``.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

from app.engine.ingest import DEFAULT_USER_AGENT
from app.engine.sources.sec import normalize_cik
from app.providers import http as provider_http

FUND_SERIES_FILENAME = "company_tickers_mf.json"
FUND_SERIES_URL = "https://www.sec.gov/files/company_tickers_mf.json"

#: The four published columns this slice reads. Everything else in the SEC
#: document is ignored, so a new column cannot break the parse.
_CIK_COLUMN = "cik"
_SERIES_COLUMN = "seriesId"
_CLASS_COLUMN = "classId"
_SYMBOL_COLUMN = "symbol"


class FundSeriesError(RuntimeError):
    """The fund ticker map could not be downloaded or decoded."""


@dataclass(frozen=True, slots=True)
class FundSeries:
    """One row of the SEC fund ticker map."""

    cik: str
    series_id: str
    class_id: str | None
    symbol: str


def fund_series_path(cache_dir: Path) -> Path:
    """Where the downloaded map lives inside the engine cache."""

    return Path(cache_dir) / FUND_SERIES_FILENAME


# ---------------------------------------------------------------------------
# §1 Parser
# ---------------------------------------------------------------------------


def _column_positions(fields: object) -> dict[str, int]:
    """Each published column name mapped to its position.

    Reading by name is the whole point of the exercise: the file's order is not
    part of its contract. A blank or non-text name is skipped rather than
    indexed, so it can never shadow a real column, and a repeated name keeps the
    last position — the same "last row wins" rule the rows follow.
    """

    if isinstance(fields, (str, bytes)) or not isinstance(fields, Sequence):
        return {}
    positions: dict[str, int] = {}
    for position, name in enumerate(fields):
        if isinstance(name, str) and name.strip():
            positions[name.strip()] = position
    return positions


def _cell(row: object, position: int | None) -> object:
    """The row's value at ``position``, or ``None`` for a short/malformed row."""

    if position is None:
        return None
    if isinstance(row, (str, bytes)) or not isinstance(row, Sequence):
        return None
    if position >= len(row):
        return None
    return row[position]


def _text(value: object) -> str | None:
    """A stripped cell, or ``None`` when it is absent or blank."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def parse_fund_series(payload: object) -> dict[str, FundSeries]:
    """Parse the SEC fund ticker map into a symbol-keyed mapping.

    ``payload`` is ``{"fields": [...], "data": [[...], ...]}``. A row that does
    not name a symbol, a CIK or a series id cannot identify a series, so it is
    skipped rather than published under a guessed key: a missing ticker means
    "not in the map", which the collector turns into the fallback path, while a
    wrong row would send the collector to another fund's portfolio.

    ``cik`` is normalised to the ten-digit spelling the rest of the engine uses
    (the SEC file publishes it as a bare integer). ``classId`` is optional by
    contract — a row with no share class is still a usable fund.
    """

    if not isinstance(payload, Mapping):
        return {}
    positions = _column_positions(payload.get("fields"))
    rows = payload.get("data")
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        return {}

    mapping: dict[str, FundSeries] = {}
    for row in rows:
        cik = normalize_cik(_cell(row, positions.get(_CIK_COLUMN)))
        series_id = _text(_cell(row, positions.get(_SERIES_COLUMN)))
        symbol = _text(_cell(row, positions.get(_SYMBOL_COLUMN)))
        if cik is None or series_id is None or symbol is None:
            continue
        ticker = symbol.upper()
        mapping[ticker] = FundSeries(
            cik=cik,
            series_id=series_id,
            class_id=_text(_cell(row, positions.get(_CLASS_COLUMN))),
            symbol=ticker,
        )
    return mapping


def load_fund_series(path: Path) -> dict[str, FundSeries]:
    """Read the cached map, or ``{}`` when it is absent or unreadable.

    A missing map means "fall back", never a crash and never a wrong series: an
    operator who has not run the downloader still gets a working N-PORT lookup,
    and a half-written cache from a killed process cannot be mistaken for the
    complete document.
    """

    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    try:
        payload = json.loads(text)
    except ValueError:
        return {}
    return parse_fund_series(payload)


# ---------------------------------------------------------------------------
# §2 Download and CLI
# ---------------------------------------------------------------------------


def _headers() -> dict[str, str]:
    """Per-request headers; sent on the request so an injected client keeps them.

    EDGAR answers ``403`` to an anonymous client, so the User-Agent always
    carries the contact address from
    :data:`~app.engine.ingest.DEFAULT_USER_AGENT`.
    """

    return {"user-agent": DEFAULT_USER_AGENT, "accept": "application/json"}


async def _fetch_text(url: str, *, client: Any) -> str:
    """GET one document; every transport answer becomes one domain error.

    ``client=None`` builds the provider client **as a module attribute** so the
    suite can install a ``MockTransport`` at that seam. ``follow_redirects=False``
    means a 3xx is a refusal, so a non-200 is a failure rather than a body.
    """

    headers = _headers()
    try:
        if client is None:
            async with provider_http.build_client(headers=headers) as owned:
                response = await owned.get(url, headers=headers)
        else:
            response = await client.get(url, headers=headers)
        text = response.text
    except Exception as exc:  # noqa: BLE001 - one domain error for the boundary
        raise FundSeriesError(
            f"GET {url} failed: {type(exc).__name__}: {exc}"
        ) from exc
    if response.status_code != 200:
        raise FundSeriesError(f"GET {url} failed: HTTP {response.status_code}")
    return text


def _write_atomically(path: Path, text: str) -> None:
    """Write the map atomically, so a crash cannot leave half a document.

    The map is read on every holdings call; a reader must see either the old
    complete file or the new complete one, never a prefix that parses to a
    smaller universe of funds.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


async def download_fund_series(
    path: Path, *, client: Any = None
) -> dict[str, FundSeries]:
    """Download the fund ticker map, cache it at ``path`` and parse it.

    The raw body is written, not a re-serialisation: the cache should hold the
    document SEC answered, so a later parse sees the same bytes this call did.
    An empty or unparseable document is refused **before** the write — caching
    an error page would make every later lookup silently fall back.
    """

    target = Path(path)
    text = await _fetch_text(FUND_SERIES_URL, client=client)
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise FundSeriesError(
            f"{FUND_SERIES_URL} did not answer JSON: {exc}"
        ) from exc
    mapping = parse_fund_series(payload)
    if not mapping:
        raise FundSeriesError(f"{FUND_SERIES_URL} named no fund series")
    _write_atomically(target, text)
    return mapping


def _parser() -> argparse.ArgumentParser:
    """The CLI: one required cache directory, nothing else."""

    parser = argparse.ArgumentParser(
        prog="python -m app.engine.fund_series",
        description=(
            "Download SEC's company_tickers_mf.json fund map into the engine "
            "cache."
        ),
    )
    parser.add_argument(
        "--cache-dir",
        required=True,
        type=Path,
        help="engine cache directory that receives company_tickers_mf.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Download the map and print ``{"symbols": N, "path": ...}``.

    A failure is one JSON line on stderr and exit code 2, because the caller is
    an operator script or a cron entry: it needs a parseable answer, not a
    traceback, and a non-zero status so the job is visibly failed.
    """

    args = _parser().parse_args(argv)
    path = fund_series_path(args.cache_dir)
    try:
        mapping = asyncio.run(download_fund_series(path))
    except Exception as exc:  # noqa: BLE001 - CLI boundary: JSON error, exit 2
        print(
            json.dumps({"error": f"{type(exc).__name__}: {exc}"}),
            file=sys.stderr,
        )
        return 2
    print(json.dumps({"symbols": len(mapping), "path": str(path)}))
    return 0


if __name__ == "__main__":  # pragma: no cover - module CLI entry point
    raise SystemExit(main())


__all__ = [
    "FUND_SERIES_FILENAME",
    "FUND_SERIES_URL",
    "FundSeries",
    "FundSeriesError",
    "download_fund_series",
    "fund_series_path",
    "load_fund_series",
    "main",
    "parse_fund_series",
]
