import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { installCanvas } from "./__fixtures__/canvas";
import { json, wireFor } from "./__fixtures__/chart";
import { StockChart, STOCK_CHART_HEIGHT } from "./StockChart";

/** The stock page's instrument: 280 px of chart, and no page around it. */

beforeEach(() => {
  installCanvas({ width: 640, height: 260 });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("StockChart", () => {
  it("reserves 280 px, draws candles, and adds no page chrome", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(json(wireFor("AAPL", "1d"))));
    vi.stubGlobal("fetch", fetchMock);

    render(<StockChart symbol="AAPL" />);

    expect(screen.getByTestId("stock-chart").getAttribute("data-symbol")).toBe("AAPL");
    const chart = screen.getByTestId("chart");
    expect(chart.style.height).toBe(`${STOCK_CHART_HEIGHT}px`);
    expect(chart.getAttribute("data-symbol")).toBe("AAPL");

    const canvas = (await screen.findByTestId("chart-canvas")) as HTMLCanvasElement;
    await waitFor(() => expect(canvas.getAttribute("data-candles")).not.toBe("0"));

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
    await screen.findByTestId("chart-canvas");
    expect(screen.getByTestId("chart-range-1y").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("chart").getAttribute("data-range")).toBe("1y");
  });
});
