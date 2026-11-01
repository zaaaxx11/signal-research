/**
 * MiniMap: the map as a card, and every claim that card makes.
 *
 * The canvas is faked (jsdom has no 2D context), so these tests assert the
 * things the fake can still see plus everything the DOM carries: the counts on
 * `data-drawn` / `data-omitted`, the geometry on `data-sectors`, the hit test
 * behind the popup, and the click that opens a stock.
 *
 * Geometry note: jsdom's `getBoundingClientRect` is all zeros, so a client
 * coordinate is already a canvas coordinate — the same seam `MapPage.test`
 * uses. The expected tile is recomputed here from the shipped layout, at the
 * same 520 × 220 box the observer reports, so a test cannot hover "somewhere"
 * and hope.
 */

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { installCanvas, type CanvasHarness } from "./__fixtures__/canvas";
import { SMALL_WIRE, WIDE_WIRE, generateGiantSector } from "./__fixtures__/payload";
import { layoutMap } from "./layout";
import { MiniMap, type MiniMapSectorInput } from "./MiniMap";
import { parseMap } from "./types";

let harness: CanvasHarness;

beforeEach(() => {
  harness = installCanvas({ width: 520, height: 220 });
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 1024 });
  Object.defineProperty(window, "innerHeight", { configurable: true, value: 768 });
});

const SMALL: readonly MiniMapSectorInput[] = SMALL_WIRE.sectors;

/** Six names in one sector, so "top four" has a wrong fourth to leave out. */
const SIX: readonly MiniMapSectorInput[] = [
  {
    id: "chips",
    label: "Chips",
    value: 1.2,
    count: 6,
    adv: 4,
    dec: 2,
    symbols: [
      { s: "CCC", c: 30, v: 3.1 },
      { s: "BBB", c: 40, v: -2.4 },
      { s: "FFF", c: 5, v: 2.0 },
      { s: "DDD", c: 20, v: 1.0 },
      { s: "AAA", c: 50, v: 0.4 },
      { s: "EEE", c: 10, v: -0.5 },
    ],
  },
];

function geometryOf(sectors: readonly MiniMapSectorInput[], width = 520, height = 220) {
  return layoutMap(parseMap({ sectors }), width, height);
}

/** The biggest painted tile's centre, in canvas coordinates. */
function centreOfLargestTile(sectors: readonly MiniMapSectorInput[] = SMALL): { x: number; y: number; symbol: string } {
  const tiles = [...geometryOf(sectors).tiles].sort((left, right) => right.w * right.h - left.w * left.h);
  const tile = tiles[0];
  if (!tile) throw new Error("fixture drew no tiles");
  return { x: tile.x + tile.w / 2, y: tile.y + tile.h / 2, symbol: tile.symbol };
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

async function renderMap(
  sectors: readonly MiniMapSectorInput[] = SMALL,
  props: { onOpenSymbol?: (symbol: string) => void; height?: number; ariaLabel?: string } = {},
) {
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  const view = render(<MiniMap sectors={sectors} {...props} />);
  const canvas = (await screen.findByTestId("minimap-canvas")) as HTMLCanvasElement;
  // The observer delivers its first box in a microtask, as a real one does.
  await waitFor(() => expect(canvas.getAttribute("data-drawn")).not.toBe("0"));
  await act(async () => {
    await Promise.resolve();
  });
  return { canvas, fetchMock, view };
}

describe("MiniMap — the count is the contract", () => {
  it("accounts for every tile: drawn plus omitted is the payload's total", async () => {
    const { canvas } = await renderMap(WIDE_WIRE.sectors);

    const drawn = Number(canvas.getAttribute("data-drawn"));
    const omitted = Number(canvas.getAttribute("data-omitted"));
    const total = Number(canvas.getAttribute("data-total"));

    expect(total).toBe(5576);
    expect(drawn).toBeGreaterThan(0);
    expect(drawn + omitted).toBe(total);
    // At card size some of the smallest names cannot be a whole pixel; the
    // count is printed rather than the tile being faked into existence.
    expect(omitted).toBeGreaterThanOrEqual(0);
  });

  it("draws a single-sector payload without needing a second sector to lean on", async () => {
    const { canvas } = await renderMap([SMALL_WIRE.sectors[0] as MiniMapSectorInput]);

    expect(canvas.getAttribute("data-drawn")).toBe("3");
    expect(canvas.getAttribute("data-omitted")).toBe("0");
    expect(sectorsOf(canvas)).toHaveLength(1);
    expect(sectorsOf(canvas)[0]?.id).toBe("technology");
  });

  it("renders a labelled empty box, not a blank canvas, when there is nothing to size", async () => {
    render(<MiniMap sectors={[]} />);

    const empty = screen.getByTestId("minimap-empty");
    expect(empty.getAttribute("role")).toBe("img");
    expect(empty.getAttribute("aria-label")).toMatch(/no sectors/i);
    expect(empty.textContent).toContain("No sectors to draw");
    expect(document.querySelector("canvas")).toBeNull();
  });

  it("keeps one canvas and no tile elements", async () => {
    await renderMap(WIDE_WIRE.sectors);

    expect(document.querySelectorAll("canvas")).toHaveLength(1);
    expect(document.querySelectorAll("[data-tile]")).toHaveLength(0);
    // Wrapper + canvas + a ten-row hidden list + the tooltip paragraph.
    expect(document.querySelectorAll("*").length).toBeLessThan(40);
  });
});

describe("MiniMap — hover", () => {
  it("shows the sector, its value, its breadth and only its four biggest movers", async () => {
    const { canvas } = await renderMap(SIX);
    const hit = centreOfLargestTile(SIX);
    const paintedOnce = canvas.dataset.ms;

    fireEvent.pointerMove(canvas, { clientX: hit.x, clientY: hit.y });

    // A hover is a tooltip, not a repaint: the painter's effect is keyed on the
    // layout and the box, so moving the pointer must not re-enter it.
    expect(canvas.dataset.ms).toBe(paintedOnce);

    const popup = screen.getByTestId("minimap-popup");
    expect(within(popup).getByTestId("minimap-popup-sector").textContent).toBe("Chips");
    expect(within(popup).getByTestId("minimap-popup-value").textContent).toBe("+1.20%");
    expect(within(popup).getByTestId("minimap-popup-breadth").textContent).toContain("4 adv");
    expect(within(popup).getByTestId("minimap-popup-breadth").textContent).toContain("2 dec");

    const stocks = within(popup).getAllByTestId(/^minimap-popup-stock-/);
    expect(stocks.map((stock) => stock.textContent)).toEqual(["CCC", "BBB", "FFF", "DDD"]);
    expect(within(popup).queryByTestId("minimap-popup-stock-AAA")).toBeNull();
  });

  it("names the sector when the pointer is in the label band and hides on leave", async () => {
    const { canvas } = await renderMap();
    const technology = sectorsOf(canvas).find((sector) => sector.id === "technology");
    if (!technology) throw new Error("technology was not laid out");

    fireEvent.pointerMove(canvas, { clientX: technology.x + technology.w / 2, clientY: technology.y + 8 });
    expect(screen.getByTestId("minimap-popup-sector").textContent).toBe("Technology");

    fireEvent.pointerLeave(canvas);
    expect(screen.queryByTestId("minimap-popup")).toBeNull();
  });

  it("cannot sit between the pointer and the map, and stays inside a narrow viewport", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 300 });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 240 });

    const { canvas } = await renderMap();
    const technology = sectorsOf(canvas).find((sector) => sector.id === "technology");
    if (!technology) throw new Error("technology was not laid out");

    // The far right of a block that itself sits past the viewport's edge.
    fireEvent.pointerMove(canvas, {
      clientX: technology.x + technology.w - 2,
      clientY: technology.y + technology.h - 2,
    });

    const popup = screen.getByTestId("minimap-popup");
    expect(popup.style.pointerEvents).toBe("none");
    expect(popup.className).toContain("pointer-events-none");

    const left = Number.parseFloat(popup.style.left);
    const top = Number.parseFloat(popup.style.top);
    expect(left).toBeGreaterThanOrEqual(8);
    expect(left + 208).toBeLessThanOrEqual(300 - 8);
    expect(top).toBeGreaterThanOrEqual(8);
    expect(top + 124).toBeLessThanOrEqual(240 - 8);
  });
});

describe("MiniMap — click opens the stock", () => {
  it("calls onOpenSymbol with the symbol under the pointer", async () => {
    const onOpen = vi.fn();
    const { canvas } = await renderMap(SMALL, { onOpenSymbol: onOpen });
    const hit = centreOfLargestTile(SMALL);

    fireEvent.click(canvas, { clientX: hit.x, clientY: hit.y });
    expect(onOpen).toHaveBeenCalledWith(hit.symbol);
  });

  it("routes to /stock/{symbol} when no handler is wired", async () => {
    const { canvas } = await renderMap(SMALL);
    const hit = centreOfLargestTile(SMALL);

    fireEvent.click(canvas, { clientX: hit.x, clientY: hit.y });
    expect(window.location.pathname).toBe(`/app/stock/${hit.symbol}`);
  });

  it("gives the popup's links the same destination", async () => {
    const onOpen = vi.fn();
    const { canvas } = await renderMap(SIX, { onOpenSymbol: onOpen });
    const hit = centreOfLargestTile(SIX);

    fireEvent.pointerMove(canvas, { clientX: hit.x, clientY: hit.y });
    const link = screen.getByTestId("minimap-popup-stock-CCC");
    expect(link.getAttribute("href")).toBe("/app/stock/CCC");

    fireEvent.click(link);
    expect(onOpen).toHaveBeenCalledWith("CCC");
  });
});

describe("MiniMap — assistive technology", () => {
  it("labels the image with the sector count and the largest sector only", async () => {
    await renderMap();

    const label = screen.getByRole("img").getAttribute("aria-label") ?? "";
    expect(label).toContain("3 sectors");
    expect(label).toContain("Technology");
    // Adversarial (c): a label that recited eleven names would be noise.
    expect(label).not.toContain("Financials");
    expect(label).not.toContain("Energy");
  });

  it("does not recite twenty-one sectors in the label either", async () => {
    const many: MiniMapSectorInput[] = Array.from({ length: 21 }, (_, index) => ({
      id: `sec${String(index).padStart(2, "0")}`,
      label: `Sector ${String(index).padStart(2, "0")}`,
      value: index - 10,
      symbols: [{ s: `T${String(index).padStart(2, "0")}`, c: 100 - index, v: index - 10 }],
    }));

    await renderMap(many, { ariaLabel: "Sector map" });

    const label = screen.getByRole("img").getAttribute("aria-label") ?? "";
    expect(label).toContain("21 sectors");
    expect(label).toContain("Sector 00");
    expect(label).not.toContain("Sector 01");
    expect(label.length).toBeLessThan(90);
  });

  it("lists the ten largest sectors for a screen reader, largest first", async () => {
    await renderMap(WIDE_WIRE.sectors);

    const list = screen.getByTestId("minimap-largest");
    expect(list.className).toContain("sr-only");
    expect(list.getAttribute("aria-label")).toMatch(/largest sectors/i);

    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(10);
    expect(rows[0]?.textContent).toContain("Technology");
    expect(rows[0]?.textContent).toContain("%");
    expect(rows[1]?.textContent).toContain("Financials");
  });
});

describe("MiniMap — adversarial", () => {
  it("does not let one giant name collapse the rest of the map", async () => {
    const wire = generateGiantSector();
    const { canvas } = await renderMap(wire.sectors);

    const drawn = Number(canvas.getAttribute("data-drawn"));
    const omitted = Number(canvas.getAttribute("data-omitted"));
    expect(drawn + omitted).toBe(404);
    // The giant, plus the three names in the sector next door, which keeps a
    // real block instead of being squeezed to nothing.
    expect(drawn).toBeGreaterThanOrEqual(4);
    expect(omitted).toBeGreaterThan(0);

    const small = sectorsOf(canvas).find((sector) => sector.id === "small");
    expect(small).toBeDefined();
    expect(small?.w).toBeGreaterThan(1);
    expect(small?.h).toBeGreaterThan(1);

    if (!small) return;
    fireEvent.pointerMove(canvas, { clientX: small.x + small.w / 2, clientY: small.y + small.h / 2 });
    expect(screen.getByTestId("minimap-popup-sector").textContent).toBe("Small Cap");

    // Narrower still, a 0.66 % sector is a two-pixel strip: the layout cannot
    // give it area it does not have. What it must not do is draw zero-height
    // tiles or lose the count — 403 of 404 rows are reported as omitted.
    act(() => harness.resize(320, 220));
    const narrowDrawn = Number(canvas.getAttribute("data-drawn"));
    const narrowOmitted = Number(canvas.getAttribute("data-omitted"));
    expect(narrowDrawn + narrowOmitted).toBe(404);
    expect(narrowDrawn).toBeLessThan(drawn);
    expect(narrowOmitted).toBe(403);
  });

  it("never fetches anything, on mount, hover or click", async () => {
    const onOpen = vi.fn();
    const { canvas, fetchMock } = await renderMap(SMALL, { onOpenSymbol: onOpen });
    const hit = centreOfLargestTile(SMALL);

    fireEvent.pointerMove(canvas, { clientX: hit.x, clientY: hit.y });
    fireEvent.click(canvas, { clientX: hit.x, clientY: hit.y });
    fireEvent.pointerLeave(canvas);

    expect(fetchMock).not.toHaveBeenCalled();
    expect(harness.observers).toBe(1);
  });
});

describe("MiniMap — resize and DPR", () => {
  it("repaints the backing store to the container's new width", async () => {
    const { canvas } = await renderMap();
    expect(canvas.width).toBe(520);
    expect(canvas.height).toBe(220);

    act(() => harness.resize(320, 220));

    expect(canvas.width).toBe(320);
    expect(canvas.getAttribute("data-ms")).not.toBeNull();
    expect(Number(canvas.getAttribute("data-ms"))).toBeGreaterThanOrEqual(0);
  });

  it("scales the backing store, not the CSS box, at DPR 2", async () => {
    harness = installCanvas({ width: 520, height: 220, dpr: 2 });
    const { canvas } = await renderMap();

    await waitFor(() => expect(canvas.getAttribute("data-dpr")).toBe("2"));
    expect(canvas.width).toBe(1040);
    expect(canvas.height).toBe(440);
    expect(canvas.style.width).toBe("");
    expect(canvas.style.height).toBe("");
  });

  it("animates nothing, so reduced motion needs no guard", async () => {
    const { canvas } = await renderMap();
    fireEvent.pointerMove(canvas, { clientX: 10, clientY: 10 });

    const popup = screen.getByTestId("minimap-popup");
    expect(popup.className).not.toMatch(/transition|animate/);
    expect(popup.style.transition).toBe("");
  });
});
