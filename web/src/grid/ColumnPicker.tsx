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
 *
 * The header's select-all box is the same edge in one gesture: it ticks what
 * fits and says so ("Showing the first 40 of 116 columns") instead of pretending
 * the panel got wider, and unticking it returns to the identity floor rather
 * than to nothing — the ticker lane, the row number and the company name are
 * what make a row a row.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

import { columnSpec } from "./columnMeta";
import { COLUMN_CAP, sameColumns, selectAllColumns } from "./columnSelection";

export interface ColumnPickerProps {
  open: boolean;
  onClose: () => void;
  /** Every key the served catalogue offers. */
  keys: readonly string[];
  /**
   * The extra **Computed** keys — native scan metrics the 116-key catalogue
   * does not name (`computedColumns.ts`). They are offered in their own group
   * and never duplicated into the catalogue list.
   */
  computedKeys?: readonly string[];
  /** The selection currently on screen. */
  selected: readonly string[];
  onToggle: (key: string) => void;
  /** Tick every offered key, cut to the panel cap. */
  onSelectAll: () => void;
  /** Return to the identity floor: row number, ticker and company. */
  onClearAll: () => void;
  onReset: () => void;
  /** True when `selected` is not the view's own column list. */
  custom: boolean;
  /** The cap sentence, when the last add was refused. */
  notice: string | null;
}

/** A stable empty list, so the default prop does not churn memo deps. */
const NO_COMPUTED: readonly string[] = [];

/**
 * The keys a search needle keeps: the exact spelling or the human label.
 *
 * Labels come from `columnSpec`, never from the key, so "momentum" and
 * "Momentum (price change, 10d)" both find the computed momentum column.
 */
function matchingColumns(
  source: readonly string[],
  query: string,
): readonly string[] {
  const needle = query.trim().toLowerCase();
  if (needle.length === 0) return source;
  return source.filter((key) => {
    const spec = columnSpec(key);
    return (
      key.toLowerCase().includes(needle) ||
      spec.label.toLowerCase().includes(needle)
    );
  });
}

export function ColumnPicker({
  open,
  onClose,
  keys,
  computedKeys = NO_COMPUTED,
  selected,
  onToggle,
  onSelectAll,
  onClearAll,
  onReset,
  custom,
  notice,
}: ColumnPickerProps) {
  const [query, setQuery] = React.useState("");
  const searchRef = React.useRef<HTMLInputElement>(null);
  const selectAllRef = React.useRef<HTMLInputElement>(null);
  const chosen = React.useMemo(() => new Set(selected), [selected]);

  /** Everything the picker offers: the catalogue and the Computed group. */
  const offered = React.useMemo(
    () => [...keys, ...computedKeys],
    [keys, computedKeys],
  );

  /**
   * The selection as this picker can count it: keys outside the offered
   * vocabulary (a link may name one) are not rows of this list, so they cannot
   * make the box read "all".
   */
  const chosenOffered = React.useMemo(
    () => selected.filter((key) => offered.includes(key)),
    [selected, offered],
  );
  /** As many as one panel can hold, given what is offered. */
  const fullest = Math.min(offered.length, COLUMN_CAP);
  const allChecked = fullest > 0 && chosenOffered.length >= fullest;
  /** Mixed: something is ticked, but the panel is not as full as it can be. */
  const mixed = chosenOffered.length > 0 && !allChecked;
  /** The list a select-all produces, so the cut sentence is never a guess. */
  const cut = React.useMemo(
    () => (offered.length > COLUMN_CAP ? selectAllColumns(offered) : []),
    [offered],
  );
  const truncated = offered.length > COLUMN_CAP && sameColumns(chosenOffered, cut);

  React.useEffect(() => {
    // `indeterminate` is a DOM property, not an attribute React can render:
    // without this line the box shows "off" for a partial panel.
    if (selectAllRef.current) selectAllRef.current.indeterminate = mixed;
  }, [mixed, open]);

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

  const matches = React.useMemo(
    () => matchingColumns(keys, query),
    [keys, query],
  );
  const computedMatches = React.useMemo(
    () => matchingColumns(computedKeys, query),
    [computedKeys, query],
  );
  const nothing = matches.length === 0 && computedMatches.length === 0;

  /** One checkbox row; the catalogue and the Computed group share it. */
  const renderRow = (key: string) => {
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
  };

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
        <div className="flex shrink-0 items-center gap-3 border-b border-line px-3 py-2">
          <h2 id="column-picker-title" className="text-ui font-medium text-fg">
            Columns
          </h2>
          <label className="-ml-1 flex cursor-default items-center gap-1.5">
            <input
              ref={selectAllRef}
              type="checkbox"
              checked={allChecked}
              onChange={(event) => (event.target.checked ? onSelectAll() : onClearAll())}
              disabled={keys.length === 0}
              aria-label="Select all columns"
              data-testid="column-picker-select-all"
              className="size-3 accent-[var(--accent)]"
            />
            <span className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
              All
            </span>
          </label>
          <span
            data-testid="column-picker-count"
            className={cn(
              "ml-auto font-mono text-label uppercase tracking-[0.08em]",
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

        {truncated ? (
          // The other half of the cap: the reader asked for everything and got
          // a panel. Saying so is the difference between a cut and a lie.
          <p
            data-testid="column-picker-cap-note"
            className="shrink-0 border-b border-line bg-surface-raised px-3 py-1.5 text-data text-fg-subtle"
          >
            Showing the first {COLUMN_CAP} of {keys.length} columns
          </p>
        ) : null}

        <div className="shrink-0 border-b border-line px-3 py-2">
          <input
            ref={searchRef}
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            aria-label="Find a column"
            placeholder={`Find a column — ${offered.length} keys, ${COLUMN_CAP} per panel`}
            className="w-full rounded-sm border border-line bg-surface-sunken px-2 py-1 text-data text-fg placeholder:text-fg-subtle focus-visible:outline-2 focus-visible:outline-focus"
          />
        </div>

        {notice !== null ? (
          // The cap refusal: an alert, because the checkbox the reader just
          // ticked did not take. The one `role="status"` in the app is the
          // row-range region in `ScreenPanel`.
          <p
            role="alert"
            data-testid="column-picker-notice"
            className="shrink-0 border-b border-line bg-surface-raised px-3 py-1.5 text-data text-alert"
          >
            {notice}
          </p>
        ) : null}

        <div className="min-h-0 flex-1 overflow-y-auto px-1 py-1">
          <ul aria-label="Catalogue columns">
            {matches.map(renderRow)}
          </ul>
          {computedKeys.length > 0 ? (
            <section
              data-testid="column-picker-computed-group"
              aria-labelledby="column-picker-computed-title"
              className="mt-1 border-t border-line pt-1"
            >
              <p
                id="column-picker-computed-title"
                className="px-2 py-1 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle"
              >
                Computed
              </p>
              <ul aria-label="Computed columns">
                {computedMatches.map(renderRow)}
              </ul>
            </section>
          ) : null}
          {nothing ? (
            <p className="px-2 py-3 text-data text-fg-subtle">
              No column matches that.
            </p>
          ) : null}
        </div>

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
            {matches.length + computedMatches.length} shown
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
