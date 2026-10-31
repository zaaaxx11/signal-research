/**
 * The exchange menu's one read of `GET /market/exchanges`.
 *
 * The selector offers the market's venues — the codes and counts the route
 * tallied over the same universe `/screens/select` filters — and falls back to
 * the `exchange` cells of the page when the route is not there.
 *
 * It is a *separate route from `/market/breadth`, on purpose*: breadth is
 * already read by the strip and by the sector menu, and a facet bolted onto it
 * would make a document that must stay reconcilable (`advancing + declining +
 * unchanged = universe_total`) the carrier of a list it does not define. One
 * route per claim, one read per mount.
 *
 * That route is new, so this hook treats **every** failure the same way for the
 * panel's purposes: no list. A 404 (the route has not shipped), a 503 (the
 * scanner has no snapshot), a malformed document, a network error — all leave
 * `venues: []`, and `ScreenPanel` derives the menu from the rows it is drawing
 * and says so. The status is still reported (`failed` vs `unavailable`) so the
 * control can word its tooltip honestly.
 *
 * Imports are leaf modules only: the shell already mounts the strip, and this
 * must not pull a second copy of it into the graph.
 */

import * as React from "react";

import { ApiError, getJson } from "@/lib/api";

import { venueBucketsFromReport, type VenueBucket } from "./exchanges";

export type ExchangeOptionsStatus = "loading" | "ready" | "unavailable" | "failed";

export interface ExchangeOptions {
  /** The market's venues, US majors first — empty when the read failed. */
  venues: VenueBucket[];
  status: ExchangeOptionsStatus;
  /** The API's own sentence when there is one, for the control's tooltip. */
  message: string | null;
}

/**
 * The same population `/screens/select` filters.
 *
 * `common_only=0` is the selector's own default — `requestBody` sends no flag,
 * which is `false` — so these counts are the totals the grid's rows are drawn
 * from. Asking for `1` would narrow the universe and publish a different one
 * (that is the 3.8% over-count the route's own doc warns about), which is the
 * one thing a menu of counts must never do.
 */
export function exchangesPath(region: string, assetClass: string): string {
  const params = new URLSearchParams({
    region,
    asset_class: assetClass,
    common_only: "0",
  });
  return `/market/exchanges?${params.toString()}`;
}

export function useExchangeOptions(region = "us", assetClass = "stock"): ExchangeOptions {
  const path = React.useMemo(() => exchangesPath(region, assetClass), [region, assetClass]);
  const [state, setState] = React.useState<ExchangeOptions>({
    venues: [],
    status: "loading",
    message: null,
  });

  React.useEffect(() => {
    const controller = new AbortController();
    let live = true;
    setState((previous) => ({ ...previous, status: "loading" }));

    getJson(path, controller.signal)
      .then((body) => {
        if (!live) return;
        setState({ venues: venueBucketsFromReport(body), status: "ready", message: null });
      })
      .catch((cause: unknown) => {
        if (!live || controller.signal.aborted) return;
        const code = cause instanceof ApiError ? cause.status : null;
        setState({
          venues: [],
          // 503 is the scanner saying it has no snapshot; 404 is this build
          // asking a deployment that does not serve the route yet. Both leave
          // the menu to the rows; anything else is a document we could not read.
          status: code === 503 || code === 404 ? "unavailable" : "failed",
          message: cause instanceof Error ? cause.message : null,
        });
      });

    return () => {
      live = false;
      controller.abort();
    };
  }, [path]);

  return state;
}
