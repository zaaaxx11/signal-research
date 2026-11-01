/**
 * The column solver: integer widths that sum to the container, exactly.
 *
 * `table-layout: fixed` means the `<colgroup>` is the truth — the header and
 * the body cannot disagree because neither one measures anything. This module
 * turns a view's weight vector into that colgroup.
 *
 * The contract is arithmetic, not visual:
 *
 *   sum(widths) === floor(container)        when the declared minimums fit
 *   sum(widths) === floor, overflow: true   when they do not
 *
 * at every breakpoint, including a container that does not divide evenly. The
 * remainder is not sprinkled across columns (which would make the header
 * shimmer at every resize); it goes to the widest text column, the one with
 * the most to gain from a few pixels and the least risk of clipping a number.
 *
 * **Below the floor, the answer is the floor.** A container narrower than the
 * sum of the columns' minimums used to be compressed proportionally, which cut
 * numbers (`$823,210.89` printed as `$823,21…`, a 1000× misread). It now
 * returns the declared minimums unchanged and reports `overflow: true`, so the
 * panel scrolls horizontally instead of lying about its cell widths. And this
 * module is arithmetic: it never throws on any container it is handed, because
 * a throw here happens inside `render` and takes the whole SPA down with it.
 */

import { IDENTITY_WIDTH, ROW_NUMBER_KEY, columnSpec, type ColumnSpec } from "./columnMeta";

export interface SolverColumn {
  key: string;
  weight: number;
  min: number;
  /** A column the solver may not resize (the identity lane). */
  fixed?: number;
  kind?: ColumnSpec["kind"];
}

/** One solve: the widths, the width they sum to, and whether they fit. */
export interface WidthSolution {
  widths: number[];
  /** `sum(widths)` — the table's width in integer pixels. */
  total: number;
  /** `floor(container)` — the width the table was solved for. */
  container: number;
  /** The declared floor: fixed lanes + every flexible column at its `min`. */
  floor: number;
  /** True when `total > container`: the panel must scroll, never compress. */
  overflow: boolean;
}

export interface SolvedColumns extends WidthSolution {
  columns: SolverColumn[];
}

function sum(values: readonly number[]): number {
  return values.reduce((total, value) => total + value, 0);
}

/**
 * Pick the column that absorbs the integer remainder: the widest text column,
 * or — in a view with no text (today, value, …) — the widest allocated column.
 * Deterministic: equal candidates resolve to the earlier column.
 */
function absorbIndex(columns: readonly SolverColumn[], widths: readonly number[]): number {
  let best = -1;
  let bestWidth = -1;
  for (let index = 0; index < columns.length; index += 1) {
    if (columns[index]?.kind !== "text") continue;
    const width = widths[index] ?? 0;
    if (width > bestWidth) {
      best = index;
      bestWidth = width;
    }
  }
  if (best >= 0) return best;

  best = 0;
  bestWidth = -1;
  for (let index = 0; index < widths.length; index += 1) {
    if ((widths[index] ?? 0) > bestWidth) {
      best = index;
      bestWidth = widths[index] ?? 0;
    }
  }
  return best;
}

/**
 * Solve `columns` into integer pixel widths for `container`.
 *
 * `container` is floored first: a measured 1279.6 px container is a 1279 px
 * table, which is the only way `sum(widths) ≤ container` can hold in a browser
 * that reports fractional layout boxes. A container this module cannot believe
 * (zero, negative, non-finite) is treated as zero and answered with the floor —
 * never with an exception.
 */
export function solveWidths(
  columns: readonly SolverColumn[],
  container: number,
): WidthSolution {
  const target = Number.isFinite(container) ? Math.max(0, Math.floor(container)) : 0;
  if (columns.length === 0) {
    return { widths: [], total: 0, container: target, floor: 0, overflow: false };
  }

  const widths = new Array<number>(columns.length).fill(0);
  const flexible: number[] = [];
  const fixedTotal = columns.reduce((accumulator, column, index) => {
    if (column.fixed === undefined) {
      flexible.push(index);
      return accumulator;
    }
    widths[index] = Math.max(0, Math.round(column.fixed));
    return accumulator + (widths[index] ?? 0);
  }, 0);

  const available = target - fixedTotal;
  if (flexible.length === 0) {
    // No flexible columns: the only exact answer is to scale the fixed lane.
    // Unreachable from a real view (the identity lane always has siblings), and
    // it still cannot throw.
    const scale = target / (fixedTotal || 1);
    let running = 0;
    columns.forEach((_column, index) => {
      const value =
        index === columns.length - 1 ? target - running : Math.floor((widths[index] ?? 0) * scale);
      widths[index] = Math.max(0, value);
      running += value;
    });
    const total = sum(widths);
    return { widths, total, container: target, floor: fixedTotal, overflow: total > target };
  }

  const mins = flexible.map((index) => Math.max(1, columns[index]?.min ?? 1));
  const weights = flexible.map((index) => Math.max(0, columns[index]?.weight ?? 1));
  const minTotal = sum(mins);
  const floor = fixedTotal + minTotal;

  if (available < minTotal) {
    // Below the floor. Give every column its declared minimum, keep the fixed
    // lane, and say the table does not fit: the panel scrolls. This is the one
    // branch that used to allocate `1 + allocate(negative)` and throw.
    flexible.forEach((index, slot) => {
      widths[index] = mins[slot] ?? 1;
    });
    const total = sum(widths);
    return { widths, total, container: target, floor, overflow: total > target };
  }

  const free = available - minTotal;
  const exact = weights.map((weight) => (free * weight) / (sum(weights) || 1));
  const floors = exact.map((value) => Math.floor(value));
  let leftover = free - sum(floors);

  flexible.forEach((index, slot) => {
    widths[index] = (mins[slot] ?? 1) + (floors[slot] ?? 0);
  });

  // Every leftover pixel goes to one column, chosen after allocation. If the
  // choice found nothing (impossible: `flexible` is non-empty here), the first
  // flexible column absorbs it rather than the pixel being lost.
  if (leftover > 0) {
    const winner = absorbIndex(
      flexible.map((index) => columns[index] as SolverColumn),
      flexible.map((index) => widths[index] ?? 0),
    );
    const absolute = flexible[winner >= 0 ? winner : 0];
    if (absolute !== undefined) {
      widths[absolute] = (widths[absolute] ?? 0) + leftover;
      leftover = 0;
    }
  }

  const total = sum(widths);
  // The invariant is guaranteed by construction; `overflow` reports the truth
  // if a future edit ever breaks it, instead of throwing inside render.
  return { widths, total, container: target, floor, overflow: total > target };
}

/**
 * The pinned lanes first — the row number when the view draws it, then the
 * identity lane — and then the view's columns in the order the server gave.
 */
export function solverColumns(viewColumns: readonly string[]): SolverColumn[] {
  const hasNumber = viewColumns.includes(ROW_NUMBER_KEY);
  const lead: SolverColumn[] = [];
  if (hasNumber) {
    const number = columnSpec(ROW_NUMBER_KEY);
    lead.push({
      key: number.key,
      weight: 0,
      min: number.min,
      fixed: number.min,
      kind: number.kind,
    });
  }
  const identity: SolverColumn = {
    key: "identity",
    weight: 0,
    min: IDENTITY_WIDTH,
    fixed: IDENTITY_WIDTH,
    kind: "text",
  };
  return [
    ...lead,
    identity,
    ...viewColumns
      .filter((key) => key !== ROW_NUMBER_KEY)
      .map((key) => {
        const spec = columnSpec(key);
        return {
          key: spec.key,
          weight: spec.weight,
          min: spec.min,
          kind: spec.kind,
        } satisfies SolverColumn;
      }),
  ];
}

/** The one call a table makes: view columns + container -> columns + widths. */
export function solveView(viewColumns: readonly string[], container: number): SolvedColumns {
  const columns = solverColumns(viewColumns);
  return { columns, ...solveWidths(columns, container) };
}
