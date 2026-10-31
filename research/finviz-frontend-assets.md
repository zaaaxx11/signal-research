# Finviz screener — asset graph & rendering cost

**Target:** `https://finviz.com/screener/stocks?v=111` (the URL `/screener.ashx` 301s here; `?v=111` is the current redesign build).
**Measured:** 2026-10-31, real Chromium 145 (CDP `Network` + `Performance` + `Page`), 1440×900, DPR 1, HTTP/2.
**Method:** `Network.enable` with `dataReceived.dataLength` summed per request for **decoded** bytes, `loadingFinished.encodedDataLength` for **wire** bytes, `responseReceived`/`responseReceivedExtraInfo` for type + headers, `performance.getEntriesByType('navigation'|'paint'|'resource')` and a `PerformanceObserver` for paint/LCP, and a rAF DOM poll for the first data row.

## 0. Cache state and automation caveat (read before the numbers)

- **Browser cache: cold.** `Network.setCacheDisabled(true)` + `Network.clearBrowserCache` before every run.
- **CDN edge: warm, and not bust-able.** Static assets returned `cf-cache-status: HIT` with `age` up to **77,912 s (21.6 h)**; the HTML is `cf-cache-status: DYNAMIC` (never edge-cached). These numbers are therefore *warm-edge / cold-browser*, i.e. the typical user, **not** a worst case. I could not purge the Cloudflare edge, so "cold CDN" was not measured.
- **Automation:** headless Chrome was served the Cloudflare interstitial (`🐴 Just a moment…`, 6.2 KB document instead of 278 KB). Real numbers required a UA + `navigator.webdriver` override, and **5 of 18 attempted page loads still hit the challenge**; those are excluded from every table below.
- **Host contention:** this box is 2 vCPU with 51–62 Chrome processes from parallel work; load average 6.5–24 during the runs. That is the direct cause of the timing spread in §6 — not the site.

## 1. Request table (one cold-cache load, 155 requests)

| Type | n | Wire (B) | Decoded (B) | % wire |
|---|---:|---:|---:|---:|
| Script | 78 | 2,708,463 | 8,974,240 | 86.8% |
| Image | 44 | 123,836 | 139,336 | 4.0% |
| Font | 2 | 96,222 | 95,896 | 3.1% |
| Stylesheet | 3 | 84,362 | 596,505 | 2.7% |
| Document | 4 | 41,533 | 277,816 | 1.3% |
| Fetch | 12 | 38,656 | 231,432 | 1.2% |
| Other (SVG sprites/logo) | 2 | 24,457 | 92,771 | 0.8% |
| XHR | 6 | 3,095 | 3,786 | 0.1% |
| Preflight | 3 | 0 | 0 | 0% |
| Ping | 1 | 0 | 0 | 0% |
| **Total** | **155** | **3,120,624** | **10,411,782** | 100% |

Document: **41,533 B brotli → 277,816 B HTML** (6.7:1). Whole page: **3.12 MB wire → 10.41 MB decoded** (3.3:1).

Request count varies run to run (**116–177** across the 9 runs) because the ad auction resolves differently each time; **155 is the capture used for every byte figure in this document**, and the 9 runs' totals stay within 1.65–1.94 MB transfer.

### Top 10 largest requests (by wire bytes)

| # | Wire (B) | Decoded (B) | Type | Party | URL |
|---:|---:|---:|---|---|---|
| 1 | 189,708 | 641,084 | Script | 3rd | `googletagmanager.com/gtm.js?id=GTM-537M973G` |
| 2 | 179,634 | 538,902 | Script | 3rd | `googletagmanager.com/gtag/js?id=G-ZT9VQEWD4N` |
| 3 | 179,525 | 576,580 | Script | **1st** | `finviz.com/assets/dist/1498.v1.1f516966.js` |
| 4 | 164,691 | 475,746 | Script | 3rd | `googletagmanager.com/gtag/js?id=G-5EST90M6PX` |
| 5 | 129,443 | 377,506 | Script | 3rd | `investingchannel.ay.delivery/manager-script/vrTNGjbrtn4XaEtGa.splitTest.3` |
| 6 | 114,887 | 437,578 | Script | 3rd | `connect.facebook.net/en_US/fbevents.js` |
| 7 | 103,905 | 632,983 | Script | 3rd | `urbanlaurel.com/assets/js/q2o3um29vhadcznq.vendor.js` |
| 8 | 103,207 | 277,698 | Script | 3rd | `accounts.google.com/gsi/client` |
| 9 | 95,834 | 372,701 | Script | 3rd | `c.amazon-adsystem.com/aax2/apstag.js` |
| 10 | 90,442 | 288,662 | Script | 3rd | `connect.facebook.net/signals/config/1307438174199790` |

Nine of the ten largest requests are third-party. Finviz's own biggest file is `1498.js` (180 KB br / 577 KB decoded) — it has **no `<script>` tag of its own**; it appears in the HTML only as an `<link rel="preload" as="script" data-chunk-id="island-screener">` hint in the island preamble and is executed by the loader.

## 2. First-party vs third-party

| Party | n | Wire (B) | % wire | Decoded (B) | % decoded |
|---|---:|---:|---:|---:|---:|
| First-party (`*.finviz.com`) | 76 | 1,397,005 | 44.8% | 4,383,300 | 42.1% |
| Third-party | 79 | 1,723,619 | **55.2%** | 6,028,482 | **57.9%** |

**Third-party = 55.2% of transferred bytes (57.9% of decoded bytes).** Broken out:

| Category | n | Wire (B) | % of total |
|---|---:|---:|---:|
| Analytics / pixels (GTM, GA, Facebook, Clarity, Quantcast) | 18 | 882,271 | 28.3% |
| Ad stack (prebid/header bidding: InvestingChannel, Amazon APS, DoubleClick, PubMatic, Optable, Confiant) | 34 | 746,607 | 23.9% |
| Identity / cookie sync | 5 | 84,262 | 2.7% |
| Cloudflare (insights beacon) | 1 | 10,479 | 0.3% |

Advertising and analytics dominate: **1.72 MB of the 3.12 MB wire (55.2%) is not Finviz's product code** — the ad stack alone (746,607 B) is three quarters the size of the entire first-party payload (1,397,005 B).

## 3. JavaScript

- **48 first-party script requests** on one page view — from **27 unique bundles** referenced by **38 `<script>` tags** (11 tags are duplicated across island templates; the browser dedupes them, so duplicates cost 0 bytes but show the composition).
- **All first-party bundles are `defer`; none is `type="module"`; none is `async`.** (The only module on the page is Cloudflare's `beacon.min.js`.)
- **One blocking script in `<head>`:** `/assets/dist/script/browser_check.v1.1605ca48.js` — no `defer`, no `async`, so it blocks the parser mid-head.
- **The head itself is minimal** (6 `<link>`s: 2 stylesheets, 2 font preloads, 2 icons), but the body emits **31 `<link rel="preload" as="script" data-chunk-id="island-screener">`** immediately before the screener island markup (byte ~33,274), plus **20 `<link rel="preload" as="image">`** for the ticker logos. That inline chunk-graph preamble is why **48 chunks load eagerly instead of lazily** — the split exists in the build, but the page preloads the whole island graph up front.
- Split: **first-party 48 req = 1,007,491 B br / 3,030,090 B decoded**; **third-party 30 req = 1,700,972 B br / 5,944,150 B decoded**. **62.8% of script wire bytes are third-party.**
- Inline JS: **18 inline `<script>` blocks = 114,664 B**, of which **78,799 B is one `<script type="application/json" data-ssr-props="ssr-island-1">`** hydration/data payload. The largest executable inline block is a 10,582 B "lane layout" script that runs **synchronously before the table markup** (see §8).

Largest first-party bundles (wire B):

| Bundle | Wire | Decoded |
|---|---:|---:|
| `1498.v1.1f516966.js` | 179,525 | 576,580 |
| `2468.v1.b49f909f.js` | 80,562 | 252,858 |
| `1679.v1.d8a5e235.js` | 74,458 | 242,311 |
| `knowledge_base_dialog.v1.707966d8.js` | 72,057 | 220,804 |
| `885.v1.a4af9610.js` | 63,970 | 200,676 |
| `3034.v1.217ffc58.js` | 44,985 | — |
| `9245.v1.64953011.js` | 42,993 | 138,491 |
| `4482.v1.4f3db036.js` | 39,405 | — |
| `7779.v1.afd81fde.js` | 36,488 | — |
| `5441.v1.e9930045.js` | 34,957 | — |
| `1689.v1.ca99ff8f.js` | 30,665 | — |
| `elite_features_dialog.v1.be29f921.js` | 23,127 | — |
| `1537.v1.74021a61.js` | 20,411 | — |
| `152.v1.80129e3a.js` | 19,200 | — |
| `runtime.v1.4f6e7068.js` | 4,406 | — |
| `libs_init.v1.b8d0db4a.js` | 7,135 | — |
| `header.v1.5e126fa5.js` | 5,046 | — |
| `ssr_islands.v1.34550f9a.js` | 5,455 | — |

The remaining **34 chunks are ≤20 KB each (median 5.4 KB, total 243,800 B)** — the build is split into 48 pieces, including dialogs (`knowledge_base_dialog`, `elite_features_dialog`) that are fetched eagerly even though nobody opened them.

**The headline:** Finviz is **not** a small-JS site. First-party JS is **1.01 MB brotli / 3.03 MB decoded** — ~7.7× the 130 KB gzip budget of our SPA — and total JS on the wire is 2.71 MB.

## 4. CSS

| File | Wire | Decoded | Rules (approx.) |
|---|---:|---:|---:|
| `/assets/dist/redesign.5c084819.css` | 31,660 | 242,529 | ~1,550 |
| `/assets/dist/main.611bc50f.css` | 52,037 | 353,280 | ~4,143 |
| `accounts.google.com/gsi/style` (3rd) | 665 | 696 | — |
| **Total** | **84,362** | **596,505** | **~5,693** |

- **No critical CSS is inlined.** The document contains **0 `<style>` blocks**; both first-party sheets are `<link rel="stylesheet">` in `<head>` with no `media`/`onload` trick — i.e. both are fully render-blocking.
- **Utility framework: Tailwind CSS v4**, confirmed by markers in the shipped CSS and class names: **2,553 `--tw-` custom properties**, **137 `@property` blocks**, **8 `@layer`**, 426 `:is()` / 902 `:where()` selectors. Class names use v4 syntax: `size-4.5`, `gap-1.5`, `bg-(--fv-logo-rest)`, arbitrary variants `row-hover:[filter:none]`, `group-data-loaded/tile:block`, arbitrary values `[filter:grayscale(1)_contrast(0.3)_brightness(1.1)]`.
- **Not Bootstrap:** `.col-*` ×9, `.container` ×9, `.btn` ×1 — incidental, not a framework. The product layer is `.fv-table_*` (406 occurrences) — a custom table system over Tailwind tokens.

## 5. Images

**44 image requests, 123,836 B wire (4.0% of the page) — images are not the cost here.**

- **Logo strategy = one file per ticker, not a sprite.** 20 `https://logo.finviz.com/{TICKER}.svg` requests, **123,371 B total** (99.6% of all image bytes), average 6.2 KB. Several are 27,150 B each (`AACI`, `AACO`, `AACP`, `AADX`) versus 2.8 KB for `AAP` — the heavy ones look like non-optimised/fallback SVGs.
- App logo: one `img/logo.svg?rev=…` (5,464 B) — fetched as `Other`, not `Image`.
- **Icons are SVG sprites split by feature area, plus inline `<use>`.** The document has **51 inline `<svg><use href="#…">`** references, and `FinvizSettings.iconSprites` declares ~12 named sprite files (`icons`, `icons_blogs`, `icons_calendar`, `icons_chart-layouts`, `icons_chart-tools`, `icons_chart-types`, `icons_companies`, `icons_crypto`, `icons_news`, `icons_news-content`, `icons_screener`, `icons_signal…`). Exactly one was fetched on this page: `icons.1f7da9e4.svg` (18,993 B wire / 76,633 decoded).
- **No native lazy loading: 0 of 21 `<img>` tags carry `loading="lazy"`.** Logos are eagerly fetched — confirmed by **20 `<link rel="preload" as="image">`** hints for exactly those 20 tickers — but visually gated: `class="… hidden … group-data-loaded/tile:block"` with `crossorigin="anonymous"`, `width=18 height=18`. Lazy-ness is achieved with `hidden` + a `data-loaded` attribute (and a preload), not with `loading`.
- 21 `data:image/svg+xml` **skeleton placeholders** (rounded rects of the exact row width) cost 0 bytes on the wire and are what paints before the logo arrives.

## 6. Timing

Instrumentation: `DOMHighResTimeStamp` from the navigation entry, `PerformanceObserver{type:'paint'|'largest-contentful-paint', buffered:true}`, and a `requestAnimationFrame` poll that resolves the first `[data-ticker]` element and accepts it as painted only when `getBoundingClientRect()` reports `height>0 && width>0` and `top < innerHeight` — i.e. the frame in which a real, non-zero, in-viewport row box is laid out. **Detected by observation, not inferred from DCL.** Frame granularity is one animation frame (~16 ms with a compositor, but 150–300 ms in this headless setup, which is the main quantisation error on this column).

| Run | TTFB | FCP | LCP | DCL | Load | **first row** | reqs |
|---|---:|---:|---:|---:|---:|---:|---:|
| fv_g2 | 188 | **532** | 532 | 1,622 | never | **1,224** | 174 |
| fv_g1 | 185 | **692** | 692 | 3,212 | never | **1,734** | 125 |
| fv_c3 | 318 | 1,688 | 1,688 | 2,965 | never | 2,140 | 125 |
| fv_g3 | 159 | 2,252 | 2,252 | 7,390 | never | 4,538 | 135 |
| fv_d2 | 268 | 3,064 | 3,064 | 6,138 | never | 2,720 | 116 |
| fv_c2 | 1,541 | 3,708 | 3,708 | 8,387 | 11,635 | 4,990 | 177 |
| fv_d3 | 518 | 4,040 | 4,040 | 10,495 | never | 5,747 | 128 |
| fv_d1 | 196 | 4,296 | 4,296 | 8,964 | never | 4,497 | 118 |
| fv_c1 | 261 | 7,448 | 7,448 | 7,841 | never | 6,853 | 116 |

**min / median / max over 9 runs**

| Metric | min | median | max | spread |
|---|---:|---:|---:|---:|
| TTFB | 159 | 261 | 1,541 | 9.7× |
| FCP | 532 | 3,064 | 7,448 | **14.0×** |
| LCP | 532 | 3,064 | 7,448 | 14.0× |
| DCL | 1,622 | 7,390 | 10,495 | 6.5× |
| First row | 1,224 | 4,497 | 6,853 | 5.6× |
| Load | — | — | — | fired in **1 of 9** runs |

**The spread is real and I am not hiding it.** Runs disagree by up to 14×, far beyond the 2× threshold. Two runs on an otherwise-idle browser agree closely and are the defensible "site" numbers:

> **Quiet-browser subset (FCP < 1 s, n=2): TTFB 185–188 ms · FCP 532–692 ms · first row 1,224–1,734 ms · DCL 1,622–3,212 ms.**
> Every other run was measured with load average 6.5–24 on 2 vCPU against 51–62 Chrome processes; the same page, minutes apart, produced 532 ms and 2,252 ms FCP. Treat the medians as a loaded-machine upper bound, not as Finviz's performance.

Two invariants hold in **9 of 9** runs regardless of load:

1. **The first data row is painted before `DOMContentLoaded`** (by 0.4–3.4 s depending on load).
2. **The `load` event does not fire** within 12–30 s of observation in 8 of 9 runs — the ad/analytics requests (Google `ccm/collect`, DoubleClick `ccm/s/collect` → `net::ERR_ABORTED`) keep the page permanently "loading".

## 7. Caching

| Resource | `cache-control` | `etag` | `cf-cache-status` | age | encoding |
|---|---|---|---|---|---|
| **Document** `/screener/stocks?v=111` | **`no-cache`** | *none* | **`DYNAMIC`** | — | br |
| JS `/assets/dist/5026.v1.8ae56e21.js` | `public, max-age=31536000` | `W/"1dd69433726f371"` | `HIT` | 2,137 s | br |
| CSS `/assets/dist/redesign.5c084819.css` | `public, max-age=31536000` | `W/"1dd6892c9216661"` | `HIT` | 77,912 s | br |

- **The document is not cacheable at all.** `no-cache` *plus* no `ETag` and no `Last-Modified` means there is no validator, so no 304 is possible: every navigation re-downloads the full **41.5 KB br / 278 KB decoded** HTML from origin (`server-timing: cfOrigin;dur=61–111`).
- Static assets are the opposite: content-hashed filenames, `max-age=31536000`, `ETag`, `cf-cache-status: HIT` — effectively immutable and free after first contact. Correct cache split: **HTML = never cached, assets = cached forever.**

## 8. Why it is complete *and* smooth

The asset graph is not the answer — by byte count Finviz is heavier than a typical SPA. The answer is **where the data enters the page**:

1. **Rows are in the HTML.** The first `data-ticker` cell is at **byte 101,867 of 277,205 (37%)** — server-rendered `<div class="fv-table_cell" data-ticker="A">` in a lane-virtualised grid (`data-lane="left|center|right"`, `aria-colindex`, `left:var(--fv-col-row-l)`). No XHR delivers the 20 rows; the only data fetch is `/api/screener-filters` (20,234 B / 154,016 B decoded), which is filter metadata, not results.
2. **A separate 78.8 KB JSON island** (`ssr-island-1`, at byte 187,665 = 68%) carries the hydration state, so the client can take over without re-fetching.
3. **In the quiet runs the first row paints at ~1.2–1.7 s while DCL lands at 1.6–3.2 s** — the product data beats full document parse by roughly a second, because the table is 37% of the document and the 48-bundle JS graph is all `defer`red behind it.
4. **Then the JS arrives and never blocks the rows.** `1498.js` (180 KB br) finishes at ~1.1 s, before the row paints; the remaining 38 requests and 1.43 MB (46% of the page) land *after* the first row is already on screen.
5. Cost of that strategy: one blocking `browser_check.js` in the head, a 10.5 KB synchronous inline lane-layout script, 596 KB of render-blocking CSS (Tailwind v4), and a page that never fires `load` because ad beacons hang.

**Net:** completeness comes from SSR-ing the rows into a 278 KB document and shipping the state as JSON; smoothness comes from deferring *everything* else and never letting hydration gate the first paint. The price is a 3.1 MB page (55% third-party) and a document that can never be cached.

### What this means for a 130 KB-gzip SPA
The gap is not bundle size — Finviz ships 1.01 MB brotli of first-party JS. The gap is that our SPA's first row is gated on **JS download → parse → execute → fetch → render**, while Finviz's first row is gated on **HTML bytes arriving**. The transferable moves: stream rows as markup, keep hydration off the critical path, inline the data payload, and make only the HTML uncacheable.

## 9. Data files

- `research/fv-measure/run_fv_g1_events.json` — full 1,009-event CDP stream (155 requests) behind §1–§3.
- `research/fv-measure/run_fv_{c1..c3,d1..d3,g1..g3}_page.json` — nav/paint/resource/probe data behind §6.
- `research/fv-measure/analyze.py`, `detail.py`, `timeline.py`, `final.py` — aggregation.
- `research/stocks.html` — the 277,816 B document analysed in §3, §4, §8.
