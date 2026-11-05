ZTOCKZ ENRICHMENT PLAN — 5 Nov 2026
══════════════════════════════════════════════
CONSTRAINT: 1 subagent at a time, scoped vitest, no full suite, no parallel builds.
Deploy timer OFF. GitHub push unavailable (repo not found). All commits LOCAL.

══════════════════════════════════════════════
BATCH 1 — CRYPTO PAGE ENRICHMENT
══════════════════════════════════════════════
Backend: /market/crypto/meta + /market/crypto/global already live (CoinGecko + DeFiLlama).
Frontend: AltPage.tsx (shared crypto/futures/forex) — add crypto-only columns.

Files: web/src/alt/AltPage.tsx, web/src/alt/alt.ts, web/src/alt/useAlt.ts

  A. Join /market/crypto/meta into alt rows:
     - Logo (<img> from meta image URL, fallback monogram)
     - Market cap (formatted: $1.5T, $48.2B)
     - Rank (#1, #2, …)
     - 7d change (pct_7d from meta)
     - TVL column (visible when sources includes "defillama")

  B. Global pulse strip above table:
     - Total crypto market cap ($2.7T)
     - BTC dominance (59.5%)
     - 24h market cap change
     - Active cryptocurrencies count

  C. Sparkline per row:
     - Fetch /market/chart/{symbol}?window=7d per row (lazy, cache Map)
     - Mini SVG sparkline (like HoverBubble but inline)
     - Colored: up=green, down=red

  D. SectionTitle variant 8 already present — verify
  E. Tests: update src/alt tests (63 existing, add ~10 for meta join)
  F. Verify: npx vitest run src/alt && npm run build

────────────────────────────────────────────────
BATCH 2 — SYMBOL CACHE (FRONTEND)
────────────────────────────────────────────────
  A. web/src/lib/queryCache.ts — stale-while-revalidate cache
     - sessionStorage-backed
     - get(key): return stale immediately, re-fetch in background
     - set(key, data, ttl)
     - Works for: alt data, screener pages, map tiles, groups, insider, news

  B. Wire into existing hooks: useAlt, useMapData, useGroups, useNews, useInsider
     - Each hook reads cache first, renders stale, fetches fresh in background
     - No UI change needed — just faster perceived load

  C. Tests: queryCache.test.ts
  D. Verify: npx vitest run src/lib/queryCache.test.ts

────────────────────────────────────────────────
BATCH 3 — STOCK DETAIL PAGE
────────────────────────────────────────────────
Current: /stock/{symbol} — basic quote + chart.
Enrich with tabs using already-existing backend endpoints:

  A. Tabs: Overview | Financials | Peers | Insider | News
  B. Overview: company name, sector, employees, 52w range, beta (from /engine/profile)
  C. Financials: annual/quarterly statements table (from /engine/statements)
  D. Peers: same-sector companies with market cap (from /engine/peers)
  E. Insider: per-company filings (from /engine/insider)
  F. News: headlines (from /news/{symbol})

Files: web/src/stock/*

────────────────────────────────────────────────
BATCH 4 — NEWS ENRICHMENT
────────────────────────────────────────────────
  A. Finnhub adapter already exists (app/providers/adapters/finnhub.py)
     — Wire into /market/news as additional provider
  B. CoinGecko news for Crypto tab (replace alt table fallback)
     — GET /api/v3/news (keyless, crypto headlines)
  C. MarketWatch RSS for Market Pulse tab
  D. Source icons/logos per outlet

────────────────────────────────────────────────
BATCH 5 — CALENDAR + MAPS EXTRAS
────────────────────────────────────────────────
  A. Calendar: market cap badge per row (join depth store)
  B. Calendar: price + 1d change on hover (via quoteProps, already there)
  C. Calendar: dividends tab (ex_dividend_date from depth store)
  D. Maps: crypto treemap (40 coin by cap)
  E. Maps: futures treemap (25 contracts)
  F. Maps: ETF treemap

────────────────────────────────────────────────
ESTIMASI TOTAL: 5 batch × ~30-60 min per agent = ~3-5 jam
CPU: 1 agent at a time, scoped vitest only, builds at end of each batch.
────────────────────────────────────────────────
BATCH 6 — CRYPTO PER-SYMBOL DETAIL  (done 2026-11-05)
────────────────────────────────────────────────
  Backend:
    A. GET /market/crypto/coin/{symbol} — maps Yahoo sym → Coingecko id
       (COINGECKO_IDS), fetches /coins/{id} (keyless, lean flags), 1h cache.
       Refused/unknown id → null shape, available:false, no invented data.
       [app/api/enrich.py + tests/test_api_crypto_enrich.py: +4 tests]
  Frontend:
    B. fetchCoinDetail + CoinDetailDoc [web/src/alt/cryptoEnrich.ts]
    C. /crypto/{symbol} detail page now shows:
       - About: real Coingecko description + category chips
       - Links & Community: website, X handle, followers, sentiment, reddit
       [web/src/crypto/CryptoDetailPage.tsx + useCoinDetail + 3 tests]

────────────────────────────────────────────────
STATUS: all 6 batches done. Per-symbol crypto detail live-verified:
  ETH-USD / SOL-USD return real name, website, categories, description.

────────────────────────────────────────────────
BATCH 7 — CRYPTO UNIVERSE 40 → 64  (done 2026-11-05)
────────────────────────────────────────────────
  A. +24 coins to CRYPTO (alt.py) + COINGECKO_IDS (enrich.py):
     ZEC DAI UNI SUI TAO PEPE DASH APT PYTH CRV FET XTZ BONK GRT
     RUNE EIGEN WIF IMX DYDX GALA BAND XAI DYM EOS
  B. Price resilience: CoinGecko /coins/markets now ships current_price +
     24h change into the meta map (per_page 50→250 so all 64 are covered).
  C. The 8 coins Yahoo no longer quotes (UNI/SUI/TAO/PEPE/APT/GRT/IMX/XAI)
     are surfaced from Coingecko in the list (AltPage supplement) and fall
     back to meta.current_price on the detail page — so the full 64-render,
     every one with a live number, not just the 56 Yahoo prices.
  D. Frontend ALT_PER 50→70 (covers 64; futures/forex unaffected).
  E. Fix: MEE_SAVED_SCREENS_PATH registered in config_guard allowlist
     (pre-existing guard gap surfaced by the suite).
  Verified live: alt=56 Yahoo rows / meta=64 / 8 supplements priced.
