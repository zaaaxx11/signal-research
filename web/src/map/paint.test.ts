import { describe, expect, it } from "vitest";

import { fakeContext } from "./__fixtures__/canvas";
import { SMALL_MAP, WIDE_MAP } from "./__fixtures__/payload";
import { layoutMap } from "./layout";
import { paintMap } from "./paint";
import { prepareTheme, rampFill, type MapTheme } from "./theme";

/**
 * What the painter puts on the canvas, counted. The real frame time is measured
 * in Chromium against the built bundle (see the proof), because jsdom's fake
 * context cannot answer "how long did this take" — but it can answer "how many
 * rects, how many in one pass, and is the context scaled".
 */

const THEME: MapTheme = {
  surface: "#181215",
  raised: "#1f181c",
  border: "#332a2f",
  fg: "#efe9ec",
  muted: "#b9afb4",
  ink: "#1f0a14",
  up: "#3ddc97",
  down: "#ff5f4d",
  flat: "#a6a2a4",
  focus: "#ff8ec4",
};

const PALETTE = prepareTheme(THEME);

describe("paintMap", () => {
  it("draws one rect per tile, plus one plane per sector and the background", () => {
    const layout = layoutMap(SMALL_MAP, 900, 600);
    const fake = fakeContext();
    const stats = paintMap(fake.ctx, layout, PALETTE, { dpr: 1, width: 900, height: 600 });
    const pass = fake.passes().at(-1);

    expect(stats.tiles).toBe(layout.drawn);
    // Background + one label band per labelled sector + one rect per tile.
    const bands = layout.sectors.filter((sector) => sector.header !== null).length;
    expect(pass?.fillRects).toHaveLength(1 + bands + layout.drawn);
  });

  it("scales the context, not the box (adversarial b)", () => {
    const layout = layoutMap(SMALL_MAP, 900, 600);
    const fake = fakeContext();
    paintMap(fake.ctx, layout, PALETTE, { dpr: 2, width: 900, height: 600 });
    expect(fake.passes().at(-1)?.transform).toEqual([2, 0, 0, 2, 0, 0]);
  });

  it("draws symbol text only where the tile can hold it, and only when it is readable", () => {
    const wide = layoutMap(SMALL_MAP, 900, 600);
    const big = fakeContext();
    const bigStats = paintMap(big.ctx, wide, PALETTE, { dpr: 1, width: 900, height: 600 });
    expect(bigStats.symbols).toBeGreaterThan(0);
    expect(bigStats.symbols).toBeLessThanOrEqual(bigStats.tiles);

    // Every symbol painted sits on a fill whose best ink clears 4.5:1.
    const symbols = new Set(wide.tiles.map((tile) => tile.symbol));
    const painted = big.passes().at(-1)?.texts.filter((text) => symbols.has(text)) ?? [];
    expect(painted.length).toBe(bigStats.symbols);
    for (const text of painted) {
      const tile = wide.tiles.find((candidate) => candidate.symbol === text);
      expect(tile).toBeDefined();
      if (!tile) continue;
      expect(rampFill(tile.change, PALETTE).contrast).toBeGreaterThanOrEqual(4.5);
    }

    // 40×30: every tile is a few pixels across, so no symbol and no sector
    // label may be painted — text at a size the rect cannot contain is a smear.
    const small = layoutMap(SMALL_MAP, 40, 30);
    const tiny = fakeContext();
    const tinyStats = paintMap(tiny.ctx, small, PALETTE, { dpr: 1, width: 40, height: 30 });
    expect(tinyStats.tiles).toBeGreaterThan(0);
    expect(tinyStats.symbols).toBe(0);
    expect(tinyStats.labels).toBe(0);
  });

  it("rings the selected tile", () => {
    const layout = layoutMap(SMALL_MAP, 900, 600);
    const fake = fakeContext();
    const symbol = layout.tiles[0]?.symbol ?? "";
    paintMap(fake.ctx, layout, PALETTE, { dpr: 1, width: 900, height: 600, selected: symbol });
    // Sector frames (3) + one selection ring.
    expect(fake.passes().at(-1)?.strokes.length).toBe(layout.sectors.length + 1);
  });

  it("paints the whole 5,576-tile market in a single pass", () => {
    const layout = layoutMap(WIDE_MAP, 1280, 800);
    const fake = fakeContext();
    const stats = paintMap(fake.ctx, layout, PALETTE, { dpr: 1, width: 1280, height: 800 });
    const passes = fake.passes();
    expect(passes).toHaveLength(1);
    expect(stats.tiles).toBe(5576);
    expect(passes[0]?.fillRects).toHaveLength(1 + 11 + 5576);
  });
});
