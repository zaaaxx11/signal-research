"""TradingView scanner: one request per page, ``d`` arrays mapped **by name**.

Why the shape is what it is
---------------------------

* **Bulk, never per symbol.** One POST returns a page of rows. A screen that
  asked TradingView once per ticker would be 20 requests before the user sees
  anything, and 81k requests for the whole universe.
* **Positional on the wire, named in the parser.** The response carries
  ``data[i].d`` arrays aligned with the ``columns`` the *client* asked for, and
  no column list of its own. :func:`parse_scan` therefore zips ``columns`` with
  ``d``; a parser with hardcoded indices silently swaps volume with market cap
  the first time the caller reorders its request (the suite proves this with a
  shuffled body).
* **Rows stay provider-shaped.** ``close``, ``Value.Traded``,
  ``market_cap_basic``, ``RSI``... are carried verbatim, plus ``symbol`` and
  ``venue`` split out of ``s`` (``"NASDAQ:AAPL"``). Normalisation into screen
  columns belongs to :mod:`app.screen.universe`; a scanner that renamed fields
  would be an undocumented second schema.
* **200 + HTML is a block, not a parse error.** TradingView's bot wall answers
  ``200`` with a challenge page. Retrying it is how a scraper gets banned, so
  :class:`ScanBlocked` is terminal: exactly one request was sent and the caller
  can choose to serve a stale snapshot instead of hammering the endpoint.

The single HTTP seam (``app.providers.http.build_client``) is called as a module
attribute so a ``MockTransport`` can be installed over it; a scanner that
builds its own client is not patchable and would reach a socket from the test
process.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.contracts import VerdictKind
from app.providers import http as provider_http

#: ``region`` → the scanner path. US has its own endpoint (america) and the
#: global one carries every other venue; a single endpoint would double count.
#: ``us_etf`` is the US fund tab: the *same* america endpoint and the same
#: universe rules, addressed with ``type=fund``/``subtype=etf`` (see
#: :func:`asset_class_for`).
REGION_PATHS: dict[str, str] = {
    "us": "/america/scan",
    "global": "/global/scan",
    "us_etf": "/america/scan",
}
REGIONS: tuple[str, ...] = tuple(REGION_PATHS)
SCANNER_HOST = "https://scanner.tradingview.com"

#: Instrument families the screen addresses. ``stock`` is the default and the
#: one the browse tab has always scanned; ``etf`` is the US fund tab.
ASSET_CLASSES: tuple[str, ...] = ("stock", "etf")
STOCK_TYPE = "stock"
ETF_TYPE = "fund"
ETF_SUBTYPE = "etf"

#: The browse contract's US floor, inclusive. It is a property of the *stock*
#: tab, not of the market: an ETF screen has no cap floor (a USD 5M fund is a
#: listed fund), which is why the floor is resolved from the asset class
#: instead of being a bare function default.
DEFAULT_STOCK_FLOOR = 100_000_000


class _DefaultFloor:
    """Sentinel for "use the asset class's own floor".

    ``min_market_cap=None`` already means *no floor* (the selection contract
    relies on it), so the default cannot be ``None`` without losing that
    distinction. This sentinel keeps both meanings: omitted → stock 100M / ETF
    none; explicit ``None`` → no floor; explicit number → that floor.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "DEFAULT_FLOOR"


DEFAULT_FLOOR = _DefaultFloor()


#: Rows one page may return. The display only needs 20 and the universe filter
#: runs locally, so the page is deliberately small: a single request, not a
#: pagination loop against a bot-walled endpoint.
PAGE_ROWS = 5_000
#: The page index a single-request scan always asks for.
FIRST_PAGE = 1
#: One request large enough to hold the filtered live universe (observed
#: 65k rows for Global, 3.9k for US). Above this the snapshot falls back to
#: ``fetch_all``; the display refresh should not page 13 times.
FULL_SCAN_ROWS = 100_000

#: Fields the scan asks for. The first block is what the screen row needs; the
#: last three (``type``/``subtype``/``currency``) exist so the universe filter and
#: the FX honesty rule can run without a second request.
SCAN_COLUMNS: tuple[str, ...] = (
    "name",
    "description",
    "exchange",
    "country",
    "sector",
    "type",
    "subtype",
    "currency",
    "close",
    "change",
    "volume",
    "Value.Traded",
    "market_cap_basic",
    "RSI",
    "beta_1_year",
    "price_52_week_high",
    "price_52_week_low",
)

#: ETFs report AUM (``aum``) and no ``market_cap_basic``; a fund screen that
#: asked only for market cap would rank 5.7k funds as "unknown". The ETF scan
#: therefore carries the AUM column and the universe uses it as the cap.
ETF_SCAN_COLUMNS: tuple[str, ...] = SCAN_COLUMNS + ("aum",)

#: The market the US tab scans. ``global`` sends every market the venue list
#: names; the universe splits the tabs afterwards. ``us_etf`` scans the same
#: America market as ``us`` — the instrument family is a filter, not a venue.
REGION_MARKETS: dict[str, tuple[str, ...]] = {
    "us": ("america",),
    # TradingView has no ``global`` *market id*: sending ``markets=["global"]``
    # answers an empty page. Omitting the key scans every venue; the universe
    # layer removes US issuers for the Global tab.
    "global": (),
    "us_etf": ("america",),
}


def asset_class_for(region: str, asset_class: str | None = None) -> str:
    """Resolve the instrument family of ``region`` (``stock`` or ``etf``).

    ``us_etf`` is *defined* as the US fund tab, so it can only ever be ``etf``:
    a caller that asks for ``asset_class="stock"`` there is contradicting the
    region name and gets a ``ValueError`` instead of a silently empty screen.
    ``us`` may explicitly ask for ``etf`` (the ETF universe without the tab
    alias), while ``global`` is stock-only in this product — the ETF contract
    is US (PLAN-finviz-parity.md §2).
    """

    if region not in REGION_PATHS:
        raise ValueError(f"unknown scan region {region!r}; expected one of {', '.join(REGIONS)}")
    inferred = "etf" if region == "us_etf" else "stock"
    if asset_class is None:
        return inferred
    if asset_class not in ASSET_CLASSES:
        raise ValueError(
            f"unknown asset_class {asset_class!r}; expected one of {', '.join(ASSET_CLASSES)}"
        )
    if region == "us_etf" and asset_class != "etf":
        raise ValueError(
            f"region 'us_etf' is the US ETF tab; asset_class must be 'etf', got {asset_class!r}"
        )
    if region == "global" and asset_class == "etf":
        raise ValueError(
            "the ETF screen is US-only (region 'us' or 'us_etf'); region 'global' "
            "is not an ETF universe"
        )
    return asset_class


REQUEST_HEADERS: dict[str, str] = {
    "accept": "application/json",
    "content-type": "application/json",
    "origin": "https://www.tradingview.com",
    "referer": "https://www.tradingview.com/",
    "user-agent": "Mozilla/5.0 (compatible; ztockz-screen/1.0; +https://ztockz.local)",
}


class ScanError(RuntimeError):
    """The scanner could not produce a page (never a silent empty universe)."""


class ScanBlocked(ScanError):
    """The bot wall answered: HTTP 200 with HTML, or a throttling status.

    Terminal by design. ``verdict`` is the content verdict the rest of the
    engine already understands (02 §3.4), so a blocked scan is reported as a
    *blocked provider*, not as an empty screen with ``total=0``.
    """

    def __init__(
        self,
        message: str,
        *,
        verdict: VerdictKind = VerdictKind.BLOCKED,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.verdict = verdict
        self.status_code = status_code


@dataclass(frozen=True)
class ScanPage:
    """One provider page: the provider's own row count and the parsed rows."""

    total_count: int
    rows: list[dict[str, Any]] = field(default_factory=list)
    region: str | None = None
    #: Rows the provider sent, before any local filter. ``total_count`` may be
    #: larger than ``len(rows)`` when the page is capped.
    received: int = 0


def scan_body(
    region: str,
    *,
    columns: Sequence[str] | None = None,
    offset: int = 0,
    limit: int = PAGE_ROWS,
    min_market_cap: float | None | _DefaultFloor = DEFAULT_FLOOR,
    subtype: str | None = "common",
    asset_class: str | None = None,
) -> dict[str, Any]:
    """The POST body for one page of ``region``.

    ``filter`` is expressed as a filter list rather than left to the universe
    layer alone: asking for common stock only keeps the page small and the
    local filter stays the authority (a provider that ignores the filter cannot
    widen the universe, because :func:`app.screen.universe.build_universe` runs
    regardless).

    ``min_market_cap`` and ``subtype`` are the two filters a caller may drop, and
    ``None`` means exactly that — the key is not sent, so the provider does not
    hide rows the caller wants to see:

    * ``min_market_cap=None`` scans the whole cap range (a selection filtering on
      P/E must see a USD 30M company too; the default 100M floor is the browse
      contract, not a property of the market);
    * ``subtype=None`` scans every subtype of ``type=stock`` (the Finviz panel
      addresses stocks, not only common shares).

    ``asset_class='etf'`` (or ``region='us_etf'``) swaps the type gate to
    ``type=fund`` + ``subtype=etf``. The ETF contract has **no cap floor**: an
    omitted ``min_market_cap`` sends no cap filter, because a small fund is
    still a listed fund. ``subtype`` is pinned to ``etf`` there — a fund without
    the subtype gate would also admit closed-end funds and commodity pools,
    which are not the Finviz ETF universe. OTC exclusion and the US market stay
    on in both families: a pink-sheet fund is out of scope whatever the class.

    Neither ``None`` relaxes the type gate or the OTC exclusion: a fund is not a
    stock and a pink sheet is out of scope, whatever the caller asks for.
    """

    if region not in REGION_PATHS:
        raise ValueError(f"unknown scan region {region!r}; expected one of {', '.join(REGIONS)}")
    family = asset_class_for(region, asset_class)
    requested = tuple(columns) if columns is not None else (
        ETF_SCAN_COLUMNS if family == "etf" else SCAN_COLUMNS
    )
    filters: list[dict[str, Any]] = [
        {
            "left": "type",
            "operation": "equal",
            "right": STOCK_TYPE if family == "stock" else ETF_TYPE,
        },
    ]
    if family == "etf":
        filters.append({"left": "subtype", "operation": "equal", "right": ETF_SUBTYPE})
    elif subtype is not None:
        filters.append({"left": "subtype", "operation": "equal", "right": subtype})
    floor = min_market_cap
    if isinstance(floor, _DefaultFloor):
        floor = DEFAULT_STOCK_FLOOR if family == "stock" else None
    if floor is not None:
        value = (
            floor
            if isinstance(floor, (int, float)) and not isinstance(floor, bool)
            else float(floor)
        )
        filters.append(
            {"left": "market_cap_basic", "operation": "greater", "right": value}
        )
    filters.append({"left": "exchange", "operation": "not_in_range", "right": ["OTC", "PNK"]})
    body: dict[str, Any] = {
        "filter": filters,
        "options": {"lang": "en"},
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": list(requested),
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
        "range": [max(0, int(offset)), max(0, int(offset)) + max(1, int(limit))],
    }
    markets = REGION_MARKETS[region]
    if markets:
        body["markets"] = list(markets)
    return body


def scan_url(region: str) -> str:
    if region not in REGION_PATHS:
        raise ValueError(f"unknown scan region {region!r}; expected one of {', '.join(REGIONS)}")
    return SCANNER_HOST + REGION_PATHS[region]


def split_ticker(ticker: str) -> tuple[str, str]:
    """``"NASDAQ:AAPL"`` → ``("NASDAQ", "AAPL")``; a bare ticker has no venue."""

    head, _, tail = str(ticker).partition(":")
    if not tail:
        return "", head
    return head, tail


def parse_scan(body: Mapping[str, Any], columns: Sequence[str]) -> ScanPage:
    """Parse one page: ``d`` is positional, so it is mapped through ``columns``.

    ``totalCount`` is carried verbatim — the provider's count, not
    ``len(data)``: a page may be capped while the provider still says how many
    rows the query has.
    """

    names = tuple(columns)
    raw_rows = body.get("data") or []
    total = body.get("totalCount")
    rows: list[dict[str, Any]] = []
    for entry in raw_rows:
        if not isinstance(entry, Mapping):
            raise ScanError(f"scan row is not an object: {entry!r}")
        cells = entry.get("d") or []
        if not isinstance(cells, (list, tuple)):
            raise ScanError(f"scan row has a non-array d: {cells!r}")
        row = {name: (cells[index] if index < len(cells) else None) for index, name in enumerate(names)}
        venue, symbol = split_ticker(str(entry.get("s") or ""))
        row["venue"] = venue
        row["symbol"] = symbol
        rows.append(row)

    if total is None:
        total = len(rows)
    try:
        total_count = int(total)
    except (TypeError, ValueError) as exc:
        raise ScanError(f"scan body has a non-numeric totalCount: {total!r}") from exc

    return ScanPage(total_count=total_count, rows=rows, received=len(rows))


def _looks_like_html(content_type: str, payload: bytes) -> bool:
    if "html" in content_type.lower():
        return True
    return payload.lstrip()[:1] == b"<"


async def fetch_scan(
    region: str,
    *,
    columns: Sequence[str] | None = None,
    timeout_s: float = 20.0,
    offset: int = 0,
    limit: int = PAGE_ROWS,
    min_market_cap: float | None | _DefaultFloor = DEFAULT_FLOOR,
    subtype: str | None = "common",
    asset_class: str | None = None,
) -> ScanPage:
    """Fetch one page for ``region`` — exactly one request, no retry.

    A retry loop against a bot wall is how a scraper earns a ban, so every
    failure is terminal here: the caller (the snapshot layer) decides whether to
    serve a stale snapshot, and it never re-asks the same request in this call.

    ``min_market_cap``/``subtype``/``asset_class`` reach :func:`scan_body`
    unchanged; ``None`` drops a provider filter (see there for why a selection
    needs the wider page while the browse display does not, and why the ETF
    family has no cap floor by default).
    """

    url = scan_url(region)
    body = scan_body(
        region,
        columns=columns,
        offset=offset,
        limit=limit,
        min_market_cap=min_market_cap,
        subtype=subtype,
        asset_class=asset_class,
    )
    async with provider_http.build_client(
        timeout_s=timeout_s, headers=dict(REQUEST_HEADERS)
    ) as client:
        response = await client.post(url, json=body)
        payload = response.content or b""
        content_type = response.headers.get("content-type", "")

        if response.status_code != 200:
            verdict = (
                VerdictKind.THROTTLED
                if response.status_code in (429, 503)
                else VerdictKind.BLOCKED
            )
            raise ScanBlocked(
                f"scanner answered HTTP {response.status_code} for {url}",
                verdict=verdict,
                status_code=response.status_code,
            )
        if _looks_like_html(content_type, payload):
            raise ScanBlocked(
                "scanner answered 200 with an HTML body (bot wall); the scan is "
                "blocked and was not retried",
                status_code=response.status_code,
            )
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ScanError(f"scanner body is not JSON for {url}: {exc}") from exc

    if not isinstance(document, Mapping):
        raise ScanError(f"scanner body is not a JSON object for {url}")
    page = parse_scan(document, tuple(body["columns"]))
    return ScanPage(
        total_count=page.total_count, rows=page.rows, region=region, received=page.received
    )


async def fetch_all(
    region: str,
    *,
    columns: Sequence[str] | None = None,
    timeout_s: float = 20.0,
    max_rows: int | None = None,
    min_market_cap: float | None | _DefaultFloor = DEFAULT_FLOOR,
    subtype: str | None = "common",
    asset_class: str | None = None,
) -> ScanPage:
    """Page through the whole scanner result for an on-demand export.

    The display path deliberately sends one request and keeps the top 20; a
    full export is the only caller allowed to walk every page. ``totalCount``
    is the provider's promise, so the loop stops when the received rows reach
    it (or when a page stops making progress, which would otherwise spin).

    Every page is requested with the **same** ``min_market_cap``/``subtype``/
    ``asset_class`` as the first: a second page that silently re-applied the cap
    would drop rows the caller asked for and still look complete.
    """

    first = await fetch_scan(
        region,
        columns=columns,
        timeout_s=timeout_s,
        min_market_cap=min_market_cap,
        subtype=subtype,
        asset_class=asset_class,
    )
    rows = list(first.rows)
    total = first.total_count
    if max_rows is not None:
        total = min(total, max(0, int(max_rows)))
    while len(rows) < total:
        page = await fetch_scan(
            region,
            columns=columns,
            timeout_s=timeout_s,
            offset=len(rows),
            min_market_cap=min_market_cap,
            subtype=subtype,
            asset_class=asset_class,
        )
        if not page.rows:
            break
        rows.extend(page.rows)
        if len(page.rows) < min(PAGE_ROWS, total - (len(rows) - len(page.rows))):
            # A short page means the provider has no more rows to give.
            break
    return ScanPage(
        total_count=total,
        rows=rows[:total] if max_rows is not None else rows,
        region=region,
        received=len(rows),
    )


__all__ = [
    "ASSET_CLASSES",
    "DEFAULT_FLOOR",
    "DEFAULT_STOCK_FLOOR",
    "ETF_SUBTYPE",
    "ETF_TYPE",
    "ETF_SCAN_COLUMNS",
    "FIRST_PAGE",
    "PAGE_ROWS",
    "REGIONS",
    "REGION_PATHS",
    "SCANNER_HOST",
    "SCAN_COLUMNS",
    "STOCK_TYPE",
    "ScanBlocked",
    "ScanError",
    "ScanPage",
    "asset_class_for",
    "fetch_all",
    "fetch_scan",
    "parse_scan",
    "scan_body",
    "scan_url",
    "split_ticker",
]
