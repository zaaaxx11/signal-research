/**
 * The theme store's contract.
 *
 * Three things are being proven here, and only the first is obvious:
 *
 *   1. the resolved theme is stored choice → OS → dark, and a corrupt key is
 *      not a light preference;
 *   2. the toggle writes `data-theme` on <html> *and* remembers the choice, so
 *      a reload lands on the same theme;
 *   3. storage is never load-bearing. Private mode throws on read and on write,
 *      and both must leave a working theme behind — including a toggle that
 *      flips back, which is why the painted attribute outranks storage.
 *
 * The store is module state, so a test that needs the import-time behaviour
 * re-evaluates the module (`vi.resetModules()` + `import`) against the storage
 * it has just seeded.
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { THEME_KEY, applyTheme, readTheme, setTheme, useTheme } from "./theme";

const LIGHT_QUERY = "(prefers-color-scheme: light)";

/** The document attribute is the app's output; each test starts with none set. */
function clearAttribute(): void {
  delete document.documentElement.dataset.theme;
}

/** jsdom has no `matchMedia`; this is the only part of it the module uses. */
function stubMedia(prefersLight: boolean): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query === LIGHT_QUERY && prefersLight,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }));
}

/**
 * `window.localStorage` is an own accessor on jsdom's window. Safari's private
 * mode throws from that getter, which is a different failure from a `setItem`
 * that throws — both are exercised.
 */
const realStorage = Object.getOwnPropertyDescriptor(window, "localStorage");

function denyStorageAccess(): void {
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    get() {
      throw new Error("SecurityError: localStorage is disabled");
    },
  });
}

function restoreStorage(): void {
  if (realStorage !== undefined) Object.defineProperty(window, "localStorage", realStorage);
  else delete (window as { localStorage?: Storage }).localStorage;
}

beforeEach(() => {
  restoreStorage();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  clearAttribute();
});

afterEach(() => {
  restoreStorage();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  clearAttribute();
});

describe("theme", () => {
  it("follows the OS preference when nothing is stored", () => {
    stubMedia(true);
    expect(readTheme()).toBe("light");
    stubMedia(false);
    expect(readTheme()).toBe("dark");
  });

  it("lets a stored choice win over the OS preference", () => {
    stubMedia(false);
    window.localStorage.setItem(THEME_KEY, "light");
    expect(readTheme()).toBe("light");
    stubMedia(true);
    window.localStorage.setItem(THEME_KEY, "dark");
    expect(readTheme()).toBe("dark");
  });

  it("treats a corrupt stored value as no light preference", () => {
    stubMedia(true); // even an OS asking for light does not smuggle it in
    window.localStorage.setItem(THEME_KEY, "sepia");
    expect(readTheme()).toBe("dark");
    // ...and painting does not launder it either: the key is still the key.
    applyTheme("light");
    clearAttribute();
    expect(readTheme()).toBe("dark");
  });

  it("toggles the attribute, the hook and the stored value", () => {
    stubMedia(false);
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("dark");

    act(() => result.current.toggle());
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(window.localStorage.getItem(THEME_KEY)).toBe("light");
    expect(result.current.theme).toBe("light");

    // A second flip, not just the first: a toggle that only works once is the
    // bug a one-way test cannot see.
    act(() => result.current.toggle());
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(result.current.theme).toBe("dark");
  });

  it("sets an explicit theme without going through the toggle", () => {
    setTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(window.localStorage.getItem(THEME_KEY)).toBe("light");
    expect(readTheme()).toBe("light");
  });

  it("still returns a theme when reading storage throws", () => {
    denyStorageAccess();
    stubMedia(true);
    expect(readTheme()).toBe("light");
    stubMedia(false);
    expect(readTheme()).toBe("dark");
  });

  it("still paints when writing storage throws", () => {
    vi.spyOn(Object.getPrototypeOf(window.localStorage) as Storage, "setItem").mockImplementation(
      () => {
        throw new Error("QuotaExceededError: storage is full");
      },
    );
    expect(() => setTheme("light")).not.toThrow();
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(readTheme()).toBe("light");
  });

  it("still flips back and forth when storage is entirely unavailable", () => {
    // The private-mode path: nothing can be read or written, so the painted
    // attribute is the only memory the page has. It has to be enough.
    denyStorageAccess();
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("dark");

    act(() => result.current.toggle());
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(result.current.theme).toBe("light");

    act(() => result.current.toggle());
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(result.current.theme).toBe("dark");
  });

  it("applies a stored choice while the module is evaluated, before the first render", async () => {
    window.localStorage.setItem(THEME_KEY, "light");
    clearAttribute();
    vi.resetModules();
    const fresh = await import("./theme");
    // By the time the module finishes evaluating, <html> already carries the
    // choice: that is ahead of React's first render, so no frame can flash dark.
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(fresh.readTheme()).toBe("light");
  });

  it("does not turn the environment default into a stored choice", async () => {
    stubMedia(true);
    window.localStorage.clear();
    clearAttribute();
    vi.resetModules();
    await import("./theme");
    expect(document.documentElement.dataset.theme).toBe("light");
    // Persisting the OS default would freeze it: the reader's system could
    // switch to dark and this page would never follow.
    expect(window.localStorage.getItem(THEME_KEY)).toBeNull();
  });
});
