/**
 * The active filters, above the table.
 *
 * Every chip is the state: it shows the same label/value the drawer's control
 * shows, and its × calls the same `removeFilter` the drawer would. "Clear all"
 * is the identity — an unfiltered panel — not a second, differently-behaving
 * reset.
 *
 * The bar stays mounted when the screen matches nothing: a zero-row answer with
 * its chips visible is how the reader undoes the filter that caused it.
 */

import type { ActiveFilter } from "./types";
import type { FilterRegistry } from "./registry";
import { describeFilter } from "./state";

export interface ChipBarProps {
  registry: FilterRegistry;
  filters: readonly ActiveFilter[];
  onRemove: (id: string) => void;
  onClear: () => void;
  /** A link's ignored keys, shown where the user can see them next to the data. */
  notice?: string | null;
  onDismissNotice?: () => void;
}

export function ChipBar({
  registry,
  filters,
  onRemove,
  onClear,
  notice,
  onDismissNotice,
}: ChipBarProps) {
  if (filters.length === 0 && !notice) return null;

  return (
    <div
      data-testid="chip-bar"
      className="flex flex-wrap items-center gap-1 border-b border-line bg-surface-sunken px-2 py-1"
    >
      {filters.length > 0 ? (
        <ul aria-label="Active filters" className="flex flex-wrap items-center gap-1">
          {filters.map((filter) => {
            const described = describeFilter(registry, filter);
            return (
              <li key={filter.id}>
                <span className="inline-flex h-6 items-center gap-1 rounded-sm border border-line-strong bg-surface-raised pl-1.5 text-data text-fg">
                  <span className="text-fg-subtle">{described.label}</span>
                  <span className="nums text-fg-muted">{described.value}</span>
                  <button
                    type="button"
                    aria-label={`Remove filter: ${described.text}`}
                    onClick={() => onRemove(filter.id)}
                    className="h-5 w-5 rounded-sm text-fg-muted transition-colors hover:bg-row-hover hover:text-fg"
                  >
                    ×
                  </button>
                </span>
              </li>
            );
          })}
        </ul>
      ) : null}

      {filters.length > 0 ? (
        <button
          type="button"
          onClick={onClear}
          className="ml-1 rounded-sm px-1.5 py-0.5 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle transition-colors hover:text-fg"
        >
          Clear all
        </button>
      ) : null}

      {notice ? (
        <p role="status" className="ml-auto flex items-center gap-2 text-data text-alert">
          <span>{notice}</span>
          {onDismissNotice ? (
            <button
              type="button"
              aria-label="Dismiss filter notice"
              onClick={onDismissNotice}
              className="rounded-sm px-1 text-fg-muted hover:text-fg"
            >
              ×
            </button>
          ) : null}
        </p>
      ) : null}
    </div>
  );
}
