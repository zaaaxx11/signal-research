/**
 * The small state machine behind "something is loading, and it is not the
 * first load".
 *
 * Three decisions live here, and they are all about *when* rather than *what*.
 *
 * **The delay.** A request that answers in 40 ms must not paint a bar: the
 * bar would appear and vanish inside one glance and read as a glitch, which is
 * worse than silence (Nielsen's 0.1 s boundary is the threshold below which a
 * response is indistinguishable from no wait at all). `PENDING_FLASH_DELAY_MS`
 * is 120 ms — 100 ms of that is the boundary, the remaining 20 ms is the
 * commit-plus-paint that happens after a response is decoded, so an answer
 * that arrives at 100 ms still never reaches the screen. Above 120 ms the wait
 * is real and the reader is told.
 *
 * The delay gates only the *visual* layer. Disabling the paging controls is
 * not delayed: a second click must not queue a second request, and that is
 * correctness, not decoration.
 *
 * **The motion.** A `prefers-reduced-motion` reader gets the same bar, label
 * and dimming with the sweep standing still. The animation is a hint that work
 * is happening; the words are what actually say so.
 *
 * **The words.** `pendingLabel` names the range being fetched, because
 * "Loading…" is a shrug and "Loading 101–200…" is an answer to the question
 * the reader is asking. A re-ask of the page already on screen is
 * "Refreshing…" — same range, so naming it would be noise.
 */

import * as React from "react";

/**
 * How long a request may be in flight before the pending chrome is allowed to
 * paint. See the note above: 100 ms is the perceptual boundary, 120 ms leaves
 * room for the commit after the answer is decoded.
 */
export const PENDING_FLASH_DELAY_MS = 120;

/**
 * `active`, but only after it has stayed true for `delay` ms.
 *
 * Flips back to false immediately (with the timer cancelled), so a request
 * that settles inside the window never paints anything at all — the timer is
 * cleared, not merely ignored.
 */
export function useDelayedFlag(active: boolean, delay: number = PENDING_FLASH_DELAY_MS): boolean {
  const [shown, setShown] = React.useState(false);

  React.useEffect(() => {
    if (!active) {
      setShown(false);
      return;
    }
    const timer = setTimeout(() => setShown(true), delay);
    return () => clearTimeout(timer);
  }, [active, delay]);

  return shown;
}

const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

/** jsdom, an old browser and a locked-down webview all lack `matchMedia`. */
function readReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  try {
    return window.matchMedia(REDUCED_MOTION_QUERY).matches;
  } catch {
    return false;
  }
}

/**
 * The reader's motion preference, live.
 *
 * The stylesheet also carries a `prefers-reduced-motion` block (the single
 * source of truth for *paint*), but the component needs to know too: the class
 * that starts the sweep is not rendered at all when the answer is "reduced",
 * so what a test can see matches what the browser does.
 */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = React.useState(readReducedMotion);

  React.useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    let list: MediaQueryList;
    try {
      list = window.matchMedia(REDUCED_MOTION_QUERY);
    } catch {
      return;
    }
    const update = () => setReduced(list.matches);
    update();
    if (typeof list.addEventListener !== "function") return;
    list.addEventListener("change", update);
    return () => list.removeEventListener("change", update);
  }, []);

  return reduced;
}

export interface PendingLabelInput {
  /** The rows on screen are the answer to the query being repeated. */
  refreshing: boolean;
  pageIndex: number;
  limit: number;
  /** The last known total, for clamping the last page; null before any answer. */
  total: number | null;
}

/** "Loading 101–200…" or "Refreshing…" — the one sentence the panel says. */
export function pendingLabel({ refreshing, pageIndex, limit, total }: PendingLabelInput): string {
  if (refreshing) return "Refreshing…";
  const span = Math.max(1, limit);
  const first = pageIndex * span + 1;
  const last =
    total !== null && total > 0
      ? Math.min(first + span - 1, Math.max(first, total))
      : first + span - 1;
  return `Loading ${first}–${last}…`;
}
