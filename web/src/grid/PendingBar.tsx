/**
 * The 2 px rose line on the panel's top edge — the panel's only moving part.
 *
 * Why absolute: a page turn takes a second; the table must look like the same
 * table with a light on it, not like a screen that was swapped out. The track
 * is positioned over the top edge (`inset-x-0 top-0 h-0.5`), so it contributes
 * nothing to layout — not one row moves when it appears or goes away.
 *
 * Why one segment rather than a progress fill: the client cannot know how much
 * of the answer has arrived, so a fill would be a lie. A segment travelling
 * across a track is the honest shape of "unknown, still working".
 *
 * Why the keyframes are here: `src/styles/**` is the shared sheet and is owned
 * by the token gate; a keyframe pair that exactly one component uses is better
 * carried by that component. No colour literal is named — the paint is
 * `--accent`, the token the theme audit already pins.
 */

import { cn } from "@/lib/utils";

/** The sweep: enters from the left, leaves to the right, forever. */
const SWEEP_CSS = `
@keyframes ztz-pending-travel {
  from { transform: translateX(-100%); }
  to   { transform: translateX(222%); }
}
.ztz-pending-sweep {
  animation: ztz-pending-travel 1.2s ease-in-out infinite;
}
@media (prefers-reduced-motion: reduce) {
  .ztz-pending-sweep {
    animation: none;
    transform: none;
    width: 100%;
  }
}
`;

export interface PendingBarProps {
  /** True when the reader has asked for motion to be reduced. */
  reducedMotion?: boolean;
}

/**
 * The bar is decoration: the sentence in the footer is the announcement, and
 * it is the only live region, so this is hidden from assistive tech rather
 * than given a `progressbar` role it could not report a value for.
 */
export function PendingBar({ reducedMotion = false }: PendingBarProps) {
  return (
    <div
      data-testid="fetch-bar"
      data-motion={reducedMotion ? "reduced" : "full"}
      aria-hidden="true"
      // Above the sticky header (z 20–40) and clipped by the panel's own
      // `overflow-hidden`, so the ends stay inside the rounded corners.
      className="pointer-events-none absolute inset-x-0 top-0 z-50 h-0.5 overflow-hidden"
    >
      <style>{SWEEP_CSS}</style>
      <div
        data-testid="fetch-bar-sweep"
        className={cn(
          "h-full bg-[var(--accent)]",
          reducedMotion ? "w-full" : "ztz-pending-sweep w-[45%]",
        )}
      />
    </div>
  );
}
