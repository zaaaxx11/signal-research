/**
 * The right column: five tables and a grid, each the shape of its data.
 *
 * These are the blocks a reader checks in passing — the contract, the pair, the
 * listing, the filing — so none of them gets a card title. The first row of the
 * table already names the column ("Futures", "Forex & Bonds"), and a heading
 * above it would say the same word twice. The one block that does carry a title
 * is the map, because "1 day performance" is a fact the tiles cannot state.
 *
 * Empty is a state, not a blank: a table with no source says so in one quiet
 * line, and the economic calendar — which has no source at all — keeps its
 * header and says "No economic releases today", the way a terminal does. A
 * reader can tell a quiet day from a broken feed.
 */

import type { BlockState, HomeSideRow } from "../home";

const EMPTY = "—";

function num(row: HomeSideRow, key: string): number | null {
  const value = row[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function text(row: HomeSideRow, key: string): string | null {
  const value = row[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function money(value: number | null, digits = 2): string {
  if (value === null) return EMPTY;
  return value.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function percent(value: number | null): string {
  if (value === null) return EMPTY;
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function deltaClass(value: number | null): string {
  if (value === null) return "text-fg-subtle";
  return value > 0 ? "text-up" : value < 0 ? "text-down" : "text-fg-muted";
}

function Table({
  children,
  testId,
}: {
  readonly children: React.ReactNode;
  readonly testId: string;
}) {
  return (
    <div data-testid={testId} className="min-w-0 overflow-hidden rounded-sm border border-line">
      <table className="w-full border-collapse text-[11px] tabular-nums">{children}</table>
    </div>
  );
}

function Th({ children, right }: { readonly children: React.ReactNode; readonly right?: boolean }) {
  return (
    <th
      scope="col"
      className={`border-b border-line px-1.5 py-1 text-[10px] font-normal uppercase tracking-[0.04em] text-fg-subtle ${
        right ? "text-right" : "text-left"
      }`}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  right,
  className = "",
}: {
  readonly children: React.ReactNode;
  readonly right?: boolean;
  readonly className?: string;
}) {
  return (
    <td
      className={`truncate border-b border-line/60 px-1.5 py-0.5 ${
        right ? "text-right" : "text-left"
      } ${className}`}
    >
      {children}
    </td>
  );
}

function Empty({ colSpan, children }: { readonly colSpan: number; readonly children: string }) {
  return (
    <tr>
      <td colSpan={colSpan} className="px-1.5 py-1 text-[10px] text-fg-subtle">
        {children}
      </td>
    </tr>
  );
}

/** Futures: name, last, change, change %. */
export function FuturesTable({ rows }: { readonly rows: readonly HomeSideRow[] | null }) {
  const body = (rows ?? []).slice(0, 8);
  return (
    <Table testId="home-futures">
      <thead>
        <tr>
          <Th>Futures</Th>
          <Th right>Last</Th>
          <Th right>Change</Th>
          <Th right>Change %</Th>
        </tr>
      </thead>
      <tbody>
        {body.length === 0 ? (
          <Empty colSpan={4}>No contract prices available.</Empty>
        ) : (
          body.map((row) => {
            const change = num(row, "change_pct");
            return (
              <tr key={text(row, "symbol") ?? Math.random()}>
                <Td>{text(row, "name") ?? text(row, "symbol") ?? EMPTY}</Td>
                <Td right>{money(num(row, "price"))}</Td>
                <Td right className={deltaClass(change)}>
                  {percent(change)}
                </Td>
                <Td right className={deltaClass(change)}>
                  {percent(change)}
                </Td>
              </tr>
            );
          })
        )}
      </tbody>
    </Table>
  );
}

/** Forex & bonds: pair, last, change, change %. */
export function ForexTable({ rows }: { readonly rows: readonly HomeSideRow[] | null }) {
  const body = (rows ?? []).slice(0, 8);
  return (
    <Table testId="home-forex">
      <thead>
        <tr>
          <Th>Forex &amp; Bonds</Th>
          <Th right>Last</Th>
          <Th right>Change</Th>
          <Th right>Change %</Th>
        </tr>
      </thead>
      <tbody>
        {body.length === 0 ? (
          <Empty colSpan={4}>No pair prices available.</Empty>
        ) : (
          body.map((row) => {
            const change = num(row, "change_pct");
            return (
              <tr key={text(row, "symbol") ?? Math.random()}>
                <Td>{text(row, "name") ?? text(row, "symbol") ?? EMPTY}</Td>
                <Td right>{money(num(row, "price"), 4)}</Td>
                <Td right className={deltaClass(change)}>
                  {percent(change)}
                </Td>
                <Td right className={deltaClass(change)}>
                  {percent(change)}
                </Td>
              </tr>
            );
          })
        )}
      </tbody>
    </Table>
  );
}

/** Latest IPOs: a grid of tickers and their first-day-style percentage. */
export function IpoGrid({ rows }: { readonly rows: readonly HomeSideRow[] | null }) {
  const body = (rows ?? []).slice(0, 30);
  return (
    <div data-testid="home-ipos" className="min-w-0 rounded-sm border border-line p-1.5">
      <div className="mb-1 text-[10px] uppercase tracking-[0.04em] text-fg-subtle">Latest IPOs</div>
      {body.length === 0 ? (
        <p className="text-[10px] text-fg-subtle">No recent listings in this scan.</p>
      ) : (
        <ul className="grid grid-cols-2 gap-x-2 gap-y-0.5 sm:grid-cols-3">
          {body.map((row, index) => {
            const change = num(row, "change_pct");
            return (
              <li
                key={`${text(row, "symbol") ?? index}`}
                className="flex items-baseline justify-between gap-1 text-[11px] tabular-nums"
              >
                <span className="truncate text-fg">{text(row, "symbol") ?? EMPTY}</span>
                <span className={deltaClass(change)}>{percent(change)}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/** Latest insider trading: ticker, owner, relationship, date, side, cost, size, value. */
export function InsiderLatestTable({
  rows,
  state,
}: {
  readonly rows: readonly HomeSideRow[] | null;
  readonly state?: BlockState;
}) {
  if (state !== undefined && state.kind !== "ready") {
    return (
      <div
        data-testid="home-insider-latest"
        data-status={state.kind}
        className="min-w-0 rounded-sm border border-line px-2 py-1.5 text-[11px] text-fg-subtle"
      >
        {state.reason ?? "Insider trading did not load."}
      </div>
    );
  }
  const body = (rows ?? []).slice(0, 14);
  return (
    <Table testId="home-insider-latest">
      <thead>
        <tr>
          <Th>Ticker</Th>
          <Th>Latest Insider Trading</Th>
          <Th>Relationship</Th>
          <Th>Date</Th>
          <Th>Transaction</Th>
          <Th right>Cost</Th>
          <Th right>#Shares</Th>
          <Th right>Value($)</Th>
        </tr>
      </thead>
      <tbody>
        {body.length === 0 ? (
          <Empty colSpan={8}>No Form 4 filings readable.</Empty>
        ) : (
          body.map((row, index) => (
            <tr key={`${text(row, "symbol") ?? "?"}-${text(row, "date") ?? index}-${index}`}>
              <Td className="font-medium text-fg">{text(row, "symbol") ?? EMPTY}</Td>
              <Td>{text(row, "owner") ?? EMPTY}</Td>
              <Td className="text-fg-muted">{text(row, "role") ?? EMPTY}</Td>
              <Td className="text-fg-muted">{text(row, "date") ?? EMPTY}</Td>
              <Td
                className={
                  text(row, "transaction") === "Buy"
                    ? "text-up"
                    : text(row, "transaction") === "Sale"
                      ? "text-down"
                      : "text-fg-muted"
                }
              >
                {text(row, "transaction") ?? EMPTY}
              </Td>
              <Td right>{money(num(row, "cost"))}</Td>
              <Td right>{money(num(row, "shares"), 0)}</Td>
              <Td right>{money(num(row, "value"), 0)}</Td>
            </tr>
          ))
        )}
      </tbody>
    </Table>
  );
}

/** Top insider trading: the largest transactions among the names this page read. */
export function InsiderTopTable({ rows }: { readonly rows: readonly HomeSideRow[] | null }) {
  const body = (rows ?? []).slice(0, 6);
  return (
    <Table testId="home-insider-top">
      <thead>
        <tr>
          <Th>Ticker</Th>
          <Th>Top Insider Trading</Th>
          <Th>Date</Th>
          <Th>Transaction</Th>
          <Th right>Value($)</Th>
        </tr>
      </thead>
      <tbody>
        {body.length === 0 ? (
          <Empty colSpan={5}>No Form 4 filings readable.</Empty>
        ) : (
          body.map((row, index) => (
            <tr key={`${text(row, "symbol") ?? "?"}-${index}`}>
              <Td className="font-medium text-fg">{text(row, "symbol") ?? EMPTY}</Td>
              <Td>{text(row, "owner") ?? EMPTY}</Td>
              <Td className="text-fg-muted">{text(row, "date") ?? EMPTY}</Td>
              <Td
                className={
                  text(row, "transaction") === "Buy"
                    ? "text-up"
                    : text(row, "transaction") === "Sale"
                      ? "text-down"
                      : "text-fg-muted"
                }
              >
                {text(row, "transaction") ?? EMPTY}
              </Td>
              <Td right>{money(num(row, "value"), 0)}</Td>
            </tr>
          ))
        )}
      </tbody>
    </Table>
  );
}

/** Economic releases: the header a terminal keeps even on a quiet day. */
export function EconomicTable({ rows }: { readonly rows: readonly HomeSideRow[] | null }) {
  const body = rows ?? [];
  return (
    <Table testId="home-economic">
      <thead>
        <tr>
          <Th>Date</Th>
          <Th>Time</Th>
          <Th>Impact</Th>
          <Th>Release</Th>
          <Th>For</Th>
          <Th right>Actual</Th>
          <Th right>Expected</Th>
          <Th right>Prior</Th>
        </tr>
      </thead>
      <tbody>
        {body.length === 0 ? (
          <Empty colSpan={8}>No economic releases today.</Empty>
        ) : (
          body.map((row, index) => (
            <tr key={`${text(row, "date") ?? index}-${text(row, "release") ?? index}`}>
              <Td>{text(row, "date") ?? EMPTY}</Td>
              <Td className="text-fg-muted">{text(row, "time") ?? EMPTY}</Td>
              <Td>{text(row, "impact") ?? EMPTY}</Td>
              <Td>{text(row, "release") ?? EMPTY}</Td>
              <Td className="text-fg-muted">{text(row, "for") ?? EMPTY}</Td>
              <Td right>{text(row, "actual") ?? EMPTY}</Td>
              <Td right>{text(row, "expected") ?? EMPTY}</Td>
              <Td right>{text(row, "prior") ?? EMPTY}</Td>
            </tr>
          ))
        )}
      </tbody>
    </Table>
  );
}

/** Everything in the narrow column, in the order a reader scans it. */
export function SideColumn({
  futures,
  forex,
  ipos,
  insiderTop,
  economic,
}: {
  readonly futures: readonly HomeSideRow[] | null;
  readonly forex: readonly HomeSideRow[] | null;
  readonly ipos: readonly HomeSideRow[] | null;
  readonly insiderTop: readonly HomeSideRow[] | null;
  readonly economic: readonly HomeSideRow[] | null;
}) {
  return (
    <div data-testid="home-side" className="flex min-w-0 flex-col gap-2">
      <EconomicTable rows={economic} />
      <FuturesTable rows={futures} />
      <ForexTable rows={forex} />
      <IpoGrid rows={ipos} />
      <InsiderTopTable rows={insiderTop} />
    </div>
  );
}
