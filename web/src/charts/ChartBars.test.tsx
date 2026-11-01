import { act, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatPrice } from "@/home/signals";

import { toBars, utcLabel } from "./bars";
import { ChartBars } from "./ChartBars";
import { fakeCharts, lastChart, resetFakeCharts } from "./__fixtures__/lightweight";
import { resetChartThemeCache } from "./theme";

/**
 * The component's contract, on the library's own terms.
 *
 * The real renderer needs a canvas jsdom does not have, so `lightweight-charts`
 * is replaced with a recorder (`__fixtures__/lightweight`). What is left to
 * prove is exactly what this component owns: one chart per mount, a BAR series
 * (not a candlestick), `setData` for a data change, `remove` on unmount, the
 * token palette, the visually hidden table, and no entrance animation for a
 * reader who asked for less motion.
 */

vi.mock("lightweight-charts", async () => {
  const { fakeLightweightCharts } = await import("./__fixtures__/lightweight");
  return fakeLightweightCharts();
});

const T0 = 1_767_364_200;

const BARS = toBars({
  bars: [
    { t: T0, o: 10, h: 11, l: 9.5, c: 10.5, v: 1_000 },
    { t: T0 + 300, o: 10.5, h: 12, l: 10, c: 11.5, v: 2_000 },
    { t: T0 + 600, o: 11.5, h: 12.5, l: 11, c: 12, v: 3_000 },
  ],
});

/** The five tokens the component actually paints with. */
const TOKENS: Record<string, string> = {
  "--surface": "#181215",
  "--surface-raised": "#1f181c",
  "--border": "#2b2227",
  "--fg-muted": "#b9afb4",
  "--fg-subtle": "#9d9398",
  "--up": "#3ddc97",
  "--down": "#ff5f4d",
  "--flat": "#a6a2a4",
  "--accent": "#ff8ec4",
};

class TestResizeObserver {
  private readonly callback: ResizeObserverCallback;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
  }

  observe(): void {
    queueMicrotask(() => {
      this.callback(
        [{ contentRect: { width: 220, height: 36 } } as ResizeObserverEntry],
        this as unknown as ResizeObserver,
      );
    });
  }

  unobserve(): void {}

  disconnect(): void {}
}

beforeEach(() => {
  resetFakeCharts();
  resetChartThemeCache();
  vi.stubGlobal("ResizeObserver", TestResizeObserver);
  vi.spyOn(window, "getComputedStyle").mockImplementation(
    () =>
      ({
        getPropertyValue: (name: string) => TOKENS[name] ?? "",
      }) as unknown as CSSStyleDeclaration,
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ChartBars — lifecycle", () => {
  it("creates one chart with one bar series and the payload", async () => {
    render(<ChartBars bars={BARS} height={180} label="S&P 500 chart" />);
    await act(async () => {
      await Promise.resolve();
    });

    expect(fakeCharts).toHaveLength(1);
    const chart = lastChart();
    expect(chart).toBeDefined();
    expect(chart?.series.map((series) => series.kind)).toEqual(["Bar", "Histogram"]);
    // A bar series, not a candlestick: the definition's type is the fact.
    expect(chart?.series[0]?.kind).toBe("Bar");
    expect(chart?.series[0]?.data).toEqual([
      { time: T0, open: 10, high: 11, low: 9.5, close: 10.5 },
      { time: T0 + 300, open: 10.5, high: 12, low: 10, close: 11.5 },
      { time: T0 + 600, open: 11.5, high: 12.5, low: 11, close: 12 },
    ]);
    expect(chart?.timeScale.fitContent).toHaveBeenCalled();
  });

  it("sizes from ResizeObserver, asks the library to auto-size, and stays out of a strip's swipe", async () => {
    render(<ChartBars bars={BARS} height={36} label="S&P 500 strip" />);
    await act(async () => {
      await Promise.resolve();
    });

    expect(lastChart()?.options.autoSize).toBe(true);
    expect(screen.getByTestId("chart-bars").getAttribute("data-width")).toBe("220");
    // A 36 px chart has no interaction to offer; it must not eat the gesture
    // that scrolls the strip.
    expect(screen.getByTestId("chart-bars-canvas").className).toContain("pointer-events-none");
  });

  it("updates a mounted chart with setData, never a second chart", async () => {
    const { rerender } = render(<ChartBars bars={BARS} height={180} label="S&P 500 chart" />);
    const chart = lastChart();
    expect(chart?.series[0]?.setData).toHaveBeenCalledTimes(1);

    const next = toBars({ bars: [{ t: T0 + 900, o: 12, h: 13, l: 11.5, c: 12.5, v: 4_000 }] });
    rerender(<ChartBars bars={next} height={180} label="S&P 500 chart" />);

    expect(fakeCharts).toHaveLength(1);
    expect(chart?.series[0]?.setData).toHaveBeenCalledTimes(2);
    expect(chart?.series[0]?.data).toHaveLength(1);
  });

  it("removes the chart on unmount and leaves nothing behind", async () => {
    const first = render(<ChartBars bars={BARS} height={180} label="One" />);
    const chart = lastChart();
    first.unmount();

    expect(chart?.remove).toHaveBeenCalledTimes(1);

    const second = render(<ChartBars bars={BARS} height={180} label="Two" />);
    expect(fakeCharts).toHaveLength(2);
    second.unmount();
    expect(chart?.remove).toHaveBeenCalledTimes(1);
    expect(lastChart()?.remove).toHaveBeenCalledTimes(1);
  });
});

describe("ChartBars — the palette", () => {
  it("paints the bars with --up/--down and the plane with --surface", () => {
    render(<ChartBars bars={BARS} height={180} label="S&P 500 chart" />);
    const chart = lastChart();

    expect(chart?.series[0]?.options.upColor).toBe("#3ddc97");
    expect(chart?.series[0]?.options.downColor).toBe("#ff5f4d");
    expect(chart?.options.layout).toMatchObject({
      background: { type: "solid", color: "#181215" },
      textColor: "#b9afb4",
      attributionLogo: false,
    });
  });

  it("shows the volume histogram only when asked", () => {
    const { rerender } = render(<ChartBars bars={BARS} height={180} label="S&P 500 chart" />);
    const chart = lastChart();
    const volume = chart?.series[1];

    expect(volume?.options.visible).toBe(false);
    expect(volume?.data).toEqual([]);

    rerender(<ChartBars bars={BARS} height={180} label="S&P 500 chart" showVolume />);
    expect(volume?.options.visible).toBe(true);
    expect(volume?.data).toHaveLength(3);
    expect(volume?.data[0]).toMatchObject({ time: T0, value: 1_000 });
  });
});

describe("ChartBars — the text twin", () => {
  it("lists every bar in a visually hidden table", () => {
    render(<ChartBars bars={BARS} height={180} label="S&P 500 chart" showVolume />);

    const table = screen.getByTestId("chart-bars-table");
    expect(table.tagName).toBe("TABLE");
    expect(table.closest(".sr-only")).not.toBeNull();
    expect(table.querySelectorAll("tbody tr")).toHaveLength(3);

    const headers = [...table.querySelectorAll("thead th")].map((cell) => cell.textContent);
    expect(headers).toEqual(["Time", "Open", "High", "Low", "Close", "Volume"]);

    const first = table.querySelector("tbody tr");
    expect(first?.querySelector("th")?.textContent).toBe(utcLabel(T0));
    expect(first?.querySelectorAll("td")[0]?.textContent).toBe(formatPrice(10));
    expect(first?.querySelectorAll("td")[4]?.textContent).toBe("1.0K");

    expect(table.querySelector("caption")?.textContent).toContain("3 bars, oldest first");
    expect(table.querySelector("caption")?.textContent).toContain("Times are UTC");
  });

  it("names the image and points it at the table's caption", () => {
    render(<ChartBars bars={BARS} height={180} label="S&P 500 chart" />);

    const image = screen.getByRole("img", { name: "S&P 500 chart" });
    const describedBy = image.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    const description = document.getElementById(describedBy ?? "");
    // The caption, not the table body: a description that read 30 rows would
    // be a wall of numbers, and the table is still there to browse.
    expect(description?.tagName).toBe("CAPTION");
    expect(description?.textContent).toContain("3 bars, oldest first");
  });

  it("says an ordinal series has no clock", () => {
    const closes = toBars({ closes: [10, 10.5, 11] });
    render(<ChartBars bars={closes} height={36} label="S&P 500: 3 closes" />);

    expect(screen.getByTestId("chart-bars").getAttribute("data-bars-source")).toBe("ordinal");
    const table = screen.getByTestId("chart-bars-table");
    expect(table.querySelector("caption")?.textContent).toContain("bar numbers");
    expect(within(table).getAllByRole("row")[1]?.textContent).toContain("Bar 1");
  });
});

describe("ChartBars — motion", () => {
  it("animates the entrance by default", () => {
    render(<ChartBars bars={BARS} height={180} label="S&P 500 chart" />);

    expect(screen.getByTestId("chart-bars").getAttribute("data-motion")).toBe("full");
    expect(screen.getByTestId("chart-bars-canvas").className).toContain("zt-chart-in");
  });

  it("does not render the entrance animation when motion is reduced", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query.includes("prefers-reduced-motion"),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));

    render(<ChartBars bars={BARS} height={180} label="S&P 500 chart" />);

    expect(screen.getByTestId("chart-bars").getAttribute("data-motion")).toBe("reduced");
    expect(screen.getByTestId("chart-bars-canvas").className).not.toContain("zt-chart-in");
  });
});
