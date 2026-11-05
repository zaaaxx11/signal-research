/**
 * Stale-while-revalidate cache: render from cache instantly, refresh in background.
 *
 * Any data hook (useAlt, useMapData, useNews, useGroups, useInsider) calls
 * `cacheGet` before fetching. When a cache hit exists, the hook returns the
 * stale payload immediately AND still fetches fresh data. The page paints
 * old data on navigation (zero spinner), then swaps in fresh data when the
 * background fetch resolves. Cache lives in sessionStorage per tab, so two
 * tabs never share a stale value, and a reload is a cold start.
 */
export interface CacheEntry {
  /** The raw payload, as JSON. */
  readonly data: unknown;
  /** epoch ms when the entry was written. */
  readonly storedAt: number;
}

/** Default: five minutes. Short enough that a 60 s backend cache won't mask a move. */
export const DEFAULT_TTL_MS = 300_000;

/** Maximum entries before oldest is evicted (a memory budget, not a hit target). */
const MAX_ENTRIES = 128;

const STORE_KEY = "ztockz:query-cache";

function readStore(): Map<string, CacheEntry> {
  try {
    const raw = sessionStorage.getItem(STORE_KEY);
    if (raw === null) return new Map();
    const parsed = JSON.parse(raw) as [string, CacheEntry][];
    return new Map(parsed);
  } catch {
    return new Map();
  }
}

function writeStore(map: Map<string, CacheEntry>): void {
  try {
    sessionStorage.setItem(STORE_KEY, JSON.stringify([...map]));
  } catch {
    // sessionStorage full — drop oldest half and retry once
    const keys = [...map.keys()];
    for (let i = 0; i < Math.ceil(keys.length / 2); i += 1) map.delete(keys[i]);
    try {
      sessionStorage.setItem(STORE_KEY, JSON.stringify([...map]));
    } catch {
      // still full — give up, cache miss for this session
    }
  }
}

/**
 * Read from cache. Returns the stale payload if within ttl, or null on miss.
 * Also prunes expired entries on every read (amortised O(1) housekeeping).
 */
export function cacheGet(key: string, ttlMs: number = DEFAULT_TTL_MS): unknown | null {
  const map = readStore();
  const entry = map.get(key);
  if (entry === undefined) return null;
  if (Date.now() - entry.storedAt >= ttlMs) {
    map.delete(key);
    writeStore(map);
    return null;
  }
  return entry.data;
}

/**
 * Write to cache. Evicts oldest if over MAX_ENTRIES. The key should encode
 * the request parameters (e.g. "/market/alt?class=crypto&per=50").
 */
export function cacheSet(key: string, data: unknown): void {
  const map = readStore();
  map.set(key, { data, storedAt: Date.now() });
  // Evict oldest (Map keys are insertion-ordered)
  while (map.size > MAX_ENTRIES) {
    const oldest = map.keys().next().value as string;
    map.delete(oldest);
  }
  writeStore(map);
}

/** Clear the entire cache (user switcher, logout). */
export function cacheClear(): void {
  try {
    sessionStorage.removeItem(STORE_KEY);
  } catch {
    // best-effort
  }
}