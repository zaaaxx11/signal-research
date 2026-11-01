/**
 * The strip: five index cards and three ticker sparks in one row of eight.
 *
 * Two documents feed it — `indices` and `sparks` — and either can fail on its
 * own, so the card carries two states rather than one. A failed half prints its
 * own reason in the grid, in the place its tiles would have been; the other half
 * still draws. That is the difference between a dashboard with a hole and a
 * dashboard that disappeared.
 *
 * The row is 2-up on a phone, 4-up on a tablet and 8-up from 1100 px, which is
 * exactly the payload's eight tiles: nothing wraps, nothing is hidden, and the
 * reserved height is the same before and after the answer lands.
 */

import type { BlockState, HomeIndex, HomeSpark } from "../home";
import { BLOCK_HEIGHT, BlockMessage, Card, SkeletonTiles } from "./HomeBlock";
import { Delta, Price } from "./Values";
import { SparkCard } from "./SparkCard";
import { Sparkline } from "./Sparkline";

const COLUMNS = "grid-cols-2 min-[700px]:grid-cols-4 min-[1100px]:grid-cols-8";
const MESSAGE_SPAN = "col-span-2 min-[700px]:col-span-4 min-[1100px]:col-span-8";

export interface IndexStripProps {
  readonly indicesState: BlockState;
  readonly indices: readonly HomeIndex[] | null;
  readonly sparksState: BlockState;
  readonly sparks: readonly HomeSpark[] | null;
  readonly className?: string;
}

function IndexTile({ index }: { index: HomeIndex }) {
  return (
    <div
      data-testid={`index-card-${index.symbol}`}
      className="flex min-w-0 flex-col gap-1 rounded-sm border border-line bg-surface-sunken p-1.5"
    >
      <h3 className="truncate text-[10px] uppercase tracking-[0.05em] text-fg-subtle" title={index.label}>
        {index.label}
      </h3>
      <div className="flex min-w-0 items-baseline justify-between gap-1">
        <Price value={index.price} className="text-[13px] leading-none" />
        <Delta value={index.changePct} missing={`${index.label} change`} className="text-[11px]" />
      </div>
      <Sparkline
        closes={index.closes}
        symbol={index.symbol}
        price={index.price}
        changePct={index.changePct}
        height={18}
      />
    </div>
  );
}

export function IndexStrip({
  indicesState,
  indices,
  sparksState,
  sparks,
  className,
}: IndexStripProps) {
  const loading = indicesState.kind === "loading" || sparksState.kind === "loading";
  const errored = indicesState.kind === "error" || sparksState.kind === "error";

  return (
    <Card
      id="strip"
      title="Market strip"
      className={className}
      minHeight={BLOCK_HEIGHT.strip}
      actions={<span className="shrink-0 text-[10px] text-fg-subtle">Delayed</span>}
    >
      {loading ? (
        <SkeletonTiles count={8} columns={COLUMNS} height={72} testId="strip-skeleton" />
      ) : errored ? (
        <BlockMessage
          id="strip"
          failed
          reason={
            indicesState.kind === "error" ? indicesState.reason ?? "Unavailable." : sparksState.reason ?? "Unavailable."
          }
        />
      ) : (
        <div className={`grid gap-2 p-2 ${COLUMNS}`}>
          {indices === null ? (
            <p
              data-testid="indices-reason"
              title={indicesState.reason ?? undefined}
              className={`${MESSAGE_SPAN} text-[11px] text-fg-subtle`}
            >
              {indicesState.reason}
            </p>
          ) : indices.length === 0 ? (
            <p className={`${MESSAGE_SPAN} text-[11px] text-fg-subtle`}>
              No index quotes in this response.
            </p>
          ) : (
            indices.map((index) => <IndexTile key={index.symbol} index={index} />)
          )}

          {sparks === null ? (
            <p
              data-testid="sparks-reason"
              title={sparksState.reason ?? undefined}
              className={`${MESSAGE_SPAN} text-[11px] text-fg-subtle`}
            >
              {sparksState.reason}
            </p>
          ) : sparks.length === 0 ? (
            <p className={`${MESSAGE_SPAN} text-[11px] text-fg-subtle`}>
              No ticker sparks in this response.
            </p>
          ) : (
            sparks.map((spark) => <SparkCard key={spark.symbol} spark={spark} />)
          )}
        </div>
      )}
    </Card>
  );
}
