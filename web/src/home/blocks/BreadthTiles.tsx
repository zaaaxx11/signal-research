/**
 * Breadth, in the shape the operator reads on Finviz: four opposed pairs, each
 * side carrying its share and its count.
 *
 *   Advancing   25.9% (1,458)    Declining   70.2% (3,944)
 *   New high    26.6% (162)      New low     73.4% (448)
 *   Above SMA50 36.5% (2,047)    Below SMA50 63.5% (3,556)
 *   Above SMA200 39.1% (2,189)   Below SMA200 60.9% (3,414)
 *
 * The share is the count over the denominator that count belongs to: the whole
 * universe for the advancers and the extremes, and the *measured* rows for the
 * two moving-average pairs — a company whose average this deployment never
 * fetched is not "below" it, and inflating a denominator with rows we did not
 * measure would turn a coverage gap into a market signal.
 *
 * There is no card title. The block is a strip of figures; a heading above it
 * would only repeat the word the reader already knows, and the operator asked
 * for the strip without one.
 */

import type { BlockState, HomeBreadth } from "../home";
import { BLOCK_HEIGHT, HomeBlock, SkeletonTiles } from "./HomeBlock";
import { Count } from "./Values";

const COLUMNS = "grid-cols-1 min-[700px]:grid-cols-2";

interface Row {
  readonly label: string;
  readonly value: number | null;
  readonly base: number | null;
  readonly testId: string;
}

/** `25.9%` from a count and its denominator; `—` when either is unknown. */
export function share(value: number | null, base: number | null): string {
  if (value === null || base === null || base <= 0) return "—";
  return `${((value / base) * 100).toFixed(1)}%`;
}

export interface BreadthTilesProps {
  readonly state: BlockState;
  readonly breadth: HomeBreadth | null;
  readonly className?: string;
}

export function BreadthTiles({ state, breadth, className }: BreadthTilesProps) {
  const universe = breadth?.universe ?? null;
  const measured50 =
    breadth?.above50 !== null && breadth?.above50 !== undefined &&
    breadth?.below50 !== null && breadth?.below50 !== undefined
      ? breadth.above50 + breadth.below50
      : null;
  const measured200 =
    breadth?.above200 !== null && breadth?.above200 !== undefined &&
    breadth?.below200 !== null && breadth?.below200 !== undefined
      ? breadth.above200 + breadth.below200
      : null;

  const rows: readonly Row[] = breadth === null
    ? []
    : [
        { label: "Advancing", value: breadth.advancing, base: universe, testId: "advancing" },
        { label: "Declining", value: breadth.declining, base: universe, testId: "declining" },
        { label: "New high", value: breadth.newHighs, base: universe, testId: "newHighs" },
        { label: "New low", value: breadth.newLows, base: universe, testId: "newLows" },
        { label: "Above SMA50", value: breadth.above50, base: measured50, testId: "above50" },
        { label: "Below SMA50", value: breadth.below50, base: measured50, testId: "below50" },
        { label: "Above SMA200", value: breadth.above200, base: measured200, testId: "above200" },
        { label: "Below SMA200", value: breadth.below200, base: measured200, testId: "below200" },
      ];

  return (
    <HomeBlock
      id="breadth"
      title="Breadth"
      hideTitle
      className={className}
      state={state}
      minHeight={BLOCK_HEIGHT.breadth}
      skeleton={<SkeletonTiles count={8} columns={COLUMNS} height={18} testId="breadth-skeleton" />}
    >
      {breadth === null ? null : (
        <>
          <dl className={`grid gap-x-6 gap-y-0.5 px-2 py-1.5 ${COLUMNS}`}>
            {rows.map((row) => (
              <div
                key={row.testId}
                data-testid={`breadth-tile-${row.testId}`}
                className="flex min-w-0 items-baseline justify-between gap-2 border-b border-line/60 py-[3px] last:border-b-0"
              >
                <dt className="min-w-0 truncate text-[11px] text-fg-muted">{row.label}</dt>
                <dd className="flex shrink-0 items-baseline gap-1.5">
                  <span className="nums text-[11px] font-medium text-fg">{share(row.value, row.base)}</span>
                  <span className="nums text-[11px] text-fg-subtle">
                    (
                    <Count
                      value={row.value}
                      missing={row.label.toLowerCase()}
                      className="text-[11px]"
                    />
                    )
                  </span>
                </dd>
              </div>
            ))}
          </dl>
          <p className="px-2 pb-1 text-[10px] text-fg-subtle">
            Shares of the{" "}
            <Count value={universe} missing="universe" className="text-[10px]" /> companies in the
            universe; the SMA pairs count only the rows whose average was measured.
          </p>
        </>
      )}
    </HomeBlock>
  );
}
