"""Prove the SEC bulk index prune changes no mapped field, and price the saving.

Run:  .venv/bin/python scripts/facts_prune_diff.py [--quarters 2026q3,2025q4] [--limit 500]

``app.engine.bulk_financials`` drops, at build time, rows a mapper can never
select, in two passes that are both claims about the mapper:

* **duplicates** — one survivor per
  ``(tag, version, ddate, qtrs, uom, segments, coreg)``, the newest ``filed``;
* **dimensions** — ``app.market.sec_fundamentals._rows_for`` skips a row whose
  ``segments`` is non-empty, and 63.85% of one quarter's mapped rows are
  dimensional (502,194 of 786,489, measured 2026-11-07).

Supposed to be a pure memory optimisation, so this harness is the evidence, not
the claim: it builds the same quarters **twice** — once raw (``prune=False``,
``undimensioned=False``) and once exactly as production builds it — and, for
every CIK, compares the full field set
``app.market.sec_fundamentals.fundamentals_for_cik`` publishes. It also checks
the structural invariant that the production rows are a subset of the raw ones:
a restatement may move a value, but no row may be invented.

Run it after any change to ``prune_facts``, ``FactIndex.build`` or ``_rows_for``.

Rules
-----
* **Read-only, offline.** It reads cached ZIPs under
  ``<cache>/sec-bulk/<quarter>.zip``; no download, no socket.
* **Production shape.** By default the CIK set comes from the live snapshot, so
  the comparison covers what the deployment actually serves; ``--snapshot ''``
  compares every filer instead.
* **Honest.** Every mismatch is printed with the CIK, the field and both
  values; the list is capped at :data:`MAX_MISMATCHES`, and ``identical`` is
  ``true`` only when the list is empty **and** no row was invented. The exit
  code is 0 only for an identical report.
* **Measured, not estimated.** ``row_reduction_pct`` is the real
  ``(raw - production) / raw``; nothing here guesses. A quarter missing from the
  cache is skipped and named — never silently treated as empty.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]

#: The service's ``EnvironmentFile`` — the same one ``scripts/mem_probe.py``
#: reads — so a bare ``--quarters`` resolves the cache exactly where the engine
#: puts it (``MEE_ENGINE_CACHE_DIR``), not under a bare ``./data``.
ENV_FILE = Path(os.environ.get("MEE_ENV_FILE", "/home/ubuntu/ztockz-data/engine.env"))

#: The snapshot whose CIK set mirrors production when ``--snapshot`` is omitted.
DEFAULT_SNAPSHOT = os.environ.get(
    "MEE_ENGINE_SNAPSHOT", "/home/ubuntu/ztockz-data/engine-snapshot.json"
)

#: The quarters the depth source defaults to, newest first.
DEFAULT_QUARTERS = ("2026q3", "2026q2", "2026q1", "2025q4")

#: Do not flood the report: the shape is what matters, not the ten-thousandth
#: copy of a broken field.
MAX_MISMATCHES = 20

#: A fixed, positive quote: it keeps the price-dependent columns in the compare
#: (a value that is present must be *the same* value), and any positive number
#: exercises the same arithmetic path.
COMPARE_PRICE = 100.0


def load_env_file(path: Path) -> int:
    """Put the service's ``EnvironmentFile`` into ``os.environ`` (``KEY=VALUE``).

    A key already in the environment wins, so an operator can point one run at
    a different cache without editing the file.
    """

    applied = 0
    if not path.exists():
        return 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip()
            applied += 1
    return applied


def _facts_total(index: Any) -> int:
    """Rows the index holds across every issuer."""

    return sum(len(facts) for facts in index.by_cik.values())


def resolve_paths(
    cache_dir: Path | str | None, quarters: tuple[str, ...]
) -> tuple[list[Path], list[str]]:
    """The cached ZIP per quarter, plus the labels whose ZIP is absent."""

    from app.engine.bulk_financials import dataset_cache_path

    found: list[Path] = []
    missing: list[str] = []
    for quarter in quarters:
        path = dataset_cache_path(cache_dir, quarter)
        if path.is_file():
            found.append(path)
        else:
            missing.append(quarter)
    return found, missing


def resolve_ciks(snapshot: Path | None, limit: int | None) -> list[str] | None:
    """The CIK set the deployment serves, so the proof runs in the real shape.

    ``None`` means every filer the quarters carry — slower, and not what
    production asks for. A snapshot that cannot be read answers ``None`` with a
    warning rather than an empty universe.
    """

    if snapshot is None:
        return None
    try:
        from app.engine.store import EngineStore

        dataset = EngineStore(snapshot).load()
    except Exception as exc:  # noqa: BLE001 - a harness reports, it does not raise
        print(
            f"warning: could not read {snapshot} ({type(exc).__name__}: {exc}); "
            "comparing every filer instead",
            file=sys.stderr,
        )
        return None
    ciks = sorted({record.cik for record in dataset.security_master.records() if record.cik})
    return ciks[:limit] if limit is not None else ciks


def build_report(
    paths: list[Path],
    *,
    ciks: list[str] | None,
    undimensioned: bool,
    missing: list[str],
) -> dict[str, Any]:
    """Build the raw and the production index, then diff every mapped field."""

    from app.engine.bulk_financials import build_quarters, prune_facts
    from app.market.sec_fundamentals import (
        FIELDS,
        MAPPED_CONCEPTS,
        fundamentals_for_cik,
    )

    started = time.perf_counter()
    raw = build_quarters(
        paths, ciks=ciks, tags=MAPPED_CONCEPTS, prune=False, undimensioned=False
    )
    built = time.perf_counter()
    production = build_quarters(
        paths, ciks=ciks, tags=MAPPED_CONCEPTS, prune=True, undimensioned=undimensioned
    )
    dimmed = time.perf_counter()

    raw_facts = _facts_total(raw)
    production_facts = _facts_total(production)

    mismatches: list[str] = []
    invented: list[str] = []
    fields_compared = 0
    for cik in sorted(set(raw.by_cik) | set(production.by_cik)):
        # Structural invariant: production may only lose rows. A restatement may
        # replace one row's value, but no row may appear that the raw build never
        # carried.
        if not set(production.facts_for(cik)) <= set(raw.facts_for(cik)):
            if len(invented) < MAX_MISMATCHES:
                invented.append(cik)

        before = fundamentals_for_cik(raw, cik, price=COMPARE_PRICE)
        after = fundamentals_for_cik(production, cik, price=COMPARE_PRICE)
        for name in FIELDS:
            fields_compared += 1
            if before[name] != after[name] and len(mismatches) < MAX_MISMATCHES:
                mismatches.append(
                    f"{cik} {name}: raw={before[name]!r} production={after[name]!r}"
                )

    dimensional_rows = sum(
        1 for facts in production.by_cik.values() for fact in facts if fact.segments
    )
    reduction = 0.0 if raw_facts == 0 else (raw_facts - production_facts) / raw_facts * 100.0
    return {
        "quarters": [path.stem for path in paths],
        "missing_quarters": missing,
        "dimension_filter": undimensioned,
        "ciks": len(set(raw.by_cik) | set(production.by_cik)),
        "raw_facts": raw_facts,
        "production_facts": production_facts,
        "row_reduction_pct": round(reduction, 2),
        "dimensional_rows_kept": dimensional_rows,
        "fields_compared": fields_compared,
        "mismatches": mismatches,
        "rows_invented": invented,
        "identical": not mismatches and not invented,
        # Named so a rename of the rule breaks this harness instead of silently
        # testing an older one.
        "prune_rule": prune_facts.__name__,
        "raw_build_s": round(built - started, 1),
        "production_build_s": round(dimmed - built, 1),
    }


def main(argv: list[str] | None = None) -> int:
    """Resolve the quarters, run the diff, print the JSON report."""

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--quarters",
        default=",".join(DEFAULT_QUARTERS),
        help="cached dataset labels, newest first (default: the depth window)",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="engine cache root (default: the engine's own MEE_ENGINE_CACHE_DIR)",
    )
    parser.add_argument(
        "--snapshot",
        default=DEFAULT_SNAPSHOT,
        help="snapshot whose CIK set mirrors production ('' compares every filer)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="compare only the first N CIKs (a smoke run)",
    )
    parser.add_argument(
        "--keep-dimensional",
        action="store_true",
        help="skip the dimensional filter, to price the duplicate prune alone",
    )
    args = parser.parse_args(argv)

    sys.path.insert(0, str(REPO))
    os.chdir(REPO)
    load_env_file(ENV_FILE if args.cache_dir is None else Path(os.devnull))

    quarters = tuple(item.strip() for item in args.quarters.split(",") if item.strip())
    paths, missing = resolve_paths(args.cache_dir, quarters)
    if not paths:
        print(
            json.dumps({"error": "no_cached_quarter", "quarters": list(quarters)}),
            file=sys.stderr,
        )
        return 1

    report = build_report(
        paths,
        ciks=resolve_ciks(Path(args.snapshot) if args.snapshot else None, args.limit),
        undimensioned=not args.keep_dimensional,
        missing=missing,
    )
    print(json.dumps(report, indent=2))
    return 0 if report["identical"] else 2


if __name__ == "__main__":  # pragma: no cover - the harness's own entry point
    raise SystemExit(main())
