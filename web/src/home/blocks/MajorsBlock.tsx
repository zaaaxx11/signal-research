/**
 * Majors: the three indices moving most today.
 *
 * "Major" is defined by the data, not by a list someone typed: the payload
 * carries five indices and this block sorts them by the absolute change, so the
 * card is a reading of the day ("energy is moving, utilities are not") rather
 * than a second copy of the strip. A tie keeps the wire's order.
 *
 * Each row is the same three facts as an index card — label, last, change — with
 * the arrow and the screen-reader word carried by `Delta`, so the block is usable
 * without its colour.
 */

import type { BlockState, HomeIndex } from "../home";
import { BLOCK_HEIGHT, HomeBlock, SkeletonRows } from "./HomeBlock";
import { Delta, Price } from "./Values";

/** The three largest absolute moves, ties in wire order. */
export function majorIndices(indices: readonly HomeIndex[] | null): HomeIndex[] {
  if (indices === null) return [];
  const ranked = indices
    .map((index, order) => ({ index, order }))
    .sort((left, right) => {
      const a = left.index.changePct === null ? -1 : Math.abs(left.index.changePct);
      const b = right.index.changePct === null ? -1 : Math.abs(right.index.changePct);
      if (a === b) return left.order - right.order;
      return b - a;
    });
  return ranked.slice(0, 3).map((entry) => entry.index);
}

export interface MajorsBlockProps {
  readonly state: BlockState;
  readonly indices: readonly HomeIndex[] | null;
  readonly className?: string;
}

export function MajorsBlock({ state, indices, className }: MajorsBlockProps) {
  const rows = majorIndices(indices);

  return (
    <HomeBlock
      id="majors"
      title="Major indices"
      className={className}
      state={state}
      minHeight={BLOCK_HEIGHT.column}
      skeleton={<SkeletonRows count={3} testId="majors-skeleton" />}
    >
      {rows.length === 0 ? (
        <p className="px-2 py-2 text-[11px] text-fg-subtle">No index quotes in this response.</p>
      ) : (
        <ul className="flex min-w-0 flex-col">
          {rows.map((index) => (
            <li
              key={index.symbol}
              data-major={index.symbol}
              className="flex h-7 min-w-0 items-baseline justify-between gap-2 border-b border-line/60 px-2 last:border-b-0"
            >
              <span className="min-w-0 truncate text-[11px] text-fg-muted" title={index.label}>
                {index.label}
              </span>
              <span className="flex shrink-0 items-baseline gap-2">
                <Price value={index.price} className="text-[11px]" />
                <Delta value={index.changePct} missing={`${index.label} change`} className="text-[11px]" />
              </span>
            </li>
          ))}
        </ul>
      )}
    </HomeBlock>
  );
}
