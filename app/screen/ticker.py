"""The ticker page's header bundle and peer group.

Both read the **screener's** universe rows — the output of
:func:`app.screen.universe.build_universe`, one row per issuer from the scan
that ``/screens/select`` already runs. There is no second price path here: a
header is the scan row's own cells, and a peer is a scan row whose market cap
is close to the subject's.

Two rules the code exists to enforce
------------------------------------
* **A value is the row's cell, or ``None``.** No quote is fetched, no RSI is
  recomputed from bars, no market cap is re-derived from shares. A cell the
  scan did not carry is a visible absence, never a zero.
* **A null does not group with another null.** Two companies with no sector
  are not "in the same sector", and two with no industry are not peers. The
  same rule the identity-master peer group uses, applied to the scan's own
  classification.

``prev_close`` is the one arithmetic cell, and it is imported from
:func:`app.screen.fundamentals.derive_view_cells` — the same identity the wide
table already publishes (``price / (1 + change_pct / 100)``), not a second
implementation of it.

Contract: ``.hermes/plans/2026-11-02_ticker-page.md`` (T2, T3).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from app.screen.fundamentals import derive_view_cells

#: The four labels a header's ``index_membership`` speaks, in panel order. The
#: snapshot owns the list; this module only passes it through.
__all__ = ["peer_document", "profile_document"]


# ---------------------------------------------------------------------------
# Header bundle (T3)
# ---------------------------------------------------------------------------


def profile_document(
    row: Mapping[str, Any], *, index_members: Sequence[str] | None = None
) -> dict[str, Any]:
    """One scan row projected onto the ticker page's header.

    ``index_members`` is :func:`app.screen.index_membership.indices_for`'s
    answer: a list when the vendored snapshot was read (possibly empty, meaning
    "in none of the four"), ``None`` when no snapshot exists. The distinction
    is preserved on the wire because an empty list is a membership claim.
    """

    price = _number(row.get("price"))
    change = _number(row.get("change_pct"))
    return {
        "symbol": _text(row.get("symbol")),
        "company": _text(row.get("name")),
        "price": price,
        "change_pct": change,
        "volume": _number(row.get("volume")),
        "market_cap": _number(row.get("market_cap_usd")),
        "prev_close": derive_view_cells(row, None).get("prev_close"),
        "sector": _text(row.get("sector")),
        "industry": _text(row.get("industry")),
        "index_membership": None if index_members is None else list(index_members),
        "high_52w": _number(row.get("high_52w")),
        "low_52w": _number(row.get("low_52w")),
        "beta": _number(row.get("beta_1y")),
        "rsi": _number(row.get("rsi_14")),
    }


# ---------------------------------------------------------------------------
# Peers (T2)
# ---------------------------------------------------------------------------


def peer_document(
    rows: Sequence[Mapping[str, Any]], symbol: str, *, limit: int = 10
) -> dict[str, Any] | None:
    """The subject's sector group, nearest market cap first, or ``None``.

    ``None`` answers "this symbol is not in the universe"; a known symbol with
    no sector and no industry is an honest empty group, not an error. The
    fallback is to the industry when the sector group (the subject excluded)
    has fewer than :data:`SECTOR_MINIMUM` members — a five-company sector fits
    on one screen, so the page asks the broader question instead.

    A peer is "nearest" only when both caps are known: a company with no market
    cap cannot be at a distance from the subject, so it is not ranked. The
    subject having no cap leaves nothing to measure against, and the group is
    empty rather than ordered by a number nobody has.
    """

    subject = _find(rows, symbol)
    if subject is None:
        return None

    sector = _text(subject.get("sector"))
    industry = _text(subject.get("industry"))
    others = [
        row
        for row in rows
        if _text(row.get("symbol")) is not None
        and _text(row.get("symbol")) != _text(subject.get("symbol"))
    ]

    sector_group = [row for row in others if sector is not None and _same(row.get("sector"), sector)]
    if len(sector_group) >= SECTOR_MINIMUM or industry is None:
        basis = "sector" if sector is not None else None
        group = sector_group if sector is not None else []
    else:
        basis = "industry"
        group = [row for row in others if _same(row.get("industry"), industry)]

    cap = _number(subject.get("market_cap_usd"))
    peers = _nearest(group, cap, limit) if cap is not None else []
    return {
        "symbol": _text(subject.get("symbol")),
        "sector": sector,
        "industry": industry,
        "basis": basis,
        "count": len(peers),
        "peers": [_peer(row) for row in peers],
    }


#: A sector group smaller than this asks the industry question instead. Eight
#: is the point where a sector page stops being a coincidence of one theme.
SECTOR_MINIMUM = 8


def _nearest(
    rows: Sequence[Mapping[str, Any]], cap: float, limit: int
) -> list[Mapping[str, Any]]:
    """The ``limit`` rows whose caps are closest to ``cap``, ties by symbol."""

    ranked: list[tuple[float, float, str, Mapping[str, Any]]] = []
    for row in rows:
        value = _number(row.get("market_cap_usd"))
        if value is None:
            continue
        ranked.append((abs(value - cap), -value, _text(row.get("symbol")) or "", row))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in ranked[: max(limit, 0)]]


def _peer(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "symbol": _text(row.get("symbol")),
        "name": _text(row.get("name")),
        "exchange": _text(row.get("exchange")),
        "country": _text(row.get("country")),
        "sector": _text(row.get("sector")),
        "industry": _text(row.get("industry")),
        "market_cap": _number(row.get("market_cap_usd")),
        "price": _number(row.get("price")),
        "change_pct": _number(row.get("change_pct")),
    }


def _find(rows: Sequence[Mapping[str, Any]], symbol: str) -> Mapping[str, Any] | None:
    target = str(symbol or "").strip().upper()
    if not target:
        return None
    for row in rows:
        if str(row.get("symbol") or "").strip().upper() == target:
            return row
    return None


def _same(value: Any, expected: str) -> bool:
    """A null is not equal to anything, not even another null."""

    text = _text(value)
    return text is not None and text.casefold() == expected.casefold()


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _number(value: Any) -> float | None:
    """A finite float, or ``None``; a bool is not a number here."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None
