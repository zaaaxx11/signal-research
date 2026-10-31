/**
 * A representative slice of `GET /screens/select/schema` (HEAD d43aafc).
 *
 * The **key tables are complete** — all 50 usable, 3 choice and 32 refused keys —
 * because the registry test asserts its key set equals the schema's usable set;
 * that assertion is only meaningful over the whole catalogue. Choice vocabularies
 * are trimmed to the tokens the tests exercise (the registry copies options
 * verbatim; `scripts`/the live test checks the real vocabularies).
 */

import type { FilterSchema } from "../schema";

type UsableRow = [key: string, label: string, tab: string, kind: "number" | "text" | "bool", field: string];
type ChoiceRow = [key: string, label: string, tab: string, tokens: string[]];
type RefusedRow = [key: string, label: string, tab: string, reason: string];
type AliasRow = [name: string, field: string, kind: string, label: string | null];

export const USABLE_KEYS: UsableRow[] = [
  ["exch", "Exchange", "descriptive", "text", "exchange"],
  ["sec", "Sector", "descriptive", "text", "sector"],
  ["industry", "Industry", "descriptive", "text", "industry"],
  ["geo", "Country", "descriptive", "text", "country"],
  ["cap", "Market Cap.", "descriptive", "number", "market_cap_usd"],
  ["fa_div", "Dividend Yield", "descriptive", "number", "dividend_yield"],
  ["sh_short", "Short Float", "descriptive", "number", "short_float"],
  ["an_recom", "Analyst Recom.", "descriptive", "number", "recom_finviz"],
  ["earningsdate", "Earnings Date", "descriptive", "number", "earnings_date"],
  ["sh_avgvol", "Average Volume", "descriptive", "number", "avg_volume_30d"],
  ["sh_relvol", "Relative Volume", "descriptive", "number", "rel_volume_10d"],
  ["sh_curvol", "Current Volume", "descriptive", "number", "volume"],
  ["sh_price", "Price $", "descriptive", "number", "price"],
  ["targetprice", "Target Price", "descriptive", "number", "target_price"],
  ["sh_outstanding", "Shares Outstanding", "descriptive", "number", "shares_outstanding"],
  ["sh_float", "Float", "descriptive", "number", "float_shares"],
  ["fa_pe", "P/E", "fundamental", "number", "pe_ttm"],
  ["fa_fpe", "Forward P/E", "fundamental", "number", "pe_forward"],
  ["fa_peg", "PEG", "fundamental", "number", "peg"],
  ["fa_ps", "P/S", "fundamental", "number", "ps"],
  ["fa_pb", "P/B", "fundamental", "number", "pb"],
  ["fa_pc", "Price/Cash", "fundamental", "number", "price_to_cash"],
  ["fa_pfcf", "Price/Free Cash Flow", "fundamental", "number", "pfcf"],
  ["fa_evebitda", "EV/EBITDA", "fundamental", "number", "ev_ebitda"],
  ["fa_evsales", "EV/Sales", "fundamental", "number", "ev_to_sales"],
  ["fa_roa", "Return on Assets", "fundamental", "number", "roa"],
  ["fa_roe", "Return on Equity", "fundamental", "number", "roe"],
  ["fa_roi", "Return on Invested Capital", "fundamental", "number", "roic"],
  ["fa_curratio", "Current Ratio", "fundamental", "number", "current_ratio"],
  ["fa_quickratio", "Quick Ratio", "fundamental", "number", "quick_ratio"],
  ["fa_debteq", "Debt/Equity", "fundamental", "number", "debt_to_equity"],
  ["fa_grossmargin", "Gross Margin", "fundamental", "number", "gross_margin"],
  ["fa_opermargin", "Operating Margin", "fundamental", "number", "oper_margin"],
  ["fa_netmargin", "Net Profit Margin", "fundamental", "number", "profit_margin"],
  ["fa_payoutratio", "Payout Ratio", "fundamental", "number", "payout_ratio"],
  ["sh_insiderown", "Insider\nOwnership", "fundamental", "number", "insider_own"],
  ["sh_instown", "Institutional\nOwnership", "fundamental", "number", "inst_own"],
  ["ta_rsi", "RSI (14)", "technical", "number", "rsi_14"],
  ["ta_gap", "Gap", "technical", "number", "gap_pct"],
  ["ta_sma20", "20-Day Simple Moving Average", "technical", "number", "sma_20"],
  ["ta_sma50", "50-Day Simple Moving Average", "technical", "number", "sma_50"],
  ["ta_sma200", "200-Day Simple Moving Average", "technical", "number", "sma_200"],
  ["ta_change", "Change", "technical", "number", "change_pct"],
  ["ta_changeopen", "Change from Open", "technical", "number", "change_from_open"],
  ["ta_highlow52w", "52-Week High/Low", "technical", "number", "pct_from_52w_high"],
  ["ta_beta", "Beta", "technical", "number", "beta_1y"],
  ["ta_averagetruerange", "Average True Range", "technical", "number", "atr_14"],
];

export const CHOICE_KEYS: ChoiceRow[] = [
  ["ta_perf", "Performance", "technical", ["dup", "d15u", "1w10u", "52wup", "ytd5o", "5y100o"]],
  ["ta_perf2", "Performance 2", "technical", ["dup", "d15u", "1w10u", "52wup", "ytd5o", "5y100o"]],
  ["ta_volatility", "Volatility", "technical", ["wo2", "wo3", "wo4", "wo5", "wo6", "wo7"]],
];

export const REFUSED_KEYS: RefusedRow[] = [
  ["idx", "Index", "descriptive", "needs index membership (S&P 500, Nasdaq-100, Dow, Russell): the scan has no index-constituent column, and a market-cap threshold is not a membership test"],
  ["sh_opt", "Option/Short", "descriptive", "needs an optionable/shortable flag: the scan carries no such column"],
  ["sh_trades", "Trades", "descriptive", "needs a trade-count column: the scan carries no trades counter"],
  ["ipodate", "IPO Date", "descriptive", "needs an IPO-date column the scanner fills: `ipo_date` answered null for all 5,720 US rows (and 96,278 global rows) when probed live, so a filter on it can only return an empty screen"],
  ["theme", "Theme", "descriptive", "needs theme/sub-theme classification: the scan carries no theme column"],
  ["subtheme", "Sub-theme", "descriptive", "needs theme/sub-theme classification: the scan carries no theme column"],
  ["fa_divgrowth", "Dividend Growth", "fundamental", "needs a dividend growth rate: the scan carries a dividend yield only (`dividends_yield`), which is not a growth rate"],
  ["fa_epsyoy", "EPS Growth\nThis Year", "fundamental", "needs a growth-rate column (EPS and sales growth this year, next year, quarterly, 3-5 year and TTM): the scan carries forward EPS and revenue *levels* for the next FY/FQ, and a level is not a growth rate"],
  ["fa_epsyoy1", "EPS Growth\nNext Year", "fundamental", "needs a growth-rate column (EPS and sales growth this year, next year, quarterly, 3-5 year and TTM): the scan carries forward EPS and revenue *levels* for the next FY/FQ, and a level is not a growth rate"],
  ["fa_epsqoq", "EPS Growth\nQtr Over Qtr", "fundamental", "needs a growth-rate column (EPS and sales growth this year, next year, quarterly, 3-5 year and TTM): the scan carries forward EPS and revenue *levels* for the next FY/FQ, and a level is not a growth rate"],
  ["fa_epsyoyttm", "EPS Growth TTM", "fundamental", "needs a growth-rate column (EPS and sales growth this year, next year, quarterly, 3-5 year and TTM): the scan carries forward EPS and revenue *levels* for the next FY/FQ, and a level is not a growth rate"],
  ["fa_eps3years", "EPS Growth\nPast 3 Years", "fundamental", "needs a growth-rate column (EPS and sales growth this year, next year, quarterly, 3-5 year and TTM): the scan carries forward EPS and revenue *levels* for the next FY/FQ, and a level is not a growth rate"],
  ["fa_eps5years", "EPS Growth\nPast 5 Years", "fundamental", "needs a growth-rate column (EPS and sales growth this year, next year, quarterly, 3-5 year and TTM): the scan carries forward EPS and revenue *levels* for the next FY/FQ, and a level is not a growth rate"],
  ["fa_estltgrowth", "EPS Growth\nNext 5 Years", "fundamental", "needs a growth-rate column (EPS and sales growth this year, next year, quarterly, 3-5 year and TTM): the scan carries forward EPS and revenue *levels* for the next FY/FQ, and a level is not a growth rate"],
  ["fa_salesqoq", "Sales Growth\nQtr Over Qtr", "fundamental", "needs a growth-rate column (EPS and sales growth this year, next year, quarterly, 3-5 year and TTM): the scan carries forward EPS and revenue *levels* for the next FY/FQ, and a level is not a growth rate"],
  ["fa_salesyoyttm", "Sales Growth TTM", "fundamental", "needs a growth-rate column (EPS and sales growth this year, next year, quarterly, 3-5 year and TTM): the scan carries forward EPS and revenue *levels* for the next FY/FQ, and a level is not a growth rate"],
  ["fa_sales3years", "Sales Growth\nPast 3 Years", "fundamental", "needs a growth-rate column (EPS and sales growth this year, next year, quarterly, 3-5 year and TTM): the scan carries forward EPS and revenue *levels* for the next FY/FQ, and a level is not a growth rate"],
  ["fa_sales5years", "Sales Growth\nPast 5 Years", "fundamental", "needs a growth-rate column (EPS and sales growth this year, next year, quarterly, 3-5 year and TTM): the scan carries forward EPS and revenue *levels* for the next FY/FQ, and a level is not a growth rate"],
  ["fa_epsrev", "Earnings & Revenue Surprise", "fundamental", "needs an earnings-surprise column: the scan carries no surprise history"],
  ["fa_ltdebteq", "LT Debt/Equity", "fundamental", "needs long-term debt separately: the scan carries total debt/equity only"],
  ["sh_insidertrans", "Insider\nTransactions", "fundamental", "needs insider and institutional *transaction* flows: the scan carries no transaction column, and holdings are not transactions"],
  ["sh_insttrans", "Institutional\nTransactions", "fundamental", "needs insider and institutional *transaction* flows: the scan carries no transaction column, and holdings are not transactions"],
  ["ta_highlow20d", "20-Day High/Low", "technical", "needs 20-day and 50-day high/low columns: the scan carries the 52-week extremes only"],
  ["ta_highlow50d", "50-Day High/Low", "technical", "needs 20-day and 50-day high/low columns: the scan carries the 52-week extremes only"],
  ["ta_alltime", "All-Time High/Low", "technical", "needs an all-time high/low column: the scan carries the 52-week extremes only"],
  ["ta_pattern", "Pattern", "technical", "needs chart-pattern detection: the scan returns no pattern classification"],
  ["ta_candlestick", "Candlestick", "technical", "needs candlestick detection: the scan returns no candlestick classification"],
  ["ah_close", "After-Hours Close", "technical", "needs after-hours prices: the scan covers the regular session only"],
  ["ah_change", "After-Hours Change", "technical", "needs after-hours prices: the scan covers the regular session only"],
  ["news_date", "Latest News", "news", "needs the news store: the scan returns no news timestamp or keywords"],
  ["news_keywords", "News Keywords", "news", "needs the news store: the scan returns no news timestamp or keywords"],
  ["etf_heldby", "Held By", "etf", "needs ETF holdings data: the scan does not carry a fund's constituent list"],
];

export const ALIASES: AliasRow[] = [
  ["atr", "atr_14", "number", "ATR"],
  ["atr_14", "atr_14", "number", null],
  ["aum_usd", "aum_usd", "number", null],
  ["average_volume", "avg_volume_30d", "number", "Avg Volume"],
  ["avg_volume_10d", "avg_volume_10d", "number", null],
  ["avg_volume_30d", "avg_volume_30d", "number", null],
  ["beta", "beta_1y", "number", "Beta"],
  ["beta_1y", "beta_1y", "number", null],
  ["change", "change_pct", "number", "Change %"],
  ["change_from_open", "change_from_open", "number", "Change from Open %"],
  ["change_pct", "change_pct", "number", null],
  ["company", "name", "text", "Company"],
  ["country", "country", "text", "Country"],
  ["currency", "currency", "text", null],
  ["current_ratio", "current_ratio", "number", "Curr R"],
  ["debt_to_equity", "debt_to_equity", "number", "Debt/Eq"],
  ["dividend_yield", "dividend_yield", "number", "Dividend Yield"],
  ["earnings_date", "earnings_date", "number", "Earnings"],
  ["eps_forecast_next_fq", "eps_forecast_next_fq", "number", null],
  ["eps_forecast_next_fy", "eps_forecast_next_fy", "number", null],
  ["ev_ebitda", "ev_ebitda", "number", "EV/EBITDA"],
  ["ev_sales", "ev_to_sales", "number", "EV/Sales"],
  ["ev_to_sales", "ev_to_sales", "number", null],
  ["exchange", "exchange", "text", "Exchange"],
  ["float_shares", "float_shares", "number", null],
  ["forward_pe", "pe_forward", "number", "Forward P/E"],
  ["gap", "gap_pct", "number", "Gap"],
  ["gap_pct", "gap_pct", "number", null],
  ["gross_margin", "gross_margin", "number", "Gross M"],
  ["high_52w", "high_52w", "number", "52W High"],
  ["insider_own", "insider_own", "number", "Insider Own"],
  ["inst_own", "inst_own", "number", "Inst Own"],
  ["ipo_date", "listing_date", "number", "IPO Date"],
  ["is_primary", "is_primary", "bool", null],
  ["listing_date", "listing_date", "number", null],
  ["low_52w", "low_52w", "number", "52W Low"],
  ["market_cap", "market_cap_usd", "number", "Market Cap"],
  ["market_cap_usd", "market_cap_usd", "number", null],
  ["name", "name", "text", null],
  ["net_margin", "profit_margin", "number", "Profit M"],
  ["oper_margin", "oper_margin", "number", null],
  ["operating_margin", "oper_margin", "number", "Oper M"],
  ["payout_ratio", "payout_ratio", "number", "Payout Ratio"],
  ["pb", "pb", "number", "P/B"],
  ["pc", "price_to_cash", "number", "P/C"],
  ["pct_from_52w_high", "pct_from_52w_high", "number", null],
  ["pe", "pe_ttm", "number", "P/E"],
  ["pe_forward", "pe_forward", "number", null],
  ["pe_ttm", "pe_ttm", "number", null],
  ["peg", "peg", "number", "PEG"],
  ["perf_10y", "ret_10y", "number", "Perf 10Y"],
  ["perf_13w", "ret_3m", "number", "Perf Quart"],
  ["perf_1w", "ret_1w", "number", "Perf Week"],
  ["perf_26w", "ret_6m", "number", "Perf Half"],
  ["perf_3y", "ret_3y", "number", "Perf 3Y"],
  ["perf_4w", "ret_1m", "number", "Perf Month"],
  ["perf_52w", "ret_1y", "number", "Perf Year"],
  ["perf_5y", "ret_5y", "number", "Perf 5Y"],
  ["perf_ytd", "ret_ytd", "number", "Perf YTD"],
  ["pfcf", "pfcf", "number", "P/FCF"],
  ["price", "price", "number", "Price"],
  ["price_to_cash", "price_to_cash", "number", null],
  ["profit_margin", "profit_margin", "number", null],
  ["ps", "ps", "number", "P/S"],
  ["quick_ratio", "quick_ratio", "number", "Quick R"],
  ["recom_finviz", "recom_finviz", "number", null],
  ["recommendation", "recommendation", "number", "Recom"],
  ["rel_volume_10d", "rel_volume_10d", "number", null],
  ["relative_volume", "rel_volume_10d", "number", "Rel Volume"],
  ["ret_10y", "ret_10y", "number", null],
  ["ret_1m", "ret_1m", "number", null],
  ["ret_1w", "ret_1w", "number", null],
  ["ret_1y", "ret_1y", "number", null],
  ["ret_3m", "ret_3m", "number", null],
  ["ret_3y", "ret_3y", "number", null],
  ["ret_5y", "ret_5y", "number", null],
  ["ret_6m", "ret_6m", "number", null],
  ["ret_ytd", "ret_ytd", "number", null],
  ["revenue_forecast_next_fy", "revenue_forecast_next_fy", "number", null],
  ["roa", "roa", "number", "ROA"],
  ["roe", "roe", "number", "ROE"],
  ["roic", "roic", "number", "ROIC"],
  ["rsi", "rsi_14", "number", "RSI"],
  ["rsi_14", "rsi_14", "number", null],
  ["sector", "sector", "text", "Sector"],
  ["shares_float", "float_shares", "number", "Float"],
  ["shares_outstanding", "shares_outstanding", "number", "Outstanding"],
  ["short_float", "short_float", "number", "Short Float"],
  ["sma20", "sma_20", "number", "SMA20"],
  ["sma200", "sma_200", "number", "SMA200"],
  ["sma50", "sma_50", "number", "SMA50"],
  ["sma_20", "sma_20", "number", null],
  ["sma_200", "sma_200", "number", null],
  ["sma_50", "sma_50", "number", null],
  ["symbol", "symbol", "text", null],
  ["target_price", "target_price", "number", "Target Price"],
  ["ticker", "symbol", "text", "Ticker"],
  ["turnover", "turnover", "number", null],
  ["turnover_usd", "turnover_usd", "number", null],
  ["volatility_1w", "volatility_w", "number", "Volatility W"],
  ["volatility_4w", "volatility_m", "number", "Volatility M"],
  ["volatility_d", "volatility_d", "number", null],
  ["volatility_m", "volatility_m", "number", null],
  ["volatility_w", "volatility_w", "number", null],
  ["volume", "volume", "number", "Volume"],
];

export const VIEW_NAMES: Array<[string, string, string, number]> = [
  ["today", "Today", "What is moving right now?", 11],
  ["value", "Value", "Is it cheap?", 13],
  ["quality", "Quality", "Is the business good?", 12],
  ["growth", "Growth", "Is it growing?", 12],
  ["momentum", "Momentum", "How has it performed?", 10],
  ["trend", "Trend", "Where is it relative to its own history?", 12],
  ["ownership", "Ownership", "Who owns it, and who is short?", 10],
  ["income", "Income", "What does it pay?", 8],
  ["profile", "Profile", "What is it?", 12],
  ["all", "All columns", "What does the whole catalogue look like at once?", 116],
];

/** The document-shaped fixture the registry is built from. */
export function filterSchemaFixture(): FilterSchema {
  const keys: FilterSchema["keys"] = {};
  for (const [key, label, tab, kind, field] of USABLE_KEYS) {
    keys[key] = { label, tab, field, kind };
  }
  for (const [key, label, tab, tokens] of CHOICE_KEYS) {
    keys[key] = {
      label,
      tab,
      field: null,
      choice: {
        grammar: "",
        fields: [],
        windows: {},
        options: Object.fromEntries(tokens.map((token) => [token, `predicate for ${token}`])),
        unsupported: {},
      },
    };
  }
  for (const [key, label, tab, reason] of REFUSED_KEYS) {
    keys[key] = { label, tab, field: null, reason };
  }
  const aliases: FilterSchema["aliases"] = {};
  for (const [name, field, kind, label] of ALIASES) {
    aliases[name] = label === null ? { field, kind } : { field, kind, label };
  }
  const views = VIEW_NAMES.map(([name, label, job, count]) => ({
    name,
    label,
    job,
    columns: Array.from({ length: count }, (_, index) => `column_${index}`),
  }));
  return { keys, aliases, views, keyCount: Object.keys(keys).length, aliasCount: Object.keys(aliases).length };
}

/**
 * The same fixture in the wire shape `GET /screens/select/schema` serves, so a
 * test can mock the boot fetch without re-spelling the tables.
 */
export function filterSchemaWire(): Record<string, unknown> {
  const xFilterKeys: Record<string, unknown> = {};
  for (const [key, label, tab, kind, field] of USABLE_KEYS) {
    xFilterKeys[key] = { label, tab, field, kind };
  }
  for (const [key, label, tab, tokens] of CHOICE_KEYS) {
    xFilterKeys[key] = {
      label,
      tab,
      field: null,
      choice: {
        grammar: "",
        fields: [],
        windows: {},
        options: Object.fromEntries(tokens.map((token) => [token, `predicate for ${token}`])),
        unsupported: {},
      },
    };
  }
  for (const [key, label, tab, reason] of REFUSED_KEYS) {
    xFilterKeys[key] = { label, tab, field: null, reason };
  }
  const xFilterAliases: Record<string, unknown> = {};
  for (const [name, field, kind, label] of ALIASES) {
    xFilterAliases[name] = label === null ? { field, kind } : { field, kind, label };
  }
  const views = VIEW_NAMES.map(([name, label, job, count]) => ({
    name,
    label,
    job,
    columns: Array.from({ length: count }, (_, index) => `column_${index}`),
  }));
  return {
    "x-filter-key-count": Object.keys(xFilterKeys).length,
    "x-filter-keys": xFilterKeys,
    "x-filter-alias-count": Object.keys(xFilterAliases).length,
    "x-filter-aliases": xFilterAliases,
    views,
  };
}
