/**
 * The advanced builder's compiler: one level of AND/OR becomes `criteria.filters`.
 *
 * The API has one combinator — it ANDs the filter list (`app/screen/filters.py`,
 * "apply_filters then ANDs the predicates and keeps input order"). There is no
 * OR operator and no nested boolean node, so an OR group is not free-form: it
 * is expressible exactly when its alternatives are values of *one* field, which
 * is what the API's `in` operator means. `Sector is Technology OR Healthcare`
 * compiles to `{key:"sec", op:"in", value:["Technology","Healthcare"]}`.
 *
 * An OR across two fields cannot be one request at all. Rather than drop half
 * the group or invent a request shape the server would 422, the compiler
 * refuses it and names both fields. That is the honest ceiling of the API, and
 * it is why the editor offers "all of these" as the default and an "any of
 * these" group that picks its field first.
 *
 * Nesting is capped at the root group plus one level: the API cannot express
 * `(A AND B) OR (C AND D)` in one body, so a deeper tree would be a UI that
 * compiles only sometimes.
 */

import type { FilterOp, FilterValue, WireFilter } from "./types";
import type { FilterRegistry } from "./registry";
import type { DraftFilter } from "./validation";
import { validateDraft } from "./validation";

export interface BuilderCondition {
  kind: "condition";
  id?: string;
  key: string;
  op: FilterOp;
  value: unknown;
}

export interface BuilderGroup {
  kind: "group";
  id?: string;
  match: "and" | "or";
  children: BuilderNode[];
}

export type BuilderNode = BuilderCondition | BuilderGroup;

export type CompileCode =
  | "invalid_value"
  | "or_across_fields"
  | "or_operator"
  | "empty_group"
  | "too_deep"
  | "missing_key";

export class CompileError extends Error {
  readonly code: CompileCode;
  /** The field the message names, when the failure belongs to one. */
  readonly field: string | null;

  constructor(code: CompileCode, message: string, field: string | null = null) {
    super(message);
    this.name = "CompileError";
    this.code = code;
    this.field = field;
  }
}

function labelOf(registry: FilterRegistry, key: string): string {
  return registry.byKey.get(key)?.label ?? key;
}

function compileCondition(registry: FilterRegistry, condition: BuilderCondition): WireFilter {
  if (!condition.key) {
    throw new CompileError("missing_key", "A condition needs a filter key.", null);
  }
  const draft: DraftFilter = {
    key: condition.key,
    op: condition.op,
    value: condition.value,
  };
  const result = validateDraft(registry, draft);
  if (!result.ok) {
    throw new CompileError("invalid_value", result.error, labelOf(registry, condition.key));
  }
  return { key: result.key, op: result.op, value: result.value };
}

/**
 * An OR group's alternatives, merged into one `in` (or `eq`) on one key.
 *
 * `applyBucket`-style numeric unions are deliberately not attempted: two
 * disjoint ranges cannot be one predicate, and a between-envelope would match
 * the gap between them.
 */
function compileOr(registry: FilterRegistry, children: BuilderNode[]): WireFilter {
  if (children.length === 0) {
    throw new CompileError("empty_group", "An “any of” group needs at least one condition.", null);
  }

  const conditions: BuilderCondition[] = [];
  for (const child of children) {
    if (child.kind !== "condition") {
      throw new CompileError(
        "too_deep",
        "An “any of” group can only hold conditions; nesting another group is beyond what one request can express.",
        null,
      );
    }
    conditions.push(child);
  }

  const keys = [...new Set(conditions.map((condition) => condition.key))];
  if (keys.length > 1) {
    const names = keys.map((key) => labelOf(registry, key)).join(" and ");
    throw new CompileError(
      "or_across_fields",
      `Can’t OR ${names}: the API ANDs filters, so an “any of” group must offer alternatives for one field. Split it, or make it “all of”.`,
      names,
    );
  }

  const key = keys[0] ?? "";
  const values: FilterValue[] = [];
  for (const condition of conditions) {
    if (condition.op !== "eq" && condition.op !== "in") {
      throw new CompileError(
        "or_operator",
        `${labelOf(registry, key)} alternatives must be “is” or “any of” values; the API cannot OR “${condition.op}”.`,
        labelOf(registry, key),
      );
    }
    const compiled = compileCondition(registry, condition);
    if (Array.isArray(compiled.value)) {
      for (const item of compiled.value) if (!values.includes(item)) values.push(item);
    } else if (!values.includes(compiled.value)) {
      values.push(compiled.value);
    }
  }

  if (values.length === 0) {
    throw new CompileError(
      "empty_group",
      `The “any of” group for ${labelOf(registry, key)} has no values.`,
      labelOf(registry, key),
    );
  }
  if (values.length === 1) {
    return { key, op: "eq", value: values[0] as FilterValue };
  }
  return { key, op: "in", value: values as string[] | number[] };
}

function compileNode(registry: FilterRegistry, node: BuilderNode, depth: number): WireFilter[] {
  if (node.kind === "condition") return [compileCondition(registry, node)];
  if (node.match === "or") return [compileOr(registry, node.children)];
  if (depth > 1) {
    throw new CompileError(
      "too_deep",
      "The builder is one level deep: a nested group inside a nested group cannot be sent as one request.",
      null,
    );
  }

  const filters: WireFilter[] = [];
  for (const child of node.children) {
    if (child.kind === "group" && child.match === "or") {
      filters.push(compileOr(registry, child.children));
    } else {
      filters.push(...compileNode(registry, child, depth + 1));
    }
  }
  return filters;
}

/**
 * Compile a builder tree to the exact `criteria.filters` array.
 *
 * A bare condition is accepted as a one-element AND. Throws `CompileError`
 * before any request is made; nothing here returns a partial list.
 */
export function compileGroup(node: BuilderNode, registry: FilterRegistry): WireFilter[] {
  if (node.kind === "group") {
    if (node.match === "and") return compileNode(registry, node, 0);
    return [compileOr(registry, node.children)];
  }
  return [compileCondition(registry, node)];
}

/** Rebuild an editable tree from a flat filter list (for a round-trip test). */
export function toBuilder(filters: readonly WireFilter[]): BuilderGroup {
  return {
    kind: "group",
    match: "and",
    children: filters.map((filter) => ({
      kind: "condition",
      key: filter.key,
      op: filter.op,
      value: filter.value,
    })),
  };
}

/**
 * Rows → tree for the row-based editor: consecutive OR rows form one "any of"
 * group, every other row is a top-level AND condition.
 *
 * A row whose connector is "or" but whose key differs from the run's first key
 * still compiles into that run; the compiler refuses it with both field names,
 * which is the message the builder shows. Grouping it silently under AND would
 * be the worse answer.
 */
export interface BuilderRow {
  id: string;
  connector: "and" | "or";
  key: string;
  op: FilterOp;
  value: unknown;
}

export function rowsToGroup(rows: readonly BuilderRow[]): BuilderGroup {
  const children: BuilderNode[] = [];
  let current: BuilderCondition[] = [];
  let currentOr = false;

  const flush = () => {
    if (current.length === 0) return;
    if (current.length === 1 && !currentOr) {
      children.push(current[0] as BuilderCondition);
    } else {
      children.push({
        kind: "group",
        match: currentOr ? "or" : "and",
        children: [...current],
      });
    }
    current = [];
    currentOr = false;
  };

  rows.forEach((row, index) => {
    const condition: BuilderCondition = {
      kind: "condition",
      id: row.id,
      key: row.key,
      op: row.op,
      value: row.value,
    };
    if (index === 0) {
      current = [condition];
      currentOr = false;
      return;
    }

    if (row.connector === "or") {
      // OR binds tighter than AND: "any" makes this row an alternative to the
      // row above. If that row is the last member of a longer all-group, it
      // moves into the new any-group so `A AND B OR C` reads as
      // `A AND (B OR C)` — the only reading one request can express.
      if (!currentOr && current.length > 1) {
        const last = current.pop() as BuilderCondition;
        children.push({ kind: "group", match: "and", children: [...current] });
        current = [last];
      }
      current.push(condition);
      currentOr = true;
      return;
    }

    flush();
    current = [condition];
    currentOr = false;
  });
  flush();

  return { kind: "group", match: "and", children };
}
