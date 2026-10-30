# Depth → Screener Integration Plan

> **For Hermes:** RED-first per task, file ownership disjoint, parent gates 3× before commit.

**Goal:** kolom depth (106/116, 4.217 simbol) bisa dipakai di `/screens/select` — difilter, di-sort, dan dikembalikan — tanpa mengubah kontrak yang sudah ada saat depth tidak tersedia.

## Recon findings (2026-10-29, live `/screens/select`)

Terukur sebelum menulis kode:

```text
universe scan          5.600 simbol (TradingView)
respons baris          15 key; sebagian besar katalog = None
katalog                 116 kolom
dipetakan dari provider  21 kolom
tanpa pemetaan           95 kolom  ← hanya depth yang punya
```

Contoh nyata (NVDA, `limit=1`):

```text
market_cap_usd   5.149.206.116.736   ✅ dari provider
pe_ttm                     None      ❌
target_price               None      ❌
sector            Electronic Technology ✅
```

**Temuan yang mengubah desain:** filter `target_price` sudah **diterima** dan
benar-benar dijalankan — `> 1` menghasilkan 3.877 dari 5.600, `> 1.000.000`
menghasilkan 0. Artinya nilainya ada di baris provider (TradingView mengirim
`price_target_average`, dipetakan di `app/screen/universe.py:214`), tetapi
**tidak sampai ke respons** karena proyeksi baris tidak meminta kolom itu.

Konsekuensi: overlay depth harus berjalan **sebelum** `build_universe`, supaya
baik filter maupun respons melihat nilai yang sama. Menambal di lapisan respons
saja akan membuat filter dan tampilan tidak sinkron — persis bug yang sudah ada
sekarang.

Nama field juga beda antar lapisan: katalog menulis `pe`, screener menulis
`pe_ttm`. Overlay memakai key katalog; pemetaan nama milik lapisan screener.

## Status (2026-10-30)

| Phase | Status |
|---|---|
| 1 — overlay murni | ✅ `app/screen/depth_overlay.py`, 37 test |
| 2 — reader + wiring | ✅ `depth_source.py`, 3 seam (snapshot, export, select) |
| 3 — filter | ✅ 18 alias nama; terukur `roic>15` 607, `pe_ttm<15` 1.108, `rsi_14>80` 48 |
| 3b — kolom di respons | ✅ parameter `columns` (opsi B), default tetap light row |
| 5 — verifikasi | ✅ live, tanpa depth baris identik |
| 4 — UI (`ui/app.js` + pemilih kolom) | ⏳ **belum** — sengaja terakhir |

**Keputusan yang diambil di jalan:**

1. **Overlay dipasang setelah `build_universe`**, bukan sebelum. `build_universe`
   membangun ulang baris dari key set-nya sendiri — merge sebelum itu dibuang.
   Test menangkapnya (baris kosong), bukan asumsi.
2. **Kunci ticker = `symbol`**, bukan `ticker`. Tanpa itu overlay tidak
   menemukan satu simbol pun dan gagal tanpa suara.
3. **Alias 18 nama** (`pe`→`pe_ttm`, `rsi`→`rsi_14`, …). Katalog dan screener
   menamai pengukuran yang sama dengan nama berbeda; tanpa peta ini store penuh
   tapi filter tetap menjawab nol.
4. **Proyeksi baris ditolak sebagai perubahan default.** Sempat ditambahkan 41
   kolom ke `display_row`; test kontrak light row merah, dan itu benar. Diganti
   parameter `columns` yang opt-in.

**Kontrak `columns`:**

```text
"columns": ["roic", "pe_ttm"]        → baris = symbol + sel yang diminta + as_of
tanpa "columns"                       → light row historis, identik
kolom dikenal tapi tidak ada di baris → None (bukan hilang)
kolom tak dikenal                     → 422, menyebut namanya
columns + view                        → 422 (dua cara menamai satu keluaran)
maksimum 40 kolom
```

## Masalah terukur

```text
/screens  → 25 field asli per simbol (engine facts)
depth     → 106/116 kolom, 4.217 simbol
```

Dua pulau. UI memanggil `/screens/select` → `runtime.screen_repository` →
`build_universe(page.rows, ...)`. Depth tidak pernah masuk ke `page.rows`.

## Seam

`app/screen/universe.py:build_universe(rows, ...)` — murni, menerima
`Iterable[Mapping]`, tidak menyentuh I/O. Depth di-merge ke `rows` **sebelum**
fungsi itu berjalan.

Pemanggil yang harus ikut:

```text
app/screen/snapshot.py:129   jalur utama (snapshot dari scan)
app/screen/export.py:450     jalur export
```

## Aturan merge (tidak boleh dinegosiasikan)

1. **Fill-missing-only.** Nilai provider yang sudah ada tidak pernah ditimpa.
   Kalau provider bilang `pe=12.0` dan depth bilang `11.8`, provider menang.
2. **Catalog keys only.** Depth punya field internal (`source_time`, `conflict`)
   yang tidak boleh bocor ke baris.
3. **Ticker normalisasi dua arah.** `BRK.B` (provider) ↔ `BRK-B` (katalog
   depth) ↔ `BRK.B`. Tanpa ini, kelas multi-class hilang diam-diam.
4. **Absen bukan nol.** Field yang tidak ada di depth tidak ditambahkan sama
   sekali; tidak ada `0.0` pengganti.
5. **Satu pembacaan per scan.** Store depth dibaca sekali (mtime-cached), bukan
   per baris.

## Phase 1 — overlay murni (0,5 hari)

**File baru:** `app/screen/depth_overlay.py`
**Test baru:** `tests/test_screen_depth_overlay.py`

```python
def overlay(
    rows: Iterable[Mapping[str, Any]],
    depth: Mapping[str, Mapping[str, Any]],
    *,
    default_ticker_key: str = "ticker",
) -> list[dict[str, Any]]:
    """Kembalikan salinan baris dengan field depth yang belum ada."""
```

Test:
- field depth yang belum ada ditambahkan
- nilai provider tidak ditimpa
- key non-katalog diabaikan
- `BRK.B` menemukan entri `BRK-B`
- baris tanpa entri depth tidak berubah (identitas objek boleh berbeda, isi sama)
- `None` di depth tidak menambahkan key

## Phase 2 — wiring (0,5 hari)

**File:** `app/screen/snapshot.py`, `app/screen/export.py`, `app/config.py`

- setting `screen_depth: bool = True` (`MEE_SCREEN_DEPTH`)
- pembacaan store depth sekali per scan, mtime-cached, kegagalan baca = depth
  dianggap kosong (scan tetap jalan, seperti hari ini)
- `overlay()` dipanggil tepat sebelum `build_universe`

Test:
- dengan depth: baris membawa field depth
- tanpa depth / file hilang / JSON rusak: hasil identik dengan hari ini
- flag off: tidak ada pembacaan file sama sekali

## Phase 3 — filter & kolom (0,5 hari)

`/screens/select` memvalidasi field terhadap katalog + kolom yang ada di baris.
Setelah Phase 2, kolom depth otomatis dikenali. Yang harus dibuktikan:

- filter `pe < 20` mengembalikan hasil (bukan 422)
- `columns: ["target_price", "inst_own"]` mengembalikan nilai
- sort by `market_cap` tetap benar
- field yang tidak ada di depth tetap 422 kalau tidak ada di katalog

## Phase 4 — UI (0,5 hari)

`ui/app.js` + `/screens/schema`: kolom depth muncul di pemilih kolom.
Tidak ada perubahan bentuk data — dokumen sel tetap `{value, unit, provenance}`.

## Phase 5 — verifikasi (0,5 hari)

```text
GET /screens/select dengan filter depth-only → total > 0
baris membawa provenance.source == "depth"
tanpa depth → respons identik dengan baseline (byte-for-byte pada satu query)
4.217 simbol tetap terlayani, latensi tidak naik >20%
```

## Non-goals

- Menulis depth balik ke dataset engine (dua sumber kebenaran).
- Menghapus kolom katalog yang belum ada sumbernya.
- Mengubah katalog 116 kolom.
