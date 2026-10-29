"""Bulk quote adapters for the QuoteHub.

Yahoo is reused from :mod:`app.market.sources` (batch 400). CNBC is the
premium loop's source: one request can carry up to 500 symbols, its timestamp
has millisecond precision, and it publishes the previous close. Its endpoint is
an undocumented website backend, so this module is deliberately small: it
parses only the fields the hub needs, never retries inside a cycle, and never
turns a missing price into ``0``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from itertools import batched
from typing import Any

from app.market.quote import SourceQuote
from app.market.sources import QuoteSourceError, clean_symbols
from app.providers import http as provider_http

CNBC_HOST = "https://quote.cnbc.com"
CNBC_QUOTE_PATH = "/quote-html-webservice/restQuote/symbolType/symbol"
CNBC_MAX_BATCH = 500
CNBC_USER_AGENT = "Ztockz/0.1 (quote-hub; contact: mafazapratista@smkn2-solo.net)"

#: CNBC prints ``"2026-10-27T14:49:51.640-0400"``.
_CNBC_STAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})$"
)
#: CNBC also prints a bare market date, ``"2026-10-28"``, with the zone only
#: in the companion ``last_timedate`` field.
_CNBC_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: …and that companion, ``"10/28/26 EDT"``: month/day/two-digit-year + zone.
_CNBC_TIMEDATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{2})\s+([A-Z]{3})$")

#: The zones CNBC names in ``last_timedate``; US market data only.
_CNBC_ZONE_OFFSETS = {"EDT": -4, "EST": -5, "CDT": -5, "CST": -6, "PDT": -7, "PST": -8}

_NA = frozenset({"", "N/A", "NA", "--", "-", "NONE", "NULL"})


def _number(value: Any) -> float | None:
    """Parse ``"1,281.20"``/``"+0.51%"``/``"$218.555"``; unknown → ``None``."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = (
            value.strip()
            .replace("$", "")
            .replace(",", "")
            .replace("%", "")
            .replace("+", "")
        )
        if text.upper() in _NA:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        return None
    return number if math.isfinite(number) else None


def _positive(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _non_negative(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and number >= 0 else None


def _stamp(value: Any, timedate: Any = None) -> datetime | None:
    """Read CNBC's stamp in any of the three shapes it prints.

    The full ISO-8601 form carries the time of day and the offset. The bare
    ``"2026-10-28"`` form carries only the market date, so the zone is taken
    from the companion ``last_timedate`` (``"10/28/26 EDT"``) when it is
    there; without either the date is read as UTC midnight, which is honest
    about what is known — a market date, not an intraday instant.
    """

    text = str(value or "").strip()
    if text and _CNBC_STAMP_RE.match(text):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(UTC)

    if text and _CNBC_DATE_RE.match(text):
        try:
            day = date.fromisoformat(text)
        except ValueError:
            return None
        offset = _zone_offset(timedate)
        moment = datetime(day.year, day.month, day.day, tzinfo=UTC)
        if offset is None:
            return moment
        return (moment - timedelta(hours=offset)).astimezone(UTC)

    companion = _zone_offset(timedate)
    if companion is None:
        return None
    match = _CNBC_TIMEDATE_RE.match(str(timedate or "").strip())
    if match is None:
        return None
    month, day, year = (int(part) for part in match.groups()[:3])
    try:
        moment = datetime(2000 + year, month, day, tzinfo=UTC)
    except ValueError:
        return None
    return (moment - timedelta(hours=companion)).astimezone(UTC)


def _zone_offset(timedate: Any) -> int | None:
    """The UTC offset hours CNBC's ``last_timedate`` names, or ``None``."""

    match = _CNBC_TIMEDATE_RE.match(str(timedate or "").strip())
    if match is None:
        return None
    return _CNBC_ZONE_OFFSETS.get(match.group(4))


def _truth(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def parse_cnbc_quote(payload: Any) -> SourceQuote | None:
    """One CNBC row → one quote; missing price/time means ``None``."""

    if not isinstance(payload, Mapping):
        return None
    last = _positive(payload.get("last"))
    stamp = _stamp(payload.get("last_time"), payload.get("last_timedate"))
    if last is None or stamp is None:
        return None
    return SourceQuote(
        last=last,
        ts_exchange=stamp,
        prev_close=_positive(payload.get("previous_day_closing")),
        volume=_non_negative(payload.get("volume")),
        realtime=_truth(payload.get("realTime")),
    )


def parse_cnbc_quotes(
    payload: Any, symbols: Sequence[str]
) -> dict[str, SourceQuote | None]:
    """Map a ``FormattedQuoteResult`` envelope to one answer per requested symbol."""

    root = payload.get("FormattedQuoteResult") if isinstance(payload, Mapping) else None
    if not isinstance(root, Mapping):
        raise QuoteSourceError("cnbc body has no FormattedQuoteResult envelope")
    rows = root.get("FormattedQuote")
    if rows is None:
        rows = []
    if isinstance(rows, Mapping):
        rows = [rows]
    if not isinstance(rows, list):
        raise QuoteSourceError("cnbc FormattedQuote is not a list")

    by_symbol: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        name = str(row.get("symbol") or "").strip().upper()
        if name:
            by_symbol[name] = row

    answers: dict[str, SourceQuote | None] = {}
    for raw in symbols:
        key = str(raw).strip().upper()
        row = by_symbol.get(key)
        answers[key] = parse_cnbc_quote(row) if row is not None else None
    return answers


class CnbcBatchSource:
    """Batch quotes from CNBC's undocumented ``restQuote`` backend."""

    name = "cnbc"

    def __init__(
        self,
        *,
        batch_size: int = CNBC_MAX_BATCH,
        timeout_s: float = 10.0,
        user_agent: str = CNBC_USER_AGENT,
    ) -> None:
        if isinstance(batch_size, bool) or not isinstance(batch_size, int):
            raise TypeError(f"batch_size must be an int, got {batch_size!r}")
        if not 1 <= batch_size <= CNBC_MAX_BATCH:
            raise ValueError(
                f"batch_size must be 1..{CNBC_MAX_BATCH}, got {batch_size!r}"
            )
        if not timeout_s > 0:
            raise ValueError(f"timeout_s must be > 0, got {timeout_s!r}")
        if not user_agent or not str(user_agent).strip():
            raise ValueError("user_agent must be a non-empty string")
        self.batch_size = batch_size
        self.timeout_s = float(timeout_s)
        self.user_agent = str(user_agent)

    def headers(self) -> dict[str, str]:
        return {
            "user-agent": self.user_agent,
            "accept": "application/json,text/plain,*/*",
            "accept-language": "en-US,en;q=0.9",
            "referer": "https://www.cnbc.com/",
        }

    def url(self, symbols: Sequence[str]) -> str:
        joined = "|".join(symbols)
        return (
            f"{CNBC_HOST}{CNBC_QUOTE_PATH}?symbols={joined}"
            "&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1"
            "&output=json&events=1"
        )

    async def _fetch_chunk(
        self, symbols: Sequence[str]
    ) -> dict[str, SourceQuote | None]:
        # A fresh client per request is intentional: the live bench measured
        # p50 67 ms for a new connection versus 463 ms on a warm keep-alive.
        async with provider_http.build_client(
            timeout_s=self.timeout_s, headers=self.headers(), http2=False
        ) as client:
            response = await client.get(self.url(symbols))
            status = int(getattr(response, "status_code", 0) or 0)
            if status != 200:
                raise QuoteSourceError(f"cnbc answered {status}", status=status)
            try:
                payload = response.json()
            except ValueError as exc:
                raise QuoteSourceError(f"cnbc body is not JSON: {exc}") from exc
        return parse_cnbc_quotes(payload, symbols)

    async def fetch_many(
        self, symbols: Sequence[str]
    ) -> dict[str, SourceQuote | None]:
        """One answer per symbol, in chunks of at most ``batch_size``."""

        wanted = clean_symbols(symbols)
        if not wanted:
            return {}
        answers: dict[str, SourceQuote | None] = {}
        failures: list[QuoteSourceError] = []
        for chunk in batched(wanted, self.batch_size):
            try:
                answers.update(await self._fetch_chunk(chunk))
            except QuoteSourceError as exc:
                failures.append(exc)
        if failures and not answers:
            first = failures[0]
            raise QuoteSourceError(
                f"cnbc failed for {len(wanted)} symbol(s) in {len(failures)} "
                f"request(s): {first}",
                status=first.status,
            ) from first
        return {key: answers.get(key) for key in wanted}


__all__ = [
    "CNBC_HOST",
    "CNBC_MAX_BATCH",
    "CnbcBatchSource",
    "parse_cnbc_quote",
    "parse_cnbc_quotes",
]
