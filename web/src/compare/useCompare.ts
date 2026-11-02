/**
 * The comparison's one read.
 *
 * Every symbol travels in **one** `POST /screens/select` request: the selection
 * is `{field: "symbol", op: "in", value: [...]}`, a membership predicate the
 * server executes over one scan. Ten lookups would be ten scans for a page whose
 * whole point is that the numbers sit side by side.
 *
 * The response's order is the server's (it ranks by market cap even when a
 * filter names the rows), so the hook re-orders by the URL's list. A company the
 * API did not return keeps its column and is reported as `missing`: a shared
 * link to a delisted ticker must say so rather than silently dropping a column
 * and re-labelling the rest.
 */

import * as React from "react";

import {
  useScreens,
  type ScreenRow,
  type ScreenStatus,
  type ScreensQuery,
} from "@/data/screens";

import { COMPARE_COLUMNS } from "./metrics";
import { MIN_COMPARE } from "./symbols";

/** One column: the symbol the reader asked for, and the row that answered. */
export interface CompareCompany {
  readonly symbol: string;
  /** Null until the read lands, or forever if the API had no such row. */
  readonly row: ScreenRow | null;
}

export interface CompareState {
  readonly companies: readonly CompareCompany[];
  readonly status: ScreenStatus;
  readonly error: string | null;
  /** Symbols the API returned no row for, once the read has landed. */
  readonly missing: readonly string[];
  /** The server's stamp for the rows on screen, for the quiet footer. */
  readonly asOf: string | null;
  /** True while a request for a changed list is in flight. */
  readonly refreshing: boolean;
  readonly refresh: () => void;
}

/**
 * The view label.
 *
 * `requestBody` never sends `view` — it is the client's name for "these rows
 * belong to this question", which is what keys the page cache and keeps one
 * comparison's rows from being drawn under another's header. The seven-column
 * path below is the only one this page uses.
 */
export const COMPARE_VIEW = "compare";

export function useCompare(symbols: readonly string[]): CompareState {
  /** The list as a stable string: the query's identity, not its array's. */
  const key = symbols.join(",");

  const query = React.useMemo<ScreensQuery | null>(() => {
    // Rebuilt from the key, so the query depends on the list and not on the
    // identity of an array a parent re-creates on every render.
    const list = key.length === 0 ? [] : key.split(",");
    if (list.length < MIN_COMPARE) return null;
    return {
      view: COMPARE_VIEW,
      columns: COMPARE_COLUMNS,
      region: "us",
      assetClass: "stock",
      // One row per name, no more: a membership filter answers exactly the
      // symbols asked for.
      limit: list.length,
      filters: [{ field: "symbol", op: "in", value: list }],
    };
  }, [key]);

  const result = useScreens(query);

  const companies = React.useMemo<CompareCompany[]>(() => {
    const bySymbol = new Map(result.rows.map((row) => [row.symbol, row]));
    return symbols.map((symbol) => ({ symbol, row: bySymbol.get(symbol) ?? null }));
  }, [symbols, result.rows]);

  const missing = React.useMemo(
    () =>
      result.status === "ready" || result.status === "empty"
        ? companies.filter((company) => company.row === null).map((company) => company.symbol)
        : [],
    [companies, result.status],
  );

  return {
    companies,
    status: result.status,
    error: result.error?.message ?? null,
    missing,
    asOf: result.meta?.asOf || null,
    refreshing: result.status === "loading",
    refresh: result.refresh,
  };
}
