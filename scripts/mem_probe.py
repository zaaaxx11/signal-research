"""Where does the API's RAM go? Stage-by-stage attribution, offline.

Run:  .venv/bin/python scripts/mem_probe.py [--snapshot PATH]

The service holds ~6.8 GB RSS on an 11.9 GB box, so "which store is the hog" has
to be a measurement, not a guess: the depth store file is 12 MB on disk, the
engine snapshot 23 MB, and a JSON file's Python footprint is not its byte count.
This probe loads each store the way startup does, one stage at a time, and
prints the resident-set delta after every stage, so the answer names a component
instead of a suspicion.

Rules
-----
* **Read-only.** Nothing here writes a store or opens a socket: it is safe to run
  beside the live service.
* **Bounded.** A stage that would push the probe past ``--cap-mb`` (default
  3000) is reported and the run stops, so a surprise cannot take the box down.
* **Honest on failure.** A loader that raises prints its exception and the run
  continues; a missing store is a fact about the deployment, not a crash.
"""

from __future__ import annotations

import argparse
import gc
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

REPO = Path(__file__).resolve().parents[1]
ENV_FILE = Path(os.environ.get("MEE_ENV_FILE", "/home/ubuntu/ztockz-data/engine.env"))

T = TypeVar("T")


def rss_mb() -> float:
    """This process's resident set, in MB (``/proc/self/statm`` pages → MB)."""

    with open("/proc/self/statm", encoding="ascii") as handle:
        fields = handle.read().split()
    return int(fields[1]) * (os.sysconf("SC_PAGE_SIZE") / 1024 / 1024)


def load_env_file(path: Path) -> int:
    """Put the service's ``EnvironmentFile`` into ``os.environ`` (``KEY=VALUE``).

    The systemd unit starts uvicorn with this file, so a probe that skipped it
    would read different settings than production and measure the wrong stores.
    An existing environment variable wins, so an operator can override one value
    for a run without editing the file.
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


class Probe:
    """One running total, one cap, one printed line per stage."""

    def __init__(self, cap_mb: float) -> None:
        self.cap_mb = cap_mb
        self.baseline = rss_mb()
        self.rows: list[tuple[str, float, float, str]] = []

    def stage(self, label: str, fn: Callable[[], T], detail: Callable[[T], str] | None = None) -> T | None:
        """Run ``fn``, record the RSS delta, and stop before the cap."""

        before = rss_mb()
        value: T | None = None
        note = ""
        try:
            value = fn()
            if detail is not None and value is not None:
                note = detail(value)
        except BaseException as exc:  # a missing store is a fact, not a crash
            note = f"{type(exc).__name__}: {exc}"
        gc.collect()
        after = rss_mb()
        self.rows.append((label, after - before, after, note))
        print(f"{label:<34} Δ{after - before:>8.1f} MB   total {after:>8.1f} MB   {note}", flush=True)
        if after > self.cap_mb:
            print(f"-- cap {self.cap_mb:.0f} MB reached at '{label}': stopping.", flush=True)
            raise SystemExit(3)
        return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage-by-stage RSS attribution.")
    parser.add_argument("--snapshot", default=None, help="engine snapshot path")
    parser.add_argument("--cap-mb", type=float, default=3000.0)
    parser.add_argument(
        "--sec-index",
        action="store_true",
        help="also build the SEC bulk depth index (minutes of CPU; cap it)",
    )
    args = parser.parse_args()

    applied = load_env_file(ENV_FILE)
    sys.path.insert(0, str(REPO))
    os.chdir(REPO)

    probe = Probe(args.cap_mb)
    print(f"baseline {probe.baseline:.1f} MB (env from {ENV_FILE}: {applied} keys)", flush=True)

    from app.config import get_settings

    settings = probe.stage("import app.config + settings", get_settings)
    snapshot_path = args.snapshot or os.environ.get("MEE_ENGINE_SNAPSHOT", "")
    print(f"snapshot  = {snapshot_path}", flush=True)
    print(f"depth     = {os.environ.get('MEE_DEPTH_STORE_PATH', '')}", flush=True)

    dataset = None
    if snapshot_path:
        from app.engine.store import EngineStore

        store = EngineStore(snapshot_path)
        dataset = probe.stage(
            "EngineStore.load() snapshot",
            store.load,
            lambda ds: (
                f"symbols={len(ds.security_master.records())} "
                f"facts_symbols={len(ds.facts)} "
                f"cells={sum(len(facts) for facts in ds.facts.values())}"
            ),
        )

    if dataset is not None:
        from app.engine.dataset import DatasetRows

        rows = probe.stage(
            "DatasetRows.rows()",
            lambda: DatasetRows(dataset).rows(),
            lambda value: f"rows={len(value)}",
        )

        if rows is not None:
            from app.screen.universe import build_universe

            probe.stage(
                "build_universe(us)",
                lambda: build_universe(rows, region="us"),
                lambda value: f"issuers={len(value)}",
            )

    from app.screen.depth_source import current_depth

    probe.stage(
        "depth store (depth.json)",
        lambda: current_depth(settings),
        lambda value: f"symbols={value.count}",
    )

    from app.screen import news_index

    probe.stage(
        "news index build",
        lambda: news_index.index(),
        lambda value: f"type={type(value).__name__}",
    )

    from app.screen import after_hours, etf_holdings, entitlements, index_membership

    probe.stage("index membership (json)", index_membership.load)
    probe.stage("etf holdings (json)", etf_holdings.load)
    probe.stage("after hours (json)", after_hours.load)
    probe.stage("entitlements (json)", entitlements.load)

    if args.sec_index:
        # The depth collector's SEC bulk source: the one store whose size the
        # code itself calls out (~3.9 GB unfiltered for the six-quarter
        # universe). Measure the filtered build the source actually performs.
        import asyncio

        from app.market.sec_fundamentals_depth import SecFundamentalsDepthSource

        source = SecFundamentalsDepthSource(snapshot_path=snapshot_path or None)
        index = probe.stage(
            "sec bulk index (6 quarters)",
            lambda: asyncio.run(source._ensure_index()),
            lambda value: (
                f"issuers={len(value.by_cik)} "
                f"facts={sum(len(facts) for facts in value.by_cik.values())}"
                if value is not None
                else f"build_error={source.build_error}"
            ),
        )
        if index is not None:
            tags = {fact.tag for facts in index.by_cik.values() for fact in facts}
            print(f"distinct concepts kept: {len(tags)}", flush=True)

    print("\n-- ordered by footprint --", flush=True)
    for label, delta, total, note in sorted(probe.rows, key=lambda row: -row[1]):
        print(f"{delta:>9.1f} MB  {label:<34} {note}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
