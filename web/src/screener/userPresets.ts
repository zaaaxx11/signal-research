/**
 * The reader's own column sets, kept in this browser.
 *
 * A **column preset** here is not one of the built-in tabs: it is a set the
 * reader built, named and saved, and expects to find again tomorrow. It lives
 * in `localStorage` under one key, survives a reload and a new tab, and is
 * transportable in nothing but this browser — the server has no opinion about
 * how somebody likes their columns.
 *
 * One entry always exists: **My stock presets** (`DEFAULT_PRESET_ID`). It is the
 * default working set — save the columns on screen into it and it is the one
 * the header opens with. It cannot be renamed or deleted, because a default
 * that can be deleted stops being a default; it *can* be overwritten, which is
 * the whole point of "save".
 *
 * Storage is read once and written on every change. A stored document that is
 * not what this build wrote (another version, a hand-edited string, a browser
 * that threw on read) is treated as empty rather than trusted: a corrupted
 * preset must cost the reader their presets, never the screen.
 */

import * as React from "react";

/** The one key this feature owns. */
export const USER_PRESETS_KEY = "ztockz.screens.v1";

/**
 * The key this feature shipped under first. Read once when the new key is
 * empty, written to the new key, left in place: a reader who saved screens
 * before this build must not open the box to an empty list.
 */
export const LEGACY_PRESETS_KEY = "ztockz.columns.v1";

/** The default entry: always present, never renamed or deleted. */
export const DEFAULT_PRESET_ID = "my-stock-presets";

/** The name the default entry always wears. */
export const DEFAULT_PRESET_NAME = "My stock presets";

/** The most columns one preset may carry — the panel's own request cap. */
export const PRESET_COLUMN_CAP = 40;

/** The most presets one browser may keep, so the select stays a menu. */
export const PRESET_LIMIT = 24;

export interface SavedColumnPreset {
  id: string;
  name: string;
  /** Column keys, in the order the table draws them. */
  columns: string[];
}

export interface UserPresetState {
  presets: SavedColumnPreset[];
  /** Which preset the Columns control shows; the default when unknown. */
  selected: string;
}

/** One preset as a control reads it: the default entry is flagged. */
export interface UserPresetEntry extends SavedColumnPreset {
  builtin: boolean;
  /** True once the default entry has columns saved into it. */
  saved: boolean;
}

function defaultPreset(): SavedColumnPreset {
  return { id: DEFAULT_PRESET_ID, name: DEFAULT_PRESET_NAME, columns: [] };
}

export function emptyUserPresets(): UserPresetState {
  return { presets: [defaultPreset()], selected: DEFAULT_PRESET_ID };
}

function cleanColumns(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  const seen = new Set<string>();
  const keys: string[] = [];
  for (const entry of value) {
    if (typeof entry !== "string" || entry.length === 0 || seen.has(entry)) continue;
    seen.add(entry);
    keys.push(entry);
    if (keys.length >= PRESET_COLUMN_CAP) break;
  }
  return keys;
}

function cleanName(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const name = value.replace(/\s+/g, " ").trim();
  if (name.length === 0) return null;
  return name.slice(0, 60);
}

/**
 * The stored document, or an empty one.
 *
 * Every preset is re-validated: an id, a name and a column list. A preset that
 * fails any of them is dropped by name, and the default entry is re-inserted if
 * the document had lost it — so the control always has something to show.
 */
export function parseUserPresets(raw: string | null): UserPresetState {
  if (raw === null || raw.length === 0) return emptyUserPresets();

  let body: unknown;
  try {
    body = JSON.parse(raw) as unknown;
  } catch {
    return emptyUserPresets();
  }
  if (typeof body !== "object" || body === null) return emptyUserPresets();

  const record = body as { version?: unknown; presets?: unknown; selected?: unknown };
  if (record.version !== 1 || !Array.isArray(record.presets)) return emptyUserPresets();

  const presets: SavedColumnPreset[] = [];
  const seenIds = new Set<string>();
  const seenNames = new Set<string>([DEFAULT_PRESET_NAME.toLowerCase()]);

  for (const entry of record.presets) {
    if (typeof entry !== "object" || entry === null) continue;
    const candidate = entry as { id?: unknown; name?: unknown; columns?: unknown };
    const id = typeof candidate.id === "string" && candidate.id.length > 0 ? candidate.id : null;
    const name = cleanName(candidate.name);
    const columns = cleanColumns(candidate.columns);
    if (id === null || name === null || columns === null) continue;
    if (seenIds.has(id)) continue;
    // A second preset with the same name would make the select ambiguous.
    if (seenNames.has(name.toLowerCase())) continue;
    seenIds.add(id);
    seenNames.add(name.toLowerCase());
    presets.push({ id, name, columns });
    if (presets.length >= PRESET_LIMIT) break;
  }

  const fallback = presets.find((preset) => preset.id === DEFAULT_PRESET_ID);
  if (!fallback) presets.unshift(defaultPreset());
  else if (fallback.name !== DEFAULT_PRESET_NAME) {
    fallback.name = DEFAULT_PRESET_NAME;
  }

  const selected =
    typeof record.selected === "string" && presets.some((preset) => preset.id === record.selected)
      ? record.selected
      : DEFAULT_PRESET_ID;

  return { presets, selected };
}

/** The stored form. Written only after `parseUserPresets` has vetted the state. */
export function serializeUserPresets(state: UserPresetState): string {
  return JSON.stringify({ version: 1, presets: state.presets, selected: state.selected });
}

/** Stable, readable ids: one per save, no randomness a test cannot predict. */
let sequence = 0;
export function nextPresetId(name: string): string {
  sequence += 1;
  const slug =
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 24) || "preset";
  return `${slug}-${sequence}`;
}

/** Test seam: ids restart, like the filter ids. */
export function resetPresetSequence(): void {
  sequence = 0;
}

/**
 * Save `columns` under `name`.
 *
 * A name already in use is **overwritten**, not duplicated: the reader typed a
 * name they already had, and two presets called "Cheap tech" would make the
 * control ask which one they meant. The default entry keeps its own name.
 */
export function savePreset(
  state: UserPresetState,
  name: string,
  columns: readonly string[],
): { state: UserPresetState; preset: SavedColumnPreset } {
  const clean = cleanName(name) ?? DEFAULT_PRESET_NAME;
  const keys = cleanColumns(columns) ?? [];
  const existing = state.presets.find(
    (preset) => preset.name.toLowerCase() === clean.toLowerCase(),
  );
  if (existing) {
    const next = { ...existing, columns: keys };
    return {
      state: {
        presets: state.presets.map((preset) => (preset.id === existing.id ? next : preset)),
        selected: existing.id,
      },
      preset: next,
    };
  }
  const preset: SavedColumnPreset = { id: nextPresetId(clean), name: clean, columns: keys };
  return {
    state: {
      presets: [...state.presets, preset].slice(0, PRESET_LIMIT),
      selected: preset.id,
    },
    preset,
  };
}

/** Rename a saved preset. The default entry's name is fixed. */
export function renamePreset(
  state: UserPresetState,
  id: string,
  name: string,
): UserPresetState {
  if (id === DEFAULT_PRESET_ID) return state;
  const clean = cleanName(name);
  if (clean === null) return state;
  const taken = state.presets.some(
    (preset) => preset.id !== id && preset.name.toLowerCase() === clean.toLowerCase(),
  );
  if (taken) return state;
  return {
    ...state,
    presets: state.presets.map((preset) =>
      preset.id === id ? { ...preset, name: clean } : preset,
    ),
  };
}

/** Delete a saved preset. The default entry cannot be deleted. */
export function removePreset(state: UserPresetState, id: string): UserPresetState {
  if (id === DEFAULT_PRESET_ID) return state;
  const presets = state.presets.filter((preset) => preset.id !== id);
  return {
    presets,
    selected: state.selected === id ? DEFAULT_PRESET_ID : state.selected,
  };
}

export function selectPreset(state: UserPresetState, id: string): UserPresetState {
  if (!state.presets.some((preset) => preset.id === id)) return state;
  return { ...state, selected: id };
}

/** The entries the control draws, the default entry first. */
export function presetEntries(state: UserPresetState): UserPresetEntry[] {
  return state.presets.map((preset) => ({
    ...preset,
    builtin: preset.id === DEFAULT_PRESET_ID,
    saved: preset.columns.length > 0,
  }));
}

function readStorage(): string | null {
  try {
    const current = window.localStorage.getItem(USER_PRESETS_KEY);
    if (current !== null && current.length > 0) return current;
    const legacy = window.localStorage.getItem(LEGACY_PRESETS_KEY);
    if (legacy !== null && legacy.length > 0) {
      // Carry the old document forward under the key this build writes.
      window.localStorage.setItem(USER_PRESETS_KEY, legacy);
      return legacy;
    }
    return null;
  } catch {
    // A browser that refuses storage (private mode, a locked-down profile) is
    // not an error the reader can act on: the presets simply do not persist.
    return null;
  }
}

function writeStorage(state: UserPresetState): void {
  try {
    window.localStorage.setItem(USER_PRESETS_KEY, serializeUserPresets(state));
  } catch {
    // Same rule: the screen works, the set is session-only.
  }
}

export interface UserPresetsApi {
  presets: UserPresetEntry[];
  selected: string;
  /** Save the current columns under a name; returns the preset that was written. */
  save: (name: string, columns: readonly string[]) => SavedColumnPreset;
  rename: (id: string, name: string) => void;
  remove: (id: string) => void;
  select: (id: string) => void;
}

export function useUserColumnPresets(): UserPresetsApi {
  const [state, setState] = React.useState<UserPresetState>(() =>
    parseUserPresets(readStorage()),
  );

  const commit = React.useCallback((next: UserPresetState) => {
    setState(next);
    writeStorage(next);
  }, []);

  const save = React.useCallback(
    (name: string, columns: readonly string[]) => {
      const result = savePreset(state, name, columns);
      commit(result.state);
      return result.preset;
    },
    [state, commit],
  );

  const rename = React.useCallback(
    (id: string, name: string) => commit(renamePreset(state, id, name)),
    [state, commit],
  );

  const remove = React.useCallback(
    (id: string) => commit(removePreset(state, id)),
    [state, commit],
  );

  const select = React.useCallback(
    (id: string) => commit(selectPreset(state, id)),
    [state, commit],
  );

  const entries = React.useMemo(() => presetEntries(state), [state]);

  return { presets: entries, selected: state.selected, save, rename, remove, select };
}
