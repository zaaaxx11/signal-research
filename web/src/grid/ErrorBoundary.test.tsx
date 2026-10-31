import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ViewDefinition } from "@/data/screens";

import { TODAY_COLUMNS } from "./__fixtures__/today100";
import { DataTable, type DataTableProps } from "./DataTable";
import { ErrorBoundary } from "./ErrorBoundary";
import { ScreenPanel } from "./ScreenPanel";

/**
 * F2 — the solver's throw inside `useMemo` used to reach the React root and
 * unmount the whole shell: `#root` 0 children, no message, no way back.
 *
 * Two separate promises are tested here:
 *
 * * a render error *inside the panel* is caught by the boundary, says what
 *   happened, and offers a way out — it never reaches the root;
 * * a real API error is **not** caught by it: the data hook handles those and
 *   the panel keeps rendering the API's own sentence. A boundary that hid a 422
 *   behind "something went wrong" would be a different bug.
 */

const TODAY_VIEW: ViewDefinition = {
  name: "today",
  label: "Today",
  job: "What is moving right now?",
  columns: [...TODAY_COLUMNS],
};

const TABLE_PROPS: DataTableProps = {
  view: TODAY_VIEW,
  rows: [],
  status: "ready",
  error: null,
  revalidated: false,
  pageIndex: 0,
  pageSize: 100,
  rowCount: 0,
  onRetry: vi.fn(),
  containerWidth: 1280,
};

/** A child that throws while rendering — the shape of the F2 failure. */
let explode = true;
function Faulty() {
  if (explode) throw new Error("widths sum to 112, container is 110");
  return <p>drawn again</p>;
}

afterEach(() => {
  explode = true;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ErrorBoundary", () => {
  it("catches a render error and says what happened instead of blanking the page", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary>
        <Faulty />
      </ErrorBoundary>,
    );

    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("widths sum to 112");
    expect(alert.textContent).toContain("Try again");
    // The rest of the shell is still mounted: only the panel was replaced.
    expect(screen.queryByText("drawn again")).toBeNull();
  });

  it("recovers when the fault is gone and the reader asks again", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const user = userEvent.setup();
    render(
      <ErrorBoundary>
        <Faulty />
      </ErrorBoundary>,
    );

    explode = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));

    await waitFor(() => expect(screen.getByText("drawn again")).toBeTruthy());
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("clears a failure when the view changes", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { rerender } = render(
      <ErrorBoundary resetKey="today">
        <Faulty />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toBeTruthy();

    explode = false;
    rerender(
      <ErrorBoundary resetKey="value">
        <Faulty />
      </ErrorBoundary>,
    );
    expect(screen.getByText("drawn again")).toBeTruthy();
  });

  it("lets the 105 px table render instead of taking the SPA down with it", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary>
        <DataTable {...TABLE_PROPS} containerWidth={105} />
      </ErrorBoundary>,
    );

    // F2: this used to throw out of `solveView` inside render.
    expect(screen.getByTestId("grid-table")).toBeTruthy();
    expect(screen.queryByTestId("grid-crash")).toBeNull();
  });

  /** Adversarial (b): a real API error must reach the reader, not the boundary. */
  it("does not hide an API error behind the fallback", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const message = "unknown sort 'bogus'; expected one of market_cap, turnover, gainers";
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: "invalid_selection", errorMessage: message }), {
            status: 422,
            headers: { "content-type": "application/json" },
          }),
      ),
    );

    render(
      <ErrorBoundary>
        <ScreenPanel
          view={TODAY_VIEW}
          selectedSymbol={null}
          onActivate={vi.fn()}
          onClear={vi.fn()}
          containerWidth={1280}
        />
      </ErrorBoundary>,
    );

    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("unknown sort 'bogus'"));
    expect(screen.getByRole("alert").textContent).toContain("422");
    expect(screen.queryByTestId("grid-crash")).toBeNull();
    expect(console.error).not.toHaveBeenCalled();
  });
});
