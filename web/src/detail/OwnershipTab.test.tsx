import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { OwnershipTab } from "./OwnershipTab";
import { parseOwnership } from "./useDetail";
import type { OwnershipData } from "./useDetail";
import { resource, selectPayload } from "./__fixtures__/detail";

function data() {
  const parsed = parseOwnership(selectPayload(), "AAPL");
  if (!parsed) throw new Error("fixture did not parse");
  return parsed;
}

describe("Ownership tab", () => {
  it("shows its own skeleton while the request is pending", () => {
    render(<OwnershipTab symbol="AAPL" state={resource("loading")} onRetry={vi.fn()} />);
    expect(screen.getByTestId("ownership-loading").getAttribute("aria-busy")).toBe("true");
  });

  it("renders shares, float, insider, institutional and short cells", () => {
    render(<OwnershipTab symbol="AAPL" state={resource("ready", data())} onRetry={vi.fn()} />);

    const loaded = screen.getByTestId("ownership-loaded");
    expect(loaded.querySelectorAll('[data-testid^="ownership-"]')).toHaveLength(6);

    expect(screen.getByTestId("ownership-shares_outstanding").textContent).toContain("14.59B");
    expect(screen.getByTestId("ownership-float_shares").textContent).toContain("14.58B");
    expect(screen.getByTestId("ownership-insider_own").textContent).toContain("1.65");
    expect(screen.getByTestId("ownership-inst_own").textContent).toContain("66.34");
    expect(screen.getByTestId("ownership-short_float").textContent).toContain("0.90");
    expect(screen.getByTestId("ownership-short_ratio").textContent).toContain("3.48");
    expect(screen.getByTestId("ownership-short_ratio").textContent).toContain("×");
  });

  it("renders an empty state when the snapshot has no row", () => {
    render(<OwnershipTab symbol="AAPL" state={resource<OwnershipData>("empty")} onRetry={vi.fn()} />);
    expect(screen.getByTestId("ownership-empty").textContent).toContain(
      "No ownership data for AAPL.",
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows its own error and retries it", async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(
      <OwnershipTab
        symbol="AAPL"
        state={resource<OwnershipData>("error", null, "HTTP 503")}
        onRetry={onRetry}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("Could not load ownership.");
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
