# Descriptive filters — data availability (measured 2026-11-01)

Scope: every control in the request's Descriptive panel — the order-by list (114 field names
once its multi-field items are expanded), the signal list, exchange, index, the 17 earnings buckets, the 41 price and 16 dividend buckets, the 39
themes + sub-theme + dilution risk, and the standalone controls (analyst recom, option/short,
short float, volume, target price, IPO date, shares, float, country, market cap).

## 0. What was measured

- **Universe**: live parity scan of `scanner.tradingview.com/america/scan` built with the
  selector's own arguments (`min_market_cap=0`, no subtype gate, region `us`). Provider
  `totalCount` **5,613**; `build_universe()` → **5,576 rows** — the same population
  `/market/exchanges` and `/market/signals` report. (With no cap gate at all the stock scan is
  5,715 rows; the 37 rows between 5,613 and 5,576 are issuer dedupe / rows with no reported cap.)
- **Depth store**: `/home/ubuntu/ztockz-data/depth.json`, `as_of 2026-11-01T11:38:42Z`,
  **4,217 symbols**, 112 keys. It joins **4,178** universe symbols by exact ticker spelling and
  **4,187 (75.09%)** counting the dot↔hyphen alias (`BRK.B`/`BRK-B` and eight more class
  shares). That ~75 % is the ceiling for every depth-only field below.
- **Pipeline**: `build_universe` → `app.screen.depth_overlay.overlay` → fundamentals join →
  facts join (the last two read `fundamentals_snapshots` / `screen_facts` in `ztockz.db`).
  Coverage = non-null after that pipeline.
- **Bucket/text vocabularies**: tested through `app.screen.selection.parse_selection` +
  `app.screen.filters`, i.e. exactly what `POST /screens/select` accepts or rejects.
- Probe scripts: `/home/ubuntu/.hermes/cache/scratch/probe{1..17}.py` (not committed).
- All percentages are of **5,576** unless the cell says otherwise.

Two facts change the reading of every table below:

1. **`screen_facts` and `fundamentals_snapshots` are 100 % expired.** 3,831 and 3,902 rows,
   newest `expires_at` 2026-10-26T14:54Z and 2026-10-26T05:23Z; both joins keep *fresh rows
   only* (`app/screen/facts.py:119` TTL 86,400 s). They contribute nothing today. The depth
   overlay already supplies the same cells, so the visible numbers below come from depth.
2. **Bucket tokens are not compiled.** Only `ta_perf`, `ta_perf2`, `ta_volatility` have a
   token→predicate compiler (`app/screen/choice_filters.py`). The other 47 field keys take an
   explicit value (43 numeric, 4 text): `{"key":"sh_price","op":"eq","value":"u5"}` → 422
   *"price expects a number, got 'u5'"*. The Finviz vocabularies exist as labels in
   `FILTER_CATALOG`, not as filters.

## 1. Verdict counts

| status | control rows |
|---|---|
| HAVE | 50 |
| PARTIAL | 36 |
| MISSING | 16 |
| **total** | **102** |

Counting rule: one row per table row in §2–§8; a row that names several order-by fields
with the same column and coverage is counted once; the four `available: false` signals
(insider buying/selling, upgrades, downgrades) are counted as MISSING. The 17 chart-pattern
signals share one row. Counting each Finviz price token separately would add 40 MISSING rows
of one identical defect (no token compiler), not 40 independent gaps.

## 2. Descriptive controls

| Finviz control | our field | coverage | status | source | note |
|---|---|---|---|---|---|
| Exchange (AMEX, CBOE, NASDAQ, NYSE, TXSE) | `exchange` (MIC) | 100% | HAVE | scan `exchange` → MIC | XNAS 3,308 / XNYS 2,009 / XASE 258 / XCBO 1. `FILTER_CATALOG['exch']` offers only amex/cboe/nasd/nyse — `txse` is absent (the vendored Finviz list has it), and the provider returns 0 TXSE listings in 5,715 US stock rows, so the option would match nothing today. |
| Index (S&P 500, NDX, DJIA, RUT) | `index_membership` | 49.03% (2,734) | PARTIAL | depth (official.py: SSGA SPY/MDY/SPSM/DIA, Nasdaq NDX, iShares IWM, Cboe) | Comma string, tokens: RUSSELL 2000 1,865 · S&P 600 586 · S&P 500 477 · S&P 400 390 · NDX 89 · DJIA 30 · RUT 9 (second spelling). The 2,842 nulls conflate two states: 1,392 universe symbols are outside the depth join, 1,450 are in it and belong to none of the six tracked indices. Key `idx` is refused — data exists, no wiring. |
| Sector | `sector` | 100% (21 values) | PARTIAL | scan `sector` | TradingView taxonomy, not Finviz's 11: Finance 1,559 · Health Technology 830 · Technology Services 576 · Electronic Technology 338 … Text `eq` is **case-sensitive**: `"Finance"` → 1,559, `"finance"` → 0. **0 of 11** Finviz tokens match a row exactly (all are lowercase); a case-insensitive compare would match 1 (`Utilities`) for 116 rows. |
| Industry | `industry` | 100% (129 values) | PARTIAL | scan `industry` | Same case defect: `"Semiconductors"` → 87, `"semiconductors"` → 0. **0 of 146** tokens match exactly; case-insensitive would match 10 (501 rows, 9.0%). |
| Country | `country` | 100% (55 values) | PARTIAL | scan `country` | "United States" 4,590; `geo=usa` → **0 rows** (no alias table for country). **0 of 62** tokens match exactly; case-insensitive would match 41 (797 rows, 14.3%). |
| Market Cap | `market_cap_usd` | 100% | HAVE | scan `market_cap_basic` | Token `mega` → 422; numeric ranges work. UI presets offer 4 bands (micro <2B … mega ≥200B). |
| Dividend Yield + 16 buckets | `dividend_yield` | 39.51% (2,203) | PARTIAL | scan `dividends_yield` (percent) | 78 rows = 0.0, 2,125 >0, 703 >5%, 174 >10%. "None (0%)" via `eq 0` returns **78** rows; `dividend_ttm == 0` covers **2,682**. The 3,373 nulls include real non-payers that cannot be named as such. |
| Short Float | `short_float` | 74.86% (4,174) | HAVE | scan null → depth `short_float` (Yahoo) | depth 4,204/4,217. Percent points (0.18 = 0.18%). Filter `sh_short` works. |
| Analyst Recommendation | `recom_finviz` | 99.84% (5,567) | HAVE | scan `Recommend.All` (−1..+1) → `3 − 2r` | 1..5 median 3.44. Token `strongbuy` → 422; numeric only. `recommendation_mark` (1..3) exists but is not the Finviz scale. |
| Option/Short | `optionable`, `shortable` | 74.89% each | PARTIAL | depth (Cboe HEAD; Yahoo) | optionable Yes 3,042 / No 1,134 / null 1,400; shortable Yes 4,150 / No 26. Key `sh_opt` is refused. |
| Earnings Date + buckets | `earnings_date` | 82.96% (4,626) | PARTIAL | scan `earnings_release_next_date` (epoch s) | **Next report only**: 0 past dates, range 2026-11-02…2027-05-24, 950 null. Past buckets are empty by construction. |
| Average Volume | `avg_volume_30d` | 99.26% (5,535) | HAVE | scan | 10-day window also carried (99.84%). "Avg Volume 3M" is not available — 30-day is the only window. |
| Relative Volume | `rel_volume_10d` | 99.82% (5,566) | HAVE | scan | Intraday volume ÷ 10-day average; null for 10 rows. |
| Current Volume | `volume` | 100% | HAVE | scan | |
| Trades | — | 0% | MISSING | none | No column: probe `number_of_trades` and `trades` return 0/400. Source: exchange trade counts (Polygon, IEX, Databento). |
| Price + 41 buckets | `price` | 100% | PARTIAL | scan `close` | `sh_price eq u5` → 422; `lt 5` works. UI presets: 4 bands only. |
| Target Price | `target_price` | 69.30% (3,864) | HAVE | scan `price_target_average` | depth 3,690/4,217. Median target/price 1.315; p99 21.8× (no clipping). |
| IPO Date | `listing_date` | 0% on filter path; 74.9% in depth | PARTIAL | depth `ipo_date` = `quoteType.firstTradeDateEpochUtc` | Scan sends the key as `null` on every row and `listing_date` is not in `SCAN_NULL_CELLS`, so the overlay cannot fill it (light rows do get it: AAPL 1980-12-12). depth formats: 4,188 ISO + 29 "Oct 18, 2016". Key `ipodate` refused. **Ticker collision:** `PAXG` = "Pine Tree Acquisition Corp.", ipo 2026-10-23, CIK 2076561 — not Paxos Gold. Same row also in the security master. |
| Shares Outstanding | `shares_outstanding` | 99.32% (5,538) | HAVE | scan | |
| Float + % buckets | `float_shares` / `float_pct` | 97.81% / 73.05% | HAVE | scan / depth | `float_pct` is depth-only, so it caps at the ~75% join. Percent buckets need a token compiler. |
| Theme (39) | — | 0% | MISSING | none | 40 tokens exist in `FILTER_CATALOG` (the 39 listed + a `Commodities` umbrella); no column anywhere in scan, depth, engine snapshot or SQLite. |
| Sub-theme (270) | — | 0% | MISSING | none | Same; 268 tokens in `FILTER_CATALOG`, no data. |
| Dilution Risk | — | 0% | MISSING | none | Needs share-count history. Derivable from SEC `dei:EntityCommonStockSharesOutstanding` per filing (same feed as `engine-snapshot.json` facts) but no history is ingested. |
| Employees | `employees` | 71.52% (3,988) | HAVE | depth `employees` | Median 724; max 2.1M; no zeros. (`number_of_employees` in the scan would lift this to 93.9% on a 3,896-row probe.) |

## 3. Order-by fields (114 names, multi-field items expanded)

Sorting is a separate hard limit: `app/screen/rank.py` exposes **3 sorts** —
`market_cap`, `turnover`, `gainers`. Every other name below is refused as a sort
("unknown sort") regardless of data. Data coverage is what the table measures.

| Order-by field(s) | row field | coverage | status / note |
|---|---|---|---|
| Ticker, Company, Sector, Industry, Country, Exchange | `symbol`, `name`, `sector`, `industry`, `country`, `exchange` | 100% | HAVE — text vocabulary caveats in §2 |
| Index | `index_membership` | 49.03% | PARTIAL — compound tokens, 2 spellings of Russell |
| Market Cap | `market_cap_usd` | 100% | HAVE |
| Dilution Risk | — | 0% | MISSING |
| P/E, Forward P/E, PEG, P/S, P/B, P/Cash, P/FCF | `pe_ttm` 52.78 · `pe_forward` 64.10 · `peg` 41.41 · `ps` 65.06 · `pb` 86.69 · `price_to_cash` 74.66 · `pfcf` 44.15 | 41–87% | HAVE — all seven are scan/depth cells; `peg` and `pfcf` are "definition" quality (median deviation 29.6% / 30.5%, `depth_fields.py`) |
| Dividend Yield, Payout Ratio | `dividend_yield` 39.51 · `payout_ratio` 60.94 | — | PARTIAL — yield is reporting-only (null ≠ non-payer); payout is Yahoo trailing-EPS based, Finviz uses forward EPS |
| EPS (TTM), Income, Sales | `eps` 73.30 · `net_income` 74.34 · `sales` 72.92 | 72–74% | HAVE |
| EPS Est Next Q | `eps_forecast_next_fq` | 78.37% | HAVE |
| EPS Growth This Year, Next Year | `eps_this_y` 59.13 · `eps_next_y` 60.33 | ~60% | HAVE (depth `eps_this_y`/`eps_next_y`) — the API refuses these keys, the data exists |
| EPS Growth Past 3Y, Past 5Y, Next 5Y | `eps_past_3y` 48.73 · `eps_past_5y` 42.74 · `eps_next_5y` 43.88 | 42–49% | PARTIAL — Yahoo dropped +5y/-5y estimate periods; `eps_past_5y` is `missing` per `depth_fields` yet 2,383 rows carry it |
| EPS QoQ, EPS YoY TTM | `eps_qoq` 72.42 · `eps_yoy_ttm` 69.49 | — | HAVE |
| Sales Growth 3Y, 5Y, QoQ, YoY TTM | `sales_past_3y` 60.15 · `sales_past_5y` 55.90 · `sales_qoq` 64.54 · `sales_yoy_ttm` 63.61 | 56–65% | HAVE/PARTIAL — `sales_past_5y` quality `missing` |
| EPS Surprise, Revenue Surprise | `eps_surprise` 64.58 · `revenue_surprise` 55.85 | — | HAVE — `revenue_surprise` marked `missing` in `depth_fields` but populated |
| Shares Outstanding, Shares Float | `shares_outstanding` 99.32 · `float_shares` 97.81 | — | HAVE |
| Float/Outstanding | `float_pct` | 73.05% | HAVE (depth-only) |
| Insider Ownership, Institutional Ownership | `insider_own` 75.07 · `inst_own` 75.07 | 75% | HAVE — definition differs (Yahoo holders include entities Finviz does not) |
| Insider Transactions, Institutional Transactions | `insider_trans` 73.85 · `inst_trans` 74.64 | 74% | PARTIAL — present but distrusted: `signals.py` cites TSLA 76.0 / META 45.9 / GME 8.7 as "a mixture of ownership levels and junk" |
| Short Interest Share, Short Interest Ratio, Short Interest | `short_float` 74.86 · `short_ratio` 75.00 · `short_interest` 74.95 | 75% | HAVE — short interest is FINRA-sourced, median 2.77M shares, 125 rows < 1,000 |
| Earnings Date | `earnings_date` | 82.96% | PARTIAL — next report only, no time of day |
| Latest News | `news_time`, `news_title` | 73.74% | PARTIAL — latest headline only; 536 rows <24 h, 2,984 <7 d |
| ROA, ROE, ROIC | `roa` 89.92 · `roe` 84.06 · `roic` 87.54 | 84–90% | HAVE — ROA/ROIC use different denominators (`definition`) |
| Current Ratio, Quick Ratio | 95.48 · 84.09 | — | HAVE |
| LT Debt/Equity, Total Debt/Equity | `lt_debt_to_equity` 73.74 · `debt_to_equity` 88.76 | — | HAVE — LT D/E exists in depth; the filter key is refused |
| Gross, Operating, Net Margin | 70.39 · 81.64 · 81.64 | 70–82% | HAVE |
| Analyst Recommendation | `recom_finviz` | 99.84% | HAVE |
| Perf Week/Month/Quarter/Half/YTD/Year/3Y/5Y/10Y | `ret_1w` 99.86 · `ret_1m`/`ret_3m`/`ret_6m`/`ret_1y`/`ret_3y`/`ret_5y`/`ret_10y` 99.95 · `ret_ytd` 100 | 99.9% | HAVE |
| Beta | `beta_1y` | 99.71% | PARTIAL — 1-year window vs Finviz 5-year monthly; provider `beta_5_year` covers 4,927/5,000 (98.5%) but is not ingested |
| ATR | `atr_14` | 99.71% | HAVE (price units, not percent) |
| Volatility Week, Month | `volatility_w` 99.95 · `volatility_m` 99.95 | — | HAVE — estimator is a proxy (mean true range, not Finviz's) |
| 20/50/200-Day SMA relative | `sma_20` 99.61 · `sma_50` 98.62 · `sma_200` 93.35 | 93–100% | PARTIAL — the row carries the **level**, not the distance; depth's `sma20` is a percent, so the two stores disagree in shape |
| 50-Day High/Low relative | `high_50d` / `low_50d` | 75.09% | PARTIAL — depth-only; stored as price **levels** despite `depth_fields` documenting a percent formula |
| 52-Week High/Low relative | `high_52w` 99.95 · `low_52w` 99.95 · `pct_from_52w_high` 99.95 | — | PARTIAL — only the high side has a derived distance; the low side is absolute |
| 52-Week Range | `week_52_range` | 75.09% | HAVE — stored as width in percent (AAC 2.51), not the "low .. high" string the catalog implies |
| All-Time High/Low relative | `all_time_high` / `all_time_low` | 75.07% | PARTIAL — levels only; key `ta_alltime` refused though the data is on the row |
| RSI(14) | `rsi_14` | 99.70% | HAVE |
| Avg Volume 3M, Relative Volume | `avg_volume_30d` 99.26 · `rel_volume_10d` 99.82 | — | PARTIAL — 30-day, not 3-month |
| Change %, Change from Open %, Gap, Volume | `change_pct` 99.95 · `change_from_open` 99.95 · `gap_pct` 99.95 · `volume` 100 | — | HAVE |
| Candlestick | `candlestick_pattern` | 42.59% | PARTIAL — 3 classes only (doji 1,151 / engulfing 755 / hammer 469) vs Finviz's 11; `engulfing` is not one of Finviz's 11 |
| Open, High, Low, Previous Close | — | 0% | MISSING — no such key on the row; depth carries neither (quote hub owns price/prev close, and `open`/`high`/`low` are not in the 112 depth keys) |
| Price | `price` | 100% | HAVE |
| Target Price | `target_price` | 69.30% | HAVE |
| IPO Date | `listing_date` | 0% / 74.9% in depth | PARTIAL — see §2 |
| Book Value/Share, Cash/Share | 75.02 · 74.98 | 75% | HAVE |
| Dividend, Dividend Ex-Date, Dividend TTM | 28.80 · 34.58 · 75.09 | — | PARTIAL — `dividend` (per-share amount) only for 1,606 rows; TTM is 4,187 |
| Dividend Growth 1Y, 3Y, 5Y | 28.80 · 24.64 · 23.62 | 24–29% | PARTIAL — chart-event CAGR, dividend payers in depth only; key `fa_divgrowth` refused |
| Employees | `employees` | 71.52% | HAVE (depth) |
| Income, Sales | `net_income` 74.34 · `sales` 72.92 | 72–74% | HAVE |
| Enterprise Value, EV/EBITDA, EV/Sales | 75.04 · 49.41 · 64.90 | 49–75% | HAVE — EV/EBITDA quality `equivalent` |
| Optionable, Shortable | `optionable` / `shortable` | 74.89% | PARTIAL — data present, keys refused |
| News URL, News Title, News Time | `news_url`/`news_title`/`news_time` | 73.74% | PARTIAL — one headline per symbol |
| Daily Digest | — | 0% | MISSING — owner is `product`, nothing generates it |

## 4. Exchange and index vocabularies

| Control | rows | status | note |
|---|---|---|---|
| AMEX | 258 | HAVE | XASE |
| CBOE | 1 | HAVE | XCBO |
| NASDAQ | 3,308 | HAVE | XNAS |
| NYSE | 2,009 | HAVE | XNYS |
| TXSE | 0 | MISSING | No Texas Stock Exchange listing in the 5,715-row provider stock universe; no MIC in `MIC_BY_EXCHANGE` and no `txse` token in `FILTER_CATALOG` (Finviz's own list has it). A 1-line map + option change when listings appear. |
| S&P 500 | 477 | PARTIAL | Token in `index_membership`; source SSGA SPY holdings. Not filterable (`idx` refused). |
| NASDAQ 100 | 89 | PARTIAL | Nasdaq list (101 rows upstream); 89 present in the 4,217-symbol depth set. |
| DJIA | 30 | PARTIAL | Complete count, same wiring gap. |
| RUSSELL 2000 | 1,874 | PARTIAL | 1,865 "RUSSELL 2000" + 9 "RUT"; iShares IWM holdings. |

## 5. Earnings-date buckets

`earnings_date` on the row is the provider's **next** release (epoch seconds), 4,626 rows.
The scan also returns `earnings_release_date` (the **last** report): 1,424 rows dated within
the last 30 days on a 5,000-row sample. It is not one of `PARITY_COLUMNS`, so it is never
requested.

| Bucket(s) | status | measured / why |
|---|---|---|
| Any | HAVE | 4,626 (82.96%) |
| Today, Tomorrow, Yesterday | PARTIAL | Date comparisons work on the 4,626; "yesterday" matches 0 rows because the column is next-only |
| Next 5 Days, This Week, Next Week, This Month | HAVE (data) | all derivable from the next date; no token compiler (`earningsdate eq today` → 422) |
| Previous 5 Days, Previous Week | MISSING on the row | Needs `earnings_release_date` (last report). Provider has it; 1,424 rows within 30 days. |
| Today/Tomorrow/Yesterday BMO and AMC (8 tokens) | PARTIAL | Depth holds 257 BMO/AMC strings ("Aug 06 BMO"): 235 past, **22 upcoming (to 2026-11-16)**. After the scan overlay **0 survive** — the epoch next-date wins. Provider flag `earnings_release_next_time`: 1 for 1,053 rows (all 21:00–21:30 UTC), −1 for 787 (11:00–14:30 UTC), 0 for 2,479 (forced 12:00 UTC placeholder) in a 5,000-row sample. Not ingested. |

## 6. Price and dividend bucket vocabularies

| Control | status | measured |
|---|---|---|
| Price: 41 tokens (39 buckets: Under $1/2/3/4/5/7/10/15/20/30/40/50; Over $1…100; 10 ranges; + Any and custom) | PARTIAL | Data 100%; tokens rejected (`sh_price eq u5` → 422 "price expects a number"). Every bucket is expressible as `gte`/`lt`; the UI ships 4 presets, the API compiles none. |
| Dividend: 16 tokens (14 buckets: None, Positive, High >5%, Very High >10%, Over 1%…10%; + Any and custom) | PARTIAL | `dividend_yield`: 2,203 reported / 78 zero / 2,125 positive / 703 >5% / 174 >10%. "None (0%)" = 78 rows, but 2,682 rows have `dividend_ttm == 0`; the 3,373 nulls are unclassifiable. Token `high` → 422. |
| Market Cap: 16 tokens (mega…nano, over/under) | PARTIAL | Same: 100% data, 0 compiled tokens. |
| Average/Relative/Current Volume buckets | PARTIAL | Data 99–100%, tokens rejected. |

## 7. Theme, sub-theme, dilution risk

| Control | status | note |
|---|---|---|
| Theme — all 39: Aging Population & Longevity, Agriculture & FoodTech, Artificial Intelligence, Autonomous Systems, Big Data, Biometrics, Cloud Computing, Commodities-Agriculture/-Energy/-Metals, Consumer Goods, Crypto & Blockchain, Cybersecurity, Defense & Aerospace, Digital Entertainment, E-commerce, Education Technology, Electric Vehicles, Energy-Renewable, Energy-Traditional, Environmental Sustainability, FinTech, Hardware, Healthcare & Biotech, Healthy Food & Nutrition, Industrial Automation, Internet of Things, Nanotechnology, Quantum Computing, Real Estate & REITs, Robotics, Semiconductors, Smart Home, Social Media, Software, Space Tech, Telecommunications, Transportation & Logistics, Virtual & Augmented Reality, Wearables | MISSING | 40 tokens in `FILTER_CATALOG` (the 39 above + `Commodities`), zero rows classified. No column in scan/depth/engine snapshot/SQLite. GICS→theme mapping is editorial, not derivable. |
| Sub-theme (270 tokens) | MISSING | Same. |
| Dilution Risk | MISSING | No share-count history. Closest source: SEC XBRL `dei:EntityCommonStockSharesOutstanding` across filings (the `engine-snapshot.json` facts already carry one `shares_outstanding` per symbol, 3,340 of 4,197) — a YoY change needs the filing history, which is fetched and discarded. |

## 8. Signals: 13 ids vs the 33 requested tokens

The request says 27 signals but names **33**. Live ids from `app/api/signals.py` (`SIGNALS`),
row counts computed on today's 5,576-row universe with the same predicates.

| Finviz signal | our id | rows today | status |
|---|---|---|---|
| Top Gainers | `gainers` | 1,514 | HAVE |
| Top Losers | `losers` | 3,879 | HAVE |
| New High | `new_high` | 77 | HAVE (52-week high only) |
| New Low | `new_low` | 56 | HAVE (52-week low only) |
| Most Volatile | `most_volatile` | 5,573 | HAVE (`volatility_w`, ATR/price fallback) |
| Most Active | `most_active` | 5,576 | HAVE (any reported volume) |
| Unusual Volume | `unusual_volume` | 160 | HAVE (vol/avg ≥ 3) |
| Overbought | `overbought` | 271 | HAVE (RSI ≥ 70) |
| Oversold | `oversold` | 619 | HAVE (RSI ≤ 30) |
| Downgrades | `downgrades` | 0 | **unavailable** — "no analyst ratings source"; `recommendation` is a level, not a revision |
| Upgrades | `upgrades` | 0 | **unavailable** — same reason |
| Earnings Before | — | 787 in a 5,000-row sample | PARTIAL — no id; provider `earnings_release_next_time = −1` (11:00–14:30 UTC) not ingested |
| Earnings After | — | 1,053 in the same sample | PARTIAL — same, flag `1` (21:00–21:30 UTC) |
| Recent Insider Buying | `insider_buying` | 0 | **unavailable** — vendor `insider_trans` rejected ("TSLA 76.0, META 45.9, GME 8.7 — a mixture of ownership levels and junk"); Form 4 data exists per company only |
| Recent Insider Selling | `insider_selling` | 0 | **unavailable** — same |
| Major News | — | 536 (<24 h) | PARTIAL — headlines exist (`news_title` 73.74%), no importance classifier, no id |
| Horizontal S/R, TL Resistance, TL Support, Wedge Up, Wedge Down, Triangle Ascending, Triangle Descending, Wedge, Channel Up, Channel Down, Channel, Double Top, Double Bottom, Multiple Top, Multiple Bottom, Head & Shoulders, Head & Shoulders Inverse (17) | — | 0 | MISSING — no pattern engine and no persisted OHLC bars; `yahoo_chart` enrichment computes indicators and discards the series |

## 9. Buildable today with zero new data

These have a populated column and only need wiring (a filter key, a sort entry, or a token
compiler):

- Index membership (2,734 rows, official sources) — `idx` refused today.
- Optionable / Shortable (4,176 rows) — `sh_opt` refused today.
- IPO date via depth `ipo_date` (4,217 symbols) — needs `listing_date` added to
  `SCAN_NULL_CELLS` so the overlay can fill it, plus ISO/long-form normalisation.
- All-Time High/Low (4,186) and 50-Day High/Low (4,187) — keys `ta_alltime`,
  `ta_highlow50d` refused; data already on the row.
- Dividend growth 1Y/3Y/5Y (1,606 / 1,374 / 1,317) — `fa_divgrowth` refused though depth has it.
- EPS/sales growth keys (2,383–4,038 rows) — `fa_epsyoy*`, `fa_sales*` refused though depth has them.
- Earnings & revenue surprise (3,601 / 3,114) — `fa_epsrev` refused though depth has it.
- LT Debt/Equity (4,112) — `fa_ltdebteq` refused though depth has it.
- Short float, insider/institutional ownership (4,174 / 4,186 / 4,186) — already wired.
- Every numeric order-by in §3 — the column exists; only 3 sorts are exposed.

## 10. Buildable with derivation (formula named)

| Control | formula | inputs / coverage |
|---|---|---|
| Analyst Recommendation (1..5) | `3 − 2 × recommendation` | already implemented (`app/screen/derived.py`), 5,567 rows |
| Change from Open % | `((1 + change_pct/100) / (1 + gap_pct/100) − 1) × 100` | implemented, 5,573 rows |
| 52-Week High distance | `(price / high_52w − 1) × 100` | implemented, 5,573 rows |
| 52-Week Low distance | `(price / low_52w − 1) × 100` | one line, same inputs (5,573) |
| 50-Day High/Low distance | `(price / high_50d − 1) × 100`, `(price / low_50d − 1) × 100` | 4,187 rows |
| All-Time High/Low distance | `(price / all_time_high − 1) × 100` etc. | 4,186 rows |
| SMA distance (20/50/200) | `(price / sma_20 − 1) × 100` | 5,554 / 5,499 / 5,205 |
| Float/Outstanding | `float_shares / shares_outstanding × 100` | 5,454 / 5,538 → ~5,430 |
| Dividend "None" | `dividend_ttm == 0` (not `yield == 0`) | 2,682 rows |
| Payout Ratio | `dividend_ttm / eps` | 1,505 payers with EPS |
| Dilution Risk (proxy) | `shares_outstanding_t / shares_outstanding_t-4q − 1` | needs SEC share history (not ingested) |

## 11. Needs a new source

| Control | missing metric | named source |
|---|---|---|
| Theme (39), Sub-theme (270) | classification | Finviz taxonomy has no public feed; options are a licensed dataset, an editorial GICS→theme map, or a vendor (FactSet RBICS, S&P thematic) |
| Dilution Risk | share-count history | SEC XBRL `dei:EntityCommonStockSharesOutstanding` via `companyfacts` (same API as `engine-snapshot.json`) |
| Trades | trade count per session | Polygon.io aggregates, IEX HIST, or Databento |
| Chart-pattern signals (17) | OHLC bars persisted + detector | Yahoo chart (already fetched by `yahoo_chart` enrichment, series discarded); needs a bars store + pattern code |
| Earnings Before/After | release time of day | provider `earnings_release_next_time` (already returned: 1,053 AMC / 787 BMO) or Nasdaq/Zacks calendar |
| Previous 5 Days / Previous Week earnings | last report date | provider `earnings_release_date` (1,424 rows in last 30 days) |
| Open, High, Low, Previous Close | intraday OHLC | quote hub owns price/prev close (`QUOTE_OWNED_FIELDS`) but the row never receives them; scan has no open/high/low column |
| Candlestick (11 patterns) | 8 more classifiers | same bars store; today doji/hammer cover 2 of Finviz's 11, `engulfing` is extra |
| Daily Digest | editorial summary | no generator; `daily_digest` is `product`-owned and always null |
| Upgrades/Downgrades | analyst rating revisions | no ratings source in the deployment (Benzinga/Zacks/Finnhub) |
| Insider Buying/Selling (universe-wide) | Form 4 aggregation | SEC EDGAR Form 4 (already cached per CIK: 1,046 files under `ztockz-data/cache`) but no cross-symbol rollup |
| Avg Volume 3M | 3-month average | provider `average_volume_...` has 10d/30d only; needs a bars store |
| TXSE | venue | provider returns 0 TXSE listings today |

## 12. Named defects with evidence

1. **`ipo_date` returns a ticker collision, not a date.** The depth row for `PAXG` is
   "Pine Tree Acquisition Corp.", ipo_date `2026-10-23`, CIK 2076561. Paxos Gold is not in
   the security master at all; the same symbol/CIK pair is in `engine-snapshot.json`, so the
   error is upstream of depth. Any IPO-date filter must de-duplicate by CIK, not ticker.
2. **Text filters are case-sensitive.** `sector eq "Finance"` → 1,559 rows;
   `"finance"` → 0; `"FINANCE"` → 0. Every Finviz token in `FILTER_CATALOG` is lowercase and
   every provider label is Title Case, so **all** sector, industry and country tokens return
   an empty screen: `sector technology` 0, `industry semiconductors` 0, `country usa` 0
   (with 4,590 US rows present).
3. **The universe is TradingView's taxonomy, not Finviz's.** 21 sectors / 129 industries /
   55 countries vs Finviz 11 / 148 / 64. No sector, industry or country token matches a row
   exactly; even case-insensitively only 1/11, 10/146 and 41/62 do (116 / 501 / 797 rows).
4. **Bucket vocabularies are inert.** 43 of the 50 usable keys take a numeric value (4 are
   text, 3 are choice); `u5`, `mega`, `high`, `todaybefore`, `o3`, `sa20`, `nh`, `strongbuy`
   all produce 422 "expects a number". Only `ta_perf`, `ta_perf2`, `ta_volatility` compile tokens.
5. **Ordering is 3-way.** `rank.SORTS = ("market_cap", "turnover", "gainers")`. Of the 114
   requested names only Market Cap and Change % map to an exposed sort; the other 112 are
   refused as sorts even where the column exists.
6. **`listing_date` cannot be filled by the overlay.** It is present-but-`null` on the parity
   row and missing from `SCAN_NULL_CELLS` (`['cash_per_share','enterprise_value',
   'insider_own','inst_own','payout_ratio','pe_forward','ps','short_float']`), so 4,217
   depth IPO dates never reach a selectable row. Light rows do get them.
7. **Earnings dates are next-only.** 4,626 future dates, **0 past**, max 2027-05-24. Every
   "Yesterday / Previous" bucket is structurally empty. The 257 BMO/AMC strings in depth
   (235 past, 22 upcoming) are Finviz-parse residue and none survive the overlay.
8. **Two sector vocabularies coexist.** Screen rows carry TradingView sectors ("Electronic
   Technology"); depth rows carry Yahoo sectors ("Financials", "Industrials"). A join by
   symbol is safe, a join by sector name is not.
9. **Two legacy stores are dead weight.** `screen_facts` (3,831) and `fundamentals_snapshots`
   (3,902) are 100 % expired; their joins are no-ops on every request. The depth overlay
   already covers `short_float`, `ps`, `pe_forward`, `payout_ratio`.
10. **`depth_fields` quality labels are out of date.** `eps_past_5y`, `eps_next_5y`,
    `revenue_surprise`, `fa_ltdebteq` are marked `missing`/refused but are populated
    (2,383 / 2,447 / 3,114 / 4,112 rows). Conversely `high_50d`/`low_50d` are documented as
    percent distances but stored as price levels.
11. **Signals are 9 of 33.** 4 ids are `available: false`; 2 more (earnings before/after)
    have no id; 17 pattern signals have no engine and no bars.

## 13. Suggested order of work

1. Case-insensitive text `eq` + token aliases for sector/industry/country (today 0 of 11
   sector, 0 of 146 industry and 0 of 62 country tokens match a single row; no new data).
2. Compile the bucket vocabularies already in `FILTER_CATALOG` (price, market cap, dividend,
   volume, earnings, recom, SMA, high/low) into numeric ranges — the same pattern
   `choice_filters.py` already uses.
3. Wire the keys whose data is on the row: `idx`, `sh_opt`, `ta_alltime`, `ta_highlow50d`,
   `fa_divgrowth`, `fa_eps*`, `fa_sales*`, `fa_epsrev`, `fa_ltdebteq`, `ipodate`
   (+ `listing_date` in `SCAN_NULL_CELLS`).
4. Generalise `SORTS` to any numeric column (the ranking already breaks ties on symbol).
5. Ingest three provider columns that unblock whole families: `earnings_release_next_time`
   (BMO/AMC), `earnings_release_date` (past earnings), `premarket_close`/`postmarket_close`
   (after-hours, 90.7 % / 100 % on a 400-row probe).
6. Then the true new-source work: themes, dilution history, bars/patterns, trade counts.
