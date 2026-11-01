/**
 * The honesty line, in one place so both pages cannot drift apart.
 *
 * It is not a disclaimer bolted on: there is no authentication in this product,
 * so there is no account to attach a list to and no server that could answer for
 * one. `localStorage` is the store. The sentence says exactly that, and the word
 * "sync" means the server kind — a second tab reading the same key is the same
 * browser, not an account.
 */

export const LOCAL_ONLY_SENTENCE =
  "Saved in this browser only — there is no account and no sync.";

export function LocalNotice() {
  return (
    <p data-testid="local-only-notice" className="mt-0.5 text-data text-fg-muted">
      {LOCAL_ONLY_SENTENCE}
    </p>
  );
}
