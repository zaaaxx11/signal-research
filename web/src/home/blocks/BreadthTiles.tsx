/**
 * Six breadth tiles: two directions, two extremes, and the 50-day split.
 *
 * The tiles are counts, never shares of a bar: "1,557 advancing" is a fact, and
 * a 27.9 % bar beside it is the same fact drawn twice. The universe is printed
 * once under the tiles so every count has a denominator, and a tile whose value
 * the response did not carry prints an em dash with a title instead of a zero —
 * "no new highs" and "we did not measure new highs" are different sentences.
 */

import type { BlockState, HomeBreadth } from "../home";
import { BLOCK_HEIGHT, HomeBlock, SkeletonTiles } from "./HomeBlock";
import { Count } from "./Values";

// Auto-fit, not a fixed count: six tiles on a wide panel, three on a tablet,
// two on a phone, and every tile the same width because the row divides itself.
const COLUMNS = "grid-cols-[repeat(auto-fit,minmax(128px,1fr))]";

interface Tile {
  readonly key: keyof HomeBreadth;
  readonly label: string;
}

const TILES: readonly Tile[] = [
  { key: "advancing", label: "Advancing" },
  { key: "declining", label: "Declining" },
  { key: "newHighs", label: "New highs" },
  { key: "newLows", label: "New lows" },
  { key: "above50", label: "Above 50-day" },
  { key: "below50", label: "Below 50-day" },
];

export interface BreadthTilesProps {
  readonly state: BlockState;
  readonly breadth: HomeBreadth | null;
  readonly className?: string;
}

export function BreadthTiles({ state, breadth, className }: BreadthTilesProps) {
  return (
    <HomeBlock
      id="breadth"
      title="Breadth"
      className={className}
      state={state}
      minHeight={BLOCK_HEIGHT.breadth}
      skeleton={<SkeletonTiles count={6} columns={COLUMNS} height={36} testId="breadth-skeleton" />}
    >
      {breadth === null ? null : (
        <>
          <dl className={`grid gap-2 px-2 pt-1.5 ${COLUMNS}`}>
            {TILES.map((tile) => (
              <div
                key={tile.key}
                data-testid={`breadth-tile-${tile.key}`}
                className="flex min-w-0 flex-col-reverse rounded-sm border border-line bg-surface-sunken px-1.5 py-1"
              >
                <dt className="mt-0.5 truncate text-[10px] uppercase tracking-[0.05em] text-fg-subtle">
                  {tile.label}
                </dt>
                <dd className="leading-none">
                  <Count
                    value={breadth[tile.key]}
                    missing={tile.label.toLowerCase()}
                    className="text-[15px]"
                  />
                </dd>
              </div>
            ))}
          </dl>

          <p className="mt-1 px-2 pb-1 text-[10px] text-fg-subtle">
            <Count value={breadth.universe} missing="universe" className="text-[10px]" /> names in
            the universe
            {breadth.unchanged === null ? null : (
              <>
                {" · "}
                <Count value={breadth.unchanged} missing="unchanged" className="text-[10px]" />{" "}
                unchanged
              </>
            )}
          </p>
        </>
      )}
    </HomeBlock>
  );
}
