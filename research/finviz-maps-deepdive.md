# Finviz Maps — deep dive: app structure, DOM, JS, data flow

Measured 2026-10-31 15:20–15:35 UTC from this box, real Chromium over CDP with a desktop UA
override and a `navigator.webdriver` shim (Cloudflare otherwise serves a Turnstile interstitial),
plus raw `curl` for the API and the bundles. Every number below was observed, not estimated.

## 1. The map in one paragraph

The map is **5,906 rectangles painted on one 1025×569 canvas**, driven by a d3-hierarchy
squarify layout, with a second canvas on top that does nothing but hover highlights and drag
affordances. The DOM contains **22 nodes** for those 5,906 tiles. Data arrives as a single JSON
object of `ticker → percent change` (35,556 B on the wire for all stocks); geometry, colour,
text fitting and hit-testing are all computed in the browser. Hovering adds a **fixed-position
DOM tooltip** (400×627, `pointer-events:none`, z-index 10000) that lists the hovered industry's
peers with prices — that panel is the only DOM that grows.

## 2. App structure (how the page is assembled)

| layer | what it is | evidence |
|---|---|---|
| document | server-rendered shell (16,280 B br / 50,571 B decoded for `/map`) with an empty `#root` and inline config | `curl` |
| config | global `FinvizSettings` — **29 keys**: `hasDarkTheme`, `featureFlags{knowledgebase,quotestream,redesign,…}`, `iconSprites` (13 SVG sprite files), `nodeChartsDomain` (`charts2-node.finviz.com`), `showTickerLogos`, `indexTickersWithLogo` | inline script #5, 2,333 B |
| bootstrap | one inline dispatcher: `window.addEventListener('Finviz:FinvizInitCanvas', () => window.FinvizInitCanvas({initialPerf:{nodes:{…}}}))`, then the page fires that event | inline script, 6,759 B payload |
| island | `window.FinvizInitCanvas` creates a React root on `#root` and renders the map widget | `map.v1.df3b4f6c.js` |
| bundles | webpack-style runtime + numbered chunks: `runtime.v1`, `libs_init.v1`, `5026` (shared), `map.v1` (111,974 B), `5716` (145,092 B canvas renderer), `152` (d3), plus header/notices/dialogs | script tags + grep |
| data layer | **react-query** (`queryKey:["mapCounts"]`, `staleTime: 36e5`), and a store with `setHoveredNode`, `zoom`, `changeTranslate` (Zustand-style) | `map.v1` minified source |
| CSS | Tailwind v4 (2,553 custom properties, `@property`, `@layer`), utility classes only, no critical CSS inlined | chunk analysis from the assets report |

So it is **not** an SPA and **not** a plain MPA: each widget is its own React island, mounted by a
global function the server calls from an inline script. Navigation between sections is a real
document load; within a widget, everything is client state.

## 3. DOM — the whole map is 22 nodes

```
#root
└─ div.flex.h-full.map-viewport
   ├─ aside#map-view-sidebar[aria-label="Map controls"]     ← controls, 40 ticker buttons
   │  ├─ nav                Map · Bubbles · Matrix          (real <a href>)
   │  ├─ section "SIZE BY"  Market / Equal                  (aria-pressed)
   │  ├─ section "MAP FILTER"  S&P 500 … All Stocks 5.6K, ETFs 657, Crypto, Futures, Themes
   │  ├─ search + ticker list
   └─ div (main)
      └─ #map > #body
         ├─ canvas.chart.initialized                        1025×569, painted
         ├─ canvas.hover-canvas.absolute.top-0.left-0       1025×569, highlights only
         ├─ .map-legend  (-3% … +3%)                        DOM
         └─ #hover  (only while hovering)                   fixed, 400×627, z 10000
```

- Map filter items are **links**: `/map?t=sec`, `sec_dji`, `sec_ndx`, `sec_rut`, `sec_all`,
  `cap`, `geo`, `etf`, `crypto`, `futures`, `themes`; the matrix view is `/map?t=sec_all&view=matrix`.
- `#hover` is a DOM panel with exactly three parts: `<h3>` industry name, a "Why is it moving"
  line (with a sprite icon), and a peer table (`V 413.59 +0.74%`, `MA 638.01 +0.36%`, …).
- Hovering changes DOM nodes **22 → ~207**; the canvas hover layer also repaints
  (PNG 18,374 → 21,346 B). Leaving restores 22.

## 4. Data flow

```
GET /map                       → SSR shell + initialPerf{nodes:{503 tickers: perf}}   (6,759 B inline)
GET /api/maps/counts           → 326 B    {"sec_all":5551,"etf":657}
GET /api/map_perf?t=sec&st=d1  → 3,354 B wire / 503 nodes   ← same data as the island, revalidated
GET /api/map_perf?t=sec_all&st=d1 → 35,556 B wire / 5,906 nodes / 296 ms
GET /api/map_sparklines?t=VZ,TMUS,T,CMCSA,ECHO,CHTR&ty=sec_all → 1,326 B
```

```json
{"nodes":{"AVD":0,"BIOX":-9.368,"CF":4.412,…},
 "additional":{},"subtype":"d1","version":15,"hash":"C076…CBBC"}
```

That is the entire payload: **ticker → percent change**. No prices, no market caps, no names, no
sector labels in the map response. Size and grouping come from the model already on the client
(the same reason the SSR island only needs 503 pairs). `st` selects the metric (`d1`, `d5`, …)
which changes both the colour scale and the legend text.

Only the **default** view is server-rendered. Selecting "All Stocks" is an in-page state change:
the route updates to `/map?t=sec_all`, one 35.6 KB request fires, and the canvas repaints —
the document does not reload.

## 5. The renderer

| piece | implementation | evidence |
|---|---|---|
| layout | **d3-hierarchy treemap + squarify** (`_squarify`, `ratio`, `dice` in chunk `152`) | bundle source |
| painting | hand-written canvas painter in `5716` (145 KB) | `getContext("2d")` ×5, `fillRect` ×4 |
| tile draw | `ctx.fillStyle = colorScale(perf); ctx.fillRect(x, y, dx - 1/scale, dy - 1/scale)` — the 1/scale is the 1 px gutter | function `S` |
| text | width-fitted label via `getLongestText`, font `500 ${fontSize}px ${family}` | `fillText` ×3 |
| text shadow | fill twice: `rgba(0,0,0,.5)` offset, then `rgba(255,255,255,1)` | `S`/`w` |
| logos | sprite image drawn with `drawImage` inside the tile when it is big enough | `getSpriteImage` |
| colour | `colorScale` with a palette chosen by `scale.id`; symmetric ±3 % ramp unless min/max are asymmetric (then thirds) | `map-legend` + source |
| hover | `onMouseMove → offsetX/Y → treemap.getNodeAtPosition(x,y,view)`; if no node, the industry at that point; then `store.setHoveredNode` → repaint | `_onMouseMove` |
| tooltip | fixed DOM panel `#hover` (industry, "why moving", peer prices), `pointer-events:none` | DOM dump |
| zoom / pan | camera `{translate, scale}`; `wheel → store.zoom(camera, -deltaY, x, y)`, pointer pan and pinch | `_onWheel`, `_onPanMove` |
| drill | double-click resolves node → industry → sector and calls `onNodeClick` (no navigation) | `_onDoubleClick` |
| high-DPI | backing store scaled by camera scale; `willReadFrequently` only when a contrast/read pass is needed | `l(e,t)` factory |

Metrics of the painted result: base canvas `toDataURL` = **385,686 B** for 503 tiles and
**427,926 B** after a zoom step (a cheap proof that pixels really change), 432 quantised colour
buckets, 5,906 tiles with the same 22 DOM nodes.

## 6. Why this is fast (mechanical, not vibes)

1. **One draw call model**: 5,906 rects = 5,906 `fillRect`s in one pass, not 5,906 DOM nodes with
   layout and paint. Layout is computed once in JS (squarify) and cached in the treemap model.
2. **Hit-testing is arithmetic**, not `elementFromPoint`: `getNodeAtPosition` walks the model with
   the camera transform. No DOM query per mousemove.
3. **Hover touches two things only**: the hover canvas and one fixed panel. The base canvas is
   never repainted for a hover; the sidebar and grid do not re-render.
4. **The data is 6 bytes per tile.** 5,906 tiles cost 35.6 KB — the browser does the geometry.
5. **Only the default view is SSR**; every other view is one cheap JSON call, and the shell never
   reloads.

## 7. What it would take us to have this

We already have the hard part: `depth.json` carries 4,217 symbols with `sector` (Finviz taxonomy),
`market_cap`, and performance fields. A `/market/map?metric=perf_1d&universe=all` endpoint
returning `{symbol: value}` would be ~25 KB on the wire for 4,217 names — the same order as their
35.6 KB for 5,906.

Missing pieces, in order:

1. **Squarify layout** (~80 lines: sort by value, split the remaining rect, place rows).
2. **Canvas painter** (tiles, 1 px gutter, fitted label, colour ramp, optional logo when the tile
   is big enough).
3. **Hit test + pointer state** (map screen point → tile through the camera transform).
4. **Tooltip** — ours should be simpler than theirs: symbol, name, price, change, sector, and a
   link into the detail panel. No peer table needed for v1.
5. **Camera** (zoom/pan) — v2; v1 can be a fixed viewport with drill-by-click.

Rough effort: 2–3 days for 1–4 on our stack (canvas + a Python endpoint), +1 day for zoom/drill.
The design lesson is not the code: **do not build 4,217 DOM tiles.** Their 22-node map is the
whole reason it stays smooth at 5,906.

## 8. Caveats

- Cloudflare served a Turnstile interstitial to plain headless Chromium; all measurements come
  from sessions that cleared it with a desktop UA override.
- One direct navigation to `/map?t=sec_all` (fresh process, cache disabled) never initialised the
  canvas: `canvas.chart` stayed unclassed, no `/api/map_perf` request fired, `#map` had 22 nodes.
  The in-app switch to the same URL worked and is what the numbers above use. I did not chase the
  cause; a blocked ad/analytics script is the likely trigger.
- Ads were present (the Elite dialog had to be dismissed before the canvas could receive pointer
  events — a real, if annoying, detail about testing this site).
- The map bundle is minified; function names above are the minified ones, quoted from source.
