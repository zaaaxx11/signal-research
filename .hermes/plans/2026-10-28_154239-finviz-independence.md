# Finviz Independence Implementation Plan

## Status (2026-10-29)

Plan **dieksekusi dan Finviz sudah dimatikan** di produksi (`MEE_DEPTH_FINVIZ=0`,
`MEE_DEPTH_YAHOO_FULL=0`, `MEE_DEPTH_STOCKANALYSIS=0`, enrichment sebagai rung
utama). Gate 5.3 diukur ulang pada store 4.217 simbol: dua kriterianya memakai
denominator yang salah — bukan kode yang salah. Revisi ada di §5.3a.

| Phase | Status |
|---|---|
| 0 — guardrails + flags | ✅ |
| 1 — Yahoo quoteSummary + timeseries | ✅ |
| 2 — chart 11y | ✅ |
| 3.1 — FINRA short interest | ✅ |
| 3.2 — index membership | ✅ match rate 100% |
| 3.3 — Cboe optionable | ⚠️ diganti Yahoo options (Cboe 429) |
| 3.4 — Nasdaq verification | ⛔ keluar scope, keputusan di §Task 3.4 |
| 3.5 — SEC 5Y growth | ✅ CAGR 3y/5y di `sec_fundamentals` |
| 4 — RSS news + dedupe | ✅ |
| 5.1/5.2 — A/B + compare report | ✅ |
| 5.3 — acceptance gate | ✅ setelah revisi §5.3a |
| 5.4 — flip + rollback | ✅ |

**Coverage terukur:** 106/116 kolom pada 4.217 simbol, median 96 field/simbol.

---

> **For Hermes:** Use `subagent-driven-development` to implement this plan task-by-task. Every code task is RED-first: a tests-only subagent writes the failing test, the parent verifies the failure is `AssertionError`/`NotImplementedError` (not `ImportError`), commits RED, then a builder is dispatched.

**Goal:** Menghapus Finviz sebagai sumber wajib depth collector, menggantinya dengan Yahoo + sumber resmi (FINRA, SSGA/Nasdaq, Cboe, SEC, RSS), dan mempertahankan atau menaikkan coverage tanpa mengubah kontrak publik `GET /market/depth`.

**Architecture:** Setiap sumber hanya menerbitkan field yang menjadi tanggung jawabnya (`field_owners`), sehingga tidak ada perang prioritas antar sumber. `DepthCollector` tetap memanggil semua sumber pada setiap siklus dan `DepthStore` tetap merge per field. Finviz tetap ada di belakang flag `MEE_DEPTH_FINVIZ` selama satu rilis sebagai rollback, lalu dihapus dari jalur produksi setelah A/B penuh 4.217 simbol lulus.

**Tech Stack:** Python 3.14, FastAPI, httpx2, pytest, JSON snapshot store; Yahoo Finance, FINRA consolidated short interest, SSGA/Nasdaq index constituents, Cboe options endpoint, SEC identity, Yahoo/Nasdaq RSS.

---

## 0. Verified baseline (jangan diubah tanpa alasan)

| Item | Nilai | Bukti |
|---|---:|---|
| Universe | 4.217 simbol | `research/probes-1000/universe.json` |
| Current full depth | 92/116 kolom | `/home/ubuntu/ztockz-data/depth.json`, 2026-10-28 14:22 UTC |
| Median field | 87 | hitung dari file yang sama |
| Semua simbol hadir | 4.217/4.217 | canonical symbol, termasuk `BRK-B` |
| Store size | 6,7 MB | `depth.json` |
| Current suite | 2.913 passed ×3 | HEAD `6fcec9e` |
| No-Finviz baseline | 34/116, median 37 | observasi dari checkpoint SA+Yahoo+SEC |

Riset yang menjadi dasar plan:

- `research/finviz-off-yahoo.md` — Yahoo 108/116, 94 A+B, exact mapping dan biaya.
- `research/finviz-off-nasdaq.md` — FINRA/SSGA/Nasdaq/Cboe, 8 field pengganti terverifikasi.
- `research/finviz-off-derived.md` — 20 field chart, formula, RSS, integration audit.

**Batasan penting:** angka 108/116 berasal dari 100 simbol, bukan run penuh 4.217. Plan ini tidak boleh mengklaim angka produksi sebelum A/B penuh di Phase 5.

---

## 1. Target yang harus lulus

### 1.1 Coverage target

| Metrik | Target | Catatan |
|---|---:|---|
| Kolom terisi | **108/116** | Yahoo stack; bukan 116 |
| Exact/equivalent (A+B) | **94/116** | definisi aman |
| Definition mismatch (C) | 11 | perlu keputusan produk/internal |
| Non-comparable (N) | 3 | jangan dipublikasikan sebagai field yang sama |
| Benar-benar tidak ada (D) | 8 | `shortable`, `eps_past_5y`, `eps_next_5y`, `sales_past_5y`, `revenue_surprise`, `daily_digest`, `sort_order`, `row_type` |
| Simbol hadir | 4.217/4.217 | sama dengan baseline |
| Error sumber | ≤1% simbol | satu simbol gagal tidak boleh membunuh batch |
| Siklus penuh | ≤60 menit | ESTIMATE; Yahoo 2 worker ~40 menit |
| Store | ≤12 MB | masih disk-efisien untuk snapshot harian |

Catatan: 108 adalah batas atas bila 11 field C tetap diterbitkan dengan metadata definisi. Jika `pfcf` diputuskan tidak dipublikasikan (open gate §9), target quality-safe menjadi **107/116**; angka itu harus dilaporkan, bukan disembunyikan.

`eps_past_5y` dan `sales_past_5y` hanya dapat ditutup lewat SEC `companyfacts`; itu pekerjaan opsional yang mahal. Jangan menjanjikan 110/116 sebelum crawl facts terbukti.

### 1.2 Quality target

- 94 field A+B harus punya nilai yang secara definisi setara; deviasi dicatat di laporan A/B.
- 11 field C tidak boleh disebut “sama dengan Finviz”. Pilih satu definisi kanonik, simpan metadata internal, dan jangan campur dengan field berlabel lain.
- 3 field N (`eps_next_y`, `short_interest`, `relative_volume`) jangan diambil dari Yahoo jika satuannya berbeda; FINRA/chart menjadi sumbernya.
- 8 field D tetap kosong atau `unknown`; jangan mengisi dengan proxy yang menyesatkan.

---

## 2. Source ownership map

| Kelompok field | Sumber utama | Sumber verifikasi | Alasan |
|---|---|---|---|
| `ticker`, `company`, `exchange`, `country`, `industry`, `sector`, `security_type` | SEC identity | Yahoo | SEC official, sudah ada |
| Valuation/fundamental (`market_cap` … `roic`) | Yahoo quoteSummary + timeseries | SEC facts | 94 A+B |
| Performance/technical (`perf_*`, `sma*`, `rsi`, `atr`, `volatility_*`, `high/low_52w`) | Yahoo chart 10y | — | formula terverifikasi |
| `short_interest`, `short_ratio`, `short_float` | FINRA consolidated | Nasdaq | FINRA seluruh pasar; Nasdaq hanya Nasdaq-listed |
| `index_membership` | SSGA SPY/MDY/SPSM/DIA + Nasdaq NDX | — | Yahoo hanya NDX/DJI |
| `optionable` | Yahoo options endpoint | Cboe HEAD | Yahoo 100/100; Cboe 98/99 |
| `target_price`, `recommendation` | Yahoo | Nasdaq target/ratings | Yahoo A/B; Nasdaq definisi kategorik |
| `earnings_date` | Yahoo calendarEvents | Nasdaq earnings calendar | Yahoo berikutnya; Nasdaq calendar lebih maju |
| `news_*` | Yahoo RSS + Nasdaq RSS | — | 313 item/10 simbol, 0 cacat; dedupe judul |
| `shortable` | — | — | tidak ada sumber gratis; `unknown` |
| `eps_next_5y`, `revenue_surprise` | — | — | Yahoo sudah menghapus periodenya |
| `daily_digest` | internal (future) | — | fitur, bukan data source |
| `sort_order`, `row_type` | internal UI | — | bukan data pasar |

### 2.0 Source budget (ESTIMATE)

| Sumber | Request untuk 4.217 | Catatan |
|---|---:|---|
| Yahoo quoteSummary | 4.217 | 14 modul, satu crumb per batch |
| Yahoo fundamentals-timeseries | 4.217 | 4 FY + 5 kuartal |
| Yahoo chart 10y | 4.217 | ~71 KB gzip/simbol |
| Yahoo options | 4.217 | hanya untuk `optionable` |
| Yahoo/Nasdaq RSS | 8.434 | 2 feed/simbol, dedupe |
| FINRA short interest | 5 | seluruh pasar per settlement |
| SSGA + NDX index | ~5 | file harian |
| **Total** | **~25.300** | 40–60 menit pada 2 worker; 230–370 MB |

### 2.1 Field ownership registry

Buat `app/market/depth_fields.py`:

```python
from dataclasses import dataclass
from typing import Literal

Quality = Literal["exact", "equivalent", "definition", "proxy", "missing"]

@dataclass(frozen=True)
class FieldSpec:
    name: str
    owner: str
    quality: Quality
    unit: str
    formula: str | None = None

FIELD_SPECS: dict[str, FieldSpec] = {
    "market_cap": FieldSpec("market_cap", "yahoo", "exact", "USD"),
    "short_interest": FieldSpec("short_interest", "finra", "exact", "shares"),
    "sma20": FieldSpec("sma20", "yahoo_chart", "exact", "pct"),
    "pfcf": FieldSpec("pfcf", "yahoo", "definition", "ratio"),
    # ... isi seluruh 116 kolom; test memastikan tidak ada yang terlewat
}
```

Aturan:

- Sumber hanya boleh menerbitkan field yang `owner`-nya sesuai.
- `DepthObservation.__post_init__` tetap melakukan normalisasi alias/unit yang sudah ada.
- Test `test_field_specs_cover_catalog` memastikan union `FIELD_SPECS` = 116 key katalog.
- Test `test_no_source_emits_foreign_field` mencegah Yahoo mengisi `short_interest` lagi.

---

## 3. Phase 0 — Guardrails dan flag (0,5 hari)

### Task 0.1: Baseline manifest

**Objective:** Simpan baseline yang bisa dibandingkan setelah perubahan.

**Files:**
- Create: `research/finviz-off-baseline.json`
- Create: `scripts/depth_audit.py`

**Steps:**
1. Tulis skrip yang membaca `depth.json`, menghitung count, coverage 116, median field, sha256, dan distribusi sektor.
2. Jalankan:
   ```bash
   .venv/bin/python scripts/depth_audit.py \
     --input /home/ubuntu/ztockz-data/depth.json \
     --out research/finviz-off-baseline.json
   ```
3. Verifikasi output: `count=4217`, `coverage=92`, `sha256` tercatat.
4. Commit: `test: add depth baseline manifest`.

**Test:** `pytest tests/test_market_depth.py -q` tetap hijau.

### Task 0.2: Config flags

**Objective:** Finviz dan chart bisa dinyalakan/dimatikan tanpa mengubah kode.

**Files:**
- Modify: `app/config.py:66-74`
- Test: `tests/test_market_depth.py`

**Step 1: Write failing test**

```python
def test_settings_parses_mee_depth_finviz_zero(monkeypatch):
    monkeypatch.setenv("MEE_DEPTH_FINVIZ", "0")
    from app.config import Settings
    assert Settings().depth_finviz is False
```

**Step 2: Run** — expected FAIL `AttributeError: depth_finviz`.

**Step 3: Implement**

```python
depth_finviz: bool = True      # MEE_DEPTH_FINVIZ
depth_chart: bool = False      # MEE_DEPTH_CHART; opt-in sampai A/B lulus
```

**Step 4: Run** — expected PASS.

**Step 5: Commit** — `feat: add depth source flags`.

### Task 0.3: Factory exclusion + observability

**Objective:** `MEE_DEPTH_FINVIZ=0` benar-benar tidak membuat `FinvizDepthSource`.

**Files:**
- Modify: `app/market/depth.py:978-1011`
- Modify: `app/main.py:322-342`
- Test: `tests/test_market_depth.py`

**Test:**

```python
def test_build_depth_collector_excludes_finviz_when_disabled():
    settings = SimpleNamespace(
        depth_finviz=False,
        depth_chart=False,
        depth_sec_snapshot=None,
        depth_concurrency=1,
        depth_delay_s=0,
        depth_yahoo_concurrency=1,
        depth_store_path=None,
    )
    collector = build_depth_collector(settings, ["AAPL"])
    assert "finviz" not in [source.name for source in collector.sources]
```

**Implementation shape:**

```python
sources = [SecDepthSource(...)]
if getattr(settings, "depth_finviz", True):
    sources.append(FinvizDepthSource(...))
sources.extend([StockAnalysisDepthSource(...), YahooDepthSource(...)])
if getattr(settings, "depth_chart", False):
    sources.append(YahooChartDepthSource(...))
```

Set `application.state.depth_sources = tuple(source.name for source in collector.sources)`.

**Verification:** `pytest tests/test_market_depth.py -q` → all pass; default still includes `finviz`.

---

## 4. Phase 1 — Yahoo quoteSummary penuh (2 hari)

### Task 1.1: Yahoo depth client with one auth session

**Objective:** Satu cookie/crumb untuk seluruh batch, 14 modul quoteSummary, tanpa request auth per simbol.

**Files:**
- Create: `app/market/yahoo_depth.py`
- Test: `tests/test_market_yahoo_depth.py`

**Test:** mock `fc.yahoo.com`, `/v1/test/getcrumb`, and `quoteSummary`; assert one cookie request and one crumb request for two symbols.

**Implementation:**
- Reuse the existing `YahooAdapter` auth pattern.
- `YAHOO_DEPTH_MODULES`:
  ```python
  (
      "price", "summaryDetail", "defaultKeyStatistics", "financialData",
      "assetProfile", "quoteType", "calendarEvents", "earnings",
      "earningsTrend", "earningsHistory", "recommendationTrend",
      "majorHoldersBreakdown", "netSharePurchaseActivity", "institutionOwnership",
  )
  ```
- `fetch_many()` returns `DepthObservation(source="yahoo")`.
- Retry once on 401/403 with a fresh crumb.
- One failed symbol logs and continues.

**Live check:** 100 symbols from `universe.json`; expected ≥95% HTTP 200, no sustained 429.

### Task 1.2: fundamentals-timeseries

**Objective:** Isi field yang tidak lagi tersedia di `quoteSummary`.

**Files:**
- Modify: `app/market/yahoo_depth.py`
- Test: `tests/test_market_yahoo_timeseries.py`

**Endpoint:**

```text
GET /ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}
  ?symbol={symbol}
  &type=annualTotalRevenue,annualDilutedEPS,annualEBIT,annualInvestedCapital,
        annualLongTermDebt,annualStockholdersEquity,annualPretaxIncome,
        annualTaxProvision,annualCurrentAssets,annualCurrentLiabilities,
        annualInventory,quarterlyTotalRevenue,quarterlyDilutedEPS,
        trailingDilutedEPS
  &period1=1262304000&period2=<now>&merge=false&crumb=<crumb>
```

**Tests:** fixture with 4 annual + 5 quarterly points; assert `eps_past_3y`, `sales_past_3y`, `eps_qoq`, `sales_qoq`, `eps_yoy_ttm`, `roic`, `lt_debt_to_equity`.

**Do not** compute 5Y growth here; Yahoo only provides 4 FY. Leave `eps_past_5y`/`sales_past_5y` as `missing`.

### Task 1.3: Field normalisation and quality labels

**Objective:** Jangan mencampur unit/definisi.

**Files:**
- Modify: `app/market/depth_fields.py`
- Modify: `app/market/yahoo_depth.py`
- Test: `tests/test_market_depth_fields.py`

**Rules:**
- `debt_to_equity` from Yahoo = percent → divide by 100 (existing `DepthObservation` behavior).
- `dividend_yield` quoteSummary = fraction → ×100.
- `short_interest` **tidak** dari Yahoo; FINRA.
- `pfcf` = `definition`, jangan diterbitkan sebagai exact.
- `sma*` = persen jarak dari harga, bukan harga SMA.
- `high_52w`/`low_52w` = USD.

**Test:** unit fixture for AAPL; `sma20 == 1.73`-style value, not `341`.

### Task 1.4: Live 100-symbol Yahoo coverage

**Objective:** Buktikan 108/116 di lingkungan repo.

**Steps:**
1. Jalankan probe 100 simbol dengan source baru saja.
2. Simpan `research/probes-finviz-off/yahoo-live-100.json`.
3. Expected: ≥100/100 symbols, 108/116 catalog fields, 0 sustained 429.
4. Commit: `feat: add full Yahoo depth source`.

**Gate:** jika <100/116, jangan lanjut ke Phase 2 sebelum gap dijelaskan.

---

## 5. Phase 2 — Chart 10y untuk performance/technical (2 hari)

### Task 2.1: Pure chart parser

**Objective:** 20 field dari satu response chart, teruji tanpa jaringan.

**Files:**
- Create: `app/market/chart.py`
- Test: `tests/test_market_chart.py`

**Function:**

```python
def parse_chart_metrics(entry: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    """Return canonical depth fields from one Yahoo chart entry."""
```

**Formulas (dari brief, jangan diubah tanpa test):**

```text
perf_1w  = close[-1] / close[-6]  - 1
perf_4w  = close[-1] / close[-22] - 1
perf_13w = close[-1] / close[-64] - 1
perf_26w = close[-1] / close[-127] - 1
perf_52w = close[-1] / anchor(today - 12 months) - 1
perf_ytd = close[-1] / last_close_previous_year - 1
perf_3y  = close[-1] / anchor(today - 36 months) - 1
perf_5y  = close[-1] / anchor(today - 60 months) - 1
perf_10y = close[-1] / anchor(today - 120 months) - 1
```

- Pakai **raw close**, bukan `adjclose`.
- Window bar: 5/21/63/126/252; anchor kalender untuk 52w/3y/5y/10y.
- `sma20/50/200` = `(close - sma) / sma * 100` (persen).
- `atr` = ATR(14) dalam USD.
- `volatility_1w/4w` = rata-rata `(high-low)/close` 5/21 sesi, **bukan stdev**.
- `average_volume` = rata-rata 65 sesi termasuk hari berjalan.
- `high_52w/low_52w` dari 365 hari kalender; jangan `meta.fiftyTwoWeekHigh`.
- `relative_volume` = volume hari ini / average_volume.
- `gap` = `(open - prev_close) / prev_close * 100`.
- `change_from_open` = `(close - open) / open * 100`.
- `high_50d/low_50d` = harga (bukan persen) — lihat open decision §11.
- `week_52_range` = `(high_52w - low_52w) / low_52w * 100`.
- Buang bar tidak lengkap (`close is None` atau `high < max(open, close)`).

**Tests (wajib, dari brief):**
- `test_chart_perf_uses_raw_close_not_adjclose`
- `test_chart_perf_windows_are_five_twentyone_sixtythree`
- `test_chart_sma_is_percent_distance`
- `test_chart_high_52w_is_price_and_covers_365_calendar_days`
- `test_chart_volatility_is_average_daily_range`
- `test_chart_average_volume_is_sixtyfive_sessions_including_today`
- `test_chart_skips_incomplete_bar`
- `test_chart_partial_bar_policy`
- `test_chart_returns_no_field_outside_the_catalog`

### Task 2.2: YahooChartDepthSource

**Objective:** Sumber yang memanggil chart 10y dan menerbitkan 20 field.

**Files:**
- Modify: `app/market/depth.py` (or `app/market/chart.py` for class)
- Test: `tests/test_market_chart_source.py`

**Tests:**
- `test_chart_source_emits_depth_observations`
- `test_chart_source_uses_provider_http_build_client`
- `test_chart_source_records_one_failure_without_killing_the_batch`
- `test_chart_source_excludes_price_volume_prev_close`

**Live:** 100 symbols; expected 19–20 fields, 100/100 HTTP 200; compare against `depth.json`; `perf_10y` filled ~73% because IPO <10y (bukan regresi).

**Commit:** `feat: derive depth technicals from Yahoo chart`.

---

## 6. Phase 3 — Sumber resmi untuk field yang tidak boleh dari Yahoo (2 hari)

### Task 3.1: FINRA short interest

**Objective:** Ganti `short_interest`/`short_ratio`/`short_float` dengan data bertanggal settlement.

**Files:**
- Create: `app/market/finra.py`
- Test: `tests/test_market_finra.py`

**Endpoint:**
```text
POST https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest
{"limit":5000,"offset":0}
```

**Rules:**
- 5 request = seluruh pasar; cache per `settlementDate`.
- `short_interest` = shares (konversi ke unit katalog jika perlu).
- `short_ratio` = `short_interest / average_volume_3m`.
- `short_float` = `short_interest / shares_float * 100`.
- Jangan pakai `daysToCover` Nasdaq.
- Simpan `settlementDate` internal; jangan tampilkan sebagai field publik baru.

**Tests:**
- `test_finra_parses_all_pages`
- `test_finra_units_are_shares`
- `test_finra_short_ratio_uses_three_month_average_volume`
- `test_finra_missing_symbol_is_not_zero`
- Live 100 symbols: 98/100 fill; 82/97 within 1%.

### Task 3.2: Index membership

**Objective:** Isi S&P 500/400/600, DJIA, NDX tanpa Finviz.

**Files:**
- Create: `app/market/indexes.py`
- Test: `tests/test_market_indexes.py`

**Sources:**
```text
SSGA SPY   → S&P 500
SSGA MDY   → S&P 400
SSGA SPSM  → S&P 600
SSGA DIA   → DJIA
Nasdaq list-type/nasdaq100 → NDX
```

**Rules:**
- Parse XLSX (use existing dependency if available; otherwise add `openpyxl`).
- Normalize `BRK.B` ↔ `BRK-B`.
- Cache daily; output sorted comma labels.
- `RUT` remains unavailable; do not invent it.

**Tests:**
- `test_index_membership_sp500`
- `test_index_membership_normalizes_class_shares`
- `test_index_membership_known_aapl`
- `test_index_membership_unknown_is_none_not_empty_string`
- Live 100: 100/100 for SPX/NDX/DJIA.

### Task 3.3: Cboe optionable verification

**Objective:** Verifikasi field `optionable` yang sudah diisi Yahoo.

**Files:**
- Create: `app/market/cboe.py` (optional; only if Yahoo mismatch >1%)
- Test: `tests/test_market_cboe.py`

**Endpoint:** `HEAD https://cdn.cboe.com/api/global/delayed_quotes/options/{T}.json`; 200 = optionable, 403 = not.

**Rule:** Use only as verification, not primary; Yahoo options endpoint is 100/100 and cheaper. Gate: if Yahoo vs Cboe mismatch ≤1%, skip Cboe from production.

### Task 3.4: Nasdaq earnings/target/recommendation (verification only)

**Keputusan (2026-10-29): keluar scope.**

Ketiga field sudah terisi dari Yahoo depth pada 4.217 simbol:

```text
target_price        3.690  (87,5%)
recommendation      3.659  (86,8%)
earnings_date       3.730  (88,4%)
eps_next_q          3.417  (81,0%)
```

Menambah Nasdaq sebagai sumber kedua tidak menambah satu kolom pun — ia hanya
mengecek ulang field yang sudah ada, dengan biaya terukur **57,35 s per 100
simbol** (benchmark 2026-10-28) dan endpoint per-simbol tanpa batch. Kalau suatu
saat satu nilai diperdebatkan, verifikasi dilakukan sebagai skrip QA offline
terhadap simbol itu saja, bukan sebagai rung di siklus harian.

Alasan pola ini tidak dipakai untuk SEMUA field: cross-check bernilai ketika
sebuah field menentukan keputusan uang. `target_price` dari satu sumber sudah
cukup untuk screener; menambah sumber kedua menggandakan biaya crawl untuk
nol kolom baru.


**Objective:** Fallback untuk `target_price`, `recommendation`, `earnings_date`.

**Files:**
- Create: `app/market/nasdaq_depth.py`
- Test: `tests/test_market_nasdaq_depth.py`

**Endpoints:**
```text
/api/analyst/{T}/targetprice
/api/analyst/{T}/ratings
/api/calendar/earnings?date=YYYY-MM-DD
```

**Rule:** Yahoo remains primary; Nasdaq only fills nulls. Do not overwrite Yahoo with a categorical recommendation unless `FIELD_SPECS` says so.

### Task 3.5: SEC 5Y growth — deferred

**Objective:** Only if operator wants `eps_past_5y`/`sales_past_5y`.

**Rule:** `companyfacts` per CIK is large; do not put it in the daily 4.217 loop. Use a separate bounded job with cache and only for symbols with facts. Default: leave missing. This keeps Phase 3 honest.

---

## 7. Phase 4 — News RSS (0,5 hari)

### Task 4.1: RSS parser and dedupe

**Files:**
- Create: `app/market/news.py`
- Test: `tests/test_market_news.py`

**Endpoints:**
```text
Yahoo:  https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym}&region=US&lang=en-US
Nasdaq: https://nasdaq.com/feed/rssoutbound?symbol={sym}
```

**Fields:** `news_title`, `news_url`, `news_time`.

**Tests:**
- `test_yahoo_rss_item_maps_to_news_item`
- `test_nasdaq_rss_unescapes_html_entities`
- `test_cross_feed_dedupe_by_normalised_title`
- `test_news_time_is_aware_utc`

**Rule:** Dedupe by normalized title, not URL; 24/313 duplicates measured. `daily_digest` remains internal future feature.

---

## 8. Phase 5 — A/B penuh 4.217 dan flip (1 hari)

### Task 5.1: Two-store A/B

**Objective:** Bandingkan Finviz-on dan Finviz-off tanpa saling menimpa.

**Commands:**

```bash
# ON (baseline)
MEE_DEPTH_FINVIZ=1 MEE_DEPTH_CHART=0 \
MEE_DEPTH_STORE_PATH=/home/ubuntu/ztockz-data/depth-on.json \
.venv/bin/python -m app.market.depth_run --universe research/probes-1000/universe.json

# OFF (candidate)
MEE_DEPTH_FINVIZ=0 MEE_DEPTH_CHART=1 \
MEE_DEPTH_STORE_PATH=/home/ubuntu/ztockz-data/depth-off.json \
.venv/bin/python -m app.market.depth_run --universe research/probes-1000/universe.json
```

If no CLI exists, add one thin runner only; do not duplicate collector logic.

### Task 5.2: Compare report

**Files:**
- Create: `scripts/depth_compare.py`
- Output: `research/finviz-off-bench.md` and `research/finviz-off-compare.json`

**Per field:**
- coverage count
- null count
- median relative delta vs ON (numeric)
- exact field list
- source owner

**Per symbol:**
- fields missing vs ON
- fields added vs ON

### 5.3a Gate diukur ulang (2026-10-29)

Dua kriteria asli memakai denominator yang tidak bisa dicapai, bukan karena
sumbernya rusak:

**`108/116` kolom.** Store berisi 106. Sepuluh kolom kosong terdiri dari dua
kelompok yang bukan milik depth:

```text
price, prev_close, change, volume, open, high, low   → QuoteHub (quotes.json)
daily_digest, sort_order, row_type                   → screener-internal
```

Tujuh kolom harga pindah ke QuoteHub setelah gate ini ditulis. Plafon depth
adalah **106**, bukan 108.

**`index_membership >= 95%`.** Diukur atas seluruh universe hasilnya 64,9%,
tapi angka itu mengukur komposisi universe, bukan kesehatan sumber. Universe
4.217 memuat micro-cap dan OTC yang memang tidak masuk indeks mana pun. Diukur
dengan denominator yang benar:

| Indeks | Ter-parse | Nominal | Fidelity | Di universe | Berlabel | Match |
|---|---:|---:|---:|---:|---:|---:|
| S&P 500 | 503 | 500 | 100,6% | 480 | 480 | **100%** |
| S&P 400 | 400 | 400 | 100% | 390 | 390 | **100%** |
| S&P 600 | 601 | 600 | 100,2% | 588 | 588 | **100%** |
| Russell 2000 | 1.967 | 2.000 | 98,4% | 1.872 | 1.872 | **100%** |
| NDX | ~99 | 101 | ~98% | 90 | 90 | **100%** |
| DJIA | 30 | 30 | 100% | 30 | 30 | **100%** |
| **Union** | 2.872 | — | — | **2.735** | **2.735** | **100%** |

Setiap nama indeks yang ada di universe berlabel. Yang tidak berlabel adalah
137 nama yang tidak ada di universe kita sejak awal. Sembilan simbol berlabel
`RUT` di luar keenam daftar (AFCG, AIV, ARI, CASH, EMAT, FRBT, GDC, LNZA, PED)
— varian label Russell, bukan kebocoran.

**Gate revisi:**

```text
[x] 4.217/4.217 simbol hadir
[x] 106/116 kolom depth (7 harga milik QuoteHub, 3 screener-internal)
[x] index match rate 100% — setiap nama indeks di universe berlabel
[x] index parse fidelity >=95% per indeks (terendah 98,4%)
[x] short_interest >=95%   (4.209/4.217 = 99,8%)
[x] optionable >=95%       (4.197/4.217 = 99,5%)
[x] store <=12 MB          (9,0 MB)
[x] no Finviz HTTP saat MEE_DEPTH_FINVIZ=0
[x] cycle <=60 menit       (diukur dari log progres per sumber)
[x] setiap sumber melaporkan durasinya sendiri (log INFO mee.depth)
```

Kriteria terakhir ditutup dengan log progres per sumber, bukan dengan menebak
durasi: `depth source <name> done in <seconds>s (<fields> fields)` muncul di
`journalctl -u ztockz` setiap siklus, jadi "cycle <= 60 menit" bisa dibaca
langsung dari log alih-alih diukur manual.

### Task 5.3: Acceptance gate (asli, disimpan untuk riwayat)

Lulus hanya jika:

```text
[ ] 4.217/4.217 symbols present
[ ] 108/116 columns populated
[ ] 94/116 A+B fields populated at >=95% symbols
[ ] index_membership >=95%
[ ] short_interest >=95%
[ ] optionable >=95%
[ ] no source error >1% of symbols
[ ] cycle <=60 minutes
[ ] store <=12 MB
[ ] no Finviz HTTP request when MEE_DEPTH_FINVIZ=0
```

If any fail: do not flip; write the gap report and return to the owning phase.

### Task 5.4: Flip and rollback

**Files:**
- Modify: `/home/ubuntu/ztockz-data/engine.env`
- Modify: `README.md:127-132`
- Modify: `deploy/` if it lists env

```bash
MEE_DEPTH_FINVIZ=0
MEE_DEPTH_CHART=1
```

Then:

```bash
sudo systemctl restart ztockz
curl -s http://127.0.0.1:8099/engine/health
curl -s 'http://127.0.0.1:8099/market/depth?symbols=AAPL'
```

Rollback = set `MEE_DEPTH_FINVIZ=1`, restart. Keep Finviz code for at least one release; delete only after 7 days of stable Finviz-off runs.

---

## 9. Definition/quality decisions (open gates)

| # | Decision | Recommended | Consequence |
|---|---|---|---|
| 1 | `high_50d`/`low_50d` unit | **Price USD** (consistent with 52w) | Update catalog unit from `pct` to `price`; UI must read USD |
| 2 | `pfcf` | **Do not publish** until formula matches | Coverage drops by 1; no misleading ratio |
| 3 | `inst_own` | Yahoo canonical + internal `definition` metadata | Nasdaq 11.7% median difference; never mix |
| 4 | `inst_trans` | Yahoo only; no Nasdaq delta as primary | Nasdaq approximation median 1.7 pp, lag 28–120 days |
| 5 | `shortable` | `unknown` | 1 field lost; Finviz `Yes` 991/994 |
| 6 | `eps_past_5y`/`sales_past_5y` | Deferred SEC facts job | 2 fields missing until full facts crawl |
| 7 | Depth snapshot time | Pre-open fixed window for A/B | Performance fields shift up to 1.5 pp intraday |
| 8 | Finviz retention | Keep flag one release | Rollback safety |

These are **open** until the operator answers; do not mark fixed from silence.

---

## 10. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Yahoo ToS / 429 / crumb expiry | Source outage | Browser UA, one crumb/session, retry 401 once, fallback to last good snapshot |
| Yahoo quoteSummary schema drift | Missing modules | Schema/coverage tests; fail loudly per module |
| FINRA bulk endpoint changes | Short fields missing | Cache settlement; do not zero-fill |
| SSGA XLSX/HTML changes | Index fields missing | Parse headers defensively; test with fixture |
| `DepthStore.merge` priority-first | New source cannot override old | Field ownership registry; A/B in two stores, not one |
| Unit/definition mismatch | Wrong numbers | `FIELD_SPECS` quality/unit; tests |
| Partial bar / pre-open | Performance drift | Injected clock; pre-open A/B |
| 4.217 x 5 Yahoo requests | ~40–60 min, 230–370 MB | 2 workers, cache, skip RSS/options reschedule |
| Public redistribution | Legal risk | Keep `MEE_ENGINE_PUBLIC_MODE=0` until licensed; `/market/depth` remains internal |

---

## 11. What is NOT in scope

- UI/cockpit migration.
- Full SEC `companyfacts` crawl for 10.429 symbols.
- IBKR gateway.
- Sector taxonomy beyond the existing SIC classifier.
- Public redistribution license.
- Removing Finviz code in the same release as the flip.

---

## 12. Evidence and rollback

- Baseline file: `/home/ubuntu/ztockz-data/depth.json` (4.217, 92/116, 6,7 MB, 2026-10-28 14:22 UTC).
- Yahoo report: `research/finviz-off-yahoo.md` (108/116; 94 A+B; 5 missing Finviz-only fields).
- Nasdaq report: `research/finviz-off-nasdaq.md` (8 verified replacements; 2 no-source).
- Derived report: `research/finviz-off-derived.md` (20 chart fields; RSS; integration points).
- Current code: HEAD `6fcec9e`, suite `2.913 passed ×3`.

**Definition of done:** setelah Phase 5, `MEE_DEPTH_FINVIZ=0` menghasilkan **108/116** kolom pada 4.217 simbol, 94 A+B, tanpa satu request Finviz, dan rollback satu env var berhasil.
