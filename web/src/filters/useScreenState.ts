/**
 * Screen state + its URL. One hook, so there is one answer to "what is on
 * screen" — the chips, the tabs, the pager and the address bar read it.
 *
 * The initial read happens once, after the schema has arrived (the registry is
 * what can decide whether a link's filter key still exists). From then on every
 * change replaces the URL in place: back/forward still work for a same-document
 * link, and a reload re-reads the same four things.
 *
 * `dropped` is the honest half of (e): a link naming a key this build cannot
 * apply is *ignored*, and the shell says which key — it never retries a 422.
 */

import * as React from "react";

import type { PresetBucket, PresetGroup, WireFilter } from "./types";
import type { FilterRegistry } from "./registry";
import {
  droppedNotice,
  encodeScreenState,
  emptyScreenState,
  parseScreenState,
  type ScreenState,
} from "./urlState";
import { appendFilter, appendFilters, applyBucket, clearFilters, removeFilter } from "./state";

export interface ScreenStateApi {
  state: ScreenState;
  /** False until the schema has been read and the link parsed. */
  ready: boolean;
  /** Filter spellings the link carried that this build cannot apply. */
  dropped: string[];
  notice: string | null;
  dismissDropped: () => void;
  setView: (name: string) => void;
  /** A catalogue sort token: `key` (highest first) or `-key` (lowest first). */
  setSort: (sort: string) => void;
  setSignal: (signal: string | null) => void;
  setPage: (page: number) => void;
  setFilters: (filters: ScreenState["filters"]) => void;
  addFilter: (draft: WireFilter) => void;
  addFilters: (drafts: WireFilter[]) => void;
  removeFilter: (id: string) => void;
  clearFilters: () => void;
  applyBucket: (group: PresetGroup, bucket: PresetBucket) => void;
}

export function useScreenState(
  registry: FilterRegistry | null,
  viewNames: readonly string[],
  /**
   * False while the panel document is still in flight.
   *
   * The link's `f=` list is validated against two documents: the schema (keys,
   * kinds, aliases) and the filter document (the values the panel offers). A
   * parse that ran before the second one landed would drop exactly the filters
   * the header writes, so the boot parse waits for it — and fails open when it
   * errors, because a screen that will not load is worse than one filter
   * ignored with a sentence.
   */
  vocabularyReady = true,
): ScreenStateApi {
  const [state, setState] = React.useState<ScreenState>(emptyScreenState);
  const [dropped, setDropped] = React.useState<string[]>([]);
  const [ready, setReady] = React.useState(false);
  const initialised = React.useRef(false);
  const namesKey = viewNames.join("\u0000");
  const namesRef = React.useRef(viewNames);
  namesRef.current = viewNames;

  React.useEffect(() => {
    if (registry === null || viewNames.length === 0 || initialised.current) return;
    if (!vocabularyReady) return;
    initialised.current = true;
    const parsed = parseScreenState(window.location.search, registry, viewNames);
    setState({ ...parsed.state, view: parsed.state.view || viewNames[0] || "" });
    setDropped(parsed.dropped);
    setReady(true);
  }, [registry, namesKey, viewNames, vocabularyReady]);

  React.useEffect(() => {
    if (!ready) return;
    const query = encodeScreenState(state);
    const path = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
    window.history.replaceState(null, "", path);
  }, [state, ready]);

  React.useEffect(() => {
    if (!ready || registry === null) return;
    const onPopState = () => {
      const parsed = parseScreenState(window.location.search, registry, namesRef.current);
      setState({
        ...parsed.state,
        view: parsed.state.view || namesRef.current[0] || "",
      });
      setDropped(parsed.dropped);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [ready, registry]);

  /**
   * Every mutation is also the end of the link's notice: the sentence described
   * the state the link arrived in, and once the reader changes the screen it
   * describes nothing still on display. One place to say that, so no setter can
   * forget it.
   */
  const mutate = React.useCallback((change: (previous: ScreenState) => ScreenState) => {
    setState(change);
    setDropped([]);
  }, []);

  const setView = React.useCallback((name: string) => {
    mutate((previous) => ({ ...previous, view: name, page: 0 }));
  }, [mutate]);

  const setSort = React.useCallback((sort: string) => {
    mutate((previous) => ({ ...previous, sort, page: 0 }));
  }, [mutate]);

  /**
   * A signal is a whole screen, so it starts at the first page. Clearing it
   * (`null`) is the same mutation: the request simply leaves the field out.
   */
  const setSignal = React.useCallback((signal: string | null) => {
    mutate((previous) => ({ ...previous, signal, page: 0 }));
  }, [mutate]);

  const setPage = React.useCallback((page: number) => {
    mutate((previous) => ({ ...previous, page: Math.max(0, page) }));
  }, [mutate]);

  const setFilters = React.useCallback((filters: ScreenState["filters"]) => {
    mutate((previous) => ({ ...previous, filters, page: 0 }));
  }, [mutate]);

  const addFilter = React.useCallback((draft: WireFilter) => {
    mutate((previous) => ({
      ...previous,
      page: 0,
      filters: appendFilter(previous.filters, draft),
    }));
  }, [mutate]);

  const addFilters = React.useCallback((drafts: WireFilter[]) => {
    mutate((previous) => ({
      ...previous,
      page: 0,
      filters: appendFilters(previous.filters, drafts),
    }));
  }, [mutate]);

  const remove = React.useCallback((id: string) => {
    mutate((previous) => ({
      ...previous,
      page: 0,
      filters: removeFilter(previous.filters, id),
    }));
  }, [mutate]);

  const clear = React.useCallback(() => {
    mutate((previous) => ({ ...previous, page: 0, filters: clearFilters() }));
  }, [mutate]);

  const chooseBucket = React.useCallback((group: PresetGroup, bucket: PresetBucket) => {
    mutate((previous) => ({
      ...previous,
      page: 0,
      filters: applyBucket(previous.filters, group, bucket),
    }));
  }, [mutate]);

  const dismissDropped = React.useCallback(() => setDropped([]), []);

  return {
    state,
    ready,
    dropped,
    notice: droppedNotice(dropped),
    dismissDropped,
    setView,
    setSort,
    setSignal,
    setPage,
    setFilters,
    addFilter,
    addFilters,
    removeFilter: remove,
    clearFilters: clear,
    applyBucket: chooseBucket,
  };
}

