/**
 * One-time migration: Ztockz → Signal Research localStorage/sessionStorage keys.
 *
 * Runs at import time — before any store reads — so the first render already
 * sees data under the new keys.  Idempotent: a key is copied only when the old
 * key exists AND the new key does not.  Old keys are never deleted, so a
 * rollback or audit always has the original bytes.
 *
 * The migration is deliberately a module side-effect, not a React hook: stores
 * read from localStorage in their module scope (createLocalStore's initial
 * snapshot), and a hook would fire after those reads.
 */

/** localStorage migrations: old key → new key. */
const LOCAL_MIGRATIONS: Readonly<Record<string, string>> = {
  "ztockz.theme.v1": "signal-research.theme.v1",
  "ztockz.watchlist.v1": "signal-research.watchlist.v1",
  "ztockz.portfolio.v1": "signal-research.portfolio.v1",
  "ztockz.screens.v1": "signal-research.screens.v1",
  "ztockz.columns.v1": "signal-research.columns.v1",
  "ztockz.alerts.v1": "signal-research.alerts.v1",
};

/** sessionStorage migrations: old key → new key. */
const SESSION_MIGRATIONS: Readonly<Record<string, string>> = {
  "ztockz:query-cache": "signal-research:query-cache",
};

function migrateStore(
  store: Storage | null,
  migrations: Readonly<Record<string, string>>,
): void {
  if (!store) return;
  for (const [oldKey, newKey] of Object.entries(migrations)) {
    try {
      if (store.getItem(newKey) !== null) continue; // already migrated or user has new data
      const value = store.getItem(oldKey);
      if (value === null) continue; // nothing to migrate
      store.setItem(newKey, value);
    } catch {
      // Storage denied (private mode, quota full) — skip this key, the app
      // already handles missing storage gracefully.
    }
  }
}

/** Run once at import time, before any store reads its key. */
function run(): void {
  if (typeof window === "undefined") return;
  migrateStore(window.localStorage, LOCAL_MIGRATIONS);
  migrateStore(window.sessionStorage, SESSION_MIGRATIONS);
}

run();