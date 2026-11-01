import { describe, expect, it } from "vitest";

import { GIANT_MAP, SMALL_MAP, WIDE_MAP } from "./__fixtures__/payload";
import { hitTest, layoutMap, nextTile, squarify, type MapLayout, type Rect } from "./layout";

/**
 * The layout's four invariants, proven on the documents the page actually
 * receives: a seven-row map, the 5,576-row market, and the adversarial
 * one-giant-name sector.
 *
 * "Within 1 %" is a relative tolerance on the cap share, not one percentage
 * point of the canvas — a 2 % sector that drifts by a whole point would pass a
 * loose reading and be wrong by 50 % of itself.
 */

function area(rect: Rect): number {
  return rect.w * rect.h;
}

function expectInside(child: Rect, parent: Rect, label: string): void {
  const epsilon = 1e-6;
  expect(child.x, `${label} left`).toBeGreaterThanOrEqual(parent.x - epsilon);
  expect(child.y, `${label} top`).toBeGreaterThanOrEqual(parent.y - epsilon);
  expect(child.x + child.w, `${label} right`).toBeLessThanOrEqual(parent.x + parent.w + epsilon);
  expect(child.y + child.h, `${label} bottom`).toBeLessThanOrEqual(parent.y + parent.h + epsilon);
}

/**
 * Pairwise disjointness by sweep: sort by left edge, keep the rects still
 * overlapping the sweep line. The naive double loop over 5,576 tiles is 15.5M
 * comparisons; this is a few thousand.
 */
function expectDisjoint(rects: readonly Rect[], label: string): void {
  const epsilon = 1e-6;
  const sorted = [...rects].sort((left, right) => left.x - right.x);
  const active: Rect[] = [];
  let comparisons = 0;

  for (const rect of sorted) {
    for (let index = active.length - 1; index >= 0; index -= 1) {
      const other = active[index] as Rect;
      if (other.x + other.w <= rect.x + epsilon) {
        active.splice(index, 1);
        continue;
      }
      comparisons += 1;
      const overlapX = Math.min(other.x + other.w, rect.x + rect.w) - Math.max(other.x, rect.x);
      const overlapY = Math.min(other.y + other.h, rect.y + rect.h) - Math.max(other.y, rect.y);
      if (overlapX > epsilon && overlapY > epsilon) {
        throw new Error(`${label}: ${JSON.stringify(other)} overlaps ${JSON.stringify(rect)}`);
      }
    }
    active.push(rect);
  }
  // A floor, so a broken sweep (everything sorted into one bucket) is visible.
  expect(comparisons).toBeGreaterThan(0);
}

function sectorCaps(layout: MapLayout): number {
  return layout.sectors.reduce((total, sector) => total + sector.cap, 0);
}

describe("squarify", () => {
  it("tiles a box with the areas it was given", () => {
    const areas = [6, 6, 4, 3, 2, 2, 1];
    const rect: Rect = { x: 0, y: 0, w: 600, h: 400 };
    const cells = squarify(areas, rect);
    const total = areas.reduce((sum, value) => sum + value, 0);

    expect(cells).toHaveLength(areas.length);
    for (let index = 0; index < cells.length; index += 1) {
      const share = area(cells[index] as Rect) / (rect.w * rect.h);
      expect(Math.abs(share - (areas[index] as number) / total)).toBeLessThan(0.01 * ((areas[index] as number) / total));
      expectInside(cells[index] as Rect, rect, `cell ${index}`);
    }
    expectDisjoint(cells, "squarify");

    const covered = cells.reduce((sum, cell) => sum + area(cell), 0);
    expect(Math.abs(covered - rect.w * rect.h)).toBeLessThan(1e-6 * rect.w * rect.h);
  });

  it("keeps the aspect ratio of a dominant tile sane", () => {
    // The one-giant case at the primitive level: 95 % of the box, 100 slivers.
    const areas = [95, ...Array.from({ length: 100 }, () => 0.05)];
    const cells = squarify(areas, { x: 0, y: 0, w: 800, h: 400 });
    const giant = cells[0] as Rect;
    expect(giant.h / giant.w).toBeGreaterThan(0.25);
    expect(giant.h / giant.w).toBeLessThan(4);
  });
});

describe("layoutMap — the 5,576-row market", () => {
  const layout = layoutMap(WIDE_MAP, 1280, 800);

  it("draws every row or counts it as omitted", () => {
    expect(layout.drawn + layout.omitted).toBe(layout.total);
    expect(layout.total).toBe(5576);
    // The fixture's cap floor is $1B, so at 1280×800 every row clears 1 px.
    expect(layout.drawn).toBe(5576);
    expect(layout.omitted).toBe(0);
  });

  it("keeps sector areas within 1 % of their cap share", () => {
    const totalCap = sectorCaps(layout);
    const drawnArea = layout.sectors.reduce((sum, sector) => sum + area(sector.rect), 0);
    expect(totalCap).toBeGreaterThan(0);
    let worst = 0;
    for (const sector of layout.sectors) {
      const expected = sector.cap / totalCap;
      const actual = area(sector.rect) / drawnArea;
      const relative = Math.abs(actual - expected) / expected;
      worst = Math.max(worst, relative);
      expect(relative, `${sector.id} share`).toBeLessThan(0.01);
    }
    expect(worst).toBeLessThan(0.01);
  });

  it("keeps stock areas within 1 % of their cap share inside the sector", () => {
    const sector = layout.sectors[0];
    expect(sector).toBeDefined();
    if (!sector) return;
    const content = sector.content.w * sector.content.h;
    const cap = sector.cap;
    expect(content).toBeGreaterThan(0);

    for (const tile of sector.tiles) {
      const expected = tile.cap / cap;
      const actual = area(tile) / content;
      // A tile whose own area rounds below a pixel is filtered, not stretched;
      // everything emitted carries the same area-per-cap ratio.
      expect(Math.abs(actual - expected) / expected, `${tile.symbol} area`).toBeLessThan(0.01);
    }
  });

  it("contains every tile: root ⊃ rect ⊃ block ⊃ content ⊃ tile", () => {
    for (const sector of layout.sectors) {
      expectInside(sector.rect, layout.root, `${sector.id} rect`);
      expectInside(sector.block, sector.rect, `${sector.id} block`);
      expectInside(sector.content, sector.block, `${sector.id} content`);
      for (const tile of sector.tiles) expectInside(tile, sector.content, `${sector.id}/${tile.symbol}`);
    }
  });

  it("overlaps nothing", () => {
    expectDisjoint(layout.sectors.map((sector) => sector.rect), "sectors");
    for (const sector of layout.sectors) expectDisjoint(sector.tiles, `tiles in ${sector.id}`);
  });

  it("emits no tile smaller than a pixel", () => {
    for (const tile of layout.tiles) {
      expect(tile.w).toBeGreaterThanOrEqual(1);
      expect(tile.h).toBeGreaterThanOrEqual(1);
    }
  });

  it("is deterministic", () => {
    const again = layoutMap(WIDE_MAP, 1280, 800);
    expect(JSON.stringify(again)).toBe(JSON.stringify(layout));
  });

  it("survives a zero-size box without drawing or throwing", () => {
    const zero = layoutMap(WIDE_MAP, 0, 0);
    expect(zero.drawn).toBe(0);
    expect(zero.omitted).toBe(5576);
    expect(zero.sectors.every((sector) => sector.rect.w === 0 && sector.rect.h === 0)).toBe(true);
  });
});

describe("layoutMap — adversarial: one name owns the sector", () => {
  const layout = layoutMap(GIANT_MAP, 900, 600);
  const mega = layout.sectors.find((sector) => sector.id === "mega");

  it("keeps every drawn tile at least 1 px", () => {
    expect(layout.tiles.length).toBeGreaterThan(0);
    for (const tile of layout.tiles) {
      expect(tile.w).toBeGreaterThanOrEqual(1);
      expect(tile.h).toBeGreaterThanOrEqual(1);
    }
  });

  it("reports the slivers it refused to draw instead of painting them", () => {
    expect(mega).toBeDefined();
    if (!mega) return;
    // 400 names share 0.06 % of the sector: at 900×600 they are a third of a
    // pixel each. They must be counted, not squeezed into zero-height rows.
    expect(mega.omitted).toBeGreaterThan(300);
    expect(mega.count).toBe(401);
    expect(mega.tiles.length + mega.omitted).toBe(401);
    expect(layout.omitted).toBe(mega.omitted);
  });

  it("still gives the giant name its cap share", () => {
    if (!mega) return;
    const giant = mega.tiles.find((tile) => tile.symbol === "GIANT");
    expect(giant).toBeDefined();
    if (!giant) return;
    const content = mega.content.w * mega.content.h;
    const expected = 9000 / (9000 + 400 * 0.08);
    expect(Math.abs(area(giant) / content - expected)).toBeLessThan(0.01);
  });

  it("does not starve the neighbouring sector", () => {
    const small = layout.sectors.find((sector) => sector.id === "small");
    expect(small).toBeDefined();
    if (!small) return;
    expect(small.rect.w).toBeGreaterThan(2);
    expect(small.rect.h).toBeGreaterThan(2);
    expect(small.tiles).toHaveLength(3);
  });
});

describe("hitTest", () => {
  const layout = layoutMap(SMALL_MAP, 900, 600);

  it("finds the tile under a point, by arithmetic", () => {
    const sector = layout.sectors[0] as (typeof layout.sectors)[number];
    const tile = sector.tiles[0] as (typeof sector.tiles)[number];
    const hit = hitTest(layout, tile.x + tile.w / 2, tile.y + tile.h / 2);
    expect(hit?.sector.id).toBe(sector.id);
    expect(hit?.tile?.symbol).toBe(tile.symbol);
  });

  it("returns the sector for a point in the label band, with no tile", () => {
    const sector = layout.sectors.find((candidate) => candidate.header !== null);
    expect(sector).toBeDefined();
    if (!sector || !sector.header) return;
    const hit = hitTest(layout, sector.header.x + 2, sector.header.y + sector.header.h / 2);
    expect(hit?.sector.id).toBe(sector.id);
    expect(hit?.tile).toBeNull();
  });

  it("returns null outside the canvas", () => {
    expect(hitTest(layout, -1, -1)).toBeNull();
    expect(hitTest(layout, 5000, 5000)).toBeNull();
  });
});

describe("nextTile", () => {
  const layout = layoutMap(SMALL_MAP, 900, 600);

  it("starts at the first tile when nothing is selected", () => {
    expect(nextTile(layout, -1, "right")).toBe(0);
  });

  it("moves right to a tile that is actually to the right", () => {
    const from = layout.tiles[0] as (typeof layout.tiles)[number];
    const index = nextTile(layout, 0, "right");
    expect(index).toBeGreaterThan(0);
    const to = layout.tiles[index] as (typeof layout.tiles)[number];
    expect(to.x + to.w / 2).toBeGreaterThan(from.x + from.w / 2);
    expect(to.symbol).not.toBe(from.symbol);
  });

  it("stays put when there is nothing in that direction", () => {
    const last = layout.tiles.length - 1;
    expect(nextTile(layout, last, "right")).toBe(last);
    // The topmost tile (smallest centre y) has nothing above it.
    const top = layout.tiles.reduce(
      (best, tile, index) =>
        tile.y + tile.h / 2 < (layout.tiles[best] as (typeof layout.tiles)[number]).y + (layout.tiles[best] as (typeof layout.tiles)[number]).h / 2
          ? index
          : best,
      0,
    );
    expect(nextTile(layout, top, "up")).toBe(top);
  });
});
