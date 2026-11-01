/**
 * One read of `/market/quotes` for a set of symbols.
 *
 * * **No symbols, no request.** The address is `""` and the hook stays `idle`;
 *   an empty watchlist must not spend a round trip to be told it is empty.
 * * **One request per *set*, not per render.** The path is built from the
 *   canonical (sorted, deduped) symbols, so reordering the watchlist — a purely
 *   local gesture — is not a new address. Adding or removing one symbol is.
 * * **The report is stamped with the set it answers.** A render that happens
 *   between a write and the effect's first tick would otherwise hand the new
 *   symbol the old document and draw "Unavailable" for a quote that is on its
 *   way. Stamping makes the intermediate state `loading`, which is the truth.
 */

import * as React from "react";

import { ApiError, getJson } from "@/lib/api";

import { QuotesMalformed, canonicalSymbols, quotesPath, readQuotes, type QuotesReport } from "./quotes";

export type QuotesStatus = "idle" | "loading" | "ready" | "error";

export interface QuotesView {
  readonly status: QuotesStatus;
  /** Answers only the current symbol set; `null` until it lands. */
  readonly report: QuotesReport | null;
  readonly error: string | null;
  readonly retry: () => void;
}

interface Snapshot {
  readonly key: string;
  readonly status: QuotesStatus;
  readonly report: QuotesReport | null;
  readonly error: string | null;
}

function messageOf(cause: unknown, status: number): string {
  if (cause instanceof QuotesMalformed) return "The quote answer could not be read.";
  if (status === 404) return "This deployment does not serve quotes yet.";
  if (status === 503) return "No quote source answered just now.";
  if (cause instanceof Error && cause.message.length > 0) return cause.message;
  return "Request failed.";
}

export function useQuotes(symbols: readonly string[]): QuotesView {
  const canonical = canonicalSymbols(symbols);
  const key = canonical.join(",");
  const [attempt, setAttempt] = React.useState(0);
  const [snapshot, setSnapshot] = React.useState<Snapshot>(() => ({
    key,
    status: key === "" ? "idle" : "loading",
    report: null,
    error: null,
  }));

  // A snapshot for another set is not this set's answer.
  const current: Snapshot =
    snapshot.key === key
      ? snapshot
      : { key, status: key === "" ? "idle" : "loading", report: null, error: null };

  React.useEffect(() => {
    if (key === "") {
      setSnapshot({ key, status: "idle", report: null, error: null });
      return;
    }

    const controller = new AbortController();
    let live = true;
    const requested = key.split(",");
    setSnapshot({ key, status: "loading", report: null, error: null });

    getJson(quotesPath(requested), controller.signal)
      .then((body) => {
        if (!live) return;
        setSnapshot({ key, status: "ready", report: readQuotes(body, requested), error: null });
      })
      .catch((cause: unknown) => {
        if (!live || controller.signal.aborted) return;
        const status = cause instanceof ApiError ? cause.status : 0;
        setSnapshot({ key, status: "error", report: null, error: messageOf(cause, status) });
      });

    return () => {
      live = false;
      controller.abort();
    };
  }, [key, attempt]);

  const retry = React.useCallback(() => setAttempt((value) => value + 1), []);

  return { status: current.status, report: current.report, error: current.error, retry };
}
