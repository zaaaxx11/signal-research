/**
 * The Overview's technicals block: the mentor's numbers, finally on the page.
 *
 * The block owns two reads (`/engine/technicals` and `/engine/risk`) and four
 * groups. The tests pin the facts the owner went looking for: MTD is printed
 * with the API's own value and a percent sign, an absent value is an em dash,
 * Alpha and Beta keep their different unit shapes, and a 404 is a sentence
 * inside this block — never a blank page.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TechnicalsBlock } from "./TechnicalsBlock";
import {
  json,
  notFound,
  stubTickerApi,
  technicalsPayload,
} from "./__fixtures__/ticker";

function cell(key: string): Element | null {
  return screen.getByTestId("technicals-block").querySelector(`[data-technical="${key}"]`);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TechnicalsBlock", () => {
  it("prints MTD with the API's value, a percent sign and its direction", async () => {
    stubTickerApi();
    render(<TechnicalsBlock symbol="AAPL" />);

    await waitFor(() => expect(cell("perf_mtd")?.textContent).toBe("+5.83%"));
    expect(cell("perf_mtd")?.getAttribute("data-tone")).toBe("up");
    expect(cell("perf_wtd")?.textContent).toBe("+2.17%");
    expect(cell("momentum_10")?.textContent).toBe("+4.61%");
    expect(cell("rsi_14")?.textContent).toBe("58.42");
  });

  it("draws a null as an em dash and keeps a reported zero as zero", async () => {
    const body = technicalsPayload();
    body["values"] = {
      ...(body["values"] as Record<string, unknown>),
      sma_13: null,
      momentum_10: null,
      perf_mtd: 0,
    };
    stubTickerApi({ technicals: () => json(body) });

    render(<TechnicalsBlock symbol="AAPL" />);

    await waitFor(() => expect(cell("perf_mtd")?.textContent).toBe("0.00%"));
    expect(cell("sma_13")?.textContent).toBe("—");
    expect(cell("sma_13")?.getAttribute("data-absent")).toBe("true");
    expect(cell("momentum_10")?.textContent).toBe("—");
    expect(cell("momentum_10")?.getAttribute("data-absent")).toBe("true");
    // Zero is a fact; it claims no direction and is not absent.
    expect(cell("perf_mtd")?.getAttribute("data-tone")).toBe("none");
    expect(cell("perf_mtd")?.getAttribute("data-absent")).toBe("false");
  });

  it("renders the risk group with Alpha and Beta in their own units", async () => {
    stubTickerApi();
    render(<TechnicalsBlock symbol="AAPL" />);

    await waitFor(() => expect(cell("alpha")?.textContent).toBe("+4.82%"));
    expect(cell("beta")?.textContent).toBe("1.08");
    expect(cell("volatility")?.textContent).toBe("21.43%");
    expect(cell("sharpe")?.textContent).toBe("+1.42");
    expect(cell("max_drawdown")?.textContent).toBe("-18.35%");
    expect(cell("max_drawdown")?.getAttribute("data-tone")).toBe("down");
    expect(cell("r_squared")?.textContent).toBe("0.74");
    expect(screen.getByText("vs S&P 500, 1Y daily")).toBeTruthy();
  });

  it("names every group's window and collapses the grid to two columns on mobile", async () => {
    stubTickerApi();
    render(<TechnicalsBlock symbol="AAPL" />);

    await waitFor(() => expect(cell("perf_mtd")?.textContent).toBe("+5.83%"));

    expect(screen.getAllByText("Daily closes, 1 year")).toHaveLength(3);
    expect(screen.getByText("vs S&P 500, 1Y daily")).toBeTruthy();

    const grid = screen.getByTestId("technicals-grid");
    expect(grid.className).toContain("grid-cols-2");
    expect(grid.className).toContain("min-[900px]:grid-cols-4");

    // Numbers stay mono and right-aligned at every width.
    expect(cell("sma_13")?.className).toContain("font-mono");
    expect(cell("sma_13")?.className).toContain("text-right");
    expect(cell("beta")?.className).toContain("font-mono");
    expect(cell("beta")?.className).toContain("text-right");
  });

  it("shows a technicals 404's reason and leaves the risk group standing", async () => {
    const reason = "technicals_unavailable: no daily close series for ZZZZ";
    stubTickerApi({ technicals: () => notFound(reason) });

    render(<TechnicalsBlock symbol="ZZZZ" />);

    const failure = await screen.findByTestId("technicals-error");
    expect(failure.textContent).toContain(reason);
    expect(cell("perf_mtd")).toBeNull();
    // The other read is independent: its group still paints.
    await waitFor(() => expect(cell("alpha")?.textContent).toBe("+4.82%"));
  });

  it("shows a risk 404's reason and leaves the technicals groups standing", async () => {
    const reason = "risk_unavailable: the benchmark series is not on file";
    stubTickerApi({ risk: () => notFound(reason) });

    render(<TechnicalsBlock symbol="AAPL" />);

    await waitFor(() => expect(cell("perf_mtd")?.textContent).toBe("+5.83%"));
    const failure = await screen.findByTestId("risk-error");
    expect(failure.textContent).toContain(reason);
    expect(screen.getAllByText("Daily closes, 1 year")).toHaveLength(3);
  });

  it("does not fire the reads twice for one symbol", async () => {
    const fetchMock = stubTickerApi();
    render(<TechnicalsBlock symbol="AAPL" />);

    await waitFor(() => expect(cell("perf_mtd")?.textContent).toBe("+5.83%"));
    // Let any duplicate effect flush before counting.
    await new Promise((resolve) => setTimeout(resolve, 0));

    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls.filter((url) => url.includes("/engine/technicals/"))).toHaveLength(1);
    expect(urls.filter((url) => url.includes("/engine/risk/"))).toHaveLength(1);
    expect(urls).toContain("/engine/technicals/AAPL");
    expect(urls).toContain("/engine/risk/AAPL");
  });
});
