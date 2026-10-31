/**
 * The gate between a typed value and the API.
 *
 * A filter that cannot be honoured is refused **here**, with a sentence that
 * names the field. The API would answer 422 with a message about a row column
 * (`market_cap_usd expects a number`), which is not the field the reader
 * clicked; worse, an inverted range would be a round trip that teaches nothing.
 *
 * The rules mirror `app/screen/filters.py` exactly:
 *
 *   * a number must be finite — `NaN`/`Infinity`/`` are not numbers;
 *   * `between` is two finite numbers, low ≤ high (a reversed range is an
 *     error, not an empty result);
 *   * `in` is at least one value;
 *   * `contains` needs non-empty text;
 *   * a choice key takes one of the schema's published option tokens.
 *
 * "Empty" is an error rather than "no filter": a row the reader added and then
 * left blank is a half-made decision, and silently dropping it would apply a
 * screen they did not ask for.
 */

import type { FilterOp, FilterValue } from "./types";
import type { FilterRegistry, RegistryEntry } from "./registry";

export interface DraftFilter {
  key: string;
  op: FilterOp;
  value: unknown;
}

export type ValidationResult =
  | { ok: true; key: string; op: FilterOp; value: FilterValue }
  | { ok: false; error: string };

function quoted(value: unknown): string {
  if (typeof value === "string") return `“${value}”`;
  return `“${String(value)}”`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A finite number from a number or a non-blank numeric string. */
export function toFiniteNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "boolean" || value === null || value === undefined) return null;
  if (typeof value === "string") {
    const text = value.trim();
    if (text.length === 0) return null;
    const number = Number(text);
    return Number.isFinite(number) ? number : null;
  }
  return null;
}

function listOf(value: unknown): unknown[] | null {
  if (Array.isArray(value)) return value;
  if (typeof value === "string") {
    if (value.trim().length === 0) return [];
    return value.split(",").map((part) => part.trim());
  }
  return null;
}

function boundsOf(value: unknown): [unknown, unknown] | null {
  if (Array.isArray(value) && value.length === 2) return [value[0], value[1]];
  if (isRecord(value)) return [value["min"], value["max"]];
  return null;
}

/** The label a message uses: the human label when the key is offered. */
function fieldName(registry: FilterRegistry, key: string): string {
  return registry.byKey.get(key)?.label ?? key;
}

function numberValue(entry: RegistryEntry, op: FilterOp, raw: unknown): ValidationResult {
  if (op === "between") {
    const bounds = boundsOf(raw);
    if (!bounds) {
      return { ok: false, error: `${entry.label} needs two numbers (a minimum and a maximum).` };
    }
    const low = toFiniteNumber(bounds[0]);
    const high = toFiniteNumber(bounds[1]);
    if (low === null || high === null) {
      const bad = low === null ? bounds[0] : bounds[1];
      return {
        ok: false,
        error: `${entry.label} needs numbers for its range, not ${quoted(bad ?? "")}.`,
      };
    }
    if (low > high) {
      return {
        ok: false,
        error: `${entry.label} has its range inverted: the minimum ${low} is greater than the maximum ${high}.`,
      };
    }
    return { ok: true, key: entry.key, op, value: [low, high] };
  }

  if (op === "in") {
    const items = listOf(raw);
    if (!items || items.length === 0) {
      return { ok: false, error: `${entry.label} needs at least one value.` };
    }
    const numbers: number[] = [];
    for (const item of items) {
      const number = toFiniteNumber(item);
      if (number === null) {
        return { ok: false, error: `${entry.label} needs numbers, not ${quoted(item)}.` };
      }
      numbers.push(number);
    }
    return { ok: true, key: entry.key, op, value: numbers };
  }

  if (raw === "" || raw === null || raw === undefined) {
    return { ok: false, error: `${entry.label} needs a value.` };
  }
  const number = toFiniteNumber(raw);
  if (number === null) {
    return { ok: false, error: `${entry.label} needs a number, not ${quoted(raw)}.` };
  }
  return { ok: true, key: entry.key, op, value: number };
}

function textValue(entry: RegistryEntry, op: FilterOp, raw: unknown): ValidationResult {
  if (op === "in") {
    const items = listOf(raw);
    if (!items || items.length === 0) {
      return { ok: false, error: `${entry.label} needs at least one value.` };
    }
    const strings: string[] = [];
    for (const item of items) {
      if (typeof item !== "string" || item.trim().length === 0) {
        return { ok: false, error: `${entry.label} needs text values, not ${quoted(item)}.` };
      }
      strings.push(item.trim());
    }
    return { ok: true, key: entry.key, op, value: strings };
  }

  if (typeof raw !== "string" || raw.trim().length === 0) {
    return { ok: false, error: `${entry.label} needs some text.` };
  }
  return { ok: true, key: entry.key, op, value: raw.trim() };
}

function booleanValue(entry: RegistryEntry, op: FilterOp, raw: unknown): ValidationResult {
  if (op === "in") {
    const items = listOf(raw);
    if (!items || items.length === 0) {
      return { ok: false, error: `${entry.label} needs at least one value.` };
    }
    return {
      ok: true,
      key: entry.key,
      op,
      value: items.map((item) => item === true || item === "true"),
    };
  }
  if (raw === true || raw === "true") return { ok: true, key: entry.key, op, value: true };
  if (raw === false || raw === "false") return { ok: true, key: entry.key, op, value: false };
  return { ok: false, error: `${entry.label} is either true or false, not ${quoted(raw)}.` };
}

function choiceValue(entry: RegistryEntry, op: FilterOp, raw: unknown): ValidationResult {
  if (op !== "eq") {
    return {
      ok: false,
      error: `${entry.label} takes the API's option tokens, not “${op}”.`,
    };
  }
  const token = typeof raw === "string" ? raw.trim() : "";
  if (token.length === 0) return { ok: false, error: `${entry.label} needs an option.` };
  if (!entry.options.some((option) => option.token === token)) {
    return { ok: false, error: `${entry.label} has no option ${quoted(token)}.` };
  }
  return { ok: true, key: entry.key, op, value: token };
}

/**
 * Validate one draft and return the value the API should receive.
 *
 * Key resolution happens first: a refused key names the schema's reason, and an
 * unknown key names itself. Both are errors — neither reaches `fetch`.
 */
export function validateDraft(registry: FilterRegistry, draft: DraftFilter): ValidationResult {
  const entry = registry.byKey.get(draft.key);
  if (!entry) {
    const reason = registry.refused[draft.key];
    return {
      ok: false,
      error: reason
        ? `${draft.key} cannot be filtered: ${reason}`
        : `Unknown filter ${quoted(draft.key)}.`,
    };
  }

  if (!entry.ops.includes(draft.op)) {
    return {
      ok: false,
      error: `${entry.label} cannot use ${quoted(draft.op)}; it accepts ${entry.ops.join(", ")}.`,
    };
  }

  switch (entry.kind) {
    case "number":
      return numberValue(entry, draft.op, draft.value);
    case "text":
      return textValue(entry, draft.op, draft.value);
    case "bool":
      return booleanValue(entry, draft.op, draft.value);
    case "choice":
      return choiceValue(entry, draft.op, draft.value);
  }
}

/** The field label a compile/validation error should name. */
export function fieldLabel(registry: FilterRegistry, key: string): string {
  return fieldName(registry, key);
}

/** Validate a whole value list, preserving order; the first error wins. */
export function validateFilters(
  registry: FilterRegistry,
  drafts: readonly DraftFilter[],
): { ok: true; filters: Array<{ key: string; op: FilterOp; value: FilterValue }> } | { ok: false; error: string } {
  const filters: Array<{ key: string; op: FilterOp; value: FilterValue }> = [];
  for (const draft of drafts) {
    const result = validateDraft(registry, draft);
    if (!result.ok) return result;
    filters.push({ key: result.key, op: result.op, value: result.value });
  }
  return { ok: true, filters };
}
