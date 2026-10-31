/**
 * The advanced builder: rows with an AND/OR connector, compiled before they
 * reach the state.
 *
 * A row is one condition; the connector on a row says how it joins the one
 * above. A run of "any" rows becomes one `in` on their shared field (the only
 * OR the API can express); everything else is ANDed. The compiler, not this
 * component, decides whether a run is expressible, and its refusal names the
 * fields — the builder shows that sentence instead of inventing a request.
 *
 * Validation is per row and immediate: a blank, non-numeric, or inverted range
 * is a sentence under the row and the Add button stays disabled. Nothing here
 * can call `onAdd` with an invalid predicate.
 */

import * as React from "react";

import type { FilterOp, WireFilter } from "./types";
import type { FilterRegistry, RegistryEntry } from "./registry";
import { OP_LABELS } from "./registry";
import { CompileError, compileGroup, rowsToGroup, type BuilderRow } from "./compiler";
import { validateDraft } from "./validation";

export interface ConditionBuilderProps {
  registry: FilterRegistry;
  onAdd: (filters: WireFilter[]) => void;
  /** A key chosen in ⌘K; the row is created and its value focused. */
  focusKey?: string | null;
}

interface RowState {
  id: string;
  connector: "and" | "or";
  key: string;
  op: FilterOp;
  value: string;
  second: string;
  touched: boolean;
}

let rowSequence = 0;

function rowId(): string {
  rowSequence += 1;
  return `row-${rowSequence}`;
}

function defaultOp(entry: RegistryEntry): FilterOp {
  if (entry.kind === "choice") return "eq";
  if (entry.kind === "number") return "gte";
  return "eq";
}

function newRow(entry: RegistryEntry, connector: "and" | "or" = "and"): RowState {
  return {
    id: rowId(),
    connector,
    key: entry.key,
    op: defaultOp(entry),
    value: "",
    second: "",
    touched: false,
  };
}

function rowDraft(
  row: RowState,
  entry?: RegistryEntry,
): { key: string; op: FilterOp; value: unknown } {
  if (row.op === "between") return { key: row.key, op: row.op, value: [row.value, row.second] };
  if (row.op === "in") {
    return {
      key: row.key,
      op: row.op,
      value: row.value
        .split(",")
        .map((part) => part.trim())
        .filter((part) => part.length > 0),
    };
  }
  if (entry?.kind === "bool") {
    return { key: row.key, op: row.op, value: row.value === "true" };
  }
  return { key: row.key, op: row.op, value: row.value };
}

export function ConditionBuilder({ registry, onAdd, focusKey }: ConditionBuilderProps) {
  const [rows, setRows] = React.useState<RowState[]>([]);
  const [error, setError] = React.useState<string | null>(null);
  const valueRefs = React.useRef(new Map<string, HTMLElement | null>());
  const pendingFocus = React.useRef<string | null>(null);
  const rowsRef = React.useRef(rows);
  rowsRef.current = rows;

  const update = (id: string, patch: Partial<RowState>) => {
    setRows((previous) =>
      previous.map((row) => (row.id === id ? { ...row, ...patch, touched: true } : row)),
    );
    setError(null);
  };

  const addRow = () => {
    const first = registry.entries[0];
    if (!first) return;
    setRows((previous) => [
      ...previous,
      newRow(first, previous.length === 0 ? "and" : "and"),
    ]);
    setError(null);
  };

  const removeRow = (id: string) => {
    setRows((previous) => previous.filter((row) => row.id !== id));
    setError(null);
  };

  // A key chosen in the palette: create (or find) its row and focus its value.
  React.useEffect(() => {
    if (!focusKey) return;
    const entry = registry.byKey.get(focusKey);
    if (!entry) return;
    const existing = rowsRef.current.find((row) => row.key === focusKey);
    if (existing) {
      valueRefs.current.get(existing.id)?.focus();
      return;
    }
    pendingFocus.current = focusKey;
    setRows((previous) => [...previous, newRow(entry)]);
  }, [focusKey, registry]);

  React.useEffect(() => {
    const key = pendingFocus.current;
    if (!key) return;
    const row = rows.find((candidate) => candidate.key === key);
    if (!row) return;
    pendingFocus.current = null;
    const element = valueRefs.current.get(row.id);
    if (element) {
      element.scrollIntoView?.({ block: "center" });
      element.focus();
    }
  }, [rows]);

  const results = rows.map((row) => {
    const entry = registry.byKey.get(row.key);
    const result = entry ? validateDraft(registry, rowDraft(row, entry)) : { ok: false as const, error: `Unknown filter “${row.key}”.` };
    return { row, entry, result };
  });

  const canAdd = results.length > 0 && results.every(({ result }) => result.ok);

  const commit = () => {
    const builderRows: BuilderRow[] = rows.map((row) => ({
      id: row.id,
      connector: row.connector,
      ...rowDraft(row, registry.byKey.get(row.key)),
    }));
    try {
      const compiled = compileGroup(rowsToGroup(builderRows), registry);
      onAdd(compiled);
      setRows([]);
      setError(null);
    } catch (failure) {
      if (failure instanceof CompileError) {
        setError(failure.message);
        return;
      }
      throw failure;
    }
  };

  return (
    <section aria-label="Advanced filter builder" className="border-t border-line px-3 py-2">
      <div className="mb-1 flex items-baseline justify-between">
        <h3 className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
          Build a rule
        </h3>
        <span className="text-data text-fg-subtle">AND / OR · one level</span>
      </div>

      {/* Every usable key is reachable here, preset or not: this is the
          drawer's complete vocabulary, not a second curated list. */}
      <select
        aria-label="Add a filter"
        value=""
        onChange={(event) => {
          const entry = registry.byKey.get(event.target.value);
          if (!entry) return;
          setRows((previous) => [...previous, newRow(entry)]);
          setError(null);
        }}
        className="mb-2 w-full rounded-sm border border-line-control bg-surface-sunken px-1 py-1 text-data text-fg"
      >
        <option value="">Add a filter…</option>
        {registry.tabs.map((tab) => (
          <optgroup key={tab} label={tab}>
            {registry.entries
              .filter((candidate) => candidate.tab === tab)
              .map((candidate) => (
                <option key={candidate.key} value={candidate.key}>
                  {candidate.label}
                </option>
              ))}
          </optgroup>
        ))}
      </select>

      {rows.length === 0 ? (
        <p className="py-1 text-data text-fg-subtle">
          Combine filters the presets do not cover. A run of “any” rows becomes one
          “any of” on that field.
        </p>
      ) : null}

      <ol className="flex flex-col gap-2">
        {results.map(({ row, entry, result }, index) => {
          const label = entry?.label ?? row.key;
          return (
            <li key={row.id} className="rounded-sm border border-line bg-surface-raised p-2">
              <div className="flex items-center gap-1">
                {index > 0 ? (
                  <select
                    aria-label={`Match condition ${index + 1}`}
                    value={row.connector}
                    onChange={(event) =>
                      update(row.id, { connector: event.target.value as "and" | "or" })
                    }
                    className="rounded-sm border border-line-control bg-surface-sunken px-1 py-0.5 font-mono text-label uppercase text-fg-muted"
                  >
                    <option value="and">all</option>
                    <option value="or">any</option>
                  </select>
                ) : (
                  <span className="font-mono text-label uppercase text-fg-subtle">where</span>
                )}

                <select
                  aria-label={`Filter ${index + 1} field`}
                  value={row.key}
                  onChange={(event) => {
                    const next = registry.byKey.get(event.target.value);
                    if (!next) return;
                    update(row.id, { key: next.key, op: defaultOp(next), value: "", second: "" });
                  }}
                  className="min-w-0 flex-1 rounded-sm border border-line-control bg-surface-sunken px-1 py-0.5 text-data text-fg"
                >
                  {registry.tabs.map((tab) => (
                    <optgroup key={tab} label={tab}>
                      {registry.entries
                        .filter((candidate) => candidate.tab === tab)
                        .map((candidate) => (
                          <option key={candidate.key} value={candidate.key}>
                            {candidate.label}
                          </option>
                        ))}
                    </optgroup>
                  ))}
                </select>

                <button
                  type="button"
                  aria-label={`Remove condition ${index + 1}`}
                  onClick={() => removeRow(row.id)}
                  className="h-5 w-5 rounded-sm text-fg-muted hover:bg-row-hover hover:text-fg"
                >
                  ×
                </button>
              </div>

              {entry && entry.kind !== "choice" ? (
                <div className="mt-1 flex items-center gap-1">
                  <select
                    aria-label={`Filter ${index + 1} operator`}
                    value={row.op}
                    onChange={(event) => update(row.id, { op: event.target.value as FilterOp })}
                    className="rounded-sm border border-line-control bg-surface-sunken px-1 py-0.5 text-data text-fg-muted"
                  >
                    {entry.ops.map((op) => (
                      <option key={op} value={op}>
                        {OP_LABELS[op]}
                      </option>
                    ))}
                  </select>

                  {entry.kind === "bool" ? (
                    <select
                      aria-label={`${label} value (${index + 1})`}
                      value={row.value}
                      onChange={(event) => update(row.id, { value: event.target.value })}
                      className="rounded-sm border border-line-control bg-surface-sunken px-1 py-0.5 text-data text-fg"
                    >
                      <option value="">—</option>
                      <option value="true">true</option>
                      <option value="false">false</option>
                    </select>
                  ) : row.op === "between" ? (
                    <span className="flex min-w-0 flex-1 items-center gap-1">
                      <input
                        ref={(element) => {
                          valueRefs.current.set(row.id, element);
                        }}
                        aria-label={`${label} minimum (${index + 1})`}
                        inputMode="decimal"
                        value={row.value}
                        onChange={(event) => update(row.id, { value: event.target.value })}
                        className="min-w-0 flex-1 rounded-sm border border-line-control bg-surface-sunken px-1 py-0.5 text-data text-fg"
                      />
                      <span className="text-fg-subtle">to</span>
                      <input
                        aria-label={`${label} maximum (${index + 1})`}
                        inputMode="decimal"
                        value={row.second}
                        onChange={(event) => update(row.id, { second: event.target.value })}
                        className="min-w-0 flex-1 rounded-sm border border-line-control bg-surface-sunken px-1 py-0.5 text-data text-fg"
                      />
                    </span>
                  ) : (
                    <input
                      ref={(element) => {
                        valueRefs.current.set(row.id, element);
                      }}
                      aria-label={`${label} value (${index + 1})`}
                      inputMode={entry.kind === "number" ? "decimal" : "text"}
                      placeholder={row.op === "in" ? "a, b, c" : entry.kind === "number" ? "number" : "text"}
                      value={row.value}
                      onChange={(event) => update(row.id, { value: event.target.value })}
                      className="min-w-0 flex-1 rounded-sm border border-line-control bg-surface-sunken px-1 py-0.5 text-data text-fg"
                    />
                  )}
                </div>
              ) : null}

              {entry?.kind === "choice" ? (
                <select
                  ref={(element) => {
                    valueRefs.current.set(row.id, element);
                  }}
                  aria-label={`${label} option (${index + 1})`}
                  value={row.value}
                  onChange={(event) => update(row.id, { value: event.target.value })}
                  className="mt-1 w-full rounded-sm border border-line-control bg-surface-sunken px-1 py-0.5 text-data text-fg"
                >
                  <option value="">Choose an option…</option>
                  {entry.options.map((option) => (
                    <option key={option.token} value={option.token}>
                      {option.label}
                    </option>
                  ))}
                </select>
              ) : null}

              {row.touched && !result.ok ? (
                <p role="alert" className="mt-1 text-data text-alert">
                  {result.error}
                </p>
              ) : null}
            </li>
          );
        })}
      </ol>

      {error ? (
        <p role="alert" className="mt-2 text-data text-alert">
          {error}
        </p>
      ) : null}

      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={addRow}
          className="rounded-sm border border-line px-2 py-0.5 text-data text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
        >
          Add condition
        </button>
        <button
          type="button"
          onClick={commit}
          disabled={!canAdd}
          className="rounded-sm border border-line-control bg-surface-sunken px-2 py-0.5 text-data text-fg transition-colors hover:border-line-strong disabled:opacity-40"
        >
          Add to screen
        </button>
      </div>
    </section>
  );
}
