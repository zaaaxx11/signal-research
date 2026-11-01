/**
 * A canvas that records every command, and a ResizeObserver that answers at
 * once.
 *
 * jsdom has no 2D context, so a component test would otherwise prove only that
 * a `<canvas>` exists. The fake keeps one bucket per `setTransform` (one frame)
 * and records the things the chart's contract is about: the scale factor, the
 * number of polyline runs (`moveTo`), the axis labels (`fillText`), the dashes
 * on the previous-close line, and the fills of the volume strip.
 */

import { vi } from "vitest";

export interface TextMark {
  text: string;
  x: number;
  y: number;
  font: string;
}

export interface PaintBucket {
  transform: number[];
  fillRects: number[][];
  strokes: number[][];
  moveTos: number[][];
  lineTos: number[][];
  texts: TextMark[];
  dashes: number[][];
  fills: number;
  strokesIssued: number;
  arcs: number;
  alpha: number[];
}

export interface FakeCanvas {
  ctx: CanvasRenderingContext2D;
  /** One bucket per `setTransform` — i.e. one per paint pass. */
  passes: () => PaintBucket[];
}

export function fakeContext(): FakeCanvas {
  const buckets: PaintBucket[] = [];
  let current: PaintBucket = blank([]);

  const begin = (transform: number[]): void => {
    current = blank(transform);
    buckets.push(current);
  };

  const ctx = {
    fillStyle: "",
    strokeStyle: "",
    font: "",
    textAlign: "left",
    textBaseline: "alphabetic",
    lineWidth: 1,
    lineJoin: "miter",
    globalAlpha: 1,
    setTransform: (...args: number[]) => begin(args),
    clearRect: () => {},
    fillRect: (...args: number[]) => {
      current.fillRects.push(args);
    },
    strokeRect: (...args: number[]) => {
      current.strokes.push(args);
    },
    beginPath: () => {},
    closePath: () => {},
    moveTo: (...args: number[]) => {
      current.moveTos.push(args);
    },
    lineTo: (...args: number[]) => {
      current.lineTos.push(args);
    },
    stroke: () => {
      current.strokesIssued += 1;
    },
    fill: () => {
      current.fills += 1;
    },
    arc: () => {
      current.arcs += 1;
    },
    setLineDash: (segments: number[]) => {
      current.dashes.push([...segments]);
    },
    fillText: (text: string, x: number, y: number) => {
      current.texts.push({ text, x, y, font: ctx.font });
    },
    measureText: (text: string) => ({ width: text.length * 6 }),
    save: () => {},
    restore: () => {
      ctx.globalAlpha = 1;
    },
  };

  return { ctx: ctx as unknown as CanvasRenderingContext2D, passes: () => buckets };
}

function blank(transform: number[]): PaintBucket {
  return {
    transform,
    fillRects: [],
    strokes: [],
    moveTos: [],
    lineTos: [],
    texts: [],
    dashes: [],
    fills: 0,
    strokesIssued: 0,
    arcs: 0,
    alpha: [],
  };
}

export interface CanvasHarness {
  readonly fake: FakeCanvas;
  /** Deliver a new content box to every observer the component created. */
  resize(width: number, height: number): void;
  readonly observers: number;
}

/** Install the fake context + observer + DPR. Call before `render`. */
export function installCanvas(
  options: { width?: number; height?: number; dpr?: number } = {},
): CanvasHarness {
  const size = { width: options.width ?? 640, height: options.height ?? 320 };
  const observers: { emit(): void }[] = [];

  class TestResizeObserver {
    private readonly callback: ResizeObserverCallback;

    constructor(callback: ResizeObserverCallback) {
      this.callback = callback;
      observers.push(this);
    }

    observe(): void {
      queueMicrotask(() => this.emit());
    }

    unobserve(): void {}

    disconnect(): void {}

    emit(): void {
      const entry = { contentRect: { ...size } } as unknown as ResizeObserverEntry;
      this.callback([entry], this as unknown as ResizeObserver);
    }
  }

  vi.stubGlobal("ResizeObserver", TestResizeObserver);
  Object.defineProperty(window, "devicePixelRatio", {
    configurable: true,
    value: options.dpr ?? 1,
  });

  const fake = fakeContext();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => fake.ctx);

  return {
    fake,
    resize(width: number, height: number) {
      size.width = width;
      size.height = height;
      for (const observer of observers) observer.emit();
    },
    get observers() {
      return observers.length;
    },
  };
}
