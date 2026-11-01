/**
 * One read of `GET /market/chart/{symbol}?range=…`, with an explicit retry and
 * a hard abort.
 *
 * A reader can click 1D → 5D → 1M faster than the network answers, and two
 * chart payloads arriving out of order would either draw the older range over
 * the newer one or paint twice for one state. The hook is the same discipline
 * as `map/useMapData`, one level smaller:
 *
 *   * every effect run owns an `AbortController`, and its cleanup aborts it;
 *   * every resolution checks `signal.aborted` and its own sequence number
 *     before touching state, so a straggler is dropped rather than merged;
 *   * `reload` bumps a nonce, which aborts and re-reads — the retry button is
 *     the only way a failed chart asks again.
 *
 * The 404 is special-cased to a sentence, because "no chart for X" is a fact
 * about the symbol, not a transport failure and definitely not an empty frame
 * with axes around it.
 */

import * as React from "react";

import { ApiError, getJson } from "@/lib/api";

import {
  ChartMalformed,
  parseChart,
  type ChartRange,
  type ChartSeries,
} from "./types";

export type ChartStatus = "loading" | "ready" | "error";

export interface ChartView {
  readonly status: ChartStatus;
  readonly series: ChartSeries | null;
  /** The API's own message (or our parser's), when there is one. */
  readonly error: string | null;
  readonly statusCode: number | null;
  readonly reload: () => void;
}

/** The same-origin path. Root-absolute, mount-free, symbol encoded. */
export function chartPath(symbol: string, range: ChartRange): string {
  return `/market/chart/${encodeURIComponent(symbol.trim().toUpperCase())}?range=${range}`;
}

interface Snapshot {
  status: ChartStatus;
  series: ChartSeries | null;
  error: string | null;
  statusCode: number | null;
}

const LOADING: Snapshot = { status: "loading", series: null, error: null, statusCode: null };

export function useChartData(symbol: string, range: ChartRange): ChartView {
  const path = chartPath(symbol, range);

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
        const parsed = parseChart(body, { symbol, range });
        setSnapshot({
          status: "ready",
          series: { ...parsed, receivedAt: performance.now() },
          error: null,
          statusCode: 200,
        });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || mine !== sequence.current) return;
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        const statusCode = cause instanceof ApiError ? cause.status : null;
        const message =
          statusCode === 404
            ? `No chart for ${symbol.trim().toUpperCase()}`
            : cause instanceof ChartMalformed
              ? `Unreadable chart: ${cause.message}`
              : cause instanceof Error
                ? cause.message
                : "The chart could not be read";
        setSnapshot({ status: "error", series: null, error: message, statusCode });
      });

    return () => {
      controller.abort();
    };
  }, [path, nonce, symbol, range]);

  const reload = React.useCallback(() => setNonce((value) => value + 1), []);

  return { ...snapshot, reload };
}
