# Phase 7 — parity proof: the old cockpit → the new surface

**Question this answers.** The old UI (`/ui`, vanilla JS) was replaced by the new app
(`/app`, `web/dist`). Did anything the old UI could **show** or **filter** disappear?

**Answer.** One column cell and one ordering control are gone; one surviving column reads a
different window and says so (G4). Everything else is reachable, most of it under a new
name and in a new view. The single column is a Finviz composite the catalogue deliberately
split in two (both halves are visible); the ordering control is a narrowing (three
server-side sorts, no client-side re-sort of the visible page). Zero old column keys are
unclassified.

Measured at `349b768` (HEAD) against the live process on `127.0.0.1:8099`, read-only:
`GET /screens`, `POST /screens/select`, `GET /logos/{symbol}`, `GET /screens/select/schema`.

---

## 1. Counts

| measure | count |
|---|---:|
| column keys declared in `ui/app.js` `COLUMN_META` | **72** |
| distinct keys renderable across the six tabs (`All columns` is the derived union) | 71 |
| declared but in no tab (`market_cap_usd` — a defensive duplicate of `market_cap`) | 1 |
| **mapped** — old key is already a catalogue key | **48** |
| **mapped** — old key resolves through the catalogue table to a catalogue key | **23** |
| — of those 23, a single-cell alias listed in `views.LEGACY_COLUMN_ALIASES` | 22 |
| — of those 23, `market_cap_usd`: a row column sharing `market_cap`'s field | 1 |
| reachable in a **named** canonical view | 69 |
| reachable **only** in the All-columns destination | 2 (`eps_next_5y`, `sales_past_5y`) |
| **gaps** — old key names no single row cell | **1** (`option_short`, the 23rd `LEGACY_COLUMN_ALIASES` entry: a documented composite, not a rename) |
| **unclassified** | **0** |
| old filter controls enumerated | 17 |
| filter controls reachable | 16 (one by a different mechanism) |
| filter controls lost | 1 (per-column sort) |
| old advanced-filter keys rejected by the registry | 0 of 5 |
| logo samples | 30, all `200` + `image/*` |

The new surface being mapped onto: 116 catalogue keys (`app/screen/columns.py`), nine
views + `all` (`app/screen/views.py`), six documented absences (`ABSENT_COLUMNS`), 23
legacy aliases, 82 Finviz filter keys of which **50** are live (47 column-backed + 3
choice), 105 filter aliases, and three sorts (`app/screen/rank.py`).

Method note, because it decides how much the proof is worth: `ui/app.js` builds its
column list **statically** — `COLUMN_META` (line 8) and `SCREEN_VIEWS` (line 83) are
object literals in the file; nothing about the column set is fetched from an API
response. The only dynamically assembled list is `SCREEN_VIEWS.all` (line 91), which is
the deduped union of the six named tabs. So parsing the source *is* the whole surface.

---

## 2. Table 1 — columns (72 rows, one per old key)

`tab(s)` = the old tabs that drew the key. `new name` = the catalogue key(s) that answer
it now. `home` = where the new app renders it.

Verdicts: **reachable** (same key, still a column) · **renamed** (old name → catalogue
key, alias recorded in `app/screen/columns.py`) · **absent** (no cell).

| # | old key | label | tab(s) | new name | home | verdict |
|---:|---|---|---|---|---|---|
| 1 | `price` | Price | overview, technical | `price` | today | **reachable** |
| 2 | `prev_close` | Prev close | technical | `prev_close` | today | **reachable** |
| 3 | `change_pct` | 1D | overview, technical | `change` | today | **renamed** |
| 4 | `volume` | Volume | technical | `volume` | today | **reachable** |
| 5 | `avg_volume` | Avg vol | technical | `average_volume` | today | **renamed** |
| 6 | `market_cap` | Mkt cap | overview, valuation, technical | `market_cap` | value | **reachable** |
| 7 | `market_cap_usd` | Mkt cap | — | `market_cap` | value | **renamed** |
| 8 | `enterprise_value` | EV | valuation | `enterprise_value` | value | **reachable** |
| 9 | `pe` | P/E | overview, valuation | `pe` | value | **reachable** |
| 10 | `forward_pe` | Fwd P/E | overview, valuation | `forward_pe` | value | **reachable** |
| 11 | `peg` | PEG | valuation | `peg` | value | **reachable** |
| 12 | `ps` | P/S | valuation | `ps` | value | **reachable** |
| 13 | `pb` | P/B | valuation | `pb` | value | **reachable** |
| 14 | `pc` | P/C | valuation | `pc` | value | **reachable** |
| 15 | `pfcf` | P/FCF | valuation | `pfcf` | value | **reachable** |
| 16 | `ev_ebitda` | EV/EBITDA | valuation | `ev_ebitda` | value | **reachable** |
| 17 | `ev_sales` | EV/Sales | valuation | `ev_sales` | value | **reachable** |
| 18 | `eps_ttm` | EPS ttm | financial | `eps` | growth | **renamed** |
| 19 | `eps_next_y` | EPS next Y | financial | `eps_next_y` | growth | **reachable** |
| 20 | `rsi_14` | RSI | overview, technical | `rsi` | momentum | **renamed** |
| 21 | `rel_volume` | Rel vol | technical | `relative_volume` | today | **renamed** |
| 22 | `atr_14` | ATR | technical | `atr` | trend | **renamed** |
| 23 | `beta` | Beta | technical | `beta` | trend | **reachable** |
| 24 | `perf_week` | 1W | performance | `perf_1w` | momentum | **renamed** |
| 25 | `perf_month` | 1M | performance | `perf_4w` | momentum | **renamed** |
| 26 | `perf_quarter` | 1Q | performance | `perf_13w` | momentum | **renamed** |
| 27 | `perf_half_y` | 6M | performance | `perf_26w` | momentum | **renamed** |
| 28 | `perf_ytd` | YTD | overview, performance | `perf_ytd` | momentum | **reachable** |
| 29 | `perf_year` | 1Y | performance | `perf_52w` | momentum | **renamed** |
| 30 | `perf_3y` | 3Y | performance | `perf_3y` | momentum | **reachable** |
| 31 | `perf_5y` | 5Y | performance | `perf_5y` | momentum | **reachable** |
| 32 | `perf_10y` | 10Y | performance | `perf_10y` | momentum | **reachable** |
| 33 | `week52_high` | 52W high | performance | `high_52w` | trend | **renamed** |
| 34 | `week52_low` | 52W low | performance | `low_52w` | trend | **renamed** |
| 35 | `insider_own` | Insider own | ownership | `insider_own` | ownership | **reachable** |
| 36 | `insider_trans` | Insider trans | ownership | `insider_trans` | ownership | **reachable** |
| 37 | `inst_own` | Inst own | ownership | `inst_own` | ownership | **reachable** |
| 38 | `inst_trans` | Inst trans | ownership | `inst_trans` | ownership | **reachable** |
| 39 | `shares_outstanding` | Shares out | ownership | `shares_outstanding` | ownership | **reachable** |
| 40 | `shares_float` | Float | ownership | `shares_float` | ownership | **reachable** |
| 41 | `option_short` | Shortable | ownership | `optionable` + `shortable` | — (Profile carries the two flags) | **absent** |
| 42 | `income` | Income | financial | `net_income` | growth | **renamed** |
| 43 | `sales` | Sales | financial | `sales` | growth | **reachable** |
| 44 | `book_per_share` | Book/sh | financial | `book_per_share` | quality | **reachable** |
| 45 | `cash_per_share` | Cash/sh | financial | `cash_per_share` | quality | **reachable** |
| 46 | `eps_next_q` | EPS next Q | financial | `eps_next_q` | growth | **reachable** |
| 47 | `eps_this_y` | EPS this Y | financial | `eps_this_y` | growth | **reachable** |
| 48 | `eps_next_y_growth` | EPS next Y | financial | `eps_next_y` | growth | **renamed** |
| 49 | `eps_next_5y` | EPS next 5Y | financial | `eps_next_5y` | All columns only | **reachable (All columns only)** |
| 50 | `eps_past_3_5y` | EPS past 3/5Y | financial | `eps_past_5y` | growth | **renamed** |
| 51 | `sales_past_3_5y` | Sales past 3/5Y | financial | `sales_past_5y` | All columns only | **renamed (All columns only)** |
| 52 | `eps_yoy_ttm` | EPS YOY ttm | financial | `eps_yoy_ttm` | growth | **reachable** |
| 53 | `sales_yoy_ttm` | Sales YOY ttm | financial | `sales_yoy_ttm` | growth | **reachable** |
| 54 | `eps_qoq` | EPS Q/Q | financial | `eps_qoq` | growth | **reachable** |
| 55 | `sales_qoq` | Sales Q/Q | financial | `sales_qoq` | growth | **reachable** |
| 56 | `roa` | ROA | financial | `roa` | quality | **reachable** |
| 57 | `roe` | ROE | financial | `roe` | quality | **reachable** |
| 58 | `roic` | ROIC | financial | `roic` | quality | **reachable** |
| 59 | `gross_margin` | Gross margin | financial | `gross_margin` | quality | **reachable** |
| 60 | `oper_margin` | Oper margin | financial | `operating_margin` | quality | **renamed** |
| 61 | `profit_margin` | Profit margin | financial | `net_margin` | quality | **renamed** |
| 62 | `quick_ratio` | Quick ratio | financial | `quick_ratio` | quality | **reachable** |
| 63 | `current_ratio` | Current ratio | financial | `current_ratio` | quality | **reachable** |
| 64 | `debt_to_equity` | Debt/Eq | financial | `debt_to_equity` | quality | **reachable** |
| 65 | `lt_debt_to_equity` | LT Debt/Eq | financial | `lt_debt_to_equity` | quality | **reachable** |
| 66 | `employees` | Employees | financial | `employees` | profile | **reachable** |
| 67 | `ipo` | IPO | financial | `ipo_date` | profile | **renamed** |
| 68 | `sma20` | SMA20 | technical | `sma20` | trend | **reachable** |
| 69 | `sma50` | SMA50 | technical | `sma50` | trend | **reachable** |
| 70 | `sma200` | SMA200 | technical | `sma200` | trend | **reachable** |
| 71 | `volatility` | Volatility | technical | `volatility_1w` | trend | **renamed** |
| 72 | `index` | Index | overview | `index_membership` | profile | **renamed** |

**Reading the table.** 71 of 72 keys land on a cell. The two `(All columns only)` rows
are not losses: `eps_next_5y` and `sales_past_5y` are in `views.DETAIL_ONLY` — no named
view carries them, and the All-columns destination renders all 116 catalogue keys
(`web/src/grid/AllColumnsPanel.tsx` requests `catalogue.keys`), so both still get a
column, just not in a tab.

**Live cell check (not just names).** All 72 keys were requested from
`POST /screens/select` as a `columns` projection in two batches (40 + 32, the
`COLUMN_LIMIT`) over the top 20 US rows: every key came back present in each row, so no
name is silently dropped by the projection either. Fill histogram over those 20 rows:
14 keys on 20/20, 23 keys on 19/20, 31 keys on 10–15 (the fundamentals and estimate
lane — the depth store's own coverage, not a parity defect), 1 key on 16, 17 and 18 each,
and `option_short` on **0/20** — the one absent column, exactly as G1 says.
`avg_volume` resolves to `avg_volume_30d` — see gap G4.

**Adversarial check that was run, not skipped.** Every non-catalogue name was grepped
for its *value* in the new source before being called renamed: `perf_week`→`perf_1w`,
`week52_high`→`high_52w`, `oper_margin`→`operating_margin`, `index`→`index_membership`,
`ipo`→`ipo_date`, `income`→`net_income` are all in `CATALOGUE_COLUMNS` with a row field
behind them, and every alias is also exercised at runtime: the frozen legacy views
(`view=overview|valuation|performance|financial|technical|ownership`) still answer `200`
with their exact old column set (32/16/16/33/18/15 keys), and that is where `option_short`
is measured as `null` on every row — the decision in G1, not an accident of the join.

---

## 3. Table 2 — filter controls (17 rows)

`probe` = the live `POST /screens/select` result at 12:33–12:36 UTC; "total" is the
post-filter row count, compared with **5,576** for the same query with no filters.

| # | kind | old control | old behaviour | new mechanism | probe | verdict |
|---:|---|---|---|---|---|---|
| 1 | filter | Advanced → "Market cap, min" ($B) | sends `cap gte v×1e9` | `{"key":"cap","op":"gte"}` → `market_cap_usd` | `200`, total 179 | **reachable** |
| 2 | filter | Advanced → "P/E, max" | `fa_pe lte` | `fa_pe` → `pe_ttm` | `200`, total 3,535 | **reachable** |
| 3 | filter | Advanced → "Price, min" | `sh_price gte` | `sh_price` → `price` | `200`, total 1,309 | **reachable** |
| 4 | filter | Advanced → "RSI, max" | `ta_rsi lte` | `ta_rsi` → `rsi_14` | `200`, total 5,289 | **reachable** |
| 5 | filter | Advanced → "Sector" | `sec eq` | `sec` → `sector` | `200`, total 338 | **reachable** |
| 6 | sort | Advanced → "Sort" select | `market_cap` / `turnover` / `gainers` | `criteria.sort`, same three | all three `200` | **reachable** (identical vocabulary) |
| 7 | sort | Toolbar sort button (client-side over the 20 visible rows) | cycles `market_cap`→`change_pct`→`pe`→`forward_pe`→`rsi_14`→`perf_ytd` | server `SORTS` only | `pe`, `change_pct` → **422** `unknown sort` | **lost** — see G3 |
| 8 | preset | Chip "All names" | no filters | empty `filters` | total 5,576 | **reachable** |
| 9 | preset | Chip "Momentum" (`change_pct > 0`) | client-side, visible page only | `ta_change gt 0` | `200`, total 1,514 | **reachable** (whole universe, not the page) |
| 10 | preset | Chip "Value" (`0 < pe < 25`) | client-side | `fa_pe gt 0` **and** `fa_pe lt 25` | `200`, total 1,973 | **reachable** |
| 11 | preset | Chip "Quality" (`roe >= 15`) | client-side | `fa_roe gte 15` | `200`, total 1,075 | **reachable** |
| 12 | preset | Chip "Watchlist" (`localStorage`) | client-side membership | `mode: "symbols"` + `snapshot_id` + `source` | `200`, 2/2 resolved | **reachable**, mechanism changed |
| 13 | search | Toolbar search (ticker **or** company substring) | client-side over the page | `{"field":"symbol","op":"contains"}` / `{"field":"name","op":"contains"}` | `200`, totals 5 / 4 | **reachable** |
| 14 | scope | Region tabs `us` / `global` / `us_etf` | `region` on the request | same `region` parameter | `200` — 5,576 / 40,774 / 5,776 | **reachable** |
| 15 | columns | Column menu (hide `fpe`, `rsi`, `ytd`) | 3 checkboxes, client-side | `ColumnPicker` (any schema key, 40 per panel) | static: `web/src/grid/ColumnPicker.tsx` | **reachable** (superset) |
| 16 | page | Pager, 20 rows/page | `limit` 20 | `criteria.limit`/`offset`, up to 100 | `200`, `returned` honours `limit` | **reachable** (widened) |
| 17 | reset | "Clear filters" | clears chips, query, drawer | empty `filters` array | identity, total 5,576 | **reachable** |

Counts: **5 of 5** advanced-filter keys are live registry bindings and each one *narrows*
the answer (179 / 3,535 / 1,309 / 5,289 / 338 against 5,576), so none is a no-op.
**0** old filter keys answer 422. The two 422s in this table are sort values, not filters
— reported as a loss in G3, not excused as a gap.

Registry reach (for a reviewer who wants the ceiling): `GET /screens/select/schema`
publishes `x-filter-keys` = 82 (50 available, 32 refused with a reason) and
`x-filter-aliases` = 105 catalogue spellings, i.e. 187 names a filter may carry. The old
drawer used 5 of them.

---

## 4. Table 3 — logos

Old UI: `row.logo` in `ui/app.js` (lines 491, 571, 669) — always non-empty, either
`/ui/logos/{SYM}.{ext}` or a `data:image/svg+xml;base64` monogram (`app/branding/logos.py`
`logo_for`). New app: `<img src="/logos/{symbol}">` (`app/api/logos.py`, a four-rung
ladder: store → vendored `ui/logos` → one fixed favicon host → the same monogram).

Sample: 30 symbols, **all drawn from the live US top-40 by market cap** — the rows the new
grid's first page actually shows — split across the three rungs a real screen hits: 18 with
a vendored asset, 6 served from the on-disk store, 6 that fall through to the monogram.
The set deliberately includes the awkward rows a live screen really returns: preferred and
alias symbols containing `/` (`JPM/PC`, `ORCL/PD`), a dotted class (`BRK.B`), and the
GOOG/META alias pairs (`GOOGM`, `GOOGN`, `METAM`).

| # | symbol | status | content-type | bytes | magic | rung | verdict | note |
|---:|---|---|---|---:|---|---|---|---|
| 1 | `AAPL` | 200 | `image/png` | 2468 | PNG | vendored | **ok** | identical to `ui/logos/AAPL.png` (sha256 match) |
| 2 | `MSFT` | 200 | `image/png` | 426 | PNG | vendored | **ok** | identical to `ui/logos/MSFT.png` (sha256 match) |
| 3 | `NVDA` | 200 | `image/png` | 1607 | PNG | vendored | **ok** | identical to `ui/logos/NVDA.png` (sha256 match) |
| 4 | `AMZN` | 200 | `image/png` | 658 | PNG | vendored | **ok** | identical to `ui/logos/AMZN.png` (sha256 match) |
| 5 | `AMD` | 200 | `image/png` | 183 | PNG | vendored | **ok** | identical to `ui/logos/AMD.png` (sha256 match) |
| 6 | `META` | 200 | `image/jpeg` | 2319 | JPEG | vendored | **ok** | identical to `ui/logos/META.jpg` (sha256 match) |
| 7 | `TSLA` | 200 | `image/png` | 1569 | PNG | vendored | **ok** | identical to `ui/logos/TSLA.png` (sha256 match) |
| 8 | `V` | 200 | `image/png` | 338 | PNG | vendored | **ok** | identical to `ui/logos/V.png` (sha256 match) |
| 9 | `GOOGL` | 200 | `image/png` | 2326 | PNG | vendored | **ok** | identical to `ui/logos/GOOGL.png` (sha256 match) |
| 10 | `LLY` | 200 | `image/png` | 1464 | PNG | vendored | **ok** | identical to `ui/logos/LLY.png` (sha256 match) |
| 11 | `JPM` | 200 | `image/jpeg` | 1680 | JPEG | vendored | **ok** | identical to `ui/logos/JPM.jpg` (sha256 match) |
| 12 | `CVX` | 200 | `image/png` | 749 | PNG | store | **ok** | no `ui/logos` asset; served from the on-disk store |
| 13 | `COST` | 200 | `image/png` | 777 | PNG | store | **ok** | no `ui/logos` asset; served from the on-disk store |
| 14 | `PLTR` | 200 | `image/png` | 388 | PNG | store | **ok** | no `ui/logos` asset; served from the on-disk store |
| 15 | `CSCO` | 200 | `image/png` | 201 | PNG | store | **ok** | no `ui/logos` asset; served from the on-disk store |
| 16 | `KO` | 200 | `image/png` | 413 | PNG | store | **ok** | no `ui/logos` asset; served from the on-disk store |
| 17 | `ORCL` | 200 | `image/png` | 1243 | PNG | store | **ok** | no `ui/logos` asset; served from the on-disk store |
| 18 | `GOOGM` | 200 | `image/svg+xml` | 461 | SVG | monogram | **ok** | no asset; the declared monogram (same generator as `row.logo`) |
| 19 | `GOOGN` | 200 | `image/svg+xml` | 461 | SVG | monogram | **ok** | no asset; the declared monogram (same generator as `row.logo`) |
| 20 | `METAM` | 200 | `image/svg+xml` | 461 | SVG | monogram | **ok** | no asset; the declared monogram (same generator as `row.logo`) |
| 21 | `BRK.B` | 200 | `image/svg+xml` | 460 | SVG | monogram | **ok** | no asset; the declared monogram (same generator as `row.logo`) |
| 22 | `JPM/PC` | 200 | `image/svg+xml` | 461 | SVG | monogram | **ok** | no asset; the declared monogram (same generator as `row.logo`) |
| 23 | `ORCL/PD` | 200 | `image/svg+xml` | 461 | SVG | monogram | **ok** | no asset; the declared monogram (same generator as `row.logo`) |
| 24 | `AVGO` | 200 | `image/png` | 4730 | PNG | vendored | **ok** | identical to `ui/logos/AVGO.png` (sha256 match) |
| 25 | `MU` | 200 | `image/png` | 769 | PNG | vendored | **ok** | identical to `ui/logos/MU.png` (sha256 match) |
| 26 | `WMT` | 200 | `image/png` | 1679 | PNG | vendored | **ok** | identical to `ui/logos/WMT.png` (sha256 match) |
| 27 | `SPCX` | 200 | `image/png` | 579 | PNG | vendored | **ok** | identical to `ui/logos/SPCX.png` (sha256 match) |
| 28 | `ABBV` | 200 | `image/jpeg` | 759 | JPEG | vendored | **ok** | identical to `ui/logos/ABBV.jpg` (sha256 match) |
| 29 | `MA` | 200 | `image/png` | 1253 | PNG | vendored | **ok** | identical to `ui/logos/MA.png` (sha256 match) |
| 30 | `XOM` | 200 | `image/png` | 353 | PNG | vendored | **ok** | identical to `ui/logos/XOM.png` (sha256 match) |

**30/30 answered `200` with an `image/*` content type and real image magic bytes.** The
18 vendored symbols are byte-identical (SHA-256) to the files under `ui/logos`, so the
new path serves exactly the asset the old client showed. `/logos/JPM%2FPC` and the raw
`/logos/JPM/PC` both answer `200` with the same strong ETag, so a symbol containing `/`
cannot produce a broken image. Every response carried
`Cache-Control: public, max-age=2592000, immutable` + `ETag`.

Parity holds for the reason that matters: **the old UI never had a hole** (a monogram
backstop), and the new path has the *same* backstop with the same generator — so the set
of symbols that "get a logo" is unchanged, and for the 18 vendored brands sampled the bytes
are identical to the files the old client served.

---

## 5. Gaps and losses, with the decision behind each

### G1 — `option_short` (the only absent column) — decided, plan §6.1

* **What the old UI showed:** one "Shortable" text cell in the Ownership tab (`optionable`
  or `shortable` collapsed by the provider into a single word).
* **What the user loses:** the single combined word. Not the two facts.
* **Why:** `app/screen/columns.py` `ABSENT_COLUMNS["option_short"]` — a composite of two
  catalogue keys with two different sources; one name cannot address two cells, so any
  view rendering it draws `null`. `optionable` and `shortable` both render in the
  **Profile** view. Confirmed live: `option_short` was the only one of the 72 old keys
  null on all 20 sampled rows, while `optionable`/`shortable` answer `"Yes"` for the same
  rows (`{"columns":["optionable","shortable","option_short"]}` → `"Yes"`, `"Yes"`, `null`).
* **Decided?** Yes — plan §6.1 (the Ownership view carries the three short-side cells, and
  `profile` carries the two flags), and the test carries it in `ALLOWED_GAPS` with this
  reasoning.

### G2 — `eps_next_5y`, `sales_past_5y`: moved, not lost

Old Financial tab drew both. Plan §6.1 lists them among the sixteen **detail-panel-only**
keys, and `views.DETAIL_ONLY` implements that decision, so no named view carries them. In
the shipped app they are reachable through the **All-columns destination** (which requests
all 116 catalogue keys); the detail panel's own metric list (`web/src/detail/metrics.ts`) is
the fundamentals statement, and contains neither — so "detail-panel only" landed as
"All-columns only". A column exists in both readings, which is why the verdict is
reachable, not absent.

### G3 — ordering by an arbitrary column — lost, narrowed in the shipped grid

* **What the old UI did:** the toolbar sort button cycled
  `market_cap → change_pct → pe → forward_pe → rsi_14 → perf_ytd` and re-sorted the 20
  visible rows in the browser.
* **What the user loses:** ordering by P/E, forward P/E, RSI, 1-day move or YTD. Live
  proof of the narrowing: `{"sort":"pe"}` and `{"sort":"change_pct"}` answer
  `422 unknown sort 'pe'; expected one of market_cap, turnover, gainers`.
* **Why:** `app/screen/rank.py` — three transparent sorts over the whole universe, no
  composite score, server-side so page boundaries are stable. The new grid sets
  `enableSorting: false` on every column (`web/src/grid/DataTable.tsx`), so this is not
  "moved to a header click".
* **Decided?** Partly. `rank.py` decides the *server* vocabulary (three sorts), and the new
  app exposes exactly those three in its sort control. The plan's D3 had assumed TanStack
  multi-sort would be available, so disabling column sorting is an implementation
  narrowing rather than a plan line. Either way it is a real capability loss, recorded as
  such and not as a gap: filters can narrow to a P/E band, they cannot order by it.

### G4 — `avg_volume` reads a different window — disclosed number change

Old key `avg_volume` → catalogue `average_volume` → row field `avg_volume_30d`.
Measured on AAPL: `avg_volume` = `avg_volume_30d` = 38,706,338 vs
`avg_volume_10d` = 42,134,188. The old UI's "Avg vol" was the provider's trailing
average, not the ten-day window; the ten-day cell stays addressable as `avg_volume_10d`.
Decided in `columns.py` (audit F6). No column is lost; the number a reviewer compares may
differ from a ten-day figure.

### G5 — known gaps in the brief that the old UI never showed

These are **plan gaps, not parity gaps**. Both were checked against the old source rather
than assumed:

* **"the 7 keyless fields".** Today's table lists **six** names with no row field:
  `open`, `high`, `low`, `sort_order`, `row_type`, `option_short`
  (`app/screen/columns.py` `ABSENT_COLUMNS`, and `views._validate` fails the import if a
  fourth *view* column appears without a field). The audit's "seven" also counted
  `change`, which has since acquired a real column (`CATALOGUE_COLUMNS["change"] =
  "change_pct"`, filled on every row — `view=today` returned `-1.2266` for AAPL). Of the
  six, exactly **one** — `option_short` — was ever a column in `ui/app.js`; `open`,
  `high`, `low`, `sort_order` and `row_type` were never drawn by the old UI either, so
  they cost no parity. `open`/`high`/`low` do render `null` in the `today` view, which is a
  plan gap (`universe.PARITY_FIELDS` does not request session open/high/low); the other
  two are transport plumbing no view names.
* **the intraday QuoteHub columns.** `grep -ci "hub|intraday|bid|ask" ui/app.js` = **0**,
  and there is no `QuoteHub` reference anywhere under `web/src`. Neither UI surfaces
  intraday quotes. Both read the same 5-minute snapshot: `GET /screens?source=tradingview`
  reported `as_of 12:36:25Z` / `next_refresh_at 12:41:25Z` while the new grid's
  `POST /screens/select` (same store) stamped `as_of` inside the same window. So the new
  app neither lost nor gained intraday freshness — the QuoteHub overlay is unwired in
  both.

---

## 6. The test that keeps this true — `tests/test_ui_parity.py`

83 tests, no HTTP, hermetic (`pytest tests/test_ui_parity.py -q` → `83 passed`).

It parses `ui/app.js` (not a fixture, not a copy) for `COLUMN_META` keys and the six tab
column lists, `ui/index.html` for the `#filterSort` options, and `ui/app.js`
`filterFields()`/`applyFilters()` for the five drawer keys. It then requires every old
column key to be:

1. a catalogue key, **or**
2. a name `app.screen.columns.catalogue_keys_for` resolves to a catalogue key *and* whose
   targets are still in `CATALOGUE_KEY_SET` *and* have a home (a canonical view or
   `DETAIL_ONLY`), **or**
3. an entry in `ALLOWED_GAPS` — currently one (`option_short`), with the reasoning of G1
   in the message.

Anything else fails with the old key's name, label and tab. The alias path re-checks the
*targets* rather than trusting `field_for`, because `rsi_14` would keep resolving to its
own row column after `rsi` was deleted — the column would render null and nothing would
notice. That is the exact hole this test closes.

**Failure mode, verified by injection** (four mutations applied in a scratch session, each
caught, each reverted):

| injected change | test that fails | message |
|---|---|---|
| delete `rsi` from `CATALOGUE_KEY_SET` | `test_every_old_column_is_mapped_or_a_documented_gap[rsi_14]` | `'rsi_14' (RSI, tab: overview,technical) resolves to ['rsi'], which is not in the 116-key catalogue` |
| add a column to the frozen client | same test, `[brand_new_metric]` | `is neither a catalogue key nor an alias of one, and it is not in ALLOWED_GAPS` |
| make `option_short` renderable | `test_an_allowlisted_gap_is_still_a_gap` | `ALLOWED_GAPS entry/entries ['option_short'] now resolve to a row field` |
| delete the `option_short` allowlist entry | same test | `names no single row field, so any view that renders it draws null` |

Plus the ratchets that keep the test from going quietly green: the corpus is pinned at
**72** keys (deleting a block of `COLUMN_META` cannot shrink the input set), the class
arithmetic is asserted as `48 catalogue + 23 alias + 1 gap = 72`, the five old filter keys
must be live bindings (`binding(key).is_available`, with a 422 reported as a *regression*),
and the old sort select must equal `rank.SORTS` exactly.

---

## 7. Reproduce

```bash
cd /home/ubuntu/ztockz
.venv/bin/python -m pytest tests/test_ui_parity.py -q          # 83 passed

# logos (30 symbols, all 200 image/*)
for s in AAPL MSFT NVDA AMZN AMD META TSLA V GOOGL LLY JPM CVX COST PLTR CSCO KO ORCL \
         GOOGM GOOGN METAM BRK.B JPM%2FPC ORCL/PD AVGO MU WMT SPCX ABBV MA XOM; do
  printf "%-10s " "$s"; curl -s -o /dev/null -w "%{http_code} %{content_type}\n" \
    "http://127.0.0.1:8099/logos/$s"
done

# the five old filter keys narrow (5,576 → 179 / 3,535 / 1,309 / 5,289 / 338)
curl -s -X POST http://127.0.0.1:8099/screens/select -H 'content-type: application/json' \
  -d '{"mode":"criteria","region":"us","criteria":{"filters":[{"key":"cap","op":"gte","value":100000000000}],"sort":"market_cap","limit":3}}'

# the lost sort control, stated by the API
curl -s -X POST http://127.0.0.1:8099/screens/select -H 'content-type: application/json' \
  -d '{"mode":"criteria","region":"us","criteria":{"filters":[],"sort":"pe","limit":1}}'
# → 422 unknown sort 'pe'; expected one of market_cap, turnover, gainers
```
