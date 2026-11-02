/**
 * The ticker page's reads, one state machine per resource.
 *
 * Seven sources open in parallel, because each answers a different question
 * and none of them should hold another hostage:
 *
 *   * `GET /engine/profile/{symbol}` — the header's quote fields. It does not
 *     exist on every deployment yet, so a `404` is an **empty answer**, not a
 *     fault: the header falls back to the selection row and prints what is
 *     known.
 *   * `GET /engine/security/{symbol}` — identity: company, sector, industry.
 *   * `GET /engine/fundamentals/{symbol}` — the statement-derived metrics the
 *     snapshot falls back to when its own endpoint is unavailable.
 *   * `GET /engine/snapshot/{symbol}` — the 84-cell snapshot's one read, with
 *     a unit per key (see `useSnapshot`; Overview is its only customer).
 *   * `GET /engine/technicals/{symbol}` and `GET /engine/risk/{symbol}` — the
 *     Overview's technicals block: moving averages, RSI, momentum, WTD/MTD and
 *     the benchmark-relative risk group. Each is its own failure.
 *   * `POST /screens/select` in symbols mode — the scan row: price, change,
 *     market cap, volume, previous close and the six ownership cells.
 *   * `GET /engine/peers/{symbol}?limit=10` — the industry list.
 *   * `GET /engine/insider/{symbol}?limit=20` and `GET /news/{symbol}` — the
 *     two lists the overview shortens to ten and the tabs show in full.
 *
 * A `404` from any read is an empty state; only a transport or 5xx failure is
 * an error, and only inside the panel that needed it. Every effect aborts on
 * unmount and drops a late answer, so a symbol change cannot paint AAPL's
 * price under MSFT's header.
 */

import * as React from "react";

import { ApiError, getJson, postJsonConditional, readErrorMessage } from "@/lib/api";
import type { SnapshotDocument } from "./snapshot";
import { parseSnapshot } from "./snapshot";
import {
  parseRisk,
  parseTechnicals,
  type MetricDocument,
} from "./technicals";
import {
  parseFundamentals,
  parseInsider,
  parseNews,
  readSnapshotId,
  type FundamentalsData,
  type InsiderData,
  type NewsData,
  type OwnershipData,
  type Resource,
  type ResourceState,
} from "@/detail/useDetail";

import { parseOptions, type OptionsChain } from "./options";


export type TickerTabId =
  | "overview"
  | "statements"
  | "ownership"
  | "insider"
  | "filings"
  | "news"
  | "chart"
  | "options";

export interface Peer {
  readonly symbol: string;
  readonly name: string;
  readonly exchange: string | null;
  readonly industry: string | null;
  readonly country: string | null;
}

export interface PeersData {
  readonly symbol: string;
  readonly industry: string | null;
  readonly asOf: string | null;
  readonly peers: readonly Peer[];
}

export interface TickerProfile {
  readonly symbol: string;
  readonly company: string | null;
  readonly sector: string | null;
  readonly industry: string | null;
  readonly indexMembership: string | null;
  readonly price: number | null;
  readonly change: number | null;
  readonly volume: number | null;
  readonly marketCap: number | null;
  readonly prevClose: number | null;
  readonly asOf: string | null;
  /** The document unchanged, for the snapshot's key lookup. */
  readonly raw: Readonly<Record<string, unknown>>;
}

export interface SecurityIdentity {
  readonly symbol: string;
  readonly name: string | null;
  readonly sector: string | null;
  readonly industry: string | null;
  readonly exchange: string | null;
  readonly country: string | null;
}

export interface CompanyOfficer {
  readonly name: string;
  readonly title: string | null;
}

export interface CompanyProfile {
  readonly symbol: string;
  readonly name: string | null;
  readonly description: string | null;
  readonly officers: readonly CompanyOfficer[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function firstText(record: Record<string, unknown>, keys: readonly string[]): string | null {
  for (const key of keys) {
    const value = text(record[key]);
    if (value !== null) return value;
  }
  return null;
}

function firstNumber(record: Record<string, unknown>, keys: readonly string[]): number | null {
  for (const key of keys) {
    const value = num(record[key]);
    if (value !== null) return value;
  }
  return null;
}

/**
 * The columns the page asks the selection route for. Fifteen, well under the
 * route's forty-column cap; `float_shares` is the join key the existing detail
 * panel already asks for (the catalogue spells the same fact `shares_float`).
 */
export const TICKER_COLUMNS = [
  "price",
  "change",
  "market_cap",
  "volume",
  "prev_close",
  "index_membership",
  "shares_outstanding",
  "float_shares",
  "short_ratio",
  "insider_own",
  "inst_own",
  "short_float",
] as const;

/** One peer row, or null when the payload cannot name a symbol. */
export function parsePeer(raw: unknown): Peer | null {
  if (!isRecord(raw)) return null;
  const symbol = text(raw["symbol"]);
  if (symbol === null) return null;
  return {
    symbol: symbol.toUpperCase(),
    name: text(raw["name"]) ?? symbol,
    exchange: text(raw["exchange"]),
    industry: text(raw["industry"]),
    country: text(raw["country"]),
  };
}

export function parsePeers(body: unknown): PeersData | null {
  if (!isRecord(body) || !Array.isArray(body["peers"])) return null;
  const peers: Peer[] = [];
  const seen = new Set<string>();
  for (const raw of body["peers"]) {
    const peer = parsePeer(raw);
    if (peer === null || seen.has(peer.symbol)) continue;
    seen.add(peer.symbol);
    peers.push(peer);
  }
  const symbol = text(body["symbol"]);
  if (symbol === null || peers.length === 0) return null;
  return {
    symbol,
    industry: text(body["industry"]),
    asOf: text(body["as_of"]),
    peers,
  };
}

/**
 * The profile document, tolerating the spellings a backend landing in stages
 * may use. Unknown fields are kept in `raw`, not discarded: the snapshot may
 * read a key this parser does not label.
 */
export function parseProfile(body: unknown): TickerProfile | null {
  if (!isRecord(body)) return null;
  const symbol = text(body["symbol"]);
  if (symbol === null) return null;
  const index = body["index_membership"] ?? body["index"] ?? body["indices"];
  return {
    symbol: symbol.toUpperCase(),
    company: firstText(body, ["company", "name", "company_name"]),
    sector: text(body["sector"]),
    industry: text(body["industry"]),
    indexMembership: Array.isArray(index)
      ? index.filter((entry): entry is string => typeof entry === "string").join(", ") || null
      : text(index),
    price: firstNumber(body, ["price", "last", "last_price"]),
    change: firstNumber(body, ["change", "change_pct", "change_percent"]),
    volume: firstNumber(body, ["volume"]),
    marketCap: firstNumber(body, ["market_cap", "marketCap"]),
    prevClose: firstNumber(body, ["prev_close", "previous_close", "prevClose"]),
    asOf: text(body["as_of"]),
    raw: body,
  };
}

export function parseSecurity(body: unknown): SecurityIdentity | null {
  if (!isRecord(body)) return null;
  const symbol = text(body["symbol"]);
  if (symbol === null) return null;
  return {
    symbol: symbol.toUpperCase(),
    name: text(body["name"]),
    sector: text(body["sector"]),
    industry: text(body["industry"]),
    exchange: text(body["exchange"]),
    country: text(body["country"]),
  };
}

/**
 * `GET /engine/company/{symbol}`: a business description and the officers.
 *
 * A document with neither a description nor a named officer is **empty**, not
 * a profile with blank fields: the panel owes the reader one sentence, not a
 * heading over nothing. The description keeps its paragraphs as spaces (the
 * block is one clamped paragraph); an officer without a name is dropped, and a
 * title may be absent without taking the name with it.
 */
export function parseCompany(body: unknown, symbol: string): CompanyProfile | null {
  if (!isRecord(body)) return null;
  const description = text(body["description"]);
  const officers: CompanyOfficer[] = [];
  if (Array.isArray(body["officers"])) {
    for (const raw of body["officers"]) {
      if (!isRecord(raw)) continue;
      const name = text(raw["name"]);
      if (name === null) continue;
      officers.push({ name, title: text(raw["title"]) });
    }
  }
  if (description === null && officers.length === 0) return null;
  return {
    symbol: (text(body["symbol"]) ?? symbol).toUpperCase(),
    name: text(body["name"]),
    description: description === null ? null : description.replace(/\s+/g, " ").trim(),
    officers,
  };
}

/** The row for this symbol, or the first row when the route answered one. */
export function parseSelectedRow(body: unknown, symbol: string): OwnershipData | null {
  if (!isRecord(body) || !Array.isArray(body["rows"])) return null;
  const wanted = symbol.trim().toUpperCase();
  for (const raw of body["rows"]) {
    if (!isRecord(raw)) continue;
    const rowSymbol = text(raw["symbol"]) ?? text(raw["ticker"]);
    if (rowSymbol === null || rowSymbol.toUpperCase() !== wanted) continue;
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

function useResource<T>(
  symbol: string,
  load: (symbol: string, signal: AbortSignal) => Promise<T | null>,
  enabled = true,
): Resource<T> {
  const [state, setState] = React.useState<ResourceState<T>>({ ...LOADING });
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(() => {
    if (!enabled) return;
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
  }, [symbol, attempt, load, enabled]);

  return { ...state, retry: React.useCallback(() => setAttempt((n) => n + 1), []) };
}

async function loadProfile(symbol: string, signal: AbortSignal): Promise<TickerProfile | null> {
  const body = await getJson(`/engine/profile/${encodeURIComponent(symbol)}`, signal);
  return parseProfile(body);
}

async function loadSecurity(symbol: string, signal: AbortSignal): Promise<SecurityIdentity | null> {
  const body = await getJson(`/engine/security/${encodeURIComponent(symbol)}`, signal);
  return parseSecurity(body);
}

async function loadFundamentals(
  symbol: string,
  signal: AbortSignal,
): Promise<FundamentalsData | null> {
  const body = await getJson(`/engine/fundamentals/${encodeURIComponent(symbol)}`, signal);
  return parseFundamentals(body);
}

async function loadPeers(symbol: string, signal: AbortSignal): Promise<PeersData | null> {
  const body = await getJson(`/engine/peers/${encodeURIComponent(symbol)}?limit=10`, signal);
  return parsePeers(body);
}

async function loadCompany(symbol: string, signal: AbortSignal): Promise<CompanyProfile | null> {
  const body = await getJson(`/engine/company/${encodeURIComponent(symbol)}`, signal);
  return parseCompany(body, symbol);
}

async function loadInsider(symbol: string, signal: AbortSignal): Promise<InsiderData | null> {
  const body = await getJson(`/engine/insider/${encodeURIComponent(symbol)}?limit=20`, signal);
  return parseInsider(body);
}

async function loadNews(symbol: string, signal: AbortSignal): Promise<NewsData | null> {
  const body = await getJson(`/news/${encodeURIComponent(symbol)}?limit=20`, signal);
  return parseNews(body);
}

async function loadOptions(symbol: string, signal: AbortSignal): Promise<OptionsChain | null> {
  const body = await getJson(`/engine/options/${encodeURIComponent(symbol)}`, signal);
  return parseOptions(body);
}

async function loadRow(symbol: string, signal: AbortSignal): Promise<OwnershipData | null> {
  const snapshot = await readSnapshotId();
  const answer = await postJsonConditional(
    "/screens/select",
    {
      mode: "symbols",
      source: "tradingview",
      snapshot_id: snapshot,
      symbols: [symbol],
      columns: [...TICKER_COLUMNS],
    },
    { signal },
  );
  if (answer.status === 404) return null;
  if (answer.status < 200 || answer.status >= 300) {
    throw new ApiError(answer.status, readErrorMessage(answer.body, answer.status));
  }
  return parseSelectedRow(answer.body, symbol);
}

export interface TickerResources {
  readonly profile: Resource<TickerProfile>;
  readonly security: Resource<SecurityIdentity>;
  readonly fundamentals: Resource<FundamentalsData>;
  readonly row: Resource<OwnershipData>;
  readonly peers: Resource<PeersData>;
  readonly insiders: Resource<InsiderData>;
  readonly news: Resource<NewsData>;
  readonly options: Resource<OptionsChain>;
}

export interface HeaderView {
  readonly symbol: string;
  readonly company: string | null;
  readonly sector: string | null;
  readonly industry: string | null;
  readonly indexMembership: string | null;
  readonly price: number | null;
  readonly change: number | null;
  readonly volume: number | null;
  readonly marketCap: number | null;
  readonly prevClose: number | null;
}

function metricText(fundamentals: FundamentalsData | null, key: string): string | null {
  const value = fundamentals?.metrics[key]?.value;
  return text(value);
}

/** `index_membership` is a string on one route and a list on another. */
function indexText(value: unknown): string | null {
  if (typeof value === "string") return text(value);
  if (Array.isArray(value)) {
    const entries = value.filter((entry): entry is string => typeof entry === "string");
    return entries.length > 0 ? entries.join(", ") : null;
  }
  return null;
}

/**
 * The header band's facts, in precedence order: the profile document wins
 * (it is purpose-built for this band), then the identity master, then the scan
 * row, then the fundamentals document. Each fallback is a real source, never a
 * derivation from another field.
 */
export function headerView(resources: TickerResources, symbol: string): HeaderView {
  const profile = resources.profile.data;
  const security = resources.security.data;
  const row = resources.row.data;
  const fundamentals = resources.fundamentals.data;

  return {
    symbol,
    company:
      profile?.company ??
      security?.name ??
      metricText(fundamentals, "company") ??
      null,
    sector: profile?.sector ?? security?.sector ?? null,
    industry:
      profile?.industry ??
      security?.industry ??
      metricText(fundamentals, "industry") ??
      null,
    indexMembership: profile?.indexMembership ?? indexText(row?.["index_membership"]),
    price: profile?.price ?? num(row?.["price"]),
    change: profile?.change ?? num(row?.["change"]),
    volume: profile?.volume ?? num(row?.["volume"]),
    marketCap: profile?.marketCap ?? num(row?.["market_cap"]),
    prevClose: profile?.prevClose ?? num(row?.["prev_close"]),
  };
}

/**
 * Seven reads, opened by what the reader is looking at.
 *
 * The header's four (profile, identity, fundamentals, the scan row) always
 * open: every tab draws the band. Peers belong to Overview; insider rows are
 * needed by Overview, Insider and Filings; news by Overview and News. A deep
 * link to Statements therefore pays for the band and the collector, not for a
 * peer list nobody will see.
 */
export function useTicker(symbol: string, active: TickerTabId = "overview"): TickerResources {
  const overview = active === "overview";
  return {
    profile: useResource(symbol, loadProfile),
    security: useResource(symbol, loadSecurity),
    fundamentals: useResource(symbol, loadFundamentals),
    row: useResource(symbol, loadRow),
    peers: useResource(symbol, loadPeers, overview),
    insiders: useResource(
      symbol,
      loadInsider,
      overview || active === "insider" || active === "filings",
    ),
    news: useResource(symbol, loadNews, overview || active === "news"),
    options: useResource(symbol, loadOptions, active === "options"),
  };
}

/**
 * The company profile is its own read because it is its own block: Overview
 * mounts it above the peers, and no other tab mounts it. Keeping it out of
 * `useTicker` means a deep link to Statements never asks for a description.
 */
export function useCompany(symbol: string): Resource<CompanyProfile> {
  return useResource(symbol, loadCompany);
}

/** What the snapshot grid's own read is doing, and what to say about it. */
export interface SnapshotState {
  readonly data: SnapshotDocument | null;
  readonly status: "loading" | "ready" | "unavailable";
  /**
   * One sentence for the grid's header when the read failed. `null` while the
   * request is in flight and after it answers — a grid of em dashes is only
   * honest when the endpoint said the values are null.
   */
  readonly notice: string | null;
}

/**
 * `GET /engine/snapshot/{symbol}`: one read for the 84-cell grid.
 *
 * The other resources treat a 404 as an empty answer and drop its message.
 * This one cannot: the endpoint is new, and while a deployment does not serve
 * it the grid falls back to the fundamentals document and the scan row. The
 * reader is owed the reason in one line, not twelve silent em dashes, so the
 * message survives into `notice`.
 *
 * Fired once per symbol while the overview is the open tab: leaving the tab
 * aborts an in-flight read, and coming back is a fresh read of the same URL.
 */
export function useSnapshot(symbol: string, enabled = true): SnapshotState {
  const [state, setState] = React.useState<SnapshotState>({
    status: "loading",
    data: null,
    notice: null,
  });

  React.useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let live = true;
    setState({ status: "loading", data: null, notice: null });

    getJson(`/engine/snapshot/${encodeURIComponent(symbol)}`, controller.signal)
      .then((body) => {
        if (!live) return;
        const data = parseSnapshot(body, symbol);
        if (data === null) {
          setState({
            status: "unavailable",
            data: null,
            notice:
              "The snapshot endpoint answered without values. Showing the fundamentals document and the latest scan row.",
          });
          return;
        }
        setState({ status: "ready", data, notice: null });
      })
      .catch((error: unknown) => {
        if (!live || controller.signal.aborted || isAbortError(error)) return;
        const reason =
          error instanceof Error && error.message.length > 0 ? error.message : "the request failed";
        setState({
          status: "unavailable",
          data: null,
          notice: `Snapshot unavailable — ${reason}. Showing the fundamentals document and the latest scan row.`,
        });
      });

    return () => {
      live = false;
      controller.abort();
    };
  }, [symbol, enabled]);

  return state;
}

/** What one derived-metric read is doing, and the reason when it failed. */
export type MetricStatus = "loading" | "ready" | "error";

export interface MetricResource {
  readonly status: MetricStatus;
  readonly data: MetricDocument | null;
  /** The endpoint's own message. A 404 is a failure here, not an empty state. */
  readonly error: string | null;
  readonly retry: () => void;
}

/**
 * One read of a derived document, fired once per symbol.
 *
 * Unlike the identity resources, a 404 is **not** an empty answer: the block
 * the reader is looking at exists to carry these numbers, so the reason must
 * reach the page. The two reads are independent — the technicals failing must
 * not take the risk group with it — which is why each hook owns its own
 * controller and error string.
 */
function useMetricDocument(
  symbol: string,
  kind: "technicals" | "risk",
  parse: (body: unknown, symbol: string) => MetricDocument | null,
): MetricResource {
  const [state, setState] = React.useState<Omit<MetricResource, "retry">>({
    status: "loading",
    data: null,
    error: null,
  });
  const [attempt, setAttempt] = React.useState(0);
  const retry = React.useCallback(() => setAttempt((value) => value + 1), []);

  React.useEffect(() => {
    const controller = new AbortController();
    let live = true;
    setState({ status: "loading", data: null, error: null });

    getJson(`/engine/${kind}/${encodeURIComponent(symbol)}`, controller.signal)
      .then((body) => {
        if (!live) return;
        const data = parse(body, symbol);
        if (data === null) {
          setState({
            status: "error",
            data: null,
            error: "The endpoint answered without any values.",
          });
          return;
        }
        setState({ status: "ready", data, error: null });
      })
      .catch((error: unknown) => {
        if (!live || controller.signal.aborted || isAbortError(error)) return;
        const reason =
          error instanceof ApiError
            ? error.message
            : error instanceof Error && error.message.length > 0
              ? error.message
              : "the request failed";
        setState({ status: "error", data: null, error: reason });
      });

    return () => {
      live = false;
      controller.abort();
    };
  }, [symbol, kind, parse, attempt]);

  return { ...state, retry };
}

/** `GET /engine/technicals/{symbol}`. Overview's block is its only customer. */
export function useTechnicals(symbol: string): MetricResource {
  return useMetricDocument(symbol, "technicals", parseTechnicals);
}

/** `GET /engine/risk/{symbol}` — Alpha, Beta and the rest, vs the S&P 500. */
export function useRisk(symbol: string): MetricResource {
  return useMetricDocument(symbol, "risk", parseRisk);
}
