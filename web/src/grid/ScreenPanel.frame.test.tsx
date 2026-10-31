import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ViewDefinition } from "@/data/screens";

import { ScreenPanel } from "./ScreenPanel";

/**
 * F8 — a view switch committed one frame with the previous view's rows under
 * the new view's headers. The auditor proved it at hook level: a render exists
 * with `propView=value`, `stateView=today`, `rows=6`, `status=ready`.
 *
 * Proving it needs a frame log, and the mock below keeps one: `rerender` flushes
 * the passive effect, but the commit *before* that effect still runs the
 * component, which is precisely the frame the reader could see.
 */

const { frames } = vi.hoisted(() => ({
  frames: [] as Array<{ view: string; rows: number; status: string }>,
}));

vi.mock("./DataTable", () => ({
  DataTable: (props: { view: { name: string }; rows: unknown[]; status: string }) => {
    frames.push({ view: props.view.name, rows: props.rows.length, status: props.status });
    return (
      <div
        data-testid="frame-probe"
        data-view={props.view.name}
        data-rows={props.rows.length}
        data-status={props.status}
      />
    );
  },
}));

const TODAY: ViewDefinition = {
  name: "today",
  label: "Today",
  job: "What is moving right now?",
  columns: ["price", "change"],
};

const VALUE: ViewDefinition = {
  name: "value",
  label: "Value",
  job: "Is it cheap?",
  columns: ["market_cap", "pe"],
};

function page(view: string, count = 2): Response {
  return new Response(
    JSON.stringify({
      as_of: "2026-10-31T01:28:01Z",
      limit: 100,
      total: 5600,
      universe_total: 5600,
      returned: count,
      columns: TODAY.columns,
      rows: Array.from({ length: count }, (_, index) => ({
        symbol: `${view}${index}`,
        price: 1,
        change: 0,
        market_cap: 1,
        pe: 1,
      })),
      budget: { truncated: false, dropped_fields: [] },
    }),
    { status: 200, headers: { "content-type": "application/json", etag: `W/"${view}"` } },
  );
}

const PANEL_PROPS = {
  selectedSymbol: null,
  onActivate: vi.fn(),
  onClear: vi.fn(),
  containerWidth: 1280,
};

afterEach(() => {
  frames.length = 0;
  vi.unstubAllGlobals();
});

describe("a view switch (F8)", () => {
  it("never commits the old rows under the new headers", async () => {
    let releaseValue: (value: Response) => void = () => {};
    const pending = new Promise<Response>((resolve) => {
      releaseValue = resolve;
    });

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(page("today"))
      .mockImplementationOnce(() => pending);
    vi.stubGlobal("fetch", fetchMock);

    const { rerender } = render(<ScreenPanel view={TODAY} {...PANEL_PROPS} />);
    await waitFor(() =>
      expect(screen.getByTestId("frame-probe").getAttribute("data-rows")).toBe("2"),
    );

    frames.length = 0;
    rerender(<ScreenPanel view={VALUE} {...PANEL_PROPS} />);

    // Every frame committed while the switch is in flight.
    expect(frames.filter((frame) => frame.view === "value" && frame.rows > 0)).toEqual([]);
    expect(frames.some((frame) => frame.view === "value")).toBe(true);
    expect(frames.every((frame) => frame.status !== "ready" || frame.rows === 0)).toBe(true);

    releaseValue(page("value"));
    await waitFor(() =>
      expect(screen.getByTestId("frame-probe").getAttribute("data-rows")).toBe("2"),
    );
  });

  it("keeps the rows on screen when only the page changes", async () => {
    // Paging inside one view is a revalidation, not a switch: the rows stay, so
    // the fix for F8 must not turn every page turn into an empty panel.
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(page("today"))
      .mockImplementationOnce(() => new Promise<Response>(() => {}));
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<ScreenPanel view={TODAY} {...PANEL_PROPS} />);
    await waitFor(() =>
      expect(screen.getByTestId("frame-probe").getAttribute("data-rows")).toBe("2"),
    );

    frames.length = 0;
    await user.click(screen.getByRole("button", { name: "Next" }));

    expect(frames.length).toBeGreaterThan(0);
    expect(frames.every((frame) => frame.rows === 2)).toBe(true);
  });
});
