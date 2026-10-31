import * as React from "react";

import { ApiError, getJson } from "@/lib/api";

import { BreadthMalformed, parseBreadth, type BreadthReport } from "./report";

/**
 * One read of `GET /market/breadth`, with an explicit retry.
 *
 * The route is server-cached (one scan serves every reader for its TTL, and a
 * failed scan answers from a 5 s failure window), so polling it would buy a
 * busier page and no fresher numbers. The strip therefore reads once per mount
 * and offers `reload`, exactly like `shell/useHealth`.
 *
 * `status` separates the three outcomes a user can act on differently:
 *
 *   loading       nothing to show yet
 *   ready         a parsed document, honest denominators and all
 *   unavailable   the scanner answered 503: there is no snapshot, so there is
 *                 nothing to print — not a market of zeros
 *   failed        anything else (transport, 4xx, a document we cannot read)
 */

export type BreadthStatus = "loading" | "ready" | "unavailable" | "failed";

export interface BreadthView {
  readonly status: BreadthStatus;
  readonly report: BreadthReport | null;
  /** The API's own message, when it sent one. */
  readonly message: string | null;
  /** HTTP status when the failure had one (503 → scanner unavailable). */
  readonly statusCode: number | null;
  readonly reload: () => void;
}

export interface BreadthQuery {
  readonly region?: string;
  readonly assetClass?: string;
}

const DEFAULT_REGION = "us";
const DEFAULT_ASSET_CLASS = "stock";

/** The market-wide path. Same-origin, root-absolute, never a client-side count. */
export function breadthPath(region: string, assetClass: string): string {
  const params = new URLSearchParams({ region, asset_class: assetClass });
  return `/market/breadth?${params.toString()}`;
}

interface Snapshot {
  status: BreadthStatus;
  report: BreadthReport | null;
  message: string | null;
  statusCode: number | null;
}

const LOADING: Snapshot = {
  status: "loading",
  report: null,
  message: null,
  statusCode: null,
};

export function useBreadth(query: BreadthQuery = {}): BreadthView {
  const region = query.region ?? DEFAULT_REGION;
  const assetClass = query.assetClass ?? DEFAULT_ASSET_CLASS;
  const path = React.useMemo(() => breadthPath(region, assetClass), [region, assetClass]);

  const [snapshot, setSnapshot] = React.useState<Snapshot>(LOADING);
  const [nonce, setNonce] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    let live = true;
    setSnapshot(LOADING);

    getJson(path, controller.signal)
      .then((body) => {
        if (!live) return;
        setSnapshot({ status: "ready", report: parseBreadth(body), message: null, statusCode: 200 });
      })
      .catch((cause: unknown) => {
        if (!live || controller.signal.aborted) return;
        const statusCode = cause instanceof ApiError ? cause.status : null;
        const detail =
          cause instanceof BreadthMalformed
            ? `Unreadable breadth snapshot: ${cause.message}`
            : cause instanceof Error
              ? cause.message
              : "Market breadth could not be read";
        setSnapshot({
          // A 503 from this route means one thing: the scanner had no answer
          // and no cached scan to fall back on. It is a state, not a zero.
          status: statusCode === 503 ? "unavailable" : "failed",
          report: null,
          message: detail,
          statusCode,
        });
      });

    return () => {
      live = false;
      controller.abort();
    };
  }, [path, nonce]);

  const reload = React.useCallback(() => setNonce((value) => value + 1), []);

  return { ...snapshot, reload };
}
