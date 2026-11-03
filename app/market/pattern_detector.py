"""Pattern detection over the bar store — scan symbols for candlestick patterns."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from app.market.barstore import BarStore
from app.market.patterns import ALL_PATTERNS


def detect(
    symbols: Sequence[str],
    *,
    cache_dir: Path,
    lookback_bars: int = 3,
) -> dict[str, list[str]]:
    """Scan *symbols* for candlestick patterns using *lookback_bars* 3-bar windows.

    For each symbol the bar store is queried, the most recent *lookback_bars*
    three-bar windows are extracted, and every pattern in :data:`ALL_PATTERNS
    <app.market.patterns.ALL_PATTERNS>` is evaluated against each window.
    Matched pattern names are deduplicated and returned in sorted order.

    Symbols with fewer than three bars, or not present in the store, produce
    an empty list — they are never an error.
    """
    store = BarStore(cache_dir)
    pattern_items = list(ALL_PATTERNS.items())
    result: dict[str, list[str]] = {}

    for symbol in symbols:
        bars = store.load(symbol)
        if len(bars) < 3:
            result[symbol] = []
            continue

        # Slide up to *lookback_bars* windows of size 3 from the tail.
        matched: set[str] = set()
        windows = min(lookback_bars, len(bars) - 2)
        for offset in range(windows):
            start = len(bars) - 3 - offset
            window = bars[start : start + 3]
            for name, fn in pattern_items:
                if fn(window):  # type: ignore[operator]
                    matched.add(name)

        result[symbol] = sorted(matched)

    return result