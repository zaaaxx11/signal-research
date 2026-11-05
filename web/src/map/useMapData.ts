import * as React from "react";

import { ApiError, getJson } from "@/lib/api";

import { MapMalformed, parseMap, type MapPayload, type MarketGroup } from "./types";

export type MapStatus = "loading" | "ready" | "error";

export interface MapView {
  readonly status: MapStatus;
  readonly payload: MapPayload | null;
  readonly error: string | null;
  readonly statusCode: number | null;
  readonly reload: () => void;
}

export const TIMEFRAMES = ["1d", "1w", "1m", "3m", "6m", "1y"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];

export const TIMEFRAME_LABELS: Record<Timeframe, string> = {
  "1d": "1D",
  "1w": "1W",
  "1m": "1M",
  "3m": "3M",
  "6m": "6M",
  "1y": "1Y",
};

export const TIMEFRAME_METRIC_LABELS: Record<Timeframe, string> = {
  "1d": "1-Day performance",
  "1w": "1-Week performance",
  "1m": "1-Month performance",
  "3m": "3-Month performance",
  "6m": "6-Month performance",
  "1y": "1-Year performance",
};

const DEFAULT_REGION = "us";
const DEFAULT_TIMEFRAME: Timeframe = "1d";

export function mapPath(region: string, timeframe: string): string {
  const params = new URLSearchParams({ region, timeframe, shape: "2" });
  return `/market/map?${params.toString()}`;
}

export function groupsPath(): string {
  return "/market/groups";
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
  readonly timeframe?: Timeframe;
}

export function useMapData(query: MapQuery = {}): MapView {
  const region = query.region ?? DEFAULT_REGION;
  const timeframe = query.timeframe ?? DEFAULT_TIMEFRAME;
  const path = mapPath(region, timeframe);

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

export function useMarketGroups(): {
  groups: MarketGroup[];
  status: "loading" | "ready" | "error";
} {
  const [groups, setGroups] = React.useState<MarketGroup[]>([]);
  const [status, setStatus] = React.useState<"loading" | "ready" | "error">("loading");

  React.useEffect(() => {
    const controller = new AbortController();
    getJson(groupsPath(), controller.signal)
      .then((body) => {
        if (controller.signal.aborted) return;
        const raw = Array.isArray(body) ? body : [];
        setGroups(
          raw.map((entry: unknown) => {
            if (typeof entry === "object" && entry !== null) {
              const e = entry as Record<string, unknown>;
              return {
                id: String(e.id ?? ""),
                label: String(e.label ?? e.id ?? ""),
                count: Number(e.count ?? 0),
              };
            }
            return { id: "", label: "", count: 0 };
          }).filter((g: MarketGroup) => g.id.length > 0),
        );
        setStatus("ready");
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setStatus("error");
      });
    return () => controller.abort();
  }, []);

  return { groups, status };
}