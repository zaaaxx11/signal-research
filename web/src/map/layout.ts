/**
 * The treemap: areas proportional to market cap, squarified, deterministic.
 *
 * Two levels, one algorithm. Sectors are laid out into the canvas, then each
 * sector's own rect is laid out into stocks. That is the whole model; there is
 * no third pass and no collision repair, because a layout that needs repair is
 * a layout whose invariants were never true.
 *
 * **Why squarified and not slice-and-dice.** Slice-and-dice gives every tile
 * the full height of its parent and a width proportional to its share: the
 * twenty smallest names in a sector become 0.3 px columns, which is both
 * unreadable and impossible to hit. Squarify chooses the row shape that keeps
 * the *worst* aspect ratio in it as close to 1 as it can, so the smallest
 * tiles stay hittable for far longer.
 *
 * **The floor is a filter, not a lie.** A tile smaller than `MIN_TILE` on
 * either side cannot be seen or pointed at, so it is not emitted: it is
 * counted in `omitted` and the page prints that count. Growing it to 1 px in
 * place would overlap its neighbours, and drawing it at sub-pixel size would
 * put a colour on screen that a pointer can never select. Filtering keeps
 * every invariant below true for everything that *is* emitted, and the number
 * that went missing is on screen rather than in a comment.
 *
 * The invariants (each one is a test in `layout.test.ts`):
 *
 *   proportional   a sector's area / canvas area == its cap / total cap
 *                  (within float noise; the test allows 1%)
 *   contained      every stock rect is inside its sector's content rect, which
 *                  is inside its block, which is inside the canvas
 *   disjoint       no two emitted rects overlap by more than a rounding error
 *   deterministic  the same payload and box produce byte-identical output
 *
 * All coordinates are CSS px and fractional. The painter rounds at the last
 * moment; the *layout* keeps fractions so areas stay proportional instead of
 * drifting by a pixel per row.
 */

import type { MapPayload, MapSector, MapStock } from "./types";
import { sectorCap } from "./types";

export interface Rect {
  readonly x: number;
  readonly y: number;
  readonly w: number;
  readonly h: number;
}

export interface Tile extends Rect {
  readonly symbol: string;
  readonly sectorId: string;
  readonly change: number;
  readonly cap: number;
  readonly name: string | null;
  readonly price: number | null;
}

export interface SectorLayout {
  readonly id: string;
  readonly label: string;
  readonly value: number | null;
  readonly count: number;
  readonly adv: number;
  readonly dec: number;
  /** Market cap the block's area is proportional to, USD billions. */
  readonly cap: number;
  /** The full sector rectangle as squarified into the canvas. */
  readonly rect: Rect;
  /** The painted block. Equal to `rect` by construction — see `layoutSector`. */
  readonly block: Rect;
  /** The label band, when the block is big enough to hold one. */
  readonly header: Rect | null;
  /** The area stocks are laid out into (block minus header). */
  readonly content: Rect;
  readonly tiles: readonly Tile[];
  /** Stocks too small to draw at this size. Already excluded from `tiles`. */
  readonly omitted: number;
  /** The eight largest absolute moves, for the hover popup. */
  readonly top: readonly MapStock[];
}

export interface MapLayout {
  readonly root: Rect;
  readonly sectors: readonly SectorLayout[];
  /** Every drawn tile, in sector order then cap order — the keyboard order. */
  readonly tiles: readonly Tile[];
  readonly drawn: number;
  readonly omitted: number;
  readonly total: number;
}

export interface LayoutOptions {
  /** Outer breathing room inside the canvas, px. */
  readonly pad?: number;
  /** The sector label band, px. */
  readonly headerHeight?: number;
  /** Below this a block gets no label band at all. */
  readonly headerMinWidth?: number;
  readonly headerMinHeight?: number;
  /** Nothing smaller than this in either dimension is emitted. */
  readonly minTile?: number;
}

/** The smallest emitted tile, px. One CSS pixel is the smallest target. */
export const MIN_TILE = 1;
/** Space between two sector blocks, px, split 1 px per side. */
export const SECTOR_GUTTER = 2;
export const SECTOR_HEADER = 16;
export const ROOT_PAD = 1;

const DEFAULTS: Required<LayoutOptions> = {
  pad: ROOT_PAD,
  headerHeight: SECTOR_HEADER,
  headerMinWidth: 72,
  headerMinHeight: 34,
  minTile: MIN_TILE,
};

/** The eight movers the popup lists: largest absolute change wins. */
const TOP_MOVERS = 8;

function clampRect(rect: Rect, bounds: Rect): Rect {
  const x = Math.max(bounds.x, Math.min(rect.x, bounds.x + bounds.w));
  const y = Math.max(bounds.y, Math.min(rect.y, bounds.y + bounds.h));
  const right = Math.max(x, Math.min(rect.x + rect.w, bounds.x + bounds.w));
  const bottom = Math.max(y, Math.min(rect.y + rect.h, bounds.y + bounds.h));
  return { x, y, w: right - x, h: bottom - y };
}

/** Shrink a rect on every side by `amount`, never past empty. */
function deflate(rect: Rect, amount: number): Rect {
  const w = Math.max(0, rect.w - amount * 2);
  const h = Math.max(0, rect.h - amount * 2);
  return { x: rect.x + (rect.w - w) / 2, y: rect.y + (rect.h - h) / 2, w, h };
}

/**
 * The worst aspect ratio a row would have, from its running sum/max/min.
 *
 * This is the squarify objective (Bruls, Huizing & van Wijk 2000): a row is
 * extended for as long as the worst tile in it keeps getting squarer. Infinity
 * for a degenerate row, which makes the first tile always acceptable.
 *
 * It takes the aggregates rather than the row because the row is built one
 * element at a time: recomputing sum/max/min over a copied array for every
 * candidate made the 5,576-row layout O(n²) in allocations (25 ms). With the
 * aggregates carried forward it is one pass and no garbage.
 */
function worstRatio(sum: number, max: number, min: number, side: number): number {
  if (!(sum > 0) || !(min > 0) || !(side > 0)) return Infinity;
  const side2 = side * side;
  const sum2 = sum * sum;
  return Math.max((side2 * max) / sum2, sum2 / (side2 * min));
}

/**
 * Lay `areas` into `rect`, in the order given, returning one rect per area.
 *
 * The areas are normalized to the rect's own area, so callers pass cap values
 * (or anything positive) and get a covering partition back. Zero and negative
 * areas come back as zero-size rects at the origin of the rect: they cannot be
 * drawn, and the caller's `minTile` filter is what decides their fate.
 */
export function squarify(areas: readonly number[], rect: Rect): Rect[] {
  const count = areas.length;
  if (count === 0) return [];
  if (!(rect.w > 0) || !(rect.h > 0)) {
    return areas.map(() => ({ x: rect.x, y: rect.y, w: 0, h: 0 }));
  }

  let total = 0;
  for (const area of areas) if (area > 0) total += area;
  if (!(total > 0)) return areas.map(() => ({ x: rect.x, y: rect.y, w: 0, h: 0 }));

  const scale = (rect.w * rect.h) / total;
  const scaled = areas.map((area) => (area > 0 ? area * scale : 0));
  const out: Rect[] = new Array(count);

  let x = rect.x;
  let y = rect.y;
  let w = rect.w;
  let h = rect.h;
  let index = 0;

  while (index < count) {
    const side = Math.min(w, h);
    if (!(side > 0)) {
      // A zero-area strip left over from a degenerate row: the rest cannot be
      // placed, and pretending otherwise would put rects outside the parent.
      for (; index < count; index += 1) out[index] = { x, y, w: 0, h: 0 };
      break;
    }

    let rowSum = scaled[index] as number;
    let rowMax = rowSum;
    let rowMin = rowSum;
    let rowWorst = worstRatio(rowSum, rowMax, rowMin, side);
    let next = index + 1;
    while (next < count) {
      const candidate = scaled[next] as number;
      const nextSum = rowSum + candidate;
      const nextMax = candidate > rowMax ? candidate : rowMax;
      const nextMin = candidate < rowMin ? candidate : rowMin;
      const nextWorst = worstRatio(nextSum, nextMax, nextMin, side);
      if (nextWorst <= rowWorst) {
        rowSum = nextSum;
        rowMax = nextMax;
        rowMin = nextMin;
        rowWorst = nextWorst;
        next += 1;
      } else break;
    }

    if (!(rowSum > 0)) {
      for (let i = index; i < next; i += 1) out[i] = { x, y, w: 0, h: 0 };
      index = next;
      continue;
    }

    if (w >= h) {
      const columnWidth = rowSum / h;
      let cursor = y;
      for (let i = index; i < next; i += 1) {
        const tileHeight = (scaled[i] as number) / columnWidth;
        out[i] = { x, y: cursor, w: columnWidth, h: tileHeight };
        cursor += tileHeight;
      }
      x += columnWidth;
      w -= columnWidth;
    } else {
      const rowHeight = rowSum / w;
      let cursor = x;
      for (let i = index; i < next; i += 1) {
        const tileWidth = (scaled[i] as number) / rowHeight;
        out[i] = { x: cursor, y, w: tileWidth, h: rowHeight };
        cursor += tileWidth;
      }
      y += rowHeight;
      h -= rowHeight;
    }

    index = next;
  }

  // Floating point can leave the last row a hair past the parent's edge. The
  // clamp is on the parent, never the neighbours, so it cannot create overlap.
  return out.map((cell) => clampRect(cell, rect));
}

/** Descending by cap, ties by id/symbol, so two runs cannot disagree. */
function bySize<T extends { cap: number }>(key: (item: T) => string) {
  return (left: T, right: T): number => {
    if (right.cap !== left.cap) return right.cap - left.cap;
    return key(left) < key(right) ? -1 : key(left) > key(right) ? 1 : 0;
  };
}

function topMovers(stocks: readonly MapStock[]): MapStock[] {
  return [...stocks]
    .sort((left, right) => {
      const a = Math.abs(left.change);
      const b = Math.abs(right.change);
      if (b !== a) return b - a;
      return left.symbol < right.symbol ? -1 : left.symbol > right.symbol ? 1 : 0;
    })
    .slice(0, TOP_MOVERS);
}

function layoutSector(
  sector: MapSector,
  rect: Rect,
  cap: number,
  options: Required<LayoutOptions>,
): SectorLayout {
  // The block *is* the squarified rect: shrinking it here would make a small
  // sector's painted share drift below its cap share by a fixed 2 px ring, and
  // proportionality is the one thing a treemap may not fudge. The gutter is
  // taken out of the content instead — 1 px on each side — so the seam is
  // between tiles and the block, never between the block and its area.
  const block = rect;
  const labelled = block.w >= options.headerMinWidth && block.h >= options.headerMinHeight;
  const header = labelled
    ? { x: block.x, y: block.y, w: block.w, h: Math.min(options.headerHeight, block.h) }
    : null;
  const inset = SECTOR_GUTTER / 2;
  const content: Rect = {
    x: block.x + inset,
    y: block.y + (header?.h ?? 0),
    w: Math.max(0, block.w - inset * 2),
    h: Math.max(0, block.h - (header?.h ?? 0) - inset),
  };

  const stocks = [...sector.stocks].sort(bySize<MapStock>((stock) => stock.symbol));
  const drawable = stocks.filter((stock) => stock.cap > 0);
  let omitted = stocks.length - drawable.length;

  const tiles: Tile[] = [];
  if (content.w > 0 && content.h > 0 && drawable.length > 0) {
    const rects = squarify(
      drawable.map((stock) => stock.cap),
      content,
    );
    for (let index = 0; index < drawable.length; index += 1) {
      const stock = drawable[index] as MapStock;
      const cell = rects[index] as Rect;
      if (cell.w < options.minTile || cell.h < options.minTile) {
        omitted += 1;
        continue;
      }
      tiles.push({
        symbol: stock.symbol,
        sectorId: sector.id,
        change: stock.change,
        cap: stock.cap,
        name: stock.name,
        price: stock.price,
        x: cell.x,
        y: cell.y,
        w: cell.w,
        h: cell.h,
      });
    }
  } else {
    omitted += drawable.length;
  }

  return {
    id: sector.id,
    label: sector.label,
    value: sector.value,
    count: sector.count,
    adv: sector.adv,
    dec: sector.dec,
    cap,
    rect,
    block,
    header,
    content,
    tiles,
    omitted,
    top: topMovers(sector.stocks),
  };
}

/**
 * Lay a payload into a `width` × `height` box.
 *
 * Sectors are ordered by cap (largest first) before squarifying — squarify is
 * only good at keeping aspect ratios when the inputs descend, and the order is
 * also the legend's order and the keyboard's order, so one decision serves
 * three surfaces. Sectors with no cap at all are kept in the result with an
 * empty rect so the legend, the table and the omitted count still name them.
 */
export function layoutMap(payload: MapPayload, width: number, height: number, options: LayoutOptions = {}): MapLayout {
  const settings: Required<LayoutOptions> = { ...DEFAULTS, ...options };
  const root: Rect = {
    x: 0,
    y: 0,
    w: Number.isFinite(width) && width > 0 ? width : 0,
    h: Number.isFinite(height) && height > 0 ? height : 0,
  };
  const frame = deflate(root, settings.pad);
  const empty: Rect = { x: frame.x, y: frame.y, w: 0, h: 0 };

  const sized = payload.sectors.map((sector) => ({ sector, cap: sectorCap(sector) }));
  const ordered = [...sized].sort((left, right) => {
    if (right.cap !== left.cap) return right.cap - left.cap;
    return left.sector.id < right.sector.id ? -1 : left.sector.id > right.sector.id ? 1 : 0;
  });
  const drawable = ordered.filter((entry) => entry.cap > 0);

  const rects = frame.w > 0 && frame.h > 0 ? squarify(drawable.map((entry) => entry.cap), frame) : [];

  let rectIndex = 0;
  const sectors: SectorLayout[] = ordered.map((entry) => {
    if (entry.cap <= 0) return layoutSector(entry.sector, empty, 0, settings);
    const rect = rects[rectIndex] ?? empty;
    rectIndex += 1;
    return layoutSector(entry.sector, rect, entry.cap, settings);
  });

  const tiles: Tile[] = [];
  let omitted = 0;
  let total = 0;
  for (const sector of sectors) {
    tiles.push(...sector.tiles);
    omitted += sector.omitted;
    total += sector.tiles.length + sector.omitted;
  }

  return { root, sectors, tiles, drawn: tiles.length, omitted, total };
}

export interface MapHit {
  readonly sector: SectorLayout;
  /** null when the pointer is over the sector's gutter or its label band. */
  readonly tile: Tile | null;
}

function inside(rect: Rect, x: number, y: number): boolean {
  return x >= rect.x && x < rect.x + rect.w && y >= rect.y && y < rect.y + rect.h;
}

/**
 * Which sector and stock a canvas coordinate lands on.
 *
 * Arithmetic over the same rects the painter used — there is no per-tile DOM
 * and no `elementFromPoint`. Sectors are few, so the first loop is 11
 * comparisons; only the sector that owns the point is searched tile by tile.
 */
export function hitTest(layout: MapLayout, x: number, y: number): MapHit | null {
  for (const sector of layout.sectors) {
    if (sector.rect.w <= 0 || sector.rect.h <= 0) continue;
    if (!inside(sector.rect, x, y)) continue;
    for (const tile of sector.tiles) {
      if (inside(tile, x, y)) return { sector, tile };
    }
    return { sector, tile: null };
  }
  return null;
}

export type Direction = "left" | "right" | "up" | "down";

/**
 * The next tile in a direction, by centre distance.
 *
 * Candidates are the tiles whose centre lies far enough in the direction to be
 * a real move (half a pixel, so a rounding nudge cannot make "right" pick the
 * tile already selected), scored by the distance along the axis plus one and a
 * half times the distance across it. The bias keeps a wall of same-row tiles
 * reachable: straight ahead always wins over a slightly nearer diagonal.
 */
export function nextTile(layout: MapLayout, current: number, direction: Direction): number {
  const tiles = layout.tiles;
  if (tiles.length === 0) return -1;
  if (current < 0 || current >= tiles.length) return 0;

  const from = tiles[current] as Tile;
  const cx = from.x + from.w / 2;
  const cy = from.y + from.h / 2;
  const horizontal = direction === "left" || direction === "right";
  const forward = direction === "right" || direction === "down";

  let best = -1;
  let bestScore = Infinity;
  for (let index = 0; index < tiles.length; index += 1) {
    if (index === current) continue;
    const tile = tiles[index] as Tile;
    const tx = tile.x + tile.w / 2;
    const ty = tile.y + tile.h / 2;
    const along = (horizontal ? tx - cx : ty - cy) * (forward ? 1 : -1);
    if (along <= 0.5) continue;
    const across = Math.abs(horizontal ? ty - cy : tx - cx);
    const score = along + across * 1.5;
    if (score < bestScore) {
      bestScore = score;
      best = index;
    }
  }
  return best >= 0 ? best : current;
}

/** The index of a symbol's tile, or -1. Used to restore a selection. */
export function indexOfSymbol(layout: MapLayout, symbol: string): number {
  for (let index = 0; index < layout.tiles.length; index += 1) {
    if ((layout.tiles[index] as Tile).symbol === symbol) return index;
  }
  return -1;
}
