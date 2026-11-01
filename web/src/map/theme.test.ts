import { describe, expect, it } from "vitest";

import {
  contrastRatio,
  cssRgb,
  inkFor,
  mergeTheme,
  parseColour,
  prepareTheme,
  rampColor,
  rampFill,
  rampRgb,
  readTheme,
  swatch,
  type MapTheme,
} from "./theme";

/**
 * The ramp, on the actual token values. The point of these tests is that the
 * painter's colour decisions are arithmetic over `tokens.css` — not hexes
 * buried in a component — so the assertions are about *relationships*
 * (midpoint is the plane, extremes are the up/down tokens, ink flips where the
 * fill gets light) rather than about a particular picture.
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

describe("parseColour", () => {
  it("reads the three shapes a token can arrive in", () => {
    expect(parseColour("#3ddc97")).toEqual({ r: 0x3d, g: 0xdc, b: 0x97 });
    expect(parseColour("#3d9")).toEqual({ r: 0x33, g: 0xdd, b: 0x99 });
    expect(parseColour("rgb(61, 220, 151)")).toEqual({ r: 61, g: 220, b: 151 });
    expect(parseColour("rgba(61,220,151,0.4)")).toEqual({ r: 61, g: 220, b: 151 });
  });

  it("returns null rather than a guess", () => {
    expect(parseColour("")).toBeNull();
    expect(parseColour("mint")).toBeNull();
    expect(parseColour("#12345")).toBeNull();
  });
});

describe("ramp", () => {
  it("is the plane at zero, the direction at the clamp, and between in between", () => {
    expect(rampRgb(0, THEME)).toEqual(parseColour(THEME.raised));
    expect(rampRgb(3, THEME)).toEqual(parseColour(THEME.up));
    expect(rampRgb(-3, THEME)).toEqual(parseColour(THEME.down));

    // The clamp: a 12 % day is the strongest green, not a colour outside the set.
    expect(rampRgb(12, THEME)).toEqual(parseColour(THEME.up));
    expect(rampRgb(-40, THEME)).toEqual(parseColour(THEME.down));

    const half = rampRgb(1.5, THEME);
    const up = parseColour(THEME.up)!;
    expect(half.g).toBeGreaterThan(parseColour(THEME.raised)!.g);
    expect(half.g).toBeLessThan(up.g);
  });

  it("paints a canvas-ready string", () => {
    expect(rampColor(3, THEME)).toBe("rgb(61, 220, 151)");
    expect(cssRgb(parseColour("#000")!)).toBe("rgb(0, 0, 0)");
  });

  it("uses the flat token for an absent sector value, not for a flat stock", () => {
    // A sector with no measured average is grey (unknown); a 0.00 % stock is
    // the plane (measured, unchanged). Two different facts, two colours.
    expect(swatch(null, THEME)).toBe(THEME.flat);
    expect(swatch(0, THEME)).toBe(rampColor(0, THEME));
    expect(swatch(0, THEME)).not.toBe(swatch(null, THEME));
  });
});

describe("ink", () => {
  it("flips to the dark token on a light fill", () => {
    const up = parseColour(THEME.up)!;
    expect(inkFor(up, THEME)).toBe(THEME.ink);
    const plane = parseColour(THEME.raised)!;
    expect(inkFor(plane, THEME)).toBe(THEME.fg);
  });

  it("chooses the higher-contrast ink at every point, and knows when neither is enough", () => {
    // The ramp crosses a mid red where neither declared ink reaches 4.5:1. The
    // rule is therefore "always the better of the two", and the painter's rule
    // is "no text where the better one is still short" — see paint.test.ts.
    const light = parseColour(THEME.fg)!;
    const dark = parseColour(THEME.ink)!;
    let worst = Infinity;
    for (const change of [-10, -4, -2, -0.5, 0, 0.5, 2, 4, 10]) {
      const fill = rampRgb(change, THEME);
      const chosen = parseColour(inkFor(fill, THEME))!;
      const expected = Math.max(contrastRatio(fill, light), contrastRatio(fill, dark));
      expect(contrastRatio(fill, chosen), `change ${change}`).toBeCloseTo(expected, 6);
      worst = Math.min(worst, expected);
    }
    expect(worst).toBeGreaterThan(4.4);
  });

  it("scores black on white at 21:1", () => {
    expect(contrastRatio({ r: 0, g: 0, b: 0 }, { r: 255, g: 255, b: 255 })).toBeCloseTo(21, 1);
  });
});

describe("prepareTheme", () => {
  it("parses the palette once and keeps the painter's ramp identical", () => {
    const palette = prepareTheme(THEME);
    expect(palette.neutral).toEqual(parseColour(THEME.raised));
    for (const change of [-5, -1, 0, 1, 5]) {
      const fast = rampFill(change, palette);
      expect(fast.fill).toBe(rampColor(change, THEME));
      expect(fast.ink).toBe(inkFor(fast.rgb, THEME));
    }
  });
});

describe("readTheme / mergeTheme", () => {
  it("reads a theme object even where the stylesheet cannot resolve", () => {
    // jsdom does not resolve custom properties; the reader must still answer a
    // complete object rather than throw or return undefined slots.
    const theme = readTheme();
    expect(Object.keys(theme).sort()).toEqual(
      ["border", "down", "fg", "flat", "focus", "ink", "muted", "raised", "surface", "up"].sort(),
    );
  });

  it("keeps the caller's colour for a slot the document cannot resolve", () => {
    const empty = readTheme();
    expect(mergeTheme(THEME, empty)).toBe(THEME);
    expect(mergeTheme(THEME, { ...empty, up: "#00ff00" })).toEqual({ ...THEME, up: "#00ff00" });
  });
});
