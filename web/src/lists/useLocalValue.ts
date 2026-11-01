/**
 * A store read as React state, without a context provider.
 *
 * `useSyncExternalStore` is the whole subscription: same-document writes notify
 * through the store's listener set, another tab's write through its `storage`
 * event, and `read()` returns the cached reference until one of those happens,
 * so React is not handed a new array on every render.
 */

import * as React from "react";

import type { LocalStore } from "./storage";

export function useLocalValue<T>(store: LocalStore<T>): T {
  return React.useSyncExternalStore(store.subscribe, store.read, store.read);
}
