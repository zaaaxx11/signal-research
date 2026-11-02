"""Persist ChartBar objects to disk as JSON Lines.

Each line is one JSON object with keys: day, open, high, low, close, volume.
Files are sharded by year under ``ztockz-data/bars/<symbol>.jsonl``.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from app.market.chart import ChartBar

#: Root directory for bar storage.
BAR_DIR = Path(os.environ.get("ZSTOCKZ_BAR_DIR", "ztockz-data/bars"))

#: One JSON object per line.
_LINE_SEP = "\n"


class BarStore:
    """Append-only bar store backed by JSON Lines files.

    Parameters
    ----------
    base_dir:
        Root directory; defaults to ``ztockz-data/bars`` relative to cwd.
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._base = Path(base_dir or BAR_DIR)
        self._base.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str) -> Path:
        return self._base / f"{symbol.upper()}.jsonl"

    def append(self, symbol: str, bar: ChartBar) -> None:
        """Append one bar for *symbol*; overwrite is a no-op."""

        path = self._path(symbol)
        exists = path.exists()
        last = self._last_bar(symbol) if exists else None
        if last is not None and bar.day <= last.day:
            return  # guard against duplicates / out-of-order
        record = asdict(bar)
        record["day"] = _serialize_day(record["day"])
        with open(path, "a", encoding="utf-8") as fh:
            if exists:
                fh.write(_LINE_SEP)
            fh.write(json.dumps(record, separators=(",", ":")))
            fh.write(_LINE_SEP)

    def append_many(self, symbol: str, bars: list[ChartBar]) -> int:
        """Append only bars newer than the last stored bar.

        Returns the number of bars actually appended.
        """

        deduped = self._dedupe(symbol, bars)
        if not deduped:
            return 0
        path = self._path(symbol)
        exists = path.exists()
        with open(path, "a", encoding="utf-8") as fh:
            for idx, bar in enumerate(deduped):
                record = asdict(bar)
                record["day"] = _serialize_day(record["day"])
                if exists or idx > 0:
                    fh.write(_LINE_SEP)
                fh.write(json.dumps(record, separators=(",", ":")))
        return len(deduped)

    def load(self, symbol: str) -> list[ChartBar]:
        """Return all stored bars for *symbol*, oldest first."""

        path = self._path(symbol)
        if not path.exists():
            return []
        bars: list[ChartBar] = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                record["day"] = _parse_day(record["day"])
                bars.append(ChartBar(**record))
        return bars

    def latest(self, symbol: str) -> ChartBar | None:
        """The newest stored bar, or ``None``."""

        bars = self.load(symbol)
        return bars[-1] if bars else None

    def symbols(self) -> list[str]:
        """Every symbol that has at least one stored bar."""

        if not self._base.exists():
            return []
        return sorted(
            p.stem for p in self._base.glob("*.jsonl") if p.stat().st_size > 0
        )

    def count(self, symbol: str) -> int:
        """Number of stored bars for *symbol*."""

        path = self._path(symbol)
        if not path.exists():
            return 0
        with open(path, "r", encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())

    def _last_bar(self, symbol: str) -> ChartBar | None:
        """Fast tail read: only the last non-empty line."""

        path = self._path(symbol)
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            if size == 0:
                return None
            chunk = 4096
            offset = max(0, size - chunk)
            while offset >= 0:
                fh.seek(offset)
                tail = fh.read().decode("utf-8", errors="replace")
                lines = [l for l in tail.split("\n") if l.strip()]
                for line in reversed(lines):
                    record = json.loads(line)
                    record["day"] = _parse_day(record["day"])
                    return ChartBar(**record)
                if offset == 0:
                    break
                offset = max(0, offset - chunk)
        return None

    def _dedupe(
        self, symbol: str, bars: list[ChartBar]
    ) -> list[ChartBar]:
        """Drop bars already stored (by day), preserving order."""

        path = self._path(symbol)
        if not path.exists():
            return list(bars)
        last = self._last_bar(symbol)
        if last is None:
            return list(bars)
        cutoff = last.day
        return [b for b in bars if b.day > cutoff]


def _serialize_day(value: date | str) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return value


def _parse_day(value: str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


__all__ = ["BarStore", "BAR_DIR"]
