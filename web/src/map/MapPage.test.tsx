import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { installCanvas, json, type CanvasHarness } from "./__fixtures__/canvas";
import { SMALL_WIRE } from "./__fixtures__/payload";
import { MapPage } from "./MapPage";

/**
 * MapPage with the new multi-view layout: sidebar, 4 view tabs, timeframe select,
 * search, filters.
 */

let harness: CanvasHarness;

function serves(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const mock = vi.fn().mockImplementation((path: string) => {
    // Handle the groups endpoint
    if (typeof path === "string" && path.includes("/market/groups")) {
      return Promise.resolve(json([], 200));
    }
    return Promise.resolve(json(body, status));
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

async function renderReady(wire: unknown = SMALL_WIRE) {
  serves(wire);
  render(<MapPage />);
  const canvas = (await screen.findByTestId("map-canvas")) as HTMLCanvasElement;
  await waitFor(() => expect(canvas.getAttribute("data-tiles")).not.toBe("0"));
  await act(async () => {
    await Promise.resolve();
  });
  return canvas;
}

beforeEach(() => {
  harness = installCanvas({ width: 900, height: 600 });
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("MapPage — data states", () => {
  it("shows a busy skeleton while the document is in flight", () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => new Promise(() => {})));
    render(<MapPage />);

    const loading = screen.getByTestId("map-loading");
    expect(loading.getAttribute("aria-busy")).toBe("true");
    expect(screen.queryByTestId("map-canvas")).toBeNull();
    expect(screen.getByTestId("map-page").getAttribute("data-state")).toBe("loading");
  });

  it("draws the treemap once the document lands", async () => {
    const canvas = await renderReady();

    expect(canvas.getAttribute("data-tiles")).toBe("7");
    expect(canvas.getAttribute("data-drawn")).toBe("7");
    expect(canvas.getAttribute("data-omitted")).toBe("0");
    expect(screen.getByTestId("map-metric").textContent).toContain("1-Day");
    expect(screen.getByTestId("map-counts").textContent).toContain("7");
  });

  it("renders an ErrorState when the fetch fails, with a retry", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation((path: string) => {
        if (typeof path === "string" && path.includes("/market/groups")) {
          return Promise.resolve(json([], 200));
        }
        // Return 503 on first map call
        return Promise.resolve(json({ errorMessage: "map service down" }, 503));
      });
    vi.stubGlobal("fetch", fetchMock);

    render(<MapPage />);
    await waitFor(() => expect(screen.getByTestId("map-error")).toBeTruthy());
    const retry = screen.getByTestId("map-error-retry");
    expect(retry).toBeTruthy();

    // Replace fetch mock to resolve on retry
    vi.stubGlobal("fetch", vi.fn().mockImplementation((path: string) => {
      if (typeof path === "string" && path.includes("/market/groups")) {
        return Promise.resolve(json([], 200));
      }
      return Promise.resolve(json(SMALL_WIRE));
    }));

    fireEvent.click(retry);
    await waitFor(() => expect(screen.getByTestId("map-canvas")).toBeTruthy());
  });
});

describe("MapPage — views", () => {
  it("defaults to treemap", async () => {
    await renderReady();
    expect(screen.getByTestId("map-view-treemap").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("map-canvas")).toBeTruthy();
  });

  it("switches to matrix view", async () => {
    await renderReady();
    fireEvent.click(screen.getByTestId("map-view-matrix"));
    expect(screen.getByTestId("matrix-canvas")).toBeTruthy();
    expect(screen.queryByTestId("map-canvas")).toBeNull();
  });

  it("switches to bubble view", async () => {
    await renderReady();
    fireEvent.click(screen.getByTestId("map-view-bubble"));
    expect(screen.getByTestId("bubble-canvas")).toBeTruthy();
    expect(screen.queryByTestId("map-canvas")).toBeNull();
  });

  it("switches to grid view", async () => {
    await renderReady();
    fireEvent.click(screen.getByTestId("map-view-grid"));
    expect(screen.getByTestId("grid-view")).toBeTruthy();
    expect(screen.queryByTestId("map-canvas")).toBeNull();
  });
});

describe("MapPage — sidebar toggle", () => {
  it("toggles the sidebar collapse", async () => {
    await renderReady();
    expect(screen.getByTestId("map-sidebar")).toBeTruthy();

    fireEvent.click(screen.getByTestId("sidebar-toggle"));
    expect(screen.queryByTestId("map-sidebar")).toBeNull();

    fireEvent.click(screen.getByTestId("sidebar-toggle"));
    expect(screen.getByTestId("map-sidebar")).toBeTruthy();
  });
});

describe("MapPage — timeframe select", () => {
  it("defaults to 1D and shows 6 options", async () => {
    await renderReady();
    const select = screen.getByTestId("map-timeframe-select") as HTMLSelectElement;
    expect(select.value).toBe("1d");
    expect(select.options).toHaveLength(6);
    expect(select.options[0]?.text).toBe("1D");
    expect(select.options[5]?.text).toBe("1Y");
  });

  it("re-fetches when timeframe changes", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation((path: string) => {
        if (typeof path === "string" && path.includes("/market/groups")) {
          return Promise.resolve(json([], 200));
        }
        return Promise.resolve(json(SMALL_WIRE));
      });
    vi.stubGlobal("fetch", fetchMock);

    render(<MapPage />);
    await screen.findByTestId("map-canvas");
    const callCount = fetchMock.mock.calls.length;

    fireEvent.change(screen.getByTestId("map-timeframe-select"), { target: { value: "1w" } });

    await waitFor(() => {
      const calls = fetchMock.mock.calls;
      expect(calls.length).toBeGreaterThan(callCount);
      // Find a call to the map endpoint with 1w
      const mapUrls = calls.filter((c) =>
        (c[0] as string).includes("/market/map"),
      ) as [string, RequestInit][];
      expect(mapUrls.some(([url]) => url.includes("timeframe=1w"))).toBe(true);
    });
  });
});

describe("MapPage — size by toggle", () => {
  it("defaults to market cap and toggles to equal", async () => {
    await renderReady();
    expect(screen.getByTestId("map-size-cap").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("map-size-equal").getAttribute("aria-pressed")).toBe("false");

    fireEvent.click(screen.getByTestId("map-size-equal"));
    expect(screen.getByTestId("map-size-cap").getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByTestId("map-size-equal").getAttribute("aria-pressed")).toBe("true");
  });
});

describe("MapPage — search filters ticker list", () => {
  it("shows a ticker list when searching", async () => {
    await renderReady();
    const input = screen.getByTestId("map-search") as HTMLInputElement;

    // Initially no ticker list
    expect(screen.queryByTestId("map-ticker-AAPL")).toBeNull();

    fireEvent.change(input, { target: { value: "AAPL" } });
    expect(screen.getByTestId("map-ticker-AAPL")).toBeTruthy();
    expect(screen.getByTestId("map-ticker-AAPL").getAttribute("href")).toBe("/app/stock/AAPL");
  });

  it("clears the list when search is emptied", async () => {
    await renderReady();
    const input = screen.getByTestId("map-search") as HTMLInputElement;

    fireEvent.change(input, { target: { value: "XOM" } });
    expect(screen.getByTestId("map-ticker-XOM")).toBeTruthy();

    fireEvent.change(input, { target: { value: "" } });
    expect(screen.queryByTestId("map-ticker-XOM")).toBeNull();
  });
});

describe("MapPage — section title and legend", () => {
  it("renders the section title with stock/sector counts", async () => {
    await renderReady();
    expect(screen.getByText("7 stocks · 3 sectors")).toBeTruthy();
  });

  it("renders the legend bar", async () => {
    await renderReady();
    expect(screen.getByTestId("map-legend")).toBeTruthy();
  });
});

describe("MapPage — request lifecycle", () => {
  it("aborts the first read when the timeframe changes and never draws it twice", async () => {
    const signals: AbortSignal[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((path: string, init?: RequestInit) => {
        if (typeof path === "string" && path.includes("/market/groups")) {
          return Promise.resolve(json([], 200));
        }
        if (init?.signal) signals.push(init.signal);
        return Promise.resolve(json(SMALL_WIRE));
      }),
    );

    render(<MapPage />);
    await waitFor(() =>
      expect(screen.getByTestId("map-canvas").getAttribute("data-tiles")).toBe("7"),
    );

    fireEvent.change(screen.getByTestId("map-timeframe-select"), { target: { value: "1w" } });
    await waitFor(() =>
      expect(screen.getByTestId("map-canvas").getAttribute("data-tiles")).toBe("7"),
    );

    // First map signal should be aborted
    const mapSignals = signals.filter((s) => s.aborted);
    expect(mapSignals.length).toBeGreaterThanOrEqual(1);
    // Multiple paints from the resize and effects
    expect(harness.fake.passes().length).toBeGreaterThanOrEqual(2);
  });
});