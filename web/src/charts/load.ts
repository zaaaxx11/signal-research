/**
 * The chart chunk's loader, in its own module.
 *
 * The page imports the *loader*, not the chart: a lazy component's module type
 * is bound to the first import() call, so retrying a failed download needs a
 * fresh type — the same reason `detail/load.ts` exists.
 */
export const loadStockChart = () =>
  import("./StockChart").then((module) => ({ default: module.StockChart }));
