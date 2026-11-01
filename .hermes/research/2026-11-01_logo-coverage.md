# Logo coverage — 2026-11-01

**4,957 of 5,352 screen symbols resolve to a real logo (92.62%), up from 376
(7.03%).** The disk backfill is live; the new provider rungs need a restart.

## Universe
The live US tab scans ~5,576 symbols; offline that is `depth.json` (4,217 rows,
cap + website) plus the US non-OTC listings in `engine-snapshot.json` it has not
reached — **5,352**, the script's default (`--universe FILE` overrides it).

## Providers measured
| provider | address | measured |
|---|---|---|
| parqet | `assets.parqet.com/logos/symbol/{SYM}?format=png` | keyless, 100x100 PNG, 404 on unknown, 79% of pilot |
| fmp | `images.financialmodelingprep.com/symbol/{SYM}.png` | keyless, 100/250px, 404 on unknown, covers most parqet misses |
| google | `google.com/s2/favicons?domain={DOMAIN}&sz=128` | keyless, real but often a 32px upscale — last rung |
| clearbit | `logo.clearbit.com/{DOMAIN}` | **dead**: no A record, confirmed via DNS-over-HTTPS; off by default |

Both 404 rather than return a placeholder for invented tickers (`ZZZZQQ`, `2330`,
`0700`); no placeholder hash among 4,965 downloads (CEPS/CEPV/CEPF are one issuer).

## Pilot — 200 symbols, stratified across market-cap deciles (seed 42)
| provider | tried | real >=48px | small | 404 | error |
|---|---|---|---|---|---|
| parqet | 200 | 158 | 0 | 48 | 0 |
| fmp | 42 | 33 | 0 | 11 | 0 |
| google | 4 | 3 | 0 | 1 | 0 |

**17/200 before (8.5%) -> 194/200 after (97.0%)**, 6 unresolved, 16.0 s.

## Full backfill — 5,352 symbols
| provider | tried | real | small | 404 | error | breaker |
|---|---|---|---|---|---|---|
| cache (already real) | — | 376 | — | — | — | — |
| parqet | 4,976 | 3,497 | 0 | 1,674 | 0 | 0 |
| fmp | 1,479 | 1,022 | 4 | 578 | 0 | 0 |
| google | 84 | 62 | 8 | 14 | 0 | 0 |

**7.03% -> 92.62%**, 413.8 s, 13.0 symbols/s. `404` counts responses (a class
share spelled twice, `BRK.B` -> `BRK-B`, can exceed "tried"). Residual 395: 270
US micro-caps/SPACs with no mark anywhere, 125 venue-suffixed foreign listings no
symbol provider carries; only 22 have a website. Store: 511 -> 4,965 files
(4,958 real, 7 small), 20.7 MB of the 33.5 MB budget.
## Commands
    .venv/bin/python scripts/logos-backfill.py --sample 200 --dir /tmp/logo-pilot --report /tmp/pilot-after.json
    .venv/bin/python scripts/logos-backfill.py --dir /home/ubuntu/ztockz-data/logos --report .hermes/research/2026-11-01_logo-coverage.json
    .venv/bin/python scripts/logos-backfill.py --measure-only --dir /home/ubuntu/ztockz-data/logos

Resumable (>=48px files skipped), concurrency 4, timeout 8 s, breaker at 8 failures.
## Wiring and tests
`app/api/logos.py`: store -> ui/logos -> parqet -> fmp -> google -> monogram,
bounded by `LADDER_BUDGET_S = 12`. `tests/test_api_logos.py` (61) +
`tests/test_logos_backfill.py` (35) + `tests/test_branding_logos.py` (11) =
**107 passed in 2.86s**. Proved: cached file wins with zero fetches; no cache and
no domain still answers 200 with a monogram (never 404); parqet precedes the
domain rung; fall-through to google works; the breaker stops a dead provider;
32px/HTML answers are never cached.

Not done: no request to port 8099, no `ztockz.service` restart, no commit, no
scrape of a site that forbids it (only public logo endpoints).
