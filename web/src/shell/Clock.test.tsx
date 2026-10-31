import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Clock, formatClock, msToNextMinute } from "./Clock";

/**
 * The clock is 12-hour and updates once a minute.
 *
 * The second half is the adversarial one: a clock that re-rendered the header
 * every second would be a component scheduling 3 600 state updates an hour to
 * change its text 60 times. The test proves one pending timer and a text that
 * does not move between minute boundaries.
 */

afterEach(() => {
  vi.useRealTimers();
});

describe("formatClock", () => {
  it("prints 12-hour time with an AM/PM suffix", () => {
    expect(formatClock(new Date(2026, 9, 31, 15, 25))).toBe("3:25 PM");
    expect(formatClock(new Date(2026, 9, 31, 9, 5))).toBe("9:05 AM");
    expect(formatClock(new Date(2026, 9, 31, 0, 5))).toBe("12:05 AM");
    expect(formatClock(new Date(2026, 9, 31, 12, 0))).toBe("12:00 PM");
  });

  it("waits exactly the remainder of the current minute", () => {
    expect(msToNextMinute(0)).toBe(60_020);
    expect(msToNextMinute(59_000)).toBe(1_020);
  });
});

describe("Clock", () => {
  it("updates on the minute boundary, not every second", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 9, 31, 15, 24, 30));

    render(<Clock />);
    const clock = screen.getByTestId("clock");
    expect(clock.textContent).toBe("3:24 PM");
    expect(clock.getAttribute("aria-live")).toBe("off");

    // Half a minute later nothing has changed …
    act(() => {
      vi.advanceTimersByTime(20_000);
    });
    expect(clock.textContent).toBe("3:24 PM");
    // … and there is still exactly one timer, not a one-second interval.
    expect(vi.getTimerCount()).toBe(1);

    // … then the boundary arrives.
    act(() => {
      vi.advanceTimersByTime(10_100);
    });
    expect(clock.textContent).toBe("3:25 PM");
    expect(vi.getTimerCount()).toBe(1);
  });

  it("does not expose a live region", () => {
    render(<Clock />);
    expect(screen.getByTestId("clock").getAttribute("role")).toBeNull();
  });
});
