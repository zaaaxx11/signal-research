"""Can the whole universe be enriched on the free direct exit, in batches?"""

import asyncio
import json
import time
from pathlib import Path

from app.market.enrichment import EnrichmentStage
from app.market.yahoo_depth import YahooDepthSource
from app.market.yahoo_egress import YahooEgress, rainproxy_url

UNIVERSE = "/home/ubuntu/briefs/market-engine/research/probes-1000/universe.json"
STATE = Path("/tmp/enrich-free-proof.json")


async def main() -> None:
    rows = json.load(open(UNIVERSE))["rows"]
    symbols = [r["name"] for r in rows if r.get("name")]
    STATE.unlink(missing_ok=True)

    # Free-first: direct, then rainproxy only if direct refuses.
    egress = YahooEgress(
        proxies=[], paid_proxies=[rainproxy_url()] if rainproxy_url() else [], max_attempts=1
    )
    proxy = await egress.find_proxy()
    label = proxy.rsplit("@", 1)[-1] if proxy and "@" in proxy else (proxy or "direct")
    print(f"exit={label} universe={len(symbols)}", flush=True)

    stage = EnrichmentStage(
        yahoo_source=YahooDepthSource(concurrency=6, request_delay_s=0.15, proxy=proxy),
        batch_size=500,
        state_path=STATE,
    )

    covered: set[str] = set()
    start_all = time.time()
    cycle = 0
    while len(covered) < len(symbols) and cycle < 12:
        cycle += 1
        started = time.time()
        result = await stage.run(symbols)
        elapsed = time.time() - started
        covered.update(result)
        cursor = json.loads(STATE.read_text())["cursor"]
        print(
            f"cycle {cycle}: +{len(result)} elapsed={elapsed:.1f}s "
            f"covered={len(covered)}/{len(symbols)} cursor={cursor}",
            flush=True,
        )
        if not result:
            print("empty cycle — the exit is refusing; stopping", flush=True)
            break
        await asyncio.sleep(2)

    print(
        f"TOTAL {len(covered)}/{len(symbols)} in {time.time() - start_all:.0f}s "
        f"({cycle} cycles) exit={label}",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
