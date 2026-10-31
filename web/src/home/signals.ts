/**
 * The Home surface's data model: thirteen signal groups, eleven of which have a
 * source.
 *
 * `GET /market/signals` answers one document with every group in it, so Home is
 * **one** request, not eleven. The client still owns the group list — the
 * order, the labels, and the two groups nobody can fill — because that is a
 * statement about this page's shape, not about the market. A group the server
 * omits is drawn as empty; a group it answers is drawn with its rows. Neither
 * can invent a table.
 *
 * Upgrades and Downgrades are the honest half. There is no analyst-ratings
 * source in this deployment, so instead of an empty table with a spinner that
 * never resolves, they say one sentence and stop.
 *
 * Wire tolerance is deliberate: the endpoint is new and its document may spell
 * a group as an array, an object with `rows`, or a keyed map, and a row's
 * price/change fields may arrive under either the snake_case or the camelCase
 * name. The parser accepts those spellings and drops anything it cannot read —
 * a row without a symbol is not a row.
 */

/** How many rows a card shows. Mirrors the screener's page size. */
export const SIGNAL_LIMIT = 20;

/** The one sentence the two unsourced groups print, word for word. */
export const UNAVAILABLE_REASON = "not available — no analyst ratings source";

export interface SignalGroupDefinition {
  readonly key: string;
  readonly label: string;
  /** What the row's Signal cell says: the membership, in three or four words. */
  readonly signal: string;
  readonly available: boolean;
  readonly reason?: string;
}

export const SIGNAL_GROUPS: readonly SignalGroupDefinition[] = [
  { key: "gainers", label: "Gainers", signal: "Up today", available: true },
  { key: "losers", label: "Losers", signal: "Down today", available: true },
  { key: "new_high", label: "New High", signal: "At 52w high", available: true },
  { key: "new_low", label: "New Low", signal: "At 52w low", available: true },
  { key: "overbought", label: "Overbought", signal: "Overbought", available: true },
  { key: "oversold", label: "Oversold", signal: "Oversold", available: true },
  { key: "unusual_volume", label: "Unusual Volume", signal: "Volume spike", available: true },
  { key: "most_active", label: "Most Active", signal: "Most active", available: true },
  { key: "most_volatile", label: "Most Volatile", signal: "Most volatile", available: true },
  { key: "insider_buying", label: "Insider Buying", signal: "Insider buy", available: true },
  { key: "insider_selling", label: "Insider Selling", signal: "Insider sale", available: true },
  {
    key: "upgrades",
    label: "Upgrades",
    signal: "—",
    available: false,
    reason: UNAVAILABLE_REASON,
  },
  {
    key: "downgrades",
    label: "Downgrades",
    signal: "—",
    available: false,
    reason: UNAVAILABLE_REASON,
  },
] as const;

/** The eleven groups that must be drawn from the one read. */
export const AVAILABLE_GROUPS = SIGNAL_GROUPS.filter((group) => group.available);

/** The two that must not be. */
export const UNAVAILABLE_GROUPS = SIGNAL_GROUPS.filter((group) => !group.available);

export interface SignalRow {
  symbol: string;
  name: string;
  price: number | null;
  changePct: number | null;
  volume: number | null;
}

export interface SignalsReport {
  asOf: string | null;
  /** Rows per group key; a group the server did not mention is simply absent. */
  groups: Readonly<Record<string, SignalRow[]>>;
  /**
   * The server's one-sentence explanation for a group it answered with nothing
   * ("no rows matched: rsi_14 >= 70; 2 of 2 rows carry rsi_14"). Kept out of
   * the card's flow — it belongs in the empty row's tooltip, not in a line the
   * reader must parse on every glance — but never dropped: an empty table with
   * no reason is the thing this page is trying to avoid.
   */
  notes: Readonly<Record<string, string>>;
}

/**
 * The one GET. `per` is the route's rows-per-group knob (20 is also its
 * default); `region` is named because a document that counted a different
 * universe than the screener is a different market.
 */
export function signalsPath(per = SIGNAL_LIMIT): string {
  const params = new URLSearchParams({ region: "us", per: String(per) });
  return `/market/signals?${params.toString()}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function num(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim().length > 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function firstNumber(row: Record<string, unknown>, keys: readonly string[]): number | null {
  for (const key of keys) {
    const value = num(row[key]);
    if (value !== null) return value;
  }
  return null;
}

function firstString(row: Record<string, unknown>, keys: readonly string[]): string {
  for (const key of keys) {
    const value = row[key];
    if (typeof value === "string" && value.length > 0) return value;
  }
  return "";
}

/** One row, or null when it cannot be keyed. */
export function readSignalRow(value: unknown): SignalRow | null {
  if (!isRecord(value)) return null;
  const symbol = firstString(value, ["symbol", "ticker", "code"]);
  if (symbol.length === 0) return null;
  return {
    symbol,
    name: firstString(value, ["name", "company", "company_name"]),
    price: firstNumber(value, ["price", "last", "close", "last_price"]),
    changePct: firstNumber(value, ["change_pct", "changePct", "change", "change_percent"]),
    volume: firstNumber(value, ["volume", "vol", "volume_today"]),
  };
}

/** The rows inside one group entry, however the entry happens to be shaped. */
function rowsOf(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (!isRecord(value)) return [];
  for (const key of ["rows", "items", "data", "symbols"] as const) {
    const candidate = value[key];
    if (Array.isArray(candidate)) return candidate;
  }
  return [];
}

function readGroupRows(value: unknown): SignalRow[] {
  return rowsOf(value)
    .map(readSignalRow)
    .filter((row): row is SignalRow => row !== null)
    .slice(0, SIGNAL_LIMIT);
}

/**
 * The groups in a wire document.
 *
 * Three shapes are accepted because the route is new and the client should not
 * be the thing that decides a spelling was wrong:
 *
 *   `{ groups: [{ key, rows }] }`   a list, keyed by name
 *   `{ groups: { gainers: { rows } } }`  a map
 *   `{ gainers: { rows } }`         the groups at the top level
 *
 * Unknown keys are ignored; known keys are read from whichever shape carried
 * them.
 */
export function readSignals(body: unknown): SignalsReport {
  const groups: Record<string, SignalRow[]> = {};
  const notes: Record<string, string> = {};
  if (!isRecord(body)) return { asOf: null, groups, notes };

  const asOf = typeof body["as_of"] === "string" ? body["as_of"] : null;
  const known = new Set(SIGNAL_GROUPS.map((group) => group.key));

  const collect = (key: unknown, value: unknown) => {
    if (typeof key !== "string" || !known.has(key)) return;
    groups[key] = readGroupRows(value);
    if (isRecord(value) && typeof value["note"] === "string" && value["note"].length > 0) {
      notes[key] = value["note"];
    }
  };

  const container = body["groups"] ?? body["signals"] ?? body["data"];
  if (Array.isArray(container)) {
    for (const entry of container) {
      if (!isRecord(entry)) continue;
      const key = entry["key"] ?? entry["id"] ?? entry["name"] ?? entry["slug"];
      collect(key, entry);
    }
  } else if (isRecord(container)) {
    for (const [key, value] of Object.entries(container)) collect(key, value);
  }

  // A document that puts the groups at the top level (`{ gainers: [...] }`).
  for (const key of known) {
    if (groups[key] !== undefined) continue;
    if (body[key] !== undefined) collect(key, body[key]);
  }

  return { asOf, groups, notes };
}

/* ---------------------------------------------------------------------------
 * Formatting. Every number is nullable: a missing value is an em dash, never a
 * zero, because a zero is a number the server did not send.
 * ------------------------------------------------------------------------ */

export function formatPrice(value: number | null): string {
  if (value === null) return "—";
  const fraction = value >= 1 ? 2 : 4;
  return `$${value.toLocaleString("en-US", {
    minimumFractionDigits: fraction,
    maximumFractionDigits: fraction,
  })}`;
}

export function formatChange(value: number | null): string {
  if (value === null) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

export function formatVolume(value: number | null): string {
  if (value === null) return "—";
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(Math.round(value));
}

/** `up` / `down` / `flat`, for the change cell's colour token. */
export function changeTone(value: number | null): "up" | "down" | "flat" {
  if (value === null || value === 0) return "flat";
  return value > 0 ? "up" : "down";
}
