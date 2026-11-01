/**
 * Numbers, printed once, the same way everywhere on the page.
 *
 * Four rules, inherited from the grid's formatter and re-stated here because
 * the dashboard is the surface most tempted to break them:
 *
 * * **A price, a volume and a percent go through an existing formatter** —
 *   `formatMoney`, `formatSignedPercent` (`@/grid/format`) and `formatVolume`
 *   (`./signals`) — so Home cannot disagree with the grid about what `0.004`
 *   looks like.
 * * **Absent is not zero.** A `null` prints an em dash and carries a `title`
 *   naming the missing fact. It never prints `$0.00` or `+0.00%`.
 * * **Direction is in the text, not in the colour.** A delta reads
 *   `▲ +1.23%` with a screen-reader word after it (`up`), so the arrow, the
 *   sign and the word all survive colour blindness, greyscale and a reader that
 *   ignores CSS.
 * * **The tone follows the digits that were printed**, not the raw float: a
 *   value that rounds to `0.00%` is flat, not "minus zero".
 */

import { formatMoney, formatNumber, formatSignedPercent } from "@/grid/format";
import { cn } from "@/lib/utils";

import { formatVolume } from "../signals";

const TONE = {
  up: "text-up",
  down: "text-down",
  flat: "text-flat",
} as const;

const ARROW = { up: "▲", down: "▼", flat: "" } as const;

type Tone = keyof typeof TONE;

function printedTone(text: string): Tone {
  if (text.startsWith("+")) return "up";
  if (text.startsWith("-")) return "down";
  return "flat";
}

export interface DeltaProps {
  readonly value: number | null;
  /** What is missing when `value` is null: `change`, `1-day change`, … */
  readonly missing?: string;
  readonly className?: string;
  readonly precision?: number;
}

/**
 * A signed percentage: `▲ +1.23%` + a visually hidden `up`.
 *
 * `null` is an em dash with a title, never a zero: a dashboard that paints an
 * unmeasured move as flat is telling the reader the market did nothing.
 */
export function Delta({ value, missing = "change", className, precision = 2 }: DeltaProps) {
  if (value === null) {
    return (
      <span
        data-null="true"
        title={`${missing} unavailable — the response did not carry one`}
        className={cn("nums text-fg-subtle", className)}
      >
        —
      </span>
    );
  }

  const text = formatSignedPercent(value, precision);
  const tone = printedTone(text);

  return (
    <span
      data-tone={tone}
      title={text}
      className={cn("nums inline-flex items-baseline gap-1 whitespace-nowrap", TONE[tone], className)}
    >
      {tone === "flat" ? null : (
        <span aria-hidden="true" className="text-[9px] leading-none">
          {ARROW[tone]}
        </span>
      )}
      <span>{text}</span>
      <span className="sr-only">{tone === "up" ? "up" : tone === "down" ? "down" : "unchanged"}</span>
    </span>
  );
}

export interface ValueProps {
  readonly value: number | null;
  readonly missing?: string;
  readonly className?: string;
}

/** A last price: `$344.63`; a missing one is `—` with a title. */
export function Price({ value, missing = "last price", className }: ValueProps) {
  return (
    <span
      data-null={value === null ? "true" : undefined}
      title={value === null ? `${missing} unavailable — the response did not carry one` : formatMoney(value)}
      className={cn("nums whitespace-nowrap", value === null ? "text-fg-subtle" : "text-fg", className)}
    >
      {value === null ? "—" : formatMoney(value)}
    </span>
  );
}

/** Share volume: `42.42M`; a missing one is `—` with a title. */
export function Volume({ value, missing = "volume", className }: ValueProps) {
  return (
    <span
      data-null={value === null ? "true" : undefined}
      title={value === null ? `${missing} unavailable — the response did not carry one` : `${formatNumber(value, 0)} shares`}
      className={cn(
        "nums whitespace-nowrap",
        value === null ? "text-fg-subtle" : "text-fg-muted",
        className,
      )}
    >
      {value === null ? "—" : formatVolume(value)}
    </span>
  );
}

/** A count: `1,557`. Counts are never compacted — `1.6K` hides a rounding. */
export function Count({ value, missing = "count", className }: ValueProps) {
  return (
    <span
      data-null={value === null ? "true" : undefined}
      title={value === null ? `${missing} unavailable — the response did not carry one` : undefined}
      className={cn(
        "nums whitespace-nowrap tabular-nums",
        value === null ? "text-fg-subtle" : "text-fg",
        className,
      )}
    >
      {value === null ? "—" : formatNumber(value, 0)}
    </span>
  );
}
