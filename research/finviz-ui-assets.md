# Finviz UI/UX — assets, fonts, tokens (measured)

Diambil 2026-10-31 18:40–18:55 UTC dari `finviz.com/screener/stocks?v=111` dengan Chromium
desktop + `curl` (UA Chrome 141). Semua angka di bawah diukur; yang tidak bisa diukur ditandai.

## 1. Inventaris aset

| aset | jumlah | ukuran | catatan |
|---|---:|---:|---|
| stylesheet | 2 | 596,5 KB decoded · 84,4 KB wire | `main.611bc50f.css` 353 KB, `redesign.5c084819.css` 243 KB · 5.693 rule · Tailwind v4 |
| font | 4 wajah (2 diunduh) | 45.712 B normal + 50.184 B italic (latin) | IBM Plex Sans **variable** 100–900, latin + latin-ext |
| ikon | 1 sprite utama | 76.633 B (`icons.svg`) | 13 sprite family dideklarasikan di `FinvizSettings.iconSprites` |
| logo ticker | 1 SVG per simbol | 300 B – 5.066 B, rata-rata 6,2 KB | `logo.finviz.com/{TICKER}.svg`, 20 per halaman ≈ 123 KB |
| favicon | 2 | png 1x/2x | |
| gambar raster | 0 | — | seluruh visual tabel = canvas + SVG |

Hash (16 char) untuk memverifikasi unduhan yang sama: `main` 3796b81648f66227 ·
`redesign` aaf862ac5205ef0e · font normal e2291e842cf5af16 · italic 4363757d2695c763.
Berkasnya **tidak** dimasukkan ke repo (lisensi pihak ketiga); hanya analisis ini yang dikomit.

## 2. Tipografi — ini yang paling menentukan "premium"

```css
@font-face {
  font-family: "IBM Plex Sans";
  font-weight: 100 900;          /* satu berkas variabel, bukan 4 berat statis */
  font-display: swap;
  size-adjust: 105%;             /* font-nya digambar 5% lebih besar */
  src: url(/fonts/ibm-plex-sans-latin-normal.woff2);
}
@font-face {
  font-family: "IBM Plex Sans Fallback";   /* metrik Arial disamakan */
  src: local(Arial);
  ascent-override: 101%; descent-override: 27%; line-gap-override: 0%;
  size-adjust: 105%;
}
```

Dua hal yang jarang dikerjakan orang:

1. **Variable font + `size-adjust: 105%`** — satu berkas 45 KB melayani 100–900, dan ukurannya
   dinaikkan 5% supaya x-height IBM Plex setara dengan sistem font. Itu sebabnya teks 12 px
   mereka terasa lebih besar dari 12 px kita tanpa mengubah `font-size`.
2. **Metric-matched fallback** — `IBM Plex Sans Fallback` adalah Arial dengan ascent/descent/
   line-gap/size di-override. Selama font asli dimuat, teks memakai fallback yang **tingginya
   identik**, jadi tidak ada pergeseran layout (CLS ≈ 0). Ini yang membuat first paint terasa
   "sudah jadi" bukan "berkedip".

| ukuran | nilai | line-height | dipakai untuk |
|---|---|---|---|
| 3xs | `.625rem` 10 px | 18 px | label mikro, legend |
| 2xs | `.75rem` 12 px | **20 px** | **sel tabel, header, tombol** |
| xs | `.8125rem` 13 px | 20 px | teks UI sekunder |
| sm | `.875rem` 14 px | 1,43 | body panel |
| base | `1rem` 16 px | 24 px | dokumen |
| lg | `1.125rem` 18 px | 1,56 | judul kecil |

```text
berat: 300 · 400 · 420 · 440 · 500 · 600 · 700 · 800
body 16px/440 · tabel 12px/440 · header 12px/500 · tombol 12px/500
tracking: tight -0.025em · normal 0 · wide 0.025em · wider 0.05em
angka:   font-variant-numeric: tabular-nums hanya di 11 tempat (3 main + 8 redesign), bukan global
```

`440` bukan angka bulat — itu titik di sumbu variabel yang membuat 12 px terlihat tegas tanpa
jadi bold. Kita memakai 400 dan menebusnya dengan `font-weight: 700` di header: dua ekstrem,
bukan satu titik.

## 3. Warna — lima lapis permukaan, tiga tingkat teks

```text
permukaan  #14161d  page / bg-secondary
           #1c1f27  base
           #22262f  primary (header tabel, baris)
           #2c303b  neutral / raised
           #30343f  surface (chip, kontrol)
border     #353945  secondary   ·  #4c5263  primary
teks       #f3f3f5  default     ·  #c3c6d0  muted     ·  #a3a8b9  muted-2
aksen      #2f91ef  link/highlight  ·  #57aefb hover
positif    #34c062  ·  negatif #fd8487  ·  caution #fcdc86
bayangan   --shadow-color #14161d1f  (8% dari warna page, bukan hitam)
radius     xs 2 · sm 4 · md 6 · lg 8 · xl 12 · 2xl 16 · 3xl 24 px
```

Yang membuatnya terlihat "mahal": bayangan memakai **warna permukaan**, bukan hitam; dan
setiap lapisan hanya berbeda 6–10 unit kecerahan, jadi kedalaman terbaca tanpa garis tebal.

## 4. Geometri — di sinilah kita kalah paling jauh

```text
--fv-header-height            126px        (chrome total)
--fv-table-header-height      24px
--fv-table-row-height-base    20px         (= line-height teks 2xs)
--fv-table-control-size       24px         (tombol, input, pager)
--fv-table-cell-padding-x     4px
--fv-table-scrollbar-size     15px         (thumb inset 3px)
--fv-table-copy-flash         700ms        (umpan balik copy)
--fv-ticker-row               24px
```

Diukur di Chromium (1280×633):

| | Finviz | ztockz | selisih |
|---|---:|---:|---:|
| tinggi baris | **20 px** | 28 px | +40% |
| padding sel | 0 4 px | 0 8 px | 2× |
| tinggi header tabel | 24 px | 24 px | sama |
| tinggi tombol/tab | 24 / 28 px | **35 px** | +25–46% |
| radius kontrol | 6 px | 0 px (tab) | — |
| chrome atas | 126 px | 48 px + tab 35 + strip 223 | +180 px |
| **baris terlihat @633 px** | **≈21** (dari CSS: (633−198)/20) | **7** (terukur) | **3×** |

Tujuh baris. Bukan karena font kita jelek — karena **strip breadth 223 px + chrome 83 px +
baris 28 px**. Finviz menampilkan tiga kali lebih banyak saham di layar yang sama.

## 5. Yang mereka kerjakan dan kita belum

1. **Font variabel + fallback bermetrik sama.** Kita memakai `ui-sans-serif, system-ui` —
   tampilan berbeda di tiap OS, tanpa sumbu berat, dan tanpa perlindungan CLS.
2. **Berat 420/440 sebagai default teks.** Satu titik di sumbu variabel, bukan 400 vs 700.
3. **Kepadatan 20 px.** Kita 28 px dan padding 8 px; baris kita 40% lebih tinggi.
4. **Chrome 126 px.** Kita memakai 83 px chrome + 223 px strip = 306 px sebelum baris pertama.
5. **Kontrol 24 px + radius 6 px.** Kita 35 px, sebagian radius 0.
6. **`size-adjust` 105%.** Teks 12 px mereka setara 12,6 px kita — tanpa mengubah ukuran.

## 6. Yang kita lakukan lebih baik (jangan ikut-ikutan)

```text
JS awal        130,7 KB vs 1,01 MB          (7,7× lebih ringan)
third-party    0% vs 55,2%                  (nol iklan/analytics)
revalidasi     304 dalam 33 ms vs mustahil  (dokumen mereka no-cache tanpa validator)
semantik       caption, scope=col, aria-current, skip link, satu live region
               mereka: 0 <main>, 0 <h1>, tanpa <nav>, tanpa aria-current
state data     tabel dari API 2,6 KB + pushState; kita 38 KB per 100 baris
```

## 7. Enam langkah, urut dampak

| # | langkah | dampak | usaha |
|---|---|---|---|
| 1 | **Baris 28 → 22 px**, padding sel 8 → 6 px | +27% baris terlihat langsung | 0,5 hari |
| 2 | **Strip breadth 223 → 56 px** (ringkas: angka + bar tipis, detail di hover) | +6 baris | 0,5 hari |
| 3 | **Font variabel** (IBM Plex Sans atau Geist Variable) + fallback bermetrik | konsisten lintas OS, nol CLS | 1 hari |
| 4 | **Berat teks 400 → 420/440**, header 700 → 500 | teks 12 px lebih tegas, header tidak berteriak | 0,25 hari |
| 5 | **Kontrol 35 → 28 px**, radius 6 px seragam | chrome lebih ringkas | 0,5 hari |
| 6 | **`size-adjust` + `tabular-nums` selektif** | angka sejajar, teks terasa lebih besar | 0,25 hari |

Total ~3 hari untuk menutup jarak yang paling terasa. Yang **tidak** perlu dicontek: 596 KB CSS
mereka (kita 11 KB), 55% iklan, dan DOM tanpa landmark.

## 8. Catatan kejujuran

- Baris terlihat Finviz dihitung dari token CSS (`--fv-header-height` 126, header tabel 24,
  baris 20) karena DOM tabelnya tidak termuat pada percobaan terakhir (Cloudflare + iklan).
  Angka 21 adalah aritmetika, bukan hitungan DOM; angka kita (7) terukur langsung.
- `size-adjust: 105%` memengaruhi tinggi baris teks; efek visualnya terukur sebagai rasio,
  bukan pengukuran piksel per glif.
- Berkas font/CSS mentah tidak dikomit; hanya hash dan angka di laporan ini.
