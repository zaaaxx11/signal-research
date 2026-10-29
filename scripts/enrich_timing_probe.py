"""Batched enrichment timing probe: Yahoo + news, per stage."""

import asyncio
import json
import sys
import time

from app.market.news import RssNewsSource
from app.market.yahoo_depth import YahooDepthSource
from app.market.yahoo_egress import YahooEgress, rainproxy_url

UNIVERSE = "/home/ubuntu/briefs/market-engine/research/probes-1000/universe.json"


async def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    rows = json.load(open(UNIVERSE))["rows"]
    symbols = [r["name"] for r in rows if r.get("name")][:n]

    egress = YahooEgress(proxies=[], paid_proxies=[rainproxy_url()], max_attempts=1)
    proxy = await egress.find_proxy()
    label = proxy.rsplit("@", 1)[-1] if proxy and "@" in proxy else (proxy or "direct")
    print(f"exit={label}", flush=True)

    yahoo = YahooDepthSource(concurrency=6, request_delay_s=0.15, proxy=proxy)
    start = time.time()
    observations = await yahoo.fetch_many(symbols)
    print(f"yahoo {len(observations)}/{n} {time.time() - start:.1f}s", flush=True)

    news = RssNewsSource(concurrency=8, fallback=True)
    start = time.time()
    items = await news.fetch_many(symbols)
    print(f"news {len(items)}/{n} {time.time() - start:.1f}s", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
