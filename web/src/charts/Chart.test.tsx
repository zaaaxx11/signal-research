import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatPrice, formatVolume } from "@/home/signals";

import { installCanvas, type CanvasHarness } from "./__fixtures__/canvas";
import { bar, intradayBars, json, wireFor } from "./__fixtures__/chart";
import { Chart } from "./Chart";
import { barTime } from "./format";
import type { ChartRange } from "./types";

/**
 * The instrument at DOM level, over a mocked `fetch` and a recording canvas.
 *
 * What a jsdom test can still prove: the accessible contract, the four states,
 * the request lifecycle, the range selector, the readout's formatted fields,
 * and the numbers the canvas publishes on `data-*` (bars, runs, DPR). Pixels and
 * milliseconds are the browser proof's job.
 */

let harness: CanvasHarness;

function serve(wire: unknown, status = 200): ReturnType<typeof vi.fn> {
  const mock = vi.fn().mockImplementation(() => Promise.resolve(json(wire, status)));
  vi.stubGlobal("fetch", mock);
  return mock;
}

async function renderReady(wire: unknown = wireFor("AAPL", "1d")) {
  const mock = serve(wire);
  render(<Chart symbol="AAPL" height={320} />);
  const canvas = (await screen.findByTestId("chart-canvas")) as HTMLCanvasElement;
  await waitFor(() => expect(canvas.getAttribute("data-painted")).not.toBe("0"));
  await act(async () => {
    await Promise.resolve();
  });
  return { canvas, mock };
}

beforeEach(() => {
  harness = installCanvas({ width: 640, height: 320 });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Chart — states", () => {
  it("shows a busy skeleton while the series is in flight", () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => new Promise(() => {})));
    render(<Chart symbol="AAPL" />);

    const loading = screen.getByTestId("chart-loading");
    expect(loading.getAttribute("aria-busy")).toBe("true");
    expect(screen.queryByTestId("chart-canvas")).toBeNull();
    expect(screen.getByTestId("chart").getAttribute("data-state")).toBe("loading");
  });

  it("draws a single-bar series rather than a blank canvas", async () => {
    const one = {
      ...wireFor("AAPL", "1d"),
      bars: [{ t: 1_767_364_200, o: 230, h: 232, l: 229, c: 231, v: 1_500_000 }],
      previous_close: 229.5,
    };
    const { canvas } = await renderReady(one);

    expect(canvas.getAttribute("data-bars")).toBe("1");
    expect(canvas.getAttribute("data-points")).toBe("1");
    expect(canvas.getAttribute("data-painted")).toBe("1");
    expect(harness.fake.passes()[0]?.fills).toBeGreaterThanOrEqual(1);
  });

  it("reports 404 as 'No chart for X', not an empty box", async () => {
    serve({ errorMessage: "unknown symbol" }, 404);
    render(<Chart symbol="ZZZZ" />);

    const alert = await screen.findByTestId("chart-error");
    expect(alert.textContent).toContain("No chart for ZZZZ");
    expect(alert.textContent).toContain("not in this chart source");
    expect(alert.getAttribute("data-not-found")).toBe("true");
    expect(screen.queryByTestId("chart-canvas")).toBeNull();
    expect(screen.queryByTestId("chart-empty")).toBeNull();
  });

  it("says an empty range has no bars, with a retry-free message", async () => {
    serve({ ...wireFor("AAPL", "1d"), bars: [] });
    render(<Chart symbol="AAPL" />);

    const empty = await screen.findByTestId("chart-empty");
    expect(empty.textContent).toContain("No bars for AAPL");
    expect(screen.queryByTestId("chart-canvas")).toBeNull();
  });

  it("shows the API's message for a transport error and retries", async () => {
    const mock = vi
      .fn()
      .mockImplementationOnce(() => Promise.resolve(json({ errorMessage: "upstream offline" }, 503)))
      .mockImplementationOnce(() => Promise.resolve(json(wireFor("AAPL", "1d"))));
    vi.stubGlobal("fetch", mock);
    render(<Chart symbol="AAPL" />);

    const alert = await screen.findByTestId("chart-error");
    expect(alert.textContent).toContain("upstream offline");
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("chart-canvas")).toBeTruthy();
    expect(mock).toHaveBeenCalledTimes(2);
  });
});

describe("Chart — range selector", () => {
  it("is a group of pressed buttons, 1D by default", async () => {
    await renderReady();
    const group = screen.getByRole("group", { name: /Chart range for AAPL/i });
    expect(group).toBeTruthy();

    const buttons = within(group).getAllByRole("button");
    expect(buttons.map((button) => button.textContent)).toEqual([
      "1D",
      "5D",
      "1M",
      "6M",
      "1Y",
      "5Y",
      "Max",
    ]);
    expect(buttons[0]?.getAttribute("aria-pressed")).toBe("true");
    expect(buttons[1]?.getAttribute("aria-pressed")).toBe("false");
  });

  it("fires exactly one request per range, and none for a repeated click", async () => {
    const mock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      const range = /range=([^&]+)/.exec(url)?.[1] ?? "1d";
      return Promise.resolve(json(wireFor("AAPL", range as ChartRange)));
    });
    vi.stubGlobal("fetch", mock);
    render(<Chart symbol="AAPL" />);
    await screen.findByTestId("chart-canvas");

    fireEvent.click(screen.getByTestId("chart-range-5d"));
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(2));
    expect(String(mock.mock.calls[0]?.[0])).toBe("/market/chart/AAPL?range=1d");
    expect(String(mock.mock.calls[1]?.[0])).toBe("/market/chart/AAPL?range=5d");

    fireEvent.click(screen.getByTestId("chart-range-1mo"));
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(3));
    expect(String(mock.mock.calls[2]?.[0])).toBe("/market/chart/AAPL?range=1mo");

    fireEvent.click(screen.getByTestId("chart-range-1y"));
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(4));
    expect(String(mock.mock.calls[3]?.[0])).toBe("/market/chart/AAPL?range=1y");

    // The long window the toolbar was missing: it must fetch too, once.
    fireEvent.click(screen.getByTestId("chart-range-5y"));
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(5));
    expect(String(mock.mock.calls[4]?.[0])).toBe("/market/chart/AAPL?range=5y");
    expect(screen.getByTestId("chart-range-5y").getAttribute("aria-pressed")).toBe("true");

    // Clicking the active range is not a new fact; it must not refetch.
    fireEvent.click(screen.getByTestId("chart-range-5y"));
    await act(async () => {
      await Promise.resolve();
    });
    expect(mock).toHaveBeenCalledTimes(5);
    expect(screen.getByTestId("chart-range-5y").getAttribute("aria-pressed")).toBe("true");
  });

  it("aborts the in-flight request when a new range is clicked", async () => {
    const signals: AbortSignal[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((_path: string, init: RequestInit) => {
        signals.push(init.signal as AbortSignal);
        return new Promise<Response>(() => {});
      }),
    );
    render(<Chart symbol="AAPL" />);
    await waitFor(() => expect(signals).toHaveLength(1));

    fireEvent.click(screen.getByTestId("chart-range-5d"));
    await waitFor(() => expect(signals).toHaveLength(2));
    fireEvent.click(screen.getByTestId("chart-range-1mo"));
    await waitFor(() => expect(signals).toHaveLength(3));

    expect(signals[0]?.aborted).toBe(true);
    expect(signals[1]?.aborted).toBe(true);
    expect(signals[2]?.aborted).toBe(false);
  });
});

describe("Chart — accessibility", () => {
  it("names the symbol, range and first/last close on the image", async () => {
    await renderReady();
    const canvas = screen.getByRole("img");
    const label = canvas.getAttribute("aria-label") ?? "";

    expect(label).toContain("AAPL chart");
    expect(label).toContain("1 day");
    expect(label).toContain("first close $");
    expect(label).toContain("last close $");
  });

  it("carries a visually hidden summary table of first, last, high and low", async () => {
    const wire = wireFor("AAPL", "1d");
    await renderReady(wire);
    const bars = wire.bars as Array<{ c: number; h: number; l: number }>;
    const first = bars[0] as { c: number };
    const last = bars[bars.length - 1] as { c: number };

    expect(screen.getByTestId("chart-summary").tagName).toBe("TABLE");
    expect(screen.getByTestId("chart-summary-first").textContent).toBe(formatPrice(first.c));
    expect(screen.getByTestId("chart-summary-last").textContent).toBe(formatPrice(last.c));
    expect(screen.getByTestId("chart-summary-high").textContent).toBe(
      formatPrice(Math.max(...bars.map((entry) => entry.h))),
    );
    expect(screen.getByTestId("chart-summary-low").textContent).toBe(
      formatPrice(Math.min(...bars.map((entry) => entry.l))),
    );
  });
});

describe("Chart — hover", () => {
  it("reads out the hovered bar's time, OHLC and volume with the house formatters", async () => {
    const wire = wireFor("AAPL", "1d");
    const bars = wire.bars as Array<{
      t: number;
      o: number;
      h: number;
      l: number;
      c: number;
      v: number;
    }>;
    for (const entry of bars) entry.v = 1_500_000;
    const { canvas } = await renderReady(wire);

    for (const clientX of [60, 320, 600]) {
      fireEvent.pointerMove(canvas, { clientX, clientY: 120 });
      const index = Number(canvas.getAttribute("data-hover-index"));
      expect(Number.isInteger(index)).toBe(true);
      const entry = bars[index] as {
        t: number;
        o: number;
        h: number;
        l: number;
        c: number;
        v: number;
      };

      const readout = screen.getByTestId("chart-readout");
      expect(within(readout).getByTestId("chart-readout-time").textContent).toBe(
        barTime(entry.t, "1d", "UTC"),
      );
      expect(within(readout).getByTestId("chart-readout-o").textContent).toBe(formatPrice(entry.o));
      expect(within(readout).getByTestId("chart-readout-h").textContent).toBe(formatPrice(entry.h));
      expect(within(readout).getByTestId("chart-readout-l").textContent).toBe(formatPrice(entry.l));
      expect(within(readout).getByTestId("chart-readout-c").textContent).toBe(formatPrice(entry.c));
      expect(within(readout).getByTestId("chart-readout-v").textContent).toBe(
        formatVolume(entry.v),
      );
    }

    expect(screen.getByTestId("chart-readout-v").textContent).toBe("1.50M");

    fireEvent.pointerLeave(canvas);
    expect(screen.queryByTestId("chart-readout")).toBeNull();
  });

  it("moves the readout with the keyboard", async () => {
    const { canvas } = await renderReady();
    fireEvent.pointerMove(canvas, { clientX: 300, clientY: 100 });
    const first = Number(canvas.getAttribute("data-hover-index"));
    fireEvent.keyDown(canvas, { key: "ArrowRight" });
    expect(Number(canvas.getAttribute("data-hover-index"))).toBe(first + 1);
    fireEvent.keyDown(canvas, { key: "End" });
    expect(Number(canvas.getAttribute("data-hover-index"))).toBe(77);
    fireEvent.keyDown(canvas, { key: "Home" });
    expect(Number(canvas.getAttribute("data-hover-index"))).toBe(0);
  });
});

describe("Chart — canvas contract", () => {
  it("backs the canvas at DPR 2 and paints text at CSS pixel sizes", async () => {
    harness = installCanvas({ width: 640, height: 320, dpr: 2 });
    const { canvas } = await renderReady(wireFor("AAPL", "1d"));

    expect(canvas.width).toBe(1280);
    expect(canvas.height).toBe(640);
    expect(canvas.getAttribute("data-dpr")).toBe("2");
    expect(harness.fake.passes()[0]?.transform).toEqual([2, 0, 0, 2, 0, 0]);
    for (const mark of harness.fake.passes()[0]?.texts ?? []) {
      expect(mark.font).toContain("10px");
    }
  });

  it("repaints at a new size after the observer reports one", async () => {
    const { canvas } = await renderReady();
    harness.resize(320, 200);
    await waitFor(() => expect(canvas.getAttribute("data-width")).toBe("320"));
    expect(canvas.width).toBe(320);
    expect(canvas.getAttribute("data-height")).toBe("200");
  });

  it("re-backs the canvas when the display's pixel ratio changes under it", async () => {
    const { canvas } = await renderReady();
    expect(canvas.width).toBe(640);

    Object.defineProperty(window, "devicePixelRatio", { configurable: true, value: 2 });
    fireEvent(window, new Event("resize"));
    await waitFor(() => expect(canvas.width).toBe(1280));
    expect(canvas.getAttribute("data-dpr")).toBe("2");
  });

  it("publishes two runs when the data has a hole", async () => {
    const head = intradayBars(10, 300);
    const tailStart = (head[head.length - 1] as { t: number }).t + 6 * 3600;
    const tail = intradayBars(10, 300).map((entry, index) => ({
      ...entry,
      t: tailStart + index * 300,
    }));
    const wire = {
      ...wireFor("AAPL", "1d"),
      bars: [...head, ...tail].map((entry) => ({ ...entry })),
    };
    const { canvas } = await renderReady(wire);

    expect(canvas.getAttribute("data-segments")).toBe("2");
    expect(canvas.getAttribute("data-bars")).toBe("20");
  });

  it("publishes a marked previous close when the payload states one", async () => {
    const { canvas } = await renderReady();
    expect(canvas.getAttribute("data-prev")).toBe("marked");
  });

  it("draws candles when asked", async () => {
    serve(wireFor("AAPL", "1d"));
    render(<Chart symbol="AAPL" kind="candle" />);
    const canvas = (await screen.findByTestId("chart-canvas")) as HTMLCanvasElement;
    await waitFor(() => expect(canvas.getAttribute("data-candles")).not.toBe("0"));
    expect(canvas.getAttribute("data-painted")).toBe("78");
  });

  it("reports the landed series upward for a card header", async () => {
    const onData = vi.fn();
    serve(wireFor("AAPL", "1d"));
    render(<Chart symbol="AAPL" compact height={132} onData={onData} />);
    await screen.findByTestId("chart-canvas");
    await waitFor(() => expect(onData).toHaveBeenCalled());
    const reported = onData.mock.calls.map((call) => call[0] as { bars: unknown[] } | null);
    const series = reported.find((entry) => entry !== null && entry.bars.length > 0);
    expect(series?.bars.length).toBeGreaterThan(0);
  });
});

describe("Chart — compact", () => {
  it("has no toolbar in a gallery card", async () => {
    serve(wireFor("^GSPC", "1d"));
    render(<Chart symbol="^GSPC" compact height={132} />);
    await screen.findByTestId("chart-canvas");
    expect(screen.queryByTestId("chart-toolbar")).toBeNull();
    expect(screen.queryByRole("group")).toBeNull();
  });
});

describe("Chart — empty payload shape", () => {
  it("renders no chart for a bar with no prices", async () => {
    const wire = { ...wireFor("AAPL", "1d"), bars: [bar(1_767_364_200, 0, 0, 0, 0, 0)] };
    serve(wire);
    render(<Chart symbol="AAPL" />);
    // A zero price is not a datum; the parser drops it and the series is empty.
    const empty = await screen.findByTestId("chart-empty");
    expect(empty.textContent).toContain("No bars for AAPL");
  });
});
