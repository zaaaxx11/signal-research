/**
 * Home's one read of `GET /market/signals`.
 *
 * One request for thirteen cards: the route answers every group in one
 * document, and a client that asked per card would spend eleven round trips to
 * paint one page. The hook is deliberately the same shape as `useExchanges` —
 * a path computed once, an abort on unmount, a status a renderer can trust.
 *
 * Nothing here caches across mounts: Home is a page, and a page that has been
 * left and re-entered should show what the market is doing now, not what it was
 * doing when the tab was first opened.
 */

import * as React from "react";

import { ApiError, getJson } from "@/lib/api";

import { readSignals, signalsPath, type SignalsReport } from "./signals";

export type SignalsStatus = "loading" | "ready" | "error";

export interface SignalsState {
  status: SignalsStatus;
  report: SignalsReport | null;
  error: string | null;
  retry: () => void;
}

export function useSignals(): SignalsState {
  const path = React.useMemo(() => signalsPath(), []);
  const [state, setState] = React.useState<Omit<SignalsState, "retry">>({
    status: "loading",
    report: null,
    error: null,
  });
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    let live = true;
    setState((previous) => ({ ...previous, status: "loading", error: null }));

    getJson(path, controller.signal)
      .then((body) => {
        if (!live) return;
        setState({ status: "ready", report: readSignals(body), error: null });
      })
      .catch((cause: unknown) => {
        if (!live || controller.signal.aborted) return;
        const status = cause instanceof ApiError ? cause.status : 0;
        setState({
          status: "error",
          report: null,
          error:
            status === 404
              ? "This deployment does not serve market signals yet."
              : cause instanceof Error
                ? cause.message
                : "Request failed",
        });
      });

    return () => {
      live = false;
      controller.abort();
    };
  }, [path, attempt]);

  const retry = React.useCallback(() => setAttempt((value) => value + 1), []);
  return { ...state, retry };
}
