import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LIVE_BREADTH } from "./__fixtures__/breadth";
import { BreadthStrip, BREADTH_RESERVED_HEIGHT } from "./BreadthStrip";

/**
 * The strip end-to-end over a mocked `fetch`: the states a user can land in
 * (loading, loaded, stale, inconsistent, scanner down, unreadable) and the two
 * numbers the old client-side breadth got wrong — the universe and the
 * 52-week denominators.
 */

function answer(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** A fetch that answers every call with a fresh Response (a body reads once). */
function serves(handler: () => Response) {
  const mock = vi.fn().mockImplementation(() => Promise.resolve(handler()));
  vi.stubGlobal("fetch", mock);
  return mock;
}

/** The rendered integer behind a `data-testid`, commas removed. */
function rendered(testId: string): number {
  const text = screen.getByTestId(testId).textContent ?? "";
  const value = Number(text.replace(/,/g, ""));
  expect(Number.isFinite(value)).toBe(true);
  return value;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("BreadthStrip", () => {
  it("asks the market-wide endpoint, not the visible rows", async () => {
    const fetchMock = serves(() => answer(LIVE_BREADTH));

    render(<BreadthStrip />);
    await waitFor(() => expect(screen.getByTestId("breadth-advancing")).toBeTruthy());

    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe("/market/breadth?region=us&asset_class=stock");
    expect(init.method ?? "GET").toBe("GET");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows a skeleton while the read is in flight", () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => new Promise(() => {})));

    render(<BreadthStrip />);

    expect(screen.getByTestId("breadth-skeleton")).toBeTruthy();
    expect(screen.getByTestId("breadth-strip").getAttribute("data-state")).toBe("loading");
    expect(screen.queryByTestId("breadth-advancing")).toBeNull();
    expect(screen.queryByTestId("sector-grid")).toBeNull();
  });

  it("prints the endpoint's counts, and the three buckets add up to the universe", async () => {
    serves(() => answer(LIVE_BREADTH));

    render(<BreadthStrip />);
    await waitFor(() => expect(screen.getByTestId("breadth-advancing")).toBeTruthy());

    expect(rendered("breadth-advancing")).toBe(1557);
    expect(rendered("breadth-declining")).toBe(3932);
    expect(rendered("breadth-unchanged")).toBe(189);
    expect(rendered("breadth-universe")).toBe(5678);

    // The measured goal: advancing + declining + unchanged === universe_total,
    // read off the DOM, not off the fixture.
    expect(
      rendered("breadth-advancing") + rendered("breadth-declining") + rendered("breadth-unchanged"),
    ).toBe(rendered("breadth-universe"));

    expect(screen.getByTestId("breadth-strip").getAttribute("data-state")).toBe("ready");
    expect(screen.queryByTestId("breadth-sum-warning")).toBeNull();
  });

  it("states the 52-week denominators instead of bare counts", async () => {
    serves(() => answer(LIVE_BREADTH));

    render(<BreadthStrip />);
    await waitFor(() => expect(screen.getByTestId("breadth-new-highs")).toBeTruthy());

    expect(screen.getByTestId("breadth-new-highs").textContent).toBe("45 / 5,675 reported");
    expect(screen.getByTestId("breadth-new-lows").textContent).toBe("54 / 5,675 reported");
    // The denominator is explained, not decorative.
    expect(screen.getByTestId("breadth-new-highs").getAttribute("title")).toContain("52w");
  });

  it("dates the snapshot from as_of", async () => {
    serves(() => answer(LIVE_BREADTH));

    render(<BreadthStrip />);
    await waitFor(() => expect(screen.getByTestId("breadth-as-of")).toBeTruthy());

    const stamp = screen.getByTestId("breadth-as-of");
    expect(stamp.textContent).toContain("09:25:02Z");
    expect(stamp.getAttribute("datetime")).toBe("2026-10-31T09:25:02+00:00");
  });

  it("gives the bar an accessible name that repeats both counts", async () => {
    serves(() => answer(LIVE_BREADTH));

    render(<BreadthStrip />);
    await waitFor(() => expect(screen.getByTestId("breadth-bar")).toBeTruthy());

    const bar = screen.getByTestId("breadth-bar");
    expect(bar.getAttribute("role")).toBe("img");
    const label = bar.getAttribute("aria-label") ?? "";
    expect(label).toContain("Advancing 1,557");
    expect(label).toContain("declining 3,932");
    expect(label).toContain("189 unchanged");

    // The segments are sized from the counts the endpoint returned:
    // 1557 / (1557 + 3932) and 3932 / (1557 + 3932).
    const up = screen.getByTestId("breadth-bar-up");
    const down = screen.getByTestId("breadth-bar-down");
    expect(Number.parseFloat(up.style.width)).toBeCloseTo(28.366, 2);
    expect(Number.parseFloat(down.style.width)).toBeCloseTo(71.634, 2);
    expect(Number.parseFloat(up.style.width) + Number.parseFloat(down.style.width)).toBeCloseTo(100, 6);
  });

  it("renders every sector the endpoint returned", async () => {
    serves(() => answer(LIVE_BREADTH));

    render(<BreadthStrip />);
    await waitFor(() => expect(screen.getByTestId("sector-grid")).toBeTruthy());

    expect(screen.getAllByTestId("sector-cell")).toHaveLength(21);
  });

  it("says the scanner is unavailable on a 503, and prints no counts", async () => {
    serves(() =>
      answer(
        {
          error: "scanner_unavailable",
          errorMessage:
            "the scanner could not answer the breadth request and no earlier scan is cached; the same failure was seen less than 5 s ago, so this request was refused without a new scan",
        },
        503,
      ),
    );

    render(<BreadthStrip />);
    await waitFor(() => expect(screen.getByTestId("breadth-unavailable")).toBeTruthy());

    expect(screen.getByText(/scanner unavailable/i)).toBeTruthy();
    // A 503 is not a flat market: none of the count slots exist at all.
    expect(screen.queryByTestId("breadth-advancing")).toBeNull();
    expect(screen.queryByTestId("breadth-declining")).toBeNull();
    expect(screen.queryByTestId("breadth-unchanged")).toBeNull();
    expect(screen.queryByTestId("breadth-universe")).toBeNull();
    expect(screen.queryByTestId("breadth-bar")).toBeNull();
    expect(screen.queryByText("5,678")).toBeNull();
    expect(screen.queryByTestId("sector-grid")).toBeNull();
    expect(screen.getByTestId("breadth-unavailable").getAttribute("data-status")).toBe("503");
    expect(screen.getByTestId("breadth-strip").getAttribute("data-state")).toBe("unavailable");
  });

  it("retries after a 503 and shows the counts when the scanner answers", async () => {
    let calls = 0;
    serves(() => {
      calls += 1;
      return calls === 1
        ? answer({ error: "scanner_unavailable", errorMessage: "no scan cached" }, 503)
        : answer(LIVE_BREADTH);
    });

    const user = userEvent.setup();
    render(<BreadthStrip />);
    await waitFor(() => expect(screen.getByTestId("breadth-unavailable")).toBeTruthy());

    await user.click(screen.getByRole("button", { name: /retry/i }));
    await waitFor(() => expect(screen.getByTestId("breadth-advancing")).toBeTruthy());

    expect(calls).toBe(2);
    expect(rendered("breadth-universe")).toBe(5678);
  });

  it("marks a snapshot the server called stale", async () => {
    serves(() => answer({ ...LIVE_BREADTH, stale: true }));

    render(<BreadthStrip />);
    await waitFor(() => expect(screen.getByTestId("breadth-stale")).toBeTruthy());

    expect(screen.getByTestId("breadth-stale").textContent).toContain("Stale");
    expect(screen.getByTestId("breadth-strip").getAttribute("data-stale")).toBe("true");
    // Stale is a label, not a reason to hide the data.
    expect(rendered("breadth-advancing")).toBe(1557);
  });

  it("surfaces counts that do not add up as a warning, not as fact", async () => {
    serves(() => answer({ ...LIVE_BREADTH, unchanged: 182 }));

    render(<BreadthStrip />);
    await waitFor(() => expect(screen.getByTestId("breadth-sum-warning")).toBeTruthy());

    const warning = screen.getByTestId("breadth-sum-warning");
    expect(warning.textContent).toContain("5,671");
    expect(warning.textContent).toContain("5,678");
    expect(warning.textContent).toContain("-7");
    expect(warning.textContent).toMatch(/inconsistent/i);

    // The numbers still render — with the warning attached, not instead of one.
    expect(rendered("breadth-advancing")).toBe(1557);
    expect(rendered("breadth-universe")).toBe(5678);
  });

  it("survives a market with no advancers and no decliners", async () => {
    serves(() =>
      answer({
        ...LIVE_BREADTH,
        advancing: 0,
        declining: 0,
        unchanged: 5678,
        unchanged_reported: 5675,
        unchanged_missing: 3,
        new_highs: 0,
        new_lows: 0,
        sectors: [],
      }),
    );

    render(<BreadthStrip />);
    await waitFor(() => expect(screen.getByTestId("breadth-advancing")).toBeTruthy());

    // No `NaN%`, no zero-width segments: the flat track is the honest drawing.
    expect(screen.queryByTestId("breadth-bar-up")).toBeNull();
    expect(screen.queryByTestId("breadth-bar-down")).toBeNull();
    expect(screen.getByTestId("breadth-bar-flat")).toBeTruthy();
    expect(screen.getByTestId("breadth-bar").getAttribute("aria-label")).toContain(
      "No advancing or declining names",
    );
    expect(document.body.textContent).not.toContain("NaN");

    expect(rendered("breadth-advancing") + rendered("breadth-declining") + rendered("breadth-unchanged")).toBe(
      rendered("breadth-universe"),
    );
    // An empty sector list is an empty state, not a crash.
    expect(screen.getByTestId("sector-empty")).toBeTruthy();
  });

  it("refuses an unreadable payload rather than printing zeros", async () => {
    serves(() => answer({ universe_total: 5678 }));

    render(<BreadthStrip />);
    await waitFor(() => expect(screen.getByTestId("breadth-failed")).toBeTruthy());

    expect(screen.getByText(/could not be read/i)).toBeTruthy();
    expect(screen.getByText(/Unreadable breadth snapshot/i)).toBeTruthy();
    expect(screen.queryByTestId("breadth-advancing")).toBeNull();
    expect(screen.getByTestId("breadth-strip").getAttribute("data-state")).toBe("failed");
  });
});

/**
 * F8 — the skeleton was 196 px and the resolved strip 223 px, so a slow
 * `/market/breadth` moved the grid down 27 px when it landed (CLS 0.0125 in
 * Chromium). jsdom has no layout, so the promise is asserted as the contract
 * that produces it: the section carries the settled height as a floor in every
 * state, so no state can be shorter than the one that follows it.
 */
describe("the strip's reserved height (F8)", () => {
  it("floors the skeleton and the resolved strip at the same height", async () => {
    expect(BREADTH_RESERVED_HEIGHT).toBe(223);

    let settle!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => {
      settle = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => pending),
    );

    render(<BreadthStrip />);

    const strip = screen.getByTestId("breadth-strip");
    expect(strip.getAttribute("data-state")).toBe("loading");
    expect(screen.getByTestId("breadth-skeleton")).toBeTruthy();
    const reserved = strip.style.minHeight;
    expect(reserved).toBe(`${BREADTH_RESERVED_HEIGHT}px`);

    settle(answer(LIVE_BREADTH));
    await waitFor(() => expect(strip.getAttribute("data-state")).toBe("ready"));
    expect(screen.getByTestId("breadth-advancing")).toBeTruthy();
    // Same floor: the grid above/below it cannot move by a pixel.
    expect(strip.style.minHeight).toBe(reserved);
  });

  it("keeps the floor when the scanner is unavailable", async () => {
    serves(() => answer({ errorMessage: "scanner down" }, 503));

    render(<BreadthStrip />);
    await waitFor(() => expect(screen.getByTestId("breadth-unavailable")).toBeTruthy());
    expect(screen.getByTestId("breadth-strip").style.minHeight).toBe(
      `${BREADTH_RESERVED_HEIGHT}px`,
    );
  });
});
