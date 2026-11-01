/**
 * The column presets: eighteen names, nine served views, and one honest
 * fallback when the catalogue cannot back a tab.
 *
 * The invariant worth pinning is the first one: every view the schema serves is
 * reachable from the bar. The tab bar is the only view switcher on this screen,
 * so a view with no tab is a view a reader cannot open.
 */

import { describe, expect, it } from "vitest";

import { VIEW_FIXTURE } from "@/grid/__fixtures__/views";

import {
  COLUMN_PRESETS,
  activePresetId,
  resolvePreset,
  resolvePresets,
} from "./columnPresets";

/** Every key the served catalogue carries, plus the ones only the wide view has. */
const CATALOGUE = [
  ...new Set([
    ...VIEW_FIXTURE.flatMap((view) => view.columns),
    "ticker",
    "news_time",
    "news_title",
    "news_url",
    "daily_digest",
  ]),
];

describe("the column presets", () => {
  it("is eighteen distinct names, in the product's own vocabulary", () => {
    expect(COLUMN_PRESETS).toHaveLength(18);
    expect(new Set(COLUMN_PRESETS.map((preset) => preset.id)).size).toBe(18);
    expect(new Set(COLUMN_PRESETS.map((preset) => preset.label)).size).toBe(18);
    expect(COLUMN_PRESETS.map((preset) => preset.label)).toEqual([
      "Summary",
      "Valuation",
      "Financials",
      "Growth",
      "Ownership",
      "Momentum",
      "Technical",
      "Dividends",
      "Basics",
      "Custom",
      "Charts",
      "Tickers",
      "TA",
      "News",
      "Snapshot",
      "Stats",
      "Insiders",
      "Liquidity",
    ]);
  });

  it("names no vendor, in a label, a job or a note", () => {
    const text = COLUMN_PRESETS.map((preset) => `${preset.label} ${preset.job}`).join(" ");
    for (const vendor of ["finviz", "tradingview", "yahoo", "bloomberg"]) {
      expect(text.toLowerCase()).not.toContain(vendor);
    }
  });

  it("reaches every view the schema serves", () => {
    const resolved = resolvePresets(VIEW_FIXTURE, CATALOGUE);
    const reachable = new Set(
      resolved.map((entry) => entry.viewName).filter((name): name is string => name !== null),
    );
    for (const view of VIEW_FIXTURE) {
      expect(reachable.has(view.name), `${view.name} has no tab`).toBe(true);
    }
  });

  it("takes a view's own columns, and says which view", () => {
    const entry = resolvePreset(
      COLUMN_PRESETS.find((preset) => preset.id === "valuation")!,
      VIEW_FIXTURE,
      CATALOGUE,
    );
    expect(entry.source).toBe("view");
    expect(entry.viewName).toBe("value");
    expect(entry.columns).toEqual(VIEW_FIXTURE.find((view) => view.name === "value")!.columns);
    expect(entry.note).toContain("13 columns");
    expect(entry.missing).toEqual([]);
  });

  it("keeps only the columns this build serves, and reports the rest", () => {
    const entry = resolvePreset(
      COLUMN_PRESETS.find((preset) => preset.id === "news")!,
      VIEW_FIXTURE,
      ["news_title", "ticker"],
    );
    expect(entry.source).toBe("catalogue");
    expect(entry.viewName).toBeNull();
    expect(entry.columns).toEqual(["news_title", "ticker"]);
    expect(entry.missing).toEqual(["news_time", "news_url", "daily_digest", "company"]);
    expect(entry.note).toMatch(/no view of its own yet/i);
    expect(entry.description).toMatch(/no view of its own yet/);
  });

  it("falls back to the default columns, and says so, when a view is withdrawn", () => {
    // A preset that names a view, against a catalogue that no longer serves it.
    const geared = { id: "gone", label: "Gone", view: "retired-view", job: "A tab whose view left." };
    const entry = resolvePreset(geared, VIEW_FIXTURE, CATALOGUE);
    expect(entry.source).toBe("default");
    expect(entry.viewName).toBeNull();
    expect(entry.columns).toBeNull();
    expect(entry.note).toContain("default columns stay on screen");
    expect(entry.description).toBe("default columns, no view yet");
  });

  it("stands for the reader's own columns without pretending to be a preset", () => {
    const entry = resolvePreset(
      COLUMN_PRESETS.find((preset) => preset.id === "custom")!,
      VIEW_FIXTURE,
      CATALOGUE,
    );
    expect(entry.source).toBe("custom");
    expect(entry.columns).toBeNull();
    expect(entry.description).toBe("your own columns");
  });

  it("derives the active tab from the columns on screen, never from a separate state", () => {
    const resolved = resolvePresets(VIEW_FIXTURE, CATALOGUE);
    const today = VIEW_FIXTURE[0]!.columns;
    const value = VIEW_FIXTURE.find((view) => view.name === "value")!.columns;
    const news = resolved.find((entry) => entry.preset.id === "news")!.columns!;

    expect(activePresetId(resolved, "today", today)).toBe("summary");
    expect(activePresetId(resolved, "value", value)).toBe("valuation");
    // A catalogue preset has no view, so the comparison is columns-only.
    expect(activePresetId(resolved, "value", news)).toBe("news");
    // Columns no preset claims: the bar says Custom rather than lying.
    expect(activePresetId(resolved, "value", ["price"])).toBe("custom");
  });
});
