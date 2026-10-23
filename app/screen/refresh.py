"""Five-minute screen refresh, as a CLI and a testable loop.

The display contract is a snapshot, not a request-time scrape: a user opening the
tab must not pay for the 65k-row Global scan. This module is the process that
pays for it on a schedule and leaves the snapshots behind.

* ``refresh_all_screens`` refreshes every (region, sort) pair, continuing after
  one failure: a broken Global scan must not leave the US tab stale too.
* ``python -m app.screen.refresh --once`` is the cron entry. It is deliberately
  a separate process: the ASGI worker should not own a scheduler, and a failed
  refresh cannot take the API down with it.
* ``--loop`` is for a host that prefers one daemon over cron; both paths call
  the same function, so there is no second refresh implementation.

The 300-second interval matches ``SNAPSHOT_TTL_S``. A refresh that takes longer
than the interval is not started twice: the loop sleeps for the remainder of the
window after the pass completes.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Iterator, Sequence

from app.config import get_settings
from app.screen.repository import SqliteScreenRepository
from app.screen.snapshot import SNAPSHOT_TTL_S, Snapshot, refresh_screen

logger = logging.getLogger("mee.screen.refresh")

#: Same window as the display snapshot. One constant, not two.
REFRESH_INTERVAL_S = SNAPSHOT_TTL_S
REGIONS: tuple[str, ...] = ("us", "global")
#: The two tabs the product ships. ``gainers`` stays on demand until it has a
#: dedicated UI surface; refreshing a sort nobody reads is wasted provider load.
SORTS: tuple[str, ...] = ("market_cap", "turnover")

RefreshCall = Callable[..., Awaitable[Snapshot]]


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def refresh_all_screens(
    repository: SqliteScreenRepository,
    *,
    regions: Sequence[str] = REGIONS,
    sorts: Sequence[str] = SORTS,
    refresh: RefreshCall = refresh_screen,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Refresh every pair once; return one report per pair.

    A failure is recorded and the next pair still runs. The caller (cron or
    loop) sees a non-zero ``failed`` count and can alert, while the API keeps
    serving the last good snapshot.
    """

    moment = now or _utcnow()
    results: list[dict[str, Any]] = []
    for region in regions:
        for sort in sorts:
            started = time.monotonic()
            try:
                snapshot = await refresh(
                    region,
                    sort,
                    repository=repository,
                    now=moment,
                    ttl_s=REFRESH_INTERVAL_S,
                )
                results.append(
                    {
                        "region": region,
                        "sort": sort,
                        "status": "ok",
                        "screen_id": snapshot.screen_id,
                        "total": int(snapshot.screen.get("total_count") or 0),
                        "returned": len(snapshot.rows),
                        "seconds": round(time.monotonic() - started, 3),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - one tab must not stop the rest
                logger.exception("screen refresh failed region=%s sort=%s", region, sort)
                results.append(
                    {
                        "region": region,
                        "sort": sort,
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                        "seconds": round(time.monotonic() - started, 3),
                    }
                )
    return {
        "as_of": moment.isoformat(timespec="microseconds"),
        "interval_s": REFRESH_INTERVAL_S,
        "ok": sum(1 for item in results if item["status"] == "ok"),
        "failed": sum(1 for item in results if item["status"] == "failed"),
        "results": results,
    }


async def run_forever(
    repository: SqliteScreenRepository,
    *,
    interval_s: int = REFRESH_INTERVAL_S,
    regions: Sequence[str] = REGIONS,
    sorts: Sequence[str] = SORTS,
    refresh: RefreshCall = refresh_screen,
    stop: asyncio.Event | None = None,
) -> None:
    """Refresh once per window until stopped; never overlap two passes."""

    if interval_s < 1:
        raise ValueError(f"interval_s must be >= 1, got {interval_s!r}")
    stopper = stop or asyncio.Event()
    while not stopper.is_set():
        started = time.monotonic()
        report = await refresh_all_screens(
            repository, regions=regions, sorts=sorts, refresh=refresh
        )
        logger.info(
            "screen refresh pass ok=%d failed=%d next_in=%ds",
            report["ok"],
            report["failed"],
            interval_s,
        )
        remaining = max(0.0, interval_s - (time.monotonic() - started))
        try:
            await asyncio.wait_for(stopper.wait(), timeout=remaining)
        except TimeoutError:
            continue


def _pairs(values: str, allowed: Sequence[str]) -> tuple[str, ...]:
    items = tuple(item.strip() for item in values.split(",") if item.strip())
    unknown = [item for item in items if item not in allowed]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown value(s) {unknown}; expected any of {', '.join(allowed)}"
        )
    return items or tuple(allowed)


async def _main_once(args: argparse.Namespace) -> int:
    settings = get_settings()
    repository = SqliteScreenRepository(settings.db_path)
    try:
        report = await refresh_all_screens(
            repository,
            regions=args.regions,
            sorts=args.sorts,
        )
    finally:
        await repository.aclose()
    import json

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["failed"] == 0 else 1


async def _main_loop(args: argparse.Namespace) -> int:
    settings = get_settings()
    repository = SqliteScreenRepository(settings.db_path)
    try:
        await run_forever(
            repository,
            interval_s=args.interval,
            regions=args.regions,
            sorts=args.sorts,
        )
    finally:
        await repository.aclose()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.screen.refresh")
    parser.add_argument(
        "--once",
        action="store_true",
        help="refresh every pair once, print a JSON report, exit non-zero on failure",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help=f"refresh every --interval seconds (default {REFRESH_INTERVAL_S})",
    )
    parser.add_argument("--interval", type=int, default=REFRESH_INTERVAL_S)
    parser.add_argument(
        "--regions",
        type=lambda value: _pairs(value, REGIONS),
        default=REGIONS,
        help="comma-separated regions (default: us,global)",
    )
    parser.add_argument(
        "--sorts",
        type=lambda value: _pairs(value, SORTS),
        default=SORTS,
        help="comma-separated sorts (default: market_cap,turnover)",
    )
    args = parser.parse_args(argv)
    if args.once == args.loop:
        parser.error("choose exactly one of --once or --loop")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    return asyncio.run(_main_once(args) if args.once else _main_loop(args))


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    sys.exit(main())


__all__ = [
    "REFRESH_INTERVAL_S",
    "REGIONS",
    "SORTS",
    "main",
    "refresh_all_screens",
    "run_forever",
]
