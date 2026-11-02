/**
 * The Options tab: expiry chips, a strikes table, sticky headers, the delay
 * caption, and the four states.
 *
 * The component takes a `Resource<OptionsChain>` from the page, so every state
 * is constructed by hand — no network. The chain carries strikes for the
 * selected expiry only; the endpoint does not answer per-expiry, so clicking a
 * chip filters the returned strikes client-side.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { parseOptions } from "./options";
import { optionsPayload } from "./__fixtures__/ticker";
import { OptionsTab } from "./OptionsTab";

function chain(overrides: Record<string, unknown> = {}) {
  const parsed = parseOptions(optionsPayload(overrides));
  if (!parsed) throw new Error("fixture did not parse");
  return parsed;
}

function ready(overrides: Record<string, unknown> = {}) {
  return { status: "ready" as const, data: chain(overrides), error: null, retry: vi.fn() };
}

describe("Options tab states", () => {
  it("shows a spinner while the chain fetches", () => {
    render(
      <OptionsTab
        symbol="AAPL"
        state={{ status: "loading", data: null, error: null, retry: vi.fn() }}
      />,
    );
    expect(screen.getByTestId("options-loading").getAttribute("aria-busy")).toBe("true");
  });

  it("shows the reason and a Retry on error", () => {
    const onRetry = vi.fn();
    render(
      <OptionsTab
        symbol="AAPL"
        state={{ status: "error", data: null, error: "HTTP 503", retry: onRetry }}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("HTTP 503");
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("shows the empty sentence when the symbol has no listed chain", () => {
    render(
      <OptionsTab
        symbol="ZZZZ"
        state={{ status: "empty", data: null, error: null, retry: vi.fn() }}
      />,
    );
    expect(screen.getByTestId("options-empty").textContent).toBe("No options for this symbol.");
  });
});

describe("Options tab chain", () => {
  it("renders the delay caption and the expiry header", () => {
    render(<OptionsTab symbol="AAPL" state={ready()} />);
    expect(screen.getByTestId("options-header").textContent).toContain("2026-11-04");
    expect(screen.getByTestId("options-header").textContent).toContain("2026-11-02");
    expect(screen.getByTestId("options-caption").textContent).toBe("Delayed 15 minutes");
  });

  it("renders one chip per expiry and selects the nearest by default", async () => {
    const user = userEvent.setup();
    render(<OptionsTab symbol="AAPL" state={ready()} />);

    const chips = screen.getAllByRole("button", { name: /2026-/ });
    expect(chips).toHaveLength(2);
    const defaultSelected = chips.find((c) => c.getAttribute("aria-pressed") === "true");
    expect(defaultSelected?.textContent).toContain("2026-11-04");

    await user.click(screen.getByRole("button", { name: /2026-11-06/ }));
    expect(screen.getByRole("button", { name: /2026-11-06/ }).getAttribute("aria-pressed")).toBe("true");
  });

  it("filters strikes client-side when a chip is clicked", async () => {
    const user = userEvent.setup();
    render(<OptionsTab symbol="AAPL" state={ready({ thirdExpiry: true })} />);

    const table = () => screen.getByTestId("options-table");
    expect(table().querySelectorAll("tbody tr[data-strike-row]")).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: /2026-11-06/ }));
    expect(table().querySelectorAll("tbody tr[data-strike-row]")).toHaveLength(1);
  });

  it("renders greeks at four decimals, IV as a percent, premium as money", () => {
    render(<OptionsTab symbol="AAPL" state={ready()} />);

    const row = screen
      .getByTestId("options-table")
      .querySelector('tr[data-strike-row="275"]') as HTMLElement;
    const cells = within(row);
    expect(cells.getByTestId("call-delta")).toHaveTextContent("+0.9877");
    expect(cells.getByTestId("call-gamma")).toHaveTextContent("+0.0010");
    expect(cells.getByTestId("call-theta")).toHaveTextContent("-0.1158");
    expect(cells.getByTestId("call-vega")).toHaveTextContent("+0.0080");
    expect(cells.getByTestId("put-delta")).toHaveTextContent("-0.0122");
    expect(cells.getByTestId("call-premium")).toHaveTextContent("$62.35");
    expect(cells.getByTestId("implied-vol")).toHaveTextContent("136.10%");
  });

  it("sorts strikes ascending", () => {
    render(<OptionsTab symbol="AAPL" state={ready({ shuffled: true })} />);
    const strikes = Array.from(
      screen.getByTestId("options-table").querySelectorAll("tr[data-strike-row]"),
    ).map((r) => r.getAttribute("data-strike-row"));
    expect(strikes).toEqual(["275", "290"]);
  });

  it("carries the mobile collapse and sticky header classes", () => {
    render(<OptionsTab symbol="AAPL" state={ready()} />);
    expect(screen.getByTestId("options-table").className).toContain("md:table-fixed");
    expect(screen.getByTestId("options-thead").className).toContain("sticky");
    expect(screen.getByTestId("options-thead").className).toContain("top-0");
  });
});