"""Find the direct-exit throttle threshold: back-to-back 500-symbol batches."""

import asyncio
import json
import time
from pathlib import Path

from app.market.enrichment import EnrichmentStage
from app.market.yahoo_depth import YahooDepthSource

UNIVERSE = "/home/ubuntu/briefs/market-engine/research/probes-1000/universe.json"
STATE = Path("/tmp/enrich-threshold.json")


async def main() -> None:
    rows = json.load(open(UNIVERSE))["rows"]
    symbols = [r["name"] for r in rows if r.get("name")]
    STATE.unlink(missing_ok=True)

    stage = EnrichmentStage(
        yahoo_source=YahooDepthSource(concurrency=6, request_delay_s=0.1, proxy=None),
        batch_size=500,
        state_path=STATE,
    )

    covered: set[str] = set()
    started_all = time.time()
    for cycle in range(1, 13):
        began = time.time()
        result = await stage.run(symbols)
        elapsed = time.time() - began
        covered.update(result)
        print(
            f"cycle {cycle}: +{len(result)} elapsed={elapsed:.1f}s "
            f"covered={len(covered)} total={time.time() - started_all:.0f}s",
            flush=True,
        )
        if len(result) < 50:
            print("THROTTLED — the direct exit stopped answering", flush=True)
            break


if __name__ == "__main__":
    asyncio.run(main())
