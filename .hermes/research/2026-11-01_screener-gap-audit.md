# Screener gap audit — what exists today vs Finviz

2026-11-01. Method: read `app/api/screens.py`, `app/screen/{selection,filters,filter_fields,views,rank}.py`,
`app/api/{signals,exchanges}.py`, `web/src/{screener,grid,filters}/**`, then drove the route via
`fastapi.testclient` with the production depth store
(`MEE_DEPTH_STORE_PATH=/home/ubuntu/ztockz-data/depth.json`). Port 8099 / `ztockz.service`
untouched. Finviz's signals read from its own bundle (`assets/dist/1498.v1.f35b5a27.js`). Live:
5,715 provider rows → **5,576 universe rows**, matching `app/api/exchanges.py:14-19`.

## 1. What `POST /screens/select` accepts

**No text grammar.** Each filter is one JSON object with **exactly one of** `key` (Finviz key) or
`field` (row column), plus `op`, `value` (`app/screen/selection.py:493-502`). The `field op value`
regex at `app/screen/filters.py:323-329` (`_EXPRESSION = r"^\s*(?P<field>[A-Za-z_][A-Za-z0-9_]*)\s*(?:(?P<symbol><=|>=|==|!=|=|<|>)|(?P<name>contains|between|gte|lte|eq|ne|gt|lt|in)\b)\s*(?P<value>.*?)\s*$"`)
is **unreachable from this endpoint**: `_criterion` only passes dicts to `compile_filter`
(`selection.py:537-541`), and a string filter is refused "must be an object" (`selection.py:489-490`).

Operators — closed set of nine (`filters.py:266-276`):

```python
NUMERIC_OPS = frozenset({"gt","gte","lt","lte","between"})
TEXT_OPS    = frozenset({"contains"})
OPERATORS   = frozenset({"eq","ne","gt","gte","lt","lte","in","between","contains"})
```

`eq/ne/in` work on every kind; the five numeric ops require `kind=="number"`, `contains` requires
`kind=="text"` (`filters.py:677-683`). No `or`, nesting, `not` or arithmetic — every criterion is
ANDed (`selection.py:868-883`).

Fields — **not every catalog column.** `FIELDS` = 74 row columns (`filters.py:144-264`); 133
catalogue keys exist but only **78 resolve** to a filterable field (`_alias_column`,
`selection.py:606-624`); **55 cannot be filtered at all**, incl. `index_membership`, `optionable`,
`shortable`, `employees`, `eps`, `net_income`, `sales`, `dividend`, `prev_close`, all dividend
growth, `insider_trans`, `inst_trans`, `short_ratio/interest`, `high_50d`, `low_50d`,
`all_time_high/low`. 39 of the 74 names are internal spellings no catalogue key uses
(`market_cap_usd`, `pe_ttm`, `ret_1m`). Of 82 Finviz keys, **50 are live** (47 direct + 3 choice:
`ta_perf`, `ta_perf2`, `ta_volatility`), 32 refused by name (`filter_fields.py:145-215, 237-239`).

Errors — verified, envelope `{error, errorMessage}` (`app/auth.py:24-31`, `app/main.py:63`):

| request | status / `error` | body |
|---|---|---|
| `{"field":"nosuch","op":"gt","value":1}` | 422 `invalid_filter` | `criteria.filters[0]: unknown filter field 'nosuch'; available fields: symbol, name, exchange, …` (all 74) |
| `{"field":"price","op":"approx","value":1}` | 422 `invalid_filter` | `unknown filter operator 'approx'; available operators: between, contains, eq, gt, gte, in, lt, lte, ne` |
| `{"key":"idx","op":"eq","value":"sp500"}` | 422 `unavailable_filter_key` | `filter key 'idx' (Index) cannot be applied: it needs index membership…` |
| `{"field":"exchange","op":"eq","value":"NOPE"}` | 422 `unknown_value` | `exchange value 'NOPE' is not a venue in region 'us'; allowed: XASE, XCBO, XNAS, XNYS…` |

Unknown knobs are refused, never ignored (`_reject_extra`, `selection.py:346-357`); top-level
`sort` earns a hint (`_HINTS`, `:215-219`). This is the strongest part of the module.

## 2. Views and the column cap

Served catalogue (`views.py:124-134, 358`): `today, value, quality, growth, momentum, trend,
ownership, income, profile, all`. Five **legacy** names are still accepted though absent from the
schema's `views` (`views.py:626`): `overview, valuation, performance, financial, technical` —
`POST {"view":"financial"}` → **200** with 28 columns (verified).

Columns sent: today 11 · value 13 · quality 12 · growth 12 · momentum 10 · trend 12 ·
ownership 10 · income 8 · profile 12 · **all 116**; legacy overview 27, valuation 11,
performance 11, financial 28, technical 13.

Caps: `COLUMN_LIMIT = 40` (`selection.py:189`), mirrored `MAX_COLUMNS = 40`
(`web/src/data/screens.ts:30`); rows per panel `PANEL_LIMIT = 100` (`selection.py:158`) while the
UI asks **20** (`web/src/data/screens.ts:29`, `ScreenPanel.tsx:219`). `view` xor `columns`
(`selection.py:673-677`). `all` = 3 chunked requests of ≤40 (`web/src/grid/useAllColumns.ts:4-5`).

## 3. Sorting

Server-side, **3 values, one direction**. `SORTS = ("market_cap","turnover","gainers")`
(`rank.py:26`), `SORT_METRICS = {"market_cap":"market_cap_usd","turnover":"turnover","gainers":"change_pct"}`
(`rank.py:29-33`). `_sort` refuses the rest: `unknown sort 'pe'; expected one of market_cap,
turnover, gainers` (`selection.py:377-384`). No direction parameter exists; every sort is
descending with symbol-ascending ties and nulls last (`rank.py:57-99`). `GET /screens?sort=` shares
the same three (`screens.py:168`).

The UI sorts **server-side, toolbar only**: three `aria-pressed` buttons
(`ScreenPanel.tsx:502-534`). **No header is clickable, no `aria-sort`** — stated at
`web/src/grid/DataTable.tsx:433-443`: *"every column is `enableSorting: false`, the toolbar owns the
ordering"*. Sorting by P/E is impossible by construction.

## 4. What the UI renders for filters

Not a text box, not a rail of selects: **left drawer + chip bar + two toolbar menus**.

- `web/src/screener/ScreenerScreen.tsx:231-284` — one `<ScreenPanel>`; filter props are
  `filters`, `registry`, `onRemoveFilter`, `onClearFilters`, `onOpenFilters`, `filterNotice`,
  `sectorOptions`, `onSectorChange`, `exchangeOptions`, `onExchangeChange`.
- `web/src/filters/FilterDrawer.tsx:226-235` modal; **preset bucket rows** (Any/Mega/Micro…,
  `:171-216`) + row builder `ConditionBuilder` (`:276-282`) + "On screen" list.
- `web/src/filters/ConditionBuilder.tsx:183-206` — `<select>` of usable keys, op `<select>`, value
  input, and/any connector; a run of ORs compiles to one `in` (`compiler.ts`), rest ANDed.
- Chips `web/src/filters/ChipBar.tsx:19-26` (`filters`, `onRemove`, `onClear`, `notice`).
- Sector/exchange each write one predicate into the same array (`ScreenPanel.tsx:484-500`), chips
  drawn separately (`:550-564`); ⌘K palette (`ScreenerScreen.tsx:105-116`).
- State: one `ActiveFilter{id,key,op,value}[]` in `useScreenState` (`useScreenState.ts:47-171`),
  mirrored to `?view&sort&page&f` (`urlState.ts:61-63`); the wire body is built once in
  `requestBody` (`web/src/data/screens.ts:156-169`).
- **32 of 82 keys never appear in the UI**: refused keys are dropped from the registry
  (`schema.ts:181-201`, `registry.ts:16-20`) and `refused` is rendered nowhere.

## 5. Signals — 13 groups, 9 alive, vs Finviz's 27

`app/api/signals.py:416-530`: gainers, losers, new_high, new_low, overbought, oversold,
unusual_volume, most_active, most_volatile (**9 available**, rules at `:303-398`); then
`insider_buying` (`:490-499`) and `insider_selling` (`:500-509`) `available=False`,
reason `insider flow needs filings, not a vendor field`; `upgrades` (`:510-519`) and `downgrades`
(`:520-529`) `available=False`, reason `no analyst ratings source`.

It is **not a screener feature**: `GET /market/signals` is a read-only home-page wall, each table
capped at `per` 5–50 rows (`signals.py:155-157`), with no way to filter the grid by a signal.
Finviz's Signal is a screener predicate (`&s=ta_topgainers`).

Finviz's 27 (its bundle has 35 entries: minus 2 Elite-only after-hours, minus 6 generic
duplicates — TL Resistance/Support, Wedge, Channel, Multiple Top/Bottom):

- **Working (9):** Top Gainers, Top Losers, New High, New Low, Most Volatile, Most Active,
  Unusual Volume, Overbought, Oversold.
- **Id present, disabled (4):** Upgrades, Downgrades, Insider Buying, Insider Selling.
- **Absent (14):** Earnings Before, Earnings After, Major News, Horizontal S/R, Wedge Up,
  Wedge Down, Triangle Ascending, Triangle Descending, Channel Up, Channel Down, Double Top,
  Double Bottom, Head & Shoulders, Head & Shoulders Inverse.

Earnings Before/After are nearly free — `earnings_date` is on 4,626/5,576 rows (measured); only the
pre/post-open flag is missing.

## 6. Exchanges and indices

`filters.py:287-313`: on `us`/`us_etf` the closed set `("XASE","XCBO","XNAS","XNYS")` + aliases
`nyse, nasdaq, nasd, amex, cboe`; elsewhere a provider code matching `^[A-Z0-9]{1,8}$`
(`PROVIDER_CODE`, `:318`). Measured today (`GET /market/exchanges` + a direct scan):

| code | label | rows | share |
|---|---|---|---|
| XNAS | NASDAQ | 3,308 | 59.3% |
| XNYS | NYSE | 2,009 | 36.0% |
| XASE | NYSE American | 258 | 4.6% |
| XCBO | Cboe BZX | 1 | 0.02% |
| — | **total** | **5,576** | |

`global` carries provider tokens (`LSE` → 1,389 rows measured), 90 distinct codes
(`research/exchange-contract.md:105`).

**No index filter exists.** `idx` is catalogued (`filters.py:823-830`, tokens `sp500/ndx/dji/rut`)
and refused at `filter_fields.py:145` ("the scan has no index-constituent column"); it is absent
from the schema's usable keys, so the UI shows no Index control at all. But the data is there:
`index_membership` is a catalogue column (`columns.py:186`) filled by the depth overlay that
`POST /screens/select` runs before filtering (`screens.py:1227-1228`). Measured on today's 5,576
rows: **2,734 carry it (49.0%)** — `Russell 2000` 1,865 · `S&P 600` 586 · `S&P 500` 477 ·
`S&P 400` 390 · `NDX` 89 · `DJIA` 30 · `RUT` 9. Two data traps for any future filter: Russell 2000
is split across two spellings (`Russell 2000` + `RUT`), and `S&P 500` holds 477 rows because the
store covers 4,217 of 5,576 symbols (`depth.json: count=4217`). Filtering it fails twice:
`{"key":"idx"}` → 422 `unavailable_filter_key`; `{"field":"index_membership"}` → 422 unknown field.

## 7. Pagination, export, alerts, compare, presets

| feature | state | evidence |
|---|---|---|
| Pagination | ✅ offset/limit, Prev/Next only | `selection.py:421-430, 882-883`; `ScreenPanel.tsx:640-685`; no page-size control, no jump |
| CSV export | ⚠️ server-only, unreachable | `POST /screens/{id}/export` → 202 job (`screens.py:1299-1348`) exports a **stored snapshot id**, not a selection; nothing calls it — `web/src/grid/export.test.ts:1-21`: *"There is **no** client-side export in this app"* |
| Alerts | ❌ | no route (`grep '@router' app/api/*.py \| grep -i alert` → 0); `alert` in `web/src` is `role="alert"` only |
| Compare | ❌ | none; `compare` hits are string comparators (`alt/alt.ts:197`) |
| Presets (saved screens) | ❌ | `web/src/filters/presets.ts` is **numeric buckets** ("under $2B"); screen state lives in the URL only (`useScreenState.ts:68-73`) |

## 8. Where an unavailable filter silently returns an empty set

The contract is "refuse, never no-op" (`filter_fields.py:9-15`). Four holes:

1. **`listing_date` — the `field` form bypasses the refusal.** `ipodate` is refused because
   `ipo_date` "answered null for all 5,720 US rows"; the same column is filterable by name
   (`filters.py:230`) and is null on every row: `{"field":"listing_date","op":"lte","value":9.9e9}`
   → **200, total 0** (verified). Cause: `universe.py:268-271` lists
   `{"pe_forward","ps","short_float","listing_date"}` as scan-null columns, but
   `depth_overlay.py:101-112` (`SCAN_NULL_CELLS`) kept only three — so the depth store's real IPO
   date (4,217/4,217 symbols, e.g. `'1999-11-18'`) can never fill the row.
2. **A well-shaped venue that does not exist.** `{"key":"exch","op":"eq","value":"ZZZZ"}` on
   `global` → **200, total 0**; `PROVIDER_CODE` checks only the shape (`filters.py:315-318`).
3. **A field that only exists on another tab.** `{"field":"aum_usd","op":"gte","value":0}` on the
   stock tab → **200, total 0**; nothing says `aum_usd`/`is_primary` are ETF cells.
4. **A null cell is "no match", everywhere** (`filters.py:733-735`). With P/E on ~57% of rows,
   `fa_pe lte 5` silently drops 2,417 unmeasured rows.

Distinct from these: six **stale refusal reasons**. The depth overlay landed after they were
written, so the message claims a missing column the filtered row now carries (measured fill on
5,576 rows):

| key | refusal says | row actually has |
|---|---|---|
| `sh_opt` (`:168`) | "no optionable/shortable column" | `optionable`/`shortable` 74.9% |
| `sh_insidertrans`, `sh_insttrans` (`:199`) | "no transaction column" | `insider_trans` 73.9%, `inst_trans` 74.6% (quality disputed, `signals.py:62-67`) |
| `ta_highlow20d/50d` (`:204`) | "52-week extremes only" | `high_50d`/`low_50d` 75.1% |
| `ta_alltime` (`:209`) | "no all-time high/low column" | `all_time_high` 75.1% |
| `idx` (`:145`) | "no index-constituent column" | `index_membership` 49.0% |

## Ranked gaps

1. **Index filter** — data on 49% of rows, only the key is missing.
   `app/screen/filters.py` (`FIELDS`), `app/screen/filter_fields.py:145`.
2. **Sort by any column, with direction** — three hardcoded descending sorts.
   `app/screen/rank.py:26-33`, `app/screen/selection.py:377-384`.
3. **Clickable headers / `aria-sort`** (today explicitly disabled).
   `web/src/grid/DataTable.tsx:433-443`.
4. **`listing_date` silent zero** — one entry in a set makes Finviz's IPO-date filter work.
   `app/screen/depth_overlay.py:101-112`.
5. **Refresh the six stale refusals** (`sh_opt`, insider/institutional transactions, 20/50-day,
   all-time, idx): wire them or stop claiming the column is absent.
   `app/screen/filter_fields.py:145-215`.
6. **Signal-as-filter** — 4 disabled, 14 missing, and no way to screen *by* a signal.
   `app/api/signals.py:416-530`, `app/screen/filters.py`.
7. **Earnings Before/After** — `earnings_date` on 4,626 rows; only the session flag is absent.
   `app/api/signals.py`, `app/screen/universe.py`.
8. **Export from the screener** — pipeline exists, but only for a stored screen id.
   `app/api/screens.py:1299`, `web/src/grid/ScreenPanel.tsx:450-543`.
9. **Saved presets** — no persistence beyond the URL. New `screen_presets` table
   (`app/screen/repository.py`) + `web/src/filters/`.
10. **Page size + range control** — fixed 20 rows; server already serves 100.
    `web/src/data/screens.ts:29`, `web/src/grid/ScreenPanel.tsx:640-685`.

Honorable mentions: text-query box (grammar exists, unreachable — `filters.py:323-329` vs
`selection.py:489-490`); OR/nesting beyond one `in` run; theme/sub-theme and Finviz's new
"Dilution Risk" key (absent from our 82); compare; alerts.
