/**
 * The screens data layer: one request, one validator, one hook.
 *
 * Everything the grid knows about a screen arrives from `POST /screens/select`.
 * Three rules make this module worth existing:
 *
 * 1. **The server's ETag is the only cache key, and it belongs to the rows.**
 *    A refresh sends the tag it was served; a `304` means the rows already on
 *    screen are the answer, so they stay and a quiet "not refreshed" badge says
 *    why. A `304` must never clear the grid — that is the whole point of asking
 *    conditionally. And it may only be presented when the rows on screen are
 *    the answer to *this* query: the rows carry their `queryKey`, because a tag
 *    that is valid for the request and stale for the body is how one sort's
 *    rows ended up under another sort's label.
 * 2. **A new query aborts the old one.** Two views racing would otherwise let
 *    whichever response lands last paint the other view's columns.
 * 3. **Rows are identified by `symbol`, never by their index.** The API is free
 *    to return a page in any order; position is not identity.
 *
 * The request body is built here and nowhere else, from the column list the
 * schema served — the client never spells a catalogue key itself.
 */

import * as React from "react";

import { ApiError, getJson, postJsonConditional, readErrorMessage } from "@/lib/api";

/** The panel's hard limits, mirrored from the API contract. */
export const PANEL_LIMIT = 20;
export const MAX_COLUMNS = 40;

/** The three transparent sorts `POST /screens/select` has always offered. */
export const SORTS = ["market_cap", "turnover", "gainers"] as const;
export type ScreenSort = (typeof SORTS)[number];

/** The name each sort wears in a control and in the grid's caption. */
export const SORT_LABELS: Record<ScreenSort, string> = {
  market_cap: "Market cap",
  turnover: "Turnover",
  gainers: "Gainers",
};

/**
 * The order a page is ranked by: a catalogue key, and which end is first.
 *
 * The wire spelling is the API's own (`app/screen/rank.py`): a bare key orders
 * **highest first** and a leading `-` orders **lowest first**. Keeping that
 * spelling in the state — rather than a `{key, direction}` pair — means the
 * address bar, the request body and the header control all speak the one token
 * the server reads, and there is nothing to translate at any boundary.
 */
export type SortDirection = "desc" | "asc";

/** `-country` → `asc`; `country` → `desc`. */
export function sortDirection(sort: string): SortDirection {
  return sort.startsWith("-") ? "asc" : "desc";
}

/** The bare catalogue key: `-country` → `country`. */
export function sortKey(sort: string): string {
  return sort.startsWith("-") ? sort.slice(1) : sort;
}

/** The wire token for a key and a direction. */
export function encodeSort(key: string, direction: SortDirection): string {
  return direction === "asc" ? `-${key}` : key;
}

/**
 * One header click.
 *
 * A new column starts at its highest value (the reader asked "what is biggest
 * here" first, which is the question a table is usually opened with); clicking
 * the column already sorted flips the end. This is the whole rule, and it is a
 * pure function so the header and its test cannot disagree about it.
 */
export function nextSort(current: string, key: string): string {
  if (sortKey(current) !== key) return encodeSort(key, "desc");
  return sortDirection(current) === "desc" ? encodeSort(key, "asc") : encodeSort(key, "desc");
}

/** A readable name for any sort token, for captions and select options. */
export function sortLabel(sort: string): string {
  const key = sortKey(sort);
  if ((SORTS as readonly string[]).includes(key)) return SORT_LABELS[key as ScreenSort];
  return key
    .split("_")
    .filter(Boolean)
    .map((word, index) => (index === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word))
    .join(" ");
}

/** One canonical view, exactly as `GET /screens/select/schema` serves it. */
export interface ViewDefinition {
  name: string;
  label: string;
  job: string;
  columns: string[];
}

/** One row: a symbol and the cells that were asked for (plus `as_of`). */
export interface ScreenRow {
  symbol: string;
  [key: string]: unknown;
}

export interface ScreenMeta {
  asOf: string;
  total: number;
  universeTotal: number;
  returned: number;
  limit: number;
  offset: number;
  sort: string | null;
  /** True when the API's 60 KB panel budget cut rows or fields. */
  truncated: boolean;
  droppedFields: string[];
}

export type ScreenStatus = "idle" | "loading" | "ready" | "empty" | "error";

export interface ScreenError {
  status: number;
  message: string;
}

/**
 * One predicate, exactly as `criteria.filters` takes it: one of `key` (a
 * published filter key) or `field` (a catalogue column), an operator and a value.
 */
export interface ScreenFilter {
  key?: string;
  field?: string;
  op: "eq" | "ne" | "gt" | "gte" | "lt" | "lte" | "in" | "between" | "contains";
  value: unknown;
}

export interface ScreensQuery {
  view: string;
  columns: readonly string[];
  limit?: number;
  offset?: number;
  /** A catalogue key (`-key` for lowest first), exactly as the API reads it. */
  sort?: string;
  region?: "us" | "global" | "us_etf";
  assetClass?: "stock" | "etf";
  filters?: readonly ScreenFilter[];
  /**
   * A named screen the API compiles itself (`top-gainers`, `unusual-volume`).
   *
   * A signal is not a filter: it replaces the where-clause *and* the ordering
   * with its own, which is why it travels as a top-level body field rather than
   * inside `criteria.filters`. Omitted when unset, so an unsignalled request is
   * byte-identical to the one this client has always sent.
   */
  signal?: string | null;
}

export interface ScreensResult {
  rows: ScreenRow[];
  /** The view the rows belong to; a view switch discards rows from another. */
  view: string | null;
  /**
   * The query the rows are the answer to. A conditional request may only
   * present a tag when this equals the key of the request being made — a tag is
   * valid for a *request*, and the body it validates must be the body on
   * screen.
   */
  queryKey: string | null;
  meta: ScreenMeta | null;
  etag: string | null;
  status: ScreenStatus;
  error: ScreenError | null;
  /** True when the last refresh was answered `304 Not Modified`. */
  revalidated: boolean;
  /** Client clock at the last completed check, for the stale badge. */
  checkedAt: string | null;
  refresh: () => void;
}

interface WireResponse {
  rows?: unknown;
  as_of?: unknown;
  total?: unknown;
  universe_total?: unknown;
  returned?: unknown;
  limit?: unknown;
  offset?: unknown;
  sort?: unknown;
  budget?: unknown;
}

const EMPTY = {
  rows: [] as ScreenRow[],
  view: null,
  queryKey: null,
  meta: null,
  etag: null,
  status: "idle" as ScreenStatus,
  error: null,
  revalidated: false,
  checkedAt: null,
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function asNumber(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asString(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

/** A row without a string `symbol` cannot be keyed, so it is not a row. */
export function isScreenRow(value: unknown): value is ScreenRow {
  return isRecord(value) && typeof value["symbol"] === "string" && value["symbol"].length > 0;
}

/** The exact wire body: mode, region, asset class, columns, criteria. */
export function requestBody(query: ScreensQuery): Record<string, unknown> {
  return {
    mode: "criteria",
    region: query.region ?? "us",
    asset_class: query.assetClass ?? "stock",
    columns: [...query.columns],
    ...(query.signal ? { signal: query.signal } : {}),
    criteria: {
      limit: query.limit ?? PANEL_LIMIT,
      offset: query.offset ?? 0,
      filters: (query.filters ?? []).map((filter) => ({ ...filter })),
      ...(query.sort ? { sort: query.sort } : {}),
    },
  };
}

export interface SelectOptions {
  signal?: AbortSignal;
  /** The weak validator from the previous answer for the *same* query. */
  etag?: string | null;
}

export interface SelectAnswer {
  status: 200 | 304;
  etag: string | null;
  body: WireResponse | null;
}

/**
 * One conditional selection. `304` is a success with no body, not an error —
 * the shared transport has `ok === false` for it, so it is handled before the
 * failure branch or a revalidation would look like a broken API.
 */
export async function selectScreens(
  query: ScreensQuery,
  options: SelectOptions = {},
): Promise<SelectAnswer> {
  const answer = await postJsonConditional("/screens/select", requestBody(query), {
    signal: options.signal,
    etag: options.etag ?? null,
  });

  if (answer.status === 304) {
    return { status: 304, etag: answer.etag, body: null };
  }
  if (answer.status >= 400 || answer.status < 200) {
    throw new ApiError(answer.status, readErrorMessage(answer.body, answer.status));
  }
  if (!isRecord(answer.body)) {
    throw new ApiError(answer.status, "Response was not an object");
  }
  return { status: 200, etag: answer.etag, body: answer.body as WireResponse };
}

/** Normalise a wire body into the grid's shape, dropping rows it cannot key.
 *
 * `offset` is the one field the API does not always echo. The request's offset
 * is the honest fallback: without it a second page would label itself 1–20
 * while showing rows 21–40.
 */
export function readResponse(
  body: WireResponse,
  requestedOffset = 0,
): { rows: ScreenRow[]; meta: ScreenMeta } {
  const rows = Array.isArray(body.rows) ? body.rows.filter(isScreenRow) : [];
  const budget = isRecord(body.budget) ? body.budget : {};
  return {
    rows,
    meta: {
      asOf: asString(body.as_of, ""),
      total: asNumber(body.total, rows.length),
      universeTotal: asNumber(body.universe_total, rows.length),
      returned: asNumber(body.returned, rows.length),
      limit: asNumber(body.limit, PANEL_LIMIT),
      offset: asNumber(body.offset, requestedOffset),
      sort: typeof body.sort === "string" ? body.sort : null,
      truncated: budget["truncated"] === true,
      droppedFields: Array.isArray(budget["dropped_fields"])
        ? budget["dropped_fields"].filter((key): key is string => typeof key === "string")
        : [],
    },
  };
}

/**
 * Stable query identity: the ETag belongs to this string, not the object.
 *
 * Built from the **wire body**, not from a hand-kept list of knobs: anything
 * that reaches the request is in the key by construction, so a filter (or a
 * knob added next year) cannot change the answer while leaving the key — and
 * therefore the tag — untouched. `view` rides alongside because it shapes the
 * response without appearing in the body.
 */
export function queryKey(query: ScreensQuery): string {
  return JSON.stringify({ view: query.view, request: requestBody(query) });
}

/**
 * The selection hook.
 *
 * `query` may be null while the view catalogue is loading. Its identity is
 * not trusted: the effect keys on :func:`queryKey`, so an inline object literal
 * in the caller does not re-fetch on every render.
 */
export function useScreens(query: ScreensQuery | null): ScreensResult {
  const [state, setState] = React.useState<Omit<ScreensResult, "refresh">>(EMPTY);
  const [attempt, setAttempt] = React.useState(0);
  const queryRef = React.useRef(query);
  queryRef.current = query;
  const latest = React.useRef(state);
  latest.current = state;
  const key = query ? queryKey(query) : null;
  const seq = React.useRef(0);
  const etags = React.useRef(new Map<string, string>());
  /**
   * Keys that have already spent their one free retry after a `304` that could
   * not be honoured. Bounded so a server that insists cannot loop the client.
   */
  const retried = React.useRef(new Set<string>());

  React.useEffect(() => {
    const current = queryRef.current;
    if (!current || key === null) {
      setState(EMPTY);
      return;
    }

    const controller = new AbortController();
    const mine = ++seq.current;
    const view = current.view;

    // Same view: keep the rows visible while revalidating. A different view:
    // drop them now, so the previous view's numbers can never sit under the new
    // view's header for a frame.
    setState((previous) => {
      if (previous.view === view) {
        return { ...previous, status: "loading", error: null };
      }
      return { ...EMPTY, view, status: "loading" };
    });

    const run = async () => {
      try {
        // The tag belongs to the rows, not to the view. Send it only when the
        // rows on screen are the answer to *this* query; otherwise the server's
        // correct 304 would freeze another query's rows under this label.
        const shown = latest.current;
        const etag =
          shown.queryKey === key && shown.rows.length > 0
            ? etags.current.get(key) ?? null
            : null;

        const answer = await selectScreens(current, {
          signal: controller.signal,
          etag,
        });
        if (controller.signal.aborted || mine !== seq.current) return;

        if (answer.status === 304) {
          const onScreen = latest.current;
          if (onScreen.queryKey !== key || onScreen.rows.length === 0) {
            // A 304 we cannot honour: it validates a body that is not on
            // screen. Never paint the rows we have under this query's label —
            // drop the tag and ask once more unconditionally.
            etags.current.delete(key);
            if (!retried.current.has(key)) {
              retried.current.add(key);
              setAttempt((value) => value + 1);
              return;
            }
            setState((previous) => ({
              ...previous,
              status: "error",
              error: {
                status: 304,
                message:
                  "The server answered 304 for a query whose rows are not on screen; reload to start again.",
              },
              revalidated: false,
            }));
            return;
          }
          setState((previous) =>
            previous.queryKey === key
              ? {
                  ...previous,
                  status: previous.rows.length > 0 ? "ready" : "empty",
                  error: null,
                  revalidated: true,
                  checkedAt: new Date().toISOString(),
                }
              : previous,
          );
          return;
        }

        if (answer.etag) etags.current.set(key, answer.etag);
        retried.current.delete(key);
        const { rows, meta } = readResponse(answer.body ?? {}, current.offset ?? 0);
        setState({
          rows,
          view,
          queryKey: key,
          meta,
          etag: answer.etag,
          status: rows.length > 0 ? "ready" : "empty",
          error: null,
          revalidated: false,
          checkedAt: new Date().toISOString(),
        });
      } catch (error) {
        if (controller.signal.aborted || mine !== seq.current) return;
        if (error instanceof DOMException && error.name === "AbortError") return;
        const status = error instanceof ApiError ? error.status : 0;
        setState((previous) => ({
          ...previous,
          status: "error",
          error: {
            status,
            message: error instanceof Error ? error.message : "Request failed",
          },
          revalidated: false,
        }));
      }
    };

    void run();
    return () => controller.abort();
  }, [key, attempt]);

  const refresh = React.useCallback(() => setAttempt((value) => value + 1), []);

  return { ...state, refresh };
}

export interface CatalogueResult {
  views: ViewDefinition[];
  status: "loading" | "ready" | "error";
  error: string | null;
  retry: () => void;
}

function isViewDefinition(value: unknown): value is ViewDefinition {
  if (!isRecord(value)) return false;
  return (
    typeof value["name"] === "string" &&
    typeof value["label"] === "string" &&
    typeof value["job"] === "string" &&
    Array.isArray(value["columns"]) &&
    value["columns"].every((key) => typeof key === "string")
  );
}

/**
 * The nine views, served. A client that hardcodes this table is the drift
 * Phase 0's F1 recorded, so the list comes from the same schema document the
 * validator publishes, and every column key the grid asks for comes from here.
 */
export function useViewCatalogue(): CatalogueResult {
  const [state, setState] = React.useState<Omit<CatalogueResult, "retry">>({
    views: [],
    status: "loading",
    error: null,
  });
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    setState((previous) => ({ ...previous, status: "loading", error: null }));

    const run = async () => {
      try {
        const body = await getJson("/screens/select/schema", controller.signal);
        if (!isRecord(body) || !Array.isArray(body["views"])) {
          throw new ApiError(200, "Schema served no view catalogue");
        }
        const views = body["views"].filter(isViewDefinition);
        if (views.length === 0) throw new ApiError(200, "Schema served no views");
        setState({ views, status: "ready", error: null });
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof DOMException && error.name === "AbortError") return;
        setState({
          views: [],
          status: "error",
          error: error instanceof Error ? error.message : "Could not load views",
        });
      }
    };

    void run();
    return () => controller.abort();
  }, [attempt]);

  const retry = React.useCallback(() => setAttempt((value) => value + 1), []);
  return { ...state, retry };
}
