/**
 * The alt table's one read.
 *
 * One request per mount, and one more only when the reader asks for one: the
 * filter and the sort are client-side, so neither may put a query on the wire.
 * The hook owns exactly three things the page must not guess at — whether the
 * answer landed, why it failed, and how to ask again — and it aborts the
 * in-flight request on unmount so a late answer cannot paint into a page that
 * is gone.
 */

import * as React from "react";

import { ApiError, getJson } from "@/lib/api";

import {
  AltMalformed,
  altPath,
  readAlt,
  type AltReport,
  type MarketClass,
} from "./alt";

export type AltStatus = "loading" | "ready" | "error";

export interface AltView {
  readonly status: AltStatus;
  readonly report: AltReport | null;
  readonly error: string | null;
  readonly retry: () => void;
}

export function useAlt(marketClass: MarketClass): AltView {
  const path = React.useMemo(() => altPath(marketClass), [marketClass]);

  const [state, setState] = React.useState<Omit<AltView, "retry">>({
    status: "loading",
    report: null,
    error: null,
  });
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    let live = true;
    // The old table stays on screen while a retry is in flight, but the status
    // says loading: the skeleton is the frame's state, and a reader who just
    // pressed Retry must see that something is happening.
    setState((previous) => ({ ...previous, status: "loading", error: null }));

    getJson(path, controller.signal)
      .then((body) => {
        if (!live) return;
        setState({ status: "ready", report: readAlt(body, marketClass), error: null });
      })
      .catch((cause: unknown) => {
        if (!live || controller.signal.aborted) return;
        const status = cause instanceof ApiError ? cause.status : 0;
        setState({
          status: "error",
          report: null,
          error:
            cause instanceof AltMalformed
              ? "The market payload could not be read."
              : status === 404
                ? "This deployment does not serve this market class yet."
                : cause instanceof Error
                  ? cause.message
                  : "Request failed",
        });
      });

    return () => {
      live = false;
      controller.abort();
    };
  }, [path, attempt, marketClass]);

  const retry = React.useCallback(() => setAttempt((value) => value + 1), []);
  return { ...state, retry };
}
