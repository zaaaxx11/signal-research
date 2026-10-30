# Watchlist & Multi-User Plan — dokumen saja, belum dieksekusi

**Status:** rencana. Menunggu 3 keputusan di §8.

**Masalah:** 100 user premium dengan watchlist berbeda. Kalau tiap user memicu
fetch sendiri, 100 user × 50 simbol = 5.000 request untuk data yang sama.

---

## 1. Prinsip

**Fetch per simbol unik, bukan per user.**

```text
100 user × 50 simbol        = 5.000 langganan
simbol unik (overlap nyata) ≈ 1.200–1.800
```

Watchlist adalah **langganan ke cache bersama**, bukan pemicu crawl.

---

## 2. Model data

```sql
users        (id, email, tier, created_at, last_seen_at)
watchlists   (id, user_id, name, created_at)
watchlist_symbols (watchlist_id, symbol, added_at, PK(watchlist_id, symbol))
```

100 user × 50 simbol = 5.000 baris. SQLite menangani ini tanpa suara.

**Hot set** = `SELECT symbol, COUNT(*) FROM watchlist_symbols GROUP BY symbol`
dihitung ulang tiap menit (atau lewat trigger). Hasilnya: simbol mana yang
sedang ditonton dan oleh berapa user.

---

## 3. Tiga tingkat kesegaran

| Tier | Isi | Refresh | Sumber budget |
|---|---|---|---|
| **A — on screen** | simbol di layar user yang sedang buka app | **15 s** | ~200–300 simbol |
| **B — watchlist** | sisa watchlist | **60 s** | ~1.500 simbol |
| **C — universe** | 4.217 sisanya | **harian** | siklus depth |

Kalau hot set membengkak, sistem **menurunkan tier B**, bukan menolak user.

```text
maks watchlist per user   200 simbol
maks hot set global       3.000 simbol
```

---

## 4. Alur request

```text
GET /me/watchlist
  → baca watchlist_symbols user
  → JOIN cache quote (in-memory / SQLite)
  → susun dokumen + ETag dari revisi cache
  → 200 atau 304
```

Tidak ada jalur yang menyentuh sumber data. Semua dari cache.

Loop background:

```text
tiap 15 s : fetch tier A (batch CNBC 500/request)
tiap 60 s : fetch tier B
harian    : depth cycle (sudah ada)
```

---

## 5. ETag — sudah siap

Sudah diimplementasi (commit `91a0682`, `a503158`):

```text
ETag = namespace + revisi store + as_of + query
304 = 0 byte
```

Yang perlu ditambah saat watchlist ada: `namespace="watchlist"` dan daftar
simbol user sebagai bagian kunci. Tidak ada pekerjaan baru di lapisan cache.

**Penghematan terukur:** polling 15 detik tanpa ETag ≈ 5 GB/hari untuk 1.500
simbol; dengan 304 ≈ 0,5–1 GB/hari.

---

## 6. Free vs premium

Satu cache, dua jeda:

```text
cache menyimpan nilai terbaru
  premium → kirim apa adanya
  free    → kirim nilai 15 menit lalu (dari history)
```

Butuh penyimpanan history ringan. Usul: snapshot quote terkompresi per 15
menit, retensi 24 jam (~50–100 MB) — atau mulai tanpa history dan free user
mendapat data harian saja.

---

## 7. Tahapan

| Phase | Isi | Usaha |
|---|---|---|
| 1 | tabel user + auth (API key dulu) | 1 hari |
| 2 | tabel watchlist + endpoint CRUD | 1 hari |
| 3 | hot-set refcount + loop tier A/B | 1,5 hari |
| 4 | `GET /me/watchlist` + ETag | 0,5 hari |
| 5 | tier & batas (200 simbol, 3.000 hot set) | 0,5 hari |
| 6 | uji beban 100 user sintetis | 0,5 hari |

**Total ~5 hari.** Phase 1–2 sudah memberi nilai walau quote loop belum ada
(watchlist bisa disimpan dan ditampilkan dengan data harian).

---

## 8. Tiga keputusan yang menunggu

**a. Auth**
```text
API key per user (X-API-Key)   → paling cepat, cukup untuk API publik
email + password + JWT         → perlu reset password, verifikasi
Google/Apple sign-in           → paling nyaman, tambah dependensi
```

**b. Pembeda free vs premium**
```text
delay data (free 15 menit)     ← paling umum untuk produk data
batas watchlist (20 vs 200)
batas query screener per hari
```

**c. Watchlist di server atau browser**
```text
server  → lintas device, butuh auth + tabel
browser → nol backend, hilang saat ganti device
```

---

## 9. Skala

| User | Simpanan | Catatan |
|---|---|---|
| 100 | SQLite | sekarang |
| 1.000 | SQLite + ETag | masih nyaman |
| 10.000 | SQLite (WAL) | watchlist 500k baris, masih OK |
| 50.000+ | **Postgres** | lihat `2026-10-31_postgres-migration.md` |

Redis baru masuk bila API >1 proses atau ada websocket fan-out.

---

## 10. Non-goals

- Websocket push. Polling + ETag cukup sampai >500 user bersamaan.
- Watchlist real-time per user di server (cache bersama sudah cukup).
- Billing. Tier disimpan sebagai kolom; penagihan menyusul.
