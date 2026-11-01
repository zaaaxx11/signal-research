/**
 * The server's filter vocabulary, read from `GET /screens/filters`.
 *
 * The panel does not own a list of labels, groups or values: every option the
 * header offers is one the API published, together with the predicate it
 * compiles to. That is the same rule the schema read follows — a client that
 * spells an option itself is a client that offers a value the API refuses.
 *
 * Two shapes travel together:
 *
 *   groups[]           the five catalogue tabs, each a list of controls
 *   groups[].controls  one control with `key` + `op` (a filter) or without
 *                      (`order`, `signal`, which are request settings)
 *
 * **Tier and entitlement fields are optional here.** A deployment that has not
 * published them yet is not a broken deployment: `tier` is read when present
 * and never required, and an option without one is simply not marked. The same
 * goes for every other field: a missing `options` array is an empty control,
 * not a parse failure, because the header can only lose its own select — never
 * the screen — when one group is thinner than expected.
 *
 * The document is fetched once per page load and memoised in this module. It is
 * ~460 KB uncompressed, so a second read per keystroke, per select or per
 * StrictMode double-effect would be a real cost paid for nothing.
 */

import * as React from "react";

import { getJson } from "@/lib/api";
import type { FilterOp, WireFilter } from "@/filters/types";
import { FILTER_OPS } from "@/filters/types";

export const FILTERS_PATH = "/screens/filters";

/**
 * How long the boot read may take before it is abandoned.
 *
 * The screen's own first request waits for this document (the link's filters
 * are validated against it), so a read that never settles would hold the grid
 * on its skeleton forever. Ten seconds is far longer than the same-origin read
 * ever takes and short enough that a broken deployment is a sentence, not a
 * hung page.
 */
export const FILTERS_TIMEOUT_MS = 10_000;

/** The id the panel's "show every group" choice uses; never a group id. */
export const ALL_GROUPS = "all";

/** One pickable value, exactly as the API publishes it. */
export interface FilterOption {
  id: string;
  label: string;
  /** What a request sends in `value`; the id is namespaced, this is not. */
  token: string | null;
  /** The predicate this option compiles to, or null when it cannot run. */
  predicate: string | null;
  available: boolean;
  reason: string | null;
  /** Present once the deployment publishes plans; absent is not an error. */
  tier?: string;
}

/** One control: a filter when it names a `key`, a request setting otherwise. */
export interface FilterControl {
  id: string;
  label: string;
  key: string | null;
  control: string;
  form: string;
  op: FilterOp | null;
  options: FilterOption[];
  available: boolean;
  status: string | null;
  reason: string | null;
  hint: string | null;
  note: string | null;
  /** The unit a number control's values are measured in (`usd`, `percent`…). */
  unit: string | null;
  /** Where a select's options come from, when the document only references them. */
  optionsSource: string | null;
  tier?: string;
  disposition: string | null;
}

/** One group: a tab of the panel, in the order the API published it. */
export interface FilterGroup {
  id: string;
  label: string;
  controls: FilterControl[];
}

export interface FiltersDocument {
  /** The server's own version string; opaque to this client, used for cache keys. */
  version: string;
  groups: FilterGroup[];
  /** The `order` control, when published: the request's sort vocabulary. */
  order: FilterControl | null;
  /** The `signal` control, when published: the request's signal vocabulary. */
  signal: FilterControl | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function textOrNull(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function bool(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

/** One operator, or null when the control is not a filter. */
function readOp(value: unknown): FilterOp | null {
  return typeof value === "string" && (FILTER_OPS as readonly string[]).includes(value)
    ? (value as FilterOp)
    : null;
}

function readOption(value: unknown): FilterOption | null {
  if (!isRecord(value)) return null;
  const id = text(value["id"]);
  if (id.length === 0) return null;
  const option: FilterOption = {
    id,
    label: text(value["label"], id).replace(/\s+/g, " ").trim() || id,
    token: textOrNull(value["token"]),
    predicate: textOrNull(value["predicate"]),
    available: bool(value["available"], true),
    reason: textOrNull(value["reason"]),
  };
  if (typeof value["tier"] === "string") option.tier = value["tier"];
  return option;
}

function readControl(value: unknown): FilterControl | null {
  if (!isRecord(value)) return null;
  const id = text(value["id"]);
  if (id.length === 0) return null;
  const rawOptions = Array.isArray(value["options"]) ? value["options"] : [];
  const options: FilterOption[] = [];
  for (const raw of rawOptions) {
    const option = readOption(raw);
    if (option) options.push(option);
  }
  const control: FilterControl = {
    id,
    label: text(value["label"], id).replace(/\s+/g, " ").trim() || id,
    key: textOrNull(value["key"]),
    control: text(value["control"], "select"),
    form: text(value["form"], "single"),
    op: readOp(value["op"]),
    options,
    available: bool(value["available"], true),
    status: textOrNull(value["status"]),
    reason: textOrNull(value["reason"]),
    hint: textOrNull(value["hint"]),
    note: textOrNull(value["note"]),
    unit: textOrNull(value["unit"]),
    optionsSource: textOrNull(value["options_source"]),
    disposition: textOrNull(value["disposition"]),
  };
  if (typeof value["tier"] === "string") control.tier = value["tier"];
  return control;
}

/**
 * The served document → the vocabulary this header may draw.
 *
 * A body with no `groups` table is a boot error, for the same reason an empty
 * filter schema is: the panel would be a lie about the product ("nothing can be
 * filtered") rather than a failure the reader can see and retry.
 */
export function parseFiltersDocument(body: unknown): FiltersDocument {
  if (!isRecord(body)) throw new Error("The filter document was not an object");
  const rawGroups = body["groups"];
  if (!Array.isArray(rawGroups) || rawGroups.length === 0) {
    throw new Error("The filter document served no groups");
  }

  const groups: FilterGroup[] = [];
  for (const raw of rawGroups) {
    if (!isRecord(raw)) continue;
    const id = text(raw["id"]);
    if (id.length === 0) continue;
    const rawControls = Array.isArray(raw["controls"]) ? raw["controls"] : [];
    const controls: FilterControl[] = [];
    for (const rawControl of rawControls) {
      const control = readControl(rawControl);
      if (control) controls.push(control);
    }
    groups.push({ id, label: text(raw["label"], id).replace(/\s+/g, " ").trim() || id, controls });
  }
  if (groups.length === 0) throw new Error("The filter document served no usable groups");

  const everyControl = groups.flatMap((group) => group.controls);
  return {
    version: text(body["schema_version"], ""),
    groups,
    order: everyControl.find((control) => control.control === "order") ?? null,
    signal: everyControl.find((control) => control.control === "signal") ?? null,
  };
}

// ---------------------------------------------------------------------------
// One fetch per page load
// ---------------------------------------------------------------------------

let cached: Promise<FiltersDocument> | null = null;

/**
 * The boot read. Concurrent callers share one request; a rejection clears the
 * cache so the retry button really retries instead of replaying the failure.
 */
export function loadFiltersDocument(): Promise<FiltersDocument> {
  if (cached === null) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), FILTERS_TIMEOUT_MS);
    cached = getJson(FILTERS_PATH, controller.signal)
      .then(parseFiltersDocument)
      .catch((error: unknown) => {
        cached = null;
        if (error instanceof DOMException && error.name === "AbortError") {
          throw new Error("The filter document took too long to load");
        }
        throw error;
      })
      .finally(() => clearTimeout(timer));
  }
  return cached;
}

/** Test seam and the retry path: forget the cached document. */
export function resetFiltersDocumentCache(): void {
  cached = null;
}

export interface FiltersDocumentResult {
  doc: FiltersDocument | null;
  status: "loading" | "ready" | "error";
  error: string | null;
  retry: () => void;
}

export function useFiltersDocument(): FiltersDocumentResult {
  const [state, setState] = React.useState<Omit<FiltersDocumentResult, "retry">>({
    doc: null,
    status: "loading",
    error: null,
  });
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(() => {
    let alive = true;
    setState((previous) => ({ ...previous, status: "loading", error: null }));

    loadFiltersDocument().then(
      (doc) => {
        if (alive) setState({ doc, status: "ready", error: null });
      },
      (error: unknown) => {
        if (!alive) return;
        setState({
          doc: null,
          status: "error",
          error: error instanceof Error ? error.message : "Could not load the filter document",
        });
      },
    );

    return () => {
      alive = false;
    };
  }, [attempt]);

  const retry = React.useCallback(() => {
    resetFiltersDocumentCache();
    setAttempt((value) => value + 1);
  }, []);

  return { ...state, retry };
}

// ---------------------------------------------------------------------------
// Reading one control
// ---------------------------------------------------------------------------

/**
 * True when this control can put a predicate in the request at all.
 *
 * A key and an operator are the whole test: a *select* control also publishes
 * `options`, a *number* or *text* control carries none and is answered by an
 * input, and a control with neither key nor operator (`order`, `signal`) is a
 * request setting rather than a filter.
 */
export function isFilterControl(control: FilterControl): boolean {
  return control.key !== null && control.op !== null;
}

/**
 * The value a request sends for one option.
 *
 * The token is the API's own spelling; the id is the panel's stable key. They
 * differ for a bucket (`cap:mega` versus `mega`) and the request takes the
 * token, so the token wins whenever it is published.
 */
export function optionValue(option: FilterOption): string {
  return option.token ?? option.id;
}

/**
 * The predicate choosing this option writes into the one filter list.
 *
 * A multi-value control (`form: "multi"`, `op: "in"`) takes a list, because
 * that is the shape its operator compiles; everything else takes one value.
 * An unavailable option, or a control that is not a filter, produces nothing —
 * a value nobody can send must not look like one that can.
 */
export function optionFilter(control: FilterControl, option: FilterOption): WireFilter | null {
  if (!isFilterControl(control) || !control.available) return null;
  if (!option.available) return null;
  const key = control.key;
  const op = control.op;
  if (key === null || op === null) return null;
  const value = optionValue(option);
  if (value.length === 0) return null;
  if (op === "in" || control.form === "multi") {
    return { key, op: op === "in" ? "in" : op, value: [value] };
  }
  return { key, op, value };
}

/**
 * The document's filter vocabulary, in the shape a registry can carry.
 *
 * One entry per key a control names, with every value it publishes. This is
 * what keeps `?f=` honest when the schema and the panel document disagree for a
 * release: the document says what runs, so a link naming its key survives.
 */
export function publishedFilters(
  doc: FiltersDocument | null,
): Map<string, { key: string; label: string; op: FilterOp; values: Map<string, string> }> {
  const index = new Map<
    string,
    { key: string; label: string; op: FilterOp; values: Map<string, string> }
  >();
  if (!doc) return index;
  for (const group of doc.groups) {
    for (const control of group.controls) {
      if (control.key === null || control.op === null) continue;
      let entry = index.get(control.key);
      if (!entry) {
        entry = { key: control.key, label: control.label, op: control.op, values: new Map() };
        index.set(control.key, entry);
      }
      for (const option of control.options) {
        const value = optionValue(option);
        if (value.length === 0) continue;
        entry.values.set(value, option.label);
      }
    }
  }
  return index;
}

/** The option a select value names: `controlId` + the option's own id. */
export function optionKey(control: FilterControl, option: FilterOption): string {
  return `${control.id}\u0000${option.id}`;
}

/** Split a select value back into the control and option it names. */
export function parseOptionKey(value: string): { controlId: string; optionId: string } | null {
  const at = value.indexOf("\u0000");
  if (at <= 0) return null;
  return { controlId: value.slice(0, at), optionId: value.slice(at + 1) };
}

/** The control in a group with this id, if it is still published. */
export function controlById(group: FilterGroup, id: string): FilterControl | null {
  return group.controls.find((control) => control.id === id) ?? null;
}
