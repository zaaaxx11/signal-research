#!/usr/bin/env python3
"""Vendor Finviz's Descriptive option lists into one file the API can ship.

``GET /screens/filters`` publishes every control of Finviz's Descriptive group
with its options and the predicate each option compiles to. Eleven of those
controls compile through :mod:`app.screen.buckets`, whose labels and ranges are
already owned there; the rest are *Finviz's labels for Finviz's tokens*, and
retyping 40 themes and 268 sub-themes by hand is how a vocabulary drifts from the
panel it claims to describe.

So the labels are vendored, not retyped:

    python scripts/descriptive-options.py           # write the file
    python scripts/descriptive-options.py --check    # verify it is current

The source is the probe the rest of the catalogue already reads
(``research/finviz-ui-raw/views/v111-filters.json``), and the output is
deterministic: no timestamp, no dict ordering from the source file's whim — the
same probe produces the same bytes, so a diff shows a vocabulary change and
never a reflow. Only the 23 ``tab == "descriptive"`` keys are copied, and only
their **named** options: ``Any`` is the empty selection, ``Custom``/``frange``/
``modal`` is Finviz's free-width width, which no named bucket can express.

``sh_dilution`` is not one of the catalogue's 82 keys (no dilution input exists),
but it *is* a Descriptive control and the panel draws it disabled with its five
options, so it is vendored here like the rest.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "research" / "finviz-ui-raw" / "views" / "v111-filters.json"
OUTPUT = ROOT / "app" / "screen" / "data" / "descriptive_options.json"

#: The tokens that are a width, not an option: Finviz's Custom input.
SKIPPED: frozenset[str] = frozenset({"modal", "frange"})


def extract(document: dict[str, Any]) -> dict[str, Any]:
    """The 23 descriptive keys, named options only, in panel order."""

    controls: dict[str, Any] = {}
    for key, spec in document.items():
        if not isinstance(spec, dict) or spec.get("tab") != "descriptive":
            continue
        title = spec.get("title") or {}
        options: list[dict[str, Any]] = []
        for option in spec.get("options") or ():
            value = option.get("value")
            if not isinstance(value, str) or not value or value in SKIPPED:
                continue
            entry: dict[str, Any] = {"token": value, "label": option.get("label")}
            if option.get("isEliteOnly"):
                # Kept as a fact, never as a gate: Finviz's tier is not ours.
                entry["elite"] = True
            options.append(entry)
        controls[key] = {
            "key": key,
            "label": title.get("short") or title.get("full") or key,
            "description": title.get("description"),
            "uiType": spec.get("uiType"),
            "rangePrefix": spec.get("rangePrefix"),
            "rangeSuffix": spec.get("rangeSuffix"),
            "options": options,
        }
    return controls


def build() -> dict[str, Any]:
    document = json.loads(PROBE.read_text(encoding="utf-8"))
    controls = extract(document)
    return {
        "source": {
            "path": str(PROBE.relative_to(ROOT)),
            "generator": "scripts/descriptive-options.py",
            "keys": len(controls),
        },
        "controls": controls,
    }


def encoded(payload: dict[str, Any]) -> str:
    """The one encoder: two-space indent, UTF-8, one trailing newline."""

    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the file, write nothing")
    args = parser.parse_args(argv)

    payload = build()
    text = encoded(payload)
    if args.check:
        try:
            current = OUTPUT.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"{OUTPUT} cannot be read: {exc}", file=sys.stderr)
            return 1
        if current != text:
            print(
                f"{OUTPUT.relative_to(ROOT)} is stale: regenerate it with "
                "python scripts/descriptive-options.py",
                file=sys.stderr,
            )
            return 1
        print(f"{OUTPUT.relative_to(ROOT)} is current ({payload['source']['keys']} keys)")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text, encoding="utf-8")
    counts = ", ".join(
        f"{key}={len(spec['options'])}" for key, spec in payload["controls"].items()
    )
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({payload['source']['keys']} keys)")
    print(counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
