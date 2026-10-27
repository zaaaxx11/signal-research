"""Bootstrap a runnable snapshot anywhere.

`docker run` and a fresh host both need the same first step: turn a list of
tickers into ``data/engine-snapshot.json`` without a Python one-liner. This
module is that step. It owns no data logic — it calls ``load_sec_dataset`` and
the same atomic ``EngineStore.save`` the service uses — so a snapshot made here
is byte-identical to one made by the startup path.

Contract: ``README.md`` §Portable deployment.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.engine import paths
from app.engine.ingest import DEFAULT_USER_AGENT, load_sec_dataset
from app.engine.store import EngineStore


async def build_snapshot(
    symbols: Sequence[str],
    *,
    output: Path | str | None = None,
    cache_dir: Path | str | None = None,
    as_of: str | None = None,
    client: Any = None,
) -> dict[str, object]:
    """Load ``symbols`` and write one snapshot; return a small report.

    Empty or blank symbols are dropped; the rest keep their order. A failure
    from the loader propagates — the CLI turns it into the house error envelope,
    because a half-written snapshot is worse than an honest failure.
    """

    cleaned = [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]
    if not cleaned:
        raise ValueError("at least one symbol is required")

    dataset = await load_sec_dataset(
        cleaned,
        cache_dir=Path(cache_dir) if cache_dir is not None else paths.cache_dir(),
        as_of=as_of,
        client=client,
    )
    target = Path(output) if output is not None else paths.snapshot_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    saved = EngineStore(target).save(dataset)

    return {
        "output": str(saved),
        "symbols": len(dataset.security_master),
        "facts": len(dataset.facts),
        "errors": len(dataset.errors),
        "error_symbols": sorted(dataset.errors),
        "user_agent": DEFAULT_USER_AGENT,
    }


def _parser() -> argparse.ArgumentParser:
    """The one-command bootstrap interface."""

    parser = argparse.ArgumentParser(
        prog="python -m app.engine.bootstrap",
        description="Build an engine snapshot from SEC for a list of tickers.",
    )
    parser.add_argument(
        "--symbols",
        required=True,
        help="comma-separated tickers, e.g. AAPL,MSFT,NVDA",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="snapshot path (default: MEE_ENGINE_SNAPSHOT or ./data/engine-snapshot.json)",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="document cache (default: MEE_ENGINE_CACHE_DIR or ./data/cache)",
    )
    parser.add_argument("--as-of", default=None, help="override the observation date")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the bootstrap; JSON on stdout, house envelope on stderr."""

    args = _parser().parse_args(sys.argv[1:] if argv is None else list(argv))
    symbols = [part.strip() for part in args.symbols.split(",") if part.strip()]
    try:
        report = asyncio.run(
            build_snapshot(
                symbols,
                output=args.output,
                cache_dir=args.cache_dir,
                as_of=args.as_of,
            )
        )
    except (ValueError, OSError) as exc:
        print(
            json.dumps(
                {"error": "bootstrap_failed", "errorMessage": f"{type(exc).__name__}: {exc}"},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
