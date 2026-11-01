/**
 * The Maps surface, exported for the shell to route.
 *
 * The page is the deliverable (`MapPage`); everything else is exported because
 * it is independently useful and independently tested: the pure layout, the
 * payload reader, the ramp, and the canvas itself (which a future embed might
 * mount without the page's chrome).
 */

export { MapPage, type MapPageProps } from "./MapPage";
export { MapCanvas, type MapCanvasProps, type MapCanvasStats } from "./MapCanvas";
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
export { MapMalformed, parseMap, sectorCap, type MapPayload, type MapSector, type MapStock } from "./types";
export { mapPath, useMapData, type MapStatus, type MapView } from "./useMapData";
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
