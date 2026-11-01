import { describe, expect, it } from "vitest";

import { mergeChartTheme, readChartTheme, type ChartTheme } from "./theme";

/**
 * The palette arrives from `tokens.css`; jsdom cannot resolve custom
 * properties, so the reader must answer a complete object and the merge must
 * keep the caller's colours rather than painting with `undefined`.
 */

const THEME: ChartTheme = {
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
  accent: "#ff8ec4",
  subtle: "#9d9398",
};

describe("readChartTheme / mergeChartTheme", () => {
  it("answers every slot even where the stylesheet cannot resolve", () => {
    const theme = readChartTheme();
    expect(Object.keys(theme).sort()).toEqual(
      [
        "accent",
        "border",
        "down",
        "fg",
        "flat",
        "focus",
        "ink",
        "muted",
        "raised",
        "subtle",
        "surface",
        "up",
      ].sort(),
    );
    for (const value of Object.values(theme)) expect(typeof value).toBe("string");
  });

  it("keeps the caller's colour when the document resolves nothing", () => {
    expect(mergeChartTheme(THEME, readChartTheme())).toBe(THEME);
  });

  it("adopts a resolved slot, including the chart-only ones", () => {
    const resolved = { ...readChartTheme(), accent: "#123456", subtle: "#654321" };
    const merged = mergeChartTheme(THEME, resolved);
    expect(merged.accent).toBe("#123456");
    expect(merged.subtle).toBe("#654321");
    expect(merged.up).toBe(THEME.up);
  });
});
