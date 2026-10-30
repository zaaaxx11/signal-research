# Keyless Price & Depth Stack — Implementation Plan

## Status (2026-10-29, malam)

| Phase | Status | Bukti |
|---|---|---|
| 0 — Config flags | ✅ | `dfd6bc2` |
| 1 — Keyless quote (CNBC + Nasdaq) | ✅ | 4.217/4.217 dalam 13 s |
| 2 — OHLC/technical generik | ✅ | 43 test |
| 3 — SEC bulk + TTM + growth | ✅ | `2e4baf3`, `b70630c`, `9cf8fe4` |
| 3b — Tag bank/utility/IFRS | ✅ | sales 96/100 |
| 3c — FINRA + official | ✅ | `2ddeea` |
| 4 — Integrasi collector | ✅ | `487ec78`, `f7ad52c` |
| 4b — Enrichment | ✅ | split cadence + `POST /market/enrich` (`0f72fb9`) |
| 4c — Egress ladder + rainproxy | ✅ | `e34dc6c`, `5779370`, `fbd0121` |
| 5 — Rollout | ✅ | siklus penuh persist 21:51:59, cursor enrich 500 / news 1.000 |

**Coverage terukur:** fast path 88/116 (4.217, ~5,9 mnt) · dengan enrichment 109/116 (100 simbol) · 4.140/4.217 simbol punya data Yahoo.

**Finviz baseline:** 92/116. Kita di atasnya setelah enrichment.

**Ditutup 2026-10-29 (commit `2ab1484`):**
- backup `depth.json` — `ops backup --extra`, arsip 9.871.113 byte + sha256
- monitoring — `GET /market/depth/stats` (106 field, median 94) + `depth_coverage_floor` warning
- rate limit — 10 req/60 s, 429 + `Retry-After`
- EV multi-class — `shares=` reference; META/BRK-A/BRK-B/GOOGL terisi
- 1.000 simbol — median 108 field, 978/1.000 ≥90 field (97,8%)
- enrichment lewat egress — proxy wired; cursor Yahoo 0 → 500


---

> **For Hermes:** Plan only. Jangan implementasi sebelum operator bilang `gas`. Gunakan `subagent-driven-development`; test RED dulu, parent gate, builder, suite 3×, commit.

**Goal:** Menghapus ketergantungan pada Alpaca/Finnhub/Twelve Data/Polygon dan menjadikan stack keyless sebagai jalur utama: CNBC batch, Nasdaq, SEC, FINRA, SSGA/iShares/NDX, Cboe, RSS. Yahoo tetap boleh menjadi fallback, bukan satu-satunya sumber.

**Architecture:** Pisahkan **price** dan **depth**. Price registry keyless memakai CNBC batch sebagai primer dan Nasdaq sebagai verifikasi/fallback. Depth memakai SEC untuk identitas/fundamental, FINRA untuk short, index resmi untuk membership, Cboe untuk options, RSS untuk news. Yahoo hanya fallback untuk field yang tidak dimiliki sumber resmi; tidak ada API key di jalur default.

**Tech Stack:** Python 3.14, FastAPI, httpx2, pytest, JSON snapshot; CNBC quote webservice, Nasdaq API, SEC bulk financial statement data sets, FINRA, SSGA/iShares/NDX, Cboe, RSS.

---

## 0. Baseline yang sudah terukur

| Hal | Angka | Bukti |
|---|---:|---|
| Universe | 4.217 simbol | `research/probes-1000/universe.json` |
| Baseline Finviz-on | 92/116, median 87 | `/home/ubuntu/ztockz-data/depth.json` |
| Keyless sample 100 | **100/116, median 98** | run stack baru, sebelum full |
| Full Finviz-off valid terakhir | 94/116, median 53 | `depth-off.json` — bukan hasil final |
| CNBC batch 100 | **0,66 s**, 131 KB | `price-benchmark-2026-10-28.md` |
| Nasdaq info 100 | 100/100, **57,35 s**, p50 2,195 s | benchmark |
| Nasdaq historical 100 | 100/100, **60,29 s** | benchmark |
| Yahoo chart 100 | 98/100, **2,54 s** | benchmark |
| Alpaca/Finnhub/Twelve/Polygon | **401 tanpa key** | benchmark |
| Test suite | **3234 passed ×3** | commit `1a5c242` |

### 0.1 Hasil ukur bulk path (2026-10-28)

| Jalur | Cakupan | Wall | Hasil |
|---|---:|---:|---|
| **CNBC batch** | 4.217 quote | **5,52 s** | 9/9 batch 200, 5,2 MB |
| **Yahoo chart** | 4.217 OHLC 1y | **23,68 s** | 4.199/4.217 200; 18×404 |
| **SEC bulk 2026q3** | fundamental | **2,58 s download + 11,29 s parse** | 65,1 MB ZIP; 3.905.397 baris `num.txt` |
| **Yahoo options** | 4.217 optionable | **gagal** | 2.461×401, 1.756×429 |
| FINRA short | 5 request | ~4 s | dari run sebelumnya |
| Index official | 6 file | ~10 s | dari run sebelumnya |

**Kesimpulan:** core keyless (quote + OHLC/technical + SEC fundamental + short + index) **< 1 menit**. `optionable` per simbol tidak masuk fast path; news/analyst/ownership juga di luar fast path.

**Keputusan operator:** API key provider mahal/tidak worth it. Default keyless; provider berkey menjadi optional, bukan dependency.

---

## 1. Target

### 1.1 Produk

```text
MEE_PRICE_KEYLESS=1        # default
MEE_DEPTH_YAHOO_FALLBACK=1 # Yahoo boleh fallback, bukan primary
```

- Tidak ada request ke Alpaca/Finnhub/Twelve/Polygon di jalur default.
- Tidak ada key yang dibaca saat startup default.
- `GET /market/depth` tetap 200.
- **Fast path 4.217 selesai ≤5 menit**: CNBC quote + Yahoo chart OHLC + SEC bulk + FINRA + index; error ≤1%; partial persist.
- **Enriched path (opsional) ≤60 menit**: tambahkan news/optionable/estimate yang memang per simbol; jangan blokir fast path.
- 100 simbol fast path: ≥95/116 kolom; full fast path: ≥90/116; enriched: ≥94/116. Field quality dicatat, tidak diklaim penuh.

### 1.2 Non-goals

- Consolidated real-time tape berlisensi.
- Analyst consensus lengkap.
- Redistribusi publik tanpa lisensi.
- Menghapus adapter berkey dari repo; cukup tidak dipakai default.

---

## 2. Source matrix keyless

| Kebutuhan | Primer | Fallback | Key? | Catatan |
|---|---|---|---|---|
| Identitas | **SEC** | — | tidak | official |
| Fundamental | **SEC bulk financial statements** | SEC companyfacts | tidak | bulk kuartalan 60–120 MB |
| Harga/quote | **CNBC batch** | Nasdaq per simbol | tidak | unofficial; CNBC 100/0,66 s |
| OHLC/technical | **Yahoo chart** | Nasdaq historical | tidak | Yahoo 98/100; Nasdaq 100/100 tapi lambat |
| Short interest | **FINRA** | — | tidak | settlement date |
| Index membership | **SSGA + iShares IWM + NDX** | — | tidak | S&P 400/500/600, DJIA, R2000, NDX |
| Options | **Cboe** | Yahoo options | tidak | Cboe 200/403 |
| News | **Yahoo RSS** | Nasdaq RSS | tidak | fallback |
| Analyst estimates | Yahoo/Nasdaq | — | tidak | coverage terbatas |
| Institutional ownership | Nasdaq/13F | Yahoo | tidak | lag 13F |

**Aturan:** keyless ≠ open data. CNBC/Yahoo/Nasdaq tetap unofficial dan punya risiko ToS/rate.

---

## 3. Phase 0 — Inventory dan flag (0,5 hari)

### Task 0.1: Audit sumber berkey

**Files:**
- Create: `research/keyless-source-audit.md`
- Read: `app/providers/registry.py`, `app/providers/adapters/*`, `app/market/depth.py`

**Steps:**
1. Daftar setiap adapter yang membaca env key.
2. Tandai mana yang dipakai default dan mana optional.
3. Tulis tabel: adapter → env → dipakai default? → cara mematikan.

**Verify:** grep `API_KEY` di `app/` dan pastikan daftar lengkap.

### Task 0.2: Config flags

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_market_depth.py`

```python
price_keyless: bool = True       # MEE_PRICE_KEYLESS
depth_yahoo_fallback: bool = True  # MEE_DEPTH_YAHOO_FALLBACK
```

**Tests:**
- env `MEE_PRICE_KEYLESS=0` → False.
- default True; tidak ada key yang diperlukan untuk startup.

### Task 0.3: Default registry tanpa key

**Files:**
- Modify: `app/providers/registry.py`
- Test: `tests/test_registry.py`

**Rule:** dengan `price_keyless=True`, `FinnhubAdapter`, `PolygonAdapter`, Alpaca/Twelve (jika ditambah) tidak masuk default chain. Adapter tetap bisa dipakai eksplisit.

**Verify:** fake key absent; startup health 200; no 401.

---

## 4. Phase 1 — Keyless quote registry (1 hari)

### Task 1.1: CNBC batch source untuk price

**Files:**
- Modify: `app/market/bulk.py` (jika perlu adapter)
- Create: `app/market/price.py`
- Test: `tests/test_market_price.py`

**Contract:**
```python
class PriceQuote:
    symbol: str
    last: float
    prev_close: float | None
    change_pct: float | None
    volume: float | None
    as_of: datetime
    source: str

class CnbcPriceSource:
    name = "cnbc"
    async def fetch_many(symbols) -> dict[str, PriceQuote]
```

**Tests:**
- parse batch response; symbol normalization; missing symbol absent; malformed quote skipped; no label in public output.
- 100 symbols in one request; status 200.

**Live check:** 100 symbols; expected <2 s, ≥98 success.

### Task 1.2: Nasdaq fallback quote

**Files:**
- Modify: `app/market/price.py`
- Test: `tests/test_market_price.py`

**Rule:** only fill symbols missing from CNBC; one request per symbol; timeout 20 s; no retry storm. Do not make Nasdaq primary for 4.217.

**Tests:**
- CNBC covers 90; Nasdaq only called for 10.
- Nasdaq 429/500 → missing, not zero.
- no key required.

### Task 1.3: Price benchmark 100/1.000

**Files:**
- Create: `research/keyless-price-bench.md`
- Create: `scripts/price_bench.py`

**Measure:** wall, p50/p95, success, bytes, missing, CNBC vs Nasdaq counts.

**Gate:** 100 symbols ≥98% success, ≤5 s; 1.000 symbols ≤60 s if batch works; if not, document.

---

## 5. Phase 2 — Keyless OHLC/technical (2 hari)

### Task 2.1: Nasdaq historical adapter

**Files:**
- Modify: `app/providers/adapters/nasdaq.py`
- Test: `tests/test_market_price.py`

**Endpoint:**
```text
/api/quote/{T}/historical?assetclass=stocks&fromdate=YYYY-MM-DD&limit=260
```

**Parse:** Date, Close/Last, Volume, Open, High, Low; strip `$`/commas; aware dates; EOD only.

**Tests:** valid row; missing field; 404; 429; empty; symbol normalization; no key.

### Task 2.2: Compute technicals from generic OHLC

**Files:**
- Create: `app/market/history.py`
- Test: `tests/test_market_history.py`

Refactor formula dari `app/market/chart.py` agar bisa menerima `list[Bar]`, bukan hanya Yahoo entry. Formula tetap:

- perf 5/21/63/126/252 bar
- SMA20/50/200 sebagai persen
- RSI Wilder 14, ATR 14
- volatility = rata-rata `(high-low)/close`
- high/low 52w, week_52_range
- average_volume 65 sesi, relative_volume
- gap/change_from_open
- dividend growth 1y/3y/5y (jika ada event; Nasdaq tidak punya event → absent)

**Tests:** pakai fixture bar yang sama dengan `test_market_chart.py`; hasil harus identik untuk input bar yang sama.

### Task 2.3: Yahoo chart fallback

**Files:**
- Modify: `app/market/depth.py`
- Test: `tests/test_market_depth.py`

**Rule:** Yahoo chart tetap source opsional; Nasdaq history menjadi fallback pertama untuk technical. Jangan biarkan Yahoo menjadi satu-satunya produsen `perf_*`, `sma*`, `rsi`.

**Tests:** collector dengan chart disabled + Nasdaq history → fields tetap ada; Yahoo failure tidak mengosongkan technical.

---

## 6. Phase 3 — SEC bulk fundamental (2 hari)

### Task 3.1: Download dan cache SEC bulk

**Files:**
- Create: `app/engine/bulk_financials.py`
- Test: `tests/test_engine_bulk_financials.py`

**Source:**
```text
https://www.sec.gov/files/dera/data/financial-statement-data-sets/2026q3.zip
```

**Steps:**
1. Download sekali per kuartal; cache di `MEE_ENGINE_CACHE_DIR`.
2. Extract `sub.txt`, `num.txt`, `pre.txt`.
3. Parse `adsh`, `cik`, `tag`, `ddate`, `qtrs`, `value`, `uom`.
4. Index per CIK; jangan load semua ke memori tanpa batas.
5. Simpan snapshot bulanan, bukan per request.

**Tests:** fixture ZIP kecil; duplicate tags; missing unit; numeric comma; quarterly vs annual (`qtrs=4`); no network.

### Task 3.2: Map ke kolom katalog

**Files:**
- Create: `app/market/sec_fundamentals.py`
- Test: `tests/test_market_sec_fundamentals.py`

**Target fields:** `sales`, `net_income`, `eps`, `gross_margin`, `operating_margin`, `net_margin`, `roa`, `roe`, `current_ratio`, `quick_ratio`, `debt_to_equity`, `book_per_share`, `cash_per_share`, `dividend`, `shares_outstanding`.

**Rules:**
- `qtrs=4` untuk TTM; annual untuk FY.
- `eps` = diluted EPS.
- `market_cap` = price × shares; price from keyless quote.
- `pe` = price / EPS; no price → absent.
- Jangan memetakan tag yang tidak ada; absen lebih baik.

**Tests:** AAPL fixture; negative EPS; missing tag; duplicate restatement; unit USD vs shares.

### Task 3.3: Bulk vs 4.217 companyfacts

**Decision:** Jangan crawl `companyfacts` 4.217 CIK harian. Pakai bulk quarterly + cache; companyfacts hanya untuk audit/verifikasi per simbol.

**Verify:** full bulk parse ≤5 menit; tidak ada request per simbol.

---

## 7. Phase 4 — Compose depth keyless (1 hari)

### Task 4.1: Source order

**Files:**
- Modify: `app/market/depth.py`
- Test: `tests/test_market_depth.py`

**Default keyless order:**
```text
1. SEC identity
2. SEC bulk fundamentals
3. CNBC/Nasdaq price (separate price store; not depth)
4. FINRA short
5. SSGA/IWM/NDX index
6. Cboe optionable
7. Nasdaq history / Yahoo chart technical
8. RSS news
9. Yahoo full fallback (opsional, flag)
```

**Rule:** `MEE_PRICE_KEYLESS=1` tidak boleh membuat `FinvizDepthSource` atau adapter berkey dipanggil. `YahooFull` hanya jika `MEE_DEPTH_YAHOO_FALLBACK=1`.

### Task 4.2: A/B

**Files:**
- Modify: `scripts/depth_compare.py` (jika perlu)
- Create: `research/keyless-depth-bench.md`

**Runs:**
```bash
MEE_PRICE_KEYLESS=1 MEE_DEPTH_FINVIZ=0 \
MEE_DEPTH_STORE_PATH=/home/ubuntu/ztockz-data/depth-keyless.json ...
```

Compare against Finviz-on baseline; 100 → 1.000 → 4.217.

### Task 4.3: Acceptance gate

```text
[ ] 0 request ke Alpaca/Finnhub/Twelve/Polygon
[ ] 0 key dibaca saat startup default
[ ] 100 simbol >=100/116
[ ] 1.000 simbol >=95% symbol coverage
[ ] 4.217 selesai <=60 menit
[ ] error <=1% chunk
[ ] /market/depth 200
[ ] partial persist
[ ] tidak ada Yahoo-only field group
```

Jika gagal: jangan flip; tulis gap di `research/keyless-depth-bench.md`.

---

## 8. Phase 5 — Rollout dan rollback (0,5 hari)

- Set service:
```bash
MEE_PRICE_KEYLESS=1
MEE_DEPTH_FINVIZ=0
MEE_DEPTH_YAHOO_FALLBACK=1
MEE_DEPTH_SOURCE_TIMEOUT_S=180
MEE_DEPTH_SOURCE_CHUNK_SIZE=250
```
- Restart; verify `/market/depth` 200 and no keyed host in logs.
- Rollback: `MEE_PRICE_KEYLESS=0` + existing stack; no code revert.
- Keep key adapters in repo but disabled; delete only after 7 days stable.

---

## 9. Risks

| Risiko | Dampak | Mitigasi |
|---|---|---|
| CNBC unofficial/rate | quote gap | batch, cache, Nasdaq fallback |
| Nasdaq lambat | full run >60m | jangan Nasdaq untuk full quote; hanya fallback |
| Yahoo hang | technical gap | Nasdaq history fallback; per-symbol timeout |
| SEC bulk stale | fundamental lag | cache kuartal; `as_of` |
| SEC tag drift | missing fields | fixture tests; unknown tag absent |
| ToS | legal | internal only; public gate tetap |
| Keyless ≠ open | salah klaim | dokumentasi jujur |
| Full 4.217 memory | OOM | streaming/chunk, no full zip in RAM |

---

## 10. Open decisions

1. **Yahoo fallback boleh?** Recommended yes, tapi bukan primary.
2. **`pfcf`** — tetap `definition`/omit; jangan paksa parity.
3. **`shortable`** — `unknown`; tidak ada keyless source.
4. **`eps_next_5y`/`revenue_surprise`** — unknown; jangan proxy.
5. **CNBC vs Nasdaq quote primary** — CNBC batch; Nasdaq fallback.
6. **Full 4.217 target** — 94/116 realistic; 108 hanya jika Yahoo fallback masih dipakai.

---

## 11. Deliverables

- `app/market/price.py` — keyless price registry.
- `app/market/history.py` — OHLC technical formulas.
- `app/providers/adapters/nasdaq.py` — historical bars.
- `app/engine/bulk_financials.py` + `app/market/sec_fundamentals.py`.
- Updated `app/market/depth.py`, `app/config.py`.
- Tests: `test_market_price.py`, `test_market_history.py`, `test_engine_bulk_financials.py`, `test_market_sec_fundamentals.py`.
- Benchmarks: `research/keyless-price-bench.md`, `research/keyless-depth-bench.md`.

**Definition of done:** full 4.217 berjalan tanpa satu pun API key provider, tanpa Finviz, dengan CNBC/Nasdaq/SEC/FINRA/index/Cboe/RSS; Yahoo hanya fallback; `/market/depth` tetap 200.