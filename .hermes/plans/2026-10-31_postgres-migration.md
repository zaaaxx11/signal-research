# Postgres Migration Plan — dokumen saja, belum dieksekusi

**Status:** rencana. Tidak ada kode yang berubah karena dokumen ini.

**Kapan dipakai:** saat salah satu pemicu di §1 terpenuhi. Bukan sebelum.

---

## 1. Pemicu (bukan angka user saja)

`>50.000 user` adalah indikator pendukung, bukan syarat. 50.000 terdaftar dengan
200 aktif tidak butuh Postgres; 5.000 user dengan API 4 proses sudah butuh.

| # | Pemicu | Kenapa SQLite berhenti |
|---|---|---|
| 1 | API jalan **>1 proses/instance** | state per-proses (rate limit, cache) tidak dibagi |
| 2 | **write/detik** ke watchlist > ~50 | SQLite satu penulis; antrean muncul |
| 3 | butuh **read replica / HA** | SQLite tidak punya replikasi |
| 4 | butuh **RLS** per pelanggan | SQLite tidak punya row-level security |
| 5 | database **>50 GB** | backup & restore mulai menyakitkan |

Indikator pendukung: `>50.000 user terdaftar` **dan** `>5.000 DAU` **dan**
`>1.000 koneksi bersamaan`.

**Aturan:** migrasi hanya dimulai bila satu pemicu struktural (1–5) benar.
Angka user sendiri tidak pernah cukup.

---

## 2. Yang pindah, yang tidak

```text
PINDAH ke Postgres
  users · sessions · api_keys
  watchlists · watchlist_symbols
  rate_limits (kalau multi-proses)
  jobs (kalau pakai queue DB)

TETAP sebagai file JSON
  engine-snapshot.json    identitas + facts SEC   (backup + interchange)
  depth.json              106 kolom × 4.217       (backup + interchange)

TETAP SQLite
  screen_rows, screens, fundamentals_snapshots     kecuali jadi multi-proses
```

Alasan mempertahankan JSON: sudah terbukti menyelamatkan data saat checksum
rusak (2026-10-29). Database adalah etalase; JSON adalah brangkas.

---

## 3. Skema awal

```sql
CREATE TABLE users (
  id            BIGSERIAL PRIMARY KEY,
  email         TEXT UNIQUE NOT NULL,
  password_hash TEXT,                    -- NULL bila hanya API key
  tier          TEXT NOT NULL DEFAULT 'free',   -- free | premium
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at  TIMESTAMPTZ
);

CREATE TABLE api_keys (
  id         BIGSERIAL PRIMARY KEY,
  user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  key_hash   TEXT NOT NULL UNIQUE,       -- simpan hash, bukan kunci
  label      TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at TIMESTAMPTZ
);

CREATE TABLE watchlists (
  id         BIGSERIAL PRIMARY KEY,
  user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name       TEXT NOT NULL DEFAULT 'default',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, name)
);

CREATE TABLE watchlist_symbols (
  watchlist_id BIGINT NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
  symbol       TEXT NOT NULL,
  added_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (watchlist_id, symbol)
);
CREATE INDEX ON watchlist_symbols (symbol);   -- untuk hot-set refcount

CREATE TABLE rate_limits (
  caller     TEXT NOT NULL,
  window_start TIMESTAMPTZ NOT NULL,
  count      INT NOT NULL DEFAULT 0,
  PRIMARY KEY (caller, window_start)
);
```

Index screener (bila `screen_rows` ikut pindah):

```sql
CREATE INDEX ON symbols (pe_ttm);
CREATE INDEX ON symbols (market_cap_usd DESC);
CREATE INDEX ON symbols (sector);
CREATE INDEX ON symbols (exchange);
```

---

## 4. Jalur migrasi (tanpa downtime)

```text
Fase A — siapkan
  1. Postgres 16 di box yang sama (atau managed)
  2. skema + migrasi Alembic
  3. repository layer: satu antarmuka, dua implementasi (sqlite, postgres)

Fase B — dual write
  4. tulis ke SQLite DAN Postgres untuk tabel yang pindah
  5. baca masih dari SQLite
  6. jalankan 3–7 hari; bandingkan hasil tiap jam (job rekonsiliasi)

Fase C — backfill
  7. salin data lama (users, watchlists) ke Postgres
  8. rekonsiliasi: SELECT count + checksum per tabel harus sama

Fase D — cutover
  9. env MEE_STORE=postgres
 10. baca dari Postgres, tulis tetap dual selama 24 jam
 11. matikan dual write

Fase E — rollback (harus tetap mungkin 7 hari)
 12. env MEE_STORE=sqlite → kembali, karena SQLite masih ditulis
```

**Rollback bukan "restore backup".** Selama dual-write hidup, SQLite tetap
lengkap — balik cukup satu env var.

---

## 5. Yang harus dibuktikan sebelum cutover

```text
[ ] parity: jumlah baris + checksum identik per tabel
[ ] latency select p95 tidak naik >20%
[ ] write watchlist p95 < 20 ms
[ ] rollback diuji sungguhan (bukan diasumsikan)
[ ] backup harian Postgres + restore diuji ke DB kosong
[ ] /health melaporkan status kedua store selama transisi
```

---

## 6. Perkiraan usaha

```text
skema + Alembic + repository abstraction     1,5 hari
dual write + rekonsiliasi                    1 hari
backfill + uji parity                        0,5 hari
cutover + rollback drill                     0,5 hari
─────────────────────────────────────────────────────
total                                        3,5 hari
```

---

## 7. Non-goals

- Memindahkan `engine-snapshot.json` dan `depth.json` ke tabel. Keduanya file
  besar yang ditulis utuh per siklus; tabel akan menambah biaya tanpa manfaat.
- Sharding. Belum relevan sampai >1 juta simbol.
- Managed service berbayar sebelum ada pendapatan.
