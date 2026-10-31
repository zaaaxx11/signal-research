/**
 * Public surface of the detail panel.
 *
 * The shell imports the named component; the default export exists so the
 * parent can split it with `React.lazy(() => import("@/detail"))` and keep the
 * panel out of first paint (scripts/check-budget.mjs counts only what
 * index.html preloads).
 */

export {
  DetailPanel,
  clampPanelWidth,
  DEFAULT_PANEL_WIDTH,
  MAX_PANEL_WIDTH,
  MIN_PANEL_WIDTH,
  RESIZE_PAGE,
  RESIZE_STEP,
  type DetailPanelProps,
} from "./DetailPanel";

export { DetailPanel as default } from "./DetailPanel";
