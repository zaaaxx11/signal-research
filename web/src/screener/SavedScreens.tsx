/**
 * Saved screen-state presets — save/load from the server, not localStorage.
 *
 * One box for a whole screen (filters, sort, view), not columns alone.  The
 * search filters the saved list; the Save button captures everything on screen
 * right now and sends it to ``POST /screens/saved``.  Every row restores its
 * state (with stale-filter discard), renames inline, or deletes immediately.
 *
 * Column persistence is deliberately absent: only the view id is saved.  The
 * view's own column definition is authoritative.
 */

import * as React from "react";

import { Icon } from "@/components/Icon";
import type { ActiveFilter, FilterOp } from "@/filters/types";
import type { ScreenStateApi } from "@/filters/useScreenState";
import type { FilterRegistry } from "@/filters/registry";
import type { SavedScreen } from "@/lib/api";
import { listSavedScreens, saveScreen, renameScreen, deleteScreen } from "@/lib/api";

export interface SavedScreensProps {
  /** The screen state this panel reads from and writes to. */
  screen: ScreenStateApi;
  /** The live registry, for stale-key discard on restore. */
  registry: FilterRegistry | null;
  onClose: () => void;
}

/**
 * Drop filters whose key is not in the registry.
 *
 * This is the stale-key discard: a saved screen may carry filters from a
 * previous build where a key existed; the client silently removes them.
 */
function discardStale(
  filters: ActiveFilter[],
  registry: FilterRegistry | null,
): ActiveFilter[] {
  if (!registry) return filters;
  return filters.filter((f) => registry.byKey.has(f.key));
}

export function SavedScreens({ screen, registry, onClose }: SavedScreensProps) {
  const [query, setQuery] = React.useState("");
  const [screens, setScreens] = React.useState<SavedScreen[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [renaming, setRenaming] = React.useState<string | null>(null);
  const [draft, setDraft] = React.useState("");

  // Load on mount
  React.useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await listSavedScreens();
        if (!cancelled) {
          setScreens(data);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not load saved screens.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, []);

  const needle = query.trim().toLowerCase();
  const visible = needle.length
    ? screens.filter((s) => s.name.toLowerCase().includes(needle))
    : screens;

  const handleSave = async () => {
    const state = {
      filters: screen.state.filters.map((f) => ({ key: f.key, op: f.op, value: f.value })),
      sort: screen.state.sort,
      view: screen.state.view,
    };
    try {
      const saved = await saveScreen("Screen", state);
      setScreens((prev) => [saved, ...prev]);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the screen.");
    }
  };

  const handleRestore = (saved: SavedScreen) => {
    const state = saved.state as Record<string, unknown>;
    // Apply view first (column selection may depend on it)
    if (typeof state.view === "string" && state.view.length > 0) {
      screen.setView(state.view);
    }
    // Apply sort
    if (typeof state.sort === "string") {
      screen.setSort(state.sort);
    }
    // Apply filters with stale-key discard
    const rawFilters = Array.isArray(state.filters) ? state.filters : [];
    const cleaned = discardStale(
      rawFilters.map((f: Record<string, unknown>, i: number) => ({
        id: `saved-${saved.id}-${i}`,
        key: String(f.key ?? ""),
        op: (String(f.op ?? "eq")) as FilterOp,
        value: f.value as ActiveFilter["value"],
      })),
      registry,
    );
    screen.setFilters(cleaned);
    onClose();
  };

  const commitRename = async (id: string) => {
    const trimmed = draft.trim();
    if (trimmed.length === 0) return;
    try {
      await renameScreen(id, trimmed);
      setScreens((prev) =>
        prev.map((s) => (s.id === id ? { ...s, name: trimmed } : s)),
      );
      setRenaming(null);
      setDraft("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not rename.");
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteScreen(id);
      setScreens((prev) => prev.filter((s) => s.id !== id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete.");
    }
  };

  return (
    <div
      role="dialog"
      aria-label="Saved screens"
      data-testid="saved-screens-panel"
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        event.stopPropagation();
        event.preventDefault();
        onClose();
      }}
      className="absolute right-0 top-[calc(100%+6px)] z-40 w-[320px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-md border border-line-strong bg-popover shadow-[0_16px_40px_-12px_rgba(0,0,0,0.85)]"
    >
      <div className="flex flex-col gap-1.5 border-b border-line p-2">
        <label className="flex h-7 items-center gap-1.5 rounded-sm border border-line-control bg-surface-sunken px-2 focus-within:border-focus">
          <Icon name="search" className="text-fg-subtle" />
          <input
            type="search"
            autoFocus
            aria-label="Search saved screens"
            placeholder="Search saved screens…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="h-full min-w-0 flex-1 bg-transparent text-data text-fg placeholder:text-fg-subtle focus-visible:outline-none"
          />
        </label>
        <button
          type="button"
          data-testid="save-current-screen"
          onClick={handleSave}
          disabled={!screen.ready}
          title="Save the current filters, view and sort as a named screen you can come back to."
          className="flex h-7 items-center justify-center gap-1.5 rounded-sm border border-line-control bg-surface-raised px-2 text-ui text-fg transition-colors hover:border-brand disabled:opacity-40"
        >
          <Icon name="bookmark" />
          Save Current Screen
        </button>
        {error ? (
          <p role="alert" className="text-data text-alert">
            {error}
          </p>
        ) : null}
      </div>

      <div className="max-h-[46vh] overflow-y-auto p-1.5">
        {loading ? (
          <p className="px-1.5 py-2 text-data text-fg-muted" aria-busy="true">
            Loading…
          </p>
        ) : screens.length === 0 ? (
          <p className="px-1.5 py-2 text-data text-fg-muted">
            You don't have any saved screens yet.
          </p>
        ) : visible.length === 0 ? (
          <p className="px-1.5 py-2 text-data text-fg-muted">
            No saved screens match "{query.trim()}".
          </p>
        ) : (
          <ul aria-label="Saved screens" className="flex flex-col">
            {visible.map((saved) => {
              const editing = renaming === saved.id;
              return (
                <li
                  key={saved.id}
                  data-testid={`saved-screen-row-${saved.id}`}
                  className="flex items-center gap-1 rounded-sm px-1 py-0.5 hover:bg-row-hover"
                >
                  {editing ? (
                    <>
                      <input
                        autoFocus
                        aria-label={`New name for ${saved.name}`}
                        value={draft}
                        onChange={(event) => setDraft(event.target.value.slice(0, 200))}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") {
                            event.preventDefault();
                            commitRename(saved.id);
                          }
                          if (event.key === "Escape") {
                            event.stopPropagation();
                            setRenaming(null);
                          }
                        }}
                        className="h-6 min-w-0 flex-1 rounded-sm border border-line-control bg-surface-sunken px-1.5 text-data text-fg"
                      />
                      <button
                        type="button"
                        onClick={() => commitRename(saved.id)}
                        className="h-6 rounded-sm border border-line-control px-1.5 text-data text-fg"
                      >
                        Save
                      </button>
                      <button
                        type="button"
                        onClick={() => setRenaming(null)}
                        className="h-6 rounded-sm px-1.5 text-data text-fg-muted hover:text-fg"
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        type="button"
                        onClick={() => handleRestore(saved)}
                        aria-label={`Restore ${saved.name}`}
                        title={`Restore filters, view and sort from ${saved.name}.`}
                        className="flex min-w-0 flex-1 items-baseline gap-2 rounded-sm px-1 py-0.5 text-left"
                      >
                        <span className="min-w-0 flex-1 truncate text-data text-fg">
                          {saved.name}
                        </span>
                        <span className="shrink-0 font-mono text-label text-fg-subtle">
                          {saved.state && typeof saved.state.view === "string"
                            ? saved.state.view
                            : ""}
                        </span>
                      </button>
                      <span
                        role="group"
                        aria-label={`Options for ${saved.name}`}
                        className="flex shrink-0 items-center gap-0.5"
                      >
                        <button
                          type="button"
                          onClick={() => {
                            setRenaming(saved.id);
                            setDraft(saved.name);
                          }}
                          aria-label={`Rename ${saved.name}`}
                          title="Rename"
                          className="flex size-5 items-center justify-center rounded-sm text-fg-subtle transition-colors hover:bg-surface hover:text-fg"
                        >
                          <Icon name="pencil" size={12} />
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDelete(saved.id)}
                          aria-label={`Delete ${saved.name}`}
                          title="Delete"
                          className="flex size-5 items-center justify-center rounded-sm text-fg-subtle transition-colors hover:bg-surface hover:text-alert"
                        >
                          <Icon name="x" size={12} />
                        </button>
                      </span>
                    </>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="flex items-center justify-between border-t border-line px-2 py-1 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
        <span>{screens.length} saved</span>
        <span>Kept on the server</span>
      </div>
    </div>
  );
}