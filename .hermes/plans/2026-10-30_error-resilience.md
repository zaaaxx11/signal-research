# Error & Bug Resilience Plan

**Tujuan:** kegagalan terdeteksi dalam menit, bukan minggu; setiap perbaikan meninggalkan test yang mencegah kambuh.

**Kondisi awal terukur (2026-10-30):**

```text
/health              {"status":"ok"}  ← tidak memeriksa apa pun
except Exception     95 di app/  ·  logger.exception hanya 4  ·  bare pass 3
alerting             tidak ada
depth store          10 jam (normal: cadence 24 jam)
snapshot facts       15 jam, 4.197 simbol, 20 error
tabel SQLite mati    quotes 0 · instruments 0 · observations 0
```

---

## Status (2026-10-30)

| Phase | Status | Bukti |
|---|---|---|
| 1 — `/health` | ✅ | 5 check, 200/503, nol network, 23–32 ms (prime saat startup: 2,47 s → 0,25 s) |
| 2 — alerting | ⏳ | mesin keputusan belum dibuat; destinasi ditunda operator |
| 3 — ledger per rung | ✅ | `depth.health.json` + key `sources` di `/market/depth/stats` |
| 4 — audit silent catch | ✅ | 104 broad handler dipindai, 11 dipin + allowlist, test AST |
| 5 — kontrak upstream | ✅ | 3/3 ditegakkan: `HoldingsFormatError`, `missing_quarters`, `CrumbRefused` |
| 6 — registry bug | 🔄 P1 dikerjakan | env guard + tabel SQLite |
| 7 — canary harian | ⏳ | menunggu 1, 3 |

**Suite:** 3.915 ×3 (dari 3.758). Commit `f724f07`, `cd20407`, `bbd9d9e`, `0148c34` (push).

**Urutan yang diputuskan operator (2026-10-30):**

```text
Phase 6  →  Phase 2  →  Phase 7  →  done
```

Phase 2 (alerting) dikerjakan **tepat setelah** Phase 6, dan harus **sink-agnostic**:
mesin keputusan yang menghasilkan event, plus antarmuka sink. Platform (Telegram,
email, webhook, Slack) dipasang belakangan — yang penting kodenya ada dulu.

UI **tidak** disentuh sampai engine matang. Yang jalan sekarang hanya riset
read-only atas Finviz (`research/finviz-ui.md`), bukan implementasi.

**Temuan yang muncul saat pengerjaan** (bukan dari rencana awal):

```text
app/api/health.py mengimpor urllib.parse → melanggar aturan import-direction
yahoo_batch.py tidak punya logger sama sekali → NameError
allowlist audit bergeser baris setelah suntingan
```

**Batas yang diterima dan dicatat** (bukan bug yang disembunyikan):

```text
/health bisa "ok" bila proses menyajikan dataset lama sementara file di disk lebih baru
cursor macet tapi parseable tidak terdeteksi (dua cadence, satu ambang salah)
audit tidak melihat contextlib.suppress atau exception yang di-alias
ledger: consecutive_errors sengaja tidak di-reset saat restart
```

## 1. Tiga prinsip

**1. Fail loud, bukan fail quiet.**
Rung opsional boleh gagal, tapi tidak boleh hilang. Bug checksum kemarin lolos
berhari-hari karena rung SEC menelan `SnapshotError` di level INFO dan
mengembalikan `{}` — store tetap melayani seed, service tampak sehat.

**2. Data fail closed, API fail open.**
Data yang tidak terverifikasi tidak boleh disajikan (checksum mismatch = tolak).
Tapi API tidak boleh 500 karena enrichment hilang (`depth.json` rusak → snapshot
kosong, screener tetap jalan).

**3. Satu tempat untuk status.**
`/health` harus menjawab "apakah semua subsistem hidup", bukan "apakah proses
uvicorn hidup".

---

## 2. Inventaris kegagalan nyata (sesi 2026-10-26 → 30)

| # | Kejadian | Kelas | Akar masalah | Status |
|---|---|---|---|---|
| 1 | Snapshot checksum basi, SEC rung mati diam-diam | silent | fail-closed tanpa alarm | ✅ `ops reseal` + WARNING |
| 2 | Bootstrap 4.217 OOM di 4,18 GB | resource | memo dokumen tanpa batas | ✅ window 8 |
| 3 | CNBC ganti format `last_time` → semua harga `None` | upstream | parser mengasumsikan satu format | ✅ 3 bentuk diterima |
| 4 | Yahoo crumb 429 setelah beban tinggi | upstream | throttle per-IP | ✅ egress ladder |
| 5 | Free proxy stall 0 byte/30 s | transport | proxy mati tanpa timeout | ⚠️ diganti rainproxy |
| 6 | Cursor enrichment mentok 0 | config | proxy tidak di-wire | ✅ `depth_enrich_proxy` |
| 7 | `StockAnalysis` 70 menit/siklus | performance | sumber redundan | ✅ dimatikan |
| 8 | `MEE_DEPTH_UNIVERSE` (salah nama) → 10.429 simbol | config | nama env tidak divalidasi | ⚠️ manual |
| 9 | Siklus "hang" 15 menit tanpa log | observability | log hanya di akhir siklus | ✅ log per sumber |
| 10 | Logger INFO dibuang di bawah uvicorn | observability | level tidak disetel | ✅ `_configure_logging` |
| 11 | 20 simbol gagal bootstrap | upstream | 12 HTTP error, 8 tanpa CIK | ⚠️ diterima |
| 12 | Tabel SQLite mati (quotes/instruments/observations) | dead code | sisa arsitektur lama | ❌ belum |
| 13 | IBKR `No valid lb criteria` | external | butuh sesi gateway | ⛔ di luar kendali |

**Pola yang berulang:** 7 dari 13 adalah **kegagalan yang tidak bersuara** atau
**asumsi tentang format pihak ketiga**. Itu dua hal yang plan ini serang.

---

## 3. Phase 1 — `/health` yang benar-benar memeriksa (1 hari)

**Sekarang:** `{"status":"ok","version":"..."}` — tidak berguna.

**Target:**

```json
GET /health
{
  "status": "ok|degraded|failing",
  "checks": {
    "depth_store":   {"ok": true, "age_s": 38700, "budget_s": 93600},
    "snapshot":      {"ok": true, "checksum": "valid", "age_s": 54000},
    "cursors":       {"ok": true, "enrich": 783, "news": 283},
    "cache":         {"ok": true, "bytes": 1999571517},
    "disk":          {"ok": true, "free_gb": 20.1},
    "screen_db":     {"ok": true, "screens": 65}
  }
}
```

Aturan:
- **read-only, tanpa network** — hanya stat file + SQLite count
- setiap check punya **budget** (mis. store boleh 26 jam = cadence 24 + 2 jam)
- `degraded` bila satu check lewat budget tapi masih bisa melayani
- `failing` bila data tidak bisa dipercaya (checksum invalid, store tidak ada)
- HTTP 200 untuk `ok`/`degraded`, **503 hanya untuk `failing`**

`GET /health/deep` (terpisah, tidak dipanggil load balancer): probe keluar
ringan — CNBC 1 simbol, Yahoo crumb, SEC 1 filing. Timeout 10 s total.

Test: setiap check punya kasus ok/lewat budget/fatal; `/health` tidak pernah
melakukan network; `/health/deep` tidak pernah >10 s.

## 4. Phase 2 — Alerting (0,5 hari)

Pengantar: job cron tiap 10 menit memanggil `/health` + `/market/depth/stats`
dan mengirim Telegram **hanya saat status berubah** (bukan tiap tick).

```text
failing                     → kirim segera
degraded > 30 menit         → kirim
coverage turun > 10 kolom   → kirim
siklus error > 1%           → kirim
pulih (ok setelah gagal)    → kirim satu kali
```

Anti-banjir: satu pesan per transisi, state disimpan di file.

## 5. Phase 3 — Health ledger per rung (1 hari)

Setiap sumber depth menulis statusnya sendiri ke `depth.health.json`:

```json
{"sec_bulk": {"last_ok": "...", "last_error": null, "ok_count": 12, "error_count": 0,
              "last_duration_s": 270.2, "last_fields": 78323}}
```

`/market/depth/stats` menyertakannya. Efeknya: "sumber mana yang sedang sakit"
bisa dijawab tanpa membaca log, dan rung yang gagal 3 siklus berturut bisa
memicu alert sendiri.

## 6. Phase 4 — Audit "silent catch" (0,5 hari)

95 `except Exception` di `app/`. Aturan yang ditegakkan:

```text
setiap except harus melakukan salah satu:
  a. raise ulang
  b. logger.warning/exception + mencatat ke ledger
  c. mengembalikan nilai yang secara eksplisit berarti "tidak tersedia"
     DAN menaikkan counter
```

Audit statis: test yang memindai AST, menolak `except Exception: pass` dan
`except Exception: return {}` tanpa logging. Daftar pengecualian yang sah
disimpan eksplisit (mis. probe opsional di `/health/deep`).

Target: dari 95 handler, yang tidak bersuara turun ke 0.

## 7. Phase 5 — Contract tests untuk pihak ketiga (1 hari)

Tiga bug upstream terjadi karena kita mengasumsikan format. Yang dibutuhkan:
fixture rekaman asli yang di-*pin*, dan test yang gagal saat bentuknya berubah.

```text
CNBC restQuote        3 bentuk timestamp (ISO, tanggal+zona, tanggal polos)  ✅ sudah
Yahoo getcrumb        respons non-token ("Too Many Requests") → error jelas   ⚠️ sebagian
Yahoo quoteSummary    modul hilang / null → field absen, bukan nol           ⚠️
SSGA xlsx             kolom bergeser → parse gagal, bukan data salah         ❌
iShares IWM CSV       preamble dua sel → sudah ditangani                     ✅
SEC companyfacts      404 vs rate-limit → dibedakan                          ❌
```

Aturan: **setiap adapter punya satu fixture "bentuk aneh"** yang direkam dari
kejadian nyata atau dibuat dari dokumentasi, dan test memastikan kegagalan itu
jelas — bukan menghasilkan angka yang salah.

## 8. Phase 6 — Registry bug terbuka (prioritas)

| Prioritas | Bug | Dampak | Effort |
|---|---|---|---|
| P1 | Tabel SQLite mati (`quotes`, `instruments`, `observations`) | bingung saat audit; `ztockz.db` 2 jalur | 0,5 hari |
| P1 | `MEE_DEPTH_UNIVERSE` typo tidak ditolak saat start | salah universe tanpa error | 0,25 hari |
| P2 | Free proxy tanpa timeout → stall 30 s | siklus menggantung | 0,25 hari |
| P2 | 12 simbol gagal HTTP SEC, 8 tanpa CIK | coverage 99,5% bukan 100% | 1 hari |
| P3 | `perf_month/quarter/year` tidak ada di depth | kolom UI kosong | 0,5 hari |
| P3 | `beta` sensitif window (1y vs 5y: 0,558 vs 1,156) | definisi tidak eksplisit | 0,25 hari |
| P3 | `DEBT_TO_EQUITY_SCALE = 100` di adapter Finviz | perlu konfirmasi unit katalog | 0,25 hari |

**Aturan registry:** setiap baris punya test regresi sebelum ditutup. Bug yang
tidak bisa direproduksi masuk "diterima" dengan alasan tertulis, bukan dihapus.

## 9. Phase 7 — Canary harian (0,5 hari)

Job 05:00 UTC, setelah siklus depth:

```text
1. /health == ok
2. /market/depth/stats fields_filled >= 100
3. 5 simbol acak: minimal 60 field, minimal 1 field berubah dalam 24 jam
4. /screens/select dengan 3 filter depth → total > 0
5. checksum snapshot valid
```

Gagal → Telegram + tulis `evidence/canary-YYYY-MM-DD.json`.

---

## Urutan eksekusi

```text
Phase 1  /health                    1 hari    ← paling tinggi, semua bergantung ini
Phase 2  alerting                   0,5 hari
Phase 3  ledger per rung            1 hari
Phase 5  contract tests             1 hari
Phase 4  audit silent catch         0,5 hari
Phase 6  registry P1                0,75 hari
Phase 7  canary                     0,5 hari
```

Total ~5 hari kerja. Phase 1–2 sudah menutup 80% nilai: tanpa itu, semua
perbaikan lain hanya bisa ditemukan dengan mata manusia.

## Non-goals

- Retry tak terbatas pada sumber yang menolak (rate limit bukan error sementara).
- Menghapus semua `except Exception` — sebagian memang batas opsional.
- APM berbayar; Telegram + journald cukup untuk satu VPS.
