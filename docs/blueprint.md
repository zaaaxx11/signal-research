# Ztockz — pipeline blueprint (sanitized)

The reader sees the product, never the supply chain: this document describes
**what each source contributes**, not who it is. Upstream names live in the code
(`app/providers/adapters/<source>.py`) and in the operator's local note — never
in anything a client can read, and never here.

## Data sources — by role

| Role | What it supplies |
|---|---|
| Screener feed | The listed universe and its fundamental columns (US stocks + ETFs) |
| Chart feed | OHLC bars; also the crypto, futures and forex listing quotes |
| Crypto metadata | Market cap, rank, supply, logo, ATH, 1h/7d/30d changes |
| DeFi TVL | Total value locked per protocol |
| Filings | Insider transactions; company facts for fundamentals and statements |
| News wire | Headline merge across desks |
| Depth overlay | The screener's deep per-symbol columns (research mapping) |
| Fallback quotes | Per-symbol quotes when the primary feeds thin out |

All are keyless or free-tier; every adapter is injected and testable.

## Screener pipeline

```
screener listing ──► dedupe ──► issuers
      │
      ▼
Universe build ──► depth overlay (data/depth.json)
      │
      ▼
Fundamentals joins (SQLite: P/E, P/S, insider ownership)
      │
      ▼
Filter DSL ──► buckets ──► signals
      │
      ▼
Rank (one metric + symbol tie-break, nulls last)
      │
      ▼
Project ≤40 columns / 100 rows / 60 KB JSON
      │
      ▼
200 OK + weak ETag (from the input, before the answer is built)
```

## API surface

- `/screens/select` — screener: filter → rank → project
- `/market/map` — map tiles: `{s,c,v,n,p}` + timeframe 1d/1w/1m/3m/6m/1y
- `/market/groups` — sector/industry summaries with performance columns
- `/market/home` — the 13 home blocks: indices, breadth, movers, map, news, insider
- `/market/chart/{symbol}` — OHLC bars, range + interval, 5 minute cache
- `/market/alt?class=crypto|futures|forex` — listing quotes, 60 s cache
- `/market/crypto/meta` — cap, rank, supply, logo, TVL (5 min)
- `/market/crypto/global` — total cap, dominance, active coins (5 min)
- `/market/insider` — insider trading: symbol, owner, shares, value
- `/market/news` — headlines: title, source, symbols (15 min)
- `/market/calendar` — earnings dates per day (depth store)
- `/market/quotes` — batch lookup, ≤50 symbols
- `/engine/snapshot/{symbol}` — the 84-cell stock detail
- `/engine/statements/{symbol}` — 10 annual + 8 quarterly, three statements, TTM

## UI pages (React + Vite, `web/`)

Home · Screener · ETF · Maps (treemap/matrix/bubble/grid) · Charts (6 timeframes)
· News (5 tabs) · Groups · Insider (3 tabs) · Futures · Forex · Crypto ·
Calendar · Watchlist · Portfolio.

## Shared pattern

1. **Fetch** — a provider adapter (injected, testable)
2. **Parse** — strict: `null ≠ 0`; a missing symbol is counted, not zeroed
3. **Cache** — in-memory dict, TTL 60 s – 5 min, keyed by input parameters
4. **ETag** — weak validator from the *input*, so a 304 costs no fetch
5. **Serve** — JSON + `Cache-Control: no-cache` + `ETag`

**Rule:** empty data is not a row of zeros. A failed symbol is absent from the
rows and still counted in the universe. No number is ever invented.

## Stack

- **Backend** — Python 3.14, FastAPI, SQLite, uvicorn (1 worker, port 8099)
- **Frontend** — React 19, Vite, Tailwind v4, lightweight-charts, canvas
- **Infra** — systemd (`ztockz.service`), cloudflared tunnel, GitHub deploy timer
- **Data** — the feeds above, all keyless or free-tier; the depth store is the
  only derived artifact that ships with the service

## Keeping it clean

`tests/test_docs_no_upstream_names.py` fails if this file (or the README) names
an upstream, and the footer guard in `web/src/shell/SiteFooter.test.tsx` does the
same for the served UI. Add a contribution to a source's role in the table above,
never its name.
