/**
 * Six breadth tiles: who is buying, who is selling, and where the extremes are.
 *
 *   Advancing / Declining   companies up and down today
 *   New highs / New lows    companies at their 52-week extreme
 *   Up volume / Down volume shares traded in the names that rose / fell
 *
 * The volume pair is what makes the count pair honest: 1,500 names rising on
 * thin volume and 900 falling on heavy volume is a selling market, and the
 * counts alone would say the opposite. Both use the same share formatter as
 * every other volume in the product.
 *
 * The tiles are counts, never shares of a bar: "1,557 advancing" is a fact, and
 * a 27.9 % bar beside it is the same fact drawn twice. The universe is printed
 * once under the tiles so every count has a denominator, and a tile whose value
 * the response did not carry prints an em dash with a title instead of a zero —
 * "no new highs" and "we did not measure new highs" are different sentences.
 */

import type { BlockState, HomeBreadth } from "../home";
import { BLOCK_HEIGHT, HomeBlock, SkeletonTiles } from "./HomeBlock";
import { Count, Volume } from "./Values";

// Auto-fit, not a fixed count: six tiles on a wide panel, three on a tablet,
// two on a phone, and every tile the same width because the row divides itself.
const COLUMNS = "grid-cols-[repeat(auto-fit,minmax(128px,1fr))]";

interface Tile {
  readonly key: keyof HomeBreadth;
  readonly label: string;
  readonly kind: "count" | "volume";
}

const TILES: readonly Tile[] = [
  { key: "advancing", label: "Advancing", kind: "count" },
  { key: "declining", label: "Declining", kind: "count" },
  { key: "newHighs", label: "New highs", kind: "count" },
  { key: "newLows", label: "New lows", kind: "count" },
  { key: "upVolume", label: "Up volume", kind: "volume" },
  { key: "downVolume", label: "Down volume", kind: "volume" },
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
                  {tile.kind === "volume" ? (
                    <Volume
                      value={breadth[tile.key]}
                      missing={tile.label.toLowerCase()}
                      className="text-[15px]"
                    />
                  ) : (
                    <Count
                      value={breadth[tile.key]}
                      missing={tile.label.toLowerCase()}
                      className="text-[15px]"
                    />
                  )}
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
