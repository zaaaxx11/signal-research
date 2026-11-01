"""ETF held-by: a fund's published holdings, vendored per fund, per stamp.

``etf_heldby`` was refused as *Elite-only* and "no fund→constituent table
exists". The second half was wrong: the funds publish their holdings as a daily
file, and this product already parses three of them
(:meth:`app.market.official.parse_ssga_holdings` for the SSGA workbooks,
:func:`app.market.official.parse_ishares_holdings` for iShares' CSV) to derive
index membership. So the table is **collected**, not invented:
``app/screen/data/etf_holdings.json``, written by ``scripts/etf-holdings.py``.

Measured, never guessed
-----------------------
The snapshot records, per fund, the URL it was fetched from, the HTTP status,
the format, the number of tickers kept and when. A fund whose file did not
answer (Invesco serves QQQ behind a bot wall: HTTP 406 on 2026-11-01) keeps its
entry with ``available: false`` and **no members at all** — the token is then a
422 ``unavailable_etf`` that repeats the measured reason. Filling QQQ from the
Nasdaq-100 index list would be an approximation wearing a holding's name, and
``heldby:qqq`` would answer a question nobody asked.

Spellings
---------
A fund writes ``BRK.B`` or ``BRK-B``; the screen writes ``BRK.B``. :func:`_key`
folds ``-`` and space to ``.`` in both directions, exactly like
:mod:`app.screen.index_membership`, so one lookup answers for either. The
snapshot stores what the fund published; the lookup normalises at read time.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

#: The funds the ``heldby`` control offers, in panel order. The first three are
#: the broad-market trackers a US screen is asked about; IWM is here because its
#: holdings file is the one this product already reads for Russell 2000
#: membership, so the same snapshot answers ``heldby:iwm`` at no extra cost.
ETFS: tuple[str, ...] = ("SPY", "QQQ", "DIA", "IWM")

#: Where the vendored snapshot lives (``pyproject`` ships ``data/*.json``).
DATA_PATH = Path(__file__).with_name("data") / "etf_holdings.json"

#: A deployment that keeps its snapshot elsewhere names it here.
PATH_ENV = "ZTOCKZ_ETF_HOLDINGS"

#: The sentence a 422 / the document repeats when there is no snapshot at all.
REASON = (
    "needs the ETF holdings snapshot (app/screen/data/etf_holdings.json): the "
    "file is missing, unstamped or carries no fund, so no constituent list "
    "exists — rebuild it with python scripts/etf-holdings.py"
)

#: The shape of the "plausible" guard: a fund whose parse lost the table must
#: never be published as a 3-name ETF. Wide enough for reconstitution.
PLAUSIBLE: Mapping[str, tuple[int, int]] = MappingProxyType(
    {"SPY": (450, 560), "QQQ": (90, 120), "DIA": (28, 32), "IWM": (1500, 2300)}
)


class UnknownEtf(LookupError):
    """The token names a fund this control does not offer."""

    code = "unknown_bucket"


class UnavailableEtf(RuntimeError):
    """The fund is offered, but its file could not be collected.

    ``reason`` is the **measured** sentence from the snapshot's source block
    (the HTTP status and the URL), so a 422 says what happened instead of "no
    data".
    """

    code = "unavailable_bucket"


@dataclass(frozen=True, slots=True)
class Fund:
    """One fund's entry: its provenance and, when it answered, its members."""

    symbol: str
    label: str
    url: str | None
    status: int | None
    retrieved_at: str | None
    members: frozenset[str]
    error: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.members)

    @property
    def reason(self) -> str | None:
        if self.available:
            return None
        if self.error:
            return (
                f"{self.label} ({self.symbol}) holdings could not be collected: "
                f"{self.error}"
                + (f" [{self.url}]" if self.url else "")
            )
        return (
            f"{self.label} ({self.symbol}) holdings were never collected: the "
            "snapshot carries no members for it"
        )


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One stamped capture: the funds it knows and the ones it could not read."""

    as_of: str | None
    funds: Mapping[str, Fund]

    @property
    def available(self) -> bool:
        return self.as_of is not None and any(fund.available for fund in self.funds.values())

    def counts(self) -> dict[str, int]:
        return {symbol: len(fund.members) for symbol, fund in self.funds.items()}

    def coverage(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "as_of": self.as_of,
            "counts": self.counts(),
            "funds": {
                symbol: {
                    "available": fund.available,
                    "members": len(fund.members),
                    "url": fund.url,
                    "status": fund.status,
                    "retrieved_at": fund.retrieved_at,
                    "reason": fund.reason,
                }
                for symbol, fund in self.funds.items()
            },
            "reason": None if self.available else REASON,
        }


def _key(symbol: object) -> str | None:
    if not isinstance(symbol, str):
        return None
    text = symbol.strip().upper().replace("-", ".").replace(" ", ".")
    return text or None


def parse(document: object) -> Snapshot:
    """One snapshot object → :class:`Snapshot`; malformed → no stamp, no funds."""

    if not isinstance(document, Mapping):
        return Snapshot(as_of=None, funds=MappingProxyType({}))
    raw_funds = document.get("funds")
    funds: dict[str, Fund] = {}
    if isinstance(raw_funds, Mapping):
        for raw_symbol, entry in raw_funds.items():
            symbol = str(raw_symbol).strip().upper()
            if symbol not in ETFS or not isinstance(entry, Mapping):
                continue
            raw_members = entry.get("members")
            members = frozenset(
                key
                for key in (_key(item) for item in raw_members or ())
                if key is not None
            )
            status = entry.get("status")
            funds[symbol] = Fund(
                symbol=symbol,
                label=str(entry.get("label") or symbol),
                url=str(entry["url"]) if entry.get("url") else None,
                status=int(status) if isinstance(status, int) else None,
                retrieved_at=str(entry["retrieved_at"]) if entry.get("retrieved_at") else None,
                members=members,
                error=str(entry["error"]).strip() if entry.get("error") else None,
            )
    as_of = document.get("as_of")
    stamp = as_of.strip() if isinstance(as_of, str) and as_of.strip() else None
    # A snapshot that lists no fund at all is not a snapshot of anything.
    return Snapshot(as_of=stamp if funds else None, funds=MappingProxyType(funds))


_CACHE: dict[str, tuple[int, int, Snapshot]] = {}


def configured_path() -> Path:
    override = os.environ.get(PATH_ENV, "").strip()
    return Path(override) if override else DATA_PATH


def load(path: Path | str | None = None) -> Snapshot:
    """The snapshot, or an unstamped empty one (never an exception at read time)."""

    target = Path(path) if path is not None else configured_path()
    try:
        stat = target.stat()
    except OSError:
        return Snapshot(as_of=None, funds=MappingProxyType({}))
    cached = _CACHE.get(str(target))
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Snapshot(as_of=None, funds=MappingProxyType({}))
    snapshot = parse(document)
    _CACHE[str(target)] = (stat.st_mtime_ns, stat.st_size, snapshot)
    return snapshot


def clear() -> None:
    _CACHE.clear()


def funds() -> Mapping[str, Fund]:
    """Every offered fund in panel order, with the ones the file lacks filled in.

    A fund the snapshot never mentions is still an option in the document — the
    control offers four tokens, not four-minus-whatever-the-last-run-fetched —
    and it is published unavailable with the collector's own sentence.
    """

    snapshot = load()
    ordered: dict[str, Fund] = {}
    for symbol in ETFS:
        found = snapshot.funds.get(symbol)
        ordered[symbol] = (
            found
            if found is not None
            else Fund(
                symbol=symbol,
                label=symbol,
                url=None,
                status=None,
                retrieved_at=None,
                members=frozenset(),
            )
        )
    return MappingProxyType(ordered)


def available() -> bool:
    return load().available


def reason() -> str | None:
    """Why ``etf_heldby`` cannot run at all, or ``None`` when some fund can."""

    return None if load().available else REASON


def members(etf: object) -> frozenset[str]:
    """One fund's constituents, folded — or :class:`UnknownEtf`/:class:`UnavailableEtf`."""

    symbol = str(etf or "").strip().upper()
    if symbol not in ETFS:
        raise UnknownEtf(
            f"unknown ETF {etf!r}; the held-by control offers {', '.join(ETFS)}"
        )
    fund = funds()[symbol]
    if not fund.available:
        raise UnavailableEtf(fund.reason or REASON)
    return fund.members


def coverage() -> dict[str, Any]:
    """The measured block the filter document publishes."""

    snapshot = load()
    block = snapshot.coverage()
    block["funds"] = {
        symbol: {
            "available": fund.available,
            "members": len(fund.members),
            "reason": fund.reason,
        }
        for symbol, fund in funds().items()
    }
    block["counts"] = {symbol: len(fund.members) for symbol, fund in funds().items()}
    block["available"] = any(fund.available for fund in funds().values())
    block["reason"] = None if block["available"] else REASON
    return block


__all__ = [
    "DATA_PATH",
    "ETFS",
    "Fund",
    "PATH_ENV",
    "PLAUSIBLE",
    "REASON",
    "Snapshot",
    "UnavailableEtf",
    "UnknownEtf",
    "available",
    "clear",
    "configured_path",
    "coverage",
    "funds",
    "load",
    "members",
    "parse",
    "reason",
]
