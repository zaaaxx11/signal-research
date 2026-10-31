/**
 * The column picker: every key the schema serves, one panel's worth of choice.
 *
 * A panel holds 40 columns and the catalogue holds 116, so this is a picker
 * with a hard edge, not a checklist. Two things it must never do:
 *
 * * offer a key the request cannot carry — the catalogue it renders is the one
 *   the schema served, and the cap it prints is the same constant the request
 *   builder enforces;
 * * swallow a refusal. The sentence naming the cap is rendered next to the
 *   list (a live region), because a checkbox that quietly fails to tick is the
 *   worst possible answer.
 *
 * It is a dialog with a real Escape path and its own search box: 116 rows is
 * more than a glance, so typing a label is the way through. Labels come from
 * `columnSpec`, never from the key spelling, but the key stays visible in mono
 * — an operator matching a request body by eye needs the exact token.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

import { columnSpec } from "./columnMeta";
import { COLUMN_CAP } from "./columnSelection";

export interface ColumnPickerProps {
  open: boolean;
  onClose: () => void;
  /** Every key the served catalogue offers. */
  keys: readonly string[];
  /** The selection currently on screen. */
  selected: readonly string[];
  onToggle: (key: string) => void;
  onReset: () => void;
  /** True when `selected` is not the view's own column list. */
  custom: boolean;
  /** The cap sentence, when the last add was refused. */
  notice: string | null;
}

export function ColumnPicker({
  open,
  onClose,
  keys,
  selected,
  onToggle,
  onReset,
  custom,
  notice,
}: ColumnPickerProps) {
  const [query, setQuery] = React.useState("");
  const searchRef = React.useRef<HTMLInputElement>(null);
  const chosen = React.useMemo(() => new Set(selected), [selected]);

  React.useEffect(() => {
    if (!open) return;
    setQuery("");
    // Focus lands in the search box: the list is 116 rows and typing is the
    // fastest way through it. `preventScroll` keeps the panel behind still.
    const frame = requestAnimationFrame(() => searchRef.current?.focus({ preventScroll: true }));
    return () => cancelAnimationFrame(frame);
  }, [open]);

  React.useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  const matches = React.useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (needle.length === 0) return keys;
    return keys.filter((key) => {
      const spec = columnSpec(key);
      return key.toLowerCase().includes(needle) || spec.label.toLowerCase().includes(needle);
    });
  }, [keys, query]);

  if (!open) return null;

  const full = selected.length >= COLUMN_CAP;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-6 pt-[8vh]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="column-picker-title"
        data-testid="column-picker"
        className="flex max-h-[78vh] w-full max-w-[440px] flex-col overflow-hidden rounded-md border border-line-strong bg-surface shadow-xl"
      >
        <div className="flex shrink-0 items-baseline justify-between gap-3 border-b border-line px-3 py-2">
          <h2 id="column-picker-title" className="text-ui font-medium text-fg">
            Columns
          </h2>
          <span
            data-testid="column-picker-count"
            className={cn(
              "font-mono text-label uppercase tracking-[0.08em]",
              full ? "text-alert" : "text-fg-subtle",
            )}
          >
            {selected.length} / {COLUMN_CAP}
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close columns"
            className="rounded-sm border border-line px-1.5 py-0.5 text-data text-fg-muted hover:border-line-strong hover:text-fg"
          >
            ✕
          </button>
        </div>

        <div className="shrink-0 border-b border-line px-3 py-2">
          <input
            ref={searchRef}
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            aria-label="Find a column"
            placeholder="Find a column — 116 keys, 40 per panel"
            className="w-full rounded-sm border border-line bg-surface-sunken px-2 py-1 text-data text-fg placeholder:text-fg-subtle focus-visible:outline-2 focus-visible:outline-focus"
          />
        </div>

        {notice !== null ? (
          <p
            role="status"
            data-testid="column-picker-notice"
            className="shrink-0 border-b border-line bg-surface-raised px-3 py-1.5 text-data text-alert"
          >
            {notice}
          </p>
        ) : null}

        <ul className="min-h-0 flex-1 overflow-y-auto px-1 py-1" aria-label="Catalogue columns">
          {matches.map((key) => {
            const spec = columnSpec(key);
            const checked = chosen.has(key);
            return (
              <li key={key}>
                <label
                  className={cn(
                    "flex cursor-default items-center gap-2 rounded-[1px] px-2 py-1 text-data hover:bg-row-hover",
                    checked ? "text-fg" : "text-fg-muted",
                  )}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => onToggle(key)}
                    aria-label={`${spec.label} (${key})`}
                    className="size-3 accent-[var(--accent)]"
                  />
                  <span className="min-w-0 flex-1 truncate">{spec.label}</span>
                  <span className="shrink-0 font-mono text-label text-fg-subtle">{key}</span>
                </label>
              </li>
            );
          })}
          {matches.length === 0 ? (
            <li className="px-2 py-3 text-data text-fg-subtle">No catalogue key matches that.</li>
          ) : null}
        </ul>

        <div className="flex shrink-0 items-center justify-between gap-2 border-t border-line bg-surface-sunken px-3 py-2">
          <button
            type="button"
            onClick={onReset}
            disabled={!custom}
            className="rounded-sm border border-line px-2 py-0.5 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:border-line-strong hover:text-fg disabled:opacity-40"
          >
            Reset to view
          </button>
          <span className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
            {matches.length} shown
          </span>
          <button
            type="button"
            onClick={onClose}
            className="rounded-sm border border-line-strong px-2 py-0.5 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:text-fg"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
