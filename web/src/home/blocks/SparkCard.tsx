/**
 * One ticker in the strip: name, last, change, volume, thirty closes.
 *
 * A tile, not a card: the strip is the card (it has the level-2 heading), and a
 * tile carries a level-3 heading with the symbol. That keeps the heading tree
 * honest — `h1` (visually hidden) → `h2` per block → `h3` per tile — without
 * eight boxes competing with the six blocks for attention.
 *
 * Every value is nullable and every formatter is shared with the grid, so a
 * ticker the provider only half-quoted prints an em dash (with a title saying
 * what is missing) instead of a zero.
 */

import { Delta, Price, Volume } from "./Values";
import { Sparkline } from "./Sparkline";
import type { HomeSpark } from "../home";

export interface SparkCardProps {
  readonly spark: HomeSpark;
}

export function SparkCard({ spark }: SparkCardProps) {
  return (
    <div
      data-testid={`spark-card-${spark.symbol}`}
      className="flex min-w-0 flex-col gap-1 rounded-sm border border-line bg-surface-sunken p-1.5"
    >
      <h3 className="flex min-w-0 items-baseline gap-1.5">
        <span className="truncate font-mono text-[11px] font-semibold text-fg">{spark.symbol}</span>
        {spark.name.length > 0 ? (
          <span className="truncate text-[10px] text-fg-subtle" title={spark.name}>
            {spark.name}
          </span>
        ) : null}
      </h3>

      <div className="flex min-w-0 items-baseline justify-between gap-1">
        <Price value={spark.price} className="text-[13px] leading-none" />
        <Delta value={spark.changePct} className="text-[11px]" />
      </div>

      <div className="flex min-w-0 items-baseline justify-between gap-1 text-[10px]">
        <span className="text-fg-subtle">Vol</span>
        <Volume value={spark.volume} className="text-[10px]" />
      </div>

      <Sparkline
        closes={spark.closes}
        symbol={spark.symbol}
        price={spark.price}
        changePct={spark.changePct}
        height={20}
      />
    </div>
  );
}
