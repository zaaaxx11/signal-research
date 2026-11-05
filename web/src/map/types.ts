/**
 * `GET /market/map?shape=2&timeframe=1d` — the wire contract, and the one
 * reader that is allowed to trust it.
 *
 * Shape-2 tiles carry `{s, c, v, n, p}`: symbol, cap, change%, name, price.
 * The old shape (without n/p) is still parsed; name and price fall back to
 * the symbol and null when absent.
 *
 * A map is unlike a table: one malformed row cannot be skipped at render time,
 * because every row's *area* is a share of the whole. So the payload is parsed
 * once, here, into a document the layout can lay out without asking any more
 * questions:
 *
 *   * a symbol with no cap or no number is dropped (it has no area and no
 *     colour) — never drawn at zero, which would read as "tiny", not "unknown";
 *   * a repeated symbol inside one sector keeps the larger cap, so the same
 *     ticker cannot be drawn twice in one block;
 *   * a sector's `value` that is absent is recomputed as the mean of its
 *     stocks' changes, and `count`/`adv`/`dec` fall back to what the rows
 *     themselves say.
 */

/** One stock, as the map sees it: a symbol, a size, a colour, and metadata. */
export interface MapStock {
  readonly symbol: string;
  /** Market cap, USD billions — the layout's only input for area. */
  readonly cap: number;
  /** Percent change; the colour ramp's only input. */
  readonly change: number;
  /** Company name, or null when the wire did not carry one. */
  readonly name: string | null;
  /** Last price, or null when absent. */
  readonly price: number | null;
}

/** One sector block. `value` is the average change the block is labelled with. */
export interface MapSector {
  readonly id: string;
  readonly label: string;
  /** Rows the provider counted, which may exceed `stocks.length`. */
  readonly count: number;
  /** Average change in percent; null when no row carried one. */
  readonly value: number | null;
  readonly adv: number;
  readonly dec: number;
  readonly stocks: readonly MapStock[];
}

export interface MapPayload {
  readonly asOf: string;
  readonly region: string;
  readonly metric: string;
  readonly universeTotal: number;
  readonly mapped: number;
  readonly sectors: readonly MapSector[];
}

/** Market groups for the filter checkboxes. */
export interface MarketGroup {
  readonly id: string;
  readonly label: string;
  readonly count: number;
}

/** A payload we could not read at all. Never a partial render of half a map. */
export class MapMalformed extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MapMalformed";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function text(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim().length > 0 ? value : fallback;
}

function parseStock(raw: unknown): MapStock | null {
  if (!isRecord(raw)) return null;
  const symbol = typeof raw["s"] === "string" ? raw["s"].trim().toUpperCase() : "";
  if (symbol.length === 0 || symbol.length > 16) return null;
  const cap = finite(raw["c"]);
  if (cap === null || cap < 0) return null;
  const change = finite(raw["v"]) ?? 0;
  const name =
    typeof raw["n"] === "string" && raw["n"].trim().length > 0 ? raw["n"].trim() : null;
  const price = finite(raw["p"]);
  return { symbol, cap, change, name, price };
}

function parseStocks(raw: unknown): MapStock[] {
  if (!Array.isArray(raw)) return [];
  const bySymbol = new Map<string, MapStock>();
  for (const entry of raw) {
    const stock = parseStock(entry);
    if (stock === null) continue;
    const previous = bySymbol.get(stock.symbol);
    if (previous === undefined || stock.cap > previous.cap) {
      bySymbol.set(
        stock.symbol,
        previous === undefined ? stock : { ...stock, change: previous.change },
      );
    }
  }
  return [...bySymbol.values()];
}

function parseSector(raw: unknown, index: number): MapSector | null {
  if (!isRecord(raw)) return null;
  const id = text(raw["id"], "");
  if (id.length === 0) return null;
  const stocks = parseStocks(raw["symbols"] ?? raw["tiles"]);
  if (stocks.length === 0) return null;

  const counted = finite(raw["count"]);
  const advancing = finite(raw["adv"]);
  const declining = finite(raw["dec"]);
  const value = finite(raw.value);

  const reported = value ?? meanChange(stocks);
  return {
    id,
    label: text(raw["label"], prettyId(id, index)),
    count: counted !== null && counted >= stocks.length ? Math.round(counted) : stocks.length,
    value: reported,
    adv:
      advancing !== null && advancing >= 0
        ? Math.round(advancing)
        : stocks.filter((s) => s.change > 0).length,
    dec:
      declining !== null && declining >= 0
        ? Math.round(declining)
        : stocks.filter((s) => s.change < 0).length,
    stocks,
  };
}

function prettyId(id: string, index: number): string {
  const words = id.replace(/[_-]+/g, " ").trim();
  if (words.length > 0) return words.replace(/\b[a-z]/g, (letter) => letter.toUpperCase());
  return `Sector ${index + 1}`;
}

function meanChange(stocks: readonly MapStock[]): number | null {
  if (stocks.length === 0) return null;
  let sum = 0;
  for (const stock of stocks) sum += stock.change;
  return sum / stocks.length;
}

export function parseMap(body: unknown): MapPayload {
  if (!isRecord(body)) throw new MapMalformed("payload is not an object");

  const rawSectors = body["sectors"];
  if (!Array.isArray(rawSectors)) throw new MapMalformed("payload has no sectors array");

  const sectors: MapSector[] = [];
  const seen = new Set<string>();
  for (let index = 0; index < rawSectors.length; index += 1) {
    const sector = parseSector(rawSectors[index], index);
    if (sector === null) continue;
    if (seen.has(sector.id)) continue;
    seen.add(sector.id);
    sectors.push(sector);
  }

  const universe = finite(body["universe_total"]);
  const mapped = finite(body["mapped"]);
  const rows = sectors.reduce((total, sector) => total + sector.stocks.length, 0);

  return {
    asOf: typeof body["as_of"] === "string" ? body["as_of"] : "",
    region: text(body["region"], "us"),
    metric: text(body["metric"], "change"),
    universeTotal: universe !== null && universe >= rows ? Math.round(universe) : rows,
    mapped: mapped !== null && mapped >= 0 ? Math.round(mapped) : rows,
    sectors,
  };
}

export function sectorCap(sector: MapSector): number {
  let total = 0;
  for (const stock of sector.stocks) total += stock.cap;
  return total;
}