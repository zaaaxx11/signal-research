/**
 * The crypto section's read: three documents, one mount, one retry.
 *
 * The same three things a page-level fetch has to get right apply here, one
 * storey down: **one** request per mount (plus one more per retry), an abort
 * on unmount, and a status the section can render — `loading` draws its own
 * skeleton, `error` draws an :class:`ErrorState` with the Retry, `ready` draws
 * the table over whatever sources answered.
 *
 * Three endpoints, three fates:
 *
 * * the **rows** (``/market/alt?class=crypto``) are the section's spine — if
 *   they fail, the whole section failed, and the failure is voiced twice (one
 *   `notify.error` toast, one on-page error card). A dashboard that failed
 *   silently on a red-number section is worse than one that failed loudly.
 * * the **meta map** and the **global pulse** are shadings: either one can be
 *   absent while the section still stands. Each absence is named in
 *   `missing` and printed as a quiet sentence by the block — never zero.
 *
 * A retry that succeeds after a failure gets its one `notify.success`: the
 * reader asked the section to come back, so the section says it came back.
 */

import * as React from "react";

import { notify } from "@/lib/notify";
import { ApiError, getJson } from "@/lib/api";

import { readAlt, type AltRow } from "@/alt/alt";
import {
  cryptoAltPath,
  cryptoGlobalPath,
  cryptoMetaPath,
  joinCrypto,
  metaMatches,
  readCryptoMeta,
  readCryptoGlobal,
  type CryptoGlobal,
  type CryptoMetaMap,
  type CryptoRow,
} from "./crypto";

export type CryptoStatus = "loading" | "ready" | "error";

export interface HomeCryptoState {
  readonly status: CryptoStatus;
  readonly rows: readonly CryptoRow[];
  readonly meta: CryptoMetaMap;
  readonly global: CryptoGlobal | null;
  /** The shaded sources that did not answer this request: `meta`, `global`. */
  readonly missing: readonly string[];
  readonly measured: number | null;
  readonly error: string | null;
  readonly retry: () => void;
}

const LOADING: Snapshot = {
  status: "loading",
  rows: [],
  meta: new Map(),
  global: null,
  missing: [],
  measured: null,
  error: null,
};

type Snapshot = Omit<HomeCryptoState, "retry">;

function messageOf(cause: unknown): string {
  if (cause instanceof ApiError && cause.status === 404) {
    return "This deployment does not serve the crypto endpoints yet.";
  }
  return cause instanceof Error ? cause.message : "Request failed";
}

export function useHomeCrypto(): HomeCryptoState {
  const [state, setState] = React.useState<Snapshot>(LOADING);
  const [attempt, setAttempt] = React.useState(0);
  // "The last ask failed" is what turns a retry's success into a toast: kept
  // in a ref so the effect's own state writes cannot mask it.
  const wasFailed = React.useRef(false);

  React.useEffect(() => {
    const controller = new AbortController();
    let live = true;
    // A retry keeps the previous table in hand while the new one is in flight;
    // the blocks read `status` first, so they show skeletons, not stale rows
    // under a loading label.
    setState((previous) => ({ ...previous, status: "loading", error: null }));

    Promise.allSettled([
      getJson(cryptoAltPath(), controller.signal),
      getJson(cryptoMetaPath(), controller.signal),
      getJson(cryptoGlobalPath(), controller.signal),
    ])
      .then(async ([rowsAsk, metaAsk, globalAsk]) => {
        if (!live || controller.signal.aborted) return;

        if (rowsAsk.status === "rejected") {
          wasFailed.current = true;
          const message = messageOf(rowsAsk.reason);
          notify.error("Crypto did not load", message);
          setState((previous) => ({
            ...previous,
            status: "error",
            error: message,
            missing: [],
          }));
          return;
        }

        let rows: readonly AltRow[];
        try {
          rows = readAlt(rowsAsk.value, "crypto").rows;
        } catch {
          wasFailed.current = true;
          notify.error("Crypto did not load", "The crypto rows could not be read");
          setState((previous) => ({ ...previous, status: "error", error: "The crypto rows could not be read" }));
          return;
        }

        const meta =
          metaAsk.status === "fulfilled"
            ? readCryptoMeta(metaAsk.value)
            : (new Map() as CryptoMetaMap);
        const global = globalAsk.status === "fulfilled" ? readCryptoGlobal(globalAsk.value) : null;

        const missing: string[] = [];
        if (metaAsk.status !== "fulfilled") missing.push("meta");
        if (globalAsk.status !== "fulfilled") missing.push("global");

        if (wasFailed.current) {
          wasFailed.current = false;
          notify.success("Crypto loaded", `${rows.length} symbols`);
        }

        setState({
          status: "ready",
          rows: joinCrypto(rows, meta),
          meta,
          global,
          missing,
          measured: rows.length > 0 ? metaMatches(rows, meta) : 0,
          error: null,
        });
      })
      .catch(() => undefined);

    return () => {
      live = false;
      controller.abort();
    };
  }, [attempt]);

  const retry = React.useCallback(() => setAttempt((value) => value + 1), []);

  return { ...state, retry };
}
