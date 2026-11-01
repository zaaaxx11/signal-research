/**
 * Technical indicators, computed here and nowhere else.
 *
 * Every number on the chart's overlays is arithmetic over the closes the
 * payload already carried: no endpoint, no package, no second request. That is
 * the whole reason this file is pure — an indicator that fetches is an
 * indicator that can disagree with the bars it is drawn over.
 *
 * **Alignment is part of the contract.** Each function returns an array the
 * same length as its input, with `null` in every slot that has no value yet.
 * Index `i` of the result always describes bar `i` of the series, so a caller
 * can zip the output onto the bars without counting, and the accessible table
 * can print an empty cell rather than shifting every later row by the warm-up
 * length.
 *
 * **Gaps break the window; they are not skipped.** A bar missing a close is a
 * hole in the session, not a reason to average the two bars on either side of
 * it as though they were adjacent. A window containing a gap is `null`, and a
 * gap resets the running state (an SMA's sum, an EMA's seed, Wilder's averages)
 * so a later value is only ever computed from consecutive real closes. Filling
 * the hole would invent a price; skipping it would print an average that no
 * `period` consecutive bars ever produced.
 *
 * **The flat-series convention is pinned.** Wilder's RSI is `100` when the
 * average loss is zero and gain is positive, and undefined at `0/0` when both
 * are zero. A series that has not moved at all is neither overbought nor
 * oversold, so this implementation answers **50** — the neutral midpoint —
 * rather than 100, which would claim maximum strength from zero information.
 * `rsi()` therefore always returns a number from index `period` onward; it
 * never returns `NaN`, and every value is clamped to `[0, 100]`.
 *
 * Periods are a programming contract, not user input: a non-positive or
 * fractional period is a `RangeError`, because silently returning a column of
 * `null`s would look exactly like "not enough bars yet".
 */

/** A close, or a hole where one is missing. Non-finite numbers are holes too. */
export type Slot = number | null | undefined;

/** A value, or the warm-up slot before one exists. */
export type Point = number | null;

/** The simple averages the chart offers, in drawing order. */
export const SMA_PERIODS = [20, 50, 200] as const;

export type SmaPeriod = (typeof SMA_PERIODS)[number];

/** Wilder's default, and the period the pane is labelled with. */
export const RSI_PERIOD = 14;

/**
 * The switchable indicators, in the order the chips and the table columns
 * present them. Short-term first: a reader scanning left to right meets the
 * fastest average before the slowest.
 */
export const INDICATOR_IDS = ["sma20", "sma50", "sma200", "rsi"] as const;

export type IndicatorId = (typeof INDICATOR_IDS)[number];

export type SmaId = Exclude<IndicatorId, "rsi">;

export const SMA_BY_ID: Readonly<Record<SmaId, SmaPeriod>> = {
  sma20: 20,
  sma50: 50,
  sma200: 200,
};

/** What a chip says. The period is in the name, so no legend is needed. */
export const INDICATOR_LABELS: Readonly<Record<IndicatorId, string>> = {
  sma20: "SMA 20",
  sma50: "SMA 50",
  sma200: "SMA 200",
  rsi: `RSI ${RSI_PERIOD}`,
};

/**
 * The table's column header and the caption's description: a short name, and
 * the sentence that states what the number actually is.
 */
export const INDICATOR_COLUMNS: Readonly<
  Record<IndicatorId, { readonly header: string; readonly description: string }>
> = {
  sma20: { header: "SMA 20", description: "SMA 20 (20-bar simple moving average of the close)" },
  sma50: { header: "SMA 50", description: "SMA 50 (50-bar simple moving average of the close)" },
  sma200: {
    header: "SMA 200",
    description: "SMA 200 (200-bar simple moving average of the close)",
  },
  rsi: { header: `RSI ${RSI_PERIOD}`, description: `RSI ${RSI_PERIOD} (Wilder's relative strength index)` },
};

/** True for a finite number; everything else is a hole in the series. */
function isValue(value: Slot): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function assertPeriod(period: number, name: string): void {
  if (!Number.isInteger(period) || period <= 0) {
    throw new RangeError(`${name} period must be a positive integer, received ${String(period)}`);
  }
}

/** A same-length array of `null`s: the answer for a series too short to warm up. */
function empty(length: number): Point[] {
  return new Array<Point>(length).fill(null);
}

/**
 * Simple moving average.
 *
 * A rolling sum over the last `period` consecutive closes. `sma` of `1..20`
 * with a period of 20 is therefore `null` nineteen times and `10.5` once.
 */
export function sma(values: readonly Slot[], period: number): Point[] {
  assertPeriod(period, "sma");
  const out = empty(values.length);
  let sum = 0;
  let run = 0;

  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (!isValue(value)) {
      // A hole is not a zero: the window restarts after it.
      sum = 0;
      run = 0;
      continue;
    }
    sum += value;
    run += 1;
    if (run > period) {
      // The slot leaving the window is finite, because `run` counts only the
      // consecutive values that end at `index`.
      sum -= values[index - period] as number;
    }
    if (run >= period) out[index] = sum / period;
  }

  return out;
}

/**
 * Exponential moving average, seeded with the first full window's simple
 * average — the convention every published EMA table uses, and the one that
 * makes the first value agree with an SMA of the same period.
 *
 * `alpha = 2 / (period + 1)`; a hole restarts the seed, so an EMA after a gap
 * is computed from the closes that followed it rather than carried across.
 */
export function ema(values: readonly Slot[], period: number): Point[] {
  assertPeriod(period, "ema");
  const out = empty(values.length);
  const alpha = 2 / (period + 1);
  let sum = 0;
  let run = 0;
  let previous: number | null = null;

  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (!isValue(value)) {
      sum = 0;
      run = 0;
      previous = null;
      continue;
    }
    if (previous === null) {
      sum += value;
      run += 1;
      if (run === period) {
        previous = sum / period;
        out[index] = previous;
        sum = 0;
        run = 0;
      }
      continue;
    }
    previous = value * alpha + previous * (1 - alpha);
    out[index] = previous;
  }

  return out;
}

/**
 * Wilder's relative strength index.
 *
 * The first value lands at index `period` (it needs `period` changes, hence
 * `period + 1` closes); before that the array is `null`. The seed is the plain
 * average of the first `period` gains and losses, and every value after it is
 * Wilder-smoothed: `(previous * (period - 1) + current) / period`. That
 * smoothing — not a plain moving average of gains — is what makes the number
 * comparable to every other RSI on the screen.
 *
 * A flat run of closes has no gain and no loss: `rs` would be `0/0`. The
 * convention is pinned above and tested: **50**.
 */
export function rsi(values: readonly Slot[], period: number = RSI_PERIOD): Point[] {
  assertPeriod(period, "rsi");
  const out = empty(values.length);
  let averageGain = 0;
  let averageLoss = 0;
  let changes = 0;
  let previous: number | null = null;

  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (!isValue(value)) {
      averageGain = 0;
      averageLoss = 0;
      changes = 0;
      previous = null;
      continue;
    }
    if (previous === null) {
      previous = value;
      continue;
    }

    const change = value - previous;
    previous = value;
    const gain = change > 0 ? change : 0;
    const loss = change < 0 ? -change : 0;

    if (changes < period) {
      averageGain += gain;
      averageLoss += loss;
      changes += 1;
      if (changes === period) {
        // Seed with the simple averages, then answer the same bar.
        averageGain /= period;
        averageLoss /= period;
        out[index] = strength(averageGain, averageLoss);
      }
      continue;
    }

    averageGain = (averageGain * (period - 1) + gain) / period;
    averageLoss = (averageLoss * (period - 1) + loss) / period;
    out[index] = strength(averageGain, averageLoss);
  }

  return out;
}

/**
 * `100 - 100 / (1 + rs)`, with the two degenerate cases named rather than left
 * to floating point. `0/0` is the flat series (50, see the module note);
 * a zero loss with a gain is a run of only-up closes (100).
 */
function strength(averageGain: number, averageLoss: number): number {
  if (averageLoss === 0) return averageGain === 0 ? 50 : 100;
  const value = 100 - 100 / (1 + averageGain / averageLoss);
  // Arithmetic keeps this in range; the clamp is a contract, not a correction.
  return Math.min(100, Math.max(0, value));
}

/** `20` → `"sma20"`. Kept as a type-level pair with `SMA_BY_ID`. */
export function isIndicatorId(value: unknown): value is IndicatorId {
  return typeof value === "string" && (INDICATOR_IDS as readonly string[]).includes(value);
}

/**
 * A caller's selection, in catalogue order and without duplicates.
 *
 * The chips own a set; the chart and the table want a list whose order is the
 * product's, not the click order's, so a reader who switches SMA 200 on before
 * SMA 20 still gets the columns in the same place as everyone else.
 */
export function orderIndicators(ids: readonly IndicatorId[] | undefined): IndicatorId[] {
  if (ids === undefined || ids.length === 0) return [];
  const wanted = new Set(ids);
  return INDICATOR_IDS.filter((id) => wanted.has(id));
}
