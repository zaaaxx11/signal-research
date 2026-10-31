/**
 * Three answers, one table.
 *
 * The API caps a selection at 40 columns, so the all-columns destination asks
 * for 116 keys in three requests and has to put them back together. The merge
 * rule is the only correct one: **a row is its symbol**. Chunk 2 does not have
 * "the same rows in the same order" as chunk 1 by contract — it happens to
 * today — so position is never used to join, and a symbol that appears in one
 * chunk and not another is a gap to be shown, not a row to be completed with
 * somebody else's numbers.
 *
 * Two failure shapes are distinguished because they read differently:
 *
 * * a **failed chunk** — the request itself raised, so none of its columns
 *   exist for any symbol;
 * * a **gap** — the chunk answered 200 but without a symbol the first chunk
 *   returned (a transient), so that row is missing that chunk's columns.
 *
 * Both are rendered as an absent cell with a reason, and both are said out
 * loud by `mergeNotices` — a half-filled row must never be silent.
 */

import type { ScreenRow } from "@/data/screens";
import { isScreenRow } from "@/data/screens";

/** One chunk's answer, successful or not. `keys` is what it asked for. */
export interface ChunkAnswer {
  /** Zero-based chunk position; the prose counts from 1. */
  index: number;
  keys: string[];
  rows: ScreenRow[];
  /** The transport/HTTP sentence when the request raised, else null. */
  error: string | null;
}

export interface ChunkFailure {
  chunk: number;
  keys: number;
  message: string;
}

export interface ChunkGap {
  chunk: number;
  symbols: string[];
}

export interface AbsenceIndex {
  /** Column key → the chunk that carries it. */
  chunkOf: ReadonlyMap<string, number>;
  /** Chunk → why it failed. */
  failed: ReadonlyMap<number, string>;
  /** Chunk → symbols it did not return (that some chunk did). */
  missing: ReadonlyMap<number, ReadonlySet<string>>;
  total: number;
}

export interface MergedSelection {
  rows: ScreenRow[];
  /** Symbols the first successful chunk returned that another chunk did not. */
  gaps: ChunkGap[];
  failures: ChunkFailure[];
  /** Symbols no first-chunk answer carried (a page that moved under us). */
  partial: string[];
  absence: AbsenceIndex;
}

function isAbsent(error: string | null): boolean {
  return error !== null && error.length > 0;
}

/**
 * Merge by symbol, never by index.
 *
 * The row set is the union of every successful answer, ordered by first
 * appearance (chunk 1's order when it answered). A key is copied only when the
 * chunk that asked for it actually returned it, so "the API did not send this
 * cell" stays distinguishable from "the API sent null".
 */
export function mergeChunks(answers: readonly ChunkAnswer[]): MergedSelection {
  const bySymbol = new Map<string, ScreenRow>();
  const order: string[] = [];
  const successful: Array<{ index: number; symbols: Set<string> }> = [];
  const failures: ChunkFailure[] = [];
  const chunkOf = new Map<string, number>();
  const failed = new Map<number, string>();

  for (const answer of answers) {
    for (const key of answer.keys) chunkOf.set(key, answer.index);

    if (isAbsent(answer.error)) {
      failed.set(answer.index, answer.error ?? "");
      failures.push({ chunk: answer.index, keys: answer.keys.length, message: answer.error ?? "" });
      continue;
    }

    const seen = new Set<string>();
    successful.push({ index: answer.index, symbols: seen });
    for (const row of answer.rows) {
      if (!isScreenRow(row)) continue;
      const symbol = row.symbol;
      seen.add(symbol);

      let target = bySymbol.get(symbol);
      if (!target) {
        target = { symbol };
        bySymbol.set(symbol, target);
        order.push(symbol);
      }
      for (const key of answer.keys) {
        if (key === "symbol") continue;
        if (!Object.prototype.hasOwnProperty.call(row, key)) continue;
        target[key] = row[key];
      }
    }
  }

  // The first chunk that answered is the identity authority: its symbols are
  // the page. Anything it lacked that a later chunk returned is called out as
  // `partial`, because those rows can only ever be part of a row.
  const reference = successful[0]?.symbols ?? new Set<string>();
  const all = new Set(order);
  const gaps: ChunkGap[] = [];
  const missing = new Map<number, ReadonlySet<string>>();

  for (const answer of successful) {
    const absent = new Set<string>();
    for (const symbol of all) {
      if (!answer.symbols.has(symbol)) absent.add(symbol);
    }
    if (absent.size > 0) missing.set(answer.index, absent);

    const gap = [...reference].filter((symbol) => !answer.symbols.has(symbol));
    if (gap.length > 0) gaps.push({ chunk: answer.index, symbols: gap });
  }

  const partial = order.filter((symbol) => !reference.has(symbol));

  return {
    rows: order.map((symbol) => bySymbol.get(symbol) as ScreenRow),
    gaps,
    failures,
    partial,
    absence: { chunkOf, failed, missing, total: answers.length },
  };
}

/**
 * Why a cell is empty, or null when the chunk that owns it answered with it.
 *
 * The reason is what the cell's `title` and the table's announcement carry, so
 * an empty box is never mistaken for a zero or for "not available".
 */
export function absenceReason(index: AbsenceIndex, symbol: string, key: string): string | null {
  const chunk = index.chunkOf.get(key);
  if (chunk === undefined) return "not in any requested chunk";
  const total = index.total;
  const failed = index.failed.get(chunk);
  if (failed !== undefined) return `chunk ${chunk + 1} of ${total} failed`;
  if (index.missing.get(chunk)?.has(symbol)) {
    return `chunk ${chunk + 1} of ${total} answered without ${symbol}`;
  }
  return null;
}

/** How many cells the merge could not fill — the size of the silence. */
export function absentCells(merged: MergedSelection, keys: readonly string[]): number {
  let count = 0;
  for (const row of merged.rows) {
    for (const key of keys) {
      if (absenceReason(merged.absence, row.symbol, key) !== null) count += 1;
    }
  }
  return count;
}

/**
 * The sentences the panel shows above the table. One per failed chunk and one
 * per gap, each naming the chunk — "some columns are missing" is not a report.
 */
export function mergeNotices(merged: MergedSelection): string[] {
  const total = merged.absence.total;
  const notices: string[] = [];

  for (const failure of merged.failures) {
    notices.push(
      `Chunk ${failure.chunk + 1} of ${total} failed (${failure.message}): its ${failure.keys} columns are shown as “—” on every row.`,
    );
  }
  for (const gap of merged.gaps) {
    const who = gap.symbols.slice(0, 3).join(", ");
    const more = gap.symbols.length > 3 ? ` and ${gap.symbols.length - 3} more` : "";
    notices.push(
      `Chunk ${gap.chunk + 1} of ${total} answered without ${gap.symbols.length} symbol${
        gap.symbols.length === 1 ? "" : "s"
      } (${who}${more}): that row's columns from this chunk are shown as “—”.`,
    );
  }
  if (merged.partial.length > 0) {
    notices.push(
      `${merged.partial.length} symbol${merged.partial.length === 1 ? "" : "s"} appeared only in a later chunk (${merged.partial
        .slice(0, 3)
        .join(", ")}${merged.partial.length > 3 ? "…" : ""}): those rows are incomplete, not half-filled.`,
    );
  }
  return notices;
}
