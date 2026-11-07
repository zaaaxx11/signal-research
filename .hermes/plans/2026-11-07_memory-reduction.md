# Ztockz memory reduction — measured plan (2026-11-07)

## What the numbers said

The handover document blamed per-symbol depth data (4,217 symbols resident, "lazy-load
with Parquet, hot/warm/cold tiers"). Measurement said otherwise — `scripts/mem_probe.py`,
stage by stage, on the live box:

| Stage | Δ RSS | Detail |
|---|---|---|
| engine snapshot (`EngineStore.load`) | 158.8 MB | 23 MB JSON → 10,433 symbols, 85,692 cells |
| depth store (`depth.json`) | 0.0 MB | 4,217 symbols; 12 MB on disk |
| news index + 4 JSON stores | 0.6 MB | together |
| **SEC bulk fact index (4 cached quarters)** | **1,466.4 MB** | 6,414 issuers, 2,704,468 facts |

So the process's static stores are ~200 MB and the depth store the document wanted to
redesign is invisible. Live RSS was 6.80 GB with one worker, 9h52m uptime.

## Landed (commit `f23c003`, branch `wave2`, pushed)

1. **The index is filtered to the mapper's concepts.** `_ensure_index` now passes
   `tags=MAPPED_CONCEPTS` (37 concepts derived from `TAG_MAP`). The module's own
   docstring names the unfiltered cost: ~3.9 GB for six quarters, enough to get the
   worker cgroup OOM-killed. Measured filtered: **1,466 MB**.
2. **Repeated string fields are interned** at parse time (`_shared()` in
   `app/engine/bulk_financials.py`; `segments` excluded). Measured on identical input
   (same 2,704,468 facts, same 6,414 issuers): **1,466 MB → 640 MB**.
3. **`GET /health/memory`** answers RSS/VmSize/threads, the service cgroup's
   current/high/max/peak, the GC census and the live stores; `?objects=1` adds a heap
   census by type; `MEE_TRACE_MEMORY=1` at boot adds tracemalloc's top sites. Start-up
   also calls `gc.freeze()` after the dataset and quote hub exist.

Rejected by measurement / reasoning:

- **`PYTHONMALLOC=malloc`** (document claims 0.5–1 GB): the same index cost 830 MB
  under `malloc` vs 640 MB under the default `pymalloc`.
- **Per-symbol Parquet depth store + hot/warm/cold tiers**: the depth store is the
  smallest thing in the process; the tiers would add a code path and buy nothing.
- **`uvicorn --limit-max-requests` recycling**: a worker restart re-reads the snapshot
  and re-streams the SEC ZIPs — it would move memory pressure into CPU.

## Next: apply it live (needs the operator's go)

The code is committed but **not live**: uvicorn still runs the process that built the
unfiltered index. Applying = one restart.

```bash
# record before
awk '/VmRSS/{print $2/1024 " MB"}' /proc/$(pgrep -f 'uvicorn.*8099')/status
sudo -n systemctl restart ztockz
sleep 20 && curl -s localhost:8099/health | python3 -c "import sys,json;print(json.load(sys.stdin)['status'])"
curl -s localhost:8099/health/memory | python3 -m json.tool
# ~15 min later, after the collector's first cycle built the index:
curl -s localhost:8099/health/memory | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['rss_mb'],'MB');print(d['stores'])"
```

Expected: RSS near the snapshot + dataset + filtered index (~1.2–1.6 GB) idle, with the
depth cycle adding the index (640 MB measured) once it runs. A `/health` snapshot
verdict of `ok` right after the restart is the gate; anything else means the restart
went wrong, not the fix.

## Follow-ups, ranked (not done)

1. **Prune rows the mapper can never select** — per `(cik, tag, ddate, qtrs, uom)` only
   the newest `filed` is selected; keeping every restatement multiplies rows. Guard it
   with a golden-diff test: build both indexes over one cached quarter and assert
   `fundamentals_for_cik` returns identical fields for all 6,414 issuers.
2. **Persisting the index — measured 2026-11-07, rejected.** `to_document()` on the real
   index costs **25.8 s and +1,433 MB resident**, dumps to 674 MB of JSON in 6.2 s, and
   leaves a 674 MB file, to save the 233 s the build costs once per restart. A lazy
   per-CIK read is the shape that would pay (it drops the standing 640 MB too).
   `scripts/mem_probe.py --sec-index --save-index <path>` reproduces the numbers.

## Blocked: the stale identity snapshot

`/health` is `degraded` on `snapshot=stale` (350,324 s old, budget 172,800 s). The daily
backup timer writes byte-identical copies; nothing re-generates the snapshot. The crawl
that would: 10,433 symbols − 904 cached `companyfacts` documents = ~9,529 documents at
2.15 MB avg ≈ **20.5 GB**, against **19 GB free**. Options: trim the cache
(`python -m app.engine.ops retention`) and then crawl; refresh identity only (2 requests,
but the file-age check would then be measuring something other than the facts); or make
`/health` age facts by their own `as_of`. Repointing the symlink at an existing backup is
not a fix — the bytes are identical and the warning is correct.

## Pre-existing, unrelated

The full Python suite is **not green on `wave2`** — `test_screen_schema` (stale
`schema/stock_selection_request_v1.json` artifact), `test_screen_filters`,
`test_screen_fundamentals_join`, `test_screen_groups_adversarial`,
`test_screen_premium_tiers`, `test_web_build_state`, and
`test_web_app_mount::test_shell_has_no_third_party_origins` (asserts no `https://` in
`index.html`, while the SEO commit added our own `og:url`/`og:image`). Verified
identical with this change reverted. Fixing them is its own wave.
