/**
 * ⌘K: find a filter by its label or by any catalogue spelling of its column.
 *
 * The corpus is the registry, so a refused key is not here to find — typing
 * `idx` or `Index` yields "no filter matches", not a control that will 422.
 * (Adversarial (b) is a property of the data, not a special case in the
 * matcher.)
 *
 * A plain listbox rather than a dialog library: the palette is one input and
 * one list, and the whole interaction is the keyboard. Arrow keys move,
 * Enter opens the drawer at the chosen control, Escape closes.
 */

import * as React from "react";

import type { RegistryEntry, FilterRegistry } from "./registry";

export interface CommandPaletteProps {
  open: boolean;
  registry: FilterRegistry;
  onClose: () => void;
  onPick: (key: string) => void;
}

interface Match {
  entry: RegistryEntry;
  via: string | null;
}

/** Every whitespace-separated term must appear somewhere in the entry's corpus. */
export function matchFilters(registry: FilterRegistry, query: string): Match[] {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const matches: Match[] = [];

  for (const entry of registry.entries) {
    if (terms.every((term) => entry.search.includes(term))) {
      const alias = entry.aliases.find((candidate) =>
        terms.every((term) => candidate.name.toLowerCase().includes(term) || entry.label.toLowerCase().includes(term)),
      );
      matches.push({ entry, via: alias ? alias.name : null });
    }
  }

  return matches;
}

export function CommandPalette({ open, registry, onClose, onPick }: CommandPaletteProps) {
  const [query, setQuery] = React.useState("");
  const [active, setActive] = React.useState(0);
  const inputRef = React.useRef<HTMLInputElement>(null);

  const matches = React.useMemo(
    () => (open ? matchFilters(registry, query) : []),
    [open, registry, query],
  );

  React.useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    inputRef.current?.focus();
  }, [open]);

  React.useEffect(() => {
    setActive(0);
  }, [query]);

  if (!open) return null;

  const pick = (index: number) => {
    const match = matches[index];
    if (!match) return;
    onPick(match.entry.key);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((index) => (matches.length === 0 ? 0 : (index + 1) % matches.length));
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((index) => (matches.length === 0 ? 0 : (index - 1 + matches.length) % matches.length));
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      pick(active);
    }
  };

  const listId = "filter-palette-list";

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 pt-[12vh]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-label="Find a filter"
        className="w-[min(560px,92vw)] overflow-hidden rounded-md border border-line-strong bg-surface-raised shadow-2xl"
      >
        <div className="flex items-center gap-2 border-b border-line px-3">
          <span aria-hidden="true" className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
            ⌘K
          </span>
          <input
            ref={inputRef}
            role="combobox"
            aria-label="Find a filter by name or alias"
            aria-expanded="true"
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={matches.length > 0 ? `filter-option-${active}` : undefined}
            autoComplete="off"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Find a filter — label or alias (e.g. market cap, pe)…"
            className="h-10 w-full bg-transparent text-ui text-fg outline-none placeholder:text-fg-subtle"
          />
          <button
            type="button"
            aria-label="Close filter search"
            onClick={onClose}
            className="rounded-sm px-1.5 text-fg-muted hover:text-fg"
          >
            esc
          </button>
        </div>

        <ul id={listId} role="listbox" aria-label="Filters" className="max-h-[50vh] overflow-y-auto py-1">
          {matches.map((match, index) => (
            <li
              key={match.entry.key}
              id={`filter-option-${index}`}
              role="option"
              aria-selected={index === active}
              data-filter-key={match.entry.key}
              onMouseEnter={() => setActive(index)}
              onMouseDown={(event) => {
                event.preventDefault();
                pick(index);
              }}
              className={`flex cursor-default items-baseline gap-2 px-3 py-1.5 text-ui ${
                index === active ? "bg-row-selected text-fg" : "text-fg-muted"
              }`}
            >
              <span className="truncate">{match.entry.label}</span>
              <span className="ml-auto shrink-0 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
                {match.entry.tab}
              </span>
              {match.via ? (
                <span className="shrink-0 font-mono text-label text-fg-subtle">alias {match.via}</span>
              ) : null}
            </li>
          ))}
        </ul>

        {matches.length === 0 ? (
          // A polite live region, not `role="status"`: the emptiness of a
          // search box is a running commentary, and the app reserves the one
          // status role for the grid's row range.
          <p
            aria-live="polite"
            data-testid="palette-empty"
            className="border-t border-line px-3 py-3 text-data text-fg-subtle"
          >
            No filter matches “{query.trim()}”. Refused keys are not offered.
          </p>
        ) : null}
      </div>
    </div>
  );
}
