import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { FundamentalsTab } from "./FundamentalsTab";
import { parseFundamentals } from "./useDetail";
import type { FundamentalsData } from "./useDetail";
import { fundamentalsPayload, resource, sparseFundamentalsPayload } from "./__fixtures__/detail";

function data(payload: Record<string, unknown>) {
  const parsed = parseFundamentals(payload);
  if (!parsed) throw new Error("fixture did not parse");
  return parsed;
}

describe("Fundamentals tab", () => {
  it("shows its own skeleton while the request is pending", () => {
    render(<FundamentalsTab symbol="AAPL" state={resource("loading")} onRetry={vi.fn()} />);
    const skeleton = screen.getByTestId("fundamentals-loading");
    expect(skeleton.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByText("Loading fundamentals for AAPL…")).toBeTruthy();
  });

  it("renders all 43 metrics in their groups with units", () => {
    render(
      <FundamentalsTab
        symbol="AAPL"
        state={resource("ready", data(fundamentalsPayload()))}
        onRetry={vi.fn()}
      />,
    );

    const loaded = screen.getByTestId("fundamentals-loaded");
    expect(loaded.querySelectorAll('[data-testid^="metric-"]')).toHaveLength(43);

    for (const group of ["Valuation", "Growth", "Quality", "Income", "Profile"]) {
      expect(screen.getByRole("heading", { name: group })).toBeTruthy();
    }

    expect(screen.getByText(/43 fields · 43 reported/)).toBeTruthy();

    const revenue = screen.getByTestId("metric-revenue");
    expect(revenue.textContent).toContain("466.82B");
    expect(revenue.textContent).toContain("USD");

    const margin = screen.getByTestId("metric-gross_margin");
    expect(margin.textContent).toContain("48.65");
    expect(margin.textContent).toContain("%");

    const ratio = screen.getByTestId("metric-current_ratio");
    expect(ratio.textContent).toContain("1.00");
    expect(ratio.textContent).toContain("×");

    const shares = screen.getByTestId("metric-shares_outstanding");
    expect(shares.textContent).toContain("14.61B");
    expect(shares.textContent).toContain("sh");
  });

  it("keeps a company name as text, not markup", () => {
    render(
      <FundamentalsTab
        symbol="X"
        state={resource(
          "ready",
          data(fundamentalsPayload({ company: "<img src=x onerror=alert(1)>" })),
        )}
        onRetry={vi.fn()}
      />,
    );
    const company = screen.getByTestId("metric-company");
    expect(company.querySelector("img")).toBeNull();
    expect(company.textContent).toContain("<img src=x onerror=alert(1)>");
  });

  it("draws 43 rows for a symbol that reports only three fields", () => {
    render(
      <FundamentalsTab
        symbol="ASML"
        state={resource("ready", data(sparseFundamentalsPayload()))}
        onRetry={vi.fn()}
      />,
    );

    const rows = screen
      .getByTestId("fundamentals-loaded")
      .querySelectorAll<HTMLElement>('[data-testid^="metric-"]');
    expect(rows).toHaveLength(43);
    expect(screen.getByText(/43 fields · 3 reported/)).toBeTruthy();
    expect(screen.getByTestId("metric-company").textContent).toContain("ASML HOLDING NV");
    expect(screen.getByTestId("metric-assets").textContent).toContain("—");
  });

  it("shows a placeholder, never a zero, for an absent value", () => {
    render(
      <FundamentalsTab
        symbol="AAPL"
        state={resource("ready", data(fundamentalsPayload({ inventory: null })))}
        onRetry={vi.fn()}
      />,
    );
    const row = screen.getByTestId("metric-inventory");
    expect(row.textContent).toContain("—");
    expect(row.textContent).not.toContain("0.00");
  });

  it("renders an empty state for a 404 answer", () => {
    render(<FundamentalsTab symbol="ZZZZ" state={resource<FundamentalsData>("empty")} onRetry={vi.fn()} />);
    expect(screen.getByTestId("fundamentals-empty").textContent).toContain(
      "No fundamentals reported for ZZZZ.",
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows its own error and retries it", async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(
      <FundamentalsTab
        symbol="AAPL"
        state={resource<FundamentalsData>("error", null, "HTTP 500")}
        onRetry={onRetry}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("Could not load fundamentals.");
    expect(screen.getByText("HTTP 500")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("wraps a metric label longer than the 360 px dock", () => {
    const longKey = "extraordinarily_long_metric_label_that_would_break_a_360_panel";
    render(
      <FundamentalsTab
        symbol="AAPL"
        state={resource("ready", data(fundamentalsPayload({ [longKey]: 1 })))}
        onRetry={vi.fn()}
      />,
    );
    const row = screen.getByTestId(`metric-${longKey}`);
    const label = row.querySelector("dt");
    expect(label?.className).toContain("break-words");
    expect(label?.className).toContain("min-w-0");
    expect(within(row).getByText(/Extraordinarily long metric/)).toBeTruthy();
  });
});
