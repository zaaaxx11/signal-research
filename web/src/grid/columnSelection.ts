/**
 * A view's columns, as the reader rearranged them.
 *
 * The catalogue is 116 keys; one request carries 40. So a custom selection is
 * a *panel* — at most 40 keys — chosen from every key the schema serves, and it
 * belongs to the view it was built from. That ownership is what the URL
 * spelling encodes:
 *
 *     ?view=value&cols=value:pe,peg,pb
 *
 * One `cols` parameter per view. Switching to `growth` does not apply `value`'s
 * selection (it is not `growth`'s), and switching back finds it exactly as it
 * was left: the selection survives the switch without leaking into another
 * view. Parsing is validated against the served catalogue, never trusted — a
 * link is the one input a stranger writes. An unknown key is dropped by name;
 * a list over the cap is trimmed to the cap and says so; and nothing over 40
 * ever reaches the request body.
 */

import * as React from "react";

import { MAX_COLUMNS } from "@/data/screens";

/** The panel cap, named once so the message and the guard cannot drift. */
export const COLUMN_CAP = MAX_COLUMNS;

export const COLUMNS_PARAM = "cols";

export interface ParsedColumns {
  byView: Record<string, string[]>;
  /** Sentences for the reader: a dropped key, a trimmed list, a dead view. */
  notices: string[];
}

function listOf(values: readonly string[], limit = 6): string {
  const shown = values.slice(0, limit).join(", ");
  return values.length > limit ? `${shown} and ${values.length - limit} more` : shown;
}

/**
 * `?cols=value:pe,peg` × n → selections, validated against the served catalogue.
 *
 * Rules, in the order they protect the panel:
 *
 * 1. an entry that does not name a view is ignored (it cannot belong anywhere);
 * 2. a view this build does not serve is ignored, and reported;
 * 3. unknown column keys are dropped, and reported;
 * 4. duplicates collapse (one column is requested once);
 * 5. a list longer than 40 is truncated to the first 40, and reported — the
 *    request is never built from the longer list.
 */
export function parseColumnSelection(
  search: string,
  viewNames: readonly string[],
  catalogue: readonly string[],
): ParsedColumns {
  const query = search.includes("?") ? search.slice(search.indexOf("?") + 1) : search.replace(/^\?/, "");
  const params = new URLSearchParams(query);
  const known = new Set(catalogue);
  const names = new Set(viewNames);
  const byView: Record<string, string[]> = {};
  const notices: string[] = [];

  for (const raw of params.getAll(COLUMNS_PARAM)) {
    const at = raw.indexOf(":");
    if (at <= 0) {
      notices.push(`Ignored a column list from this link (${raw.slice(0, 60)}): it does not name a view.`);
      continue;
    }
    const view = raw.slice(0, at);
    if (!names.has(view)) {
      const count = raw.slice(at + 1).split(",").filter(Boolean).length;
      notices.push(`Ignored ${count} column${count === 1 ? "" : "s"} for “${view}”, which is not a view this build serves.`);
      continue;
    }

    const seen = new Set<string>();
    const keys: string[] = [];
    const unknown: string[] = [];
    const entries = raw
      .slice(at + 1)
      .split(",")
      .map((key) => key.trim())
      .filter((key) => key.length > 0);
    for (const trimmed of entries) {
      if (!known.has(trimmed)) {
        unknown.push(trimmed);
        continue;
      }
      if (seen.has(trimmed)) continue;
      seen.add(trimmed);
      keys.push(trimmed);
    }

    if (unknown.length > 0) {
      notices.push(
        `Ignored ${unknown.length} column${unknown.length === 1 ? "" : "s"} this build does not serve: ${listOf(unknown)}.`,
      );
    }
    if (keys.length === 0) continue;

    if (keys.length > COLUMN_CAP) {
      notices.push(
        `This link asked for ${Math.max(entries.length, keys.length)} columns for “${view}”; a panel holds at most ${COLUMN_CAP}, so the first ${COLUMN_CAP} are shown.`,
      );
      keys.length = COLUMN_CAP;
    }
    byView[view] = keys;
  }

  return { byView, notices };
}

/** The `cols` values for a selection, in the catalogue's view order. */
export function encodeColumnSelection(
  byView: Record<string, readonly string[]>,
  viewNames: readonly string[],
): string[] {
  const values: string[] = [];
  const order = [...viewNames.filter((name) => (byView[name]?.length ?? 0) > 0)];
  for (const name of Object.keys(byView)) {
    if (!order.includes(name) && (byView[name]?.length ?? 0) > 0) order.push(name);
  }
  for (const name of order) {
    values.push(`${name}:${(byView[name] ?? []).join(",")}`);
  }
  return values;
}

export interface ToggleResult {
  keys: string[];
  /** The cap sentence when the add was refused; null when it landed. */
  notice: string | null;
}

/**
 * Add or remove one key.
 *
 * Removing always works. Adding is refused at the cap with a sentence that
 * names the cap and the key that did not fit — the control stays answerable
 * instead of silently doing nothing.
 */
export function toggleColumn(
  keys: readonly string[],
  key: string,
  cap: number = COLUMN_CAP,
): ToggleResult {
  if (keys.includes(key)) {
    return { keys: keys.filter((entry) => entry !== key), notice: null };
  }
  if (keys.length >= cap) {
    return {
      keys: [...keys],
      notice: `A panel holds at most ${cap} columns; remove one before adding “${key}”.`,
    };
  }
  return { keys: [...keys, key], notice: null };
}

/** The same list a view's own columns are: order preserved, no duplicates. */
export function sameColumns(left: readonly string[], right: readonly string[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((key, index) => key === right[index]);
}

function rewriteUrl(byView: Record<string, readonly string[]>, viewNames: readonly string[]): void {
  const params = new URLSearchParams(window.location.search);
  params.delete(COLUMNS_PARAM);
  for (const value of encodeColumnSelection(byView, viewNames)) params.append(COLUMNS_PARAM, value);
  const query = params.toString();
  const path = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
  window.history.replaceState(null, "", path);
}

export interface ColumnSelectionApi {
  /** True once the link has been read against the served catalogue. */
  ready: boolean;
  /** A view's custom keys, or null when it still shows its own columns. */
  columnsFor: (view: string) => readonly string[] | null;
  toggle: (view: string, key: string) => void;
  reset: (view: string) => void;
  /** The last refusal or link-trim sentence, until it is dismissed. */
  notice: string | null;
  dismiss: () => void;
}

export interface ColumnSelectionOptions {
  viewNames: readonly string[];
  catalogue: readonly string[];
  /** Each view's own columns, the fallback and the "not custom" comparison. */
  defaults: Record<string, readonly string[]>;
  /** False until the schema and the screen state have both been read. */
  ready: boolean;
  /**
   * A token that changes whenever the screen state changes. The URL is written
   * by two owners — `useScreenState` (view/sort/page/filters) and this hook
   * (`cols`) — and `useScreenState` replaces the whole query string. Declaring
   * this hook after it makes its effect run second, so the merged address bar
   * is the one that survives; this dependency is what makes that rewrite happen
   * on a screen change that did not touch the selection.
   */
  revision: unknown;
}

export function useColumnSelection({
  viewNames,
  catalogue,
  defaults,
  ready,
  revision,
}: ColumnSelectionOptions): ColumnSelectionApi {
  const [byView, setByView] = React.useState<Record<string, string[]>>({});
  const [notice, setNotice] = React.useState<string | null>(null);
  const [parsed, setParsed] = React.useState(false);
  const initialised = React.useRef(false);

  /**
   * The address bar as this page load received it.
   *
   * `useScreenState` rewrites the whole query string from its own four fields
   * as soon as it is ready, and it is declared before this hook — so by the
   * time this hook's parse effect runs, the `cols` it was going to read may
   * already have been replaced. The link is therefore read from the snapshot
   * taken during the first render, which no writer has touched yet.
   */
  const bootSearch = React.useRef<string | null>(null);
  if (bootSearch.current === null) bootSearch.current = window.location.search;

  const namesKey = viewNames.join("\u0000");
  const catalogueKey = catalogue.join("\u0000");
  const namesRef = React.useRef(viewNames);
  namesRef.current = viewNames;

  React.useEffect(() => {
    if (!ready || initialised.current || catalogue.length === 0) return;
    initialised.current = true;
    const result = parseColumnSelection(bootSearch.current ?? window.location.search, viewNames, catalogue);
    setByView(result.byView);
    // Every sentence the link earned is shown, not just the first: a 200-name
    // link is both "84 keys this build does not serve" and "trimmed to 40".
    setNotice(result.notices.length > 0 ? result.notices.join(" ") : null);
    setParsed(true);
  }, [ready, namesKey, catalogueKey, catalogue.length, viewNames]);

  React.useEffect(() => {
    if (!parsed) return;
    const onPopState = () => {
      const result = parseColumnSelection(window.location.search, namesRef.current, catalogue);
      setByView(result.byView);
      setNotice(result.notices.length > 0 ? result.notices.join(" ") : null);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [parsed, catalogueKey, catalogue]);

  // Runs after `useScreenState`'s writer (declaration order), merging this
  // hook's `cols` into whatever the screen state just wrote.
  React.useEffect(() => {
    if (!parsed) return;
    rewriteUrl(byView, namesRef.current);
  }, [byView, parsed, revision]);

  const byViewRef = React.useRef(byView);
  byViewRef.current = byView;
  const defaultsRef = React.useRef(defaults);
  defaultsRef.current = defaults;

  const toggle = React.useCallback((view: string, key: string) => {
    const base = byViewRef.current[view] ?? defaultsRef.current[view] ?? [];
    const result = toggleColumn(base, key);
    if (result.notice !== null) {
      setNotice(result.notice);
      return;
    }
    setNotice(null);
    setByView((previous) => {
      const next = { ...previous };
      if (sameColumns(result.keys, defaultsRef.current[view] ?? [])) delete next[view];
      else next[view] = result.keys;
      return next;
    });
  }, []);

  const reset = React.useCallback((view: string) => {
    setByView((previous) => {
      const next = { ...previous };
      delete next[view];
      return next;
    });
    setNotice(null);
  }, []);

  const columnsFor = React.useCallback(
    (view: string): readonly string[] | null => byView[view] ?? null,
    [byView],
  );

  const dismiss = React.useCallback(() => setNotice(null), []);

  return { ready: parsed, columnsFor, toggle, reset, notice, dismiss };
}
