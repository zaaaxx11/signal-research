/**
 * Market breadth — the strip the shell mounts above the grid.
 *
 * The shell only needs `<BreadthStrip />`; the pieces underneath are exported
 * because they are independently useful (and independently tested): the pure
 * `parseBreadth` / `sumCheck` / `sortSectors` model, the presentational
 * `SectorBlock`, and the `useBreadth` read.
 */

export { BreadthStrip, type BreadthStripProps } from "./BreadthStrip";
export { SectorBlock, type SectorBlockProps } from "./SectorBlock";
export { breadthPath, useBreadth, type BreadthQuery, type BreadthStatus, type BreadthView } from "./useBreadth";
export {
  BreadthMalformed,
  parseBreadth,
  sortSectors,
  sumCheck,
  type BreadthReport,
  type SectorBucket,
  type SumCheck,
} from "./report";
