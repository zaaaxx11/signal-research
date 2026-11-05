/**
 * The Maps surface, exported for the shell to route.
 */

export { MapPage, type MapPageProps } from "./MapPage";
export { MapCanvas, type MapCanvasProps, type MapCanvasStats } from "./MapCanvas";
export { MatrixView, type MatrixViewProps } from "./MatrixView";
export { BubbleView, type BubbleViewProps } from "./BubbleView";
export { GridView, type GridViewProps } from "./GridView";
export {
  MIN_TILE,
  ROOT_PAD,
  SECTOR_GUTTER,
  SECTOR_HEADER,
  hitTest,
  indexOfSymbol,
  layoutMap,
  nextTile,
  squarify,
  type Direction,
  type LayoutOptions,
  type MapHit,
  type MapLayout,
  type Rect,
  type SectorLayout,
  type Tile,
} from "./layout";
export {
  MapMalformed,
  parseMap,
  sectorCap,
  type MapPayload,
  type MapSector,
  type MapStock,
  type MarketGroup,
} from "./types";
export {
  mapPath,
  groupsPath,
  useMapData,
  useMarketGroups,
  TIMEFRAMES,
  TIMEFRAME_LABELS,
  TIMEFRAME_METRIC_LABELS,
  type MapStatus,
  type MapView,
  type MapQuery,
  type Timeframe,
} from "./useMapData";
export { paintMap, type PaintInput, type PaintStats } from "./paint";
export {
  contrastRatio,
  cssRgb,
  inkFor,
  mergeTheme,
  parseColour,
  prepareTheme,
  rampColor,
  rampFill,
  rampRgb,
  readTheme,
  relativeLuminance,
  swatch,
  type MapTheme,
  type Palette,
  type Rgb,
} from "./theme";
export { clockLabel, formatCount, marketCapLabel, metricLabel, signedPercent, toneClass } from "./format";