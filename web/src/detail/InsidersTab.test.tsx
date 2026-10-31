import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { InsidersTab } from "./InsidersTab";
import { parseInsider } from "./useDetail";
import type { InsiderData } from "./useDetail";
import {
  emptyInsiderPayload,
  insiderPayload,
  resource,
} from "./__fixtures__/detail";

function data(payload: Record<string, unknown>) {
  const parsed = parseInsider(payload);
  if (!parsed) throw new Error("fixture did not parse");
  return parsed;
}

describe("Insiders tab", () => {
  it("shows its own skeleton while the request is pending", () => {
    render(<InsidersTab symbol="AAPL" state={resource("loading")} onRetry={vi.fn()} />);
    expect(screen.getByTestId("insiders-loading").getAttribute("aria-busy")).toBe("true");
  });

  it("renders Form 4 transactions with date, insider, type, shares and value", () => {
    render(
      <InsidersTab
        symbol="AAPL"
        state={resource("ready", data(insiderPayload()))}
        onRetry={vi.fn()}
      />,
    );

    const rows = screen.getAllByTestId("insider-row");
    expect(rows).toHaveLength(2);

    const first = rows[0] as HTMLElement;
    expect(within(first).getByText("2026-10-27")).toBeTruthy();
    expect(within(first).getByText("Newstead Jennifer")).toBeTruthy();
    expect(within(first).getByText("Sale")).toBeTruthy();
    expect(within(first).getByText("2,399")).toBeTruthy();
    expect(within(first).getByText("$823.72K")).toBeTruthy();

    const second = rows[1] as HTMLElement;
    expect(within(second).getByText("Option")).toBeTruthy();
    expect(within(second).getByText("151")).toBeTruthy();
    // The filing reported no price: the value is the placeholder, not $0.00.
    expect(within(second).getByText("—")).toBeTruthy();
  });

  it("says there are no Form 4 filings when the list is empty", () => {
    const parsed = parseInsider(emptyInsiderPayload());
    expect(parsed).toBeNull();

    render(<InsidersTab symbol="ASML" state={resource<InsiderData>("empty")} onRetry={vi.fn()} />);
    const empty = screen.getByTestId("insiders-empty");
    expect(empty.textContent).toContain("There are no Form 4 filings for ASML.");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows its own error and retries it", async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(
      <InsidersTab
        symbol="AAPL"
        state={resource<InsiderData>("error", null, "HTTP 500")}
        onRetry={onRetry}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("Could not load insider filings.");
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
