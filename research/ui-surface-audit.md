# UI surface audit — the interface, the API, the views and the catalogue, measured

**Repo** `/home/ubuntu/ztockz` @ `c2461ed` · **API probed live** on `127.0.0.1:8099` (uvicorn pid
3910601, depth revision 20073, depth `as_of` 2026-10-30T16:13:15Z) · **Audit date** 2026-10-30.
Every number below is either quoted from a line of source, printed by a command reproduced in §8, or
measured with `curl`/`urllib` against the running server. Nothing is estimated unless it is labelled
"extrapolated".

---

## 0. Verdict, and what the brief got wrong

| Claim in the brief | Verified? | Measured value |
|---|---|---|
| `views.py` references **82** names | **No** | **71** unique names (`ALL_COLUMNS`, `views.py:284-286`). 82 is a different vocabulary: it is the count of Finviz **filter keys** (`selection.py:946` "All 82 keys", `x-filter-key-count: 82` in the published schema). |
| only **48** are real catalogue keys | **Yes** | 48 of 71. |
| **68** of the 116 catalogue keys are referenced by no view | **Yes** | 68 (`116 − 48`). |
| **23** phantom names | **Yes** | 23, all listed in §3.3 with a mapping each. |
| depth store **9 MB** | stale | **11,473,979 B** (10.9 MiB) at `~/ztockz-data/depth.json`, 4,217 symbols, `as_of` 2026-10-30T16:13:15Z. |
| **106** columns filled | **Yes** | 106 of 116 catalogue keys are present *and* non-null in the store; the other 10 are absent (they are session/transport fields, §3.1). |
| **six** named views | 6 tabs **+ `all`** = 7 selectable | `VIEW_NAMES` (6) + `ALL_VIEW_NAME`; `SCREEN_VIEW_NAMES` is 7 (`views.py:307`), and the UI renders 7 tabs (`ui/index.html:115-123`). |
| **4,136** tests | **Yes** | `pytest --collect-only -q` sums to 4,136. |
| `/screens/select` has **ETag support** | **No** | No `ETag` header on the route at all (§2.6). ETag exists only on `/market/depth` and `/market/hub`. |
| UI is vanilla, no bundler | **Yes** | No `package.json`, no bundler config, served by `StaticFiles` (`app/main.py:516`). |

The single most consequential thing this audit found is not in the brief: **the interface cannot ask
for the columns it needs.** `columns` is capped at 40 (`selection.py:124`), the "All columns" view is
71 wide, and a `columns` request silently returns `null` for 11 of the 116 keys — four of them
because of a bug in the enrichment order, not because the data is missing (§4.2).

---

## 1. The interface we have

### 1.1 Inventory and build

| File | Lines | Bytes | gzip | Role |
|---|---:|---:|---:|---|
| `ui/index.html` | 312 | 18,233 | 4,305 | shell: sidebar, hero, toolbar, static `<thead>`, 3 modals, palette |
| `ui/app.js` | 1,117 | 44,525 | 11,942 | all rendering, state, fetch, filters, CSV/JSON export |
| `ui/styles.css` | 678 | 41,349 | 8,239 | 41 KB of hand-written design tokens + components |
| **Total** | **2,107** | **104,107** | **24,486** | **101.7 KiB uncompressed / 23.9 KiB gzip** |

- **No build step.** No `package.json`, `vite.config.*`, `webpack.config.*`, `tsconfig.json` or
  `tailwind.config.*` exists at the repo root (`ls` returns "No such file or directory" for all six).
  `index.html:8` and `:310` load `styles.css?v=20261025-20` and `app.js?v=20261025-20` — a manual
  cache-buster string is the entire asset pipeline.
- Served by FastAPI: `application.mount("/ui", StaticFiles(directory=ui_dir, html=True), name="ui")`
  (`app/main.py:516`), with `/` redirecting to `/ui/` (`app/main.py:520`).
- Zero dependencies: no React, no fetch wrapper, no state library. `state` is one mutable object
  literal (`app.js:95-120`).

### 1.2 How the table is rendered

`renderTable()` (`app.js:447-524`) rebuilds `<thead>` and `<tbody>` from template strings on **every**
state change:

1. header: `` `<th>` per `currentColumns()` `` (`app.js:458-465`);
2. body: `pageRows.map(...)` → one `<tr>` per row, each cell from `renderCell(row, key)`
   (`app.js:222-242`, `:484-500`);
3. after `innerHTML`, it re-binds **three** listener sets per row — logo `error`, star `click`, row
   `click`/`keydown` (`app.js:502-519`) — plus one `click` per sortable `<th>` (`:520-522`).

Consequences that matter for the rewrite: full DOM replacement on every keystroke in the search box
(`app.js:1030-1034` calls `renderTable()` on `input`), listeners re-created per render, and no
virtualisation (all 74 × 20 = 1,480 cells are real DOM nodes). The only concession to cost is
`state.loading` short-circuiting (`app.js:448`).

The static markup disagrees with the runtime: `index.html:157-169` ships a 10-column `<thead>`
(`#`, Company, Price, 1D, Mkt cap, P/E, Fwd P/E, RSI, YTD, Watch) and `index.html:172-176` ships
skeleton rows with `colspan="10"`, but the default view renders **74** columns. The user sees the
10-column header until the first payload lands.

### 1.3 How many columns it shows today

`state.screenView` defaults to `"all"` (`app.js:108`), so the first paint is the widest table the
product has:

| View tab | Data columns | `<th>` rendered (2 identity + n + Watch) | Source |
|---|---:|---:|---|
| overview | 27 | 30 | `views.py:118-146` |
| valuation | 11 | 14 | `views.py:156-168` |
| ownership | 7 | 10 | `views.py:177-185` |
| performance | 11 | 14 | `views.py:195-207` |
| financial | 28 | 31 | `views.py:217-246` |
| technical | 13 | 16 | `views.py:256-270` |
| **all (default)** | **71** | **74** | `views.py:284-296` |

The client keeps its **own** copy of the view table (`app.js:83-94`) that has already drifted from the
server's:

| View | server | client | difference |
|---|---:|---:|---|
| overview | 27 | 8 | client drops 19 columns (`volume, eps_ttm, eps_next_y, sales, ps, roe, week52_high, week52_low, perf_year, atr_14, rel_volume, beta, shares_outstanding, shares_float, insider_own, inst_own, employees, ipo, option_short`) |
| technical | 13 | 14 | client adds `market_cap` |
| valuation / ownership / performance / financial | 11/7/11/28 | 11/7/11/28 | identical |

So "Overview" in the UI is not the API's `overview`. A request for `view=overview` returns 27 cells
per row and the UI paints 8 of them — 19 wasted cells × 20 rows on every load.

### 1.4 Network calls

All calls are same-origin `fetch`, JSON, no headers beyond `content-type` (`app.js:263-280`).

| # | Call | When | Body / query | Sends `columns`? | Conditional? |
|---|---|---|---|---|---|
| 1 | `POST /screens/select` | every load, view switch, filter apply, page step, region switch (`app.js:296-300`) | `{mode:"criteria", region, criteria:{filters, sort, limit:20, offset, common_only:true}, view:<tab>, fill_wide:<tab==="all" && region!=="global">}` (`app.js:288-294`) | **Never** — it sends `view`, which is mutually exclusive with `columns` | no |
| 2 | `GET /screens?source=tradingview[&region=…]` | every load, in parallel (`app.js:295`, `:304`) | — | no | no |
| 3 | `GET /screens?source=multi&region=…` | only if call 1 rejects (`app.js:300-303`) | — | no | no |
| 4 | `GET /news/{symbol}?limit=6` | "Open news" button (`app.js:824`) | — | no | no |

There is no call to `/engine/*` anywhere in the UI, even though `/engine/catalog`, `/engine/screen`,
`/engine/insider/{symbol}`, `/engine/peers/{symbol}` and `/engine/statements/{symbol}` all exist and
are live (§5, §6.4).

Call 2 exists only to recover `name` and `logo`, which `POST /screens/select` does not return in the
light row. The client then **joins the two responses itself** (`app.js:319-338`): it walks the
browse rows, looks each symbol up in the select rows, merges `{...light, ...overviewCache, ...rich}`,
stops at 20 (`:332`), then appends any unmatched rich rows (`:334-338`). That is a client-side join
over two independently-timed snapshots — with a hard-coded alias blacklist `["GOOGN","GOOGM"]`
(`app.js:315`) to hide duplicate share classes. A React rewrite gets `name`/`logo` from one call or
not at all; this join must not be preserved.

### 1.5 Caching / ETag

`grep -rn "ETag\|If-None-Match\|304" ui/` returns **nothing**. Every reload re-downloads the full
body (41.5 KB on the default "all" view, §6.2). The only cache-shaped thing in the UI is
`localStorage` for the watchlist and hidden columns (`app.js:5-6`, `:131-138`).

### 1.6 How filters are presented

Two unrelated filter systems share one toolbar:

**(a) Client-side chips + text search** (`app.js:423-445`) over the ≤20 rows already in memory:

| Chip | Predicate | Honest? |
|---|---|---|
| All names | — | yes |
| Momentum | `change_pct > 0` | only within the loaded page |
| Value | `0 < pe < 25` | only within the loaded page |
| Quality | `roe >= 15` | only within the loaded page |
| Watchlist | `state.watch.has(symbol)` | only the loaded page, so a watched name not on page 1 disappears |
| search box | `symbol + name` substring | only the loaded page |

**(b) Server-side "Advanced filters" modal** (`index.html:280-299`, `app.js:906-951`): four numeric
inputs mapped to Finviz filter keys — `cap` (×1e9), `fa_pe`, `sh_price`, `ta_rsi` (`app.js:897-904`)
— plus a 19-option sector `<select>` (`index.html:291`) and a 3-option sort (`market_cap`,
`turnover`, `gainers`, `index.html:292`). Applied filters replace `criteria.filters` and trigger a
full reload (`app.js:943-949`).

The chip filters and the server filters are never combined: `applyFilters()` resets the local sort to
`{key:null}` (`app.js:945`) and `loadData()` re-fetches with `common_only:true` (`app.js:291`), so the
chips are re-applied on top of the new page.

### 1.7 How the breadth bar is computed

`renderBreadth()` (`app.js:716-727`):

```
rows       = filteredRows()                                  // ≤ 20, already client-filtered
advancing  = count(row.change_pct > 0)
declining  = count(row.change_pct < 0)
upShare    = advancing / (advancing + declining) * 100
breadthUp.style.width   = `${upShare}%`
breadthDown.style.width = `${100 - upShare}%`
```

It is a **20-row stat dressed as a market stat**. The hero's "Advancing" card uses the same
population (`app.js:408-410`) and its own subtitle admits it: `"of current page"` (`:410`).
The fix is not a UI fix: no endpoint returns universe-wide advancing/declining (§5, gap G2).

### 1.8 What is hard-coded

| Value | Where | Reality |
|---|---|---|
| `5,579` / `40,950` / `5,779` scope counts | `index.html:99-101` | overwritten only for the active tab (`app.js:415-416`); the other two are static lies until clicked |
| `One scan, 20 names.` | `index.html:187` | true, and the cap is server-side |
| `Fundamentals 3,902 rows` / `Ownership facts 3,831 rows` | `index.html:252-253` | never refreshed from any endpoint |
| `perPage: 20` | `app.js:103` | duplicates `DISPLAY_ROWS` (`snapshot.py:45`) |
| 7 view definitions, 71 column names | `app.js:83-94` | duplicate of `views.py`, already drifted (§1.3) |
| 72-entry `COLUMN_META` label/format table | `app.js:8-81` | a third copy of the catalogue's labels |
| 19 sector names | `index.html:291` | correct for `/screens/select` (measured: the 19 sum to 5,532 of 5,600), **wrong** for the depth store's 13 SEC labels (§3.5) |
| `["GOOGN","GOOGM"]` | `app.js:315` | share-class alias hack |
| `"US equity"` fallback company name | `app.js:219` | fabricated label when `name` is missing |

### 1.9 What the new UI must preserve, and what it should drop

**Preserve (it is product, not decoration):**

1. **Row → detail panel selection**, keyboard-navigable (`app.js:511-519`, `:1104-1111`), with the
   52-week range bar computed from `price/low/high` (`app.js:587-595`). This is the only place the
   UI turns three numbers into a judgement.
2. **Watchlist in `localStorage`** with star affordance and a count badge (`app.js:5`, `:624-632`).
3. **Provenance on screen**: `Live scan · <timestamp>` (`app.js:417`), per-row `as_of`
   (`app.js:586`), and the integrity card's claim "unknown stays unknown" (`index.html:247`).
   The API supports all of it (`as_of` in the envelope and in every row).
4. **"Missing is not zero"** rendering: `renderCell` prints `--` for `null` and never `0`
   (`app.js:225-227`), and `fill_rate` is a first-class server concept (`views.py:86-90`).
5. **Export of exactly what is on screen** (CSV + JSON, `app.js:660-703`) — it respects the active
   view and the client filters, and it is the only bulk path that exists today.
6. **Command palette + `/` focus + `r` refresh** (`app.js:856-870`, `:1081-1113`).
7. **Three sorts surfaced as one control** (`market_cap`/`turnover`/`gainers`, `index.html:292`).

**Drop (measured as broken, not merely unfashionable):**

1. The 10-column static header (`index.html:157-169`) — it is wrong on first paint.
2. The client-side merge of two snapshots (`app.js:319-338`) and the alias blacklist (`:315`).
3. The client's duplicate view table (`app.js:83-94`) — it already contradicts the server.
4. The breadth bar as implemented (`app.js:716-727`) — a 20-row number labelled "Breadth".
5. Chips that filter only the loaded page while looking like screen-wide filters (`app.js:423-445`).
6. `<innerHTML>` string rendering with per-render listener rebinding (`app.js:484-522`).
7. Hard-coded scope counts and fact-row counts (`index.html:99-101`, `:252-253`).

---

## 2. The API contract the UI must consume

Routes: `GET /screens` (`screens.py:136`), `GET /screens/schema`, `GET /screens/select/schema`
(`:180`), `POST /screens/select` (`:192`), `POST /screens/{id}/export` (`:373`).

### 2.1 Request fields (`POST /screens/select`)

Validated by `parse_selection` (`selection.py:686-709`); unknown top-level keys are a 422
(`_reject_extra`, `:325-336`). Published schema: `schemas/stock_selection_request_v1.json`
(`additionalProperties: false`).

| Field | Type / values | Required | Mode | Evidence |
|---|---|---|---|---|
| `mode` | `"criteria"` \| `"symbols"` | **yes** | both | `selection.py:92`, `:699-707` |
| `region` | `us` \| `global` \| `us_etf` | criteria | criteria | `selection.py:587-590`, `screens.py:101` |
| `asset_class` | `stock` (default) \| `etf` | no | both | `selection.py:361-370` |
| `view` | one of `overview, valuation, ownership, performance, financial, technical, all` | no | both | `selection.py:407-417`; schema `properties.view.enum` |
| `columns` | array of known names, ≤40, unique | no | **criteria only in practice** (§2.7) | `selection.py:547-577` |
| `fill_wide` | boolean, default false | no | both | `selection.py:385-392` |
| `criteria.filters` | array of `{key\|field, op, value}` | no | criteria | `selection.py:448-505` |
| `criteria.sort` | `market_cap` \| `turnover` \| `gainers` | no (default `market_cap`) | criteria | `selection.py:351-358`, `rank.SORTS` |
| `criteria.limit` | integer ≥1, **clamped to 20** | no | criteria | `selection.py:339-348` |
| `criteria.offset` | integer ≥0 | no | criteria | `selection.py:395-404` |
| `criteria.common_only` | boolean, default false | no | criteria | `selection.py:373-382` |
| `symbols` | non-empty array of unique, trimmed tickers | symbols | symbols | `selection.py:629-650` |
| `source` | `tradingview` (only value) | symbols | symbols | `selection.py:96`, `:652-660` |
| `snapshot_id` | non-empty string naming a stored snapshot | symbols | symbols | `selection.py:662-667` |

Filter operators: `eq, ne, gt, gte, lt, lte, between, in, contains` (`filters.OPERATORS`, exposed as
`property.op.enum`). Filter vocabulary: 82 Finviz keys (`x-filter-key-count: 82`) of which some
resolve to a row column, some take a Finviz option token, and some are refused by name with a reason
(`filter_fields.binding`).

### 2.2 Response fields

`Selection.as_document()` (`selection.py:748-767`) plus the logo decoration added by
`_with_logos` (`screens.py:112-125`).

| Field | Meaning | Evidence |
|---|---|---|
| `mode`, `region`, `source`, `as_of`, `snapshot_id`, `sort`, `limit` | echo + provenance; `limit` is the **applied** limit after clamping | `:751-758` |
| `universe_total` | rows the question ran over (5,600 for `us`) | `:759` |
| `total` | rows that **matched** (post-filter), not the page | `:760` |
| `returned` | page size (`len(rows)`) | `:761` |
| `filters` | each criterion echoed as `{key, field, op, value}` with the compiled predicate | `:762`, `Criterion.as_dict` `:277-281` |
| `columns` | the requested column list, echoed | `:763` |
| `symbols`, `missing` | symbols mode echo + unknown tickers | `:764-765` |
| `rows[]` | one object per row | `:766` |
| `rows[].logo` | `/ui/logos/AAPL.png` or an inline monogram | `screens.py:122` |
| row shape (no `view`/`columns`) | light row: `rank, symbol, name, exchange, country, sector, price, change_pct, volume, turnover, turnover_usd, market_cap_usd, is_primary, as_of, logo` (15 keys) | measured |
| row shape (`view`) | `rank, symbol, name, as_of` + one key per view column (76 on `all`) | `selection.py:838-848` |
| row shape (`columns`) | `symbol` + the requested keys in order + `as_of` | `selection.py:864-868` |

Note the light row is **heavier** than a 12-column row: 13,169 B vs 12,211 B (§6.2).

### 2.3 Limits, quoted exactly

- `DISPLAY_ROWS = 20` — `app/screen/snapshot.py:45`.
- `DISPLAY_LIMIT = DISPLAY_ROWS` — "Imported, not re-declared: the browse display and the selection
  page are the same window" `selection.py:98-100`.
- Clamp, not error: `return min(raw, DISPLAY_LIMIT)` `selection.py:348`; docstring "a larger value is
  clamped (not an error …) and the envelope reports the applied `limit`" `:35-38`.
- Browse ignores `limit` outright: `del limit` `screens.py:156` with the comment "Requesting limit=100
  is not an error — it just cannot buy more rows than the contract sells" `:154-155`.
- `symbols` mode has no `limit` knob at all: `limit=DISPLAY_LIMIT` is hard-coded `selection.py:674`.
- `COLUMN_LIMIT = 40` — `selection.py:121-124`; schema `properties.columns.maxItems: 40`.
- Engine-wide scan cap: `FULL_SCAN_ROWS = 100_000` (`scanner.py:94`) — every criteria call re-scans.

Measured: `{"limit":100}` → `"limit": 20, "returned": 20`; `{"limit":21}` → `20`;
`{"limit":0}` → `422 invalid_selection "'limit' must be at least 1, got 0"`;
`{"columns":[41 items]}` → `422 "selection 'columns' asks for 41 cells; the maximum is 40"`.

### 2.4 `columns` rules

1. non-empty array of non-blank strings (`selection.py:552-567`);
2. ≤40 entries (hard 422, not clamped);
3. duplicates are **silently dropped**, order preserved (`:573-576`);
4. every name must be in `_KNOWN_COLUMNS` = filter fields ∪ **all 116 catalogue keys** ∪ light-row
   keys (`:527-544`) — so any catalogue key is *accepted*;
5. `view` and `columns` are mutually exclusive: "two ways to name the same output is a question with
   two answers" (`:593-597`);
6. an accepted-but-uncarried column returns `null`, not an error (`:856-862`).

Because `COLUMN_LIMIT` (40) < `len(ALL_COLUMNS)` (71), **the "All columns" tab cannot be expressed
through `columns`** — it needs `view: "all"` or a two-call split.

### 2.5 Error codes

Envelope is `{error, errorMessage}` (`app/auth.py:24-31`).

| HTTP | `error` | When | Evidence |
|---:|---|---|---|
| 422 | `invalid_json` | body is not JSON | `screens.py:215-219` |
| 422 | `invalid_selection` | any validation failure; message names the field | `selection.py:251` |
| 422 | `unknown_view` | `view` not in the 7 | `selection.py:413-416` |
| 422 | `invalid_filter` | bad operator/value/type | `selection.py:434`, `:486` |
| 422 | `unavailable_filter_key` / `unknown_filter_key` / `invalid_filter_key` / `choice_filter_key` | key has no light column / typo / malformed / needs option token | `filter_fields.py:245-268` |
| 422 | `invalid_filter_option` / `unknown_filter_option` / `unsupported_filter_option` / `not_a_choice_filter` | option-token errors | `choice_filters.py:211-233` |
| 404 | `snapshot_not_found` | symbols mode, unknown `snapshot_id` | `screens.py:237-242` |
| 422 | `source_mismatch` | snapshot built from another source | `screens.py:243-251` |
| 422 | `asset_class_mismatch` | stock/ETF tab contradiction | `screens.py:256-263` |
| 503 | `scanner_unavailable` | scan failed and no snapshot | `screens.py:165-170`, `:291-296` |
| 404 | `screen_not_found` / 422 `unknown_format` | export only | `screens.py:381-398` |

There is **no 429, no rate limit, no pagination cursor** on this route.

### 2.6 ETag behaviour — the brief is wrong

- `POST /screens/select` response headers, verbatim:
  `HTTP/1.1 200 OK · date · server: uvicorn · content-length: 12199 · content-type: application/json`.
  **No `ETag`, no `Cache-Control`.** `grep -n ETag app/api/screens.py` → nothing.
- `GET /screens` likewise returns no validator.
- The ETag machinery (`app/api/cache.py`) is wired only into `/market/depth` (`market.py:535-536`)
  and `/market/hub` (`market.py:460-461`), keyed on the store's `revision` plus `as_of` plus the
  requested symbol list (`etag_for`, `cache.py:32-41`).

Measured on `/market/depth?symbols=AAPL,MSFT`:

| Probe | Result |
|---|---|
| two polls 4 s apart (clock moved, store unchanged) | identical `"depth-r20073-30d63ce5c418bf4f"` |
| `If-None-Match: "depth-r20073-30d63ce5c418bf4f"` | `304`, **0 bytes** |
| different symbol list | `"depth-r20073-33b72f130e5d9c9e"` (query is in the digest) |
| `etag_for("depth", 20074, same as_of)` | different tag (revision is in the prefix) |
| `etag_for("depth", same revision, as_of + 1 s)` | different tag — **the tag is time-sensitive by construction** |

So "the ETag does not change when only the clock moves" holds **because `as_of` is the collector's
cycle stamp, not `now()`**. It would not hold for a body-hash ETag on `/screens/select`: `as_of` is
`datetime.now(UTC).isoformat(timespec="microseconds")` per call (`screens.py:341`), so two identical
requests differ in the last 6 digits — measured, every one of 5 consecutive pages carried a distinct
`as_of`. Any ETag added to the selection route must key on a **data revision**, not on the body.

### 2.7 Contract bugs and hazards found while auditing

| Bug | Evidence | Impact |
|---|---|---|
| `columns` is **silently ignored in `symbols` mode** | `_parse_symbols` never passes `columns` (`selection.py:669-683`), though the schema says "Both modes" (`:1017-1029`) | `POST {mode:"symbols", columns:["price","pe"]}` returns the 15-key light row and echoes `"columns": []` — silent contract violation, no error |
| the same route returns `logo` in `rows[]`, but the response schema has no `logo` | `screens.py:122` vs `schemas/…response…` | a typed client generated from the schema drops the field |
| browse and select report different universe sizes | `GET /screens` `total: 3896`; `POST /screens/select` `universe_total: 5600` (same region, same second) | **documented, not a bug** (`screens.py:28-32`: select drops the `min_market_cap` floor and the `subtype=common` gate), but the current UI prints both under the label "Universe" (`app.js:349-350`, `:407`) |
| `symbols` mode truncates at 20 with no `limit` knob | `selection.py:674`, `:825` | a 25-name watchlist silently returns 20; only `total > returned` hints at it |

---

## 3. Views and catalogue

### 3.1 Counts, verified from source

Reproduced by the snippet in §8.1:

```
catalogue keys: 116 (unique 116)
METRIC_BY_KEY (app/market/fields.py): 71
unique names referenced by views.py: 71
  referenced AND in catalogue: 48
  referenced NOT in catalogue: 23
catalogue keys assigned by views.py: 48
catalogue keys with NO view: 68
```

`views.py` validates against `app.market.fields.METRIC_BY_KEY`, **not** the catalogue
(`views.py:325-330`), which is why 23 names that no catalogue key carries pass import-time
validation. The two vocabularies are the Finviz *quote page* (71 metrics) and the Finviz *screener*
(116 columns); nothing translates them.

The catalogue also carries its own `views` metadata per column
(`app/engine/data/finviz_columns_116.json`), a **third**, richer taxonomy of 13 labels:
`overview(25) screener(112) valuation(11) income(20) fundamentals(37) balance_sheet(9) dividends(8)
ownership(10) performance(9) technical(16) price(11) analyst(2) news(4)`. Under that field **every
one of the 116 keys has a home** — "unassigned" is only true of `views.py`, not of the catalogue.

Depth store fill: 106 of 116 keys present and non-null across 4,217 symbols. The 10 absent are
`price, change, volume, prev_close, open, high, low` (session cells, owned by the quote/scan row),
`daily_digest, sort_order, row_type` (transport/bookkeeping).

### 3.2 The 116 catalogue keys and their current view

`views.py` column = current assignment (`none` = not referenced by any view). `catalogue views` =
the JSON `views` field with the ubiquitous `screener` label filtered out. `depth fill` = non-null
symbols out of 4,217.

> **Label caveat for the UI.** The catalogue's `label` is the provider's *abbreviated* form — `Gross M`,
> `Oper M`, `Curr R`, `LTDebt/Eq`, `EPS next Q`, `Dividend Gr. 3Y`. It is a key for a legend, not a
> column header a designer can ship. The presentable strings live in the UI's own 72-entry
> `COLUMN_META` table (`app.js:8-81`), which is the third copy of this vocabulary and will have to be
> rebuilt (or promoted into the API) for the React table.

| # | key | label | unit | category | views.py | catalogue views | depth fill |
|---:|---|---|---|---|---|---|---:|
| 1 | `ticker` | Ticker | text | identification-classification | **none** | overview | 4,217 |
| 2 | `company` | Company | text | identification-classification | **none** | overview | 4,217 |
| 3 | `sector` | Sector | text | identification-classification | **none** | overview | 4,217 |
| 4 | `industry` | Industry | text | identification-classification | **none** | overview | 4,217 |
| 5 | `security_type` | Security Type | text | identification-classification | **none** | overview | 4,217 |
| 6 | `country` | Country | text | identification-classification | **none** | overview | 4,217 |
| 7 | `exchange` | Exchange | text | identification-classification | **none** | overview | 4,217 |
| 8 | `index_membership` | Index | text | identification-classification | **none** | overview | 2,744 |
| 9 | `optionable` | Optionable | text | identification-classification | **none** | overview | 4,197 |
| 10 | `shortable` | Shortable | text | identification-classification | **none** | overview | 4,197 |
| 11 | `employees` | Employees | count | identification-classification | overview, financial | overview | 4,016 |
| 12 | `website` | Website | text | identification-classification | **none** | overview | 4,076 |
| 13 | `earnings_date` | Earnings | date | identification-classification | **none** | overview | 3,730 |
| 14 | `ipo_date` | IPO Date | date | identification-classification | **none** | overview | 4,217 |
| 15 | `market_cap` | Market Cap | USD | valuation | overview, valuation | valuation | 4,216 |
| 16 | `enterprise_value` | Enterprise Value | USD | valuation | valuation | valuation | 4,214 |
| 17 | `pe` | P/E | ratio | valuation | overview, valuation | valuation | 3,878 |
| 18 | `forward_pe` | Forward P/E | ratio | valuation | overview, valuation | valuation | 3,594 |
| 19 | `peg` | PEG | ratio | valuation | valuation | valuation | 2,452 |
| 20 | `ps` | P/S | ratio | valuation | overview, valuation | valuation | 3,654 |
| 21 | `pb` | P/B | ratio | valuation | valuation | valuation | 3,901 |
| 22 | `pc` | P/C | ratio | valuation | valuation | valuation | 4,192 |
| 23 | `pfcf` | P/FCF | ratio | valuation | valuation | valuation | 3,542 |
| 24 | `ev_ebitda` | EV/EBITDA | ratio | valuation | valuation | valuation | 3,499 |
| 25 | `ev_sales` | EV/Sales | ratio | valuation | valuation | valuation | 3,626 |
| 26 | `eps` | EPS | USD | income-statements-ratios | **none** | income, fundamentals | 4,115 |
| 27 | `net_income` | Income | USD | income-statements-ratios | **none** | income, fundamentals | 4,175 |
| 28 | `sales` | Sales | USD | income-statements-ratios | overview, financial | income, fundamentals | 4,095 |
| 29 | `eps_next_q` | EPS next Q | USD | income-statements-ratios | financial | income, fundamentals | 3,417 |
| 30 | `eps_this_y` | EPS This Y | pct | income-statements-ratios | financial | income, fundamentals | 3,310 |
| 31 | `eps_next_y` | EPS Next Y | pct | income-statements-ratios | overview, financial | income, fundamentals | 3,377 |
| 32 | `eps_past_3y` | EPS Past 3Y | pct | income-statements-ratios | **none** | income, fundamentals | 2,736 |
| 33 | `eps_past_5y` | EPS Past 5Y | pct | income-statements-ratios | **none** | income, fundamentals | 2,397 |
| 34 | `eps_next_5y` | EPS Next 5Y | pct | income-statements-ratios | financial | income, fundamentals | 2,458 |
| 35 | `sales_past_3y` | Sales Past 3Y | pct | income-statements-ratios | **none** | income, fundamentals | 3,379 |
| 36 | `sales_past_5y` | Sales Past 5Y | pct | income-statements-ratios | **none** | income, fundamentals | 3,133 |
| 37 | `eps_qoq` | EPS Q/Q | pct | income-statements-ratios | financial | income, fundamentals | 4,067 |
| 38 | `sales_qoq` | Sales Q/Q | pct | income-statements-ratios | financial | income, fundamentals | 3,624 |
| 39 | `eps_yoy_ttm` | EPS YoY TTM | pct | income-statements-ratios | financial | income, fundamentals | 3,903 |
| 40 | `sales_yoy_ttm` | Sales YoY TTM | pct | income-statements-ratios | financial | income, fundamentals | 3,572 |
| 41 | `eps_surprise` | EPS Surprise | pct | income-statements-ratios | **none** | income, fundamentals | 3,615 |
| 42 | `revenue_surprise` | Revenue Surprise | pct | income-statements-ratios | **none** | income, fundamentals | 3,126 |
| 43 | `gross_margin` | Gross M | pct | income-statements-ratios | financial | income, fundamentals | 4,214 |
| 44 | `operating_margin` | Oper M | pct | income-statements-ratios | **none** | income, fundamentals | 4,214 |
| 45 | `net_margin` | Profit M | pct | income-statements-ratios | **none** | income, fundamentals | 4,214 |
| 46 | `roa` | ROA | pct | balance-sheet-ratios | financial | balance_sheet, fundamentals | 4,000 |
| 47 | `roe` | ROE | pct | balance-sheet-ratios | overview, financial | balance_sheet, fundamentals | 3,989 |
| 48 | `roic` | ROIC | pct | balance-sheet-ratios | financial | balance_sheet, fundamentals | 4,109 |
| 49 | `current_ratio` | Curr R | ratio | balance-sheet-ratios | financial | balance_sheet, fundamentals | 4,208 |
| 50 | `quick_ratio` | Quick R | ratio | balance-sheet-ratios | financial | balance_sheet, fundamentals | 3,907 |
| 51 | `lt_debt_to_equity` | LTDebt/Eq | ratio | balance-sheet-ratios | financial | balance_sheet, fundamentals | 4,139 |
| 52 | `debt_to_equity` | Debt/Eq | ratio | balance-sheet-ratios | financial | balance_sheet, fundamentals | 4,049 |
| 53 | `book_per_share` | Book/sh | USD | balance-sheet-ratios | financial | balance_sheet, fundamentals | 4,213 |
| 54 | `cash_per_share` | Cash/sh | USD | balance-sheet-ratios | financial | balance_sheet, fundamentals | 4,211 |
| 55 | `dividend` | Dividend | USD | dividend | **none** | dividends, fundamentals | 1,620 |
| 56 | `dividend_yield` | Dividend Yield | pct | dividend | **none** | dividends, fundamentals | 4,217 |
| 57 | `dividend_ttm` | Dividend TTM | USD | dividend | **none** | dividends, fundamentals | 4,217 |
| 58 | `payout_ratio` | Payout Ratio | pct | dividend | **none** | dividends, fundamentals | 3,426 |
| 59 | `dividend_ex_date` | Dividend Ex Date | date | dividend | **none** | dividends, fundamentals | 1,944 |
| 60 | `dividend_growth_1y` | Dividend Gr. 1Y | pct | dividend | **none** | dividends, fundamentals | 1,622 |
| 61 | `dividend_growth_3y` | Dividend Gr. 3Y | pct | dividend | **none** | dividends, fundamentals | 1,388 |
| 62 | `dividend_growth_5y` | Dividend Gr. 5Y | pct | dividend | **none** | dividends, fundamentals | 1,330 |
| 63 | `shares_outstanding` | Outstanding | shares | ownership | overview, ownership | ownership | 4,216 |
| 64 | `shares_float` | Float | shares | ownership | overview, ownership | ownership | 4,207 |
| 65 | `float_pct` | Float % | pct | ownership | **none** | ownership | 4,103 |
| 66 | `insider_own` | Insider Own | pct | ownership | overview, ownership | ownership | 4,216 |
| 67 | `insider_trans` | Insider Trans | pct | ownership | ownership | ownership | 4,139 |
| 68 | `inst_own` | Inst Own | pct | ownership | overview, ownership | ownership | 4,216 |
| 69 | `inst_trans` | Inst Trans | pct | ownership | ownership | ownership | 4,192 |
| 70 | `short_float` | Short Float | pct | ownership | **none** | ownership | 4,204 |
| 71 | `short_ratio` | Short Ratio | ratio | ownership | **none** | ownership | 4,212 |
| 72 | `short_interest` | Short Interest | shares | ownership | **none** | ownership | 4,209 |
| 73 | `perf_1w` | Perf Week | pct | performance | **none** | performance | 4,215 |
| 74 | `perf_4w` | Perf Month | pct | performance | **none** | performance | 4,204 |
| 75 | `perf_13w` | Perf Quart | pct | performance | **none** | performance | 4,162 |
| 76 | `perf_26w` | Perf Half | pct | performance | **none** | performance | 4,063 |
| 77 | `perf_52w` | Perf Year | pct | performance | **none** | performance | 3,911 |
| 78 | `perf_ytd` | Perf YTD | pct | performance | overview, performance | performance | 4,217 |
| 79 | `perf_3y` | Perf 3Y | pct | performance | performance | performance | 3,599 |
| 80 | `perf_5y` | Perf 5Y | pct | performance | performance | performance | 3,390 |
| 81 | `perf_10y` | Perf 10Y | pct | performance | performance | performance | 2,523 |
| 82 | `beta` | Beta | ratio | technical-indicators | overview, technical | technical | 3,926 |
| 83 | `atr` | ATR | USD | technical-indicators | **none** | technical | 4,217 |
| 84 | `volatility_1w` | Volatility W | pct | technical-indicators | **none** | technical | 4,217 |
| 85 | `volatility_4w` | Volatility M | pct | technical-indicators | **none** | technical | 4,204 |
| 86 | `sma20` | SMA20 | pct | technical-indicators | technical | technical | 4,217 |
| 87 | `sma50` | SMA50 | pct | technical-indicators | technical | technical | 4,217 |
| 88 | `sma200` | SMA200 | pct | technical-indicators | technical | technical | 4,217 |
| 89 | `high_50d` | 50D High | pct | technical-indicators | **none** | technical | 4,217 |
| 90 | `low_50d` | 50D Low | pct | technical-indicators | **none** | technical | 4,217 |
| 91 | `high_52w` | 52W High | pct | technical-indicators | **none** | technical | 4,217 |
| 92 | `low_52w` | 52W Low | pct | technical-indicators | **none** | technical | 4,217 |
| 93 | `rsi` | RSI | ratio | technical-indicators | **none** | technical | 4,210 |
| 94 | `all_time_high` | All-Time High | pct | technical-indicators | **none** | technical | 4,216 |
| 95 | `all_time_low` | All-Time Low | pct | technical-indicators | **none** | technical | 4,216 |
| 96 | `change_from_open` | Change from Open % | pct | intraday | **none** | price, overview | 4,217 |
| 97 | `gap` | Gap | pct | intraday | **none** | price, overview | 4,217 |
| 98 | `recommendation` | Recom | ratio | sentiment-analyst-data | **none** | analyst | 3,659 |
| 99 | `target_price` | Target Price | USD | sentiment-analyst-data | **none** | analyst | 3,690 |
| 100 | `news_url` | News URL | text | news | **none** | news | 3,658 |
| 101 | `news_title` | News Title | text | news | **none** | news | 3,658 |
| 102 | `news_time` | News Time | text | news | **none** | news | 3,658 |
| 103 | `daily_digest` | Daily Digest | text | news | **none** | news | 0 |
| 104 | `prev_close` | Prev Close | USD | intraday | technical | price, overview | 0 |
| 105 | `price` | Price | USD | intraday | overview, technical | price, overview | 0 |
| 106 | `change` | Change % | pct | intraday | **none** | price, overview | 0 |
| 107 | `volume` | Volume | shares | intraday | overview, technical | price, overview | 0 |
| 108 | `average_volume` | Avg Volume | shares | intraday | **none** | price, overview | 4,217 |
| 109 | `relative_volume` | Rel Volume | ratio | intraday | **none** | price, overview | 4,214 |
| 110 | `open` | Open | USD | intraday | **none** | price, overview | 0 |
| 111 | `high` | High | USD | intraday | **none** | price, overview | 0 |
| 112 | `low` | Low | USD | intraday | **none** | price, overview | 0 |
| 113 | `candlestick_pattern` | Candlestick | unknown | technical-indicators | **none** | technical | 1,785 |
| 114 | `week_52_range` | 52W Range | unknown | technical-indicators | **none** | technical | 4,217 |
| 115 | `sort_order` | Sort Order | count | meta | **none** | — | 0 |
| 116 | `row_type` | Row Type | text | meta | **none** | — | 0 |

### 3.3 The 23 names `views.py` references that the catalogue does not have

All 23 map 1:1 or 1:many onto real keys — the naming is consistent, it is just the *other*
vocabulary. `API non-null` is how many of the top-20 rows returned a value when that target key was
requested through `columns` (measured, §4.1).

| Phantom name | Closest catalogue key | That key (label / unit) | Mapping note | API non-null |
|---|---|---|---|---:|
| `atr_14` | `atr` | ATR / USD | 1:1 rename; catalogue spells it `atr` | 15/20 |
| `avg_volume` | `average_volume` | Avg Volume / shares | 1:1 rename | 15/20 |
| `change_pct` | `change` | Change % / pct | 1:1; catalogue `change` is the percent move (unit pct) | 0/20 |
| `eps_next_y_growth` | `eps_next_y` | EPS Next Y / pct | both are the growth %; `eps_next_y` in views.py already maps here — two names, one key | 15/20 |
| `eps_past_3_5y` | `eps_past_5y` | EPS Past 5Y / pct | Finviz prints one 'EPS past 5Y'; catalogue splits 3Y/5Y | 10/20 |
| `eps_ttm` | `eps` | EPS / USD | catalogue `eps` is the TTM figure | 15/20 |
| `income` | `net_income` | Income / USD | Finviz 'Income' column = net income | 15/20 |
| `index` | `index_membership` | Index / text | 1:1 rename | 14/20 |
| `ipo` | `ipo_date` | IPO Date / date | 1:1 rename | 15/20 |
| `oper_margin` | `operating_margin` | Oper M / pct | 1:1 rename | 15/20 |
| `option_short` | `optionable` | Optionable / text | Finviz 'Option/Short' is two facts; catalogue has `optionable` + `shortable` | 15/20 |
| `perf_half_y` | `perf_26w` | Perf Half / pct | 6 months = 26 weeks | 14/20 |
| `perf_month` | `perf_4w` | Perf Month / pct | 1 month = 4 weeks | 15/20 |
| `perf_quarter` | `perf_13w` | Perf Quart / pct | 1 quarter = 13 weeks | 15/20 |
| `perf_week` | `perf_1w` | Perf Week / pct | 1 week = 1w | 15/20 |
| `perf_year` | `perf_52w` | Perf Year / pct | 1 year = 52 weeks | 14/20 |
| `profit_margin` | `net_margin` | Profit M / pct | 1:1 rename | 15/20 |
| `rel_volume` | `relative_volume` | Rel Volume / ratio | 1:1 rename | 15/20 |
| `rsi_14` | `rsi` | RSI / ratio | 1:1 rename | 15/20 |
| `sales_past_3_5y` | `sales_past_5y` | Sales Past 5Y / pct | same collapse as eps_past_3_5y | 14/20 |
| `volatility` | `volatility_1w` | Volatility W / pct | 'Volatility' is two cells in Finviz (week/month); catalogue has volatility_1w + volatility_4w | 15/20 |
| `week52_high` | `high_52w` | 52W High / pct | **lossy**: views.py prints the price, `high_52w` is the % distance (Unit.PERCENT) | 19/20 |
| `week52_low` | `low_52w` | 52W Low / pct | **lossy**: same unit change; the only price carrier is `week_52_range` ({low, high} object) | 19/20 |

Two phantoms collapse two catalogue keys into one name (`option_short` → `optionable` + `shortable`;
`volatility` → `volatility_1w` + `volatility_4w`), and two collapse *into* an already-referenced key
(`eps_next_y_growth` → `eps_next_y`, which `views.py` already names in `financial`). Two are **not
faithful renames**: `week52_high`/`week52_low` are *prices* in `views.py`, while `high_52w`/`low_52w`
are *percent distances* — the price pair only survives inside `week_52_range`, which is an object, not
a cell. A UI that swaps the names silently changes the unit. Applying this
table to `views.py` would raise catalogue coverage from **48/116 (41 %)** to **70/116 (60 %)**,
leaving 46 keys unassigned — the 46 being identity/profile (`ticker, company, sector, industry,
security_type, country, exchange, website`), dividends (8), short interest (4), surprises (2),
`high_50d/low_50d`, `all_time_*`, `gap`, `change_from_open`, `recommendation`, `target_price`,
`news_*`, `daily_digest`, `open/high/low`, `volatility_4w`, `week_52_range`, `candlestick_pattern`,
`sort_order`, `row_type`.

### 3.4 What the mapping is worth

- **Zero data migration.** Every target already exists in the catalogue and 21 of the 22 distinct
  targets are filled in the depth store (`change` is the exception — a session cell the store does not
  carry); the mapping is a rename table in `views.py` plus a translation in
  `selection.VIEW_FIELDS` (which today maps the *phantom* names, e.g. `"rsi_14": "rsi_14"` at
  `selection.py:188` — pointing at a row field that the *screener* vocabulary also spells `rsi`).
- It does **not** fix the 40-column ceiling (§4.3) or the null-collapse (§4.2).

### 3.5 Two sector vocabularies, both live

Measured by filtering each label through `{"key":"sec","op":"eq","value":…}` and summing `total`:

| Vocabulary | Values | Sum of totals | Where it is authoritative |
|---|---:|---:|---|
| Finviz (the 19 in the UI `<select>`) | 19 | **5,532 / 5,600** | `POST /screens/select` rows |
| SEC-derived (`app/market/sectors.py`) | 13 | **116 / 5,600** (only `Utilities` overlaps) | depth store `sector`, `/engine/*` |

So the UI's dropdown is *correct for the API it calls*, and a new UI that builds a sector facet from
the depth store would be wrong by construction. 68 of 5,600 rows have no label in the 19-value set.
Any sector performance feature (§5 G3) has to pick one vocabulary and translate the other.

---

## 4. Can `columns` express every view we would need?

### 4.1 Measured coverage

Requesting all 116 catalogue keys in three ≤40-column batches (same `sort`, `limit:20`, `offset:0`)
returns **105 of 116 keys with at least one non-null value**. Eleven are always null:

`enterprise_value, ps, cash_per_share, short_float, daily_digest, change, open, high, low,
sort_order, row_type`

Seven of those are genuinely not carried on this path (`change/open/high/low` are session cells
absent from the depth store; `daily_digest/sort_order/row_type` are bookkeeping). **Four are a bug**,
and all four have data in the store for the very rows that returned null:

| Key | Store value for AAPL | Store fill | Why the API returns null |
|---|---:|---:|---|
| `ps` | 10.907893 | 3,654 / 4,217 | `ps` is a parity field (`universe.PARITY_FIELDS`, `universe.py:176`), so `build_universe` emits the key with `None`; the depth overlay is **fill-missing-only by presence, not truthiness** (`depth_overlay.py` module docstring, "``pe=None`` on the row is a recorded unknown that the row owns; ``pe`` absent is a gap"), so it refuses to fill a key that exists holding `None`. |
| `short_float` | 0.9 | 4,204 / 4,217 | same mechanism (parity field, present-as-None). |
| `enterprise_value` | 5,104,612,312,120 | 4,214 / 4,217 | the overlay *does* fill it, then `apply_view_fill` **unconditionally reassigns** it from the fundamentals snapshot: `for name in ("enterprise_value", "book_per_share", "cash_per_share", "prev_close", "eps_next_y_growth"): joined[name] = display.get(name)` (`fundamentals.py:403-404`) — `None` when the snapshot lacks the inputs. |
| `cash_per_share` | 2.7068 | 4,211 / 4,217 | same unconditional reassignment (`fundamentals.py:403-404`). |

The contrast is in `facts.apply_facts`, which gets it right: `joined[name] = current if value is None
else value` (`facts.py:645-648`). So the fix is three lines and a test, not a schema change — but
until it is fixed, any UI column built on these four keys is permanently blank.

### 4.2 What `columns` cannot express, structurally

1. **The `all` view (71 > 40).** Two calls required, or use `view`.
2. **Any view + a custom column set.** `view` and `columns` are mutually exclusive (`:593`).
3. **`symbols` mode at all** — `columns` is dropped (§2.7).
4. **Fields the depth store does not fill**: `change, open, high, low` (session cells), `daily_digest`,
   `sort_order`, `row_type`. Everything else in the 116 has a source.
5. **News as cells**: `news_url/news_title/news_time` are filled (3,658 / 4,217) but are prose/URLs;
   they belong in a detail panel, not a grid cell.

### 4.3 Fields no human wants in a 100-row table (detail-panel-only)

| Key | Why it fails the scan test |
|---|---|
| `website` | a URL in a 120 px cell; unreadable and unclickable at density |
| `news_url`, `news_title`, `news_time`, `daily_digest` | prose/URLs; force multi-line rows and destroy the row rhythm |
| `sort_order`, `row_type` | transport bookkeeping, not facts about a company |
| `week_52_range` | an object `{low, high}` that duplicates `high_52w` + `low_52w` |
| `candlestick_pattern` | numeric pattern code with no legend in the grid |
| `employees` | a headcount nobody ranks on; belongs beside "IPO date" in a profile block |
| `dividend_ex_date`, `earnings_date` | dates that matter when filtering, not when scanning |
| `country`, `exchange` | useful as a filter facet, redundant once the row shows the ticker |

That is 12 keys (10 %) that should never be a default column; `week_52_range` and the two transport
keys should not be columns at all.

---

## 5. API gaps a real interface needs — ranked by how much they block

Ranked by "can a usable screener ship without it": G1 blocks the core interaction, G5 does not block
at all (it already works one symbol at a time).

| # | Gap | What is missing, precisely | What already exists that a small addition would serve | Blocks |
|---|---|---|---|---|
| **G1** | **Rows beyond 20** | `/screens/select` clamps `limit` to 20 (`selection.py:348`) and `/screens` deletes it (`screens.py:156`). There is no `limit` at all in `symbols` mode (`:674`). The **only** way to see row 21–100 is `offset`, which re-runs a full scan per page (measured 14.03 s and 2 duplicate symbols for 5 pages, §6.3). | `app/engine/screener.py` already serves **1..500 rows** (`MAX_LIMIT = 500`, `:106`) over 10,433 symbols with `offset` — measured 100 rows in **1.76 s** (§6.4). What it lacks is a projection: it returns 25 cells/row wrapped in `{value, unit, provenance}` (162 KB for 100 rows). A `columns`-style projection on `/engine/screen`, or raising `DISPLAY_LIMIT` behind a flag, closes it. | Everything: 100-row table, scroll performance, real paging |
| **G2** | **Market-wide breadth** | No endpoint and no field. The bar is computed in the browser over ≤20 rows (`app.js:716-727`). `total` and `universe_total` exist but say nothing about direction. | The full universe is already in memory inside `select()` — `universe` at `screens.py:342` is 5,600 rows each carrying `change_pct`. Two integers (`advancing`, `declining`) computed in the same pass as `total` would answer it with no new I/O. | The header/breadth card, any "market pulse" |
| **G3** | **Sector performance** | No aggregation endpoint. You can get a *count* per sector (`{"key":"sec","op":"eq"}` → `total`), and you can page rows, but nothing returns per-sector median/mean return or advance/decline. | Same in-memory universe (`screens.py:342`) has `sector` (19 Finviz labels, measured 5,532/5,600) and every `ret_*`. A `group_by: "sector"` block on the selection response is a `defaultdict` over rows already loaded. **Caveat:** the depth store's `sector` uses a different 13-value vocabulary (§3.5) — the aggregate must use the row's, not the store's. | Sector heatmap / rotation view |
| **G4** | **Symbol search / autocomplete** | No search endpoint. `/engine/security/{symbol}` is exact-match only (`engine.py:205`). `/markets/resolve` is not a symbol lookup (it validates a market document: `{market, as_of}`, not `{query}`). | `app/engine/screener.py` has a `contains` text operator (`screener.py:113`) over `ticker`/`company` on 10,433 rows — `{"filters":[{"field":"ticker","op":"contains","value":"appl"}]}` is a working substring search today, but it is a full scan and unranked. A prefix index (or `startswith`) plus a `limit:10` would make it an autocomplete. | ⌘K / search box |
| **G5** | **Per-symbol news and insider trades in the row/detail payload** | Neither is batched: news is one symbol per call (`/news/{symbol}?limit=6`, `app.js:824`), insider is one symbol per call (`/engine/insider/{symbol}?limit=1..20`, `engine.py:250-320`, measured 7,222 B / 0.87 s). Nothing in `/screens/select` returns either for the page of 20. | Both **already exist as endpoints**; the catalogue also carries `news_url/news_title/news_time` (3,658/4,217 filled) and `insider_trans` (4,139/4,217) as columns. The gap is a batch form (`?symbols=` on both) or a detail-view fetch on row select — the latter needs no backend work at all. | Detail panel only; not a blocker |
| — | *(context)* **`columns` null-collapse** | `ps`, `short_float`, `enterprise_value`, `cash_per_share` return null despite data (§4.2) | fix the overlay order / the unconditional reassignment | Any view that shows valuation or short interest |

**Ordering rationale:** G1 is the difference between a demo and a screener; G2/G3 are the difference
between a table and a cockpit; G4 removes the last modal; G5 already works per-symbol and is a UX
decision, not an API build.

---

## 6. Measurements

All wall times are `curl -w '%{time_total}'` or `urllib` against `127.0.0.1:8099`, sequential, no
concurrency, on the same host as the server. The scan is re-run per selection, so times are noisy
(±40 %); medians of 3 runs are given.

### 6.1 Page weight

| Asset | Bytes | gzip |
|---|---:|---:|
| `ui/index.html` | 18,233 | 4,305 |
| `ui/app.js` | 44,525 | 11,942 |
| `ui/styles.css` | 41,349 | 8,239 |
| **Total** | **104,107 (101.7 KiB)** | **24,486 (23.9 KiB)** |

Plus 20 logo requests per page (`/ui/logos/*.png`, 156 KB for the whole directory).

### 6.2 Response sizes and wall time (20 rows = the cap)

| Request | Bytes | Wall (median of 3) | Notes |
|---|---:|---|---|
| `POST /screens/select`, no `columns`/`view` (light row) | 13,169 | 2.54 s (1.71–2.82) | 15 keys/row, **heavier than 12 columns** |
| `POST /screens/select`, `columns` = 12 mixed price/valuation/technical | 12,211 | 1.66 s (1.64–2.80) | 592 B/row |
| `POST /screens/select`, `view: "all"` (what the UI loads by default) | 41,497 | 2.57 s (1.75–2.74) | 2,062 B/row, 76 keys/row |
| `POST /screens/select`, `view: "all"`, `fill_wide: true` | 42,855 | 2.97 s (2.89–3.04) | the slowest path, and it is the UI default |
| `GET /screens?source=tradingview` | 9,664 | 1.14 s | 20 rows, `total: 3896` |
| `POST /screens/select`, `limit: 100` | 12,213 | 2.53 s | **still 20 rows** — clamped, `limit` echoes 20 |

### 6.3 What a 100-row × 12-column response costs today

There is no single call that returns 100 rows, so it was assembled from five real 20-row pages
(`offset` 0/20/40/60/80, identical columns):

| Metric | Value |
|---|---:|
| bytes on the wire (5 pages, 5 envelopes) | **84,572** |
| bytes as one compact document (100 rows, 1 envelope) | **83,096** |
| bytes pretty-printed as one document | 86,135 |
| per-row average | **831 B** |
| wall time for 100 rows (5 sequential calls) | **14.03 s** |
| duplicate symbols across pages | **2** (`JPM/PD`, `JPM/PJ` appeared twice) — offset paging is not a stable snapshot |

Per-page sizes grow with depth (12,211 → 15,375 → 19,352 → 17,998 → 19,636 B) because deeper rows
carry fewer nulls. A single 100-row call at the same density would be **≈83 KB** and **≈2 s** — the
scan cost does not grow with `limit` (the full match set is already scanned and ranked,
`selection.py:791-794`), only serialisation and transfer do.

### 6.4 The alternative endpoint, measured

| Request | Bytes | Wall | Shape |
|---|---:|---|---|
| `POST /engine/screen` `limit:20` | 29,279 | 1.93 s | 25 keys/row, each `{value, unit, provenance{source, as_of, status, url, inputs}}` |
| `POST /engine/screen` `limit:100` | **162,517** | **1.76 s** | the same 25 keys, 100 rows — **already serves 100 rows** |
| `GET /engine/catalog` | 12,739 | 2 ms | all 116 keys + unit + `views` + `derivation` |
| `GET /engine/insider/AAPL` | 7,222 | 0.87 s | 17 Form-4 rows |

So a 100-row view is **already possible** through `/engine/screen`, at 1,625 B/row because of the
per-cell provenance envelope (2.7× the `select` density). A UI that wants 100 rows either accepts
provenance-laden rows or needs a projection.

---

## 7. Adversarial checks

**(a) Is the 20-row cap enforced in the API or only in the UI?**
Both, and the API is authoritative. `_limit()` clamps (`selection.py:339-348`), the browse route
discards `limit` (`screens.py:156`), `symbols` mode hard-codes 20 (`:674`). The UI's
`state.perPage = 20` (`app.js:103`) is a mirror, not a control.

*Does raising it break an invariant?*
- **Payload:** no structural break — 831 B/row average measured, so 100 rows ≈ 83 KB, 500 ≈ 400 KB.
- **Sorting cost:** none — `rank_rows` already runs over every match before the cut
  (`selection.py:791-794`), and `fetch_scan` already requests `FULL_SCAN_ROWS = 100_000` (`:276`).
  The 1.6–2.9 s is scan, not serialisation.
- **ETag stability:** nothing to break today (no validator), but it *would* break a naive one: `as_of`
  is per-call microsecond time (`screens.py:341`), so a body hash never repeats. A revision-keyed tag
  (the `cache.py` pattern) is required.
- **What it does break:** `DISPLAY_LIMIT = DISPLAY_ROWS` is a shared constant (`selection.py:100`) —
  raising it raises the browse display too, which is capped for a different reason (snapshot size).
  Tests pin the clamp. And `_resolved()` truncates symbols at the same limit (`:825`), so a 25-name
  watchlist already loses 5 rows silently today.

**(b) Can `columns` express every view we would need?**
No, in four ways: the 40-column ceiling is below `all` (71); `columns` is mutually exclusive with
`view`; `columns` is ignored in `symbols` mode (§2.7); and four keys return null despite data (§4.1).
Fields the depth store does not fill at all: `change, open, high, low, daily_digest, sort_order,
row_type`. Everything else in the 116 has a source.

**(c) ETag changes on data, not on clock — how proved.**
`screens/select` has none (§2.6). On `/market/depth`: two polls 4 s apart → byte-identical tag
(`depth-r20073-30d63ce5c418bf4f`); replaying it as `If-None-Match` → `304` with `content-length: 0`;
a different symbol list → different digest; `etag_for(..., revision+1, ...)` → different tag. The
clock-invariance comes from `as_of` being the collector's cycle stamp (`depth_source.py`, "the
collector's stamp"), not `now()`. The caveat is that `etag_for` *does* digest `as_of`
(`cache.py:32-41`), so any route whose `as_of` is a live quote time (e.g. `/market/hub`, `as_of =
max(ts_exchange)`) will re-send identical values whenever a new tick prints.

**(d) Fields no human wants in a 100-row table.** §4.3 — 12 candidates; the clear ones are `website`,
`news_url/title/time`, `daily_digest`, `sort_order`, `row_type`, `week_52_range`,
`candlestick_pattern`.

---

## 8. Method (reproduce every count)

### 8.1 The counting snippet (used for §3.1 and §3.2)

```python
# .venv/bin/python  (cwd = repo root)
from app.screen import views as V
from app.engine.catalog import CATALOG, CATALOG_BY_KEY
from app.market.fields import METRIC_BY_KEY

cat = [s.key for s in CATALOG]
refs = sorted({k for v in V.VIEWS for k in v.columns})
print(len(cat), len(METRIC_BY_KEY), len(refs))                      # 116 71 71
print(len([k for k in refs if k in CATALOG_BY_KEY]))                # 48
print([k for k in refs if k not in CATALOG_BY_KEY])                 # 23 phantoms
print(len([k for k in cat if k not in set(refs)]))                  # 68 unassigned
```

### 8.2 Depth fill (§3.2, §4.1)

```python
import json, collections
from app.engine.catalog import CATALOG_BY_KEY
d = json.load(open("/home/ubuntu/ztockz-data/depth.json"))          # 11,473,979 B, 4,217 symbols
filled = collections.Counter()
for cells in d["symbols"].values():
    for k, v in cells.items():
        if k in CATALOG_BY_KEY and v is not None:
            filled[k] += 1
# 106 of 116 keys non-zero; absent: price/change/volume/prev_close/open/high/low/daily_digest/sort_order/row_type
```

### 8.3 HTTP measurements (§6)

```bash
COLS='["price","change_pct","volume","market_cap","pe","forward_pe","eps","sales","roe","rsi","beta","dividend_yield"]'
curl -s -o /tmp/b.json -w '%{size_download} %{time_total}\n' -X POST http://127.0.0.1:8099/screens/select \
  -H 'content-type: application/json' \
  -d "{\"mode\":\"criteria\",\"region\":\"us\",\"criteria\":{\"filters\":[],\"sort\":\"market_cap\",\"limit\":20},\"columns\":$COLS}"
```

The 100-row figure assembles five such calls at `offset` 0/20/40/60/80 and re-serialises the
concatenated `rows` (§6.3). The engine comparison is
`POST /engine/screen {"filters":[],"sort":{"field":"market_cap","descending":true},"limit":100,"offset":0,"refresh_quotes":false}`.

---

## 9. What this means for the plan (one paragraph each, no new claims)

1. **Build on `POST /screens/select` for the first screen, not on `/screens`.** The browse route is a
   fixed 20-row snapshot with no filter language; select already returns `total` vs `returned` and
   echoes the compiled filters, which is the honesty the product claims.
2. **Ship ~14 columns by default, not 74.** 74 × 20 cells of `<innerHTML>` is what the current UI
   does; the catalogue's own `views` metadata and the measured fill rates support a 12–14 column
   default with the rest behind "All columns".
3. **Treat `columns` as the contract and `view` as a preset.** `columns` gives the client the exact
   cells, in order, and the response echoes them — but it needs the 40-cap raised (or two calls) and
   the four null-collapse bugs fixed before a valuation view can be trusted.
4. **Do not rebuild the breadth bar or the sector block until G2/G3 exist** — both are computable
   from data the server already holds, and both are currently fabricated from ≤20 rows.
5. **Keep the provenance surface.** `as_of` per row, `--` for unknown, `total` vs `returned`,
   `universe_total`, and the export of exactly what is displayed are the parts of this UI worth
   porting verbatim.
