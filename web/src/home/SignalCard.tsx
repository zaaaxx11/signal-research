/**
 * One signal card: a header, five columns, up to twenty rows.
 *
 * The card reserves its whole height before the answer arrives. Home is two
 * columns of thirteen cards, and a page whose cards grow row by row as the
 * request lands moves everything below them — the reader's eye, the scroll
 * position, the card they were about to click. So the skeleton is the same box
 * as the table: `CARD_HEIGHT` includes the header, the column row and twenty
 * 20 px rows whether or not there are rows to draw.
 *
 * A group with no source (Upgrades, Downgrades) keeps the same box and spends
 * it on one sentence. An empty group says so in one row rather than drawing an
 * empty table with no explanation.
 */

import { LogoMark } from "@/grid/LogoMark";
import { Link } from "@/shell/routes";
import { cn } from "@/lib/utils";

import {
  SIGNAL_LIMIT,
  changeTone,
  formatChange,
  formatPrice,
  formatVolume,
  type SignalGroupDefinition,
  type SignalRow,
} from "./signals";

/**
 * The measured height of a full card: header 26 + column row 20 + 20 rows × 20,
 * plus 3 px of borders (2 for the card's own edge, 1 for the collapsed rule
 * under the header). Measured in Chromium at 1280 px; the skeleton draws the
 * same 20 rows, so a card with fewer names keeps the same box instead of
 * shrinking when its answer lands.
 */
export const CARD_HEIGHT = 26 + 20 + SIGNAL_LIMIT * 20 + 3;

const TONE_CLASS = {
  up: "text-up",
  down: "text-down",
  flat: "text-flat",
} as const;

export interface SignalCardProps {
  group: SignalGroupDefinition;
  rows: readonly SignalRow[];
  loading: boolean;
  errored: boolean;
  /** The server's reason for an empty group, shown as a title on the row. */
  note?: string | null;
}

function SkeletonRows() {
  return (
    <tbody aria-hidden="true" data-testid="signal-skeleton">
      {Array.from({ length: SIGNAL_LIMIT }, (_, index) => (
        <tr key={index} className="h-5">
          <td colSpan={5} className="px-2">
            <span
              className={cn(
                "block h-[9px] rounded-[1px] bg-surface-raised",
                index % 3 === 0 ? "w-[72%]" : index % 3 === 1 ? "w-[86%]" : "w-[64%]",
              )}
            />
          </td>
        </tr>
      ))}
    </tbody>
  );
}

export function SignalCard({ group, rows, loading, errored, note }: SignalCardProps) {
  const shown = rows.slice(0, SIGNAL_LIMIT);

  return (
    <section
      data-testid={`signal-card-${group.key}`}
      data-group={group.key}
      data-available={group.available ? "true" : "false"}
      aria-busy={group.available && loading ? "true" : undefined}
      style={{ minHeight: CARD_HEIGHT }}
      className="flex min-w-0 flex-col overflow-hidden rounded-md border border-line bg-surface"
    >
      <header className="flex h-[26px] shrink-0 items-center justify-between border-b border-line-strong px-2">
        <h2 className="truncate text-[12px] font-medium text-fg">{group.label}</h2>
      </header>

      {!group.available ? (
        <p className="px-2 py-1.5 text-[11px] text-fg-subtle" data-testid={`signal-reason-${group.key}`}>
          {group.reason}
        </p>
      ) : (
        <table className="w-full table-fixed border-collapse text-[11px]">
          <colgroup>
            <col className="w-[34%]" />
            <col className="w-[16%]" />
            <col className="w-[17%]" />
            <col className="w-[15%]" />
            <col className="w-[18%]" />
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
                Change %
              </th>
              <th scope="col" className="px-2 text-right font-medium">
                Volume
              </th>
              <th scope="col" className="px-2 text-left font-medium">
                Signal
              </th>
            </tr>
          </thead>

          {loading ? (
            <SkeletonRows />
          ) : shown.length === 0 ? (
            <tbody>
              <tr>
                <td
                  colSpan={5}
                  title={note ?? undefined}
                  className="px-2 py-1.5 text-[11px] text-fg-subtle"
                >
                  {errored ? "Signals unavailable." : "No names in this signal right now."}
                </td>
              </tr>
            </tbody>
          ) : (
            <tbody>
              {shown.map((row) => (
                <tr
                  key={row.symbol}
                  data-signal-row={row.symbol}
                  className="h-5 border-b border-line/60 odd:bg-row-alt"
                >
                  <td className="min-w-0 px-2">
                    <Link
                      to={`/stock/${encodeURIComponent(row.symbol)}`}
                      title={row.name.length > 0 ? `${row.symbol} — ${row.name}` : row.symbol}
                      className="flex min-w-0 items-center gap-1.5 text-fg hover:text-accent-text"
                    >
                      <LogoMark symbol={row.symbol} size={16} />
                      <span className="truncate font-mono text-[11px] font-semibold">
                        {row.symbol}
                      </span>
                    </Link>
                  </td>
                  <td className="nums px-2 text-right text-fg">{formatPrice(row.price)}</td>
                  <td
                    className={cn(
                      "nums px-2 text-right",
                      TONE_CLASS[changeTone(row.changePct)],
                    )}
                  >
                    {formatChange(row.changePct)}
                  </td>
                  <td className="nums px-2 text-right text-fg-muted">
                    {formatVolume(row.volume)}
                  </td>
                  <td className="truncate px-2 text-fg-subtle">{group.signal}</td>
                </tr>
              ))}
            </tbody>
          )}
        </table>
      )}
    </section>
  );
}
