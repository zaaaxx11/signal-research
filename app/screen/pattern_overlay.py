"""Pattern overlay — bridge between pattern_detector and Signal.accepts.

A module-level :data:`_current` dict (``symbol → [pattern_name, …]``) is
populated by :func:`detect_patterns`, and :func:`lookup` answers whether one
symbol carries one pattern.  Descriptive's twelve chart-pattern Signal entries
wire their ``accepts`` through ``lookup``, so the panel stays disabled until
the first detection run and answers every pattern thereafter.
"""

from __future__ import annotations

from pathlib import Path

_current: dict[str, list[str]] = {}


def detect_patterns(cache_dir: Path) -> dict[str, list[str]]:
    """Run pattern detection over every symbol in the bar store.

    Returns the symbol→patterns mapping and caches it in :data:`_current`.
    """
    from app.market.barstore import BarStore
    from app.market.pattern_detector import detect

    store = BarStore(cache_dir)
    symbols = store.symbols()
    result = detect(symbols, cache_dir=cache_dir)
    _current.clear()
    _current.update(result)
    return dict(_current)


def lookup(symbol: str, pattern: str) -> bool:
    """Check whether *symbol* has *pattern* in the most recent detection run."""
    patterns = _current.get(symbol.upper(), [])
    return pattern in patterns