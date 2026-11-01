/**
 * The map's words: counts, signed percentages, the metric's name and the clock.
 *
 * Same three rules as the grid and the breadth strip, restated here because the
 * map is the surface most likely to break them: a legend that prints `-0.00%`,
 * or a number in red because it is *near* zero, is a legend that lies.
 *
 *   * absent is not zero — `null` prints `n/a`;
 *   * sign is carried by the printed digits *and* the glyph, never by colour;
 *   * a magnitude that rounds to zero claims no direction.
 */

const COUNTS = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

/** `5576` → `5,576`. Never compacted: `5.6K` hides a rounding. */
export function formatCount(value: number): string {
  return COUNTS.format(Math.round(value));
}

const DECIMALS = new Map<number, Intl.NumberFormat>();

function decimal(precision: number): Intl.NumberFormat {
  const cached = DECIMALS.get(precision);
  if (cached) return cached;
  const created = new Intl.NumberFormat("en-US", {
    minimumFractionDigits: precision,
    maximumFractionDigits: precision,
  });
  DECIMALS.set(precision, created);
  return created;
}

export type Sign = -1 | 0 | 1 | null;

export interface SignedPercent {
  readonly text: string;
  readonly sign: Sign;
  readonly absent: boolean;
}

/** `-1.0998` → `{text: "-1.10%", sign: -1}`; `null` → `{text: "n/a"}`. */
export function signedPercent(value: number | null | undefined, precision = 2): SignedPercent {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return { text: "n/a", sign: null, absent: true };
  }
  const magnitude = decimal(precision).format(Math.abs(value));
  const printed = Number(magnitude.replace(/,/g, ""));
  if (!(printed > 0)) return { text: `${magnitude}%`, sign: 0, absent: false };
  return value > 0
    ? { text: `+${magnitude}%`, sign: 1, absent: false }
    : { text: `-${magnitude}%`, sign: -1, absent: false };
}

/** The token class that repeats a printed sign (never the only channel). */
export function toneClass(sign: Sign): string {
  switch (sign) {
    case 1:
      return "text-up";
    case -1:
      return "text-down";
    case 0:
      return "text-flat";
    default:
      return "text-fg-subtle";
  }
}

/** Market cap in USD billions, as a compact label: `5034.7` → `$5.03T`. */
export function marketCapLabel(billions: number): string {
  if (!Number.isFinite(billions) || billions <= 0) return "—";
  if (billions >= 1_000) return `$${(billions / 1_000).toFixed(2)}T`;
  if (billions >= 1) return `$${billions.toFixed(1)}B`;
  return `$${(billions * 1_000).toFixed(0)}M`;
}

/**
 * The metric as the sidebar names it. Only `change` exists on the wire today;
 * an unknown metric is printed as itself rather than mislabelled "1-Day".
 */
export function metricLabel(metric: string): string {
  if (metric === "change") return "1-Day performance";
  if (metric === "change_1w") return "1-Week performance";
  return metric.replace(/[_-]+/g, " ");
}

/** `2026-10-31T09:21:58+00:00` → `09:21:58Z`; unzoned strings stay unzoned. */
export function clockLabel(iso: string): string {
  if (iso.trim().length === 0) return "—";
  const hasOffset = /(?:[Zz]|[+-]\d{2}:?\d{2})$/.test(iso);
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso.length > 24 ? `${iso.slice(0, 24)}…` : iso;
  if (!hasOffset) return `${parsed.toISOString().slice(11, 19)} (unzoned)`;
  return `${parsed.toISOString().slice(11, 19)}Z`;
}
