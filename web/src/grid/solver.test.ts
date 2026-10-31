import { describe, expect, it } from "vitest";

import { VIEW_FIXTURE } from "./__fixtures__/views";
import { IDENTITY_WIDTH, columnSpec, headerNeed, labelFor } from "./columnMeta";
import { solverColumns, solveView, solveWidths, type SolverColumn } from "./solver";

/**
 * The solver's contract is arithmetic, so the tests are arithmetic:
 *
 *   sum(widths) === floor(container)      — wherever the floor fits
 *   sum(widths) === floor, overflow: true — wherever it does not
 *
 * at 1280, 1440 and 1920 for every one of the nine views, plus a container
 * that does not divide evenly (1287) so the remainder path is exercised rather
 * than assumed.
 *
 * F2/F4 are the other half of the contract: a container the design never
 * expected may be *too narrow*, and the answer to that is a floor plus a scroll
 * — never a negative allocation, never a throw, never a table that silently
 * claims to be narrower than it is.
 */

const BREAKPOINTS = [1280, 1440, 1920] as const;

describe("solveWidths", () => {
  it.each(VIEW_FIXTURE.map((view) => [view.name, view.columns] as const))(
    "%s sums exactly to the container at every breakpoint",
    (_name, columns) => {
      for (const container of BREAKPOINTS) {
        const { widths, total, container: target, overflow } = solveView(columns, container);
        expect(total).toBe(container);
        expect(target).toBe(container);
        expect(overflow).toBe(false);
        expect(widths.reduce((a, b) => a + b, 0)).toBe(container);
        expect(widths.every((width) => Number.isInteger(width))).toBe(true);
        expect(widths.every((width) => width > 0)).toBe(true);
      }
    },
  );

  it.each(VIEW_FIXTURE.map((view) => [view.name, view.columns] as const))(
    "%s keeps every column at or above its minimum width at 1280",
    (_name, columns) => {
      const { columns: solved, widths } = solveView(columns, 1280);
      solved.forEach((column, index) => {
        // The identity lane is fixed; the rest may only grow at this size.
        expect(widths[index]).toBeGreaterThanOrEqual(column.fixed ?? column.min);
      });
    },
  );

  it("pins the identity lane at 101 px and never scales it", () => {
    for (const container of [1024, 1280, 1440, 1920]) {
      const { widths } = solveView(VIEW_FIXTURE[0]?.columns ?? [], container);
      expect(widths[0]).toBe(IDENTITY_WIDTH);
    }
  });

  it("sums exactly when the container does not divide evenly", () => {
    // 1287 is 1280 + 7; the solver must place all seven leftover pixels, not
    // truncate them into a 1 px gap that would become a scrollbar.
    for (const view of VIEW_FIXTURE) {
      const { widths, total } = solveView(view.columns, 1287);
      expect(total).toBe(1287);
      expect(widths.reduce((a, b) => a + b, 0)).toBe(1287);
    }
  });

  it("sends the remainder to the widest text column", () => {
    const columns: SolverColumn[] = [
      { key: "identity", weight: 0, min: 100, fixed: 100 },
      { key: "company", weight: 1, min: 10, kind: "text" },
      { key: "sector", weight: 1, min: 12, kind: "text" },
      { key: "price", weight: 1, min: 10, kind: "money" },
      { key: "volume", weight: 1, min: 10, kind: "integer" },
    ];
    // 100 fixed + 10 + 12 + 10 + 10 minimums = 142; 147 leaves 5 px of slack,
    // which divides as 1 px each plus a 1 px remainder. That remainder goes to
    // sector, the widest text floor — not to the last column by accident.
    const { widths } = solveWidths(columns, 147);
    expect(widths[0]).toBe(100);
    expect(widths[2]).toBe(14);
    expect(widths[1]).toBe(11);
    expect(widths[3]).toBe(11);
    expect(widths[4]).toBe(11);
    expect(widths.reduce((a, b) => a + b, 0)).toBe(147);
  });

  it("floors a fractional container so the table can never exceed it", () => {
    const { total, overflow } = solveView(VIEW_FIXTURE[0]?.columns ?? [], 1279.6);
    expect(overflow).toBe(false);
    expect(total).toBe(1279);
    expect(total).toBeLessThanOrEqual(1279.6);
  });

  it("never allocates less than a column's minimum unless the container forces it", () => {
    const columns = solverColumns(VIEW_FIXTURE[2]?.columns ?? []);
    const { widths } = solveWidths(columns, 1920);
    widths.forEach((width, index) => {
      const column = columns[index];
      if (!column) return;
      expect(width).toBeGreaterThanOrEqual(column.min);
    });
  });
});

/**
 * F2 — a container of 102–113 px used to build `1 + allocate(negative)` and
 * throw `widths sum to 112, container is 110` *inside render*, which unmounted
 * the whole SPA. F4 — a container at or below 101 px used to return a 101 px
 * table while claiming the invariant held.
 *
 * The resolution is one regime, not a throw and not a lie: below the sum of the
 * minimums the solver returns the floor widths and says `overflow: true`, so
 * the caller scrolls instead of compressing.
 */
describe("below the floor (F2, F4)", () => {
  it("reproduces the auditor's input: 105 px is a floor, not a throw", () => {
    const columns = VIEW_FIXTURE[0]?.columns ?? [];
    const solved = solveView(columns, 105);

    expect(solved.overflow).toBe(true);
    expect(solved.total).toBe(solved.floor);
    expect(solved.total).toBeGreaterThan(105);
    // Every column is at its declared floor: nothing was compressed to fit.
    solved.columns.forEach((column, index) => {
      expect(solved.widths[index]).toBe(column.fixed ?? column.min);
    });
  });

  it("returns the 101 px lane instead of pretending a 1 px container holds it", () => {
    const solved = solveView(VIEW_FIXTURE[0]?.columns ?? [], 1);
    expect(solved.widths[0]).toBe(IDENTITY_WIDTH);
    expect(solved.total).toBe(solved.floor);
    expect(solved.overflow).toBe(true);
  });

  it("never throws for any container from 0 to 4000", () => {
    // 1,000 widths, fractional ones included: the range a resize, a zoom or an
    // embedded pane can produce, far past the breakpoints the design names.
    for (let step = 0; step < 1000; step += 1) {
      const container = (step * 4000) / 999;
      for (const view of VIEW_FIXTURE) {
        expect(() => solveView(view.columns, container), `${view.name} @ ${container}`).not.toThrow();
      }
    }
  });

  it("keeps sum(widths) === total and total >= container for every width", () => {
    for (const view of VIEW_FIXTURE.slice(0, 3)) {
      for (let container = 0; container <= 4000; container += 7) {
        const solved = solveView(view.columns, container);
        const sum = solved.widths.reduce((a, b) => a + b, 0);
        expect(sum).toBe(solved.total);
        expect(solved.widths.every((width) => Number.isInteger(width) && width >= 1)).toBe(true);
        if (solved.overflow) {
          // Below the floor the table is exactly its floor: it scrolls.
          expect(solved.total).toBe(solved.floor);
          expect(solved.total).toBeGreaterThan(container);
        } else {
          // At or above the floor the table is the container, and the floor
          // is the narrowest it would have been allowed to be.
          expect(solved.total).toBe(container);
          expect(solved.floor).toBeLessThanOrEqual(container);
        }
      }
    }
  });

  it("names the floor exactly, and the container it could not fit", () => {
    for (const container of [0, 1, 50, 101, 102, 105, 110, 112, 113, 240, 431, 926, 927]) {
      const solved = solveView(VIEW_FIXTURE[1]?.columns ?? [], container);
      expect(solved.container).toBe(Math.max(0, Math.floor(container)));
      expect(solved.floor).toBe(
        solved.columns.reduce((total, column) => total + (column.fixed ?? column.min), 0),
      );
    }
  });

  it("handles a zero and a negative container without throwing", () => {
    for (const container of [0, -1, -1000]) {
      const solved = solveView(VIEW_FIXTURE[0]?.columns ?? [], container);
      expect(solved.overflow).toBe(true);
      expect(solved.container).toBe(0);
      expect(solved.total).toBe(solved.floor);
    }
  });
});

/** Adversarial (c): the overflow flag must not make every view scroll at 1280. */
describe("at 1280 nothing scrolls", () => {
  it.each(VIEW_FIXTURE.map((view) => [view.name, view.columns] as const))(
    "%s fits 1280 without overflow",
    (_name, columns) => {
      const solved = solveView(columns, 1280);
      expect(solved.overflow).toBe(false);
      expect(solved.total).toBe(1280);
    },
  );
});

/**
 * F6/F11 — a column never receives less than the width it declares, and a
 * header is never given less than the width its chosen label needs. The
 * clipping at 1024 px was the same bug as the compression: solve below the
 * floor and the numbers (and then the labels) get cut.
 */
describe("headers keep their declared floor (F11)", () => {
  it("never gives a label less width than the label needs, at any container", () => {
    const containers = [0, 102, 240, 431, 927, 983, 1024, 1100, 1239, 1280, 1920, 4000];
    for (const view of VIEW_FIXTURE) {
      for (const container of containers) {
        const { columns, widths } = solveView(view.columns, container);
        columns.forEach((column, index) => {
          const width = widths[index] ?? 0;
          const spec = columnSpec(column.key);
          const chosen = labelFor(spec, width);
          const need = headerNeed({ ...spec, label: chosen.text });
          expect(need, `${view.name}.${column.key} at ${container}px`).toBeLessThanOrEqual(width);
        });
      }
    }
  });
});

describe("the width table", () => {
  it("is reproducible and printable", () => {
    for (const view of VIEW_FIXTURE) {
      for (const container of BREAKPOINTS) {
        const { columns: solved, widths, total } = solveView(view.columns, container);
        const row = solved
          .map((column, index) => `${column.key}=${widths[index]}`)
          .join(" ");
        expect(row.length).toBeGreaterThan(0);
        expect(widths.reduce((a, b) => a + b, 0)).toBe(container);
        expect(total).toBe(container);
      }
    }
    // The registry covers every column the nine views ask for.
    for (const view of VIEW_FIXTURE) {
      for (const key of view.columns) {
        const spec = columnSpec(key);
        expect(spec.label.length).toBeGreaterThan(0);
        expect(spec.min).toBeGreaterThan(0);
      }
    }
  });
});
