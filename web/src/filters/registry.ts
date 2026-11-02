/**
 * The filter registry: the schema's usable keys, turned into controls.
 *
 * One entry per key, and the control is *derived* from the kind plus the
 * operator set the API actually allows for that kind:
 *
 *   number  eq ne gt gte lt lte between in
 *   text    eq ne in contains
 *   bool    eq ne in
 *   choice  eq only (the token names the comparison)
 *
 * The preset buckets attach by key from `presets.ts`; a key with buckets still
 * has the full operator set for a custom value, so the bucket is a shortcut and
 * not a cage.
 *
 * **Only usable keys enter.** A refused key is in the schema with a `reason`
 * and no column; it is kept in `refused` for the error message a hand-typed key
 * deserves, and it is absent from `entries`, `byKey`, `byField`, the alias index
 * and the palette's search corpus. That absence is what makes adversarial (b)
 * true by construction rather than by a filter someone can forget.
 */

import type { FilterSchema, FilterKeyEntry } from "./schema";
import { isUsableEntry, refusedKeys } from "./schema";
import { PRESETS, PRESET_UNITS } from "./presets";
import type {
  ControlType,
  FilterOp,
  PresetGroup,
  Unit,
} from "./types";

export interface ChoiceOption {
  token: string;
  label: string;
  /** The predicate the API publishes for this token, when it says. */
  predicate?: string;
}

export interface AliasRef {
  name: string;
  label: string;
}

export interface RegistryEntry {
  key: string;
  label: string;
  tab: string;
  field: string | null;
  kind: "number" | "text" | "bool" | "choice";
  control: ControlType;
  ops: FilterOp[];
  unit: Unit;
  presets: PresetGroup | null;
  options: ChoiceOption[];
  aliases: AliasRef[];
  /** Lower-cased label + key + aliases + field, for ⌘K. */
  search: string;
}

export interface AliasIndexEntry {
  /** The spelling a person may type (`pe`, `roic`, `market_cap`). */
  name: string;
  label: string;
  field: string;
  targetKey: string;
}

/** One filter key the panel document publishes, with the values it accepts. */
export interface PublishedFilter {
  key: string;
  label: string;
  op: FilterOp;
  /** Value (the API's token) → the label the document gave it. */
  values: Map<string, string>;
}

export interface FilterRegistry {
  entries: RegistryEntry[];
  byKey: Map<string, RegistryEntry>;
  byField: Map<string, string>;
  aliases: AliasIndexEntry[];
  tabs: string[];
  /** Refused keys and the schema's reason, for a hand-typed key's message. */
  refused: Record<string, string>;
  usableKeys: Set<string>;
  /**
   * Filters the *served document* publishes but the *schema* does not carry.
   *
   * The two documents are read from one deployment and can disagree for a
   * release: `GET /screens/filters` may offer a control the schema still marks
   * refused (or not at all). The document is the surface that says what runs
   * today, so a link naming one of its keys must survive a reload. These
   * entries are kept apart from `entries`/`byKey` — the picker and the palette
   * stay exactly the schema's vocabulary — and are consulted only where a
   * document-published value needs a name or a validation.
   */
  published: Map<string, PublishedFilter>;
}

const NUMERIC_OPS: FilterOp[] = ["eq", "ne", "gt", "gte", "lt", "lte", "between", "in"];
const TEXT_OPS: FilterOp[] = ["eq", "ne", "in", "contains"];
const BOOL_OPS: FilterOp[] = ["eq", "ne", "in"];
const CHOICE_OPS: FilterOp[] = ["eq"];

export const OP_LABELS: Record<FilterOp, string> = {
  eq: "is",
  ne: "is not",
  gt: "greater than",
  gte: "at least",
  lt: "less than",
  lte: "at most",
  between: "between",
  in: "any of",
  contains: "contains",
};

/** The schema writes two-line labels; a control needs one line. */
export function normalizeLabel(label: string): string {
  return label.replace(/\s+/g, " ").trim();
}

function kindOf(entry: FilterKeyEntry): "number" | "text" | "bool" | "choice" {
  if (entry.choice) return "choice";
  return entry.kind ?? "text";
}

/**
 * Column → the unit its numbers are measured in.
 *
 * The chip's job is to say what a number *is*: `Short Float ≥ 5` and
 * `Short Float ≥ 5%` are different claims, and `Target Price ≥ 200` without a
 * `$` is ambiguous in a panel that also shows share counts. This table is keyed
 * by the row column the schema publishes, so it covers every key — not only the
 * ten with preset buckets — and a new key over a known column inherits its unit
 * without an edit.
 *
 * `plain` is a decision, not a default: a count (RSI, an analyst score, an
 * earnings date) has no unit, and inventing one would be worse than none.
 */
const UNIT_BY_FIELD: Record<string, Unit> = {
  // dollars
  market_cap_usd: "usd",
  price: "price",
  target_price: "price",
  sma_20: "price",
  sma_50: "price",
  sma_200: "price",
  atr_14: "price",
  // share counts
  volume: "compact",
  avg_volume_30d: "compact",
  shares_outstanding: "compact",
  float_shares: "compact",
  // percentages (the provider's percent: 0.47 = 0.47%)
  dividend_yield: "percent",
  short_float: "percent",
  roa: "percent",
  roe: "percent",
  roic: "percent",
  gross_margin: "percent",
  oper_margin: "percent",
  profit_margin: "percent",
  payout_ratio: "percent",
  insider_own: "percent",
  inst_own: "percent",
  gap_pct: "percent",
  change_pct: "percent",
  change_from_open: "percent",
  pct_from_52w_high: "percent",
  // unit-less multiples
  pe_ttm: "ratio",
  pe_forward: "ratio",
  peg: "ratio",
  ps: "ratio",
  pb: "ratio",
  price_to_cash: "ratio",
  pfcf: "ratio",
  ev_ebitda: "ratio",
  ev_to_sales: "ratio",
  current_ratio: "ratio",
  quick_ratio: "ratio",
  debt_to_equity: "ratio",
  rel_volume_10d: "ratio",
  beta_1y: "ratio",
  // bare counts and scores
  rsi_14: "plain",
  recom_finviz: "plain",
  earnings_date: "plain",
};

/** Key-level overrides, for a key whose column is also used elsewhere. */
const UNIT_BY_KEY: Record<string, Unit> = {
  ...PRESET_UNITS,
};

function unitFor(key: string, entry: FilterKeyEntry): Unit {
  const byKey = UNIT_BY_KEY[key];
  if (byKey) return byKey;
  if (typeof entry.field === "string") return UNIT_BY_FIELD[entry.field] ?? "plain";
  return "plain";
}

function opsFor(kind: "number" | "text" | "bool" | "choice"): FilterOp[] {
  switch (kind) {
    case "number":
      return [...NUMERIC_OPS];
    case "text":
      return [...TEXT_OPS];
    case "bool":
      return [...BOOL_OPS];
    case "choice":
      return [...CHOICE_OPS];
  }
}

/**
 * A human label for a choice token, derived from the token itself because the
 * schema publishes tokens and predicates but no per-token sentence. The token
 * stays the value that is sent — this label is display only.
 */
export function choiceTokenLabel(key: string, token: string): string {
  const volatility = /^(wo|mo)(\d+)$/.exec(token);
  if (volatility) {
    const window = volatility[1] === "wo" ? "Weekly" : "Monthly";
    return `${window} ≥${volatility[2]}%`;
  }

  const performance = /^(d|1w|4w|13w|26w|ytd|52w|3y|5y|10y)(up|down|(\d+)(u|o))$/.exec(token);
  if (!performance) return token;

  const windowName: Record<string, string> = {
    d: "Today",
    "1w": "1W",
    "4w": "4W",
    "13w": "13W",
    "26w": "26W",
    ytd: "YTD",
    "52w": "52W",
    "3y": "3Y",
    "5y": "5Y",
    "10y": "10Y",
  };
  const window = windowName[performance[1] ?? "d"] ?? performance[1] ?? key;
  const direction = performance[2] ?? "";
  if (direction === "up") return `${window} up`;
  if (direction === "down") return `${window} down`;
  const amount = performance[3] ?? "";
  const side = performance[4] === "o" ? "up" : "down";
  return `${window} ${side} ${amount}%+`;
}

function optionList(entry: FilterKeyEntry, key: string): ChoiceOption[] {
  const options = entry.choice?.options ?? {};
  return Object.entries(options).map(([token, predicate]) => ({
    token,
    label: choiceTokenLabel(key, token),
    predicate,
  }));
}

/**
 * Build the registry from the served schema.
 *
 * Refused keys are never enumerated: `Object.entries` is filtered by
 * `isUsableEntry`, which is the same predicate the schema uses to mean "a row
 * column exists" or "this key compiles its option vocabulary".
 */
export function buildRegistry(
  schema: FilterSchema,
  published: Map<string, PublishedFilter> = new Map(),
): FilterRegistry {
  const entries: RegistryEntry[] = [];
  const byKey = new Map<string, RegistryEntry>();
  const byField = new Map<string, string>();

  for (const [key, raw] of Object.entries(schema.keys)) {
    if (!isUsableEntry(raw)) continue;
    const kind = kindOf(raw);
    const group = PRESETS.find((preset) => preset.key === key) ?? null;
    const control: ControlType = group
      ? "buckets"
      : kind === "choice"
        ? "choice"
        : kind === "bool"
          ? "boolean"
          : kind;
    const entry: RegistryEntry = {
      key,
      label: normalizeLabel(raw.label),
      tab: raw.tab,
      field: raw.field,
      kind,
      control,
      ops: opsFor(kind),
      unit: unitFor(key, raw),
      presets: group,
      options: kind === "choice" ? optionList(raw, key) : [],
      aliases: [],
      search: "",
    };
    entries.push(entry);
    byKey.set(key, entry);
    if (typeof raw.field === "string" && !byField.has(raw.field)) byField.set(raw.field, key);
  }

  // One column the panel filters on is not one of the schema's 82 keys:
  // `symbol`, written by the Tickers control and published under
  // `x-filter-aliases`. Registering it in `byKey` is what lets `validateDraft`
  // keep the chip across a reload; it is deliberately kept out of `entries`,
  // so the picker still offers exactly the schema's keys.
  const symbol = schema.aliases.symbol;
  if (!byKey.has("symbol") && symbol?.field === "symbol" && symbol.kind === "text") {
    byKey.set("symbol", {
      key: "symbol",
      label: "Tickers",
      tab: "descriptive",
      field: "symbol",
      kind: "text",
      control: "text",
      ops: opsFor("text"),
      unit: "plain",
      presets: null,
      options: [],
      aliases: [],
      search: "tickers symbol",
    });
    byField.set("symbol", "symbol");
  }

  const aliases: AliasIndexEntry[] = [];
  for (const [name, alias] of Object.entries(schema.aliases)) {
    const targetKey = byField.get(alias.field);
    if (!targetKey) continue;
    const entry = byKey.get(targetKey);
    if (!entry) continue;
    const label = alias.label ? normalizeLabel(alias.label) : name;
    aliases.push({ name, label, field: alias.field, targetKey });
    entry.aliases.push({ name, label });
  }

  for (const entry of entries) {
    entry.search = [
      entry.label,
      entry.key,
      entry.field ?? "",
      ...entry.aliases.map((alias) => alias.name),
      ...entry.aliases.map((alias) => alias.label),
    ]
      .join(" ")
      .toLowerCase();
  }

  const tabs: string[] = [];
  for (const entry of entries) if (!tabs.includes(entry.tab)) tabs.push(entry.tab);

  return {
    entries,
    byKey,
    byField,
    aliases,
    tabs,
    refused: refusedKeys(schema),
    usableKeys: new Set(entries.map((entry) => entry.key)),
    published,
  };
}

/** The canonical key a typed spelling names, or null when it names nothing. */
export function resolveKey(registry: FilterRegistry, name: string): string | null {
  if (registry.byKey.has(name)) return name;
  const alias = registry.aliases.find((entry) => entry.name === name);
  if (alias) return alias.targetKey;
  // A key the panel document publishes is a key the API answers, even when the
  // schema has not caught up with it yet.
  return registry.published.has(name) ? name : null;
}

/** The label a chip shows; a raw key is better than an empty chip. */
export function labelFor(registry: FilterRegistry, key: string): string {
  return registry.byKey.get(key)?.label ?? key;
}

/** Whether a key may be offered at all; refused keys are not. */
export function isOffered(registry: FilterRegistry, name: string): boolean {
  return resolveKey(registry, name) !== null;
}
