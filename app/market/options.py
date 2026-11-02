"""Option chains from CBOE delayed quotes, cached to disk.

CBOE's ``delayed_quotes`` feed is the primary (and only) source: it publishes
the full chain with exchange-computed greeks (delta, gamma, theta, vega, rho),
implied volatility, volume, and open interest. The feed is fifteen minutes
behind the tape — stated in every 200 as ``delayed_minutes: 15``.

**The OCC code is the only source of identity.** CBOE's ``option`` field is the
21-character symbol — ``AAPL261104C00275000`` — whose fixed fifteen-character
tail carries the expiry (``YYMMDD``), the right (``C``/``P``) and the strike in
thousandths of a dollar. A code that does not parse is skipped.

**The cache is the fallback.** A chain lives for five minutes in
``<options dir>/<SYMBOL>.json``. A fresh copy is served without a request. An
expired copy is served when CBOE refuses and nothing else is available.

**Dates are the chain's own.** ``as_of`` is the timestamp the source published.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("mee.market.options")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: CBOE's delayed feed is fifteen minutes behind the tape, by contract.
DELAYED_MINUTES = 15

#: The chain's refresh window in seconds.
CHAIN_TTL_S = 5 * 60.0

#: The documented CBOE delayed quotes endpoint.
CBOE_CHAIN_URL = (
    "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"
)

#: CBOE request timeout.
CBOE_TIMEOUT_S = 10.0

CALL = "call"
PUT = "put"

#: Where the cache lives. The explicit override exists for tests; otherwise
#: ztockz-data/options/ beside the depth store.
CACHE_DIR_ENV = "MEE_OPTIONS_CACHE_DIR"

#: A symbol that can safely become a filename.
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.]{0,23}$")

#: The fixed tail of an OCC symbol: YYMMDD (6) + right (1) + strike (8).
_OCC_TAIL = 15


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Scalar readers
# ---------------------------------------------------------------------------


def _num(value: Any) -> float | None:
    """A finite float, or ``None`` for anything that is not a number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Mapping):
        value = value.get("raw")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    import math
    return number if math.isfinite(number) else None


def _int(value: Any) -> int | None:
    """A count, or ``None``."""
    number = _num(value)
    return None if number is None else int(number)


def _positive(value: Any) -> float | None:
    """A price that exists: a printed zero is \"no trade\", not a free option."""
    number = _num(value)
    return number if number is not None and number > 0 else None


def _volatility(value: Any) -> float | None:
    """An implied volatility as a fraction, or ``None`` when the source prints 0."""
    number = _num(value)
    return number if number is not None and number > 0 else None


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    number = round(float(value), digits)
    return 0.0 if number == 0 else number


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _safe_symbol(raw: Any) -> str:
    """The provider spelling when it is filename-safe, else ``\"\"``."""
    key = str(raw or "").strip().upper()
    return key if _SYMBOL_RE.match(key) else ""


# ---------------------------------------------------------------------------
# The OCC code
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OccQuote:
    """The three facts an OCC symbol carries, and the root it belongs to."""

    root: str
    expiry: date
    right: str
    strike: float


def parse_occ(code: Any) -> OccQuote:
    """``AAPL261104C00275000`` → root, expiry, right, strike.

    The tail is fixed width and read from the end. A strike is thousandths of a
    dollar (``00275000`` → 275.000).
    """
    text = str(code or "").strip().upper()
    if len(text) <= _OCC_TAIL:
        raise ValueError(f"not an OCC symbol: {code!r}")
    tail = text[-_OCC_TAIL:]
    root = text[:-_OCC_TAIL]
    date_text, right_letter, strike_text = tail[:6], tail[6], tail[7:]
    if not root.strip():
        raise ValueError(f"OCC symbol has no root: {code!r}")
    if right_letter not in {"C", "P"}:
        raise ValueError(f"OCC symbol has no right: {code!r}")
    if not date_text.isdigit() or not strike_text.isdigit():
        raise ValueError(f"OCC symbol has a non-numeric tail: {code!r}")
    year = 2000 + int(date_text[:2])
    month = int(date_text[2:4])
    day = int(date_text[4:6])
    strike = int(strike_text) / 1000.0
    if strike <= 0:
        raise ValueError(f"OCC symbol has a zero strike: {code!r}")
    try:
        expiry = date(year, month, day)
    except ValueError as exc:
        raise ValueError(f"OCC symbol has no such expiry: {code!r}") from exc
    return OccQuote(
        root=root,
        expiry=expiry,
        right=CALL if right_letter == "C" else PUT,
        strike=strike,
    )


# ---------------------------------------------------------------------------
# The chain's shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Contract:
    """One listed contract: a strike, a right, and whatever the source knows."""

    strike: float
    right: str
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    iv: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    rho: float | None = None

    def payload(self) -> dict[str, Any]:
        """The cached form: every published number."""
        return {
            "strike": self.strike,
            "right": self.right,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "volume": self.volume,
            "open_interest": self.open_interest,
            "iv": self.iv,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "vega": self.vega,
            "rho": self.rho,
        }

    def document(self) -> dict[str, Any]:
        """The response form: strike, type, premium, greeks, volume, OI, IV."""
        premium = self.last
        if premium is None:
            if self.bid is not None and self.ask is not None:
                premium = (self.bid + self.ask) / 2.0
            elif self.bid is not None:
                premium = self.bid
            elif self.ask is not None:
                premium = self.ask
        return {
            "strike": self.strike,
            "type": self.right,
            "premium": _round(premium),
            "delta": _round(self.delta),
            "gamma": _round(self.gamma),
            "theta": _round(self.theta),
            "vega": _round(self.vega),
            "rho": _round(self.rho),
            "volume": self.volume,
            "open_interest": self.open_interest,
            "implied_volatility": _round(self.iv),
        }


@dataclass(frozen=True, slots=True)
class ExpiryGroup:
    """Every listed contract for one expiration date."""

    expiry: date
    contracts: tuple[Contract, ...]


@dataclass(frozen=True, slots=True)
class Chain:
    """One underlying's whole listed ladder, as one source published it."""

    symbol: str
    as_of: datetime
    source: str
    underlying_price: float | None
    expiries: tuple[ExpiryGroup, ...]


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class ChainError(Exception):
    """The chain cannot be published."""


class UnknownSymbol(ChainError):
    """No chain exists for this symbol."""


class ChainUnavailable(ChainError):
    """The provider refused and nothing usable is cached."""


# ---------------------------------------------------------------------------
# Fetch outcomes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FetchOutcome:
    """What one source had to say: a chain, \"no such symbol\", or \"not now\"."""

    chain: Chain | None = None
    unknown: bool = False
    rate_limited: bool = False
    empty: bool = False


# ---------------------------------------------------------------------------
# CBOE parsing
# ---------------------------------------------------------------------------


def _cboe_stamp(raw: Any) -> datetime:
    """``2026-11-02 11:01:17`` read as UTC."""
    text = _text(raw)
    if text:
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            pass
    return _utcnow()


def _cboe_contract(occ: OccQuote, row: Mapping[str, Any]) -> Contract:
    return Contract(
        strike=occ.strike,
        right=occ.right,
        bid=_num(row.get("bid")),
        ask=_num(row.get("ask")),
        last=_positive(row.get("last_trade_price")),
        volume=_int(row.get("volume")),
        open_interest=_int(row.get("open_interest")),
        iv=_volatility(row.get("iv")),
        delta=_num(row.get("delta")),
        gamma=_num(row.get("gamma")),
        theta=_num(row.get("theta")),
        vega=_num(row.get("vega")),
        rho=_num(row.get("rho")),
    )


def parse_cboe(symbol: Any, payload: Any) -> Chain | None:
    """CBOE's ``data.options[]`` as one chain, or ``None`` when it carries none."""
    key = _safe_symbol(symbol)
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not key or not isinstance(data, Mapping):
        return None
    rows = data.get("options")
    if not isinstance(rows, list) or not rows:
        return None

    groups: dict[date, list[Contract]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        try:
            occ = parse_occ(row.get("option"))
        except ValueError:
            continue
        contract = _cboe_contract(occ, row)
        groups.setdefault(occ.expiry, []).append(contract)
    if not groups:
        return None

    def _order(contract: Contract) -> tuple[float, int]:
        return (contract.strike, 0 if contract.right == CALL else 1)

    timestamp = payload.get("timestamp")
    return Chain(
        symbol=key,
        as_of=_cboe_stamp(timestamp),
        source="cboe",
        underlying_price=_positive(data.get("current_price"))
        or _positive(data.get("close")),
        expiries=tuple(
            ExpiryGroup(
                expiry=expiry,
                contracts=tuple(sorted(groups[expiry], key=_order)),
            )
            for expiry in sorted(groups)
        ),
    )


# ---------------------------------------------------------------------------
# The document: what the route returns
# ---------------------------------------------------------------------------


def chain_document(
    chain: Chain,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    """The response document: nearest expiry's strikes, all expiries listed.

    Every 200 carries ``delayed_minutes: 15`` and ``source``.
    ``selected_expiry`` is the nearest date with contracts; ``expiry_dates``
    lists every date with at least one contract. ``strikes`` is the flat list
    of all contracts for the selected expiry.
    """
    today = chain.as_of.date()
    active = sorted(
        (g for g in chain.expiries if g.expiry >= today),
        key=lambda g: g.expiry,
    )
    expiry_dates = [g.expiry.isoformat() for g in active]

    selected_expiry: str | None = None
    strikes: list[dict[str, Any]] = []
    if active:
        selected_expiry = active[0].expiry.isoformat()
        strikes = [c.document() for c in active[0].contracts]

    return {
        "symbol": chain.symbol,
        "as_of": chain.as_of.isoformat(),
        "source": source or chain.source,
        "delayed_minutes": DELAYED_MINUTES,
        "expiry_dates": expiry_dates,
        "selected_expiry": selected_expiry,
        "strikes": strikes,
    }


# ---------------------------------------------------------------------------
# The disk cache
# ---------------------------------------------------------------------------


def _options_dir() -> Path:
    """The chain directory: explicit override, else ztockz-data/options/."""
    configured = os.environ.get(CACHE_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    store = os.environ.get("MEE_DEPTH_STORE_PATH", "").strip()
    if store:
        parent = Path(store).expanduser().parent
        if str(parent) not in ("", "."):
            return parent / "options"
    from app.engine.paths import data_dir
    return data_dir() / "options"


def _cache_path(symbol: Any, directory: Path | None = None) -> Path | None:
    """``<options dir>/<SYMBOL>.json``, or ``None`` for an unsafe symbol."""
    key = _safe_symbol(symbol)
    if not key:
        return None
    return (directory or _options_dir()) / f"{key}.json"


def _stamp(value: Any) -> datetime | None:
    """A timezone-aware ISO stamp, or ``None``."""
    text = _text(value)
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        return None
    return moment.astimezone(UTC)


def _day(value: Any) -> date | None:
    text = _text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _cached_contract(row: Mapping[str, Any]) -> Contract | None:
    strike = _num(row.get("strike"))
    right = _text(row.get("right"))
    if strike is None or strike <= 0 or right not in (CALL, PUT):
        return None
    return Contract(
        strike=strike,
        right=right,
        bid=_num(row.get("bid")),
        ask=_num(row.get("ask")),
        last=_positive(row.get("last")),
        volume=_int(row.get("volume")),
        open_interest=_int(row.get("open_interest")),
        iv=_volatility(row.get("iv")),
        delta=_num(row.get("delta")),
        gamma=_num(row.get("gamma")),
        theta=_num(row.get("theta")),
        vega=_num(row.get("vega")),
        rho=_num(row.get("rho")),
    )


def read_cache(path: Path) -> Chain | None:
    """A valid cached chain, or ``None`` for absent/corrupt/undated files."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, Mapping):
        return None
    fetched_at = _stamp(raw.get("fetched_at"))
    payload = raw.get("payload")
    symbol = _safe_symbol(raw.get("symbol"))
    if fetched_at is None or not isinstance(payload, Mapping) or not symbol:
        return None

    as_of = _stamp(payload.get("as_of"))
    if as_of is None:
        return None

    # Use the grouped format (preserves expiry dates). The flat contracts
    # list is stored alongside but loses date grouping, so try groups first.
    groups_raw = payload.get("expiries")
    if isinstance(groups_raw, list):
        groups: list[ExpiryGroup] = []
        for group in groups_raw:
            if not isinstance(group, Mapping):
                continue
            expiry = _day(group.get("expiry"))
            rows_g = group.get("contracts")
            if expiry is None or not isinstance(rows_g, list):
                continue
            contracts = tuple(
                c for c in (
                    _cached_contract(r) for r in rows_g if isinstance(r, Mapping)
                ) if c is not None
            )
            if contracts:
                groups.append(ExpiryGroup(expiry=expiry, contracts=contracts))
        if groups:
            return Chain(
                symbol=symbol,
                as_of=as_of,
                source=_text(raw.get("source")) or "cboe",
                underlying_price=_positive(payload.get("underlying_price")),
                expiries=tuple(sorted(groups, key=lambda g: g.expiry)),
            )

    # Fallback: flat contracts list (no expiry grouping — use as_of date)
    rows = payload.get("contracts")
    if isinstance(rows, list):
        contracts = tuple(
            c for c in (
                _cached_contract(r) for r in rows if isinstance(r, Mapping)
            ) if c is not None
        )
        if contracts:
            return Chain(
                symbol=symbol,
                as_of=as_of,
                source=_text(raw.get("source")) or "cboe",
                underlying_price=_positive(payload.get("underlying_price")),
                expiries=(ExpiryGroup(expiry=as_of.date(), contracts=contracts),),
            )

    return None


def write_cache(
    path: Path, chain: Chain, fetched_at: datetime, *, source: str = ""
) -> None:
    """Write the chain and its stamp atomically; a half file is never served."""
    # Flatten all contracts across all expiries for cache
    all_contracts: list[dict[str, Any]] = []
    for group in chain.expiries:
        all_contracts.extend(c.payload() for c in group.contracts)

    body = json.dumps(
        {
            "symbol": chain.symbol,
            "fetched_at": fetched_at.isoformat(),
            "source": source or chain.source,
            "payload": {
                "as_of": chain.as_of.isoformat(),
                "underlying_price": chain.underlying_price,
                "contracts": all_contracts,
                # Also store as grouped for legacy compatibility
                "expiries": [
                    {
                        "expiry": group.expiry.isoformat(),
                        "contracts": [c.payload() for c in group.contracts],
                    }
                    for group in chain.expiries
                ],
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    import os
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _fresh(fetched_at: datetime, now: datetime, ttl_s: float) -> bool:
    return (now - fetched_at).total_seconds() <= ttl_s


def _usable(cached: Chain | None, now: datetime) -> bool:
    """A cached chain still describes something tradeable *today*."""
    if cached is None:
        return False
    return any(g.expiry >= now.date() for g in cached.expiries)


# ---------------------------------------------------------------------------
# The collector: cache → CBOE → error
# ---------------------------------------------------------------------------


async def collect_options(
    symbol: Any,
    *,
    cboe: Any = None,
    now: datetime | None = None,
    ttl_s: float = CHAIN_TTL_S,
    directory: Path | None = None,
    **__: Any,  # Accept but ignore old params (expiries, strikes, etc.)
) -> dict[str, Any]:
    """The option ladder for one symbol, from the cache or CBOE.

    ``cboe`` is the seam: any object with an async ``fetch(symbol) -> FetchOutcome``.
    Production leaves it out and uses the CBOE endpoint; a test injects a canned answer.
    """
    key = _safe_symbol(symbol)
    if not key:
        raise UnknownSymbol(f"{symbol!r} is not a symbol with an option chain")

    moment = now or _utcnow()
    path = _cache_path(key, directory)

    # 1. Fresh cache hit — no request
    cached = read_cache(path) if path is not None else None
    cached_fresh = False
    if cached is not None and path is not None and path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            cached_fetched_at = _stamp(raw.get("fetched_at"))
            if cached_fetched_at is not None and _fresh(cached_fetched_at, moment, ttl_s):
                return chain_document(cached, source="cache")
        except (OSError, ValueError):
            pass

    # 2. CBOE
    if cboe is not None:
        try:
            outcome = await cboe.fetch(key)
        except Exception as exc:
            logger.info("CBOE fetch for %s failed: %s", key, exc)
            outcome = FetchOutcome()
    else:
        try:
            outcome = await _fetch_cboe(key)
        except Exception as exc:
            logger.info("CBOE fetch for %s failed: %s", key, exc)
            outcome = FetchOutcome()

    if outcome.chain is not None:
        live_chain: Chain = outcome.chain
        if path is not None:
            try:
                write_cache(path, live_chain, moment, source="cboe")
            except OSError as exc:
                logger.warning("cache write for %s failed: %s", key, exc)
        return chain_document(live_chain, source="cboe")
    elif outcome.unknown:
        # Try stale cache before giving up
        if cached is not None and _usable(cached, moment):
            return chain_document(cached, source="cache")
        raise UnknownSymbol(f"no listed option chain exists for {key!r}")
    elif outcome.empty:
        # Symbol has a chain but no contracts: honest empty 200
        return {
            "symbol": key,
            "as_of": moment.isoformat(),
            "source": "cboe",
            "delayed_minutes": DELAYED_MINUTES,
            "expiry_dates": [],
            "selected_expiry": None,
            "strikes": [],
        }
    else:
        # Provider refused — serve stale cache if usable
        if cached is not None and _usable(cached, moment):
            return chain_document(cached, source="cache")
        raise ChainUnavailable(f"no option chain source answered for {key!r}")


async def _fetch_cboe(symbol: str) -> FetchOutcome:
    """Fetch the CBOE delayed quotes chain via the shared HTTP seam."""
    from app.providers import http as provider_http

    url = CBOE_CHAIN_URL.format(symbol=symbol.replace("-", "."))
    headers = {
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "accept": "application/json,text/plain,*/*",
    }
    try:
        client = provider_http.build_client(timeout_s=CBOE_TIMEOUT_S, headers=headers)
        async with client as c:
            response = await c.get(url)
            status = int(getattr(response, "status_code", 0) or 0)
            if status == 200:
                body = response.json()
                chain = parse_cboe(symbol, body)
                if chain is not None:
                    return FetchOutcome(chain=chain)
                return FetchOutcome(unknown=True)
            if status in (403, 404):
                return FetchOutcome(unknown=True)
            logger.info("CBOE chain for %s answered %s", symbol, status)
            return FetchOutcome()
    except Exception as exc:
        logger.info("CBOE chain for %s failed: %s", symbol, exc)
        return FetchOutcome()


__all__ = [
    "CALL",
    "PUT",
    "CACHE_DIR_ENV",
    "CBOE_CHAIN_URL",
    "CBOE_TIMEOUT_S",
    "CHAIN_TTL_S",
    "DELAYED_MINUTES",
    "Chain",
    "ChainError",
    "ChainUnavailable",
    "Contract",
    "FetchOutcome",
    "OccQuote",
    "UnknownSymbol",
    "ExpiryGroup",
    "chain_document",
    "collect_options",
    "parse_cboe",
    "parse_occ",
    "read_cache",
    "write_cache",
]