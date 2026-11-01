/**
 * A canvas that counts what was painted, and a ResizeObserver that answers at
 * once.
 *
 * jsdom has no 2D context (calling `getContext` logs "not implemented" and
 * returns null), so a component test would otherwise prove only that the canvas
 * element exists. The fake records every paint pass — `setTransform` starts a
 * bucket, so `passes()[n].fillRects` is exactly one frame's tiles — which is how
 * the tests can say "5,576 rects, once" instead of "something was drawn".
 *
 * The observer class is created *inside* `installCanvas` and closes over its own
 * list, so `resize()` reaches the instances the component actually constructed
 * even though the class itself is only visible through `globalThis`.
 */

import { vi } from "vitest";

export interface PaintBucket {
  transform: number[];
  fillRects: number[][];
  strokes: number[][];
  texts: string[];
}

export interface FakeCanvas {
  ctx: CanvasRenderingContext2D;
  /** The buckets after the initial empty one: one per `setTransform`. */
  passes: () => PaintBucket[];
}

export function fakeContext(): FakeCanvas {
  const buckets: PaintBucket[] = [
    { transform: [], fillRects: [], strokes: [], texts: [] },
  ];
  let current = buckets[0] as PaintBucket;

  const ctx = {
    fillStyle: "",
    strokeStyle: "",
    font: "",
    textAlign: "left",
    textBaseline: "alphabetic",
    lineWidth: 1,
    setTransform: (...args: number[]) => {
      current = { transform: args, fillRects: [], strokes: [], texts: [] };
      buckets.push(current);
    },
    clearRect: () => {},
    fillRect: (...args: number[]) => {
      current.fillRects.push(args);
    },
    strokeRect: (...args: number[]) => {
      current.strokes.push(args);
    },
    beginPath: () => {},
    moveTo: () => {},
    lineTo: () => {},
    stroke: () => {},
    fillText: (text: string) => {
      current.texts.push(text);
    },
    measureText: (text: string) => ({ width: text.length * 6 }),
    save: () => {},
    restore: () => {},
  };

  return { ctx: ctx as unknown as CanvasRenderingContext2D, passes: () => buckets.slice(1) };
}

export interface CanvasHarness {
  readonly fake: FakeCanvas;
  /** Deliver a new content box to every observer the component created. */
  resize(width: number, height: number): void;
  /** How many observers were constructed (one per mounted canvas). */
  readonly observers: number;
}

/** Install the fake context + observer + DPR. Call before `render`. */
export function installCanvas(
  options: { width?: number; height?: number; dpr?: number } = {},
): CanvasHarness {
  const size = { width: options.width ?? 900, height: options.height ?? 600 };
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

/** A `Response` with a JSON body (a body reads once, so always build fresh). */
export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}
