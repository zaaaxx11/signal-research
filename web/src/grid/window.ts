/**
 * Which columns are on screen.
 *
 * A 116-column table is 9 639 px wide; laying out 2 340 cells to show the 12 a
 * reader can actually see is what made the wide view take a second. This
 * function answers the only question the renderer needs: given the solved
 * widths, the scroll offset and the viewport, which contiguous run of columns
 * must be in the DOM? `overscan` columns are added on each side so a fast
 * scroll does not flash empty cells.
 *
 * It is arithmetic over the solver's own numbers, so the answer can never drift
 * from the widths the table is laid out with. A viewport this module cannot
 * believe (0, negative, non-finite — jsdom, or the first paint before the
 * ResizeObserver reports) means "render every column": a wide table that omits
 * a column because it could not measure itself is worse than a slow one.
 */

/** Columns of slop on each side of the viewport, in units of 96 px. */
export const WINDOW_OVERSCAN = 2;

export interface ColumnRange {
  /** First rendered index into the view's column list (inclusive). */
  start: number;
  /** Last rendered index (inclusive); -1 when there are no columns. */
  end: number;
}

export function visibleRange(
  widths: readonly number[],
  scrollLeft: number,
  viewport: number,
  overscan: number = WINDOW_OVERSCAN,
): ColumnRange {
  const count = widths.length;
  if (count === 0) return { start: 0, end: -1 };
  if (!Number.isFinite(scrollLeft) || !(viewport > 0)) return { start: 0, end: count - 1 };

  const pad = Math.max(0, overscan) * 96;
  const from = Math.max(0, scrollLeft - pad);
  const to = scrollLeft + viewport + pad;

  let accumulated = 0;
  let start = 0;
  let end = count - 1;
  let started = false;

  for (let index = 0; index < count; index += 1) {
    const width = Math.max(0, widths[index] ?? 0);
    if (!started && accumulated + width > from) {
      start = index;
      started = true;
    }
    if (accumulated >= to) {
      end = index - 1;
      break;
    }
    accumulated += width;
  }

  if (!started) return { start: count - 1, end: count - 1 };
  return { start, end: Math.min(Math.max(end, start), count - 1) };
}
