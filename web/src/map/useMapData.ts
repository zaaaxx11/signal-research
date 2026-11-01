import * as React from "react";

import { ApiError, getJson } from "@/lib/api";

import { MapMalformed, parseMap, type MapPayload } from "./types";

/**
 * One read of `GET /market/map`, with an explicit retry and a hard abort.
 *
 * The map is the largest document the UI asks for (~250 KB raw, 5,576 rows),
 * so a second request must not be allowed to race the first: two answers
 * arriving out of order would either draw the older map over the newer one or,
 * worse, paint twice for one state. `useScreens` solves the same problem with a
 * sequence number and an `AbortController`; this hook does the same, one level
 * smaller:
 *
 *   * every effect run owns a controller, and its cleanup aborts it;
 *   * every resolution checks `signal.aborted` and its own sequence number
 *     before touching state, so a straggler that resolved after the abort is
 *     dropped rather than merged;
 *   * `reload` bumps a nonce, which aborts and re-reads — the retry button is
 *     the only way a failed map asks again.
 *
 * The endpoint is cached server-side, so there is no poll: one read per mount,
 * like the breadth strip and unlike the screener's pages.
 */

export type MapStatus = "loading" | "ready" | "error";

export interface MapView {
  readonly status: MapStatus;
  readonly payload: MapPayload | null;
  /** The API's own message (or our parser's), when there is one. */
  readonly error: string | null;
  readonly statusCode: number | null;
  readonly reload: () => void;
}

const DEFAULT_REGION = "us";
const DEFAULT_METRIC = "change";

/** The same-origin path. Root-absolute, mount-free, never a client-side count. */
export function mapPath(region: string, metric: string): string {
  const params = new URLSearchParams({ region, metric });
  return `/market/map?${params.toString()}`;
}

interface Snapshot {
  status: MapStatus;
  payload: MapPayload | null;
  error: string | null;
  statusCode: number | null;
}

const LOADING: Snapshot = { status: "loading", payload: null, error: null, statusCode: null };

export interface MapQuery {
  readonly region?: string;
  readonly metric?: string;
}

export function useMapData(query: MapQuery = {}): MapView {
  const region = query.region ?? DEFAULT_REGION;
  const metric = query.metric ?? DEFAULT_METRIC;
  const path = mapPath(region, metric);

  const [snapshot, setSnapshot] = React.useState<Snapshot>(LOADING);
  const [nonce, setNonce] = React.useState(0);
  const sequence = React.useRef(0);

  React.useEffect(() => {
    const controller = new AbortController();
    const mine = sequence.current + 1;
    sequence.current = mine;
    setSnapshot(LOADING);

    getJson(path, controller.signal)
      .then((body) => {
        if (controller.signal.aborted || mine !== sequence.current) return;
        setSnapshot({ status: "ready", payload: parseMap(body), error: null, statusCode: 200 });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || mine !== sequence.current) return;
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        const statusCode = cause instanceof ApiError ? cause.status : null;
        const message =
          cause instanceof MapMalformed
            ? `Unreadable map: ${cause.message}`
            : cause instanceof Error
              ? cause.message
              : "The market map could not be read";
        setSnapshot({ status: "error", payload: null, error: message, statusCode });
      });

    return () => {
      controller.abort();
    };
  }, [path, nonce]);

  const reload = React.useCallback(() => setNonce((value) => value + 1), []);

  return { ...snapshot, reload };
}
