"""Identity join: universe file + engine snapshot → the screener's identity columns.

The fast path already downloads everything it needs for a price, a technical
and a fundamental; the identity columns were the one group it never joined,
because they live in two documents that are read once and never fetched again:

* the **universe file** (``research/probes-1000/universe.json``) carries the
  TradingView taxonomy — ``sector``, ``country``, ``type``/``subtype`` and the
  company description;
* the **engine snapshot** (``engine-snapshot.json``) carries the SEC SIC
  description as ``industry`` plus its own exchange and country.

The rules
---------
* **Absent, never invented.** A symbol the universe does not carry contributes
  nothing — not a guessed sector, not a default country.
* **Two vocabularies, honestly labelled.** ``sector`` is TradingView's
  (``Electronic Technology``); ``industry`` is the SEC SIC description
  (``Semiconductors``). They are different taxonomies and are never merged
  into one column, because a screener that silently relabels one as the other
  is lying about its own facets.
* **The row key is the ticker.** ``ticker`` is the canonical symbol the row is
  keyed by, not a second lookup.
* **One venue vocabulary.** The snapshot prints EDGAR's venue name (``Nasdaq``)
  and the universe file prints TradingView's code (``NASDAQ``): both reach
  ``exchange`` as the MIC (``XNAS``), because that is what every other surface
  of this engine stores (``app/market/venue.py``). The raw spelling is not
  republished here; a venue with no verified MIC (``TXSE``) keeps its name.
* **No network, no disk writes.** Both documents are read once at
  construction; a missing document is an empty index, not an error.

Contract: ``briefs/market-engine`` keyless plan, enrichment step 2.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.market.depth import canonical_symbol
from app.market.venue import normalize_venue

#: The columns this join publishes. ``exchange`` is appended last on purpose
#: (research D11): the docstring above always said the snapshot carries a venue,
#: while this tuple left it out, so no caller of the join could get the venue and
#: every surface asked the snapshot directly in its own answer shape. Appending
#: keeps every existing consumer's key order intact.
FIELDS: tuple[str, ...] = (
    "ticker",
    "company",
    "sector",
    "industry",
    "country",
    "security_type",
    "exchange",
)

#: The universe's ``type``/``subtype`` pair → the catalog's ``security_type``.
_SECURITY_TYPES = {
    ("stock", "common"): "stock",
    ("stock", ""): "stock",
    ("fund", ""): "fund",
    ("dr", ""): "dr",
}


def _read_json(path: Any) -> Any:
    """One JSON document, or ``None`` when it is absent or unreadable."""

    if path is None:
        return None
    try:
        text = Path(path).read_text()
    except (OSError, TypeError, ValueError):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _universe_rows(document: Any) -> dict[str, dict[str, Any]]:
    """The universe file's rows keyed by canonical symbol."""

    if not isinstance(document, dict):
        return {}
    rows = document.get("rows")
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        key = canonical_symbol(name)
        if key and key not in out:
            out[key] = row
    return out


def _snapshot_rows(document: Any) -> dict[str, dict[str, Any]]:
    """The engine snapshot's securities keyed by canonical symbol."""

    if not isinstance(document, dict):
        return {}
    securities = document.get("dataset", {}).get("securities")
    if not isinstance(securities, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in securities:
        if not isinstance(row, dict):
            continue
        key = canonical_symbol(str(row.get("symbol") or ""))
        if key and key not in out:
            out[key] = row
    return out


class IdentityIndex:
    """The identity columns for one symbol, read from two static documents.

    Construction reads both documents once and indexes them by canonical
    symbol; ``fields_for`` is then a dict lookup with no I/O. A document that
    is absent or malformed contributes nothing, so a box with only the
    universe file still answers sector and country, and a box with neither
    answers ``None`` for everything rather than raising.
    """

    def __init__(self, *, universe_path: Any = None, snapshot_path: Any = None) -> None:
        self._universe = _universe_rows(_read_json(universe_path))
        self._snapshot = _snapshot_rows(_read_json(snapshot_path))

    def fields_for(self, symbol: str) -> dict[str, Any]:
        """Every key of :data:`FIELDS`; a column with no evidence is ``None``."""

        key = canonical_symbol(symbol)
        universe = self._universe.get(key, {}) if key else {}
        snapshot = self._snapshot.get(key, {}) if key else {}
        known = bool(universe or snapshot)

        company = universe.get("description") or snapshot.get("name") or None
        country = universe.get("country") or snapshot.get("country") or None
        security_type = _SECURITY_TYPES.get(
            (str(universe.get("type") or ""), str(universe.get("subtype") or ""))
        )
        # The snapshot is the identity spine, so its venue wins; the universe
        # file is the fallback. Either spelling becomes the MIC the rest of the
        # engine stores (``Nasdaq``/``NASDAQ`` -> ``XNAS``).
        exchange, _ = normalize_venue(snapshot.get("exchange") or universe.get("exchange"))

        return {
            # The canonical symbol, and only for a symbol the documents
            # actually carry — an unknown ticker is absent, not echoed.
            "ticker": key if known else None,
            "company": str(company) if company else None,
            # TradingView's taxonomy, not the SEC's — the two stay separate.
            "sector": universe.get("sector") or None,
            # The SEC SIC description.
            "industry": snapshot.get("industry") or None,
            "country": str(country) if country else None,
            "security_type": security_type,
            # One vocabulary, and absent when neither document names a venue.
            "exchange": exchange or None,
        }


__all__ = ["FIELDS", "IdentityIndex"]
