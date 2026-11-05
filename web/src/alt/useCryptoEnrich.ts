/**
 * The crypto page's enrichment hooks: one for the meta + global reads,
 * one for the per-symbol sparkline Map cache.
 *
 * Meta and global are fetched once per mount for the crypto class only;
 * sparklines are fetched lazily per visible symbol and cached in a Map
 * that outlives re-renders (like HoverBubble's pattern, but inline).
 */

import * as React from "react";

import {
  fetchCryptoGlobal,
  fetchCryptoMeta,
  fetchSparkline,
  type CryptoGlobalDoc,
  type CryptoMetaDoc,
  type EnrichData,
  type SparklineSeries,
} from "./cryptoEnrich";

export type EnrichStatus = "loading" | "ready" | "error";

export interface EnrichView {
  readonly status: EnrichStatus;
  readonly data: EnrichData | null;
  readonly error: string | null;
}

/**
 * Fetch `/market/crypto/meta` and `/market/crypto/global` once.
 * Skips when `skip` is true (non-crypto classes).
 */
export function useCryptoEnrich(skip = false): EnrichView {
  const [state, setState] = React.useState<EnrichView>({
    status: skip ? "ready" : "loading",
    data: null,
    error: null,
  });

  React.useEffect(() => {
    if (skip) {
      setState({ status: "ready", data: null, error: null });
      return;
    }

    const controller = new AbortController();
    let live = true;

    let meta: CryptoMetaDoc | null = null;
    let global: CryptoGlobalDoc | null = null;

    Promise.all([
      fetchCryptoMeta(controller.signal).then((v) => { if (live) meta = v; }).catch(() => {}),
      fetchCryptoGlobal(controller.signal).then((v) => { if (live) global = v; }).catch(() => {}),
    ]).then(() => {
      if (!live) return;
      setState({
        status: meta !== null || global !== null ? "ready" : "error",
        data: { meta, global },
        error: meta === null && global === null ? "Crypto market data unavailable" : null,
      });
    });

    return () => {
      live = false;
      controller.abort();
    };
  }, [skip]);

  return state;
}

/* -----------------------------------------------------------------------
 * Sparkline Map cache — one entry per symbol, fetched once, kept forever
 * --------------------------------------------------------------------- */

/**
 * A stable Map keyed by symbol that caches sparkline fetches.
 * Built on mount, scrubbed on unmount; survives re-renders via useRef.
 */
export function useSparklineCache() {
  const cache = React.useRef<Map<string, SparklineSeries | null>>(
    new Map(),
  );

  // Clean up on unmount
  React.useEffect(() => {
    return () => cache.current.clear();
  }, []);

  return cache.current;
}

/** Trigger a lazy fetch for one symbol, mutating the cache in place. */
export function useSparkline(
  cache: Map<string, SparklineSeries | null>,
  symbol: string,
) {
  const [series, setSeries] = React.useState<SparklineSeries | null>(
    () => cache.get(symbol) ?? null,
  );

  React.useEffect(() => {
    // Already fetched (hit or miss)
    if (cache.has(symbol)) {
      setSeries(cache.get(symbol) ?? null);
      return;
    }

    // Mark as pending so concurrent renders don't re-fetch
    cache.set(symbol, null);

    const controller = new AbortController();
    let live = true;

    fetchSparkline(symbol, controller.signal).then((result) => {
      if (!live) return;
      cache.set(symbol, result);
      setSeries(result);
    });

    return () => {
      live = false;
      controller.abort();
    };
  }, [cache, symbol]);

  return series;
}