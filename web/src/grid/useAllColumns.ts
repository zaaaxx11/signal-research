/**
 * The all-columns fetch: 116 keys, three requests, one row set.
 *
 * The API's `columns` list is capped at 40 (`MAX_COLUMNS`), so this hook asks
 * in chunks and merges by symbol. It is deliberately *not* `useScreens`: that
 * hook owns one query's ETag and its rows, and a three-chunk read has three
 * validators and no single "revalidated" answer to give. What it shares is the
 * transport (`selectScreens`) and the response reader (`readResponse`), so the
 * wire shape is parsed in exactly one place.
 *
 * The requests run in parallel — the three answers are independent — and every
 * one is `POST /screens/select` with `limit: 20, offset: 0` and the same sort,
 * which is what makes their row sets joinable by symbol at all.
 */

import * as React from "react";

import {
  readResponse,
  selectScreens,
  type ScreenMeta,
  type ScreenRow,
  type ScreenSort,
} from "@/data/screens";
import { ApiError } from "@/lib/api";

import { chunkColumns } from "./catalogue";
import { mergeChunks, type ChunkAnswer, type MergedSelection } from "./merge";

export interface AllColumnsOptions {
  enabled?: boolean;
  /** The destination view's served name; identity for the query, not a column. */
  view: string;
  keys: readonly string[];
  limit: number;
  sort?: ScreenSort;
}

export type AllColumnsStatus = "idle" | "loading" | "ready" | "empty" | "error";

export interface AllColumnsResult {
  rows: ScreenRow[];
  meta: ScreenMeta | null;
  status: AllColumnsStatus;
  error: string | null;
  merged: MergedSelection;
  chunks: string[][];
  refresh: () => void;
}

const EMPTY_MERGED: MergedSelection = {
  rows: [],
  gaps: [],
  failures: [],
  partial: [],
  absence: { chunkOf: new Map(), failed: new Map(), missing: new Map(), total: 0 },
};

function failureText(error: unknown): string {
  if (error instanceof ApiError) return `HTTP ${error.status}: ${error.message}`;
  if (error instanceof Error) return error.message;
  return "the request failed";
}

export function useAllColumns({
  enabled = true,
  view,
  keys,
  limit,
  sort,
}: AllColumnsOptions): AllColumnsResult {
  const [state, setState] = React.useState<Omit<AllColumnsResult, "refresh">>({
    rows: [],
    meta: null,
    status: "idle",
    error: null,
    merged: EMPTY_MERGED,
    chunks: [],
  });
  const [attempt, setAttempt] = React.useState(0);

  const chunks = React.useMemo(() => chunkColumns(keys), [keys]);
  const signature = React.useMemo(
    () => JSON.stringify({ view, chunks, limit, sort: sort ?? null }),
    [view, chunks, limit, sort],
  );

  React.useEffect(() => {
    if (!enabled || chunks.length === 0) {
      setState({ rows: [], meta: null, status: "idle", error: null, merged: EMPTY_MERGED, chunks });
      return;
    }

    const controller = new AbortController();
    let mine = true;
    setState((previous) => ({
      ...previous,
      status: "loading",
      error: null,
      chunks,
    }));

    const run = async () => {
      const answers: Array<ChunkAnswer & { meta?: ScreenMeta }> = await Promise.all(
        chunks.map(async (columns, index) => {
          try {
            const answer = await selectScreens(
              { view, columns, limit, offset: 0, sort },
              { signal: controller.signal },
            );
            if (answer.status === 304 || answer.body === null) {
              // No conditional request is ever made here, so a 304 is a server
              // oddity: treat it as "this chunk has no body", never as success.
              return { index, keys: columns, rows: [], error: "answered 304 with no body" };
            }
            const { rows, meta } = readResponse(answer.body, 0);
            return { index, keys: columns, rows, error: null, meta };
          } catch (error) {
            if (controller.signal.aborted) {
              return { index, keys: columns, rows: [], error: "aborted" };
            }
            return { index, keys: columns, rows: [], error: failureText(error) };
          }
        }),
      );
      if (!mine || controller.signal.aborted) return;

      const merged = mergeChunks(answers.map(({ index, keys: columns, rows, error }) => ({
        index,
        keys: columns,
        rows,
        error,
      })));

      const metas = answers
        .map((answer) => (answer as { meta?: ScreenMeta }).meta)
        .filter((meta): meta is ScreenMeta => meta !== undefined);
      const first = metas[0] ?? null;
      const meta = first
        ? {
            ...first,
            truncated: metas.some((entry) => entry.truncated),
            droppedFields: [...new Set(metas.flatMap((entry) => entry.droppedFields))],
          }
        : null;

      const status: AllColumnsStatus =
        merged.failures.length === answers.length
          ? "error"
          : merged.rows.length > 0
            ? "ready"
            : "empty";

      setState({
        rows: merged.rows,
        meta,
        status,
        error:
          status === "error"
            ? `All ${answers.length} chunks failed: ${merged.failures
                .map((failure) => `chunk ${failure.chunk + 1} — ${failure.message}`)
                .join("; ")}`
            : null,
        merged,
        chunks,
      });
    };

    void run();
    return () => {
      mine = false;
      controller.abort();
    };
  }, [signature, attempt, enabled, chunks, view, limit, sort]);

  const refresh = React.useCallback(() => setAttempt((value) => value + 1), []);

  return { ...state, refresh };
}
