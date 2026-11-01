/**
 * The chart library's own door.
 *
 * `lightweight-charts` is ~55 KB gzip. It is the right renderer for a real
 * chart and the wrong thing to put in front of first paint on Home, so the
 * only module the shell imports is this one: it reaches `ChartBars` through
 * `import()`, which the bundler turns into a separate chunk. Nothing here
 * statically imports the component or the library — the type import is erased
 * — so a route that never draws a chart never downloads one.
 *
 * The loader is a named function rather than an inline arrow for the same
 * reason `charts/load.ts` exists: a failed download needs a fresh lazy type to
 * retry, and a component's type is bound to the first `import()` call.
 *
 * `ChartBarsLazy` is the component to use. Its fallback reserves the chart's
 * height so a card does not jump when the chunk lands.
 */

import * as React from "react";

import type { ChartBarsProps } from "./ChartBars";

export const loadChartBars = () =>
  import("./ChartBars").then((module) => ({ default: module.ChartBars }));

const LazyChartBars = React.lazy(loadChartBars);

export function ChartBarsLazy(props: ChartBarsProps) {
  return (
    <React.Suspense
      fallback={
        <div
          data-testid="chart-bars-fallback"
          aria-hidden="true"
          style={{ height: props.height }}
          className="w-full"
        />
      }
    >
      <LazyChartBars {...props} />
    </React.Suspense>
  );
}
