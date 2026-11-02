# Row actions without a checkbox column

Tanggal 2026-11-02. Pemilik: Zaaaxx. Status: rencana, belum dieksekusi.

## Masalah

Saat panel samping dicabut, Add to / Compare / Set alert kehilangan cara memilih
baris, jadi ditambahkan kolom checkbox di depan tabel. Kolom itu memakan 30 px,
terlihat setiap saat, dan mengotori tampilan yang seharusnya padat.

## Tujuan

Ketiga aksi tetap bisa dipanggil tanpa kolom checkbox permanen, tanpa gesture
tersembunyi, dan tetap bisa dipakai keyboard.

## Pilihan

| # | Cara | Kelebihan | Kekurangan | Nilai |
|---|------|-----------|------------|-------|
| A | Ikon mikro muncul saat hover di ujung kanan baris (star, compare, bell) | Nol lebar permanen, langsung di baris yang dimaksud | Tidak terlihat di layar sentuh; perlu tombol "⋯" untuk keyboard | Terbaik untuk desktop |
| B | Toolbar membuka pencarian ticker sendiri (tanpa seleksi) | Tidak menyentuh tabel sama sekali | Kehilangan konteks baris; satu langkah ekstra | Bagus sebagai pelengkap |
| C | Klik kanan / long-press menu konteks | Familiar di desktop | Tidak ditemukan pengguna; tidak ada di keyboard | Buruk |
| D | Seleksi via klik baris + modifier | Tanpa UI tambahan | Klik baris sudah dipakai navigasi; bentrok | Buruk |
| E | Kolom checkbox disembunyikan sampai hover | Cepat dibuat | Baris bergeser saat hover; tetap ada kolom | Sementara, bukan solusi |

## Rekomendasi: A + B

1. **Hapus kolom checkbox.** Tabel kembali selebar konten, tanpa lane tambahan.
2. **Aksi mikro per baris.** Di ujung kanan baris (setelah kolom terakhir,
   posisi absolute agar tidak menggeser layout):
   - ★ Add to (membuka menu Watchlist / Portfolio)
   - ⇄ Compare (menambah simbol ke pembanding, maks 5)
   - 🔔 Set alert
   Ikon 20 px, muncul saat baris di-hover **atau** difokus keyboard, dengan
   latar memudar agar angka di belakangnya tidak terbaca acak.
3. **Tombol "⋯ Row actions" untuk keyboard dan sentuh.** Selalu ada di ujung
   baris pada layar < 900 px dan saat baris difokus; membuka menu yang sama.
   Ini yang membuat fitur tidak bergantung pada hover.
4. **Toolbar tetap punya Add to / Compare / Set alert** sebagai pintu kedua:
   membuka pencarian ticker (B) bila tidak ada baris yang sedang disorot.
   Dengan begitu toolbar tidak pernah menjadi tombol mati.
5. **Tanpa state seleksi.** Tidak ada `selectedSymbols[]`, tidak ada checkbox,
   tidak ada select-all. Aksi selalu bekerja pada satu simbol yang jelas:
   baris yang di-hover/difokus, atau yang dipilih lewat pencarian.

## Rincian teknis

- `DataTable`: hapus lane seleksi; tambah `rowActions` opsional (render di
  sel terakhir dengan `position: relative` pada `<tr>`), dan
  `data-row-actions` untuk tes. Ikon memakai `stopPropagation` supaya tidak
  memicu navigasi baris.
- `ScreenPanel`: hapus `selection` state dan reset-on-page-change.
- Toolbar: `AddToDialog`, `CompareDialog`, `AlertDialog` sudah menerima simbol
  tunggal — tambah mode "tanpa simbol" yang membuka `SymbolSearch` lebih dulu.
- A11y: setiap ikon punya `aria-label`; menu "⋯" adalah `<button>` dengan
  `aria-haspopup="menu"`; Escape menutup dan mengembalikan fokus ke baris.
- Tes: baris tanpa checkbox; hover menampilkan 3 aksi; klik ★ tidak menavigasi;
  Enter pada baris tetap navigasi; "⋯" bisa dijangkau Tab dan membuka menu;
  toolbar tanpa baris terpilih membuka pencarian, bukan tombol mati.

## Ukuran

Satu agen, ~40 panggilan: `DataTable` + `ScreenPanel` + 3 dialog + tes.
Tidak ada perubahan backend.

## Yang tidak dilakukan

Tidak ada multi-select massal (mis. "tambahkan 20 baris ke watchlist").
Kalau nanti dibutuhkan, jalurnya adalah menu "⋯" di header tabel, bukan
checkbox permanen.
