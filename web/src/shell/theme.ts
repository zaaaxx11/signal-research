/**
 * Day/night: the document's `data-theme`, and the hook the header reads.
 *
 * The palette ships both blocks in one stylesheet — `:root` is dark,
 * `[data-theme="light"]` is day — so switching themes is one attribute write on
 * <html>: no stylesheet swap, no re-render of the tree, no network request.
 *
 * Why `useSyncExternalStore`
 *   The choice lives outside React (localStorage, the OS media query, and the
 *   DOM attribute itself). A `useState` initialiser would read it once and then
 *   drift: two components that each called `useTheme()` would keep separate
 *   copies, and the one that did not press the button would keep painting the
 *   old label. `listeners` below is the single source of truth — `applyTheme`
 *   writes the attribute, then notifies every subscriber, and React re-reads
 *   `readTheme()`.
 *
 * No flash
 *   `applyTheme(readTheme())` runs at module evaluation, which happens while
 *   the entry chunk is being evaluated — before React mounts and before the
 *   first paint — so a stored "light" choice produces `data-theme="light"`
 *   while the module graph is still loading and the first painted frame is
 *   already light. What this cannot cover is the gap *before* the module is
 *   fetched and evaluated; closing that needs a blocking inline script in
 *   index.html reading the same key. That is out of scope here: the app applies
 *   the theme on mount, and this module applies it earlier still, at import.
 *
 * Storage is best effort
 *   Private mode, a locked-down webview and a browser with cookies blocked make
 *   `localStorage` throw on read *and* on write. Every access is guarded, so a
 *   preference that cannot persist still works for the page view and nothing
 *   exported from this module throws because storage did.
 *
 * Cross-tab sync and OS-theme changes after first paint are deliberately not
 * subscribed: this module owns the current document, and a `storage`/media
 * listener is a different (and much larger) contract than "remember my choice".
 */

import * as React from "react";

export type Theme = "dark" | "light";

/** The one key. Versioned so a future shape change cannot be read as this one. */
export const THEME_KEY = "ztockz.theme.v1";

/** The product is dark-first: the OS is only consulted when nothing is stored. */
const LIGHT_QUERY = "(prefers-color-scheme: light)";

type Listener = () => void;

/** One per document, not one per component: header today, settings tomorrow. */
const listeners = new Set<Listener>();

function isTheme(value: unknown): value is Theme {
  return value === "dark" || value === "light";
}

/** `localStorage`, or `null` when merely touching it throws (private mode). */
function storage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

type Stored = { kind: "none" } | { kind: "theme"; theme: Theme } | { kind: "invalid" };

/**
 * Three outcomes, because "no choice" and "a corrupt choice" are different:
 * the first leaves the OS in charge, the second does not.
 */
function readStored(): Stored {
  const store = storage();
  if (store === null) return { kind: "none" };
  let raw: string | null;
  try {
    raw = store.getItem(THEME_KEY);
  } catch {
    return { kind: "none" };
  }
  if (raw === null) return { kind: "none" };
  return isTheme(raw) ? { kind: "theme", theme: raw } : { kind: "invalid" };
}

/** Light only when the reader's system says so — and never when it cannot say. */
function prefersLight(): boolean {
  if (typeof window === "undefined") return false;
  if (typeof window.matchMedia !== "function") return false;
  try {
    return window.matchMedia(LIGHT_QUERY).matches;
  } catch {
    return false;
  }
}

/** The theme currently painted on the document, if it is a valid one. */
function appliedTheme(): Theme | null {
  if (typeof document === "undefined") return null;
  const applied = document.documentElement.dataset.theme;
  return isTheme(applied) ? applied : null;
}

/**
 * The theme the document should be showing, in priority order: what is already
 * painted, then the stored choice, then the OS, then the shipped default.
 *
 * The painted attribute wins because it is what the reader is looking at: in
 * private mode (storage unwritable) a toggle would otherwise be undone by the
 * next read, and the label on the button would disagree with the page.
 */
export function readTheme(): Theme {
  const applied = appliedTheme();
  if (applied !== null) return applied;
  const stored = readStored();
  if (stored.kind === "theme") return stored.theme;
  // A corrupted value is not evidence of a light preference. The product ships
  // dark, so a key we cannot read falls back to the default rather than to the
  // OS — "stored but unreadable" is not the same as "never chosen".
  if (stored.kind === "invalid") return "dark";
  return prefersLight() ? "light" : "dark";
}

/**
 * Paint a theme: one attribute on <html>, which switches which block of
 * `tokens.css` applies, then wake every subscriber. Does not persist — that is
 * `setTheme`'s job, so the module can paint the *environment* default at load
 * without freezing it into storage forever.
 */
export function applyTheme(theme: Theme): void {
  if (typeof document !== "undefined") {
    document.documentElement.dataset.theme = theme;
  }
  for (const listener of listeners) listener();
}

/** Remember a choice, then paint it. Persistence is best effort, painting is not. */
export function setTheme(theme: Theme): void {
  const store = storage();
  if (store !== null) {
    try {
      store.setItem(THEME_KEY, theme);
    } catch {
      // A preference that cannot persist is still this page view's preference.
    }
  }
  applyTheme(theme);
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export interface ThemeController {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  toggle: () => void;
}

/**
 * The header's three properties. `theme` is the painted value, so the button's
 * label and `aria-pressed` cannot disagree with the page; `toggle` reads that
 * same value, so it always flips — including in private mode, where nothing is
 * written to storage.
 */
export function useTheme(): ThemeController {
  const theme = React.useSyncExternalStore(subscribe, readTheme, () => "dark" as Theme);
  const toggle = React.useCallback(() => {
    setTheme(readTheme() === "dark" ? "light" : "dark");
  }, []);
  return { theme, setTheme, toggle };
}

/*
 * Paint before the app renders. Module evaluation happens inside the entry
 * chunk, ahead of ReactDOM.createRoot(...).render(...) and therefore ahead of
 * the first frame; the write is synchronous and the attribute is already on
 * <html> by the time any component reads `useTheme()`.
 *
 * Deliberately not persisted: writing the environment default into storage on
 * a first visit would turn the OS preference into a hard choice, and a reader
 * whose system switches to dark at sunset would never see it.
 */
if (typeof document !== "undefined") {
  applyTheme(readTheme());
}
