"""End-to-end proof of the batched enrichment rotation."""

import asyncio
import json
import time
from pathlib import Path

from app.market.enrichment import EnrichmentStage
from app.market.yahoo_depth import YahooDepthSource
from app.market.yahoo_egress import YahooEgress, rainproxy_url

UNIVERSE = "/home/ubuntu/briefs/market-engine/research/probes-1000/universe.json"
STATE = Path("/tmp/enrich-rotation-proof.json")


async def main() -> None:
    rows = json.load(open(UNIVERSE))["rows"]
    symbols = [r["name"] for r in rows if r.get("name")]
    STATE.unlink(missing_ok=True)

    egress = YahooEgress(proxies=[], paid_proxies=[rainproxy_url()], max_attempts=1)
    proxy = await egress.find_proxy()
    label = proxy.rsplit("@", 1)[-1] if proxy and "@" in proxy else (proxy or "direct")
    print(f"exit={label} universe={len(symbols)}", flush=True)

    stage = EnrichmentStage(
        yahoo_source=YahooDepthSource(concurrency=6, request_delay_s=0.15, proxy=proxy),
        batch_size=500,
        state_path=STATE,
    )

    for cycle in (1, 2):
        start = time.time()
        result = await stage.run(symbols)
        elapsed = time.time() - start
        head = list(result)[:2]
        print(
            f"cycle {cycle}: {len(result)} symbols in {elapsed:.1f}s "
            f"range={stage.last_batch} head={head} "
            f"cursor={json.loads(STATE.read_text())['cursor']}",
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
