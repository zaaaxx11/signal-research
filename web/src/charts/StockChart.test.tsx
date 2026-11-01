import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { installCanvas } from "./__fixtures__/canvas";
import { json, wireFor } from "./__fixtures__/chart";
import { fakeCharts, resetFakeCharts } from "./__fixtures__/lightweight";
import { StockChart, STOCK_CHART_HEIGHT } from "./StockChart";

/**
 * The stock page's instrument: 280 px of chart, and no page around it.
 *
 * The picture is a `lightweight-charts` bar series (mocked here — jsdom has no
 * canvas), mounted through `Chart`'s bar renderer. The range selector and the
 * height are still this component's contract, so they are asserted on the
 * instrument's own root, not on the library.
 */

vi.mock("lightweight-charts", async () => {
  const { fakeLightweightCharts } = await import("./__fixtures__/lightweight");
  return fakeLightweightCharts();
});

beforeEach(() => {
  resetFakeCharts();
  installCanvas({ width: 640, height: 260 });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("StockChart", () => {
  it("reserves 280 px, draws bars, and adds no page chrome", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(json(wireFor("AAPL", "1d"))));
    vi.stubGlobal("fetch", fetchMock);

    render(<StockChart symbol="AAPL" />);

    expect(screen.getByTestId("stock-chart").getAttribute("data-symbol")).toBe("AAPL");
    const chart = screen.getByTestId("chart");
    expect(chart.style.height).toBe(`${STOCK_CHART_HEIGHT}px`);
    expect(chart.getAttribute("data-symbol")).toBe("AAPL");
    expect(chart.getAttribute("data-renderer")).toBe("bars");

    const bars = await screen.findByTestId("chart-bars");
    expect(bars.getAttribute("data-bars")).toBe("78");
    expect(bars.querySelector("[data-testid='chart-bars-table']")).toBeTruthy();

    // The library got a Bar series and the payload's own bar count.
    await waitFor(() => expect(fakeCharts).toHaveLength(1));
    expect(fakeCharts[0]?.series[0]?.kind).toBe("Bar");
    expect(fakeCharts[0]?.series[0]?.data).toHaveLength(78);

    expect(screen.queryByRole("heading")).toBeNull();
    expect(screen.queryByRole("main")).toBeNull();
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/market/chart/AAPL?range=1d");
  });

  it("honours an initial range and exposes the selector", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => Promise.resolve(json(wireFor("AAPL", "1y")))),
    );
    render(<StockChart symbol="AAPL" initialRange="1y" />);
    await screen.findByTestId("chart-bars");
    expect(screen.getByTestId("chart-range-1y").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("chart").getAttribute("data-range")).toBe("1y");
  });
});
