"""Official index membership and optionable status, from the data's owners.

``index_membership`` and ``optionable`` were the two depth fields only Finviz
printed. Both have an official producer, and neither needs a scrape:

* S&P 500/400/600 come from the SSGA SPY/MDY/SPSM daily holdings XLSX; DJIA
  from SSGA DIA. The header is located **by name** (a ticker/symbol column next
  to a weight/shares column), never by fixed offset: the sheet carries a
  three-row preamble today and has changed it before.
* NDX comes from Nasdaq's own constituent list (101 rows), not from an ETF.
* Russell 2000 comes from the IWM holdings CSV that iShares publishes: the
  fund is the index's own tracker, so its holdings are the membership list.
  That file carries an eight-row fund preamble and an ``Asset Class`` column,
  and only ``Equity`` rows are members — cash collateral, money market,
  currency and the RTY future are not.
* ``optionable`` is a Cboe ``HEAD``: 200 = listed options, 403/404 = none.
  No body is transferred, so 4k symbols cost zero bytes.

The parser half of this module is pure — bytes in, tickers out, no network, no
clock. :class:`OfficialDepthSource` is the only part that fetches, and it takes
injected ``index_loader`` / ``optionable_checker`` / ``client_factory`` so the
whole batch path is exercisable offline.

Only two fields are ever emitted, and a symbol with neither is **absent**: a
zero-filled ``optionable`` would read as "No" and lie about an underlying
nobody asked Cboe about. ``index_membership`` is a comma-sorted string
(``"DJIA, NDX, S&P 500"``), and an index label is written only when the file
that owns it actually listed the symbol.
"""

from __future__ import annotations

import asyncio
import csv
import inspect
import io
import logging
import re
import time
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from xml.etree import ElementTree as ET

from app.market.depth import DepthObservation, canonical_symbol

logger = logging.getLogger("mee.depth.official")

#: SSGA publishes one holdings workbook per fund; the fund symbol is the only
#: variable part of the URL.
SSGA_HOLDINGS_URL = (
    "https://www.ssga.com/library-content/products/fund-data/etfs/us/"
    "holdings-daily-us-en-{fund}.xlsx"
)
NDX_URL = (
    "https://api.nasdaq.com/api/quote/list-type/nasdaq100"
    "?assetclass=stocks&limit=200"
)
#: iShares' own holdings download for IWM; the product id (239710) is stable and
#: the file is a plain CSV, no session cookie required.
IWM_HOLDINGS_URL = (
    "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/"
    "latest-holdings.csv"
)
CBOE_OPTION_URL = "https://cdn-api.cboe.com/api/global/delayed_quotes/options/{symbol}.json"

RUSSELL_2000_LABEL = "Russell 2000"

#: Fund -> index label. SPTM (S&P 1500) is deliberately not used: it would
#: collapse 400/600 into one unlabeled bucket.
INDEX_FILES: tuple[tuple[str, str], ...] = (
    ("S&P 500", "spy"),
    ("S&P 400", "mdy"),
    ("S&P 600", "spsm"),
    ("DJIA", "dia"),
)

#: Label -> URL for the index files that are not SSGA workbooks. IWM is the
#: Russell 2000's own tracker, so its published holdings are the membership
#: list; the file is CSV, not a workbook.
CSV_INDEX_FILES: tuple[tuple[str, str], ...] = ((RUSSELL_2000_LABEL, IWM_HOLDINGS_URL),)

INDEX_TTL_S = 86_400.0
#: A partial load (one SSGA file down) is retried long before a day passes.
INDEX_RETRY_S = 900.0
#: Underlying option lists change a few times a year; six hours is already
#: aggressive, and it keeps the daily depth cycle off Cboe's CDN.
OPTIONABLE_TTL_S = 21_600.0
DEFAULT_CONCURRENCY = 4

#: Cboe answers ``403`` for an underlying with no listed options. ``404`` is
#: what the CDN returns for a symbol it has no record of at all; for this field
#: both mean "not optionable", while any other status stays *unknown*.
NO_OPTIONS_STATUSES = frozenset({403, 404})

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

#: Per-process cache of the parsed index files. One depth cycle asks for the
#: same six files on behalf of every symbol; parsing them once is the whole
#: point. ``clear_caches`` exists for tests and for a forced refresh.
_INDEX_CACHE: dict[str, Any] = {}


def clear_caches() -> None:
    """Drop the process-wide index cache (tests and forced refresh)."""

    _INDEX_CACHE.clear()


# ---------------------------------------------------------------------------
# XLSX reading (stdlib only)
# ---------------------------------------------------------------------------

_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _column_index(reference: str) -> int:
    """``"B5"`` -> ``1`` (zero-based column index)."""

    letters = "".join(ch for ch in reference if ch.isalpha()).upper()
    if not letters:
        return 0
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - 64)
    return max(0, index - 1)


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        raw = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    out: list[str] = []
    for node in root:
        if _local(node.tag) != "si":
            continue
        # A cell may be a rich-text run list; concatenate every <t> in order.
        out.append("".join(part.text or "" for part in node.iter() if _local(part.tag) == "t"))
    return out


def _sheet_paths(archive: zipfile.ZipFile, names: set[str]) -> list[str]:
    """Worksheets in workbook order; falls back to the raw file order."""

    fallback = sorted(
        name for name in names if name.startswith("xl/worksheets/") and name.endswith(".xml")
    )
    try:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    except (KeyError, ET.ParseError):
        return fallback
    targets = {
        str(rel.get("Id")): str(rel.get("Target") or "")
        for rel in relationships
        if _local(rel.tag) == "Relationship"
    }
    ordered: list[str] = []
    for sheet in workbook.iter():
        if _local(sheet.tag) != "sheet":
            continue
        target = targets.get(str(sheet.get(f"{_REL_NS}id") or sheet.get("id") or ""), "")
        if not target:
            continue
        candidate = target.lstrip("/")
        resolved = ""
        if candidate.startswith("xl/"):
            resolved = candidate
        elif f"xl/{candidate}" in names:
            resolved = f"xl/{candidate}"
        elif candidate in names:
            resolved = candidate
        if resolved and resolved in names and resolved not in ordered:
            ordered.append(resolved)
    return ordered or fallback


def _cell_value(cell: ET.Element, shared: Sequence[str]) -> Any:
    kind = str(cell.get("t") or "")
    raw: str | None = None
    inline: str | None = None
    for child in cell:
        name = _local(child.tag)
        if name == "v":
            raw = child.text
        elif name == "is":
            inline = "".join(part.text or "" for part in child.iter() if _local(part.tag) == "t")
    if kind == "inlineStr":
        return (inline or "").strip()
    if kind == "s":
        try:
            return shared[int(raw or "")]
        except (TypeError, ValueError, IndexError):
            return ""
    if kind == "str":
        return (raw or "").strip()
    if kind == "b":
        return (raw or "").strip() == "1"
    if kind == "e":
        return ""
    value = (raw if raw is not None else inline) or ""
    text = value.strip()
    if not text:
        return ""
    try:
        return float(text) if ("." in text or "e" in text.lower()) else int(text)
    except ValueError:
        return text


def _read_sheet(payload: bytes, shared: Sequence[str]) -> list[list[Any]]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []
    rows: list[list[Any]] = []
    for row in root.iter():
        if _local(row.tag) != "row":
            continue
        cells: dict[int, Any] = {}
        position = 0
        for cell in row:
            if _local(cell.tag) != "c":
                continue
            reference = str(cell.get("r") or "")
            index = _column_index(reference) if reference else position
            position = index + 1
            cells[index] = _cell_value(cell, shared)
        if not cells:
            continue
        rows.append([cells.get(index) for index in range(max(cells) + 1)])
    return rows


def _xlsx_sheets(raw: bytes) -> list[list[list[Any]]]:
    """Every worksheet's rows, in workbook order; ``[]`` on junk."""

    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, OSError, ValueError):
        return []
    with archive:
        names = set(archive.namelist())
        shared = _shared_strings(archive)
        sheets: list[list[list[Any]]] = []
        for path in _sheet_paths(archive, names):
            try:
                payload = archive.read(path)
            except KeyError:
                continue
            rows = _read_sheet(payload, shared)
            if rows:
                sheets.append(rows)
    return sheets


def _payload_bytes(content: bytes | str) -> bytes:
    if isinstance(content, (bytes, bytearray, memoryview)):
        return bytes(content)
    if isinstance(content, str):
        try:
            return content.encode("latin-1")
        except UnicodeEncodeError:
            return content.encode("utf-8", "replace")
    return b""


def _payload_text(content: bytes | str, raw: bytes) -> str:
    if isinstance(content, str):
        return content
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _delimited_rows(text: str) -> list[list[Any]]:
    """Fallback for a CSV/TSV response where an XLSX was expected."""

    if not text.strip():
        return []
    sample = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = max((",", "\t", ";"), key=sample.count)
    if sample.count(delimiter) == 0:
        return []
    try:
        return [list(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
    except csv.Error:
        return []


# ---------------------------------------------------------------------------
# SSGA holdings parsing
# ---------------------------------------------------------------------------

_TICKER_HEADERS = frozenset({"ticker", "tickers", "symbol", "symbols", "tickersymbol"})
_EXACT_TICKER_HEADERS = frozenset({"ticker", "symbol"})
_WEIGHT_HINTS = ("weight", "shares", "marketvalue")

#: Plain US tickers, with one optional class suffix (``BRK.B`` / ``BRK-B``).
_SYMBOL_RE = re.compile(r"^[A-Z]{1,6}(?:[.\-][A-Z]{1,2})?$")
#: iShares spells a class with a space (``MOG A``, ``BH A``); the repo keys
#: classes with a dash, so that form is collapsed before validation.
_SPACE_CLASS_RE = re.compile(r"^([A-Z]{1,6}) ([A-Z]{1,2})$")
#: Futures contract codes (``ESZ6``, ``RTYZ6``) are letters + month + year.
_FUTURE_RE = re.compile(r"^[A-Z]{1,6}[FGHJKMNQUVXZ]\d{1,2}$")
_CASH_SYMBOLS = frozenset(
    {"CASH", "USD", "USDOLLAR", "CASHUSD", "MONEYMARKET", "NA", "-", "--"}
)


def _normalize_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value if value is not None else "").lower())


def _header_columns(row: Sequence[Any]) -> tuple[int | None, int | None]:
    ticker: int | None = None
    weight: int | None = None
    for index, cell in enumerate(row):
        name = _normalize_header(cell)
        if not name:
            continue
        if ticker is None and name in _TICKER_HEADERS:
            ticker = index
        if weight is None and any(hint in name for hint in _WEIGHT_HINTS):
            weight = index
    return ticker, weight


def _find_header(rows: Sequence[Sequence[Any]]) -> tuple[int | None, int | None]:
    """Locate the header row and the ticker column in it.

    Strict pass: a row that has both a ticker column and a weight/shares column.
    Relaxed pass (for a file whose weight column was renamed): a row with at
    least three filled cells and a column literally called ``Ticker``/``Symbol``.
    The relaxed pass is what keeps the two-cell ``Ticker Symbol: SPY`` preamble
    from being mistaken for the header.
    """

    limit = min(len(rows), 60)
    for index in range(limit):
        ticker, weight = _header_columns(rows[index])
        if ticker is not None and weight is not None:
            return index, ticker
    for index in range(limit):
        row = rows[index]
        ticker, _ = _header_columns(row)
        if ticker is None or ticker >= len(row):
            continue
        filled = sum(1 for cell in row if str(cell if cell is not None else "").strip())
        if filled >= 3 and _normalize_header(row[ticker]) in _EXACT_TICKER_HEADERS:
            return index, ticker
    return None, None


def _canonical_class_symbol(value: Any) -> str:
    """Canonicalise a listed ticker, including iShares' space class form."""

    text = str(value if value is not None else "").strip().upper()
    return canonical_symbol(_SPACE_CLASS_RE.sub(r"\1-\2", text))


def _clean_ticker(value: Any) -> str | None:
    """Normalise one ticker cell, or ``None`` when the row is not an equity."""

    if value is None or isinstance(value, (bool, int, float)):
        return None
    text = str(value).strip().upper()
    if not text or text in _CASH_SYMBOLS:
        return None
    text = _SPACE_CLASS_RE.sub(r"\1-\2", text)
    if not _SYMBOL_RE.match(text) or _FUTURE_RE.match(text):
        return None
    symbol = canonical_symbol(text)
    return symbol or None


def parse_ssga_holdings(content: bytes | str, index_name: str) -> set[str]:
    """Tickers held by an SSGA fund file, labelled ``index_name``.

    ``content`` is the workbook bytes (a ``str`` is accepted for a decoded or
    delimited payload). The returned set is canonicalised through
    :func:`app.market.depth.canonical_symbol`, so ``BRK.B`` and ``BRK-B`` are
    one ticker. Preamble rows, the header, cash lines, futures contracts,
    dashes and duplicates are dropped; an unreadable file yields an empty set
    rather than an exception, because a missing index must not fail a depth
    cycle.
    """

    label = str(index_name or "").strip()
    if not label:
        raise ValueError("index_name must be non-empty")

    raw = _payload_bytes(content)
    candidates = _xlsx_sheets(raw) if raw[:2] == b"PK" else []
    if not candidates:
        delimited = _delimited_rows(_payload_text(content, raw))
        if delimited:
            candidates = [delimited]
    if not candidates:
        logger.info("ssga holdings for %s: unreadable payload (%s bytes)", label, len(raw))
        return set()

    for rows in candidates:
        header_index, ticker_column = _find_header(rows)
        if header_index is None or ticker_column is None:
            continue
        found: set[str] = set()
        for row in rows[header_index + 1 :]:
            if ticker_column >= len(row):
                continue
            symbol = _clean_ticker(row[ticker_column])
            if symbol is not None:
                found.add(symbol)
        logger.info("ssga holdings for %s: %s tickers", label, len(found))
        return found

    logger.warning("ssga holdings for %s: no ticker column found", label)
    return set()


# ---------------------------------------------------------------------------
# iShares holdings CSV parsing (Russell 2000 / IWM)
# ---------------------------------------------------------------------------

#: Header names iShares has used for the column that separates stock rows from
#: cash, collateral, money-market and futures rows.
_ASSET_CLASS_HEADERS = frozenset({"assetclass", "assettype", "asset"})
#: Only rows whose ``Asset Class`` is this word are index members.
_EQUITY_ASSET_CLASS = "equity"


def _ishares_header(rows: Sequence[Sequence[Any]]) -> tuple[int | None, int | None, int | None]:
    """Locate the holdings header row and its ticker / asset-class columns.

    The header is found **by name**: a row carrying a cell literally called
    ``Ticker`` (or ``Symbol``). The preamble's ``Ticker Symbol:`` label is not
    a header — its normalised cell is ``tickersymbol``, not ``ticker`` — and
    the eight-row preamble is not assumed, only bounded.
    """

    limit = min(len(rows), 60)
    for index in range(limit):
        ticker: int | None = None
        asset_class: int | None = None
        for column, cell in enumerate(rows[index]):
            name = _normalize_header(cell)
            if not name:
                continue
            if ticker is None and name in _EXACT_TICKER_HEADERS:
                ticker = column
            elif asset_class is None and name in _ASSET_CLASS_HEADERS:
                asset_class = column
        if ticker is not None:
            return index, ticker, asset_class
    return None, None, None


def parse_ishares_holdings(content: bytes | str, index_name: str = RUSSELL_2000_LABEL) -> set[str]:
    """Russell 2000 members from an iShares ``latest-holdings.csv`` payload.

    The file opens with a fund preamble (name, as-of date, inception, shares
    outstanding, then Stock/Bond/Cash/Other) and a blank line before the real
    header; the header is therefore located by name, never by offset. Rows are
    members only when their ``Asset Class`` cell is ``Equity`` — the file also
    lists a money-market fund, two cash-collateral lines, a currency line and
    the RTY future, and none of those are index constituents.

    Tickers are canonicalised through :func:`app.market.depth.canonical_symbol`,
    so iShares' ``MOG A``/``BRK.B`` spellings become ``MOG-A``/``BRK-B``.
    Quoted commas, duplicated rows, CRLF, a BOM and lowercase tickers are all
    tolerated; an unreadable, empty or header-less file yields an empty set
    rather than an exception, and the caller then writes no label.
    """

    label = str(index_name or "").strip()
    if not label:
        raise ValueError("index_name must be non-empty")

    raw = _payload_bytes(content)
    text = _payload_text(content, raw)
    if not text.strip():
        return set()
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=","))
    except csv.Error as exc:
        logger.warning("ishares holdings for %s: unreadable CSV (%s)", label, exc)
        return set()

    header_index, ticker_column, asset_column = _ishares_header(rows)
    if header_index is None or ticker_column is None:
        logger.warning("ishares holdings for %s: no Ticker column found", label)
        return set()

    found: set[str] = set()
    for row in rows[header_index + 1 :]:
        if ticker_column >= len(row):
            continue
        if asset_column is not None:
            if asset_column >= len(row):
                continue
            if _normalize_header(row[asset_column]) != _EQUITY_ASSET_CLASS:
                continue
        symbol = _clean_ticker(row[ticker_column])
        if symbol is not None:
            found.add(symbol)
    logger.info("ishares holdings for %s: %s tickers", label, len(found))
    return found


# ---------------------------------------------------------------------------
# Nasdaq NDX parsing
# ---------------------------------------------------------------------------


def _rows_from(payload: Any, depth: int = 0) -> list[Any]:
    """Find the constituent list in a Nasdaq envelope without a fixed shape."""

    if depth > 3:
        return []
    if isinstance(payload, Mapping):
        for key in ("rows", "data", "symbols", "list"):
            candidate = payload.get(key)
            if isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes)):
                if candidate:
                    return list(candidate)
            elif isinstance(candidate, Mapping):
                found = _rows_from(candidate, depth + 1)
                if found:
                    return found
        return []
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        return list(payload)
    return []


def parse_nasdaq_ndx(payload: Mapping) -> set[str]:
    """NDX constituents from ``api.nasdaq.com/api/quote/list-type/nasdaq100``.

    Nasdaq wraps the list as ``{"data": {"data": {"rows": [...]}}}`` today; the
    envelope is searched rather than indexed so a wrapper change degrades to an
    empty set instead of a KeyError. An empty payload is an empty set.
    """

    if not isinstance(payload, Mapping):
        return set()
    found: set[str] = set()
    for row in _rows_from(payload):
        if isinstance(row, Mapping):
            raw = row.get("symbol") or row.get("Symbol") or row.get("ticker")
        else:
            raw = row
        symbol = _clean_ticker(raw)
        if symbol is not None:
            found.add(symbol)
    return found


# ---------------------------------------------------------------------------
# Cboe optionable status
# ---------------------------------------------------------------------------


def parse_optionable_status(status: int) -> bool | None:
    """Map a Cboe ``HEAD`` status to a verdict; anything unexpected is unknown.

    200 means the CDN has an option chain for the underlying, 403 means it does
    not. Every other status (301, 404, 429, 5xx) is deliberately ``None`` here;
    :class:`OfficialDepthSource` folds 404 into ``False`` at the transport edge
    and drops the rest instead of guessing.
    """

    try:
        code = int(status)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if code == 200:
        return True
    if code == 403:
        return False
    return None


def _cboe_url(symbol: str) -> str:
    # Cboe addresses share classes with a dot: BRK-B -> BRK.B.
    return CBOE_OPTION_URL.format(symbol=symbol.replace("-", "."))


# ---------------------------------------------------------------------------
# The depth source
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _IndexSpec:
    name: str
    url: str
    kind: str


INDEX_SPECS: tuple[_IndexSpec, ...] = (
    tuple(
        _IndexSpec(name=name, url=SSGA_HOLDINGS_URL.format(fund=fund), kind="xlsx")
        for name, fund in INDEX_FILES
    )
    + tuple(_IndexSpec(name=name, url=url, kind="csv") for name, url in CSV_INDEX_FILES)
    + (_IndexSpec(name="NDX", url=NDX_URL, kind="ndx"),)
)


async def _resolve(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _as_bool(value: Any) -> bool | None:
    """Accept a bool from a checker, or its textual spelling."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"yes", "true", "1"}:
            return True
        if text in {"no", "false", "0"}:
            return False
    if isinstance(value, int):
        return bool(value)
    return None


def _normalize_index_sets(raw: Any) -> dict[str, frozenset[str]]:
    """Coerce a loader result into ``{label: frozenset(tickers)}``."""

    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, frozenset[str]] = {}
    for name, members in raw.items():
        label = str(name or "").strip()
        if not label:
            continue
        if isinstance(members, (str, bytes)) or not isinstance(members, Iterable):
            continue
        values: set[str] = set()
        try:
            for member in members:
                symbol = _clean_ticker(member)
                if symbol is not None:
                    values.add(symbol)
        except TypeError:
            continue
        if values:
            out[label] = frozenset(values)
    return out


class OfficialDepthSource:
    """``index_membership`` + ``optionable`` from SSGA, Nasdaq and Cboe.

    Production calls need no arguments: the six index files are fetched once
    per process (24 h TTL, 15 min after a partial load) and Cboe is probed with
    four concurrent HEADs. Tests inject ``index_loader`` (returns
    ``{label: tickers}``) and ``optionable_checker`` (``symbol -> bool|None``),
    which replaces the network entirely.
    """

    name = "official"

    def __init__(
        self,
        *,
        index_loader: Callable[[], Any] | None = None,
        optionable_checker: Callable[[str], Any] | None = None,
        optionable: bool = True,
        client_factory: Callable[..., Any] | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        optionable_ttl_s: float = OPTIONABLE_TTL_S,
        index_ttl_s: float = INDEX_TTL_S,
        index_retry_s: float = INDEX_RETRY_S,
        timeout_s: float = 30.0,
        optionable_delay_s: float = 0.25,
        clock: Callable[[], float] | None = None,
        cache_indices: bool | None = None,
        user_agent: str = _BROWSER_USER_AGENT,
    ) -> None:
        if int(concurrency) < 1:
            raise ValueError("concurrency must be >= 1")
        if float(optionable_ttl_s) < 0 or float(index_ttl_s) < 0:
            raise ValueError("cache TTLs must be >= 0")
        if float(timeout_s) <= 0:
            raise ValueError("timeout_s must be > 0")
        self.concurrency = int(concurrency)
        self.optionable_ttl_s = float(optionable_ttl_s)
        self.index_ttl_s = float(index_ttl_s)
        self.index_retry_s = float(index_retry_s)
        self.timeout_s = float(timeout_s)
        self.optionable_delay_s = max(0.0, float(optionable_delay_s))
        self.user_agent = user_agent
        self._index_loader = index_loader
        self._optionable_checker = optionable_checker
        self._optionable_enabled = bool(optionable)
        self._client_factory = client_factory
        self._clock = clock or time.monotonic
        # Default: only a real production transport may touch the process-wide
        # cache. An injected factory is a test double and must see its own
        # responses every time unless it asks for caching explicitly.
        self._cache_indices = (
            bool(cache_indices)
            if cache_indices is not None
            else index_loader is None and client_factory is None
        )
        self._optionable_cache: dict[str, tuple[float, bool]] = {}

    # -- plumbing ---------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "user-agent": self.user_agent,
            "accept": "application/json, text/csv, */*",
            "accept-language": "en-US,en;q=0.9",
        }

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory(timeout_s=self.timeout_s, headers=self._headers())
        # Module attribute, never a from-import: the suite monkeypatches this
        # exact attribute to install a MockTransport (app/providers/http.py).
        from app.providers import http as provider_http

        return provider_http.build_client(timeout_s=self.timeout_s, headers=self._headers())

    def reset(self) -> None:
        """Forget the per-instance optionable cache."""

        self._optionable_cache.clear()

    # -- index membership -------------------------------------------------

    async def _index_sets(self) -> dict[str, frozenset[str]]:
        if self._index_loader is not None:
            try:
                return _normalize_index_sets(await _resolve(self._index_loader()))
            except Exception as exc:  # noqa: BLE001 - membership is one field
                logger.warning("official index loader failed: %s", exc)
                return {}
        if self._cache_indices:
            cached = _INDEX_CACHE.get("sets")
            if isinstance(cached, dict) and float(_INDEX_CACHE.get("expires", 0.0)) > self._clock():
                return cached
        sets, complete = await self._fetch_index_sets()
        if self._cache_indices:
            ttl = self.index_ttl_s if complete else self.index_retry_s
            _INDEX_CACHE["sets"] = sets
            _INDEX_CACHE["expires"] = self._clock() + ttl
        return sets

    async def _fetch_index_sets(self) -> tuple[dict[str, frozenset[str]], bool]:
        """Fetch and parse all six official files; one failure is not fatal."""

        semaphore = asyncio.Semaphore(self.concurrency)
        found: dict[str, frozenset[str]] = {}
        state = {"complete": True}

        try:
            client = self._client()
        except Exception as exc:  # noqa: BLE001 - transport construction is optional
            logger.warning("official index transport failed: %s", exc)
            return {}, False

        async with client:

            async def one(spec: _IndexSpec) -> None:
                async with semaphore:
                    try:
                        response = await client.get(spec.url)
                        code = int(getattr(response, "status_code", 0) or 0)
                        if code != 200:
                            state["complete"] = False
                            logger.warning(
                                "official index %s: HTTP %s from %s", spec.name, code, spec.url
                            )
                            return
                        if spec.kind == "xlsx":
                            members = parse_ssga_holdings(
                                getattr(response, "content", b"") or b"", spec.name
                            )
                        elif spec.kind == "csv":
                            members = parse_ishares_holdings(
                                getattr(response, "content", b"") or b"", spec.name
                            )
                        else:
                            members = parse_nasdaq_ndx(response.json())
                        if not members:
                            # An empty official list is a publication problem,
                            # not "this index has no members": do not cache it
                            # for a day, and do not label anything with it.
                            state["complete"] = False
                            logger.warning("official index %s parsed empty", spec.name)
                            return
                        found[spec.name] = frozenset(members)
                    except Exception as exc:  # noqa: BLE001 - one index optional
                        state["complete"] = False
                        logger.warning("official index %s failed: %s", spec.name, exc)

            await asyncio.gather(*(one(spec) for spec in INDEX_SPECS))
        return found, bool(state["complete"])

    # -- optionable -------------------------------------------------------

    async def _probe_optionable(self, client: Any, symbol: str) -> bool | None:
        if self.optionable_delay_s:
            await asyncio.sleep(self.optionable_delay_s)
        response = await client.head(_cboe_url(symbol))
        code = int(getattr(response, "status_code", 0) or 0)
        if code in {429, 500, 502, 503, 504}:
            # Cboe rate-limits bursty HEADs; one paced retry is enough to
            # distinguish a transient throttle from an underlying with no list.
            await asyncio.sleep(max(self.optionable_delay_s, 0.5))
            response = await client.head(_cboe_url(symbol))
            code = int(getattr(response, "status_code", 0) or 0)
        verdict = parse_optionable_status(code)
        if verdict is None and code in NO_OPTIONS_STATUSES:
            return False
        return verdict

    async def _optionable_values(self, symbols: Sequence[str]) -> dict[str, bool]:
        values: dict[str, bool] = {}
        now = self._clock()
        pending: list[str] = []
        for symbol in symbols:
            cached = self._optionable_cache.get(symbol)
            if cached is not None and cached[0] > now:
                values[symbol] = cached[1]
            else:
                pending.append(symbol)
        if not pending:
            return values

        semaphore = asyncio.Semaphore(self.concurrency)
        expires = now + self.optionable_ttl_s

        def record(symbol: str, verdict: Any) -> None:
            resolved = _as_bool(verdict)
            if resolved is None:
                # Unknown is not a verdict: leave the symbol out and let the
                # next cycle ask again rather than caching a hole for six hours.
                self._optionable_cache.pop(symbol, None)
                return
            self._optionable_cache[symbol] = (expires, resolved)
            values[symbol] = resolved

        async def one(symbol: str) -> None:
            async with semaphore:
                try:
                    if self._optionable_checker is not None:
                        record(symbol, await _resolve(self._optionable_checker(symbol)))
                    else:
                        record(symbol, await self._probe_optionable(client, symbol))
                except Exception as exc:  # noqa: BLE001 - one symbol is optional
                    logger.info("official optionable failed for %s: %s", symbol, exc)

        if self._optionable_checker is not None:
            await asyncio.gather(*(one(symbol) for symbol in pending))
            return values

        try:
            client = self._client()
        except Exception as exc:  # noqa: BLE001 - transport construction is optional
            logger.warning("official optionable transport failed: %s", exc)
            return values

        async with client:
            await asyncio.gather(*(one(symbol) for symbol in pending))
        return values

    # -- depth source protocol -------------------------------------------

    async def fetch_many(
        self, symbols: Sequence[str]
    ) -> dict[str, DepthObservation]:
        ordered: list[str] = []
        seen: set[str] = set()
        for raw in symbols:
            symbol = _canonical_class_symbol(raw)
            if symbol and symbol not in seen:
                seen.add(symbol)
                ordered.append(symbol)
        if not ordered:
            return {}

        indices = await self._index_sets()
        optionable = (
            await self._optionable_values(ordered)
            if self._optionable_enabled
            else {}
        )
        as_of = datetime.now(UTC)

        out: dict[str, DepthObservation] = {}
        for symbol in ordered:
            fields: dict[str, Any] = {}
            labels = sorted(name for name, members in indices.items() if symbol in members)
            if labels:
                fields["index_membership"] = ", ".join(labels)
            verdict = optionable.get(symbol)
            if verdict is not None:
                fields["optionable"] = "Yes" if verdict else "No"
            if fields:
                out[symbol] = DepthObservation(
                    symbol=symbol,
                    fields=fields,
                    as_of=as_of,
                    source=self.name,
                )
        return out


__all__ = [
    "CBOE_OPTION_URL",
    "CSV_INDEX_FILES",
    "DEFAULT_CONCURRENCY",
    "INDEX_FILES",
    "INDEX_RETRY_S",
    "INDEX_SPECS",
    "INDEX_TTL_S",
    "IWM_HOLDINGS_URL",
    "NDX_URL",
    "NO_OPTIONS_STATUSES",
    "OPTIONABLE_TTL_S",
    "OfficialDepthSource",
    "RUSSELL_2000_LABEL",
    "SSGA_HOLDINGS_URL",
    "clear_caches",
    "parse_ishares_holdings",
    "parse_nasdaq_ndx",
    "parse_optionable_status",
    "parse_ssga_holdings",
]
