/**
 * The Charts surface, exported for the shell to route.
 *
 * `ChartsPage` is the page; `StockChart` is what the stock page embeds. The
 * rest is exported because it is independently useful and independently
 * tested: the pure geometry, the painter, the wire reader, the hook, and the
 * token palette.
 */

export { ChartsPage, CHART_INDICES, type ChartIndex, type ChartsPageProps } from "./ChartsPage";
export { StockChart, STOCK_CHART_HEIGHT, type StockChartProps } from "./StockChart";
export { Chart, type ChartProps } from "./Chart";
/**
 * The bar adapter only. `ChartBars` and the library behind it are deliberately
 * absent from this barrel: a static re-export here would make every importer of
 * `@/charts` pull the 55 KB gzip renderer into whatever chunk asked for a type.
 * Components reach the renderer through `./lazy`.
 */
export { barData, toBars, utcLabel, type BarPayload, type ChartBar } from "./bars";
export {
  INDICATOR_COLUMNS,
  INDICATOR_IDS,
  INDICATOR_LABELS,
  RSI_PERIOD,
  SMA_BY_ID,
  SMA_PERIODS,
  ema,
  isIndicatorId,
  orderIndicators,
  rsi,
  sma,
  type IndicatorId,
  type Point,
  type SmaId,
  type SmaPeriod,
  type Slot,
} from "./indicators";
export {
  AXIS_BOTTOM,
  AXIS_RIGHT,
  GAP_BREAK,
  GAP_CLAMP,
  PAD_LEFT,
  PAD_TOP,
  PRICE_PAD,
  VOLUME_GAP,
  VOLUME_MAX,
  VOLUME_MIN,
  VOLUME_SHARE,
  chartGeometry,
  nearestIndex,
  niceTicks,
  type AxisTick,
  type ChartBox,
  type ChartCandle,
  type ChartGeometry,
  type ChartPoint,
  type GeometryInput,
  type PriceDomain,
  type TimeDomain,
  type VolumeBar,
} from "./geometry";
export { paintChart, type ChartKind, type PaintOptions, type PaintStats } from "./paint";
export {
  CHART_RANGES,
  ChartMalformed,
  RANGE_LABELS,
  RANGE_NAMES,
  isChartRange,
  parseChart,
  seriesChange,
  type Bar,
  type ChartRange,
  type ChartSeries,
  type SeriesChange,
} from "./types";
export { chartPath, useChartData, type ChartStatus, type ChartView } from "./useChartData";
export { mergeChartTheme, readChartTheme, type ChartTheme } from "./theme";
export { axisPrice, axisTime, barTime, estimateLabelWidth, pricePrecision } from "./format";
