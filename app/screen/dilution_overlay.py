"""Dilution overlay — precompute dilution_pct for screener symbols.

Reads the securities list (symbol→CIK), the SEC companyfacts cache,
and computes ``dilution_pct`` (percentage change in shares outstanding)
for every symbol with cached data.  Results are cached in :data:`_current`
and populated once per process lifetime by :func:`precompute`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.market.dilution import shares_outstanding_series

logger = logging.getLogger(__name__)

_current: dict[str, float | None] = {}


def precompute(
    cache_dir: Path,
    snapshot_path: Path | str | None = None,
) -> dict[str, float | None]:
    """Build symbol→dilution_pct map from the securities list and cache.

    Called once per process lifetime.  Returns the full map and caches it
    in :data:`_current`.
    """
    if _current:
        return dict(_current)

    if snapshot_path is None:
        snapshot_path = Path("ztockz-data/engine-snapshot.json")  # symlink to latest

    snapshot_path = Path(snapshot_path)
    if not snapshot_path.exists():
        logger.warning("Dilution: snapshot not found at %s", snapshot_path)
        return {}

    try:
        data = json.loads(snapshot_path.read_text())
        securities = data["dataset"]["securities"]
    except Exception as exc:
        logger.warning("Dilution: cannot read snapshot: %s", exc)
        return {}

    # symbol → CIK (first occurrence wins)
    symbol_cik: dict[str, str] = {}
    for entry in securities:
        symbol = entry["symbol"]
        if symbol not in symbol_cik:
            symbol_cik[symbol] = entry["cik"]

    result: dict[str, float | None] = {}
    missing = 0
    for symbol, cik in symbol_cik.items():
        try:
            series = shares_outstanding_series(cache_dir, cik)
            result[symbol] = series.pct_change
        except Exception:
            # No share-count series for this CIK is an ordinary gap (a fund, a
            # brand-new listing), so the column is null rather than invented —
            # but the count is reported below: "this symbol has no series" and
            # "the read broke for most of the universe" must not look alike.
            result[symbol] = None
            missing += 1

    _current.clear()
    _current.update(result)
    logger.info(
        "Dilution precomputed for %d symbols (%d without a share-count series)",
        len(result),
        missing,
    )
    return dict(_current)


def lookup(symbol: str) -> float | None:
    """Return the dilution percentage change for *symbol*, or ``None``."""
    return _current.get(symbol.upper())