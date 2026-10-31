/**
 * The filter vocabulary, read from `GET /screens/select/schema`.
 *
 * The registry below this file is *derived*, never written down: the schema is
 * the only place a key, its label, its tab, its kind, its refusal reason and a
 * choice key's option tokens are spelled. That is deliberate — Phase 0's F1 was
 * a client that hardcoded catalogue names the server had already renamed, and
 * the same mistake here would offer a key the API answers with 422.
 *
 * Three shapes live under `x-filter-keys`:
 *
 *   {label, tab, field, kind}   a key that filters one row column
 *   {label, tab, field: null, reason}  a key the API refuses, with why
 *   {label, tab, field: null, choice}  an option-driven key (3 of them)
 *
 * "Usable" is exactly the first and third shapes — a key with a column, or a
 * choice key whose tokens compile. `usableKeys` is derived from those shapes,
 * so a key the server starts refusing disappears from the UI without an edit.
 *
 * The document is fetched once per page load. `loadFilterSchema` keeps the
 * in-flight promise, so React's StrictMode double-effect, the view strip and
 * the drawer share one request instead of three.
 */

import * as React from "react";

import { getJson } from "@/lib/api";
import type { ViewDefinition } from "@/data/screens";

export const FILTER_SCHEMA_PATH = "/screens/select/schema";

/** One option token and the row predicate it compiles to (published by the API). */
export interface ChoiceSpec {
  grammar: string;
  fields: string[];
  windows: Record<string, string | null>;
  /** Token → `"field op number"`, e.g. `"1w10u": "ret_1w lte -10.0"`. */
  options: Record<string, string>;
  unsupported: Record<string, string>;
}

/** One Finviz filter key, exactly as the schema serves it. */
export interface FilterKeyEntry {
  label: string;
  tab: string;
  field: string | null;
  kind?: "number" | "text" | "bool";
  choice?: ChoiceSpec;
  reason?: string;
}

/** One alternative spelling of a catalogue column (`pe`, `market_cap`, …). */
export interface FilterAliasEntry {
  label?: string;
  field: string;
  kind: string;
}

export interface FilterSchema {
  keys: Record<string, FilterKeyEntry>;
  aliases: Record<string, FilterAliasEntry>;
  views: ViewDefinition[];
  keyCount: number;
  aliasCount: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStringRecord(value: unknown): value is Record<string, string> {
  return (
    isRecord(value) &&
    Object.values(value).every((entry) => typeof entry === "string")
  );
}

function readChoice(value: unknown): ChoiceSpec | undefined {
  if (!isRecord(value)) return undefined;
  if (!isStringRecord(value["options"])) return undefined;
  const windows: Record<string, string | null> = {};
  if (isRecord(value["windows"])) {
    for (const [token, column] of Object.entries(value["windows"])) {
      if (typeof column === "string" || column === null) windows[token] = column;
    }
  }
  return {
    grammar: typeof value["grammar"] === "string" ? value["grammar"] : "",
    fields: Array.isArray(value["fields"])
      ? value["fields"].filter((field): field is string => typeof field === "string")
      : [],
    windows,
    options: value["options"],
    unsupported: isStringRecord(value["unsupported"]) ? value["unsupported"] : {},
  };
}

function readKeyEntry(value: unknown): FilterKeyEntry | null {
  if (!isRecord(value)) return null;
  if (typeof value["label"] !== "string" || typeof value["tab"] !== "string") return null;
  const entry: FilterKeyEntry = {
    label: value["label"],
    tab: value["tab"],
    field: typeof value["field"] === "string" ? value["field"] : null,
  };
  const kind = value["kind"];
  if (kind === "number" || kind === "text" || kind === "bool") entry.kind = kind;
  const choice = readChoice(value["choice"]);
  if (choice) entry.choice = choice;
  if (typeof value["reason"] === "string") entry.reason = value["reason"];
  return entry;
}

function readAliasEntry(value: unknown): FilterAliasEntry | null {
  if (!isRecord(value)) return null;
  if (typeof value["field"] !== "string" || typeof value["kind"] !== "string") return null;
  const entry: FilterAliasEntry = { field: value["field"], kind: value["kind"] };
  if (typeof value["label"] === "string") entry.label = value["label"];
  return entry;
}

function isView(value: unknown): value is ViewDefinition {
  if (!isRecord(value)) return false;
  return (
    typeof value["name"] === "string" &&
    typeof value["label"] === "string" &&
    typeof value["job"] === "string" &&
    Array.isArray(value["columns"]) &&
    value["columns"].every((column) => typeof column === "string")
  );
}

/**
 * The served document → the vocabulary this app may offer.
 *
 * Throws on a document that carries no filter table: an empty picker is a lie
 * about the product ("no filters exist"), so a malformed schema is a boot
 * error the shell renders rather than an empty drawer.
 */
export function parseFilterSchema(body: unknown): FilterSchema {
  if (!isRecord(body)) throw new Error("The filter schema was not an object");
  const rawKeys = body["x-filter-keys"];
  if (!isRecord(rawKeys) || Object.keys(rawKeys).length === 0) {
    throw new Error("The filter schema served no x-filter-keys table");
  }

  const keys: Record<string, FilterKeyEntry> = {};
  for (const [key, value] of Object.entries(rawKeys)) {
    const entry = readKeyEntry(value);
    if (entry) keys[key] = entry;
  }
  if (Object.keys(keys).length === 0) {
    throw new Error("The filter schema served no usable filter entries");
  }

  const aliases: Record<string, FilterAliasEntry> = {};
  if (isRecord(body["x-filter-aliases"])) {
    for (const [alias, value] of Object.entries(body["x-filter-aliases"])) {
      const entry = readAliasEntry(value);
      if (entry) aliases[alias] = entry;
    }
  }

  const views = Array.isArray(body["views"]) ? body["views"].filter(isView) : [];
  if (views.length === 0) throw new Error("The filter schema served no views");

  return {
    keys,
    aliases,
    views,
    keyCount: typeof body["x-filter-key-count"] === "number"
      ? body["x-filter-key-count"]
      : Object.keys(keys).length,
    aliasCount: typeof body["x-filter-alias-count"] === "number"
      ? body["x-filter-alias-count"]
      : Object.keys(aliases).length,
  };
}

/** A key that can filter a row today: one column, or a choice vocabulary. */
export function isUsableEntry(entry: FilterKeyEntry): boolean {
  return typeof entry.field === "string" || entry.choice !== undefined;
}

/** The keys the API accepts, in the schema's panel order. */
export function usableKeys(schema: FilterSchema): string[] {
  return Object.entries(schema.keys)
    .filter(([, entry]) => isUsableEntry(entry))
    .map(([key]) => key);
}

/** The keys the API refuses, with the schema's reason. */
export function refusedKeys(schema: FilterSchema): Record<string, string> {
  const refused: Record<string, string> = {};
  for (const [key, entry] of Object.entries(schema.keys)) {
    if (!isUsableEntry(entry)) {
      refused[key] = entry.reason ?? "no screen row carries a value this filter can compare";
    }
  }
  return refused;
}

// ---------------------------------------------------------------------------
// One fetch per page load
// ---------------------------------------------------------------------------

let cached: Promise<FilterSchema> | null = null;

/**
 * The boot fetch. Concurrent callers share one request; a rejection clears the
 * cache so the retry button really retries instead of replaying the failure.
 */
export function loadFilterSchema(): Promise<FilterSchema> {
  if (cached === null) {
    cached = getJson(FILTER_SCHEMA_PATH)
      .then(parseFilterSchema)
      .catch((error: unknown) => {
        cached = null;
        throw error;
      });
  }
  return cached;
}

/** Test seam and the retry path: forget the cached document. */
export function resetFilterSchemaCache(): void {
  cached = null;
}

export interface FilterSchemaResult {
  schema: FilterSchema | null;
  status: "loading" | "ready" | "error";
  error: string | null;
  retry: () => void;
}

export function useFilterSchema(): FilterSchemaResult {
  const [state, setState] = React.useState<Omit<FilterSchemaResult, "retry">>({
    schema: null,
    status: "loading",
    error: null,
  });
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(() => {
    let alive = true;
    setState((previous) => ({ ...previous, status: "loading", error: null }));

    loadFilterSchema().then(
      (schema) => {
        if (alive) setState({ schema, status: "ready", error: null });
      },
      (error: unknown) => {
        if (!alive) return;
        setState({
          schema: null,
          status: "error",
          error: error instanceof Error ? error.message : "Could not load the filter schema",
        });
      },
    );

    return () => {
      alive = false;
    };
  }, [attempt]);

  const retry = React.useCallback(() => {
    resetFilterSchemaCache();
    setAttempt((value) => value + 1);
  }, []);

  return { ...state, retry };
}
