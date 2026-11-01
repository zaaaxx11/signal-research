/**
 * The Home dashboard's one document: `GET /market/home?region=us`.
 *
 * Home used to be thirteen cards, each of which could go and ask for its own
 * rows. It is now a dashboard of six blocks, and the whole page is **one**
 * round trip: the server composes indices, breadth, sparks, movers, the trimmed
 * map, headlines and Form 4 filings into a single ~45 KB document. A client
 * that asked per block would spend seven round trips and paint seven times.
 *
 * Two rules shape this module, and they are the difference between a dashboard
 * and a wall of plausible-looking zeroes:
 *
 * * **A missing block is `null`, and it says so.** The wire never sends `[]` for
 *   "the source failed" — an empty array is a legitimate answer ("no new highs
 *   today") and is drawn as one. A block that is `null` is named in
 *   `unavailable` and rendered with that reason, never with zeros.
 * * **Absent is not zero.** Every number in this document is nullable, and the
 *   parser keeps `null` as `null`. A price the provider did not report is
 *   printed as an em dash with a title, not as `$0.00`.
 *
 * The wiring is snake_case, deliberately: this module is the only place a wire
 * spelling is read, and every consumer downstream sees camelCase (`changePct`,
 * `tilesTotal`). Unknown or malformed rows are dropped rather than rendered as
 * `undefined`; an array whose rows are all unreadable reads as empty, which is
 * an honest statement about the response.
 */

import { safeExternalHref } from "@/news/format";

/** Rows the movers block may draw, across all four groups. */
export const MOVER_LIMIT = 40;

/** Points kept per sparkline. The wire sends 30–60; a longer series is trimmed
 * from the front, because the newest point is the one the label states. */
export const MAX_CLOSES = 120;

/** The four groups of the movers list, in the order the block draws them. */
export const MOVER_GROUPS = [
  { key: "gainers", wire: "gainers", label: "Top gainers", chip: "Top gainers" },
  { key: "newHigh", wire: "new_high", label: "New highs", chip: "New high" },
  { key: "overbought", wire: "overbought", label: "Overbought", chip: "Overbought" },
  { key: "newLow", wire: "new_low", label: "New lows", chip: "New low" },
] as const;

export type MoverKey = (typeof MOVER_GROUPS)[number]["key"];

/** The blocks whose absence the page can name. */
export const HOME_BLOCK_KEYS = [
  "indices",
  "breadth",
  "sparks",
  "movers",
  "map",
  "news",
  "filings",
  "futures",
  "forex",
  "ipos",
  "insider_latest",
  "insider_top",
  "economic",
] as const;

export type HomeBlockKey = (typeof HOME_BLOCK_KEYS)[number];

export type HomeStatus = "loading" | "ready" | "error";

/* ---------------------------------------------------------------------------
 * The document, after parsing.
 * ------------------------------------------------------------------------ */

export interface HomeIndex {
  readonly symbol: string;
  readonly label: string;
  readonly price: number | null;
  readonly changePct: number | null;
  readonly closes: readonly number[];
}

export interface HomeBreadth {
  readonly universe: number | null;
  readonly advancing: number | null;
  readonly declining: number | null;
  readonly unchanged: number | null;
  readonly newHighs: number | null;
  readonly newLows: number | null;
  readonly above50: number | null;
  readonly below50: number | null;
  readonly above200: number | null;
  readonly below200: number | null;
  /** Shares traded in names that rose, and in names that fell. */
  readonly upVolume: number | null;
  readonly downVolume: number | null;
}

export interface HomeSpark {
  readonly symbol: string;
  readonly name: string;
  readonly price: number | null;
  readonly changePct: number | null;
  readonly volume: number | null;
  readonly closes: readonly number[];
}

export interface HomeMover {
  readonly symbol: string;
  readonly name: string;
  readonly price: number | null;
  readonly changePct: number | null;
  /** The chip the row prints: `Top gainers`, `New high`, `Overbought`, `New low`. */
  readonly group: string;
}

export interface HomeMovers {
  readonly gainers: readonly HomeMover[];
  readonly newHigh: readonly HomeMover[];
  readonly overbought: readonly HomeMover[];
  readonly newLow: readonly HomeMover[];
  /** Rows actually carried, never above `MOVER_LIMIT`. */
  readonly total: number;
}

export interface HomeMapStock {
  readonly s: string;
  /** Market cap, USD millions — the area input. */
  readonly c: number;
  /** Percent change — the colour input. */
  readonly v: number;
}

export interface HomeMapSector {
  readonly id: string;
  readonly value: number | null;
  readonly symbols: readonly HomeMapStock[];
}

export interface HomeMap {
  readonly sectors: readonly HomeMapSector[];
  readonly tiles: number | null;
  readonly tilesTotal: number | null;
}

export interface HomeNews {
  readonly headline: string;
  /** Canonical http(s) URL, or `null` when the feed sent something a browser
   * must not follow (`javascript:`, `data:`, a relative path). */
  readonly url: string | null;
  readonly source: string;
  readonly publishedAt: string | null;
}

export interface HomeFiling {
  readonly symbol: string;
  readonly owner: string;
  readonly role: string;
  readonly filed: string | null;
}

/** A side table row: whatever the server sent, and nothing it did not. */
export type HomeSideRow = Readonly<Record<string, string | number | null>>;

function readSideRow(raw: unknown): HomeSideRow | null {
  if (!isRecord(raw)) return null;
  const out: Record<string, string | number | null> = {};
  for (const [key, value] of Object.entries(raw)) {
    if (value === null || typeof value === "string" || typeof value === "number") out[key] = value;
  }
  return Object.keys(out).length > 0 ? out : null;
}

export interface HomeReport {
  readonly asOf: string | null;
  readonly indices: readonly HomeIndex[] | null;
  readonly breadth: HomeBreadth | null;
  readonly sparks: readonly HomeSpark[] | null;
  readonly movers: HomeMovers | null;
  readonly map: HomeMap | null;
  readonly news: readonly HomeNews[] | null;
  readonly filings: readonly HomeFiling[] | null;
  readonly futures: readonly HomeSideRow[] | null;
  readonly forex: readonly HomeSideRow[] | null;
  readonly ipos: readonly HomeSideRow[] | null;
  readonly insiderLatest: readonly HomeSideRow[] | null;
  readonly insiderTop: readonly HomeSideRow[] | null;
  readonly economic: readonly HomeSideRow[] | null;
  /** Block names the server says it could not build, lower-case. */
  readonly unavailable: readonly string[];
  readonly method: string | null;
}

/** The one path. Root-absolute, same-origin, region named. */
export function homePath(region = "us"): string {
  const params = new URLSearchParams({ region });
  return `/market/home?${params.toString()}`;
}

/* ---------------------------------------------------------------------------
 * Parsing.
 * ------------------------------------------------------------------------ */

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A finite number, or `null`. A numeric string is accepted because providers
 * occasionally quote; `NaN`, `Infinity`, `""` and `null` are not numbers. */
function num(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim().length > 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function str(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function firstNumber(raw: Record<string, unknown>, keys: readonly string[]): number | null {
  for (const key of keys) {
    const value = num(raw[key]);
    if (value !== null) return value;
  }
  return null;
}

function firstString(raw: Record<string, unknown>, keys: readonly string[]): string {
  for (const key of keys) {
    const value = str(raw[key]);
    if (value.length > 0) return value;
  }
  return "";
}

function closes(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  const points: number[] = [];
  for (const entry of value) {
    const point = num(entry);
    if (point !== null) points.push(point);
  }
  return points.length > MAX_CLOSES ? points.slice(points.length - MAX_CLOSES) : points;
}

/**
 * A block array, or `null` when the block itself is absent.
 *
 * The distinction is the whole honesty contract: `null` means "the server could
 * not build this" and the page prints the reason; `[]` means "the server built
 * it and there was nothing in it" and the page says so in one sentence.
 */
function readList<T>(value: unknown, read: (raw: unknown) => T | null): T[] | null {
  if (value === null || value === undefined) return null;
  if (!Array.isArray(value)) return null;
  const rows: T[] = [];
  for (const entry of value) {
    const row = read(entry);
    if (row !== null) rows.push(row);
  }
  return rows;
}

function readIndex(raw: unknown): HomeIndex | null {
  if (!isRecord(raw)) return null;
  const symbol = firstString(raw, ["symbol", "ticker"]);
  if (symbol.length === 0) return null;
  return {
    symbol,
    label: firstString(raw, ["label", "name"]) || symbol,
    price: firstNumber(raw, ["price", "last"]),
    changePct: firstNumber(raw, ["change_pct", "changePct", "change"]),
    closes: closes(raw["closes"]),
  };
}

function readBreadth(raw: unknown): HomeBreadth | null {
  if (!isRecord(raw)) return null;
  return {
    universe: firstNumber(raw, ["universe", "universe_total"]),
    advancing: firstNumber(raw, ["advancing", "adv"]),
    declining: firstNumber(raw, ["declining", "dec"]),
    unchanged: firstNumber(raw, ["unchanged"]),
    newHighs: firstNumber(raw, ["new_highs", "newHighs"]),
    newLows: firstNumber(raw, ["new_lows", "newLows"]),
    above50: firstNumber(raw, ["above_50", "above50"]),
    below50: firstNumber(raw, ["below_50", "below50"]),
    above200: firstNumber(raw, ["above_200", "above200"]),
    below200: firstNumber(raw, ["below_200", "below200"]),
    upVolume: firstNumber(raw, ["up_volume", "upVolume"]),
    downVolume: firstNumber(raw, ["down_volume", "downVolume"]),
  };
}

function readSpark(raw: unknown): HomeSpark | null {
  if (!isRecord(raw)) return null;
  const symbol = firstString(raw, ["symbol", "ticker"]);
  if (symbol.length === 0) return null;
  return {
    symbol,
    name: firstString(raw, ["name", "company"]),
    price: firstNumber(raw, ["price", "last"]),
    changePct: firstNumber(raw, ["change_pct", "changePct", "change"]),
    volume: firstNumber(raw, ["volume", "vol"]),
    closes: closes(raw["closes"]),
  };
}

function readMover(raw: unknown): HomeMover | null {
  if (!isRecord(raw)) return null;
  const symbol = firstString(raw, ["symbol", "ticker"]);
  if (symbol.length === 0) return null;
  return {
    symbol,
    name: firstString(raw, ["name", "company"]),
    price: firstNumber(raw, ["price", "last"]),
    changePct: firstNumber(raw, ["change_pct", "changePct", "change"]),
    group: "",
  };
}

/**
 * Four groups, one budget.
 *
 * The server promises ≤ 40 rows, but a promise is not a guarantee: the budget is
 * enforced here too, in group order, so a future server that sends 40 gainers
 * cannot push the block to 160 rows. A group the server omitted is empty; a
 * `movers` value that carries none of the four group names — in either the flat
 * array the server sends or the fixture's object — is a missing block.
 */
function readMovers(raw: unknown): HomeMovers | null {
  // Two shapes, one reader. The server sends a flat array (each row carries its
  // own `group`), which is the compact form: forty rows, no repeated keys, one
  // order. An object of four arrays is also accepted because that is what a
  // hand-written fixture looks like, and accepting both costs six lines.
  let byGroup: Record<string, unknown[]> | null = null;
  if (Array.isArray(raw)) {
    const buckets: Record<string, unknown[]> = {};
    for (const row of raw) {
      if (!isRecord(row)) continue;
      const wire = typeof row.group === "string" ? row.group : "";
      (buckets[wire] ??= []).push(row);
    }
    byGroup = buckets;
  } else if (isRecord(raw)) {
    byGroup = {};
    for (const group of MOVER_GROUPS) {
      const rows = raw[group.wire];
      if (Array.isArray(rows)) byGroup[group.wire] = rows;
    }
  }
  if (byGroup === null) return null;
  const present = MOVER_GROUPS.some((group) => byGroup[group.wire] !== undefined);
  if (!present) return null;

  const grouped: Record<MoverKey, HomeMover[]> = {
    gainers: [],
    newHigh: [],
    overbought: [],
    newLow: [],
  };
  let budget = MOVER_LIMIT;

  for (const group of MOVER_GROUPS) {
    const rows = readList(byGroup[group.wire], readMover) ?? [];
    for (const row of rows) {
      if (budget <= 0) break;
      grouped[group.key].push({ ...row, group: group.chip });
      budget -= 1;
    }
  }

  return {
    gainers: grouped.gainers,
    newHigh: grouped.newHigh,
    overbought: grouped.overbought,
    newLow: grouped.newLow,
    total: MOVER_LIMIT - budget,
  };
}

function readMapStock(raw: unknown): HomeMapStock | null {
  if (!isRecord(raw)) return null;
  const symbol = firstString(raw, ["s", "symbol"]);
  if (symbol.length === 0 || symbol.length > 16) return null;
  // A cap is an area. An absent or negative cap is a missing fact, not a small
  // company, so the row leaves the map rather than sitting at zero.
  const cap = firstNumber(raw, ["c", "cap"]);
  if (cap === null || cap < 0) return null;
  // `v` may legitimately be absent (a halted name). It has an area; it just has
  // no direction, and the ramp reads that as flat rather than inventing a move.
  const change = firstNumber(raw, ["v", "change"]);
  return { s: symbol.toUpperCase(), c: cap, v: change ?? 0 };
}

function readMapSector(raw: unknown): HomeMapSector | null {
  if (!isRecord(raw)) return null;
  const id = firstString(raw, ["id", "label"]);
  if (id.length === 0) return null;
  const symbols: HomeMapStock[] = [];
  if (Array.isArray(raw["symbols"])) {
    for (const entry of raw["symbols"]) {
      const stock = readMapStock(entry);
      if (stock !== null) symbols.push(stock);
    }
  }
  if (symbols.length === 0) return null;
  return { id, value: firstNumber(raw, ["value", "change"]), symbols };
}

function readMap(raw: unknown): HomeMap | null {
  if (!isRecord(raw)) return null;
  const sectors = readList(raw["sectors"], readMapSector) ?? [];
  return {
    sectors,
    tiles: firstNumber(raw, ["tiles", "drawn"]),
    tilesTotal: firstNumber(raw, ["tiles_total", "tilesTotal", "total"]),
  };
}

function readNewsItem(raw: unknown): HomeNews | null {
  if (!isRecord(raw)) return null;
  const headline = firstString(raw, ["headline", "title"]);
  if (headline.length === 0) return null;
  return {
    headline,
    // Sanitised here, not at render: an anchor that never receives a
    // `javascript:` string cannot be made to follow one.
    url: safeExternalHref(raw["url"]),
    source: firstString(raw, ["source", "feed"]),
    publishedAt: firstString(raw, ["published_at", "publishedAt", "published"]),
  };
}

function readFiling(raw: unknown): HomeFiling | null {
  if (!isRecord(raw)) return null;
  const symbol = firstString(raw, ["symbol", "ticker"]);
  const owner = firstString(raw, ["owner", "name"]);
  if (symbol.length === 0 && owner.length === 0) return null;
  return {
    symbol,
    owner,
    role: firstString(raw, ["role", "title", "relationship"]),
    filed: firstString(raw, ["filed", "filed_at", "date"]) || null,
  };
}

/**
 * The document, whatever the wire sent.
 *
 * Never throws: a body that is not an object reads as a report where every
 * block is missing, which is exactly what the page can explain. A block that is
 * absent and a block that is `null` are the same thing to a reader.
 */
export function readHome(body: unknown): HomeReport {
  if (!isRecord(body)) {
    return {
      asOf: null,
      indices: null,
      breadth: null,
      sparks: null,
      movers: null,
      map: null,
      news: null,
      filings: null,
      futures: null,
      forex: null,
      ipos: null,
      insiderLatest: null,
      insiderTop: null,
      economic: null,
      unavailable: [],
      method: null,
    };
  }

  const named = Array.isArray(body["unavailable"])
    ? body["unavailable"]
        .filter((entry): entry is string => typeof entry === "string")
        .map((entry) => entry.trim().toLowerCase())
    : [];

  return {
    asOf: str(body["as_of"]) || null,
    indices: readList(body["indices"], readIndex),
    breadth: readBreadth(body["breadth"]),
    sparks: readList(body["sparks"], readSpark),
    movers: readMovers(body["movers"]),
    map: readMap(body["map"]),
    news: readList(body["news"], readNewsItem),
    filings: readList(body["filings"], readFiling),
    futures: readList(body["futures"], readSideRow),
    forex: readList(body["forex"], readSideRow),
    ipos: readList(body["ipos"], readSideRow),
    insiderLatest: readList(body["insider_latest"], readSideRow),
    insiderTop: readList(body["insider_top"], readSideRow),
    economic: readList(body["economic"], readSideRow),
    unavailable: named,
    method: str(body["method"]) || null,
  };
}

/* ---------------------------------------------------------------------------
 * States and reasons.
 * ------------------------------------------------------------------------ */

export type BlockKind = "loading" | "error" | "missing" | "ready";

export interface BlockState {
  readonly kind: BlockKind;
  /** The sentence a `missing` or `error` card prints; `null` otherwise. */
  readonly reason: string | null;
}

export const LOADING: BlockState = { kind: "loading", reason: null };
export const READY: BlockState = { kind: "ready", reason: null };
export const FAILED: BlockState = {
  kind: "error",
  reason: "The dashboard did not load.",
};

/**
 * Why a block is empty, in one sentence the card can print.
 *
 * When the server named the block in `unavailable` we say its source failed for
 * this request — that is what the field means. When it did not, the block was
 * absent from the document, and the card says exactly that rather than guessing
 * at a cause.
 */
export function missingReason(
  report: HomeReport | null,
  key: HomeBlockKey,
  label: string,
): string {
  const named = report?.unavailable.includes(key) ?? false;
  return named
    ? `${label} unavailable — the source did not answer this request.`
    : `${label} unavailable — the response carried no ${key} block.`;
}

/** The state of one block, from the page's status and the parsed document. */
export function blockState(
  status: HomeStatus,
  report: HomeReport | null,
  key: HomeBlockKey,
  label: string,
): BlockState {
  if (status === "loading") return LOADING;
  if (status === "error") return FAILED;
  // The wire names five of these with underscores; the parsed document keeps
  // camelCase. One table, so the two spellings cannot drift.
  const block =
    key === "insider_latest"
      ? report?.insiderLatest
      : key === "insider_top"
        ? report?.insiderTop
        : key === "futures"
          ? report?.futures
          : key === "forex"
            ? report?.forex
            : key === "ipos"
              ? report?.ipos
              : key === "economic"
                ? report?.economic
                : report?.[key];
  if (block === null || block === undefined) return { kind: "missing", reason: missingReason(report, key, label) };
  return READY;
}
