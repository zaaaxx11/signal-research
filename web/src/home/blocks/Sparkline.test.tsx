import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Sparkline, paintSparkline, resetSparkInk } from "./Sparkline";
import { series, stubCanvas, stubResizeObserver } from "../__fixtures__/home";

/**
 * The sparkline's contract, on the canvas and on the label:
 *
 * * `role="img"` and an aria-label that names the symbol, the last price and the
 *   change — the picture is unreadable to a screen reader without it;
 * * a 30-point series paints a line, one point paints a dot, and neither divides
 *   by zero;
 * * DPR scales the backing store, not the layout box.
 */

interface FakeCtx {
  ctx: CanvasRenderingContext2D;
  moves: number;
  lines: number;
  arcs: number;
  strokes: number;
  fills: number;
  strokeStyle: string;
  fillStyle: string;
}

function fakeCtx(): FakeCtx {
  const record: FakeCtx = {
    moves: 0,
    lines: 0,
    arcs: 0,
    strokes: 0,
    fills: 0,
    strokeStyle: "",
    fillStyle: "",
    ctx: undefined as unknown as CanvasRenderingContext2D,
  };
  const ctx = {
    clearRect: () => undefined,
    beginPath: () => undefined,
    moveTo: () => {
      record.moves += 1;
    },
    lineTo: () => {
      record.lines += 1;
    },
    arc: () => {
      record.arcs += 1;
    },
    stroke: () => {
      record.strokes += 1;
    },
    fill: () => {
      record.fills += 1;
    },
    setTransform: () => undefined,
    get strokeStyle(): string {
      return record.strokeStyle;
    },
    set strokeStyle(value: string) {
      record.strokeStyle = value;
    },
    get fillStyle(): string {
      return record.fillStyle;
    },
    set fillStyle(value: string) {
      record.fillStyle = value;
    },
    lineWidth: 1,
    lineJoin: "miter",
    lineCap: "butt",
  };
  record.ctx = ctx as unknown as CanvasRenderingContext2D;
  return record;
}

beforeEach(() => {
  resetSparkInk();
  stubCanvas();
  stubResizeObserver(320, 22);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("paintSparkline", () => {
  it("draws a line through every point", () => {
    const fake = fakeCtx();
    const shape = paintSparkline(fake.ctx, {
      points: series(100, 30),
      width: 320,
      height: 22,
      colour: "rgb(61, 220, 151)",
    });

    expect(shape).toBe("line");
    expect(fake.moves).toBe(1);
    expect(fake.lines).toBe(29);
    expect(fake.strokes).toBe(1);
    expect(fake.arcs).toBe(0);
    expect(fake.strokeStyle).toBe("rgb(61, 220, 151)");
  });

  it("draws a dot for one close, never a divide by zero", () => {
    const fake = fakeCtx();
    const shape = paintSparkline(fake.ctx, {
      points: [344.63],
      width: 320,
      height: 22,
      colour: "rgb(255, 95, 77)",
    });

    expect(shape).toBe("dot");
    expect(fake.arcs).toBe(1);
    expect(fake.fills).toBe(1);
    expect(fake.lines).toBe(0);
    expect(fake.strokes).toBe(0);
  });

  it("draws the same marker for an empty series rather than a blank canvas", () => {
    const fake = fakeCtx();

    expect(
      paintSparkline(fake.ctx, { points: [], width: 320, height: 22, colour: "rgb(166, 162, 164)" }),
    ).toBe("dot");
    expect(fake.arcs).toBe(1);
  });

  it("draws nothing when the stylesheet gave no colour", () => {
    const fake = fakeCtx();

    expect(paintSparkline(fake.ctx, { points: series(10, 10), width: 320, height: 22, colour: "" })).toBe(
      "none",
    );
    expect(fake.strokes).toBe(0);
  });

  it("draws nothing in a zero-width box instead of throwing", () => {
    const fake = fakeCtx();

    expect(
      paintSparkline(fake.ctx, { points: series(10, 10), width: 0, height: 0, colour: "red" }),
    ).toBe("none");
  });
});

describe("Sparkline", () => {
  it("is an image with a name that states the symbol, the price and the change", () => {
    render(<Sparkline closes={series(344.63)} symbol="AAPL" price={344.63} changePct={-1.23} />);

    const canvas = screen.getByTestId("sparkline-AAPL");
    expect(canvas.getAttribute("role")).toBe("img");
    expect(canvas.getAttribute("aria-label")).toBe(
      "AAPL: last $344.63, change -1.23%, 30-point trend",
    );
  });

  it("says a price is unavailable rather than printing a zero", () => {
    render(<Sparkline closes={[]} symbol="HALT" price={null} changePct={null} />);

    expect(screen.getByTestId("sparkline-HALT").getAttribute("aria-label")).toBe(
      "HALT: last price unavailable, change unavailable, no price series to draw",
    );
  });

  it("marks a one-point series as a dot", () => {
    render(<Sparkline closes={[12.5]} symbol="ONE" price={12.5} changePct={1} />);

    const canvas = screen.getByTestId("sparkline-ONE");
    expect(canvas.getAttribute("data-shape")).toBe("dot");
    expect(canvas.getAttribute("aria-label")).toContain("one close, drawn as a dot");
  });

  it("scales the backing store by devicePixelRatio, not the CSS box", async () => {
    Object.defineProperty(window, "devicePixelRatio", { configurable: true, value: 2 });
    render(<Sparkline closes={series(50, 30)} symbol="DPR" price={50} changePct={1} />);

    const canvas = screen.getByTestId("sparkline-DPR") as HTMLCanvasElement;
    await vi.waitFor(() => expect(canvas.width).toBe(640));
    expect(canvas.height).toBe(44);
  });

  it("takes its tone from the change: up, down or flat", async () => {
    Object.defineProperty(window, "devicePixelRatio", { configurable: true, value: 1 });
    // The stylesheet is not loaded in jsdom, so the resolved slots are empty and
    // the painter draws nothing — which is the honest fallback. The tone is
    // still exercised through the label, which is what a reader gets either way.
    render(
      <>
        <Sparkline closes={series(10, 5)} symbol="UP" price={10} changePct={2} />
        <Sparkline closes={series(10, 5)} symbol="DOWN" price={10} changePct={-2} />
        <Sparkline closes={series(10, 5)} symbol="FLAT" price={10} changePct={0} />
      </>,
    );

    expect(screen.getByTestId("sparkline-UP").getAttribute("aria-label")).toContain("change +2.00%");
    expect(screen.getByTestId("sparkline-DOWN").getAttribute("aria-label")).toContain("change -2.00%");
    expect(screen.getByTestId("sparkline-FLAT").getAttribute("aria-label")).toContain("change 0.00%");
  });
});
