/**
 * The saved-screens box: one surface for search, save, apply, rename, delete.
 *
 * There used to be two controls here — a `<select>` of sets and a "Manage"
 * button that opened a dialog with a second copy of the same list. Two lists of
 * the same thing, one of which could not be searched, is how a reader saves a
 * screen and then cannot find it. This is the one box: the field filters the
 * list, the action saves what is on screen, and every row carries its own
 * pencil and ×.
 *
 * The delete is immediate, not a two-press confirm: the × is inside a list the
 * reader opened deliberately, the row is one line, and the alternative — a
 * confirm state that resets on any re-render — has cost more screens than it
 * has saved. Rename stays inline so a typo never needs a dialog.
 */

import * as React from "react";

import { Icon } from "@/components/Icon";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

import { PRESET_LIMIT, type UserPresetEntry } from "./userPresets";

export const PRESET_PANEL_ID = "screener-preset-panel";

export interface PresetPanelProps {
  presets: readonly UserPresetEntry[];
  /** The columns on screen now: what "Save Current Screen" writes. */
  currentColumns: readonly string[];
  onApply: (id: string) => void;
  onSave: (name: string) => void;
  onRename: (id: string, name: string) => void;
  onDelete: (id: string) => void;
  onClose: () => void;
}

/** "Screen 4" — the first free number, so a save never overwrites silently. */
export function nextScreenName(names: readonly string[]): string {
  const taken = new Set(names.map((name) => name.toLowerCase()));
  for (let index = 1; index <= PRESET_LIMIT + 1; index += 1) {
    const candidate = `Screen ${index}`;
    if (!taken.has(candidate.toLowerCase())) return candidate;
  }
  return `Screen ${Date.now()}`;
}

export function PresetPanel({
  presets,
  currentColumns,
  onApply,
  onSave,
  onRename,
  onDelete,
  onClose,
}: PresetPanelProps) {
  const [query, setQuery] = React.useState("");
  const [renaming, setRenaming] = React.useState<string | null>(null);
  const [draft, setDraft] = React.useState("");
  const [saved, setSaved] = React.useState<string | null>(null);

  const savedScreens = presets.filter((preset) => preset.saved);
  const needle = query.trim().toLowerCase();
  const visible = needle.length
    ? savedScreens.filter((preset) => preset.name.toLowerCase().includes(needle))
    : savedScreens;

  const save = () => {
    if (currentColumns.length === 0) return;
    const name = nextScreenName(savedScreens.map((preset) => preset.name));
    onSave(name);
    setSaved(`Saved “${name}” · ${currentColumns.length} columns.`);
  };

  const commitRename = (id: string) => {
    const trimmed = draft.trim();
    if (trimmed.length === 0) return;
    onRename(id, trimmed);
    setRenaming(null);
    setDraft("");
  };

  return (
    <div
      id={PRESET_PANEL_ID}
      role="dialog"
      aria-label="Saved screens"
      data-testid="preset-panel"
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        event.stopPropagation();
        event.preventDefault();
        onClose();
      }}
      className="absolute left-0 top-[calc(100%+6px)] z-40 w-[320px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-md border border-line-strong bg-popover shadow-[0_16px_40px_-12px_rgba(0,0,0,0.85)]"
    >
      <div className="flex flex-col gap-1.5 border-b border-line p-2">
        <label className="flex h-7 items-center gap-1.5 rounded-sm border border-line-control bg-surface-sunken px-2 focus-within:border-focus">
          <Icon name="search" className="text-fg-subtle" />
          <input
            type="search"
            autoFocus
            aria-label="Search presets"
            placeholder="Search presets..."
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="h-full min-w-0 flex-1 bg-transparent text-data text-fg placeholder:text-fg-subtle focus-visible:outline-none"
          />
        </label>
        <TooltipProvider delayDuration={250}>
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                data-testid="save-current-screen"
                onClick={save}
                disabled={currentColumns.length === 0}
                className="flex h-7 items-center justify-center gap-1.5 rounded-sm border border-line-control bg-surface-raised px-2 text-ui text-fg transition-colors hover:border-brand disabled:opacity-40"
              >
                <Icon name="bookmark" />
                Save Current Screen
              </button>
            </TooltipTrigger>
            <TooltipContent side="top">
              Save the columns on screen as a new screen you can come back to
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
        {saved ? (
          <p role="status" aria-live="polite" className="text-data text-up">
            {saved}
          </p>
        ) : null}
      </div>

      <div className="max-h-[46vh] overflow-y-auto p-1.5">
        {savedScreens.length === 0 ? (
          <p className="px-1.5 py-2 text-data text-fg-muted">
            You don't have any saved screens yet.
          </p>
        ) : visible.length === 0 ? (
          <p className="px-1.5 py-2 text-data text-fg-muted">
            No saved screens match “{query.trim()}”.
          </p>
        ) : (
          <ul aria-label="Saved screens" className="flex flex-col">
            {visible.map((preset) => {
              const editing = renaming === preset.id;
              return (
                <li
                  key={preset.id}
                  data-testid={`preset-row-${preset.id}`}
                  className="flex items-center gap-1 rounded-sm px-1 py-0.5 hover:bg-row-hover"
                >
                  {editing ? (
                    <>
                      <input
                        autoFocus
                        aria-label={`New name for ${preset.name}`}
                        value={draft}
                        onChange={(event) => setDraft(event.target.value.slice(0, 60))}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") {
                            event.preventDefault();
                            commitRename(preset.id);
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
                        onClick={() => commitRename(preset.id)}
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
                        onClick={() => onApply(preset.id)}
                        aria-label={`Show ${preset.name}`}
                        title={`Show these ${preset.columns.length} columns.`}
                        className="flex min-w-0 flex-1 items-baseline gap-2 rounded-sm px-1 py-0.5 text-left"
                      >
                        <span className="min-w-0 flex-1 truncate text-data text-fg">
                          {preset.name}
                        </span>
                        <span className="shrink-0 font-mono text-label text-fg-subtle">
                          {preset.columns.length} col{preset.columns.length === 1 ? "" : "s"}
                        </span>
                      </button>
                      <span
                        role="group"
                        aria-label={`Options for ${preset.name}`}
                        className="flex shrink-0 items-center gap-0.5"
                      >
                        <button
                          type="button"
                          onClick={() => {
                            setRenaming(preset.id);
                            setDraft(preset.name);
                          }}
                          disabled={preset.builtin}
                          aria-label={`Rename ${preset.name}`}
                          title={preset.builtin ? "The default set keeps its name." : "Rename"}
                          className={cn(
                            "flex size-5 items-center justify-center rounded-sm text-fg-subtle transition-colors hover:bg-surface hover:text-fg",
                            preset.builtin && "opacity-40",
                          )}
                        >
                          <Icon name="pencil" size={12} />
                        </button>
                        <button
                          type="button"
                          onClick={() => onDelete(preset.id)}
                          disabled={preset.builtin}
                          aria-label={`Delete ${preset.name}`}
                          title={preset.builtin ? "The default set cannot be deleted." : "Delete"}
                          className={cn(
                            "flex size-5 items-center justify-center rounded-sm text-fg-subtle transition-colors hover:bg-surface hover:text-alert",
                            preset.builtin && "opacity-40",
                          )}
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
        <span>
          {savedScreens.length} of {PRESET_LIMIT}
        </span>
        <span>Kept in this browser</span>
      </div>
    </div>
  );
}
