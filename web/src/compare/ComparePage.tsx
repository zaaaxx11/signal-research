/**
 * `/app/compare?symbols=AAPL,MSFT`: companies side by side, metrics as rows.
 *
 * The page is a **view of a URL**, not a dialog with local state. The list lives
 * in the address bar, every add and remove rewrites it, and the page derives its
 * columns from it — so a reload, a shared link, and the browser's back button
 * all land on the same comparison. That is why removal is a `replaceState`: ten
 * companies would otherwise be ten history entries, and "back" must leave the
 * comparison rather than rewind one column.
 *
 * States are the read's own, and none of them is a blank page:
 *
 * * **fewer than two** — a friendly pointer back to the screener, and no
 *   request at all (a request for nothing is a page that looks broken);
 * * **loading** — a skeleton of the table's shape, never a row of em dashes
 *   pretending the data arrived;
 * * **error** — the API's own message and a retry, because the comparison is a
 *   read and a read can fail;
 * * **ready** — the table, plus an honest line for any ticker the API did not
 *   return.
 */

import * as React from "react";

import { Link, navigate, useSearch } from "@/shell/routes";

import { AddCompany } from "./AddCompany";
import { CompareTable } from "./CompareTable";
import { formatAsOf } from "./format";
import { COMPARE_METRICS } from "./metrics";
import {
  MAX_COMPARE,
  MIN_COMPARE,
  addSymbol,
  compareHref,
  isComparable,
  removeSymbol,
  symbolsFromSearch,
} from "./symbols";
import { useCompare } from "./useCompare";

/** The undo record: the company removed, and the list it was taken from. */
interface Removal {
  readonly symbol: string;
  readonly symbols: readonly string[];
}

function Skeleton() {
  return (
    <div
      data-testid="compare-skeleton"
      role="status"
      className="rounded-md border border-line bg-surface px-2 py-2"
    >
      <span className="sr-only">Loading the comparison</span>
      <div aria-hidden="true" className="flex flex-col gap-1.5">
        <div className="flex gap-1.5">
          <span className="h-8 w-[9.5rem] shrink-0 rounded-[2px] bg-surface-raised" />
          <span className="h-8 w-[10.5rem] shrink-0 rounded-[2px] bg-surface-raised" />
          <span className="h-8 w-[10.5rem] shrink-0 rounded-[2px] bg-surface-raised" />
        </div>
        {Array.from({ length: 9 }, (_, index) => (
          <span
            key={index}
            className="h-[18px] w-full rounded-[1px] bg-surface-raised/70"
          />
        ))}
      </div>
    </div>
  );
}

export function ComparePage() {
  const search = useSearch();
  const symbols = React.useMemo(() => symbolsFromSearch(search), [search]);
  const compare = useCompare(symbols);
  const [removal, setRemoval] = React.useState<Removal | null>(null);

  /** Write a list to the URL, and only when it actually differs. */
  const write = React.useCallback(
    (next: readonly string[]) => {
      const href = compareHref(next);
      if (href !== compareHref(symbols)) navigate(href, { replace: true });
    },
    [symbols],
  );

  const onRemove = React.useCallback(
    (symbol: string) => {
      setRemoval({ symbol, symbols });
      write(removeSymbol(symbols, symbol));
    },
    [symbols, write],
  );

  const onAdd = React.useCallback(
    (symbol: string) => {
      const next = addSymbol(symbols, symbol);
      if (next.length === symbols.length) return;
      setRemoval(null);
      write(next);
    },
    [symbols, write],
  );

  const undo = React.useCallback(() => {
    if (removal === null) return;
    const restore = removal.symbols;
    setRemoval(null);
    write(restore);
  }, [removal, write]);

  const comparable = isComparable(symbols);
  const rowsLoaded = compare.companies.some((company) => company.row !== null);
  const loading = compare.refreshing && !rowsLoaded;
  const asOf = formatAsOf(compare.asOf);

  /*
   * The table is wider than the viewport by design; its frame owns the
   * horizontal scroll. Clipping the page here keeps that frame the only thing
   * that pans, so a stray absolutely-positioned cell cannot widen the document
   * (which is exactly how the sr-only "best" marker once made the page itself
   * scroll 285 px sideways at 700 px).
   */
  return (
    <main
      data-testid="compare-page"
      className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden bg-bg px-2 py-2 max-[900px]:px-1.5"
    >
      <header className="mb-1.5 flex flex-wrap items-end justify-between gap-2 px-0.5 max-[900px]:flex-col max-[900px]:items-stretch">
        <div className="min-w-0">
          <h1 className="text-ui font-medium text-fg">Compare</h1>
          <p className="mt-0.5 text-data text-fg-muted">
            {comparable
              ? `${symbols.length} of ${MAX_COMPARE} companies · ${COMPARE_METRICS.length} metrics as rows`
              : `Pick ${MIN_COMPARE}–${MAX_COMPARE} companies to compare them metric by metric.`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {compare.refreshing && rowsLoaded ? (
            <span role="status" className="text-label text-fg-subtle">
              Updating…
            </span>
          ) : null}
          <AddCompany symbols={symbols} onAdd={onAdd} />
        </div>
      </header>

      {removal !== null ? (
        <div
          data-testid="compare-undo"
          className="mb-1.5 flex flex-wrap items-center justify-between gap-2 rounded-sm border border-line bg-surface-raised px-2 py-1 text-data text-fg-muted"
        >
          <span role="status" className="min-w-0">
            <span className="font-mono font-semibold text-fg">{removal.symbol}</span> removed from
            the comparison.
          </span>
          <button
            type="button"
            onClick={undo}
            className="rounded-sm border border-line px-2 py-0.5 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:border-line-strong hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus"
          >
            Undo
          </button>
        </div>
      ) : null}

      {!comparable ? (
        <section
          data-testid="compare-empty"
          className="rounded-md border border-line bg-surface px-4 py-6 max-[900px]:px-3"
        >
          <h2 className="text-ui font-medium text-fg">Pick companies in the screener</h2>
          <p className="mt-1 max-w-[56ch] text-data text-fg-muted">
            A comparison is built from a selection: filter the market, tick two or more companies,
            then choose Compare. This page reads the list from its address, so the comparison can
            be bookmarked and shared.
          </p>
          <p className="mt-1 max-w-[56ch] text-data text-fg-subtle">
            {symbols.length === 1
              ? `Only ${symbols[0]} is selected — one more makes a comparison.`
              : "Up to ten companies fit side by side."}
          </p>
          <Link
            to="/screener"
            data-testid="compare-empty-link"
            className="focus-ring mt-3 inline-flex h-7 items-center gap-1 rounded-sm border border-line-control px-2.5 text-data text-fg transition-colors hover:border-line-strong hover:text-fg"
          >
            Open the screener
          </Link>
        </section>
      ) : loading ? (
        <Skeleton />
      ) : compare.status === "error" ? (
        <div
          data-testid="compare-error"
          role="alert"
          className="flex flex-wrap items-start justify-between gap-3 rounded-md border border-line bg-surface px-3 py-2"
        >
          <div className="min-w-0">
            <p className="text-data text-fg">The comparison did not load.</p>
            <p className="mt-0.5 text-data text-fg-muted">{compare.error}</p>
          </div>
          <button
            type="button"
            onClick={compare.refresh}
            className="shrink-0 rounded-sm border border-line px-2 py-0.5 text-data text-fg-muted transition-colors hover:border-line-strong hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus"
          >
            Retry
          </button>
        </div>
      ) : (
        <>
          {rowsLoaded ? (
            <CompareTable companies={compare.companies} onRemove={onRemove} />
          ) : (
            <div
              data-testid="compare-no-rows"
              className="rounded-md border border-line bg-surface px-3 py-2"
            >
              <p className="text-data text-fg">No rows came back for this comparison.</p>
              <p className="mt-0.5 text-data text-fg-muted">
                {symbols.join(", ")} — the screener returned none of these symbols. Remove one, or
                search for its ticker.
              </p>
            </div>
          )}

          {compare.missing.length > 0 ? (
            <p data-testid="compare-missing" className="mt-1 px-0.5 text-label text-fg-subtle">
              No row for {compare.missing.join(", ")} — the column stays, the values are dashes.
            </p>
          ) : null}

          {asOf !== null ? (
            <p className="mt-1 px-0.5 text-label text-fg-subtle">Priced as of {asOf}.</p>
          ) : null}
        </>
      )}
    </main>
  );
}
