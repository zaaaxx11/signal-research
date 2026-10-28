"""FINRA consolidated short interest: three short fields from one settlement.

``finviz-off-nasdaq.md`` §3.1 replaces Finviz's short block with FINRA's
consolidated short interest file: one unauthenticated ``POST`` per 5,000-row page,
the whole market for one settlement date, and real share counts instead of a
rounded display value.

What this module pins
---------------------
* **One settlement date per fetch.** The endpoint without a ``settlementDate``
  filter starts at 2020-04-15 (4,016,870 rows), so every request carries an
  explicit ``compareFilters`` entry. The date is either configured or discovered
  by probing the 15th / last-business-day candidates newest-first, which is the
  free way to learn which settlement FINRA has published.
* **The row is the record.** ``currentShortPositionQuantity`` is already shares,
  so it is published as ``short_interest`` unchanged — never divided by a
  million. ``daysToCoverQuantity`` is *not* ``short_ratio``: the brief measures it
  matching Finviz only 57/98, while ``short_interest / average_volume(3m)``
  matches 97/98. The divisor therefore comes from the caller's reference map;
  without it the ratio is absent, not zero.
* **``short_float`` is percentage points.** ``short_interest / shares_float *
  100`` (2.0, not 0.02); a zero or missing float omits the field.
* **Absence is data.** A symbol FINRA did not publish gets no observation. This
  source never zero-fills a row and never emits a field it did not derive.

The client is injectable at two levels: a ``fetcher`` for unit tests (no HTTP at
all) and a ``client_factory`` for the httpx seam. Production uses
``app.providers.http.build_client`` as a module attribute so the Phase-0 egress
guard and the Phase-2 mock transport keep working.
"""

from __future__ import annotations

import asyncio
import csv
import inspect
import io
import json
import logging
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol, cast

from app.market.depth import DepthObservation, canonical_symbol

logger = logging.getLogger("mee.depth.finra")

#: Verified live 2026-10-28: JSON array, ``record-total`` header, max 5,000 rows.
FINRA_URL = (
    "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
)
PAGE_LIMIT = 5_000
#: The server ignores ``limit=30000`` (brief §3.1); 5,000 is its own ceiling.
MAX_PAGE_LIMIT = 5_000
DEFAULT_TIMEOUT_S = 30.0
#: A settlement file changes twice a month; six hours is short enough to pick up
#: a correction on the next cycle and long enough to keep one fetch per day.
DEFAULT_CACHE_TTL_S = 6 * 60 * 60.0
DEFAULT_MAX_PAGES = 40
DEFAULT_LOOKBACK_MONTHS = 6

#: FINRA answers a Linux Chrome UA with Cloudflare's bot wall on other endpoints;
#: the Windows UA is the one the probes used for a 200 from this API too.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_SYMBOL_KEYS = ("symbolCode", "symbol_code", "symbol")
_SETTLEMENT_KEYS = ("settlementDate", "settlement_date")
_ACCOUNTING_KEYS = ("accountingYearMonthNumber", "accounting_year_month_number")
_CURRENT_KEYS = (
    "currentShortPositionQuantity",
    "current_short_position_quantity",
    "shortInterest",
    "short_interest",
)
_PREVIOUS_KEYS = (
    "previousShortPositionQuantity",
    "previous_short_position_quantity",
    "previousShortInterest",
)
_AVERAGE_VOLUME_KEYS = (
    "averageDailyVolumeQuantity",
    "average_daily_volume_quantity",
    "averageDailyVolume",
)
_DAYS_TO_COVER_KEYS = ("daysToCoverQuantity", "days_to_cover_quantity")
_CHANGE_PERCENT_KEYS = ("changePercent", "change_percent")
_ISSUE_KEYS = ("issueName", "issue_name")
_MARKET_CLASS_KEYS = ("marketClassCode", "market_class_code")

_SENTINELS = frozenset({"", "N/A", "NA", "NONE", "NULL", "--", "-"})
_SEPARATORS = str.maketrans("", "", ".-/")


class FinraError(RuntimeError):
    """Base error for every FINRA failure this module raises."""


class FinraSourceError(FinraError):
    """A request/response failure: the collector counts it and keeps the batch."""


class FinraParseError(FinraError):
    """The body is not a shape the consolidated short-interest file can have."""


@dataclass(frozen=True, slots=True)
class FinraPage:
    """One page of records plus the server's total when it sent one."""

    records: tuple[Mapping[str, Any], ...] = ()
    total: int | None = None


class FinraFetcher(Protocol):
    """Injectable page fetcher; production implements it with the HTTP seam."""

    async def __call__(
        self, *, settlement: str, limit: int, offset: int
    ) -> FinraPage | Sequence[Mapping[str, Any]]: ...


#: Optional reference maps: ``symbol -> average volume`` / ``symbol -> float``.
ReferenceValues = Mapping[str, Any] | Callable[[], Mapping[str, Any]] | None


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_float(value: Any) -> float | None:
    """FINRA prints numbers as JSON numbers; CSV prints ``"1,234,567"``."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip().replace(",", "").replace("$", "")
        if text.upper() in _SENTINELS:
            return None
        try:
            number = float(text)
        except (TypeError, ValueError):
            return None
    if not math.isfinite(number):
        return None
    return number


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    if number is None or number < 0:
        return None
    return int(number)


def _first(record: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name not in record:
            continue
        value = record[name]
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _normalise_date(value: Any) -> str | None:
    """``2026-10-15``, ``20261015`` and date/datetime all become ISO."""

    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not text:
        return None
    digits = text.replace("-", "").replace("/", "")
    if len(digits) == 8 and digits.isdigit():
        try:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:])).isoformat()
        except ValueError:
            return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _settlement_datetime(value: Any) -> datetime | None:
    iso = _normalise_date(value)
    if iso is None:
        return None
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


def _compact(symbol: str) -> str:
    """FINRA writes class shares without a separator: ``BRK.B`` -> ``BRKB``."""

    return symbol.upper().translate(_SEPARATORS)


# ---------------------------------------------------------------------------
# Pure parser
# ---------------------------------------------------------------------------


def parse_consolidated_short_interest(
    payload: Any,
) -> dict[str, dict[str, Any]]:
    """Parse one or more FINRA pages into ``{symbol: row}``.

    Accepts the live JSON array, an envelope with ``records``/``items``/``data``
    and an optional top-level ``settlementDate``, a JSON string, and the CSV the
    endpoint returns when ``Accept`` is left at ``*/*`` (the probe's format).
    A duplicate symbol is a restatement: a later non-null value wins, while a
    null never erases a position the file already reported.
    """

    records, envelope_date = _records_from(payload)
    out: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        symbol = canonical_symbol(_first(record, _SYMBOL_KEYS))
        if not symbol:
            continue
        settlement = _normalise_date(_first(record, _SETTLEMENT_KEYS))
        if settlement is None:
            settlement = _normalise_date(_first(record, _ACCOUNTING_KEYS))
        if settlement is None:
            settlement = envelope_date
        row: dict[str, Any] = {
            "symbol": symbol,
            "issue_name": _text(_first(record, _ISSUE_KEYS)),
            "market_class": _text(_first(record, _MARKET_CLASS_KEYS)),
            "short_interest": _to_int(_first(record, _CURRENT_KEYS)),
            "previous_short_interest": _to_int(_first(record, _PREVIOUS_KEYS)),
            "average_daily_volume": _to_int(_first(record, _AVERAGE_VOLUME_KEYS)),
            "days_to_cover": _to_float(_first(record, _DAYS_TO_COVER_KEYS)),
            "change_pct": _to_float(_first(record, _CHANGE_PERCENT_KEYS)),
            "settlement_date": settlement,
        }
        previous = out.get(symbol)
        out[symbol] = _merge_row(previous, row) if previous is not None else row
    return out


def _merge_row(
    previous: Mapping[str, Any], incoming: Mapping[str, Any]
) -> dict[str, Any]:
    merged = dict(previous)
    for key, value in incoming.items():
        if value is not None:
            merged[key] = value
    return merged


def _records_from(
    payload: Any,
) -> tuple[list[Mapping[str, Any]], str | None]:
    if payload is None:
        return [], None
    if isinstance(payload, (bytes, bytearray)):
        return _records_from(bytes(payload).decode("utf-8-sig", errors="replace"))
    if isinstance(payload, str):
        text = payload.lstrip("\ufeff").strip()
        if not text:
            return [], None
        if text[0] in "[{":
            try:
                return _records_from(json.loads(text))
            except json.JSONDecodeError as exc:
                raise FinraParseError(f"invalid FINRA JSON: {exc}") from exc
        try:
            rows = list(csv.DictReader(io.StringIO(text)))
        except csv.Error as exc:
            raise FinraParseError(f"invalid FINRA CSV: {exc}") from exc
        return cast(list[Mapping[str, Any]], rows), None
    if isinstance(payload, Mapping):
        envelope_date = _normalise_date(_first(payload, _SETTLEMENT_KEYS))
        if any(key in payload for key in _SYMBOL_KEYS):
            return [payload], envelope_date
        for key in ("records", "items", "results", "data"):
            if key not in payload:
                continue
            value = payload[key]
            if isinstance(value, Mapping):
                for inner in ("records", "items", "results"):
                    if isinstance(value.get(inner), (list, tuple)):
                        return list(value[inner]), envelope_date
                continue
            if isinstance(value, (list, tuple)):
                return list(value), envelope_date
        return [], envelope_date
    if isinstance(payload, (list, tuple)):
        return list(payload), None
    raise FinraParseError(f"unsupported FINRA payload: {type(payload).__name__}")


# ---------------------------------------------------------------------------
# Settlement dates
# ---------------------------------------------------------------------------


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _last_weekday(year: int, month: int) -> date:
    day = date(year, month, 1)
    next_month = date(*_shift_month(year, month, 1), 1)
    day = date.fromordinal(next_month.toordinal() - 1)
    while day.weekday() >= 5:
        day = date.fromordinal(day.toordinal() - 1)
    return day


def _weekday_on_or_before(day: date) -> date:
    while day.weekday() >= 5:
        day = date.fromordinal(day.toordinal() - 1)
    return day


def _weekdays_before(day: date, count: int) -> list[date]:
    out = [day]
    cursor = day
    while len(out) < count:
        cursor = date.fromordinal(cursor.toordinal() - 1)
        if cursor.weekday() < 5:
            out.append(cursor)
    return out


def settlement_candidates(
    reference: date | datetime, months: int = DEFAULT_LOOKBACK_MONTHS
) -> tuple[str, ...]:
    """FINRA settles on the 15th and the last business day, newest first.

    The last-business-day candidate carries the two weekdays before it: a
    holiday Friday moves the settlement to Thursday, and the probe (204 means
    "no file for that date") then still finds the published date without a
    holiday calendar.
    """

    if isinstance(reference, datetime):
        reference = reference.date()
    if months < 1:
        raise ValueError("months must be >= 1")
    found: set[str] = set()
    year, month = reference.year, reference.month
    for step in range(months):
        y, m = _shift_month(year, month, -step)
        last = _last_weekday(y, m)
        if last <= reference:
            for candidate in _weekdays_before(last, 3):
                found.add(candidate.isoformat())
        fifteenth = _weekday_on_or_before(date(y, m, 15))
        if fifteenth <= reference:
            found.add(fifteenth.isoformat())
    return tuple(sorted(found, reverse=True))


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------


class FinraShortSource:
    """Depth source for ``short_interest`` / ``short_ratio`` / ``short_float``."""

    name = "finra"

    def __init__(
        self,
        *,
        name: str = "finra",
        settlement_date: str | date | None = None,
        average_volume: ReferenceValues = None,
        shares_float: ReferenceValues = None,
        fetcher: FinraFetcher | None = None,
        client_factory: Callable[..., Any] | None = None,
        page_limit: int = PAGE_LIMIT,
        max_pages: int = DEFAULT_MAX_PAGES,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        cache_ttl_s: float = DEFAULT_CACHE_TTL_S,
        lookback_months: int = DEFAULT_LOOKBACK_MONTHS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("name must be non-empty")
        if page_limit < 1 or page_limit > MAX_PAGE_LIMIT:
            raise ValueError(f"page_limit must be in 1..{MAX_PAGE_LIMIT}")
        if max_pages < 1:
            raise ValueError("max_pages must be >= 1")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        if cache_ttl_s < 0:
            raise ValueError("cache_ttl_s must be >= 0")
        if lookback_months < 1:
            raise ValueError("lookback_months must be >= 1")
        if settlement_date is not None and _normalise_date(settlement_date) is None:
            raise ValueError(f"invalid settlement_date: {settlement_date!r}")
        if fetcher is not None and not callable(fetcher):
            raise TypeError("fetcher must be callable")

        self.name = clean_name
        self.settlement_date = settlement_date
        self.page_limit = int(page_limit)
        self.max_pages = int(max_pages)
        self.timeout_s = float(timeout_s)
        self.cache_ttl_s = float(cache_ttl_s)
        self.lookback_months = int(lookback_months)
        self._average_volume = average_volume
        self._shares_float = shares_float
        self._fetcher = fetcher
        self._client_factory = client_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._rows_cache: dict[
            str, tuple[float, dict[str, dict[str, Any]], dict[str, dict[str, Any]]]
        ] = {}
        self._latest: tuple[str, float] | None = None
        self._rows_lock = asyncio.Lock()
        self._resolve_lock = asyncio.Lock()

    # -- reference values ---------------------------------------------------

    def set_average_volume(self, values: ReferenceValues) -> None:
        """Replace the 3-month average-volume map used for ``short_ratio``."""

        self._average_volume = values

    def set_shares_float(self, values: ReferenceValues) -> None:
        """Replace the float-share map used for ``short_float``."""

        self._shares_float = values

    # -- cache --------------------------------------------------------------

    def clear_cache(self) -> None:
        """Drop parsed settlement files and the discovered latest date."""

        self._rows_cache.clear()
        self._latest = None

    # -- protocol -----------------------------------------------------------

    async def fetch_many(
        self, symbols: Sequence[str]
    ) -> dict[str, DepthObservation]:
        wanted = _unique_symbols(symbols)
        if not wanted:
            return {}
        settlement = await self._settlement()
        rows, index = await self._rows(settlement)
        if not rows:
            return {}
        volumes = self._reference_index(self._average_volume)
        floats = self._reference_index(self._shares_float)
        as_of = _settlement_datetime(settlement) or self._clock()

        out: dict[str, DepthObservation] = {}
        for symbol in wanted:
            row = index.get(symbol) or index.get(_compact(symbol))
            if row is None:
                continue
            short_interest = _to_int(row.get("short_interest"))
            if short_interest is None:
                # A null interest is not a position of zero.
                continue
            fields: dict[str, Any] = {"short_interest": short_interest}
            volume = _lookup_number(volumes, symbol)
            if volume is not None and volume > 0:
                fields["short_ratio"] = short_interest / volume
            shares_float = _lookup_number(floats, symbol)
            if shares_float is not None and shares_float > 0:
                # Percent points, the unit Finviz prints ("Short interest share").
                fields["short_float"] = short_interest / shares_float * 100.0
            out[symbol] = DepthObservation(
                symbol=symbol,
                fields=fields,
                as_of=as_of,
                source=self.name,
            )
        return out

    # -- settlement resolution ----------------------------------------------

    async def _settlement(self) -> str:
        if self.settlement_date is not None:
            configured = _normalise_date(self.settlement_date)
            if configured is None:
                raise FinraSourceError(
                    f"invalid settlement_date: {self.settlement_date!r}"
                )
            return configured
        now = time.monotonic()
        if self._latest is not None and now < self._latest[1]:
            return self._latest[0]
        async with self._resolve_lock:
            now = time.monotonic()
            if self._latest is not None and now < self._latest[1]:
                return self._latest[0]
            reference = self._clock().date()
            for candidate in settlement_candidates(reference, self.lookback_months):
                page = await self._page(candidate, offset=0, limit=1)
                if page.records:
                    self._latest = (candidate, now + self.cache_ttl_s)
                    return candidate
            raise FinraSourceError(
                "FINRA published no consolidated short interest for the last "
                f"{self.lookback_months} settlement dates"
            )

    # -- fetching -----------------------------------------------------------

    async def _rows(
        self, settlement: str
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        cached = self._rows_cache.get(settlement)
        if cached is not None and time.monotonic() < cached[0]:
            return cached[1], cached[2]
        async with self._rows_lock:
            cached = self._rows_cache.get(settlement)
            if cached is not None and time.monotonic() < cached[0]:
                return cached[1], cached[2]
            rows = await self._fetch_rows(settlement)
            index = _row_index(rows)
            self._rows_cache[settlement] = (
                time.monotonic() + self.cache_ttl_s,
                rows,
                index,
            )
            return rows, index

    async def _fetch_rows(self, settlement: str) -> dict[str, dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        offset = 0
        pages = 0
        while True:
            if pages >= self.max_pages:
                raise FinraSourceError(
                    f"FINRA pagination did not terminate after {self.max_pages} pages"
                )
            page = await self._page(settlement, offset=offset, limit=self.page_limit)
            pages += 1
            if not page.records:
                break
            parsed = parse_consolidated_short_interest(list(page.records))
            for symbol, row in parsed.items():
                if not row.get("settlement_date"):
                    row["settlement_date"] = settlement
                previous = rows.get(symbol)
                rows[symbol] = _merge_row(previous, row) if previous else row
            if page.total is not None and offset + self.page_limit >= page.total:
                break
            if len(page.records) < self.page_limit:
                break
            offset += self.page_limit
        return rows

    async def _page(self, settlement: str, *, offset: int, limit: int) -> FinraPage:
        if self._fetcher is not None:
            result = self._fetcher(settlement=settlement, limit=limit, offset=offset)
            if inspect.isawaitable(result):
                result = await result
            return _coerce_page(result)
        return await self._http_page(settlement, offset=offset, limit=limit)

    async def _http_page(
        self, settlement: str, *, offset: int, limit: int
    ) -> FinraPage:
        from app.providers import http as provider_http

        body = {
            "limit": limit,
            "offset": offset,
            "compareFilters": [
                {
                    "compareType": "EQUAL",
                    "fieldName": "settlementDate",
                    "fieldValue": settlement,
                }
            ],
        }
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "user-agent": BROWSER_USER_AGENT,
        }
        factory = self._client_factory or provider_http.build_client
        async with factory(timeout_s=self.timeout_s, headers=headers) as client:
            # Repeat the headers on the request: a test/mock client factory is
            # allowed to ignore client-level defaults, and the Accept header is
            # what makes FINRA answer JSON instead of CSV.
            response = await client.post(FINRA_URL, json=body, headers=headers)

        status = int(getattr(response, "status_code", 0))
        text = getattr(response, "text", "") or ""
        headers_in = getattr(response, "headers", None)
        if status == 204:
            return FinraPage((), total=0)
        if (
            status == 400
            and offset > 0
            and "larger than the number of records" in text.lower()
        ):
            # Defensive stop for a server that omits `record-total`: an offset
            # past the end is the end, not a failed batch.
            return FinraPage((), total=offset)
        if status != 200:
            raise FinraSourceError(
                f"FINRA short interest HTTP {status} at offset {offset}: {text[:200]}"
            )
        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - the body is the diagnosis
            raise FinraSourceError(
                f"FINRA short interest returned non-JSON at offset {offset}: "
                f"{text[:200]}"
            ) from exc
        records, _ = _records_from(payload)
        total = _header_int(headers_in, "record-total")
        if total is None and isinstance(payload, Mapping):
            total = _to_int(payload.get("total") or payload.get("record-total"))
        return FinraPage(tuple(records), total)

    # -- reference maps -----------------------------------------------------

    def _reference_index(self, values: ReferenceValues) -> dict[str, float]:
        try:
            raw = values() if callable(values) else values
        except Exception as exc:  # noqa: BLE001 - a ratio is optional, shares are not
            logger.warning("FINRA reference map failed: %s", exc)
            return {}
        if not isinstance(raw, Mapping):
            return {}
        index: dict[str, float] = {}
        for key, value in raw.items():
            symbol = canonical_symbol(key)
            number = _to_float(value)
            if not symbol or number is None:
                continue
            index.setdefault(symbol, number)
            index.setdefault(_compact(symbol), number)
        return index


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _unique_symbols(symbols: Sequence[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        symbol = canonical_symbol(raw)
        if symbol and symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return out


def _row_index(
    rows: Mapping[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for symbol, row in rows.items():
        index.setdefault(symbol, row)
        index.setdefault(_compact(symbol), row)
    return index


def _lookup_number(index: Mapping[str, float], symbol: str) -> float | None:
    value = index.get(symbol)
    if value is None:
        value = index.get(_compact(symbol))
    return value


def _coerce_page(value: Any) -> FinraPage:
    if isinstance(value, FinraPage):
        return value
    if isinstance(value, Mapping):
        records = None
        for key in ("records", "items", "results", "data"):
            if isinstance(value.get(key), (list, tuple)):
                records = value[key]
                break
        total = _to_int(value.get("total") or value.get("record-total"))
        return FinraPage(tuple(records or ()), total)
    if isinstance(value, (list, tuple)):
        return FinraPage(tuple(value))
    raise FinraSourceError(
        f"FINRA fetcher returned an unsupported page: {type(value).__name__}"
    )


def _header_int(headers: Any, name: str) -> int | None:
    if headers is None:
        return None
    try:
        raw = headers.get(name)
    except AttributeError:
        return None
    return _to_int(raw)


__all__ = [
    "BROWSER_USER_AGENT",
    "FINRA_URL",
    "FinraError",
    "FinraFetcher",
    "FinraPage",
    "FinraParseError",
    "FinraShortSource",
    "FinraSourceError",
    "MAX_PAGE_LIMIT",
    "PAGE_LIMIT",
    "parse_consolidated_short_interest",
    "settlement_candidates",
]
