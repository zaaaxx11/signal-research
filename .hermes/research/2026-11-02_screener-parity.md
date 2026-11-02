# Screener parity — finviz.com/screener/stocks vs Ztockz

2026-11-02. Reference read through Wayback only (direct finviz.com is Cloudflare-walled and was **not** bypassed).
Our side: `web/src/{screener,grid,filters}/**` + the served filter document (`app/screen/descriptive.py:document()`, 85 controls).

Captures used (all `id_`, raw HTML):
| # | URL | Stamp | What it gave |
|---|---|---|---|
| 1 | `.../web/20261030010319id_/https://finviz.com/screener/stocks` | 2026-10-30 | current layout, 23 Descriptive controls (adds **Dilution Risk**), totals 5,616 |
| 2 | `.../web/20261017072249id_/…/screener/stocks?ft=1` | 2026-10-17 | full JSON state: 82 filters, option lists, `tabOrder`, 20 rows |
| 3 | `…?ft=2` `:57` · `?ft=3` `:47` · `?ft=4` `:16` · `?ft=5` `:33` · `?ft=6` `:15` | 2026-10-17 | per-tab control sets |
| 4 | `.../web/20260427080942id_/https://finviz.com/screener.ashx?v=111` | 2026-04-27 | legacy `<select>` counts: Order by **131**, Signal **34** |
| 5 | `https://web.archive.org/cdx/search/cdx?url=finviz.com/screener/stocks*` | — | the `?ft=` / `?v=` capture index |

Limits: the new UI's dropdown options are client-rendered, so per-control **option counts come from capture 2's embedded state blob** (`filters.filters[].options`), not from the DOM. The 30 Oct capture is used for labels/order. Order-by and Signal counts come from capture 4 (the new UI loads those lists in JS and I did not execute its bundle).

## 1. INVENTORY (reference, DOM order)

### 1.1 Top bar
`[logo]` · nav: `Home` `Stock Screener` `ETF Screener` `Maps` `Charts` `News` `Groups` `Insider` `Futures` `Forex` `Crypto` `Watchlist` `Portfolio` `Calendar` `Pricing` `Help` (16) · search `input[placeholder="Search ticker, company or profile"]` · `Login` · `Sign up free` · banner `Elite members get real-time data, technical charting, alerts, and no ads - starting at $24.96/mo.` + `Try Elite for Free`.

### 1.2 Screener bar
| control | exact label / value | notes |
|---|---|---|
| saved-screen combobox | `My Stock Presets` | `role=combobox`, `aria-expanded=false` |
| manage presets | `Edit presets` | icon-only button, `aria-label="Edit presets"` |
| order select | label `Order by`, value `None` | `data-testid=screener-split-sort-select` |
| direction select | value `Asc` | separate control `screener-split-sort-dir-select` (`Asc` / `Desc`) |
| signal select | label `Signal`, value `None (all stocks)` | `screener-split-signal-select` |
| tickers input | label `Tickers` | `#screener-split-tickers`, submit button `aria-label="Submit tickers"` |
| filter toggle | `Filters` | `aria-expanded=true`, opens the bar below |

### 1.3 Filter console
Tabs (`role=tab`, hrefs): `Descriptive` `?ft=1` · `Fundamental` `?ft=2` · `Technical` `?ft=3` · `News` `?ft=6` · `ETF` `?ft=5` · `All` `?ft=4`. Every control is a label + a custom `<button>` dropdown whose first option is `Any`; long vocabularies open a modal. Global `Reset` does **not** exist in either capture (grep `>Reset<` = 0; reset is per-control `Any`).

**Descriptive — 23 controls (30 Oct), option count in `()`:**
`Exchange (6)` `Index (6)` `Sector (13)` `Industry (4 + 144 grouped)` `Country (64)` `Market Cap. (16)` `Dividend Yield (16)` `Short Float (16)` `Analyst Recom. (11)` `Option/Short (10)` `Earnings Date (17)` `Average Volume (20)` `Relative Volume (18)` `Current Volume (2)` `Trades (17 — shown "Elite only", disabled)` `Price $ (41)` `Target Price (16)` `IPO Date (17)` `Shares Outstanding (18)` `Float (36)` `Theme (42)` `Sub-theme (2 + 268 grouped)` `Dilution Risk (added 17→30 Oct; list client-side, uncounted)`.

**Fundamental — 37:**
`P/E 25` · `Forward P/E 25` · `PEG 10` · `P/S 24` · `P/B 24` · `Price/Cash 28` · `Price/Free Cash Flow 34` · `EV/EBITDA 26` · `EV/Sales 26` · `Dividend Growth 32` · `EPS Growth This Year 18` · `…Next Year 18` · `…Qtr Over Qtr 18` · `…TTM 18` · `…Past 3 Years 18` · `…Past 5 Years 18` · `…Next 5 Years 18` · `Sales Growth Qtr Over Qtr 18` · `…TTM 18` · `…Past 3 Years 18` · `…Past 5 Years 18` · `Earnings & Revenue Surprise 5` · `Return on Assets 26` · `Return on Equity 26` · `Return on Invested Capital 26` · `Current Ratio 14` · `Quick Ratio 14` · `LT Debt/Equity 24` · `Debt/Equity 24` · `Gross Margin 41` · `Operating Margin 42` · `Net Profit Margin 42` · `Payout Ratio 27` · `Insider Ownership 14` · `Insider Transactions 34` · `Institutional Ownership 22` · `Institutional Transactions 26`.

**Technical — 20:** `Performance 131` · `Performance 2 131` · `Volatility 24` · `RSI (14) 14` · `Gap 30` · `20-Day Simple Moving Average 27` · `50-Day … 27` · `200-Day … 36` · `Change 28` · `Change from Open 28` · `20-Day High/Low 24` · `50-Day High/Low 24` · `52-Week High/Low 38` · `All-Time High/Low 38` · `Pattern 30` · `Candlestick 13` · `Beta 21` · `Average True Range 26` · `After-Hours Close 41` · `After-Hours Change 28`.
**News — 2:** `Latest News 14` · `News Keywords 1` (free-text, Elite). **ETF — 1:** `Held By 1` (ticker input, Elite). Total **82** (17 Oct) → **83** (30 Oct, +Dilution Risk).

### 1.4 Column tabs (15, `/screener/stocks?v=`)
`Overview` 111 · `Valuation` 121 · `Financial` 161 · `Ownership` 131 · `Performance` 141 · `Technical` 171 · `Custom` 151 · `Charts` 211 · `Tickers` 411 · `Basic` 311 · `TA` 351 · `News` 321 · `Snapshot` 341 · `Maps` 711 · `Stats` 611. Each tab's `data-testid=screener-split-view-<name>`.

### 1.5 Action bar (above the table)
`Off` (`aria-label="Auto refresh: Off"`) · `Add to` (`screener-add-to-button`, menu) · `Compare` (`screener-open-in-compare`) · `Set Alert` (`screener-create-alert-link`) · `Export` (`action-bar-export-button`, menu) · readout `#1 / 5616 Total`. No `Charts` button on this page: charts are the `v=211` tab, and the row itself links to `/quote`.

### 1.6 Table
11 `role=columnheader` cells, sticky, two-line label (full + short): `No.` · `Ticker` · `Company` · `Sector` · `Industry` · `Country` · `Market Cap` / `Mkt Cap` · `P/E` · `Price` · `Change %` / `Chg %` · `Volume` / `Vol`. Sortables carry `data-sortable="true"` + `aria-sort="none|ascending|descending"`; the row-number cell has no `aria-sort`. Per row: one full-width link `a.fv-table_row-link` → `/quote?t=SYM&b=1`, a logo tile (`logo.finviz.com/SYM.svg`), and a ticker link to the same quote page. **No per-row buttons** (no inline chart/add/star).

### 1.7 Footer
`1 / 281` page select (`aria-label="Page"`) · `Previous page` / `Next page` · numbered pages `1…7` · `⋯` (`aria-label="Skip to page 144 of 273 pages"`) · `281` · `Go to` + `input[aria-label="Go to page"]` · links `Export All Screener Data (CSV)` (Elite), `Affiliate Advertise APIs Careers Contact Blog Help Privacy …` · `Stock quotes delayed by 1 minute. Futures and options delayed by 15 minutes.`

### 1.8 Presets / saved screens
`My Stock Presets` + `Edit presets`; anonymous payload ships `presets: []`, `hasWatchlist: false`, `canSaveScreen: true` (legacy).

## 2. WHAT WE ALREADY HAVE
Status: **have** = same job done; **partial** = exists but narrower/different; **missing** = absent.

| reference control | status | ours (file:line, testid) |
|---|---|---|
| global search `Search ticker, company or profile` | have | `shell/HeaderBar.tsx:158` (same placeholder) |
| nav 16 items | partial | `shell/nav.ts:19-32` — 14; no `Pricing`, `Help` |
| `My Stock Presets` | partial | `screener/PresetPanel.tsx:86-221` `preset-panel` — saves **columns only**, localStorage (`screener/userPresets.ts`), no filters/sort, no server |
| `Edit presets` | have | same panel: rename/delete per row (`:208-224`) |
| `Order by` | have | `screener/ScreenerHeader.tsx:401-414` `order-select` — 89 options vs 131 |
| `Asc`/`Desc` select | partial | `ScreenerHeader.tsx:415-423` `order-direction` — a non-interactive arrow; direction only flips by re-clicking a header (`data/screens.ts:77-81`) |
| `Signal` | partial | `ScreenerHeader.tsx:431-447` `signal-select` — 27 options, **17 disabled** (12 pattern, 5 event/ratings) |
| `Tickers` + `Submit tickers` | missing | `SymbolSearch` (`:454-461`) looks one symbol up and docks the detail panel; it does not filter rows |
| `Filters` toggle | have | `ScreenerHeader.tsx:465-496` `filter-button` (+ active count badge `:479-486`) |
| filter tabs (6) | have | scope buttons `All` + 5 groups (`:518-538`) + group headings (`:672-679`) |
| per-control filter UI | have | `ControlField` `:698-833`; `data-testid=control-<id>` — 85 controls vs 82/83, same 5 tabs (`app/screen/descriptive.py`) |
| `Sector` select | **partial (dead)** | `ScreenerHeader.tsx:820-832` renders **disabled** "No values published" (`Sec` publishes `options: []`, `options_source: /market/sectors`); test *asserts* disabled at `screener/ScreenerHeader.test.tsx:276-279`; `grid/SectorSelect.tsx` exists, unused |
| `Industry` picker (144) | partial | same file `:765-818` — free-text `contains` input, no enumeration |
| `Country` picker (64) | partial | same, free-text `contains` |
| `Exchange` / `Index` | have | `:730-763` — 5 / 4 options |
| `Any` per control | have | first option `Any` (`:749`) |
| page-level reset | n/a (ref has none) | ours adds `Clear filters` (`:550-558`) and `Clear all` (`filters/FilterDrawer.tsx:313-320`) |
| 15 column tabs | partial | `screener/columnPresets.ts:66-152` — 17 tabs; missing `Custom`, `Maps`; `Charts`/`Tickers` are column sets, not distinct renderings |
| All-columns | extra | `ScreenerHeader.tsx:627-646` `all-columns-toggle` — 116 keys, chunked |
| `Edit columns` | extra | `grid/ColumnPicker.tsx:128-135` — 116 keys, search, 40/panel cap |
| `Off` auto refresh | partial | `grid/TableToolbar.tsx:195-215` `auto-refresh` — only `Off` / `Every 5 min` |
| `Add to` | have | `TableToolbar.tsx:230-270` `add-to-button` → Watchlist/Portfolio (`grid/AddToDialog.tsx`) |
| `Compare` | partial | `TableToolbar.tsx:272-281`; `grid/CompareDialog.tsx:29` `COMPARE_LIMIT = 5` vs Finviz `settings.compareTickersLimit: 25` |
| `Set Alert` | partial | `TableToolbar.tsx:283-292`; `grid/alerts.ts:1-10` — local notes, **no delivery** |
| `Export` | have | `TableToolbar.tsx:294-331` `export-button` — CSV/XLSX/JSON/API (`grid/ExportDialog.tsx`) |
| `#1 / 5616 Total` | have | `TableToolbar.tsx:164-171` `range` → `1/5,576 Total` (`grid/pager.ts:60-63`) |
| sortable headers + `aria-sort` | have | `grid/DataTable.tsx:549-607` — `aria-sort` `:564`, per-column button `:585` |
| header full+short label | partial | meta carries `short` (`grid/columnMeta.ts:97+`) but the header prints only `label` (`DataTable.tsx:557,596`) |
| row → quote page | partial | row click docks the detail panel (`DataTable.tsx:784-788`); no `/quote` hand-off |
| pager numbers + gap + last + `Go to` | have | `grid/Pager.tsx:85-112`, `:128-148` (`go-to-page`) |
| rows-per-page | extra | `TableToolbar.tsx:335-351` `rows-per-page` 20/50/100 (Finviz fixed 20) |
| `Export All Screener Data (CSV)` | missing | footer link only in reference |
| filter drawer (buckets + arbitrary rules) | have, hidden | `filters/FilterDrawer.tsx` reachable **only** via ⌘K (`ScreenerScreen.tsx:398-409`); buckets `:171-216`, builder `:276-282` |

Filter vocabulary itself is at parity: all 82 reference keys exist in our document (`ours only: sh_dilution`; `Finviz only: none`); option counts run 1–2 lower because Finviz appends `Custom` / `Custom (Elite only)`.

**Scoreboard (reference → ours):** filter controls 83 → 85 · filter tabs 6 → 6 · controls disabled for lack of data 0 → 7 (Finviz instead plan-gates 5: `Trades`, `After-Hours Close`, `After-Hours Change`, `News Keywords`, `Held By`) · column tabs 15 → 17 (+2 missing names, 2 re-implemented as column sets) · order-by options 131 → 89 · signal options 34 → 27 (17 disabled) · page size fixed 20 → 20/50/100 · saved screens yes → columns-only.

## 3. GAPS RANKED

### Cheap wins (≤1 task)
| # | gap | why | file | size |
|---|---|---|---|---|
| 1 | `Sector` renders **disabled** while the scanner carries a sector on every row and `/market/sectors` publishes them | a visible control that cannot move is read as broken; Sector is one of the first six controls anyone reaches for | `screener/ScreenerHeader.tsx:820-832` + `grid/SectorSelect.tsx` + `grid/useSectors.ts`; flip the test at `ScreenerHeader.test.tsx:276` | S |
| 2 | `Industry` and `Country` are free-text `contains` | Finviz offers 144 industries / 62 countries (64 options incl. `Any` + `Custom`); typing "diagnostics" silently misses "Diagnostics & Research" | `ScreenerHeader.tsx:765-818` (publish options, or accept `options_source`) | M |
| 3 | No `Tickers` box | cannot ask "these 12 names, ranked"; our API already filters `symbol` (`app/screen/filters.py` FIELDS `symbol: text`, `in` allowed) | `ScreenerHeader.tsx` + one published `symbol` control | M |
| 4 | advanced filter UI is ⌘K-only | the bucket rows and arbitrary-value builder are the answer to Finviz's `Custom` option, and no reader will find them | `ScreenerHeader.tsx:517` (add "More filters" opening `FilterDrawer`) | S |
| 5 | direction is not a control | cannot ask "lowest P/E first" from the masthead; must click a header twice | `ScreenerHeader.tsx:415-423` → real `<select>`/button writing `-key` | S |
| 6 | no `Maps` tab | `/map` page already exists (`web/src/map/MapPage.tsx`, route `app.tsx:224`) — the tab is a link, not new work | `screener/columnPresets.ts:66` | S |
| 7 | headers lack the short label | wide columns ellipsise where Finviz prints `Mkt Cap` under `Market Cap` | `grid/DataTable.tsx:596` (+ `columnMeta` already has `short`) | S |
| 8 | compare caps at 5 | Finviz's `compareTickersLimit` is 25; our dialog scrolls fine | `grid/CompareDialog.tsx:29` | S |
| 9 | auto-refresh has two states | `Off` / `Every 5 min` only | `grid/TableToolbar.tsx:35-38` | S |
| 10 | footer `Export All Screener Data (CSV)` | one link to the existing export job | `grid/ScreenPanel.tsx` footer | S |

### Projects
| # | gap | why | file | size |
|---|---|---|---|---|
| 1 | **Saved screens** (filters + columns + sort, named, server-side) | the single biggest workflow gap: a screen cannot be reopened by name or shared; Finviz's `My Stock Presets` is the front door of its product | new table + `app/api/screens.py` route, `web/src/screener/{PresetPanel,userPresets}.ts` | L |
| 2 | **Alerts with delivery** | ours writes a note in localStorage and says so (`alerts.ts:1-10`); a screener alert that cannot fire is not an alert | `app/api/` route + scheduler, `grid/AlertDialog.tsx` | L |
| 3 | **Signals: 17 of 27 disabled** | 12 pattern signals, plus upgrades/downgrades, earnings before/after and major news — each is a screen Finviz users run daily | `app/api/signals.py:416-530`; needs data (§4) | M–L each |
| 4 | `Charts` as a rendered view | our `Charts` tab only selects chart-source columns; Finviz v=211 draws sparkline tables | `grid/DataTable.tsx` + `web/src/charts/**` | M |
| 5 | `Custom` tab semantics | Finviz v=151 is a user-defined column set that survives navigation; ours resets to `Edit columns` per view | `grid/columnSelection.ts` | M |

## 4. CANNOT COPY WITHOUT A DATA SOURCE
| feature | missing source |
|---|---|
| `Theme (42)` / `Sub-theme (268)` | a theme taxonomy; no layer carries one (`app/screen/descriptive.py` reason on `theme`/`subtheme`) |
| `Dilution Risk` | share-count **history**; scan has today's shares only — SEC `dei:EntityCommonStockSharesOutstanding` across filings would answer it |
| `Trades (17)` | a trade-count feed; scan has volume shares + turnover currency (`sh_trades` reason) — Polygon aggregates / IEX HIST / Databento |
| `20-Day High/Low` | 20-day bars; scan has 52-week extremes, depth store 50-day (`ta_highlow20d` reason) |
| `Pattern (30)`, 6 pattern signals | daily price series / a pattern detector; depth has only a 3-class candlestick flag (`ta_pattern` reason) |
| `Insider Transactions` / `Institutional Transactions` | a readable flow; the depth cell is a net-change the product calls junk (TSLA 76.0 vs META 45.9) |
| `Earnings Before` / `Earnings After` signals | a pre/post-session flag per earnings date (the date itself is on 4,626/5,576 rows) |
| `Recent Insider Buying/Selling` signals | filings (Form 4), not a vendor field |
| `Major News` signal | news classification |
| `Upgrades` / `Downgrades` signals | an analyst-ratings source |

Not a data gap, listed so it is not mistaken for one: Finviz's `Custom (Elite only)` entry inside each control is an entitlement, not a feed — our condition builder already expresses arbitrary values.
