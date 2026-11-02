/**
 * The ten native scanner metrics the picker shows as **Computed** columns.
 *
 * The bulk scan already answers `EMA13`…`EMA200`, `SMA13`…`SMA100`, `Mom` and
 * `Perf.W`; the 116-key catalogue is the shipped Finviz surface and does not
 * name them. They are therefore offered as an *additional* group rather than
 * folded into the catalogue: the catalogue keeps its frozen 116 keys, and
 * `POST /screens/select` accepts these ten names through
 * `app/screen/derived_columns.py`.
 *
 * "Computed" is the interface's word, not a claim about arithmetic. Every value
 * is copied verbatim from the scanner; the server's registry is the authority
 * for the key list and `tests/test_screen_computed_columns.py` fails if this
 * mirror drifts from it.
 *
 * Units matter to the renderer: the eight moving averages and `momentum` are
 * prices, and `perf_wtd` is a percent. `Mom` is a price **difference** over ten
 * sessions (`close − close[10]`), not a rate — it is never printed with a `%`.
 */

export const COMPUTED_COLUMN_KEYS = [
  "ema_13",
  "ema_21",
  "ema_50",
  "ema_100",
  "ema_200",
  "sma_13",
  "sma_21",
  "sma_100",
  "momentum",
  "perf_wtd",
] as const;

export type ComputedColumnKey = (typeof COMPUTED_COLUMN_KEYS)[number];

/** The same list as a plain readonly array, for props that take `string[]`. */
export const COMPUTED_KEYS: readonly string[] = COMPUTED_COLUMN_KEYS;

const COMPUTED_SET: ReadonlySet<string> = new Set(COMPUTED_COLUMN_KEYS);

/** Is `key` one of the ten extra columns (not a catalogue key)? */
export function isComputedColumn(key: string): boolean {
  return COMPUTED_SET.has(key);
}
