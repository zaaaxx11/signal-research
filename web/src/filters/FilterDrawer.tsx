/**
 * The filter drawer — a left overlay, not a wall above the table.
 *
 * The layout decision comes from the measurement in research/finviz-ui.md §6
 * and R5: Finviz puts 83 controls over 510 px above a 424 px table. Here the
 * table keeps its width; eight preset rows are visible, the remaining numeric
 * presets sit behind one disclosure, and everything else is the row builder.
 *
 * There is no Apply. Every bucket press, every added row and every "Any" is the
 * request: the state this drawer writes is the state the grid reads. What is
 * *not* applied is a half-built builder row, so the panel stays mounted while
 * closed (`hidden`, not unmounted) and an Escape cannot throw that row away —
 * reopening shows the same half-made rule, exactly as the reader left it.
 *
 * Three accessibility contracts this file owes the reader:
 *
 * * `aria-modal="true"`, because while it is open the panel behind is inert;
 * * a real focus trap: Tab from the last control wraps to the first and
 *   Shift+Tab from the first wraps to the last, so 60-odd controls cannot leak
 *   focus into the page behind (tab 43 used to land on the toolbar);
 * * focus returns to the control that opened the drawer when it closes, so a
 *   keyboard user does not have to find the Filters button again.
 */

import * as React from "react";

import type {
  ActiveFilter,
  PresetBucket,
  PresetGroup,
  WireFilter,
} from "./types";
import type { FilterRegistry } from "./registry";
import { PRESETS } from "./presets";
import { activeBucket, describeFilter } from "./state";
import { ConditionBuilder } from "./ConditionBuilder";

export interface FilterDrawerProps {
  open: boolean;
  registry: FilterRegistry;
  filters: readonly ActiveFilter[];
  onClose: () => void;
  onAdd: (drafts: WireFilter[]) => void;
  onRemove: (id: string) => void;
  onClear: () => void;
  onClearKey: (key: string) => void;
  onApplyBucket: (group: PresetGroup, bucket: PresetBucket) => void;
  /** A key chosen in ⌘K; scrolls the drawer to its control. */
  focusKey: string | null;
}

/** The tab stops inside the dialog, in document order. */
const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

export function FilterDrawer({
  open,
  registry,
  filters,
  onClose,
  onAdd,
  onRemove,
  onClear,
  onClearKey,
  onApplyBucket,
  focusKey,
}: FilterDrawerProps) {
  const panelRef = React.useRef<HTMLDivElement>(null);
  /** The control to hand focus back to when the drawer closes (F5). */
  const returnFocusRef = React.useRef<HTMLElement | null>(null);
  const [showMore, setShowMore] = React.useState(false);
  /** Only preset groups whose key this schema still offers. */
  const groups = React.useMemo(
    () => PRESETS.filter((group) => registry.byKey.has(group.key)),
    [registry],
  );
  const presetKeys = React.useMemo(
    () => new Set(groups.map((group) => group.key)),
    [groups],
  );
  const secondaryKeys = React.useMemo(
    () => new Set(groups.filter((group) => !group.primary).map((group) => group.key)),
    [groups],
  );

  // Capture the opener *before* the next effect moves focus into the panel,
  // and hand it back when the drawer closes. Declared first on purpose: effects
  // run in declaration order, and the trigger is the element that was focused
  // at the moment `open` became true.
  React.useEffect(() => {
    if (!open) return;
    const active = document.activeElement;
    returnFocusRef.current =
      active instanceof HTMLElement && active !== document.body ? active : null;
    return () => {
      const target = returnFocusRef.current;
      returnFocusRef.current = null;
      if (target && target.isConnected) target.focus();
    };
  }, [open]);

  React.useEffect(() => {
    if (!open) return;
    if (!focusKey) {
      if (!panelRef.current?.contains(document.activeElement)) panelRef.current?.focus();
      return;
    }

    // A secondary preset is behind the disclosure; reveal it before focusing,
    // then let the next pass land on its first bucket.
    if (secondaryKeys.has(focusKey) && !showMore) {
      setShowMore(true);
      return;
    }

    const key = focusKey;
    const element = panelRef.current?.querySelector<HTMLElement>(
      `[data-filter-key="${key.replace(/"/g, '\\"')}"]`,
    );
    if (!element) return;
    element.scrollIntoView?.({ block: "center" });
    const focusable =
      element.querySelector<HTMLElement>("button[data-bucket]") ??
      element.querySelector<HTMLElement>("button, input, select");
    focusable?.focus();
  }, [open, focusKey, registry, secondaryKeys, showMore]);

  const primary = groups.filter((group) => group.primary);
  const secondary = groups.filter((group) => !group.primary);

  /** Tab wraps inside the dialog; Escape closes it. */
  const onPanelKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;

    const items = Array.from(
      panelRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [],
    ).filter((element) => element.tabIndex >= 0);
    if (items.length === 0) {
      event.preventDefault();
      return;
    }
    const first = items[0] as HTMLElement;
    const last = items[items.length - 1] as HTMLElement;
    const active = document.activeElement;
    const inside = panelRef.current?.contains(active) ?? false;

    // Only the two edges are intercepted; every Tab in between keeps the
    // browser's own behaviour, so the trap cannot swallow the rest of the UI.
    if (event.shiftKey) {
      if (!inside || active === first) {
        event.preventDefault();
        last.focus();
      }
    } else if (!inside || active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const presetSection = (group: PresetGroup) => {
    const chosen = activeBucket(filters, group);
    const anyFilter = filters.some((filter) => filter.key === group.key);
    return (
      <section
        key={group.key}
        data-filter-key={group.key}
        data-testid={`preset-${group.key}`}
        className="border-t border-line px-3 py-2 first:border-t-0"
      >
        <div className="mb-1 flex items-baseline justify-between">
          <h3 className="text-data text-fg">{group.label}</h3>
        </div>
        <div role="group" aria-label={group.label} className="flex flex-wrap gap-1">
          <button
            type="button"
            aria-pressed={!anyFilter}
            onClick={() => onClearKey(group.key)}
            className="rounded-sm border border-line px-1.5 py-0.5 text-data text-fg-subtle transition-colors hover:border-line-strong hover:text-fg aria-pressed:border-line-strong aria-pressed:bg-surface-raised aria-pressed:text-fg"
          >
            Any
          </button>
          {group.buckets.map((bucket) => (
            <button
              key={bucket.id}
              type="button"
              aria-pressed={chosen === bucket.id}
              data-bucket={bucket.id}
              onClick={() => onApplyBucket(group, bucket)}
              className="rounded-sm border border-line px-1.5 py-0.5 text-data text-fg-muted transition-colors hover:border-line-strong hover:text-fg aria-pressed:border-brand aria-pressed:bg-row-selected aria-pressed:text-fg"
            >
              {bucket.label}
            </button>
          ))}
        </div>
        {group.hint ? (
          <p
            data-testid={`preset-hint-${group.key}`}
            className="mt-1 text-label text-fg-subtle"
          >
            {group.hint}
          </p>
        ) : null}
      </section>
    );
  };

  return (
    <div hidden={!open}>
      <div
        aria-hidden="true"
        data-testid="filter-scrim"
        className="fixed inset-0 z-40 bg-black/40"
        onMouseDown={onClose}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Filters"
        aria-describedby="filter-drawer-hint"
        tabIndex={-1}
        onKeyDown={onPanelKeyDown}
        className="fixed inset-y-0 left-0 z-50 flex w-[340px] max-w-[92vw] flex-col border-r border-line-strong bg-surface shadow-2xl"
      >
        <header className="flex h-10 shrink-0 items-center justify-between gap-2 border-b border-line-strong px-3">
          <div className="flex items-baseline gap-2">
            <h2 className="text-ui font-medium text-fg">Filters</h2>
            <span data-testid="active-filter-count" className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
              {filters.length} active
            </span>
          </div>
          <button
            type="button"
            aria-label="Close filters"
            onClick={onClose}
            className="rounded-sm border border-line px-1.5 py-0.5 text-data text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
          >
            Close
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <p id="filter-drawer-hint" className="px-3 py-2 text-data text-fg-subtle">
            Changes apply as you make them. ⌘K searches every filter label and alias.
          </p>

          {primary.map(presetSection)}

          {secondary.length > 0 ? (
            <>
              <div className="border-t border-line px-3 py-2">
                <button
                  type="button"
                  aria-expanded={showMore}
                  onClick={() => setShowMore((value) => !value)}
                  className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle hover:text-fg"
                >
                  {showMore ? "Fewer presets" : "More numeric presets"}
                </button>
              </div>
              {showMore ? secondary.map(presetSection) : null}
            </>
          ) : null}

          <ConditionBuilder
            registry={registry}
            onAdd={(draft) => {
              onAdd(draft);
            }}
            focusKey={focusKey && !presetKeys.has(focusKey) ? focusKey : null}
          />

          {filters.length > 0 ? (
            <section aria-label="Selected filters" className="border-t border-line px-3 py-2">
              <h3 className="mb-1 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
                On screen
              </h3>
              <ul className="flex flex-col gap-1">
                {filters.map((filter) => {
                  const described = describeFilter(registry, filter);
                  return (
                    <li key={filter.id} className="flex items-center justify-between gap-2 text-data">
                      <span className="truncate text-fg-muted">{described.text}</span>
                      <button
                        type="button"
                        aria-label={`Remove ${described.text}`}
                        onClick={() => onRemove(filter.id)}
                        className="rounded-sm px-1 text-fg-subtle hover:bg-row-hover hover:text-fg"
                      >
                        ×
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          ) : null}
        </div>

        <footer className="flex h-9 shrink-0 items-center justify-between border-t border-line px-3">
          <span className="text-data text-fg-subtle">{filters.length} selected</span>
          <button
            type="button"
            onClick={onClear}
            disabled={filters.length === 0}
            className="rounded-sm border border-line px-2 py-0.5 text-data text-fg-muted transition-colors hover:border-line-strong hover:text-fg disabled:opacity-40"
          >
            Clear all
          </button>
        </footer>
      </div>
    </div>
  );
}
