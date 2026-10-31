# Exchange coverage — plan (research → implementation)

**Status:** rencana. Tidak ada kode yang berubah karena dokumen ini.
**Dasar:** tiga riset read-only pada `a71fd93` — `research/exchange-datalayer.md`,
`research/exchange-contract.md`, `research/exchange-ui.md` — plus pengukuran live.
**Satu kalimat:** cakupan bursa kita sudah lengkap (4 venue, 5.576 saham); yang rusak adalah
**kosakata** (filter hanya mengerti MIC, nilai salah balas 0 tanpa error), **metadata**
(tidak ada daftar bursa untuk UI), dan **tampilan** (kolom menulis `XNAS`, bukan `NASDAQ`).

---

## 0. Fakta terukur (bukan asumsi)

### Cakupan, sumber per sumber

| venue | screener kita | Finviz (`f=exch_*`) | selisih |
|---|---:|---:|---:|
| NASDAQ (XNAS) | 3.308 | 3.438 | −130 |
| NYSE (XNYS) | 2.009 | 1.927 | +82 |
| AMEX (XASE) | 258 | 245 | +13 |
| CBOE (XCBO) | 1 | 1 | 0 |
| TXSE | 0 | 9 | −9 |
| **total** | **5.576** | **5.620** | −44 |

Tidak ada venue keenam yang tersembunyi: 3.308 + 2.009 + 258 + 1 = 5.576 = `universe_total`.

### Siapa yang menulis exchange

```
TradingView (scanner)  → build_universe._normalize → mic_for()        universe.py:446,451
                       → satu-satunya penulis di jalur /screens
SEC EDGAR              → engine-snapshot.exchange → depth.json        (sumber lain, 4.217 baris)
depth overlay          → fill-missing-only, tidak pernah menimpa sel yang ada
```

`mic_for` hanya memetakan 4 kode (NASDAQ/NYSE/AMEX/CBOE); kode lain lewat apa adanya
(`SIX`, `HKEX`, `BCS`). **`mic_for` tidak punya satu pun test.**

### Ketidaksepakatan sumber (full-set join 5.576 × 4.217 → 4.187 simbol sama)

| pola | jumlah |
|---|---:|
| depth = NYSE, screener = XASE | **164** |
| depth = TXSE, screener = XASE | 9 |
| depth = kode Yahoo (`NasdaqGM` dst), screener = XNAS | 6 |
| identik | 4.008 |
| **tidak identik** | **179** |

Sebabnya terukur: dalam 10.433 record EDGAR, kosakata venue hanya
`{Nasdaq, NYSE, OTC, CBOE, TXSE, null}` — **EDGAR tidak pernah menulis AMEX / NYSE American**.
Jadi 258 baris AMEX tidak bisa diverifikasi dari tulang punggung resmi; TradingView yang
menjadi otoritasnya, dan itu keputusan yang harus eksplisit (§1 D4).

### Kosakata filter hari ini

```
exch eq XNYS     → 2.009      exch eq NYSE   → 0     ← salah, tanpa error
exch eq "XNYS "  → 0                                ← spasi menggagalkan
exch eq nope     → 0                                ← "tidak ada" palsu
exch in [XNAS,XNYS] → 5.317   exch contains nas → … (contains ada, tidak dipakai UI)
```

Error 422 hanya muncul untuk operator yang salah (`gt`, `between`), bukan untuk nilai yang salah.

### Biaya yang sudah diukur untuk endpoint baru

```
breadth hangat            14 ms      breadth pass         11,25 ms
build_universe region us  237 ms     global scan          16,2 s / build 3,65 s
tally 17 bucket (1.900 baris)  0,31 ms · 462 B JSON
```

Jebakan: breadth memakai universe **5.678** baris, `/screens/select` **5.576**, karena
`min_market_cap` berbeda (`discovery.py:388-395` vs `screens.py:1199-1203`). Endpoint baru
harus memakai universe yang sama dengan select, atau angkanya akan beda 3,8%.

---

## 1. Keputusan yang kuambil (dan alasannya)

| # | Keputusan | Alasan |
|---|---|---|
| D1 | **Alias nilai** di tabel baru `VALUE_ALIASES` di `app/screen/filters.py`, diselesaikan di `compile_filter` sebelum `Criterion` dibentuk | `SCREEN_ALIASES` menulis ulang sel respons (depth_overlay), `LEGACY_COLUMN_ALIASES` mati bersama `/ui`, `x-filter-aliases` hanya level key |
| D2 | Nilai tak dikenal → **422** dengan daftar nilai sah untuk region itu; bukan 0 | "0 baris" dan "kamu salah ketik" adalah dua kebenaran berbeda |
| D3 | Endpoint baru **`GET /market/exchanges`**, bukan menambah beban breadth | breadth sudah 2 konsumen; route sendiri punya ETag dan bentuk yang jelas |
| D4 | **TradingView menang** untuk baris screener; EDGAR menang untuk detail yang bertanda | Universe adalah domain TradingView; 258 baris AMEX tidak ada di EDGAR, jadi memaksa EDGAR berarti menghapus satu venue |
| D5 | **TXSE tidak jadi bucket** sampai kita punya sumbernya | 9 ticker itu AMEX menurut TradingView; membuat bucket dari EDGAR akan menghasilkan 9 baris yang bertentangan dengan AMEX |
| D6 | Label tampilan dari **leaf module klien + kode sebagai fallback**, bukan field per baris | 17 kode beredar di data, server hanya tahu 4; field per baris = +2,3 KB per halaman untuk string yang tidak berubah |
| D7 | Kontrol **multi-select**, nilai = kode MIC, satu chip untuk himpunan | Dua `eq` di-AND jadi nol baris; `in` sudah first-class di seluruh jalur |
| D8 | `exchange.min` 84 → **104**, `display` lewat `ColumnSpec` | "NYSE American" 13 karakter; `table-layout: fixed` + `<colgroup>` menjaga geometri |

---

## 2. Fase dan tugas

### Phase 0 — Kontrak filter (setengah hari)

**Tujuan:** `exch eq NYSE` menjawab 2.009; `exch eq nope` menjawab 422; `exch` tetap MIC di respons.

| # | Tugas | Berkas | Test (nama) |
|---|---|---|---|
| 0.1 | `VALUE_ALIASES: dict[str, dict[str, str]]` — `{"exchange": {"nyse":"XNYS","nasdaq":"XNAS","nasd":"XNAS","amex":"XASE","cboe":"XCBO"}}` | `app/screen/filters.py` | `test_a_human_exchange_value_resolves_to_its_mic` |
| 0.2 | Resolusi di `_value`/`compile_filter` (casefold + strip **hanya** untuk lookup alias; nilai asli tetap divalidasi) | `filters.py` | `test_an_alias_is_resolved_before_the_criterion_is_built` |
| 0.3 | Validasi nilai: untuk `region=us` hanya `{XNAS,XNYS,XASE,XCBO}`; nilai lain → `422 unknown_value` dengan `allowed[]` | `filters.py`, `selection.py` | `test_an_unknown_exchange_is_a_422_not_an_empty_page` |
| 0.4 | Publikasikan kosakata: `Binding.as_dict` → `x-filter-keys.exch.values` | `selection.py` | `test_the_accepted_exchange_values_are_published` |
| 0.5 | Test nilai respons tidak berubah: `exch eq XNYS` mengembalikan sel `XNYS` walau diminta `NYSE` | `tests/test_screen_selection_api.py` | `test_the_alias_never_rewrites_the_row_value` |

**Gate:** `exch eq NYSE` = `exch eq XNYS` = 2.009; `exch eq nope` → 422; ETag sama untuk kedua
bentuk (digest request kanonik).

### Phase 1 — Endpoint metadata (1 hari)

**Tujuan:** UI tidak menebak; ada satu sumber daftar bursa + jumlah.

```
GET /market/exchanges?region=us&asset_class=stock&common_only=1
{
  "as_of": "...", "region": "us", "asset_class": "stock",
  "universe_total": 5576, "code_total": 4,
  "exchanges": [
    {"code":"XNAS","label":"NASDAQ","count":3308,"share":0.5934,"aliases":["nasdaq","nasd"]},
    {"code":"XNYS","label":"NYSE","count":2009,"share":0.3603,"aliases":["nyse"]},
    {"code":"XASE","label":"AMEX","count":258,"share":0.0463,"aliases":["amex"]},
    {"code":"XCBO","label":"CBOE","count":1,"share":0.0002,"aliases":["cboe"]}
  ],
  "method": "scan"
}
```

| # | Tugas | Berkas | Test |
|---|---|---|---|
| 1.1 | Route + model; memakai **universe yang sama** dengan `select` (bukan breadth) | `app/api/discovery.py` atau `market.py` | `test_exchange_counts_equal_the_filtered_totals` |
| 1.2 | Tally dalam satu pass, memo per `(region, family, common_only, generation)` | `app/api/*` | `test_the_tally_is_computed_once_per_generation` |
| 1.3 | ETag lemah `W/"exch-<sha256(rev)>"`; 304 sebelum build | route | `test_a_revalidation_does_not_recount` |
| 1.4 | `Cache-Control: no-cache` + ETag (breadth belum punya — jangan meniru cacatnya) | route | `test_the_route_is_revalidatable` |
| 1.5 | Label ikut di respons; klien memakainya bila ada | route | `test_each_exchange_carries_a_label` |

**Gate:** 4 bucket, total = 5.576; 304 < 50 ms; angka identik dengan filter per kode.

### Phase 2 — UI (1 hari)

| # | Tugas | Berkas | Test |
|---|---|---|---|
| 2.1 | `web/src/lib/exchanges.ts` — `exchangeLabel`, `exchangeTitle`, `sortExchanges` | baru | `exchanges.test.ts` (label, fallback kode, urutan, biaya 100k panggilan < 50 ms) |
| 2.2 | `ColumnSpec.display?: (v) => string`, dipakai di `format.ts` kasus teks; `data-value` tetap kode | `columnMeta.ts`, `format.ts`, `cellRenderers.tsx` | `format.test.ts`: `XNAS → NASDAQ`, `title "NASDAQ (XNAS)"`, `data-value="XNAS"` |
| 2.3 | `exchange.min` 84 → 104 | `columnMeta.ts` | solver test tetap hijau; tidak ada view > 1.280 px |
| 2.4 | `ExchangeSelect` multi (trigger listbox, menu `aria-multiselectable`, satu chip) | `web/src/grid/ExchangeSelect.tsx` | `exchangeSelect.test.ts` (pilih 2 → `in`, chip, URL, keyboard, kode asing tetap dikirim) |
| 2.5 | `grid/exchanges.ts` — `EXCHANGE_KEY`, `activeExchanges`, `withExchange`, `exchangeVenuesFromRows` | baru | `exchanges.test.ts` (eq/in, [] menghapus, cap bertahan) |
| 2.6 | Wiring `ScreenPanel` + `app.tsx`; opsi dari `GET /market/exchanges` dengan fallback baris halaman | `ScreenPanel.tsx`, `app.tsx`, `useSectors.ts` | `app.filters.test.tsx` (satu request, bukan dua) |
| 2.7 | Detail panel memakai `exchangeLabel` (lazy chunk tidak mengimpor `columnMeta`) | `detail/metrics.ts` | `metrics.test.ts` (XNAS → NASDAQ, 3 baris profil tetap) |
| 2.8 | Trace Tab: sisipkan "Exchange" | `a11y.test.tsx` | urutan tetap ≤ 40 tekanan |

**Gate:** bundle ≤ 133 KB gzip (+2,3 KB dari 130,7); chip + URL round-trip; filter tetap MIC.

### Phase 3 — Higiene data (1 hari)

| # | Tugas | Berkas | Bukti yang harus ada |
|---|---|---|---|
| 3.1 | `XCBO` masuk `US_VENUES` / `US_EXCHANGES` (`app/symbols.py`) | `app/symbols.py` | baris CBOE tidak lagi dilewati verifier |
| 3.2 | Depth menormalkan `NasdaqGM/NasdaqCM/NasdaqGS → XNAS`, `Nasdaq → XNAS`, `NYSE → XNYS`, `TXSE → XASE` + simpan `exchange_source` | `app/market/depth_fields.py` | 8 baris kode Yahoo hilang; `exchange_source` terisi |
| 3.3 | Tandai konflik sumber di detail (bukan kolom baru — katalog beku): `venue_note` internal | `app/screen/*`, `detail` | 164 baris NYSE-vs-XASE dapat catatan |
| 3.4 | Fixture drift: `tests/_screen.py` 3 kode → 4; `ETF_UNIVERSE_TOTAL` 6.123 → 5.776 | tests | gate hijau tanpa angka basi |
| 3.5 | Test invarian kosakata: himpunan venue di live universe harus ⊆ `MIC_BY_EXCHANGE.values()` | `tests/test_screen_universe.py` | gagal bila ada kode tak dikenal |

**Catatan:** D7 (1.389 simbol tanpa depth, 24,9%) **bukan** bagian plan ini — itu backlog depth,
bukan exchange. Dicatat di §6.

### Phase 4 — Verifikasi (setengah hari)

| # | Tugas | Bukti |
|---|---|---|
| 4.1 | Harness parity: skrip yang membandingkan 4 bucket kita dengan Finviz (`f=exch_*`) dan mencetak selisih | angka di CI/console, bukan klaim |
| 4.2 | Gate penuh Python ×3 + vitest + build | 4.521+ ×3 |
| 4.3 | Browser: label tampil, filter mengirim MIC, chip benar, Tab sampai kontrol | tangkapan DOM |
| 4.4 | Commit + push | hash remote == local |

---

## 3. Yang **tidak** termasuk

```
OTC / pink sheet          dikecualikan by policy; biayanya 6.008 baris saham + 340 ETF
TXSE sebagai bucket       menunggu sumber yang bisa dipertanggungjawabkan (D5)
kolom baru                katalog 116 dibekukan; exchange tetap satu kolom
sort per kolom            masih 3 sort di toolbar (keputusan Phase 7)
depth coverage 24,9%      backlog terpisah
```

---

## 4. Risiko

| Risiko | Dampak | Mitigasi |
|---|---|---|
| 422 untuk nilai tak dikenal memutus link lama (`f=exch:eq:Tokyo`) | user dengan URL lama dapat error | nilai di luar region US tetap diterima bila kodenya ada di universe global; 422 hanya untuk yang tidak ada di kosakata mana pun |
| `BreadthReport.venues` wajib | tsc gagal total | field **opsional** + parser toleran |
| Kontrol baru memicu request kedua | semua stub fetch gagal | opsi dari route yang sama, satu hook |
| Label mengubah export | CSV berisi "NASDAQ" padahal konsumen menunggu `XNAS` | export server-side tidak tersentuh; test `data-value` mengunci |
| Toolbar sempit (<1100 px) | dua kontrol + sort berdesakan | ukur di 1.024 px; bila sesak, Exchange masuk ke menu "More filters" |

---

## 5. Estimasi

```
Phase 0  kontrak       0,5 hari   5 tugas, 5 test
Phase 1  endpoint      1,0 hari   5 tugas, 5 test
Phase 2  UI            1,0 hari   8 tugas, ~18 test
Phase 3  higiene       1,0 hari   5 tugas, 5 test
Phase 4  verifikasi    0,5 hari   gate ×3 + browser + push
total                  4,0 hari
bundle                 +2,1–2,3 KB gzip (130,7 → ≤133)
```

---

## 6. Yang perlu keputusanmu sebelum Phase 0

1. **422 vs 0 untuk nilai tak dikenal** — aku memilih 422. Kalau kamu ingin tetap 0 (kompatibel
   dengan link lama), Phase 0.3 dibatalkan dan kita hanya dapat alias.
2. **Multi-select** — aku memilih multi (paritas Finviz, `in` sudah ada). Single lebih murah
   0,65 KB dan lebih sederhana; pilih satu.
3. **AMEX** — 258 baris yang tidak bisa diverifikasi EDGAR. Aku memilih TradingView sebagai
   otoritas + catatan konflik di detail. Kalau kamu ingin menandai mereka "unverified" di UI,
   itu pekerjaan tambahan.
4. **TXSE** — aku memilih tidak membuat bucket. Kalau kamu ingin paritas angka Finviz (9),
   kita perlu sumber TXSE yang bisa dipertanggungjawabkan, bukan menyalin label EDGAR.

---

## 7. Bukti riset

```
research/exchange-datalayer.md    trace hop-by-hop, 179 ketidaksepakatan, 11 defect (D1–D11)
research/exchange-contract.md     grammar, alias home, spec endpoint, ETag, 24 test
research/exchange-ui.md           reuse map, keputusan label, spec kontrol, 618 baris
```

Angka Finviz diambil live dari `finviz.com/screener/stocks?v=111&f=exch_*` (`totalCount`),
angka kita dari `POST /screens/select` dengan filter `exch`, semuanya pada 2026-10-31.
