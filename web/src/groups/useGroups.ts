/**
 * Groups' one read of `GET /market/groups`.
 *
 * One request per mount/param change: the route answers every group in one
 * document, and a client that asked per row would spend a round trip per line.
 * The hook has the same shape as `useBreadth` — a path computed once, an abort
 * on unmount, an explicit retry — because the page has to distinguish "not here
 * yet" from "not available" without guessing.
 *
 * Nothing is cached across mounts: a page left and re-entered shows what the
 * market is doing now, not what it was doing when the tab was opened.
 *
 * A failed read raises a notify.error toast; a successful retry after a failure
 * raises a notify.success toast so the user sees the recovery.
 */

import * as React from "react";

import { ApiError, getJson } from "@/lib/api";
import { notify } from "@/lib/notify";

import {
  GROUPS_REGION,
  GroupsMalformed,
  groupsPath,
  readGroups,
  type GroupBy,
  type GroupsReport,
  type GroupsSort,
} from "./groups";

export type GroupsStatus = "loading" | "ready" | "error";

export interface GroupsView {
  readonly status: GroupsStatus;
  readonly report: GroupsReport | null;
  readonly error: string | null;
  readonly retry: () => void;
}

export function useGroups(
  region: string = GROUPS_REGION,
  groupBy: GroupBy = "sector",
  sortBy: GroupsSort = "avg_change",
  sortDir: "asc" | "desc" = "desc",
): GroupsView {
  const path = React.useMemo(
    () => groupsPath(region, groupBy, sortBy, sortDir),
    [region, groupBy, sortBy, sortDir],
  );
  const [state, setState] = React.useState<Omit<GroupsView, "retry">>({
    status: "loading",
    report: null,
    error: null,
  });
  const [attempt, setAttempt] = React.useState(0);
  const wasFailed = React.useRef(false);

  React.useEffect(() => {
    const controller = new AbortController();
    let live = true;
    setState((previous) => ({ ...previous, status: "loading", error: null }));

    getJson(path, controller.signal)
      .then((body) => {
        if (!live) return;
        const report = readGroups(body);
        setState({ status: "ready", report, error: null });
        if (wasFailed.current) {
          wasFailed.current = false;
          notify.success("Groups loaded", `${report.rows.length} ${groupBy}s shown`);
        }
      })
      .catch((cause: unknown) => {
        if (!live || controller.signal.aborted) return;
        wasFailed.current = true;
        const status = cause instanceof ApiError ? cause.status : 0;
        const message =
          cause instanceof GroupsMalformed
            ? "The group figures could not be read."
            : status === 404
              ? "This deployment does not serve groups yet."
              : cause instanceof Error
                ? cause.message
                : "Request failed";
        notify.error("Groups did not load", message);
        setState({ status: "error", report: null, error: message });
      });

    return () => {
      live = false;
      controller.abort();
    };
  }, [path, attempt, groupBy]);

  const retry = React.useCallback(() => setAttempt((value) => value + 1), []);
  return { ...state, retry };
}