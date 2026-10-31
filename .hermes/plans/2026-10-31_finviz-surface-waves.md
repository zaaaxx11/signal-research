# Finviz-shaped surface — wave plan

**Status:** Wave 1 sedang dibangun. Dokumen ini mencatat urutan dan **kesiapan data** tiap
bagian, supaya tidak ada halaman yang tampil kosong tanpa alasan.

## Wave 1 (jalan sekarang)

| bagian | isi | data |
|---|---|---|
| 20 baris/halaman | panel default 20, pager & range ikut | ada |
| shell nav | Home · Stock Screener · ETF Screener · Maps · Charts · News · Groups · Insider · Futures · Forex · Crypto · Watchlist · Portfolio · Calendar | ada (rute) |
| kanan atas | search "Search ticker, company or profile" · jam 12h (Login/Signup/Pricing/Help ditunda) | ada (statis) |
| Home sinyal | 11 grup × 20 baris: gainers, losers, new_high, new_low, overbought, oversold, unusual_volume, most_active, most_volatile, insider_buying, insider_selling | `GET /market/signals` (baru) |
| Upgrades / Downgrades | tidak tersedia — tidak ada sumber rating analis | — |

## Wave 2 — peta & agregat (data sudah ada)

| bagian | sumber data | usaha |
|---|---|---|
| Maps | `sector`, `market_cap`, `change_pct` per simbol dari universe; treemap kanvas + popup hover berisi saham sektor itu | 2–3 hari |
| Groups | 21 sektor dari `/market/breadth` + agregat perf | 0,5 hari |
| Calendar | `earnings_date` per simbol | 1 hari |
| Halaman ticker `/stock/{symbol}` | DetailPanel yang sudah ada, dirender penuh + chart harga | 1 hari |

## Wave 3 — yang butuh endpoint baru

| bagian | yang kurang | usaha |
|---|---|---|
| Charts | seri OHLC per simbol (kolektor Yahoo chart sudah mengambilnya, belum diekspos) | 1–2 hari |
| Insider | daftar Form 4 (collector ada, penyajian belum) | 1–2 hari |
| ETF Screener penuh | universe `us_etf` sudah ada (5.776), panel perlu mode aset | 1 hari |

## Wave 4 — yang butuh universe/akun baru

| bagian | yang kurang | usaha |
|---|---|---|
| Crypto | universe crypto (Yahoo/CNBC simbol kripto) | 2–3 hari |
| Futures | kontrak + roll | 2–3 hari |
| Forex | pasangan mata uang | 1–2 hari |
| Watchlist / Portfolio | akun, sesi, penyimpanan per pengguna | 3–5 hari |
| Pricing / Help | halaman statis + konten | 0,5 hari |

## Prinsip yang dipegang

```text
0  UI hanya menampilkan apa yang dibutuhkan pembaca. Status mesin (depth store, snapshot,
   screen db, versi, timestamp UTC, source label) TIDAK pernah tampil — hanya /health untuk ops.
1  Tidak ada angka palsu. Grup tanpa sumber tampil sebagai "not available" dengan alasannya.
2  Satu request per permukaan. Home = satu /market/signals, bukan 11.
3  Setiap nav item punya href nyata; yang belum dibangun masuk /soon/{slug}.
4  Density Finviz (baris 20–22 px) tanpa mengorbankan token rose dan aksesibilitas kita.
```

## Angka acuan dari teardown

```text
baris Finviz          20 px · cell padding 4 px · chrome 126 px
baris kita sekarang   28 px · cell padding 8 px · chrome 306 px
target Wave 1         20 baris/halaman · baris 22 px · chrome ≤150 px
```
