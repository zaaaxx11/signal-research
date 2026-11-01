# Finviz filter groups — Fundamental, Technical, News, ETF (measured 2026-11-01)

Scope: the 60 controls in the request's **Fundamental** (37), **Technical** (20), **News** (2) and
**ETF** (1) panels, plus the 23 Descriptive controls the `ALL` union adds on top. Every control is
mapped to a Ztockz key (or `none`), its row field, its measured coverage, a HAVE/PARTIAL/MISSING
verdict, whether a filter can run on it **today**, and a note wherever the data is not trustworthy.

## 0. What was measured, and how

- **Universe**: a live parity scan of `scanner.tradingview.com/america/scan` built with the
  selector's own arguments (`asset_class=stock`, `subtype=None`, `min_market_cap=0`,
  `limit=100000`, `columns=PARITY_COLUMNS`): **5,613 provider rows → 5,576 universe rows** — the same
  population `POST /screens/select` answers (the earlier Descriptive audit measured 5,576 too).
- **Pipeline**: `build_universe` → `depth_overlay.overlay(rows, depth.symbols, aliases=SCREEN_ALIASES)`
  → the fundamentals join → the facts join. The last two contribute **nothing** (see §9), so every
  number below is scan + depth.
- **Depth store**: `/home/ubuntu/ztockz-data/depth.json`, `as_of **2026-11-01T11:38:42Z**`, **4,217
  symbols**, 112 keys. It joins **4,187** universe symbols (75.09 %) by exact ticker or the
  dot↔hyphen alias (`BRK.B`/`BRK-B`). **75.09 % is the ceiling for every depth-only cell** — 1,389
  universe symbols are simply not in the store.
- **Catalog**: `app/screen/filters.FILTER_CATALOG`, 82 keys, read programmatically (not transcribed).
- **Bucket vocabularies**: the vendored probe `research/finviz-ui-raw/views/v111-filters.json`
  (83 keys — it also has `sh_dilution`, which the 82-key catalog deliberately does not) supplies the
  label for every token, including the `isEliteOnly` flag.
- Probe scripts: `/home/ubuntu/.hermes/cache/scratch/probe_{cov,quality,news_quality,disposition}.py`
  (not committed). Coverage JSON: `…/coverage_us.json`.
- All percentages are of **5,576** unless a cell says otherwise.

Two cross-checks that the measurement is sound: `rsi_14 >= 70` → **271** rows and
`price <= 1.001 × low_52w` → **56** rows, the exact counts `app/api/signals.py` publishes today for
`overbought` and `new_low`.

## 1. The five questions, answered

### 1.1 Does `FILTER_CATALOG` already have fundamental / technical / news / etf tabs?

**Yes, all four, and they are the only tabs it has.** `app/screen/filters.py:134`:

```python
FILTER_TABS: tuple[str, ...] = ("descriptive", "fundamental", "technical", "news", "etf")
```

The 82 keys split **22 descriptive / 37 fundamental / 20 technical / 2 news / 1 etf**. The request's
group counts (37 / 20 / 2 / 1) are exactly the catalog's. Nothing has to be added to the catalog for
this work — `x-filter-key-count == 82` can stay 82.

(The *document* T9 publishes is a different count on purpose: one group of **25 controls** — `order`,
`signal`, the 22 descriptive keys and the keyless `sh_dilution`. The four groups below are new
groups of filter controls only; `descriptive` keeps its 25.)

### 1.2 Which of these controls already exist there?

**All 60.** Every control named in the request is a catalog key, spelled Finviz's way:
`fa_pe … sh_insttrans` (37), `ta_perf … ah_change` (20), `news_date`, `news_keywords`, `etf_heldby`.
The gap is not the catalog, it is the **disposition**:

| tab | keys | filter runs today | refused today |
|---|---|---|---|
| fundamental | 37 | 21 | **16** |
| technical | 20 | 13 | **7** |
| news | 2 | 0 | **2** |
| etf | 1 | 0 | **1** |
| **total** | **60** | **34** | **26** |

("runs today" = `filter_fields.BINDINGS[k].is_available`, i.e. a field, a choice compiler or a
bucket family. 51 of the full 82 are available.)

### 1.3 Which are refused, and with what reason (quoted)

`app/screen/filter_fields.py:156-244` holds one sentence per group; the 26 refusals come from twelve
of them (a sentence that names two keys is one group, two keys). Quoted as the assembled string —
the source splits each over several lines:

```python
#: fa_divgrowth
"needs a dividend growth rate: the scan carries a dividend yield only "
"(`dividends_yield`), which is not a growth rate"

#: fa_epsyoy, fa_epsyoy1, fa_epsqoq, fa_epsyoyttm, fa_eps3years, fa_eps5years,
#: fa_estltgrowth, fa_salesqoq, fa_salesyoyttm, fa_sales3years, fa_sales5years
"needs a growth-rate column (EPS and sales growth this year, next year, "
"quarterly, 3-5 year and TTM): the scan carries forward EPS and revenue "
"*levels* for the next FY/FQ, and a level is not a growth rate"

#: fa_epsrev
"needs an earnings-surprise column: the scan carries no surprise history"

#: fa_ltdebteq
"needs long-term debt separately: the scan carries total debt/equity only"

#: sh_insidertrans, sh_insttrans
"needs insider and institutional *transaction* flows: the scan carries "
"no transaction column, and holdings are not transactions"

#: ta_highlow20d, ta_highlow50d
"needs 20-day and 50-day high/low columns: the scan carries the "
"52-week extremes only"

#: ta_alltime
"needs an all-time high/low column: the scan carries the 52-week "
"extremes only"

#: ta_pattern
"needs chart-pattern detection: the scan returns no pattern classification"

#: ta_candlestick
"needs candlestick detection: the scan returns no candlestick classification"

#: ah_close, ah_change
"needs after-hours prices: the scan covers the regular session only"

#: news_date, news_keywords
"needs the news store: the scan returns no news timestamp or keywords"

#: etf_heldby
"needs ETF holdings data: the scan does not carry a fund's constituent list"
```

**Every one of those sentences is true of the *scan* and false of the *row*.** The depth overlay has
been copying these cells onto the row since the overlay existed — it just never got a filter key.

### 1.4 How many refused controls have data on the row anyway?

**20 of 26.** Measured non-null counts after the overlay:

| refused key | cell the row already carries | rows |
|---|---|---|
| `fa_divgrowth` | `dividend_growth_1y` / `3y` / `5y` | 1,606 / 1,374 / 1,317 |
| `fa_epsyoy` | `eps_this_y` | 3,297 |
| `fa_epsyoy1` | `eps_next_y` | 3,364 |
| `fa_epsqoq` | `eps_qoq` | 4,038 |
| `fa_epsyoyttm` | `eps_yoy_ttm` | 3,875 |
| `fa_eps3years` | `eps_past_3y` | 2,717 |
| `fa_eps5years` | `eps_past_5y` | 2,383 |
| `fa_estltgrowth` | `eps_next_5y` | 2,447 |
| `fa_salesqoq` | `sales_qoq` | 3,599 |
| `fa_salesyoyttm` | `sales_yoy_ttm` | 3,547 |
| `fa_sales3years` | `sales_past_3y` | 3,354 |
| `fa_sales5years` | `sales_past_5y` | 3,117 |
| `fa_epsrev` | `eps_surprise` + `revenue_surprise` | 3,601 / 3,114 (3,114 both) |
| `fa_ltdebteq` | `lt_debt_to_equity` | 4,112 |
| `sh_insidertrans` | `insider_trans` | 4,118 |
| `sh_insttrans` | `inst_trans` | 4,162 |
| `ta_highlow50d` | `high_50d` / `low_50d` | 4,187 |
| `ta_alltime` | `all_time_high` / `all_time_low` | 4,186 |
| `ta_candlestick` | `candlestick_pattern` | 2,375 |
| `news_date` | `news_time` / `news_title` / `news_url` | 4,112 |

The six with **no** cell anywhere: `ta_highlow20d`, `ta_pattern`, `ah_close`, `ah_change`,
`news_keywords`, `etf_heldby`.

### 1.5 Do our 10 canonical views map onto Finviz's 14 column tabs?

The request's list has **15** labels, not 14 (Overview, Valuation, Financial, Ownership, Performance,
Technical, Custom, Charts, Tickers, Basic, TA, News, Snapshot, Maps, Stats). Mapped as written:

| Finviz column tab | our view | verdict |
|---|---|---|
| Overview | `today` (alias `overview`) | **PARTIAL** — `today` is 11 session cells; the other cells our frozen `overview` tab renders (27 columns) are spread across `value`/`ownership`/`profile` |
| Valuation | `value` | **HAVE** — same 11 multiples, plus cap, EV, target, recommendation |
| Financial | `growth` (alias `financial`) | **PARTIAL** — Finviz's one tab is split three ways: `growth` + `quality` + `income` |
| Ownership | `ownership` (name kept) | **PARTIAL** — same subject, different columns (Finviz leads with insider ownership and one collapsed `option_short`; we lead with the share counts) |
| Performance | `momentum` (alias `performance`) | **HAVE** — 9 return windows + RSI |
| Technical | `trend` (alias `technical`) | **PARTIAL** — `trend` carries SMA/range/ATR/beta/volatility but not RSI, which sits in `momentum` |
| Basic | `profile` | **PARTIAL** — identity/classification covered; Basic's multiples are in `value` |
| Custom | — | **MISSING** — no user-defined column picker; `AllColumnsPanel` is a fixed 116-column destination |
| Charts | — | **MISSING** — no image/series column; charts are a route, not a column |
| Tickers | — | **MISSING** — watchlists are `symbols` mode, not a column tab |
| TA | — | **MISSING** — no distinct tab; its subject is `trend` + `momentum` |
| News | — | **MISSING by design** — `news_title`/`news_url`/`news_time` are `DETAIL_ONLY` (`views.py:364`): prose is never a grid column |
| Snapshot | — | **MISSING** — nearest surface is `all` (116 columns), which is a destination, not a snapshot strip |
| Maps | — | **MISSING** — `/market/map` is a separate route, not a column set |
| Stats | — | **MISSING** — market statistics live on `/market/breadth`, not in the grid |

Tally: **HAVE 2 · PARTIAL 5 · MISSING 8**. The mapping is not invented here: `views.LEGACY_VIEW_ALIASES`
(`views.py:440`) already records overview→today, valuation→value, performance→momentum,
financial→growth, technical→trend, and says `ownership` kept its name but not its column list.
**Eight of the fifteen tabs have no equivalent at all**, and five of those (Custom, Charts, Tickers,
Maps, Stats) are not column sets in our product — they are other surfaces.

## 2. FUNDAMENTAL — 37 controls

`wired` = can a filter run today. Coverage = non-null / 5,576 after the overlay.

| # | Finviz control | key | row field | coverage | status | wired | note |
|---|---|---|---|---|---|---|---|
| 1 | P/E | `fa_pe` | `pe_ttm` | 2,943 · 52.78% | HAVE | numeric only | 23 tokens refused; the 25-option vocabulary is inert |
| 2 | Forward P/E | `fa_fpe` | `pe_forward` | 3,574 · 64.10% | HAVE | numeric only | scan answers null on every row; depth `forward_pe` fills it (`SCAN_NULL_CELLS`) |
| 3 | PEG | `fa_peg` | `peg` | 2,307 · 41.37% | HAVE | numeric only | `depth_fields` quality `definition`, median deviation 29.6% vs the scan's own PEG |
| 4 | P/S | `fa_ps` | `ps` | 3,628 · 65.06% | HAVE | numeric only | **3 rows carry `Infinity`** (`IMNM`, `INBX`, `IPSC`) — not valid strict JSON |
| 5 | P/B | `fa_pb` | `pb` | 4,835 · 86.71% | HAVE | numeric only | clean |
| 6 | Price/Cash | `fa_pc` | `price_to_cash` | 4,163 · 74.66% | HAVE | numeric only | computed by the fundamentals join, else the depth `pc` alias; depth-only in practice |
| 7 | Price/Free Cash Flow | `fa_pfcf` | `pfcf` | 2,495 · 44.75% | HAVE | numeric only | `definition` quality (median deviation 30.5%) |
| 8 | EV/EBITDA | `fa_evebitda` | `ev_ebitda` | 2,756 · 49.43% | HAVE | numeric only | depth `ev_ebitda`; `equivalent`, not identical to Finviz's |
| 9 | EV/Sales | `fa_evsales` | `ev_to_sales` | 3,619 · 64.90% | HAVE | numeric only | |
| 10 | Dividend Growth | `fa_divgrowth` | `dividend_growth_1y/3y/5y` | 1,606 / 1,374 / 1,317 (28.8 / 24.6 / 23.6%) | PARTIAL | **no** | 30 tokens; `cy1…cy9` ("growing 1+ years") have **no column** — only 1y/3y/5y exist |
| 11 | EPS Growth This Year | `fa_epsyoy` | `eps_this_y` | 3,297 · 59.13% | PARTIAL | **no** | percent; range −35,034 … +35,114 |
| 12 | EPS Growth Next Year | `fa_epsyoy1` | `eps_next_y` | 3,364 · 60.33% | PARTIAL | **no** | percent |
| 13 | EPS Growth Qtr Over Qtr | `fa_epsqoq` | `eps_qoq` | 4,038 · 72.42% | PARTIAL | **no** | percent |
| 14 | EPS Growth TTM | `fa_epsyoyttm` | `eps_yoy_ttm` | 3,875 · 69.49% | PARTIAL | **no** | percent |
| 15 | EPS Growth Past 3Y | `fa_eps3years` | `eps_past_3y` | 2,717 · 48.73% | PARTIAL | **no** | |
| 16 | EPS Growth Past 5Y | `fa_eps5years` | `eps_past_5y` | 2,383 · 42.74% | PARTIAL | **no** | `depth_fields` marks it `missing`; 2,383 rows carry it |
| 17 | EPS Growth Next 5Y | `fa_estltgrowth` | `eps_next_5y` | 2,447 · 43.88% | PARTIAL | **no** | Yahoo dropped the +5y estimate period for many symbols |
| 18 | Sales Growth QoQ | `fa_salesqoq` | `sales_qoq` | 3,599 · 64.54% | PARTIAL | **no** | |
| 19 | Sales Growth TTM | `fa_salesyoyttm` | `sales_yoy_ttm` | 3,547 · 63.61% | PARTIAL | **no** | |
| 20 | Sales Growth Past 3Y | `fa_sales3years` | `sales_past_3y` | 3,354 · 60.15% | PARTIAL | **no** | |
| 21 | Sales Growth Past 5Y | `fa_sales5years` | `sales_past_5y` | 3,117 · 55.90% | PARTIAL | **no** | `depth_fields` quality `missing`, populated anyway |
| 22 | Earnings & Revenue Surprise | `fa_epsrev` | `eps_surprise`, `revenue_surprise` | 3,601 / 3,114 · 64.58 / 55.85% | PARTIAL | **no** | 3 tokens (`bp`,`bm`,`bn`) need **both** cells → only 3,114 rows can answer; 1,564 both-positive, 485 both-negative |
| 23 | Return on Assets | `fa_roa` | `roa` | 5,015 · 89.94% | HAVE | numeric only | `definition` — different denominator from Finviz's |
| 24 | Return on Equity | `fa_roe` | `roe` | 4,689 · 84.09% | HAVE | numeric only | |
| 25 | Return on Invested Capital | `fa_roi` | `roic` | 4,881 · 87.54% | HAVE | numeric only | |
| 26 | Current Ratio | `fa_curratio` | `current_ratio` | 5,323 · 95.46% | HAVE | numeric only | |
| 27 | Quick Ratio | `fa_quickratio` | `quick_ratio` | 4,704 · 84.36% | HAVE | numeric only | |
| 28 | LT Debt/Equity | `fa_ltdebteq` | `lt_debt_to_equity` | 4,112 · 73.74% | PARTIAL | **no** | depth cell is a **ratio** (0.66 = 0.66×), same unit as Finviz's |
| 29 | Debt/Equity | `fa_debteq` | `debt_to_equity` | 4,949 · 88.76% | HAVE | numeric only | |
| 30 | Gross Margin | `fa_grossmargin` | `gross_margin` | 3,925 · 70.39% | HAVE | numeric only | |
| 31 | Operating Margin | `fa_opermargin` | `oper_margin` | 4,552 · 81.64% | HAVE | numeric only | |
| 32 | Net Profit Margin | `fa_netmargin` | `profit_margin` | 4,552 · 81.64% | HAVE | numeric only | |
| 33 | Payout Ratio | `fa_payoutratio` | `payout_ratio` | 3,398 · 60.94% | PARTIAL | numeric only | trailing-EPS based (Finviz uses forward); **1,809 of 3,398 are exactly 0**, so "None (0%)" is half the payers and includes every symbol the join zero-filled |
| 34 | Insider Ownership | `sh_insiderown` | `insider_own` | 4,186 · 75.07% | HAVE | numeric only | **6 rows > 100%** (ATER 354.09, AAME 154.49, …) — impossible; bucket `low` is usable, `>90` is not |
| 35 | Insider Transactions | `sh_insidertrans` | `insider_trans` | 4,118 · 73.85% | PARTIAL | **no** | **garbage** — see §7.1; `signals.py` withdrew it by name |
| 36 | Institutional Ownership | `sh_instown` | `inst_own` | 4,186 · 75.07% | HAVE | numeric only | **25 rows > 130%** (FOXA 131.02 … SPHR 136.07) |
| 37 | Institutional Transactions | `sh_insttrans` | `inst_trans` | 4,162 · 74.64% | PARTIAL | **no** | 3,318 of 4,162 negative, median −13.96 — an asymmetric 3-month net change, not a flow; see §7.2 |

**Verdict: HAVE 20 · PARTIAL 17 · MISSING 0.** Twenty are wired (numeric only, every Finviz token
refused); seventeen have the data on the row and no key.

## 3. TECHNICAL — 20 controls

| # | Finviz control | key | row field | coverage | status | wired | note |
|---|---|---|---|---|---|---|---|
| 1 | Performance | `ta_perf` | choice: `ret_1w…ret_10y`, `change_pct` | 99.86–100% | HAVE | **yes**, 111/128 tokens | 17 `3y…` tokens refused with a stale reason — see §8.6 |
| 2 | Performance 2 | `ta_perf2` | same 10 windows | same | HAVE | **yes**, same 111 | token list is byte-identical to `ta_perf` (measured) |
| 3 | Volatility | `ta_volatility` | `volatility_w`, `volatility_m` | 5,573 · 99.95% | HAVE | **yes**, 22 tokens | estimator is a proxy (mean true range), not Finviz's |
| 4 | RSI (14) | `ta_rsi` | `rsi_14` | 5,559 · 99.70% | HAVE | numeric only | its 12 tokens (`ob70`, `os30`, `nob60`…) are refused; ob70 → 271 rows, os30 → 619 |
| 5 | Gap | `ta_gap` | `gap_pct` | 5,573 · 99.95% | HAVE | numeric only | **`u` means *up* here, not *under*** — see §8.2 |
| 6 | 20-Day SMA | `ta_sma20` | `sma_20` | 5,554 · 99.61% | HAVE | numeric only | the row carries the **level**; Finviz's control is the distance |
| 7 | 50-Day SMA | `ta_sma50` | `sma_50` | 5,499 · 98.62% | HAVE | numeric only | same shape mismatch |
| 8 | 200-Day SMA | `ta_sma200` | `sma_200` | 5,205 · 93.35% | HAVE | numeric only | same |
| 9 | Change | `ta_change` | `change_pct` | 5,573 · 99.95% | HAVE | numeric only | |
| 10 | Change from Open | `ta_changeopen` | `change_from_open` | 5,573 · 99.95% | HAVE | numeric only | derived, exact |
| 11 | 20-Day High/Low | `ta_highlow20d` | — | **0%** | **MISSING** | no | no 20-day window anywhere; a 50-day high cannot be narrowed |
| 12 | 50-Day High/Low | `ta_highlow50d` | `high_50d`, `low_50d` | 4,187 · 75.09% | HAVE | **no** | depth-only; **24 rows have `high_50d` > 10× price** |
| 13 | 52-Week High/Low | `ta_highlow52w` | `pct_from_52w_high`, `high_52w`, `low_52w` | 5,573 · 99.95% | HAVE | numeric (high side) | only the **high** side has a derived distance; `low_52w` is a price. `pct_from_52w_high >= -0.1` → 76 rows vs `price >= 0.999×high` → 77: the two spellings disagree by one row |
| 14 | All-Time High/Low | `ta_alltime` | `all_time_high`, `all_time_low` | 4,186 · 75.07% | PARTIAL | **no** | **wrong on 355 symbols** (22 impossible + 46 absurd + 293 copied from the 50-day high) — §7.3 |
| 15 | Pattern | `ta_pattern` | — | **0%** | **MISSING** | no | no bars store, no detector; 28 tokens |
| 16 | Candlestick | `ta_candlestick` | `candlestick_pattern` | 2,375 · 42.59% | PARTIAL | **no** | 3 classes only: doji 1,151 / **engulfing** 755 / hammer 469. `engulfing` is **not** one of Finviz's 11 tokens; the other 9 classes are 0 |
| 17 | Beta | `ta_beta` | `beta_1y` | 5,560 · 99.71% | PARTIAL | numeric only | 1-year window; Finviz is 5-year monthly. Provider `beta_5_year` covers 4,927/5,000 but is not ingested |
| 18 | Average True Range | `ta_averagetruerange` | `atr_14` | 5,560 · 99.71% | HAVE | numeric only | price units — Finviz's labels are price units too ("Over 0.25"), so no unit defect |
| 19 | After-Hours Close | `ah_close` | — | **0%** | **MISSING** | no | Elite-only control **and** no source — §10 |
| 20 | After-Hours Change | `ah_change` | — | **0%** | **MISSING** | no | same |

**Verdict: HAVE 13 · PARTIAL 3 · MISSING 4.**

## 4. NEWS — 2 controls

| Finviz control | key | row field | coverage | status | wired | note |
|---|---|---|---|---|---|---|
| Latest News | `news_date` | `news_time` (ISO-8601 UTC) | 4,112 · 73.74% | PARTIAL | **no** | **one headline per symbol**, not a news store: 506 within 24 h, 2,984 within 7 d, 3,693 within 30 d, **419 older than 30 d** (oldest 2016-07-26). Every window token is therefore a statement about the *latest* item only |
| News Keywords | `news_keywords` | — | **0%** | **MISSING** | no | Elite-only **and** needs an article corpus; we keep one title per symbol, so a keyword search over it is a keyword search over 4,112 headlines, one per company — not Finviz's "titles from the last 24 hours that contain any keyword" |

**Verdict: HAVE 0 · PARTIAL 1 · MISSING 1.** The News tab's *date* half is buildable at day
granularity; the keyword half is not.

## 5. ETF — 1 control

| Finviz control | key | row field | coverage | status | wired | note |
|---|---|---|---|---|---|---|
| Held By | `etf_heldby` | — | **0%** | **MISSING** | no | Elite-only, and it needs a fund→constituent table. Nothing in the deployment has one: the ETF scan returns the funds themselves, `index_membership.json` holds six index member lists, and no holdings feed is ingested. `etf_heldby` is a *stocks* filter ("ETF ticker(s) → the stocks they hold"), so it cannot be answered from the fund side either |

## 6. The 23 Descriptive controls in the `ALL` union (re-measured)

| control | key | row field | coverage | status | wired | note |
|---|---|---|---|---|---|---|
| Average Volume | `sh_avgvol` | `avg_volume_30d` | 5,535 · 99.26% | HAVE | yes | 30-day only; "Avg Volume 3M" does not exist |
| Relative Volume | `sh_relvol` | `rel_volume_10d` | 5,566 · 99.82% | HAVE | yes | |
| Current Volume | `sh_curvol` | `volume` | 5,576 · 100% | HAVE | yes | no token vocabulary (Any + custom only) |
| Trades | `sh_trades` | — | 0% | **MISSING** | no | Elite-only, no print-count source — §10 |
| Price $ | `sh_price` | `price` | 5,576 · 100% | HAVE | yes | 39 tokens compiled |
| Target Price | `targetprice` | `target_price` | 3,864 · 69.30% | HAVE | yes | relative to the row's own price |
| IPO Date | `ipodate` | `listing_date` **0%** / `ipo_date` 4,187 | 0% on the filter path | PARTIAL | **no** | `listing_date` is in neither `SCAN_NULL_CELLS` nor the overlay's aliases, so the scan's null wins; depth's ISO `ipo_date` lands under its own key as text. Two spellings, neither filterable (one null, one a string) |
| Shares Outstanding | `sh_outstanding` | `shares_outstanding` | 5,538 · 99.32% | HAVE | yes | |
| Float | `sh_float` | `float_shares`, `float_pct` | 5,454 · 97.81% / 4,073 · 73.05% | HAVE | yes | `float_pct` is depth-only |
| Theme | `theme` | — | 0% | **MISSING** | no | 40 tokens, no classification column anywhere |
| Sub-theme | `subtheme` | — | 0% | **MISSING** | no | 268 tokens, same |
| Dilution Risk | `sh_dilution` | — | 0% | **MISSING** | no | not even in the 82-key catalog (the probe has 83); needs share-count history |
| Short Float | `sh_short` | `short_float` | 4,174 · 74.86% | HAVE | yes | 2 rows > 100% (EROC 280.89, GEF.B 135.83) |
| Analyst Recom. | `an_recom` | `recom_finviz` | 5,567 · 99.84% | HAVE | yes | 9 tokens |
| Option/Short | `sh_opt` | `optionable`, `shortable` | 4,176 · 74.89% | PARTIAL | **no** | values are the **strings** `"Yes"`/`"No"` (Yes 3,042 / No 1,134 / null 1,400; shortable No 26); key refused |
| Earnings Date | `earningsdate` | `earnings_date` | 4,626 · 82.96% | PARTIAL | yes (9 of 15) | next report only, 0 past dates |
| Dividend Yield | `fa_div` | `dividend_yield` | 2,203 · 39.51% | PARTIAL | yes | null ≠ non-payer |
| Exchange | `exch` | `exchange` | 5,576 · 100% | HAVE | yes | |
| Index | `idx` | `index_membership` | 2,734 · 49.03% | PARTIAL | yes | six lists, comma string |
| Sector | `sec` | `sector` | 5,576 · 100% | PARTIAL | yes | TradingView taxonomy, case-sensitive |
| Industry | `industry` | `industry` | 5,576 · 100% | PARTIAL | yes | 0 of 146 tokens match |
| Country | `geo` | `country` | 5,576 · 100% | PARTIAL | yes | 0 of 62 tokens match |
| Market Cap | `cap` | `market_cap_usd` | 5,576 · 100% | HAVE | yes | 14 buckets |

**Verdict: HAVE 11 · PARTIAL 8 · MISSING 4.**

## 7. Where the data is garbage (evidence, not adjectives)

### 7.1 `insider_trans` — withdrawn, and it should stay withdrawn
4,118 values: 1,705 positive, 1,347 exactly zero, 1,066 negative, 1,458 null; range **−210,474 …
+4,110.7**. `app/api/signals.py:62` already publishes the reason the two insider signals are
`available: false`: *"a vendor field whose own numbers cannot be read as a flow (TSLA 76.0, META 45.9,
GME 8.7 — a mixture of ownership levels and junk)"*. A bucket family over it would look precise and
mean nothing. **Disposition: keep refused, rewrite the reason to name the quality, do not wire.**

### 7.2 `inst_trans` — asymmetric by construction
4,162 values: **3,318 negative**, 843 positive, 1 zero, 1,414 null; median −13.96, min −26,683, max
+300. A field that is negative for 80 % of institutions is a 3-month net *change* in holdings, not a
flow, and not comparable to Finviz's "Institutional Transactions" (% of float bought/sold). Usable as
a coarse "institutions trimmed" bucket, not as a -50…+50 scale.

### 7.3 `all_time_high` — wrong on 355 distinct symbols
- **22 rows have `all_time_high` < `high_52w`** — impossible by definition (the all-time high cannot
  be below the 52-week high).
- **46 rows are absurd**: MRDN 1.64e12, TNXP 1.92e10, ORKA 7.59e7 — a share price in the trillions.
- **293 rows have `all_time_high` exactly equal to `high_50d`** (AAPL 351.73 = 351.73, NVDA 243.37 =
  243.37) — the all-time high and the 50-day high are the same number, which is either a genuine new
  high or a copy. 7 % exact equality is too high to be all genuine.
The three tests fire on **355 distinct symbols** (361 flags, six rows hit two), out of the 4,186 that
carry a value. A distance bucket survives that (a wrong level gives a wrong distance); the `nh`/`nl`
tokens do not, because a row below its 52-week high would match "New High".

### 7.4 Impossible percentages in the ownership and dividend cells
`insider_own` > 100 on 6 rows, `inst_own` > 130 on 25, `dividend_yield` > 100 on 2 (HTCR 175.68,
GMEX 105.93), `short_float` > 100 on 2. None of these breaks a bucket (they are just large), but a
"High (>90%)" institutional bucket is meaningless when 25 rows exceed 130.

### 7.5 `high_50d` / `low_50d` are price levels, and 24 rows are nonsense
`depth_fields` documents them as percent distances; the store holds price levels (`high_50d/price`
median 1.161, min 1.0). **24 rows carry `high_50d` > 10× `price`** (max ratio 1,250×), which no
50-day high can be. Any distance formula must guard `high > 0` and refuse an implausible ratio
rather than compute "99.9 % below the high".

### 7.6 `ps` can be `Infinity`
3 rows (`IMNM`, `INBX`, `IPSC`). `json.dumps` emits `Infinity`, which is not valid JSON per RFC 8259
and which strict clients reject. Pre-existing (the key is already wired), but a P/S bucket family
multiplies the exposure: 3 rows of 3,628.

### 7.7 A single headline is not a news store
`news_time` ranges 2016-07-26 … 2026-11-01T11:20Z; 419 rows are older than 30 days. "Latest News:
Today" means *the newest article we hold is from today*, which is exactly Finviz's semantics; "In
the last 5 minutes" over a store refreshed every ~13 minutes is a window that can only ever be
empty-ish, and on this Sunday afternoon it is **0 rows** for 5 m, 30 m and 1 h.

### 7.8 Candlesticks: 3 of 11, and one of ours is not theirs
`candlestick_pattern` covers doji, hammer and **engulfing**. Finviz's 11 tokens are
`lls, lus, h, ih, stw, stb, d, dd, gd, mw, mb`. `engulfing` has no Finviz token; `h`/`d` map cleanly;
the other 9 classes are 0 rows. Only 2 of 11 can ever match.

## 8. The token vocabularies, measured (this is where the work is)

1,514 named options across the three groups (785 fundamental, 717 technical, 12 news; the ETF key
has none). Run through the existing band parser:

| | tokens |
|---|---|
| `_band()` already reads them (plain `u5`/`o1.5`/`0to2`) | **695** |
| need a per-control map (`low`, `high`, `pos`, `ob70`, `pa10`, `b5h`, `todayafter`, …) | **819** |

Of those 819: **278** are already compiled by `choice_filters` (`ta_perf` 128, `ta_perf2` 128,
`ta_volatility` 22); **73** are refused by name in the plan (27 cross events, 32 insider
transactions, 9 `cy*`, 3 news minutes, 2 all-time new highs); the other **468** take a `_BANDS` row
or a small parser — except the tokens of the four keys the plan leaves refused at the *key* level
(`ta_pattern`, `ah_close`, `ah_change`, `ta_highlow20d`).

### 8.1 `_band` cannot read a negative threshold — 51 tokens
`app/screen/buckets.py:218` matches `(u|o)(\d+(?:\.\d+)?)`; `u-50` returns `None`, so
`fa_roa`, `fa_roe`, `fa_roi`, `fa_grossmargin`, `fa_opermargin`, `fa_netmargin`, `sh_insidertrans`
and `sh_insttrans` **cannot be built with the framework as it stands** — `_numeric()` raises at
import. Measured: `_band("u-5") is None`, `_band("o-10") is None`.

### 8.2 `u` means "up" in `ta_gap`, `ta_change`, `ta_changeopen` — 15 tokens each
The vendored labels are unambiguous: `u` = "Up", `u1` = "Up 1 %", `d` = "Down", `d20` = "Down 20 %".
In `sh_price`, `u5` = "Under 5". The same letter is two different words in two controls, and
`_band("u0")` would compile "Up 0 %" as `change < 0` — the exact opposite of the intended predicate.

### 8.3 `a…h` means "above the **Low**", not "above the high"
`ta_highlow*`: `b5h` = "5 % or more **below High**", `a5h` = "5 % or more **above Low**". One control,
two anchors, so the family needs both `high` and `low` per row; a single-column band is impossible.

### 8.4 `ta_sma20/50/200` — 84 tokens, 27 of them events
`pa`/`pb` (price above/below the average), `paN`/`pbN` (N % above/below — a distance, computable),
`sa`/`sb` (SMA20 above/below SMA50 — two columns, computable), and **`pc`, `pca`, `pcb`, `cross50`,
`cross50a`, `cross50b`, `cross200`, `cross200a`, `cross200b` = 9 per key, 27 total, which are *cross
events* and need yesterday's price and yesterday's average. No row carries them.**

### 8.5 `ta_rsi` — 12 tokens, four kinds
`obN` → `rsi >= N`; `osN` → `rsi <= N`; `nobN` → `rsi < N`; `nosN` → `rsi > N`. Labels confirm
("Not Overbought (<60)", "Not Oversold (>50)").

### 8.6 `ta_perf`'s 3-year window is refused on a false premise
`app/screen/choice_filters.py:152`:

```
"no screen row carries a three-year return: the parity scan requests "
"Perf.W/1M/3M/6M/YTD/Y/5Y/10Y (`ret_1w` … `ret_10y`), and `ret_3y` exists "
"only on the deep/quote path"
```

`ret_3y` **is** in `PARITY_FIELDS` (`"Perf.3Y": "ret_3y"`, `universe.py:238`) and the row carries it:
**5,573 non-null, 99.95 %**. 17 tokens per key (34 in total, `3ydown`, `3yup`, `3y1000o` …) are
refused for a column that is there. Cheapest fix in this document.

## 9. Two stores that contribute nothing (re-verified)

`screen_facts` and `fundamentals_snapshots` are still fully expired — **3,831 and 3,902 rows, newest
`expires_at` 2026-10-26T14:54:52Z and 2026-10-26T05:23:19Z**, six days before this measurement's
2026-11-01T13:11Z — so the two joins that `filter_fields` cites as the owners of `pe_forward`, `ps`,
`payout_ratio`, `short_float`, `insider_own` and `inst_own` are no-ops on every request. Those six
cells arrive from **depth** (through `SCAN_NULL_CELLS`), which is why `pe_forward` shows 64.10 %
rather than 0. The docs in `filter_fields` still describe the joins as the source; the numbers in §2
are depth's.

## 10. Out of scope, with the reason (not faked)

| control | why it cannot be built | what it would take |
|---|---|---|
| **Trades** (`sh_trades`) | Elite-only (`isEliteOnly: true` in the probe). Its value is a **trade count** (prints); the scan carries `volume` (shares) and `Value.Traded` (currency), the depth store has no print count, FINRA publishes short-sale *volume*. `volume / 100` would be a fabricated metric wearing Finviz's label | Polygon.io aggregates, IEX HIST or Databento |
| **After-Hours Close / Change** (`ah_close`, `ah_change`) | Elite-only in the probe, and the scan covers the regular session only. The provider *does* return `premarket_close`/`postmarket_close` (90.7 % / 100 % on a 400-row probe) but no collector stores either | ingest `postmarket_close` + `postmarket_change` into depth, then a price family |
| **News Keywords** (`news_keywords`) | Elite-only, and needs an article corpus with titles. We hold **one** headline per symbol | a news collector with N articles/symbol/day + a text index |
| **ETF Held By** (`etf_heldby`) | Elite-only, and needs a fund→constituent table. None is ingested; the six index lists are not holdings | a fund-holdings feed (SSGA/iShares already parsed for index membership — a fund census is a different job) |
| **Pattern** (`ta_pattern`) | 28 tokens; no bars store, no detector. `yahoo_chart` computes indicators and discards the series | persist daily bars + a pattern engine |
| **20-Day High/Low** (`ta_highlow20d`) | No 20-day window anywhere; a 50-day high cannot be narrowed to 20 | bars store (or a provider `price_20_day_high` column, if one exists — unprobed) |
| **Insider Transactions** (`sh_insidertrans`) | Data exists (4,118 rows) but is junk (§7.1) and the product already withdrew it | SEC EDGAR Form 4 rollup per symbol |
| **Sub-theme / Theme / Dilution** | No classification source; dilution needs share-count history | licensed taxonomy; SEC `dei:EntityCommonStockSharesOutstanding` history |

## 11. Buildable today with zero new data (the work this plan scopes)

| control | rows | what is missing |
|---|---|---|
| 11 EPS/sales growth keys | 2,383–4,038 | a field name, a band table, a bucket family |
| `fa_divgrowth` | 1,317–1,606 | same, plus **cy1…cy9 have no column** (only 1y/3y/5y exist) |
| `fa_ltdebteq` | 4,112 | one field mapping |
| `fa_epsrev` | 3,114 (both) | a 3-token family over two cells |
| `ta_highlow50d` | 4,187 | a two-anchor family |
| `ta_alltime` | 4,186 | same, plus the garbage guard (§7.3) |
| `ta_candlestick` | 2,375 | 2 of 11 classes; 9 refused |
| `news_date` | 4,112 | a day/moment family over `news_time` |
| all 21 already-live fundamental + 6 technical controls | 5,559–5,576 | a band table per control |
| `ta_perf` 3y (34 tokens) | 5,573 | delete a stale refusal (§8.6) |

## 12. Counts

| group | rows | HAVE | PARTIAL | MISSING |
|---|---|---|---|---|
| Fundamental | 37 | 20 | 17 | 0 |
| Technical | 20 | 13 | 3 | 4 |
| News | 2 | 0 | 1 | 1 |
| ETF | 1 | 0 | 0 | 1 |
| Descriptive (`ALL` union) | 23 | 11 | 8 | 4 |
| **total** | **83** | **44** | **29** | **10** |

Of the 60 controls in the four groups: **34 run today, 26 are refused, and 20 of those 26 already
have the cell on the row.**

## 13. Reproduction

```bash
cd /home/ubuntu/ztockz
.venv/bin/python /home/ubuntu/.hermes/cache/scratch/probe_cov.py        # universe → overlay → coverage
.venv/bin/python /home/ubuntu/.hermes/cache/scratch/probe_quality.py    # value shapes, garbage
.venv/bin/python /home/ubuntu/.hermes/cache/scratch/probe_news_quality.py
.venv/bin/python /home/ubuntu/.hermes/cache/scratch/probe_disposition.py
```

`probe_cov.py` performs one live scan (5,613 rows → 5,576) and then reads
`/home/ubuntu/ztockz-data/depth.json`; a scan block is the only failure mode, and the numbers above
are stamped with the store's own `as_of`.
