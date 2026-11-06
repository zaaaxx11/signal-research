/**
 * The insider half of the news pairing: Form 4 rows as links.
 *
 * The desk table (`SideTables`) prints an insider filing as eight columns of
 * a wide table; this half of a 2:1 pair prints the same facts compactly —
 * ticker, owner, the day's side and the size — and makes the two names the
 * reader follows *pointers*:
 *
 * * the ticker goes to the equity detail page (`/stock/{symbol}`), the same
 *   URL every other ticker link on this page builds;
 * * the owner goes to the insider page's own query (`/insider?owner=<NAME>`)
 *   — the page reads `useSearch` and narrows itself to that one person, so a
 *   click here is a filter the reader can already see applied.
 *
 * The rows are the wire's `filings` block: `HomeFiling` (symbol, owner, role,
 * filed) is a narrower document than `HomeSideRow`, so the block also speaks
 * that shape — a field the block does not carry prints as text and text
 * alone.
 *
 * Both links are the shell's `Link`, so a plain click routes in place and a
 * modified click opens a real URL. A row with neither a symbol nor an owner
 * prints as text: a link that lands nowhere is worse than no link.
 */

import { Link } from "@/shell/routes";
import { quoteProps } from "@/lib/quoteAttrs";

import type { BlockState, HomeFiling, HomeSideRow } from "../home";

type FilingRow = HomeFiling | HomeSideRow;

function text(row: FilingRow, key: string): string | null {
  if ("role" in row || "owner" in row) {
    const value = (row as HomeFiling)[key as keyof HomeFiling];
    return typeof value === "string" && value.length > 0 ? value : null;
  }
  const value = (row as HomeSideRow)[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function num(row: FilingRow, key: string): number | null {
  const value = (row as HomeSideRow)[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function money(value: number | null): string {
  if (value === null) return EMPTY;
  return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

const EMPTY = "—";

/** How many rows the half shows: the desk table's order, shorter. */
export const INSIDER_LINK_LIMIT = 8;

export interface InsiderLinksBlockProps {
  readonly rows: readonly FilingRow[] | null;
  readonly state: BlockState;
  readonly className?: string;
}

function Side({ value }: { readonly value: string | null }) {
  const t = value === "Buy" ? "text-up" : value === "Sale" ? "text-down" : "text-fg-muted";
  return <span className={t}>{value ?? EMPTY}</span>;
}

export function InsiderLinksBlock({ rows, state, className }: InsiderLinksBlockProps) {
  const loading = state.kind === "loading";
  const body = (rows ?? []).slice(0, INSIDER_LINK_LIMIT);

  return (
    <div
      data-testid="insider-links"
      data-status={state.kind}
      className={[
        "flex min-w-0 flex-col overflow-hidden rounded-md border border-line bg-surface",
        className ?? "",
      ].join(" ")}
    >
      <div className="min-h-0 flex-1 overflow-y-auto [scrollbar-width:thin]">
        {loading ? (
          <div aria-hidden="true" data-testid="insider-links-skeleton" className="flex flex-col gap-2 p-2 [&>span]:animate-pulse">
            {Array.from({ length: 6 }, (_, index) => (
              <span
                key={index}
                style={{ width: `${84 - (index % 3) * 12}%`, height: 10 }}
                className="block rounded-[1px] bg-surface-raised"
              />
            ))}
          </div>
        ) : state.kind !== "ready" ? (
          <p className="px-2 py-2 text-[11px] text-fg-subtle">
            {state.reason ?? "Insider trading did not load."}
          </p>
        ) : body.length === 0 ? (
          <p className="px-2 py-2 text-[11px] text-fg-subtle">No Form 4 filings readable.</p>
        ) : (
          <table className="w-full table-fixed border-collapse text-[11px]">
            <caption className="sr-only">Latest insider trading, linked</caption>
            <thead>
              <tr className="h-5 border-b border-line text-[10px] uppercase tracking-[0.05em] text-fg-subtle">
                <th scope="col" className="px-2 text-left font-medium">Ticker</th>
                <th scope="col" className="px-2 text-left font-medium">Owner</th>
                <th scope="col" className="px-2 text-left font-medium">Side</th>
                <th scope="col" className="px-2 text-right font-medium">Value($)</th>
              </tr>
            </thead>
            <tbody>
              {body.map((row, index) => {
                const symbol = text(row, "symbol");
                const owner = text(row, "owner");
                return (
                  <tr
                    key={`${symbol ?? "?"}-${owner ?? "?"}-${index}`}
                    data-insider-row={symbol ?? ""}
                    className="h-6 border-b border-line/60 odd:bg-row-alt transition-[opacity] duration-150 hover:bg-row-hover"
                  >
                    <td className="px-2">
                      {symbol !== null ? (
                        <Link
                          to={`/stock/${encodeURIComponent(symbol)}`}
                          title={owner ? `${symbol} — ${owner}` : symbol}
                          className="flex min-w-0 items-center font-mono font-semibold text-fg hover:text-accent-text"
                          {...quoteProps({
                            symbol,
                            name: owner ?? symbol,
                            price: num(row, "cost"),
                            changePct: null,
                            kind: "stock",
                            note: text(row, "transaction") ?? undefined,
                          })}
                        >
                          {symbol}
                        </Link>
                      ) : (
                        <span className="text-fg-subtle">{EMPTY}</span>
                      )}
                    </td>
                    <td className="px-2">
                      {owner !== null ? (
                        <Link
                          to={`/insider?owner=${encodeURIComponent(owner)}`}
                          title={`${owner}${text(row, "role") ? ` — ${text(row, "role")}` : ""}`}
                          className="block min-w-0 truncate text-fg-muted hover:text-accent-text"
                        >
                          {owner}
                        </Link>
                      ) : (
                        <span className="text-fg-subtle">{EMPTY}</span>
                      )}
                    </td>
                    <td className="px-2">
                      <Side value={text(row, "transaction")} />
                    </td>
                    <td className="px-2 text-right nums">
                      {money(num(row, "value"))}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
