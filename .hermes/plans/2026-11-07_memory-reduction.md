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

## Done (C, second pass): the index keeps only rows the mapper can read

Two filters, both proven against the raw index over the production window (four
cached quarters, the snapshot's CIK set, 6,385 issuers):

1. **Duplicates** — one survivor per `(tag, version, ddate, qtrs, uom, segments,
   coreg)`, the newest `filed` (restatement history the mapper never reads).
   Measured alone: rows 2,696,463 → 2,279,617 (−15.46%), index RSS −17.6 MB.
   **Marginal on its own** — the strings were already interned, so the rows paid
   for themselves mostly in allocator noise.
2. **Dimensions** — `sec_fundamentals._rows_for` skips a row whose `segments` is
   non-empty, and **63.85%** of one quarter's mapped rows are dimensional
   (502,194 of 786,489). The depth source now builds with `undimensioned=True`.

Combined, measured with `scripts/mem_probe.py --sec-index`:

| | before | after |
|---|---|---|
| rows (4 quarters) | 2,696,463 | **813,175** (−69.84%) |
| SEC bulk index RSS | 640 MB | **227.6 MB** |
| mapped-field mismatches | — | **0** over 6,385 issuers × 32 fields |
| rows invented | — | **0** |

Proof tool: `scripts/facts_prune_diff.py` (multi-quarter, production shape, exit 0
only when identical). Tests: 224 targeted passed.

## Done (A): facts crawl finished — the whole health check is green

`scripts/refresh-facts.py` crawled all 4,197 served symbols in 41 min (chunked at
25, disk gate at 4 GB, writing to a NEW file):
`{facts_symbols: 4197, errors: 0, cells: 85,777, verified: true, free_gb 18.66 → 11.33}`.

Coverage vs the previous snapshot: **facts lost 0**, errors 13 → 0, cells +85.
33 symbols lost their submissions enrichment in the crawl (Hilton, MGPI, CCXI…),
so the swap carried `country`/`sector`/`industry` forward from the older snapshot
(34 records; country 5,381 → 5,393, industry 5,371 → 5,380, facts asserted
identical). Swapped the symlink + restarted.

Live after the swap, with the index built (813,175 facts / 6,380 issuers):

- **`/health` = ok** — all five checks green; clocks `identity_age_s=62`,
  `facts_age_s=305`. The degraded verdict that opened this work is gone.
- **RSS 575 MB** (cgroup 590 MB, peak 605 MB, swap 0) — from **6,889 MB** at the
  start, −91.7%.
- `/screens`, `/engine/snapshot/AAPL`, `/market/exchanges`, UI: all 200.

## Follow-ups, ranked (not done)

1. **Columnar index (the real ceiling).** Census of the pruned index: 813,175
   rows, all float values; distinct values are tiny (37 tags, 62 versions, 142
   period ends, 31 units, 22,274 accessions, 68,568 segments→0 kept). Object cost
   is now the floor: `Fact` slots ≈ 112 MB + float objects ≈ 19 MB of the 227.6 MB
   build. Storing value as an array and tag/unit/date as small int codes into the
   shared vocabularies would cut most of that, but every consumer reads through
   `facts_for()`/`map_fundamentals`, so it is a real refactor with a golden-diff
   harness already in place (`scripts/facts_prune_diff.py`).
2. **`--carry-enrichment-from` on `scripts/refresh-facts.py`.** The 2026-11-07
   swap needed a separate carry pass (34 records) because a chunked crawl's
   submissions fetch can skip or fail per issuer. Fold it into the script so the
   next refresh cannot lose `country`/`sector`/`industry` at all.
3. Rejected by measurement, kept here so nobody re-tries them: persisting the
   index as JSON (`+1,433 MB` transient for a 233 s CPU saving), and
   `PYTHONMALLOC=malloc` (830 MB vs 640 MB on the same index).

## Done (B): identity refreshed, both clocks on /health

- New command: `python -m app.engine.ops refresh-identity --snapshot <file> [--output <new>]`.
  Live run: 10,433 → 10,409 symbols (44 added, 68 delisted), 4,197 facts carried
  **byte-identically**, 5,454 records' country/industry carried, 0 conflicts, one request.
- Swapped via the symlink + restart; `/symbols/search`, `/screens`, `/engine/snapshot/AAPL`
  and the UI all answer 200; RSS 1,115 MB with the SEC index built (up from 1,016 MB
  because the refreshed file is written with `indent=2`: 40 MB vs 23 MB).
- `/health` now ages **two** clocks and names the stale one:
  `degraded | snapshot: stale | age_s=18739 | facts_age_s=461908 |
  reason: facts last read 461908s ago (budget 172800s)`. Identity is fresh; the facts
  are honestly old — that is the state until the crawl in (A) can run.

Pitfall found the hard way: rebuilding identity from the bulk document alone deleted
`country`/`sector`/`industry` (they come from the per-CIK submissions pass — country
5,389 → 0). The refresh now carries them; `enrichment_carried` in the report is the
check. Do not repoint the symlink at an older byte-identical backup to silence the
warning: the bytes are the same and the warning is correct.

## Not done (A): the facts crawl is still disk-blocked

10,433 symbols − 904 cached `companyfacts` documents ≈ 9,529 documents at 2.15 MB avg
≈ **20.5 GB**, against **19 GB free**. Options: trim the cache
(`python -m app.engine.ops retention`), then crawl; or accept the degraded verdict.

## Pre-existing, unrelated

The full Python suite is **not green on `wave2`** — `test_screen_schema` (stale
`schema/stock_selection_request_v1.json` artifact), `test_screen_filters`,
`test_screen_fundamentals_join`, `test_screen_groups_adversarial`,
`test_screen_premium_tiers`, `test_web_build_state`, and
`test_web_app_mount::test_shell_has_no_third_party_origins` (asserts no `https://` in
`index.html`, while the SEO commit added our own `og:url`/`og:image`). Verified
identical with this change reverted. Fixing them is its own wave.
