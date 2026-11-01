import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { installCanvas, json, type CanvasHarness } from "./__fixtures__/canvas";
import { SMALL_WIRE, WIDE_WIRE } from "./__fixtures__/payload";
import { MapPage } from "./MapPage";

/**
 * The page end to end, over a mocked `fetch` and at real DOM level.
 *
 * The canvas is faked (jsdom has no 2D context), so what is asserted here is
 * everything the fake can still see: the layout the component publishes on
 * `data-sectors`, the hit-test result on `data-hover`, the keyboard's selection,
 * the popup's DOM, the request lifecycle, and the fact that 5,576 tiles produce
 * zero tile elements.
 *
 * Coordinate note: jsdom's `getBoundingClientRect` is all zeros, so canvas
 * coordinates and client coordinates are the same number — which is exactly
 * what the component's `clientX - rect.left` arithmetic expects.
 */

let harness: CanvasHarness;

function serves(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const mock = vi.fn().mockImplementation(() => Promise.resolve(json(body, status)));
  vi.stubGlobal("fetch", mock);
  return mock;
}

async function renderReady(wire: unknown = SMALL_WIRE, onOpenSymbol?: (symbol: string) => void) {
  serves(wire);
  render(<MapPage onOpenSymbol={onOpenSymbol} />);
  const canvas = (await screen.findByTestId("map-canvas")) as HTMLCanvasElement;
  // The ResizeObserver delivers its first box in a microtask (as a real one
  // does after layout); the map is only laid out once that box has landed.
  await waitFor(() => expect(canvas.getAttribute("data-tiles")).not.toBe("0"));
  // Flush the effects the resize kicked off (stats, paint) before asserting.
  await act(async () => {
    await Promise.resolve();
  });
  return canvas;
}

interface SectorRect {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

function sectorsOf(canvas: HTMLCanvasElement): SectorRect[] {
  return JSON.parse(canvas.getAttribute("data-sectors") ?? "[]") as SectorRect[];
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

  it("draws the map once the document lands", async () => {
    const canvas = await renderReady();

    expect(canvas.getAttribute("data-tiles")).toBe("7");
    expect(canvas.getAttribute("data-drawn")).toBe("7");
    expect(canvas.getAttribute("data-omitted")).toBe("0");
    expect(screen.getByTestId("map-metric-label").textContent).toBe("1-Day performance");
    expect(screen.getByTestId("map-counts").textContent).toContain("7 / 9 stocks");
    expect(screen.getByTestId("map-stats").textContent).toContain("7 drawn");
  });

  it("says so when the scan has no rows, and offers a retry", () => {
    serves({ ...SMALL_WIRE, mapped: 0, sectors: [] });
    render(<MapPage />);

    return waitFor(() => {
      expect(screen.getByTestId("map-empty")).toBeTruthy();
      expect(screen.queryByTestId("map-canvas")).toBeNull();
    });
  });

  it("renders the API's error with a retry that re-reads", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() =>
        Promise.resolve(json({ errorMessage: "map service down" }, 503)),
      )
      .mockImplementationOnce(() => Promise.resolve(json(SMALL_WIRE)));
    vi.stubGlobal("fetch", fetchMock);

    render(<MapPage />);
    await waitFor(() => expect(screen.getByTestId("map-error")).toBeTruthy());
    expect(screen.getByTestId("map-error-message").textContent).toBe("map service down");
    expect(screen.getByTestId("map-error").getAttribute("role")).toBe("alert");

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByTestId("map-canvas")).toBeTruthy());
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("MapPage — one canvas, no tile elements", () => {
  it("draws 5,576 stocks without a DOM node per stock", async () => {
    const canvas = await renderReady(WIDE_WIRE);

    expect(canvas.getAttribute("data-tiles")).toBe("5576");
    expect(document.querySelectorAll("canvas")).toHaveLength(1);
    expect(document.querySelectorAll("[data-tile]")).toHaveLength(0);
    // The whole document — nav-less, but with an 11-row legend and an 11-row
    // table — stays within a few hundred nodes; 5,576 tiles would be thousands.
    expect(document.querySelectorAll("*").length).toBeLessThan(500);
  });

  it("paints one pass per layout, not one per row", async () => {
    await renderReady(WIDE_WIRE);
    const passes = harness.fake.passes();
    expect(passes).toHaveLength(1);
    // One rect per tile, plus the plane and at most one label band per sector.
    const rects = passes[0]?.fillRects.length ?? 0;
    expect(rects).toBeGreaterThanOrEqual(5576);
    expect(rects).toBeLessThanOrEqual(5576 + 12);
  });
});

describe("MapPage — hover", () => {
  it("opens a popup with the sector and its top movers, and hides it on leave", async () => {
    const canvas = await renderReady();
    const technology = sectorsOf(canvas).find((sector) => sector.id === "technology");
    expect(technology).toBeDefined();
    if (!technology) return;

    fireEvent.pointerMove(canvas, {
      clientX: technology.x + technology.w / 2,
      clientY: technology.y + technology.h / 2,
    });

    const popup = screen.getByTestId("map-popup");
    expect(canvas.getAttribute("data-hover")).toMatch(/^technology\|/);
    expect(within(popup).getByTestId("map-popup-sector").textContent).toBe("Technology");
    expect(within(popup).getByTestId("map-popup-value").textContent).toBe("+0.83%");
    expect(popup.textContent).toContain("2 adv");
    expect(popup.textContent).toContain("1 dec");

    // The eight largest absolute moves, biggest first: AAPL 2.1, MSFT 1.2, NVDA -0.8.
    const stocks = within(popup).getAllByTestId(/^map-popup-stock-/);
    expect(stocks.map((stock) => stock.textContent)).toEqual(["AAPL", "MSFT", "NVDA"]);
    expect(stocks[0]?.getAttribute("href")).toBe("/app/stock/AAPL");

    // Adversarial (d): the tooltip cannot sit between the pointer and the map.
    expect(popup.style.pointerEvents).toBe("none");
    expect(popup.className).toContain("pointer-events-none");

    fireEvent.pointerLeave(canvas);
    expect(screen.queryByTestId("map-popup")).toBeNull();
  });

  it("names the sector even when the pointer is in its label band", async () => {
    const canvas = await renderReady();
    const technology = sectorsOf(canvas).find((sector) => sector.id === "technology");
    if (!technology) return;

    // The band is the top 16 px of the block; the centre of that strip is label,
    // not a stock.
    fireEvent.pointerMove(canvas, {
      clientX: technology.x + technology.w / 2,
      clientY: technology.y + 8,
    });

    expect(canvas.getAttribute("data-hover")).toBe("technology|");
    expect(screen.getByTestId("map-popup-sector").textContent).toBe("Technology");
  });
});

describe("MapPage — keyboard", () => {
  it("moves the selection, announces it, opens it and clears it", async () => {
    const onOpen = vi.fn();
    const canvas = await renderReady(SMALL_WIRE, onOpen);

    fireEvent.focus(canvas);
    const first = canvas.getAttribute("data-selected");
    expect(first).not.toBe("");
    expect(screen.getByTestId("map-announcer").textContent).toContain(first ?? "");

    fireEvent.keyDown(canvas, { key: "ArrowRight" });
    const second = canvas.getAttribute("data-selected");
    expect(second).not.toBe(first);
    expect(second).not.toBe("");
    expect(screen.getByTestId("map-announcer").textContent).toContain(second ?? "");
    expect(screen.getByTestId("map-announcer").getAttribute("aria-live")).toBe("polite");

    fireEvent.keyDown(canvas, { key: "Enter" });
    expect(onOpen).toHaveBeenCalledWith(second);

    fireEvent.keyDown(canvas, { key: "Escape" });
    expect(canvas.getAttribute("data-selected")).toBe("");
  });

  it("opens /stock/{symbol} when no handler is wired", async () => {
    const canvas = await renderReady();
    fireEvent.focus(canvas);
    const symbol = canvas.getAttribute("data-selected");
    expect(symbol).toBeTruthy();

    fireEvent.keyDown(canvas, { key: "Enter" });
    expect(window.location.pathname).toBe(`/app/stock/${symbol}`);
  });
});

describe("MapPage — the legend zooms a sector", () => {
  it("filters the canvas to one block and restores the market", async () => {
    const canvas = await renderReady();
    expect(sectorsOf(canvas)).toHaveLength(3);

    fireEvent.click(screen.getByTestId("map-legend-financials"));
    expect(sectorsOf(canvas)).toHaveLength(1);
    expect(sectorsOf(canvas)[0]?.id).toBe("financials");
    expect(screen.getByTestId("map-focus-note").textContent).toContain("Financials");
    expect(canvas.getAttribute("data-tiles")).toBe("2");

    fireEvent.click(screen.getByTestId("map-legend-all"));
    expect(sectorsOf(canvas)).toHaveLength(3);
    expect(canvas.getAttribute("data-tiles")).toBe("7");
    expect(screen.queryByTestId("map-focus-note")).toBeNull();
  });

  it("marks the focused row for assistive technology", async () => {
    await renderReady();
    const button = screen.getByTestId("map-legend-technology");
    expect(button.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(button);
    expect(button.getAttribute("aria-pressed")).toBe("true");
  });
});

describe("MapPage — the hidden table", () => {
  it("carries one row per sector with the same numbers", async () => {
    await renderReady();
    // The wrapper is the hidden box: a `<table>` ignores `width: 1px`, so
    // hiding the table itself would leave a 700 px absolute node on the page.
    expect(screen.getByTestId("map-table-wrap").className).toContain("sr-only");
    const table = screen.getByTestId("map-table");
    const rows = within(table).getAllByRole("row");
    // Header + three sectors.
    expect(rows).toHaveLength(4);
    expect(table.querySelector("caption")?.textContent).toContain("7 of 9 stocks mapped");

    const technology = screen.getByTestId("map-row-technology");
    expect(within(technology).getByText("Technology")).toBeTruthy();
    expect(technology.textContent).toContain("+0.83%");
    expect(technology.textContent).toContain("$9.34T");
  });
});

describe("MapPage — request lifecycle", () => {
  it("aborts the first read when the metric changes and never draws it twice", async () => {
    const smaller = {
      ...SMALL_WIRE,
      mapped: 2,
      sectors: [SMALL_WIRE.sectors[2]],
    };
    const signals: AbortSignal[] = [];
    const fetchMock = vi
      .fn()
      .mockImplementationOnce((_path: string, init: RequestInit) => {
        signals.push(init.signal as AbortSignal);
        return Promise.resolve(json(SMALL_WIRE));
      })
      .mockImplementationOnce((_path: string, init: RequestInit) => {
        signals.push(init.signal as AbortSignal);
        return Promise.resolve(json(smaller));
      });
    vi.stubGlobal("fetch", fetchMock);

    const { rerender } = render(<MapPage metric="change" />);
    await waitFor(() => expect(screen.getByTestId("map-canvas").getAttribute("data-tiles")).toBe("7"));

    rerender(<MapPage metric="change_1w" />);
    await waitFor(() =>
      expect(screen.getByTestId("map-canvas").getAttribute("data-tiles")).toBe("2"),
    );

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(signals[0]?.aborted).toBe(true);
    expect(signals[1]?.aborted).toBe(false);
    // Two paints: the first document, then the second. A duplicate answer for
    // the first would have added a third (and redrawn 7 tiles, not 2).
    expect(harness.fake.passes()).toHaveLength(2);
    const last = harness.fake.passes().at(-1);
    expect(last?.fillRects.length).toBeGreaterThanOrEqual(3);
    expect(last?.fillRects.length).toBeLessThanOrEqual(3 + 1);
  });
});

describe("MapPage — resize and DPR", () => {
  it("repaints to the container's new size", async () => {
    const canvas = await renderReady();
    expect(canvas.width).toBe(900);
    expect(canvas.height).toBe(600);

    act(() => harness.resize(600, 400));

    expect(canvas.width).toBe(600);
    expect(canvas.height).toBe(400);
    const before = sectorsOf(canvas);
    expect(before.reduce((sum, sector) => sum + sector.w * sector.h, 0)).toBeLessThan(600 * 400);
  });

  it("scales the backing store, not the CSS box, at DPR 2", async () => {
    harness = installCanvas({ width: 800, height: 500, dpr: 2 });
    serves(SMALL_WIRE);
    render(<MapPage />);
    await waitFor(() => expect(screen.getByTestId("map-canvas")).toBeTruthy());

    const canvas = screen.getByTestId("map-canvas") as HTMLCanvasElement;
    await waitFor(() => expect(canvas.getAttribute("data-dpr")).toBe("2"));
    expect(canvas.width).toBe(1600);
    expect(canvas.height).toBe(1000);
    expect(canvas.getAttribute("data-dpr")).toBe("2");
    // The box is CSS-sized: no inline width/height fighting the flex parent.
    expect(canvas.style.width).toBe("");
    expect(canvas.style.height).toBe("");
  });
});
