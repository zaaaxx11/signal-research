/**
 * The column-preset manager: one dialog for the reader's own sets.
 *
 * Four operations, one surface: save the columns on screen under a name,
 * rename a saved set, delete one, and apply one. It is a dialog rather than a
 * row of buttons in the masthead because three destructive verbs do not belong
 * next to the control that picks a set — the masthead keeps "Columns" and one
 * "Manage" button, and the work happens here.
 *
 * The default set ("My stock presets") is listed with the rest but cannot be
 * renamed or deleted: it is the one the header opens with. Saving under an
 * existing name overwrites it, which is what "save" means when the name is
 * already on the list.
 */

import { XIcon } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

import { PRESET_LIMIT, type UserPresetEntry } from "./userPresets";

export interface ColumnPresetsDialogProps {
  open: boolean;
  onClose: () => void;
  presets: readonly UserPresetEntry[];
  /** The columns on screen now: what "save" writes. */
  currentColumns: readonly string[];
  /** Save the current columns under `name` (overwriting a name already used). */
  onSave: (name: string) => void;
  onRename: (id: string, name: string) => void;
  onDelete: (id: string) => void;
  /** Apply a saved set to the screen. */
  onUse: (id: string) => void;
}

const DIALOG_TITLE_ID = "column-presets-title";

export function ColumnPresetsDialog({
  open,
  onClose,
  presets,
  currentColumns,
  onSave,
  onRename,
  onDelete,
  onUse,
}: ColumnPresetsDialogProps) {
  const panelRef = React.useRef<HTMLDivElement>(null);
  const nameRef = React.useRef<HTMLInputElement>(null);
  const openerRef = React.useRef<HTMLElement | null>(null);
  const [name, setName] = React.useState("My stock presets");
  const [editing, setEditing] = React.useState<string | null>(null);
  const [draft, setDraft] = React.useState("");
  const [confirming, setConfirming] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  // The opener is captured before focus moves into the panel, so closing can
  // give it back: a dialog that dumps focus on <body> is a dialog the keyboard
  // has to find again from the top of the page.
  React.useEffect(() => {
    if (!open) return;
    openerRef.current = document.activeElement as HTMLElement | null;
    setEditing(null);
    setConfirming(null);
    setError(null);
    setName("My stock presets");
    const id = window.setTimeout(() => nameRef.current?.focus(), 0);
    return () => {
      window.clearTimeout(id);
      const opener = openerRef.current;
      if (opener && opener.isConnected) opener.focus();
    };
  }, [open]);

  const close = React.useCallback(() => {
    onClose();
  }, [onClose]);

  /** Tab wraps inside the dialog; Escape closes it. */
  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab" || !panelRef.current) return;
    const focusable = [
      ...panelRef.current.querySelectorAll<HTMLElement>(
        "button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])",
      ),
    ].filter((element) => element.offsetParent !== null || element === document.activeElement);
    if (focusable.length === 0) return;
    const first = focusable[0]!;
    const last = focusable[focusable.length - 1]!;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  if (!open) return null;

  const existing = presets.find(
    (preset) => preset.name.toLowerCase() === name.trim().toLowerCase(),
  );

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (trimmed.length === 0) {
      setError("Give the set a name first.");
      nameRef.current?.focus();
      return;
    }
    if (currentColumns.length === 0) {
      setError("There are no columns to save yet.");
      return;
    }
    setError(null);
    onSave(trimmed);
  };

  const commitRename = (id: string) => {
    const trimmed = draft.trim();
    if (trimmed.length === 0) {
      setError("A set needs a name.");
      return;
    }
    onRename(id, trimmed);
    setEditing(null);
    setError(null);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 px-3 py-10">
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={DIALOG_TITLE_ID}
        tabIndex={-1}
        onKeyDown={onKeyDown}
        className="w-full max-w-[520px] overflow-hidden rounded-md border border-line-strong bg-surface shadow-2xl"
      >
        <div className="flex items-center justify-between gap-3 border-b border-line px-3 py-2">
          <h2 id={DIALOG_TITLE_ID} className="text-ui font-semibold text-fg">
            Column presets
          </h2>
          <button
            type="button"
            onClick={close}
            aria-label="Close column presets"
            className="rounded-sm p-1 text-fg-muted transition-colors hover:text-fg"
          >
            <XIcon aria-hidden="true" className="size-3.5" />
          </button>
        </div>

        <form onSubmit={submit} className="border-b border-line px-3 py-2.5">
          <label className="flex flex-col gap-1">
            <span className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
              Save the columns on screen
            </span>
            <div className="flex items-center gap-1.5">
              <input
                ref={nameRef}
                value={name}
                onChange={(event) => setName(event.target.value.slice(0, 60))}
                aria-label="Preset name"
                placeholder="My stock presets"
                className="h-7 min-w-0 flex-1 rounded-sm border border-line-control bg-surface-sunken px-1.5 text-data text-fg"
              />
              <button
                type="submit"
                className="h-7 shrink-0 rounded-sm border border-line-control bg-surface-raised px-2 text-ui text-fg transition-colors hover:border-brand"
              >
                Save preset
              </button>
            </div>
          </label>
          <p className="mt-1 text-data text-fg-subtle">
            {currentColumns.length} column{currentColumns.length === 1 ? "" : "s"} will be saved.
            {existing ? (
              <>
                {" "}
                <span className="text-alert">
                  This replaces “{existing.name}”.
                </span>
              </>
            ) : (
              " A new set is added to the list."
            )}
          </p>
          {error ? (
            <p role="alert" className="mt-1 text-data text-alert">
              {error}
            </p>
          ) : null}
        </form>

        <div className="max-h-[46vh] overflow-y-auto px-3 py-2">
          <p className="mb-1 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
            Saved sets · {presets.length} of {PRESET_LIMIT}
          </p>
          <ul aria-label="Saved column presets" className="flex flex-col gap-1">
            {presets.map((preset) => {
              const isEditing = editing === preset.id;
              const isConfirming = confirming === preset.id;
              return (
                <li
                  key={preset.id}
                  className="flex flex-wrap items-center gap-1.5 rounded-sm border border-line bg-surface-raised px-2 py-1.5"
                >
                  {isEditing ? (
                    <>
                      <input
                        value={draft}
                        autoFocus
                        aria-label={`New name for ${preset.name}`}
                        onChange={(event) => setDraft(event.target.value.slice(0, 60))}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") {
                            event.preventDefault();
                            commitRename(preset.id);
                          }
                        }}
                        className="h-6 min-w-0 flex-1 rounded-sm border border-line-control bg-surface-sunken px-1.5 text-data text-fg"
                      />
                      <button
                        type="button"
                        onClick={() => commitRename(preset.id)}
                        className="h-6 rounded-sm border border-line-control px-1.5 text-data text-fg"
                      >
                        Save name
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditing(null)}
                        className="h-6 rounded-sm px-1.5 text-data text-fg-muted hover:text-fg"
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <>
                      <span className="min-w-0 flex-1 truncate text-data text-fg" title={preset.name}>
                        {preset.name}
                        {preset.builtin ? (
                          <span className="ml-1.5 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
                            default
                          </span>
                        ) : null}
                      </span>
                      <span className="shrink-0 font-mono text-label text-fg-subtle">
                        {preset.columns.length} col{preset.columns.length === 1 ? "" : "s"}
                      </span>
                      <button
                        type="button"
                        onClick={() => onUse(preset.id)}
                        disabled={!preset.saved}
                        title={
                          preset.saved
                            ? `Show this set: ${preset.columns.length} columns.`
                            : "This set has no columns saved yet."
                        }
                        className="h-6 rounded-sm border border-line-control px-1.5 text-data text-fg-muted transition-colors hover:text-fg disabled:opacity-40"
                      >
                        Use
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setEditing(preset.id);
                          setDraft(preset.name);
                        }}
                        disabled={preset.builtin}
                        title={
                          preset.builtin
                            ? "The default set keeps its name."
                            : "Rename this set."
                        }
                        className="h-6 rounded-sm border border-line px-1.5 text-data text-fg-muted transition-colors hover:border-line-strong hover:text-fg disabled:opacity-40"
                      >
                        Rename
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          if (isConfirming) {
                            onDelete(preset.id);
                            setConfirming(null);
                            return;
                          }
                          setConfirming(preset.id);
                        }}
                        disabled={preset.builtin}
                        title={
                          preset.builtin
                            ? "The default set cannot be deleted."
                            : isConfirming
                              ? "Click again to delete this set."
                              : "Delete this set."
                        }
                        className={cn(
                          "h-6 rounded-sm border px-1.5 text-data transition-colors disabled:opacity-40",
                          isConfirming
                            ? "border-alert text-alert"
                            : "border-line text-fg-muted hover:border-line-strong hover:text-fg",
                        )}
                      >
                        {isConfirming ? "Confirm" : "Delete"}
                      </button>
                    </>
                  )}
                </li>
              );
            })}
          </ul>
        </div>

        <div className="flex justify-end border-t border-line px-3 py-2">
          <button
            type="button"
            onClick={close}
            className="h-7 rounded-sm border border-line-control px-2 text-ui text-fg-muted transition-colors hover:text-fg"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
