/**
 * Home's one read of `GET /market/home`.
 *
 * One request paints seven blocks. The hook owns the three things a page-level
 * fetch has to get right and a component cannot:
 *
 *   * **one** request per mount (plus one more per retry), never one per block;
 *   * an abort on unmount, so a page left mid-flight does not set state on a
 *     component that is gone;
 *   * a status the renderer can trust — `loading` draws skeletons, `error` draws
 *     per-block failure lines and one Retry, `ready` draws what arrived.
 *
 * `retry` is the only way a failed page asks again. A dashboard that re-asked on
 * every render would multiply the load exactly when the server is unwell.
 */

import * as React from "react";

import { ApiError, getJson } from "@/lib/api";

import { homePath, readHome, type HomeReport, type HomeStatus } from "./home";

export interface HomeState {
  readonly status: HomeStatus;
  readonly report: HomeReport | null;
  readonly error: string | null;
  readonly retry: () => void;
}

interface Snapshot {
  status: HomeStatus;
  report: HomeReport | null;
  error: string | null;
}

const LOADING: Snapshot = { status: "loading", report: null, error: null };

export function useHome(region = "us"): HomeState {
  const path = React.useMemo(() => homePath(region), [region]);
  const [snapshot, setSnapshot] = React.useState<Snapshot>(LOADING);
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    let live = true;
    // A retry keeps the previous document in hand while the new one is in
    // flight — but the blocks read `status` first, so they show skeletons, not
    // stale numbers under a "loading" label.
    setSnapshot((previous) => ({ status: "loading", report: previous.report, error: null }));

    getJson(path, controller.signal)
      .then((body) => {
        if (!live || controller.signal.aborted) return;
        setSnapshot({ status: "ready", report: readHome(body), error: null });
      })
      .catch((cause: unknown) => {
        if (!live || controller.signal.aborted) return;
        const status = cause instanceof ApiError ? cause.status : 0;
        setSnapshot({
          status: "error",
          report: null,
          error:
            status === 404
              ? "This deployment does not serve the market dashboard yet."
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

  return { ...snapshot, retry };
}
