/**
 * The detail panel's four reads, one state machine each.
 *
 * The panel is not a page: it opens beside a grid the reader is still using, so
 * three rules are load-bearing.
 *
 * 1. **Four independent resources.** Fundamentals, ownership, insiders and news
 *    each own their status and fire at mount, in parallel. A fundamentals
 *    request that takes four seconds cannot keep the News tab on a skeleton —
 *    that is the difference between a per-tab skeleton and one global spinner.
 * 2. **A 404 is an empty answer, not a fault.** The API says
 *    `security_not_found` when it has no row for a symbol; the panel prints an
 *    empty state for that tab and never a red banner. Only a 5xx or a transport
 *    failure is an error, and only inside the tab that suffered it.
 * 3. **The previous symbol is aborted.** Every request carries an
 *    `AbortController` that cleanup aborts, and the panel body is keyed by
 *    symbol, so a late answer for AAPL can never paint under MSFT's header.
 *
 * Ownership is the one non-trivial read: `POST /screens/select` in symbols mode
 * resolves tickers against a snapshot, and the shell has no snapshot prop. The
 * current `GET /screens?source=tradingview` document names one (`screen_id`),
 * which is the same id the select route accepts as `snapshot_id` — two calls,
 * chained, still in parallel with the other three.
 */

import * as React from "react";

import { ApiError, getJson, postJsonConditional, readErrorMessage } from "@/lib/api";
import type { ConditionalAnswer } from "@/lib/api";

export type ResourceStatus = "loading" | "ready" | "empty" | "error";

export interface ResourceState<T> {
  status: ResourceStatus;
  data: T | null;
  error: string | null;
}

export interface Resource<T> extends ResourceState<T> {
  retry: () => void;
}

export interface FundamentalMetric {
  value: unknown;
  unit: string;
}

export interface FundamentalsData {
  symbol: string;
  asOf: string;
  metrics: Record<string, FundamentalMetric>;
}

export interface InsiderData {
  symbol: string;
  asOf: string;
  items: InsiderRow[];
}

export interface InsiderRow {
  accession: string;
  filed: string;
  transactionDate: string;
  owner: string;
  role: string;
  code: string;
  shares: number | null;
  price: number | null;
  acquiredDisposed: string;
  url: string | null;
}

export interface NewsData {
  symbol: string;
  asOf: string;
  items: NewsItem[];
}

export interface NewsItem {
  headline: string;
  url: unknown;
  source: string;
  publishedAt: string;
}

export type OwnershipData = Record<string, unknown> & { symbol: string };

/**
 * The ownership cells the panel asks the selection route for. These are the
 * keys the live answer serves (verified against AAPL); the route returns null
 * for any the facts store does not hold, which renders as a placeholder.
 */
export const OWNERSHIP_COLUMNS = [
  "shares_outstanding",
  "float_shares",
  "short_ratio",
  "insider_own",
  "inst_own",
  "short_float",
] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function parseFundamentals(body: unknown): FundamentalsData | null {
  if (!isRecord(body) || !isRecord(body["metrics"])) return null;
  const entries = Object.entries(body["metrics"]);
  if (entries.length === 0) return null;

  const metrics: Record<string, FundamentalMetric> = {};
  for (const [key, raw] of entries) {
    if (!isRecord(raw)) continue;
    metrics[key] = {
      value: raw["value"] ?? null,
      unit: asString(raw["unit"], "unknown") || "unknown",
    };
  }
  if (Object.keys(metrics).length === 0) return null;

  return {
    symbol: asString(body["symbol"]),
    asOf: asString(body["as_of"]),
    metrics,
  };
}

export function parseInsider(body: unknown): InsiderData | null {
  if (!isRecord(body) || !Array.isArray(body["items"])) return null;
  const items: InsiderRow[] = [];
  for (const raw of body["items"]) {
    if (!isRecord(raw)) continue;
    const owner = asString(raw["owner_name"]);
    const transactionDate = asString(raw["transaction_date"]) || asString(raw["filed"]);
    if (owner.length === 0 && transactionDate.length === 0) continue;
    items.push({
      accession: asString(raw["accession"]),
      filed: asString(raw["filed"]),
      transactionDate,
      owner,
      role: asString(raw["role"]),
      code: asString(raw["code"]),
      shares: asNumberOrNull(raw["shares"]),
      price: asNumberOrNull(raw["price"]),
      acquiredDisposed: asString(raw["acquired_disposed"]),
      url: typeof raw["url"] === "string" ? raw["url"] : null,
    });
  }
  if (items.length === 0) return null;
  return { symbol: asString(body["symbol"]), asOf: asString(body["as_of"]), items };
}

export function parseNews(body: unknown): NewsData | null {
  if (!isRecord(body) || !Array.isArray(body["items"])) return null;
  const items: NewsItem[] = [];
  for (const raw of body["items"]) {
    if (!isRecord(raw)) continue;
    const headline = asString(raw["headline"]).trim();
    if (headline.length === 0) continue;
    items.push({
      headline,
      // Kept unvalidated: the link formatter is the one place that decides what
      // is safe to put in an href, so the parser must not "repair" a bad URL
      // into a plausible one.
      url: raw["url"],
      source: asString(raw["source"]),
      publishedAt: asString(raw["published_at"]),
    });
  }
  if (items.length === 0) return null;
  return { symbol: asString(body["symbol"]), asOf: asString(body["as_of"]), items };
}

export function parseOwnership(body: unknown, symbol: string): OwnershipData | null {
  if (!isRecord(body) || !Array.isArray(body["rows"])) return null;
  const wanted = symbol.trim().toUpperCase();
  for (const raw of body["rows"]) {
    if (!isRecord(raw)) continue;
    const rowSymbol = asString(raw["symbol"]) || asString(raw["ticker"]);
    if (rowSymbol.toUpperCase() !== wanted) continue;
    return { ...raw, symbol: rowSymbol };
  }
  return null;
}

function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

const LOADING: ResourceState<never> = { status: "loading", data: null, error: null };

/**
 * The snapshot the ownership read resolves against, read at most once per
 * session.
 *
 * `GET /screens?source=tradingview` names the current scan (`screen_id`), and
 * the select route accepts that id as `snapshot_id`. It is a property of the
 * session, not of the symbol: re-reading it per row cost one identical request
 * in front of every ownership POST (the seam report measured three for
 * AAPL → NVDA → MSFT).
 *
 * Two rules keep the cache honest:
 *
 * 1. **A failed read is not a constant.** A rejection clears the cache, so the
 *    next open retries instead of replaying a broken promise for the session.
 * 2. **A rotation invalidates it.** The select route answers `404` only when
 *    the snapshot no longer exists; `loadOwnership` drops the id and re-reads
 *    it exactly once. An answer that names a different `snapshot_id` adopts it,
 *    so the next open does not have to learn the same thing with a 404.
 *
 * The read is deliberately not tied to the caller's `AbortSignal`: the first
 * panel to open owns it, and aborting on close would poison the shared promise
 * for every later open. One small GET that cannot be cancelled is cheaper than
 * a cache that can be killed by a close.
 */
let snapshotId: string | null = null;
let snapshotRead: Promise<string> | null = null;

function snapshotIdFrom(body: unknown): string {
  if (!isRecord(body)) return "";
  const id = body["screen_id"] ?? body["snapshot_id"];
  return typeof id === "string" ? id : "";
}

export function readSnapshotId(): Promise<string> {
  if (snapshotId !== null) return Promise.resolve(snapshotId);
  if (snapshotRead === null) {
    snapshotRead = getJson("/screens?source=tradingview").then(
      (body) => {
        const id = snapshotIdFrom(body);
        if (id.length === 0) {
          snapshotRead = null;
          throw new ApiError(200, "The screen did not name a snapshot to resolve against");
        }
        snapshotId = id;
        return id;
      },
      (error: unknown) => {
        snapshotRead = null;
        throw error;
      },
    );
  }
  return snapshotRead;
}

/** Adopt an id the API just named (a rotation reported by the answer itself). */
function adoptSnapshotId(body: unknown): void {
  const id = snapshotIdFrom(body);
  if (id.length === 0 || id === snapshotId) return;
  snapshotId = id;
  snapshotRead = Promise.resolve(id);
}

/** Test seam and the invalidation path: forget the session's id. */
export function resetSnapshotCache(): void {
  snapshotId = null;
  snapshotRead = null;
}

function useResource<T>(
  symbol: string,
  load: (symbol: string, signal: AbortSignal) => Promise<T | null>,
): Resource<T> {
  const [state, setState] = React.useState<ResourceState<T>>({ ...LOADING });
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    let live = true;
    setState({ status: "loading", data: null, error: null });

    load(symbol, controller.signal)
      .then((data) => {
        if (!live) return;
        setState(
          data === null
            ? { status: "empty", data: null, error: null }
            : { status: "ready", data, error: null },
        );
      })
      .catch((error: unknown) => {
        if (!live || controller.signal.aborted || isAbortError(error)) return;
        // No fundamentals / no filings is an answer. The API's 404 says
        // "security not in the identity master" or "snapshot gone"; neither is a
        // failure of this panel, so neither draws an error banner.
        if (error instanceof ApiError && error.status === 404) {
          setState({ status: "empty", data: null, error: null });
          return;
        }
        setState({
          status: "error",
          data: null,
          error: error instanceof Error ? error.message : "The request failed",
        });
      });

    return () => {
      live = false;
      controller.abort();
    };
    // `load` is a module-level function for every caller here; `attempt` is the
    // retry channel. The body is also keyed by symbol, so a symbol change
    // remounts this hook rather than mutating it.
  }, [symbol, attempt, load]);

  return { ...state, retry: React.useCallback(() => setAttempt((n) => n + 1), []) };
}

async function loadFundamentals(
  symbol: string,
  signal: AbortSignal,
): Promise<FundamentalsData | null> {
  const body = await getJson(`/engine/fundamentals/${encodeURIComponent(symbol)}`, signal);
  return parseFundamentals(body);
}

async function loadInsider(symbol: string, signal: AbortSignal): Promise<InsiderData | null> {
  const body = await getJson(`/engine/insider/${encodeURIComponent(symbol)}`, signal);
  return parseInsider(body);
}

async function loadNews(symbol: string, signal: AbortSignal): Promise<NewsData | null> {
  const body = await getJson(`/news/${encodeURIComponent(symbol)}?limit=20`, signal);
  return parseNews(body);
}

async function selectOwnership(
  symbol: string,
  snapshot: string,
  signal: AbortSignal,
): Promise<ConditionalAnswer> {
  return postJsonConditional(
    "/screens/select",
    {
      mode: "symbols",
      source: "tradingview",
      snapshot_id: snapshot,
      symbols: [symbol],
      columns: [...OWNERSHIP_COLUMNS],
    },
    { signal },
  );
}

async function loadOwnership(
  symbol: string,
  signal: AbortSignal,
): Promise<OwnershipData | null> {
  // The id comes from the session cache, not from a request per symbol (F2).
  const first = await readSnapshotId();
  let answer = await selectOwnership(symbol, first, signal);

  if (answer.status === 404) {
    // 404 on this route means one thing: that snapshot does not exist any
    // more. The cached id is stale, so drop it, read once more and retry once
    // — never in a loop, and never when the re-read returns the same id (the
    // snapshot would just be gone, which is the panel's empty state below).
    resetSnapshotCache();
    const second = await readSnapshotId();
    if (second === first) return null;
    answer = await selectOwnership(symbol, second, signal);
  }

  // The selection route answers 404 for an unknown snapshot. That is a missing
  // enrichment, not a broken panel: empty state, no banner.
  if (answer.status === 404) return null;
  if (answer.status < 200 || answer.status >= 300) {
    throw new ApiError(answer.status, readErrorMessage(answer.body, answer.status));
  }

  // The answer names the snapshot it was resolved against; if a scan rotated
  // mid-session, that id is the new session constant.
  adoptSnapshotId(answer.body);
  return parseOwnership(answer.body, symbol);
}

export interface DetailResources {
  fundamentals: Resource<FundamentalsData>;
  ownership: Resource<OwnershipData>;
  insiders: Resource<InsiderData>;
  news: Resource<NewsData>;
}

/**
 * Four reads, four states, one mount. Called from a body keyed by `symbol`, so
 * switching symbols discards the whole state tree and aborts every request in
 * flight before the new header can pair with the old numbers.
 */
export function useDetail(symbol: string): DetailResources {
  return {
    fundamentals: useResource(symbol, loadFundamentals),
    ownership: useResource(symbol, loadOwnership),
    insiders: useResource(symbol, loadInsider),
    news: useResource(symbol, loadNews),
  };
}

export { LOADING as LOADING_STATE };
