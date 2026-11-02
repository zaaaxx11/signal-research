/**
 * The column presets: eighteen ways to look at the same screen.
 *
 * A preset is a *column set*, never a filter. Clicking one changes what the
 * table draws and nothing about which rows survived — the chips, the URL's `f`
 * parameters and the request's `criteria.filters` are untouched. That
 * distinction is the whole reason the bar sits beside the filter panel instead
 * of inside it.
 *
 * Each of the nine view tabs maps to one served view through `TAB_TO_VIEW`,
 * spelled once. A tab whose view this build no longer serves, and which has no
 * column set of its own, is **disabled** and its title says which view is
 * missing. No tab is ever redirected to another one — `custom` in particular is
 * a column set, not a fallback.
 *
 * Each preset resolves against what the server actually served, in three
 * steps:
 *
 * 1. a **view** the catalogue still publishes is the preset's columns and its
 *    name in the URL (`?view=value`). The columns are the ones the screen will
 *    really draw for that view (`options.defaults`), so the Summary tab and the
 *    product's opening column set are the same set;
 * 2. otherwise a **catalogue** column list — every key checked against the
 *    served catalogue, dropped when this build does not carry it, so nothing
 *    is invented and a renamed column degrades to a smaller set;
 * 3. otherwise the tab is **disabled**, with the reason in its title and its
 *    accessible name. A tab that cannot be honoured says so instead of
 *    pretending.
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

/**
 * The tab → view mapping, spelled once and never derived from a label.
 *
 * The ids on the left are ours; the names on the right are the API's. A tab
 * whose view is missing from the served schema is disabled by `resolvePreset`
 * — it is never pointed at another tab, and `custom` is not a view name any
 * request can carry.
 */
export const TAB_TO_VIEW: Readonly<Record<string, string>> = {
  summary: "today",
  valuation: "value",
  financials: "quality",
  growth: "growth",
  ownership: "ownership",
  momentum: "momentum",
  technical: "trend",
  dividends: "income",
  basics: "profile",
};

export const COLUMN_PRESETS: readonly ColumnPreset[] = [
  { id: "summary", label: "Summary", view: TAB_TO_VIEW["summary"], job: "The tape: price, change, range and volume." },
  { id: "valuation", label: "Valuation", view: TAB_TO_VIEW["valuation"], job: "What the market pays for the business." },
  { id: "financials", label: "Financials", view: TAB_TO_VIEW["financials"], job: "Margins, returns and balance-sheet health." },
  { id: "growth", label: "Growth", view: TAB_TO_VIEW["growth"], job: "Earnings and sales, this year and the last five." },
  { id: "ownership", label: "Ownership", view: TAB_TO_VIEW["ownership"], job: "Who holds the shares, and how much is short." },
  { id: "momentum", label: "Momentum", view: TAB_TO_VIEW["momentum"], job: "Return over every window the scan carries." },
  { id: "technical", label: "Technical", view: TAB_TO_VIEW["technical"], job: "Where price sits against its own averages." },
  { id: "dividends", label: "Dividends", view: TAB_TO_VIEW["dividends"], job: "What the company pays, and how that has moved." },
  { id: "basics", label: "Basics", view: TAB_TO_VIEW["basics"], job: "What the company is, and where it lists." },
  {
    id: "custom",
    label: "Custom",
    custom: true,
    job: "Your own column set: the Columns control's selection, or the view's own columns.",
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
 * How a preset was honoured. `view` switches the served view; `catalogue`
 * applies a column set this build can serve; `custom` is the reader's set.
 * `default` is the honest refusal: the view or columns the tab names are not
 * served, so `disabled` is true and the tab says why.
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
  /**
   * True when this tab cannot be honoured by this build. The button is
   * disabled and its title says why; clicking it does nothing. A disabled tab
   * is never redirected to another view or to Custom.
   */
  disabled: boolean;
}

/** What the screen knows that the preset table alone does not. */
export interface PresetResolutionOptions {
  /**
   * Each served view's *effective* columns: what choosing that tab will draw.
   * The screen replaces the opening view's list with the product default set,
   * and the Summary tab has to describe that same set or it never matches.
   */
  defaults?: Readonly<Record<string, readonly string[]>>;
  /** The reader's own set — whatever the header's Columns control shows. */
  customColumns?: readonly string[] | null;
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
 * be relabelled without moving the tab. A preset whose view was withdrawn is
 * disabled with the missing name in its title — it is not pointed at the
 * current view, at another tab, or at Custom.
 */
export function resolvePreset(
  preset: ColumnPreset,
  views: readonly ViewDefinition[],
  catalogue: readonly string[],
  options: PresetResolutionOptions = {},
): ResolvedPreset {
  const base = { preset, missing: [] as readonly string[], disabled: false };

  if (preset.view !== undefined) {
    const view = views.find((candidate) => candidate.name === preset.view);
    if (!view) {
      return {
        ...base,
        source: "default",
        viewName: null,
        columns: null,
        disabled: true,
        note: `The “${preset.view}” view is not served by this build, so this tab is unavailable.`,
        description: `unavailable: the ${preset.view} view is not served`,
      };
    }
    // The screen may draw a different list for a view than the schema spells
    // (the opening view gets the product default set); the tab must describe
    // the list a click will really produce.
    const columns = options.defaults?.[view.name] ?? view.columns;
    return {
      ...base,
      source: "view",
      viewName: view.name,
      columns,
      note: `${columns.length} columns from the “${view.label}” view.`,
      description: `${columns.length} columns, the ${view.label} view`,
    };
  }

  if (preset.custom) {
    const known = new Set(catalogue);
    const kept = options.customColumns
      ? unique(options.customColumns.filter((key) => known.has(key)))
      : [];
    if (kept.length > 0) {
      return {
        ...base,
        source: "custom",
        viewName: null,
        columns: kept,
        note: "Your own column set, as the Columns control has it.",
        description: `${kept.length} of your own columns`,
      };
    }
    // No saved set: the tab still stands for the reader's own columns, and
    // applying it falls back to the current view's defaults.
    return {
      ...base,
      source: "custom",
      viewName: null,
      columns: null,
      note: "Your own columns; the view's defaults until you save a set.",
      description: "your own columns",
    };
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
    return {
      ...base,
      missing,
      source: "default",
      viewName: null,
      columns: null,
      disabled: true,
      note: `No column of this set is served by this build, so this tab is unavailable.`,
      description: "unavailable: none of its columns are served",
    };
  }

  return {
    ...base,
    source: "default",
    viewName: null,
    columns: null,
    disabled: true,
    note: "This tab maps to no view and no column set this build can serve.",
    description: "unavailable: no view or columns yet",
  };
}

/** Every preset, resolved in the table's order. */
export function resolvePresets(
  views: readonly ViewDefinition[],
  catalogue: readonly string[],
  options: PresetResolutionOptions = {},
): ResolvedPreset[] {
  return COLUMN_PRESETS.map((preset) => resolvePreset(preset, views, catalogue, options));
}

/**
 * The preset whose columns are exactly the ones on screen, or the Custom tab.
 *
 * Derived, never stored: the bar reflects the table, so a reader who rearranges
 * columns in the control sees the bar stop claiming a preset — and a reload
 * lands on the same tab because `?cols=` reproduces the same comparison.
 *
 * A disabled preset is never a match; neither is a preset that resolved to no
 * columns. The final fallback is Custom — the reader's own set — and never a
 * different view tab: clicking Summary cannot make the bar say Custom unless
 * the columns on screen really are Custom's.
 */
export function activePresetId(
  resolved: readonly ResolvedPreset[],
  viewName: string,
  columns: readonly string[],
): string {
  for (const entry of resolved) {
    if (entry.disabled || entry.columns === null) continue;
    if (entry.viewName !== null) {
      if (entry.viewName === viewName && sameList(entry.columns, columns)) {
        return entry.preset.id;
      }
      continue;
    }
    if (sameList(entry.columns, columns)) return entry.preset.id;
  }
  return (
    resolved.find((entry) => entry.preset.custom && !entry.disabled)?.preset.id ??
    resolved.find((entry) => !entry.disabled)?.preset.id ??
    ""
  );
}

function sameList(left: readonly string[], right: readonly string[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((key, index) => key === right[index]);
}
