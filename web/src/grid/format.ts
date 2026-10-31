/**
 * Cell formatting: one vocabulary for every value the catalogue can produce.
 *
 * Three rules are load-bearing:
 *
 * * **Absent is not zero.** `null`, `undefined`, `""` and non-finite numbers
 *   render the placeholder; a real `0` renders `0`. A grid that prints `0.00`
 *   for a field the store never filled is a grid that lies about the company.
 * * **A number is never printed as a different number.** A price of `0.004` is
 *   not `$0.00`: a non-zero value below the last digit a column can print is
 *   written `<$0.01` (or `<$0.0001` at four places), so the reader can see it
 *   is small rather than absent or nil.
 * * **Sign is never carried by colour alone.** A delta reads `▲ +1.23%` — the
 *   glyph and the sign are in the text, so it survives colour blindness, a
 *   greyscale print and a screen reader. And a magnitude that rounds to zero
 *   claims no direction at all: `-0.001` prints `0.00%`, not `-0.00%`.
 */

import type { ColumnSpec } from "./columnMeta";

/** The em dash, exposed as a constant so tests and renderers share it. */
export const NULL_PLACEHOLDER = "—";

export function isAbsent(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === "string" && value.trim().length === 0) return true;
  if (typeof value === "number" && !Number.isFinite(value)) return true;
  return false;
}

const compact = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 2,
});

/**
 * One formatter per precision, built once.
 *
 * `Intl.NumberFormat` construction is the expensive half of formatting a cell:
 * a 13-column panel used to build two per cell on every keypress (~2,600 for
 * one ArrowDown). The objects are immutable, so they cache cleanly.
 */
const formatters = new Map<number, Intl.NumberFormat>();

function plain(precision: number): Intl.NumberFormat {
  const cached = formatters.get(precision);
  if (cached) return cached;
  const created = new Intl.NumberFormat("en-US", {
    minimumFractionDigits: precision,
    maximumFractionDigits: precision,
  });
  formatters.set(precision, created);
  return created;
}

/** The smallest magnitude a precision can print: 0.01 at 2 dp, 0.0001 at 4. */
function smallestPrint(precision: number): number {
  return 10 ** -precision;
}

/** `5029592198492` -> `5.03T`; `67840642` -> `67.84M`; `150000` -> `150K`. */
export function formatCompact(value: number): string {
  return compact.format(value);
}

/**
 * `5.03T` with the currency mark in front of the sign: `-$1.2B`.
 *
 * A non-zero magnitude that would round to the precision's zero prints `<` the
 * smallest print instead — `<$0.01`, `<$0.0001` — because `$0.00` is a
 * different number. A true zero still prints `$0.00`.
 */
export function formatMoney(value: number, compactMoney = false, precision = 2): string {
  const sign = value < 0 ? "-" : "";
  const magnitude = Math.abs(value);

  if (compactMoney) {
    const text = formatCompact(magnitude);
    if (magnitude > 0 && text === formatCompact(0)) return `${sign}<$0.01`;
    return `${sign}$${text}`;
  }

  const printed = plain(precision).format(magnitude);
  if (magnitude > 0 && printed === plain(precision).format(0)) {
    return `${sign}<$${plain(precision).format(smallestPrint(precision))}`;
  }
  return `${sign}$${printed}`;
}

export function formatNumber(value: number, precision = 2): string {
  return plain(precision).format(value);
}

export function formatPercent(value: number, precision = 2): string {
  return `${formatNumber(value, precision)}%`;
}

/**
 * The signed form, without the arrow: `+1.23%`, `-1.23%`, `0.00%`.
 *
 * The sign is read off the magnitude that was actually printed. A value whose
 * magnitude rounds to `0.00` at this precision has no printed direction, and
 * `-0.00%` (or `+0.00%`) would be a claim the digits deny.
 */
export function formatSignedPercent(value: number, precision = 2): string {
  const magnitude = formatNumber(Math.abs(value), precision);
  const printed = Number(magnitude.replace(/,/g, ""));
  if (!(printed > 0)) return `${magnitude}%`;
  return value > 0 ? `+${magnitude}%` : `-${magnitude}%`;
}

export function signOf(value: number): -1 | 0 | 1 {
  if (value > 0) return 1;
  if (value < 0) return -1;
  return 0;
}

/**
 * Unix seconds (the API's `earnings_date`) to an ISO calendar date (UTC).
 * A millisecond timestamp is accepted too, because a field that silently
 * changed unit should render a date, not 1970.
 *
 * `0` is the one number that is not a date: it is the sentinel for "unknown",
 * and `1970-01-01` is a plausible-looking lie. It is absent, like `null`.
 */
export function formatDateValue(value: string | number): string {
  if (typeof value === "string") return value;
  if (value === 0) return NULL_PLACEHOLDER;
  const milliseconds = Math.abs(value) > 1e11 ? value : value * 1000;
  const date = new Date(milliseconds);
  if (Number.isNaN(date.getTime())) return NULL_PLACEHOLDER;
  return date.toISOString().slice(0, 10);
}

/** The text a cell paints, and the sign when the cell is a delta. */
export interface CellText {
  text: string;
  sign: -1 | 0 | 1 | null;
  absent: boolean;
  title: string;
}

/**
 * Format one raw value for one column kind. The renderers use this; tests use
 * it directly, so "0 renders as 0" is asserted without a DOM.
 */
export function formatCell(value: unknown, spec: ColumnSpec): CellText {
  if (isAbsent(value)) {
    return { text: NULL_PLACEHOLDER, sign: null, absent: true, title: "Not available" };
  }

  const precision = spec.precision ?? 2;
  const suffix = spec.suffix ?? "";

  switch (spec.kind) {
    case "money": {
      const number = Number(value);
      if (!Number.isFinite(number)) break;
      const text = formatMoney(number, false, precision);
      return { text, sign: null, absent: false, title: text };
    }
    case "compactMoney": {
      const number = Number(value);
      if (!Number.isFinite(number)) break;
      const text = formatMoney(number, true);
      // A `<` text has no printable magnitude: the title says the raw number
      // rather than repeating a zero the value does not have.
      const title = text.includes("<") ? `${String(number)} USD` : `${formatNumber(number)} USD`;
      return { text, sign: null, absent: false, title };
    }
    case "delta": {
      const number = Number(value);
      if (!Number.isFinite(number)) break;
      const text = formatSignedPercent(number, precision);
      // Sign and text are read from the same string, so they cannot disagree.
      const sign: -1 | 0 | 1 = text.startsWith("+") ? 1 : text.startsWith("-") ? -1 : 0;
      return { text, sign, absent: false, title: text };
    }
    case "percent": {
      const number = Number(value);
      if (!Number.isFinite(number)) break;
      const text = formatPercent(number, precision);
      return { text, sign: null, absent: false, title: text };
    }
    case "integer": {
      const number = Number(value);
      if (!Number.isFinite(number)) break;
      const text = formatCompact(number);
      return { text, sign: null, absent: false, title: plain(0).format(number) };
    }
    case "number": {
      const number = Number(value);
      if (!Number.isFinite(number)) break;
      const text = `${formatNumber(number, precision)}${suffix}`;
      return { text, sign: null, absent: false, title: text };
    }
    case "timestamp": {
      const number = Number(value);
      if (!Number.isFinite(number)) break;
      // `0` is the sentinel for "unknown", not midnight on 1 January 1970.
      if (number === 0) {
        return { text: NULL_PLACEHOLDER, sign: null, absent: true, title: "Not available" };
      }
      const text = formatDateValue(number);
      return { text, sign: null, absent: false, title: text };
    }
    case "date": {
      const text = String(value);
      return { text, sign: null, absent: false, title: text };
    }
    case "badge": {
      const text = String(value);
      return { text, sign: null, absent: false, title: text };
    }
    case "text":
    default: {
      const text = String(value);
      return { text, sign: null, absent: false, title: text };
    }
  }

  // A value whose type does not match its column (a string in a numeric cell)
  // is shown as text rather than as zero: unknown is not absent, and it is
  // certainly not 0.
  const text = String(value);
  return { text, sign: null, absent: false, title: text };
}
