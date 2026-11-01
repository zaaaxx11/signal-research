/**
 * The detail panel's read of `GET /engine/insider/{symbol}`.
 *
 * Three rules, and each one has a test:
 *
 * 1. **One request per symbol.** The effect is keyed by the symbol, not by a
 *    render; a re-render (a keystroke, a tone class) does not refetch.
 * 2. **The previous request is aborted**, not merely ignored. Two rapid clicks
 *    leave one live request — the second symbol's — and a late answer for the
 *    first cannot paint under the second's header.
 * 3. **The answer is labelled with the symbol it belongs to.** The view is
 *    `loading` whenever the state it holds was read for another symbol, so
 *    there is no frame in which AAPL's rows sit under MSFT's title.
 */

import * as React from "react";

import { ApiError, getJson } from "@/lib/api";

import { FilingsMalformed, filingsPath, readFilings, type InsiderFilings } from "./filings";

export type FilingsStatus = "idle" | "loading" | "ready" | "error";

interface FilingsState {
  readonly status: Exclude<FilingsStatus, "idle">;
  readonly filings: InsiderFilings | null;
  readonly error: string | null;
  readonly forSymbol: string | null;
}

export interface FilingsView {
  readonly status: FilingsStatus;
  readonly filings: InsiderFilings | null;
  readonly error: string | null;
  /** The symbol the current state belongs to; null while closed. */
  readonly forSymbol: string | null;
  readonly retry: () => void;
}

export function useInsiderFilings(symbol: string | null): FilingsView {
  const [state, setState] = React.useState<FilingsState>({
    status: "loading",
    filings: null,
    error: null,
    forSymbol: null,
  });
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(() => {
    if (symbol === null) {
      setState({ status: "loading", filings: null, error: null, forSymbol: null });
      return;
    }

    const controller = new AbortController();
    let live = true;
    const path = filingsPath(symbol);
    setState({ status: "loading", filings: null, error: null, forSymbol: symbol });

    getJson(path, controller.signal)
      .then((body) => {
        if (!live || controller.signal.aborted) return;
        setState({
          status: "ready",
          filings: readFilings(body),
          error: null,
          forSymbol: symbol,
        });
      })
      .catch((cause: unknown) => {
        if (!live || controller.signal.aborted) return;
        const status = cause instanceof ApiError ? cause.status : 0;
        setState({
          status: "error",
          filings: null,
          error:
            cause instanceof FilingsMalformed
              ? "The filing list could not be read."
              : status === 404
                ? "This company is not in the engine's identity master."
                : cause instanceof Error
                  ? cause.message
                  : "Request failed",
          forSymbol: symbol,
        });
      });

    return () => {
      live = false;
      controller.abort();
    };
  }, [symbol, attempt]);

  const retry = React.useCallback(() => {
    setState((previous) => ({
      status: "loading",
      filings: null,
      error: null,
      forSymbol: previous.forSymbol,
    }));
    setAttempt((value) => value + 1);
  }, []);

  if (symbol === null) {
    return { status: "idle", filings: null, error: null, forSymbol: null, retry };
  }

  // The effect has not run yet for a newly opened symbol: what the hook holds
  // belongs to a different company, so the panel is loading, not showing it.
  if (state.forSymbol !== symbol) {
    return { status: "loading", filings: null, error: null, forSymbol: symbol, retry };
  }

  return { ...state, retry };
}
