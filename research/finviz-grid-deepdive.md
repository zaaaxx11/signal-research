# Finviz — deep teardown: CSS architecture, grid internals, DOM economics

Lanjutan dari `finviz-frontend-html.md`, `finviz-ui-assets.md`, `finviz-event-model.md`.
Kali ini yang dibedah adalah **mesin di dalam grid**: bagaimana CSS-nya disusun, bagaimana sel
ditulis, dan berapa ongkos DOM-nya dibanding kita. Diukur 2026-10-31 19:20–19:45 UTC.

## 1. Arsitektur CSS: dua berkas, dua filosofi

| berkas | ukuran | blok | @media | !important | layer | isi |
|---|---:|---:|---:|---:|---|---|
| `redesign.5c084819.css` | 242.529 char | 1.821 | 132 | 81 | properties/base/components | komponen tulis tangan: `.header` 49 · `.content` 24 · `.navbar` 22 · `.fv-insider-row` 18 · `.quote-bar-chart` 15 · `.financials-table` 14 |
| `main.611bc50f.css` | 353.272 char | 4.519 | 93 | 193 | properties/theme/base/components/utilities | keluaran Tailwind v4: `.hover` 93 · `.lg` 67 · `.sm` 61 · `.xl` 61 · `.group-hover` 56 · `.w-` 53 |
| **total** | **596 KB** | **6.340** | **225** | **274** | | |

Kita, sebagai pembanding:

```text
61.153 char · 832 blok · 16 @media · 5 !important · 5 @layer
```

Artinya: mereka memakai **dua sistem** — utilitas Tailwind untuk tata letak dan CSS komponen
tulis tangan untuk bagian yang benar-benar dirancang (header, navbar, tabel financials).
Kita memakai satu sistem (token + utilitas). Itu bukan kekurangan; 832 blok untuk seluruh app
adalah hasil dari tidak menulis ulang CSS komponen lama.

Harga yang mereka bayar: 274 `!important` dan 225 media query. Itu tanda dua sistem yang saling
menimpa. **Jangan ditiru.**

Properti performa yang mereka pakai (dan kita hampir tidak):

```text
                     finviz   ztockz
content-visibility        7        1
contain-intrinsic         5        1
will-change               6        0
contain                   3        0
backface-visibility       1        0
```

## 2. Mesin grid: tiga lane, geometri lewat CSS variable

Dari bundle `9245.v1.fe80d911.js` (138.491 B), konstanta internalnya berbicara sendiri:

```js
me="--fv-col-",            // prefiks per kolom: --fv-col-<id>-l, --fv-col-<id>-w
ue="--fv-table-left-w",    // lebar lane kiri (pinned)
he="--fv-table-center-w",  // lebar lane tengah
pe="--fv-table-right-w",   // lebar lane kanan
fe="--fv-table-total-w",   // total
ge="fv-table_drag-ghost", ve="data-dragging",
ye="fv-table_column-drag-band",
we="fv-table_group-row", be="fv-table_group-cell", xe="fv-table_group-toggle"
```

```text
model       3 lane (kiri / tengah / kanan) + baris grup kolom yang bisa dilipat
geometri    tiap sel: left:var(--fv-col-<id>-l); width:var(--fv-col-<id>-w)
            → memindahkan lane atau me-resize kolom = menulis satu variabel, bukan mengukur 220 sel
drag        ghost + band khusus untuk drag kolom, status di data-dragging
baris       transform: translateY(...), bukan top/height
```

Dan yang paling penting — **penulis sel-nya imperatif, bukan React**:

```js
const c = (el, next) => {
  const prev = cache.get(el) ?? {};
  for (const k in next) if (prev[k] !== next[k]) { el.style.setProperty(k, next[k]); metrics.domWrite(); }
  for (const k in prev) if (!(k in next)) { el.style.removeProperty(k); metrics.domWrite(); }
  cache.set(el, next);
};
```

Mereka menyimpan snapshot properti terakhir per elemen dan hanya menulis yang berubah. React
dipakai untuk struktur; teks dan gaya baris ditulis langsung ke DOM.

## 3. Telemetri render di dalam produk

Bundle yang sama memuat objek metrik yang dibuat untuk mengukur dirinya sendiri:

```js
begin(kind, reasons) → { at, ms, kind, trigger, reasons,
  bandsCleared:{left,center,right}, blanked:{…}, cells:{…}, domWrites,
  placeholders:{…}, portals, rowsDeferred, rowsEntered, rowsExamined, rowsLeft,
  rowsWritten, texts:{…}, wholeRows:{…} }
end() → push ke ring buffer `recent`, agregat { passes, ms, rowsExamined, rowsWritten, … }
```

```text
yang mereka hitung per render pass:
  berapa baris diperiksa / ditulis / ditunda / masuk / keluar
  berapa sel ditulis, berapa teks, berapa placeholder per lane
  berapa DOM write, berapa portal, berapa band dibersihkan
```

Ini bukan logging debug yang tertinggal — ini **instrumentasi produk** untuk menjaga anggaran
render saat kolom dan lane bertambah. Kita punya probe eksternal (`scripts/ui_perf_probe.py`),
tapi tidak ada metrik render di dalam app. Itu perbedaan budaya engineering yang nyata.

## 4. Ongkos DOM: kita lebih boros di gambar, lebih hemat di elemen

| | Finviz (20 baris) | ztockz (100 baris) | per baris |
|---|---:|---:|---:|
| elemen total | 1.083 | 4.447 | 39 vs 44 |
| `<div>` | 483 | 145 | — |
| `<span>` | 167 | **2.488** | 8 vs 25 |
| `<td>` / cell | 220 (gridcell) | 1.200 | 11 vs 12 |
| `<a href>` | **108** | **1** (skip link) | 5,4 vs 0,01 |
| `<svg>` | 51 | 1 | 2,5 vs 0,01 |
| `<img>` | 21 | **100** (logo per baris) | 1 vs 1 |
| `<button>` | 41 | 62 | — |

Tiga kesimpulan yang bisa dipertanggungjawabkan:

1. **Per baris kita setara** (44 vs 39 elemen). Tidak ada pemborosan struktural.
2. **Span kita 2× lebih banyak per sel** (1,95 span/sel vs ~0,8). Setiap sel membawa wrapper
   angka + satuan/panah; mereka menaruh teks langsung di gridcell.
3. **Kita nyaris tidak punya anchor dan ikon.** 1 vs 108 anchor; 1 vs 51 svg. Pagination kita
   tombol, baris kita `tr` + handler; mereka `<a href>` asli + sprite ikon.

Dan satu biaya yang tidak terlihat di elemen: kita memuat **100 logo `<img>`** per halaman
karena 100 baris; mereka 20 karena 20 baris. Kalau density dinaikkan tanpa mengubah pagination,
biaya gambar ikut naik.

## 5. Gerak (motion)

```text
durasi terbanyak   .25s (250 ms) · .15s · .3s · .2s · 50ms · 75ms · .5s
easing             cubic-bezier(.4,0,.2,1)      ← Material standar
                   cubic-bezier(.22,1,.36,1)    ← easeOutQuint (masuk)
                   cubic-bezier(.23,1,.32,1)    ← easeOutQuint varian
                   cubic-bezier(.175,.885,.32,1.275)  ← spring halus untuk popover
```

Kita memakai transisi 150–200 ms di beberapa tempat, tanpa skala. Bukan masalah besar, tapi
"terasa premium" sebagian berasal dari konsistensi 250 ms + satu easing keluarga.

## 6. Yang layak diadopsi (urut dampak, dengan alasan teknis)

| # | pola | bukti | usaha |
|---|---|---|---|
| 1 | **Density**: baris 20–22 px, padding sel 4–6 px, chrome ≤150 px | 21 vs 7 baris terlihat | 1 hari |
| 2 | **Anchor asli untuk baris + pager** | 108 vs 1 anchor; middle-click, no-JS, prefetch | 1 hari |
| 3 | **Satu span per sel** (teks langsung, satuan via `::after`) | 1,95 vs 0,8 span/sel | 0,5 hari |
| 4 | **Telemetri render di app** (passes, domWrites, rowsWritten) | mereka punya; kita hanya probe luar | 0,5 hari |
| 5 | **`content-visibility` + `contain-intrinsic-size`** pada baris | mereka 7+5, kita 1+1 | 0,25 hari |
| 6 | **Motion scale** 250 ms + 2 easing | konsisten, bukan acak | 0,25 hari |

Ditunda (bukan ditolak): penulis sel imperatif dan tiga lane. Keduanya masuk akal kalau kita
suatu hari butuh 500+ baris dalam satu tampilan; untuk 100 baris React masih cukup.

## 7. Yang jangan ditiru

```text
596 KB CSS · 274 !important · 225 media query     dua sistem yang saling menimpa
2.241 listener (30 untuk UI, sisanya iklan)       satu farm 139 listener di kotak search
0 <main>, 0 <h1>, tanpa <nav>, tanpa aria-current  landmark tidak ada
tombol raksasa di mana anchor seharusnya ada      9 pager <a> vs tombol kita: justru mereka benar
```

## 8. Catatan kejujuran

- Angka 39 elemen/baris Finviz diturunkan dari 1.083 total dikurangi chrome perkiraan; angka
  per baris kita (44) dihitung dari 4.447/100. Keduanya perkiraan yang wajar, bukan hitungan
  pembagian yang presisi.
- Telemetri render dibaca dari **sumber bundle**, bukan dari runtime: objeknya module-scoped,
  tidak diekspos ke `window`.
- Bundle `9245` yang sama tidak memuat handler pointer; handler dipasang di chunk lain
  (`885`, `1679`, `2468` memuat `mousedown`/`keydown`). Pembacaan handler per-node sudah
  dilaporkan di `finviz-event-model.md` lewat `DOMDebugger`, bukan dari sumber ini.
- Tab Finviz yang lolos Cloudflare sudah tidak aktif; analisis di atas karena itu statis
  (bundle + CSS + HTML), bukan pengukuran ulang di browser.
