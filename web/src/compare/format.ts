/**
 * The comparison table's number vocabulary.
 *
 * Four units cover every cell on the page — `ratio`, `percent`, `usd`, `count`
 * — and each has exactly one printed form, because a column that switches
 * between `1.2K` and `1,234` makes two companies look like two datasets.
 *
 * The formatters are the grid's *rules* restated in a local module rather than
 * imported from it: `grid/format.ts` is owned by the table, and the compare
 * chunk must not drag the 13 KB column-metadata table in to print a number.
 * The rules copied here are the ones a reader can check:
 *
 * * absent (`null`, `undefined`, `""`, non-finite) is an em dash — never `0`;
 * * a true zero is `0.00`, because zero is a measurement;
 * * a value below the printed precision says `<` rather than rounding to a
 *   zero it does not have;
 * * percent keeps its sign, so a fall is never printed as a rise.
 */

export type MetricUnit = "ratio" | "percent" | "usd" | "count";

/** The em dash used for every absent cell, shared with the tests. */
export const NULL_CELL = "—";

export function isMissing(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === "string" && value.trim().length === 0) return true;
  if (typeof value === "number" && !Number.isFinite(value)) return true;
  return false;
}

const compact = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 2,
});

/** One formatter per precision, built once — `Intl` construction is the cost. */
const fixed = new Map<number, Intl.NumberFormat>();

function plain(precision: number): Intl.NumberFormat {
  const cached = fixed.get(precision);
  if (cached) return cached;
  const created = new Intl.NumberFormat("en-US", {
    minimumFractionDigits: precision,
    maximumFractionDigits: precision,
  });
  fixed.set(precision, created);
  return created;
}

/** `5.03T`, `63.02M`, `164K` — the grid's own compact spelling. */
function formatCompact(value: number): string {
  return compact.format(value);
}

/** A number at a fixed precision, thousands separated. */
function formatNumber(value: number, precision = 2): string {
  return plain(precision).format(value);
}

/**
 * Money. A non-zero magnitude that would print as `$0.00` says `<$0.01`
 * instead: `$0.00` is a different number from `$0.004`.
 */
function formatMoney(value: number, compactMoney = false, precision = 2): string {
  const sign = value < 0 ? "-" : "";
  const magnitude = Math.abs(value);

  if (compactMoney) {
    const text = formatCompact(magnitude);
    if (magnitude > 0 && text === formatCompact(0)) return `${sign}<$0.01`;
    return `${sign}$${text}`;
  }

  const printed = plain(precision).format(magnitude);
  if (magnitude > 0 && printed === plain(precision).format(0)) {
    return `${sign}<$${plain(precision).format(10 ** -precision)}`;
  }
  return `${sign}$${printed}`;
}

/**
 * One cell, by unit. `compact` only reaches `usd` (a market cap); the other
 * units have one form by definition.
 */
export function formatMetric(value: unknown, unit: MetricUnit, compactMoney = false): string {
  if (isMissing(value)) return NULL_CELL;
  const number = Number(value);
  if (!Number.isFinite(number)) return NULL_CELL;

  switch (unit) {
    case "ratio":
      return formatNumber(number, 2);
    case "percent":
      // The sign comes from the printed digits, so `-0.001` cannot claim a
      // direction its own display denies.
      return `${formatNumber(number, 2)}%`;
    case "usd":
      return formatMoney(number, compactMoney, 2);
    case "count":
      return formatCompact(number);
    default:
      return String(value);
  }
}

/** The quote line in a column header. */
export function formatPrice(value: unknown): string {
  return formatMetric(value, "usd");
}

/**
 * The change in a column header: signed, because the sign is the point.
 * A magnitude that rounds to zero prints no direction at all.
 */
export function formatChange(value: unknown): string {
  if (isMissing(value)) return NULL_CELL;
  const number = Number(value);
  if (!Number.isFinite(number)) return NULL_CELL;
  const magnitude = formatNumber(Math.abs(number), 2);
  if (!(Number(magnitude.replace(/,/g, "")) > 0)) return `${magnitude}%`;
  return number > 0 ? `+${magnitude}%` : `-${magnitude}%`;
}

/**
 * The server's `as_of` as a clock, not a machine timestamp.
 *
 * The API stamps microseconds and an offset (`2026-11-02T11:02:25.845291+00:00`);
 * a footer that prints that is a log line on a product page. Anything the
 * browser cannot parse is shown as served — a wrong time is worse than an
 * unfriendly one.
 */
export function formatAsOf(value: string | null): string | null {
  if (value === null || value.trim().length === 0) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.toISOString().slice(11, 16)} UTC`;
}
