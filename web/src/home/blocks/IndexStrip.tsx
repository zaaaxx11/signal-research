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
 *
 * **The index tiles now carry bars.** Each of the five gets the last 30 closes
 * as a 36 px bar chart — the same `toBars` adapter and the same `ChartBars`
 * component the charts page and the stock page use, so the strip cannot drift
 * from them. Two costs are paid deliberately:
 *
 *   * the library is behind `ChartBarsLazy`'s `import()`, so Home's first
 *     paint never downloads it (the initial budget is a gate, not a wish);
 *   * a chart is only mounted for a tile that is actually on screen. The strip
 *     scrolls sideways, and a tile two screens to the right is a canvas nobody
 *     is looking at. `useChartVisible` mounts it the first time it intersects
 *     and never unmounts it (re-mounting on scroll-back would re-download the
 *     chunk's work for no new information). In jsdom, which has no
 *     `IntersectionObserver`, every tile is visible — the honest fallback.
 *
 * The spark cards are not index tiles: they keep the lightweight canvas
 * sparkline, because eight charts in one row is eight renderers for one idea.
 */

import * as React from "react";

import { toBars, type ChartBar } from "@/charts/bars";
import { ChartBarsLazy } from "@/charts/lazy";
import { formatPrice } from "@/home/signals";

import type { BlockState, HomeIndex, HomeSpark } from "../home";
import { BLOCK_HEIGHT, BlockMessage, Card, SkeletonTiles } from "./HomeBlock";
import { Delta, Price } from "./Values";
import { SparkCard } from "./SparkCard";

const COLUMNS = "grid-cols-2 min-[700px]:grid-cols-4 min-[1100px]:grid-cols-8";

/**
 * The strip scrolls sideways.
 *
 * Eight tiles at a comfortable width do not fit a 900 px window, and the two
 * alternatives are worse: shrinking them turns a sparkline into a line, and
 * wrapping them into a second row makes the strip claim two rows of the page's
 * height for one idea. The tiles keep their width, the row scrolls, and the
 * scroll snaps to a tile so a wheel or a swipe lands on a card rather than
 * between two.
 */
const TILE_WIDTH = "w-[148px] shrink-0 snap-start";

/** The chart's height in the tile, and the box reserved before it arrives. */
const TILE_CHART_HEIGHT = 36;

/** How many closes the tile plots: the last month of sessions, oldest first. */
const TILE_CLOSES = 30;

/**
 * Has this box ever been on screen?
 *
 * `rootMargin` is vertical only: the strip's own horizontal clipping is what
 * decides a tile's intersection, and a margin to the left would mount the two
 * tiles the reader just scrolled past. `once` is the point — a chart that
 * unmounted on scroll-away would throw its canvas away and build it again.
 */
function useChartVisible(ref: React.RefObject<HTMLDivElement | null>): boolean {
  const [visible, setVisible] = React.useState(() => typeof IntersectionObserver === "undefined");

  React.useEffect(() => {
    const node = ref.current;
    if (node === null || visible || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        setVisible(true);
        observer.disconnect();
      },
      { rootMargin: "120px 0px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [ref, visible]);

  return visible;
}

/** The sentence the canvas cannot say: what the bars are and where they end. */
export function indexBarsLabel(index: HomeIndex, bars: readonly ChartBar[]): string {
  const first = bars[0];
  const last = bars[bars.length - 1];
  if (first === undefined || last === undefined) return `${index.label}: no closes to draw`;
  const count = `${bars.length} ${bars.length === 1 ? "close" : "closes"}`;
  return `${index.label}: ${count}, ${formatPrice(first.close)} to ${formatPrice(last.close)}`;
}

export interface IndexStripProps {
  readonly indicesState: BlockState;
  readonly indices: readonly HomeIndex[] | null;
  readonly sparksState: BlockState;
  readonly sparks: readonly HomeSpark[] | null;
  readonly className?: string;
}

function IndexTile({ index }: { index: HomeIndex }) {
  const chartRef = React.useRef<HTMLDivElement>(null);
  const visible = useChartVisible(chartRef);
  const bars = React.useMemo(
    () => toBars({ closes: index.closes.slice(-TILE_CLOSES) }),
    [index.closes],
  );

  return (
    <div
      data-testid={`index-card-${index.symbol}`}
      className={`${TILE_WIDTH} flex min-w-0 flex-col gap-1 rounded-sm border border-line bg-surface-sunken p-1.5`}
    >
      <h3 className="truncate text-[10px] uppercase tracking-[0.05em] text-fg-subtle" title={index.label}>
        {index.label}
      </h3>
      <div className="flex min-w-0 items-baseline justify-between gap-1">
        <Price value={index.price} className="text-[13px] leading-none" />
        <Delta value={index.changePct} missing={`${index.label} change`} className="text-[11px]" />
      </div>
      <div ref={chartRef} style={{ height: TILE_CHART_HEIGHT }} className="w-full min-w-0">
        {visible ? (
          <ChartBarsLazy
            bars={bars}
            height={TILE_CHART_HEIGHT}
            label={indexBarsLabel(index, bars)}
          />
        ) : (
          // A tile the reader has not scrolled to yet: the same box, no canvas.
          <span aria-hidden="true" className="block h-full w-full" />
        )}
      </div>
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
        <SkeletonTiles count={8} columns={COLUMNS} height={90} testId="strip-skeleton" />
      ) : errored ? (
        <BlockMessage
          id="strip"
          failed
          reason={
            indicesState.kind === "error" ? indicesState.reason ?? "Unavailable." : sparksState.reason ?? "Unavailable."
          }
        />
      ) : (
        <div
          data-testid="strip-scroll"
          className="flex snap-x snap-mandatory gap-2 overflow-x-auto p-2 [scrollbar-width:thin] [&::-webkit-scrollbar]:h-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-line-strong"
        >
          {indices === null ? (
            <p
              data-testid="indices-reason"
              title={indicesState.reason ?? undefined}
              className="min-w-[220px] text-[11px] text-fg-subtle"
            >
              {indicesState.reason}
            </p>
          ) : indices.length === 0 ? (
            <p className="min-w-[220px] text-[11px] text-fg-subtle">
              No index quotes in this response.
            </p>
          ) : (
            indices.map((index) => <IndexTile key={index.symbol} index={index} />)
          )}

          {sparks === null ? (
            <p
              data-testid="sparks-reason"
              title={sparksState.reason ?? undefined}
              className="min-w-[220px] text-[11px] text-fg-subtle"
            >
              {sparksState.reason}
            </p>
          ) : sparks.length === 0 ? (
            <p className="min-w-[220px] text-[11px] text-fg-subtle">
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
