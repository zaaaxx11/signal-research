# Finviz UI forensics — measured anatomy, and what it means for a 116-column, 100-row screener

Captured **2026-10-30, 16:10–16:15 UTC** from the live site (no Wayback needed). Every number
below is either (a) a CSS declaration quoted with its selector, (b) a count produced by a command
listed in §15, or (c) arithmetic shown inline. Raw evidence is in `research/finviz-ui-raw/`.

---

## 0. TL;DR

Finviz is not a dense table. It is a **20-row, 11-column, paginated grid that is engineered to fit
the viewport exactly**, wrapped in an 83-control filter wall, shipped in 4.10 MB and 74 requests.

Six findings that should change our design:

1. **They solved horizontal overflow by abolishing it.** Columns per view are capped at 18, and each
   column's width is `base + (container − breakpoint) × coefficient` where the bases sum to the
   breakpoint and the coefficients sum to **1.000** (measured, six views, §3.4). The table is never
   wider than the container, so there is nothing to scroll sideways. The "70-column screener" no
   longer exists in the product.
2. **20 rows is a server-declared constant, not a CSS accident**: the page ships
   `"screenerPageSizes":{"table":20,"snapshot":10,"charts":12}`. 5,620 results ÷ 20 = 281 pages.
   Their density works *because* they never ask the user to scan more than 20 rows at once.
3. **Row height 20 px, type 12 px, cell padding 4 px, header 24 px.** That is the entire density
   budget, and it is only defensible with a 20-row cap.
4. **Green/red are two different systems.** Text uses `#19803d`/`#d91e2b` (dark theme:
   `#34c062`/`#fd8487`) and passes AA. The heatmap uses a separate 41-stop ramp
   `#f63538 → #414554 → #30cc5a` with **white labels at 2.12:1 on the green end** — they paper over
   it with a 50 %-black text shadow, not with a compliant colour.
5. **The filter sidebar is gone.** 83 filters live *above* the table in a 5-column grid; on the
   "All" tab that is 17 rows × 30 px = **510 px of controls above a 424 px table**. Their filter UI
   is taller than their data.
6. **Our six views cannot render our own catalog.** Of 116 catalog keys, the six view definitions
   reference **48**; 23 of the 71 names they use do not exist in the catalog at all (§11.1).

---

## 1. Method, provenance, and what failed

| Item | Value |
|---|---|
| Primary URL | `https://finviz.com/screener.ashx?v=111` → HTTP **200**, 277,838 B |
| Canonical URL (same content) | `https://finviz.com/screener/stocks?v=111` → 200, 277,484 B |
| CSS | `/assets/dist/redesign.28194964.css` 242,572 B · `/assets/dist/main.33f321f9.css` 353,152 B |
| All 15 tabs | fetched individually, each HTTP 200 (§5) |
| Map | `https://finviz.com/map` → 200, 51,112 B (canvas app, no heatmap markup) |
| Heatmap code | `/assets/dist/5716.v1.9ae3480c.js` (144,945 B), referenced by the same build |
| UA used | Chrome 131 desktop string (`curl -sSL -A …`) |

**Same-era check (required because the heatmap colours live in JS, not in the HTML's CSS).**
Both captured documents reference *identical* hashed bundles — `main.33f321f9.css` and
`redesign.28194964.css` appear in `screener_v111_live.html` **and** `/map`; the heatmap chunk is
`5716.v1.9ae3480c.js` on the same `/map` response. So HTML, CSS and the colour ramp are one build.
No Wayback snapshot was used, so there is no snapshot-date mismatch risk.

**What failed.** A real browser (`chromium`, headless) was pointed at the same URL to read
`getComputedStyle`. Cloudflare returned the interstitial (`<title>🐴 Just a moment...</title>`) and
never cleared across three 5-second polls. So **computed style could not be captured**; every
dimension below is derived from the served CSS declarations plus inline `style` attributes in the
HTML, which for this site is sufficient because the geometry is fully declarative (no JS-set pixel
values for row height, padding, or font size). The one JS-computed surface — the treemap canvas —
is documented from its source and marked as such (§7).

**What is therefore unverified** (also listed in §14): hover/popover *behaviour* (submission
timing), actual rendered pixels at a given viewport, and any colour that exists only inside canvas
paint. Claims of the form "clicking X does Y" are flagged; claims of the form "selector S declares
V" are all quotable.

---

## 2. Page layout, measured

Finviz's screener is a **single-column document**. There is no `<aside>`, no `<main>`, no
`<header>` in the captured HTML — the only semantic landmarks are three `<nav>` elements
(`aria-label="Screener filter groups"`, `"Page moves"`, `"Pagination"`). Structure is carried by
divs plus Tailwind utilities.

| Region | Measurement | Evidence |
|---|---|---|
| Page container | `min-width:1009px; max-width:1425px; padding:0 20px` → content **969–1385 px** | `redesign.28194964.css`: `.fv-container{width:100%;min-width:1009px;max-width:1425px;margin:0 auto;padding:0 20px}` |
| Viewport contract | **`<meta name="viewport" content="width=1024">`** — the layout is designed for a 1024 px desktop and scales up, never down | captured `<head>` |
| Max-width token | `--spacing-page-viewport:max(1009px, calc(100vw - var(--fv-scrollbar-width)))` | `main.33f321f9.css` |
| Top nav | one row of 16 `a.nav-link` items (Home, Stock Screener, ETF Screener, Maps, Charts, News, Groups, Insider, Futures, Forex, Crypto, Watchlist, Portfolio, Calendar, Pricing, Help) | captured `<a class="nav-link" href="/">` … |
| Promo band | Elite upsell above the app: "Elite members get real-time data… starting at $24.96/mo" + `Try Elite for Free` → `/register?poster=trial` | captured HTML |
| Filter block | full container width, `grid-cols-5`, collapsible via a `Filters` button (`aria-controls="screener-split-filters"`) | §6 |
| Results table | full container width, **1066 px** total at the smallest supported width for the Overview view | §3 |
| Sidebar | **none.** The classic left filter rail was removed in this redesign | 0 `<aside>` elements; filters are `data-testid="screener-split-filters"` above the table |

There is no fixed "sidebar width" to copy — that is itself the finding. The nearest equivalent is the
width of one filter field, and it is computed, not observed: container content 1385 px (max) or
969 px (min), minus `px-1.5` (6 px each side) = 1373 / 957 px, divided into `grid-cols-5` with no gap
= **275 px or 191 px per field**; each field is itself `grid-cols-2` with a 6 px gap, so the label and
the control each get **≈134 px (max) or ≈93 px (min)**. A 93–134 px control is why every filter is a
dropdown that says "Any" and why none of them can be a two-input range.

The vertical budget is
spent on: promo band → nav → **83-filter block** → 24 px column header → 400 px of rows → 32 px
toolbar → 36 px pager.

---

## 3. The results grid

### 3.1 There is no `<table>`

`grep -c '<table' screener_v111_live.html` → **0**. The grid is
`role="grid"` + `role="row"` + `role="columnheader"` + `role="gridcell"` with `aria-rowindex`,
`aria-colindex`, `aria-colcount="11"`, `aria-sort="none"|"ascending"`, `aria-multiselectable`.
Rows are absolutely positioned and virtualised:

```css
.fv-table_row { --fv-row-bg:var(--background-color-primary); background:var(--fv-row-bg);
                position:absolute; top:0; left:0 }
```
```html
<div class="fv-table_row" data-row-key="AAPL" style="transform:translateY(calc(var(--fv-table-row-height) * 15));
     --fv-row-height:var(--fv-table-row-height);height:var(--fv-table-row-height)"
     data-link="true" aria-rowindex="17" data-parity="odd" data-row-index="15">
```

Why this matters to us: an HTML `<table>` cannot virtualise rows (heights must be knowable and the
browser owns layout). Finviz pays for a 20 px row by giving up `<table>`, and buys back
virtualisation, static lanes and O(1) row height. **Our UI still uses `<table class="market-table">`**
(`ui/index.html:156`), which is the right call for 100 static rows — `<table>` gives free column
alignment and a real accessibility tree. Copy the *lane* idea, not the div-grid.

### 3.2 The eight numbers

| Property | Value | Selector / token |
|---|---|---|
| **Row height** | **20 px** | `--fv-table-row-height-base:var(--text-2xs--line-height)`; `--text-2xs--line-height:1.25rem` |
| **Header height** | **24 px** | `--fv-table-header-height-base:1.5rem` |
| **Font size / line-height** | **12 px / 20 px** | `--text-2xs:.75rem`; `.fv-table{font-size:var(--text-2xs);line-height:var(--text-2xs--line-height)}` |
| **Cell padding (x)** | **4 px** | `--fv-table-cell-padding-x:var(--spacing)`; `--spacing:.25rem` |
| **Cell border** | **1 px**, `solid transparent` (reserved for focus/selection, not for grid lines) | `--fv-table-cell-border-base:1px`; `.fv-table_cell,.fv-table_header-cell{…border:var(--fv-table-cell-border) solid transparent}` |
| **Header background** | `#fff` (light) / `#22262f` (dark) — **identical to the row background**; the header is separated only by a 1 px bottom border | `.fv-table_header-cell{background:var(--background-color-primary)}`; `.fv-table_top{border-bottom:1px solid var(--border-color-secondary)}` |
| **Text colour** | `#22262f` on `#fff` (light) / `#f3f3f5` on `#22262f` (dark) | `--text-color-default:var(--color-gray-800)` |
| **Zebra stripe** | `#f3f3f5` (light) / `#2c303b` (dark), applied to **odd** rows | `.fv-table_row[data-parity=odd]{--fv-row-bg:var(--background-color-neutral)}`; `--background-color-neutral:var(--color-gray-50)` |
| **Grid line colour** | `#dedfe5` (light) / `#353945` (dark) — used only for the header underline, the pinned-lane inset shadow and `.fv-table_bottom`'s top border; **never between rows or columns** | `--border-color-secondary:var(--color-gray-100)` |

`.fv-table_cell{line-height:calc(var(--fv-row-height,var(--fv-table-row-height)) - 2 * var(--fv-table-cell-border))}`
— the line box is exactly `row height − 2×1px`, i.e. 18 px inside 20 px. There is **no vertical
padding**; the row is the line box.

Numeric columns are right-aligned (`[data-align=right]{text-align:right}`) and the grid sets
`font-variant-numeric:tabular-nums` on the container, so digits line up without per-cell classes.
This is the single cheapest density trick in the whole design and we should take it.

### 3.3 Header and first column are both pinned — with a 2-property trick

```css
.fv-table_top  { z-index:2; background:var(--background-color-primary);
                 border-bottom:var(--fv-table-cell-border) solid var(--border-color-secondary);
                 position:sticky; top:0 }
.fv-table_lane[data-lane=left] { z-index:1; background:inherit;
                 box-shadow:inset -1px 0 0 var(--border-color-secondary);
                 position:sticky; left:0 }
```

The left lane is a **lane**, not a cell: one sticky wrapper holding `No.` (31 px) + `Ticker`
(70 px) = **101 px** (`--fv-table-left-w:101px`). Right lane is symmetric and unused here
(`--fv-table-right-w:0px`). The lane separator is a `box-shadow: inset -1px 0 0`, not a border —
so it does not consume layout width and cannot desynchronise the row's absolute positioning.

We already do the equivalent with `position:sticky;left:0` on `.col-name` (`ui/styles.css:614-621`),
but we pay for it with a real `box-shadow` on every cell and a hard-coded hover background
(`#1b101d`), which breaks when the row is selected. Copy the `background:inherit` pattern instead:
the lane inherits `--fv-row-bg`, so zebra, hover and selection all flow through one variable.

### 3.4 Horizontal overflow: measured, and it does not happen

Every header cell carries an inline width of the form

```
width: calc(<base>px + max(0px, 100cqw - <breakpoint>px) * <coefficient>)
```

where the table is a **container query container** (`container-type:inline-size`). Summing the
declared widths for each view (`python3` parse of the inline styles, `views/column-widths.json`):

| View | v= | Columns | Σ base widths | Σ growth coefficients | Breakpoint |
|---|---|---|---|---|---|
| Overview | 111 | 11 (9 sized + pinned 2) | 965 px | **1.000** | 1066 px |
| Valuation | 121 | 18 (16) | 971 px | **1.000** | 1072 px |
| Ownership | 131 | 15 (13) | 859 px | **1.000** | 960 px |
| Performance | 141 | 18 (16) | 1028 px | **1.000** | 1129 px |
| Financial | 161 | 18 (16) | 1016 px | **1.000** | 1117 px |
| Technical | 171 | 15 (13) | 778 px | **0.999998** | 879 px |

Σ bases = breakpoint − 101 (the pinned lane), and Σ coefficients = 1.0. Therefore
`total width = 101 + (breakpoint − 101) + (container − breakpoint) × 1.0 = container width`,
exactly, at every viewport ≥ breakpoint. **The table is the viewport.** Below the breakpoint the
coefficients clamp to 0 (`max(0px, …)`) and the table holds its minimum width, which is why
`.fv-container` has `min-width:1009px`.

Consequences:

* The widest view (Performance, 18 columns) needs **1129 px** inside a container that offers
  **969–1385 px**. It fits at ≥1129 px viewport and overflows below that.
* Overflow, when it happens, is handled by a **hidden scrollbar plus edge fades**:
  `.fv-table_viewport{overflow:auto hidden;scrollbar-width:none}`,
  `.fv-table_viewport::-webkit-scrollbar{display:none}`, and
  `.fv-table_edge-fades:before/:after{width:16px;box-shadow:0 0 16px var(--shadow-color)}` that fade
  in only when `[data-scroll-x=middle|end]`.
* Only **11–18 columns exist per tab**. To get more, Finviz offers a *different layout*, not more
  columns: Snapshot (v=341), Basic (v=311), Charts (v=211), Tickers (v=411), Stats (v=611),
  Maps (v=711).

**Honest correction to the brief:** the "~70-column table with horizontal overflow" is not in the
current product. The column-per-view ceiling is 18. Any argument for 70 columns has to be made
against this evidence, not with Finviz as precedent.

### 3.5 Density cost of one extra column

Column widths are hand-tuned per view and reveal their priorities: `Country` 57 px, `P/E` 55 px,
`Price` 55 px, `Change %` 64 px, `Ticker` 70 px, `Volume` 83 px, `Company` 262 px (its coefficient
0.2715 is the largest — the name column absorbs 27 % of any extra width), `Industry` 189 px.
Adding a column means taking pixels from another: at 1066 px, one more 55 px column costs
`Company` ≈ 15 px of truncation. That is the real price of a 12th column, and it is visible.

---

## 4. Colour system

### 4.1 Two token sets, one semantic layer

The captured document ships `<html lang="en" class=" dark">`, so **the default rendering is dark**;
the header exposes a `#moonOutlined` toggle. Both sets are declared:

| Role | Light | Dark | Declaration |
|---|---|---|---|
| Up / positive text | **`#19803d`** | **`#34c062`** | `--text-color-positive:var(--color-green-500)` → `--color-green-500:#19803d`; dark: `--text-color-positive:var(--color-green-300)` → `#34c062` |
| Down / negative text | **`#d91e2b`** | **`#fd8487`** | `--text-color-negative:var(--color-red-500)`; dark: `…var(--color-red-300)` |
| Positive background | `#e7f7e8` | `#1e4027` | `--background-color-positive:var(--color-green-50)` / `green-700` |
| Negative background | `#fef0f0` | `#691f24` | `--background-color-negative:var(--color-red-50)` / `red-700` |
| Body text | `#22262f` | `#f3f3f5` | `--text-color-default` |
| Muted text | `#676f85` | `#a3a8b9` | `--text-color-muted` |
| Row hover | `#ebf4fe` | `#353945` | `--background-color-accent:var(--color-blue-50)` |

Applied through one attribute, not per-cell classes:

```css
.fv-table_cell[data-tone=positive] { color:var(--text-color-positive) }
.fv-table_cell[data-tone=negative] { color:var(--text-color-negative) }
.fv-table_cell[data-tone=caution]  { color:var(--text-color-caution) }
.fv-table_cell[data-tone=muted]    { color:var(--text-color-muted) }
```

In the captured Overview page: `data-tone="negative"` × 30, `data-tone="positive"` × 7 over 20 rows.

**Colour-blind scale is a first-class switch**, not a filter: `:root .is-scale-red-blue{--text-color-positive:var(--color-blue-500)}` (`#306dca`) and dark `--color-blue-400` (`#2f91ef`). The
class is applied to the map's settings popover, and swaps green→blue globally, *both* in table text
and on the heatmap ramp. That is the right architecture: one flag, all surfaces.

### 4.2 Contrast, computed (WCAG 2.1 relative luminance)

| Pair | Ratio | Verdict |
|---|---|---|
| `#19803d` on `#ffffff` (light, positive) | **5.00:1** | AA pass |
| `#d91e2b` on `#ffffff` (light, negative) | **5.03:1** | AA pass |
| `#34c062` on `#22262f` (dark, positive) | **6.39:1** | AA pass |
| `#fd8487` on `#22262f` (dark, negative) | **6.37:1** | AA pass |
| `#19803d` on `#f3f3f5` (light **zebra** row) | **4.52:1** | AA pass by 0.02 |
| `#d91e2b` on `#f3f3f5` (light zebra row) | **4.54:1** | AA pass by 0.04 |
| `#676f85` on `#ffffff` (muted / header labels) | **5.01:1** | AA pass |
| **`#ffffff` on `#30cc5a`** (heatmap label, green extreme) | **2.12:1** | **FAIL** |
| **`#ffffff` on `#f63538`** (heatmap label, red extreme) | **3.83:1** | AA-large only |
| `#ffffff` on `#414554` (heatmap label, neutral mid) | 9.53:1 | AA pass |

Two conclusions, both load-bearing:

* **The text tokens are compliant, but only just on the light zebra stripe** (4.52/4.54 against a
  4.5 floor). A 1 % tint change in `gray-50` would break AA. If we ever run a light theme, pick
  our greens/reds with more headroom than Finviz did.
* **The heatmap is not compliant and does not try to be.** White labels on the ramp reach 2.12:1.
  Their mitigation is not a colour fix; it is a text shadow, and it is visible in the source (§7.2).
  This is exactly the trap to avoid if we add shading to our own cells.

**Our tokens for comparison** (`ui/styles.css:17-18`): `--green:#82e6bd` on `--surface:#170e19` =
**12.58:1**; `--red:#ff738e` = **7.27:1**; `--faint:#9a8592` = 5.52:1. We have 2.5× the contrast
headroom Finviz has on the light theme. Do not import their hexes.

---

## 5. The tab set, measured

Fifteen tabs, each fetched (all HTTP 200). Note the **v-code permutation**: Financial is `161`,
not the classic `131`, and Ownership is `131`. Anything of ours that hard-codes classic Finviz
`v=` numbers is wrong for the current product.

| Order | Tab | href | Grid? | Columns | What it actually is |
|---|---|---|---|---|---|
| 1 | Overview | `?v=111` | yes | **11** | identity + size + price + change + volume |
| 2 | Valuation | `?v=121` | yes | **18** | all multiples, no prose columns |
| 3 | Financial | `?v=161` | yes | **18** | margins, returns, leverage + dividends |
| 4 | Ownership | `?v=131` | yes | **15** | insider/inst own+trans, short interest, share counts |
| 5 | Performance | `?v=141` | yes | **18** | 9 return windows + volatility + volume |
| 6 | Technical | `?v=171` | yes | **15** | beta, ATR, SMA distances, 52w/50d ranges, RSI |
| 7 | Custom | `?v=151` | yes | **11** (default) | same 11 as Overview; user-defined set, stored client-side |
| 8 | Charts | `?v=211` | no | — | per-row sparkline/chart layout |
| 9 | Tickers | `?v=411` | no | — | 1,000 `inline-block` ticker links (776 KB page) |
| 10 | Basic | `?v=311` | no | — | `<table class="screener_snapshot-table-*">` label/value cards |
| 11 | TA | `?v=351` | no | — | same snapshot-card layout, TA fields |
| 12 | News | `?v=321` | no | — | snapshot cards + `<table class="news-table">` |
| 13 | Snapshot | `?v=341` | no | — | snapshot cards, 14 label/value pairs per symbol |
| 14 | Maps | `?v=711` | no | — | treemap island (canvas) |
| 15 | Stats | `?v=611` | no | — | aggregate statistics, no rows |

Measured detail: the non-grid tabs use `screener_snapshot-table-header` +
`screener_snapshot-table-body` with `snapshot-td-label`/`snapshot-td-content` pairs — 280 of each
per page, i.e. **14 label/value pairs × 20 symbols**. That is Finviz's answer to "how do you show
70 fields for one symbol": you transpose into a card, and you show 20 cards, not 20 rows.

The 11 Overview columns are exactly: `row, ticker, company, sector, industry, country, marketCap,
PE, price, change, volume`. **No earnings date, no ownership, no performance** — the default view is
deliberately shallow.

---

## 6. Filters: 83 controls, no sidebar, no Apply button

The filter block is `data-testid="screener-split-filters"`, a **5-column grid above the table**:

```html
<nav aria-label="Screener filter groups" role="tablist" class="screener_filter-tabs-wrapper …">
  <a class="screener_filter-tab … h-6 … px-2.5" role="tab" aria-selected="true" href="?v=111&ft=1">Descriptive</a>
  … Fundamental (?ft=2) · Technical (ft=3) · News (ft=6) · ETF (ft=5) · All (ft=4)
</nav>
<div class="grid grid-cols-5 items-center auto-rows-[minmax(1.875rem,auto)]">
  <div class="field grid grid-cols-2 items-center gap-1.5"> <label class="field_label text-2xs text-muted text-right">Exchange</label>
    <button class="select-trigger … h-6 rounded-md border px-2 text-2xs" aria-haspopup="listbox">Any</button></div>
```

Measured census (parsed from the embedded filter schema on each `ft=` page, `views/v111-filters.json`):

| Group | `tab` | Filters |
|---|---|---|
| Descriptive | `descriptive` | **23** |
| Fundamental | `fundamental` | **37** |
| Technical | `technical` | **20** |
| News | `news` | **2** |
| ETF | `etf` | **1** |
| **All (union)** | — | **83 distinct** |

Control types — and this is the important part:

| `uiType` | Count | What it is |
|---|---|---|
| `floatRangeSelect` | **52** | a dropdown of *preset buckets* ("Under $2B", "Over $10B"), never min/max inputs |
| `modalSelect` | **16** | a popover listbox (Sector 13 options, Industry 148, Sub-theme 270) |
| `select` | **13** | short inline list |
| `tickersInput` | **2** | the only free-text control: a ticker list |

**Zero `<input type="number">`, zero checkboxes, zero radios, zero sliders**
(`type="number"` → 0, `type="checkbox"` → 0, `type="radio"` → 0, `role="slider"` → 0; the page
contains **3 `<input>` elements in total**). There is **no `Apply` and no `Reset` control**
in the captured HTML (`Apply` → 0 occurrences, `Reset` → 0). Filter state is carried in the query
string (`?v=111&ft=1`, pager `?v=111&r=21`), so each filter change is a navigation — i.e.
server round-trip, not client filter. *Whether the popover submits on selection is JS behaviour we
could not execute (§14); the absence of an Apply control plus URL-encoded state is strong evidence
of apply-on-change, but it is inference.*

**Vertical cost, computed.** `auto-rows-[minmax(1.875rem,auto)]` = 30 px minimum per row, 5 per row:

| Group | Rows | Height |
|---|---|---|
| Descriptive | ⌈23/5⌉ = 5 | 150 px |
| Fundamental | ⌈37/5⌉ = 8 | 240 px |
| Technical | ⌈20/5⌉ = 4 | 120 px |
| **All** | ⌈83/5⌉ = **17** | **510 px** |

The table module is 424 px (24 header + 20×20 rows). On the All tab the filter block is **1.2×
taller than the data it filters**. On the default tab it is 150 px — 35 % of the table's height —
for 23 controls, of which we would use maybe six.

**Elite gating:** 81 of the 83 filters contain at least one `"isEliteOnly": true` option. Filtering
is the paywall, not an afterthought.

---

## 7. The heatmap / Maps tab

`/map` is reachable (200) but contains **no heatmap markup**: `grep -c heatmap` → 0,
`grep -c treemap` → 0, `grep -c '<table'` → 0. It is a canvas app: the CSS refers to
`.index-heatmap_layout:has(#index-heatmap-root canvas.initialized)`. The in-screener Maps tab
(`?v=711`) is the same island with a 603 KB shell and no grid.

### 7.1 The colour ramps (from source)

The colour logic is in `5716.v1.9ae3480c.js`, module `3814`:

```js
n = { colors:["#f63538","#ee373a", … ,"#414554", … ,"#30cc5a"], nullColor:"#2f323d" }   // DayPerf, 41 stops
d = { colors:["#e01427", … ,"#414554", … ,"#12c93b"],           nullColor:"#2f323d" }   // Quick/Current ratio, 15 stops
c = { colors:["#f5820f", … ,"#414554", … ,"#2f8fed"],           nullColor:"#2f323d" }   // perf variants, 41 stops
u = { colors:["#fa4f55", … ,"#2f8fed"],                          nullColor:"#414554" }   // net flows, 24 stops
```

and the diverging domain is split at the ramp's midpoint:
`const o=Math.floor(a.colors.length/2)` — index 20 of 41, i.e. exactly `#414554`. Small tiles
downgrade to a 15-stop ramp (`this.getIsSmall() && this.scale.id===DayPerf → colorblind ? Sl : CK`).

| Property | Value |
|---|---|
| Neutral / zero | `#414554` (mid-ramp, not a tint of the page background) |
| No-data | `#2f323d` |
| Red extreme | `#f63538` |
| Green extreme | `#30cc5a` |
| Ramp length | 41 stops (continuous-looking, 8-bit) |
| Gradient vs buckets | continuous ramp; the "bucketed" alternative exists only for quick/current ratio |
| Colour-blind | `.is-scale-red-blue` → positive becomes blue: `#306dca` (light) / `#2f91ef` (dark), in text **and** on the ramp |

### 7.2 The label trick (the accessibility answer)

```js
e.textBaseline="middle", e.textAlign="center",
e.fillStyle="rgba(0, 0, 0, 0.5)";        // shadow pass
…
e.fillStyle="rgba(255, 255, 255, 1)";    // text pass
```

Labels are **white, drawn twice: a 50 %-black offset copy behind a white copy**. The legend chips
do the same in CSS: `text-shadow:0 1px 0 rgba(0,0,0,0.25)`. So Finviz knows the ramp cannot carry
compliant text and compensates with a shadow rather than re-picking colours. At 2.12:1 (§4.2) a
shadow is the difference between "readable" and "not", but it is not a WCAG pass and it will not
survive a screenshot, print, or a 1.4× zoom.

---

## 8. Interaction model

| Behaviour | Measured implementation |
|---|---|
| **Row hover** | `.fv-table:not([data-drag]) .fv-table_row:hover{--fv-row-bg:var(--background-color-accent)}` → `#ebf4fe` (light) / `#353945` (dark). One variable drives background, so hover, zebra and selection compose. |
| **Row click** | The whole row is one anchor: `.fv-table_row-link{position:absolute;inset:0}` with `href="/quote?t=AAPL&b=1&ty=c&p=d"`. Cells are `pointer-events:none` (`[data-link]:not([data-covered]) :is(.fv-table_lane,.fv-table_cell){pointer-events:none}`) so nothing steals the click; interactive children opt back in. **Click = navigate away**, full page load. |
| **Hover extras** | The 18 px logo tile is `filter:grayscale(1) contrast(0.3) brightness(1.1)` at rest and `row-hover:[filter:none]` on row hover — colour returns per-row. |
| **Tooltip** | Header cells carry `data-tooltip` + `aria-label` when the label is truncated (`data-label-short="true"` renders `Mkt Cap` instead of `Market Cap`). Tooltip surface: `.fv-table_tooltip{max-width:24rem;padding:4px 8px;font-size:var(--text-2xs);border:1px solid var(--border-color-secondary)}`. |
| **Sort** | `aria-sort` on the header, `.fv-table_sort-indicator{min-width:.75rem}`; multi-sort chips exist (`fv-table_sort-chip`, 20 px tall, `data-dragging` reorder) |
| **Column management** | Per-column hover menu: `.fv-table_header-menu{opacity:0;…}` → `opacity:1` on hover; menu items include **"Hide Column"** (string in `1498.v1.js`). Drag-reorder: `[data-draggable]{cursor:grab}`, `.fv-table_column-drag-band{background:rgb(from var(--border-color-highlight) r g b / .08);border:1px dashed …}`. Resize handles: `.fv-table_resize-handle{width:8px;cursor:col-resize}`. |
| **Focus** | `:is(.fv-table_cell,.fv-table_header-cell):focus-visible{border-color:var(--border-color-highlight);outline:none}` — focus is a **1 px border colour change**, no ring, and every cell is `tabindex="-1"` (keyboard movement is JS-managed, roving). |
| **Selection** | `aria-multiselectable="true"`, `[aria-selected=true]{--fv-row-bg:var(--background-color-highlight)}` (`#ebf4fe`) |
| **Pager** | `#1 / 5620 Total`; pages `?v=111&r=21,41,…,5601`; jump field 56 px wide; `aria-label="Skip to page 144 of 273 pages"` **while also linking "Page 281"** — their own pager label contradicts their own page count (5620/20 = 281). A real bug, quoted as a warning about building pagination by hand. |

---

## 9. Page weight, measured

Counted from the captured HTML (`48` unique JS URLs, `2` CSS, `21` `<img>`, `2` font preloads),
then every asset fetched and sized (`views/../measurements.json`):

Raw tag census in `screener_v111_live.html`: **56 `<script>` elements** (38 with `src`, 18 inline),
**57 `<link>` elements** = `preload as=script` 31 + `preload as=image` 20 + `preload as=font` 2 +
`stylesheet` 2 + `icon` 2, and **21 `<img>`**. Note the 20 image preloads duplicate the 20 logo
`<img>` tags, so the logos are referenced 40 times for 20 URLs.

| Kind | Requests | Bytes |
|---|---|---|
| HTML | 1 | 277,838 |
| CSS | 2 | 595,724 |
| JavaScript | **48** (47 first-party chunks + 1 beacon) | **2,944,976** |
| Images | 21 | 181,906 |
| Fonts | 2 (IBM Plex Sans woff2, roman + italic) | 95,896 |
| **Total** | **74** | **4,096,340 B ≈ 4.10 MB** |

Breakdowns that matter:

* **JS is 72 % of the page.** One chunk alone is 571,553 B (`1498.v1.js`); five exceed 200 KB.
* **Third-party + tracking = 56,661 B (1.4 % of bytes, 3 requests)**: Quantcast pixel
  `pixel.quantserve.com` 45,004 B (which **404s** and returns an HTML error page), Google One Tap
  6,886 B, first-party pageview script 4,771 B, plus a Cloudflare Insights beacon that was blocked
  in this environment (0 B here, would be ~1 request). The Elite banner is inline markup, not an
  image. **So the numbers include ad/tracker assets, and they are a rounding error — the weight is
  first-party application JS, not ads.** That inverts the usual assumption.
* **Logos: 20 requests, 136,902 B, and 4 of the 20 return HTTP 404** with a 27,150-byte HTML error
  page each → **108,600 B (79 %) of the logo bytes are error pages** for real SVGs that total
  28,302 B. Their `<img>` has no `loading="lazy"` (`loading` attribute absent; the tile uses
  `data-image-tile` + a `data-loaded`/`data-failed` observer and a 9 px letter fallback).
* 291 inline `style="…"` attributes and 18 inline `<script>` blocks — the geometry is inline, which
  is why this page cannot be themed or cached cleanly.

For scale: our entire `ui/app.js` is 44,525 B and `ui/index.html` 18,233 B. Finviz ships **66×** our
JS to render 20 rows.

---

## 10. Token table (the part we can actually use)

### 10.1 Colour

| Token | Light | Dark | Use |
|---|---|---|---|
| `--text-color-default` | `#22262f` | `#f3f3f5` | cell text |
| `--text-color-muted` | `#676f85` | `#a3a8b9` | header labels, secondary |
| `--text-color-muted-2` | `#a3a8b9` | `#676f85` | toolbar count, disabled pager |
| `--text-color-positive` | `#19803d` | `#34c062` | up |
| `--text-color-negative` | `#d91e2b` | `#fd8487` | down |
| `--text-color-caution` | `#8b6a00` | `#fcdc86` | warnings |
| `--text-color-link` | blue-500 `#306dca` | blue-300 `#57aefb` | tickers, pager |
| `--background-color-primary` | `#fff` | `#22262f` | page + row even |
| `--background-color-neutral` | `#f3f3f5` | `#2c303b` | row **odd** (zebra) |
| `--background-color-accent` | `#ebf4fe` | `#353945` | row hover / current page |
| `--background-color-highlight` | `#ebf4fe` | `#263766` | selected row |
| `--border-color-primary` | `#dedfe5` | `#4c5263` | panel/frame borders |
| `--border-color-secondary` | `#dedfe5` | `#353945` | header underline, lane shadow, pager top |
| `--border-color-highlight` | blue-400 | blue-400 | focus border, drag band, progress bar |
| `--shadow-color` | `color-mix(in srgb, #14161d 12%, transparent)` | `#0009` | edge fades, popovers |

### 10.2 Spacing

`--spacing: .25rem` (4 px) is the **only** spacing unit; every measurement is a multiple:
`calc(var(--spacing) * n)`. Observed multiples: 0.5 → 2 px (filter cell padding-x is
`calc(padding-x / 2)` = 2 px), 1 → 4 px (cell padding, gaps), 1.5 → 6 px, 2 → 8 px (toolbar pad,
popover padding), 2.5 → 10 px, 3 → 12 px (toolbar gap, pager pad-top), 6 → 24 px (overlay padding).
`auto-rows-[minmax(1.875rem,auto)]` = 30 px = 7.5×4 px — the only non-integer multiple in the system.

### 10.3 Type scale

| Token | px | Used for |
|---|---|---|
| `--text-4xs` | 8 | (reserved) |
| `--text-3xs` | **10** | sort-level badge, logo fallback letter is 9 px (arbitrary `text-[9px]`) |
| `--text-info` | 11 | info labels |
| `--text-2xs` | **12** | **the table** — cells, headers, toolbar, pager, filters |
| `--text-xs` | 13 | settings labels |
| `--text-sm` | 14 | body copy |
| `--text-base` | 16 | pager gap glyph, dialogs |
| `--text-lg…5xl` | 18 / 20 / 24 / 32 / 40 / 48 | headings |

Font family: `"IBM Plex Sans", "IBM Plex Sans Fallback", ui-sans-serif, system-ui, …`. Two woff2
files, 95,896 B total, subset to `latin` + italic. Numerics are **not** a mono face — they rely on
`font-variant-numeric: tabular-nums` on `.fv-table`.

### 10.4 Radii, borders, focus

| Token | Value | Where |
|---|---|---|
| `--border-radius-sm` | **2 px** (`.125rem`) | table action buttons, menu items |
| `--radius-sm` | 4 px (`.25rem`) | chips, small controls |
| `--border-radius-md` / `--radius-md` | **6 px** (`.375rem`) | filter controls (h-6 pills), pager, popovers |
| `--radius-lg` | 8 px | panels, dropdown |
| `--radius-xl` / `2xl` / `3xl` | 12 / 16 / 24 px | cards, dialogs |
| Table grid lines | **none** — separators are `border-bottom` on the header only + `box-shadow: inset -1px 0 0` on the pinned lane | §3.3 |
| Row separator | **none**; rows are separated by the zebra stripe alone | `.fv-table_row[data-parity=odd]` |
| Focus | 1 px border recolour, `outline:none` (no ring) | `:focus-visible{border-color:var(--border-color-highlight);outline:none}` |
| Control height | `--fv-table-control-size:24px` (buttons, pager cells) | reused everywhere |

---

## 11. Mapping our 116 catalog keys onto the six views

Source: `app/engine/data/finviz_columns_116.json` (the 116 rows), `app/engine/catalog.py`
(`CATALOG`, `EXPECTED_COLUMNS = 116`), `app/screen/views.py` (`VIEWS`), `ui/app.js` +
`ui/index.html` (what we render).

### 11.1 First, the bad news, measured

Parsing `VIEWS` out of `app/screen/views.py` and diffing against the catalog keys:

* the six views reference **71** column names, of which **48** are catalog keys;
* **23 of the 71 names do not exist in the catalog** — `change_pct`, `eps_ttm`, `week52_high`,
  `week52_low`, `perf_year`, `rsi_14`, `atr_14`, `rel_volume`, `ipo`, `index`, `option_short`,
  `perf_week`, `perf_month`, `perf_quarter`, `perf_half_y`, `income`, `eps_next_y_growth`,
  `eps_past_3_5y`, `sales_past_3_5y`, `oper_margin`, `profit_margin`, `avg_volume`, `volatility`;
* **68 of the 116 catalog keys are referenced by no view at all**;
* **coverage is 48/116 = 41 %.**

Two vocabularies are live at once (`app.market.fields` names in the views, `catalog` names in the
engine) and nothing translates them. Any UI built on `VIEWS` today can render **41 % of the
catalog**, and 23 of its columns would come out blank because the keys miss.

### 11.2 The assignment (110 assigned, 6 explicitly homeless)

Rule: primary home = the catalog's own `category`, mapped to our nearest view, with documented
deviations. Machine-readable in `research/finviz-ui-raw/views/catalog-map.json`.

| Our view | Keys | Count | Finviz tab it comes from |
|---|---|---|---|
| **overview** | ticker, company, sector, industry, security_type, country, exchange, index_membership, optionable, shortable, employees, website, earnings_date, ipo_date | **14** | Overview (v=111) |
| **valuation** | market_cap, enterprise_value, pe, forward_pe, peg, ps, pb, pc, pfcf, ev_ebitda, ev_sales, recommendation, target_price | **13** | Valuation (v=121); the last two have no tab (legacy *analyst*) |
| **financial** | eps, net_income, sales, eps_next_q, eps_this_y, eps_next_y, eps_past_3y, eps_past_5y, eps_next_5y, sales_past_3y, sales_past_5y, eps_qoq, sales_qoq, eps_yoy_ttm, sales_yoy_ttm, eps_surprise, revenue_surprise, gross_margin, operating_margin, net_margin, roa, roe, roic, current_ratio, quick_ratio, lt_debt_to_equity, debt_to_equity, book_per_share, cash_per_share, dividend, dividend_yield, dividend_ttm, payout_ratio, dividend_ex_date, dividend_growth_1y, dividend_growth_3y, dividend_growth_5y | **37** | Financial (v=161) |
| **technical** | beta, atr, volatility_1w, volatility_4w, sma20, sma50, sma200, high_50d, low_50d, high_52w, low_52w, rsi, all_time_high, all_time_low, candlestick_pattern, week_52_range, change_from_open, gap, prev_close, price, change, volume, average_volume, relative_volume, open, high, low | **27** | Technical (v=171) + the session block from Overview |
| **ownership** | shares_outstanding, shares_float, float_pct, insider_own, insider_trans, inst_own, inst_trans, short_float, short_ratio, short_interest | **10** | Ownership (v=131) |
| **performance** | perf_1w, perf_4w, perf_13w, perf_26w, perf_52w, perf_ytd, perf_3y, perf_5y, perf_10y | **9** | Performance (v=141) |
| **no home** | news_url, news_title, news_time, daily_digest, sort_order, row_type | **6** | News (v=321) for the four news keys; nothing for the two meta keys |

Totals: 14 + 13 + 37 + 27 + 10 + 9 + 6 = **116**. Zero silently unassigned.

**The six that genuinely have no home, and why:**

| Key | Why it cannot be a column | Where Finviz puts it |
|---|---|---|
| `news_url` | a URL is not a cell; truncation destroys it | News tab (v=321), as a link |
| `news_title` | 60–120 chars of prose; forces `white-space:normal` and a multi-line row, which destroys the 20 px rhythm (`.fv-table_row[data-multiline=true]` exists precisely for this and doubles row height) | News tab |
| `news_time` | meaningless without its title | News tab |
| `daily_digest` | a paragraph | News tab |
| `sort_order` | transport plumbing, not a fact about a company | not rendered |
| `row_type` | transport plumbing | not rendered |

The four news keys are not a gap in our six views — they are evidence that **prose needs a
different surface** (detail panel or a News tab), which is exactly the split Finviz makes.

**Keys that are assigned but should never be a visible column at 100 rows** (the adversarial cut):

| Key | Why it fails the scan test |
|---|---|
| `website` | a URL in a 60–70 px cell is unreadable; it belongs in the detail pane |
| `week_52_range` | an object `{low, high}` that duplicates `high_52w` + `low_52w`, which are already columns |
| `candlestick_pattern` | a numeric pattern code with no legend in the grid; unreadable without a key |
| `sort_order`, `row_type` | already homeless above |
| `optionable`, `shortable` | 3-char Y/N — *cheap*, so keep, but they are filters, not facts anyone scans |

That is **7 of 116 keys (6.0 %) that should not ship as columns**: 2 are plumbing, 2 are duplicate
or unreadable, 2 are cheap-but-filter-shaped, 1 is a URL. Add the two borderline cases —
`employees` (a headcount nobody ranks on) and `dividend_ex_date` (a date that only matters when
filtering) — and a defensible cut reaches **9 keys (7.8 %)**.

### 11.3 Which of our six views is redundant

**`overview` is provably redundant.** By construction it is the union of the five specialist views
plus one key: parsing `VIEWS`, the set difference `overview − ⋃(specialists)` is exactly
`['index']`. So the Overview tab contributes **one** column of unique information and 26 duplicates,
and `views.py`'s own docstring says so ("overview is the at-a-glance cross-section and touches every
specialist view at least once").

Finviz's Overview is not redundant — it has 11 columns, of which `sector`, `industry`, `country`
appear nowhere else in a grid view (the specialist tabs start at `marketCap`). Their Overview is
*identity + price*, not *everything*.

Recommendation: redefine `overview` as the **identity block** (ticker, company, sector, industry,
country, market_cap, price, change, volume, + one or two glanceables) — 9–11 columns, matching
Finviz's 11 — and let the specialists own their fields. That removes 26 duplicated columns and
makes the tab meaningful.

Second candidate: **`performance`** (9 columns, all `pct`, all identical formatting). It carries no
unique *type* — every column is a return window. It is a **sort/compare slice**, and at 100 rows it
is better served by a "rank by window" control than by a tab. Keep it, but demote it from tab to a
column group inside `technical` if the tab count must shrink.

---

## 12. Adversarial checks

### (a) Does their density work at 100 rows, or only because of the 20-row default?

Only because of the 20-row default. Measured:

* the page declares `"screenerPageSizes":{"table":20,"snapshot":10,"charts":12}` — 20 is a server
  constant, not a user preference (no rows-per-page control exists: `fv-table_pager-size` is an
  **empty div**, and the pager only offers page links);
* 5,620 results → **281 pages** at 20/page;
* one screen at 700 px of table viewport shows `700 / 20 = 35` rows — i.e. **the entire page is
  visible at once, without scrolling**, which is what makes a 20 px row legible: the eye never has
  to re-find its place;
* the total module is 424 px of rows + header, 492 px with toolbar and pager — it fits in a laptop
  viewport with the filter wall above it.

At 100 rows the same 20 px pitch produces **2,000 px of table** — 3 screens — with no visual anchor
other than the zebra stripe. Finviz never asks the reader to do that: they ask for a page click
instead. **The density is safe because of the truncation, and the truncation is the cost.**
Their model is "35 visible rows, 281 clicks"; ours must be "100 visible rows, zero clicks", and those
two cannot share a row pitch.

### (b) Is the green/red reused as heatmap shading, and does it fail contrast?

They are **two separate systems**, which is the interesting part:

* text: `#19803d`/`#d91e2b` (light), `#34c062`/`#fd8487` (dark) — **AA pass** at 5.00/5.03 and
  6.39/6.37, and 4.52/4.54 on the light zebra row (a 0.02 margin);
* heatmap: `#30cc5a`/`#f63538` on a `#414554` mid with `#2f323d` null — **white labels measure
  2.12:1 on green and 3.83:1 on red**. That is a **FAIL** for normal text, and even the 3.83 is
  only "large text" compliant, which 12 px is not.

So the same *semantic* (up is green) is expressed in two palettes, and only the text one is
compliant. At our font sizes (11–12 px mono, §13.1) the AA floor is 4.5:1 and the heatmap palette
would fail by 2.1×. If we shade cells, we must either (i) keep the shading very light (tint ≤ 8 %
over the surface, like their `background-color-positive:#e7f7e8` at 13.61:1 with dark text), or
(ii) keep the text colour fixed and put the magnitude in a bar/sparkline, not in the fill.

Their own escape hatch — the `.is-scale-red-blue` colourblind class — is worth copying as a *product
feature*: one class flips positive to `#306dca` (`5.04:1` on white) across table **and** map.

### (c) Is my mapping self-serving? Which key is useless, which view is redundant?

Answered in §11.2/§11.3 with counts: **7 keys (6.0 %) clearly, 9 (7.8 %) with the two borderline
ones, should not be columns**, **`overview` is
redundant by construction** (unique contribution = 1 key), and **`performance` is a slice, not a
view**. I also had to cut *against* my own table: `technical` gets 27 keys, which is a view nobody
can scan — it must be split into "session" (price/change/volume/gap) and "indicators"
(SMA/RSI/ATR/volatility) or it becomes the 66-column dump that `views.py` explicitly says it wants
to avoid.

### (d) Wayback / era mismatch

Not applicable: no Wayback. The captured HTML and CSS are from one live response, and the heatmap
chunk is referenced by the same build's `/map` page, whose `<head>` names the **same two CSS
hashes** (`main.33f321f9.css`, `redesign.28194964.css`). HTML, CSS and JS ramp are one era.

---

## 13. Recommendations, each tied to a measurement

### R1. Set the row pitch at 26–28 px for 100 rows, not Finviz's 20 px

**Measurement:** Finviz row = 20 px (`--fv-table-row-height-base:var(--text-2xs--line-height)` =
`1.25rem`) and is only used with a 20-row page (`screenerPageSizes.table = 20`). Our current row is
**≈51 px** (`ui/styles.css:290,302` — `td{padding:12px}` + 27 px `.ticker-mark`), so 100 rows =
**5,100 px** ≈ 7 screens; at 28 px it is 2,800 px ≈ 4 screens; at 20 px it is 2,000 px but every
row is a 20 px target with 4 px gutters.

**Why 28 and not 20:** at 12 px tabular numerals a 28 px line box gives 8 px of air above and below
the glyphs (line-height 2.33, versus Finviz's 1.67) and still fits **25 rows per 700 px viewport**.
It is the smallest pitch
where a row can carry a 2-line identity cell (ticker + company) without breaking rhythm — Finviz
cannot, which is why their Company column is a single truncated string
(`.fv-table_cell{white-space:nowrap;text-overflow:ellipsis;overflow:clip}`).

**Specifics:** `row-height: 28px`, `font-size: 12px`, `line-height: 28px`, `padding: 0 8px`,
`font-variant-numeric: tabular-nums`, no vertical padding. Ship a `dense` toggle at 24 px for
screens ≥ 1200 px. Do **not** go to 20 px: our rows carry a logo (18–27 px) which Finviz's 20 px
rows only fit because their logo is 18 px and `size-4.5`.

### R2. Replace the 20-row page with one 100-row virtualised panel: sticky header + pinned identity lane

**Measurement:** their sticky header is `position:sticky;top:0` on `.fv-table_top` (24 px) and the
pinned lane is `position:sticky;left:0` on `.fv-table_lane[data-lane=left]` at **101 px** — 9.5 % of
the 1066 px table, buying permanent access to `No.` + `Ticker`. Their pager costs **36 px**
(`--fv-table-pager-height`) plus 281 page loads.

**Do:** one scroll container with `max-height: calc(100dvh - 260px)`, `thead th{position:sticky;top:0}`,
`td.col-name{position:sticky;left:0;background:inherit}` — and the `background:inherit` +
`--fv-row-bg` pattern from §3.3 so zebra/hover/selection don't need three hard-coded colours.
Keep `<table>` (we have 100 static rows, not 5,620), keep `DISPLAY_LIMIT` but raise it to 100 as a
*panel size*, and delete the pager from the primary flow (keep it for "load 100 more").
**We already have `th{position:sticky;top:0}` and `td.col-name{position:sticky;left:0}`**
(`ui/styles.css:282, 614-621`) — this is a parameter change, not a rewrite.

### R3. Never scroll horizontally in the default views: budget columns per view so widths sum to the container

**Measurement:** all six Finviz views have Σbase = breakpoint − 101 and Σcoefficient = **1.000**, so
`table width ≡ container width`. The widest (18 columns) needs 1129 px inside a 969–1385 px
container. Their ceiling is **18 columns**; there is no 70-column grid anywhere in the product.

**Do:** for each of our six views, define 9–12 columns and a `flex` weight per column, and let the
table fill the panel exactly (CSS grid or `table-layout:fixed` + `<colgroup>` widths in %). Keep the
116-column surface behind an explicit **"All columns"** mode — which we already have
(`body.is-all-view .market-table{min-width:2200px}`, `ui/styles.css:632`) — but make it a deliberate
destination, not a default. Horizontal scroll with a hidden scrollbar (their
`scrollbar-width:none`) is a discoverability failure; if we scroll, show the bar or the fade.

### R4. Keep our colour tokens; if we add magnitude shading, never put white text on a mid-tone ramp

**Measurement:** their text pairs pass (5.00 / 5.03 light, 6.39 / 6.37 dark) but the light-theme
pair on the **zebra row** is 4.52 / 4.54:1 — 0.02 above AA. Their heatmap labels are
**2.12:1** on `#30cc5a`, mitigated by `fillStyle="rgba(0,0,0,0.5)"` behind white text. Our tokens:
`#82e6bd` = 12.58:1, `#ff738e` = 7.27:1 on `#170e19`.

**Do:** keep `--green`/`--red` as-is (2.5× their headroom). For magnitude, use a **background tint at
≤8 % of the semantic colour** (their `--background-color-positive:#e7f7e8` yields 13.61:1 with
`#22262f` text) or a 3 px bar — never a saturated fill with text on it. Ship the colourblind switch
as one class that recolours positive→blue everywhere (`#306dca` = 5.04:1), exactly as
`.is-scale-red-blue` does.

### R5. Do not rebuild their filter wall: 83 controls above the table cost 510 px

**Measurement:** 83 filters, 5 per row, `auto-rows-[minmax(1.875rem,auto)]` = 30 px → **17 rows =
510 px** on the All tab, versus a 424 px table (24 + 20×20). Descriptive alone (23 filters) is
150 px. There is **no Apply, no Reset**, 52 of 83 controls are preset-bucket dropdowns, and 81 of 83
contain Elite-only options.

**Do:** a **left rail** (240–280 px) with 6–8 presets visible and the rest behind a "More filters"
disclosure, or a filter **drawer** that overlays rather than pushes. Take the *interaction* idea —
preset buckets instead of min/max forms, so no Apply is needed and every change is one click — and
reject the layout. Our current chips row (`ui/index.html:106-114`, 6 chips) is closer to right than
their 83-control grid; what it lacks is the preset-bucket vocabulary (e.g. Market Cap as
"Under $2B / $2–10B / $10–200B / Over $200B" rather than two number inputs).

### Supporting mechanics worth copying (cheap, measured)

| Mechanic | Why it earns its complexity | Evidence |
|---|---|---|
| `data-label-short` (two spans, one hidden) | "Market Cap" → "Mkt Cap" when the cell is narrower than the label; prevents silent ellipsis on headers | `.fv-table_header-cell[data-label-short]>.fv-table_header-label{display:none}` + `…-label-short{display:revert}` |
| `background:inherit` on the pinned lane | zebra + hover + selection compose from one `--fv-row-bg`; no per-state hex sprawl | §3.3 |
| `box-shadow: inset -1px 0 0` for the lane edge | a separator that costs no layout width and cannot desync | §3.3 |
| tabular numerals on the container, not per-cell | every numeric column aligns for free | `.fv-table{font-variant-numeric:tabular-nums}` |
| `data-tone=positive|negative|caution|muted` | one attribute per cell instead of colour classes; maps 1:1 to our `pctClass()` | §4.1 |
| multi-sort chips (20 px, draggable) | sort is a first-class object, not a single header arrow | `.fv-table_sort-chip` |
| logo grayscale at rest, colour on row hover | rows read as text; identity appears only when you point at it | `[filter:grayscale(1)_contrast(0.3)_brightness(1.1)]` + `row-hover:[filter:none]` |

### Reject list

| Reject | Measurement |
|---|---|
| 12 px / 20 px density as a default for 100 rows | only viable at 20 rows/page; 100 rows = 2,000 px with no anchor |
| Hidden scrollbars (`scrollbar-width:none` + `::-webkit-scrollbar{display:none}`) | horizontal position becomes invisible; only a 16 px fade hints at it |
| 9 px logo-fallback letters (`text-[9px]`) and 10 px sort badges (`--text-3xs`) | below any readable floor at 100 rows |
| 510 px of filters above 424 px of data | §6 |
| 4.10 MB / 74 requests for 20 rows | §9; 72 % is JS, and 108,600 B of the image weight is four logo 404s |
| Row click = full navigation | `.fv-table_row-link` → `/quote`; we open a detail panel, keep it |
| Building a pager by hand | their own label says "273 pages" while linking "Page 281" for 5,620/20 = 281 |

---

## 14. What I could not verify

1. **Computed style.** Cloudflare served the interstitial to headless Chromium; three polls over
   15 s never cleared. Row height, padding and font size are quoted from the served declarations,
   which are authoritative here (no JS writes those values), but I did not read them back from a
   rendered box.
2. **Hover/popover behaviour.** `aria-haspopup="listbox"`, `aria-expanded="false"` are in the HTML;
   whether selecting an option submits immediately or stages an Apply is JS behaviour. The absence
   of any Apply/Reset control (`grep` → 0) plus query-string state is evidence, not proof.
3. **Heatmap pixels.** The map is canvas-painted. Colours are quoted from the ramp arrays in
   `5716.v1.9ae3480c.js` (same build hash as the captured pages), not sampled from a rendered
   canvas.
4. **The exact rendered colour of the Elite banner and nav** — outside the measured scope.
5. **Whether `dark` is the forced default or a server-side theme choice.** The document ships
   `class=" dark"`; both token sets are declared. I report both.

---

## 15. Reproducing every number

```bash
# 1. capture
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
curl -sSL -A "$UA" https://finviz.com/screener.ashx?v=111 -o research/finviz-ui-raw/screener_v111_live.html
curl -sSL -A "$UA" https://finviz.com/assets/dist/redesign.28194964.css -o research/finviz-ui-raw/redesign.28194964.css
curl -sSL -A "$UA" https://finviz.com/assets/dist/main.33f321f9.css   -o research/finviz-ui-raw/main.33f321f9.css

# 2. the grid is not a table, and the page size is declared
grep -c '<table' research/finviz-ui-raw/screener_v111_live.html                  # 0
grep -o '"screenerPageSizes":[^}]*}' research/finviz-ui-raw/screener_v111_live.html
                                                                                 # {"table":20,"snapshot":10,"charts":12}

# 3. row height chain
grep -o -- '--fv-table-row-height-base:[^;}]*' research/finviz-ui-raw/main.33f321f9.css
grep -o -- '--text-2xs--line-height:[^;}]*'    research/finviz-ui-raw/main.33f321f9.css   # 1.25rem

# 4. column-width arithmetic (produces views/column-widths.json)
for v in 111 121 131 141 161 171; do
  curl -sSL -A "$UA" "https://finviz.com/screener/stocks?v=$v" -o /tmp/v$v.html
done
python3 - <<'PY'
import re
for v in ['111','121','131','141','161','171']:
    h=open(f'/tmp/v{v}.html').read()
    ws=re.findall(r'data-column-id="([^"]+)"[^>]*style="left:[^;]*;width:calc\((\d+)px \+ max\(0px, 100cqw - (\d+)px\) \* ([\d.]+)\)"',h)
    seen={k:(int(b),float(c)) for k,b,_,c in ws}
    print(v, len(seen), sum(x[0] for x in seen.values()), round(sum(x[1] for x in seen.values()),6))
PY

# 5. filter census (produces views/v111-filters.json)
for ft in 1 2 3 4 5 6; do curl -sSL -A "$UA" "https://finviz.com/screener/stocks?v=111&ft=$ft" -o /tmp/ft$ft.html; done

# 6. weight
grep -o '<script' research/finviz-ui-raw/screener_v111_live.html | wc -l   # 56 <script> occurrences
grep -o '<script[^>]*src=' research/finviz-ui-raw/screener_v111_live.html | wc -l   # 38 with src; 48 unique with 31 preloads
# per-asset sizes: research/finviz-ui-raw/measurements.json
```

Files: `research/finviz-ui-raw/measurements.json` (totals), `views/index.json` (per-tab column
lists, row counts, table widths), `views/column-widths.json` (base + coefficient per column),
`views/catalog-map.json` (all 116 keys with view + Finviz tab + note), `views/v111-filters.json`
(the 83-filter schema), `views/v111-{header,row,pager,filters}.html` (verbatim markup excerpts).
