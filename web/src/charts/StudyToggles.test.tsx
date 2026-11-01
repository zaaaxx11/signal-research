import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatPrice } from "@/home/signals";

import { Chart } from "./Chart";
import { json, wireFor } from "./__fixtures__/chart";
import { fakeCharts, lastChart, resetFakeCharts } from "./__fixtures__/lightweight";
import { resetChartThemeCache } from "./theme";

/**
 * The studies, from the chip to the series.
 *
 * What a jsdom test can prove about a chart drawn by a canvas library is what
 * the component *asked* it to do: which series exist, in which pane, with
 * which token colour, carrying how many points. So the library is the recorder
 * in `__fixtures__/lightweight`, and this file asserts the contract the
 * toggles own:
 *
 *   * one chip group, `aria-pressed`, SMA 50 on by default;
 *   * a toggle adds or removes a line series on the chart that already exists
 *     (never a second `createChart`);
 *   * RSI creates a pane, and switching it off leaves the price pane alone;
 *   * the accessible table gains exactly the columns that are on, with empty
 *     cells where the study has not warmed up.
 */

vi.mock("lightweight-charts", async () => {
  const { fakeLightweightCharts } = await import("./__fixtures__/lightweight");
  return fakeLightweightCharts();
});

/** The tokens the studies resolve their ink from. */
const TOKENS: Record<string, string> = {
  "--surface": "#181215",
  "--surface-raised": "#1f181c",
  "--border": "#2b2227",
  "--fg": "#f4eef1",
  "--fg-muted": "#b9afb4",
  "--fg-subtle": "#9d9398",
  "--up": "#3ddc97",
  "--down": "#ff5f4d",
  "--flat": "#a6a2a4",
  "--accent": "#ff8ec4",
};

/** 250 weekday bars: enough for SMA 200 and RSI to have real values. */
const WIRE = wireFor("AAPL", "1y");

function serve(): void {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(json(WIRE))));
}

async function renderChart() {
  serve();
  render(<Chart symbol="AAPL" renderer="bars" initialRange="1y" height={360} />);
  await screen.findByTestId("chart-bars");
  await waitFor(() => expect(fakeCharts).toHaveLength(1));
  return screen.getByTestId("chart-bars");
}

/** The line series currently on the chart, in creation order. */
function lines() {
  return (lastChart()?.series ?? []).filter((series) => series.kind === "Line");
}

function headers(): string[] {
  const table = screen.getByTestId("chart-bars-table");
  return [...table.querySelectorAll("thead th")].map((cell) => cell.textContent ?? "");
}

beforeEach(() => {
  resetFakeCharts();
  resetChartThemeCache();
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

describe("studies — the chip group", () => {
  it("is a labelled group of pressed buttons with SMA 50 on by default", async () => {
    await renderChart();

    const group = screen.getByRole("group", { name: "Technical indicators for AAPL" });
    const chips = within(group).getAllByRole("button");
    expect(chips.map((chip) => chip.textContent)).toEqual(["SMA 20", "SMA 50", "SMA 200", "RSI 14"]);
    expect(chips.map((chip) => chip.getAttribute("aria-pressed"))).toEqual([
      "false",
      "true",
      "false",
      "false",
    ]);
    // Every chip is a real button: Tab reaches it and Enter/Space activates it.
    for (const chip of chips) expect(chip.tagName).toBe("BUTTON");
  });

  it("keeps the plot's height fixed when a pane appears — no layout shift", async () => {
    const bars = await renderChart();
    const before = bars.style.height;

    fireEvent.click(screen.getByTestId("chart-indicator-rsi"));

    expect(bars.style.height).toBe(before);
    expect(screen.getByTestId("chart").style.height).toBe("360px");
    expect(lastChart()?.panes()).toHaveLength(2);
  });

  it("does not remember the selection across a remount", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    serve();
    const first = render(<Chart symbol="AAPL" renderer="bars" initialRange="1y" height={360} />);
    await screen.findByTestId("chart-bars");
    fireEvent.click(screen.getByTestId("chart-indicator-sma200"));
    expect(screen.getByTestId("chart-indicator-sma200").getAttribute("aria-pressed")).toBe("true");

    first.unmount();
    render(<Chart symbol="AAPL" renderer="bars" initialRange="1y" height={360} />);
    await screen.findByTestId("chart-bars");

    expect(screen.getByTestId("chart-indicator-sma200").getAttribute("aria-pressed")).toBe("false");
    expect(setItem).not.toHaveBeenCalled();
  });
});

describe("studies — SMA overlays", () => {
  it("draws the default SMA 50 as a line on the price pane, in --fg-muted", async () => {
    await renderChart();

    const chart = lastChart();
    expect(chart?.series.map((series) => series.kind)).toEqual(["Bar", "Histogram", "Line"]);

    const [sma50] = lines();
    expect(sma50?.pane).toBe(0);
    expect(sma50?.options).toMatchObject({ color: "#b9afb4", title: "SMA 50", lineWidth: 1 });
    // 250 bars, first average at index 49: 201 points on the line.
    expect(sma50?.data).toHaveLength(201);
    expect(typeof sma50?.data[0]?.value).toBe("number");
  });

  it("adds SMA 200 with one more addSeries, never a second chart", async () => {
    await renderChart();
    const chart = lastChart();
    // Bar, volume, and the default SMA 50 overlay.
    expect(chart?.addSeries).toHaveBeenCalledTimes(3);

    fireEvent.click(screen.getByTestId("chart-indicator-sma200"));

    expect(fakeCharts).toHaveLength(1);
    expect(chart?.addSeries).toHaveBeenCalledTimes(4);
    expect(lines().map((series) => series.options.title)).toEqual(["SMA 50", "SMA 200"]);
    // --fg-subtle, the quietest of the three inks.
    expect(lines()[1]?.options.color).toBe("#9d9398");
    // 250 bars, warm-up of 199: 51 points.
    expect(lines()[1]?.data).toHaveLength(51);

    fireEvent.click(screen.getByTestId("chart-indicator-sma200"));

    expect(chart?.removeSeries).toHaveBeenCalledTimes(1);
    expect(lines().map((series) => series.options.title)).toEqual(["SMA 50"]);
    expect(screen.getByTestId("chart-indicator-sma200").getAttribute("aria-pressed")).toBe("false");
  });

  it("keeps the selection and the table's columns in catalogue order", async () => {
    await renderChart();

    fireEvent.click(screen.getByTestId("chart-indicator-sma200"));
    fireEvent.click(screen.getByTestId("chart-indicator-sma20"));

    // Creation order follows the clicks (a series is created when asked for);
    // the selection, the columns and the drawing order do not.
    expect(screen.getByTestId("chart-bars").getAttribute("data-indicators")).toBe(
      "sma20,sma50,sma200",
    );
    expect(headers().slice(-3)).toEqual(["SMA 20", "SMA 50", "SMA 200"]);
    expect(lines()).toHaveLength(3);
  });
});

describe("studies — the RSI pane", () => {
  it("adds a pane below the price bars without throwing", async () => {
    await renderChart();
    const chart = lastChart();

    expect(() => fireEvent.click(screen.getByTestId("chart-indicator-rsi"))).not.toThrow();

    expect(chart?.addPane).toHaveBeenCalledTimes(1);
    expect(chart?.panes()).toHaveLength(2);
    // The price pane keeps three quarters; the study gets one.
    expect(chart?.panes()[0]?.setStretchFactor).toHaveBeenCalledWith(3);
    expect(chart?.panes()[1]?.setStretchFactor).toHaveBeenCalledWith(1);

    const [rsi] = lines().filter((series) => series.pane === 1);
    expect(rsi?.options).toMatchObject({ color: "#ff8ec4", title: "RSI 14" });
    // 250 bars, first RSI at index 14: 236 points, all within 0..100.
    expect(rsi?.data).toHaveLength(236);
    for (const point of rsi?.data ?? []) {
      expect(Number(point.value)).toBeGreaterThanOrEqual(0);
      expect(Number(point.value)).toBeLessThanOrEqual(100);
    }
    expect(screen.getByTestId("chart-bars").getAttribute("data-rsi")).toBe("pane");
  });

  it("drops the pane again when the study is switched off", async () => {
    await renderChart();

    fireEvent.click(screen.getByTestId("chart-indicator-rsi"));
    expect(lastChart()?.panes()).toHaveLength(2);

    fireEvent.click(screen.getByTestId("chart-indicator-rsi"));

    expect(lastChart()?.panes()).toHaveLength(1);
    expect(lastChart()?.series.map((series) => series.kind)).toEqual(["Bar", "Histogram", "Line"]);
    expect(screen.getByTestId("chart-bars").getAttribute("data-rsi")).toBe("off");
    // Still one chart: a study is a series, not a renderer.
    expect(fakeCharts).toHaveLength(1);
  });
});

describe("studies — the text twin", () => {
  it("carries only the columns that are switched on", async () => {
    await renderChart();

    expect(headers()).toEqual(["Time", "Open", "High", "Low", "Close", "SMA 50"]);

    fireEvent.click(screen.getByTestId("chart-indicator-rsi"));
    expect(headers()).toEqual(["Time", "Open", "High", "Low", "Close", "SMA 50", "RSI 14"]);

    fireEvent.click(screen.getByTestId("chart-indicator-sma50"));
    expect(headers()).toEqual(["Time", "Open", "High", "Low", "Close", "RSI 14"]);

    fireEvent.click(screen.getByTestId("chart-indicator-rsi"));
    expect(headers()).toEqual(["Time", "Open", "High", "Low", "Close"]);
  });

  it("leaves a warm-up cell empty rather than printing a zero", async () => {
    await renderChart();
    fireEvent.click(screen.getByTestId("chart-indicator-sma200"));

    const table = screen.getByTestId("chart-bars-table");
    const rows = [...table.querySelectorAll("tbody tr")];
    expect(rows).toHaveLength(250);

    const cells = (row: Element) => [...row.querySelectorAll("td[data-study]")];
    // Bar 1: neither average has warmed up.
    expect(cells(rows[0] as Element).map((cell) => cell.textContent)).toEqual(["", ""]);
    // Bar 200: SMA 200 is defined at index 199; SMA 50 has been for 150 bars.
    const bar200 = cells(rows[199] as Element);
    expect(bar200[1]?.textContent).toMatch(/^\$/);
    expect(bar200[1]?.textContent).not.toBe("");
  });

  it("names the period in the table's caption", async () => {
    await renderChart();
    fireEvent.click(screen.getByTestId("chart-indicator-sma200"));

    const caption = screen.getByTestId("chart-bars-table").querySelector("caption")?.textContent ?? "";
    expect(caption).toContain("SMA 50 (50-bar simple moving average of the close)");
    expect(caption).toContain("SMA 200 (200-bar simple moving average of the close)");
    expect(caption).toContain("warm up");
  });

  it("formats an average as a price and an RSI as a plain number", async () => {
    await renderChart();
    fireEvent.click(screen.getByTestId("chart-indicator-rsi"));

    const table = screen.getByTestId("chart-bars-table");
    const rows = [...table.querySelectorAll("tbody tr")];
    const last = rows[rows.length - 1] as Element;
    const [smaCell, rsiCell] = [...last.querySelectorAll("td[data-study]")];

    expect(smaCell?.textContent).toBe(formatPrice(Number(smaCell?.textContent?.replace("$", ""))));
    // Two decimals, no currency mark.
    expect(rsiCell?.textContent).toMatch(/^\d+\.\d{2}$/);
  });
});
