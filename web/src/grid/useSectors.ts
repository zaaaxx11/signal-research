/**
 * The sector menu's one read of `GET /market/breadth`.
 *
 * The selector offers the market's sectors, so it needs the market's document —
 * the same one the strip above the grid draws. This hook is deliberately a
 * *read only*: `parseBreadth` validates it, `sectorNamesFromReport` turns it into
 * a menu, and nothing here counts or averages anything.
 *
 * The route is server-cached for its TTL, so one read per mount is the whole
 * polling budget; `reload` is the explicit retry, exactly like the strip's.
 *
 * A failure is a state, not an empty market: `names` is `[]` and `status` says
 * why, which is what lets the control fall back to the rows on screen and label
 * that fallback honestly.
 *
 * Imports are the two leaf modules, not the barrel: the shell already mounts the
 * strip, and this must not add a second copy of it to the graph.
 */

import * as React from "react";

import { ApiError, getJson } from "@/lib/api";
import { parseBreadth } from "@/breadth/report";
import { breadthPath } from "@/breadth/useBreadth";

import { sectorNamesFromReport } from "./sectors";

export type SectorOptionsStatus = "loading" | "ready" | "unavailable" | "failed";

export interface SectorOptions {
  /** The 21 provider sector labels, alphabetical — empty when the read failed. */
  names: string[];
  status: SectorOptionsStatus;
  /** The API's own sentence when there is one, for the control's tooltip. */
  message: string | null;
  reload: () => void;
}

export function useSectorOptions(region = "us", assetClass = "stock"): SectorOptions {
  const path = React.useMemo(() => breadthPath(region, assetClass), [region, assetClass]);
  const [state, setState] = React.useState<Omit<SectorOptions, "reload">>({
    names: [],
    status: "loading",
    message: null,
  });
  const [nonce, setNonce] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    let live = true;
    setState((previous) => ({ ...previous, status: "loading" }));

    getJson(path, controller.signal)
      .then((body) => {
        if (!live) return;
        setState({
          names: sectorNamesFromReport(parseBreadth(body)),
          status: "ready",
          message: null,
        });
      })
      .catch((cause: unknown) => {
        if (!live || controller.signal.aborted) return;
        const code = cause instanceof ApiError ? cause.status : null;
        setState({
          names: [],
          // 503 is the scanner saying it has no snapshot; anything else is a
          // document we could not read. Both leave the menu to the rows.
          status: code === 503 ? "unavailable" : "failed",
          message: cause instanceof Error ? cause.message : null,
        });
      });

    return () => {
      live = false;
      controller.abort();
    };
  }, [path, nonce]);

  const reload = React.useCallback(() => setNonce((value) => value + 1), []);

  return { ...state, reload };
}
