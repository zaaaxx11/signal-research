/**
 * Signals: the four screens Home can answer, as counts and their strongest name.
 *
 * This is the compact reading of the same document the movers table draws in
 * full — `Top gainers 12 · NVDA +8.21%` — so a reader can see *how many* names
 * each screen found without scrolling forty rows. A screen with no names shows
 * `0` and an em dash, not a blank line: zero is an answer here, and the count is
 * the number the server actually returned.
 */

import { Link } from "@/shell/routes";

import { MOVER_GROUPS, type BlockState, type HomeMovers } from "../home";
import { BLOCK_HEIGHT, HomeBlock, SkeletonRows } from "./HomeBlock";
import { Delta } from "./Values";
import { moverRows } from "./MoversBlock";

export interface SignalsBlockProps {
  readonly state: BlockState;
  readonly movers: HomeMovers | null;
  readonly className?: string;
}

export function SignalsBlock({ state, movers, className }: SignalsBlockProps) {
  const rows = moverRows(movers);

  return (
    <HomeBlock
      id="signals"
      title="Signals"
      hideTitle
      className={className}
      state={state}
      minHeight={BLOCK_HEIGHT.column}
      skeleton={<SkeletonRows count={4} testId="signals-skeleton" />}
    >
      <ul className="flex min-w-0 flex-col">
        {MOVER_GROUPS.map((group) => {
          const members = rows.filter((row) => row.group === group.chip);
          const leader = members[0] ?? null;
          return (
            <li
              key={group.key}
              data-signal={group.key}
              className="flex h-6 min-w-0 items-center justify-between gap-2 border-b border-line/60 px-2 last:border-b-0"
            >
              <span className="truncate text-[11px] text-fg-muted">{group.label}</span>
              <span className="flex shrink-0 items-baseline gap-2">
                <span className="nums text-[11px] text-fg" data-count={members.length}>
                  {members.length}
                </span>
                {leader === null ? (
                  <span className="text-[11px] text-fg-subtle" title={`No ${group.label.toLowerCase()} in this response`}>
                    —
                  </span>
                ) : (
                  <Link
                    to={`/stock/${encodeURIComponent(leader.symbol)}`}
                    className="font-mono text-[11px] font-semibold text-accent-text hover:underline"
                  >
                    {leader.symbol}
                  </Link>
                )}
                {leader === null ? null : <Delta value={leader.changePct} className="text-[10px]" />}
              </span>
            </li>
          );
        })}
      </ul>

    </HomeBlock>
  );
}
