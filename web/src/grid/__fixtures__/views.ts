/**
 * The nine canonical views, captured from `view_catalogue()`. Test data only:
 * the application renders whatever `GET /screens/select/schema` serves, and
 * `tests/test_screen_schema.py` asserts that answer equals this module.
 */

import type { ViewDefinition } from "@/data/screens";

export const VIEW_FIXTURE: ViewDefinition[] = [
  {
    "name": "today",
    "label": "Today",
    "job": "What is moving right now?",
    "columns": [
      "price",
      "change",
      "change_from_open",
      "gap",
      "prev_close",
      "open",
      "high",
      "low",
      "volume",
      "average_volume",
      "relative_volume"
    ]
  },
  {
    "name": "value",
    "label": "Value",
    "job": "Is it cheap?",
    "columns": [
      "market_cap",
      "enterprise_value",
      "pe",
      "forward_pe",
      "peg",
      "ps",
      "pb",
      "pc",
      "pfcf",
      "ev_ebitda",
      "ev_sales",
      "target_price",
      "recommendation"
    ]
  },
  {
    "name": "quality",
    "label": "Quality",
    "job": "Is the business good?",
    "columns": [
      "roa",
      "roe",
      "roic",
      "current_ratio",
      "quick_ratio",
      "lt_debt_to_equity",
      "debt_to_equity",
      "book_per_share",
      "cash_per_share",
      "gross_margin",
      "operating_margin",
      "net_margin"
    ]
  },
  {
    "name": "growth",
    "label": "Growth",
    "job": "Is it growing?",
    "columns": [
      "eps",
      "net_income",
      "sales",
      "eps_next_q",
      "eps_this_y",
      "eps_next_y",
      "eps_past_3y",
      "eps_past_5y",
      "eps_qoq",
      "sales_qoq",
      "eps_yoy_ttm",
      "sales_yoy_ttm"
    ]
  },
  {
    "name": "momentum",
    "label": "Momentum",
    "job": "How has it performed?",
    "columns": [
      "perf_1w",
      "perf_4w",
      "perf_13w",
      "perf_26w",
      "perf_52w",
      "perf_ytd",
      "perf_3y",
      "perf_5y",
      "perf_10y",
      "rsi"
    ]
  },
  {
    "name": "trend",
    "label": "Trend",
    "job": "Where is it relative to its own history?",
    "columns": [
      "sma20",
      "sma50",
      "sma200",
      "high_50d",
      "low_50d",
      "high_52w",
      "low_52w",
      "atr",
      "beta",
      "volatility_1w",
      "volatility_4w",
      "all_time_high"
    ]
  },
  {
    "name": "ownership",
    "label": "Ownership",
    "job": "Who owns it, and who is short?",
    "columns": [
      "shares_outstanding",
      "shares_float",
      "float_pct",
      "insider_own",
      "insider_trans",
      "inst_own",
      "inst_trans",
      "short_float",
      "short_ratio",
      "short_interest"
    ]
  },
  {
    "name": "income",
    "label": "Income",
    "job": "What does it pay?",
    "columns": [
      "dividend",
      "dividend_yield",
      "dividend_ttm",
      "payout_ratio",
      "dividend_ex_date",
      "dividend_growth_1y",
      "dividend_growth_3y",
      "dividend_growth_5y"
    ]
  },
  {
    "name": "profile",
    "label": "Profile",
    "job": "What is it?",
    "columns": [
      "company",
      "sector",
      "industry",
      "security_type",
      "country",
      "exchange",
      "index_membership",
      "optionable",
      "shortable",
      "employees",
      "earnings_date",
      "ipo_date"
    ]
  }
];
