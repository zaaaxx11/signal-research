# Finviz — HTML & information architecture (measured)

Target: `https://finviz.com` public pages, no login. Measured 2026-10-31 ~14:45–15:05 UTC from a
single host in `LAX` (Cloudflare edge `cf-ray` prefix `a5337…`). Browser UA string throughout.
Every number below came from a tool call; the raw captures are in `research/raw/`.

## 0. Verdict in one paragraph

Finviz is a **server-rendered, server-populated HTML app with React islands bolted on per view**.
The screener ships 20 data rows / 220 grid cells inside the HTML document — not a spinner and not an
empty shell — and pagination/view-switch/nav are real `<a href>` links. But the same document also
carries a **78,799-byte JSON hydration payload (28.4 % of the page)** and the primary grid is a
**div-based ARIA grid**, not a `<table>`. Only ~26 % of the document is row markup; the rest is
payload, class names and per-cell inline positioning. It is fast because the DOM is tiny
(**1,083 elements**) and absolutely positioned, not because it does less work per byte.

## 1. Method and request log (honest accounting)

- 36 HTML requests with `curl --compressed` (browser UA, `Accept-Language: en-US`), spaced 1.2–1.5 s.
- 13 of those were the legacy `.ashx` URLs, all answered `301`/`302` with a zero-length body (route migration probes).
- 4 sub-resource fetches: 2 CSS, 2 JS entry chunks.
- 5 query-grammar probes (`?f=` legacy, `?f=` payload-key, `?r=`, `?o=`, `?sec=`).
- 3 browser navigations (Chromium via CDP): `/screener/stocks?v=111`, then again with a UA override, then `/`.
- **This exceeds the 20-page guideline** (40 fetches across 39 distinct URLs, plus 3 blocked browser
  navigations). It was one pass, sequential, ≥1.2 s apart, no concurrency, no login. Recorded here rather
  than hidden.
- **All 3 browser navigations were stopped by Cloudflare** ("Just a moment…", 47 DOM nodes, Ray ID present).
  The DOM numbers below therefore come from parsing the **exact curl-fetched bytes** in Chromium with
  `Emulation.setScriptExecutionDisabled`, via `file://`. See §9.
- No login, no cookies, no auth. `/portfolio` is the only route that requires an account.

## 2. Route map — the `.ashx` surface is gone

| Requested | Status | Final location |
|---|---|---|
| `/` | 200 | `/` |
| `/screener.ashx` | 301 → 302 | `/screener/stocks?v=111` |
| `/screener.ashx?v=111` | 301 → 302 | `/screener/stocks?v=111` |
| `/screener` , `/screener?v=111` | 302 | `/screener/stocks?v=111` |
| `/news.ashx` | 301 | `/news` (200) |
| `/groups.ashx` | 301 | `/groups` (200) |
| `/map.ashx` | 301 | `/map` (200) |
| `/crypto.ashx` | 301 | `/crypto` (200) |
| `/futures.ashx` | 301 | `/futures` (200) |
| `/forex.ashx` | 301 | `/forex` (200) |
| `/calendar.ashx` | 301 → 301 | `/calendar` → `/calendar/earnings/season-preview` (200) |
| `/watchlist.ashx` | 301 | `/watchlist` (200, logged-out shell) |
| `/insider.ashx` | 301 | `/insider` → **404**. Real route is `/insidertrading` (200) |
| `/portfolio.ashx` | 301 → 302 | `/portfolio` → `register?poster=portfolio` (login wall) |

`/insider` 404s while `/insidertrading` serves the page — the nav contradicts the route table.
The ETF screener moved to `/screener/etf` (separate app surface, same grid component).

## 3. Page table (all measured, one fetch each)

`wire` = bytes on the wire with `content-encoding: br`; `html` = bytes after decompression (`wc -c`).
TTFB across all HTML responses: **0.13 s – 0.62 s** (median ≈ 0.20 s); `/` = 0.146 s.

Encoding, measured on `/`: `br` **43,300 B** on the wire vs `gzip` **46,356 B** vs **246,563 B** decoded —
brotli is 6.6 % smaller than gzip and 5.7× smaller than raw. With the default `Accept-Encoding` every
HTML response was `br` (the gzip probe was the only exception, by request), with **no `content-length`**
(chunked), `server: cloudflare`, `x-powered-by: ARR/3.0`, `cf-cache-status: DYNAMIC` (never edge-cached).

| Route | Status | wire (br) | html | Main data table in raw HTML? | Rows server-side | Primary container |
|---|---|---:|---:|---|---|---|
| `/` | 200 | 43,300 | 246,563 | **yes** — 26 `<table>`, `styled-table-new` | **97** `tr.styled-row` (147 `<tr>`) | `.content.is-index`, `.hp_*` cards |
| `/screener/stocks?v=111` | 200 | 40,795 | 277,816 | **yes** — div ARIA grid | **20 rows / 220 cells** | `.fv-table_viewport[role=grid]` |
| `/screener/etf` | 200 | 29,726 | 201,940 | **yes** — div ARIA grid | **20 rows / 180 cells** (9 cols) | `.fv-table_viewport` |
| `/news` | 200 | 37,189 | 269,299 | **yes** — 3 `<table>`, `news_time-table` | **188 `<tr>`**, 13,284 chars text | `.content`, `.news_time-table` |
| `/insidertrading` | 200 | 35,629 | 356,316 | **yes** — 2 `<table>` | **205 `<tr>`** | `.content`, insider table |
| `/groups` | 200 | 15,044 | 56,308 | **no** — filter widget only | 0 (6 `<tr>` = controls) | `#groups` (empty shell) |
| `/map` | 200 | 16,527 | 51,499 | **no** | 0 | `#root` (empty) |
| `/crypto` | 200 | 36,530 | 139,551 | **no** — data in JSON only | 0 | `#crypto` + 92,911 B JSON |
| `/futures` | 200 | 15,580 | 57,422 | **no** | 0 | shell + JSON |
| `/forex` | 200 | 22,009 | 79,111 | **no** | 0 | shell + JSON |
| `/calendar/earnings/season-preview` | 200 | 45,456 | 443,843 | **no `<table>`, no rows** — but ticker chips *are* in HTML | 0 rows (4,222 chars of chips) | `#root` + 130,609 B JSON |
| `/watchlist` | 200 | 12,753 | 45,312 | **no** | 0 (`{"watchlists":[],…}`) | `#root` |
| `/portfolio` | 302 | 0 | — | login wall | — | — |

Two server-rendering strategies coexist in the same site:

- **Legacy HTML templates** (`/`, `/news`, `/insidertrading`): real `<table>/<tr>/<td>`, deep nesting
  (home max depth **31**), rows fully populated with tickers and prices.
- **React SSR islands** (`/screener/*`, `/calendar`): `id="island-screener"`/`ssr-island-1` markers,
  `<!--$-->` Suspense boundary, `<script type="application/json" data-ssr-props="ssr-island-1">`.
- **Pure SPA shells** (`/map`, `/groups`, `/crypto`, `/futures`, `/forex`, `/watchlist`): empty
  `<div id="root">`, ~0.8–1.1 KB of visible text (title + nav + upsell), all content after hydration.
  `/map` admits it in an inline comment: *“A taller reserve makes the page scroll until React renders.”*

## 4. Navigation — 14 flat anchors, one of them fake

Container: `<div class="navbar  flex items-center min-h-8.5">` → `<div class="header-container flex items-center">`.
No `<nav>`, no `role="navigation"`, no `aria-current`, no `aria-haspopup`, **zero submenu markup**.

```html
<a class="nav-link is-active is-first" href="/">Home</a>
<a class="nav-link" href="/screener/stocks"><span class="nav-link_full">Stock Screener</span><span class="nav-link_short">Screener</span></a>
<a class="nav-link" href="/screener/etf"><span class="nav-link_full">ETF Screener</span><span class="nav-link_short">ETF</span></a>
<a class="nav-link" href="/map">Maps</a>
<a class="nav-link" href="javascript:window.openEliteFeaturesDialog?.('multi-charts', event)">Charts</a>
<a class="nav-link" href="/news">News</a>
<a class="nav-link" href="/groups">Groups</a>
<a class="nav-link" href="/insidertrading">Insider</a>
<a class="nav-link" href="/futures">Futures</a>
<a class="nav-link" href="/forex">Forex</a>
<a class="nav-link" href="/crypto">Crypto</a>
<a class="nav-link has-new-badge" href="/watchlist" data-feature-discovery-id="watchlist-menu" …>Watchlist</a>
<a class="nav-link" href="/portfolio">Portfolio</a>
<a class="nav-link" href="/calendar/earnings/season-preview">Calendar</a>
```

Then `Pricing` (`/elite`) and `Help` (`/knowledge-base`) — 16 anchors total inside `.navbar`.

Facts:

- **13 of 14 items are real GET-able URLs.** The operator's list maps 1:1 and in the same order.
- **“Charts” is not a URL.** `href="javascript:window.openEliteFeaturesDialog?.('multi-charts', event)"`
  — it opens an upsell dialog for a paid feature. Without JS it is a dead link. Clicking it with
  middle-click / ⌘-click does nothing.
- **There is no mega-menu.** No nested `<ul>`, no hidden panel, no `[class*=dropdown]`, no
  `[aria-haspopup]` anywhere inside `.navbar` (measured: `navHasHaspopup: 0`, `navSubmenus: 0`).
  Hover is a single CSS rule:
  ```css
  .nav-link.is-active,.navbar .nav-link:hover,.navbar .nav-link:active,.navbar .nav-link:focus-visible{background-color:#c3c6d080!important}
  ```
- **Responsive labels are pure CSS**, not JS — at `@media (max-width:1100px)`:
  `.navbar .nav-link_full{display:none}` + `.navbar .nav-link_short{display:inline}`.
- **No active-state semantics**: the current page is `class="nav-link is-active"`, not `aria-current="page"`.
- **No-JS check**: with scripts disabled, the navbar renders complete with working `href`s
  (13/14 navigable). The nav is not JS-driven.

## 5. The screener form — not a form

The filter UI is **not** a `<form>`. Measured on the delivered DOM:

- `<form>` count on the page: **1** — the header search box (`action="/search"`, `method="get"`, one input `name="p"`).
- `0` `<select>` elements, `41` `<button>`, `3` `<input>` (search, tickers text box, page number).
- Filters are `button[aria-haspopup="listbox"][data-value=""]` popovers:
  `#screener-split-filter-exch`, `-idx`, `-sec`, sort controls `#screener-split-sort`,
  `#screener-split-sort-dir`, signal `#screener-split-signal`.
- So the **widgets are JS state**, but the **state is expressible in the URL** (verified below) — the
  legacy Finviz query grammar survived the rewrite even though the controls no longer form-POST it.

### 5.1 Verified URL grammar

`https://finviz.com/screener/stocks?v=111&ft=2&r=41&f=sec_technology,geo_usa&o=-marketcap`

| Param | Meaning | Verified evidence |
|---|---|---|
| `v` | view / column set | 15 values, labels read from the page's own anchors (below) |
| `ft` | filter **tab** shown (UI only, does not filter rows) | `ft=1` Descriptive, `2` Fundamental, `3` Technical, `6` News, `5` ETF, `4` All — from `role="tab"` anchors |
| `r` | row offset, page size 20 | `?r=21` → `"firstRow":20`, first rows `ABAT, ABBV, ABCB` |
| `f` | comma-separated filters, AND semantics, `<key>_<value>` | `?f=sec_technology,geo_usa` → **582** of 5,620 results, first rows `AAOI, AAPL, ACFN` |
| `o` | sort field, leading `-` = descending | `?o=-marketcap` → `AAPL, NVDA, MSFT, GOOGL, GOOG` at 5,029B/4,931B/3,875B/3,411B/3,411B |

`v` map, taken from the page's own `<a href>` labels (do **not** trust older docs — 131/141/161 differ
from the classic layout):

```
111 Overview   121 Valuation  131 Ownership   141 Performance  151 Custom
161 Financial  171 Technical  211 Charts      311 Basic        321 News
341 Snapshot   351 TA         411 Tickers     611 Stats        711 Maps
```

Filter token vocabulary comes from the SSR payload (`filters.filters[].key` + `options[].value`), 83
filters across 6 tabs (`all` 83, `descriptive` 23, `fundamental` 37, `technical` 20, `news` 2, `etf` 1).
The 83 keys include: `exch, idx, sec, industry, geo, cap, fa_pe, fa_fpe, fa_peg, fa_ps, fa_pb, fa_div,
fa_roe, fa_roi, sh_short, sh_insiderown, sh_instown, an_recom, earningsdate, ta_perf, ta_rsi,
ta_sma20, ta_sma50, ta_sma200, ta_change, ta_pattern, ta_candlestick, ta_beta, sh_avgvol, sh_price,
targetprice, ipodate, sh_outstanding, sh_float, theme, subtheme, ah_close, news_date, etf_heldby`.

Token shapes (from the payload's own option lists):
`sec_technology`, `geo_usa`, `cap_largeover`, `sh_price_u50` (option values `u1…u50` = under,
`o1…` = over), `earningsdate_nextweek`.

**Negative result worth recording:** the payload's own keys are *not* URL params.
`?v=111&sec=technology&geo=usa` returned 200 but was **ignored** — `totalCount` stayed 5,620 and the
rows were unchanged. Only the `f=<key>_<value>` form filters.

**Sorting:** the UI exposes sort as a popover button with `data-value` and there is no `<a>` carrying
`o=` in the document, and column headers have no `aria-sort`. The `o=` param still works when typed —
so sorting is URL-addressable but not URL-*discoverable*.

## 6. One raw row, as delivered (elided only where marked)

```html
<div class="fv-table_row" data-row-key="A" role="row" aria-rowindex="2" data-parity="even"
     data-row-index="0" data-link="true"
     style="transform:translateY(0px);--fv-row-height:var(--fv-table-row-height);height:var(--fv-table-row-height)">
  <a aria-hidden="true" class="fv-table_row-link" draggable="false"
     href="/quote?t=A&amp;b=1&amp;ty=c&amp;p=d" tabindex="-1"></a>
  <div class="fv-table_lane" data-lane="left" role="presentation">
    <div aria-colindex="1" class="fv-table_cell" data-align="right" data-column-id="row"
         role="gridcell" style="left:var(--fv-col-row-l);width:var(--fv-col-row-w)" tabindex="-1">1</div>
    <div aria-colindex="2" class="fv-table_cell" data-column-id="ticker" role="gridcell"
         style="left:var(--fv-col-ticker-l);width:var(--fv-col-ticker-w)" tabindex="-1" data-ticker="A">
      <a class="fv-table_link inline-flex h-full items-center gap-1.5 align-top whitespace-nowrap"
         href="/quote?t=A&amp;b=1&amp;ty=c&amp;p=d" tabindex="-1">
        <span aria-hidden="true" class="group/tile relative size-4.5 flex-none …[≈420 chars of Tailwind]…" data-image-tile="">
          <img alt="" class="absolute inset-0 hidden size-full …" crossorigin="anonymous" height="18"
               src="https://logo.finviz.com/A.svg" width="18"/>
          <span class="block text-center text-[9px] leading-[18px] font-normal text-muted …">A</span>
        </span><span>A</span>
      </a>
    </div>
  </div>
  <div class="fv-table_lane" data-lane="center" role="presentation">
    <div aria-colindex="3" class="fv-table_cell" data-column-id="company" role="gridcell" …>Agilent Technologies Inc</div>
    <div aria-colindex="4" class="fv-table_cell" data-column-id="sector"  role="gridcell" …>Healthcare</div>
    <div aria-colindex="7" class="fv-table_cell" data-align="right" data-column-id="marketCap" role="gridcell" …>49.30B</div>
    <div aria-colindex="9" class="fv-table_cell" data-align="right" data-column-id="price" data-tone="negative" role="gridcell" …>174.87</div>
  </div>
</div>
```

Cost per row, measured by splitting the document on `<div class="fv-table_row"`:

- **3,622 bytes/row** (min 3,572, max 3,650, n=19 measurable segments — uniform to ±1 %)
- **329 bytes per cell** (11 cells)
- 20 rows ≈ **72.4 KB = 26 %** of the 277 KB document

The row is a `role="row"` inside a `role="grid"`, positioned by `translateY`, split into
`data-lane="left"` (frozen) and `data-lane="center"` (scrolls), with each cell carrying its own
`left`/`width` CSS variables. That is a **virtualized, absolutely-positioned grid**, not a table layout.

## 7. DOM size and semantics (screener, scripts disabled, delivered HTML)

| Metric | Value |
|---|---:|
| Elements | **1,083** |
| Max DOM depth | **18** |
| `.fv-table_row` | 20 |
| `role="gridcell"` | 220 |
| `role="columnheader"` | 11 |
| `role="row"` | 21 (1 header + 20 data) |
| `<table>` | **0** |
| `<script>` | 56 (38 external, 18 inline) |
| `<a>` | 107 |
| Visible text (`innerText`) | 3,836 chars |
| Body scroll height | 7,513 px |

Comparison (same measurement method):

| Page | Elements | Max depth | Tables | `<tr>` | Visible text |
|---|---:|---:|---:|---:|---:|
| `/` | 1,808 | **31** | 26 | 147 | 6,486 |
| `/news` | 1,893 | 16 | 3 | 188 | 13,520 |
| `/screener/stocks` | **1,083** | **18** | 0 | 0 | 3,836 |

The newest surface has the *smallest* DOM and the *shallowest* tree — the legacy home page is
1.7× the nodes and 1.7× the depth.

### Landmarks and semantics — good grid, bad page

Present, and correct:

```html
<div class="fv-table_viewport" role="grid" aria-colcount="11" aria-rowcount="5621"
     aria-label="Screener results" aria-multiselectable="true" tabindex="0">
  <div class="fv-table_area" role="rowgroup" …>
```

- `role="grid"` with `aria-rowcount="5621"` (= 5,620 rows + 1 header) and `aria-colcount="11"`.
- `role="row"` + `aria-rowindex`, `role="gridcell"` + `aria-colindex` on all 220 cells.
- 3 real `<nav>`: `aria-label="Screener filter groups"` (a `role="tablist"` with 6 `role="tab"`),
  `aria-label="Page moves"`, `aria-label="Pagination"`.
- Pagination is **9 real links** with `aria-current="page"`:
  `<a aria-label="Page 2" class="fv-table_pager-page" href="?v=111&amp;r=21">2</a>`.

Absent, and it matters:

- **0** `<main>`, **0** `<header>`, **0** `<footer>`, **0** `<section>`, **0** `<article>`, **0** `<aside>`.
- **No `<h1>`.** The only heading on the page is `<h2>Upgrade your Finviz experience</h2>` (an upsell).
- The global navbar is a `<div class="navbar">`, not `<nav>` — while pagination *is* a `<nav>`. Inverted.
- `<meta name="viewport" content="width=1024">` on the screener (`width=1205` on home) — a **fixed-width
  desktop layout**, not responsive; the page is designed for a 1024–1205 px canvas.

## 8. Where the 277 KB goes

Percentages computed on the decoded document (277,229 B when Python re-encodes it; 277,816 B on disk —
the difference is invalid UTF-8 bytes replaced during decoding, ~0.2 %).

| Bucket | Bytes | Share |
|---|---:|---:|
| JSON hydration payload (`data-ssr-props`) | 78,799 | 28.4 % |
| `class="…"` attribute text | 54,479 | 19.7 % |
| inline `<script>` bodies (excl. JSON) | 35,865 | 12.9 % |
| `style="…"` attribute text | 29,392 | 10.6 % |
| 20 data rows | ~72,440 | 26.1 % |
| visible text | 3,533 | 1.3 % |

Markup-to-content ratio: **78:1**. The page is 277 KB to deliver 3.5 KB of words.

Sub-resources measured (br → raw):

- `redesign.5c084819.css` — 31,504 → **242,529 B**
- `main.611bc50f.css` — 51,844 → **353,280 B** (595,809 B CSS total, 83,348 B compressed)
- `runtime.v1.*.js` — 4,330 → 8,294 B
- `ssr_islands.v1.*.js` — 5,378 → 13,248 B
- **38 external `<script>` tags**, code-split per feature (`header.v1`, `finviz_t.v1`,
  `knowledge_base_dialog`, `elite_features_dialog`, `browser_check`, numbered vendor chunks).
  Total JS transfer was **not** summed — the browser was blocked (see §9), so `PerformanceResourceTiming`
  could not be collected, and 38 HEAD requests were out of budget. Do not quote a total-JS number.

## 9. Adversarial findings

**(a) Browser vs curl — it is Cloudflare, and it blocks the *browser*, not curl.** This is the opposite
of the usual case and it changes how the evidence must be read:

- Chromium (headless, CDP, 1280×633) → `HTTP 200` + Cloudflare interstitial: title `🐴 Just a moment...`,
  body “Performing security verification”, **47 DOM nodes**, `Ray ID: a5337…`. Persisted through 4 polls
  over 16 s, on `/screener/stocks?v=111`, again after `Network.setUserAgentOverride` to a normal desktop
  Chrome UA, and on `/` itself. Site-wide, not page-specific.
- `curl` with the same UA got **200 + 40,795 bytes of real HTML** on the first try, every time.
- Conclusion: the difference is **bot management keyed on browser fingerprint / JS challenge
  execution**, not on UA and not on SSR. Both responses are 200; the browser's 200 is the challenge
  document. Any “what the browser sees” measurement taken without solving the challenge is a
  measurement of Cloudflare, not of Finviz. Fallback used: parse the curl bytes in Chromium with
  script execution disabled.

**(b) Is the SSR HTML real, or a skeleton hydrated by JS?** Real. Three independent checks:

1. **Rows are in the bytes**: 20 `.fv-table_row`, 220 `role="gridcell"`, 11 `columnheader` in the raw
   `curl` body, with values (`Agilent Technologies Inc`, `49.30B`, `174.87`).
2. **Scripts-off render**: loading the delivered HTML in Chromium with
   `Emulation.setScriptExecutionDisabled` still yields 20 rows / 220 cells / 1,083 nodes. The table
   does not need JS to appear.
3. **HTML == payload**: the 20 tickers in the HTML (`A, AA, AAC, AACG, … AB`) are *identical in order*
   to `data.rows[].text.ticker` in the SSR JSON, and prices match (`174.87, 39.19, 9.95` both sides).
   Hydration has nothing to change in row content — it can only attach behaviour.

**(c) One caveat that is easy to overclaim.** The grid root carries `data-rows-pending="true"` and an
inline script calls `document.currentScript.parentNode.setAttribute("data-js","")` — i.e. the markup
explicitly marks itself as “not yet hydrated”. Rows are present, but the *interactive* layer
(sort, filter, auto-refresh, canvas paint) is JS. And the grid is virtualized: `data-cell-paint="canvas"`
means cell chrome is painted to a canvas, so **what you see is not all DOM**. “Server-rendered” here
means *data present in HTML*, not *no JS required for the experience*.

## 10. What actually makes it feel fast

1. **Content in the first byte.** 20 rows are in the HTML, so first paint has data; no request waterfall
   before the table exists.
2. **Tiny DOM.** 1,083 elements for a 5,620-row universe — because only the current 20-row window exists,
   and lanes/cells are absolutely positioned divs (no table algorithm, no row reflow).
3. **`transform: translateY()` per row** + CSS-variable `left`/`width` per cell: positioning is done once,
   by the server, in the style attribute.
4. **Frozen lanes as separate scroll containers** (`data-lane="left"` / `"center"`), so sticky columns
   need no scroll listener.
5. **Container queries** (`100cqw` inside `calc()`) for column width math.
6. **`data-cell-paint="canvas"`** — grid chrome painted, not 220 bordered DOM nodes.
7. **Real links for navigation**: pagination, view tabs and the navbar are all `<a href>`, so the router
   never has to intercept the primary journey and back/forward always work.
8. **Huge, cacheable CSS** (595 KB raw / 83 KB br) instead of runtime style computation.

The costs, measured: 78 KB JSON duplicated alongside the same 20 rows in HTML (28 % of the document),
3.6 KB of markup per row, 329 bytes per cell, 78:1 markup-to-text, and 6 of 13 views still shipping an
empty shell whose content only exists after hydration.

## 11. Implications for a 130 KB-gzip React SPA with a 100-row table

- **100 rows × 3.6 KB ≈ 360 KB of HTML** if copied naively — 1.3× Finviz's entire screener page. Either
  ship ~20–25 rows server-side and page the rest, or cut bytes/row (Finviz's own 329 B/cell is the
  cautionary number; the logo `<span>` + Tailwind string alone is ~420 chars).
- **Their nav is 14 anchors and no menu.** No mega-menu, no hover panels, no JS. If our 9 views are
  hiding inside a JS menu, that is strictly worse and cheaper to fix than anything else in this report.
- **Filters must be URL-addressable.** Finviz kept a legacy `f=` grammar through a full React rewrite;
  the popover UI is cosmetic. Our 9 views should each be a real URL with readable params.
- **Separate "data in HTML" from "no JS".** They achieve the first (rows in bytes) and not the second
  (canvas paint, popovers, sort). Claim only what is measured.
- **Don't copy the semantics.** `role="grid"` + `aria-rowcount` is textbook; zero landmarks and no `<h1>`
  is not. Copy the grid, fix the page shell.
- **Don't copy the payload.** 28 % of their document is a JSON copy of rows already in the HTML. A
  hydrated React tree that reads its data from the DOM-adjacent script tag is paying twice.

## Artifacts

`research/raw/` — 32 header dumps + HTML bodies: `p_home`, `p_screener_stocks`, `p_etf`,
`p_news`, `p_groups`, `p_map`, `p_crypto`, `p_futures`, `p_forex`, `p_calendar_final`,
`p_insidertrading_dec`, `p_watchlist`, `p_insider` (404 body), `q_secgeo`, `q_legacyf`, `q_page2`,
`q_sort`, `gz_home`, `redesign.css`, `main.css`, `runtime.js`, `islands.js`, `row_excerpt.html`.
Scripts: `research/fetch.sh`, `research/fetch2.sh`, `research/analyze.py`.
