import { describe, expect, it } from "vitest";

import { VIEW_FIXTURE } from "./__fixtures__/views";
import { COLUMN_META, columnSpec, headerNeed, labelFor } from "./columnMeta";

/**
 * Adversarial (a): a header must never ellipsise into a different meaning.
 *
 * The rule is enforced as data, not as hope: any full label wider than its own
 * minimum column is required to declare a short form, and `labelFor` switches
 * to it before CSS can cut the label.
 */

describe("column metadata", () => {
  it("gives every column of the nine views a real spec", () => {
    for (const view of VIEW_FIXTURE) {
      for (const key of view.columns) {
        expect(COLUMN_META[key], `${view.name}.${key} has no label/kind/weight`).toBeDefined();
      }
    }
  });

  it("declares a short label wherever the full label cannot fit the floor", () => {
    for (const [key, meta] of Object.entries(COLUMN_META)) {
      const spec = { key, ...meta };
      if (headerNeed(spec) > spec.min) {
        expect(spec.short, `${key} (${spec.label}) is wider than its ${spec.min}px floor`).toBeTruthy();
        expect(spec.short!.length).toBeLessThan(spec.label.length);
      }
    }
  });

  it("uses the short label under the threshold and the full label above it", () => {
    const spec = columnSpec("enterprise_value");
    const need = headerNeed(spec);

    const narrow = labelFor(spec, need - 1);
    expect(narrow.text).toBe("EV");
    expect(narrow.short).toBe(true);

    const wide = labelFor(spec, need);
    expect(wide.text).toBe("Enterprise Value");
    expect(wide.short).toBe(false);
  });

  it("never silently truncates a label that declares no short form", () => {
    const spec = columnSpec("price");
    expect(labelFor(spec, 10)).toEqual({ text: "Price", short: false });
  });

  it("falls back to a humanised label for a key this build has not seen", () => {
    const spec = columnSpec("some_new_metric");
    expect(spec.label).toBe("Some new metric");
    expect(spec.kind).toBe("text");
    expect(spec.min).toBeGreaterThan(0);
    expect(labelFor(spec, 120).text).toBe("Some new metric");
  });

  it("every label fits its floor in its chosen form, exactly", () => {
    for (const [key, meta] of Object.entries(COLUMN_META)) {
      const spec = { key, ...meta };
      const chosen = labelFor(spec, spec.min);
      const need = headerNeed({ ...spec, label: chosen.text });
      // No tolerance: the floor is the promise, and a label 2 px wider than it
      // is an ellipsis in the browser (`Short Int` measured 74 px in 72).
      expect(need, `${key}: "${chosen.text}" overflows ${spec.min}px`).toBeLessThanOrEqual(spec.min);
    }
  });

  it("leaves room inside a money cell for the number, not only for its padding", () => {
    // `$823,210.89` is 76 px of text at 12 px, and a cell spends 8 px of padding
    // on each side: a floor below 92 prints `$823,21…` — a 1000× misread (F6).
    for (const key of ["price", "target_price", "prev_close"]) {
      expect(columnSpec(key).min, `${key} cannot hold a seven-figure price`).toBeGreaterThanOrEqual(92);
    }
  });

  it("gives the exchange column room for the longest venue name it prints", () => {
    // 84 px − 16 px padding = 68 px ≈ 10 characters at 12 px; `NYSE American`
    // is 13. The label is display-only, so the *width* is the only thing that
    // can cut it — and a cut label plus a code in the title is two reads for
    // one fact.
    const spec = columnSpec("exchange");
    expect(spec.min).toBe(104);
    expect(typeof spec.display).toBe("function");
    expect(spec.display?.("XNAS")).toBe("NASDAQ");
  });
});
