# finviz-ui-raw — provenance for `research/finviz-ui.md`

Captured **2026-10-30, 16:10–16:15 UTC** from `finviz.com` over plain HTTPS with a desktop Chrome
131 user-agent. No Wayback Machine was used; HTML and CSS come from the same live response set, and
the heatmap JS chunk is the one that build serves (same CSS hashes on both pages).

## Files

| File | Bytes | What it is |
|---|---|---|
| `screener_v111_live.html` | 277,838 | `https://finviz.com/screener.ashx?v=111`, HTTP 200 — the primary capture (Overview tab, 20 rows, 11 columns) |
| `redesign.28194964.css` | 242,572 | `/assets/dist/redesign.28194964.css` — layout, `.fv-container`, `.screener_*` |
| `main.33f321f9.css` | 353,152 | `/assets/dist/main.33f321f9.css` — theme tokens, `.fv-table_*` grid mechanics |
| `heatmap-ramp.5716.v1.js` | 144,945 | `/assets/dist/5716.v1.9ae3480c.js` — module `3814`: the 41/15/24-stop heatmap colour ramps, `nullColor`, colourblind switch |
| `performance-tab-v141.html` | 329,735 | `?v=141` — widest table (18 columns, breakpoint 1129 px) |
| `ownership-tab-v131.html` | 309,406 | `?v=131` — 15 columns, breakpoint 960 px |
| `map-page-excerpt.html` | 2,000 | first 2 KB of `<body>` of `https://finviz.com/map` (canvas app: no heatmap markup, no `<table>`) |
| `measurements.json` | — | weights, request counts, table geometry, page geometry |
| `views/index.json` | — | per-tab: title, column count, column ids, rows rendered, result count, left/center/total width expressions |
| `views/column-widths.json` | — | per view: each column's base px + growth coefficient, and the sums (Σbase, Σcoefficient = 1.000) |
| `views/catalog-map.json` | — | all 116 catalog keys → our view + Finviz tab + note |
| `views/v111-filters.json` | — | the 83-filter schema (`key`, `tab`, `uiType`, options) unioned over `ft=1..6` |
| `views/v111-header.html` | — | verbatim header row markup (inline `left`/`width` calc expressions) |
| `views/v111-row.html` | — | verbatim AAPL row markup (lane split, row link, logo tile) |
| `views/v111-pager.html` | — | verbatim pager markup (`r=21` step, the "273 pages" vs "Page 281" inconsistency) |
| `views/v111-filters.html` | — | verbatim filter-bar markup (`grid-cols-5`, `auto-rows-[minmax(1.875rem,auto)]`) |

Total ≈ 1.9 MB (the 15 per-tab HTML captures were trimmed to two representatives; every per-tab
number they produced is preserved in `views/index.json`, and §15 of the report shows how to
re-fetch any of them).

## Commands used

```bash
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
curl -sSL -A "$UA" https://finviz.com/screener.ashx?v=111 -o screener_v111_live.html
curl -sSL -A "$UA" https://finviz.com/assets/dist/redesign.28194964.css -o redesign.28194964.css
curl -sSL -A "$UA" https://finviz.com/assets/dist/main.33f321f9.css   -o main.33f321f9.css
for v in 111 121 131 141 151 161 171 211 311 321 341 351 411 611 711; do
  curl -sSL -A "$UA" "https://finviz.com/screener/stocks?v=$v" -o "view_$v.html"; done
curl -sSL -A "$UA" https://finviz.com/map -o map.html
```

## Known limits

* Headless Chromium was blocked by Cloudflare (`Just a moment...`), so no computed-style readback
  and no rendered-pixel sampling. All geometry is from declarations + inline styles.
* The treemap is painted on `<canvas>`; its colours are quoted from `heatmap-ramp.5716.v1.js`, not
  sampled from pixels.
* Filter popover *submission* behaviour is JS and was not executed; the report marks the
  apply-on-change conclusion as inference from the absence of any Apply/Reset control plus
  query-string state.

## What is committed and what is not

The captured HTML/CSS/JS files are third-party assets and are deliberately **not**
committed. Only the derived measurements (`measurements.json`, `views/*.json`) and
this file travel with the repo.

To reproduce a capture:

```bash
curl -sSL -A '<a real browser UA>' https://finviz.com/screener.ashx?v=111 \
  > research/finviz-ui-raw/screener_v111_live.html
grep -oE 'href="[^"]+\.css[^"]*"' research/finviz-ui-raw/screener_v111_live.html
# fetch each stylesheet into the same directory, then re-run the measurement pass
```

The measurements in `research/finviz-ui.md` cite the exact file and line they came
from, so a future capture can be checked against them.
