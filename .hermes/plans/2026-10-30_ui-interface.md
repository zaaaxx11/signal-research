# Ztockz Interface — Implementation Plan

> **For Hermes:** execute with `subagent-driven-development`; each phase is a
> separate builder with its own gate. Do not start Phase 1 until Phase 0's gate
> is green — the grid cannot be built against an API that caps at 20 rows.

**Goal:** replace the vanilla 2,107-line interface with a React + shadcn/ui
screener that renders 100 rows × 9–12 columns per view, never scrolls
horizontally, keeps the dark default, and surfaces the 116-column catalogue the
engine already fills.

**Architecture:** Vite + React 19 + TypeScript SPA, built to static assets and
served by FastAPI's existing `StaticFiles` mount. TanStack Table v9 headless for
the grid; shadcn/ui for chrome; per-view column budget computed from a weight
vector so the table width equals the container width (the Finviz trick, rebuilt
because no library ships it). Filters live in a drawer with preset buckets and an
advanced AND/OR condition builder; the symbol detail is a docked resizable panel.

**Tech stack:** Vite 7, React 19, TypeScript 5.x, Tailwind v4, shadcn/ui (MIT,
copy-in), `@tanstack/react-table@9.3.1`, `nuqs` (URL state), Playwright (E2E).

---

## 0. Locked decisions (evidence in §1)

| # | Decision | Reason |
|---|---|---|
| D1 | Rewrite as a SPA; do not extend `ui/app.js` | 2,107 lines, no build step, no types, client view table already drifted from the server (overview 8 vs 27 columns) |
| D2 | No Next.js | No SSR need; the API is same-origin JSON; a static build keeps the deploy a systemd unit, not a Node service |
| D3 | TanStack Table v9 **headless**, pinned to `9.3.1` | It gives pinning/visibility/multi-sort/manual pagination and no styling, so container-fit widths stay ours |
| D4 | **No virtualization at 100 rows** | 100 × 12 = 1,200 cells; windowing breaks container-fit sizing and print/export, and every virtualised library optimises for 10k+ rows |
| D5 | Row pitch 28 px, type 12 px, tabular numerals | 100 rows = 2,800 px (≈4 screens) and still fits 25 rows per 700 px viewport; Finviz's 20 px only works because its page is 20 rows |
| D6 | Never horizontal scroll in a view | Widths must sum to the container; the 116-column surface becomes an explicit "All columns" destination, not a default |
| D7 | Detail panel is **docked**, not an overlay | Docking shrinks the table so the solver re-runs; an overlay hides the comparison context that makes a screener useful |
| D8 | Dark is the default; light is a token set, not a second layout | Every colour pair is gated at ≥4.5:1 against the real row background |
| D9 | Build at `/app`, keep `/` on the old UI until parity | Rollback is a route change, not a revert — **operator's choice, 2026-10-30** |
| D10 | Keep the TradingView logos | Operator's choice; the logo column is part of the identity lane and the fetch already exists |
| D11 | Light theme deferred until after cutover | Dark-only ships first; the token file is built so a light set is a second block, not a second layout |
| D12 | Views are **redesigned from the catalogue's own categories**, not inherited from Finviz tabs | Operator's choice: "desain ulang". §6.1 |

## 1. Facts this plan is built on (measured, not assumed)

**The catalogue is rich; the view layer is not.**

```
catalogue keys                 116    (106 filled across 4,217 symbols)
depth store                    11,473,979 B, as_of 2026-10-30T16:13:15Z
names referenced by views.py    71
  …that are catalogue keys      48
  …that do not exist            23   (each has a known replacement, §6.2)
catalogue keys in no view       68
```

**The API cannot serve a 100-row panel today.**

```
DISPLAY_LIMIT                  20    enforced in the API (selection.py:348), not just the UI
columns cap                    40    mutually exclusive with `view`; ignored in symbols mode
12 cols × 20 rows          13,140 B   live 200, `columns: [roic, pe_ttm]`
12 cols × 100 rows         83,096 B   14.03 s — five page calls, 2 duplicated symbols
/engine/screen 100 rows   162,517 B   1.76 s — but no column projection (ships provenance)
ETag on /screens/select         no    confirmed on a live 200: no ETag, no Cache-Control
```

**Two vocabularies live in one request.**

```
filters   speak the Finviz key set    fa_pe, fa_roa, sh_price, sh_avgvol …   (82 keys)
columns   speak the catalogue keys    pe_ttm, roic, market_cap …            (116 keys)
```

A caller must therefore know both. The live proof: `{"filters":[{"key":"fa_pe","op":"lt","value":20}],
"columns":["roic","pe_ttm"]}` returns 20 rows from 1,502 — the filter key is Finviz's, the column
keys are ours, and nothing translates between them.

**The current interface.**

```
files            ui/index.html 312 · ui/app.js 1,117 · ui/styles.css 678
weight           104,107 B raw · 24,486 B gzip · no build step
columns shown    71 in "all", 14–31 per named view
sends `columns`  no (sends `view`)
uses ETag        no
breadth          computed in-browser over ≤20 visible rows
hard-coded       5,579 / 40,950 / 5,779 scope counts, "One scan, 20 names", perPage 20
```

**Finviz, measured** (`research/finviz-ui.md`): 20 px rows, 12 px type, 24 px
sticky header, 101 px pinned lane, **max 18 columns per view** with widths that
sum to the container, 83 filters in a 510 px grid above a 424 px table, and
heatmap labels at **2.12:1** (WCAG fail, patched with a 50 % black shadow). Our
tokens are `#82e6bd` 12.58:1 and `#ff738e` 7.27:1.

**The shadcn ecosystem** (`research/shadcn-inventory.md`): 558 curated entries,
~12 relevant, **zero open-source screeners**. No candidate ships container-fit
column widths or a 74-field filter drawer — both are ours to build.

## 2. Phase 0 — backend prerequisites (blocking)

Nothing in Phases 1–7 can be honest until these land. Each is additive; no
existing behaviour changes for a client that does not ask for it.

### Task 0.1 — Raise the row cap to 100 with a stable order

- **Files:** `app/screen/selection.py` (`_limit`, `DISPLAY_LIMIT`),
  `app/screen/screener.py` (rank key), `tests/test_screen_selection.py`
- **Behaviour:** `limit` accepts up to `PANEL_LIMIT = 100`; ties break on
  `(rank_score, symbol)` so page boundaries are deterministic (today two symbols
  repeat across pages — measured).
- **Test:** 100 contiguous rows contain 100 distinct symbols; `limit: 101` clamps.
- **Gate:** `pytest tests/test_screen_selection.py -q` green; 3 repeated calls
  return byte-identical symbol sequences.

### Task 0.2 — Column projection at panel size

- **Files:** `app/screen/selection.py`, `app/api/screens.py`,
  `schemas/stock_selection_request_v1.json`, `tests/test_screen_select_columns.py`
- **Behaviour:** the 100-row path must emit only the requested columns
  (`columns` list) instead of the 162 KB full document. Target ≤60 KB for
  12 columns × 100 rows.
- **Test:** response for `columns: [pe_ttm, roic, target_price]` contains exactly
  those keys per row; unknown name → 422 naming it.
- **Gate:** measured payload printed by the test and asserted `< 60_000` bytes.

### Task 0.3 — Cache the screen response

- **Files:** `app/api/screens.py`
- **Behaviour:** `POST /screens/select` returns an `ETag` derived from
  `(snapshot_id, as_of, request body hash)` and answers `304` on `If-None-Match`.
  This is the difference between a usable panel and a 14 s refresh.
- **Test:** same body twice → identical tag, `304` on the second; a changed
  filter changes the tag; a clock tick alone does not.
- **Gate:** live `curl -H 'If-None-Match: …'` returns `304` with 0 bytes.

### Task 0.4 — Market breadth and sector performance

- **Files:** `app/api/screens.py` (new `GET /market/breadth`), `tests/test_api_breadth.py`
- **Behaviour:** one pass over the in-memory universe (already loaded at
  `screens.py:342`) returns `{advancing, declining, unchanged, total,
  new_highs, new_lows, sectors: [{name, avg_change, count}]}`.
- **Test:** counts sum to `total`; a synthetic universe with 3 up / 2 down
  returns exactly that; sectors partition the rows.
- **Gate:** live call under 50 ms.

### Task 0.5 — Symbol search

- **Files:** `app/api/screens.py` or `app/api/engine.py`, `tests/test_api_symbol_search.py`
- **Behaviour:** `GET /symbols/search?q=app&limit=10` returns ranked
  `{symbol, name, exchange}` — prefix matches first, then contains.
- **Test:** `q=aapl` → AAPL first; empty `q` → 422; `limit` clamps at 20.
- **Gate:** live call under 80 ms over 10,433 securities.

### Task 0.6 — Rewrite the view vocabulary

- **Files:** `app/screen/views.py`, `tests/test_screen_views.py`
- **Behaviour:** every name in `VIEWS` must be a catalogue key. The 23 phantoms
  map as in §6.2; `overview` is replaced by `today` (session + technical), keeping
  `index_membership` which was its only unique contribution.
- **Test:** for every view, every name ∈ catalogue keys; the union of views
  covers ≥110 of 116 keys (the 6 homeless stay out by design).
- **Gate:** `VIEWS` has zero unknown names; the old `ui/app.js` table keeps
  working because it speaks the same names (it must be updated in the same commit).

### Task 0.7 — One vocabulary for filters

- **Files:** `app/screen/selection.py` (key resolution), `app/screen/filters.py`,
  `tests/test_screen_filter_aliases.py`
- **Behaviour:** accept catalogue keys (`pe`, `roic`, `market_cap`, …) as filter
  keys by resolving them to the Finviz key they already mean; keep the legacy
  `fa_*`/`sh_*` names working so nothing breaks. An ambiguous key (`pe` vs
  `forward_pe`) must resolve by the same alias table the depth overlay uses.
- **Test:** the same filter expressed both ways returns the identical symbol set;
  an unknown key still 422s with the existing message.
- **Gate:** live parity — `{"key":"pe","op":"lt","value":20}` and
  `{"key":"fa_pe","op":"lt","value":20}` return the same `total`.

### Task 0.8 — Contract bugs found in the audit

| Bug | Fix |
|---|---|
| `columns` silently ignored in symbols mode | honour it, or reject with 422 |
| `logo` present in rows but absent from the schema | add to schema |
| symbols mode truncates at 20 with no knob | accept `limit` |
| browse `total` 3,896 vs select `universe_total` 5,600 both called "Universe" in the UI | rename the browse field to `matched` |
| 4 keys null despite having data (`ps`, `short_float`, `enterprise_value`, `cash_per_share`) | fix the presence-based overlay and the `apply_view_fill` clobber |

- **Gate:** one test per row; `research/ui-surface-audit.md` §contract-bugs
  referenced in each test docstring.

## 3. Phase 1 — app shell and tokens

- **Files:** `web/` (new): `package.json`, `vite.config.ts`, `index.html`,
  `src/main.tsx`, `src/app.tsx`, `tailwind.config.ts`, `src/styles/tokens.css`
- **Serve:** `vite build --outDir web/dist`; FastAPI mounts `/app` → `web/dist`.
  Dev uses `vite dev` with a proxy to `127.0.0.1:8099`.
- **Tokens:** base ramp from tweakcn (Apache-2.0, keep NOTICE), then hand-set
  four finance tokens (`--up`, `--down`, `--flat`, `--alert`) and verify each
  at ≥4.5:1 on `--row-bg`.
- **Test:** `src/styles/tokens.test.ts` computes WCAG contrast for the four
  pairs and fails below 4.5.
- **Gate:** `npm run build` succeeds; `/app` serves the shell; contrast test green;
  bundle ≤180 KB gzip (measured, printed).

## 4. Phase 2 — the grid

- **Files:** `web/src/grid/{DataTable,ColumnSolver,cellRenderers}.tsx`
- **Solver:** per view, a weight vector of 9–12 entries; integer allocation with a
  `min-width` floor; remainder to the widest text column; `table-layout: fixed`
  with a generated `<colgroup>` so header and body cannot disagree.
- **Sticky:** `thead th { position: sticky; top: 0 }`; identity lane
  `position: sticky; left: 0` with `background: inherit` and
  `box-shadow: inset -1px 0 0` (both from the Finviz measurement — they compose
  with zebra/hover without a second colour).
- **Cell renderers:** number (tabular, compact `$B/$M`), delta (signed + arrow,
  never colour alone), bar (in-cell magnitude), heatmap (text colour computed
  from fill, not fixed white), badge (sector), sparkline (30d).
- **Test:** a jest/vitest test asserts the solver's widths sum exactly to the
  container for 9 columns at 1280/1440/1920 px.
- **Gate:** 100 rows render from a real API response; no horizontal scrollbar at
  1280 px in any of the six views (Playwright assertion).

## 5. Phase 3 — filters

- **Files:** `web/src/filters/{FilterDrawer,PresetBuckets,ConditionBuilder,ChipBar}.tsx`,
  `web/src/filters/registry.ts`
- **Registry:** one entry per filter: key, label, unit, operator set, presets
  (e.g. Market Cap → `Under $2B / $2–10B / $10–200B / Over $200B`), min/max step.
  Built from `research/finviz-ui-raw/views/v111-filters.json` (83 controls) and
  our 82-key filter vocabulary.
- **Interaction:** drawer + `⌘K` command search across field names; advanced mode
  is an AND/OR tree on the Dice UI model; active filters collapse to chips above
  the table so the drawer can close.
- **Gate:** every preset maps to a request the API accepts (a test replays each
  preset against the live API and asserts 200); zero `Apply` buttons.

## 6. Phase 4 — views and the column model

### 6.1 The nine views (designed from the catalogue, 100 % mapped)

Derived from the catalogue's own `category` field (`app/engine/data/finviz_columns_116.json`),
grouped by the job a person is doing — not by Finviz's tab order. Verified by script: all 116 keys
appear exactly once, zero unassigned, zero unknown.

| View | Cols | Job it answers | Columns |
|---|---|---|---|
| **today** | 11 | What is moving right now? | price, change, change_from_open, gap, prev_close, open, high, low, volume, average_volume, relative_volume |
| **value** | 13 | Is it cheap? | market_cap, enterprise_value, pe, forward_pe, peg, ps, pb, pc, pfcf, ev_ebitda, ev_sales, target_price, recommendation |
| **quality** | 12 | Is the business good? | roa, roe, roic, current_ratio, quick_ratio, lt_debt_to_equity, debt_to_equity, book_per_share, cash_per_share, gross_margin, operating_margin, net_margin |
| **growth** | 12 | Is it growing? | eps, net_income, sales, eps_next_q, eps_this_y, eps_next_y, eps_past_3y, eps_past_5y, eps_qoq, sales_qoq, eps_yoy_ttm, sales_yoy_ttm |
| **momentum** | 10 | How has it performed? | perf_1w, perf_4w, perf_13w, perf_26w, perf_52w, perf_ytd, perf_3y, perf_5y, perf_10y, rsi |
| **trend** | 12 | Where is it relative to its own history? | sma20, sma50, sma200, high_50d, low_50d, high_52w, low_52w, atr, beta, volatility_1w, volatility_4w, all_time_high |
| **ownership** | 10 | Who owns it, and who is short? | shares_outstanding, shares_float, float_pct, insider_own, insider_trans, inst_own, inst_trans, short_float, short_ratio, short_interest |
| **income** | 8 | What does it pay? | dividend, dividend_yield, dividend_ttm, payout_ratio, dividend_ex_date, dividend_growth_1y, dividend_growth_3y, dividend_growth_5y |
| **profile** | 12 | What is it? | company, sector, industry, security_type, country, exchange, index_membership, optionable, shortable, employees, earnings_date, ipo_date |
| **all** | 116 | Explicit destination only | every key, `min-width: 2200px`, scrollbar visible |

**Sixteen keys are detail-panel only, by design** — they are prose, URLs, meta, or duplicates:

```
growth    eps_next_5y, sales_past_3y, sales_past_5y, eps_surprise, revenue_surprise
trend     all_time_low, candlestick_pattern, week_52_range
news      news_url, news_title, news_time, daily_digest
profile   website
identity  ticker            (the pinned lane, not a column you pick)
meta      sort_order, row_type   (transport plumbing, never rendered)
```

**Why these nine and not Finviz's six:** Finviz's tabs bundle 37 financial columns into one
tab that no one scans at 100 rows. Splitting `financial` into `quality` (ratios), `growth`
(income statement deltas) and `income` (dividends) means every view is ≤13 columns and answers one
question. `today` replaces `overview`, whose only unique key was `index_membership` — now in
`profile` with the rest of identity.

### 6.2 Phantom names → catalogue keys (from the audit)

```
atr_14→atr · avg_volume→average_volume · change_pct→change · eps_ttm→eps
eps_next_y_growth→eps_next_y · eps_past_3_5y→eps_past_5y · income→net_income
index→index_membership · ipo→ipo_date · oper_margin→operating_margin
option_short→optionable+shortable · perf_week→perf_1w · perf_month→perf_4w
perf_quarter→perf_13w · perf_half_y→perf_26w · perf_year→perf_52w
profit_margin→net_margin · rel_volume→relative_volume · rsi_14→rsi
sales_past_3_5y→sales_past_5y · volatility→volatility_1w+volatility_4w
week52_high→high_52w · week52_low→low_52w   (both LOSSY: price → % distance must be explicit)
```

- **Gate:** a script asserts every one of the 116 keys appears exactly once
  across §6.1's nine views plus the detail-only list — zero unassigned, zero unknown,
  zero duplicates. The same script runs in CI.

## 7. Phase 5 — detail panel

- **Files:** `web/src/detail/{DetailPanel,FundamentalsTab,OwnershipTab,InsidersTab,NewsTab}.tsx`
- **Docked** right, resizable (shadcn `Resizable`), min 360 / max 560 px; the grid
  keeps its sticky lane and re-runs the solver on resize.
- **Data:** `GET /engine/fundamentals/{symbol}`, `GET /engine/insider/{symbol}`,
  `GET /news/{symbol}?limit=20` — all exist today; no backend work.
- **Gate:** opening a panel does not change the table's sort or filter; closing it
  restores the exact scroll position (Playwright).

## 8. Phase 6 — breadth and sectors

- **Files:** `web/src/breadth/{BreadthBar,SectorHeat}.tsx`
- **Data:** `GET /market/breadth` (Task 0.4). The bar shows advancing/declining
  counts and a 1-day sparkline; the sector block is a compact grid, not a canvas
  heatmap (canvas defeats text selection and screen readers).
- **Gate:** the bar's totals equal the endpoint's; the old client-side breadth
  computation is deleted, not left dormant.

## 9. Phase 7 — parity, QA, cutover

- **Parity checklist:** every column the old UI showed is reachable in a new view;
  every filter the old UI offered exists in the registry; the logos still load.
- **Accessibility:** keyboard path to every control; focus visible (the Finviz
  pattern of `outline: none` + 1 px border is not acceptable); `aria-sort` on
  headers; contrast test green in both themes.
- **Performance budget:** first paint ≤1.5 s on a cold cache; filter change ≤2 s
  p50 with ETag hits returning in ≤150 ms.
- **Cutover:** flip `/` to the new app; keep `/legacy` for one release.
- **Gate:** Playwright suite green; the 4,136-test Python suite still green.

## 10. Risks

| Risk | Mitigation |
|---|---|
| Vendored registry code is executable and unpinned | record the source commit SHA in a header comment of every copied file; review before install |
| Malicious lookalike templates — a fork of `shadcn-fintech` shipped `oscar-redis`/`oracle-redis` malware (GHSA-c6m6-wr2f-6f63, MAL-2026-16390) | never install a fork; verify the owner against the awesome-list entry |
| Base UI vs Radix split (Dice UI, data-table-filters, Niko ship both) | pick Radix (shadcn default) and pin it; do not mix |
| TanStack v9 API differs from the v8 examples most posts use | every snippet verified against v9 docs; pin `9.3.1` |
| Container-fit widths have no upstream | own the solver; test it at three breakpoints |
| 100 rows × 116 columns is not a grid, it is a spreadsheet | "All columns" stays a destination behind a click |

## 11. Operator answers (locked 2026-10-30)

| Question | Answer | Consequence in this plan |
|---|---|---|
| Cutover style | Build at `/app`, swap `/` after parity | D9; `/legacy` stays for one release |
| View set | Redesign | D12; nine views in §6.1, all 116 keys mapped |
| Light theme | Defer until after cutover | D11; dark tokens ship now, light is a second block later |
| Logos | Keep | D10; identity lane keeps the logo strip, `GET /screens?source=tradingview` unchanged |

## 12. Effort

```
Phase 0  backend prerequisites   2.5 days (0.1–0.8, eight small tasks)
Phase 1  shell + tokens          1 day
Phase 2  grid + solver           2 days
Phase 3  filters                 2 days
Phase 4  views + column model    1 day
Phase 5  detail panel            1.5 days
Phase 6  breadth + sectors       1 day
Phase 7  parity + QA + cutover   2 days
                                 13 days
```

## 13. Evidence

- `research/finviz-ui.md` — measured Finviz anatomy, tokens, contrast, 116-key mapping
- `research/shadcn-inventory.md` — 558 entries surveyed, shortlist, licences, risks
- `research/ui-surface-audit.md` — our UI, API contract, phantom names, payload numbers
- `research/finviz-ui-raw/` — derived measurements (raw third-party assets are not committed)
