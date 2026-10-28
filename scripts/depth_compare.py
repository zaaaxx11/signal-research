#!/usr/bin/env python3
"""Compare two depth snapshots at field level.

Both inputs use the collector's document shape:
``{"as_of": ..., "count": N, "symbols": {SYM: {field: value}}}``.

This is the evidence tool for the Finviz-off A/B; it never calls a provider.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

CATALOG = Path(__file__).resolve().parents[1] / "app/engine/data/finviz_columns_116.json"


def _load(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("symbols")
    if not isinstance(rows, dict):
        raise SystemExit(f"{path}: missing symbols object")
    return {
        str(symbol): dict(fields)
        for symbol, fields in rows.items()
        if isinstance(fields, dict)
    }


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--on", type=Path, required=True, help="Finviz-on snapshot")
    parser.add_argument("--off", type=Path, required=True, help="Finviz-off snapshot")
    parser.add_argument("--out", type=Path, help="write JSON report")
    args = parser.parse_args()

    on, off = _load(args.on), _load(args.off)
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    keys = [str(row["key"]) for row in catalog]

    def count(rows: dict[str, dict[str, Any]], key: str) -> int:
        return sum(1 for fields in rows.values() if key in fields and fields[key] is not None)

    fields: dict[str, Any] = {}
    for key in keys:
        on_count, off_count = count(on, key), count(off, key)
        deltas: list[float] = []
        for symbol in set(on) & set(off):
            left, right = _finite(on[symbol].get(key)), _finite(off[symbol].get(key))
            if left is None or right is None or left == 0:
                continue
            deltas.append(abs(right - left) / abs(left) * 100.0)
        deltas.sort()
        fields[key] = {
            "on": on_count,
            "off": off_count,
            "delta_count": off_count - on_count,
            "median_pct_delta": (
                round(deltas[len(deltas) // 2], 4) if deltas else None
            ),
            "compared": len(deltas),
        }

    report = {
        "on": str(args.on),
        "off": str(args.off),
        "symbols": {"on": len(on), "off": len(off)},
        "fields": fields,
        "off_only": sorted(
            key for key in keys if fields[key]["on"] == 0 and fields[key]["off"] > 0
        ),
        "lost": sorted(
            key for key in keys if fields[key]["on"] > 0 and fields[key]["off"] == 0
        ),
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
