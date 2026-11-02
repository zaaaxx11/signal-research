import { getJson } from "@/lib/api";

/**
 * The options chain data layer and its pure formatters.
 *
 * `parseOptions` reads the `/engine/options/{symbol}` document: every 200
 * carries `delayed_minutes: 15`, a `selected_expiry`, an `expiry_dates` list
 * and the flat `strikes` for the selected expiry. A greek the exchange could
 * not compute is `null`, never `0`. The formatters are the precision contract
 * the table must not guess at — delta/gamma/theta/vega at four decimals with a
 * sign, implied volatility as a percent, premium as money — asserted without a
 * DOM.
 */

export interface OptionContract {
  readonly strike: number;
  readonly type: "call" | "put";
  readonly premium: number | null;
  readonly delta: number | null;
  readonly gamma: number | null;
  readonly theta: number | null;
  readonly vega: number | null;
  readonly rho: number | null;
  readonly volume: number | null;
  readonly openInterest: number | null;
  readonly impliedVolatility: number | null;
  readonly expiry: string;
}

export interface OptionsChain {
  readonly symbol: string;
  readonly asOf: string;
  readonly delayedMinutes: number;
  readonly expiryDates: readonly string[];
  readonly selectedExpiry: string;
  readonly strikes: readonly OptionContract[];
}

export interface StrikeRow {
  readonly strike: number;
  readonly call: OptionContract;
  readonly put: OptionContract;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function parseContract(raw: Record<string, unknown>): OptionContract | null {
  const strike = asNumberOrNull(raw["strike"]);
  const type = asString(raw["type"]);
  if (strike === null || (type !== "call" && type !== "put")) return null;
  return {
    strike,
    type: type as "call" | "put",
    premium: asNumberOrNull(raw["premium"]),
    delta: asNumberOrNull(raw["delta"]),
    gamma: asNumberOrNull(raw["gamma"]),
    theta: asNumberOrNull(raw["theta"]),
    vega: asNumberOrNull(raw["vega"]),
    rho: asNumberOrNull(raw["rho"]),
    volume: asNumberOrNull(raw["volume"]),
    openInterest: asNumberOrNull(raw["open_interest"]),
    impliedVolatility: asNumberOrNull(raw["implied_volatility"]),
    expiry: asString(raw["expiry"]),
  };
}

export function parseOptions(body: unknown): OptionsChain | null {
  if (!isRecord(body)) return null;
  const rawStrikes = body["strikes"];
  if (!Array.isArray(rawStrikes)) return null;
  const strikes: OptionContract[] = [];
  for (const raw of rawStrikes) {
    if (!isRecord(raw)) continue;
    const contract = parseContract(raw);
    if (contract !== null) strikes.push(contract);
  }
  const expiryDates = body["expiry_dates"];
  if (!Array.isArray(expiryDates)) return null;
  const selectedExpiry = asString(body["selected_expiry"]);
  const delayedMinutes = asNumberOrNull(body["delayed_minutes"]);
  if (selectedExpiry.length === 0 || delayedMinutes === null) return null;
  return {
    symbol: asString(body["symbol"]),
    asOf: asString(body["as_of"]),
    delayedMinutes,
    expiryDates: expiryDates.map((d) => asString(d)).filter((s) => s.length > 0),
    selectedExpiry,
    strikes,
  };
}

export function strikesForExpiry(
  chain: OptionsChain,
  expiry?: string,
): StrikeRow[] {
  const targetExpiry = expiry ?? chain.selectedExpiry;
  const byStrike = new Map<number, OptionContract[]>();
  for (const contract of chain.strikes) {
    if (contract.expiry !== targetExpiry) continue;
    const existing = byStrike.get(contract.strike);
    if (existing === undefined) {
      byStrike.set(contract.strike, [contract]);
    } else {
      existing.push(contract);
    }
  }
  const rows: StrikeRow[] = [];
  for (const [strike, contracts] of byStrike) {
    const call = contracts.find((c) => c.type === "call");
    const put = contracts.find((c) => c.type === "put");
    if (call && put) {
      rows.push({ strike, call, put });
    }
  }
  rows.sort((a, b) => a.strike - b.strike);
  return rows;
}

export function sortByStrike(a: StrikeRow, b: StrikeRow): number {
  return a.strike - b.strike;
}

export function formatGreek(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value >= 0 ? "+" : "-"}${Math.abs(value).toFixed(4)}`;
}

export function formatImpliedVol(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(2)}%`;
}

export function formatPremium(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `$${value.toFixed(2)}`;
}

export async function loadOptions(
  symbol: string,
  signal: AbortSignal,
): Promise<OptionsChain | null> {
  const body = await getJson(`/engine/options/${encodeURIComponent(symbol)}`, signal);
  return parseOptions(body);
}
