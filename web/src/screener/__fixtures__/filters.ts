/**
 * A slice of `GET /screens/filters`, shaped like the served document.
 *
 * The Descriptive group carries twenty-two controls on purpose — fifteen with
 * options, two text, one number, one select with nothing published and three
 * unavailable. A test asserts the panel draws *one field per control*, so the
 * fixture has to be a panel's worth of controls, not a handful. Fundamental,
 * Technical, News and ETF are thinner: enough to prove the groups are read from
 * the document and not from a list in a component.
 */

import type { FiltersDocument, FiltersDocumentResult } from "../filtersDocument";
import { parseFiltersDocument } from "../filtersDocument";

function option(
  id: string,
  label: string,
  token: string | null,
  predicate: string | null,
  extra: Record<string, unknown> = {},
) {
  return {
    id,
    label,
    token,
    predicate,
    available: predicate !== null,
    reason: predicate === null ? "no source for this value yet" : null,
    order: null,
    ...extra,
  };
}

function control(spec: Record<string, unknown>) {
  return {
    key: null,
    tab: null,
    disposition: "scan_row",
    control: "buckets",
    form: "single",
    op: "eq",
    status: "available",
    available: true,
    reason: null,
    unit: null,
    hint: null,
    note: null,
    options_source: null,
    predicate_template: null,
    pattern: null,
    options: [],
    ...spec,
  };
}

/** A bucket control with two values, one of them unavailable. */
function bucket(id: string, label: string, key: string, one: string, two: string) {
  return control({
    id,
    label,
    key,
    options: [
      option(`${id}:${one}`, `${label}: ${one}`, one, `${key} == ${one}`),
      option(`${id}:${two}`, `${label}: ${two}`, two, null),
    ],
  });
}

export function filterDocumentWire(): Record<string, unknown> {
  return {
    schema_version: "screener-test",
    groups: [
      {
        id: "descriptive",
        label: "Descriptive",
        controls: [
          control({
            id: "order",
            label: "Order by",
            control: "order",
            op: null,
            options: [option("market_cap", "market_cap", null, "order by market_cap desc")],
          }),
          control({
            id: "signal",
            label: "Signal",
            control: "signal",
            op: null,
            options: [
              option("top-gainers", "Top Gainers", null, "change_pct > 0"),
              option("unusual-volume", "Unusual Volume", null, "volume / average_volume >= 3"),
            ],
          }),
          control({
            id: "idx",
            label: "Index",
            key: "idx",
            options: [option("idx:sp500", "S&P 500", "sp500", "'sp500' in index_members")],
          }),
          control({
            id: "cap",
            label: "Market Cap.",
            key: "cap",
            options: [
              option("cap:mega", "Mega ($200bln and more)", "mega", "market_cap_usd >= 200000000000"),
              option("cap:micro", "Micro (under $300mln)", "micro", null),
            ],
            tier: "premium",
          }),
          control({
            id: "sec",
            label: "Sector",
            key: "sec",
            control: "select",
            form: "multi",
            op: "in",
            options: [],
            options_source: "/market/sectors",
          }),
          control({
            id: "industry",
            label: "Industry",
            key: "industry",
            control: "text",
            op: "contains",
          }),
          control({
            id: "geo",
            label: "Country",
            key: "geo",
            control: "text",
            op: "contains",
          }),
          control({
            id: "exch",
            label: "Exchange",
            key: "exch",
            control: "select",
            form: "multi",
            op: "in",
            options: [option("nyse", "NYSE", "nyse", "exchange == 'XNYS'")],
          }),
          bucket("fa_div", "Dividend Yield", "fa_div", "high", "none"),
          bucket("sh_short", "Short Float", "sh_short", "high", "low"),
          bucket("an_recom", "Analyst Recom.", "an_recom", "buy", "sell"),
          bucket("sh_opt", "Option/Short", "sh_opt", "optionable", "shortable"),
          bucket("earn", "Earnings Date", "earningsdate", "today", "tomorrow"),
          bucket("sh_avgvol", "Average Volume", "sh_avgvol", "over1m", "under500k"),
          bucket("sh_relvol", "Relative Volume", "sh_relvol", "over2", "under1"),
          control({
            id: "sh_curvol",
            label: "Current Volume",
            key: "sh_curvol",
            control: "number",
            op: "gte",
            unit: "shares",
          }),
          bucket("price", "Price", "sh_price", "u5", "o100"),
          bucket("targetprice", "Target Price", "targetprice", "above", "below"),
          bucket("ipodate", "IPO Date", "ipodate", "recent", "old"),
          bucket("sh_outstanding", "Shares Outstanding", "sh_outstanding", "over100m", "under10m"),
          bucket("sh_float", "Float", "sh_float", "over50m", "under5m"),
          control({
            id: "theme",
            label: "Theme",
            key: "theme",
            control: "select",
            available: false,
            status: "unavailable",
            reason: "needs theme classification: no row carries a theme",
            options: [option("theme:ai", "Artificial Intelligence", "ai", null)],
          }),
          control({
            id: "subtheme",
            label: "Sub-theme",
            key: "subtheme",
            control: "select",
            available: false,
            status: "unavailable",
            reason: "needs theme classification: no row carries a theme",
          }),
          control({
            id: "sh_dilution",
            label: "Dilution risk",
            key: "sh_dilution",
            control: "select",
            available: false,
            status: "unavailable",
            reason: "needs a share-count history",
          }),
        ],
      },
      {
        id: "fundamental",
        label: "Fundamental",
        controls: [
          bucket("pe", "P/E", "fa_pe", "u10", "o40"),
          bucket("fpe", "Forward P/E", "fa_fpe", "u15", "o30"),
        ],
      },
      {
        id: "technical",
        label: "Technical",
        controls: [
          bucket("rsi", "RSI (14)", "ta_rsi", "ob70", "os30"),
          control({
            id: "ta_perf",
            label: "Performance",
            key: "ta_perf",
            control: "choice",
            options: [option("dup", "Today Up", "dup", "change_pct > 0.0")],
          }),
        ],
      },
      {
        id: "news",
        label: "News",
        controls: [
          bucket("news", "Latest News", "news_date", "today", "week"),
        ],
      },
      {
        id: "etf",
        label: "ETF",
        controls: [
          control({
            id: "etf_heldby",
            label: "Held By",
            key: "etf_heldby",
            control: "select",
            options: [],
            available: false,
            status: "unavailable",
            reason: "no ETF holdings feed",
          }),
        ],
      },
    ],
    membership: { available: true, as_of: "2026-11-01", counts: {}, reason: null },
    options_sources: { "/market/sectors": "live labels" },
  };
}

export function filterDocument(): FiltersDocument {
  return parseFiltersDocument(filterDocumentWire());
}

/** The select value a control's option carries: `controlId` + option id. */
export const CAP_MEGA = "cap\u0000cap:mega";
export const EXCH_NYSE = "exch\u0000nyse";
export const PE_U10 = "pe\u0000pe:u10";
export const RSI_OB70 = "rsi\u0000rsi:ob70";
export const NEWS_TODAY = "news\u0000news:today";

/** A ready document result, for a component test that is not about loading. */
export function readyDocument(): FiltersDocumentResult {
  return { doc: filterDocument(), status: "ready", error: null, retry: () => undefined };
}
