/**
 * The movers list: up to forty names across four groups, in one table.
 *
 * A table, not a list of divs, because the four things on a row are four columns
 * with a shared header — and because a caption is the only way to say to a
 * screen reader what the numbers are before it starts reading them. The caption
 * is visually hidden (the card's `<h2>` already names the block on screen) and
 * still present in the accessibility tree.
 *
 * The body scrolls inside a fixed 420 px box. At 900 px the block is 8 of 12
 * columns, and forty rows at 20 px would be an 800 px wall that pushes the map
 * and the insider strip off the screen; scrolling keeps the block the same
 * height with 4 rows or 40, so the page cannot move when the payload lands. The
 * table is `table-fixed` with a declared `colgroup`, so a long company name
 * truncates in its own column instead of widening the grid.
 *
 * Rows are sorted by group and then by the number they are about: gainers, new
 * highs and overbought read strongest-first, new lows weakest-first. Within a
 * group the order is deterministic even when two rows share a value, because a
 * list that reshuffles between renders is a list you cannot read.
 */

import { LogoMark } from "@/grid/LogoMark";
import { Link } from "@/shell/routes";

import { MOVER_LIMIT, type BlockState, type HomeMover, type HomeMovers, type MoverKey } from "../home";
import { BLOCK_HEIGHT, BlockMessage, Card, SkeletonRows } from "./HomeBlock";
import { Delta, Price } from "./Values";

/** The order each group is read in: strongest first, except the lows. */
const DESCENDING: Record<MoverKey, boolean> = {
  gainers: true,
  newHigh: true,
  overbought: true,
  newLow: false,
};

function sorted(rows: readonly HomeMover[], descending: boolean): HomeMover[] {
  return [...rows].sort((left, right) => {
    const a = left.changePct;
    const b = right.changePct;
    if (a === null && b === null) return 0;
    if (a === null) return 1;
    if (b === null) return -1;
    if (a === b) return 0;
    return descending ? b - a : a - b;
  });
}

/** Every row of the block, in group order, never above `MOVER_LIMIT`. */
export function moverRows(movers: HomeMovers | null): HomeMover[] {
  if (movers === null) return [];
  const rows: HomeMover[] = [];
  for (const key of ["gainers", "newHigh", "overbought", "newLow"] as const) {
    for (const row of sorted(movers[key], DESCENDING[key])) {
      if (rows.length >= MOVER_LIMIT) return rows;
      rows.push(row);
    }
  }
  return rows;
}

export interface MoversBlockProps {
  readonly state: BlockState;
  readonly movers: HomeMovers | null;
  readonly className?: string;
  /** Drop the card's own 26 px header: the SectionTitle bar names the section. */
  readonly hideTitle?: boolean;
}

export function MoversBlock({ state, movers, className, hideTitle = false }: MoversBlockProps) {
  const rows = moverRows(movers);
  const count = movers?.total ?? 0;

  return (
    <Card
      id="movers"
      title="Movers"
      hideTitle={hideTitle}
      className={className}
      height={BLOCK_HEIGHT.movers}
      actions={
        <div className="flex shrink-0 items-baseline gap-2">
          {state.kind === "ready" && count > 0 ? (
            <span className="text-[10px] text-fg-subtle">{count}</span>
          ) : null}
          <Link
            to="/screener"
            className="text-[10px] text-accent-text hover:underline"
            data-testid="movers-see-all"
          >
            See all
          </Link>
        </div>
      }
    >
      {state.kind === "loading" ? (
        <SkeletonRows count={12} testId="movers-skeleton" />
      ) : state.kind !== "ready" ? (
        <BlockMessage id="movers" reason={state.reason ?? "Unavailable."} failed={state.kind === "error"} />
      ) : rows.length === 0 ? (
        <p className="px-2 py-2 text-[11px] text-fg-subtle">
          No names matched the four mover screens today.
        </p>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <table className="w-full table-fixed border-collapse text-[11px]">
            <caption className="sr-only">
              Movers: top gainers, new highs, overbought and new lows
            </caption>
            <colgroup>
              <col className="w-[44%]" />
              <col className="w-[19%]" />
              <col className="w-[20%]" />
              <col className="w-[17%]" />
            </colgroup>
            <thead>
              <tr className="h-5 border-b border-line text-[10px] uppercase tracking-[0.05em] text-fg-subtle">
                <th scope="col" className="px-2 text-left font-medium">
                  Ticker
                </th>
                <th scope="col" className="px-2 text-right font-medium">
                  Last
                </th>
                <th scope="col" className="px-2 text-right font-medium">
                  Change
                </th>
                <th scope="col" className="px-2 text-left font-medium">
                  Signal
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={`${row.group}:${row.symbol}`}
                  data-mover-row={row.symbol}
                  className="h-5 border-b border-line/60 odd:bg-row-alt"
                >
                  <td className="min-w-0 px-2">
                    <Link
                      to={`/stock/${encodeURIComponent(row.symbol)}`}
                      title={row.name.length > 0 ? `${row.symbol} — ${row.name}` : row.symbol}
                      className="flex min-w-0 items-center gap-1.5 text-fg hover:text-accent-text"
                    >
                      <LogoMark symbol={row.symbol} size={16} />
                      <span className="truncate font-mono text-[11px] font-semibold">{row.symbol}</span>
                    </Link>
                  </td>
                  <td className="px-2 text-right">
                    <Price value={row.price} />
                  </td>
                  <td className="px-2 text-right">
                    <Delta value={row.changePct} className="text-[11px]" />
                  </td>
                  <td className="truncate px-2 text-fg-subtle" title={row.group}>
                    {row.group}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
