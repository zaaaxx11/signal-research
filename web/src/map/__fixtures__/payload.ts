/**
 * Synthetic map payloads.
 *
 * The endpoint's real document is ~250 KB and 5,576 rows; a test that hand-wrote
 * that would be unreadable, and one that downloaded it would not run in CI. So
 * the payloads here are generated: a seeded PRNG (no `Math.random`, so the same
 * run is the same map on every machine), eleven real US sector labels with
 * realistic weights, and a log-normal cap distribution so a handful of names
 * dominate — which is the shape the layout has to survive.
 *
 * `SMALL_MAP` is the readable one: seven stocks, three sectors, numbers a
 * person can check in their head. The UI tests use it. `WIDE_MAP` is the
 * performance/proportionality one: 5,576 rows.
 */

import { parseMap, type MapPayload } from "../types";

export interface WireSymbol {
  s: string;
  c: number;
  v: number;
}

export interface WireSector {
  id: string;
  label: string;
  count: number;
  value: number;
  adv: number;
  dec: number;
  symbols: WireSymbol[];
}

export interface WireMap {
  as_of: string;
  region: string;
  metric: string;
  universe_total: number;
  mapped: number;
  sectors: WireSector[];
}

/** The eleven US sectors, in the order a US map draws them. */
export const SECTOR_SEEDS: readonly { id: string; label: string; weight: number }[] = [
  { id: "technology", label: "Technology", weight: 0.27 },
  { id: "financials", label: "Financials", weight: 0.13 },
  { id: "healthcare", label: "Healthcare", weight: 0.12 },
  { id: "consumer_cyclical", label: "Consumer Cyclical", weight: 0.105 },
  { id: "industrials", label: "Industrials", weight: 0.09 },
  { id: "communication_services", label: "Communication Services", weight: 0.085 },
  { id: "consumer_defensive", label: "Consumer Defensive", weight: 0.07 },
  { id: "energy", label: "Energy", weight: 0.05 },
  { id: "utilities", label: "Utilities", weight: 0.03 },
  { id: "real_estate", label: "Real Estate", weight: 0.025 },
  { id: "basic_materials", label: "Basic Materials", weight: 0.02 },
];

/** mulberry32: 32 bits of state, uniform enough for a fixture, no dependency. */
function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Box–Muller, so caps are log-normal rather than uniform. */
function normal(random: () => number): number {
  const u = Math.max(1e-9, random());
  const v = random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

/** A stable four-letter ticker from an index: 0 → AAAA, 1 → AAAB, … */
export function symbolAt(index: number): string {
  const letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  let value = index;
  let out = "";
  for (let place = 0; place < 4; place += 1) {
    out = letters[value % 26] + out;
    value = Math.floor(value / 26);
  }
  return out;
}

export interface GenerateOptions {
  readonly total?: number;
  readonly seed?: number;
  readonly asOf?: string;
}

/** A full-market wire document. Deterministic for a given seed. */
export function generateWireMap(options: GenerateOptions = {}): WireMap {
  const total = options.total ?? 5576;
  const random = mulberry32(options.seed ?? 20261031);

  const counts = SECTOR_SEEDS.map((sector) => Math.round(sector.weight * total));
  const assigned = counts.reduce((sum, count) => sum + count, 0);
  counts[0] = (counts[0] as number) + (total - assigned);

  let cursor = 0;
  const sectors: WireSector[] = SECTOR_SEEDS.map((seed, sectorIndex) => {
    const count = counts[sectorIndex] as number;
    const symbols: WireSymbol[] = [];
    for (let i = 0; i < count; i += 1) {
      const change = Math.max(-18, Math.min(18, normal(random) * 1.9));
      // e^(1.8 + 1.4 z): median near $6B, a tail past $500B, a floor at $1B.
      // The floor matters: every one of the 5,576 rows must be *drawable* at
      // 1280×800, so the draw-time proof is about 5,576 tiles rather than
      // about the 5,300 that survived a filter.
      const cap = Math.max(1, Math.exp(1.8 + 1.4 * normal(random)));
      symbols.push({ s: symbolAt(cursor + i), c: Math.round(cap * 100) / 100, v: Math.round(change * 100) / 100 });
    }
    cursor += count;

    let sum = 0;
    let adv = 0;
    let dec = 0;
    for (const row of symbols) {
      sum += row.v;
      if (row.v > 0) adv += 1;
      else if (row.v < 0) dec += 1;
    }
    return {
      id: seed.id,
      label: seed.label,
      count,
      value: Math.round((sum / Math.max(1, symbols.length)) * 100) / 100,
      adv,
      dec,
      symbols,
    };
  });

  return {
    as_of: options.asOf ?? "2026-10-31T20:58:12+00:00",
    region: "us",
    metric: "change_1d",
    universe_total: total + 102,
    mapped: total,
    sectors,
  };
}

/**
 * The adversarial shape: one sector where a single name is ~99.3 % of the cap
 * and 400 names share the rest, plus a small sector next door. Any layout that
 * does not filter or squarify honestly turns those 400 into zero-height
 * slivers; the small sector must still keep a readable strip.
 */
export function generateGiantSector(total = 401): WireMap {
  const symbols: WireSymbol[] = [{ s: "GIANT", c: 9000, v: 2.4 }];
  for (let i = 1; i < total; i += 1) {
    symbols.push({ s: symbolAt(i), c: 0.001, v: -0.4 - (i % 13) * 0.05 });
  }
  return {
    as_of: "2026-10-31T20:58:12+00:00",
    region: "us",
    metric: "change",
    universe_total: total + 1,
    mapped: total,
    sectors: [
      { id: "mega", label: "Mega Cap", count: total, value: 2.38, adv: 1, dec: total - 1, symbols },
      { id: "small", label: "Small Cap", count: 3, value: -0.4, adv: 1, dec: 2, symbols: [
        { s: "SML1", c: 30, v: 0.8 },
        { s: "SML2", c: 20, v: -0.9 },
        { s: "SML3", c: 10, v: -1.1 },
      ] },
    ],
  };
}

export const SMALL_WIRE: WireMap = {
  as_of: "2026-10-31T20:58:12+00:00",
  region: "us",
  metric: "change_1d",
  universe_total: 9,
  mapped: 7,
  sectors: [
    {
      id: "technology",
      label: "Technology",
      count: 3,
      value: 0.83,
      adv: 2,
      dec: 1,
      symbols: [
        { s: "AAPL", c: 5034.7, v: 2.1 },
        { s: "MSFT", c: 3100.2, v: 1.2 },
        { s: "NVDA", c: 1204.9, v: -0.8 },
      ],
    },
    {
      id: "financials",
      label: "Financials",
      count: 2,
      value: -0.55,
      adv: 1,
      dec: 1,
      symbols: [
        { s: "JPM", c: 612.4, v: -1.4 },
        { s: "BAC", c: 301.7, v: 0.3 },
      ],
    },
    {
      id: "energy",
      label: "Energy",
      count: 2,
      value: -1.3,
      adv: 0,
      dec: 2,
      symbols: [
        { s: "XOM", c: 452.8, v: -2.6 },
        { s: "CVX", c: 208.3, v: 0 },
      ],
    },
  ],
};

export const SMALL_MAP: MapPayload = parseMap(SMALL_WIRE);
export const WIDE_WIRE: WireMap = generateWireMap();
export const WIDE_MAP: MapPayload = parseMap(WIDE_WIRE);
export const GIANT_MAP: MapPayload = parseMap(generateGiantSector());

/** The wire JSON, as the route would send it (for fetch mocks). */
export function wireJson(map: WireMap): unknown {
  return map;
}
