/**
 * The bridge between the payloads this product already parses and the bar
 * series `lightweight-charts` draws.
 *
 * The library is not allowed to see the wire. A chart endpoint can repeat a
 * timestamp, answer out of order, pad one array and not another, or state a
 * high below the close it shipped with — none of which `setData` forgives
 * (it throws on a non-ascending time and draws a spike from a bad extreme).
 * So every caller goes through this one function:
 *
 *   * **one shape out.** `ChartBar` is ascending, de-duplicated, finite and
 *     internally consistent; the library's contract is satisfied before it is
 *     asked to hold anything.
 *   * **three shapes in.** `{bars: [{t,o,h,l,c,v}]}` is the chart endpoint's
 *     document (and `ChartSeries` from `types.ts` is assignable to it);
 *     `{timestamp[], close[], open?…}` is the columnar form an aggregate
 *     endpoint hands back; `{closes[]}` is Home's index series, which has no
 *     clock at all.
 *   * **gaps stay gaps.** A missing session is not filled, a dropped row is
 *     not renumbered, and a duplicate timestamp keeps the last write — the
 *     same correction convention `parseChart` uses. Nothing here invents a
 *     price, a time or a bar that the answer did not contain.
 *   * **OHLC is repaired only where it can be.** If the open or close sits
 *     outside the stated high/low (a provider that rounds one field but not
 *     the other), the wick is widened to contain the body. The alternative —
 *     dropping the row — would delete a real session over a rounding error.
 *
 * **The ordinal case is stated, not hidden.** `{closes[]}` carries no
 * timestamps, so its x-axis is the bar's position, not a date. Those bars set
 * `timeLabel` to `Bar N` and the caller must keep the time axis hidden (the
 * Home strip does); a date would be a fabrication with a calendar's authority.
 */

import type { UTCTimestamp } from "lightweight-charts";

import type { Bar } from "./types";

/**
 * One bar, ready for `series.setData`.
 *
 * `time` is epoch **seconds** (the wire's unit). `timeLabel` is the same
 * instant in the accessible table's words: `2026-10-31 14:30` (UTC) for a
 * dated series, `Bar 7` for an ordinal one.
 */
export interface ChartBar {
  readonly time: UTCTimestamp;
  readonly open: number;
  readonly high: number;
  readonly low: number;
  readonly close: number;
  readonly volume: number;
  readonly timeLabel: string;
}

/**
 * Every payload shape a chart in this product can be handed.
 *
 * The properties are all optional because the shapes are alternatives; the
 * reader below picks the most authoritative one that is present. `bars` means
 * the chart endpoint answered, `timestamp` + `close` means an aggregate did,
 * `closes` means Home's index series did.
 */
export interface BarPayload {
  /** Epoch seconds, one per close. */
  readonly timestamp?: readonly number[];
  readonly close?: readonly number[];
  readonly open?: readonly number[];
  readonly high?: readonly number[];
  readonly low?: readonly number[];
  readonly volume?: readonly number[];
  /** The chart endpoint's document (`ChartSeries` is assignable to this). */
  readonly bars?: readonly Bar[];
  /** Home's index series: prices only, ordered oldest → newest. */
  readonly closes?: readonly number[];
}

/** A finite number, or null. `0` is finite; whether it is a *price* is below. */
function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** A price must be positive: a zero is a missing field, not a free asset. */
function price(value: unknown): number | null {
  const number = finite(value);
  return number !== null && number > 0 ? number : null;
}

/**
 * `YYYY-MM-DD HH:MM`, UTC.
 *
 * Fixed width, no locale, no `Intl` — a table cell has no room for a long
 * month name and two readers on two machines must see the same string. The
 * caption says the zone, so the string does not have to.
 */
export function utcLabel(seconds: number): string {
  const date = new Date(seconds * 1000);
  if (!Number.isFinite(date.getTime())) return String(seconds);
  const pad = (value: number) => String(value).padStart(2, "0");
  return [
    `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`,
    `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`,
  ].join(" ");
}

function makeBar(
  time: number,
  open: number,
  high: number,
  low: number,
  close: number,
  volume: number,
  ordinal: boolean,
): ChartBar {
  return {
    time: time as UTCTimestamp,
    // Widen the wick to contain the body; never narrow the body to fit the
    // wick (that would delete a price the answer stated).
    open,
    high: Math.max(high, open, close),
    low: Math.min(low, open, close),
    close,
    volume: volume > 0 ? volume : 0,
    timeLabel: ordinal ? `Bar ${time + 1}` : utcLabel(time),
  };
}

/** Ascending, one bar per timestamp, the later row winning. */
function ordered(bars: readonly ChartBar[]): ChartBar[] {
  const byTime = new Map<number, ChartBar>();
  for (const bar of bars) byTime.set(bar.time, bar);
  return [...byTime.values()].sort((left, right) => left.time - right.time);
}

function fromWire(bars: readonly Bar[]): ChartBar[] {
  const kept: ChartBar[] = [];
  for (const bar of bars) {
    const t = finite(bar.t);
    const o = price(bar.o);
    const h = price(bar.h);
    const l = price(bar.l);
    const c = price(bar.c);
    // A bar with no time or no body is not a candle with a missing wick; it
    // is a row that cannot be placed. Dropping it keeps the rest drawable.
    if (t === null || o === null || h === null || l === null || c === null) continue;
    const v = finite(bar.v);
    kept.push(makeBar(t, o, h, l, c, v ?? 0, false));
  }
  return ordered(kept);
}

function fromColumns(payload: BarPayload, stamps: readonly number[], closes: readonly number[]): ChartBar[] {
  const kept: ChartBar[] = [];
  // Whichever column is shorter ends the series; a timestamp without a close
  // is a hole in the payload, not a reason to reach for yesterday's price.
  const count = Math.min(stamps.length, closes.length);
  for (let index = 0; index < count; index += 1) {
    const t = finite(stamps[index]);
    const c = price(closes[index]);
    if (t === null || c === null) continue;
    const o = price(payload.open?.[index]) ?? c;
    const h = price(payload.high?.[index]) ?? c;
    const l = price(payload.low?.[index]) ?? c;
    const v = finite(payload.volume?.[index]);
    kept.push(makeBar(t, o, h, l, c, v ?? 0, false));
  }
  return ordered(kept);
}

function fromCloses(closes: readonly number[]): ChartBar[] {
  const kept: ChartBar[] = [];
  // `forEach` keeps the original index, so a dropped point leaves its slot
  // empty rather than sliding every later bar one position left.
  closes.forEach((value, index) => {
    const c = price(value);
    if (c === null) return;
    kept.push(makeBar(index, c, c, c, c, 0, true));
  });
  return kept;
}

/**
 * The payload, as bars the library can draw.
 *
 * An absent or unusable payload is an empty array, never a throw: a chart with
 * no data is a state the card already knows how to describe, and a component
 * that throws on it takes the page down instead.
 */
export function toBars(payload: BarPayload): ChartBar[] {
  if (payload.bars !== undefined) return fromWire(payload.bars);
  if (payload.timestamp !== undefined && payload.close !== undefined) {
    return fromColumns(payload, payload.timestamp, payload.close);
  }
  if (payload.closes !== undefined) return fromCloses(payload.closes);
  return [];
}

/** The library's own datum, without the table's label crossing the boundary. */
export function barData(bars: readonly ChartBar[]): Array<{
  time: UTCTimestamp;
  open: number;
  high: number;
  low: number;
  close: number;
}> {
  return bars.map(({ time, open, high, low, close }) => ({ time, open, high, low, close }));
}
