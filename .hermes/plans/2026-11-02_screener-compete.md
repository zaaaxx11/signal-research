# Plan: menyaingi screener acuan — dari paritas ke keunggulan

Basis: .hermes/research/2026-11-02_screener-parity.md, 2026-11-01_descriptive-filters.md,
2026-11-01_finviz-filter-groups.md. Tanggal 2026-11-02.

## 1. Tesis

Kita tidak menang dengan menyalin 82 filter — backend kita sudah setara (82 key
acuan ada semua, 85 kontrol, 5 tab, 1.328 bucket id). Kita menang di empat hal
yang acuan tidak berikan:

1. Kecepatan nyata: pindah halaman tanpa skeleton, dokumen filter 35 KB gzip,
   total dihitung atas seluruh universe.
2. Kedalaman data: laporan keuangan 10 tahun (income, balance, cash flow) dari
   SEC — acuan hanya 2 periode dan tanpa neraca/arus kas.
3. Kejujuran: filter yang tidak bisa dijawab ditolak 422 dengan alasan, bukan
   hasil kosong; tier premium eksplisit dan bisa diubah lewat konfigurasi.
4. Tanpa paywall untuk hal dasar: filter, sort, ekspor, preset, alert, watchlist.

## 2. Posisi sekarang (terukur)

```text
filter key        82/82 ada, 85 kontrol, 5 tab, 2.197 opsi
bucket            1.328 id (1.240 tersedia)
dokumen           530 KB identity / 35 KB gzip, ETag kuat, 304 tanpa body
screener          11 kolom wajib, sort semua kolom (teks & angka), 17 tab
toolbar           count, refresh+auto, add to, compare, alert, export, presets
navigasi          cache per query: pindah halaman 0 request, 0 skeleton
ticker page       7 tab, snapshot 84 sel, statements 10 tahunan + 8 kuartalan
tes               1.293 web · 5.410 python
```

## 3. Di mana acuan masih menang

| # | Celah | Bukti | Ukuran |
|---|-------|-------|--------|
| 1 | Sector mati | `sec` dikirim `options: []`; `ScreenerHeader.tsx:820` disabled | S |
| 2 | Tickers filter tidak ada | `SymbolSearch` hanya cari 1 simbol | S |
| 3 | Industry/Country free-text | 144/62 enumerasi di acuan | M |
| 4 | Arah sort cuma panah | acuan punya select Asc/Desc | S |
| 5 | Saved screen = kolom saja | acuan simpan filter+sort+kolom | M |
| 6 | Signal 27, 17 disabled | signals.py | L (sebagian butuh data) |
| 7 | Theme/Sub-theme kosong | tak ada taksonomi | L |
| 8 | Riwayat jumlah saham (dilution) | tak ada seri | L |
| 9 | Trade count | tak ada feed | XL (di luar kendali) |
| 10 | Bar 20-hari, pola chart | tak ada bar store | L |
| 11 | Arus insider | data vendor tidak bisa dibaca | M (Form 4 agregasi) |

## 4. Fase

### P0 — tutup yang rusak (1–2 hari, tanpa sumber baru)
Tujuan: tidak ada kontrol mati, tidak ada jalan buntu.

- **P0.1 Sector hidup.** Backend: inline 21 sektor ke dokumen filter (dari
  `/market/sectors`), atau kirim `options` terisi. UI: hapus cabang disabled,
  pakai `SectorSelect`. Tes: dokumen memuat 21 opsi; memilih satu menambah chip
  dan mengirim `f=sec:eq:...`.
- **P0.2 Tickers filter.** Kotak teks menerima daftar (koma/spasi), memfilter
  baris by simbol (server sudah bisa `symbol in [...]`). UI: satu kontrol
  `Tickers` di Descriptive + chip. Tes: `AAPL,MSFT` → 2 baris.
- **P0.3 Enumerasi Industry (144) dan Country (62).** Backend: endpoint
  `/screens/options/{key}` dengan cache 1 jam, dihuni dari universe.
  UI: ganti input teks jadi combobox dengan pencarian. Tes: opsi terisi,
  memilih satu mengirim nilai persis.
- **P0.4 Arah sort.** Ganti panah non-interaktif dengan select Asc/Desc yang
  menulis `-field` bila perlu; sinkron dengan header tabel.
- **P0.5 Saved screen penuh.** Simpan `{filters, sort, view, columns}` bukan
  kolom saja; migrasi dari `ztockz.screens.v1` lama. Tes: simpan → ubah filter →
  muat → semua kembali.

### P1 — paritas sinyal yang bisa dihitung sendiri (3–4 hari)
- **P1.1** Signal dari data kita: Most volatile, Unusual volume, Most active,
  Earnings before/after (kita punya tanggal), Recent insider buying/selling
  (agregasi Form 4 30 hari), Major news (kata kunci di korpus berita).
  Tes: tiap sinyal punya definisi tertulis + jumlah baris wajar.
- **P1.2** Signal chart sederhana dari deret harian: horizontal S/R, double
  top/bottom — hanya kalau bar store ada (lihat P2.3). Kalau belum, tulis
  alasan di UI.

### P2 — proyek data (1–2 minggu)
- **P2.1 Theme/Sub-theme.** Taksonomi 40/268; butuh pemetaan simbol→tema.
  Sumber kandidat: klasifikasi industri + kata kunci deskripsi perusahaan
  (kita sudah punya `description` dari company profile). Status: riset dulu.
- **P2.2 Dilution.** Butuh riwayat shares outstanding per kuartal (SEC XBRL
  `dei:EntityCommonStockSharesOutstanding` per filing) — kita sudah punya
  companyfacts, jadi ini derivasi, bukan sumber baru.
- **P2.3 Bar store.** Simpan OHLCV harian ke disk (kita sudah menarik Yahoo
  chart lalu membuangnya). Ini membuka 20-day high/low dan pola chart.
- **P2.4 Insider flow.** Agregasi Form 4: beli vs jual 30/90 hari, nilai, jumlah
  filer. Bukan angka vendor.

### P3 — keunggulan yang tidak mereka punya (1 minggu)
- **P3.1** Statements di dalam screener: kolom derived (revenue growth 5Y,
  FCF margin) dari SEC — bukan hanya di halaman ticker.
- **P3.2** Saved screen bisa dibagikan lewat URL (encode filter+sort), bukan
  hanya localStorage.
- **P3.3** Ekspor API: endpoint yang menerima screen sama dan mengembalikan
  CSV/JSON dengan token, bukan hanya tombol.
- **P3.4** Budget performa: dokumen filter ≤ 40 KB gzip, pindah halaman ≤ 16 ms
  dari cache, TTI screener ≤ 1,2 s di 4G lambat.

### P4 — poles (3 hari)
Keyboard penuh (semua kontrol), a11y (tabel tersembunyi untuk chart), density
mode (28/32 px), dan empty-state yang menjelaskan.

## 5. Definition of done keseluruhan

```text
paritas     setiap kontrol acuan punya padanan yang benar-benar berfungsi
            (bukan disabled), diukur oleh tes yang menghitung kontrol aktif
data        tidak ada kolom yang menampilkan 0 untuk data yang tidak ada
kecepatan   p95 pindah halaman < 50 ms; dokumen filter < 40 KB gzip
kejujuran   setiap refusal punya kode 422 + kalimat; setiap tier premium
            bisa diubah lewat entitlements.json tanpa deploy
tes         web > 1.300, python > 5.400, tidak ada tes yang dihapus untuk hijau
```

## 6. Risiko

- Theme taxonomy bisa berakhir sebagai tebakan; kalau tidak ada sumber yang
  bisa dipertanggungjawabkan, lebih baik tetap kosong dengan alasan.
- Bar store menambah beban disk dan collector; harus punya retensi.
- Trade count tidak akan pernah ada tanpa feed berbayar — jangan dipaksakan.
- Menambah kontrol memperbesar dokumen; setiap penambahan harus diukur
  terhadap budget 40 KB.

## 7. Urutan eksekusi yang disarankan

P0.1 → P0.2 → P0.3 → P0.4 → P0.5 (semua S/M, tanpa data baru)
lalu P1.1 (sinyal dari data sendiri)
lalu P2.3 (bar store) karena membuka P1.2 dan P2.2
lalu P3.1 dan P3.2
