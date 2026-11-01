/**
 * News's one read of `GET /market/news`.
 *
 * One request per mount, and one more per explicit retry. The hook owns the
 * three things the page must not guess at: whether the answer has landed,
 * whether it failed, and how to ask again. Parsing happens on arrival, so a
 * malformed document is a labelled fault rather than a half-painted list.
 *
 * The request is aborted on unmount. There is no polling: a headlines page
 * that refreshed itself under the reader would move the row they were about to
 * click.
 */

import * as React from "react";

import { ApiError, getJson } from "@/lib/api";

import { NEWS_LIMIT, NewsMalformed, newsPath, readNews, type NewsReport } from "./news";

export type NewsStatus = "loading" | "ready" | "error";

export interface NewsView {
  readonly status: NewsStatus;
  readonly report: NewsReport | null;
  readonly error: string | null;
  readonly retry: () => void;
}

export interface NewsQuery {
  readonly limit?: number;
}

export function useNews(query: NewsQuery = {}): NewsView {
  const limit = query.limit ?? NEWS_LIMIT;
  const path = React.useMemo(() => newsPath(limit), [limit]);

  const [state, setState] = React.useState<Omit<NewsView, "retry">>({
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
        setState({ status: "ready", report: readNews(body), error: null });
      })
      .catch((cause: unknown) => {
        if (!live || controller.signal.aborted) return;
        const status = cause instanceof ApiError ? cause.status : 0;
        setState({
          status: "error",
          report: null,
          error:
            cause instanceof NewsMalformed
              ? "The news payload could not be read."
              : status === 404
                ? "This deployment does not serve a news feed yet."
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
