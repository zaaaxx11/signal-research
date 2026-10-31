# Finviz event model & DOM logic — measured

Diambil 2026-10-31 19:00–19:15 UTC dari `finviz.com/screener/stocks?v=111`, Chromium desktop.
Dua metode: (1) perekam `addEventListener` disuntik sebelum halaman dimuat untuk sensus lengkap,
(2) `DOMDebugger.getEventListeners` per node untuk melihat siapa memegang apa.

## 1. Sensus listener saat load

```text
listener dipasang        2.241
listener dilepas           100
capture phase            1.072   (48%)
passive: true              170
passive: false               2
once                        13
```

Tipe terbanyak:

| event | jumlah | event | jumlah |
|---|---:|---|---:|
| mousedown | 202 | keyup | 39 |
| mouseleave | 107 | touchstart | 39 |
| scroll | 78 | keydown | 38 |
| mouseup | 61 | touchend | 37 |
| click | 60 | error | 34 |
| load | 42 | mousemove | 33 |
| visibilitychange | 42 | focusin | 29 |
| message | 39 | focusout | 29 |

Target terbanyak: `window` 227 · `#search` 138 · `#time` 138 · lima React root ×138 ·
`#ssr-island-1` 138 · `document` 126 · `.menu-item` 63 · `.cnx-button` 40.

Angka 138 pada setiap root itu pola **farm**: satu pustaka memasang satu listener untuk
*setiap tipe event* di setiap node yang ia sukai. `#search` memegang 139 listener capture
(`abort`, `auxclick`, `click`, `copy`, `drag`, …) padahal itu kotak pencarian.

## 2. Siapa memegang apa (bagian UI yang sebenarnya)

| node | listener | isi |
|---|---:|---|
| `document.body` | **2** | `mousedown`, `keydown` — delegasi utama |
| `.fv-table_viewport` | **8** | `focusin` ×2, `focusout` ×2, `scroll` (passive), `pointerover`, `pointerout`, `keydown` |
| `.fv-table_row` | **0** | tidak ada handler per baris |
| `.fv-table_row-link` | **1** per baris (20) | `mousedown` |
| `.navbar` | 0 | navigasi murni `<a href>` |
| `document` | 126 | mayoritas `mouseenter`/`mouseleave` capture (pustaka interaksi) |
| `window` | 196 | `visibilitychange`, `focus`, `pagehide` (analytics) |

Jadi seluruh grid — 20 baris × 11 sel, pinning, hover, keyboard — dijalankan oleh
**30 listener**: 2 + 8 + 20. Sisanya iklan, session replay, dan analytics.

## 3. Pola event yang bisa dicuri

1. **Delegasi, bukan per-baris.** Hover ditangani `pointerover`/`pointerout` di viewport,
   bukan `mouseenter` di 20 baris. Menambah 100 baris tidak menambah listener.
2. **`scroll` passive.** Satu-satunya listener scroll di viewport memakai `{passive:true}`,
   jadi browser tidak menunggu JS saat menggulir.
3. **`mousedown`, bukan `click`, untuk navigasi baris.** Setiap baris punya `<a href>` asli
   yang menutupi baris (`aria-hidden`, `tabindex=-1`), dan handler `mousedown` di anchor itu
   membuat navigasi mulai sebelum `mouseup` — itulah kenapa klik terasa instan. Fallback tanpa
   JS tetap jalan karena href-nya nyata.
4. **`focusin`/`focusout` di viewport** = roving focus untuk grid; fokus berpindah antar sel
   tanpa memasang handler di tiap sel.
5. **`body` memegang `keydown`**, jadi shortcut global tidak perlu listener di tiap komponen.
6. **Anchor asli untuk semua navigasi.** `.navbar` 0 listener; 13 dari 14 menu adalah `<a>`,
   jadi klik = dokumen baru, bukan router.

## 4. Logic DOM baris

Satu baris (dari HTML mentah, sebelum hidrasi) — struktur yang menjelaskan semuanya:

```html
<div class="fv-table_row" data-row-key="A" role="row" aria-rowindex="2"
     data-parity="even" data-row-index="0" data-link="true"
     style="transform:translateY(0px); --fv-row-height:…">
  <a class="fv-table_row-link" href="/quote?t=A&b=1&ty=c&p=d"
     aria-hidden="true" tabindex="-1" draggable="false"></a>   <!-- overlay seluruh baris -->
  <div class="fv-table_lane" data-lane="left" role="presentation">
    <div role="gridcell" aria-colindex="1" data-column-id="row"
         style="left:var(--fv-col-row-l); width:var(--fv-col-row-w)">1</div>
    …
  </div>
  <div class="fv-table_lane" data-lane="center" role="presentation"> … </div>
</div>
```

```text
geometri   posisi & lebar tiap sel = CSS variable (--fv-col-<id>-l / -w), di-set sekali
           → memindahkan lane (pin kiri) tidak mengukur ulang 220 sel
lane       left (pinned) + center; `data-lane` jadi penanda, bukan kelas berbeda
baris      transform: translateY(...) + --fv-row-height, bukan top/height
warna      data-tone="negative" pada sel, bukan class per nilai
nomor      aria-rowindex / aria-colindex sudah benar sejak HTML pertama
hover      dihitung dari pointerover + closest('[data-ticker]'), bukan listener per sel
```

## 5. Pembanding: app kita

| | Finviz | ztockz |
|---|---:|---:|
| listener saat load (UI saja) | ~30 | — |
| listener total (dengan iklan) | **2.241** | **161** |
| `window` | 196 | 16 |
| `document` | 126 | 2 |
| `body` | 2 | 0 |
| root aplikasi | 5×138 (React island per widget) | **143** (React 19, satu set delegasi) |
| `table` / baris | 0 / 0 | 0 / 0 |
| scroll | 1 passive di viewport | native + React onScroll |

Kita sudah memakai delegasi (React 19 memasang satu listener per tipe event di `#root`), jadi
**tidak ada handler per baris** di kedua produk. Perbedaannya: mereka punya 30 listener UI
karena lima island kecil, kita 143 karena satu root React menanggung semua tipe event.

## 6. Yang layak diadopsi

| # | pola | alasan | usaha |
|---|---|---|---|
| 1 | `pointerover`/`pointerout` + `closest()` untuk hover baris | 100 baris, 0 listener per baris, satu handler | 0,25 hari |
| 2 | `scroll` passive untuk strip/panel | browser tidak menunggu JS | 0,1 hari |
| 3 | navigasi baris lewat `<a href>` + `mousedown` | instan, tetap jalan tanpa JS, middle-click hidup | 0,5 hari |
| 4 | roving focus via `focusin`/`focusout` di container | keyboard tanpa handler per sel | 0,25 hari |
| 5 | audit listener pihak ketiga | 2.241 → 30 adalah selisih yang bisa dihapus | 0,1 hari |
| 6 | geometri sel lewat CSS variable | pin/resize tanpa mengukur ulang 220 node | 1 hari (kalau mau pin kolom) |

## 7. Catatan kejujuran

- Handler `mousedown` pada baris **tidak berhasil kubaca sumbernya**: `Runtime.callFunctionOn`
  pada objek handler perlu sesi yang sama, dan tab Finviz yang lolos Cloudflare sudah
  kunavigasikan pergi. Kesimpulan "navigasi dimulai saat mousedown" adalah inferensi dari
  (a) nama event, (b) anchor href asli, (c) tidak adanya handler `click` di baris — bukan
  pembacaan kode.
- Uji hover/klik dengan pointer asli gagal: `Input.dispatchMouseEvent` timeout 5 detik karena
  main thread Finviz jenuh (iklan + 22 baris × 11 sel ber-transform). Sensus listener tetap
  valid karena diambil lewat `DOMDebugger`, bukan lewat interaksi.
- Angka "30 listener UI" adalah penjumlahan node yang kuverifikasi satu per satu (body,
  viewport, row-link), bukan klaim bahwa tidak ada listener lain di elemen lain.
