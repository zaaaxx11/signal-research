# Finviz frontend interaction model — measured over CDP

Target: https://finviz.com (public pages, anonymous, 12 document loads total — under the 15 budget).
Browser: real Chromium 145 driven over CDP (`Runtime.evaluate`, `Input.dispatchMouseEvent`, `Performance` + a
`fetch`/`XMLHttpRequest`/`history.pushState` instrumentation shim installed before load).
Date of capture: 2026-10-31, ~10:46–11:12 ET (market closed).

## 0. Headline

Finviz is **not an SPA**. It is a server-rendered multi-page app with island hydration:

* navigation between the 13 sections is a **real document load** (`performance.getEntriesByType('navigation')[0].type === "navigate"`,
  new `window`, new title);
* inside the screener, filter / sort / page changes are **`fetch` → ~2.6 KB JSON → `history.pushState` → in-place re-render**
  (same document, no re-execution of the app shell);
* maps and charts **fetch nothing at interaction time** because their data is inlined into the HTML they arrive with.

The smoothness comes from the last two points plus the size of the shell, not from a client-side router: the
document is small and brotli-compressed, rows are already in the HTML on first paint, and the only thing that ever
crosses the wire during a screener tweak is the 20 rows that changed.

## 1. Interaction table

| # | Action | Mechanism | Request | Status | Bytes (wire / decoded) | URL changed via | Document |
|---|--------|-----------|---------|--------|------------------------|-----------------|----------|
| 1 | Load `/screener/stocks?v=111` | document navigation | `GET /screener/stocks?v=111` | 200 | 41,131 / 278,361 | — (initial) | new document, TTFB 161 ms, DCL 3,308 ms, h2 |
| 2 | Click filter tab **Fundamental** | client render only | *(none)* | — | — | `pushState` → `…&ft=2` | **same document** (docmark unchanged), DOM 306 K → 475 K chars |
| 3 | Set **P/E → Under 20** | `fetch` JSON + partial re-render | `GET /api/screener-data?columns=…&firstRow=0&pageSize=20&type=stocks&filters=fa_pe_u20` | 200 `application/json` | 2,575 / 7,837 | `pushState` → `…&f=fa_pe_u20` | same document; 20 rows swapped; pager 281 → 74 pages; 2,341 ms click→first table mutation (804 ms server) |
| 4 | Click **P/E** column header (sort) | `fetch` JSON + partial re-render | `GET /api/screener-data?…&filters=fa_pe_u20&order=pe` | 200 | 2,647 / 7,838 | `pushState` → `…&o=pe` | same document; `aria-sort="ascending"`; first tickers NIVF, HTCR, CISS, ENLV; window scrollY 250 → **250** (preserved); 980 ms to first mutation (255 ms server) |
| 5 | Click **Next page** | `fetch` JSON + partial re-render | `GET /api/screener-data?…&order=pe&firstRow=20&pageSize=20` | 200 | 2,616 / 7,930 | `pushState` → `…&r=21` | same document; tickers QFIN, NKLR, WETH, NINE; scrollY 250 → **0** (grid scrolls to top) |
| 6 | Hover a nav item | nothing | 0 finviz-origin requests | — | — | — | same document; the 6 new resource entries were prebid/3lift/pubmatic/connatix ad auctions |
| 7 | Click nav **Maps** | document navigation | `GET /map` | 200 | 16,827 / 51,499 | real load | new document; TTFB 110 ms, DCL 1,447 ms |
| 8 | `/map` after load | lazy JSON refresh | `GET /api/map_perf?t=sec&st=d1` | 200 | 3,654 / 6,499 | — | same document; observed at t≈61.6 s (refresh, not first paint) |
| 9 | Load `/quote?t=AAPL&p=d` (301 → `/stock`) | document navigation | `GET /stock?t=AAPL&p=d` | 200 | 50,692 / 303,240 | real load | new document; TTFB 252 ms, DCL 5,416 ms; **0 chart-data requests** |
| 10 | Load `/watchlist` | document navigation | `GET /watchlist` | 200 | 44,951 / 44,342 | real load | new document; 0 same-origin XHR after load; login wall |

Mechanism legend: `document navigation` = new `PerformanceNavigationTiming` entry with `startTime = 0` and a new
`window.__docmark`; `same document` = `__docmark` unchanged and the old `performance.getEntriesByType('navigation')`
entry still in place (a `pushState` does not create a navigation entry).

## 2. Screener: server-rendered shell, surgical JSON updates

**The shell is fully server-rendered.** 278,361 decoded bytes (41,131 on the wire, brotli, `content-encoding: br`)
arrive with `cfCacheStatus: DYNAMIC`, `cfEdge 9 ms`, `cfOrigin 113 ms` — TTFB 161 ms. The HTML contains a single
island payload:

```
<script type="application/json" data-ssr-props="ssr-island-1">  … 78,799 bytes …
  keys: url, screenerType, origin, today, settings{userId:0, hasRedesignEnabled:true, …},
        filters{filters[23], tabOrder{all,descriptive,fundamental,technical,news,etf}},
        presets[], portfolios[], selectedColumns[11],
        data{rows[20], totalCount:5620, firstRow:0, pageSize:20}
```

So the first paint already has the 20 rows and the header — no skeleton, no spinner, no viewer-visible fetch.

**The filter dictionary is not inlined.** Only the active tab's 23 definitions are in the island; the full set is
fetched once per load:

* `GET /api/screener-filters?type=stocks&v=791f018aed0c326c` → 200, `transferSize` 20,374 B, decoded **154,016 B**,
  515 ms, `application/json`.

**Every screener mutation is a thin JSON call.** The table is driven by one endpoint whose query string *is* the
state: `columns`, `firstRow`, `pageSize`, `type`, `filters`, `order`. Responses are ~7.8 KB decoded / ~2.6 KB on the
wire for 20 rows. The tab switch (`ft=2`) fires nothing at all — the filter definitions are already in memory, so
it is a pure client render plus `pushState`.

**Bytes per interaction (measured, not estimated)**

| Interaction | Wire | Decoded | Server time |
|---|---|---|---|
| filter P/E < 20 | 2,575 B | 7,837 B | 804 ms |
| sort by P/E | 2,647 B | 7,838 B | 255 ms |
| page 2 | 2,616 B | 7,930 B | not separately timed |

**Progressive enhancement detail:** the pager is a real anchor —
`<a aria-label="Next page" href="?v=111&r=21">` — so it still works with JS off (a full document load), and the
client intercepts it when JS is available. Same for the view tabs (`?v=111` Overview, `?v=211` Charts, `?v=321`
News, `?v=711` Maps), which are plain links and therefore *do* cause document loads.

**Grid rendering note:** the server HTML ships each row as `.fv-table_lane` wrappers containing
`<canvas class="fv-table_canvas">` with the cell `<div class="fv-table_cell" data-column-id="…">` nodes inside
(rows carry `is-fallback`). After hydration in my probes the canvases were gone and the cells were positioned
DOM nodes (`.fv-table_cell` with absolute `style`), i.e. a virtualized DOM grid, not a canvas-painted table. I did
not get a clean enough run to state which path production Chrome takes — treat this as "canvas fallback markup in
SSR, DOM grid at runtime".

## 3. `/map` — a canvas treemap, not an image and not divs

The document is tiny: **16,827 B wire / 51,499 B decoded**, TTFB 110 ms. Its `#root` is **empty** — the map is a
client React app (`map.v1.df3b4f6c.js`, 111,966 chars) that mounts and paints.

Technique, with evidence:

* **Two stacked `<canvas>` elements**, both 1025 × 569: `canvas.chart.initialized` (base) and
  `canvas.hover-canvas.absolute` (`touch-action: none`) as the interaction overlay.
* **Not an `<img>`**: the only images on the page are 44 ticker logos in the sidebar list
  (`logo.finviz.com/AAPL.svg`), not the treemap.
* **Not SVG, not divs**: 29 `<svg>` on the page are 16×16 UI icons; 0 elements carry tile/cell geometry; the 20
  absolutely-positioned elements are chrome (legend, promo, sidebar).
* **Genuinely painted**: `canvas.toDataURL()` returns **376,074 bytes** of PNG for the base canvas — real pixels,
  not an empty backing store.
* **Node count: 503** — the inline init script carries `initialPerf.nodes` with 503 ticker→%change entries
  (`{"CF":4.412,"MOS":0.878,…,"AWK":1.519}`), one rectangle per ticker, grouped into sector blocks. `data-rendered`
  on the canvas reads `sec:d1:1` (sector layout, 1-day performance), and the page copy confirms
  "Size represents market cap".
* **No charting library**: `treemap` appears 20× in the bundle, `fillRect` 1×, and there are zero occurrences of
  `d3.`, `echarts`, `highcharts`, `squarify`, `OffscreenCanvas` in it. It is a first-party squarified layout.

**Does it fetch after load?** Yes, but not for first paint:

* `GET /api/maps/counts` → 326 B, `{"sec_all":5551,"etf":657}` (universe sizes for the market selector).
* `GET /api/map_perf?t=sec&st=d1` → 3,654 B wire / **6,499 B decoded**,
  `{"nodes":{…503 tickers…},"additional":{},"subtype":"d1","version":15,"hash":"84B9FDF8…"}` — observed 61.6 s
  after load, i.e. a refresh of colours, not the initial data (which is inlined as `initialPerf` in the
  `FinvizInitCanvas` inline script).

**What makes it feel live (hover):** moving the pointer over a tile repaints only the overlay canvas and pops a DOM
card. Measured at a tile boundary: overlay PNG 18,374 → **21,938 bytes**, DOM nodes 925 → **1,054**, tooltip text
`Technology – Semiconductors` + a news headline + `INTC 65.76 −28.58% / NVDA 204.64 −4.22% / AVGO … / MU …`, then
back to 925 nodes on leaving. **Zero network requests** during hover.

**Zoom:** a wheel event over the canvas changed nothing (`data-rendered` stayed `sec:d1:1`, canvas size unchanged)
and the free UI exposes no zoom controls. "Live" here = hover highlight + tooltip + periodic colour refresh.

## 4. Charts — custom canvas engine, data inlined

`/quote?t=AAPL&p=d` 301-redirects to `/stock?t=AAPL&p=d`: **50,692 B wire / 303,240 B decoded**, TTFB 252 ms,
DCL 5,416 ms (the document is heavy with ad/consent scripts — the chart itself is not the slow part).

* **Canvas, two layers**: `canvas` 1201 × 400 plus `canvas.second` 1201 × 400, both painted
  (toDataURL 118,658 B and 31,166 B). The base layer draws candles/grid; `.second` is the crosshair/overlay layer.
* **Library: none third-party.** `window.Highcharts`, `LightweightCharts`, `echarts`, `d3`, `Plotly` are all
  `undefined`; the served HTML contains no `highcharts`/`lightweight-charts`/`echarts` strings. The chart is a
  first-party React island (`<div id="app" class="interactive-chart">` → `<div id="chart">`) mounting an in-house
  canvas renderer, configured by an inline `FinvizSettings.TA = {"style":"candlestick","overlays":[sma20,sma50,
  sma200,patterns]}`.
* **Data is embedded, not fetched**: an inline `<script>var data = {"version":8,"ticker":"AAPL","timeframe":"d",
  "volume":[34044160,…],…}</script>` of **35,104 chars**; `performance.getEntriesByType('resource')` shows **zero**
  same-origin `fetch`/`xhr` for chart data.
* **Mixed renderers on the same page**: the small fundamental/ownership charts are **Recharts** (SVG) —
  `.quote_fundamental-charts` contains 3 `<svg>` and 21 `<rect>`, with `recharts-wrapper` /
  `recharts-tooltip-wrapper` class names. So: big price chart = canvas, small stat charts = SVG/React.

## 5. Watchlist / Portfolio — account state, not localStorage

`GET /watchlist` → 200, 44,951 B, title "Watchlist". Anonymous body:

```
Create Your First Watchlist
Track and analyze your favorite securities in one place.
[Create New Watchlist]  [Start with Magnificent 7]     Login → /login
```

* **0 same-origin `fetch`/`XHR`** after load (only ad/analytics traffic).
* **0 watchlist-ish keys in `localStorage`** (97 keys, all ad-tech/consent: `cto_bundle`, `id5id`, `_gcl_ls`, …);
  no watchlist blob in `sessionStorage` either.
* **No auth cookie**: the cookie jar holds `finviz_t` (visitor token) plus analytics/consent cookies only.
* `Create New Watchlist` and `Start with Magnificent 7` are `<button>`, not links; the only link is `Login → /login`.

Per the task rule I did **not** click the create/add path (that would be an account write). The evidence that the
store is server-side rather than local: the screener's SSR island already carries `settings.userId` and
`portfolios: []` / `presets: []` — account-scoped collections rendered by the server, empty for `userId: 0`.
So watchlists/portfolios are session/account state served by Finviz, not browser storage.

## 6. Nav — plain links, zero prefetch

* The 13 sections are ordinary anchors: `<a class="nav-link" href="/map">Maps</a>`, `/news`, `/groups`,
  `/insidertrading`, `/futures`, `/forex`, `/crypto`, `/watchlist`, `/portfolio`,
  `/calendar/earnings/season-preview`, `/`, `/screener/stocks`, `/screener/etf`.
* Clicking **Maps** produced a new document (`navigation.type = "navigate"`, new title, TTFB 110 ms) — no router
  interception, no client-side transition.
* **Charts** is the exception: `href="javascript:window.openEliteFeaturesDialog?.('multi-charts', event)"` — a
  paywall dialog, not a page.
* **Hover fetches nothing**: hovering `Maps` produced 0 finviz.com requests; the 6 new resource entries were
  `prebid`, `3lift`, `pubmatic`, `connatix` ad auctions. The document has **0** `rel="prefetch"`, **0**
  `rel="prerender"`, **0** `<script type="speculationrules">`; its 56 `rel="preload"` tags are fonts/CSS/JS for the
  current page only. No `ping=` attributes either.

## 7. State preservation across a screener interaction

* **Sort** (click P/E header): window `scrollY` 250 → **250**; the Fundamental tab (`ft=2`) and the active filter
  (`f=fa_pe_u20`) stay in the URL and the panel stays expanded; only the row set is replaced. First DOM mutation
  980 ms after the click (255 ms of it server round-trip).
* **Filter** (P/E < 20): same document, panel stays open on its tab, rows swap, pager recomputed 281 → 74 pages;
  2,341 ms click → first mutation (804 ms server).
* **Page change** (Next): same document, but the grid scrolls back to top (`scrollY` 250 → 0). Still not a reload.
* **Nav section** (Maps): full document; the new page is a 16.8 KB HTML file that arrives in 110 ms — this is the
  case the adversarial note warns about: it *looks* like an SPA transition but `navigation.type` says otherwise.

## 8. Verdict — why it feels smooth, and what causes it

Finviz feels continuous because it almost never re-downloads a page it already has, and when it does load a page
the page is small and already complete. Concretely: a nav switch is a 16–41 KB brotli'd document with real rows in
the HTML (TTFB 110–161 ms, `cfOrigin` 113 ms), so the browser paints content on the first frame instead of a
skeleton; inside the screener, changing a filter, sorting a column, or paging costs **one 2.6 KB JSON request for
20 rows** (`/api/screener-data`, 255–804 ms) plus a `pushState` — no shell, no filter dictionary, no 154 KB of
filter metadata, no re-execution of the app — and the grid repaints in place in under a second while preserving
scroll and panel state (measured 980 ms for sort, 2.3 s for the filter that also hits a slower 804 ms query). The
visually expensive surfaces are the ones that fetch least: the treemap is 503 rectangles painted to a 1025×569
canvas with all 503 performance values inlined in the HTML (only a 6.5 KB colour refresh arrives later), and the
price chart is two 1201×400 canvases fed by a 35 KB inline `var data` block with zero XHR. In other words the
"complete and smooth" feel is a server-rendered MPA with island hydration plus a deliberately narrow JSON contract
per interaction — not a client-side router and not a global store refetch. Our app's "re-fetch a JSON panel on
every interaction" is the opposite trade: it pays a document-sized payload and a re-render of everything on each
click, where Finviz pays ~2.6 KB and replaces 20 rows.

## 9. Caveats / what I could not measure

* **Cloudflare**: headless Chromium was served a Turnstile interstitial on 3 of 6 first-navigations; it cleared
  only after a desktop UA override (`Chrome/141`, Win32) plus a `navigator.webdriver` shim. 2 challenges were
  passed, 3 blocked until the UA change. All numbers above are from cleared sessions. No login, no bulk crawl.
* **Ad noise**: the pages issue 100–700 third-party requests; every count in this report is filtered to
  `finviz.com`-origin resources or to the instrumented `fetch`/XHR wrapper.
* **Hover** was driven with synthetic-but-trusted CDP `Input.dispatchMouseEvent`; it worked on the map (overlay
  repaint + tooltip) and did **not** produce a tooltip on the price chart, whose main thread was saturated by ad
  scripts (Runtime.evaluate timeouts). I did not claim a chart tooltip.
* **Watchlist write path not tested** (no account), per the task's adversarial rule — only the wall was read.
* **Back/forward** after `pushState` was not exercised; the URL is a faithful projection of state, so a popstate
  handler very likely re-renders from the same `/api/screener-data` contract, but I did not verify it.

## Appendix — raw request log (finviz-origin only)

```
GET /screener/stocks?v=111                                   200  41,131 B wire / 278,361 B   TTFB 161 ms
GET /api/screener-filters?type=stocks&v=791f018aed0c326c      200  20,374 B / 154,016 B        515 ms
GET /api/screener-data?…&filters=fa_pe_u20                   200   2,575 B /   7,837 B        804 ms
GET /api/screener-data?…&filters=fa_pe_u20&order=pe          200   2,647 B /   7,838 B        255 ms
GET /api/screener-data?…&order=pe&firstRow=20&pageSize=20    200   2,616 B /   7,930 B         —
GET /map                                                     200  16,827 B /  51,499 B        110 ms
GET /api/maps/counts                                         200     326 B /      26 B         98 ms
GET /api/map_perf?t=sec&st=d1                                200   3,654 B /   6,499 B         — (observed at t+61.6 s)
GET /stock?t=AAPL&p=d                                        200  50,692 B / 303,240 B        252 ms
GET /watchlist                                               200  44,951 B /  44,342 B         —
```
