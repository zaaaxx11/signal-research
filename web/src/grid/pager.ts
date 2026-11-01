/**
 * Page arithmetic, and the numbers a pager draws.
 *
 * The bottom bar shows at most seven items — first, last, the current page and
 * one neighbour each side, with a gap where the numbers were skipped. That is
 * the whole rule, and it is a pure function so the truncation can be tested
 * without a DOM: 281 pages must not print 281 buttons.
 */

/** One slot in the pager: a page number, or a skipped run. */
export type PageItem = number | "gap";

/**
 * The pages to draw for `current` of `count`.
 *
 * Always includes 1 and `count`; includes `current - 1`, `current`,
 * `current + 1` when they are in range; inserts a gap wherever the run is
 * broken, and never draws a gap of one page (a single skipped page is cheaper
 * to print than to elide).
 */
export function pageItems(current: number, count: number): PageItem[] {
  if (count <= 0) return [];
  const page = Math.min(Math.max(current, 1), count);
  if (count <= 7) return Array.from({ length: count }, (_, index) => index + 1);

  const wanted = new Set<number>([1, count, page - 1, page, page + 1]);
  // Near an edge the window would be lopsided; widen the far side so the bar
  // always shows the same number of numbers.
  if (page <= 3) {
    wanted.add(2);
    wanted.add(3);
    wanted.add(4);
  }
  if (page >= count - 2) {
    wanted.add(count - 1);
    wanted.add(count - 2);
    wanted.add(count - 3);
  }

  const pages = [...wanted].filter((value) => value >= 1 && value <= count).sort((a, b) => a - b);
  const items: PageItem[] = [];
  for (let index = 0; index < pages.length; index += 1) {
    const value = pages[index]!;
    const previous = pages[index - 1];
    if (previous !== undefined && value - previous === 2) items.push(previous + 1);
    else if (previous !== undefined && value - previous > 2) items.push("gap");
    items.push(value);
  }
  return items;
}

/** How many pages a total makes at a page size. Never zero: an empty screen is page 1. */
export function pagesOf(total: number, limit: number): number {
  const size = Math.max(1, Math.floor(limit));
  if (!Number.isFinite(total) || total <= 0) return 1;
  return Math.max(1, Math.ceil(total / size));
}

/** The page a requested number lands on, clamped to what exists. */
export function clampPage(requested: number, count: number): number {
  if (!Number.isFinite(requested)) return 1;
  return Math.min(Math.max(Math.trunc(requested), 1), Math.max(1, count));
}

/** `1/5,576` — the page position over the filtered total. */
export function countReadout(pageIndex: number, total: number): string {
  if (total <= 0) return "—";
  return `${pageIndex + 1}/${total.toLocaleString("en-US")} Total`;
}
