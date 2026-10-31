/**
 * The clock in the nav's second line.
 *
 * Twelve-hour, no seconds, one digit for the hour: "3:25 PM". It is a `<time>`
 * element with a machine-readable `dateTime`, and it is **not** a live region —
 * a clock that announced itself every minute would be a screen reader reading
 * the time over whatever the reader was doing, so `aria-live` is explicitly
 * off.
 *
 * The update is aligned to the minute boundary: the first timeout waits the
 * remainder of the current minute, then each one waits a full minute. A
 * one-second interval would re-render the header 60× for one change, and a
 * component that keeps state per second is a component that keeps the whole
 * nav in the render path every second. Nothing here reads `setInterval`.
 */

import * as React from "react";

const FORMAT = new Intl.DateTimeFormat("en-US", {
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
});

/** "3:25 PM" for a date, in the reader's own timezone. */
export function formatClock(date: Date): string {
  // ICU emits a narrow no-break space before AM/PM in some versions; the
  // product copy is a plain space, and tests compare the string.
  return FORMAT.format(date).replace(/[\u202f\u00a0]/g, " ");
}

/** Milliseconds until the next whole minute, plus a hair so the tick lands after it. */
export function msToNextMinute(nowMs: number): number {
  return 60_000 - (nowMs % 60_000) + 20;
}

export function Clock() {
  const [now, setNow] = React.useState(() => new Date());

  React.useEffect(() => {
    let timer = 0;
    const schedule = () => {
      timer = window.setTimeout(() => {
        setNow(new Date());
        schedule();
      }, msToNextMinute(Date.now()));
    };
    schedule();
    return () => window.clearTimeout(timer);
  }, []);

  return (
    <time
      data-testid="clock"
      dateTime={now.toISOString()}
      // Off on purpose: a minute-by-minute announcement is noise, not status.
      aria-live="off"
      className="nums tabular-nums text-[11px] text-fg-subtle"
    >
      {formatClock(now)}
    </time>
  );
}
