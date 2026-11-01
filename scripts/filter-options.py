#!/usr/bin/env python3
"""Vendor Finviz's Fundamental / Technical / News / ETF option labels.

The bucket families read their labels from Finviz's own panel, never from a
string typed here: ``pe:low`` is "Low (<15)" because the probe says so, and the
day Finviz relabels that band is the day the document should say the new label.
:mod:`app.screen.buckets` loads the output of this script at import and
:func:`app.screen.buckets._label` is its only reader.

    python scripts/filter-options.py            # write the file
    python scripts/filter-options.py --check    # verify it is current

The source is the same probe the catalogue already vendored
(``research/finviz-ui-raw/views/v111-filters.json``). Every control of the probe
is copied (83, including ``sh_dilution``, which the 82-key catalogue deliberately
omits), and only its **named** options: ``Any`` is the empty selection,
``Custom``/``frange``/``modal`` is Finviz's free width, which no named bucket can
express. ``descriptive_options.json`` keeps the Descriptive group's richer
extract for the document; this file is the one label table the bucket families
read, so a family can never publish a label Finviz does not draw. The output is
deterministic: no timestamp, source order preserved, so a diff shows a
vocabulary change and never a reflow.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "research" / "finviz-ui-raw" / "views" / "v111-filters.json"
OUTPUT = ROOT / "app" / "screen" / "data" / "filter_options.json"

#: Every tab the probe carries. ``sh_dilution`` is here too: it is a Finviz
#: control (the catalogue's 82 keys deliberately do not include it), and a label
#: table that dropped it could not explain the one control the panel draws.
TABS: tuple[str, ...] = ("descriptive", "fundamental", "technical", "news", "etf")

#: The tokens that are a width, not an option: Finviz's Custom input.
SKIPPED: frozenset[str] = frozenset({"modal", "frange", "modal_intraday"})


def extract(document: dict[str, Any]) -> dict[str, Any]:
    """Every control's named options, in panel order."""

    controls: dict[str, Any] = {}
    for key, spec in document.items():
        if not isinstance(spec, dict) or spec.get("tab") not in TABS:
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
            "tab": spec.get("tab"),
            "label": str(title.get("short") or key).replace("\n", " "),
            "elite": bool(spec.get("isEliteOnly")),
            "options": options,
        }
    return {
        "source": {
            "path": "research/finviz-ui-raw/views/v111-filters.json",
            "generator": "scripts/filter-options.py",
            "tabs": list(TABS),
            "keys": len(controls),
        },
        "controls": controls,
    }


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the file is stale")
    arguments = parser.parse_args(argv)

    document = extract(json.loads(PROBE.read_text(encoding="utf-8")))
    text = render(document)
    if arguments.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != text:
            print(f"{OUTPUT} is stale: rerun python scripts/filter-options.py", file=sys.stderr)
            return 1
        print(f"{OUTPUT}: {document['source']['keys']} controls, current")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"{OUTPUT}: {document['source']['keys']} controls written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
