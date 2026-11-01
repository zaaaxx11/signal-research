/**
 * The column presets: eighteen ways to look at the same screen.
 *
 * A preset is a *column set*, never a filter. Clicking one changes what the
 * table draws and nothing about which rows survived — the chips, the URL's `f`
 * parameters and the request's `criteria.filters` are untouched. That
 * distinction is the whole reason the bar sits beside the filter panel instead
 * of inside it.
 *
 * Each preset resolves against what the server actually served, in three
 * steps:
 *
 * 1. a **view** the catalogue still publishes is the preset's columns and its
 *    name in the URL (`?view=value`);
 * 2. otherwise a **catalogue** column list — every key checked against the
 *    served catalogue, dropped when this build does not carry it, so nothing
 *    is invented and a renamed column degrades to a smaller set;
 * 3. otherwise the **default** columns of the current view, with the reason in
 *    the control's title and accessible name. A tab that cannot be honoured
 *    says so instead of pretending.
 *
 * The labels are ours. They describe what the reader is looking at, and the
 * table below is the only place they are spelled.
 */

import type { ViewDefinition } from "@/data/screens";

export interface ColumnPreset {
  id: string;
  /** The tab's text and the Columns select's option text. */
  label: string;
  /** The served view this preset is, when the catalogue still has one. */
  view?: string;
  /** Columns for a preset with no view of its own; all must exist to be shown. */
  columns?: readonly string[];
  /** One line, for the tooltip: what this tab is for. */
  job: string;
  /** True for the tab that stands for the reader's own selection. */
  custom?: boolean;
}

export const COLUMN_PRESETS: readonly ColumnPreset[] = [
  { id: "summary", label: "Summary", view: "today", job: "The tape: price, change, range and volume." },
  { id: "valuation", label: "Valuation", view: "value", job: "What the market pays for the business." },
  { id: "financials", label: "Financials", view: "quality", job: "Margins, returns and balance-sheet health." },
  { id: "growth", label: "Growth", view: "growth", job: "Earnings and sales, this year and the last five." },
  { id: "ownership", label: "Ownership", view: "ownership", job: "Who holds the shares, and how much is short." },
  { id: "momentum", label: "Momentum", view: "momentum", job: "Return over every window the scan carries." },
  { id: "technical", label: "Technical", view: "trend", job: "Where price sits against its own averages." },
  { id: "dividends", label: "Dividends", view: "income", job: "What the company pays, and how that has moved." },
  { id: "basics", label: "Basics", view: "profile", job: "What the company is, and where it lists." },
  {
    id: "custom",
    label: "Custom",
    custom: true,
    job: "Your own column selection; the view's defaults until you change them.",
  },
  {
    id: "charts",
    label: "Charts",
    columns: ["price", "change", "volume", "sma20", "sma50", "sma200", "high_52w", "low_52w"],
    job: "The columns a chart is drawn from: price, volume and its averages.",
  },
  {
    id: "tickers",
    label: "Tickers",
    columns: ["ticker", "company", "price", "change", "volume", "market_cap"],
    job: "A compact list: symbol, name and the few numbers that identify it.",
  },
  {
    id: "ta",
    label: "TA",
    columns: [
      "rsi",
      "atr",
      "beta",
      "sma20",
      "sma50",
      "sma200",
      "volatility_1w",
      "volatility_4w",
      "high_52w",
      "low_52w",
    ],
    job: "The indicators: RSI, ATR, beta, moving averages and volatility.",
  },
  {
    id: "news",
    label: "News",
    columns: ["news_time", "news_title", "news_url", "daily_digest", "company", "ticker"],
    job: "The latest headline each row carries, and when it landed.",
  },
  {
    id: "snapshot",
    label: "Snapshot",
    columns: [
      "market_cap",
      "pe",
      "forward_pe",
      "recommendation",
      "target_price",
      "rsi",
      "beta",
      "dividend_yield",
    ],
    job: "One line per company: size, multiple, rating and income.",
  },
  {
    id: "stats",
    label: "Stats",
    columns: ["market_cap", "pe", "roe", "debt_to_equity", "beta", "dividend_yield", "rsi", "atr"],
    job: "The comparison numbers a first pass reads across.",
  },
  {
    id: "insiders",
    label: "Insiders",
    columns: [
      "insider_own",
      "insider_trans",
      "inst_own",
      "inst_trans",
      "shares_outstanding",
      "shares_float",
      "float_pct",
    ],
    job: "Insider and institutional holdings, and the share count behind them.",
  },
  {
    id: "liquidity",
    label: "Liquidity",
    columns: ["volume", "average_volume", "relative_volume", "market_cap", "beta", "atr", "short_float"],
    job: "How easily the name trades: volume, relative volume and volatility.",
  },
];

/**
 * How a preset was honoured. `default` is the honest fallback: the service
 * did not publish a view or a column this build carries, so the current
 * view's own columns stay and the control says why.
 */
export type PresetSource = "view" | "catalogue" | "default" | "custom";

export interface ResolvedPreset {
  preset: ColumnPreset;
  source: PresetSource;
  /** The served view to switch to, when this preset is one. */
  viewName: string | null;
  /** The columns to apply, when this preset carries its own set. */
  columns: readonly string[] | null;
  /** The preset's keys the served catalogue does not carry, by name. */
  missing: readonly string[];
  /** The sentence a title carries: where these columns come from. */
  note: string;
  /** The same fact, short enough for an accessible name. */
  description: string;
}

function unique(keys: readonly string[]): string[] {
  const seen = new Set<string>();
  const kept: string[] = [];
  for (const key of keys) {
    if (typeof key !== "string" || key.length === 0 || seen.has(key)) continue;
    seen.add(key);
    kept.push(key);
  }
  return kept;
}

/**
 * One preset against what was served.
 *
 * The lookup is by view *name*, never by the preset's label: a served view may
 * be relabelled without moving the tab, and a preset whose view was withdrawn
 * falls through to its catalogue list (or to the default) instead of pointing
 * at a name the schema no longer answers.
 */
export function resolvePreset(
  preset: ColumnPreset,
  views: readonly ViewDefinition[],
  catalogue: readonly string[],
): ResolvedPreset {
  const base = { preset, missing: [] as readonly string[] };

  if (preset.view) {
    const view = views.find((candidate) => candidate.name === preset.view);
    if (view) {
      return {
        ...base,
        source: "view",
        viewName: view.name,
        columns: view.columns,
        note: `${view.columns.length} columns from the “${view.label}” view.`,
        description: `${view.columns.length} columns, the ${view.label} view`,
      };
    }
  }

  if (preset.columns && preset.columns.length > 0) {
    const known = new Set(catalogue);
    const kept = unique(preset.columns.filter((key) => known.has(key)));
    const missing = preset.columns.filter((key) => !known.has(key));
    if (kept.length > 0) {
      return {
        ...base,
        missing,
        source: "catalogue",
        viewName: null,
        columns: kept,
        note:
          `No view of its own yet: ${kept.length} column${kept.length === 1 ? "" : "s"} picked from the served catalogue` +
          (missing.length > 0 ? `, ${missing.length} not carried by this build` : "") +
          ".",
        description: `${kept.length} catalogue column${kept.length === 1 ? "" : "s"}, no view of its own yet`,
      };
    }
  }

  if (preset.custom) {
    return {
      ...base,
      source: "custom",
      viewName: null,
      columns: null,
      note: "Your own columns; the view's defaults until you rearrange them.",
      description: "your own columns",
    };
  }

  return {
    ...base,
    source: "default",
    viewName: null,
    columns: null,
    note: "No view or column set this build can serve: the default columns stay on screen.",
    description: "default columns, no view yet",
  };
}

/** Every preset, resolved in the table's order. */
export function resolvePresets(
  views: readonly ViewDefinition[],
  catalogue: readonly string[],
): ResolvedPreset[] {
  return COLUMN_PRESETS.map((preset) => resolvePreset(preset, views, catalogue));
}

/**
 * The preset whose columns are exactly the ones on screen, or the Custom tab.
 *
 * Derived, never stored: the bar reflects the table, so a reader who rearranges
 * columns in the panel sees the bar stop claiming a preset — and a reload lands
 * on the same tab because `?cols=` reproduces the same comparison.
 */
export function activePresetId(
  resolved: readonly ResolvedPreset[],
  viewName: string,
  columns: readonly string[],
): string {
  for (const entry of resolved) {
    if (entry.viewName !== null && entry.viewName === viewName && entry.columns !== null) {
      if (sameList(entry.columns, columns)) return entry.preset.id;
    }
    if (entry.source === "catalogue" && entry.columns !== null && sameList(entry.columns, columns)) {
      return entry.preset.id;
    }
  }
  return resolved.find((entry) => entry.preset.custom)?.preset.id ?? resolved[0]?.preset.id ?? "";
}

function sameList(left: readonly string[], right: readonly string[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((key, index) => key === right[index]);
}
